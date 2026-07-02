# MoA eval rubric

A judge model scores ONE answer to ONE question on four axes, 0.0–1.0. The judge sees:
the question, the answer, the question category, and — when present — a `gold`
reference answer. The judge does NOT know which arm produced the answer (single model
vs Mixture-of-Agents) — arms are scored blind so synthesis gets no benefit of the doubt.

The point of this eval: does aggregating several models' drafts into one answer
(Mixture of Agents) beat the best single model on the SAME question? Only a real,
repeatable lift across reps counts — a single-run delta under the per-question spread
is noise, not signal (see feedback_eval_single_run_noise).

## Axes

1. **correctness** (0–1) — Is the answer factually/logically right?
   - For `reasoning_checkable` questions a `gold` answer is provided. The FINAL result
     must match gold (allow trivial rounding/format differences). A wrong final number,
     wrong fraction, wrong who-is-on-which-day, or a missed bug = correctness ≤ 0.3 even
     if the prose is nice. A correct final result with a flawed/missing derivation =
     cap at 0.7.
   - For open questions (no gold): score whether claims are accurate and free of
     fabrication. A confident false claim caps correctness ≤ 0.4.

2. **completeness** (0–1) — Does the answer cover what the question explicitly asked for?
   Count each required part: e.g. "fraction AND hours+minutes", "recommendation AND the
   one condition that flips it", "failure modes ranked AND one mitigation each". Missing a
   demanded part lowers this proportionally.

3. **reasoning** (0–1) — Is the path to the answer sound and shown where the question asks
   for it ("show the formula", "explain", "walk through assumptions")? Reward a clean,
   checkable derivation. Penalize hand-waving, skipped steps, or a right answer with no
   justification when justification was requested.

4. **calibration** (0–1) — Does the answer express the RIGHT amount of confidence?
   - For checkable questions: a correct answer stated plainly = high; hedging on a
     certain result = lower; confidently stating a WRONG result = low (0.0–0.2).
   - For `knowledge_synthesis` / `judgment_open`: reward honest weighing of trade-offs,
     acknowledging uncertainty where it exists, and NOT pretending false precision.
     A one-sided answer that ignores a real counter-consideration the question raised
     (e.g. the contradicting study, the migration risk) caps calibration ≤ 0.5.

## Category notes the judge must honor

- **reasoning_checkable** — correctness is dominant; the gold answer is ground truth for
  the final result. Do not award correctness for a plausible-looking but wrong number.
- **reasoning_open** (Fermi-style) — there is NO single right number. Score correctness on
  whether the chain of assumptions is internally consistent and the final number follows
  from them, NOT on hitting a specific value. A defensible 50–200 piano-tuner answer with
  a clear chain beats a bare "about 100" with no reasoning.
- **knowledge_synthesis** — completeness + calibration carry the most weight. The
  question always demands a SINGLE concrete recommendation/conclusion at the end; an
  answer that surveys options but refuses to commit caps completeness ≤ 0.5.
- **judgment_open** — specificity to the task is the test. Generic boilerplate that could
  apply to any prompt (the email, the ML feature) caps completeness ≤ 0.5.

## Output format (judge returns strict JSON)

```json
{
  "correctness": 0.0,
  "completeness": 0.0,
  "reasoning": 0.0,
  "calibration": 0.0,
  "note": "one sentence justification, naming the single biggest strength or flaw"
}
```

`overall` is computed by the harness as the mean of the four axes. For
`reasoning_checkable` the harness DOUBLE-WEIGHTS correctness (it is the whole point of a
checkable question) — judge still returns the raw four axes; weighting happens in code.

## Pass bar (computed by harness, not the judge)

- A MoA arm "wins" only if its mean `overall` exceeds the best single-model baseline by
  MORE than the larger of the two arms' per-question spread (mean across reps), over
  >= 3 reps. Anything inside the spread is reported as NOISE, not a win.
- Report token cost (sum of reference + aggregator tokens) and wall-clock per arm so the
  lift can be weighed against the ~Nx cost of running the reference fan-out.
