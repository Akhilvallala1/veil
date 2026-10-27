"""Private SAM backend: sends pixels only to our endpoint, with our key, and maps boxes."""

from __future__ import annotations

import asyncio
import json

import httpx

from veil.detect.sam import SamClient
from veil.frame import detect_visual


def test_private_sam_calls_only_our_endpoint(monkeypatch):
    monkeypatch.setenv("VEIL_SAM_URL", "https://sam.private.example")
    monkeypatch.setenv("VEIL_SAM_KEY", "sk_test")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)  # must not need the Meta key
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = json.loads(req.content)
        assert body["concepts"] == ["face", "id card"]
        return httpx.Response(200, json={"seconds": 0.1, "regions": {
            "face": [{"box": [10, 20, 60, 90], "score": 0.9}], "id card": []}})

    sam = SamClient(backend="private")
    sam.http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers=sam.http.headers)
    dets = asyncio.run(detect_visual(b"\x89PNG fake", sam, ("face", "id card")))

    assert len(seen) == 1, "one request per frame, all concepts together"
    assert seen[0].url.host == "sam.private.example"
    assert seen[0].headers["authorization"] == "Bearer sk_test"
    assert [(d.entity, d.box, d.source) for d in dets] == [("face", (10, 20, 60, 90), "sam")]


def test_private_sam_error_propagates(monkeypatch):
    """A failing SAM endpoint must raise (frame pipeline then fails closed), not return []."""
    monkeypatch.setenv("VEIL_SAM_URL", "https://sam.private.example")
    monkeypatch.setenv("VEIL_SAM_KEY", "sk_test")
    sam = SamClient(backend="private")
    sam.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text="boom")))
    try:
        asyncio.run(detect_visual(b"x", sam, ("face",)))
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected an error")
