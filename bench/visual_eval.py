"""Visual PII check: are ID cards and faces covered, and are harmless images left alone?

Renders a synthetic page (insurance card scan, member photo, company logo, bar
chart, plain text) at 1280x800, runs it through veil_frame with the chosen SAM
backend, and measures coverage per element from the DOM boxes (ground truth).

  VEIL_SAM=private uv run python bench/visual_eval.py
  VEIL_SAM=api     uv run python bench/visual_eval.py   # Meta API, synthetic only

Pass criteria (what matters for privacy, not pixel-perfect element boxes):
- no face is detectable in the veiled frame (Apple Vision face detector,
  independent of SAM), while the raw frame has faces;
- none of the card's text (name, member ID, group) is OCR-readable after veiling;
- harmless images (logo, chart) are <= 10% covered.
Element coverage is reported too: SAM masks the card/face itself, not the blank
margins of the <img> element around it, so 100% element coverage is not expected.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from veil.detect.sam import SamClient
from veil.frame import MASK_FILL, veil_frame
from veil.vault import Vault

load_dotenv(".env")
ASSETS = Path("bench/assets")
OUT = Path("bench/results")


def _logo() -> str:
    img = Image.new("RGB", (220, 70), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((8, 10, 58, 60), fill=(40, 90, 200))
    d.text((70, 25), "ClearPath Health", fill=(30, 30, 30))
    return _b64(img)


def _chart() -> str:
    img = Image.new("RGB", (320, 180), "white")
    d = ImageDraw.Draw(img)
    for i, h in enumerate([60, 110, 80, 140, 95]):
        d.rectangle((20 + i * 58, 170 - h, 60 + i * 58, 170), fill=(90, 160, 120))
    return _b64(img)


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def page_html() -> str:
    card = "data:image/png;base64," + base64.b64encode((ASSETS / "insurance_card_1.png").read_bytes()).decode()
    face = "data:image/png;base64," + base64.b64encode((ASSETS / "face_1.png").read_bytes()).decode()
    return f"""<html><body style="font-family:Helvetica;margin:24px;background:#f6f7f9">
<img data-vis="harmless:logo" src="{_logo()}" style="width:220px">
<h2>Member documents</h2>
<div style="display:flex;gap:28px;align-items:flex-start">
  <div><div>ID card (front)</div><img data-vis="sensitive:id_card" src="{card}" style="width:430px"></div>
  <div><div>Member photo</div><img data-vis="sensitive:face" src="{face}" style="width:170px"></div>
  <div><div>Claims this year</div><img data-vis="harmless:chart" src="{_chart()}" style="width:320px"></div>
</div>
<p>Questions about coverage? Call the number on the back of your card.</p>
</body></html>"""


def capture() -> tuple[bytes, list[dict]]:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1280, "height": 800})
        p.set_content(page_html())
        p.wait_for_load_state("networkidle")
        els = p.eval_on_selector_all("[data-vis]", """els => els.map(e => {
            const r = e.getBoundingClientRect();
            return {kind: e.dataset.vis, box: [r.left, r.top, r.right, r.bottom]};})""")
        png = p.screenshot()
        b.close()
    return png, els


def coverage(veiled: bytes, box) -> float:
    img = np.asarray(Image.open(io.BytesIO(veiled)).convert("RGB"))
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    region = img[y1:y2, x1:x2].reshape(-1, 3)
    fill = np.array(MASK_FILL[:3])
    return float((np.abs(region.astype(int) - fill).sum(axis=1) <= 12).mean())


def faces(png: bytes) -> int:
    """Independent check: Apple Vision face rectangles (not SAM)."""
    import Vision
    from Foundation import NSData
    req = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    h = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(png, len(png)), None)
    h.performRequests_error_([req], None)
    return len(req.results() or [])


CARD_TEXT = ["Maria Delgado", "HZ162275869", "GRP-76510"]


async def main() -> None:
    backend = os.environ.get("VEIL_SAM", "private")
    png, els = await asyncio.to_thread(capture)
    sam = SamClient(backend=backend)
    await veil_frame(png, Vault(), origin="payer-portal", sam=sam)  # warm-up (cold start)
    r = await veil_frame(png, Vault(), origin="payer-portal", sam=sam)
    rows = [{"element": e["kind"], "covered": round(coverage(r.png, e["box"]), 3)} for e in els]
    from veil.perceive import ocr
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    veiled_text = norm(" ".join(ln.text for ln in ocr(r.png)[0]))
    raw_text = norm(" ".join(ln.text for ln in ocr(png)[0]))
    card_readable_raw = [t for t in CARD_TEXT if norm(t) in raw_text]
    card_readable_veiled = [t for t in CARD_TEXT if norm(t) in veiled_text]
    faces_raw, faces_veiled = faces(png), faces(r.png)
    harmless_ok = all(x["covered"] <= 0.10 for x in rows if x["element"].startswith("harmless"))
    ok = faces_raw > 0 and faces_veiled == 0 and not card_readable_veiled and harmless_ok
    res = {"backend": backend, "blocked": r.blocked, "pass": ok,
           "faces_detected": {"raw": faces_raw, "veiled": faces_veiled},
           "card_text_readable": {"raw": card_readable_raw, "veiled": card_readable_veiled},
           "element_coverage": rows,
           "frame_seconds": round(r.timings["total"], 2),
           "sam_seconds": round(r.timings.get("visual_detect", 0), 2)}
    OUT.mkdir(exist_ok=True)
    (OUT / f"visual_eval_{backend}.json").write_text(json.dumps(res, indent=2))
    Path("spikes/out").mkdir(parents=True, exist_ok=True)
    Path(f"spikes/out/visual_{backend}_raw.png").write_bytes(png)
    Path(f"spikes/out/visual_{backend}_veiled.png").write_bytes(r.png)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
