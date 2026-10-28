"""A line that mixes a value the vault already knows with new values keeps the known
token, so the agent can match the task's person to the screen (live failure, Oct 28)."""

from __future__ import annotations

from veil.frame import detect_text
from veil.perceive import Line
from veil.vault import Vault


def _vault_with_task_name() -> Vault:
    v = Vault()
    v.tokenize("Grace Nakamura", "person_name", "user")  # from the task text
    return v


def test_known_name_keeps_its_token_next_to_new_id():
    v = _vault_with_task_name()
    line = Line("Member: Grace Nakamura · HZ162275869", (49, 134, 400, 150), 1.0)
    dets = detect_text([line], v, "payer-portal")
    tokens = {d.token for d in dets}
    assert "NAME_1" in tokens                      # same token as in the task
    assert not any(t.startswith("NAME_2") for t in tokens if t)
    # The member ID is still masked, as its own value.
    masked = {e.value for e in v.entries()}
    assert "HZ162275869" in masked
    assert not any("Grace Nakamura ·" in m or "Nakamura · HZ" in m for m in masked)


def test_whole_value_line_unchanged():
    # Nothing known inside: behaves exactly as before (one span, one new token).
    v = Vault()
    dets = detect_text([Line("Member: Ravi Okafor", (49, 134, 300, 150), 1.0)], v, "payer-portal")
    assert [d.entity for d in dets] == ["person_name"]


def test_possessive_task_name_matches_screen():
    # The live failure: task said "Grace Nakamura's", screen OCR'd "Grace Nakamura • HZ...".
    from veil.textveil import tokenize_text
    v = Vault()
    assert tokenize_text("update member Grace Nakamura's mailing address", v) == \
        "update member ‹NAME_1›'s mailing address"
    line = Line("Member: Grace Nakamura • HZ162275869", (49, 134, 400, 150), 1.0)
    assert "NAME_1" in {d.token for d in detect_text([line], v, "payer-portal")}
