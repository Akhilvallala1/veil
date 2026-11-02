# Veil demo video

`veil_demo.mp4`: 1920x1080, 30 fps, about 3:11, narrated in the author's own voice (his ElevenLabs
voice clone), with burned-in captions. It isn't committed (14 MB); rebuild it with:

```bash
uv run --with openai-whisper python video/clean_takes.py   # make + verify clean voice takes
uv run python video/make_video.py --eleven                 # render with them
uv run python video/make_video.py                          # fallback: Microsoft neural voice
```

## Voice

- **Whose voice:** the author's own ElevenLabs voice clone ("Akhil"), used with his consent at his request. It needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `.env`, which git ignores.
- **Settings:** chosen so every line is free of fillers, using his voice clone:
  - Eleven Multilingual v2
  - stability 0.9, similarity 0.8, style 0, speaker boost on, speed 0.88 (a slow, clear read at about 157 words a minute)

  His UI settings (stability 0.55, style 0.2) sounded more expressive, but the clone then reproduced "uh" and "um" sounds from its training audio. A filler-aware check counted 12 fillers and a stutter in the first three scenes alone.
- **Clean takes only:** `video/clean_takes.py` generates each line, transcribes it with Whisper primed to write fillers out, and rejects any take with an "uh", "um", a repeated word, or a misheard "Veil". It keeps re-generating, up to 4 takes. Result: all 11 lines were clean on the first take. In the final video's soundtrack: 481 words, **0 fillers, 0 repeats**, and "Veil" heard correctly 11 of 11 times. Each take's transcript is in `video/takes_report.json`.
- **Pronunciation, checked by transcribing every line with Whisper:**
  - "Veil" was heard as "whale" or "Vail", so the voice is sent the spelling "Vayl", which transcribes as "Veil".
  - "redaction" was heard as "reduction", so the voice says "redacting".
  - "MCP" is spelled out as "M C P".
  - Captions always show the script's spelling.
- **Re-takes:** an earlier take of the attack scene said "without fail" instead of "without Veil". Such takes are now caught by the check above.
- **Sync:** the animation cues were tuned to the reference voice. The renderer re-times them to each sentence of this voice using ElevenLabs' per-character timestamps, so callouts appear as the words are spoken.

## What's on screen

- **Every screen is a real frame** from recorded Veil runs in `runs/`, with synthetic data only:
  - the member page, raw and veiled: session `s_a819e2830a` step 4;
  - the planted claim note and the message the unprotected agent wrote: `runs/attack/raw_workflow_trial3`;
  - the empty Messages page through Veil: `s_0c6352f9fd`;
  - the model's answer, word for word: `s_22fd15fe30`.
- **Every number** matches the README and `bench/results/`.
- **Words:** the narration is in `video/script.py`. Edit it and re-run; only changed lines are re-generated, since audio is cached by text and settings.
