"""Measure the frame pipeline on portal screens: leak rate, over-masking, latency.

Ground truth is read from the DOM (data-pii). Veil itself only sees pixels.
- Leak: a ground-truth value is recoverable by OCR from the veiled frame.
- Over-masking: non-sensitive words visible in the raw frame that vanish after veiling.

Run with the portal up: uv run python bench/frame_eval.py [--sam]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from veil.frame import veil_frame
from veil.perceive import ocr
from veil.vault import Vault, norm

load_dotenv(".env")
OUT = Path("bench/results")
WORLD = "portal"
PORTAL = "http://127.0.0.1:8100"
WORLDS = {
    "portal": ("http://127.0.0.1:8100", lambda: (
        ["/?q=a", "/?q=e", "/?q=Nakamura", "/claims?q=a", "/messages"]
        + [f"/member/p{i:02d}" for i in range(12)] + [f"/claim/p{i:02d}/0" for i in range(6)]
        + ["/member/p00/address"])),
    "ehr": ("http://127.0.0.1:8200", lambda: (
        ["/", "/patients"] + [f"/chart/p{i:02d}" for i in range(10)])),
}


def capture(base: str, paths: list[str]) -> list[dict]:
    frames = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 800})
        for p in paths:
            page.goto(base + p)
            page.wait_for_load_state("networkidle")
            truth = page.eval_on_selector_all(
                "[data-pii]", """els => els.filter(e => {const r = e.getBoundingClientRect();
                return r.bottom > 0 && r.top < innerHeight && r.width > 0;})
                .map(e => [e.dataset.pii, e.innerText || e.getAttribute('src') || ''])""")
            frames.append({"path": p, "png": page.screenshot(), "truth": truth})
        b.close()
    return frames


async def run(use_sam: bool, frames: list[dict], use_guard: bool = False) -> dict:
    sam = None
    if use_sam:
        from veil.detect.sam import SamClient
        sam = SamClient()
    guard = None
    if use_guard:
        from veil.detect.guard import Guard
        guard = Guard()
    rows, leaks, totals = [], [], []
    tot_truth = tot_leak = tot_words = tot_lost = 0
    for f in frames:
        vault = Vault()  # fresh session per frame: worst case, no help from earlier frames
        r = await veil_frame(f["png"], vault, origin="payer-portal", sam=sam, guard=guard)
        if r.blocked:
            rows.append({"path": f["path"], "blocked": True, "egress_hits": r.egress_hits})
            continue
        veiled_text = norm(" ".join(ln.text for ln in ocr(r.png)[0]))
        text_truth = [(k, v) for k, v in f["truth"] if k != "id_card"]
        leaked = [(k, v) for k, v in text_truth if norm(v) and norm(v) in veiled_text]
        raw_lines = ocr(f["png"])[0]
        truth_norm = [norm(v) for _, v in text_truth]
        words = [w for ln in raw_lines for w in ln.text.split()
                 if len(norm(w)) >= 3 and not any(norm(w) in t for t in truth_norm)]
        lost = [w for w in words if norm(w) not in veiled_text]
        tot_truth += len(text_truth)
        tot_leak += len(leaked)
        tot_words += len(words)
        tot_lost += len(lost)
        leaks += [{"path": f["path"], "entity": k, "value": v} for k, v in leaked]
        totals.append(r.timings["total"])
        rows.append({"path": f["path"], "truth": len(text_truth), "leaked": leaked,
                     "dets": len(r.detections), "over_masked": lost[:10],
                     "timings": {k: round(v, 3) for k, v in r.timings.items()}})
        Path("spikes/out/veiled").mkdir(parents=True, exist_ok=True)
        name = WORLD + "_" + (norm(f["path"]) or "root")
        Path(f"spikes/out/veiled/{name}_raw.png").write_bytes(f["png"])
        Path(f"spikes/out/veiled/{name}_veiled.png").write_bytes(r.png)
    summary = {
        "frames": len(frames), "blocked": sum(1 for r in rows if r.get("blocked")),
        "sensitive_values": tot_truth, "leaked": tot_leak,
        "leak_rate": round(tot_leak / max(tot_truth, 1), 4),
        "non_sensitive_words": tot_words, "over_masked_words": tot_lost,
        "over_mask_rate": round(tot_lost / max(tot_words, 1), 4),
        "latency_p50_s": round(statistics.median(totals), 3) if totals else None,
        "latency_max_s": round(max(totals), 3) if totals else None,
        "sam": use_sam,
        "guard": (guard.model + (" (private)" if guard.private else " (Meta API)")) if guard else None,
        "guard_usage": guard.usage if guard else None,
    }
    return {"summary": summary, "leaks": leaks, "frames": rows}


def main() -> None:
    global WORLD
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", action="store_true")
    ap.add_argument("--world", default="portal", choices=list(WORLDS))
    ap.add_argument("--guard", action="store_true", help="add the L2 semantic detector")
    args = ap.parse_args()
    WORLD = args.world
    base, paths = WORLDS[args.world]
    res = asyncio.run(run(args.sam, capture(base, paths()), args.guard))
    res["summary"]["world"] = args.world
    OUT.mkdir(parents=True, exist_ok=True)
    gm = os.environ.get("VEIL_GUARD_MODEL", "muse-spark-1.3")
    res["summary"]["guard_model"] = gm if args.guard else None
    tag = ("_sam" if args.sam else "") + ("_guard" if args.guard else "")
    if args.guard and gm == "muse-glimmer":
        tag += "_glimmer"
    (OUT / f"frame_eval_{args.world}{tag}.json").write_text(
        json.dumps(res, indent=2))
    print(json.dumps(res["summary"], indent=2))
    for leak in res["leaks"][:20]:
        print("LEAK", leak)
    for r in res["frames"]:
        if r.get("over_masked"):
            print("over-masked on", r["path"], r["over_masked"][:6])


if __name__ == "__main__":
    main()
