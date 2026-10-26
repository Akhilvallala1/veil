"""Playwright browser driver for the Responses API computer tool.

Maps each documented action (click, double_click, drag, keypress, move, scroll,
type, screenshot, wait) to a real browser event at the pixel coordinates of the
last screenshot, and returns PNG screenshots of the viewport.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, sync_playwright

KEYMAP = {
    "enter": "Enter", "return": "Enter", "tab": "Tab", "esc": "Escape", "escape": "Escape",
    "backspace": "Backspace", "delete": "Delete", "space": " ", "up": "ArrowUp",
    "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight", "home": "Home",
    "end": "End", "pageup": "PageUp", "pagedown": "PageDown", "ctrl": "Control",
    "control": "Control", "cmd": "Meta", "meta": "Meta", "alt": "Alt", "shift": "Shift",
    "super": "Meta", "option": "Alt",
}


def _key(k: str) -> str:
    return KEYMAP.get(k.lower(), k if len(k) > 1 else k)


def _get(action: Any, name: str, default=None):
    if isinstance(action, dict):
        return action.get(name, default)
    return getattr(action, name, default)


class BrowserDriver:
    def __init__(self, start_url: str, width: int = 1280, height: int = 800,
                 headless: bool = True) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self.page: Page = self._browser.new_page(viewport={"width": width, "height": height})
        self.page.goto(start_url)
        self.page.wait_for_load_state("networkidle")

    @property
    def url(self) -> str:
        return self.page.url

    def screenshot(self) -> bytes:
        last: Exception | None = None
        for _ in range(5):
            try:
                self.page.wait_for_load_state("load", timeout=5000)
                return self.page.screenshot(type="png")
            except Exception as e:  # mid-navigation: retry once the page settles
                last = e
                time.sleep(0.4)
        raise RuntimeError(f"screenshot failed after retries: {last}")

    def execute(self, actions: list[Any]) -> list[str]:
        """Run a batch in order. Returns a short log line per action."""
        log = []
        for a in actions:
            t = _get(a, "type")
            m = self.page.mouse
            if t == "click":
                button = {"left": "left", "right": "right", "wheel": "middle"}.get(
                    _get(a, "button", "left"), "left")
                m.click(_get(a, "x"), _get(a, "y"), button=button)
            elif t == "double_click":
                m.dblclick(_get(a, "x"), _get(a, "y"))
            elif t == "drag":
                path = _get(a, "path") or []
                if path:
                    p0 = path[0]
                    m.move(_get(p0, "x"), _get(p0, "y"))
                    m.down()
                    for p in path[1:]:
                        m.move(_get(p, "x"), _get(p, "y"))
                    m.up()
            elif t == "keypress":
                keys = [_key(k) for k in _get(a, "keys", [])]
                self.page.keyboard.press("+".join(keys))
            elif t == "move":
                m.move(_get(a, "x"), _get(a, "y"))
            elif t == "scroll":
                m.move(_get(a, "x", 0), _get(a, "y", 0))
                m.wheel(_get(a, "scroll_x", 0) or 0, _get(a, "scroll_y", 0) or 0)
            elif t == "type":
                self.page.keyboard.type(_get(a, "text", ""))
            elif t == "wait":
                time.sleep(1.0)
            elif t == "screenshot":
                pass
            else:
                log.append(f"unsupported action {t}")
                continue
            log.append(t)
            try:
                self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
        return log

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
