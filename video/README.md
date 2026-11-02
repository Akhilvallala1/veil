# Veil demo video

`veil_demo.mp4`: 1920x1080, 30 fps, 3:09, voiceover and burned-in captions. It isn't committed
(13 MB); rebuild it with:

```bash
uv run python video/make_video.py            # full video -> video/veil_demo.mp4 (~4 min)
uv run python video/make_video.py --scene 06 # one scene, for a quick look
```

- **Every screen is a real frame** from recorded Veil runs in `runs/`, with synthetic data only:
  - the member page, raw and veiled: session `s_a819e2830a` step 4;
  - the planted claim note and the message the unprotected agent wrote: `runs/attack/raw_workflow_trial3`;
  - the empty Messages page through Veil: `s_0c6352f9fd`;
  - the model's answer, word for word: `s_22fd15fe30`.
- **Every number** matches the README and `bench/results/`.
- **Narration:** a synthetic Microsoft neural voice (edge-tts, `en-US-AndrewMultilingualNeural`). It is not the author's voice and no voice cloning is involved. The words are in `video/script.py`.
- **Using your own voice:** record one file per scene as `video/voice/<id>.mp3`, using the ids in `video/script.py` (`01_hook` ... `11_close`). Then run
  `uv run python video/make_video.py --own-voice`. Each scene re-times to your recording. Animation cues are tuned to the default voice's pacing, so a much slower or faster read may need small tweaks.
