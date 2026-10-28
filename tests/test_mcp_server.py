"""Veil's MCP server core: real Veil pipeline and firewall, fake browser (offline)."""

from __future__ import annotations

import asyncio
import io

from PIL import Image, ImageDraw, ImageFont

from veil.audit import verify
from veil.mcp_server import VeilBrowser
from veil.perceive import ocr


def _font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _page(lines: list[tuple[str, int, int]]) -> bytes:
    img = Image.new("RGB", (1280, 800), "white")
    d = ImageDraw.Draw(img)
    for text, x, y in lines:
        d.text((x, y), text, fill="black", font=_font(22))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


MEMBER = _page([("Member search", 40, 30), ("Name: Grace Nakamura", 40, 120),
                ("Member ID: HZ162275869", 40, 170), ("DOB: 03/14/1961", 40, 220)])
MESSAGES = _page([("Secure messages", 40, 30), ("To", 40, 120), ("Message", 40, 220),
                  ("Send", 60, 400)])


class FakeDriver:
    def __init__(self, url: str) -> None:
        self.url = url
        self.done: list[dict] = []

    @property
    def page(self):
        drv = self

        class P:
            def goto(self, url):
                drv.url = url
        return P()

    def screenshot(self) -> bytes:
        return MESSAGES if "messages" in self.url else MEMBER

    def execute(self, actions):
        self.done.extend(actions)
        return [a["type"] for a in actions]

    def close(self):
        pass


def _run(coro):
    return asyncio.run(coro)


def test_screenshot_is_veiled_and_tokens_are_listed(tmp_path):
    b = VeilBrowser(driver_factory=FakeDriver, run_dir=tmp_path)
    png, note = _run(b.open("http://127.0.0.1:8100/member/p00"))
    seen = " ".join(l.text for l in ocr(png)[0])
    assert "Nakamura" not in seen and "HZ162275869" not in seen and "1961" not in seen
    assert "NAME_1" in note and "MEMBER_ID_1" in note
    assert verify(b.audit.path)[0]


def test_token_typed_into_portal_is_rehydrated_locally(tmp_path):
    b = VeilBrowser(driver_factory=FakeDriver, run_dir=tmp_path)
    _run(b.open("http://127.0.0.1:8100/member/p00"))
    res = _run(b.act([{"type": "type", "text": "{{NAME_1}}"}], approve=lambda m: _no()))
    assert res[0]["decision"] == "allow"
    assert b._driver.done[-1]["text"] == "Grace Nakamura"  # real value, private side only


async def _no():
    return False


async def _yes():
    return True


def test_member_data_into_messages_is_denied_and_never_typed(tmp_path):
    b = VeilBrowser(driver_factory=FakeDriver, run_dir=tmp_path)
    _run(b.open("http://127.0.0.1:8100/member/p00"))
    _run(b.open("http://127.0.0.1:8100/messages"))
    res = _run(b.act([{"type": "type", "text": "{{MEMBER_ID_1}}"},
                      {"type": "keypress", "keys": ["Enter"]}], approve=lambda m: _yes()))
    assert res[0]["decision"] == "deny" and len(res) == 1  # stops at the deny
    assert b._driver.done == []


def test_send_needs_human_and_decline_blocks(tmp_path):
    b = VeilBrowser(driver_factory=FakeDriver, run_dir=tmp_path)
    _run(b.open("http://127.0.0.1:8100/messages"))
    lines = ocr(MESSAGES)[0]
    send = next(l for l in lines if l.text.strip() == "Send")
    x, y = (send.box[0] + send.box[2]) // 2, (send.box[1] + send.box[3]) // 2
    asked: list[str] = []

    async def human_says_no(msg):
        asked.append(msg)
        return False

    res = _run(b.act([{"type": "click", "x": x, "y": y}], approve=human_says_no))
    assert asked and "Send" in asked[0]
    assert res[0]["decision"] == "deny" and b._driver.done == []

    res = _run(b.act([{"type": "click", "x": x, "y": y}], approve=lambda m: _yes()))
    assert res[0]["decision"] == "allow" and len(b._driver.done) == 1
    status = b.audit_status()
    assert status["chain_ok"] and all("value" not in v for v in status["vault"])


def test_mcp_server_lists_tools():
    from veil.mcp_server import build_server
    names = {t.name for t in _run(build_server().list_tools())}
    assert {"open_url", "screenshot", "act", "audit"} <= names
