---
name: feedback_eval_reuse_gold
description: "Eval runs must reuse prior Opus gold answers, never regenerate gold"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c030a6eb-c9a0-4b5b-8923-08455579bf7f
---

When running `eval/run.py` (the Brain-vs-Opus eval in `eval/`), NEVER run the gold side fresh. Always pass `--skip-gold --reuse-results <prior_dir>` to reuse a previous run's `gold.json` answers.

**Why:** the gold side is Opus 4.7 + vanilla MemPalace MCP querying the same static palace — it does not change when Brain's code/config changes, and each fresh gold run burns ~45 Opus calls against the Max subscription. Re-running it is pure waste.

**How to apply:** pick the most recent results dir under `eval/results/` that has a complete 15/15 `gold.json` set with real text (>100 chars in the `result`/`text` field), e.g. validated via a quick glob+json check. Reuse that as `--reuse-results`. Only the Brain answers + judge get regenerated. See [[project_brain_vs_opus_eval]] for the harness layout.
