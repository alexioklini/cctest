# web_fetch optimization eval — judge rubric

You are scoring whether a content-reducing **optimization** in `web_fetch` lost
answer-critical information.

Two answers to the SAME question are given:

- **GOLD answer** — written from the COMPLETE page content (optimizations off,
  full body fetched). This is the reference: treat its facts as ground truth.
- **OPT answer** — written from the OPTIMIZED fetch (the new code path:
  abstract-first survey, academic-PDF rewrite, matched-region trim, or the
  tool's conversion-method choice).

The question is whether the optimization preserved enough content for the OPT
answer to match the GOLD answer. You are NOT judging writing quality.

## Score each answer on two axes (0.0–1.0)

**completeness** — Does the answer contain the specific facts the question asks
for? Full marks only if every requested item is present and correct.
- 1.0 — all requested facts present and correct
- 0.5 — some present, some missing or vague
- 0.0 — the key facts are absent

**faithfulness** — Are the stated facts actually true (not fabricated)? An
answer that hedges "the provided content does not include X" is FAITHFUL (1.0)
even if incomplete — honest omission is not a fabrication. An answer that
invents a plausible-but-wrong value is unfaithful (0.0).
- 1.0 — every stated fact is correct, or correctly flagged as unavailable
- 0.5 — mostly correct with one wrong/invented claim
- 0.0 — central claims fabricated

`total` for each answer = round(0.5 * completeness + 0.5 * faithfulness, 2).

## Comparison

- `winner` — "gold", "opt", or "tie". Pick "tie" when the OPT answer is as
  complete and faithful as the GOLD answer (the optimization lost nothing).
  Pick "gold" when the OPT answer is missing facts the GOLD answer has (the
  optimization dropped answer-critical content). "opt" only if the optimized
  answer is genuinely better.
- `content_loss` — boolean. True iff the optimization caused the OPT answer to
  miss facts the GOLD answer correctly provided. This is the headline signal.
- `summary` — one sentence on what (if anything) the optimization dropped.

## Output format

Output ONLY this JSON object — no prose, no markdown fences:

```
{
  "gold":  {"completeness": 0.0, "faithfulness": 0.0, "total": 0.0},
  "opt":   {"completeness": 0.0, "faithfulness": 0.0, "total": 0.0},
  "comparison": {"winner": "gold|opt|tie", "content_loss": false, "summary": "…"}
}
```
