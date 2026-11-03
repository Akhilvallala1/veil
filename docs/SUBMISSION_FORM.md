# Submission form answers (paste-ready)

## Which track are you submitting to?
Indie Developer

## Which problem statement did you build towards?
Build for Your World

## Most Viral Track: X post link
(Paste your X post link here once posted. Draft is in docs/SUBMISSION.md.)

## Project Description
Veil is a privacy firewall for computer-use agents. These agents work by sending screenshots to a cloud model, so every patient name, member ID, date of birth and insurance card on screen leaves the machine. That is why healthcare, insurance and finance can't use them.

Veil is a drop-in proxy: change one line, the agent's base_url. Before each screenshot leaves, on-device OCR plus Meta's open-weight Muse Glimmer and SAM 3.1, running on GPUs I control, find personal data and cover it. Real values become tokens like NAME_1, so the agent can still do the job: it types {{NAME_1}} and Veil fills in the real name locally. Every value remembers where it came from, and a policy file decides where it may be written, so a prompt injection that fools the model still can't send member data out. Sending, paying, or changing contact details waits for a human. Every step goes into a hash-chained audit log. It fails closed: if any check fails, the frame isn't sent.

Results on synthetic data: 0 of 170 sensitive values leaked on a held-out medical records app built after the rules were frozen (a typical regex redactor leaked 64%). A planted "workflow step" injection fooled the unprotected agent into sending member data in 4 of 8 runs; through Veil, 0 of 15. Task success matched the unprotected agent (6 of 6 vs 6 of 6), with about 2 seconds added per step at the median. Veil also runs as an MCP server.

## Public GitHub Repository
https://github.com/Akhilvallala1/veil

## 3-Minute Demo Video
https://youtu.be/k56rUhF-R4Q

## Describe your use of Meta products to build the submitted project
- Muse Spark 1.3 (Meta Model API, computer tool): the agent. It only ever receives masked screenshots and tokenized text.
- Muse Glimmer 30B (open weights): the detector. Served on my own H100 on Modal with vLLM in FP8. It reads the OCR'd screen with its layout and finds personal data that rules miss, such as names in lists and IDs next to unusual labels. On the held-out app, rules alone leaked 21%; with Glimmer, 0%.
- SAM 3.1 (open weights): served on my own A10G on Modal. It finds faces and ID cards so they are masked as regions. Checked with an independent face detector: 2 faces before, 0 after.
- Muse Image: generated the synthetic faces and insurance card used in the benchmark, so no real person's data was ever used.
- Muse Wonder: planned as the agent, but the Model API currently rejects the computer tool for Wonder (400). Organizers confirmed and advised Spark, and I filed a support ticket. Veil switches models with no code change.

## Why are Meta's models important to your solution?
The split between Meta's open-weight and hosted models is the privacy boundary itself. Raw pixels and raw text only touch models the customer runs (Glimmer and SAM 3.1, open weights on their own GPUs), and the hosted model (Spark) only sees masked frames. Without strong open-weight models, the "guard" would have to be a hosted API, which means sending the raw data out anyway. With them, a hospital can keep the sensitive half on its own hardware and still use a frontier hosted agent for the work.

## Feedback on partner technologies (Fireworks, Modal, Baseten)
Modal: I used it to serve both open-weight models (Muse Glimmer 30B on an H100 with vLLM FP8, SAM 3.1 on an A10G), each behind a bearer key with scale-to-zero. Deploying was fast and secrets handling was clean. Cold starts of about 2 to 3 minutes for the 30B model were the main friction, and idle-warm time made per-step cost at hackathon scale much higher than at volume ($0.049 all-in vs about $0.005 marginal per step). A cheap way to keep one GPU warm only during active sessions would help.
Fireworks and Baseten: not used for this project.

## How likely are you to build with Meta Model API?
(Your call. Suggested: Likely.)

## How would you describe your overall opinion of Meta Model API?
(Your call. Suggested: Positive.)

## How likely are you to recommend the Meta Model API to another developer or engineering team building with LLMs?
(Your call, 0 to 10. Suggested: 8.)

## How likely are you to recommend Muse Code to another developer or engineering team?
I didn't use Muse Code for this project, so pick the neutral or N/A option if there is one.

## What was the biggest obstacle your team hit while building this project?
Muse Wonder doesn't accept the computer tool on the Model API (400 "Unsupported tool type"), so I built the agent on Spark 1.3 instead. Beyond that: running Glimmer 30B on a 24 GB Mac was too slow, so the private models moved to my own GPUs on Modal. Masking a link made the agent miss it by a few pixels, so Veil now snaps near-miss clicks onto links it masked. And an address-change attack showed that a data-flow rule alone can't see changes made inside one app, which led to human approval for contact and payment changes.

## Compared with other AI models your team has used for the same kind of work, how did Meta's models perform on this project?
Spark 1.3 drove the browser reliably with the computer tool and kept working on masked screens: it matched tokens to the right rows and completed tasks as often as on raw screens. Glimmer was the standout: as a layout-aware detector it closed the gap rules left on an unseen app (21% leaked down to 0%), and having it as open weights is what makes the privacy design possible. SAM 3.1 segmented faces and ID cards cleanly on UI screenshots. Spark did follow a well-disguised injection about half the time, which is exactly why Veil enforces policy outside the model.

## When your team chooses an AI model or coding tool, what matters most?
Whether it can run where the data is allowed to be (open weights for the sensitive part), measurable accuracy on our own held-out data, tool-use reliability, and cost per task.

## What makes it hardest for your team to switch to a different AI model or coding tool?
Re-validating it. Every model change means re-running the leak and attack benchmarks and re-tuning prompts and tool-call handling. Veil's proxy keeps the code change to one line, but the evidence has to be rebuilt each time.

## If your team took this project to production, what would most likely stop you from using Meta's models?
For healthcare data, the hosted side would need compliance terms (a business associate agreement, data retention and residency) confirmed, even though it only sees masked frames. Computer use on Muse Wonder isn't available on the API yet. And during testing the API returned short bursts of 500/503 errors, so production would need clearer reliability guarantees.
