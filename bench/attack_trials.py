"""Run the injection bench N times per arm, sequentially (arms share portal state).

  uv run python bench/attack_trials.py --n 3 --variant workflow

Writes bench/results/attack_<variant>.json with every trial's outcome.
Requires the portal on :8100 and, for the veil arm, the proxy on :8787.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--variant", default="workflow")
    ap.add_argument("--arms", default="raw,veil")
    args = ap.parse_args()
    rows = []
    for arm in args.arms.split(","):
        for t in range(1, args.n + 1):
            tag = f"_trial{t}"
            cmd = [sys.executable, "bench/attack.py", "--variant", args.variant, "--tag", tag]
            if arm == "veil":
                cmd.append("--veil")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
            path = Path(f"runs/attack/{arm}_{args.variant}{tag}/result.json")
            if not path.exists():
                rows.append({"arm": arm, "trial": t, "outcome": "harness_error",
                             "error": proc.stderr[-400:]})
            else:
                r = json.loads(path.read_text())
                rows.append({"arm": arm, "trial": t, "outcome": r["outcome"],
                             "leaked_fields": r["leaked_fields"], "steps": r["steps"],
                             "messages_sent": r["messages_sent"], "session": r["session"]})
            print(json.dumps(rows[-1]), flush=True)
    summary = {arm: dict(Counter(r["outcome"] for r in rows if r["arm"] == arm))
               for arm in args.arms.split(",")}
    out = Path(f"bench/results/attack_{args.variant}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"variant": args.variant, "n": args.n, "summary": summary,
                               "trials": rows}, indent=2))
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main()
