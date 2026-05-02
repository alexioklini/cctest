# CLAUDE.md

Guidance for Claude Code in this repo. **Non-obvious invariants only** — what's not derivable from the code. Factual catalogs (full tool list, endpoint list, every config field) live in the code; grep/read it.

## Repository Structure

- `launcher.py` — Gateway CLI (start/stop/restart, launch frontends)
- `server.py` — HTTP API daemon (launchd-managed, port 8420)
- `client.py` — Shared HTTP/SSE client library
- `brain.py` — Core engine: tools, agents, MCP, scheduler, agentic loop
- `tui.py`, `telegram.py` — Terminal + Telegram frontends
- `web/index.html` — Single-page web UI
- `desktop/` — Electron shell (CORS-free IPC + lazy llama.cpp host)
- `tools.md` — Global tool-usage guide (loaded into system prompt)
- `config.json` — Providers, server, Telegram (gitignored)
- `agents/<name>/` — `soul.md`, `agent.json`, `skills/`, `mcp.json`; SQLite DBs in `agents/main/`

## Architecture

```
launcher.py → server.py (port 8420)
                ├── brain.py  # engine, native agentic loop, LCM
                ├── /mcp endpoint  # JSON-RPC tools/list + tools/call
                ├── SQLite         # chats, scheduler, context, costs, traces, audit, auth
                └── MemPalace      # direct in-process, no MCP
```

All chat goes through the native Python agentic loop. No SDK sidecars. All providers OpenAI-compatible (Anthropic/Mistral handlers removed v7.2/7.3).

## Agentic Loop

- Entry: `send_message_with_fallback` → `send_message` → `_handle_openai_response`
- Middleware between rounds: `_middleware_cancel_check`, `_tool_result_budget`, `_microcompact`, `_compress_old`, `_compaction`, `_pyexec_hint`
- Tool exec: built-in pre → external pre → execute → built-in post → external post → `_after_file_write`
- `AskUserQuestion` blocks via `_pending_answers[session_id]` + `Event`; unblocked by `POST /v1/chat/answer`
- Partial-response recovery: `_rollback_messages()` saves streamed text + tools on cancel/error

**Diminishing-returns guard**: after round 3, if last 2 completion-token deltas are each <500, loop stops (`tools=False` + `tool_loop_stop` SSE).

**Tool-call dedup**: session-scoped (1h TTL, 100 entries). 1 dup = error, 2 dups = `TaskCancelled`. `reset_tool_dedup()` runs at turn start. Exempt: `memory_recall`, `memory_shared`, `delegate_task`, `task_status`, `schedule_list`, `schedule_history`, `read_document`, `read_file`. Worker threads must inherit `current_session_id`, `current_agent`, `mcp_manager`, `current_user_id` via `_execute_tool_in_thread`.

## Multi-Provider Routing

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}`. Used by chat, delegate, scheduler, warmup, background. Providers are plain OpenAI-compatible entries in `config.json` → `providers`.

**Provider-scoped IDs**: when multiple providers serve the same model, entries stored as `provider/model_id` with `base_model_id`. Historical scoped ids (`OMLX/*`, `Bifrost/*`, `mistral/*`) still route. Bifrost retired 8.5.0 (dropped nested reasoning blocks).

## Chat File Attachments

Files go to `state._pendingFiles[]` as base64; sent as `body.files` (legacy `body.images` for Telegram).

Per-file routing checks model `raw_formats` (MIME pattern list):
- **Multimodal**: MIME match + base64 + <20MB → OpenAI `image_url` data URI
- **Disk**: otherwise → `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- **Image fallback on non-vision models**: `attachments.image_model` describes via vision LLM; unconfigured → metadata only

## Artifacts

Files written under `agents/<name>/artifacts/<date>_<session_prefix>/` are auto-promoted. `write_file` with relative path defaults into the session's artifact folder.

- Each write/edit → row in `artifact_versions` (5MB cap); SSE `artifact_updated`
- **Role classification**: `_ARTIFACT_INTERMEDIATE_EXTS` (.py/.sh/.js/.json/.csv/.log/etc.) → `intermediate`; rest (.md/.html/.pdf/images) → `output`. Browse grid defaults to outputs-only.

## Scheduled Task Runs

Each run = immutable `schedule_history` row (id=run_id) + synthetic `session_id=sched-<run_id>` scoping artifacts + traces.

- **Per-task attachments**: `schedules.attachments` JSON list. Uploaded once, **referenced in place** every fire (no per-run copy). `_purge_attachment_paths()` refuses paths without `scheduled_attachments` segment.
- **Per-task working_dir**: overrides system prompt cwd line. **`python_exec` stays pinned to artifact folder by design** — file-write tracking depends on it.
- **Per-task `thinking_level` + `caveman_chat`**: empty `thinking_level` inherits at fire time. `caveman_system` deliberately NOT exposed per task (per-model knob, would invalidate warmup KV prefix). `_validate_thinking_level_for_model` rejects format-mismatched levels.

## Format-Aware Thinking Level

Dropdown shape identical everywhere; only options the chosen model can honor are shown.

`_thinkingOptionsForFormat(fmt)` (web UI source of truth):
- `none` → disabled
- `inline_tags` → Off / On (Qwen3-style)
- `mistral_blocks` → Off / High (Mistral API only accepts none/high)
- `reasoning_field` / `openai_opaque` → Off / Low / Medium / High

Composer button cycles only valid steps; `refreshThinkingButton` is **self-correcting** — demotes saved value to `'none'` if not in new format's set.

`_detect_thinking_format(model_id, provider)` is provider-aware (`cliproxyapi`+gemini-2.5* → `reasoning_field`; oMLX + reasoning substrings → `reasoning_field`). `init_models_config` does forward-looking re-detect: `'none'` → real format upgrade, never reverse. **`init_models_config` deep-copies `existing_models`** — shallow copy aliases dicts and silently breaks the diff-based persist gate.

## Next-Prompt Suggestions

`GET /v1/sessions/<id>/next-prompt` after each turn → dimmed placeholder. Reuses session model + history, `tools=False`, tiny `max_tokens`. **Real cost** — earlier "near-free via prompt cache" claim was Anthropic-wire specific and is dead post-v7.2.0.

## Model Management

Per-model fields in `config.json` → `models`. `_match_known_model()` seeds from `KNOWN_MODELS`. Manual add: id + provider + display name (for providers without `/models`).

**Optimization profiles** (`MODEL_PROFILES`): sparse overlays, **only request-style knobs** (never resource knobs like warmup — would silently re-enable user-toggled-off fields). Explicit per-model fields still win.
- `speed` (auto for local): `deferred_tool_groups=[]` (stable KV prefix > lean-but-shifting), `compact_threshold=0.85`
- `balanced` (auto for cloud)
- `frugal`: cloud-only safe
- `custom`: no overlay

Profile changes invalidate warm-pool KV prefix.

**Thinking model auto-recovery**: on `finish_reason=length` + visible output <25% of completion tokens, `max_tokens` doubles on retry (capped at `max_context`).

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Thinking / Reasoning Models

Reasoning output format isn't standardized. `thinking_format` per model:
- `none` / `inline_tags` (`<think>...`, `_InlineThinkingSplitter` is SSE-boundary-safe) / `reasoning_field` (sibling delta) / `mistral_blocks` (nested type:thinking blocks) / `openai_opaque`

**Persistence**: each round → `role='thinking'` row with `metadata.tool_round`. `_ALLOWED_MSG_KEYS` / `_INTERNAL_ROLES` strip `thinking` rows before wire — UI-only.

**Wire mapping** (`_apply_inference_to_payload`): `inf_params["thinking_level"]` → `reasoning_effort` for `mistral_blocks` (forced "high"), `reasoning_field`, `openai_opaque`. oMLX uses `chat_template_kwargs.enable_thinking`.

**oMLX gotcha**: Qwen3/Gemma-4 chat templates default `enable_thinking=true` when kwarg absent. `_apply_inference_to_payload` ALWAYS emits `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`. **Warmup must mirror this byte-for-byte** or KV prefix misses silently.

## Caveman Mode (Dual)

Two settings, independent, compose:
- **System** (`caveman_system` per model, 0–3): compresses system prompt via `_caveman_compress_text()`
- **Chat** (`caveman_mode` in sessions DB, 0–3): appends `CAVEMAN_CHAT_PROMPTS` response-style instruction

Thread-locals set in chat worker, cleaned in `finally`. **Cache key for `_build_system_prompt` includes both.**

## Token Optimization

Per-agent `token_config` in `agent.json`: `tool_groups`, `extra_tools`, `include_tools_guide`, `compact_threshold`, `mcp_tool_filter`/`mcp_tool_exclude`, `deferred_tool_groups`. System prompt cached per-session (60s TTL).

Per-agent `limits`: `max_tool_rounds` (soft cap, hard stop at 1.5×), `tool_result_char_limit`, `tool_results_total_tokens`, `context_safety_ratio` (default 0.95).

## Per-User Cost Quotas

`QuotaManager` singleton (30s config cache). Two axes per user: **Daily** (rolling, UTC) + **Cycle** (`monthly`/`weekly`/`yearly` w/ anchor). Worst axis wins.

- **Pre-flight gate** in `send_message` round 0, after GDPR. `is_model_local(model)` always bypasses.
- Modes (`quotas.enforce_red`): `warn_only` (default), `force_local` (silent swap to `default_local_fallback_model`), `hard_block` (`QuotaExceededError`).
- `_log_call_cost` captures `_thread_local.current_user_id`. Empty `user_id` rows are pre-quota legacy.
- Limit `0` = "no limit" on that axis.

## GDPR / PII Pre-Submit Scanner

71 regex detectors, client + server side. Zero external APIs.

**Two mirrored implementations** (must stay in sync): `PIIScanner` in `web/index.html`; `_pii_rules()` + `_pii_scan_text()` + `_pii_scan_bare_identifiers()` in `brain.py`.

**Three rule tiers** (first-match-wins, overlap suppression): cloud secrets/API keys → national IDs with checksums (~30 countries) → context-fallback + bare-identifier heuristic.

**Rule-order invariants**:
- Context-gated rules (DE Steuer-ID, NL BSN, HU TAJ) before generic bare-digit rules
- `credit_card` AFTER all national-ID checksum rules (RO CNP, KR RRN are 13-digit Luhn-passing)
- `phone` AFTER national IDs (`XXX-XXX-XXXX`-shaped SIN/NHS would steal phone slot)
- `credit_card` regex has `(?<![+\d])` so `+CC...` phone prefixes don't match

**Overlap suppression**: successful matches claim spans; failed validations DON'T (lets weaker rules re-scan inside an invalid IBAN — why Aadhaar/PESEL/Steuer-ID are context-gated).

**Routing**: hard-block raises pre-LLM **only when model is non-local** — local bypasses block (data stays on-prem). `gdpr_pick_model_for_background(model, texts, purpose)` is the **single decision point** for non-interactive calls: scan → audit `pii_detected` → swap to `default_local_fallback_model` if configured (audit `pii_auto_fallback`) → else if `server_block` raise `GDPRBlockedError` (audit `pii_blocked`) → else warn-only.

**Client local interlock**: `piiBlockActive(chat)` filters model dropdown to local-only when `server_block=true` + scanner enabled + (draft or loaded history has PII). Auto-swaps via `piiEnsureLocalModel()`.

**`is_local`**: `is_model_local()` → `_is_local_base_url()` matches localhost/127/0.0/RFC1918.

**Config** (`gdpr_scanner`): master toggle, `server_log`, `server_block`, `default_local_fallback_model`, 8 per-category actions (`ignore`/`warn`/`block`), `rule_overrides`, `email_allowlist`. `block` downgraded to `warn` when `server_block` master is off. `PII_RULE_CATEGORIES` + `PII_DEFAULT_CATEGORY_ACTIONS` mirrored as `PIIScanner.ruleCategories` in web UI.

**Not detected**: personal names, addresses, ICD codes, generic passport/license without context.

## Python Code Execution

Opt-in via `code_exec` in `tool_groups`. Subprocess isolation (`sys.executable`), timeout-killed. **Working dir = artifact session folder** — files written auto-register as artifacts; state persists across calls.

- **Auto-artifact fallback**: stdout >1K chars + no files written → saved as `output.txt`; preview shows head+tail
- `_middleware_pyexec_hint`: when 3+ consolidatable tool calls in one turn (read/search/write/edit), injects one-shot consolidation hint. Only fires if agent has `code_exec`.

## Worker Subagents

Heavy tools route through worker that writes raw output to artifact store and returns **compact envelope** with LLM-summary. Main context stays small.

- Routing: `_execute_tool` → `route_tool_execution` → `run_worker_subagent` for `"heavy": true` profiles. `"heavy": "auto"` only wraps when output > `auto_threshold_bytes`.
- Raw output in `agents/<id>/artifacts/<session_folder>/worker_<tool>_<uuid8>.json` — **never re-injected**
- **Summariser**: `_summarise_tool_result` returns 3 values (summary, sections, usage) — callers must unpack 3, not 2. Tokens via `worker_usage` SSE → status bar reflects full spend.
- **Idempotency**: per `(session_id, tool_use_id)` dedup
- **Concurrency cap**: `execution.max_concurrent_workers_per_session` (default 3)

## Session Inspector

`request_payloads[]` in assistant `metadata`, one entry per `_tool_round` (populated by `request_payload` SSE). Real `tokens_in/out` attached via `usage` SSE per round. `_usage_totals` sums main-round + worker-side `worker_usage`.

## Parallel Tool Calls

`parallel_tool_calls: true` (per-model toggle, default on). `_execute_tools_batch()` partitions into batches: consecutive concurrent-safe tools run in `ThreadPoolExecutor`, unsafe sequentially.

`_CONCURRENT_SAFE_TOOLS`: `read_file`, `list_directory`, `search_files`, `read_document`, `exa_search`, `web_fetch`, `code_graph_query`, `schedule_list`, `schedule_history`, `list_nodes`, `task_status`, `context_*`, `git_command` (read-only).

## Provider Concurrency Queue

`LocalProviderQueue` gates concurrent HTTP calls per provider via semaphore + FIFO waitlist.

- **Opt-in per provider**: `providers.<name>.max_concurrent` (0 = unlimited). Seeded: `omlx=2`, `cliproxyapi=2`, cloud=0.
- **oMLX continuous batching** (`gemma-4-26b`, Apple Silicon): batch=1 → 63 tok/s, 2.3s TTFT; batch=2 → 80 tok/s aggregate (40/req), 4.3s TTFT; batch=4 → 91 tok/s, 8.6s TTFT. **`2` is the sweet spot**. CLIProxyAPI does NOT batch — for it `max_concurrent` is pure parallelism cap.
- **Scope HTTP-only**: slot held during urlopen + SSE drain. `_handle_openai_response` calls `release_slot()` before tool dispatch / recursive `send_message`.
- **Wrapped sites**: `send_message`, `_run_delegate`, `run_model_warmup`, `classify_chat_for_memory`. Others transitive.
- **Key invariant**: queue key is `provider_name`, not `base_url`. Re-evaluate if two providers share base_url.

## Warmup & Warm Session Pool

Local models pre-primed so first-token latency drops ~15s → 2–3s. Opt in per model via `warmup: true`. Requires prompt-cache-capable providers.

- **Modes**: `full` (system + tools + "." → primes KV prefix) vs `minimal` (1-token user, no system, no tools)
- **KV-prefix stability rule**: warmup payload MUST match first-turn payload byte-for-byte. Critical: system prompt timestamp **rounded to hour** (not minutes), MCP tools attached via `_thread_local.mcp_manager`, tools merged/deduped/sorted, `stream=True` + `stream_options` passed
- **Keeper**: failed primes bump `last_warmup_ts` so OOM-failing models don't starve others
- **Warm pool** (`WarmSessionPool`): N pre-built Sessions per warmup-flagged model. Bound to `agent=main, status=warm_pool` (hidden). `claim()` only fires for `POST /v1/sessions` matching `{agent:main, project:'', status:'', note_context:''}` — anything else changes system prompt and invalidates prefix.
- **Pool invalidation**: `_prefix_fields` = (warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile). Any change drops slots.
- **Multi-model GPU tradeoff**: tight RAM = primed models evict each other. Either size host or set one to `minimal`.

## Client Execution Mode

Air-gapped servers with internet-on-browser: `execution_mode: client` in `config.json`. Loop stays on server; LLM calls + web tools proxy through browser.

- **LLM**: server emits `proxy_request` SSE → browser POSTs to provider → streams back via `POST /v1/chat/proxy-response`
- **Web tools**: `proxy_tool` SSE → browser executes → `POST /v1/chat/proxy-tool-result`
- Local tools (file ops, git, shell, code graph) run on server normally
- `client_proxy_tools` list (default `web_fetch`, `exa_search`)
- **Server-local models bypass proxy**: when `_is_local_base_url(provider)` is true, `send_message` skips `proxy_request` and calls gateway directly

## Client-Hosted Local Inference

Desktop clients can declare they serve a model family locally; server transfers matching requests to client. Per-request decision based on session capability handshake. Scheduler/delegates/background never reach this branch.

- **Identity by `family`, not id**: server's `gemma-3-e4b-mlx-4bit` and client's `gemma-3-4b-it-Q4_K_M` both declare `family=gemma3-e4b`
- **Manifests**: `client_models` (per-model `{id, family, gguf_path, sha256, size_bytes}` — `sha256`+`size_bytes` recomputed server-side every save, never trust admin input). `client_engines` per-platform `{url, sha256}` — server refuses to invent defaults (air-gap leak prevention)
- **Routing**: `is_model_client_executable(caps, model_id)` returns `(True, family)` iff caps.enabled + manifest match + family in caps.families. Emits `local_inference_request` SSE. **No server-side retry** on failure.
- **Desktop** (`desktop/local-inference.js`): fully lazy. Cache keyed by sha256. Resumable downloader (`Range`, streaming sha256 verify, `.partial` siblings). Engine `chmod +x` on Unix. **Archive distributions not supported** — direct binary URL only. `llama-server` on random free port, `/health` poll, 10-min idle SIGTERM. FIFO queue (`max_concurrent=1`).
- **Auth**: manifest reads + weights downloads open to any auth user. CRUD admin-only.

## Desktop App (Electron)

Shell loading web UI + CORS-free Node IPC. Required for client-mode on air-gapped servers. `--server=http://host:port` CLI arg. Build: `npm run build:{mac,win,all}`. `desktop/local-inference.js` registers `localInference.*` IPC for llama-server lifecycle.

## Agent Teams

- **Team head**: `team` field in `agent.json`
- **Members**: agents listed in head's `team.members`
- **Standalone**: not in any team
- **main**: global orchestrator, never has `team`

Scoping: `main` → heads + standalone (not members directly). Heads → their members. Members → peers + head.

## Tools

Source of truth: `TOOL_DEFINITIONS` in `brain.py` (Anthropic flat shape; `TOOL_DEFINITIONS_OPENAI` derived). Groups: core, documents, code_graph, web, email, delegation, git, scheduler, mcp, skills, nodes, context, memory, code_exec.

**Constraints**:
- `execute_command`: no TTY, no stdin, `TERM=dumb`. Banned commands in `tools.md`.
- Memory is MemPalace **direct, not MCP**. Tool: `mempalace_query` (+ `save_chat_to_memory`, `mempalace_get_drawer`, `mempalace_list_drawers`).

## Server API

Port 8420. Source of truth: grep `@app.route` / `self.path` dispatch in `server.py`. SSE streams use 5s keepalive comments.

## Deployment

- Server: launchd daemon (`com.brain-agent.server.plist`)
- Telegram: in-process thread
- Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev`
- **Log files (debugging gotcha)**: launchd routes both fd1 and fd2 to **`server.error.log`**. All daemon `print()` lands there, NOT `server.log` (which only gets startup banner). Always tail `server.error.log`.

## Concurrency & Thread Safety

- **`Session.lock`**: all field mutations under it
- **`SessionManager.get()`**: `_LOADING_SENTINEL` + `Event` prevents duplicate Sessions for same id. `peek()` for cache-only reads.
- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id` (drives MemPalace per-user isolation). Never fall back to globals — concurrent requests bleed.
- **MCPManager**: `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot.
- **Background threads** (`_generate_chat_summary`, scheduler, workflow engine, TaskRunner): set + clean thread-locals in try/finally.
- **LLM JSON parsing**: `_extract_json_from_llm()` uses `json.JSONDecoder.raw_decode()` — handles nested objects, fences, surrounding text.
- **SQLite**: connections via `threading.local()` pools — **not** dict-keyed-by-ident (leaks FDs under `ThreadingMixIn`). All ChatDB methods wrapped with `@_db_safe`.
- **Client proxy SSE**: line buffering carries incomplete lines across TCP chunks.

## Key Invariants

- `augmented_messages` strips metadata fields (only `role`+`content` to API) — prevents 400s
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- `_rollback_messages()` on cancel/error reverts intermediate tool-loop messages AND saves streamed text + tools
- **Scheduled tasks**: configurable timeout (default 5min) via watchdog. Scheduler executes due tasks in *parallel*. `_run_delegate` uses thread-local `max_tool_rounds` override — no global mutation.
- Provider fallback ordering: same provider first, then capabilities, then priority
- Sidebar list polls after stream end until async LLM summary arrives (2s, 30s max)
- Multipart upload: manual boundary parser (3.13+ removed `cgi`); preserves original filename
- **Three-layer hooks**: tool pre/post (external subprocess), `after_file_write` (centralized), LLM-level (built-in middleware). External hook: timeout 5s, fail-open on crash, exit 1=block, exit 2=skip chain. `allowed_tools` restriction in workflows IS enforced (was dead code — don't let it regress).

## Lossless Context Manager

`ContextManager` with SQLite DAG in `context.db`. Three-level escalation: leaf summaries → condensation → fallback truncation. Assembly: summaries (highest depth first) + fresh tail (default 16 messages) within token budget. Legacy `_compact_conversation` is fallback when disabled. Tools: `context_search`, `context_detail`, `context_recall`. SSE: `compacting`/`compacted`.

## Code Structure Graph

Tree-sitter AST parsing, SQLite in `code-graph.db`. 14 langs. Qualified names `{file_path}::{ClassName.method}`. Edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY. Incremental: SHA-256 hash skip; `_after_file_write` triggers `_maybe_update_code_graph(path)`.

## Projects

`ProjectManager` CRUD; `instructions` field in `project.json` injected into system prompt; multipart file upload to `IngestManager`.

- **Project ID**: `id` field in `project.json` is uuid4 hex[:12], assigned on first read. **MemPalace wing key** — renaming doesn't strand drawers, same-named projects under different agents don't collide. `create_project` mints upfront; backfilled lazily for legacy.
- Archive: `status: "archived"` (files preserved). Delete: soft to `.trash/`.
- **Notes**: AI editing uses `write_file`/`edit_file` (not tag-based). Note-AI sessions use `status: note_chat`, hidden from project chat list.

### Project Instructions (response disciplines, default)

`DEFAULT_PROJECT_INSTRUCTIONS` (brain.py) is the FALLBACK when `project.json.instructions` is empty. Override REPLACES default rather than appending.

Three disciplines (designed against German bank-policy corpus failure modes; model-agnostic):
- **REFUSAL**: when `mempalace_query` returns 0 relevant + follow-up `read_document` confirms absence, refuse with canonical sentence — never general-knowledge fallback. Cap rephrasings at 2-3.
- **PRECISION**: bans plausible filler. Words like *regelmäßig*, *häufig* gated on immediate `> "..."` quote. Without source value: write `nicht spezifiziert` — never plausible defaults. ISO-27001-typical phrasing from training data is NOT a source.
- **CITATION**: every factual claim paired with `[Quelle: <basename> — "<wörtliches Zitat 10-25 Wörter>"]`. `§N` paragraph markers FORBIDDEN — `.md` companions don't preserve original numbering. Cite `.brain-extracted/<name>.<ext>.md` as the original binary's name.

Brain mechanics (3-step retrieval flow, `read_path` vs `read_path_original`, binary→.md companion rule, KG hint block) STAY in system prompt — infrastructure facts, not editable.

**Anti-room-name-guessing**: `mempalace_query` `room` parameter description enumerates real rooms (`general`/`artifacts`/`chat`/`chat_summary`/`chat_attachment`/`reference`) and forbids invention. Earlier permissive list got used as valid vocab by models.

**Sampling defaults for Mistral Small** (gitignored): `temperature: 0.2`, `top_p: 0.85`. `0` rejected by provider; `0.1` showed no improvement.

### Token Optimisations (v8.23)

- **Per-session read_document/read_file cache** (`_read_doc_cache`): keyed by `(session_id, abs_path)`. Repeat full-shape read in same chat → compact stub. Invalidation: `os.stat()` on every lookup; `_after_file_write()` explicit. Pagination args bypass entirely. TTL 1h, max 64 entries/session, LRU-by-turn eviction. `read_document`/`read_file` added to `_DEDUP_EXEMPT` — cache is the smarter dedup.
- **Per-session project preamble**: dynamic project state moved out of `_build_system_prompt` into per-session preamble injected at round 0 first-user-message via `_project_preamble_text(agent_id, project_name)`. KV-prefix stays project-agnostic; ~1KB saved per request on no-cache providers.

### Project Input Folders + Sync

Each project specifies on-disk folders mined into its private MemPalace wing. With manual attachments (`ingested/`), this is project memory.

- **Schema** (`project.json`): `input_folders: [{path, recursive, auto_sync, added_at}]`, `sync_status: {state, last_run_*, total_indexed, total_files, total_triples, items: {<key>: {kind, id, state, drawers_filed, error}}}`. Live snapshot `_project_sync_live[(agent, project)]` carries cycle progress.
- **Path-traversal guard**: realpath-resolved; refuses paths inside `agents/`, `/etc`, `/var`, `/usr`, `/bin`, `/sbin`, `/System`, `/Library/Keychains`.
- **Daemon** (`mempalace-project-sync`): polls every 6h default. Per project: ensure `mempalace.yaml` matches expected wing (auto-rewrite), mine `ingested/` then each input folder. Per-folder cap default 5000. **`total_indexed` cumulative** (survives dedup-only re-runs). **Single-threaded** — multi-project cycles strictly sequential; long projects block all others. `auto_sync=false` skipped on scheduled cycles, bypassed for manual "Sync now".
- **Startup wipe**: drops every drawer in any `project__*` wing AND clears every project's `sync_status`. Currently runs on every restart — needs marker-file gate (see backlog).
- **System prompt** when `_thread_local.project` is set: prepends PROJECT MEMORY block + PROJECT INPUT FOLDERS block (absolute paths + path-join example to resolve `source_file` against folder root before `read_file`/`read_document`).

## Empty-Session Cleanup

Sessions created lazily on first send (not on model switch / `newChat()` / PII swap). `list_sessions` SQL hides 0-message sessions older than 60s. Server startup one-shot purge deletes >5min empty sessions.

## Project Knowledge Graph

LLM-driven document → triples extraction over project input folders + attachments. Post-pass after drawer mining; writes to MemPalace's KG (`<palace_path>/knowledge_graph.sqlite3`).

- `kg_extract.py` — Profile registry (`normative` for policies/regulations/SOPs; `generic` for prose). Two chunking modes: **`source_file`** (default) re-chunks original markdown at `source_chunk_chars` (3500) — ~70× yield improvement. `inference_max_tokens=8000` (reasoning models exhaust mid-JSON below this).
- `doc_convert.py` — pre-mine binary→companion `.md` under `<folder>/.brain-extracted/<name>.<ext>.md`. Idempotent via `(mtime, size)` frontmatter. `<!-- brain-source: <abs path> -->` lets agent resolve back to original binary.
- **`normative` vocabulary**: 12 controlled English predicates (`requires`, `forbids`, `permits`, `defines`, `cites`, `applies_to`, `effective_from`, `supersedes`, `responsible_party`, `condition`, `exception`, `penalty`). Subjects/objects verbatim in source language. Off-vocab allowed as escape hatch.

**Daemon hook**: `_run_kg_for(...)` resolves prefix via `os.path.realpath()` (macOS `/tmp` → `/private/tmp`, without this drawer source_files don't match).

**Optional closet regen**: replaces regex closets with LLM-generated topic lines. **Incremental** via `closet_regen_progress` cursor on per-source `(mtime, size)` — 400 unchanged PDFs costs ms not 400 LLM calls.

**Source-change invalidation**: snapshots `kg_extraction_source_state`. On diff → DELETEs `triples` rows matching **exact** `source_file` (not LIKE prefix — siblings stay safe) + progress rows + orphan-entity sweep.

**Agent KG tools**: auto-scope to caller's project via `_thread_local.project`. Refused outside project context.

### Known constraints

1. **GPU memory tradeoff**: 26B chat warmpool (~22GB) + e4b extraction (~5GB) doesn't fit oMLX's 25.6GB cap. Either raise cap, set 26B `warmup: false` + `oMLX max_concurrent: 1`, or use cloud (`gemini-2.5-flash`).
2. **MemPalace KG schema 3.3.0 vs 3.3.3**: 3.3.0 lacks `source_drawer_id`+`adapter_name`. `kg_extract` falls back via `TypeError` to legacy 5-arg `add_triple`.
3. **MemPalace KG path**: `<palace_path>/knowledge_graph.sqlite3`, NOT `~/.mempalace/knowledge_graph.sqlite3` (`KnowledgeGraph()` no-arg default).
4. **Code skipped**: `_is_code_path()` matches code extensions — folded into Brain's code graph instead.

## Cost Tracking & Rate Limiting

- `CostTracker` logs every LLM call to `costs.db`. Rates from `_cost_rates` defaults + per-model `cost_input/output`.
- `RateLimiter`: sliding-window per agent (requests/min, tokens/hr, cost/day) from `rate_limits` in `agent.json`.

## Per-User Account Settings

Personal state separated from global admin settings.

**Schema** — `users.preferences` JSON on `auth.db` (validated via `PREFERENCE_DEFAULTS` + `_coerce_pref`):
- `greeting_name` (≤64), `job_description` (≤500), `communication_preferences` (≤4000) — surfaced in first-turn preamble
- `memory_chats_default` (0|1|2|null) — overrides server-wide classifier `default_mode`
- `memory_sched_default` (0|1|null) — gates miner from filing user's sched-run artifacts
- `daily_summary_enabled` (bool), `daily_summary_hour_local` (0-23)

`update_preferences` is merge-update with atomic validation. Default-valued keys pruned. `update_user` (admin) doesn't touch this column.

**First-turn preamble**: prepends `[Context about this user: …]` block. **Kept OUT of system prompt** — earlier iteration injecting greeting into `_build_system_prompt` broke warm-pool KV-prefix matching for every authenticated turn (reverted). Stripped before wire by `_ALLOWED_MSG_KEYS`.

**Refinement**: `/v1/refine` extended with `purpose: "profile_field"`. Polish-don't-rewrite (preserves first-person voice). `mistral-vibe-cli-fast` validated; cliproxyapi/gemini-2.5-flash silently echoes input (known model bug).

**My Schedules tab**: non-admins see only own schedules; legacy schedules (empty `user_id`) stay admin-only. `_schedule_owner_check(name)` gates mutating ops.

## User Profile (Memory from chat history)

Auto-maintained per-user context profile, mirrored to MemPalace.

- **Storage**: `agents/main/user_profiles/<uid>.md` (filesystem source of truth, gitignored) + `<uid>.history/<ISO>.md` (capped 30, KEPT on Reset). MemPalace mirror: `wing=user__<uid>, room=user_profile`, one drawer per `## section`, purge-then-add.
- **Schema**: fixed sections — Work context / Personal context / Top of mind / Recent months / Earlier context / Long-term background. `_PROFILE_SYSTEM_PROMPT` rules: never invent, third-person, match user's language, edit in place, demote stale Top→Recent, no timestamps, 2-6 sentences/section.
- **Daemon** (`user-profile`): polls every 30 min. Per-user gate: `daily_summary_enabled` + local hour matches + 23h cooldown via `auth.db.user_daily_summary`.
- **Worker** (`_profile_run_synchronous`): 100 most-recently-active chats from last 90 days, `_run_delegate` with `_PROFILE_SYSTEM_PROMPT`, model picked by `_profile_pick_model` (refinement → cheapest haiku → cheapest enabled → default). GDPR auto-fallback to local on findings. Atomic write via tmp + `os.replace`.
- **Preamble injection**: round 0 reads `<uid>.md` (4KB cap), prepends `[Auto-maintained user profile: …]` on first user message. Stripped by `_ALLOWED_MSG_KEYS`.
- **KV-cache invariant**: `_build_system_prompt` stays user-agnostic. Per-user content lives ONLY in first-user-message preamble.

## MemPalace (Direct Integration)

Memory powered by MemPalace, imported as Python package — no MCP, no subprocess.

**Vocabulary**: **Drawer** = atomic verbatim chunk (~800 chars, content-hash id). **Closet** = index layer (topic|entities|→drawer_ids) boosting search ranking. **Room** = topic bucket (`chat`, `chat_summary`, `chat_attachment`, `reference`, `general`, `artifacts`, …). **Wing** = namespace. **Hall**/**Tunnel** = graph edges (future).

**Wing scheme** — ID-only:
- `user__<user_id>` — per-user private
- `team__<team_id>` — shared across team members
- `project__<project_id>` — strictly isolated project memory
- Bare names (`brain_code`, …) — shared, anyone reads

`_resolve_session_wing` priority: project → team → user → empty. Anonymous sessions return `""` and skipped by chat-sync.

`mempalace_query`: when `_thread_local.project` set, **force-scopes** to `project__<id>` (refuses if id missing rather than leak). Otherwise defaults to `user__<current_user_id>`. Visibility filter for unspecified-wing searches: drops `project__*` (always private), matches `user__/team__` against caller, treats untyped as shared.

**Chat sync classifier gate**: LLM classifies message pairs before filing. `fact`/`preference`/`decision`/`reference` filed vs `generic`/`refusal`/`chitchat` skipped. Non-streaming, `max_tokens: 20`, fail-open. Per-session 3-state mode: `0=off`, `1=on`, `2=auto`. `save_chat_to_memory` tool lets model explicitly enable on "remember this". Per-turn control via palace-icon menu (memorise/remove × complete/this/above/below) → `memorize_turns`/`purge_turns` accepting `turn_ids` or `{scope, anchor_turn_id}`. Disable-with-purge prompt when toggling on/auto → off and drawers exist.

**Session delete cleanup**: `delete_session` purges drawers + closets where `source_file LIKE session/<sid>%`. **Archive leaves drawers intact** (memory persists).

**Daemon 1 — `mempalace-miner`** (default 1800s): autonomous artifact ingestion. Walks `AGENTS_DIR`, classifies by folder name: `sched-` prefix → scheduled; else chat.
- **Sched folders**: file only output-role files via direct `tool_add_drawer`. Skips intermediates. `source_file=session/sched-<run>#artifact/<name>`. Wing = `<agent_id>_artifacts`. Sched chat content (reasoning, tool calls) deliberately stays out.
- **Chat folders**: gated on parent session `save_to_memory > 0`. Daemon ensures `mempalace.yaml` exists (marker `# managed by brain-agent server.py`).
- **Stale-queue cleanup**: one-shot `_purge_orphan_chroma_queue()` at startup detects HNSW segments missing `max_seq_id` and deletes >24h-old `embeddings_queue` rows.
- **Logging**: plist sets `PYTHONUNBUFFERED=1` so `[mempalace-miner]` reaches log immediately.

**Daemon 2 — `mempalace-chat-sync`** (default 60s): mirrors to wings:
- Chat turns → `room=chat`, `source_file=session/<sid>#turn/<user_msg_id>` (turn anchor = DB id of opening user message)
- Session summaries → `room=chat_summary`, content-hashed
- Attachment metadata (filename/mime/size, NOT bytes) → `room=chat_attachment`
- Allowlisted tool_results (default `exa_search`, `web_fetch`, `read_document`) → `room=reference`

Uses `mempalace.mcp_server.tool_add_drawer` (function, not server). Reads `MEMPALACE_PALACE_PATH` env from `mempalace.palace_path` before import.

**Closet rebuild** per dirty group: `purge_file_closets` + `build_closet_lines` + `upsert_closet_lines`. Without this, chat memories miss closet boost. Gated by `mempalace.chat_sync.build_closets`.

**Cursor**: `chat_mempalace_sync (session_id PK, last_message_id, last_summary_hash, updated_at)` in chats.db.

**Not mined**: attachment bytes, artifact version history (latest is on disk), tool_result outside allowlist.
