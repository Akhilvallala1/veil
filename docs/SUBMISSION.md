# Veil: submission drafts

Paste-ready text for the hackathon form and social posts. Every number links back to a file in
`bench/results/` and is explained in the README. Update the video link before submitting.

---

## Name

Veil

## Tagline (one line)

A privacy firewall for computer-use agents: the cloud model sees the screen's work, never the person on it.

## Track

Indie

## Short description (about 50 words)

Computer-use agents read screenshots, so the patient names, member IDs and dates of birth on screen go to a cloud model. Veil sits in between. Open-weight Meta models you run (Muse Glimmer, SAM 3.1) mask personal data before a frame leaves, swap real values for tokens, and block actions that would move data where it shouldn't go.

## Full description

### The problem

Computer-use agents are ready for the work that fills most office hours: claims, prior authorizations, patient records, billing. That work happens on screens full of regulated data. A computer-use agent sends its screenshots, with that data visible, to a hosted model. Healthcare, insurance and finance can't adopt agents on those terms.

### What Veil does

Veil is a drop-in proxy. Change one line, the agent's `base_url`, and every step goes through it:

1. **Mask.** On-device OCR reads the screenshot. Rules, **Muse Glimmer 30B** (open weights, on your own GPU) and **SAM 3.1** (open weights, on your own GPU) find names, IDs, dates of birth, addresses, faces and ID cards. They are covered before the frame leaves.
2. **Tokenize.** "Grace Nakamura" becomes `NAME_1` everywhere: on screen, in the task, in every later frame. The agent can still match the right row and type `{{NAME_1}}`. Veil fills in the real value locally.
3. **Firewall actions.** Every value remembers where it was read. A YAML policy says where it may be written. A member ID from the payer portal can go back into the portal, never into a message to an outside address. This holds even when the model is fully hijacked by a prompt injection.
4. **Ask a human** before Send, Pay, Delete or any change to contact or bank details.
5. **Prove it.** Every step lands in a hash-chained audit log, and a console shows the real screen next to what the model saw.

Veil fails closed. If any detector errors, or the masked frame still shows a real value, that frame is not sent.

### How we used Meta's models

| Model | Where it runs | Job |
|---|---|---|
| Muse Spark 1.3 | Meta Model API | The agent (computer tool). Sees masked frames only. |
| Muse Glimmer 30B | Our own H100 (Modal, vLLM, FP8) | Reads the screen text with its layout and finds personal data that rules miss. |
| SAM 3.1 | Our own A10G (Modal) | Finds faces and ID cards so they're masked as regions. |
| Muse Image | Meta Model API | Generated the synthetic faces and insurance card for the benchmark. |

The split between open-weight and hosted models is the privacy boundary. Raw pixels and raw text only touch models you run. The hosted model only sees masked frames. We planned Muse Wonder as the agent, but the Model API doesn't accept the computer tool for Wonder yet. The organizers confirmed this and advised Spark 1.3. Veil switches models with no code change.

### Results (synthetic data only)

- **Leaks:** 0 of 170 sensitive values readable in frames sent to the cloud, on a held-out EHR built after the rules were frozen. 0 of 246 on the development portal. For comparison, a typical regex redaction tool leaked 64% and 72% on the same screens.
- **Prompt injection:** a hidden "workflow step" fooled the unprotected agent into exfiltrating member data 4 times in 8 runs. Through Veil: 0 of 15. In 13 of them the model was fooled, and Veil's flow policy blocked the action anyway.
- **Replaying real attacks:** every action the unprotected agent took in successful attacks, replayed through Veil's firewall: 3 of 3 address hijacks and 4 of 4 data exfiltrations would have stopped for a human. Ordinary tasks: 0 prompts.
- **Task success:** 6 of 6 through Veil vs 6 of 6 unprotected on the current code, with 0 extra approval prompts.
- **Speed:** a median 1.95 s added per step across 393 live steps. The model's own think time is a median 12 s.
- **Cost:** no added Meta API cost. Masked frames are the same size. About $0.005 per step of GPU time at volume.

### What we're honest about

- The attack sample is small. It shows the mechanism works when the model is fooled. It isn't a rate to quote.
- 7.3% of harmless words get masked on the held-out EHR. That's the safe direction, but it isn't zero.
- Running Glimmer on a 24 GB Mac works, but it's too slow. The private models run on your own GPU.
- macOS only for now, because OCR runs on-device with Apple Vision.

### Challenges

- Masking a link made the agent miss it by a few pixels, so Veil now moves near-miss clicks onto links it masked itself.
- An address-change attack showed that a data-flow rule can't see a change made inside one app. That led to human approval for contact and payment changes.
- Replaying real attacks found two gaps in the approval check: clicks on the edge of a button, and Tab then Enter. Both are fixed and tested.

### What's next

- A fine-tuned "Veil-Glimmer" for faster detection and less over-masking.
- Fully local mode on 32 GB+ Macs.
- Desktop apps beyond the browser.
- A policy pack for HIPAA's minimum-necessary standard.
- Audit export for compliance reviewers.

Veil already runs as an MCP server, so other agent frameworks can use it today.

## Links

- Code: https://github.com/Akhilvallala1/veil
- Video: <add link>

## Built with

Python, FastAPI, Playwright, Apple Vision OCR, vLLM, Modal, Muse Spark 1.3, Muse Glimmer 30B, SAM 3.1, Muse Image, MCP

---

## X post (main)

> AI agents that use a computer send screenshots to the cloud: patient names, member IDs, dates of birth.
>
> Veil masks them first, using open-weight Muse Glimmer + SAM 3.1 on your own GPU.
>
> Held-out test: 0 of 170 leaked. Regex redaction: 64%.
>
> [video]
> github.com/Akhilvallala1/veil

(265 characters with the GitHub link, which X counts as 23 whatever its length. Every thread post below is under 210. Attach the video file to the post rather than adding a second link, which would take it over 280. Add the hackathon's official hashtag or handle only after checking the exact one on the hackathon page.)

## X thread (optional follow-ups)

2/ Prompt injection: a fake "step 2 of 2" note fooled the unprotected agent into sending member data in 4 of 8 runs. Through Veil: 0 of 15. The model was still fooled. Veil's flow policy blocked the action.

3/ Veil swaps real values for tokens. The agent sees NAME_1, types {{NAME_1}}, and Veil fills in the real name locally, only where policy allows it.

4/ Drop-in: change one line, base_url. Also runs as an MCP server. Every step goes into a hash-chained audit log.
