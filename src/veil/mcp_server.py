"""Veil as an MCP server: any MCP agent (Claude Desktop, Cursor, custom frameworks) can
drive a browser through Veil without using the Responses API proxy.

The agent never gets raw pixels or raw values. It gets:
  - veiled screenshots (same pipeline as the proxy: OCR, rules, Glimmer guard if
    configured, SAM if configured, egress re-check, fail closed),
  - tokens like NAME_1 it can type as {{NAME_1}}, rehydrated locally only if the
    flow policy allows that value into that app,
  - a human approval (MCP elicitation) before Send / Save address / Pay / ... .
    If the client can't ask its human, the action is refused: Veil fails closed.
Every step goes to the same hash-chained audit log.

Run (stdio):  uv run veil-mcp
Client config: {"command": "uv", "args": ["--directory", "<repo>", "run", "veil-mcp"]}
Env: same as the proxy (VEIL_GUARD_URL, VEIL_SAM, VEIL_POLICY).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable

from veil.audit import AuditLog, verify
from veil.firewall import Policy, check_action
from veil.frame import blank_frame, gate_hint, snap_to_link, veil_frame
from veil.vault import Vault

try:  # optional: only the MCP entry point needs it
    from mcp.server.mcpserver import Context, Image, MCPServer
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    Context = Image = MCPServer = BaseModel = None  # type: ignore[assignment,misc]

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = {"click", "double_click", "type", "keypress", "scroll", "move", "drag", "wait"}

Approver = Callable[[str], Awaitable[bool]]


class VeilBrowser:
    """One private browser session behind Veil. Model-free core, tested directly."""

    def __init__(self, driver_factory=None, policy: Policy | None = None,
                 run_dir: Path | None = None, guard=None, sam=None) -> None:
        self.policy = policy or Policy(os.environ.get("VEIL_POLICY",
                                                      ROOT / "policy" / "default.yaml"))
        self.vault = Vault()
        self.id = f"m_{uuid.uuid4().hex[:10]}"
        self.audit = AuditLog((run_dir or Path(os.environ.get("VEIL_RUN_DIR", "runs/mcp")))
                              / self.id / "audit.jsonl")
        self.guard, self.sam = guard, sam
        self._factory = driver_factory
        self._driver = None
        self._lines: list = []
        self._dets: list = []
        self._last_click = ""
        # Playwright's sync API must stay on one thread, away from the event loop.
        self._pool = ThreadPoolExecutor(max_workers=1)

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *a)

    @property
    def url(self) -> str:
        return self._driver.url if self._driver else ""

    async def open(self, url: str) -> tuple[bytes, str]:
        if self._driver is None:
            factory = self._factory
            if factory is None:
                from veil.driver.browser import BrowserDriver
                factory = BrowserDriver
            self._driver = await self._run(factory, url)
        else:
            await self._run(self._driver.page.goto, url)
        self.audit.append("open", url=url)
        return await self.screenshot()

    async def screenshot(self) -> tuple[bytes, str]:
        if self._driver is None:
            raise RuntimeError("no page open; call open(url) first")
        raw = await self._run(self._driver.screenshot)
        origin = self.policy.app_for(self.url)
        r = await veil_frame(raw, self.vault, origin=origin, sam=self.sam, guard=self.guard)
        if r.blocked:
            self.audit.append("frame", blocked=True, egress_hits=r.egress_hits)
            return (blank_frame(r.width, r.height, "Frame withheld by Veil (privacy check "
                                "failed). Take a new screenshot."), "Frame withheld by Veil.")
        self._lines, self._dets = r.lines, r.detections
        self.audit.append("frame", blocked=False, app=origin,
                          detections=[{"entity": d.entity, "token": d.token, "source": d.source}
                                      for d in r.detections])
        tokens = sorted({d.token for d in r.detections if d.token})
        note = (f"App: {origin}. Masked on screen: {', '.join(tokens) or 'nothing'}. "
                "Type a masked value as {{TOKEN}}; Veil fills it in locally if policy allows.")
        return r.png, note

    async def act(self, actions: list[dict], approve: Approver) -> list[dict]:
        """Run actions in order through the firewall. Stops at the first deny or decline."""
        if self._driver is None:
            raise RuntimeError("no page open; call open(url) first")
        results = []
        for a in actions:
            a = dict(a)
            if a.get("type") not in ACTIONS:
                results.append({"action": a.get("type"), "decision": "deny",
                                "reason": f"unsupported action {a.get('type')!r}"})
                break
            if a.get("type") in ("click", "double_click") and a.get("x") is not None:
                snapped = snap_to_link(self._dets, a["x"], a["y"])
                if snapped:
                    a["x"], a["y"] = snapped
            hint = gate_hint(a, self._lines, self._last_click)
            v = check_action(a, self.vault, self.policy, self.url, focus_hint=hint)
            if a.get("type") in ("click", "double_click"):
                self._last_click = hint
            decision = v.decision
            if decision == "ask":
                decision = "allow" if await approve(f"Veil: {v.reason}. Allow the agent to do "
                                                    "this?") else "deny"
                reason = f"{v.reason}; human {'approved' if decision == 'allow' else 'declined'}"
            else:
                reason = v.reason
            self.audit.append("action", action={k: a.get(k) for k in ("type", "x", "y", "keys")},
                              tokens=v.tokens, decision=decision, rule=v.rule, reason=reason)
            results.append({"action": a.get("type"), "decision": decision, "rule": v.rule,
                            "reason": reason})
            if decision != "allow":
                break
            await self._run(self._driver.execute, [v.rewritten or a])
        return results

    def audit_status(self) -> dict:
        ok, msg = verify(self.audit.path)
        return {"session": self.id, "chain_ok": ok, "detail": msg, "head": self.audit.head,
                "vault": [{"token": e.token, "entity": e.entity, "origins": sorted(e.origins)}
                          for e in self.vault.entries()]}

    def close(self) -> None:
        if self._driver is not None:
            self._pool.submit(self._driver.close).result()
            self._driver = None


if BaseModel is not None:
    class _Approval(BaseModel):
        approve: bool


def build_server():
    server = MCPServer(
        name="veil", title="Veil privacy firewall",
        instructions=("Drive a web browser through Veil. Screenshots are veiled: personal data "
                      "is replaced by tokens such as NAME_1. To type a masked value, type "
                      "{{NAME_1}}. Sending, saving contact details, paying and similar actions "
                      "need the user's approval, and may be refused by policy."))
    state: dict[str, Any] = {}

    def browser() -> VeilBrowser:
        if "b" not in state:
            guard = sam = None
            if os.environ.get("VEIL_GUARD_URL"):
                from veil.detect.guard import Guard
                guard = Guard()
            if os.environ.get("VEIL_SAM") in ("api", "private"):
                from veil.detect.sam import SamClient
                sam = SamClient(backend=os.environ["VEIL_SAM"])
            state["b"] = VeilBrowser(guard=guard, sam=sam)
        return state["b"]

    async def ask_human(ctx: Context, message: str) -> bool:
        try:
            res = await ctx.elicit(message, _Approval)
        except Exception:  # client can't ask its human: fail closed
            return False
        return getattr(res, "action", "") == "accept" and bool(getattr(res.data, "approve", False))

    @server.tool(description="Open a URL in Veil's private browser. Returns a veiled screenshot.")
    async def open_url(url: str) -> list:
        png, note = await browser().open(url)
        return [Image(data=png, format="png"), note]

    @server.tool(description="Take a veiled screenshot of the current page.")
    async def screenshot() -> list:
        png, note = await browser().screenshot()
        return [Image(data=png, format="png"), note]

    @server.tool(description=(
        "Run computer actions in order, then return a veiled screenshot. Each action is a "
        "dict like {'type':'click','x':120,'y':300}, {'type':'type','text':'{{NAME_1}}'}, "
        "{'type':'keypress','keys':['Enter']}, {'type':'scroll','x':640,'y':400,'scroll_y':300}. "
        "Stops at the first action Veil denies or the user declines."))
    async def act(actions: list[dict], ctx: Context) -> list:
        b = browser()
        results = await b.act(actions, lambda m: ask_human(ctx, m))
        png, note = await b.screenshot()
        lines = [f"{r['action']}: {r['decision']} ({r['reason']})" for r in results]
        return [Image(data=png, format="png"), "\n".join(lines) + "\n" + note]

    @server.tool(description="Veil's audit status: hash chain check and token list (no values).")
    async def audit() -> dict:
        return browser().audit_status()

    return server


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    build_server().run("stdio")


if __name__ == "__main__":
    main()
