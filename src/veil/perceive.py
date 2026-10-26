"""Perception: OCR a screenshot into text lines with pixel boxes.

Apple Vision (accurate mode) via ocrmac. Measured on Day 1: 100% recall of
sensitive values on 1280x800 portal screens, ~0.3 s per frame, fully on-device.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image


@dataclass
class Line:
    text: str
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 pixels, top-left origin
    conf: float

    def sub_box(self, start: int, end: int) -> tuple[int, int, int, int]:
        """Approximate pixel box of characters [start, end) assuming even glyph width."""
        n = max(len(self.text), 1)
        x1, y1, x2, y2 = self.box
        w = x2 - x1
        return (x1 + int(w * start / n), y1, x1 + int(w * end / n + 0.999), y2)


def ocr(png: bytes) -> tuple[list[Line], tuple[int, int]]:
    from ocrmac import ocrmac

    img = Image.open(io.BytesIO(png))
    W, H = img.size
    lines = []
    for text, conf, (nx, ny, nw, nh) in ocrmac.OCR(img, recognition_level="accurate").recognize():
        # Vision boxes are normalized with a bottom-left origin.
        x1 = int(nx * W)
        y1 = int((1 - ny - nh) * H)
        x2 = int((nx + nw) * W + 0.999)
        y2 = int((1 - ny) * H + 0.999)
        lines.append(Line(text, (x1, y1, x2, y2), float(conf)))
    return lines, (W, H)
