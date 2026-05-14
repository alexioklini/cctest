# Phase 5 Deletion Audit

Comprehensive inventory of all callers of Phase 5 deletion targets.
Generated: 2026-05-14

---

## CORE LOOP

### `send_message` (brain.py:25846)

**Definition:**
- brain.py:25846

**Callers (all LIVE or wrapped by sidecar_proxy):**
- brain.py:20798 — via `send_message(...)` in `_generate_chat_summary`  
  LIVE (background task, generates sidebar title)
- brain.py:27091 — in `_run_delegate` inline LLM call for model auto-detect  
  LIVE (post-`send_message_with_fallback` fallback when primary fails)
- handlers/chat.py:1094–1096 — direct calls from `_handle_chat` worker  
  LIVE (sidecar disabled path; sidecar enabled path routes via `sidecar_proxy.run_turn`)
- server_lib/translate/text.py:129, 150 — `brain._run_delegate` calls (wraps `send_message` internally)  
  LIVE (translate module, runs on `execute_command` output)

**Status:** LIVE — used by background generators and fallback paths. Delete only after handlers/chat.py sidecar is mandatory + all Phase 4 sites migrated.

---

### `send_message_with_fallback` (brain.py:27141)

**Definition:**
- brain.py:27141

**Callers:**
- brain.py:27424 in `_run_delegate` for fallback retry on model swap  
  LIVE (error recovery within `_run_delegate`)
- execution.py:680 — `TaskRunner._wrap_and_isolate`, wraps tool-result summariser  
  LIVE (worker subagent, Phase 5 deletion target but currently called by live chat paths)
- handlers/chat.py:1275 — guided execution synthesis fallback  
  LIVE (sidecar disabled; sidecar enabled path never calls this)

**Status:** LIVE — tight coupling to error recovery + subagent wrapping. Phase 5: delete after `_run_delegate` and worker subagent envelope deleted.

---

### `_handle_openai_response` (brain.py:26636)

**Definition:**
- brain.py:26636

**Callers (tool-loop dispatch):**
- brain.py:12338 in `_run_delegate` (SSE handler)  
  SAFE (called within `_run_delegate`, both Phase 5 targets)
- brain.py:26443 in `send_message` main loop  
  SAFE (called within `send_message`, both Phase 5 targets)
- brain.py:26477, 26491 in `send_message` recursive tool-loop calls  
  SAFE (internal recursion within `send_message`)

**Status:** SAFE — completely internal to the native agentic loop. Will be deleted with `send_message`.

---

### `_run_delegate` (brain.py:12185)

**Definition:**
- brain.py:12185

**Callers:**
- brain.py:11029 in `_run_delegate_with_fallback` — primary call  
  SAFE (calling function is also Phase 5 target)
- brain.py:11035 in `_run_delegate_with_fallback` — fallback call  
  SAFE (calling function is also Phase 5 target)
- brain.py:11984 in `run_guided_execution` — per-task execution  
  SAFE (calling function is Phase 5 target; called by handlers/chat.py but only when guided execution is on)
- brain.py:12145 in `_run_guided_execution_impl` per-subtask  
  SAFE (calling function is Phase 5 target)
- brain.py:16375 in `_handle_chat` worker (handlers/chat.py line references are actually in brain.py's fallback path)  
  LIVE (sidecar disabled fallback; used when `sidecar_proxy.sidecar_enabled()` is false)
- server_lib/translate/text.py:129, 150 — text translation  
  LIVE (used by `execute_command` handler for document translation)
- server_lib/translate/document.py:184, 239  
  LIVE (document translation, called from `/v1/translate/document`)
- server_lib/translate/detect.py:86  
  LIVE (language detection, called from `/v1/translate/detect`)

**Status:** MIXED
- SAFE callers (11029, 11035, 11984, 12145): all Phase 5 targets
- LIVE callers (handlers/chat.py fallback, translate/* modules): require decision
  - **handlers/chat.py fallback**: documented in SDK_MIGRATION_HANDOVER.md as part of sidecar disabled path; can delete once sidecar is mandatory (Phase 5.2)
  - **translate modules**: LIVE PRODUCTION CODE; unclear if these are in scope or post-Phase-5

---

### `_run_delegate_with_fallback` (brain.py:11025)

**Definition:**
- brain.py:11025

**Callers:**
- brain.py:10604 in `tool_delegate_task` (delegate tool)  
  LIVE (active tool)
- brain.py:10762 in `tool_schedule_run` (manual schedule trigger)  
  LIVE (active tool)
- brain.py:10914 in `tool_schedule_approve` (schedule approval)  
  LIVE (active tool)
- brain.py:10978 in `tool_schedule_decline` (schedule decline)  
  LIVE (active tool)
- execution.py:894 in `TaskRunner._wrap_and_isolate`  
  LIVE (called when workersubagent wrapping is enabled via variance flags)

**Status:** LIVE — active tool used by `delegate_task`, schedule tools, and worker subagent wrapping. Currently NOT routing through sidecar. Delete in Phase 5 as part of native-loop removal.

---

## GUIDED EXECUTION

### `run_guided_execution` (brain.py:11916)

**Definition:**
- brain.py:11916

**Callers:**
- brain.py:12255 in `_run_delegate` (when `_should_guide()` returns non-None)  
  SAFE (called within Phase 5 target)
- handlers/chat.py:1275 in `_handle_chat` worker  
  LIVE (sidecar disabled path; when `sidecar_proxy.sidecar_enabled()` is False)

**Status:** LIVE — guided execution is a chat-level feature, not yet migrated to sidecar. Delete when chat handler becomes sidecar-only.

---

### `_run_guided_execution_impl` (brain.py:11963)

**Definition:**
- brain.py:11963

**Callers:**
- brain.py:11955 in `_should_guide` → return value passed to `run_guided_execution_impl` indirectly  
  Wait, checking more carefully: line 11955 `return _run_guided_execution_impl(...)` — direct call from `_should_guide`  
  SAFE (calling function `_should_guide` is Phase 5 target)

**Status:** SAFE — only called from `_should_guide`, which is also being deleted.

---

### `_should_guide` (brain.py:11867)

**Definition:**
- brain.py:11867

**Callers:**
- brain.py:12251 in `_run_delegate` (decision gate)  
  SAFE (within Phase 5 target)
- handlers/chat.py:1273 in `_handle_chat` worker  
  LIVE (sidecar disabled path)

**Status:** LIVE — called from live chat handler when sidecar is disabled. Delete after sidecar migration.

---

### `_GUIDED_DECOMPOSE_SYSTEM` and `_GUIDED_DECOMPOSE_SYSTEM_FINE` (brain.py)

**Definition:**
- brain.py (constants at module level)

**Usage:**
- brain.py:11984 in `_run_guided_execution_impl`  
  SAFE (within Phase 5 target)

**Status:** SAFE — only used within guided execution implementation.

---

### `_build_guided_prior_results_block` (brain.py:11824)

**Definition:**
- brain.py:11824

**Callers:**
- brain.py:12145 in `_run_guided_execution_impl`  
  SAFE (within Phase 5 target)

**Status:** SAFE — helper only for guided execution.

---

### `_extract_user_text` (brain.py:11896)

**Definition:**
- brain.py:11896

**Callers:**
- brain.py:11984 in `_run_guided_execution_impl`  
  SAFE (within Phase 5 target)

**Status:** SAFE — helper only for guided execution.

---

### `_guided_file_task_to_mempalace` (brain.py:11775)

**Definition:**
- brain.py:11775

**Callers:**
- Search shows no callers found in codebase  
  UNCLEAR — appears to be dead code

**Status:** UNCLEAR — appears unused; confirm before deletion.

---

## MIDDLEWARE

### `_middleware_cancel_check`, `_middleware_tool_result_budget`, `_middleware_microcompact`, `_middleware_compress_old`, `_middleware_pyexec_hint`

**Definitions:**
- All in brain.py (lines ~24550–24640 region)

**Usage:**
- All registered in `_PRE_LLM_MIDDLEWARE` and `_POST_LLM_MIDDLEWARE` lists  
  SAFE (internal to native agentic loop, only called from `send_message` → `_handle_openai_response`)
- handlers/chat.py:1094–1096 call `_apply_tool_result_budget` and `_microcompact` directly  
  LIVE (sidecar disabled path)

**Status:** MIXED
- Middleware lists themselves: SAFE (internal loop)
- Direct calls from chat handler: LIVE (sidecar disabled fallback)

---

### `_PRE_LLM_MIDDLEWARE` / `_POST_LLM_MIDDLEWARE` lists (brain.py)

**Definition:**
- brain.py:24615–24619

**Callers:**
- brain.py:12250 in `_run_delegate` (iterates and calls each)  
  SAFE (within Phase 5 target)

**Status:** SAFE — only used within `_run_delegate`.

---

## CONTEXT MANIPULATION

### `_compress_old_tool_results` (brain.py)

**Definition:**
- brain.py (location to search)

**Callers:**
- brain.py:24568 in `_apply_tool_result_budget`  
  SAFE (called within Phase 5 middleware target)
- handlers/chat.py:1096 direct call  
  LIVE (sidecar disabled path)

**Status:** MIXED — LIVE caller in handlers/chat.py fallback path.

---

### `_apply_tool_result_budget` (brain.py)

**Definition:**
- brain.py (location to search)

**Callers:**
- brain.py:24553 in middleware list  
  SAFE
- handlers/chat.py:1094 direct call  
  LIVE (sidecar disabled fallback)

**Status:** LIVE — called from sidecar disabled fallback in live chat handler.

---

### `_microcompact` (brain.py)

**Definition:**
- brain.py (location to search)

**Callers:**
- brain.py:24559 in middleware list  
  SAFE
- brain.py:26473 in `send_message`  
  SAFE (within Phase 5 target)
- handlers/chat.py:1096 direct call  
  LIVE (sidecar disabled fallback)

**Status:** LIVE — called from sidecar disabled fallback path.

---

### `_summarise_tool_result` (brain.py)

**Definition:**
- brain.py (location to search)

**Callers:**
- execution.py:894, 973 in `TaskRunner._wrap_and_isolate` and subagent wrapper  
  LIVE (worker subagent framework, currently active)

**Status:** LIVE — core to worker subagent wrapping. Delete with subagent envelope code.

---

## VARIANCE

### `_VARIANCE_DEFAULTS` (brain.py:24654)

**Definition:**
- brain.py:24654

**Callers:**
- brain.py:24701, 24707, 24713–24735 in `_variance_normalize` and `_variance_flag`  
  SAFE (internal to variance subsystem)

**Status:** SAFE — internal variance implementation.

---

### `_variance_flag` (brain.py:24713)

**Definition:**
- brain.py:24713

**Callers (17+ variance gate sites in brain.py + execution.py):**
- brain.py:23277 (tool_dedup gate)  
  SAFE (within Phase 5 target `_run_delegate`)
- brain.py:23423 (read_doc_cache gate)  
  SAFE (within Phase 5 target)
- brain.py:24270 (parallel_tool_batching gate)  
  SAFE (within `_execute_tools_batch`)
- brain.py:24804 (sanitize_tool_result_cap gate)  
  SAFE (within Phase 5 target)
- brain.py:26250 (diminishing_returns_guard gate)  
  SAFE (within `_handle_openai_response`)
- brain.py:26271 (proactive_round0_compaction gate)  
  SAFE (within `send_message`)
- brain.py:26467 (reactive_400_compaction gate)  
  SAFE (within `_handle_openai_response`)
- brain.py:26966 (intent_action_guard gate)  
  SAFE (within `_handle_openai_response`)
- execution.py:618, 1008, 1024 in worker subagent paths  
  LIVE (TaskRunner, worker wrapping)

**Status:** MIXED
- Variance gates in loop: SAFE (will delete with loop)
- Variance gates in worker/execution: LIVE (subagent envelope is Phase 5 target but currently called)

---

### `_variance_normalize` (brain.py:24694)

**Definition:**
- brain.py:24694

**Callers:**
- brain.py:24730 in `_variance_flag` (validation)  
  SAFE (within variance subsystem)
- handlers/admin.py:1729 in `/v1/variance` POST handler  
  LIVE (admin settings, currently active)

**Status:** LIVE — admin UI for variance switches. Delete if `/v1/variance` endpoints are removed.

---

### `/v1/variance` endpoints (server.py:1094, 1128, 1425, 1690)

**Routes:**
- `server.py:1425` GET `/v1/variance`  
  LIVE (admin settings read)
- `server.py:1690` POST `/v1/variance`  
  LIVE (admin settings write)

**Handlers:**
- `handlers/admin.py:1708` `_handle_variance_get`  
  LIVE
- `handlers/admin.py:1729` `_handle_variance_save`  
  LIVE

**Status:** LIVE — admin UI, currently maintained. Delete in Phase 5.

---

## OTHER TARGETS

### `run_citation_reround` (brain.py:23774)

**Definition:**
- brain.py:23774

**Callers:**
- handlers/chat.py:1503 in citation validator  
  LIVE (research mode is still active, validator fires on citation threshold)

**Status:** LIVE — active feature in research mode. Delete when research mode disciplines shift to system-prompt-only (Phase 5).

---

### `_InlineThinkingSplitter` (brain.py:26557)

**Definition:**
- brain.py:26557 (class definition)

**Callers:**
- brain.py:26671 in `_handle_openai_response` (instantiation for inline_tags format)  
  SAFE (within Phase 5 target)

**Status:** SAFE — only used within loop's thinking-parsing logic.

---

### `_parse_gemma_tool_calls` (brain.py:26522)

**Definition:**
- brain.py:26522

**Callers:**
- brain.py:26863 in `_handle_openai_response` (tool-call parsing for Gemma format)  
  SAFE (within Phase 5 target)

**Status:** SAFE — internal to response parsing.

---

### `_mistral_thinking_open` variable handling (brain.py:26674, 26751, 26758, 26825)

**Definition:**
- brain.py:26674 (variable initialization in `_handle_openai_response`)

**Status:** SAFE — internal state variable within loop.

---

### Intent-action guard (`_intent_action_recovery_count`, `_INTENT_ACTION_PATTERNS`) (brain.py)

**Definition:**
- brain.py:25878 (reset in round 0)
- brain.py:26966–26970 (guard logic in `_handle_openai_response`)

**Status:** SAFE — internal to response handling in loop.

---

## ENGINE MODULES

### `engine/provider.py` — LocalProviderQueue usage

**Search result:**
- `LocalProviderQueue` is defined in brain.py:12698 (NOT in engine/)
- Callers:
  - brain.py:12320 (in `_run_delegate`)  
    SAFE
  - brain.py:13179 (in `run_model_warmup`)  
    SAFE
  - brain.py:26424 (in `send_message`)  
    SAFE
  - handlers/providers.py:648 (GET `/v1/queue/status`)  
    LIVE
  - handlers/providers.py:698 (POST `/v1/queue/cancel`)  
    LIVE

**Status:** UNCLEAR — `LocalProviderQueue` is LIVE (admin UI uses it), but it's defined in brain.py not engine/. The handover doc claimed "LocalProviderQueue is dead if sidecar owns provider routing" but the sidecar doesn't own provider routing — Brain still dispatches tools. Keep until Phase 5 confirms sidecar owns the provider queue or the endpoints are removed.

---

### `engine/loop.py`, `engine/provider.py` — dead modules

**Finding:** Both modules were already deleted in v8.29.0 per CLAUDE.md engine/CLAUDE.md. No live callers remain.

**Status:** SAFE — already gone.

---

## FRONTENDS

### TUI (`frontends/tui.py`)

**Usage:** Launched from launcher.py:319. No direct calls to Phase 5 targets found in search.

**Status:** UNCLEAR — TUI is a separate frontend; verify if it still calls native loop or routes through HTTP API.

---

### Telegram (`frontends/telegram.py`)

**Callers of deletion targets:**
- Telegram uses `brain.send_message_with_fallback` is NOT found in search of telegram.py
- Telegram has its own `send_message` method (line 126) for Telegram API

**Status:** UNCLEAR — appears to use HTTP API to Brain server, not direct function calls. Verify.

---

## SERVER LIBRARY

### `/v1/tools/call` handler (server_lib/tool_mcp.py)

**Purpose:** Tool dispatch endpoint called by the sidecar to execute tools on Brain.

**Callers:**
- Sidecar (external process) via HTTP POST

**Current status:**
- handlers/chat.py calls sidecar_proxy which POSTs to `/v1/tools/call`
- handler reconstitutes thread-locals from sidecar context and dispatches to engine.TOOL_DISPATCH

**Status:** LIVE — Phase 2 wiring is complete. Stays in Phase 5 (the tool dispatch infrastructure itself).

---

## VERIFICATION RESULTS (post-audit, 2026-05-14)

Re-verified the UNCLEAR / partial items myself:

### TUI (`frontends/tui.py`)
- 0 imports of `brain.*`. Routes through HTTP API.
- **SAFE** — no native-loop calls. No Phase 5 work needed.

### Telegram (`frontends/telegram.py`)
- 0 imports of `brain.*`. `send_message` in this file is the Telegram Bot API method, not `brain.send_message`.
- **SAFE** — no native-loop calls. No Phase 5 work needed.

### `_guided_file_task_to_mempalace`
- Caller IS present at brain.py:11851 inside `_run_guided_execution_impl` — the auditor missed it.
- **SAFE** — internal to guided execution, deletes with it.

### Translation modules (`server_lib/translate/*`)
- 5 direct `brain._run_delegate(...)` calls: `text.py:129/150`, `document.py:184/239`, `detect.py:86`.
- These are LIVE production endpoints (`/v1/translate/*`).
- Each is a single non-tool LLM call → maps cleanly to `sidecar_proxy.background_call(...)`.
- **DECISION NEEDED**: migrate to `background_call` (5 sites, mechanical) BEFORE deleting `_run_delegate`. Otherwise translate breaks.

### `_run_delegate_with_fallback` — full caller list (7 sites, not 5)
- `brain.py:10604, 10762, 10914, 10978` — `tool_delegate_task` + 3 schedule tools. LIVE.
- `brain.py:20324, 20387, 20661` — `ContextManager.summarize_chunk`, `.condense`, `.recall` (LCM internals). LIVE.
- `execution.py:894` — worker subagent envelope. LIVE (but Phase 5 deletion target).
- **DECISION NEEDED**: ContextManager.summarize_chunk/condense/recall are not in the deletion list — LCM stays. Their LLM calls must migrate to `background_call`. The delegate + schedule tools must migrate too (they're real tools agents call).

### `LocalProviderQueue`
- Defined in `brain.py:12698`. Live callers: `_run_delegate` (12320), `run_model_warmup` (13179), `send_message` (26424), `handlers/providers.py:648/698` (admin UI).
- The first 3 die with the native loop. The admin UI endpoints (`/v1/queue/status`, `/v1/queue/cancel`) become orphaned.
- The sidecar handles its own provider serialization via the anthropic SDK — but Brain's own concurrency control (e.g. warmup vs. chat racing the same oMLX) goes away.
- **DECISION NEEDED**: Delete LocalProviderQueue + the admin endpoints (sidecar owns provider concurrency now), OR keep the queue and wrap sidecar HTTP calls in it from `sidecar_proxy.run_turn`. Handover doc claimed "dead if sidecar owns provider routing" — that's correct for chat, but `run_model_warmup` still bypasses the sidecar and hits provider directly. **Recommendation**: keep `LocalProviderQueue` for now, delete only the `send_message` / `_run_delegate` acquire sites (they die with the loop); revisit when `run_model_warmup` migrates.

### `run_model_warmup` (not in deletion list)
- Not yet migrated to sidecar; pings provider directly to keep KV-cache warm.
- Out of scope for Phase 5 — leave as-is. Confirms the LocalProviderQueue keep-decision above.

---

## RISKS / OPEN QUESTIONS

1. **Handler fallback paths**: handlers/chat.py has explicit fallback branches when `sidecar_proxy.sidecar_enabled()` is False. These branches call `_run_delegate`, guided execution, and middleware functions directly. Phase 5 plan requires sidecar to be mandatory; confirm this is the intended approach (no native fallback path after v9.0.0).

2. **Translation modules** (server_lib/translate/*): These modules directly call `brain._run_delegate`. They are LIVE production code but NOT in the deletion target list. Are they in-scope or should they be migrated to sidecar post-Phase-5?

3. **TUI and Telegram frontends**: Unable to confirm if they route through HTTP API or make direct function calls to Phase 5 targets. Code search suggests HTTP API usage but should be verified.

4. **LocalProviderQueue and provider queue endpoints**: Currently LIVE (admin UI uses `/v1/queue/status` and `/v1/queue/cancel`). These are not in the Phase 5 deletion list but manage provider concurrency for the native loop. Confirm if they should be deleted or retained for tool dispatch routing.

5. **Worker subagent envelope code**: Phase 5 plan calls for deletion of "worker-envelope wrap, auto_isolation / worker_subagent logic" but `execution.py` is the live implementation. Confirm this module is also Phase 5 target or should be migrated to sidecar.

6. **Citation validator and re-round**: Currently LIVE in research mode (handlers/chat.py:1503). Phase 5 plan says to delete; confirm research mode will rely only on system-prompt disciplines (no re-round validator).

7. **Variance switches**: The admin UI at `/v1/variance` is LIVE and used by internal testing. Phase 5 plan deletes these endpoints. After deletion, there will be no kill-switches for loop features — confirm this is intentional.

---

## SUMMARY

**Total deletion targets:** 30+ functions/constants + 2 endpoints + multiple variance flags

**Breakdown:**
- **SAFE** (internal to loop / will delete with loop): ~18 targets
  - All send_message, _handle_openai_response, _run_delegate internals
  - All guided execution helpers
  - All middleware and context manipulation
  - All variance infrastructure

- **LIVE** (currently called by production code):
  - handlers/chat.py sidecar-disabled fallback paths (will require sidecar-mandatory)
  - Delegate tool and schedule tools calling `_run_delegate_with_fallback`
  - Worker subagent wrapping via execution.py
  - Citation validator and re-round (research mode)
  - Variance admin endpoints
  - Admin UI queue status/cancel endpoints

- **UNCLEAR** (need verification):
  - TUI frontend routing
  - Telegram frontend routing
  - Translation modules (in-scope?)
  - _guided_file_task_to_mempalace (appears unused)

**Readiness for deletion:** Requires Phase 2 (sidecar chat migration) to be finalized + Phase 3/4 background task migration to be complete. Then can delete SAFE targets in one batch. LIVE targets require explicit decisions on fallback behavior, translation modules, and admin endpoints.

