---
name: Variance kill-switches — new defaults validated 2026-05-13
description: Tool-call pipeline kill-switches infrastructure + 4-flag default (force_all_light + compress_old + tool_dedup + read_doc_cache); validated on schedule task (3/3 clean vs 2/3 prior stalls) + policy eval (all-time-best brain mean)
type: project
originSessionId: 2d6453da-b121-4880-899d-a1ce5bbf25b3
---
Shipped: per-piece runtime kill-switches for every variance-introducing site in the agentic loop (`brain.py:23972+ _VARIANCE_DEFAULTS`, 17 flags). Admin UI under Settings → Diagnostics → "Variance switches" with dependency-aware locking. Endpoints `GET/POST /v1/variance`. Server normalises dead-children on save + read.

**New defaults** (validated 2026-05-13, replacing the all-true baseline):
```
force_all_light          : true     ← bypass heaviness routing
worker_subagent          : false    (forced by force_all_light)
auto_isolation           : false    (forced by force_all_light)
tool_result_summariser   : false    (forced by force_all_light)
compress_old_middleware  : true     ← cumulative budget cap — load-bearing
tool_dedup               : true     ← runaway-loop defense
read_doc_cache           : true     ← per-session re-read cache
all others               : false
```

**Schedule-task evidence** (task "Mistral AI News", Mistral Medium 3.5, temp 0.7):
- Run 788: 23 tools, 36 KB written report — initial minimal-pipeline result, lucky converge
- Runs 789–790 (minimal pipeline): 2/2 stalled at the "Now I'll compile…" transition — 13 + 26 visible output tokens at synthesis round, context bloated past 86–160K input tokens with no compression
- Added `compress_old_middleware` → Runs 791, 792, 793: **3/3 wrote full reports** (45–53 KB), synthesis turn producing 11–15K visible tokens consistently
- The cumulative-budget cap is the missing piece. Microcompact is NOT needed (it rewrites in place every 2 rounds — bad for citation work). Compress-old fires only past 50K cumulative budget, only touches older results, keeps recent context intact.

**Policy eval evidence** (KG-Real-Policies, 15Q, Mistral Medium 3.5, Mistral judge):
| Metric | Today's 3 runs | Previous all-time best (`kg-enabled` 2026-05-03) | Δ |
|---|---|---|---|
| Brain mean | 0.829 / 0.850 / 0.941 → mean **0.873** | 0.823 | **+0.050** |
| Gold mean | 0.895 average | 0.921 | −0.026 |
| Δ vs gold | mean −0.022 (statistical tie within ±0.09 judge noise) | −0.098 | +0.076 |

Today's WORST run (0.829) ≈ previous all-time best (0.823). Today's BEST (0.941) is +0.12 above prior best.

**Why:** Killing the summariser cascade (force_all_light + worker + auto_isolation + summariser all off) means the model sees raw tool output instead of LLM-compressed envelopes. For policy-reproduction work where the validator scores verbatim quotes, this is exactly what's needed. The 4-flag minimum keeps `compress_old`/`tool_dedup`/`read_doc_cache` as structural defenses for redundant-access workloads (the eval re-reads same policy docs across questions).

**Known remaining instability** (NOT fixed by kill-switches):
- F1_geldwaesche: refuse-or-answer dice roll (0.93 / 0.17 / 0.25 across the 3 runs)
- C2_passwort_zitat: citation-format judge variance (0.88 / 0.50 / 0.70)
- C3_isms_ziele: citation judge variance (0.83 / 0.50 / 0.38)
These three drive 95% of run-to-run delta. They've been unstable in every eval since 2026-05-01. Temp 0.2 didn't fix F1 (model still finds surface keywords in Löschkonzept and answers tangentially).

**Why:** When recommending further variance work for policy-eval mean, F1's refusal logic + C2/C3 citation format are workload-specific issues that need prompt or retrieval interventions, NOT pipeline ones.

**How to apply:** New `_VARIANCE_DEFAULTS` are the canonical first-run state. Users can flip via GUI (Settings → Diagnostics → Variance switches). The 10s TTL cache invalidates on save. Server normalises dead-children-of-locked-parent on write. For diagnostic bisects, "Minimal pipeline" button turns everything off; "Reset to defaults" restores the new defaults.

**Don't regress:** Don't re-enable `microcompact_middleware` — measured in policy work it rewrites tool results in place every 2 rounds, which destroys citation traceability. Don't re-enable `auto_isolation` without the summariser — that path is now dead code under force_all_light, but enabling auto_isolation alone would re-introduce the 8 KB boundary stochasticity.

---

## v8.37.0 follow-up — intent_action_guard (added 2026-05-13)

After committing the 4-flag default, run 797 stalled at *"Now let me search for information about local LLM hosting on Mac OS with MLX"* — 19 visible tokens, no synthesis, no follow-up tool call. Same pattern as historical runs 779/784/789/790/794. The 4-flag config doesn't tame this because the stall happens at the model-side decision boundary, not in pipeline middleware.

**Added 18th kill-switch**: `intent_action_guard` (default ON). Fires when `finish_reason=stop` + empty `tool_calls_map` + `_usage_out < 100` tokens + `_INTENT_ACTION_PATTERNS` regex match at the tail of `full_text`. Pattern matches: "Now I'll [create|write|compile|save|compose|generate|produce|prepare|build|search|look|check|gather|fetch|find|retrieve|consult|review|examine]", "Let me [same verbs]", "I'll now [same]" — sentence-anchored at the reply tail. Max 1 retry per turn (counter on `_thread_local._intent_action_recovery_count`, reset at round 0).

Re-prompt text: *"You announced the next action but didn't actually take it. Either call the tool now, or if you've already gathered enough information, write the final answer directly. Do not announce — execute."*

Emits SSE event `intent_action_guard` with `attempt` + `stalled_text` tail.

**Validated on**: run 799 — clean synthesis, guard didn't fire (it's a safety net, only triggers when the model would have stalled). Future runs that would have stalled should now retry once and either complete or fall through to the normal "return full_text" path on the retry.

**Guided execution + variance interaction** (the question that prompted this addition):
`run_guided_execution()` decomposes a user prompt into N subtasks, then calls `_run_delegate()` once per subtask. Each subtask is a normal agentic loop — same `send_message` → `_handle_openai_response` path. **All variance kill-switches apply per-subtask, with no special-casing.** This is the right behavior: guided execution doesn't suppress the gates, it just adds a planning layer on top.

For Gemma 4 specifically (the model that motivated re-enabling guided execution): the Gemma `<|tool_call>...<tool_call|>` text-mode parser at `_parse_gemma_tool_calls` runs *before* the intent_action_guard check. If Gemma emits a successful text-mode tool call, `tool_calls_map` will be non-empty and the guard's `if not tool_calls_map` branch won't fire. The risk is that Gemma's `<think>I'll search for X next.</think>` thinking text could leak past `_InlineThinkingSplitter` if the chat template emits visible `<think>` blocks — that would falsely trigger the guard on what was actually a successful thinking-then-call sequence. Watch `server.error.log` for `[intent-action guard: stall detected, re-prompting…]` when guided execution is enabled on Gemma 4 — if it fires on cases that DID call tools, the kill-switch is the fix (turn it off for Gemma) and the proper fix is to tighten the thinking-strip.

**Don't regress (v8.37.0 additions):** Don't broaden the intent_action_guard regex without testing. The current pattern targets ~25 transition verbs and requires sentence-boundary anchoring at the tail. If you add verbs like "verify", "consider", "think about", you'll catch legitimate model thinking ("I should consider X before answering"). Conservative is correct here — false positives waste an LLM call; false negatives just fall through to the old behavior (model returns short text, loop ends, user sees the stall).
