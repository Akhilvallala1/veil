"""Turn the author's own read of RECORDING_SCRIPT.md into clean, per-scene narration.

  uv run --with openai-whisper python video/own_voice.py video/voice/full.m4a
  uv run python video/make_video.py --own-voice

Steps
1. Clean the audio (ffmpeg): high-pass 80 Hz, de-hum, spectral noise reduction, gentle
   compression, de-ess, loudness to -16 LUFS. Mono 48 kHz.
2. Transcribe with word timestamps (Whisper small.en, primed to write fillers out).
3. Align the transcript to the script word by word (difflib on normalized tokens). For
   every script sentence, take the LAST complete reading of it, so a re-read after a stumble
   replaces the first try. Words not in the script (uh, um, false starts) are dropped.
4. Cut each sentence from the cleaned audio with a short margin and fades, and join the
   sentences of a scene with natural 0.35 s gaps (0.5 s after a question or the short beats
   "Name." etc. stay as read).
5. Write video/voice/<scene>.wav and <scene>.json (sentence start/end in the new clip), which
   make_video.py --own-voice uses to time captions and re-time every animation cue.
6. Report: per scene, sentences found, anything missing or with low match, fillers removed.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import whisper

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "video"))
from script import SCENES  # noqa: E402

VOICE_DIR = ROOT / "video" / "voice"
PRIME = "Umm, so, uh, Veil is, like, a privacy firewall. Hmm, uh, the model, um, sees tokens."
SR = 48000
FILTERS = ("highpass=f=80,lowpass=f=15000,"
           "afftdn=nr=12:nf=-50:tn=1,"            # broadband noise reduction, tracks noise
           "equalizer=f=60:t=q:w=1:g=-6,"          # mains hum region
           "acompressor=threshold=-20dB:ratio=3:attack=10:release=150:makeup=2,"
           "deesser=i=0.4,"
           "loudnorm=I=-16:TP=-1.5:LRA=9")

# How the script is written vs how it is spoken, so words line up.
SPOKEN = [(r"\bM C P\b", "mcp"), (r"\bNAME one\b", "name one"),
          (r"\bMEMBER ID one\b", "member id one"), (r"\bon-device\b", "on device"),
          (r"\bopen-weight\b", "open weight"), (r"\bhash-chained\b", "hash chained"),
          (r"\btamper-evident\b", "tamper evident")]
NUM = {"64": "sixty four", "21": "twenty one", "170": "one hundred seventy", "15": "fifteen",
       "8": "eight", "4": "four", "2": "two", "3.1": "three point one", "1": "one",
       "0": "zero"}


def toks(s: str) -> list[str]:
    s = s.lower()
    for a, b in SPOKEN:
        s = re.sub(a.lower(), b, s)
    s = s.replace("%", " percent").replace("-", " ")
    out = []
    for w in re.findall(r"[a-z0-9.']+", s):
        w = w.strip(".'")
        if not w:
            continue
        out += NUM.get(w, w).split()
    return out


def sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if p.strip()]


def clean(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(SR),
                    "-af", FILTERS, str(dst)], check=True)


_ENV: dict = {}


def snap_to_quiet(wav: Path, a: float, b: float) -> tuple[float, float]:
    """Move cut points to the quietest 10 ms within 0.15 s, so cuts land in silence."""
    import numpy as np
    if wav not in _ENV:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1",
                              "-ar", "16000", "-"], capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        n = len(x) // 160
        _ENV[wav] = np.sqrt((x[: n * 160].reshape(n, 160) ** 2).mean(axis=1))
    env = _ENV[wav]

    def best(t, lo, hi):
        i0, i1 = max(0, int((t + lo) * 100)), min(len(env) - 1, int((t + hi) * 100))
        return t if i1 <= i0 else (i0 + int(env[i0:i1].argmin())) / 100
    return best(a, -0.15, 0.0), best(b, 0.0, 0.15)


def main(src: str) -> None:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = VOICE_DIR / "_cleaned.wav"
    clean(Path(src), cleaned)
    model = whisper.load_model("small.en")
    r = model.transcribe(str(cleaned), language="en", initial_prompt=PRIME,
                         word_timestamps=True, temperature=0)
    heard = [(w["word"], w["start"], w["end"]) for s in r["segments"] for w in s["words"]]
    htoks, hidx = [], []           # spoken tokens and which heard word each came from
    for i, (w, _, _) in enumerate(heard):
        for t in toks(w):
            htoks.append(t)
            hidx.append(i)

    report = {}
    cursor = 0                       # sentences are read in order: search forward only
    for scene in SCENES:
        sid = scene["id"]
        sents = sentences(scene["say"])
        spans, rep = [], {"sentences": len(sents), "found": 0, "weak": [], "missing": []}
        for sent in sents:
            st = toks(sent)
            # Find every reading of this sentence ahead of the cursor; keep the last good one
            # that comes before the next sentence's first reading.
            best = None
            window = htoks[cursor: cursor + 400]
            sm = difflib.SequenceMatcher(None, window, st, autojunk=False)
            blocks = [b for b in sm.get_matching_blocks() if b.size]
            if blocks:
                # group matches into candidate readings separated by big jumps
                cands, cur = [], [blocks[0]]
                for b in blocks[1:]:
                    if b.b <= cur[-1].b:      # sentence restarted: a re-read
                        cands.append(cur)
                        cur = [b]
                    else:
                        cur.append(b)
                cands.append(cur)
                scored = [(sum(b.size for b in c) / len(st), c) for c in cands]
                good = [x for x in scored if x[0] >= 0.6]
                score, c = (good[-1] if good else max(scored, key=lambda x: x[0]))
                a = cursor + c[0].a
                z = cursor + c[-1].a + c[-1].size - 1
                best = (score, a, z)
            if not best or best[0] < 0.45:
                rep["missing"].append(sent)
                continue
            score, a, z = best
            if score < 0.8:
                rep["weak"].append({"sentence": sent, "match": round(score, 2)})
            w0, w1 = heard[hidx[a]], heard[hidx[z]]
            spans.append([w0[1], w1[2], sent])
            rep["found"] += 1
            cursor = z + 1
        # Whisper word times can start late / end early by 0.1-0.3 s, clipping first and last
        # syllables. Widen each sentence into the silence around it (up to 0.35 s before,
        # 0.4 s after, never into the next sentence), then snap cuts to the quietest point.
        for i, sp in enumerate(spans):
            prev_end = spans[i - 1][1] if i else 0.0
            nxt_start = spans[i + 1][0] if i + 1 < len(spans) else sp[1] + 1.0
            sp[0] = max(prev_end + 0.02, sp[0] - 0.35, 0.0)
            sp[1] = min(nxt_start - 0.02, sp[1] + 0.4)
            sp[0], sp[1] = snap_to_quiet(cleaned, sp[0], sp[1])
        # Cut and join this scene's sentences.
        parts, meta, t = [], [], 0.0
        for i, (a, b, sent) in enumerate(spans):
            seg = VOICE_DIR / f"_{sid}_{i:02d}.wav"
            d = b - a
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}",
                            "-i", str(cleaned), "-af",
                            f"afade=t=in:d=0.02,afade=t=out:st={max(d - 0.04, 0):.3f}:d=0.04",
                            str(seg)], check=True)
            meta.append([round(t, 3), round(t + d, 3), sent])
            parts.append(seg)
            gap = 0.5 if sent.endswith("?") else 0.35
            t += d + gap
            sil = VOICE_DIR / f"_{sid}_{i:02d}_gap.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{gap}", "-i",
                            f"anullsrc=r={SR}:cl=mono", str(sil)], check=True)
            parts.append(sil)
        if parts:
            lst = VOICE_DIR / f"_{sid}.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in parts[:-1]))
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i",
                            str(lst), "-c", "copy", str(VOICE_DIR / f"{sid}.wav")], check=True)
            (VOICE_DIR / f"{sid}.json").write_text(json.dumps(meta, indent=1))
        report[sid] = rep
        flag = "OK" if rep["found"] == rep["sentences"] and not rep["weak"] else "CHECK"
        print(f"{sid:12s} {rep['found']}/{rep['sentences']} sentences  {flag}"
              + (f"  missing: {rep['missing']}" if rep["missing"] else "")
              + (f"  weak: {rep['weak']}" if rep["weak"] else ""), flush=True)
    fillers = [w for w, _, _ in heard if re.fullmatch(r"\W*(uh+|um+|hmm+|er+|ah+)\W*", w, re.I)]
    report["_fillers_removed"] = len(fillers)
    (VOICE_DIR / "_report.json").write_text(json.dumps(report, indent=1))
    print(f"fillers heard and removed: {len(fillers)}")


if __name__ == "__main__":
    main(sys.argv[1])
