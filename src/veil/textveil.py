"""Text side of the veil: tokenize outbound text, rehydrate inbound actions.

Outbound user text (the task) is scanned with the same validators plus a small
set of name cues, and known vault values are replaced with ‹TOKEN›. Inbound
`type` actions have {{TOKEN}} / ‹TOKEN› replaced with the real value on the
private side, after the firewall approves the flow.
"""

from __future__ import annotations

import re

from veil.detect.validators import find_spans
from veil.vault import Vault

# Phrases in a task that introduce a person: "member Grace Nakamura", "patient John Doe".
NAME_CUE = re.compile(
    r"\b(?:member|patient|subscriber|customer|client|insured|for|named)\s+"
    r"((?:[A-Z][a-z'\-]+)(?:\s+[A-Z][a-z'\-]+){1,2})")

TOKEN_GUIDE = (
    "PRIVACY LAYER: Sensitive values on screen and in the task are replaced by tokens such as "
    "‹NAME_1› or ‹MEMBER_ID_2›, shown as dark boxes with yellow labels. The same token always "
    "means the same value. To type a sensitive value, type its token in double braces, for "
    "example {{NAME_1}}; it is filled in locally. Never guess or reconstruct hidden values. "
    "In answers, refer to people and identifiers by their tokens.")


def tokenize_text(text: str, vault: Vault, origin: str = "user") -> str:
    spans = []
    for m in NAME_CUE.finditer(text):
        spans.append((m.start(1), m.end(1), "person_name"))
    for s in find_spans(text):
        if s.entity != "date":
            spans.append((s.start, s.end, s.entity))
    out, last = [], 0
    for start, end, ent in sorted(spans):
        if start < last:
            continue
        out.append(text[last:start])
        out.append(f"‹{vault.tokenize(text[start:end], ent, origin)}›")
        last = end
    out.append(text[last:])
    result = "".join(out)
    # Values already in the vault (from frames) wherever they reappear.
    for e in sorted(vault.entries(), key=lambda e: -len(e.value)):
        if len(e.value) >= 4:
            result = re.sub(re.escape(e.value), f"‹{e.token}›", result, flags=re.I)
    return result


def detokenize_for_display(text: str, vault: Vault) -> str:
    return vault.rehydrate(text)[0]
