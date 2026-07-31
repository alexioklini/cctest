---
name: Mistral Small 4 quality on policy-reproduction tasks is highly stochastic — score varies 1/7 to 5.5/7 across runs with identical prompt/corpus
description: 2026-04-29 same canary same model same corpus — sessions returned 5.5/7, 1.5/7, 1/7, 3.5/7 across the day; Mistral Small invents details (formulas, values) when it doesn't reproduce verbatim
type: feedback
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
End of 2026-04-29 measurement on the IT-Risk Score canary (`project_it_risk_score_canary_answer.md`), all with `mistral-experimental/mistral-small-2603` on the same kg-real-policies corpus, same vanilla baseline:

| Session | Prompt state | Score | Notes |
|---|---|---|---|
| 6e70de0e | full prompt | **5.5/7** | best; full Prozent-zu-Score table, citation correct |
| 87bf6124 | earlier prompt | 5/7 | similar quality |
| bbc8472c | full prompt | **0/7** | invented `room='document'` filter, retrieved nothing, full hallucination |
| c71b3258 | full + REPRODUCTION DISCIPLINE bloat | 1.5/7 | drifted to wrong section (2.1-2.12 instead of 2.13) |
| fa71c05a | same bloated prompt | 1.5/7 | reproduced the topic-drift |
| 9776a7cd | trimmed prompt | **3.5/7** | new failure mode — invented a formula `\frac{\sum...}{N}` and wrong "+0.5" weighting; fetched a SECOND document (20_2_12 Risikomatrix) proactively, confused two frameworks |
| d74d1f41 | post-rollback state (= 6e70de0e config + room-warning) | **2/7** | methodology-overview answer, no Prozent-zu-Score table, no blockades, no Richtlinien-Abzüge — same model, same config, same canary, different output |

**Pattern**: Mistral Small 4's quality on this exact task is highly variable across runs even with identical inputs. Same model, same corpus, same query, similar prompts — outputs range from 0/7 to 5.5/7. Average maybe 2-3/7.

**Three distinct failure modes observed**:
1. **Filter invention**: model guesses `room='document'` (or similar) → empty retrieval → hallucinated answer (bbc8472c)
2. **Topic drift**: model reads the right document but composes from the wrong section, giving a methodology summary instead of the requested section's content (c71b3258, fa71c05a)
3. **Formula/value invention**: model retrieves correctly, but instead of reproducing the source's percent-to-score table verbatim, INVENTS a plausible-looking formula like `\frac{\sum Asset+Kat+Hersteller}{N}` and assigns numbers like "+0.5 for expired policy" that are not in the source (9776a7cd)

The third is the most pernicious because the answer LOOKS more authoritative (math, structured tables) but is partially fabricated. The "Richtlinien (1 abgelaufen): +0,5" claim contradicts the actual source which says "abgelaufene Richtlinie reduziert den Score um 5%" (a -5% deduction, not a +0.5 addend).

**What this means for the Brain stack**:

- Brain's infrastructure is correct — verified through-the-day debugging closed every retrieval/read/path/dedupe bug. The data IS available to the model.
- The variance is in the model, not in Brain.
- Vanilla Claude Code achieved 7/7 on the same canary because Opus 4.7 has stronger anti-hallucination tendencies and reproduces tables verbatim more reliably. That is a model-class advantage, not a tooling advantage.
- For DSGVO-compliant local-only operation, **Mistral Small 4 is the best available option but expect 2-5/7 quality on policy-reproduction tasks with run-to-run variance**.

**How to apply**:

When evaluating a policy-Q&A pipeline on Mistral Small:
- Run the canary query 3+ times before drawing conclusions about a prompt/config change. A single run is not a measurement.
- Watch for invented numbers (formulas, percentages, factors). Grep the answer for the exact values from the source — if the model paraphrased a number range, it probably hallucinated.
- Topic drift is detected by checking which section of the source the answer covers. If the answer talks about "Methodology" when the question was about "Berechnung", the model loaded the wrong scope.
- The 3-step flow (mempalace_query → read_document → answer) is necessary but not sufficient for accuracy. The remaining gap is composition fidelity, which Mistral Small 4 doesn't reliably provide.

**To stop chasing 7/7**: this is the natural end. The infrastructure is solid. To get higher accuracy, options are:
- Switch to Opus (rejected by user — DSGVO).
- Wait for a future Mistral model with better verbatim-reproduction tendencies.
- Build human-in-the-loop validation that flags inconsistencies between consecutive Mistral Small answers on the same query.

**Don't add more prompt rules** in pursuit of accuracy. The bloat-experiment (c71b3258, fa71c05a) showed that more rules degrade Mistral Small's performance on this size class. Single-sentence rules naming the failure mode are the limit.
