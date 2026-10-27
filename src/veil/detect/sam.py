"""SAM 3.1 client: concept prompt in, boxes plus pixel masks out.

Two backends share one interface:
- "api": Meta Model API (sam-3.1). Used only on synthetic data.
- "private": open-weight facebook/sam3.1 on the private plane
  (infra/modal_sam.py). Raw screenshots never leave infrastructure we run.
  Set VEIL_SAM_URL and VEIL_SAM_KEY. Boxes only (masks are filled as boxes).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import numpy as np
from meta_sam_parser import decode_mask_to_raster, image_segmentation_format, parse_responses_stream
from openai import AsyncOpenAI


@dataclass
class Region:
    concept: str
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in image pixels, x2/y2 exclusive
    mask: np.ndarray | None  # bool array covering `box`, or None
    confidence: float | None = None


def png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


class SamClient:
    def __init__(self, backend: str = "api", api_key: str | None = None,
                 base_url: str = "https://api.meta.ai/v1") -> None:
        self.backend = backend
        if backend == "private":
            import httpx
            self.url = os.environ["VEIL_SAM_URL"].rstrip("/")
            self.http = httpx.AsyncClient(timeout=600, headers={
                "Authorization": f"Bearer {api_key or os.environ['VEIL_SAM_KEY']}"})
            return
        if backend != "api":
            raise ValueError(f"unknown SAM backend {backend!r}")
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or os.environ["MODEL_API_KEY"])

    async def segment_many(self, png: bytes, concepts: tuple[str, ...]) -> dict[str, list[Region]]:
        """All concepts for one image. Private backend: one request, image encoded once."""
        if self.backend != "private":
            import asyncio
            res = await asyncio.gather(*(self.segment(png, c) for c in concepts))
            return dict(zip(concepts, res))
        r = await self.http.post(f"{self.url}/segment", json={
            "image_b64": base64.b64encode(png).decode(), "concepts": list(concepts)})
        r.raise_for_status()
        out = r.json()["regions"]
        return {c: [Region(c, tuple(x["box"]), None, x["score"]) for x in out.get(c, [])]
                for c in concepts}

    async def segment(self, png: bytes, concept: str) -> list[Region]:
        if self.backend == "private":
            return (await self.segment_many(png, (concept,)))[concept]
        stream = await self.client.responses.create(
            model="sam-3.1",
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": png_data_url(png)}]}],
            stream=True,
        )
        result = await parse_responses_stream(stream, image_segmentation_format()).final_result()
        boxes: dict[str, Region] = {}
        for rec in result.records:
            if rec.kind == "box":
                boxes[rec.object_id] = Region(
                    concept, (int(rec.left), int(rec.top), int(rec.right), int(rec.bottom)),
                    None, rec.confidence)
            elif rec.kind == "mask" and rec.object_id in boxes:
                region = boxes[rec.object_id]
                h, w = rec.mask.height, rec.mask.width
                raw = decode_mask_to_raster(rec.mask)  # row-major bytes of 0/1, H*W long
                raster = np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(bool)
                region.mask = raster
        return list(boxes.values())
