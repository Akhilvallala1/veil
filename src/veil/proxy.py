"""Veil proxy: an OpenAI-compatible Responses API endpoint in front of Meta.

Point any computer-use agent at it by changing base_url. For each request:
  outbound: tokenize text, veil every screenshot, egress-check, fail closed,
            add Veil's token guide to the instructions, attach Veil's own
            safety-check acknowledgements.
  inbound:  run every computer action through the firewall, rehydrate approved
            {{TOKEN}}s for the client (which runs on the private side), drop
            denied actions and tell the model why on the next turn, and turn
            risky actions into standard pending_safety_checks for a human.
Everything is written to a hash-chained audit log.

Optional headers from the client:
  X-Veil-Session: stable session id (otherwise chained via previous_response_id)
  X-Veil-Context: the URL/app the agent is acting in (for flow policy)

Run: uv run uvicorn veil.proxy:app --port 8799
Env: VEIL_SAM=private (open-weight SAM 3.1 on our Modal) or api (Meta API, synthetic only).
"""

from __future__ import annotations

import base64
import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from veil.audit import AuditLog, sha256, verify
from veil.firewall import Policy, check_action, check_safety_checks
from veil.frame import blank_frame, gate_hint, snap_to_link, veil_frame
from veil.session import Session, Sessions
from veil.textveil import TOKEN_GUIDE, tokenize_text

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
UPSTREAM = os.environ.get("VEIL_UPSTREAM", "https://api.meta.ai/v1")
RUN_DIR = Path(os.environ.get("VEIL_RUN_DIR", "runs/proxy"))
RUN_DIR.mkdir(parents=True, exist_ok=True)
BUDGET_USD = float(os.environ.get("VEIL_BUDGET_USD", "100"))
PRICES = {"muse-wonder-1.0": (4.0, 0.2, 20.0)}  # input, cached, output per 1M
DEFAULT_PRICE = (1.25, 0.15, 4.25)

app = FastAPI(title="Veil proxy")
_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20))
SESSIONS = Sessions()
POLICY = Policy(os.environ.get("VEIL_POLICY", Path(__file__).resolve().parents[2] / "policy" / "default.yaml"))
SAM = None
if os.environ.get("VEIL_SAM") in ("api", "private"):
    from veil.detect.sam import SamClient
    SAM = SamClient(backend=os.environ["VEIL_SAM"])
SPEND = {"usd": 0.0}
GUARD_ON = os.environ.get("VEIL_GUARD", "1") == "1"
# Raw OCR text must not go to a hosted API by accident. Without a private guard URL,
# the L2 guard is off (rules only) unless VEIL_GUARD_ALLOW_HOSTED=1 is set explicitly
# (synthetic benchmarks only).
if GUARD_ON and not os.environ.get("VEIL_GUARD_URL") and \
        os.environ.get("VEIL_GUARD_ALLOW_HOSTED") != "1":
    print("veil: no VEIL_GUARD_URL set; L2 guard OFF (rules only). "
          "Deploy infra/modal_glimmer.py for full detection.", flush=True)
    GUARD_ON = False


def _guard(s: Session):
    """One L2 guard per session, so its line cache follows the agent across steps."""
    if not GUARD_ON:
        return None
    if not hasattr(s, "_guard"):
        from veil.detect.guard import Guard
        s._guard = Guard()  # type: ignore[attr-defined]
    return s._guard  # type: ignore[attr-defined]


def _audit(s: Session) -> AuditLog:
    if not hasattr(s, "_audit"):
        s._audit = AuditLog(RUN_DIR / s.id / "audit.jsonl")  # type: ignore[attr-defined]
    return s._audit  # type: ignore[attr-defined]


def _decode(url: str) -> bytes | None:
    if not url.startswith("data:image"):
        return None
    return base64.b64decode(url.split(",", 1)[1])


def _encode(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _cost(model: str, usage: dict | None) -> float:
    if not usage:
        return 0.0
    pin, pcached, pout = PRICES.get(model, DEFAULT_PRICE)
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    inp = (usage.get("input_tokens") or 0) - cached
    return (inp * pin + cached * pcached + (usage.get("output_tokens") or 0) * pout) / 1e6


# ---------------------------------------------------------------- outbound

async def _veil_image(part: dict, key: str, s: Session, step: dict) -> None:
    raw = _decode(part.get(key, ""))
    if raw is None:
        # Remote URL: Veil cannot inspect it, so it never leaves.
        part[key] = _encode(blank_frame(*s.last_frame_size, "Remote image withheld by Veil"))
        step["frames"].append({"blocked": True, "reason": "remote image url"})
        return
    r = await veil_frame(raw, s.vault, origin=s.origin, sam=SAM, guard=_guard(s))
    if r.blocked:
        out = blank_frame(r.width or s.last_frame_size[0], r.height or s.last_frame_size[1],
                          "Frame withheld by Veil (privacy check failed). Request a new screenshot.")
    else:
        out = r.png
        s.last_frame_size = (r.width, r.height)
        s.last_lines = r.lines  # type: ignore[attr-defined]
        s.last_dets = r.detections  # type: ignore[attr-defined]
    part[key] = _encode(out)
    frame_dir = RUN_DIR / s.id / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    n = len(s.steps)
    (frame_dir / f"{n:03d}_veiled.png").write_bytes(out)
    if os.environ.get("VEIL_KEEP_RAW") == "1":  # local demo console only
        (frame_dir / f"{n:03d}_raw.png").write_bytes(raw)
    step["frames"].append({
        "raw_sha256": sha256(raw), "sent_sha256": sha256(out), "blocked": r.blocked,
        "egress_hits": r.egress_hits,
        "detections": [{"entity": d.entity, "source": d.source, "token": d.token, "box": d.box}
                       for d in r.detections],
        "timings": {k: round(v, 3) for k, v in r.timings.items()},
    })


def _tokenize_parts(content: list, s: Session, role: str) -> None:
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
            part["text"] = tokenize_text(part.get("text", ""), s.vault,
                                         origin="user" if role == "user" else s.origin)


async def outbound(body: dict, s: Session, step: dict) -> dict:
    body = copy.deepcopy(body)
    items = body.get("input")
    if isinstance(items, str):
        items = [{"role": "user", "content": [{"type": "input_text", "text": items}]}]
    for item in items or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "computer_call_output":
            out = item.get("output") or {}
            if out.get("image_url"):
                await _veil_image(out, "image_url", s, step)
            acks = [a for a in item.get("acknowledged_safety_checks") or []
                    if not str(a.get("id", "")).startswith("veil_")]
            approved = [a for a in item.get("acknowledged_safety_checks") or []
                        if str(a.get("id", "")).startswith("veil_")]
            if approved:
                step["human_approvals"] = [a.get("message") for a in approved]
            # get, not pop: the OpenAI client retries on 5xx with the same body.
            acks += s.__dict__.setdefault("auto_ack", {}).get(item.get("call_id"), [])
            item["acknowledged_safety_checks"] = acks
        elif t == "computer_call":  # stateless replay: re-veil any rehydrated text
            for a in item.get("actions") or []:
                if a.get("type") == "type":
                    a["text"] = tokenize_text(a.get("text", ""), s.vault, s.origin)
        content = item.get("content")
        if isinstance(content, list):
            _tokenize_parts(content, s, item.get("role", "user"))
            for part in content:
                if isinstance(part, dict) and part.get("type") == "input_image":
                    await _veil_image(part, "image_url", s, step)
        elif isinstance(content, str):
            item["content"] = tokenize_text(content, s.vault, "user")
    notes = s.__dict__.pop("pending_notes", [])
    if notes:
        items = list(items or []) + [{"role": "user", "content": [
            {"type": "input_text", "text": "[Veil] " + " ".join(notes)}]}]
    body["input"] = items
    body["instructions"] = (body.get("instructions", "") + "\n\n" + TOKEN_GUIDE).strip()
    return body


# ---------------------------------------------------------------- inbound

def inbound(data: dict, s: Session, step: dict, dest_url: str) -> dict:
    data = copy.deepcopy(data)
    verdicts = []
    for item in data.get("output") or []:
        if item.get("type") == "computer_call":
            kept, blocked = [], None
            for a in item.get("actions") or []:
                snapped = None
                if a.get("type") in ("click", "double_click") and a.get("x") is not None \
                        and os.environ.get("VEIL_SNAP_LINKS", "1") == "1":
                    snapped = snap_to_link(getattr(s, "last_dets", []), a["x"], a["y"])
                    if snapped:
                        a = {**a, "x": snapped[0], "y": snapped[1]}
                hint = gate_hint(a, getattr(s, "last_lines", []),
                                 getattr(s, "last_click_text", ""))
                v = check_action(a, s.vault, POLICY, dest_url, focus_hint=hint)
                if a.get("type") in ("click", "double_click"):
                    s.last_click_text = hint  # type: ignore[attr-defined]
                verdicts.append({"action": {k: a.get(k) for k in ("type", "x", "y", "keys")},
                                 "typed_tokens": v.tokens, "decision": v.decision,
                                 "rule": v.rule, "reason": v.reason,
                                 **({"snapped_to_link": True} if snapped else {})})
                if v.decision == "deny":
                    blocked = v
                    break  # later actions in the batch depended on this one
                kept.append(v.rewritten or a)
                if v.decision == "ask":
                    item.setdefault("pending_safety_checks", []).append(
                        {"id": f"veil_{len(s.steps)}_{len(kept)}", "code": "veil_approval_required",
                         "message": f"Veil: {v.reason}. Approve?"})
            if blocked:
                s.__dict__.setdefault("pending_notes", []).append(
                    f"An action was blocked by the privacy policy ({blocked.rule}): "
                    f"{blocked.reason}. Do not retry it; continue the task another way or stop.")
            item["actions"] = kept or [{"type": "screenshot"}]
            auto, human = check_safety_checks(
                [c for c in item.get("pending_safety_checks") or []
                 if not str(c.get("id", "")).startswith("veil_")], POLICY)
            veil_checks = [c for c in item.get("pending_safety_checks") or []
                           if str(c.get("id", "")).startswith("veil_")]
            if auto and not blocked:
                s.__dict__.setdefault("auto_ack", {})[item.get("call_id")] = auto
            item["pending_safety_checks"] = human + veil_checks + ([] if not blocked else auto)
            step["safety"] = {"auto_acknowledged": [c.get("code") for c in auto] if not blocked else [],
                              "to_human": [c.get("code") for c in human + veil_checks]}
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    step["model_text_tokenized"] = part.get("text", "")
                    part["text"] = s.vault.rehydrate(part.get("text", ""))[0]
    step["verdicts"] = verdicts
    return data


# ---------------------------------------------------------------- routes

def _headers(request: Request) -> dict:
    keep = {"authorization", "content-type", "accept"}
    h = {k: v for k, v in request.headers.items() if k.lower() in keep}
    if "authorization" not in {k.lower() for k in h} and os.environ.get("MODEL_API_KEY"):
        h["authorization"] = f"Bearer {os.environ['MODEL_API_KEY']}"
    return h


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"message": f"Veil: {msg}", "type": "invalid_request_error"}},
                        status_code=code)


@app.post("/v1/responses")
async def responses(request: Request) -> Response:
    body = await request.json()
    if body.get("stream"):
        return _err("streaming is not supported yet")
    if SPEND["usd"] >= BUDGET_USD:
        return _err(f"budget of ${BUDGET_USD:.2f} reached; raise VEIL_BUDGET_USD to continue", 429)
    s = SESSIONS.resolve(request.headers.get("x-veil-session"), body.get("previous_response_id"))
    dest_url = request.headers.get("x-veil-context", "")
    s.origin = POLICY.app_for(dest_url) if dest_url else s.origin
    step: dict = {"n": len(s.steps), "dest": dest_url, "frames": []}
    t0 = time.perf_counter()
    try:
        safe = await outbound(body, s, step)
    except Exception as e:  # fail closed: nothing leaves
        _audit(s).append("blocked_request", error=repr(e))
        return _err(f"request blocked, privacy pipeline error: {e!r}", 500)
    step["veil_secs"] = round(time.perf_counter() - t0, 3)
    t1 = time.perf_counter()
    up = await _client.post(f"{UPSTREAM}/responses", json=safe, headers=_headers(request))
    step["model_secs"] = round(time.perf_counter() - t1, 3)
    try:
        data = up.json()
    except ValueError:
        return Response(up.content, status_code=up.status_code)
    if up.status_code != 200:
        _audit(s).append("upstream_error", status=up.status_code, error=data.get("error"))
        if os.environ.get("VEIL_DEBUG_UPSTREAM") == "1":  # veiled body only, never raw
            dbg = RUN_DIR / s.id / f"upstream_{up.status_code}_{len(s.steps)}.json"
            dbg.write_text(json.dumps({k: v for k, v in safe.items() if k != "input"} | {
                "input": [{k: (v if k != "output" else "<image>") for k, v in i.items()}
                          if isinstance(i, dict) else i for i in safe.get("input") or []]},
                indent=2, default=str))
        return JSONResponse(data, status_code=up.status_code)
    step["usd"] = round(_cost(body.get("model", ""), data.get("usage")), 5)
    SPEND["usd"] += step["usd"]
    if data.get("id"):
        SESSIONS.bind_response(data["id"], s)
    t2 = time.perf_counter()
    out = inbound(data, s, step, dest_url)
    step["firewall_secs"] = round(time.perf_counter() - t2, 4)
    s.steps.append(step)
    _audit(s).append("step", model=body.get("model"), **step)
    return JSONResponse(out, headers={"x-veil-session": s.id})


@app.get("/veil")
def console() -> FileResponse:
    return FileResponse(Path(__file__).parent / "console" / "index.html")


@app.get("/veil/sessions")
def list_sessions() -> list[dict]:
    return [{"id": s.id, "steps": len(s.steps), "tokens": len(s.vault.entries())}
            for s in SESSIONS.all()]


@app.get("/veil/sessions/{sid}")
def session_detail(sid: str) -> JSONResponse:
    s = SESSIONS.get(sid)
    if not s:
        return _err("no such session", 404)
    ok, msg = verify(RUN_DIR / sid / "audit.jsonl") if (RUN_DIR / sid / "audit.jsonl").exists() \
        else (True, "empty")
    return JSONResponse({
        "id": s.id, "steps": s.steps, "audit": {"ok": ok, "detail": msg},
        "vault": [{"token": e.token, "entity": e.entity, "origins": sorted(e.origins)}
                  for e in s.vault.entries()],
        "spend_usd": round(SPEND["usd"], 4)})


@app.get("/veil/sessions/{sid}/frames/{name}")
def frame(sid: str, name: str) -> Response:
    p = RUN_DIR / sid / "frames" / name
    if not p.exists() or "/" in name:
        return _err("no such frame", 404)
    return FileResponse(p)


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE"])
async def passthrough(path: str, request: Request) -> Response:
    if request.method == "POST" and path.startswith(("chat/completions", "messages", "images",
                                                       "files")):
        return _err(f"/{path} is not routed through Veil yet; it would bypass the privacy layer", 403)
    up = await _client.request(request.method, f"{UPSTREAM}/{path}", content=await request.body(),
                               headers=_headers(request), params=request.query_params)
    return Response(up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type"))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "upstream": UPSTREAM, "sam": SAM.backend if SAM else None,
            "guard": (os.environ.get("VEIL_GUARD_MODEL", "muse-spark-1.3") + " @ " +
                      os.environ.get("VEIL_GUARD_URL", "https://api.meta.ai/v1")) if GUARD_ON else None,
            "spend_usd": round(SPEND["usd"], 4), "budget_usd": BUDGET_USD}


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)
