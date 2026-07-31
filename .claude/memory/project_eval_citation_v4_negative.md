---
name: Citation discipline v4 — short hard rule + self-review (no improvement)
description: 2026-05-01 — shortened CITATION DISCIPLINE block from 2577→1320 chars with a self-review step before sending; brain mean stayed flat at 0.74; the 5 user-flagged citation failures (P2/P3/M2/C2/C3) showed ZERO movement on citation axis
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Tested whether reformulating the CITATION DISCIPLINE block as a hard rule + self-review step would force Mistral Medium 3.5 to emit per-claim verbatim quotes more reliably.

**The change:** rewrote the discipline block from 2577 chars to 1320 chars. Kept the hard requirement ("every sentence and every bullet with a factual claim MUST end with [Quelle: ... — '...']"), removed the worked examples (per `feedback_prompt_bloat_regression`), added an explicit self-review step before sending ("read every sentence; if it has a factual claim and no bracket, DELETE it"), kept the no-fabricated-§N rule.

**Results vs no-proactive baseline (results dir `20260501T115323_disc-none_citation-v4`):**

```
                            no-proactive    citation-v4    Δ
brain mean                  0.752           0.737         −0.015
measured Δ                 −0.137          −0.150         −0.013
```

**Per-question citation-axis movement on the user-flagged failures (Q5/Q6/Q8/Q13/Q14/Q15):**

| q | citation no-proactive | citation v4 | Δ |
|---|---|---|---|
| Q5 P2_archivierung | 0.0 | 0.0 | 0 |
| Q6 P3_loeschfristen | 0.5 | 0.5 | 0 |
| Q8 M2_neuer_mitarbeiter | 0.3 | 0.3 | 0 |
| Q13 C1_ki_policy | 0.5 | 1.0 | +0.5 ✓ |
| Q14 C2_passwort | 0.0 | 0.0 | 0 |
| Q15 C3_isms_ziele | 0.0 | 0.0 | 0 |

**Five out of six user-flagged citation failures showed ZERO movement.** Only C1 actually responded to the rewrite. Mean brain score went DOWN slightly (0.75→0.74), with notable F3 regression (0.93→0.50 — Mistral started searching instead of refusing, the shorter discipline block likely weakened the refusal anchor).

**Conclusion: prompt-only edits cannot fix Mistral Medium 3.5's citation discipline.** Whatever determines whether Mistral cites per-claim or not in a given response, it is NOT prompt-side configurable. The same model on the same questions with the same retrieval flow either gets it right or gets it wrong, regardless of how the rule is worded — strong, soft, short, long, with worked examples, with self-review steps, with hard "delete if no bracket" instruction.

**This is the third consecutive prompt-side experiment to fail on the structural Brain failure modes**:
1. discipline-v2 (longer REFUSAL with negative few-shot) — regressed
2. hoist-refuse + .md/binary cleanup (structural reorder) — regressed
3. citation-v4 (shorter hard rule + self-review) — flat / slight regression

**Path forward — server-side enforcement, not prompt:**
- Post-process validation hook: after the model emits its response, scan for sentences/bullets without `[Quelle: ...]` brackets. Either reject the response and force a re-generation round with explicit feedback, or rewrite the response to drop unciteable claims, or just flag the gap to the user. This is option C from earlier — heaviest fix but the only one that's deterministic.
- Output format prefill: prefix the model's response with a numbered structure so each item starts in the citation pattern. Risk: not all OpenAI-compatible providers support prefill cleanly.
- Different model: Mistral Medium 3.5 may simply not have the citation-discipline behavior we want. A specialised reasoning model (Magistral) or a larger model (mistral-large) might. Worth a single eval run to check before committing to server-side enforcement.

Reverted to no-proactive baseline (project.json restored from `.bak-pre-citation-v4`, then bak deleted; Brain restarted).
