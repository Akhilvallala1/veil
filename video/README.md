# Veil demo video

`veil_demo.mp4`: 1920x1080, 30 fps, about 3:22, narrated in the author's own voice (his ElevenLabs
voice clone), with burned-in captions. It isn't committed (15 MB); rebuild it with:

```bash
uv run python video/make_video.py --eleven       # author's cloned voice (needs keys in .env)
uv run python video/make_video.py                # fallback: Microsoft neural voice (edge-tts)
uv run python video/make_video.py --eleven --scene 06   # one scene, for a quick look
```

## Voice

- **Whose voice:** the author's own ElevenLabs voice clone ("Akhil"), used with his consent at his request. It needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `.env`, which git ignores.
- **Settings:** exactly as he set them in the ElevenLabs UI, confirmed against `GET /v1/voices/{id}/settings`:
  - Eleven Multilingual v2
  - speed 1.0, stability 0.55, similarity 0.75, style 0.2, speaker boost on
- **Pronunciation, checked by transcribing every line with Whisper:**
  - "Veil" was heard as "whale" or "Vail", so the voice is sent the spelling "Vayl", which transcribes as "Veil".
  - "redaction" was heard as "reduction", so the voice says "redacting".
  - "MCP" is spelled out as "M C P".
  - Captions always show the script's spelling.
- **Re-takes:** one take of the attack scene said "without fail" instead of "without Veil", so it was re-generated and re-checked. Earlier takes are kept in the build cache as `*.take1.*`.
- **Sync:** the animation cues were tuned to the reference voice. The renderer re-times them to each sentence of this voice using ElevenLabs' per-character timestamps, so callouts appear as the words are spoken.

## What's on screen

- **Every screen is a real frame** from recorded Veil runs in `runs/`, with synthetic data only:
  - the member page, raw and veiled: session `s_a819e2830a` step 4;
  - the planted claim note and the message the unprotected agent wrote: `runs/attack/raw_workflow_trial3`;
  - the empty Messages page through Veil: `s_0c6352f9fd`;
  - the model's answer, word for word: `s_22fd15fe30`.
- **Every number** matches the README and `bench/results/`.
- **Words:** the narration is in `video/script.py`. Edit it and re-run; only changed lines are re-generated, since audio is cached by text and settings.
