"""Test-wide guard: the suite is offline. Any real HTTP to a model or GPU fails loudly."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_send = httpx.AsyncClient.send

    async def guarded(self, request, *a, **kw):
        if request.url.host in ("testserver",) or request.url.host.endswith(".example"):
            return await real_send(self, request, *a, **kw)
        if isinstance(getattr(self, "_transport", None), httpx.MockTransport):
            return await real_send(self, request, *a, **kw)
        raise AssertionError(f"test tried to reach the network: {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded)
