"""Session state for the proxy: one vault per agent conversation.

Sessions are keyed by the client's X-Veil-Session header if present, otherwise
chained through previous_response_id so a normal Responses loop stays in one
session without any client change.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from veil.vault import Vault


@dataclass
class Session:
    id: str
    vault: Vault = field(default_factory=Vault)
    steps: list[dict] = field(default_factory=list)
    last_frame_size: tuple[int, int] = (1280, 800)
    origin: str = "app"


class Sessions:
    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}
        self._by_response: dict[str, str] = {}
        self._lock = threading.Lock()

    def resolve(self, header_id: str | None, previous_response_id: str | None) -> Session:
        with self._lock:
            sid = header_id or (self._by_response.get(previous_response_id or "") or None)
            if sid is None:
                sid = f"s_{uuid.uuid4().hex[:10]}"
            if sid not in self._by_id:
                self._by_id[sid] = Session(sid)
            return self._by_id[sid]

    def bind_response(self, response_id: str, session: Session) -> None:
        with self._lock:
            self._by_response[response_id] = session.id

    def get(self, sid: str) -> Session | None:
        return self._by_id.get(sid)

    def all(self) -> list[Session]:
        return list(self._by_id.values())
