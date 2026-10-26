"""Veil proxy: an OpenAI-compatible Responses API endpoint in front of Meta.

Clients change one line (base_url) to route through Veil. Day 1 is pass-through:
every request and response is forwarded unchanged, and each exchange is logged
with which screenshots and computer actions crossed the boundary. Later stages
plug into `outbound()` (veil frames, tokenize text) and `inbound()` (action
firewall, rehydrate tokens).

Run: uv run uvicorn veil.proxy:app --port 8787
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

load_dotenv()
UPSTREAM = os.environ.get("VEIL_UPSTREAM", "https://api.meta.ai/v1")
LOG_DIR = Path(os.environ.get("VEIL_LOG_DIR", "runs/proxy"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Veil proxy")
_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20))


def iter_images(body: dict) -> list[dict]:
    """Every image the request would send upstream: user input_image parts and
    computer_call_output screenshots."""
    found = []
    items = body.get("input")
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "computer_call_output":
            out = item.get("output") or {}
            if out.get("image_url"):
                found.append(out)
        for part in item.get("content") or [] if isinstance(item.get("content"), list) else []:
            if isinstance(part, dict) and part.get("type") == "input_image":
                found.append(part)
    return found


def outbound(body: dict) -> dict:
    """Hook: transform the request before it leaves. Pass-through on day 1."""
    return body


def inbound(body: dict) -> dict:
    """Hook: transform the model response before the client acts on it. Pass-through on day 1."""
    return body


def log_exchange(req: dict, resp: Any, secs: float) -> None:
    images = iter_images(req)
    calls = [o for o in (resp.get("output") or []) if o.get("type") == "computer_call"] \
        if isinstance(resp, dict) else []
    record = {
        "ts": time.time(), "secs": round(secs, 3), "model": req.get("model"),
        "previous_response_id": req.get("previous_response_id"),
        "images_out": len(images),
        "image_bytes_out": sum(len(i.get("image_url", "")) for i in images),
        "computer_actions_in": [a.get("type") for c in calls for a in c.get("actions", [])],
        "usage": resp.get("usage") if isinstance(resp, dict) else None,
    }
    with (LOG_DIR / "exchanges.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def _forward_headers(request: Request) -> dict:
    keep = {"authorization", "content-type", "accept", "openai-beta"}
    h = {k: v for k, v in request.headers.items() if k.lower() in keep}
    if "authorization" not in {k.lower() for k in h} and os.environ.get("MODEL_API_KEY"):
        h["authorization"] = f"Bearer {os.environ['MODEL_API_KEY']}"
    return h


@app.post("/v1/responses")
async def responses(request: Request) -> Response:
    body = await request.json()
    if body.get("stream"):
        return JSONResponse({"error": {"message": "Veil proxy: streaming not supported yet",
                                       "type": "invalid_request_error"}}, status_code=400)
    t0 = time.perf_counter()
    up = await _client.post(f"{UPSTREAM}/responses", json=outbound(body),
                            headers=_forward_headers(request))
    secs = time.perf_counter() - t0
    try:
        data = up.json()
    except ValueError:
        return Response(up.content, status_code=up.status_code,
                        media_type=up.headers.get("content-type"))
    if up.status_code == 200:
        data = inbound(data)
    log_exchange(body, data, secs)
    return JSONResponse(data, status_code=up.status_code)


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE"])
async def passthrough(path: str, request: Request) -> Response:
    """Everything else (models, files, ...) goes straight through."""
    up = await _client.request(request.method, f"{UPSTREAM}/{path}",
                               content=await request.body(), headers=_forward_headers(request),
                               params=request.query_params)
    return Response(up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type"))


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "upstream": UPSTREAM}
