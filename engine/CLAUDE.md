# engine/ — Core Agentic Engine

Non-obvious invariants for this package. Root CLAUDE.md has the full architecture picture.

## Module Map

| File | Owns |
|------|------|
| `loop.py` | Agentic loop, tool dispatch, `send_message*`, all middleware |
| `provider.py` | Provider resolution, concurrency queue, warmup, warm-session pool |
| `models.py` | Model config, `init_models_config`, `_match_known_model`, thinking-format detection |
| `agents.py` | Agent config, tool-group loading, skills/plugin management |
| `scheduler.py` | `Scheduler` class, cron firing, per-task watchdog |
| `tasks.py` | `TaskRunner`, `WorkflowEngine`, delegation tools (`_run_delegate`) |
| `mcp.py` | MCP stdio/SSE client + `MCPManager` |
| `context.py` | `ContextManager` (SQLite DAG), token helpers, cancellation primitives |
| `execution.py` | Worker-subagent routing (`run_worker_subagent`, `route_tool_execution`) |
| `constants.py` | `TOOL_DEFINITIONS`, `MODEL_PROFILES`, `_CONCURRENT_SAFE_TOOLS`, version |
| `memory/` | MemPalace direct integration (no MCP), daemons, chat-sync |
| `analytics/` | Cost tracking, PII scanner, quota manager, audit log, tracing |
| `tools/` | Tool implementations: files, git, web, email, code graph |
| `kg_extract.py` | LLM-driven triple extraction post-pass over project input folders |
| `doc_convert.py` | binary → `.md` companion conversion (idempotent, `(mtime, size)` frontmatter) |

## Agentic Loop (loop.py)

- Entry: `send_message_with_fallback` → `send_message` → `_handle_openai_response`
- Middleware between rounds: `_middleware_cancel_check`, `_tool_result_budget`, `_microcompact`, `_compress_old`, `_compaction`, `_pyexec_hint`
- Tool exec pipeline: built-in pre → external pre → execute → built-in post → external post → `_after_file_write`
- `AskUserQuestion` blocks via `_pending_answers[session_id]` + `Event`; unblocked by `POST /v1/chat/answer`
- `_rollback_messages()` on cancel/error reverts intermediate tool-loop messages AND saves streamed text + tools

**Diminishing-returns guard**: after round 3, if last 2 completion-token deltas are each <500, loop stops (`tools=False` + `tool_loop_stop` SSE).

**Tool-call dedup**: session-scoped (1h TTL, 100 entries). 1 dup = error, 2 dups = `TaskCancelled`. `reset_tool_dedup()` runs at turn start. Exempt: `memory_recall`, `memory_shared`, `delegate_task`, `task_status`, `schedule_list`, `schedule_history`, `read_document`, `read_file`. Worker threads must inherit `current_session_id`, `current_agent`, `mcp_manager`, `current_user_id` via `_execute_tool_in_thread`.

**Parallel tool calls**: `_execute_tools_batch()` partitions into batches — consecutive concurrent-safe tools run in `ThreadPoolExecutor`, unsafe sequentially. `_CONCURRENT_SAFE_TOOLS` is in `constants.py`.

**Three-layer hooks**: tool pre/post (external subprocess), `after_file_write` (centralized), LLM-level (built-in middleware). External hook: timeout 5s, fail-open on crash, exit 1=block, exit 2=skip chain. `allowed_tools` restriction in workflows IS enforced — don't let it regress.

## Provider & Warmup (provider.py)

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}`.

**Provider concurrency queue** (`LocalProviderQueue`): slot held during urlopen + SSE drain. `_handle_openai_response` calls `release_slot()` before tool dispatch / recursive `send_message`. Key is `provider_name`, not `base_url`.

**KV-prefix stability**: warmup payload MUST match first-turn payload byte-for-byte — system prompt timestamp rounded to hour (not minutes), MCP tools attached, tools merged/deduped/sorted, `stream=True` + `stream_options`. Warm pool `claim()` only fires for `{agent:main, project:'', status:'', note_context:''}` — anything else changes system prompt and invalidates prefix.

**Pool invalidation fields** (`_prefix_fields`): warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile.

**oMLX gotcha**: Qwen3/Gemma-4 chat templates default `enable_thinking=true` when kwarg absent. `_apply_inference_to_payload` ALWAYS emits `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`. Warmup must mirror this byte-for-byte or KV prefix misses silently.

## Model Config (models.py)

`init_models_config` does forward-looking re-detect of thinking format: `'none'` → real format upgrade, never reverse. **Must deep-copy `existing_models`** — shallow copy aliases dicts and silently breaks the diff-based persist gate.

`MODEL_PROFILES` overlays: **only request-style knobs**, never resource knobs (warmup, GPU) — silently re-enables user-toggled-off fields.

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Worker Subagents (execution.py)

- `_summarise_tool_result` returns **3 values** (summary, sections, usage) — callers must unpack 3, not 2.
- `"heavy": "auto"` only wraps when output > `auto_threshold_bytes`. Raw output never re-injected.
- Concurrency cap: `execution.max_concurrent_workers_per_session` (default 3).
- Per `(session_id, tool_use_id)` idempotency dedup.

## Concurrency & Thread Safety

- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id`. Never fall back to globals — concurrent requests bleed.
- **MCPManager** (`mcp.py`): `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot.
- **Background threads** (scheduler, TaskRunner, workflow engine): set + clean thread-locals in try/finally.
- **`_run_delegate`** uses thread-local `max_tool_rounds` override — no global mutation.

## Scheduled Tasks (scheduler.py)

Each run = immutable `schedule_history` row + synthetic `session_id=sched-<run_id>`. Due tasks fire in **parallel**. Per-task `working_dir` overrides system prompt cwd; `python_exec` stays pinned to artifact folder by design — file-write tracking depends on it.

`_validate_thinking_level_for_model` rejects format-mismatched levels at fire time. `caveman_system` deliberately NOT exposed per task (per-model knob, would invalidate warmup KV prefix).

## Knowledge Graph (kg_extract.py / doc_convert.py)

`_run_kg_for(...)` resolves prefix via `os.path.realpath()` — macOS `/tmp` → `/private/tmp`; without this drawer source_files don't match.

Source-change invalidation DELETEs triples rows matching **exact** `source_file` (not LIKE prefix — siblings stay safe).

`doc_convert.py` companion `.md` files live under `<folder>/.brain-extracted/<name>.<ext>.md`. `<!-- brain-source: <abs path> -->` lets agent resolve back to the original binary.
