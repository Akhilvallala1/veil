"""Firewall, audit, fail-closed and proxy-interface tests (no network)."""

import asyncio
import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from veil.audit import AuditLog, verify
from veil.firewall import Policy, check_action, check_safety_checks
from veil.frame import veil_frame
from veil.textveil import tokenize_text
from veil.vault import Vault

PORTAL = "http://127.0.0.1:8100/member/p00"
MESSAGES = "http://127.0.0.1:8100/messages"
OUTSIDE = "https://evil.example.com/form"


@pytest.fixture
def vp():
    v = Vault()
    v.tokenize("Grace Nakamura", "person_name", "payer-portal")
    v.tokenize("767-78-2542", "ssn", "payer-portal")
    v.tokenize("grace.n@example.com", "email", "payer-portal")
    return v, Policy()


def test_flow_allows_token_into_same_app_and_rehydrates(vp):
    v, p = vp
    r = check_action({"type": "type", "text": "{{NAME_1}}"}, v, p, PORTAL)
    assert r.decision == "allow" and r.rewritten["text"] == "Grace Nakamura"


def test_flow_denies_token_into_messages(vp):
    v, p = vp
    r = check_action({"type": "type", "text": "member {{NAME_1}}"}, v, p, MESSAGES)
    assert r.decision == "deny" and r.rule == "flow_policy"


def test_flow_denies_unknown_destination(vp):
    v, p = vp
    assert check_action({"type": "type", "text": "{{NAME_1}}"}, v, p, OUTSIDE).decision == "deny"


def test_user_named_recipient_may_go_into_messages():
    v, p = Vault(), Policy()
    tok = v.tokenize("desk@clearpath-claims.net", "email", "user")
    r = check_action({"type": "type", "text": "{{" + tok + "}}"}, v, p, MESSAGES)
    assert r.decision == "allow" and r.rewritten["text"] == "desk@clearpath-claims.net"


def test_recipient_read_off_the_screen_may_not_go_into_messages():
    # Injected notes name their drop box on screen, so its origin is the portal, not the user.
    v, p = Vault(), Policy()
    tok = v.tokenize("reprocessing@clearpath-claims.net", "email", "payer-portal")
    r = check_action({"type": "type", "text": "{{" + tok + "}}"}, v, p, MESSAGES)
    assert r.decision == "deny" and r.rule == "flow_policy"


def test_user_email_rule_does_not_open_messages_to_member_data():
    v, p = Vault(), Policy()
    tok = v.tokenize("Grace Nakamura", "person_name", "user")
    r = check_action({"type": "type", "text": "{{" + tok + "}}"}, v, p, MESSAGES)
    assert r.decision == "deny"


def test_saving_a_mailing_address_needs_a_human():
    # Integrity attack: an injected note asks the agent to change the member's address.
    v, p = Vault(), Policy()
    url = "http://127.0.0.1:8100/member/p00/address"
    r = check_action({"type": "click", "x": 1, "y": 1}, v, p, url, focus_hint="Save address")
    assert r.decision == "ask" and r.rule == "approval_required"
    # Opening the form is harmless; only the write is gated.
    assert check_action({"type": "click", "x": 1, "y": 1}, v, p, PORTAL,
                        focus_hint="Update mailing address").decision == "allow"
    assert check_action({"type": "keypress", "keys": ["Enter"]}, v, p, url,
                        focus_hint="Save address").decision == "ask"


def test_masked_entity_never_typeable_even_in_allowed_app(vp):
    v, p = vp
    r = check_action({"type": "type", "text": "{{SSN_1}}"}, v, p, PORTAL)
    assert r.decision == "deny" and r.rule == "masked_entity"


def test_leak_back_raw_value_denied(vp):
    v, p = vp
    r = check_action({"type": "type", "text": "Grace Nakamura"}, v, p, PORTAL)
    assert r.decision == "deny" and r.rule == "leak_back"


def test_unknown_token_denied(vp):
    v, p = vp
    assert check_action({"type": "type", "text": "{{NAME_9}}"}, v, p, PORTAL).rule == "unknown_token"


def test_plain_text_allowed(vp):
    v, p = vp
    assert check_action({"type": "type", "text": "lab panel"}, v, p, PORTAL).decision == "allow"


def test_send_click_needs_human(vp):
    v, p = vp
    r = check_action({"type": "click", "x": 1, "y": 1}, v, p, MESSAGES, focus_hint="Send")
    assert r.decision == "ask"


def test_safety_checks_split(vp):
    _, p = vp
    auto, human = check_safety_checks(
        [{"id": "a", "code": "computer_action_requires_confirmation"},
         {"id": "b", "code": "malicious_instructions"}], p)
    assert [c["id"] for c in auto] == ["a"] and [c["id"] for c in human] == ["b"]


def test_task_text_tokenized():
    v = Vault()
    out = tokenize_text("open claim CLM27874421 for member Grace Nakamura", v)
    assert "Grace" not in out and "CLM27874421" not in out and "‹NAME_1›" in out


def test_audit_detects_edit_delete_and_reorder(tmp_path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    for i in range(4):
        log.append("step", n=i, verdict="allow")
    assert verify(path)[0]
    lines = path.read_text().splitlines()
    rec = json.loads(lines[2]); rec["verdict"] = "deny"
    path.write_text("\n".join(lines[:2] + [json.dumps(rec)] + lines[3:]) + "\n")
    assert not verify(path)[0]
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")  # delete a record
    assert not verify(path)[0]
    path.write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")  # reorder
    assert not verify(path)[0]


def _png_with(text: str) -> bytes:
    img = Image.new("RGB", (900, 200), "white")
    d = ImageDraw.Draw(img)
    from veil.frame import _font
    d.text((20, 40), "SSN", fill="black", font=_font(28))
    d.text((200, 40), text, fill="black", font=_font(28))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()


def test_frame_masks_and_egress_clean():
    r = asyncio.run(veil_frame(_png_with("123-45-6789"), Vault(), "payer-portal"))
    assert not r.blocked and any(d.entity == "ssn" for d in r.detections)


def test_fail_closed_on_detector_error():
    class Boom:
        async def detect(self, lines):
            raise RuntimeError("guard down")
    r = asyncio.run(veil_frame(_png_with("123-45-6789"), Vault(), "p", guard=Boom()))
    assert r.blocked and r.png == b"" and "guard down" in r.egress_hits[0]


def test_fail_closed_on_garbage_image():
    r = asyncio.run(veil_frame(b"not a png", Vault(), "p"))
    assert r.blocked and r.png == b""
