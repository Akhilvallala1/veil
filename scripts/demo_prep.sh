#!/usr/bin/env bash
# One command before recording: start everything, warm both GPUs, reset the portal.
#
#   bash scripts/demo_prep.sh            # warm + start, then prints what to run on camera
#   bash scripts/demo_prep.sh --stop     # stop local servers afterwards
#
# Keeps the Mac awake while it runs. GPUs bill per second while warm and scale to zero
# 5 minutes after the last request, so start recording within ~5 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--stop" ]]; then
  pkill -f "uvicorn veil.proxy:app --port 8799" || true
  pkill -f "uvicorn bench.worlds.payer_portal:app --port 8100" || true
  echo "stopped"; exit 0
fi

[[ -f .env ]] || { echo "missing .env (copy .env.example)"; exit 1; }
set -a; . ./.env; set +a
caffeinate -dims -w $$ &

echo "1/5 local servers"
pkill -f "uvicorn veil.proxy:app --port 8799" 2>/dev/null || true
pkill -f "uvicorn bench.worlds.payer_portal:app --port 8100" 2>/dev/null || true
sleep 1
mkdir -p runs
nohup uv run uvicorn bench.worlds.payer_portal:app --port 8100 > runs/portal.log 2>&1 &
VEIL_KEEP_RAW=1 nohup uv run uvicorn veil.proxy:app --port 8799 > runs/proxy_8799.log 2>&1 &
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8799/healthz >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8100/ >/dev/null || { echo "portal did not start (runs/portal.log)"; exit 1; }

echo "2/5 warming SAM 3.1 (up to ~3 min if cold)"
uv run python bench/sam_private_check.py bench/assets/face_1.png | tail -2

echo "3/5 warming Glimmer (up to ~4 min if cold)"
code=$(curl -s -m 900 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $VEIL_GLIMMER_KEY" "$VEIL_GLIMMER_URL/models")
[[ "$code" == "200" ]] || { echo "Glimmer not ready: HTTP $code"; exit 1; }
uv run python - <<'PY'
import os, time
from openai import OpenAI
c = OpenAI(base_url=os.environ["VEIL_GLIMMER_URL"], api_key=os.environ["VEIL_GLIMMER_KEY"])
t = time.time()
c.chat.completions.create(model="muse-glimmer", messages=[{"role": "user", "content": "ok"}], max_tokens=5)
print(f"   Glimmer warm, {time.time() - t:.1f}s")
PY

echo "4/5 reset portal (attack note on claim CLM27874421)"
uv run python - <<'PY'
import json, httpx
from bench.attack import INJECTIONS
httpx.post("http://127.0.0.1:8100/_bench/reset",
           data={"notes": json.dumps({"p00/0": [INJECTIONS["workflow"]]})}).raise_for_status()
print("   portal reset")
PY

echo "5/5 health"
curl -s http://127.0.0.1:8799/healthz; echo
cat <<'TXT'

READY. Record within ~5 minutes (GPUs scale to zero after 5 idle minutes).
  Console:            open http://127.0.0.1:8799/veil
  Scene 2 (normal):   uv run python bench/run_task.py --veil
  Scene 3 (attack):   uv run python bench/attack.py --veil --tag _demo
  Unprotected run:    uv run python bench/attack.py --tag _demo     (may take 2-3 tries to fool Spark)
  Afterwards:         bash scripts/demo_prep.sh --stop
TXT
