"""Veil private plane on Modal: open-weight Muse Glimmer (vLLM) behind an
OpenAI-compatible endpoint. Raw screens are only ever processed by weights we
run ourselves; Modal stands in for a customer's own VPC or on-prem GPUs.

Deploy:   modal deploy infra/modal_glimmer.py
Smoke:    modal run infra/modal_glimmer.py
Requires a Modal secret named `huggingface` with HF_TOKEN.

Cost control: scales to zero after 5 idle minutes; one H100 at a time.
"""

from __future__ import annotations

import subprocess

import modal

MODEL = "meta-models/Muse-Glimmer-30B"
SERVED = "muse-glimmer"
PORT = 8000
MINUTES = 60

image = (
    modal.Image.from_registry("vllm/vllm-openai:muse-glimmer", add_python="3.12")
    .entrypoint([])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_XET_HIGH_PERFORMANCE": "1"})
    .pip_install("huggingface_hub[hf_transfer]")
)
hf_cache = modal.Volume.from_name("veil-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("veil-vllm-cache", create_if_missing=True)

app = modal.App("veil-glimmer")


@app.function(
    image=image,
    gpu="H100",
    secrets=[modal.Secret.from_name("huggingface")],
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    timeout=30 * MINUTES,
    scaledown_window=5 * MINUTES,
    max_containers=1,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=PORT, startup_timeout=25 * MINUTES)
def serve() -> None:
    cmd = [
        "vllm", "serve", MODEL,
        "--served-model-name", SERVED,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--tensor-parallel-size", "1",
        "--max-model-len", "16384",
        "--gpu-memory-utilization", "0.92",
        "--enable-auto-tool-choice",
        "--tool-call-parser", "muse_glimmer",
        "--reasoning-parser", "muse_glimmer",
        "--generation-config", "auto",
    ]
    subprocess.Popen(" ".join(cmd), shell=True)


@app.local_entrypoint()
def smoke() -> None:
    import time
    import urllib.request
    import json

    url = serve.get_web_url()
    print("endpoint:", url)
    body = json.dumps({"model": SERVED, "max_tokens": 400,
                       "messages": [{"role": "user", "content": "Reply with exactly: glimmer ok"}]})
    t0 = time.time()
    for _ in range(60):
        try:
            req = urllib.request.Request(f"{url}/v1/chat/completions", data=body.encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                out = json.loads(r.read())
            print(f"{time.time() - t0:.0f}s:", out["choices"][0]["message"]["content"])
            return
        except Exception as e:  # cold start: weights download + load
            print(f"{time.time() - t0:.0f}s waiting: {e}")
            time.sleep(20)
