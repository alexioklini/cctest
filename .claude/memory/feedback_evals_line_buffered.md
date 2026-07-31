---
name: feedback_evals_line_buffered
description: "Always make eval harnesses line-buffered / unbuffered so background-run progress is visible live, not stuck until the end"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 4e01ac63-6b3d-486f-a0ab-b3f88a30cdcb
---

When writing or running eval harnesses (eval/*.py) that run in the background (nohup / run_in_background), make their output LINE-BUFFERED so partial results stream to the log as each block finishes — don't leave them block-buffered where a long run (esp. slow local models like Ornith-35B at ~24s/call) shows a 0-byte log for many minutes and I can't report partial status.

**Why:** during the codegraph eval (2026-06-27) the full matrix ran 6+ min with an empty log because Python buffers stdout when piped to a file; I couldn't show partial numbers mid-flight. User asked to fix this for the future.

**How to apply (any of):**
- Run with `python3 -u eval/foo.py` (unbuffered), OR
- `PYTHONUNBUFFERED=1 python3 eval/foo.py`, OR
- in the script: `print(..., flush=True)` on every result line, or `sys.stdout.reconfigure(line_buffering=True)` at top of main().
- Prefer `print(..., flush=True)` baked into the harness so it's correct regardless of how it's launched.

Pairs with [[feedback_eval_single_run_noise]] (≥3 reps) and the harness work in [[project_codegraph_replacement_eval]].
