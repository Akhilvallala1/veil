from veil.detect.layout import label_spans
from veil.detect.validators import find_spans, luhn_ok
from veil.perceive import Line
from veil.vault import Vault


def test_luhn():
    assert luhn_ok("4539578763621486")
    assert not luhn_ok("4539578763621487")


def test_validators_find_structured_ids():
    kinds = {s.entity for s in find_spans("SSN 123-45-6789 mail a.b@example.com call (239) 288-8104")}
    assert {"ssn", "email", "phone"} <= kinds


def test_validators_reject_bad_card_checksum():
    assert not [s for s in find_spans("ref 4539578763621487") if s.entity == "card_number"]


def test_vault_stable_tokens_and_rehydrate():
    v = Vault()
    t1 = v.tokenize("Grace Nakamura", "person_name", "portal")
    t2 = v.tokenize("grace  nakamura", "person_name", "portal")
    assert t1 == t2 == "NAME_1"
    text, used = v.rehydrate("search {{NAME_1}} now")
    assert text == "search Grace Nakamura now" and used[0].token == "NAME_1"
    assert v.find_raw("hello grace nakamura") and not v.find_raw("hello world")


def _l(text, x1, y1, x2, y2):
    return Line(text, (x1, y1, x2, y2), 1.0)


def test_key_value_grid():
    lines = [_l("Name", 49, 135, 89, 149), _l("Grace Nakamura", 235, 135, 350, 149),
             _l("Plan", 598, 189, 628, 203), _l("Gold HMO 500", 784, 189, 884, 203)]
    spans = label_spans(lines)
    assert spans[1][0].entity == "person_name"
    assert 3 not in spans  # plan is not sensitive


def test_table_column_stops_at_table_end():
    lines = [_l("Claim #", 37, 339, 95, 355), _l("Service date", 271, 339, 360, 355),
             _l("CLM1", 36, 377, 138, 394), _l("05/14/2026", 271, 377, 346, 394),
             _l("CLM2", 36, 417, 138, 434), _l("03/04/2026", 271, 417, 346, 434),
             _l("Update mailing address", 26, 461, 188, 480)]
    spans = label_spans(lines)
    assert set(spans) == {2, 4}
