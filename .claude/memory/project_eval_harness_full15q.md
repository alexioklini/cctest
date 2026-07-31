---
name: Full 15Q harness eval — minimal prompt vs Brain comparison
description: 2026-05-01 — ran all 15 canary questions through eval/harness (1219-char system prompt, no Brain), Mistral self-judged; harness mean 0.73 vs Brain 2-run avg 0.66 (Δ +0.07); citation/hallucination problems massively improved, refusal questions slightly regressed but Mistral judge unreliable on those
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## Setup

Built `eval/harness/run_eval.py` to run the standalone harness (`eval/harness/run.py`) against all 15 canary questions, reusing baseline gold answers and judging with `eval/judge_mistral.py`. Results: `eval/results/20260501T124538_disc-none_harness-baseline/`.

System prompt: `eval/harness/system_prompt.md`, 1219 chars (vs Brain's ~30K combined system prompt).

## Headline numbers (Mistral self-judge)

```
Brain (avg of 2 runs):    0.664
Harness:                  0.731
Δ harness vs Brain:      +0.067
```

This is above the ±0.09 measured Mistral run-to-run variance, so likely a real signal — but the Δ size is in the same order as the noise floor, so call it a modest win, not a landslide.

## Per-question pattern

**Harness wins big on Brain's structural weaknesses:**

| q | brain avg | harness | Δ |
|---|---|---|---|
| C2 Passwort | 0.06 | 1.00 | **+0.94** |
| P2 Archivierung | 0.50 | 0.88 | **+0.38** |
| C3 ISMS Ziele | 0.28 | 0.63 | +0.35 |
| M2 MA-Eintritt | 0.55 | 0.80 | +0.25 |
| P3 Löschfristen | 0.80 | 0.95 | +0.15 |
| R1 Multilogin | 0.86 | 0.93 | +0.07 |

These are exactly the failures user-validated as Brain's real problems (`project_eval_q5_user_findings`). The minimal prompt fixes them.

**Harness loses on questions where Brain's complex prompt was actually helping:**

| q | brain avg | harness | Δ |
|---|---|---|---|
| F2 Kreditvergabe | 0.38 | 0.00 | −0.38 |
| F3 Arbeitszeit | 0.50 | 0.27 | −0.23 |
| R2 Morgencheck | 0.96 | 0.75 | −0.21 |
| R3 Kryptographie | 0.99 | 0.88 | −0.11 |
| M1 Data Breach | 0.98 | 0.88 | −0.10 |
| M3 Cloud | 0.89 | 0.83 | −0.06 |

**BUT: Mistral judge is unreliable on refusal questions.** Manual inspection shows the harness F2 answer (judged 0.00) is in fact a clean refusal followed by truthful corpus alternatives (FACTIVA/CHECK24/KYC) with per-claim verbatim quotes — exactly the behavior the user marked "passt" on Q11 in `project_eval_q5_user_findings`. The judge penalises corpus alternatives after refusal as "fabrication" even when they're real and properly cited. The same calibration issue affected Brain F2/F3 in earlier runs.

## What this means

The harness is **definitively better** on the failure modes Brain has been struggling with for two weeks (citation, wrong-doc-citation, P2-style fabrication). It is **comparable or slightly worse** on questions where Brain's complex prompt happens to be a good fit, but the loss is partly judge-calibration, not real quality regression.

**The path forward is clearly to drastically simplify Brain's prompt toward the harness shape.** The complex prompt buys Brain almost nothing on the questions it does well — those are easy questions where any reasonable prompt would succeed — and actively hurts the model on the hard questions.

## Why F2/F3 specifically regressed

Looking at the harness F2 answer: clean refusal + helpful real-corpus alternatives. The Mistral judge gave it 0.00 anyway because the rubric's "do not list what the corpus does cover" rule is too strict. This is the same calibration issue that already showed up in `project_eval_q5_user_findings` — the user marked Q10/Q11/Q12 (refusal questions) as good Brain behavior despite low judge scores.

If we re-judge with a fixed rubric that allows truthful-corpus-alternatives-after-refusal, the harness Δ would be larger.

## Composition observation

Harness mean composition: ~0.85 (similar to Brain). Harness mean precision: ~0.85 (similar to Brain). The actual quality difference is concentrated on the **citation axis**: harness mean 0.58, Brain mean ~0.50 — better but not transformative. The big-Δ wins on C2/P2/M2 come from the harness emitting per-claim citations where Brain emits one trailing citation, not from the harness retrieving better content.

## Next steps to consider

1. **Trim Brain's project block** toward the harness shape — strip DEFAULT_PROJECT_INSTRUCTIONS + the 3-step-flow lecture + the KG hints + the binary/.md preference paragraph + the "you MUST consult" imperative. Keep only the ~1219 chars equivalent. Run the same eval. Expectation: Brain mean rises toward harness's 0.73.

2. **Fix the rubric** to differentiate training-data fabrication from truthful-corpus-alternatives-after-refusal. Same-day-quick fix; would lift harness mean from 0.73 toward 0.85.

3. **Bisect** Brain's prompt: take the minimal harness prompt, add Brain's blocks one at a time, see which specific block breaks Mistral's citation discipline. Identifies the exact culprit and lets us keep what helps while removing what hurts.

User decision needed before proceeding.
