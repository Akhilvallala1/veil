"""Veil the frame: the pipeline every screenshot passes before it leaves.

perceive (OCR) -> detect (validators, labels, vault recall, SAM) -> tokenize ->
render (solid masks + token labels) -> egress check (OCR the veiled frame again
and look for any vault value). Fails closed: if anything errors or the egress
check finds a value, the frame is blocked.
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from veil.detect.layout import label_spans
from veil.detect.validators import Span, find_spans
from veil.perceive import Line, ocr
from veil.vault import Vault, norm

MASK_FILL = (31, 41, 51)
MASK_TEXT = (255, 214, 102)
LINK_TEXT = (125, 200, 255)
PAD = 3


def _is_link(img: Image.Image, box) -> bool:
    """Was the original text link-colored? Keeps the affordance after masking, so the agent
    still sees what is clickable."""
    a = np.asarray(img.crop(box).convert("RGB")).reshape(-1, 3).astype(int)
    if a.size == 0:
        return False
    lum = a.sum(axis=1)
    ink = a[lum < np.percentile(lum, 30)]
    if len(ink) == 0:
        return False
    r, g, b = ink.mean(axis=0)
    return b > r + 40 and b > 90

# Validator entities that are low-risk alone and kept visible unless a label says otherwise.
PASS_ENTITIES = {"date"}


@dataclass
class Detection:
    entity: str
    box: tuple[int, int, int, int]
    source: str
    token: str | None = None
    label: str = ""


@dataclass
class VeilResult:
    png: bytes  # what may be sent
    blocked: bool
    detections: list[Detection]
    egress_hits: list[str]
    timings: dict[str, float] = field(default_factory=dict)
    width: int = 0
    height: int = 0


def _font(size: int):
    for path in ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc",
                 "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _merge(spans: list[Span]) -> list[Span]:
    """Prefer label-derived spans (they carry semantic type), drop overlaps."""
    order = {"label": 0, "vault": 1, "validator": 2}
    out: list[Span] = []
    for s in sorted(spans, key=lambda s: (order.get(s.source, 9), s.start)):
        if any(o.start < s.end and s.start < o.end for o in out):
            continue
        out.append(s)
    return sorted(out, key=lambda s: s.start)


def _vault_spans(line: Line, vault: Vault) -> list[Span]:
    """Values already in the vault (seen on earlier frames, typed by the agent, or in the task)
    wherever they reappear, including inside input fields with no label."""
    spans = []
    low = line.text.lower()
    squashed = norm(line.text)
    for e in vault.entries():
        v = e.value.lower()
        if len(norm(v)) < 4:
            continue
        i = low.find(v)
        if i == -1 and norm(v) in squashed:
            # Same value, different spacing or punctuation: mask the whole line.
            spans.append(Span(0, len(line.text), e.entity, "vault", line.text))
            continue
        while i != -1:
            spans.append(Span(i, i + len(v), e.entity, "vault", line.text[i:i + len(v)]))
            i = low.find(v, i + 1)
    return spans


def detect_text(lines: list[Line], vault: Vault, origin: str) -> list[Detection]:
    labeled = label_spans(lines)
    dets: list[Detection] = []
    for i, line in enumerate(lines):
        spans = list(labeled.get(i, []))
        spans += [s for s in find_spans(line.text) if s.entity not in PASS_ENTITIES]
        spans += _vault_spans(line, vault)
        for s in _merge(spans):
            value = line.text[s.start:s.end].strip()
            if len(norm(value)) < 2:
                continue
            # Label/validator spans can be a whole cell that contains a known value plus OCR
            # noise; reuse the existing token when a vault value is inside.
            known = [e for e in vault.entries() if len(norm(e.value)) >= 4
                     and norm(e.value) in norm(value)]
            token = known[0].token if known and s.source != "vault" and \
                len(norm(known[0].value)) >= len(norm(value)) - 2 else \
                vault.tokenize(value, s.entity, origin)
            dets.append(Detection(s.entity, line.sub_box(s.start, s.end), s.source, token))
    return dets


async def detect_visual(png: bytes, sam, concepts: tuple[str, ...]) -> list[Detection]:
    if sam is None:
        return []
    results = await asyncio.gather(*(sam.segment(png, c) for c in concepts))
    dets = []
    for concept, regions in zip(concepts, results):
        for r in regions:
            ent = {"face": "face", "id card": "id_card"}.get(concept, concept.replace(" ", "_"))
            dets.append(Detection(ent, r.box, "sam"))
    return dets


def _inside(inner, outer) -> bool:
    return inner[0] >= outer[0] - 2 and inner[1] >= outer[1] - 2 and \
        inner[2] <= outer[2] + 2 and inner[3] <= outer[3] + 2


def render(png: bytes, dets: list[Detection]) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    src = img.copy()
    W, H = img.size
    draw = ImageDraw.Draw(img)
    regions = [d.box for d in dets if d.source == "sam"]
    # Visual regions first (large), text tokens on top, except text already inside a region.
    for d in sorted(dets, key=lambda d: 0 if d.source == "sam" else 1):
        if d.source != "sam" and any(_inside(d.box, r) for r in regions):
            continue
        x1, y1, x2, y2 = d.box
        x1, y1 = max(0, x1 - PAD), max(0, y1 - PAD)
        x2, y2 = min(W, x2 + PAD), min(H, y2 + PAD)
        draw.rectangle((x1, y1, x2, y2), fill=MASK_FILL)
        label = d.token or d.entity.upper()
        h = y2 - y1
        if d.source == "sam":
            font = _font(16)
            tw = draw.textlength(label, font=font)
            draw.text((x1 + max(3, ((x2 - x1) - tw) / 2), y1 + h / 2 - 9), label,
                      fill=MASK_TEXT, font=font)
            continue
        size = max(9, min(int(h * 0.7), 18))
        link = _is_link(src, d.box)
        font = _font(size)
        tw = draw.textlength(label, font=font)
        while tw > (x2 - x1) - 4 and size > 8:
            size -= 1
            font = _font(size)
            tw = draw.textlength(label, font=font)
        ty = y1 + max(0, (h - size) // 2 - 1)
        color = LINK_TEXT if link else MASK_TEXT
        draw.text((x1 + 3, ty), label, fill=color, font=font)
        if link:
            draw.line((x1 + 3, ty + size + 1, x1 + 3 + tw, ty + size + 1), fill=color, width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def egress_check(png: bytes, vault: Vault) -> list[str]:
    """OCR the frame we are about to send and look for any vault value."""
    lines, _ = ocr(png)
    text = norm(" ".join(ln.text for ln in lines))
    hits = []
    for e in vault.entries():
        k = norm(e.value)
        if len(k) >= 4 and k in text:
            hits.append(e.token)
    return hits


async def veil_frame(png: bytes, vault: Vault, origin: str = "", sam=None,
                     sam_concepts: tuple[str, ...] = ("face", "id card")) -> VeilResult:
    t = {}
    t0 = time.perf_counter()
    try:
        lines, (W, H) = await asyncio.to_thread(ocr, png)
        t["ocr"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        visual_task = asyncio.create_task(detect_visual(png, sam, sam_concepts))
        dets = detect_text(lines, vault, origin)
        t["text_detect"] = time.perf_counter() - t1
        dets += await visual_task
        t["visual_detect"] = time.perf_counter() - t1
        t2 = time.perf_counter()
        out = render(png, dets)
        t["render"] = time.perf_counter() - t2
        t3 = time.perf_counter()
        hits = await asyncio.to_thread(egress_check, out, vault)
        t["egress"] = time.perf_counter() - t3
    except Exception as e:  # fail closed
        return VeilResult(b"", True, [], [f"pipeline error: {e!r}"],
                          {"total": time.perf_counter() - t0})
    t["total"] = time.perf_counter() - t0
    return VeilResult(out if not hits else b"", bool(hits), dets, hits, t, W, H)


def blank_frame(width: int, height: int, message: str) -> bytes:
    img = Image.new("RGB", (width or 1280, height or 800), MASK_FILL)
    ImageDraw.Draw(img).text((40, 40), message, fill=MASK_TEXT, font=_font(22))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mask_ratio(png: bytes) -> float:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    return float((a == np.array(MASK_FILL)).all(axis=2).mean())
