"""Layout context detector: values identified by the label next to them.

On forms and record screens, the label says what a value is ("Member ID",
"DOB", "SSN"). This finds label lines and claims the value on the same row to
their right, or directly below. Labels are generic back-office vocabulary, not
tied to any one app's formats. This is the deterministic half of L2; Muse
Glimmer adds open-ended semantic detection on top.
"""

from __future__ import annotations

import re

from veil.detect.validators import Span
from veil.perceive import Line

LABELS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(member|patient|subscriber|insured|customer|account holder|full)?\s*name:?$", re.I),
     "person_name"),
    (re.compile(r"^member:?$|^patient:?$|^subscriber:?$", re.I), "person_name"),
    (re.compile(r"^(date of birth|dob|birth ?date):?$", re.I), "date_of_birth"),
    (re.compile(r"^(ssn|social security( number| no\.?)?|sin|tax id|tin):?$", re.I), "ssn"),
    (re.compile(r"^(member|subscriber|patient|policy|insurance|account|mrn|medical record)"
                r"\s*(id|#|no\.?|number)?:?$", re.I), "member_id"),
    (re.compile(r"^(group|group number|group no\.?|group #):?$", re.I), "group_number"),
    (re.compile(r"^(claim|claim number|claim #|claim no\.?|reference|case)\s*(#|no\.?|number)?:?$",
                re.I), "claim_number"),
    (re.compile(r"^(phone|telephone|mobile|cell|fax)( number)?:?$", re.I), "phone"),
    (re.compile(r"^(e-?mail|email address)i?:?$", re.I), "email"),
    (re.compile(r"^(address|mailing address|home address|street)i?:?$", re.I), "address"),
    (re.compile(r"^(card|card number|account number|routing number|iban)( #)?:?$", re.I),
     "card_number"),
]

# Table headers whose column cells are sensitive.
COLUMN_HEADERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(member|patient|name|subscriber)$", re.I), "person_name"),
    (re.compile(r"^(member id|patient id|mrn|account)$", re.I), "member_id"),
    (re.compile(r"^(date of birth|dob)$", re.I), "date_of_birth"),
    (re.compile(r"^(claim #|claim number|claim)$", re.I), "claim_number"),
    (re.compile(r"^(ssn)$", re.I), "ssn"),
    (re.compile(r"^(to|from|recipient)$", re.I), "email"),
]


def _label_entity(text: str, table: list) -> str | None:
    t = text.strip()
    for pat, ent in table:
        if pat.match(t):
            return ent
    return None


def _vcenter(b) -> float:
    return (b[1] + b[3]) / 2


def label_spans(lines: list[Line]) -> dict[int, list[Span]]:
    """Return {line_index: [Span covering the whole value line]}."""
    out: dict[int, list[Span]] = {}

    def claim(j: int, ent: str) -> None:
        txt = lines[j].text
        if _is_label(txt):
            return  # never mask another label or header
        out.setdefault(j, []).append(Span(0, len(txt), ent, "label", txt))

    for i, lab in enumerate(lines):
        # "Label: value" inside one OCR line.
        if ":" in lab.text:
            head, _, tail = lab.text.partition(":")
            ent = _label_entity(head + ":", LABELS)
            if ent and tail.strip():
                start = len(head) + 1 + (len(tail) - len(tail.lstrip()))
                out.setdefault(i, []).append(Span(start, len(lab.text), ent, "label", tail.strip()))
                continue
        ent = _label_entity(lab.text, LABELS)
        col = _label_entity(lab.text, COLUMN_HEADERS)
        h = lab.box[3] - lab.box[1]
        same_row = [j for j, ln in enumerate(lines)
                    if j != i and abs(_vcenter(ln.box) - _vcenter(lab.box)) < h * 0.6]
        right = [j for j in same_row if lines[j].box[0] > lab.box[2] - 2]
        nearest_right = min(right, key=lambda j: lines[j].box[0]) if right else None
        # A table header row: the cell immediately to the right is another header, so there is
        # no value between them. In a key/value grid the right neighbor is the value instead.
        in_header_row = nearest_right is not None and _is_label(lines[nearest_right].text)
        if (col or ent) and in_header_row:
            col_ent = col or ent
            x1 = lab.box[0]
            cells = sorted((j for j, ln in enumerate(lines)
                            if j != i and ln.box[1] > lab.box[3] and abs(ln.box[0] - x1) < max(h, 12)),
                           key=lambda j: lines[j].box[1])
            prev_y, pitch, x_ref = lab.box[1], None, None
            for j in cells:
                gap = lines[j].box[1] - prev_y
                # Rows are evenly spaced. Allow up to two missing rows (OCR can skip a cell);
                # a gap that is not close to a whole number of rows ends the table.
                if pitch is not None:
                    rows = gap / pitch
                    if rows > 3.3 or abs(rows - round(rows)) > 0.3:
                        break
                if x_ref is not None and abs(lines[j].box[0] - x_ref) > max(h * 0.6, 6):
                    break  # different left edge: not this column any more
                # A table row has other cells beside it; a lone line below the table does not.
                cj = _vcenter(lines[j].box)
                hj = lines[j].box[3] - lines[j].box[1]
                if not any(k != j and abs(_vcenter(lines[k].box) - cj) < hj * 0.6
                           for k in range(len(lines))):
                    break
                if pitch is None:
                    pitch = max(gap, h)
                    x_ref = lines[j].box[0]
                claim(j, col_ent)
                prev_y = lines[j].box[1]
            continue
        if ent:
            if nearest_right is not None:
                claim(nearest_right, ent)
                continue
            below = [j for j, ln in enumerate(lines)
                     if j != i and 0 < ln.box[1] - lab.box[3] < h * 1.8
                     and abs(ln.box[0] - lab.box[0]) < h * 2]
            if below:
                claim(min(below, key=lambda j: lines[j].box[1]), ent)
    return out


def _is_label(text: str) -> bool:
    if _label_entity(text, LABELS) or _label_entity(text, COLUMN_HEADERS):
        return True
    return text.strip().lower().rstrip(":") in GENERIC_HEADERS


# Common non-sensitive table headers; seeing one next to a label marks a header row.
GENERIC_HEADERS = {"plan", "status", "billed", "amount", "service date", "procedure", "date",
                   "message", "subject", "type", "description", "total", "balance", "coverage"}
