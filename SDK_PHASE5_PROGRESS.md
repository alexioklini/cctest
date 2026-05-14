# Phase 5 Deletion Pass — Session State (2026-05-14)

Companion to `SDK_MIGRATION_HANDOVER.md` and `SDK_MIGRATION_PLAN.md`.
This document captures the in-flight state of the Phase 5 deletion campaign so the next session can pick up cleanly.

---

## ⏭️ Next session: pick up here

**Steps 1, 2, 3, 5, 6, 7, 8, 9 are done and committed. Step 4 was skipped at user direction. Step 10 is next.**

The /v1/chat path through the sidecar is verified live after every step. Eval has NOT been re-run since v8.37.0 — defer to gate-3 at step 10.

**Resume from**: step 10 (gate-3 eval run). v9.0.0 tag is held until that passes.

---

## Status table

| # | Step | Status | Commit | Net LOC |
|---|---|---|---|---|
| 1 | Deletion audit & inventory | DONE | `51e443e` | +597 (audit doc) |
| 2 | Drop sidecar-disabled fallback branches | DONE | `90d7387` | −62 |
| 3 | Delete guided execution | DONE | `77f99da` | −1076 |
| 4 | Delete citation validator + re-round | **SKIPPED** (per user) | — | — |
| 5 | Delete variance kill-switches infrastructure | DONE | `d0e85f8` | −396 |
| 6 | Delete middleware + guards | DONE | `0c7fb4a` | −510 |
| 7 | Delete native loop core | DONE | `707285d` + `fdcb655` | −1520 |
| 8 | Unwire LCM auto-trigger; add manual button | DONE | `ffbde8d` | −26 |
| 9 | CLAUDE.md rewrite + orphan/comment sweep (tag deferred) | DONE | `d4d3bce` + `a37c6b5` + `5a8b3ea` | −1296 code / +166 docs / −84 docs |
| 10 | Gate-3 eval run + tag v9.0.0 | PENDING | — | — |

**Net so far**: −4886 LOC code, +679 LOC docs. Ten code/doc commits.

### Step 9 — CLAUDE.md rewrite + orphan & comment sweep
Three commits, scope per the user-confirmed plan ("CLAUDE.md only + flagged
orphans + sweep all stale references"). v9.0.0 tag held until gate-3.

- **`d4d3bce` refactor(sdk-phase5-9): delete TUI/CLI orphans + LCM legacy
  wrappers** (−1296 LOC). Three blocks gone, all flagged in step 8 as
  orphaned by the sidecar migration:
  - `_check_and_compact` + `_compact_conversation` (~140 LOC). Only caller
    was the chat-worker auto-trigger removed in step 8 plus the dead TUI
    loop. Manual ✂️ button calls `_context_manager.check_and_compact`
    directly.
  - `main()` + `_run_interactive()` + 12 TUI helpers + the
    `if __name__ == '__main__'` block (~1040 LOC). The launcher.py /
    tui.py path drives the live TUI; brain.py's `_run_interactive` still
    imported the missing `sdk_backend` module (deleted in v7.0.0) and
    was unreachable.
  - `EscapeWatcher` + `Spinner` classes (~100 LOC). Both TUI-only; zero
    external callers. `CancelToken` docstring updated to drop the
    EscapeWatcher reference (CancelToken itself stays — handlers/chat.py
    uses it as the chat worker's cancel signal).
  - **Known follow-ups** (NOT done — outside step 9 scope): orphans
    `_execute_tools_batch`, `_display_tool_call`, `_format_tool_call`,
    `_format_tool_result`, `_toggle_tool_output`, `render_markdown` and
    the markdown-rendering helpers (~400 LOC of TUI-only code). All
    have zero external callers post-Phase-5 but tearing them out
    requires verifying no buried in-process call site, which is bigger
    than the step 9 brief. README.md still claims `python3 brain.py
    start` etc. — those entry points are now gone, README needs an
    update.

- **`a37c6b5` docs(sdk-phase5-9): sweep stale references to deleted
  native-loop symbols** (~+18 / −22 LOC). Six docstring/comment updates
  across brain.py + handlers/providers.py replacing references to
  `_run_delegate` / `send_message_with_fallback` /
  `_handle_openai_response` / `_summarise_tool_result` /
  `escape_watcher.cancelled` with the live equivalents
  (`sidecar_proxy.background_call`, the proxy's `_watch_cancel` thread,
  etc.).

- **`5a8b3ea` docs(sdk-phase5-9): rewrite CLAUDE.md for sidecar
  architecture** (+166 / −84 LOC). Six section rewrites in CLAUDE.md
  per the original Phase 5 plan, plus engine/CLAUDE.md:
  - Architecture: ASCII picture now shows sidecar process owning the
    agentic loop.
  - Agentic Loop: rewritten — sidecar_proxy interactive vs background,
    /v1/tools/call dispatch, cancel via X-Turn-Id, "don't reintroduce"
    list of native-loop relics.
  - Resumable Streaming: documents proxy translation + Phase 5 step 1c
    Brain-restart recovery (active_turns + recover_active_turns_on_boot
    + sidecar /turn/<id>/events).
  - Format-Aware Thinking Level: deleted (SDK handles natively).
  - Thinking / Reasoning: rewritten short — SDK passthrough + oMLX
    warmup invariant + dropdown shape.
  - Worker Subagents: deleted (envelope removed in step 6).
  - Guided Prompt Execution: deleted (entire feature removed in step 3).
  - Tools: rewritten to describe HTTP MCP dispatch path + the
    synchronous-by-design dispatch constraint.
  - Lossless Context Manager: rewritten — manual-only trigger, sidecar
    background_call routing for ContextManager's own LLM calls.
  - Key Invariants: dropped intermediate-tool-message rollback line
    (sidecar owns those now); added "sidecar is the only LLM execution
    path".
  - Trailing sweeps: Python Code Execution lost the `_middleware_pyexec_hint`
    bullet; User Profile worker `_run_delegate` → `sidecar_proxy.background_call`.
  - engine/CLAUDE.md: same Agentic Loop rewrite, Worker Subagents
    section deleted, Provider Concurrency Queue clarified (warmup-only
    now), Concurrency & Thread Safety updated for sidecar dispatch
    callback's thread-local rebuild.

Live verification after each commit: smoke chat through
`CLIProxyAPI/mistral-medium-3.5` → `event: done` + persisted assistant
message + no tracebacks in `server.error.log`. Manual `POST
/v1/context/compact` still returns `status=compacted`.

### Step 8 — LCM auto-trigger unwired (`ffbde8d`)
~26 LOC deleted from `handlers/chat.py` (the `_check_and_compact` block + the
`compacting`/`compacted` SSE emission at the top of `_handle_chat`). LCM is
now manual-only via the existing status-bar ✂️ button → `POST /v1/context/compact`
→ `handlers/admin.py:_handle_context_compact`, which calls
`engine._context_manager.check_and_compact(...)` directly.

Banner logic in `web/js/panels.js:170` updated: previously hid at ≥80% on the
assumption proactive compaction would auto-fire; now stays visible for the
whole ≥60% range since the user is the only trigger.

Kept in tree (per the Phase 5 plan): `ContextManager` class, `context.db`,
`context_search` / `context_detail` / `context_recall` tools, the manual
endpoint, the `compacting` / `compacted` SSE handlers in `chat.js`. Also kept:
the `_apply_tool_result_budget` + `_microcompact` pre-processing in chat.py
— these are separate token-shaping middlewares (not LCM) and were already
unconditional pre-Phase-5.

Functions that became orphans (no live caller after this commit):
- `engine._check_and_compact` (the wrapper at `brain.py:20189`) — only
  remaining reference is `brain.py:25799` inside `_run_interactive`, which
  is dead code (`import sdk_backend` fails — file deleted in v7.0.0). Step 9
  CLAUDE.md sweep can remove the wrapper + dead TUI block together.
- `engine._compact_conversation` (the legacy fallback at `brain.py:20103`) —
  only called from `_check_and_compact`. Same fate.

Live verification: smoke chat through `CLIProxyAPI/mistral-medium-3.5` returns
`event: done` + persists a 28-byte assistant message; **no `compacting`/
`compacted` events fire**. Manual `POST /v1/context/compact` still returns
`status=compacted` for a populated session. No tracebacks in
`server.error.log`.

### Step 7 — native loop core (`707285d` + `fdcb655`)
~1520 LOC deleted; the sidecar is now the only LLM execution path.

- **707285d** `refactor(sdk-phase5-7): migrate translate/* to sidecar.background_call` — 5 _run_delegate sites in the translation pipeline (text translate + rewrite, document chunk translate + rewrite, LLM language fallback).
- **fdcb655** `refactor(sdk-phase5-7): delete native loop core — _run_delegate, send_message, _handle_openai_response` — 8 remaining brain.py migrations + ~1500 LOC of deletions in one commit:
  - Migrations: `trigger_relationship_discovery`, 3 autodream calls (dedup/conflicts/skill_candidates), `ContextManager.summarize_chunk`/`condense`/`recall`, `_compact_conversation`, `TaskRunner._run_task`, CLI one-shot mode (`main`), TUI interactive mode (`_run_interactive`).
  - Deletions: `_run_delegate_with_fallback`, `_run_delegate`, `send_message`, `_parse_gemma_tool_calls`, `_InlineThinkingSplitter`, `_handle_openai_response`, `_classify_error_transient`, `_retry_with_backoff`, `send_message_with_fallback`.
  - Stale references in comments/docstrings left untouched — step 9 sweeps them with the CLAUDE.md rewrite.

Live verification: smoke chat through CLIProxyAPI/mistral-medium-3.5 returns `event: done` and persists a 28-byte assistant message; no tracebacks in `server.error.log`.

---

## What each completed step did

### Step 1 — audit (`51e443e`)
Read-only inventory at `PHASE5_AUDIT.md` of every caller of the Phase 5 deletion targets, classified SAFE / LIVE / UNCLEAR. Three LIVE-but-handle-able findings surfaced and signed off by the user:
1. **Translation modules** (`server_lib/translate/*`): 5 direct `brain._run_delegate(...)` calls → migrate to `sidecar_proxy.background_call` **before** step 7.
2. **delegate + 3 schedule tools** (`tool_delegate_task`, `tool_schedule_run`, `tool_schedule_approve`, `tool_schedule_decline`): call `_run_delegate_with_fallback` → migrate to `sidecar_proxy.background_call` **before** step 7.
3. **`ContextManager.summarize_chunk` / `condense` / `recall`** (LCM internals, not in the deletion list): call `_run_delegate_with_fallback` → migrate to `background_call` **before** step 7. LCM itself stays.

Other findings (confirmed SAFE, no work needed): TUI + Telegram route through HTTP API; `_guided_file_task_to_mempalace` was internal to guided execution; `LocalProviderQueue` kept (still used by `run_model_warmup`).

### Step 2 — sidecar-disabled fallback branches (`90d7387`)
Sidecar is now the only chat / scheduler path.
- `handlers/chat.py`: deleted the `if not sidecar_enabled():` branch in `_handle_chat.worker()` — both the guided-execution gate and the `send_message_with_fallback` fallback. ~90 LOC dropped.
- `brain.py:_execute_scheduled`: collapsed the `if _use_sidecar / else _run_delegate(...)` arms. GDPR pre-flight now runs unconditionally (it was already running inside the sidecar arm — same behavior, less indentation).
- `handlers/sidecar_proxy.py`: `sidecar_enabled()` helper deleted (no remaining callers).

The deletion targets themselves (`run_guided_execution`, `_should_guide`, `send_message_with_fallback`, `_run_delegate`) are still in the tree at this point — they die in later steps.

### Step 3 — guided execution (`77f99da`)
~1076 LOC deleted across 6 files.
- **brain.py (−509 LOC)**: constants `_GUIDED_FILE_OUTPUT_RULE`, `_GUIDED_DECOMPOSE_SYSTEM`, `_GUIDED_DECOMPOSE_SYSTEM_FINE`, `_GUIDED_CTX_CAP`, `_GUIDED_STUB_HEAD_CHARS`; functions `_guided_file_task_to_mempalace`, `_build_guided_prior_results_block`, `_should_guide`, `_extract_user_text`, `run_guided_execution`, `_run_guided_execution_impl`; the call site inside `_run_delegate`.
- **handlers/chat.py (−81 LOC)**: `guided_tasks_live` accumulator state, 3 SSE branches (`guided_task_start`, `guided_task_progress`, `guided_task_done`), `msg_metadata["guided_tasks"]` persistence block, dead duplicate `request_payload` branch (unreachable second copy at the bottom of the handler).
- **web/js (−490 LOC)**:
  - `chat.js`: `_mirrorRefsToChatRefs` helper, guided_task_* SSE handlers, `_guidedActive` guards in `tool_call`/`tool_result`, `chat.guidedTasks` persistence mirror, two `meta.guided_tasks` reload sites, `renderGuidedTasksBlock`, the entire `window._gt*` family, `renderAssistantMessage` integration, streaming-bubble integration.
  - `sessions.js`: `meta.guided_tasks` reload + `_guidedTasksOpen` init.
  - `panels.js`: guided_tasks reference-extraction in `collectChatReferences` + `getReferencesForMessage`.
  - `settings.js`: per-model "Guided Execution" checkbox + granularity dropdown + save handler.
- **config.json** (gitignored): two `guided_execution_granularity` model fields removed.

### Step 5 — variance kill-switches (`d0e85f8`)
~396 LOC deleted.
- **brain.py**: `_VARIANCE_DEFAULTS`, `_VARIANCE_DEP_RULES`, `_variance_normalize`, `_variance_cache`, `_variance_lock` gone. **`_variance_flag(name)` retained as a stub** returning the v8.37.0 validated default per flag — preserves behavior at the 17 in-loop call sites until step 6/7 collapse them.
- **handlers/admin.py**: `_handle_variance_get` + `_handle_variance_save` (~80 LOC).
- **server.py**: `/v1/variance` removed from `_ADMIN_GET_PATHS` + `_ADMIN_POST_EXACT`; both route-dispatch `elif`s deleted. Endpoint now 404s.
- **web/js/settings.js (−243 LOC)**: "Variance switches" tab button under Diagnostics, the tab body, all `window.variance*` / `window._variance*` globals, `_VARIANCE_META` + `_VARIANCE_RULES` dependency graph.
- **config.json** (gitignored): `variance_kill_switches` block stripped.

Live verification: `GET /v1/variance` and `POST /v1/variance` both return 404; smoke chat through the sidecar returns the expected reply; no `NameError` / `AttributeError` in `server.error.log`.

### Step 6 — middleware + guards (`0c7fb4a`)
~510 LOC deleted across brain.py + execution.py. Every `_variance_flag(name)` call site collapsed to the frozen v8.37.0 default; the stub + `_VARIANCE_FROZEN_DEFAULTS` removed.

- **brain.py**: True-default wrappers dropped at `_check_tool_dedup` + `_read_doc_cache_lookup` + `intent_action_guard`. False-default branches deleted at the middleware pipeline (`_middleware_tool_result_budget`, `_middleware_microcompact`, `_middleware_pyexec_hint` gone; `_MIDDLEWARE_FLAG_NAMES` + flag check in `_run_middleware` gone; pipeline reduces to `[cancel_check, compress_old]`), inside `_execute_tools_batch` (parallel ThreadPoolExecutor branch removed; `_CONCURRENT_SAFE_TOOLS` + `_execute_tool_in_thread` deleted), inside `_sanitize_tool_result` (`sanitize_tool_result_cap` truncate), inside `_handle_openai_response` (diminishing-returns guard + `DIMINISHING_*` constants + `_round_deltas`; proactive_round0_compaction; reactive_400_compaction + `_has_attempted_reactive_compact`; truncated_tool_call_discard; max_output_recovery + `MAX_OUTPUT_RECOVERY_LIMIT` + `_MAX_OUTPUT_RESUME_MSG` + `_max_output_recovery_count`).
- **brain.py kept in tree**: `_apply_tool_result_budget` + `_microcompact` + their supporting constants + the two `_find_tool_name_*` helpers. handlers/chat.py:1043–1045 calls these unconditionally before every chat turn; the call site predates the variance flag and was never variance-gated. Step 7's broader cleanup decides whether to keep them.
- **execution.py**: `_summarise_tool_result` collapsed to a single `_static_summary` return (preserves the 3-tuple shape for `run_worker_subagent` + `maybe_retroactive_isolate` callers). `route_tool_execution` collapsed to `return inner_fn(tool_name, args)`. Worker-subagent + auto-isolation machinery (`_resolve_heaviness`, `run_worker_subagent`, `maybe_retroactive_isolate`, `_SUMMARISER_SYSTEM`, `_parse_summariser_output`) left in tree — they share a module with the still-live `WorkerRegistry` + worker UI endpoints and step 7 sweeps them.

Live verification: `launchctl kickstart` Brain → both listeners up; smoke chat through CLIProxyAPI/mistral-medium-3.5 emits `event: done`, persists a 28-byte assistant message; no tracebacks in `server.error.log`.

---

## Why step 4 was skipped

**Citation validator + synchronous re-round** (`run_citation_reround` in `brain.py:23774` + the wiring in `handlers/chat.py` around the citation-validation block).

Per `project_eval_citation_reround_phase2`: the re-round measurably improved the eval (brain mean 0.743, Δ +0.08 vs Brain-full). Per the Phase 5 plan, it gets deleted because research mode after v9.0.0 relies purely on system-prompt disciplines. User chose to skip the deletion at this point. The code is still in the tree and still fires on threshold violations. **It will need a decision again** before step 9 (CLAUDE.md rewrite tags v9.0.0). Options: (a) revisit step 4 and delete; (b) keep the validator + re-round as a permanent feature and remove it from the Phase 5 deletion list.

---

## Architecture notes for step 6 (historical — step 6 is done; kept for context)

`_variance_flag(name)` was a pure-function stub:

```python
_VARIANCE_FROZEN_DEFAULTS = {
    "force_all_light": True,          # → kept-on (worker subagent path dies w/ it)
    "worker_subagent": False,          # → dead branch
    "auto_isolation": False,           # → dead branch
    "tool_result_summariser": False,   # → dead branch
    "tool_result_budget_middleware": False,
    "microcompact_middleware": False,
    "compress_old_middleware": True,   # → kept-on
    "pyexec_hint_middleware": False,
    "diminishing_returns_guard": False,
    "max_output_recovery": False,
    "truncated_tool_call_discard": False,
    "proactive_round0_compaction": False,
    "reactive_400_compaction": False,
    "parallel_tool_batching": False,
    "tool_dedup": True,                # → kept-on
    "read_doc_cache": True,            # → kept-on
    "sanitize_tool_result_cap": False,
    "intent_action_guard": True,       # → kept-on
}

def _variance_flag(name: str) -> bool:
    return _VARIANCE_FROZEN_DEFAULTS.get(name, True)
```

This means every `if _variance_flag("X"):` call site is now a literal `if <const>:`. Step 6's edit pattern at each call site:
- **default True** → drop the wrapping `if`, keep the body.
- **default False** → drop the whole branch (dead code).

Once every call site has been reduced, `_variance_flag` and `_VARIANCE_FROZEN_DEFAULTS` themselves can be deleted at the end of step 6.

The 17 call sites and their resolved fates per the dict above:

| Flag | Default | Step 6 action |
|---|---|---|
| `force_all_light` | True | drop `if`, keep body (forces light tool path) |
| `worker_subagent` | False | delete branch + all worker-subagent envelope code |
| `auto_isolation` | False | delete branch + all auto-isolation code |
| `tool_result_summariser` | False | delete branch + summariser code (lives inside the deleted worker envelope) |
| `tool_result_budget_middleware` | False | delete branch + the middleware function |
| `microcompact_middleware` | False | delete branch + the middleware function |
| `compress_old_middleware` | True | drop `if`, keep body (still on) |
| `pyexec_hint_middleware` | False | delete branch + the middleware function |
| `diminishing_returns_guard` | False | delete branch + the guard code |
| `max_output_recovery` | False | delete branch + the recovery code |
| `truncated_tool_call_discard` | False | delete branch + the discard code |
| `proactive_round0_compaction` | False | delete branch (LCM still has manual button per step 8) |
| `reactive_400_compaction` | False | delete branch |
| `parallel_tool_batching` | False | delete branch + the ThreadPoolExecutor path |
| `tool_dedup` | True | drop `if`, keep body |
| `read_doc_cache` | True | drop `if`, keep body |
| `sanitize_tool_result_cap` | False | delete branch (raw bytes pass through; only base64 strip remains) |
| `intent_action_guard` | True | drop `if`, keep body — **but** this whole code is inside `_handle_openai_response` which step 7 deletes; so step 6 can also just leave it untouched and let step 7 sweep it |

After step 7, the entire native loop is gone, so any "kept on" branches that live inside the loop also vanish. Step 6 doesn't need to be surgical about loop-internal kept-on flags — step 7 deletes their parent function.

**Suggested step 6 scope**: collapse the False-default branches throughout `brain.py` + `execution.py`. Leave True-default branches alone (they die in step 7). At the end, delete `_variance_flag` + `_VARIANCE_FROZEN_DEFAULTS`.

---

## Audit cross-references (still load-bearing for step 7)

These are the LIVE callers per `PHASE5_AUDIT.md` that **must migrate to `sidecar_proxy.background_call`** before `_run_delegate` / `_run_delegate_with_fallback` can be deleted in step 7:

```
server_lib/translate/text.py:129       brain._run_delegate(...)
server_lib/translate/text.py:150       brain._run_delegate(...)
server_lib/translate/document.py:184   brain._run_delegate(...)
server_lib/translate/document.py:239   brain._run_delegate(...)
server_lib/translate/detect.py:86      brain._run_delegate(...)
brain.py:10604  tool_delegate_task       _run_delegate_with_fallback(...)
brain.py:10762  tool_schedule_run        _run_delegate_with_fallback(...)
brain.py:10914  tool_schedule_approve    _run_delegate_with_fallback(...)
brain.py:10978  tool_schedule_decline    _run_delegate_with_fallback(...)
brain.py:20324  ContextManager.summarize_chunk
brain.py:20387  ContextManager.condense
brain.py:20661  ContextManager.recall
execution.py:894  TaskRunner._wrap_and_isolate
```

Line numbers were correct at the time of audit (pre-deletion); they will have drifted slightly after steps 3 + 5. Re-grep before editing.

---

## Verification commands (used at the end of each step)

```bash
# Restart Brain
launchctl kickstart -k gui/$UID/com.brain-agent.server

# Wait for both listeners
for i in $(seq 1 12); do
  curl -sf http://127.0.0.1:8420/v1/status -o /dev/null && echo brain-ok && break
  sleep 1
done
curl -sf http://127.0.0.1:8421/health

# Smoke chat through the sidecar
TOKEN=$(curl -sf -X POST http://127.0.0.1:8420/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
SID=$(curl -sf -X POST http://127.0.0.1:8420/v1/sessions \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"model":"CLIProxyAPI/mistral-medium-3.5","agent":"main"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
curl -N -X POST http://127.0.0.1:8420/v1/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d "{\"session_id\":\"$SID\",\"message\":\"Say hi in five words.\"}" \
  --max-time 30 -s | grep -E "^event: (done|error)"
sqlite3 agents/main/chats.db \
  "SELECT role,length(content) FROM messages WHERE session_id='$SID' ORDER BY id"

# Tracebacks since last restart
tail -100 ~/.brain-agent/server.error.log | grep -iE "traceback|nameerror|attribute" | head
```

Expected: `event: done` fires, an assistant row is persisted, no tracebacks.
