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
    link: bool = False  # original text was a link; set by render()


@dataclass
class VeilResult:
    png: bytes  # what may be sent
    blocked: bool
    detections: list[Detection]
    egress_hits: list[str]
    timings: dict[str, float] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    lines: list = field(default_factory=list)  # raw OCR lines, private side only


def text_at(lines: list, x: float, y: float, pad: int = 6) -> str:
    """OCR text under a click point (for risk rules like 'Send'). Private side only."""
    for ln in lines:
        x1, y1, x2, y2 = ln.box
        if x1 - pad <= x <= x2 + pad and y1 - pad <= y <= y2 + pad:
            return ln.text
    return ""


def click_context(lines: list, x: float, y: float, pad_x: int = 16, pad_y: int = 16) -> str:
    """Every OCR line within a button's reach of a click, joined, for the approval gate.

    OCR boxes hug the glyphs, but a button extends ~10-15 px around its label, and agents
    often click its lower edge. In a replay of a real address hijack, a click at y=330
    hit "Save address" (text box y 309-320) and text_at(pad=6) saw nothing, so the gate
    stayed open. For approvals the safe error is asking too often, so this looks wider
    and returns all nearby lines, not just the first.
    """
    near = []
    for ln in lines:
        x1, y1, x2, y2 = ln.box
        if x1 - pad_x <= x <= x2 + pad_x and y1 - pad_y <= y <= y2 + pad_y:
            near.append(ln.text)
    return " | ".join(near)


SUBMIT_KEYS = {"enter", "return", "space", " "}


def gate_hint(action: dict, lines: list, last_click: str) -> str:
    """What a click or key press would act on, for the approval gate. Used by the proxy
    and by bench/gate_replay.py, so the replay tests exactly what runs live.

    Clicks: every OCR line within a button's reach (click_context).
    Enter/Space: Veil can't see keyboard focus, and Tab moves it. A replay of the raw
    exfiltration runs found 2 of 4 sends done as Tab, Enter after clicks that missed the
    button, so "the last thing clicked" was empty and the Send gate never fired. So an
    Enter or Space also counts every short control-like line on screen (4 words or
    fewer): if a Send / Save address / Pay control is visible, a submit key asks a human.
    """
    t = action.get("type")
    if t in ("click", "double_click") and action.get("x") is not None:
        return click_context(lines, action["x"], action["y"])
    if t == "keypress":
        keys = {str(k).lower() for k in action.get("keys") or []}
        if keys & SUBMIT_KEYS:
            controls = [ln.text for ln in lines if 0 < len(ln.text.split()) <= 4]
            return " | ".join([last_click, *controls]) if last_click else " | ".join(controls)
        return last_click
    return ""


def snap_to_link(dets: list, x: float, y: float, max_dy: int = 14,
                 max_dx: int = 4) -> tuple[int, int] | None:
    """If a click lands just outside a link that Veil masked, move it onto the link.

    Masking a link replaces its glyphs with a token label in a dark box. The agent
    aims at the label, and on the claim result row its clicks landed a few pixels
    below the original link (3/13 Veil runs never opened the claim; 0/15 raw). This
    only touches masked *links* Veil drew, never plain text or buttons, and only
    when the click is outside every such box but within a few pixels of one.
    """
    links = [d for d in dets if getattr(d, "link", False) and d.source != "sam"]
    for d in links:
        x1, y1, x2, y2 = d.box
        if x1 - PAD <= x <= x2 + PAD and y1 - PAD <= y <= y2 + PAD:
            return None  # already on a link
    best = None
    for d in links:
        x1, y1, x2, y2 = d.box
        if not (x1 - max_dx <= x <= x2 + max_dx):
            continue
        dy = (y1 - PAD - y) if y < y1 - PAD else (y - y2 - PAD)
        if 0 < dy <= max_dy and (best is None or dy < best[0]):
            best = (dy, (int(min(max(x, x1 + 2), x2 - 2)), (y1 + y2) // 2))
    return best[1] if best else None


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
    order = {"label": 0, "guard": 1, "vault": 2, "validator": 3}
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


def _split_known(s: Span, line: Line, vault: Vault) -> list[Span]:
    """A label/guard span that contains a value the vault already knows, plus more text,
    is split so the known value keeps its token.

    Seen live (Oct 28): the address form reads "Member: Grace Nakamura · HZ162275869" on
    one line. The label rule took the whole value, it became a new token NAME_2, and the
    task said NAME_1, so the agent decided it was on the wrong member's form and spent 14
    steps searching. Now: NAME_1 for the name, and the rest is masked on its own.
    """
    if s.source not in ("label", "guard"):
        return [s]
    seg = line.text[s.start:s.end]
    low = seg.lower()
    hits = []
    for e in vault.entries():
        v = e.value.lower()
        if len(norm(v)) >= 4 and v in low and len(v) < len(seg.strip()):
            i = low.find(v)
            hits.append((i, i + len(v), e))
    if not hits:
        return [s]
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, pos = [], 0
    for i, j, e in hits:
        if i < pos:
            continue
        out.append((pos, i, None))
        out.append((i, j, e))
        pos = j
    out.append((pos, len(seg), None))
    spans = []
    for i, j, e in out:
        if e is not None:
            spans.append(Span(s.start + i, s.start + j, e.entity, "vault", seg[i:j]))
            continue
        piece = seg[i:j]
        # Trim separators (" · ", ", ", " - ") so the residual box hugs the value.
        lead = len(piece) - len(piece.lstrip(" ·•,;:-|/"))
        core = piece.strip(" ·•,;:-|/")
        if len(norm(core)) < 2:
            continue
        a = s.start + i + lead
        found = [f for f in find_spans(core) if f.entity not in PASS_ENTITIES]
        ent = found[0].entity if found else (
            "id_number" if any(c.isdigit() for c in core) else s.entity)
        spans.append(Span(a, a + len(core), ent, s.source, core))
    return spans


def detect_text(lines: list[Line], vault: Vault, origin: str,
                guard_spans: list | None = None) -> list[Detection]:
    labeled = label_spans(lines)
    extra: dict[int, list[Span]] = {}
    for g in guard_spans or []:
        text = lines[g.line].text
        i = text.lower().find(g.text.lower())
        if i == -1:  # model paraphrased: fall back to the whole line
            i, j = 0, len(text)
        else:
            j = i + len(g.text)
        extra.setdefault(g.line, []).append(Span(i, j, g.entity, "guard", text[i:j]))
    dets: list[Detection] = []
    for i, line in enumerate(lines):
        spans = list(labeled.get(i, [])) + extra.get(i, [])
        spans += [s for s in find_spans(line.text) if s.entity not in PASS_ENTITIES]
        spans += _vault_spans(line, vault)
        for s in [p for m in _merge(spans) for p in _split_known(m, line, vault)]:
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
    by_concept = await sam.segment_many(png, concepts)
    dets = []
    for concept in concepts:
        for r in by_concept.get(concept, []):
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
        d.link = link
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
                     sam_concepts: tuple[str, ...] = ("face", "id card"),
                     guard=None) -> VeilResult:
    t = {}
    t0 = time.perf_counter()
    try:
        lines, (W, H) = await asyncio.to_thread(ocr, png)
        t["ocr"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        visual_task = asyncio.create_task(detect_visual(png, sam, sam_concepts))
        guard_spans = await guard.detect(lines) if guard is not None and lines else []
        t["guard"] = time.perf_counter() - t1
        dets = detect_text(lines, vault, origin, guard_spans)
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
    return VeilResult(out if not hits else b"", bool(hits), dets, hits, t, W, H, lines)


def blank_frame(width: int, height: int, message: str) -> bytes:
    img = Image.new("RGB", (width or 1280, height or 800), MASK_FILL)
    ImageDraw.Draw(img).text((40, 40), message, fill=MASK_TEXT, font=_font(22))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mask_ratio(png: bytes) -> float:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    return float((a == np.array(MASK_FILL)).all(axis=2).mean())
