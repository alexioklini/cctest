---
name: gemma26b-loadaware-eval-win
description: "2026-05-15 — gemma-4-26B local eval: pre-fix 0.31 → post-fix 0.82 brain mean (Δ +0.51) after warmup load-aware backoff + skip_warmup on session create"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6a44543c-73d9-47db-bace-21bca96ec48d
---

## Setup
- Brain on gemma-4-26B-A4B-it-MLX-4bit (local oMLX, max_concurrent=2)
- 15Q policy canary, reused Opus golds from morning run
- Mistral Medium 3.5 self-judge via eval/judge_mistral.py
- 4 questions errored out (gemma silent-after-tool, not a Brain infra issue)

## Two infrastructure changes (both shipped 2026-05-15)
1. **Warmup keeper load-aware backoff** — keeper skips a cycle when its
   target provider has live user load or recent release (15s grace).
   See [[project_warmup_load_aware_backoff.md]].
2. **`skip_warmup: true` on session create** — eval client opts out of
   the per-session `_trigger_warmup`. Per-session prefill races the
   actual chat call on the same provider queue; on gemma-4-26B this
   caused 3 visible failures in the first re-attempt run (1-char
   truncated replies after tool rounds, no error/cancel in sidecar log).

## Results
| run | brain mean | wins | errors |
|-----|-----------|------|--------|
| pre-fix (07:58) | 0.31 | 0/15 | 1 |
| post-fix (17:52) | **0.82** | **2/15** | 4 |

Opus gold mean ~0.91 in both runs. Post-fix Δ_brain−gold = −0.08 (within
Mistral judge variance). Brain even won M3_cloud_drittparteien because
Opus failed to produce any answer there.

## What still fails — gemma model bug
The 4 errors (F1/F2/F3/C3) all came back from the sidecar with
`reply=1c rounds=N tools=N-1 cancelled=False error=None`. Pattern:
gemma-4-26B makes tool calls successfully, then returns empty content
on the post-tool continuation. Same failure motif as e4b in
[[feedback_gemma_e4b_unsuitable_for_tools.md]] and the dispatch failures
in [[project_guided_execution_broken_local.md]] — model-level, not a
queue or warmup issue. The infra is clean.

## Not investigated yet
- cb1d42f3 (from the first run, ad-hoc chat not in eval): citation
  discipline overshoot — model refused to answer because "Quelldatei
  liegt nicht vor" instead of calling read_document. Separate
  prompt-tuning issue.
