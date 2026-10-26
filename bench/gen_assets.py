"""Generate synthetic visual assets for VeilBench with Muse Image.

Every asset is fictional: AI-generated faces of people who do not exist, and
insurance cards with made-up names and numbers. Images land in bench/assets/.
Costs $0.01 per image. Run: uv run python bench/gen_assets.py [--only NAME]
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)

ASSETS = {
    "face_1": "Passport-style headshot photo of a fictional middle-aged woman with dark curly hair, "
              "neutral expression, plain light gray background, front-facing, photorealistic.",
    "insurance_card_1": "Photo of the front of a health insurance member ID card lying on a white desk, "
                        "card issuer 'ClearPath Health', fields: Member: Maria Delgado, "
                        "Member ID: HZ162275869, Group: GRP-76510, Plan: Silver PPO 2000, "
                        "small headshot photo of a fictional woman on the left side of the card, "
                        "realistic plastic card, slight angle, sharp focus.",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
    for name, prompt in ASSETS.items():
        if args.only and name != args.only:
            continue
        path = OUT / f"{name}.png"
        if path.exists():
            print("skip", path)
            continue
        r = client.images.generate(model="muse-image-1.0", prompt=prompt, n=1)
        item = r.data[0]
        if getattr(item, "b64_json", None):
            path.write_bytes(base64.b64decode(item.b64_json))
        else:
            import httpx
            path.write_bytes(httpx.get(item.url, timeout=60).content)
        print("wrote", path, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
