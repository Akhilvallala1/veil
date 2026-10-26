"""Day-1 spike: does SAM 3.1 find visual PII (faces, ID cards) on UI screenshots?

Screenshots the member page that shows a scanned ID card, asks SAM 3.1 (Meta API,
synthetic data only) for several concepts in parallel, and reports boxes, mask
coverage, latency, and overlap with the ground-truth card region.
Run with the portal up: uv run python spikes/sam_spike.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from veil.detect.sam import SamClient

load_dotenv()
OUT = Path("spikes/out")
OUT.mkdir(parents=True, exist_ok=True)
CONCEPTS = ["face", "id card", "card", "photo", "signature", "text"]


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return inter / (area(a) + area(b) - inter + 1e-9)


def capture() -> tuple[bytes, tuple]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto("http://127.0.0.1:8100/member/p00")
        page.wait_for_load_state("networkidle")
        page.locator("[data-pii=id_card]").scroll_into_view_if_needed()
        png = page.screenshot()
        bb = page.locator("[data-pii=id_card]").bounding_box()
        browser.close()
    return png, (bb["x"], bb["y"], bb["x"] + bb["width"], bb["y"] + bb["height"])


async def run(png: bytes) -> list:
    sam = SamClient()

    async def one(c: str):
        t0 = time.perf_counter()
        regions = await sam.segment(png, c)
        return c, time.perf_counter() - t0, regions

    t0 = time.perf_counter()
    out = await asyncio.gather(*(one(c) for c in CONCEPTS))
    print(f"all {len(CONCEPTS)} concepts in parallel: {time.perf_counter() - t0:.2f}s wall")
    return out


def main() -> None:
    png, card_box = capture()
    (OUT / "sam_member_p00.png").write_bytes(png)
    results = asyncio.run(run(png))
    img = Image.open(OUT / "sam_member_p00.png").convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle(card_box, outline=(0, 180, 0), width=3)
    report = []
    for concept, dt, regions in results:
        objs = []
        for r in regions:
            objs.append({"box": r.box, "iou_card": round(iou(r.box, card_box), 3),
                         "mask_px": int(r.mask.sum()) if r.mask is not None else None})
            if r.mask is not None:
                m = Image.fromarray((r.mask * 110).astype("uint8"), "L").resize(
                    (r.box[2] - r.box[0], r.box[3] - r.box[1]))
                img.paste((220, 0, 0), (r.box[0], r.box[1]), m)
            draw.rectangle(r.box, outline=(220, 0, 0, 255), width=2)
            draw.text((r.box[0] + 3, r.box[1] + 3), concept, fill=(220, 0, 0, 255))
        report.append({"concept": concept, "secs": round(dt, 2), "objects": objs})
        print(f"{concept:10s} {dt:5.2f}s  {len(objs):2d} obj  " + ", ".join(
            f"{o['box']} iou={o['iou_card']}" for o in objs[:4]))
    img.save(OUT / "sam_overlay_p00.png")
    (OUT / "sam_results.json").write_text(json.dumps(
        {"card_box": card_box, "report": report}, indent=2, default=str))
    print("ground-truth card box:", tuple(int(v) for v in card_box))


if __name__ == "__main__":
    main()
