"""SAM 3.1 client: concept prompt in, boxes plus pixel masks out.

Two backends share one interface:
- "api": Meta Model API (sam-3.1). Used only on synthetic data.
- "private": open-weight facebook/sam3.1 served on the private plane (Modal),
  added once that endpoint exists. Real screens must use this backend.
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
        if backend != "api":
            raise NotImplementedError("private SAM backend lands with the Modal endpoint")
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or os.environ["MODEL_API_KEY"])

    async def segment(self, png: bytes, concept: str) -> list[Region]:
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
