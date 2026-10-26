"""Measure guard latency inside a real agent session (frames change step to step).

Replays the frames of a recorded proxy session through veil_frame with ONE vault
and ONE guard, as the proxy does, and reports per-step latency and how many
lines hit the cache. Synthetic data only (guard stand-in runs on Meta API).

  uv run python bench/latency.py runs/proxy/<session>/frames
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from veil.detect.guard import Guard
from veil.frame import veil_frame
from veil.vault import Vault

load_dotenv(".env")


async def main(frame_dir: Path) -> None:
    raws = sorted(frame_dir.glob("*_raw.png"))
    vault, guard = Vault(), Guard()
    times = []
    for p in raws:
        before = dict(guard.usage)
        t0 = time.perf_counter()
        r = await veil_frame(p.read_bytes(), vault, "payer-portal", guard=guard)
        dt = time.perf_counter() - t0
        times.append(dt)
        sent = guard.usage["lines_sent"] - before["lines_sent"]
        cached = guard.usage["lines_cached"] - before["lines_cached"]
        print(f"{p.name:16s} {dt:5.2f}s  guard {r.timings.get('guard', 0):5.2f}s  "
              f"lines sent {sent:3d}  cached {cached:3d}  blocked={r.blocked}")
    print(f"\n{len(times)} steps  p50 {statistics.median(times):.2f}s  max {max(times):.2f}s  "
          f"guard calls {guard.usage['calls']}  usage {guard.usage}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
