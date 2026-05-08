# engine/ — Core Agentic Engine

Non-obvious invariants for this package. Root CLAUDE.md has the full architecture picture.

**Runtime source of truth — IMPORTANT.** `brain.py` is the live monolith — everything that handlers/server.py reach via `import brain as engine` runs from `brain.py`'s namespace. The agentic loop, system-prompt builder, tool dispatch, provider routing, and all middleware live in `brain.py`. The `engine/` subtree is a **partial extraction** — only the modules below are actually imported at runtime.

**Live engine/ modules (imported externally):**

| File | Owns | Importers |
|------|------|-----------|
| `tools/*.py` | Tool implementations (files, web, git, email, code_graph, image_gen) | `brain.py` TOOL_DISPATCH |
| `kg_extract.py` | LLM-driven triple extraction over project input folders | `handlers/admin.py`, `handlers/projects.py`, `server.py` |
| `doc_convert.py` | binary → `.md` companion conversion | `engine/tools/files.py`, `server.py` |
| `sync_log.py` | Sync run log table + helpers | `handlers/projects.py`, `server.py` |
| `memory/` | MemPalace direct integration, daemons, chat-sync | `engine/tools/`, `brain.py` |

**Deleted in 8.29.0**: `loop.py` (3819 LOC) and `constants.py` (878 LOC). **Deleted in 8.30.0**: `agents.py`, `cli.py`, `context.py`, `mcp.py`, `models.py`, `provider.py`, `scheduler.py`, `tasks.py` (8 modules, ~7573 LOC) plus the entire `analytics/` package — `audit.py`, `costs.py`, `pii.py`, `quotas.py`, `tracing.py` (~2345 LOC). Total ~9918 LOC of dead extraction. Same pattern as 8.29.0: brain.py owns the live equivalents (`CostTracker`, `AuditLog`, `TraceManager`, `PIIScanner`, `QuotaManager`, `MCPManager`, `LocalProviderQueue`, `Scheduler`, `AgentConfig`, `ContextManager`, scheduler/tasks/cli helpers). The 8.30.0 sweep also fixed silently-broken OCR cost tracking — `engine/doc_convert.py:_log_ocr_cost` was reading the dead `engine.analytics.costs._cost_tracker` singleton (always `None`); rewired to `brain._cost_tracker` and ported `log_ocr` onto brain's `CostTracker`.

The invariants below describe brain.py's runtime behavior.

## Agentic Loop (brain.py)

- Entry: `send_message_with_fallback` → `send_message` → `_handle_openai_response`
- Middleware between rounds: `_middleware_cancel_check`, `_tool_result_budget`, `_microcompact`, `_compress_old`, `_compaction`, `_pyexec_hint`
- Tool exec pipeline: built-in pre → external pre → execute → built-in post → external post → `_after_file_write`
- `AskUserQuestion` blocks via `_pending_answers[session_id]` + `Event`; unblocked by `POST /v1/chat/answer`
- `_rollback_messages()` on cancel/error reverts intermediate tool-loop messages AND saves streamed text + tools

**Diminishing-returns guard**: after round 3, if last 2 completion-token deltas are each <500, loop stops (`tools=False` + `tool_loop_stop` SSE).

**Tool-call dedup**: session-scoped (1h TTL, 100 entries). 1 dup = error, 2 dups = `TaskCancelled`. `reset_tool_dedup()` runs at turn start. Exempt: `memory_recall`, `memory_shared`, `delegate_task`, `task_status`, `schedule_list`, `schedule_history`, `read_document`, `read_file`. Worker threads must inherit `current_session_id`, `current_agent`, `mcp_manager`, `current_user_id` via `_execute_tool_in_thread`.

**Parallel tool calls**: `_execute_tools_batch()` partitions into batches — consecutive concurrent-safe tools run in `ThreadPoolExecutor`, unsafe sequentially. `_CONCURRENT_SAFE_TOOLS` lives in `brain.py`.

**Three-layer hooks**: tool pre/post (external subprocess), `after_file_write` (centralized), LLM-level (built-in middleware). External hook: timeout 5s, fail-open on crash, exit 1=block, exit 2=skip chain. `allowed_tools` restriction in workflows IS enforced — don't let it regress.

## Provider & Warmup (brain.py)

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}`.

**Provider concurrency queue** (`LocalProviderQueue`): slot held during urlopen + SSE drain. `_handle_openai_response` calls `release_slot()` before tool dispatch / recursive `send_message`. Key is `provider_name`, not `base_url`.

**KV-prefix stability**: warmup payload MUST match first-turn payload byte-for-byte — system prompt timestamp rounded to hour (not minutes), MCP tools attached, tools merged/deduped/sorted, `stream=True` + `stream_options`. Warm pool `claim()` only fires for `{agent:main, project:'', status:'', note_context:''}` — anything else changes system prompt and invalidates prefix.

**Pool invalidation fields** (`_prefix_fields`): warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile.

**oMLX gotcha**: Qwen3/Gemma-4 chat templates default `enable_thinking=true` when kwarg absent. `_apply_inference_to_payload` ALWAYS emits `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`. Warmup must mirror this byte-for-byte or KV prefix misses silently.

## Model Config (brain.py)

`init_models_config` does forward-looking re-detect of thinking format: `'none'` → real format upgrade, never reverse. **Must deep-copy `existing_models`** — shallow copy aliases dicts and silently breaks the diff-based persist gate.

`MODEL_PROFILES` overlays: **only request-style knobs**, never resource knobs (warmup, GPU) — silently re-enables user-toggled-off fields.

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Worker Subagents (brain.py)

- `_summarise_tool_result` returns **3 values** (summary, sections, usage) — callers must unpack 3, not 2.
- `"heavy": "auto"` only wraps when output > `auto_threshold_bytes`. Raw output never re-injected.
- Concurrency cap: `execution.max_concurrent_workers_per_session` (default 3).
- Per `(session_id, tool_use_id)` idempotency dedup.

## Concurrency & Thread Safety

- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id`. Never fall back to globals — concurrent requests bleed.
- **MCPManager** (`brain.py`): `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot.
- **Background threads** (scheduler, TaskRunner, workflow engine): set + clean thread-locals in try/finally.
- **`_run_delegate`** uses thread-local `max_tool_rounds` override — no global mutation.

## Scheduled Tasks (brain.py)

Each run = immutable `schedule_history` row + synthetic `session_id=sched-<run_id>`. Due tasks fire in **parallel**. Per-task `working_dir` overrides system prompt cwd; `python_exec` stays pinned to artifact folder by design — file-write tracking depends on it.

`_validate_thinking_level_for_model` rejects format-mismatched levels at fire time. `caveman_system` deliberately NOT exposed per task (per-model knob, would invalidate warmup KV prefix).

## Knowledge Graph (kg_extract.py / doc_convert.py)

`_run_kg_for(...)` resolves prefix via `os.path.realpath()` — macOS `/tmp` → `/private/tmp`; without this drawer source_files don't match.

Source-change invalidation DELETEs triples rows matching **exact** `source_file` (not LIKE prefix — siblings stay safe).

`doc_convert.py` companion `.md` files live under `<folder>/.brain-extracted/<name>.<ext>.md`. `<!-- brain-source: <abs path> -->` lets agent resolve back to the original binary.
