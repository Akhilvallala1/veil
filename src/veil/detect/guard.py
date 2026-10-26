"""L2 semantic detector: an LLM reads the OCR'd screen (text + positions) and names
every sensitive span, using layout and meaning rather than fixed formats.

Text-only by design: it works with every Muse Glimmer build (bf16, GGUF, NVFP4,
MLX), costs a fraction of image input, and never needs the raw pixels.

Backend is any OpenAI-compatible chat endpoint:
  VEIL_GUARD_URL    default https://api.meta.ai/v1   (Glimmer on Modal: https://...modal.run/v1)
  VEIL_GUARD_MODEL  default muse-spark-1.3           (Glimmer: muse-glimmer)
  VEIL_GUARD_KEY    default MODEL_API_KEY
The Meta-API default is for synthetic VeilBench data only. Real screens must use a
self-hosted Glimmer endpoint, so raw text never leaves the private plane.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from veil.perceive import Line

ENTITIES = ["person_name", "date_of_birth", "ssn", "member_id", "mrn", "group_number",
            "claim_number", "account_number", "card_number", "phone", "email", "address",
            "id_number"]

SYSTEM = """You are a privacy detector for screenshots of business software (portals, EHRs,
CRMs, email). You receive the screen as OCR lines, each with an index and a box [x1,y1,x2,y2].
Return every line that contains personal or regulated data about an individual: names of
people, dates of birth, government or insurance identifiers, medical record numbers, claim or
case or reference numbers tied to a person, account or card numbers, phone numbers, email
addresses, street addresses. Use layout: a label usually sits left of or above its value; a
table header names the cells below it. Do NOT flag labels or headers themselves, navigation,
buttons, product or plan names, company names, staff or provider names shown as
organization roles (e.g. "Dr. Chen" as a provider), dollar amounts, statuses, or ordinary
dates that are not a date of birth.
For a line that mixes ordinary words and sensitive values, return the exact sensitive
substring(s) in "text".
Answer with JSON only: {"spans":[{"i":<line index>,"text":"<exact substring>","entity":"<type>"}]}
Entity types: """ + ", ".join(ENTITIES)


@dataclass
class GuardSpan:
    line: int
    text: str
    entity: str


class Guard:
    def __init__(self, url: str | None = None, model: str | None = None,
                 key: str | None = None) -> None:
        self.url = url or os.environ.get("VEIL_GUARD_URL", "https://api.meta.ai/v1")
        self.model = model or os.environ.get("VEIL_GUARD_MODEL", "muse-spark-1.3")
        self.client = AsyncOpenAI(base_url=self.url, api_key=key or os.environ.get(
            "VEIL_GUARD_KEY") or os.environ.get("MODEL_API_KEY", "none"), timeout=60)
        self._cache: dict[str, list[GuardSpan]] = {}
        # Per-line memory: (text, rounded box) -> spans found on that line last time.
        # Screens change little between agent steps, so most lines are already known.
        self._line_cache: dict[tuple, list[tuple[str, str]]] = {}
        self.usage = {"calls": 0, "input": 0, "output": 0, "lines_sent": 0, "lines_cached": 0}

    @property
    def private(self) -> bool:
        return "api.meta.ai" not in self.url

    @staticmethod
    def _key(line: Line) -> tuple:
        return (line.text, line.box[0] // 8, line.box[1] // 8)

    async def detect(self, lines: list[Line]) -> list[GuardSpan]:
        """Classify only lines not seen before. New lines are sent together with their
        immediate neighbors (for label context), but only new lines are cached from the reply."""
        new_idx = [i for i, l in enumerate(lines) if self._key(l) not in self._line_cache]
        self.usage["lines_cached"] += len(lines) - len(new_idx)
        if new_idx:
            ctx = sorted({j for i in new_idx for j in (i - 2, i - 1, i, i + 1)
                          if 0 <= j < len(lines)})
            sub = [lines[j] for j in ctx]
            found = await self._detect_all(sub) if len(ctx) < len(lines) * 0.8 \
                else await self._detect_all(lines)
            index_map = ctx if len(ctx) < len(lines) * 0.8 else list(range(len(lines)))
            per_line: dict[int, list[tuple[str, str]]] = {}
            for g in found:
                per_line.setdefault(index_map[g.line], []).append((g.text, g.entity))
            for i in new_idx:
                self._line_cache[self._key(lines[i])] = per_line.get(i, [])
            self.usage["lines_sent"] += len(sub) if index_map is ctx else len(lines)
        out = []
        for i, l in enumerate(lines):
            for text, ent in self._line_cache.get(self._key(l), []):
                out.append(GuardSpan(i, text, ent))
        return out

    async def _detect_all(self, lines: list[Line]) -> list[GuardSpan]:
        payload = "\n".join(f"{i}\t[{l.box[0]},{l.box[1]},{l.box[2]},{l.box[3]}]\t{l.text}"
                            for i, l in enumerate(lines))
        key = hashlib.sha256(payload.encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        r = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": payload}],
            max_tokens=4000, temperature=0,
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "minimal"} if not self.private else {},
        )
        self.usage["calls"] += 1
        if r.usage:
            self.usage["input"] += r.usage.prompt_tokens or 0
            self.usage["output"] += r.usage.completion_tokens or 0
        content = r.choices[0].message.content or "{}"
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
        spans = []
        for s in data.get("spans", []):
            try:
                i = int(s["i"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= i < len(lines) and s.get("text"):
                spans.append(GuardSpan(i, str(s["text"]), str(s.get("entity", "id_number"))))
        self._cache[key] = spans
        return spans
