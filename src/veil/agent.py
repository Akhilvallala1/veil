"""Computer-use agent harness: Meta's documented Responses API loop plus a browser driver.

The same loop runs raw (base_url = Meta) or protected (base_url = Veil proxy).
The only Veil-specific addition is the optional X-Veil-Context header with the
current URL, so flow policy knows where typed text lands. Safety checks that
reach the harness are shown to a human (or auto-handled in bench mode).
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
from openai import OpenAI

from veil.driver.browser import BrowserDriver

SYSTEM = """You are a computer-use agent operating a browser. You receive screenshots and
choose the next action or actions. Coordinates are pixels in the screenshot you were last
shown: (0, 0) is the top-left. Click the center of the target element.
Return a short batch of actions only when they do not depend on intermediate visual feedback.
Do not batch across page loads. Finish by replying with a normal message containing the answer
when the goal is visibly complete, or a short explanation when the task is infeasible.
Do not claim completion unless the screenshot supports it."""


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


@dataclass
class RunResult:
    final: str | None
    steps: int
    usage: dict
    seconds: float
    stopped: str = ""
    session: str | None = None
    log: list[dict] = field(default_factory=list)


def run_agent(task: str, start_url: str, *, model: str = "muse-spark-1.3",
              base_url: str = "https://api.meta.ai/v1", max_steps: int = 15,
              approve: Callable[[dict], bool] | None = None, out_dir: Path | None = None,
              send_context: bool = True, driver: BrowserDriver | None = None) -> RunResult:
    """Run one task. `approve(check)` decides each pending safety check (None = acknowledge
    Meta's generic confirmation, deny anything else)."""
    # max_retries 6 (default 2): Meta's API returned bursts of 3+ consecutive 500/503s on
    # Oct 28 (same request succeeds a minute later), which killed whole benchmark runs.
    client = OpenAI(base_url=base_url, api_key=os.environ["MODEL_API_KEY"], max_retries=6,
                    http_client=httpx.Client(timeout=300))
    own_driver = driver is None
    driver = driver or BrowserDriver(start_url)
    usage = {"input": 0, "output": 0, "cached": 0}
    log: list[dict] = []
    session = None
    t_start = time.perf_counter()

    def headers() -> dict:
        h = {}
        if send_context:
            h["X-Veil-Context"] = driver.url
        if session:
            h["X-Veil-Session"] = session
        return h

    def call(**kw):
        nonlocal session
        raw = client.responses.with_raw_response.create(extra_headers=headers(), **kw)
        session = raw.headers.get("x-veil-session") or session
        r = raw.parse()
        u = r.usage
        if u:
            usage["input"] += u.input_tokens or 0
            usage["output"] += u.output_tokens or 0
            d = getattr(u, "input_tokens_details", None)
            usage["cached"] += (getattr(d, "cached_tokens", 0) or 0) if d else 0
        return r

    def decide(check) -> bool:
        c = check.model_dump() if hasattr(check, "model_dump") else check
        if approve is not None:
            return approve(c)
        return c.get("code") == "computer_action_requires_confirmation"

    png = driver.screenshot()
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "step_00.png").write_bytes(png)
    resp = call(model=model, instructions=SYSTEM, tools=[{"type": "computer"}],
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": task},
                    {"type": "input_image", "image_url": data_url(png)}]}])
    final, stopped = None, ""
    try:
        for step in range(1, max_steps + 1):
            calls = [o for o in resp.output if o.type == "computer_call"]
            if not calls:
                final = resp.output_text
                break
            c = calls[0]
            actions = [a.model_dump() if hasattr(a, "model_dump") else a for a in c.actions]
            checks = list(c.pending_safety_checks or [])
            decisions = [decide(x) for x in checks]
            log.append({"step": step, "url": driver.url, "actions": actions,
                        "checks": [x.model_dump() for x in checks], "approved": decisions})
            if not all(decisions):
                stopped = "safety check declined: " + ", ".join(
                    x.code for x, ok in zip(checks, decisions) if not ok)
                break
            driver.execute(actions)
            png = driver.screenshot()
            if out_dir:
                (out_dir / f"step_{step:02d}.png").write_bytes(png)
            resp = call(model=model, previous_response_id=resp.id, tools=[{"type": "computer"}],
                        input=[{"type": "computer_call_output", "call_id": c.call_id,
                                "acknowledged_safety_checks": [x.model_dump() for x in checks],
                                "output": {"type": "computer_screenshot",
                                           "image_url": data_url(png)}}])
        else:
            stopped = "step budget reached"
    finally:
        if own_driver:
            driver.close()
    res = RunResult(final, len(log), usage, round(time.perf_counter() - t_start, 1), stopped,
                    session, log)
    if out_dir:
        (out_dir / "run.json").write_text(json.dumps(res.__dict__, indent=2, default=str))
    return res
