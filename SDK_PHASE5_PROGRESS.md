# Phase 5 Deletion Pass — Session State (2026-05-14)

Companion to `SDK_MIGRATION_HANDOVER.md` and `SDK_MIGRATION_PLAN.md`.
This document captures the in-flight state of the Phase 5 deletion campaign so the next session can pick up cleanly.

---

## ⏭️ Next session: pick up here

**Steps 1, 2, 3, 5, 6 are done and committed. Step 4 was skipped at user direction. Step 7 is next.**

The /v1/chat path through the sidecar is verified live after every step. Eval has NOT been re-run since v8.37.0 — defer to gate-3 at end of step 9.

**Resume from**: step 7 (native loop core deletion).

The audit cross-references below list the LIVE callers of `_run_delegate` /
`_run_delegate_with_fallback` that must migrate to `sidecar_proxy.background_call`
BEFORE `_run_delegate` can be deleted. Start there.

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
| 7 | Delete native loop core | PENDING | — | — |
| 8 | Unwire LCM auto-trigger; add manual button | PENDING | — | — |
| 9 | Update CLAUDE.md + tag v9.0.0 | PENDING | — | — |
| 10 | Gate-3 eval run | PENDING | — | — |

**Net so far**: −2044 LOC code, +597 LOC docs. Five code commits.

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
