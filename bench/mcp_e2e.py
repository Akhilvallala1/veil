"""End-to-end check of Veil's MCP server with a real MCP client over stdio.

Spawns `veil-mcp` exactly as Claude Desktop or Cursor would, opens the synthetic portal,
and checks what an MCP agent would receive and be allowed to do. No LLM: the "agent" is
this script, so every step is deterministic. Needs the portal on :8100.

  1. open a member page -> the screenshot it returns has no real values (OCR check)
  2. type {{MEMBER_ID_1}} into Messages -> denied by flow policy, nothing typed
  3. click Send -> Veil asks the human (MCP elicitation); we decline -> 0 messages
  4. audit tool -> hash chain intact, vault lists tokens only

  uv run python bench/mcp_e2e.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
import mcp.types as t
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from veil.perceive import ocr

PORTAL = "http://127.0.0.1:8100"


async def main() -> dict:
    httpx.post(f"{PORTAL}/_bench/reset", data={"notes": "{}"}).raise_for_status()
    people = httpx.get(f"{PORTAL}/_bench/people").json()
    grace = people[0]
    asked: list[str] = []

    async def human(context, params) -> t.ElicitResult:
        asked.append(getattr(params, "message", ""))
        return t.ElicitResult(action="accept", content={"approve": False})

    server = StdioServerParameters(command="uv", args=["run", "veil-mcp"],
                                   env={"VEIL_GUARD": "0", "VEIL_SAM": "", "PATH": "/usr/bin:/bin:"
                                        + str(Path.home() / ".local/bin") + ":/opt/homebrew/bin"})
    out: dict = {}
    async with stdio_client(server) as (r, w), ClientSession(r, w, elicitation_callback=human) as s:
        await s.initialize()
        out["tools"] = sorted(x.name for x in (await s.list_tools()).tools)

        def image_and_text(res):
            png = next(base64.b64decode(c.data) for c in res.content if c.type == "image")
            text = " ".join(c.text for c in res.content if c.type == "text")
            return png, text

        # 1. member page
        png, note = image_and_text(await s.call_tool("open_url", {"url": f"{PORTAL}/member/p00"}))
        seen = " ".join(l.text for l in ocr(png)[0]).lower()
        real = {k: grace[k] for k in ("name", "member_id", "dob") if grace.get(k)}
        out["member_page_values_visible"] = [k for k, v in real.items() if v.lower() in seen]
        out["member_page_note"] = note[:160]

        # 2. member data into Messages
        await s.call_tool("open_url", {"url": f"{PORTAL}/messages"})
        _, txt = image_and_text(await s.call_tool("act", {"actions": [
            {"type": "click", "x": 257, "y": 180}, {"type": "type", "text": "{{MEMBER_ID_1}}"}]}))
        out["type_member_id_into_messages"] = txt.splitlines()[:2]

        # 3. click Send: find it on the veiled frame (labels aren't masked), decline
        png, _ = image_and_text(await s.call_tool("screenshot", {}))
        send = next(l for l in ocr(png)[0] if l.text.strip() == "Send")
        x, y = (send.box[0] + send.box[2]) // 2, (send.box[1] + send.box[3]) // 2
        _, txt = image_and_text(await s.call_tool("act", {"actions": [
            {"type": "click", "x": x, "y": y}]}))
        out["click_send"] = txt.splitlines()[0]
        out["human_was_asked"] = asked

        # 4. audit
        a = await s.call_tool("audit", {})
        out["audit"] = json.loads(next(c.text for c in a.content if c.type == "text"))

    out["messages_in_portal"] = len(httpx.get(f"{PORTAL}/_bench/state").json()["messages"])
    out["pass"] = bool(
        not out["member_page_values_visible"]
        and "deny" in out["type_member_id_into_messages"][-1]
        and asked and "deny" in out["click_send"]
        and out["messages_in_portal"] == 0
        and out["audit"].get("chain_ok")
        and all("value" not in v for v in out["audit"].get("vault", [])))
    return out


if __name__ == "__main__":
    res = asyncio.run(main())
    Path("bench/results/mcp_e2e.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2)[:3000])
    sys.exit(0 if res["pass"] else 1)
