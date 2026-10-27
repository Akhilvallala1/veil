"""Does Muse Wonder accept the computer tool on this account yet?

  uv run python bench/check_wonder.py

Sends Meta's documented minimal computer-use request (bare {"type": "computer"},
one blank screenshot) to Wonder and to Spark, and prints status + request IDs
to share with Meta support. No secrets are printed.
"""

from __future__ import annotations

import base64
import io
import os

import httpx
from dotenv import load_dotenv
from PIL import Image

load_dotenv(".env")
B = "https://api.meta.ai/v1"
H = {"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]}

buf = io.BytesIO()
Image.new("RGB", (1280, 800), "white").save(buf, "PNG")
img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
inp = [{"role": "user", "content": [{"type": "input_text", "text": "Click the center of the screen."},
                                    {"type": "input_image", "image_url": img}]}]

ok = {}
for model in ("muse-wonder-1.0", "muse-spark-1.3"):
    r = httpx.post(f"{B}/responses", headers=H, timeout=180, json={
        "model": model, "tools": [{"type": "computer"}], "input": inp, "max_output_tokens": 2000})
    rid = r.headers.get("x-request-id", "?")
    if r.status_code == 200:
        types = [o.get("type") for o in r.json().get("output", [])]
        ok[model] = "computer_call" in types
        print(f"{model:16} 200  output={types}  request_id={rid}")
    else:
        ok[model] = False
        msg = r.json().get("error", {}).get("message", r.text[:200])
        print(f"{model:16} {r.status_code}  {msg}  request_id={rid}")

print()
if ok.get("muse-wonder-1.0"):
    print("Wonder computer use WORKS. Run the agent on it:")
    print("  uv run python bench/run_task.py --model muse-wonder-1.0")
else:
    print("Wonder still rejects the computer tool. Veil keeps using muse-spark-1.3.")
