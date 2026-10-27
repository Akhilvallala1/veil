"""Run the injection bench N times per arm, sequentially (arms share portal state).

  uv run python bench/attack_trials.py --n 3 --variant workflow

Writes bench/results/attack_<variant>.json with every trial's outcome.
Requires the portal on :8100 and, for the veil arm, the proxy on :8799.
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
    ap.add_argument("--out-tag", default="", help="suffix for the results file, e.g. _private")
    args = ap.parse_args()
    veil_config = None
    if "veil" in args.arms:
        import httpx
        try:
            h = httpx.get("http://127.0.0.1:8799/healthz", timeout=5).json()
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Veil proxy not reachable on :8799 ({e}). Start it first.")
        if "upstream" not in h or "guard" not in h:
            sys.exit(f"Port 8799 is not the Veil proxy (got {h}).")
        veil_config = {"guard": h["guard"], "sam": h.get("sam")}
        print("veil config:", veil_config, flush=True)
    ap_tag = args.out_tag
    out = Path(f"bench/results/attack_{args.variant}{ap_tag}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    prior = json.loads(out.read_text())["trials"] if out.exists() else []
    rows = [r for r in prior if r["outcome"] != "harness_error"]
    done = {(r["arm"], r["trial"]) for r in rows}
    for arm in args.arms.split(","):
        for t in range(1, args.n + 1):
            if (arm, t) in done:
                continue
            tag = f"{args.out_tag}_trial{t}"
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
                             "veil_config": veil_config if arm == "veil" else None,
                             "leaked_fields": r["leaked_fields"], "steps": r["steps"],
                             "messages_sent": r["messages_sent"], "session": r["session"]})
            print(json.dumps(rows[-1]), flush=True)
            print(f"JCODE_PROGRESS {json.dumps({'current': len(rows), 'total': args.n * 2, 'unit': 'trials'})}", flush=True)
            out.write_text(json.dumps({"variant": args.variant, "n": args.n, "trials": rows}, indent=2))
    summary = {arm: dict(Counter(r["outcome"] for r in rows if r["arm"] == arm))
               for arm in args.arms.split(",")}
    out.write_text(json.dumps({"variant": args.variant, "n": args.n, "summary": summary,
                               "trials": rows}, indent=2))
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main()
