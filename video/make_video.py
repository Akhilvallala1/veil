"""Render the Veil demo video: real frames from recorded runs, animated, with voiceover + captions.

  uv run python video/make_video.py            # full render -> video/veil_demo.mp4
  uv run python video/make_video.py --scene 03 # render one scene for a quick look

Voice: Microsoft neural TTS (edge-tts, en-US Andrew). Not the user's voice; no cloning.
To use your own voice instead, record video/voice/<scene_id>.mp3 files (same ids as
video/script.py) and run with --own-voice; scenes re-time to your recording.

Visual language matches the Veil console: dark ink background, gold = masked token,
coral = blocked. Every screen shown is a real frame from runs/ (synthetic data).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "video"))
from script import RATE, SCENES, VOICE  # noqa: E402

W, H, FPS = 1920, 1080, 30
OUT = ROOT / "video"
BUILD = Path.home() / ".jcode" / "scratch" / "vid" / "build"

BG = (18, 24, 29)
SURF = (26, 34, 40)
INK = (238, 243, 246)
MUTED = (155, 173, 184)
GOLD = (255, 214, 102)
CORAL = (255, 138, 122)
MASK = (31, 41, 51)

SANS = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"

R = ROOT / "runs"
F_MEMBER_RAW = R / "proxy/s_a819e2830a/frames/003_raw.png"
F_MEMBER_VEIL = R / "proxy/s_a819e2830a/frames/003_veiled.png"
F_SEARCH_VEIL = R / "proxy/s_a819e2830a/frames/001_veiled.png"
F_SEARCH_RAW = R / "proxy/s_a819e2830a/frames/001_raw.png"
F_CLAIM_RAW = R / "attack/raw_workflow_trial3/step_04.png"
F_MSG_FILLED = R / "attack/raw_workflow_trial3/step_06.png"
F_MSG_VEIL_EMPTY = R / "proxy/s_0c6352f9fd/frames/013_veiled.png"
F_CONSOLE = ROOT / "docs/img/console.png"
MEMBER_DETS = R / "proxy/s_a819e2830a/audit.jsonl"


def font(size: int, mono: bool = False, weight: str = "") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(MONO if mono else SANS, size)
    if weight and not mono:
        try:
            f.set_variation_by_name(weight)
        except Exception:  # noqa: BLE001
            pass
    return f


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_io(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def fade(t: float, start: float, dur: float = 0.5) -> float:
    return ease((t - start) / dur)


def canvas() -> Image.Image:
    return Image.new("RGB", (W, H), BG)


def load(p: Path) -> Image.Image:
    return Image.open(p).convert("RGB")


def paste_alpha(base: Image.Image, im: Image.Image, xy, alpha: float) -> None:
    if alpha <= 0:
        return
    if alpha >= 1:
        base.paste(im, xy)
        return
    region = base.crop((xy[0], xy[1], xy[0] + im.width, xy[1] + im.height))
    base.paste(Image.blend(region, im, alpha), xy)


def text(d: ImageDraw.ImageDraw, xy, s: str, size: int, fill=INK, mono=False, weight="",
         anchor="la", alpha: float = 1.0, bg=BG):
    if alpha <= 0:
        return
    col = tuple(int(bg[i] + (fill[i] - bg[i]) * alpha) for i in range(3))
    d.text(xy, s, font=font(size, mono, weight), fill=col, anchor=anchor)


def shadowed(base: Image.Image, im: Image.Image, xy, radius=28, alpha=1.0) -> None:
    sh = Image.new("RGBA", (im.width + radius * 4, im.height + radius * 4), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((radius * 2, radius * 2 + 10, radius * 2 + im.width,
                                          radius * 2 + im.height + 10), 14,
                                         fill=(0, 0, 0, int(150 * alpha)))
    sh = sh.filter(ImageFilter.GaussianBlur(radius))
    base.paste(sh, (xy[0] - radius * 2, xy[1] - radius * 2), sh)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width, im.height), 12, fill=int(255 * alpha))
    base.paste(im, xy, mask)


def screen(p: Path | Image.Image, w: int) -> Image.Image:
    im = p if isinstance(p, Image.Image) else load(p)
    return im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)


def crop_zoom(im: Image.Image, box, out_w: int, out_h: int) -> Image.Image:
    return im.crop(box).resize((out_w, out_h), Image.LANCZOS)


def lerp_box(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def member_dets() -> list[dict]:
    for line in MEMBER_DETS.read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "step" and r.get("n") == 3:
            return r["frames"][0]["detections"]
    return []


# --------------------------------------------------------------------------- scenes
# Each scene: f(t, dur) -> Image. t in seconds from scene start.

def s_hook(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    # Title card for the first 3.2 s, then the agent loop diagram.
    if t < 3.4:
        a = fade(t, 0.1, 0.7) * (1 - fade(t, 2.9, 0.5))
        text(d, (W // 2, 470), "Veil", 150, INK, weight="Bold", anchor="mm", alpha=a)
        text(d, (W // 2, 590), "a privacy firewall for computer-use agents", 44, MUTED,
             anchor="mm", alpha=a)
        text(d, (W // 2, 990), "Meta Global AI Developer Hackathon · Indie track · all data synthetic",
             26, MUTED, anchor="mm", alpha=a * 0.8)
        return im
    t2 = t - 3.4
    shot = screen(F_SEARCH_RAW, 760)
    x0, y0 = 140, 300
    shadowed(im, shot, (x0, y0), alpha=fade(t2, 0, 0.6))
    text(d, (x0, y0 - 60), "Your screen", 34, MUTED, alpha=fade(t2, 0, 0.6))
    # arrow + cloud
    p = ease_io((t2 - 1.4) / 1.6)
    ax0, ax1, ay = x0 + 800, 1420, y0 + 240
    if p > 0:
        d.line((ax0, ay, ax0 + (ax1 - ax0) * p, ay), fill=CORAL, width=6)
        if p > 0.98:
            d.polygon([(ax1, ay - 18), (ax1 + 30, ay), (ax1, ay + 18)], fill=CORAL)
        text(d, ((ax0 + ax1) // 2, ay - 40), "every screenshot", 30, CORAL, anchor="mm",
             alpha=fade(t2, 1.6, 0.5))
    a = fade(t2, 2.6, 0.6)
    if a > 0:
        cx, cy = 1600, ay
        d.rounded_rectangle((cx - 150, cy - 110, cx + 150, cy + 110), 28, fill=SURF)
        text(d, (cx, cy - 20), "Cloud", 44, INK, weight="Semibold", anchor="mm", alpha=a, bg=SURF)
        text(d, (cx, cy + 34), "AI model", 32, MUTED, anchor="mm", alpha=a, bg=SURF)
    return im


def s_problem(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    raw = load(F_MEMBER_RAW)
    # Slow push-in on the real member page (card stays in frame), callouts on each field.
    z = ease_io(t / max(dur, 1)) * 0.5
    box = lerp_box((0, 0, 1280, 800), (0, 40, 1120, 740), z)
    vh_target = 900
    vw = int(vh_target * (box[2] - box[0]) / (box[3] - box[1]))
    view = crop_zoom(raw, box, vw, vh_target)
    vx, vy = 70, (H - vh_target) // 2
    shadowed(im, view, (vx, vy))
    sx = vw / (box[2] - box[0])
    sy = vh_target / (box[3] - box[1])
    dets = {d_["token"]: d_ for d_ in member_dets()}
    seq = [("NAME_1", "Name"), ("DOB_1", "Date of birth"), ("SSN_1", "Social Security #"),
           ("MEMBER_ID_1", "Member ID"), (None, "Insurance card")]
    for i, (tok, label) in enumerate(seq):
        a = fade(t, [2.7, 3.4, 4.7, 6.3, 7.3][i], 0.3)
        if a <= 0:
            continue
        if tok and tok in dets:
            b = dets[tok]["box"]
        else:
            b = [103, 690, 408, 801]
        rx0 = vx + (b[0] - 6 - box[0]) * sx
        ry0 = vy + (b[1] - 6 - box[1]) * sy
        rx1 = vx + (b[2] + 6 - box[0]) * sx
        ry1 = vy + (b[3] + 6 - box[1]) * sy
        if ry1 > H - 20:
            ry1 = H - 20
        if ry0 < H - 30:
            d.rounded_rectangle((rx0, ry0, rx1, ry1), 8, outline=CORAL, width=4)
        tx = vx + vw + 50
        ty = 300 + i * 92
        text(d, (tx, ty), label, 36, CORAL, weight="Semibold", alpha=a)
    a = fade(t, 9.1, 0.5)
    if a > 0:
        bx = vx + vw + 40
        d.rounded_rectangle((bx, 760, W - 30, 860), 16, fill=SURF)
        text(d, ((bx + W - 30) // 2, 810), "sent to the cloud", 30, CORAL, anchor="mm", alpha=a,
             bg=SURF)
    return im


def curtain(raw: Image.Image, veil: Image.Image, p: float) -> Image.Image:
    out = veil.copy()
    cut = int(raw.width * p)
    if cut > 0:
        out.paste(raw.crop((0, 0, cut, raw.height)), (0, 0))
    d = ImageDraw.Draw(out)
    d.rectangle((cut - 2, 0, cut + 2, raw.height), fill=GOLD)
    d.ellipse((cut - 16, raw.height // 2 - 16, cut + 16, raw.height // 2 + 16), fill=GOLD)
    return out


def s_reveal(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    raw, veil = load(F_MEMBER_RAW), load(F_MEMBER_VEIL)
    # Curtain sweeps from fully real (p=1) to half, then to fully veiled, then back to half.
    if t < 2.4:
        p = 1.0
    elif t < 5.0:
        p = 1 - 0.5 * ease_io((t - 2.4) / 2.6)
    elif t < 7.6:
        p = 0.5
    elif t < 10.0:
        p = 0.5 - 0.5 * ease_io((t - 7.6) / 2.4)
    elif t < 14.6:
        p = 0.0
    else:
        p = 0.5 * ease_io((t - 14.6) / 2.5)
    frame = curtain(raw, veil, p)
    w = 1500
    shot = screen(frame, w)
    x, y = (W - w) // 2, 150
    shadowed(im, shot, (x, y))
    a = fade(t, 0.3)
    text(d, (x, 95), "Your screen: never leaves your machine", 32, MUTED, alpha=a)
    text(d, (x + w, 95), "What Meta's model receives", 32, GOLD, anchor="ra", alpha=a)
    # Zoom inset on tokens once the veil is showing.
    a2 = fade(t, 8.2, 0.6) * (1 - fade(t, 14.4, 0.5))
    if a2 > 0:
        inset = crop_zoom(veil, (220, 120, 900, 270), 820, 181)
        shadowed(im, inset, (W - 820 - 80, H - 181 - 60), alpha=a2)
    return im


def type_on(s: str, t: float, cps: float = 14) -> str:
    return s[: max(0, int(t * cps))]


def s_tokens(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    # Left: what the model sees and types. Right: what actually gets typed locally.
    colw = 820
    lx, rx, top = 110, W - 110 - colw, 210
    text(d, (lx, top - 70), "The cloud model", 36, GOLD, weight="Semibold", alpha=fade(t, 0))
    text(d, (rx, top - 70), "Veil, on your side", 36, INK, weight="Semibold", alpha=fade(t, 0.3))
    sv = screen(load(F_SEARCH_VEIL).crop((0, 0, 1280, 330)), colw)
    sr = screen(load(F_SEARCH_RAW).crop((0, 0, 1280, 330)), colw)
    shadowed(im, sv, (lx, top), alpha=fade(t, 0))
    shadowed(im, sr, (rx, top), alpha=fade(t, 0.3))
    # Typing animation
    by = top + sv.height + 70
    d.rounded_rectangle((lx, by, lx + colw, by + 110), 16, fill=SURF)
    d.rounded_rectangle((rx, by, rx + colw, by + 110), 16, fill=SURF)
    text(d, (lx + 30, by + 22), "types", 26, MUTED, bg=SURF)
    text(d, (rx + 30, by + 22), "browser receives", 26, MUTED, bg=SURF)
    s1 = type_on("{{NAME_1}}", t - 1.6)
    text(d, (lx + 30, by + 56), s1, 38, GOLD, mono=True, bg=SURF)
    if t > 4.0:
        s2 = type_on("Grace Nakamura", t - 4.1)
        text(d, (rx + 30, by + 56), s2, 38, INK, mono=True, bg=SURF)
    a = ease_io((t - 3.2) / 0.8)
    if a > 0:
        ax0, ax1, ay = lx + colw + 20, rx - 20, by + 55
        d.line((ax0, ay, ax0 + (ax1 - ax0) * a, ay), fill=GOLD, width=5)
        if a > 0.97:
            d.polygon([(ax1, ay - 14), (ax1 + 22, ay), (ax1, ay + 14)], fill=GOLD)
    # Answer bubble
    a = fade(t, 6.8, 0.6)
    if a > 0:
        y = by + 170
        d.rounded_rectangle((lx, y, W - 110, y + 120), 18, fill=SURF)
        text(d, (lx + 34, y + 26), "The model's real answer, word for word (session s_22fd15fe30)", 26,
             MUTED, alpha=a, bg=SURF)
        text(d, (lx + 34, y + 64), "Claim ‹ID_1› for ‹NAME_1› has Status: Denied and Billed amount: $1044.00.",
             32, INK, alpha=a, bg=SURF)
    return im


def s_how(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    text(d, (W // 2, 120), "Everything that sees raw pixels runs on hardware you control", 40, INK,
         weight="Semibold", anchor="mm", alpha=fade(t, 0))
    boxes = [
        ("1", "On-device OCR + rules", "Apple Vision · validators · label layout", 4.3),
        ("2", "Muse Glimmer 30B", "open weights · your GPU · reads layout, catches what rules miss", 7.8),
        ("3", "SAM 3.1", "open weights · your GPU · faces and ID cards", 13.3),
    ]
    y = 230
    for n, title, sub, start in boxes:
        a = fade(t, start, 0.6)
        if a <= 0:
            y += 170
            continue
        d.rounded_rectangle((260, y, 1660, y + 140), 22, fill=SURF)
        text(d, (330, y + 70), n, 64, GOLD, weight="Bold", anchor="mm", alpha=a, bg=SURF)
        text(d, (420, y + 34), title, 40, INK, weight="Semibold", alpha=a, bg=SURF)
        text(d, (420, y + 88), sub, 28, MUTED, alpha=a, bg=SURF)
        y += 170
    a = fade(t, 17.1, 0.6)
    if a > 0:
        d.rounded_rectangle((260, 760, 1660, 860), 22, outline=GOLD, width=3)
        text(d, (W // 2, 810), "Only the masked frame goes to Meta's hosted model", 36, GOLD,
             anchor="mm", alpha=a)
    a = fade(t, 20.0, 0.5)
    if a > 0:
        text(d, (W // 2, 905), "Any detector error  →  frame withheld.  Fails closed.", 36, CORAL,
             weight="Semibold", anchor="mm", alpha=a)
    return im


def s_attack(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    claim, msg = load(F_CLAIM_RAW), load(F_MSG_FILLED)
    split = 9.2
    if t < split:
        # Whole page first, then push in on the planted note (full width of the note).
        z = ease_io((t - 1.4) / 2.6)
        box = lerp_box((0, 0, 1280, 800), (14, 262, 1170, 372), z)
        vw = 1760
        vh = int(vw * (box[3] - box[1]) / (box[2] - box[0]))
        vh = min(vh, 860)
        view = crop_zoom(claim, box, vw, vh)
        shadowed(im, view, ((W - vw) // 2, (H - vh) // 2 - 10))
        text(d, (110, 70), "A note planted in the claim", 36, CORAL, weight="Semibold",
             alpha=fade(t, 0.5))
    else:
        t2 = t - split
        # Zoom on the To + Message fields so the stolen values are readable.
        part = msg.crop((24, 60, 744, 330))
        view = part.resize((1440, 540), Image.LANCZOS)
        shadowed(im, view, ((W - 1440) // 2, 190), alpha=fade(t2, 0, 0.4))
        text(d, (210, 130), "Without Veil: the model writes the member's data into a message", 36,
             CORAL, weight="Semibold", alpha=fade(t2, 0.2))
        a = fade(t2, 0.6, 0.5)
        if a > 0:
            d.rounded_rectangle((460, 770, 1460, 880), 22, fill=SURF)
            text(d, (W // 2, 825), "4 of 8 runs sent it to the attacker", 46, CORAL,
                 weight="Bold", anchor="mm", alpha=a, bg=SURF)
    return im


def s_blocked(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    view = screen(load(F_MSG_VEIL_EMPTY).crop((0, 0, 1280, 470)), 980)
    shadowed(im, view, (110, 190))
    text(d, (110, 120), "Through Veil: same attack, same model", 38, GOLD, weight="Semibold",
         alpha=fade(t, 0))
    rx = 1170
    cards = [
        (2.8, "Model tries to type", "the address from the note", INK),
        (9.5, "Blocked by flow policy", "screen-read email → Messages: not allowed", CORAL),
        (13.5, "Send needs a human", "Veil asks before any send", GOLD),
    ]
    y = 220
    for start, title, sub, col in cards:
        a = fade(t, start, 0.5)
        if a > 0:
            d.rounded_rectangle((rx, y, rx + 650, y + 150), 20, fill=SURF)
            d.rectangle((rx, y, rx + 8, y + 150), fill=col)
            text(d, (rx + 40, y + 36), title, 38, col, weight="Semibold", alpha=a, bg=SURF)
            text(d, (rx + 40, y + 94), sub, 27, MUTED, alpha=a, bg=SURF)
        y += 180
    a = fade(t, 16.9, 0.5)
    if a > 0:
        text(d, (W // 2, 905), "0 of 15 runs leaked", 64, GOLD, weight="Bold", anchor="mm", alpha=a)
    a2 = fade(t, 12.6, 0.4)
    if a2 > 0:
        d.rounded_rectangle((110, 190 + view.height + 30, 1090, 190 + view.height + 110), 16,
                            fill=SURF)
        text(d, (140, 190 + view.height + 70), "Portal's Sent list after the run: empty", 32,
             INK, weight="Semibold", anchor="lm", alpha=a2, bg=SURF)
    return im


def s_results(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    text(d, (130, 110), "Held-out medical records app · 170 personal values", 40, INK,
         weight="Semibold", alpha=fade(t, 0))
    text(d, (130, 165), "built after Veil's rules were frozen", 30, MUTED, alpha=fade(t, 0.2))
    bars = [
        ("Typical regex redactor", 109, "64% leaked", CORAL, 4.6),
        ("Veil rules only", 35, "21% leaked", (255, 186, 120), 9.3),
        ("Full Veil (rules + Glimmer + SAM)", 0, "0 of 170", GOLD, 12.1),
    ]
    maxw = 1100
    y = 290
    for label, n, tag, col, start in bars:
        a = fade(t, start, 0.4)
        p = ease((t - start) / 1.2)
        text(d, (130, y), label, 34, INK, alpha=a)
        bw = int(maxw * n / 170 * p)
        d.rounded_rectangle((130, y + 54, 130 + max(bw, 6 if a > 0 else 0), y + 124), 10,
                            fill=col if a > 0 else BG)
        text(d, (130 + max(bw, 6) + 30, y + 89), tag, 40, col, weight="Bold", anchor="lm", alpha=a)
        y += 190
    a = fade(t, 15.0, 0.5)
    if a > 0:
        d.rounded_rectangle((130, 830, 900, 930), 18, fill=SURF)
        text(d, (160, 880), "Task success: 6 of 6, same as no Veil", 32, INK, anchor="lm",
             alpha=a, bg=SURF)
        d.rounded_rectangle((960, 830, 1790, 930), 18, fill=SURF)
        text(d, (990, 880), "Added time: ~2 s per step (median)", 32, INK, anchor="lm",
             alpha=a, bg=SURF)
    return im


def s_proof(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    con = load(F_CONSOLE)
    z = ease_io(t / dur)
    box = lerp_box((0, 0, 1440, 900), (0, 60, 1300, 873), z * 0.6)
    view = crop_zoom(con, box, 1200, int(1200 * (box[3] - box[1]) / (box[2] - box[0])))
    shadowed(im, view, (90, (H - view.height) // 2))
    x = 1360
    a = fade(t, 1.0)
    text(d, (x, 260), "Hash-chained", 40, INK, weight="Semibold", alpha=a)
    text(d, (x, 312), "audit log", 40, INK, weight="Semibold", alpha=a)
    a = fade(t, 7.0)
    d.rounded_rectangle((x, 420, x + 470, 540), 18, fill=SURF) if a > 0 else None
    text(d, (x + 30, 448), "one-line adoption", 26, MUTED, alpha=a, bg=SURF)
    text(d, (x + 30, 486), "base_url → Veil", 34, GOLD, mono=True, alpha=a, bg=SURF)
    a = fade(t, 11.3)
    d.rounded_rectangle((x, 580, x + 470, 700), 18, fill=SURF) if a > 0 else None
    text(d, (x + 30, 608), "also ships as", 26, MUTED, alpha=a, bg=SURF)
    text(d, (x + 30, 646), "an MCP server", 34, INK, alpha=a, bg=SURF)
    return im


def s_limits(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    text(d, (W // 2, 230), "Stated plainly", 52, INK, weight="Semibold", anchor="mm", alpha=fade(t, 0))
    items = ["Synthetic data only", "Small attack sample", "macOS for now (on-device OCR)",
             "Every number links to its raw results"]
    for i, s in enumerate(items):
        a = fade(t, [1.6, 3.2, 4.8, 6.3][i], 0.5)
        col = GOLD if i == 3 else MUTED
        text(d, (W // 2, 380 + i * 110), s, 44, col, anchor="mm", alpha=a)
    return im


def s_close(t, dur):
    im = canvas()
    d = ImageDraw.Draw(im)
    lines = [("Open weights guard the screen.", MUTED, 0.2), ("The hosted model does the work.",
                                                              MUTED, 1.7)]
    for i, (s, c, st) in enumerate(lines):
        text(d, (W // 2, 330 + i * 80), s, 48, c, anchor="mm", alpha=fade(t, st))
    a = fade(t, 3.5, 0.7)
    text(d, (W // 2, 560), "Veil", 140, INK, weight="Bold", anchor="mm", alpha=a)
    text(d, (W // 2, 680), "Let agents work where the sensitive work is.", 44, GOLD, anchor="mm",
         alpha=a)
    a = fade(t, 5.0, 0.6)
    text(d, (W // 2, 860), "github.com/Akhilvallala1/veil", 40, INK, mono=True, anchor="mm", alpha=a)
    text(d, (W // 2, 930), "Muse Spark 1.3 · Muse Glimmer 30B · SAM 3.1 · Muse Image   ·   all data synthetic",
         26, MUTED, anchor="mm", alpha=a)
    return im


RENDER = {
    "01_hook": s_hook, "02_problem": s_problem, "03_reveal": s_reveal, "04_tokens": s_tokens,
    "05_how": s_how, "06_attack": s_attack, "07_blocked": s_blocked, "08_results": s_results,
    "09_proof": s_proof, "10_limits": s_limits, "11_close": s_close,
}
LEAD = {"01_hook": 3.4}   # silent title before narration starts
TAIL = 0.6                # breath after each scene's narration
END_HOLD = 2.5


# --------------------------------------------------------------------------- audio + captions

def tts(scene: dict, own_voice: bool) -> tuple[Path, list[tuple[float, float, str]]]:
    BUILD.mkdir(parents=True, exist_ok=True)
    mp3 = BUILD / f"{scene['id']}.mp3"
    srt = BUILD / f"{scene['id']}.srt"
    if own_voice:
        src = OUT / "voice" / f"{scene['id']}.mp3"
        shutil.copy(src, mp3)
        cues = [(0.0, duration(mp3), scene["say"])]
        return mp3, split_cues(cues)
    ok = mp3.exists() and srt.exists() and srt.stat().st_size > 0 and mp3.stat().st_size > 1000
    if not ok:
        import time
        for attempt in range(5):
            r = subprocess.run(["uvx", "edge-tts", "--voice", VOICE, f"--rate={RATE}", "--text",
                                scene["say"], "--write-media", str(mp3), "--write-subtitles",
                                str(srt)], capture_output=True, text=True)
            if r.returncode == 0 and srt.exists() and srt.stat().st_size > 0:
                break
            time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"TTS failed for {scene['id']}: {r.stderr[-300:]}")
    return mp3, split_cues(parse_srt(srt.read_text()))


def parse_srt(s: str) -> list[tuple[float, float, str]]:
    def ts(x):
        h, m, rest = x.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    out = []
    for block in s.strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            a, b = lines[1].split(" --> ")
            out.append((ts(a), ts(b), " ".join(lines[2:])))
    return out


def split_cues(cues, max_chars=70):
    """Long sentences -> caption chunks broken at commas or word boundaries, never mid-phrase
    if avoidable; time split by character share."""
    out = []
    for a, b, s in cues:
        if len(s) <= max_chars:
            out.append((a, b, s))
            continue
        # prefer a break at a comma/colon nearest the middle
        parts = [s]
        while any(len(p) > max_chars for p in parts):
            nxt = []
            for p in parts:
                if len(p) <= max_chars:
                    nxt.append(p)
                    continue
                mid = len(p) // 2
                cands = [i for i, ch in enumerate(p) if ch in ",:;" and 15 < i < len(p) - 10]
                if cands:
                    i = min(cands, key=lambda i: abs(i - mid)) + 1
                else:
                    sp = [i for i, ch in enumerate(p) if ch == " "]
                    i = min(sp, key=lambda i: abs(i - mid))
                nxt += [p[:i].strip(), p[i:].strip()]
            parts = nxt
        total = sum(len(c) for c in parts) or 1
        t = a
        for c in parts:
            dt = (b - a) * len(c) / total
            out.append((t, t + dt, c))
            t += dt
    return out


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def spoken(s: str) -> str:
    """Caption text: digits and tokens instead of the words the voice needs."""
    rep = [("NAME one", "NAME_1"), ("MEMBER ID one", "MEMBER_ID_1"), ("SAM three point one", "SAM 3.1"),
           ("sixty four percent", "64%"), ("twenty one percent", "21%"),
           ("zero of one hundred seventy", "0 of 170"), ("Zero of fifteen", "0 of 15"),
           ("four of eight", "4 of 8"), ("step two of two", "step 2 of 2")]
    for a, b in rep:
        s = s.replace(a, b)
    return s


def draw_caption(im: Image.Image, s: str) -> None:
    if not s:
        return
    d = ImageDraw.Draw(im)
    f = font(36)
    tw = d.textlength(s, font=f)
    x0, y0 = (W - tw) / 2 - 22, H - 104
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle((x0, y0, x0 + tw + 44, y0 + 62), 14, fill=(0, 0, 0, 170))
    im.paste(ov, (0, 0), ov)
    d.text((W / 2, y0 + 31), s, font=f, fill=INK, anchor="mm")


# --------------------------------------------------------------------------- build

def render_scene(scene, own_voice: bool, captions: bool) -> tuple[Path, float]:
    sid = scene["id"]
    mp3, cues = tts(scene, own_voice)
    lead = LEAD.get(sid, 0.0)
    vo = duration(mp3)
    dur = lead + vo + TAIL + (END_HOLD if sid == "11_close" else 0)
    fdir = BUILD / f"frames_{sid}"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    n = int(math.ceil(dur * FPS))
    fn = RENDER[sid]
    for i in range(n):
        t = i / FPS
        im = fn(t, dur)
        if captions:
            tv = t - lead
            cur = next((spoken(c) for a, b, c in cues if a <= tv < b + 0.15), "")
            draw_caption(im, cur)
        # global fade in/out per scene
        k = min(1.0, t / 0.35, (dur - t) / 0.35)
        if k < 1:
            im = Image.blend(Image.new("RGB", im.size, BG), im, max(0.0, k))
        im.save(fdir / f"{i:05d}.png", compress_level=1)
    # Audio padded with the lead silence and tail.
    wav = BUILD / f"{sid}.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af",
                    f"adelay={int(lead * 1000)}:all=1,apad=whole_dur={dur}", "-ar", "48000",
                    "-ac", "2", str(wav)], check=True)
    mp4 = BUILD / f"{sid}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i",
                    str(fdir / "%05d.png"), "-i", str(wav), "-c:v", "libx264", "-preset", "medium",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", str(mp4)], check=True)
    shutil.rmtree(fdir)
    return mp4, dur


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", help="render only scenes whose id starts with this")
    ap.add_argument("--own-voice", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    args = ap.parse_args()
    scenes = [s for s in SCENES if not args.scene or s["id"].startswith(args.scene)]
    parts, total = [], 0.0
    for s in scenes:
        mp4, dur = render_scene(s, args.own_voice, not args.no_captions)
        parts.append(mp4)
        total += dur
        print(f"{s['id']:12s} {dur:5.1f}s  (total {total:5.1f}s)", flush=True)
    lst = BUILD / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    name = "veil_demo.mp4" if not args.scene else f"preview_{args.scene}.mp4"
    out = OUT / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac",
                    "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True)
    print(f"wrote {out}  {duration(out):.1f}s")


if __name__ == "__main__":
    main()
