"""Smoke test the private SAM endpoint (cold start tolerant) and its auth.

  uv run python bench/sam_private_check.py [image.png ...]
"""

from __future__ import annotations

import base64
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv(".env")
URL = os.environ["VEIL_SAM_URL"].rstrip("/")
KEY = os.environ["VEIL_SAM_KEY"]
imgs = sys.argv[1:] or ["bench/assets/insurance_card_1.png", "bench/assets/face_1.png"]


def post(body: dict, key: str) -> httpx.Response:
    # Modal answers slow requests with 303 to a result URL; follow it with GET.
    r = httpx.post(f"{URL}/segment", json=body, headers={"Authorization": f"Bearer {key}"},
                   timeout=300)
    while r.status_code == 303:
        r = httpx.get(r.headers["location"], timeout=300)
    return r


t0 = time.time()
for path in imgs:
    body = {"image_b64": base64.b64encode(open(path, "rb").read()).decode(),
            "concepts": ["face", "id card", "photo"]}
    for _ in range(90):
        try:
            r = post(body, KEY)
            if r.status_code == 200:
                break
            print(f"{time.time() - t0:5.0f}s HTTP {r.status_code} {r.text[:120]}", flush=True)
            if 400 <= r.status_code < 500:
                sys.exit(1)  # our bug, not a cold start: do not spin
        except httpx.HTTPError as e:
            print(f"{time.time() - t0:5.0f}s waiting ({type(e).__name__})", flush=True)
        time.sleep(10)
    j = r.json()
    summary = {c: [(x["box"], x["score"]) for x in v] for c, v in j["regions"].items()}
    print(f"{time.time() - t0:5.0f}s {path}: server {j['seconds']}s {summary}")

bad = post({"image_b64": "", "concepts": []}, "nope").status_code
print("wrong key ->", bad)
assert bad == 401
