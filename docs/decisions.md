# Decisions

Each entry: what we tested, what we measured, what we decided. Newest first.

## 2026-10-26 (kickoff day, Day 1 spikes)

### Rule check: building starts at kickoff
The guide requires work to be "built during the event". The event kicked off Oct 26, 12:00 PM ET,
and the Oct 27 date is the schedule's label for the building period, not a rule. All commits are
public and timestamped after kickoff.

### OCR: Apple Vision, accurate mode
- Test: 5 portal pages at 1280x800, 74 ground-truth sensitive values (from `data-pii`).
- Accurate mode: **74/74 (100%)** of values recovered, mean 0.28 s per frame, max 0.71 s.
- Fast mode: 39/74 (53%). Misses names, dates, phones, emails. Rejected.
- Decision: on-device Apple Vision (`ocrmac`, accurate) for the Mac demo. Runs on the private
  plane by construction, costs nothing. A Linux OCR is needed for Modal bench runs (later).

### SAM 3.1: visuals only, run concepts in parallel
- Test: member page with a Muse Image generated insurance card, 6 concepts via Meta API.
- `id card` and `card`: one box tightly around the card (IoU 0.62 against the padded container,
  visually exact). `face`: the photo on the card, 0.84 s. `photo`: same region.
- `text`, `signature`: zero matches. SAM does not segment UI text.
- 6 concepts in parallel: 3.1 s wall. Single concept: 0.8 to 3.1 s.
- Decision: SAM handles visual PII only (faces, ID cards, photos, signatures). Text stays with
  OCR + validators + Glimmer, as the spec planned. Use a small fixed concept set per frame, in
  parallel, and only when the frame has image regions. Cost: $2.50 per 1,000 images per concept.
- The official parser (`meta-sam-parser`) is async only, so the pipeline is async.

### Agent model: Muse Spark 1.3 for computer use (Wonder rejects the tool)
- `muse-wonder-1.0` with `tools=[{"type":"computer"}]` returns HTTP 400
  `Unsupported tool type: 'computer'` on this account. Wonder accepts function tools.
- `muse-spark-1.3` accepts the computer tool. Raw (unprotected) run on the task "find Grace
  Nakamura, report her most recent claim status and billed amount": **correct answer in 4 actions**,
  23k input tokens (12.6k cached), about $0.02.
- Every computer_call carried `pending_safety_checks: computer_action_requires_confirmation`,
  even for a search-box click. The raw baseline acknowledges them; Veil will route them to its
  own policy so humans only see the risky ones.
- Decision: Spark 1.3 is the agent for now. Ask in Discord office hours whether Wonder computer
  use needs enablement. Keep Wonder for text-side work (function tools) if it never opens up.
- Two harness bugs fixed: screenshot taken mid-navigation (retry until load) and the agent
  clicking table cells that were not links (rows now navigate, as in real portals).

### Proxy: one-line adoption works
- Pass-through Responses proxy on :8787. The same raw agent with only `base_url` changed
  completed the task identically. Exchange log shows 1 screenshot out per turn (56 to 230 KB
  as base64) and the actions coming back. This is the hook point for veiling.

### Pending
- Glimmer on Modal: app written (`infra/modal_glimmer.py`), waiting on `modal setup`.
