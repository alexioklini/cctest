---
name: Discipline v2 (REFUSAL rewrite + negative few-shot) failed to fix Mistral refusal
description: 2026-05-01 — eval test of refusal-discipline rewrite reverted; mean got slightly worse (0.64→0.62); refusal axis only fixed F3, F1/F2 still fabricate
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Tested whether a more aggressive REFUSAL DISCIPLINE block in `project.json` instructions would close the −0.64 average refusal gap on Mistral Medium 3.5.

**Approach (discipline v2):**
- Reword trigger from "0 relevant drawers" → "the retrieved text does not contain the literal answer using the same subject vocabulary"
- Add YES/NO/NO branching self-test before composing
- Embed an explicit negative few-shot using **the literal F1 GwG question** as the worked example
- List "forbidden exit ramps" (training-data synthesis, padding refusal with general guidance, citing tangentially-related docs)
- Block size: 2400 chars added (5414 → 7814)

**Results vs baseline (results dir `20260501T100757_disc-none_discipline-v2`):**

| q | baseline | v2 | Δ |
|---|---|---|---|
| F1 GwG | 0.23 | 0.23 | 0 (no change — still fabricates FM-GwG content) |
| F2 Kreditvergabe | 0.43 | 0.33 | −0.10 (worse) |
| F3 Arbeitszeit | 0.33 | **0.90** | +0.57 (fixed) |
| Mean (all 15 Qs) | 0.64 | 0.62 | −0.02 |

**Why it didn't work — the F1 smoking gun:** Brain's F1 answer literally opens with *"in den durchsuchten Dokumenten der Wiener Privatbank finden sich keine expliziten interne Schwellenwerte"* (correct refusal opener), THEN proceeds to dump ~400 lines of FM-GwG training-data specifics (§13, §16, 10.000€, 15.000€, PEP rules) as if answering. The model recognizes the corpus has nothing AND proceeds anyway. This is a behavioral gap (cannot stop writing once started), not a comprehension gap.

**Collateral damage on non-refusal questions:** R2/P1/P3 slid from tie (~0.95) → loss (0.78/0.88/0.62). The longer REFUSAL block likely ate attention from the surrounding QUERY/PRECISION/CITATION disciplines.

**Conclusion:** Prompt edits alone won't close the refusal gap on Mistral Medium 3.5. The model's refusal *opener* is correct but it lacks the discipline to *stop*. Two paths remain:

1. **Server-side gate (the original option 1)**: post-retrieval classifier call ("does retrieved text contain the answer? yes/no"); if no, force-inject the canonical refusal sentence + short-circuit before the next round. The model never gets to write the post-refusal padding.
2. **Stop-token injection**: after the canonical refusal sentence, inject an end-of-turn signal so the model can't continue. Simpler than (1) but requires hooking into the agentic loop's text generation.

Reverted project.json from `project.json.bak-pre-discipline-v2`. The bak file was deleted after revert. Baseline run dir `20260501T092520_disc-none_medium-3.5` and v2 run dir `20260501T100757_disc-none_discipline-v2` both kept for comparison.

**Lesson for future eval-driven config tweaks:** longer prompts can regress unrelated questions even when the targeted axis improves. Always look at the FULL summary table, not just the changed axis, before deciding a tweak is a win.
