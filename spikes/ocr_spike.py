"""Day-1 spike: how well does on-device OCR read sensitive values on UI screenshots?

Captures portal pages with Playwright, runs Apple Vision OCR (ocrmac), and checks
for each ground-truth sensitive value (from data-pii) whether OCR recovered it.
Run with the portal up: uv run python spikes/ocr_spike.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ocrmac import ocrmac
from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path("spikes/out")
OUT.mkdir(parents=True, exist_ok=True)
PAGES = ["/?q=a", "/member/p00", "/member/p03", "/claim/p01/0", "/member/p05"]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> None:
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        for path in PAGES:
            page.goto(f"http://127.0.0.1:8100{path}")
            png = OUT / f"ocr_{norm(path) or 'root'}.png"
            page.screenshot(path=str(png))
            truth = page.eval_on_selector_all(
                "[data-pii]", "els => els.map(e => [e.dataset.pii, e.innerText])")
            for fast in (False, True):
                t0 = time.perf_counter()
                level = "fast" if fast else "accurate"
                anns = ocrmac.OCR(Image.open(png), recognition_level=level).recognize()
                dt = time.perf_counter() - t0
                text = norm(" ".join(a[0] for a in anns))
                found = [(k, v) for k, v in truth if norm(v) in text]
                missed = [(k, v) for k, v in truth if norm(v) not in text]
                results.append({"page": path, "level": level, "secs": round(dt, 3),
                                "boxes": len(anns), "truth": len(truth), "found": len(found),
                                "missed": missed})
        browser.close()

    for level in ("accurate", "fast"):
        rows = [r for r in results if r["level"] == level]
        t = sum(r["truth"] for r in rows)
        f = sum(r["found"] for r in rows)
        secs = [r["secs"] for r in rows]
        print(f"{level:8s} recall {f}/{t} = {f/t:.1%}  mean {sum(secs)/len(secs):.2f}s  "
              f"max {max(secs):.2f}s")
        for r in rows:
            if r["missed"]:
                print("   missed on", r["page"], r["missed"])
    (OUT / "ocr_results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
