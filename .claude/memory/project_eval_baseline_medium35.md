---
name: First eval baseline — Mistral Medium 3.5 vs Opus 4.7 (2026-05-01)
description: 2026-05-01 — full 15Q eval/run.py result; means gold 0.91 / brain 0.64 / Δ −0.28; refusal and wrong-doc-citation are Brain's two structural failure modes
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
First full run of the eval harness against the new Mistral Medium 3.5 (`mistral-vibe/mistral-medium-3.5`) released 2026-05-01. Results dir: `eval/results/20260501T092520_disc-none_medium-3.5/`.

**Headline:** Brain mean 0.64 vs Opus mean 0.91, Δ −0.28. Wins 9 / ties 5 / Brain 1.

**The one Brain "win" (R3 Kryptographie, brain 0.98 vs gold 0.25) was a Claude Code meta-ack glitch**, not a real win — Opus's `--output-format json` `result` field carried "Acknowledged — the background find command completed..." with `num_turns=1` instead of the actual answer. Only 1/15 questions hit this trap so the dataset is still mostly clean. R3 should be excluded when computing the "real" gap; effective Δ is closer to −0.32.

**Two structural Brain failure modes:**

1. **Refusal failure on every refusal question** (F1/F2/F3, avg Δ −0.64). Despite the explicit REFUSAL DISCIPLINE block in `project.json`, Mistral Medium 3.5 fabricates content from training data instead of refusing cleanly:
   - F1 GwG → fabricates full FM-GwG policy (0.23 vs 0.97)
   - F2 Kreditvergabe → buries refusal under invented CHECK24-Festgeld content (0.43 vs 0.97)
   - F3 Arbeitszeit → hedges then fabricates (0.33 vs 0.97)
   Opus refuses cleanly even with NO disciplines injected. The discipline block is not what's making Opus refuse — its native judgment is.

2. **Retrieval-miss + wrong-doc-citation** (P2 Archivierung 0.15, C2 Passwort 0.15, M2 MA-Eintritt 0.38, C3 ISMS-Ziele 0.42). Brain finds *a* document, doesn't verify it actually contains the answer, then cites it confidently. Opus reads the source first and silently rejects irrelevant hits.

**Where Brain ties or near-ties (6/15):** R2 Morgencheck, P1 Password (per-system numbers, well-cited), P3 Löschfristen, M3 Cloud, C1 KI-Policy bullets, R1 Multilogin (close: 0.80 vs 0.90). These are questions where retrieval is unambiguous (single canonical document with the answer in the title) and the model just needs to read + quote.

**The Mistral Small → Medium 3.5 jump is real but small.** R1 went 0.68 (smoke test, Small 2603) → 0.80 (Medium 3.5). Maybe 0.10–0.15 mean improvement. Not enough to close a 0.27 gap.

**Calibration note on the harness:** baseline establishes the gap is mostly composition + judgment + refusal, not retrieval infrastructure. Future config tweaks (KG re-enable, closet rerank, prompt edits) should be measured by re-running `eval/run.py --label <change>` and comparing against this baseline run dir. The 0.28 gap is the number to beat.

**One known harness gotcha to fix before the next run:** R3-style meta-ack on `--output-format json` happened once. The fix is to switch to `--output-format stream-json` and concatenate all assistant text blocks rather than relying on the `result` field. Deferred — 1/15 noise rate is tolerable for now.
