"""Proxy public interface over HTTP, with a recorded fake upstream (no network).

Exercises the exact path a client uses: POST /v1/responses with a screenshot,
then checks what the upstream received (veiled, tokenized) and what the client
got back (actions rewritten by the firewall).
"""

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from veil.frame import _font


def _png() -> str:
    img = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 40), "Name", fill="black", font=_font(26))
    d.text((240, 40), "Grace Nakamura", fill="black", font=_font(26))
    d.text((20, 110), "SSN", fill="black", font=_font(26))
    d.text((240, 110), "767-78-2542", fill="black", font=_font(26))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("VEIL_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("VEIL_GUARD", "0")
    # Tests must not reach the private GPUs: the proxy loads .env on import, which may
    # enable private SAM. setenv("") wins over load_dotenv (it does not override).
    monkeypatch.setenv("VEIL_SAM", "")
    import importlib
    import veil.proxy as px
    px = importlib.reload(px)
    seen = []
    replies = []

    async def fake_post(url, json=None, headers=None):
        seen.append(json)
        return httpx.Response(200, json=replies.pop(0))
    monkeypatch.setattr(px._client, "post", fake_post)
    return px, seen, replies


def _call(i, actions, checks=()):
    return {"id": f"resp_{i}", "output": [{"type": "computer_call", "call_id": f"c{i}",
            "actions": actions, "pending_safety_checks": list(checks), "status": "completed"}],
            "usage": {"input_tokens": 100, "output_tokens": 10}}


def test_outbound_veiled_and_inbound_firewalled(proxy):
    px, seen, replies = proxy
    c = TestClient(px.app)
    replies.append(_call(1, [{"type": "type", "text": "{{NAME_1}}"}],
                         [{"id": "s1", "code": "computer_action_requires_confirmation",
                           "message": "m"}]))
    r = c.post("/v1/responses", headers={"X-Veil-Context": "http://127.0.0.1:8100/"},
               json={"model": "muse-spark-1.3", "tools": [{"type": "computer"}], "input": [
                   {"role": "user", "content": [
                       {"type": "input_text", "text": "Find member Grace Nakamura"},
                       {"type": "input_image", "image_url": _png()}]}]})
    assert r.status_code == 200
    sent = json.dumps(seen[0], ensure_ascii=False)
    assert "Grace Nakamura" not in sent and "767-78-2542" not in sent  # text side
    assert "‹NAME_1›" in sent and "PRIVACY LAYER" in sent
    # Pixel side: OCR the exact image the upstream received.
    from veil.perceive import ocr
    from veil.vault import norm
    img_url = seen[0]["input"][0]["content"][1]["image_url"]
    upstream_text = norm(" ".join(l.text for l in ocr(base64.b64decode(img_url.split(",")[1]))[0]))
    assert "gracenakamura" not in upstream_text and "767782542" not in upstream_text
    assert "name" in upstream_text  # labels survive, only values are veiled
    out = r.json()["output"][0]
    assert out["actions"][0]["text"] == "Grace Nakamura"  # rehydrated for the private side
    assert out["pending_safety_checks"] == []  # generic check auto-acknowledged by policy
    sid = r.headers["x-veil-session"]

    # Next turn: hijacked model tries to type the name into Messages.
    replies.append(_call(2, [{"type": "type", "text": "{{NAME_1}}"},
                             {"type": "keypress", "keys": ["Enter"]}]))
    r2 = c.post("/v1/responses", headers={"X-Veil-Context": "http://127.0.0.1:8100/messages",
                                          "X-Veil-Session": sid},
                json={"model": "muse-spark-1.3", "previous_response_id": "resp_1",
                      "tools": [{"type": "computer"}], "input": [
                          {"type": "computer_call_output", "call_id": "c1",
                           "acknowledged_safety_checks": [],
                           "output": {"type": "computer_screenshot", "image_url": _png()}}]})
    # The auto-ack from turn 1 was attached for Meta:
    assert seen[1]["input"][0]["acknowledged_safety_checks"][0]["id"] == "s1"
    assert r2.json()["output"][0]["actions"] == [{"type": "screenshot"}]  # denied, dropped
    detail = c.get(f"/veil/sessions/{sid}").json()
    assert detail["audit"]["ok"]
    v = detail["steps"][1]["verdicts"][0]
    assert v["decision"] == "deny" and v["rule"] == "flow_policy"


def test_remote_image_withheld_and_bypass_routes_blocked(proxy):
    px, seen, replies = proxy
    c = TestClient(px.app)
    replies.append({"id": "r", "output": [], "usage": {}})
    c.post("/v1/responses", json={"model": "m", "input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "https://example.com/x.png"}]}]})
    assert "example.com" not in json.dumps(seen[0])
    assert c.post("/v1/chat/completions", json={}).status_code == 403
    assert c.post("/v1/responses", json={"model": "m", "input": "x", "stream": True}).status_code == 400


def test_click_on_send_escalates_to_human(proxy):
    px, seen, replies = proxy
    c = TestClient(px.app)
    img = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(img).text((40, 60), "Send", fill="black", font=_font(30))
    buf = io.BytesIO(); img.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    replies.append(_call(1, [{"type": "click", "button": "left", "x": 70, "y": 76}]))
    r = c.post("/v1/responses", headers={"X-Veil-Context": "http://127.0.0.1:8100/messages"},
               json={"model": "m", "tools": [{"type": "computer"}], "input": [
                   {"role": "user", "content": [{"type": "input_text", "text": "reply"},
                                                {"type": "input_image", "image_url": url}]}]})
    checks = r.json()["output"][0]["pending_safety_checks"]
    assert any(c["code"] == "veil_approval_required" and "Send" in c["message"] for c in checks)


def test_budget_cap_stops_calls(proxy, monkeypatch):
    px, seen, replies = proxy
    monkeypatch.setattr(px, "BUDGET_USD", 0.0)
    r = TestClient(px.app).post("/v1/responses", json={"model": "m", "input": "x"})
    assert r.status_code == 429 and not seen
