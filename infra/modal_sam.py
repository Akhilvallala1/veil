"""Veil private plane, visual half: open-weight SAM 3.1 on Modal.

Raw screenshots go here (never to a hosted API) to find faces, ID cards and
photos. One request = one image + several concept prompts -> boxes per concept.

Deploy:   modal deploy infra/modal_sam.py
Smoke:    modal run infra/modal_sam.py
Requires Modal secrets `huggingface` (HF_TOKEN, gated facebook/sam3.1) and
`veil-sam-key` (VEIL_SAM_KEY). Requests without that bearer key get 401.

Cost control: L4-class GPU is not enough for the 848M-param detector + text
encoder at 1008px with headroom, so we use an A10G; scales to zero after 5 idle
minutes; one container at a time.
"""

# No `from __future__ import annotations` here: FastAPI must resolve the request
# model defined inside web(); string annotations make it a query param (422).

import modal

MINUTES = 60
SAM3_REF = "main"

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install("torch==2.10.0", "torchvision", index_url="https://download.pytorch.org/whl/cu128")
    .run_commands(f"pip install 'git+https://github.com/facebookresearch/sam3.git@{SAM3_REF}'")
    .pip_install("fastapi[standard]", "huggingface_hub", "pillow", "numpy<2",
                 # sam3 imports these but does not declare them (found by import probe)
                 "einops", "setuptools<81", "pycocotools", "psutil")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)
hf_cache = modal.Volume.from_name("veil-hf-cache", create_if_missing=True)
app = modal.App("veil-sam")


@app.cls(
    image=image,
    gpu="A10G",
    secrets=[modal.Secret.from_name("huggingface"), modal.Secret.from_name("veil-sam-key")],
    volumes={"/root/.cache/huggingface": hf_cache},
    timeout=10 * MINUTES,
    scaledown_window=5 * MINUTES,
    max_containers=1,
)
@modal.concurrent(max_inputs=4)
class Sam:
    @modal.enter()
    def load(self) -> None:
        import torch
        from sam3.model.sam3_image_processor import Sam3Processor
        from sam3.model_builder import build_sam3_image_model, download_ckpt_from_hf

        ckpt = download_ckpt_from_hf(version="sam3.1")
        self.model = build_sam3_image_model(checkpoint_path=ckpt, load_from_HF=False)
        self.processor = Sam3Processor(self.model, confidence_threshold=0.5)
        self.torch = torch

    @modal.asgi_app()
    def web(self):
        import base64
        import io
        import os
        import time

        from fastapi import Body, FastAPI, Header, HTTPException
        from PIL import Image
        from pydantic import BaseModel

        api = FastAPI()
        key = os.environ["VEIL_SAM_KEY"]

        class Req(BaseModel):
            image_b64: str
            concepts: list[str]
            threshold: float = 0.5

        @api.post("/segment")
        def segment(req: Req = Body(...), authorization: str = Header(default="")):
            if authorization != f"Bearer {key}":
                raise HTTPException(401, "unauthorized")
            t0 = time.time()
            img = Image.open(io.BytesIO(base64.b64decode(req.image_b64))).convert("RGB")
            out = {}
            with self.torch.inference_mode(), self.torch.autocast("cuda", dtype=self.torch.bfloat16):
                state = self.processor.set_image(img)
                self.processor.set_confidence_threshold(req.threshold, state)
                for c in req.concepts:
                    self.processor.reset_all_prompts(state)
                    st = self.processor.set_text_prompt(prompt=c, state=state)
                    boxes = st["boxes"].float().cpu().tolist()
                    scores = st["scores"].float().cpu().tolist()
                    out[c] = [{"box": [round(v) for v in b], "score": round(s, 3)}
                              for b, s in zip(boxes, scores)]
            return {"regions": out, "seconds": round(time.time() - t0, 3)}

        @api.get("/health")
        def health():
            return {"ok": True}

        return api


@app.local_entrypoint()
def smoke() -> None:
    import base64
    import json
    import os
    import time
    from pathlib import Path

    import httpx

    url = Sam().web.get_web_url()
    print("endpoint:", url)
    img = Path("bench/assets/insurance_card_1.png")
    body = {"image_b64": base64.b64encode(img.read_bytes()).decode(),
            "concepts": ["face", "id card"]}
    hdr = {"Authorization": f"Bearer {os.environ['VEIL_SAM_KEY']}"}
    t0 = time.time()
    for _ in range(60):  # cold start: image pull + checkpoint download + load
        try:
            r = httpx.post(f"{url}/segment", json=body, headers=hdr, timeout=300)
            if r.status_code == 200:
                print(f"{time.time() - t0:.0f}s:", img.name, r.json())
                return
            print(f"{time.time() - t0:.0f}s: HTTP {r.status_code}")
        except httpx.HTTPError as e:
            print(f"{time.time() - t0:.0f}s waiting: {type(e).__name__}")
        time.sleep(10)
