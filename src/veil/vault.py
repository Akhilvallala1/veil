"""Token vault: maps sensitive values to stable tokens for one session.

The same value always gets the same token (across frames and in the task text),
so the agent can still match "the member in my task" to "the row on screen"
without ever seeing the value. Each entry records provenance (where it was
seen) for flow policy. The vault never leaves the private plane.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

ENTITY_PREFIX = {
    "person_name": "NAME", "date_of_birth": "DOB", "date": "DATE", "ssn": "SSN",
    "member_id": "MEMBER_ID", "group_number": "GROUP", "claim_number": "CLAIM",
    "phone": "PHONE", "email": "EMAIL", "address": "ADDRESS", "card_number": "CARD",
    "secret": "SECRET", "id_number": "ID", "face": "FACE", "id_card": "ID_CARD",
}

TOKEN_RE = re.compile(r"\{\{([A-Z_]+_\d+)\}\}|‹([A-Z_]+_\d+)›")


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", value.lower())


@dataclass
class Entry:
    token: str
    entity: str
    value: str
    origins: set[str] = field(default_factory=set)


class Vault:
    def __init__(self) -> None:
        self._by_norm: dict[str, Entry] = {}
        self._by_token: dict[str, Entry] = {}
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def tokenize(self, value: str, entity: str, origin: str = "") -> str:
        key = norm(value)
        with self._lock:
            e = self._by_norm.get(key)
            if e is None:
                prefix = ENTITY_PREFIX.get(entity, entity.upper())
                self._counts[prefix] = self._counts.get(prefix, 0) + 1
                e = Entry(f"{prefix}_{self._counts[prefix]}", entity, value)
                self._by_norm[key] = e
                self._by_token[e.token] = e
            if origin:
                e.origins.add(origin)
            return e.token

    def lookup(self, token: str) -> Entry | None:
        return self._by_token.get(token)

    def entries(self) -> list[Entry]:
        return list(self._by_token.values())

    def rehydrate(self, text: str) -> tuple[str, list[Entry]]:
        """Replace {{TOKEN}} / ‹TOKEN› with real values. Returns new text and entries used."""
        used: list[Entry] = []

        def sub(m: re.Match) -> str:
            e = self._by_token.get(m.group(1) or m.group(2))
            if e is None:
                return m.group(0)
            used.append(e)
            return e.value

        return TOKEN_RE.sub(sub, text), used

    def find_raw(self, text: str) -> list[Entry]:
        """Vault values that appear verbatim (normalized) in text: a leak signal."""
        t = norm(text)
        return [e for k, e in self._by_norm.items() if len(k) >= 4 and k in t]
