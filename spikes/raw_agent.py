"""Day-1 spike: unprotected Muse Wonder computer-use loop on the payer portal.

This is the baseline Veil must protect: every screenshot (full of synthetic PHI)
goes to the cloud model. The loop follows Meta's documented Responses API
computer-use pattern. Logs steps, latency, token usage, and saves each frame.

Run with the portal up:
  uv run python spikes/raw_agent.py [--model muse-spark-1.3] [--base-url http://127.0.0.1:8799/v1]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from veil.driver.browser import BrowserDriver

load_dotenv()
OUT = Path("spikes/out/raw_agent")

SYSTEM = """You are a computer-use agent operating a browser. You receive screenshots and
choose the next action or actions. Coordinates are pixels in the screenshot you were last
shown: (0, 0) is the top-left. Click the center of the target element.
Return a short batch of actions only when they do not depend on intermediate visual feedback.
Do not batch across page loads. Finish by replying with a normal message containing the answer
when the goal is visibly complete, or a short explanation when the task is infeasible.
Do not claim completion unless the screenshot supports it."""

TASK = ("In the ClearPath provider portal, find the member Grace Nakamura and tell me the "
        "status of her most recent claim by service date, and the billed amount.")


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="muse-wonder-1.0")
    ap.add_argument("--base-url", default="https://api.meta.ai/v1")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--task", default=TASK)
    ap.add_argument("--approve-checks", action="store_true",
                    help="acknowledge every pending safety check (raw baseline only)")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    client = OpenAI(base_url=args.base_url, api_key=os.environ["MODEL_API_KEY"],
                    http_client=httpx.Client(timeout=180))
    driver = BrowserDriver("http://127.0.0.1:8100/")
    usage = {"input": 0, "output": 0, "cached": 0}
    steps = []

    def track(r):
        u = getattr(r, "usage", None)
        if u:
            usage["input"] += u.input_tokens or 0
            usage["output"] += u.output_tokens or 0
            d = getattr(u, "input_tokens_details", None)
            usage["cached"] += (getattr(d, "cached_tokens", 0) or 0) if d else 0

    png = driver.screenshot()
    (OUT / "step_00.png").write_bytes(png)
    t0 = time.perf_counter()
    resp = client.responses.create(
        model=args.model, instructions=SYSTEM, tools=[{"type": "computer"}],
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": args.task},
            {"type": "input_image", "image_url": data_url(png)}]}],
    )
    track(resp)
    final = None
    for step in range(1, args.max_steps + 1):
        dt = time.perf_counter() - t0
        calls = [o for o in resp.output if o.type == "computer_call"]
        if not calls:
            final = resp.output_text
            steps.append({"step": step, "model_secs": round(dt, 2), "final": final})
            break
        call = calls[0]
        actions = [a.model_dump() if hasattr(a, "model_dump") else a for a in call.actions]
        checks = list(call.pending_safety_checks or [])
        print(f"step {step:2d} {dt:5.1f}s  {json.dumps(actions)[:160]}"
              + (f"  SAFETY: {[c.code for c in checks]}" if checks else ""))
        steps.append({"step": step, "model_secs": round(dt, 2), "actions": actions,
                      "safety_checks": [c.model_dump() for c in checks], "url": driver.url})
        if checks and not args.approve_checks:
            final = f"stopped: safety check {[c.code for c in checks]}"
            break
        acked = [c.model_dump() for c in checks]
        driver.execute(actions)
        png = driver.screenshot()
        (OUT / f"step_{step:02d}.png").write_bytes(png)
        t0 = time.perf_counter()
        resp = client.responses.create(
            model=args.model, previous_response_id=resp.id, tools=[{"type": "computer"}],
            input=[{"type": "computer_call_output", "call_id": call.call_id,
                    "acknowledged_safety_checks": acked,
                    "output": {"type": "computer_screenshot", "image_url": data_url(png)}}],
        )
        track(resp)
    driver.close()

    price_in, price_out, price_cached = {"muse-wonder-1.0": (4.0, 20.0, 0.2)}.get(
        args.model, (1.25, 4.25, 0.15))
    cost = ((usage["input"] - usage["cached"]) * price_in + usage["cached"] * price_cached
            + usage["output"] * price_out) / 1e6
    summary = {"model": args.model, "task": args.task, "final": final, "steps": len(steps),
               "usage": usage, "est_cost_usd": round(cost, 4)}
    (OUT / "summary.json").write_text(json.dumps({"summary": summary, "steps": steps}, indent=2))
    print("\nFINAL:", final)
    print("usage:", usage, f"est ${cost:.4f}")


if __name__ == "__main__":
    main()
