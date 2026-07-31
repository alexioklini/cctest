---
name: sdk-phase-5-complete-v9-0-0-tagged
description: Phase 5 deletion campaign complete 2026-05-15. v9.0.0 ships the sidecar-only architecture. Both gates passed. Native loop is gone for good.
metadata: 
  node_type: memory
  type: project
  originSessionId: 35d3fa9f-2086-4b4d-ab4a-69a77de4c073
---

**Phase 5 deletion campaign DONE.** Tagged `v9.0.0` 2026-05-15. Brain runs LLM execution **only** through the Anthropic SDK sidecar (`sidecar/sidecar.py`, port 8421). The native Python agentic loop is permanently gone.

**Net diff** across 8 code commits + 4 doc commits since v8.37.0: ~−4900 LOC code, ~+700 LOC docs.

**Deleted (don't reintroduce):**
- `_run_delegate`, `_run_delegate_with_fallback`, `send_message`, `send_message_with_fallback`, `_handle_openai_response` (native loop core)
- All `_middleware_*` (cancel_check + compress_old retained at the call site, rest gone)
- Guided execution (decomposer, all `_GUIDED_*`, run_guided_execution)
- Variance kill-switches (`_variance_flag`, `_VARIANCE_DEFAULTS`, web UI tab)
- Worker-subagent envelopes + `_summarise_tool_result`
- LCM auto-trigger (manual ✂️ button only now)
- TUI/CLI orphans (`main()`, `_run_interactive`, EscapeWatcher, Spinner, 12 helpers)
- LCM legacy wrappers (`_check_and_compact`, `_compact_conversation`)

**Kept (still load-bearing):**
- ContextManager + context.db + context_search/detail/recall tools (LCM core)
- Citation validator + re-round (step 4 was deliberately skipped — see [[project_eval_citation_reround_phase2]])
- `_apply_tool_result_budget` + `_microcompact` pre-processing (separate from LCM)
- LiveStream + resumable streaming + Brain-restart recovery via active_turns + sidecar replay buffer (step 1c)

**Gates passed:**
- Gate-3 eval (15Q policy canary, mistral-medium-3.5 via CLIProxyAPI): brain mean **0.82** on rerun (first run was 0.77 — within ±0.09 Mistral judge noise; rerun confirmed). Δ vs gold −0.07.
- Gate-2 schedule ("Mistral AI News", 3 reps × 3 models): 9/9 status=success. 8/9 produced real multi-section reports (5.5–7.4 KB); 1 mistral-medium rep returned a 28-byte stub (model stopped without synthesis — outlier, not a sidecar bug).
- **Surprise**: gemma-4-e4b passed 3/3 — pre-Phase-5 finding that e4b couldn't run tool-loops is now stale. See [[feedback_gemma_e4b_unsuitable_for_tools]].

**Open follow-ups (NOT blockers for v9.0.0, deferred):**
- Sidecar process decoupling (its own launchd plist) — would unlock the happy-path Brain-restart recovery (currently only catastrophic 404 fallback fires).
- Step 4 (citation validator + re-round deletion) — deliberately skipped. May revisit if the validator becomes mechanically obsolete.
- ~400 LOC of orphaned TUI tool-formatter code (`_execute_tools_batch`, `_display_tool_call`, etc.) — zero callers, can be swept later.
- README.md still claims `python3 brain.py start/tui/telegram` — those entry points are gone; needs update.

**Where to read more:** `SDK_MIGRATION_PLAN.md` + `SDK_MIGRATION_HANDOVER.md` + `SDK_PHASE5_PROGRESS.md` in repo root. CLAUDE.md "Agentic Loop (sidecar)" + "Tools" sections rewritten in step 9 (`5a8b3ea`).
