"""Hash-chained audit log: tamper-evident record of what crossed the boundary.

Each record stores hashes of the raw and veiled frames (never the raw pixels),
detections as types and boxes (never values), actions and verdicts, and the
SHA-256 of the previous record. Editing, inserting, or deleting any line breaks
the chain, which `verify()` reports.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

GENESIS = "0" * 64


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._prev = GENESIS
        self._seq = 0
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._prev, self._seq = r["hash"], r["seq"] + 1

    def append(self, kind: str, **data) -> dict:
        with self._lock:
            rec = {"seq": self._seq, "ts": round(time.time(), 3), "kind": kind, "prev": self._prev,
                   **data}
            rec["hash"] = sha256(_canon(rec))
            with self.path.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._prev, self._seq = rec["hash"], self._seq + 1
            return rec

    @property
    def head(self) -> str:
        return self._prev


def verify(path: Path | str) -> tuple[bool, str]:
    prev, n = GENESIS, 0
    for i, line in enumerate(Path(path).read_text().splitlines()):
        if not line.strip():
            continue
        rec = json.loads(line)
        claimed = rec.pop("hash", None)
        if rec.get("seq") != i:
            return False, f"record {i}: sequence is {rec.get('seq')}, expected {i}"
        if rec.get("prev") != prev:
            return False, f"record {i}: chain broken (prev hash mismatch)"
        if sha256(_canon(rec)) != claimed:
            return False, f"record {i}: contents were modified"
        prev, n = claimed, i + 1
    return True, f"{n} records, chain intact, head {prev[:16]}"
