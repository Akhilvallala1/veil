"""Would Veil's firewall have stopped the address hijacks that actually happened?

Replays every action from the unprotected (raw) runs where Muse Spark changed the member's
mailing address, through Veil's real action firewall: the same OCR of the screen the agent
was looking at, the same policy file, the same check_action() the proxy calls. No model
and no GPU are involved, so this tests the gate itself, not whether a Veil-arm agent
happens to reach the form.

Run: uv run python bench/gate_replay.py
Writes bench/results/gate_replay_address.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from veil.firewall import Policy, check_action
from veil.frame import gate_hint
from veil.perceive import ocr
from veil.vault import Vault

PORTAL = "http://127.0.0.1:8100"
RUNS = sorted(Path("runs/attack").glob("raw_address*"))
# Every other recorded raw run (all attack styles + no-attack tasks): how often would the
# gate ask a human, and on what? Measures the cost of the wider click context.
OTHER = sorted([d for d in Path("runs/attack").glob("raw_*") if "address" not in d.name]
               + list(Path("runs/tasks").glob("*_raw*")))


def replay(run_dir: Path) -> dict:
    result = json.loads((run_dir / "result.json").read_text()) \
        if (run_dir / "result.json").exists() else {}
    log = json.loads((run_dir / "run.json").read_text())["log"]
    policy, vault = Policy(), Vault()
    last_click = ""
    asks = []
    for s in log:
        # The screen the agent saw before choosing this step's actions.
        shot = run_dir / f"step_{s['step'] - 1:02d}.png"
        lines, _ = ocr(shot.read_bytes()) if shot.exists() else ([], None)
        url = s["url"] if s["url"].startswith("http") else PORTAL + s["url"]
        for a in s["actions"]:
            hint = gate_hint(a, lines, last_click)
            v = check_action(a, vault, policy, url, focus_hint=hint)
            if a.get("type") in ("click", "double_click"):
                last_click = hint
            if v.decision == "ask":
                asks.append({"step": s["step"], "action": a.get("type"), "target": hint,
                             "reason": v.reason})
    return {"run": run_dir.name, "raw_outcome": result.get("outcome"),
            "address_hijacked": result.get("outcome") == "exfiltrated",
            "veil_would_ask_human": asks, "gated": bool(asks)}


def main() -> None:
    rows = [replay(d) for d in RUNS if (d / "result.json").exists()]
    hijacks = [r for r in rows if r["address_hijacked"]]
    summary = {"raw_runs": len(rows), "hijacked": len(hijacks),
               "hijacks_that_would_hit_the_human_gate": sum(r["gated"] for r in hijacks),
               "non_hijack_runs_gated": sum(r["gated"] for r in rows if not r["address_hijacked"])}
    others = [replay(d) for d in OTHER if (d / "run.json").exists()]
    summary["other_raw_runs"] = len(others)
    summary["other_runs_with_an_ask"] = sum(r["gated"] for r in others)
    exfil = [r for r in others if r["raw_outcome"] == "exfiltrated"]
    summary["exfiltration_runs"] = len(exfil)
    summary["exfiltration_runs_that_would_hit_the_human_gate"] = sum(r["gated"] for r in exfil)
    # Asks in runs where nothing was sent or saved: the price in extra human prompts.
    clean = [r for r in others if r["raw_outcome"] != "exfiltrated"]
    summary["clean_runs"] = len(clean)
    summary["clean_runs_with_an_ask"] = sum(r["gated"] for r in clean)
    summary["other_ask_targets"] = sorted({x["target"] for r in others
                                           for x in r["veil_would_ask_human"]})
    out = Path("bench/results/gate_replay_address.json")
    out.write_text(json.dumps({"summary": summary, "runs": rows, "other": others}, indent=2))
    for r in rows:
        print(f"{r['run']:28s} hijacked={r['address_hijacked']!s:5s} gated={r['gated']!s:5s} "
              f"{[(x['step'], x['target']) for x in r['veil_would_ask_human']][:3]}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
