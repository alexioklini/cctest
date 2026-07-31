---
name: Guided execution broken on local models (2026-05-13)
description: Run 805 (gemma-4-e4b-it-4bit, fine, 11 subtasks): 10/11 subtasks produced 0 narrative tokens after tool calls; synthesis got truncated JSON fallback and explicitly complained "Prior task results only contain a detailed summary for Task 6". User concluded guided execution is not working — leaving as-is, do not silently patch.

type: project
originSessionId: fe3309b9-f4b8-4c17-9cce-94197e4eface
---
Run 805 evidence (sched-805, gemma-4-e4b-it-4bit, granularity=fine):

- 11 subtasks decomposed (fine cap = 12, coarse = 5)
- 16 tool calls total
- 10 of 11 subtasks: round 0 called exa_search → round 1 LLM returned 0 tokens_out (model emitted EOS immediately after tool result, no narrative)
- Only Task 6 (Skills Development) actually narrated (478 + 531 tokens across 2 rounds)
- v8.33.15 fallback (capture raw tool_result → stuff into `task_result` if delegate returns empty) DID fire, but `_GUIDED_CTX_CAP=800` truncates each exa_search JSON dump
- Synthesis (also gemma-4-e4b) received `[exa_search result]\n<800-char JSON>` for 10 tasks, couldn't synthesize from that, output explicitly says "Tasks 1, 2, 3, 4, 5, 7, 8, 9, 10, 11 noted as searched but results not present"
- Final report is mostly `[Insert summary of X]` placeholder lines

**Why:** Two compounding failure modes:
1. Gemma-4-e4b habit: call tool, see result, EOS with no narration (well-documented, v8.33.15 was supposed to fix it)
2. Fallback produces raw JSON in `task_result`. Synthesis model (same Gemma) treats raw exa_search JSON as "no useful content" and refuses to incorporate it.

**How to apply:** User disabled `guided_execution` on Gemma-4 models 2026-05-13 after this evidence. Do NOT propose re-enabling without first fixing the underlying issues (Gemma-4-e4b silent-after-tool + raw-JSON-in-fallback). Don't propose patches to the fallback (e.g. per-task summarisation LLM call) unprompted. If guided execution comes up again for local models, the real questions are: does `fine` mode need a different synthesis strategy (skip 800-char truncation, file full tool output to MemPalace, synthesis reads from there)? Is gemma-4-e4b just unsuitable for the synthesis role specifically?

Related runs: 803 also had 0-out rounds after exa_search (subtask 1 + 4). Same pattern, same root cause. Not a one-off.
