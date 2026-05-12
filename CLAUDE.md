# CLAUDE.md

# CLAUDE.md — 12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 — Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 — Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 — Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.


Guidance for Claude Code in this repo. **Non-obvious invariants only** — what's not derivable from the code. Factual catalogs (full tool list, endpoint list, every config field) live in the code; grep/read it.


## Repository Structure

- `launcher.py` — Gateway CLI (start/stop/restart, launch frontends)
- `server.py` — HTTP API daemon (launchd-managed, port 8420)
- `client.py` — Shared HTTP/SSE client library
- `brain.py` — Core engine: tools, agents, MCP, scheduler, agentic loop
- `engine/` — Extracted engine modules (loop, provider, models, scheduler, tasks, tools, …) — see `engine/CLAUDE.md`
- `handlers/` — HTTP handler modules extracted from server.py — see `handlers/CLAUDE.md`
- `server_lib/` — DB, auth, sessions, notifications, profile helpers
- `tui.py`, `telegram.py` — Terminal + Telegram frontends
- `web/index.html` — Single-page web UI (`web/js/` split into api/chat/files/settings/… modules)
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

Entry point in `engine/loop.py`. Full invariants in `engine/CLAUDE.md`. Summary:

- Entry: `send_message_with_fallback` → `send_message` → `_handle_openai_response`
- Middleware between rounds: `_middleware_cancel_check`, `_tool_result_budget`, `_microcompact`, `_compress_old`, `_compaction`, `_pyexec_hint`
- Tool exec: built-in pre → external pre → execute → built-in post → external post → `_after_file_write`
- `AskUserQuestion` blocks via `_pending_answers[session_id]` + `Event`; unblocked by `POST /v1/chat/answer`

## Resumable Streaming (decoupled from HTTP connection)

The agentic-loop **worker thread is not tied to any HTTP connection**. `_handle_chat` opens a `LiveStream` (`server.py`) on `session.live_stream` before spawning the worker; the worker emits **every** SSE event into it. A `LiveStream` is an ordered replay log + a set of subscriber queues — `emit()` appends to the log AND fans out to current subscribers; `attach()` returns `(queue, replay_snapshot, already_done)` under the same lock (no event lost / no dup across the attach boundary).

- **Originating `POST /v1/chat`** connection is just one subscriber: after `t.start()` it calls `_stream_live_to_client(live, worker_thread=t)` — replays the (usually empty) snapshot, then drains its queue until terminal `done`/`error` or worker death.
- **`GET /v1/chat/stream?session_id=X`** (handler in `handlers/chat.py`) re-attaches: replays the buffer from turn start, then follows live events until terminal. Emits a single `idle` event if no turn is running (chat idle, or the turn finished between the client's `GET /messages` and this call). **Any number of tabs may attach concurrently.** Client disconnect here NEVER cancels the worker — only `POST /v1/chat/cancel` (`session.cancel_token.cancel()`) does.
- **Incremental persistence**: `sessions.streaming_text` / `streaming_meta` columns hold the in-flight assistant reply, written by `event_callback` on `text_delta` (throttled ~0.4s), cleared in the worker's `finally`. `GET /v1/sessions/<id>/messages` returns `streaming: true` + `streaming_text` while a turn is live. Read only when `_streaming` is True → always fresh within a turn (a stale value after a restart-mid-stream is never surfaced because the reloaded `Session._streaming` is False).
- **Worker `finally` invariants**: emit `error` if `not live.done` (covers a worker that died without a terminal event), then `session._streaming = False`, then `session.live_stream = None` (order matters — when `live_stream` is None, `_streaming` is already False, so `GET /chat/stream`'s `idle` path can't loop), then clear `streaming_text`.
- **Client** (`web/js/`): `buildStreamCallbacks(chat, isActive)` builds the SSE callback map, shared by `API.streamChat` (originating send) and `API.attachStream` (reconnect). `openSession()` re-attaches when `GET /messages` reports `streaming: true`; on reconnect it **drops trailing `thinking` DB rows** (the live replay re-emits them via `thinking_done`) and does **not** pre-seed `streamingText` from `streaming_text` (replay rebuilds it fully — pre-seeding would double it). `API.abortStreamAttach()` on leaving a chat (harmless to the worker).
- `send_message` mutates the in-memory `messages` list only — intermediate tool messages are NOT persisted mid-turn (only the user msg, `thinking` rows from `event_callback`, and the final assistant msg reach the DB). `_rollback_messages`' DB-delete arm is a defensive no-op in the common case.

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

## Data View

Sidebar entry only — the `#data-view` container is currently an empty placeholder (nav case in `web/js/nav.js`). The Data Workbench feature (DuckDB-per-session, `data_viz` tools, anonymisation, file scan, chart builder) was removed; the menu entry stays so the view can be re-populated later.

## Worker Subagents

Full invariants in `engine/CLAUDE.md`. Key gotcha: `_summarise_tool_result` returns **3 values** (summary, sections, usage) — callers must unpack 3, not 2.

## Guided Prompt Execution

Per-model opt-in (`guided_execution: true` + optional `guided_execution_granularity` `coarse`/`fine` in model config). Decomposes the user message into ≤5 (coarse) / ≤12 (fine) sequential subtasks; the last subtask is a synthesis task whose result IS the final answer. `run_guided_execution()` in brain.py.

- **Single decision point**: `_should_guide(model, tools)` → granularity or `None`. Bails when the model has no `guided_execution` flag, when `tools` is falsy (transform callers — vision-describe, memory-extract, code-graph summaries, `ask_llm` — stay single-shot), or when `_thread_local._in_guided_execution` is set (re-entrancy guard — `run_guided_execution` calls `_run_delegate` per subtask).
- **Applies to ALL LLM paths, not just chat**: the gate lives **inside `_run_delegate`** (after the GDPR fallback, before payload build), so scheduled tasks, `delegate_task`, agent-to-agent delegation, etc. behave the same as interactive chat. Interactive chat (`handlers/chat.py`, which goes through `send_message`, not `_run_delegate`) is gated separately at its own call site but uses the same `_should_guide`. Workflows' only LLM entry (`tool_ask_llm`) passes `tools=False`, so it's auto-excluded — decomposition inside a workflow node would conflict with the `.flow` DAG (intentional).
- **`task_system_prompt` param**: when a non-interactive caller (the scheduler) passes its `mode="scheduled"` system prompt, `run_guided_execution` uses it verbatim for each subtask instead of rebuilding the interactive prompt via `_build_system_prompt()`. Chat passes `None` → interactive prompt (unchanged behavior).
- **No-SSE is fine**: `event_callback` is fully optional throughout — scheduled tasks forward their `on_event` (it ignores the new `guided_task_*` event types harmlessly); other callers pass nothing.
- **Decomposer safety valve**: returns `[]` for single-step requests → `run_guided_execution` returns `('', False)` → caller falls through to the normal single LLM call. So even callers whose prompts shouldn't decompose (profile-gen, skill-gen JSON tasks) are safe.

## Provider Concurrency Queue

`LocalProviderQueue` in `engine/provider.py`. Key numbers: `omlx=2` (continuous batching sweet spot), `cliproxyapi=2` (serialized, no batching), cloud=0 (unlimited). Queue key is `provider_name`, not `base_url`.

## Warmup & Warm Session Pool

Full invariants in `engine/CLAUDE.md`. Key rule: warmup payload must match first-turn payload byte-for-byte — hour-rounded timestamp, same tools, same `stream_options`. `claim()` only fires for bare `{agent:main, project:'', status:'', note_context:''}` sessions.

## Desktop App (Electron)

Shell loading web UI + CORS-free Node IPC. `--server=http://host:port` CLI arg. Build: `npm run build:{mac,win,all}`.

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
- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id`. Never fall back to globals — concurrent requests bleed.
- **SQLite**: connections via `threading.local()` pools — **not** dict-keyed-by-ident (leaks FDs under `ThreadingMixIn`). All ChatDB methods wrapped with `@_db_safe`.
- **Client proxy SSE**: line buffering carries incomplete lines across TCP chunks.

## Key Invariants

- `augmented_messages` strips metadata fields (only `role`+`content` to API) — prevents 400s
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- `_rollback_messages()` on cancel/error reverts intermediate tool-loop messages AND saves streamed text + tools
- Provider fallback ordering: same provider first, then capabilities, then priority
- Sidebar list polls after stream end until async LLM summary arrives (2s, 30s max)

## Lossless Context Manager

`ContextManager` with SQLite DAG in `context.db`. Three-level escalation: leaf summaries → condensation → fallback truncation. Assembly: summaries (highest depth first) + fresh tail (default 16 messages) within token budget. Legacy `_compact_conversation` is fallback when disabled. Tools: `context_search`, `context_detail`, `context_recall`. SSE: `compacting`/`compacted`.

## Code Structure Graph

Tree-sitter AST parsing, SQLite in `code-graph.db`. 14 langs. Qualified names `{file_path}::{ClassName.method}`. Edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY. Incremental: SHA-256 hash skip; `_after_file_write` triggers `_maybe_update_code_graph(path)`.

## Projects

`ProjectManager` CRUD; `instructions` field in `project.json` injected into system prompt; multipart file upload to `IngestManager`.

- **Project ID**: `id` field in `project.json` is uuid4 hex[:12], assigned on first read. **MemPalace wing key** — renaming doesn't strand drawers, same-named projects under different agents don't collide. `create_project` mints upfront; backfilled lazily for legacy.
- Archive: `status: "archived"` (files preserved). Delete: soft to `.trash/`.
- **Notes**: AI editing uses `write_file`/`edit_file` (not tag-based). Note-AI sessions use `status: note_chat`, hidden from project chat list.

### Project Mode: `research_mode` (v8.31.0)

`project.json.research_mode` (bool) gates the strict retrieval/refusal regime independently of the `instructions` field. Per-session `sessions.research_mode_override` (sticky NULL/0/1) layers on top — set from the composer button or session settings; null = use project default.

Effective mode = `session.research_mode_override if not None else project.research_mode`. Resolution lives in `_build_system_prompt` (sets `_thread_local.research_mode_override` upstream so `handlers/chat.py` reads the same value when gating the citation validator + re-round).

**Research mode ON** (Q&A / policy-reproduction / compliance projects):
- Strict `PROJECT MEMORY` block — mandatory 3-step flow (`mempalace_query` → `read_document(path=read_path)` → answer); REFUSE-on-error.
- KG decision rule (`mempalace_kg_search` first if research_mode AND `kg.enabled`).
- `DEFAULT_PROJECT_INSTRUCTIONS` discipline block injected DIRECTLY by Brain (REFUSAL / PRECISION / CITATION / QUERY).
- Server-side citation validator + synchronous re-round on threshold violation (>30% uncited or ≥2 unverified quotes), gated by `mempalace.citation_reround.enabled`.

**Research mode OFF** (codegen / drafting / build-with-context projects):
- Soft `PROJECT MEMORY` block — "memory is available, use `mempalace_query` when relevant", no forced flow, no refuse-on-error.
- KG hint block skipped (model can still discover the tools from definitions).
- `DEFAULT_PROJECT_INSTRUCTIONS` NOT injected.
- Citation validator + re-round skipped entirely.

**Owner `instructions` field is purely additive** in both modes — appended verbatim after the mode-specific blocks. Never used as a fallback for the disciplines (that was the v8.23 behavior; replaced because it conflated owner intent with Brain behavior). Editor lost the "Load default" button + helper text.

**Migration for legacy projects**: `_project_research_mode(cfg)` infers default when `research_mode` field is absent — empty `instructions` → True (preserves v8.23 behavior); non-empty `instructions` → False (owner already overrode the disciplines). Owners flip the per-project default in the project settings panel.

**Composer button** (`btn-research-mode`, hidden in non-project chats): two-state cycle — project default ↔ override-opposite-of-default. Clicking installs `not project_default` as `session.research_mode_override`; clicking again clears (back to default). Sticky across turns of the same session, mirrors `save_to_memory` semantics.

Brain mechanics (3-step flow text body when ON, BINARY DOCUMENTS block, `read_path` vs `read_path_original`) STAY in system prompt regardless of mode — infrastructure facts.

`DEFAULT_PROJECT_INSTRUCTIONS` constant kept as the source of the discipline text; injected only when research_mode is on. Endpoint `/v1/projects/default-instructions` removed.

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
