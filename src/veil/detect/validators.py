"""L1 detectors: deterministic validators for structured identifiers.

Fast, explainable, and free. Each finding is a (start, end, entity) span inside
a single OCR line. Checksums (Luhn) cut false positives on card numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Span:
    start: int
    end: int
    entity: str
    source: str  # which detector found it
    value: str


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


PATTERNS: list[tuple[str, re.Pattern, callable | None]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), None),
    ("ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b"), None),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,19}\b"),
     lambda m: luhn_ok(re.sub(r"\D", "", m.group()))),
    ("phone", re.compile(r"(?:\+?1[ .-]?)?\(?\b\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), None),
    ("date", re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b"), None),
    # Payer- and org-specific identifiers (member IDs, claim numbers, group numbers) are
    # deliberately NOT here: their formats vary by organization, and matching our synthetic
    # formats would inflate VeilBench. They are found by label context and Glimmer instead.
    ("secret", re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[bap])[-_][A-Za-z0-9_-]{16,}\b"), None),
]


def find_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for entity, pat, check in PATTERNS:
        for m in pat.finditer(text):
            if check and not check(m):
                continue
            if any(s.start < m.end() and m.start() < s.end for s in spans):
                continue  # earlier, more specific pattern already claimed it
            spans.append(Span(m.start(), m.end(), entity, "validator", m.group()))
    return sorted(spans, key=lambda s: s.start)
