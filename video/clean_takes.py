"""Generate the narration in the author's cloned voice and keep only clean takes.

For each scene: generate, transcribe with a filler-aware Whisper pass (primed so it writes
"uh", "um", stutters instead of hiding them), and reject any take with a filler, a repeated
word, or a misheard key term. Up to 4 takes per scene (each new take is a fresh sample: a
different seed). The accepted take is copied to the cache name make_video.py uses.

  uv run --with openai-whisper python video/clean_takes.py
Writes video/takes_report.json (what was heard for every take).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

import whisper
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "video"))
import make_video as m  # noqa: E402

load_dotenv(ROOT / ".env")
KEY, VOICE = os.environ["ELEVENLABS_API_KEY"], os.environ["ELEVENLABS_VOICE_ID"]
PRIME = "Umm, so, uh, Veil is, like, a privacy firewall. Hmm, uh, the model, um, sees tokens."
FILLER = re.compile(r"^(uh+|um+|umm+|hmm+|er+|erm+|ah+|mm+|eh+)$", re.I)
# Words that must be heard as written (key terms whose mishearing changes the meaning).
MUST = {"03_reveal": ["veil"], "06_attack": ["veil"], "07_blocked": ["veil"],
        "08_results": ["veil", "redact"], "11_close": ["veil"], "02_problem": ["veil"]}
MAX_TAKES = 4
model = whisper.load_model("small.en")


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def generate(scene: dict, seed: int) -> tuple[bytes, dict]:
    body = {"text": m.ELEVEN_TEXT(scene["say"]), "model_id": m.ELEVEN_MODEL,
            "voice_settings": m.ELEVEN_SETTINGS, "seed": seed}
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps"
        "?output_format=mp3_44100_128", data=json.dumps(body).encode(),
        headers={"xi-api-key": KEY, "content-type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return base64.b64decode(d["audio_base64"]), d["alignment"]


def judge(path: Path, sid: str) -> dict:
    r = model.transcribe(str(path), language="en", initial_prompt=PRIME, word_timestamps=True,
                         temperature=0)
    words = [w["word"].strip() for s in r["segments"] for w in s["words"]]
    fillers = [w for w in words if FILLER.match(norm(w))]
    repeats = [a for a, b in zip(words, words[1:]) if norm(a) == norm(b) and len(norm(a)) > 1]
    text = r["text"].lower()
    missing = [t for t in MUST.get(sid, []) if t not in text]
    return {"text": r["text"].strip(), "fillers": fillers, "repeats": repeats,
            "missing": missing, "ok": not fillers and not repeats and not missing}


def main() -> None:
    report = {}
    for scene in m.SCENES:
        sid = scene["id"]
        body = {"text": m.ELEVEN_TEXT(scene["say"]), "model_id": m.ELEVEN_MODEL,
                "voice_settings": m.ELEVEN_SETTINGS}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:10]
        final_mp3, final_meta = m.BUILD / f"{sid}_el_{h}.mp3", m.BUILD / f"{sid}_el_{h}.json"
        takes = []
        best = None
        for k in range(MAX_TAKES):
            seed = 1000 + k
            tmp = m.BUILD / f"{sid}_take{k}_{h}.mp3"
            tmeta = m.BUILD / f"{sid}_take{k}_{h}.json"
            if not (tmp.exists() and tmeta.exists()):
                audio, al = generate(scene, seed)
                tmp.write_bytes(audio)
                tmeta.write_text(json.dumps(al))
            v = judge(tmp, sid)
            v["take"], v["seed"] = k, seed
            takes.append(v)
            print(f"{sid:12s} take {k}: {'CLEAN' if v['ok'] else 'reject'} "
                  f"fillers={v['fillers']} repeats={v['repeats']} missing={v['missing']}",
                  flush=True)
            if v["ok"]:
                best = (tmp, tmeta)
                break
        if best is None:  # keep the take with the fewest problems, and say so
            k = min(range(len(takes)), key=lambda i: len(takes[i]["fillers"]) * 2
                    + len(takes[i]["repeats"]) + len(takes[i]["missing"]) * 3)
            best = (m.BUILD / f"{sid}_take{k}_{h}.mp3", m.BUILD / f"{sid}_take{k}_{h}.json")
            print(f"{sid:12s} no clean take in {MAX_TAKES}; using take {k}", flush=True)
        shutil.copy(best[0], final_mp3)
        shutil.copy(best[1], final_meta)
        report[sid] = {"accepted": best[0].name, "takes": takes}
    (ROOT / "video" / "takes_report.json").write_text(json.dumps(report, indent=1))
    bad = [s for s, r in report.items() if not any(t["ok"] for t in r["takes"])]
    print("ALL CLEAN" if not bad else f"NOT CLEAN: {bad}")


if __name__ == "__main__":
    main()
