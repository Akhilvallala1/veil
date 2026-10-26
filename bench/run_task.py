"""Run one task through Veil (or raw) and print what happened.

  uv run python bench/run_task.py --veil            # through the proxy on :8799
  uv run python bench/run_task.py                   # raw, straight to Meta
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from veil.agent import run_agent

load_dotenv(".env")

TASKS = {
    "claim_status": ("In the ClearPath provider portal, find the member Grace Nakamura and tell me "
                     "the status of her most recent claim by service date, and the billed amount."),
    "member_lookup": ("Look up member ID HZ162275869 in the ClearPath portal and tell me which plan "
                      "the member is on and whether coverage is active."),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="claim_status")
    ap.add_argument("--veil", action="store_true")
    ap.add_argument("--model", default="muse-spark-1.3")
    ap.add_argument("--max-steps", type=int, default=12)
    args = ap.parse_args()
    base = "http://127.0.0.1:8799/v1" if args.veil else "https://api.meta.ai/v1"
    out = Path("runs/tasks") / f"{args.task}_{'veil' if args.veil else 'raw'}"
    r = run_agent(TASKS.get(args.task, args.task), "http://127.0.0.1:8100/", model=args.model,
                  base_url=base, max_steps=args.max_steps, out_dir=out)
    for s in r.log:
        print(f"step {s['step']:2d} {s['url'][-30:]:30s} {json.dumps(s['actions'])[:150]}")
    print("\nFINAL:", r.final, "\nSTOPPED:", r.stopped, "\nsession:", r.session,
          "\nusage:", r.usage, f"{r.seconds}s")


if __name__ == "__main__":
    main()
