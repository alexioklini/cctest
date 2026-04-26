# CLAUDE.md

Guidance for Claude Code when working in this repository. Structured as: what lives where, and the **non-obvious invariants** that aren't derivable by reading code. Factual catalogs (tool list, endpoint list, config fields) live in the code — grep/read it.

## Repository Structure

- `brain.py` — Gateway CLI: start/stop/restart server, launch frontends
- `server.py` — HTTP API server daemon (launchd-managed, port 8420)
- `client.py` — Shared HTTP/SSE client library for frontends
- `claude_cli.py` — Core engine: tools, agents, MCP, scheduler, agentic loop
- `tui.py`, `telegram.py` — Terminal + Telegram frontends
- `web/index.html` — Single-page web UI (light/dark theme)
- `desktop/` — Electron shell (macOS/Windows) providing CORS-free IPC for air-gapped client mode
- `tools.md` — Global tool-usage guide (loaded into system prompt at runtime)
- `config.json` — Providers, server settings, Telegram config (not in git)
- `agents/<name>/` — `soul.md`, `agent.json`, `skills/`, `mcp.json`, plus `chats.db`/`scheduler.db` in `agents/main/`

## Architecture

```
brain.py → server.py (daemon, port 8420)
              ├── claude_cli.py   # engine, native agentic loop, LCM
              ├── /mcp endpoint   # JSON-RPC tools/list + tools/call with hooks
              ├── SQLite          # chats, scheduler, context, costs, traces, audit, auth
              └── MCP clients     # memory via MemPalace (direct, no MCP)
telegram.py runs as an in-process thread. desktop/ is an Electron shell.
```

All chat goes through the native Python agentic loop. No SDK sidecars. All providers are OpenAI-compatible.

## Agentic Loop (native)

- Entry: `send_message_with_fallback()` → `send_message()` → `_handle_openai_response()` (streaming, tool-call aggregation, multi-round loop, usage accounting)
- Middleware pipeline runs *between tool rounds* to keep context lean: `_middleware_cancel_check`, `_middleware_tool_result_budget`, `_middleware_microcompact`, `_middleware_compress_old`, `_middleware_compaction`, `_middleware_pyexec_hint`
- Tool execution goes through `_execute_tool()`: built-in pre → external pre → execute → built-in post → external post → `_after_file_write()` side-effects
- Interactive: `AskUserQuestion` blocks the loop via `_pending_answers[session_id]` + `Event`, unblocked by `POST /v1/chat/answer`
- Partial-response recovery: on cancel/error, streamed text + tool calls are saved via `_rollback_messages()` so the user sees what was produced

**Diminishing-returns guard**: after round 3, if the last 2 completion-token deltas are each < 500, the loop stops (`tools=False` + `tool_loop_stop` SSE). Catches models plateauing without new progress.

**Tool-call dedup** is session-scoped (`_dedup_sid()`, 1h TTL, 100 entries/session) — *not thread-local*, so worker subagents + `ThreadPoolExecutor` batches share one set. 1 dup = error, 2 dups = `TaskCancelled`. `reset_tool_dedup()` runs at turn start. Exempt: `memory_recall`, `memory_shared`, `delegate_task`, `task_status`, `schedule_list`, `schedule_history`. Worker threads must inherit `current_session_id`, `current_agent`, `mcp_manager`, `current_user_id` via `_execute_tool_in_thread`.

## Multi-Provider Routing

`resolve_provider_for_model(model)` in `claude_cli.py` is the **single source of truth** for `{api_key, base_url, provider_name}`. Used by chat, delegate, scheduler, warmup, background LLM calls. Providers are plain OpenAI-compatible entries in `config.json` under `providers` (`api_key` + `base_url`, `type` defaults to `openai`).

**Provider-scoped model IDs**: when multiple providers serve the same model, entries are stored as `provider/model_id` with `base_model_id` for the actual API call. `get_api_model_id(model)` resolves. Historical scoped ids (`OMLX/*`, `Bifrost/*`, `mistral/*`) still route.

**Current providers** (live in `config.json`):
- `omlx` — local Apple-Silicon MLX on `http://localhost:8000/v1`. Reasoning via `chat_template_kwargs.enable_thinking` → `message.reasoning_content`
- `cliproxyapi` — local Gemini proxy on `http://localhost:8317/v1`. Gemini 2.5 with `reasoning_effort` → `reasoning_content`
- `mistral-experimental` / `mistral-vibe` — `https://api.mistral.ai/v1`. Magistral + `mistral-small-2603` emit reasoning as nested content blocks when `reasoning_effort` is set
- `kilo` — cloud OpenAI-compatible gateway

**Why direct (8.5.0)**: Bifrost gateway was retired because its `ChatContentBlock` struct silently dropped nested Mistral `thinking[]` and oMLX `reasoning_content` during re-serialization.

## Web UI (Claude.ai-style)

Sidebar + multi-view layout. Views: welcome, chat, chats, projects, project-detail, notes, artifacts-browse. `navigateTo(view)` toggles `display:none`. Claude.ai design system (Anthropic fonts, warm light theme); CSS custom props (`--bg-*`, `--text-*`, `--accent-*`) drive theming.

- **Tool blocks**: collapsible `div.tool-block` via `toolDescribe(name, args)` mapping to human-readable labels. Args shown as key-value table, not raw JSON. Tool call + result merged into one block. Timestamps on `tool_call`/`tool_result` drive duration badges. Tool calls persisted in assistant `metadata.tools[]`, reconstructed on session restore. `state.showToolCalls` toggle hides/shows (localStorage).
- **Streaming** uses raw socket SSE for unbuffered tokens. `renderStreamingMessage()` updates in place; `renderMessages()` does full re-render.
- **Right panel** (`#right-panel`): tabbed Attachments/References/Files/Artifacts, resizable. Auto-opens References on web tool results; always switches when `artifact_updated` fires. `#toggle-right-panel-btn` in `#page-header-right`; `syncRightPanelToggle()` + `toggleRightPanel()` keep button/state in sync.
- **References panel**: Le Chat-style source cards from `exa_search`/`web_fetch`. `extractReferencesFromToolResult()` parses JSON with regex fallback for truncated results. Previews via `api.microlink.io` screenshots (lazy). Clicking a ref-badge calls `openReferencesPanel(link)` which scrolls + highlights the card (2s outline).
- **Resizable sidebars**: drag handles on right edge of left sidebar, left edge of project panel; widths persisted to localStorage.
- **Stream state is per-agent chat**: `_streamStartTime`, `_streamTimerInterval`, `_streamGen` (generation counter) live on the chat object, not globally — stops stale microtasks from a completed stream killing a newer stream's spinner.

## Chat File Attachments

Routing is dynamic based on model capabilities. All files go to `state._pendingFiles[]` as base64. Unified send path: browser sends all as `body.files` (plus legacy `body.images` for Telegram).

Server per-file routing checks the model's `raw_formats` (MIME pattern list in `KNOWN_MODELS` / `config.json`):
- **Multimodal**: MIME matches + base64 + <20MB → injected as OpenAI `image_url` data URI content block
- **Disk**: otherwise → saved to `/tmp/brain-attachments/{session_id}/`, agent uses `read_document` (requires `documents` tool group)
- **Fallback for images on non-vision models**: `attachments.image_model` in config describes via vision LLM; unconfigured → metadata only

Helpers: `get_model_raw_formats(model)`, `_mime_matches(mime, patterns)` in `claude_cli.py`. Models tab detail panel edits `raw_formats`.

## Artifacts

Files written under `agents/<name>/artifacts/<date>_<session_prefix>/` are auto-promoted to artifacts. Everything else is a regular file. `write_file` with a relative path defaults into the session's artifact folder.

- Each write/edit creates a row in `artifact_versions` (content blob, capped at 5MB)
- SSE: `artifact_updated` (enriched `file_created` with `artifact_id`, `artifact_version`, `artifact_type`)
- Tables: `artifacts(id, session_id, agent_id, name, path, type, role)` + `artifact_versions(content, version, size, action)`
- API: `GET /v1/artifacts?session_id=X`, `GET /v1/artifacts/<id>/content?version=N`, `…/download?version=N`, `GET /v1/artifacts/browse?agent_id=X&limit=N`
- **Panel**: type-aware rendering (code = highlight.js, html = iframe, svg inline, markdown rendered). Artifact cards in chat open the panel, not a modal
- **Browse view**: sidebar nav → full-page grid with type/agent filters; clicking a card opens the source session + panel
- **Role classification (v8.11.0)**: `role` column (`output` default / `intermediate`) set at registration via `_ARTIFACT_INTERMEDIATE_EXTS` — `.py/.sh/.bash/.js/.ts/.rb/.pl/.json/.jsonl/.yaml/.yml/.toml/.ini/.cfg/.csv/.tsv/.log` are classed as working files; everything else (`.md/.html/.pdf/.docx/images/svg`) is an output. Browse grid has an `Outputs only`/`Show working files` filter (outputs-only default); right-side artifact list auto-shows the same toggle when the session has any intermediates. Heuristic only — no agent-level override yet. Back-compat: the migration defaults every pre-existing row to `output` so nothing disappears from the grid silently

## Scheduled Task Runs

Each scheduled task execution gets an immutable `schedule_history` row (`id` = `run_id`) and a synthetic `session_id = sched-<run_id>` that scopes its artifacts + traces.

- **Inline history accordion** on each sched-card (no modal): lazy-loads the last 30 runs via `/v1/scheduler` action `history`; each row has Open (loads `openScheduledArtifact` — read-only pseudo-chat built from traces + run row), Details (`_schedViewRunDetail` stats modal), Delete (`_schedDeleteRun`)
- **Deletion** (v8.11.0): `POST /v1/scheduler` actions `delete_run` (single run: history row + session artifacts + files + empty folder; refuses on `status='running'`) and `clear_history` (every non-running run for a named schedule; schedule definition kept). Backed by `Scheduler.delete_run()` / `Scheduler.delete_history()` and `ChatDB.delete_artifacts_for_session()`
- **Read-only chat view banner** shows schedule name, run #, timestamp, status, duration, tool count, model, and a collapsible task prompt block; `Delete run` button on the banner navigates back to Scheduled view after purge
- **Per-task attachments + working dir** (v8.16.0): two columns on `schedules` — `attachments` (JSON list of `{name,path,mime,size}`) and `working_dir` (absolute path). Files are uploaded once via `POST /v1/schedule/upload` (multipart), stored under `agents/<agent>/scheduled_attachments/<uuid>/<name>`, and **referenced in place on every fire** — no per-run copy (the durable path is on the same filesystem, copying would only churn `/tmp`). `_execute_scheduled` appends the existing chat-style "files saved to disk" notice listing the absolute paths so the agent reads them via `read_document`/`read_file`. `working_dir` overrides the `Current working directory:` line in the system prompt and adds an explicit directive to pass it as `cwd=` to `execute_command`. **`python_exec` stays pinned to the artifact folder by design** — file-write tracking depends on it; the working-dir setting only affects shell calls. **Cleanup**: `Scheduler.remove()` reads `attachments` before DELETE and rmtrees each per-upload uuid folder via `_purge_attachment_paths()`; `Scheduler.update()` diffs old vs new attachment paths and purges orphans (chip-removal in the edit modal frees bytes). The purge helper refuses to touch any path whose components don't include `scheduled_attachments` as a guard. UI: create + edit modals get a multi-file picker (uploads on selection, removable chips) and a read-only working-dir input + Browse… button. Browse opens a stacked folder-picker modal (z-index above the schedule modal) backed by `GET /v1/files/tree?path=X&depth=0`; empty `path` defaults to `$HOME` server-side so the picker has a starting point without leaking `$HOME` via a separate endpoint. `_schedUploadFiles` must send `Authorization: Bearer <token>` from `localStorage('auth-token')` and use `BASE_URL` — bare `fetch()` hit the global `/v1/*` auth gate and 401-ed.
- **Per-task thinking level + caveman mode** (v8.18.0): two more columns — `thinking_level TEXT DEFAULT ''` (empty = inherit model defaults at fire time; `none` / `low` / `medium` / `high` force) and `caveman_chat INTEGER DEFAULT 0` (0..3, response-style compression analogous to the chat composer toggle). `_execute_scheduled` overlays `thinking_level` onto `inference_params` (via `sched_inf["thinking_level"] = …` or `pop` + `thinking=False` on `'none'`) before `_run_delegate`, and appends `CAVEMAN_CHAT_PROMPTS[level]` directly to the system prompt — same suffix the chat composer toggle uses. **`caveman_system` is intentionally NOT exposed per task** — it's a per-model knob tied to KV-prefix stability and would invalidate warmup. **Server-side validation**: `_validate_thinking_level_for_model(model, level)` rejects format-mismatched levels (e.g. `medium` on a `mistral_blocks` model that only accepts `none`/`high`) with a helpful message; called from `Scheduler.add` before INSERT and `Scheduler.update` cross-field after individual validation, using the effective model (incoming patch or existing row). Empty `model` defers validation to runtime so "Default model" schedules still work.

## Format-Aware Thinking-Level UX (v8.18.0)

The thinking dropdown is identical-shaped everywhere it appears (composer toggle, schedule modal, Models tab) so users only see options the chosen model can actually honor.

- **Single source of truth in the web UI**: `_thinkingOptionsForFormat(fmt)` returns the option set for a given `thinking_format`; `_thinkingOptionsForModel(modelId)` looks up the model and dispatches. The mapping:
  - `none` → `(unsupported)` disabled select. The composer button is also disabled (existing behavior).
  - `inline_tags` → Off / On (level isn't dialable; Qwen3-style models think unconditionally per turn — only on/off via `chat_template_kwargs.enable_thinking` matters).
  - `mistral_blocks` → Off / High (Mistral API only accepts `reasoning_effort: none|high`).
  - `reasoning_field` / `openai_opaque` → Off / Low / Medium / High.
- **Schedule modal**: adds an "Inherit from model" entry on top so the schedule can be model-agnostic. `_schedRefreshThinking(modelSelectId, thinkingSelectId, currentValue)` re-renders the level dropdown when the model selector changes; preserves the user's prior choice when still valid, falls back to "Inherit" otherwise. When the schedule is set to "Default model" the option set is the union of all formats with a hint that it's resolved at fire time.
- **Models tab General Settings detail panel**: per-row Thinking Level dropdown is rendered next to the existing Thinking Format selector. The format `<select>` has an `onchange` that re-renders its sibling level via `_mdlRefreshThinkingLevel(formatSelectEl)`. Save path captures `inference.thinking_level` (or omits when unset/disabled); skips when the row's format select is `none` (level select is disabled).
- **Composer thinking button**: `cycleThinkingLevel` uses `_composerLevelsForFormat(fmt)` and only cycles through valid steps for the active model (mistral_blocks cycles Off→High; inline_tags cycles Off→On). `refreshThinkingButton` is **self-correcting**: when called after a model switch, it demotes `state.thinkingLevel` to `'none'` if the saved value isn't in the new format's set, persisting the change to `localStorage`. Every existing call site that already refreshes after a model change (the model dropdown, session restore, startup, PII auto-swap, etc.) naturally enforces the rule — no per-site instrumentation needed. Tooltip shows the cycle the current format supports.
- **`thinking_format` detection**: `_detect_thinking_format(model_id, provider)` is now provider-aware. `cliproxyapi` + `gemini-2.5*` returns `reasoning_field` even when the stored id is bare ("gemini-2.5-flash" with no `cliproxyapi/` prefix); `omlx` + reasoning-capable model substrings (`qwen3`, `deepseek-r1`, `glm-zero`, `magistral`, `thinking`, `gemma-4`/`gemma4`) returns `reasoning_field` even without an `OMLX/` prefix. **Gemma 4 is a reasoning model** (channel-token output, `enable_thinking` chat-template kwarg) and oMLX maps the channel-token thoughts to `delta.reasoning_content` — verified live on `gemma-4-26B-A4B-it-MLX-4bit`. Gemma 3 was non-reasoning so the original detector skipped the family entirely; that gap closed in 8.18.1. `_match_known_model` passes provider through. `init_models_config` does a forward-looking re-detect — when the stored format is the conservative default `'none'` but the provider-aware detector now returns a real format, upgrade in place. Never the other direction (would clobber a deliberate user "off"). Server startup persists the upgraded `models` block when init produces a real change (was first-run-only before), so e.g. `gemini-2.5-flash` and the Gemma 4 trio auto-upgraded from `none` to `reasoning_field`. **`init_models_config` deep-copies `existing_models`** (8.18.1 fix): a shallow copy aliased the per-model cfg dicts back to server.py's pre-init snapshot, so `_models_differ` saw matching values after the in-place upgrade and the persist gate skipped the save. The shipped 8.18.0 gemini-2.5 upgrade only happened to land in `config.json` because of unrelated saves; without the deep copy any future detector addition would silently fail to persist on restart.
- **Streaming thinking block default-collapsed** (8.18.1): the live `renderStreamingMessage` thinking panel previously auto-expanded while reasoning text was arriving and only collapsed once the answer started streaming, which dumped a wall of chain-of-thought into the chat for several seconds on every reasoning turn. Header still shows `Thinking...` → `Thinking` progress; click to peek. The two finalized renderers (`renderThinkingMessage` history path + the inline assistant-message thinking) were already collapsed-by-default via the `.thinking-block.open` CSS rule, so nothing changed there.

## Next-Prompt Suggestions (ghost text)

After each assistant turn, the UI fetches `GET /v1/sessions/<id>/next-prompt` and shows it as dimmed placeholder. Tab/→ accepts, Enter on empty accepts+sends, Esc/typing dismisses.

- Engine: `generate_next_prompt_suggestion(session)` — strips metadata, reuses session model + history, `tools=False`, tiny `max_tokens`
- **Cost reality**: small direct LLM call. Prior "near-free via prompt cache reuse" claim was tied to Anthropic `cache_control` markers — those were removed in v7.2.0 and no OpenAI-wire provider we rely on offers an equivalent caching path. Treat this as a real cost.
- Config in `agent.json` → `next_prompt_suggestions` (`enabled`, `model`, `max_words`). Edit the JSON; no dedicated UI

## Model Management

Models carry `display_name` (editable, default = shortname derived from ID). All UI surfaces show `displayName (provider)` via `modelShortName(mid, withProvider)`.

Per-model config fields (all in `config.json` → `models`, editable in Models tab detail panel):
- `profile` (`speed`|`balanced`|`frugal`|`custom`) — optimization preset, see below
- `max_context`, `max_output`, `inference` (temp/top_p/top_k/max_tokens), provider-specific inference (`reasoning_effort` etc.), `cost_input`/`cost_output`, `raw_formats`, `presets`, `warmup`, `warmup_mode`, `warmup_allow_cloud`, `parallel_tool_calls`, `caveman_system`, `thinking_format`
- `_match_known_model()` seeds defaults from `KNOWN_MODELS` (claude, gemini, qwen, crow, llama, mistral, minimax, devstral)
- Manual add: model ID + provider + display name (for providers without `/models` endpoint)

**Optimization profiles** (`MODEL_PROFILES` in `claude_cli.py`): sparse overlays selecting speed vs token-frugality. Applied lazily via `resolve_model_settings(mid)` — explicit per-model fields still win, so a profile just sets defaults.

- `speed` (auto for local providers): warmup on, `warmup_mode=full`, `deferred_tool_groups=[]` (stable KV prefix beats lean-but-shifting), `compact_threshold=0.85`, generous tool-result limits. Optimises for first-token latency + cache reuse. Extra tokens don't matter on local.
- `balanced` (auto for cloud providers): current shipping defaults. `deferred_tool_groups=["email","documents","code_graph","scheduler"]`, `compact_threshold=0.70`.
- `frugal`: `caveman_system=2`, warmup off, aggressive deferral (adds `nodes`, `git`), `compact_threshold=0.50`, `tool_result_char_limit=15000`, `max_tool_rounds=8`, `include_tools_guide=False`. Only safe on capable cloud models.
- `custom` (default on migration): no overlay, use raw per-model fields. Backward compat.

Auto-picked at model discovery via `_is_local_base_url(provider.base_url)`. Precedence: defaults < profile < raw model fields < agent config < per-request. Resolution is cache-free on every read — editing a profile definition updates every model using it without rewriting `config.json`. Profile changes invalidate the warm-pool KV prefix (included in `_prefix_fields`).

**Thinking model auto-recovery**: when `finish_reason == "length"` and visible output is <25% of completion tokens (thinking ate the budget), `max_tokens` doubles on retry, capped at `max_context`. Logged as `[thinking model: boosting max_tokens X → Y]`.

**Deletion tombstones** (v8.7.0): user deletions persist in `config.json` → `deleted_models: []`. `init_models_config` honors that list on startup AND on every `action: 'sync'`, so a deleted model never silently returns. The only path that clears tombstones is the per-provider **Full Resync** button (`action: 'resync_provider'`) — which drops every model attributed to that provider, clears their tombstones, then re-discovers from `/models`. Manual re-add (or a `save`/`update` carrying the id) also revives the entry. Never wire an automatic clear path; that defeats the whole point.

## Thinking / Reasoning Models

Reasoning output format is not standardized across providers. Each model carries `thinking_format` which tells the engine how to parse the stream. `_detect_thinking_format(model_id)` picks from patterns during discovery and backfills at `init_models_config`.

Formats:
- `none` — no reasoning (default). UI toggle disabled.
- `inline_tags` — `<think>...</think>` inside content. `_InlineThinkingSplitter` is a boundary-safe state machine handling tags spanning SSE chunk boundaries. Used by DeepSeek-R1 distills, GLM-Zero, `*-thinking` variants.
- `reasoning_field` — sibling `delta.reasoning_content`. oMLX (when `enable_thinking`), Gemini 2.5 with `reasoning_effort`, DeepSeek-R1 direct.
- `mistral_blocks` — `[{type:"thinking", thinking:[{type:"text", text:"..."}]}, {type:"text", ...}]`. Streaming deltas carry partial `thinking[].text`. Used by `magistral-*`, `mistral-small-2603/latest` when `reasoning_effort` set.
- `openai_opaque` — hidden; only `usage.completion_tokens_details.reasoning_tokens` exposed. UI shows grey "Thought for N tokens" badge. OpenAI `o1-*`, `o3-*`, `o4-mini`.

**Persistence**: each reasoning round becomes its own `role='thinking'` row with `metadata.tool_round`. Payload filter (`_ALLOWED_MSG_KEYS` / `_INTERNAL_ROLES`) strips `thinking` rows before sending to the provider — UI-only.

**Events**: `thinking_start`/`thinking_delta`/`thinking_done`/`thinking_summary`. Server persists on `thinking_done` (or fallback at turn-end if stream was truncated).

**Reload interleave**: tools live in `metadata.tools[]` (not DB rows) with `tool_round`; the client buckets tools by round and interleaves them with thinking rows loaded from DB, reconstructing original thinking→tool→… chronology.

**Request param**: `inf_params["thinking_level"]` (low/medium/high) in addition to legacy `thinking=true, thinking_budget=N`. `_apply_inference_to_payload` maps to `reasoning_effort` on the wire for `mistral_blocks` (forced "high" — Mistral only accepts none/high), `reasoning_field`, `openai_opaque`. oMLX uses `chat_template_kwargs.enable_thinking`.

**oMLX thinking-default-on gotcha** (v8.7.0): Qwen3 chat templates served via oMLX default `enable_thinking=true` when the kwarg is **absent** — so omitting it leaks reasoning even when the user toggled thinking off. Fix: `_apply_inference_to_payload` always emits `chat_template_kwargs.enable_thinking` (true OR false) on every oMLX request whose model has a non-`none` `thinking_format`. The early-return for empty `params` was removed and the two call sites (`send_message`, `_run_delegate`) now invoke the function unconditionally so the off case still fires. **KV-prefix consequence**: warmup payloads must mirror this exactly or the prefix won't match — `run_model_warmup` now also calls `_apply_inference_to_payload`.

## Caveman Mode (Dual)

Two independent settings that compose:
- **System-level** (`caveman_system` per model, 0–3): compresses the *system prompt itself*. Prepends a meta-instruction + applies `_caveman_compress_text()`. Levels: 1=whitespace/indent, 2=filler phrases + strip markdown headers/bold/rules + drop articles before capitalized words, 3=hedging + strip examples (e.g./i.e. sentences).
- **Chat-level** (`caveman_mode` in sessions DB, 0–3): appends `CAVEMAN_CHAT_PROMPTS` response-style instruction. Composer button cycles 0→1→2→3→0. Persisted to `localStorage('caveman-chat-mode')` + auto-applied on new sessions via `ensureSession()`.

Thread-locals `_thread_local.caveman_system` + `_thread_local.caveman_chat` set in chat worker, cleaned in `finally`. **Cache key** for `_build_system_prompt` includes both — avoids stale prompts.

## Token Optimization

Per-agent `token_config` block in `agent.json`:

```json
{
  "token_config": {
    "tool_groups": ["core", "context", "web", "delegation", "git", "skills", "nodes", "scheduler"],
    "extra_tools": [],
    "include_tools_guide": true,
    "compact_threshold": 0.70,
    "scheduled_task_tools": false,
    "mcp_tool_filter": null,
    "mcp_tool_exclude": null,
    "deferred_tool_groups": ["email", "documents", "code_graph", "scheduler"]
  }
}
```

- `tool_groups`: subset of `{core, context, web, email, documents, delegation, code_graph, git, scheduler, mcp, skills, nodes, memory, code_exec}`. `null` = all
- `include_tools_guide`: inject `tools.md` (~400 tokens)
- `compact_threshold`: override context compaction threshold (default 0.60)
- `mcp_tool_filter` / `mcp_tool_exclude`: fnmatch globs; filter runs first, exclude second
- `deferred_tool_groups`: groups excluded from every request until discovered via `tool_search`. Default set saves ~1,760 tokens/request. System prompt tells the model which groups are deferred.
- System prompt cached per-session (60s TTL) to avoid disk I/O on tool loops
- `_filter_tools()`, `_get_agent_tool_names()` handle filtering
- GUI: **Tokens tab** → Tool Definition Cost card + Measure button (`GET /v1/tools/breakdown?agent=<id>`) → per-group defer checkboxes + per-tool MCP filter rows

## Per-Agent Runtime Limits

Optional `limits` block in `agent.json` overrides global defaults:

```json
"limits": {
  "max_tool_rounds": 15,
  "tool_result_char_limit": 30000,
  "tool_results_total_tokens": 50000,
  "context_safety_ratio": 0.95
}
```

- `max_tool_rounds`: soft cap → `tools=False` on next round. **Hard stop at 1.5× this value** terminates the loop.
- `tool_result_char_limit`: per-result truncation in `_sanitize_tool_result`
- `tool_results_total_tokens`: accumulated tool-result budget before `_compress_old_tool_results` kicks in
- `context_safety_ratio`: pre-flight in `send_message` raises `RuntimeError` if estimated tokens > `max_context * ratio` (default 0.95) — avoids provider 400s
- Resolved via `_get_agent_limits()` + `AGENT_LIMITS_DEFAULTS`

## Per-User Cost Quotas (v8.14.0)

Replaces the old `cost_limits.max_session_cost_usd` machinery (deleted in 8.14.0). Quotas are per-user with role-based defaults and optional per-user overrides; cost data lives in `cost_log.user_id` (column added 8.14.0).

- **Engine**: `QuotaManager` in `claude_cli.py` (singleton `_quota_manager`). 30s config cache. Two axes per user:
  - **Daily** (rolling, resets at UTC midnight)
  - **Cycle** — `monthly` (default, anchor day-of-month, clamped to last day of short months), `weekly` (anchor 0=Mon..6=Sun), or `yearly` (anchor month-of-year)
- **Levels**: `green` < `warn_pct` (70 default) ≤ `yellow` < `block_pct` (100 default) ≤ `red`. Worst axis wins for the overall pill colour.
- **Pre-flight gate**: `send_message` round 0, after the GDPR scan. `is_model_local(model)` always bypasses (local cost = 0). Modes via `quotas.enforce_red`:
  - `warn_only` (default) — pill goes red, requests still allowed, no server-side refusal
  - `force_local` — silently swap to `default_local_fallback_model` (audit `quota_force_local`); raises `QuotaExceededError` if the configured fallback is unusable
  - `hard_block` — raise `QuotaExceededError` pre-LLM (audit `quota_blocked`)
- **Cost logging**: `_log_call_cost` captures `_thread_local.current_user_id` at insert time. Rows with empty `user_id` are pre-quota legacy data — see Backfill below.
- **API**:
  - `GET /v1/quotas/me` — daily + cycle state with reset timestamps and worst-axis level
  - `GET/POST /v1/quotas/config` (admin) — full config
  - `GET /v1/quotas/admin/users` (admin) — every user's daily + cycle + level
  - `GET /v1/quotas/admin/breakdown?user_id=X&days=N` (admin or self) — per-model breakdown for current cycle + daily 30-day series
  - `/v1/costs` and `/v1/costs/daily` extended with optional `?user_id=X` (ownership-gated)
- **UI**:
  - Status-bar **Plan-usage donut** (`#status-quota`) — small SVG arc tinted green/yellow/red by worst-axis level, label shows higher of (daily_pct, cycle_pct), hides when both limits are zero. `QuotaMonitor` polls `/v1/quotas/me` every 30s and refreshes after every turn ends
  - Click opens an anchored popover (`position:fixed`, right-aligned to the pill, two-frame measure-then-position so the bottom edge stays on-screen) with daily + cycle bars, reset countdowns, role + override chips, and a mode-aware footer
  - **Settings → Quotas tab** (admin only): cycle config, warn/block thresholds, enforce-mode dropdown, local fallback model picker (filtered to enabled local models), per-role limits table (admin/poweruser/user × daily_usd/cycle_usd), per-user override prompts, org-wide user list with level chips, per-user **Details** modal showing per-model + 30-day breakdown
- **Config** (`config.json` → `quotas`):
  ```json
  {
    "enabled": true,
    "billing_cycle": "monthly",
    "cycle_start_day": 1,
    "warn_pct": 70,
    "block_pct": 100,
    "enforce_red": "warn_only",
    "default_local_fallback_model": "",
    "limits": {
      "admin":     {"daily_usd": 50, "cycle_usd": 800},
      "poweruser": {"daily_usd": 20, "cycle_usd": 200},
      "user":      {"daily_usd": 5,  "cycle_usd": 50}
    },
    "user_overrides": {"<user_id>": {"daily_usd": 10, "cycle_usd": 100}}
  }
  ```
  Set any limit to `0` to mean "no limit" for that axis. Invalidated on `_quota_manager.save_config()`.
- **Audit trail**: `quota_force_local`, `quota_blocked`, `quota_config_save`. Combined with the cost_log row, every quota refusal is reconstructible.
- **Backfill (one-time, 8.14.0 migration)**: pre-quota `cost_log` rows have `user_id=''`. The migration takes user from `chats.db.sessions.user_id` where the session row still exists; remaining rows (deleted sessions, scheduler synthetic `sched-*`, background non-chat calls — classifier, summariser, next-prompt, warmup) are reassigned to the org admin. A `cost_log_backfill` row in `audit.db` records the bulk reassignment.
- **Stages 2+3 deferred**: per-user GDPR compliance reporting (already auditable via `audit.db` filter on user_id), harmful-prompt analytics.

## GDPR / PII Pre-Submit Scanner

71 regex-based detectors that scan every outgoing chat message + text attachment for personal data **before** it leaves the client, and again server-side before it hits the LLM. Zero external APIs, offline, free.

- **Two mirrored implementations**, must stay in sync:
  - `PIIScanner` in `web/index.html` — runs on composer input (live badge), on loaded chat history (`piiHistoryText`/`piiHistoryHasFindings` with a per-chat cache keyed by message count; invalidated on `openSession` / user-message push / stream done / `newChat`), and on submit (blocking modal when not already on local)
  - `_pii_rules()` + `_pii_scan_text()` + `_pii_scan_bare_identifiers()` in `claude_cli.py` — runs in `send_message` on `_tool_round == 0` only (subsequent rounds replay the same user content), and via `gdpr_pick_model_for_background()` on every non-interactive LLM call site (see below)
- **Three rule tiers, evaluated in order** (first-match-wins via overlap suppression):
  1. **Cloud secrets / API keys** (distinct prefixes, highest signal): AWS, GitHub, Slack, Google, Stripe, OpenAI, Anthropic, Twilio, SendGrid, Mailgun, JWT, Azure Storage / account keys, PEM private keys, basic-auth-in-URL, entropy-gated generic `api_key = "..."` assignments
  2. **National IDs with real checksums**: UK NINO + NHS (mod-11), NL BSN (11-proef), BE national number (mod-97), PL PESEL, PT NIF, SE personnummer (Luhn), DK CPR, NO fødselsnummer (dual mod-11), CH AHV (EAN-13), CZ rodné číslo, RO CNP, HU TAJ, GR AMKA, BG EGN, IE PPS, ES DNI/NIE, IT Codice Fiscale, DE Steuer-ID (context-gated), FR INSEE, AT SVNR, US SSN, BR CPF + CNPJ, CA SIN (Luhn), MX CURP, AR DNI, IN Aadhaar (Verhoeff, context-gated), JP My Number, KR RRN, SG NRIC, TW national ID
  3. **Context-fallback + bare-identifier heuristic**: fire on keyword (`SVNR`, `SSN`, `Steuer-ID`, `Führerschein`, `passport`, `account number`, etc.) + number-shape **regardless of checksum** — catches malformed or made-up identifiers the user is clearly asking about. Plus a whole-text heuristic: when ≥60% of non-empty lines are 9-14-digit ID-shaped, flag remaining lines as "Numeric identifier (unverified)" (the "paste a list of numbers" pattern)

- **Rule-order invariants** (if you touch the list):
  - Context-gated rules with keywords (DE Steuer-ID, NL BSN, HU TAJ) run **before** generic bare-digit rules of the same length so the wider keyword+digits match wins
  - `credit_card` runs **after** all national-ID checksum rules — a 13-digit Luhn-passing RO CNP or KR RRN would otherwise be misclassified as a card
  - `phone` runs **after** national IDs — `XXX-XXX-XXXX`-shaped SIN/NHS/SIN values would otherwise steal phone's slot
  - `credit_card` regex has `(?<![+\d])` so `+CC...` international phone prefixes don't match

- **Overlap suppression**: each successful match records its span; subsequent matches inside already-claimed spans are dropped. **Failed validations do NOT record the span** — an IBAN that fails mod-97 leaves its text available for weaker rules to re-scan, which is why Aadhaar / PESEL / Steuer-ID are context-gated (otherwise they'd false-positive inside invalid IBANs)

- **Modal**: 640px amber-gradient banner with pop-in shield, hero count, per-source pill badges, redacted monospace samples (first 2 + last 2 chars visible, rest `•`-masked), session-scoped "Don't warn again" checkbox, keyboard shortcuts (Esc cancels, Cmd/Ctrl+Enter sends), click-outside dismiss. Inline composer badge is an amber pill with shield SVG and formatted count

- **Server-side main-chat behavior**: when findings present, print `[gdpr] session=... findings=N (...)` and (if `server_log` enabled) append a `pii_detected` row to `audit.db` via `_audit_log.log_action(action_type="pii_detected", tool_name="gdpr_scanner", source="llm_request")`. Hard-block mode (`server_block: true`) raises a `RuntimeError` with a user-visible message pre-LLM, **but only when `is_model_local(model)` is false** — selecting a local model from the UI bypasses the block because the data stays on-prem. Message text nudges the user toward the dropdown

- **Local-model routing for background/worker calls** (v8.10.0): `gdpr_pick_model_for_background(model, texts, purpose)` in `claude_cli.py` is the single decision point for every non-interactive LLM call. Called by `generate_next_prompt_suggestion`, `classify_chat_for_memory`, `_summarise_tool_result` (worker subagents), `_run_delegate` (delegate_task tool + scheduler tasks + agent-to-agent delegation), `_generate_chat_summary`. Flow: scan `texts` → emit `pii_detected` audit row (always, independent of swap) → if model is already local, return unchanged → if `default_local_fallback_model` is configured, enabled, and local, swap and emit `pii_auto_fallback` → else if `server_block=true`, emit `pii_blocked` and raise `GDPRBlockedError` (`RuntimeError` subclass) so the caller refuses cleanly (next-prompt returns None, classifier returns None, summariser returns static summary, delegate returns `"Delegation error: [GDPR block]..."`, chat summary skips) → else warn-only: proceed on the original cloud model
- **Client-side local interlock**: `piiBlockActive(chat)` is true when `server_block=true` AND scanner is enabled AND (draft has PII OR loaded history has PII). Under that state `toggleModelDropdown` filters to `cfg.is_local` only with an amber header, `piiEnsureLocalModel()` auto-swaps the chat to `state.piiLocalFallback` (or the first enabled local), and `sendMessage` refuses-with-toast if the current model still isn't local. Badge has three states: red (no local available), green (routing via local, "auto-selected" on first swap), amber (warn-only). Label distinguishes "Personal data in your message" vs "Personal data earlier in this chat"
- **`is_local` derivation**: `is_model_local(model_id)` in `claude_cli.py` resolves the provider and calls `_is_local_base_url()` (matches `localhost`, `127.0.0.1`, `0.0.0.0`, RFC1918). `GET /v1/models/config` annotates every model entry with `is_local` so the client doesn't duplicate URL parsing; fallback heuristic in `isModelLocal()` for older server builds

- **Config** (`config.json` → `gdpr_scanner`, 30s cache, invalidated on save via `engine._invalidate_gdpr_cache()`):
  ```json
  {
    "enabled": true,
    "server_log": true,
    "server_block": false,
    "default_local_fallback_model": "",
    "categories": {
      "secrets":         {"action": "block"},
      "national_id":     {"action": "warn"},
      "national_id_ctx": {"action": "warn"},
      "financial":       {"action": "warn"},
      "contact":         {"action": "ignore"},
      "network":         {"action": "ignore"},
      "personal":        {"action": "warn"},
      "bare_id":         {"action": "warn"}
    },
    "rule_overrides": {"<rule_id>": "ignore|warn|block"},
    "email_allowlist": ["user@company.com", "@trusted.com"]
  }
  ```
  `default_local_fallback_model` must be an enabled local model id. **Categories (v8.12.0)** group the ~70 rules into 8 semantic buckets; each has an action: `ignore` (rule never runs — no scan, no log, no audit row), `warn` (shows the confirmation modal), `block` (refuses unless current model is local; composer auto-routes to fallback). `rule_overrides` let a specific `rule_id` override its category's action. `block` is downgraded to `warn` when the `server_block` master switch is off (back-compat for pre-8.12 configs). **Email allowlist**: findings from the `email` rule matching an entry in `email_allowlist` (exact address OR `@domain` pattern, case-insensitive, no whitespace) are suppressed entirely. `PII_RULE_CATEGORIES` + `PII_DEFAULT_CATEGORY_ACTIONS` in `claude_cli.py` are the single source of truth, mirrored as `PIIScanner.ruleCategories` / `defaultCategoryActions` in `web/index.html`. Block decisions (main-chat + background) gate on `_pii_worst_action(findings) == "block"` so warn-only findings never raise — only a block-severity category can refuse. `POST /v1/services/server` validates categories (unknown silently dropped), rule_overrides (unknown `rule_id` → 400), and allowlist entries (missing `@` → 400). UI: dedicated **Settings → GDPR** tab with master switches, email allowlist textarea, and collapsible per-category rows showing rule counts + inline action dropdowns + per-rule override dropdowns. Server tab shows a one-line GDPR status chip with Configure → link. `applyGdprConfigToScanner(gs)` is the single client entry point that syncs `PIIScanner.policy` + `state.pii*` from the server response; called from the startup fetch, the Server/GDPR tab refresh, and after every save

- **Suppression state lives in `sessionStorage`**: key `pii-suppress:<session_id_or_"_new">` — cleared on page reload, not on server restart. Intentional: protection resets every browser session

- **What's deliberately not detected** (keep in mind if expanding): personal names (needs NLP, too noisy for regex), physical addresses (format varies per country, needs dictionaries), medical terms / ICD codes (dictionary-based, not regex), passport numbers for all 50+ countries without context (too generic — only context-gated passport rule exists), driver's license per country without context. The Microsoft Purview SIT catalog lists ~300 detectors; we ship the ~70 that can be implemented faithfully from public specs without copying Purview's own patterns

- **Audit trail** covers three events: `pii_detected` (every finding, main chat or background), `pii_auto_fallback` (swap happened, with `args_summary="<cloud_model> -> <local_model>"`), `pii_blocked` (refusal fired, with `source="background"`). Filter in the Audit tab to reconstruct any request's PII decision trail

## Tool Definition Cost Measurement

`GET /v1/tools/breakdown?agent=<id>` returns per-group + per-tool tokens, decomposed into `name_tokens + desc_tokens + schema_tokens + param_count`. `source: "builtin"|"mcp"` (MCP grouped by server via `MCPManager._tool_to_server`). UI flags tools with schema >60% of total as ⚠. Also returns `deferred_builtin_groups`, `deferrable_mcp.tokens_saved_if_deferred`.

## Python Code Execution (python_exec)

Opt-in via `"code_exec"` in `token_config.tool_groups`. Subprocess isolation (`sys.executable`), killed on timeout. **Working dir = artifact session folder** — files written auto-register as artifacts via `_after_file_write()`, and state persists across calls in the same session.

- **Auto-artifact fallback**: stdout >1K chars with no files written → saved as `output.txt` artifact; tool result shows head+tail preview only (token savings)
- Config in `tools_config.json` → `python_exec`: `timeout` (30s), `max_output_chars` (50000), `venv_path`
- Available packages: docx, openpyxl, pptx, reportlab, PIL, csv
- `_middleware_pyexec_hint`: when 3+ consolidatable tool calls (read_file/search_files/list_directory/read_document/write_file/edit_file/write_document/edit_document) in one turn, injects a one-shot hint suggesting python_exec consolidation. Only fires if agent has `code_exec`. Resets per chat request.

## Worker Subagents (`execution.py`)

Heavy tools are routed through a worker wrapper that writes raw output to the artifact store and returns a **compact envelope** with an LLM-generated summary — so the main context window stays small even when the tool produces megabytes.

Routing: `_execute_tool` → `route_tool_execution` → `run_worker_subagent` for tools whose profile has `"heavy": true` (defaults: `exa_search`, `web_fetch`, `gmail_search/inbox/read`, `search_files`, `python_exec`, `execute_command`). Tools marked `"heavy": "auto"` only wrap when output exceeds `auto_threshold_bytes`. Light tools run inline.

- **Phases** (appended to `Worker.flow`, each emits `worker.progress` SSE): `executing tool` → `storing artifact` → `summarising` → `done`
- **Envelope**: `{worker:true, worker_id, summary, sections, artifacts:[...], duration_seconds, state, flow:[...], summariser_usage}`. Raw output lives in `agents/<id>/artifacts/<session_folder>/worker_<tool>_<uuid8>.json` — **never back in the envelope** on subsequent rounds
- **Summariser**: `_summarise_tool_result` calls a cheap LLM (session model or `agent.json.summariser_model`) → short summary + `SECTIONS: [...]` drill-in hints. Its tokens surface via `worker_usage` SSE so status bar turn totals reflect full LLM spend; main `messages[]` history only gets the envelope
- **Flow kinds**: `phase`, `artifact`, `question`/`answer` (`worker_ask_user`), `state` (PAUSED/RESUMED/ABORTED+reason), `error`, `summariser`. Flow is in the envelope → rehydrates losslessly on reload
- **State machine**: `QUEUED → RUNNING → {PAUSED, WAITING_FOR_USER, COMPLETED, FAILED, TIMED_OUT, ABORTED}` with validated transitions
- **Idempotency**: per `(session_id, tool_use_id)` dedup — concurrent retries wait on one worker's event
- **Concurrency cap**: `execution.max_concurrent_workers_per_session` (default 3). Over limit returns an error envelope telling the model to wait or abort
- Config: `config.json` → `execution` (`workers_enabled`, `auto_threshold_bytes`, `worker_timeout_seconds`, `summariser_max_input_chars`, per-tool `profiles`)
- API: `GET /v1/workers` (live + flow), `GET /v1/workers/recent`, `POST /v1/workers/{id}/{answer,abort,pause,resume,send}`
- UI: `renderWorkerFlow(wf)` shared between chat tool blocks + session inspector. Live updates via `worker.started/progress/finished/paused/resumed/aborted/question/answered/worker_usage` into `state.workerFlows[worker_id]`. `worker.question` renders a standalone card that stays visible regardless of the tool-calls toggle

## Session Inspector — Per-Round API Requests

`request_payloads[]` in assistant `metadata` carries one entry per `_tool_round` (populated by `request_payload` SSE emitted before each LLM call). Fields: `tool_round`, `system_prompt` + tokens, `tools_count/tokens/names`, `history` + tokens, `user_message` + tokens, `total_payload_tokens`.

- **Actual API tokens**: the `usage` SSE now carries `tool_round` → chat worker callback attaches real `tokens_in`/`tokens_out` to the matching `request_payloads[i]` (not turn-cumulative `_usage_totals`)
- UI: round 0 auto-opens. Continuation rounds show `+N msgs` delta badge and auto-open History with `NEW` highlighting on new entries. Empty `user_message` sections hidden
- **Turn totals**: `_usage_totals` sums main-round `usage` + worker-side `worker_usage` so status bar reflects full turn spend even though main context only contains main-round payloads

## Parallel Tool Calls

`parallel_tool_calls: true` added to payload when tools present (per-model toggle, default on). `_execute_tools_batch()` partitions tool calls into batches: consecutive **concurrent-safe** tools run in `ThreadPoolExecutor`, unsafe tools run sequentially.

`_CONCURRENT_SAFE_TOOLS`: `read_file`, `list_directory`, `search_files`, `read_document`, `exa_search`, `web_fetch`, `code_graph_query`, `schedule_list`, `schedule_history`, `list_nodes`, `task_status`, `context_search`, `context_detail`, `context_recall`, `git_command` (read-only subcommands only).

## Provider Concurrency Queue (v8.9.0)

Local LLM gateways have very different concurrency semantics. **oMLX supports continuous batching** — multiple in-flight `/chat/completions` are fused into batched forward passes; aggregate throughput goes up but per-request decode and TTFT regress. **CLIProxyAPI serializes internally** — a second concurrent request stalls the first regardless of GPU memory. `LocalProviderQueue` in `claude_cli.py` gates concurrent HTTP calls per provider via a semaphore + strict-FIFO waitlist; capacity is a per-provider tradeoff between aggregate throughput and per-request latency.

- **Opt-in per provider**: `config.json` → `providers.<name>.max_concurrent` (0 = unlimited = no queue, default). Seeded: `omlx=2`, `cliproxyapi=2`, cloud providers stay 0.
- **Tuning oMLX `max_concurrent`** (continuous batching tradeoff): on `gemma-4-26b-a4b-it-4bit` (Apple Silicon), benchmark numbers are: batch=1 → 63 tok/s decode, 2.3s TTFT; batch=2 → 80 tok/s aggregate (40 tok/s/req), 4.3s TTFT; batch=4 → 91 tok/s aggregate (23 tok/s/req), 8.6s TTFT. **`2` is the sweet spot** for multi-user / parallel-tool / warmup-overlap workloads — 27% aggregate throughput gain for ~40% per-request decode slowdown when both slots are full. Going to 4 punishes per-request latency too hard. Solo single-user workloads see no benefit from `>1` (no concurrent requests to batch). Watch GPU RAM headroom — KV cache footprint scales with in-flight sequences. CLIProxyAPI does *not* batch internally, so for it `max_concurrent` is a pure parallelism cap not a throughput knob.
- **Scope — HTTP-only**: slot is held during `urlopen` + SSE stream drain. `_handle_openai_response` calls `release_slot()` immediately after the `for line in response:` loop completes, **before** any tool dispatch or recursive `send_message`. Tool work (worker subagents, `_summarise_tool_result`, `exa_search`, `python_exec`) runs with the slot freed, so other chats can use the gateway in parallel.
- **No re-entrancy needed**: because the slot is always released before nested calls, a worker subagent's `send_message_with_fallback` just queues normally. Earlier thread-local re-entrancy design was tried (2026-04-23) and reverted — HTTP-scoped release is simpler and yields better throughput.
- **Call sites wrapped** (all already use `resolve_provider_for_model`): `send_message` (main chat), `_run_delegate` (scheduler delegates), `run_model_warmup` (so the keeper can't cut in front of a live chat; label=`warmup`), `classify_chat_for_memory`. `generate_next_prompt_suggestion` + `_summarise_tool_result` covered transitively via `send_message_with_fallback`.
- **SSE events**: `queue_wait` (position changes only, not every tick), `queue_acquired` (fires once), `queue_released` (fires once). Web UI shows per-turn inline banner "Waiting in queue on `<provider>` — position N of M · Xs" in the streaming bubble.
- **Status bar pill** (`#status-queue`): always visible when any provider has `max_concurrent > 0`. Shows `N/M` (active/capacity) when idle, `N+W/M` when any ticket is waiting. Click opens a modal listing active + waiting tickets per provider (label, model, session, agent, age). `QueueMonitor` polls `/v1/queue/status` (1s fast / 10s slow).
- **Admin cancel**: `POST /v1/queue/cancel` (admin-only, 403 otherwise; audit-logged as `queue_cancel`). Body: `{ticket_id, reason?}`.
  - Waiting ticket → `_admin_cancelled` flag set; waiting thread raises `TaskCancelled("Queue cancel by admin: <reason>")` on next 200ms poll tick.
  - Running ticket → flag set AND ticket's `cancel_token` fired (same signal as the per-chat Stop button). The SSE loop in `_handle_openai_response` bails on the next incoming chunk.
  - Modal rows render a red Cancel button per ticket only for `state.authUser.role === 'admin'`.
- **API**: `GET /v1/queue/status` returns `{providers: {name: {max_concurrent, active_count, waiting_count, active[], waiting[]}}, any_waiting, any_active}`. Providers with `max_concurrent=0` are omitted; providers with `>0` are always present (zero counts when idle).
- **Cancellation during wait**: ticket is removed from the deque cleanly; semaphore is never acquired. Tested.
- **Timeouts**: chat/delegate default 300s; warmup + classifier use `max(http_timeout*2, 30-60s)` so they don't hold the slot beyond their HTTP deadline.
- **Key invariant**: the queue key is `provider_name` (not `base_url`). If two providers share a base_url, they'd have independent queues — re-evaluate when that happens.

## Warmup & Warm Session Pool

Brain pre-primes local models so first-token latency drops from ~15s → 2–3s. Opt in per model via `warmup: true`. Requires prompt-cache-capable providers (oMLX tested; any runtime that deduplicates KV prefix by exact token match).

- **Engine**: `run_model_warmup(model, mode="full"|"minimal")` in `claude_cli.py` is the single source of truth. Used by the keeper daemon AND by session-level `_trigger_warmup` (server.py). UI dot reads from `_warmup_state`.
- **Modes** (`warmup_mode` per model, default `"full"`):
  - `"full"` — system prompt + all tools + "." user. Primes KV prefix → ~2–3s first response
  - `"minimal"` — 1-token user, no system, no tools. Only loads weights into GPU. First response ~10–15s. Use when GPU RAM is tight and the prefix would evict anyway
- **KV-prefix stability rule** (critical): warmup payload MUST match first-turn payload *byte-for-byte* or the cache misses silently. Four previously-drifting fields are now aligned:
  - System prompt timestamp **rounded to hour precision** (not minutes) in `_build_system_prompt`
  - MCP tools attached via `_thread_local.mcp_manager = _mcp_manager`
  - Tools merged, deduped, sorted by name
  - `stream=True` + `stream_options` passed
- **Keeper** (`_warmup_keeper_loop`): runs every `warmup.interval_seconds` (default 30). Picks idle/cold/failed candidates or ones whose configured mode flipped since last prime. `max_concurrent` per cycle (default 1). `_warmup_wakeup: Event` lets callers kick the loop immediately
- **Starvation fix**: failed primes bump `last_warmup_ts` so oldest-first sort doesn't rerun the same OOM-failing model every cycle
- **Warm session pool** (`WarmSessionPool`): N pre-built Session objects per warmup-flagged model (`warmup.pool_depth`, default 3, clamp 1–10). Bound to `agent=main`, `status=warm_pool` (hidden from sidebar). Fill gated on `_warmup_state[model]["state"] == "warm"`. `claim()` pops FIFO (oldest = warmest) and kicks `try_build()`. Only fires for incoming `POST /v1/sessions` matching `{agent:"main", project:"", status:"", note_context:""}` — anything else changes the system prompt and invalidates the primed prefix
- **Pool invalidation on config save**: `/v1/models/config` tracks `_prefix_fields = (warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile)`. Any change drops pool slots, resets `_warmup_state` to idle, wakes keeper
- **Multi-model tradeoff**: on oMLX with tight GPU RAM, two full-primed models fight for KV space — each prime evicts the other. Either size the host (24GB+ for gemma-26b + e2b) or set one to `minimal`. User picks per model
- **UI**: composer dots (green=warm, amber=warming, red=failed, grey=idle/skipped_cloud). Status bar Pool indicator (`#status-warmpool`) shows `ready/target` + modal with per-model state badge, progress bar, desired/actual mode chips (⟲ if re-prime pending), `last_warmup_ts` age, `last_error`, "Warm now" button
- **API**: `GET /v1/warmup/status` (poll 1s when `any_warming`, 5s otherwise), `POST /v1/warmup/trigger`, `POST /v1/sessions/{id}/warmup` (always `"full"`)
- **Log**: `[warmup-keeper] <model>: warm (<mode>, <ms>ms)`, `[warm-pool] <model>: +1 ready (<sid8>, total N/depth)`

## OpenAI-Only Wire Format (v7.2.0 + v7.3.0)

Brain is OpenAI-wire only. `send_message` and `_run_delegate` always build OpenAI chat/completions and call `_handle_openai_response`. Anthropic/Mistral handlers + Mistral SDK helpers were removed. `api_type` parameter removed from all function signatures (Purge B). Providers return only `{api_key, base_url, provider_name}`.

`TOOL_DEFINITIONS` (Anthropic flat shape) is retained as **internal source of truth** for lookups/display; `TOOL_DEFINITIONS_OPENAI` is derived for the wire.

## Client Execution Mode

For air-gapped servers where the browser has internet: `"execution_mode": "client"` in `config.json`. Agentic loop stays on the server; LLM calls + web-accessing tools (`web_fetch`, `exa_search`) proxy through the browser.

- **LLM**: server emits `proxy_request` SSE → browser calls provider's `/chat/completions` → streams back via `POST /v1/chat/proxy-response` (types: `chunk`, `chunks`, `done`, `error`)
- **Web tools**: server emits `proxy_tool` SSE → browser executes → returns via `POST /v1/chat/proxy-tool-result`
- **Local tools** (file ops, git, shell, code graph) run on the server as normal
- `ProxyChannel` in `claude_cli.py`: thread-safe queue bridging loop ↔ browser
- `_get_execution_mode()` / `_get_client_proxy_tools()`: 30s config cache
- `client_proxy_tools` list (default `web_fetch`, `exa_search`) controls routing
- `GET /v1/config/execution-mode` returns mode + provider creds for browser
- Session inspector: purple `CLIENT` badge on proxied turns. Status bar: purple `CLIENT` pill when active
- Requires CORS-enabled providers (Mistral API, OpenAI API, Bifrost confirmed). Chrome primary
- **Server-local models skip the proxy** (v8.13.0): when the model resolves to a local-gateway provider (`_is_local_base_url` → true), `send_message` bypasses the `proxy_request` emission and calls the gateway directly. There's no point round-tripping through the browser for a model the server can already reach on localhost. Tool proxying (web_fetch, exa_search) is unaffected — those are about internet access, not inference. Side effect: the purple `CLIENT` badge is now per-turn accurate instead of per-session — it fires only when the turn actually went through the browser.

## Client-Hosted Local Inference (v8.13.0)

Desktop clients can declare they serve a model family locally (Electron spawns `llama-server`), and the server transparently transfers matching interactive requests to the client. Queue-free per-user inference, works in both server and client execution modes, stays local on air-gapped deployments. Independent of `execution_mode`: the routing decision is per-request based on the session's capability handshake, not a global toggle. Scheduler tasks, delegates, and background calls never reach this branch because the chat worker is the only caller that populates the `client_capabilities` thread-local.

**Model identity is by `family`, not id.** Server's oMLX `gemma-3-e4b-mlx-4bit` and client's GGUF `gemma-3-4b-it-Q4_K_M` both declare `family="gemma3-e4b"` and route as the same model. Quant / format differ by design — this is "same model for user intent, different physical backend."

- **Server-side manifest** (`config.json` → `client_models`): `[{id, family, gguf_path, sha256, size_bytes, auto_download}]`. `id` is the server-facing model id clients pull by; `family` is the routing key; `gguf_path` is an absolute path on the server's filesystem (admin drops the file, no upload flow). `sha256` + `size_bytes` are recomputed server-side on every save — never trust what the admin passed in.
- **Engine manifest** (`config.json` → `client_engines`): `{darwin-arm64, darwin-x64, win32-x64, linux-x64 → {url, sha256}}`. Admin points URLs at an internal mirror; server refuses to invent defaults (misconfigured air-gap would otherwise silently fetch from the public internet). Published via `GET /v1/client/engines`.
- **API**:
  - `GET /v1/client/models/manifest` (any auth user) — returns `{id, family, sha256, size_bytes, auto_download, download_path}`; gguf_path stripped.
  - `GET /v1/client/models/<id>/weights` — HTTP Range streaming with `X-Model-SHA256` response header for self-verification. Audit-logged on `start == 0` (first chunk / full fetch) so range resumes don't flood. Path-traversal guarded.
  - `POST /v1/client/models` (admin) — CRUD; server computes sha256 + size on save.
  - `GET /v1/client/engines` — per-platform binary URLs + sha256 (no URL defaults).
  - `POST /v1/sessions/<id>/capabilities` — `{enabled, families: [...]}`; unknown families are silently dropped (cross-checked against manifest). Session-scoped, never persisted.
  - `POST /v1/chat/local-inference-usage` — `{session_id, model, tokens_in, tokens_out}`; logged to `costs.db` with `provider="client:<sid8>"` and cost=0 (electricity on the user's laptop is not our problem). Lets dashboards distinguish client- vs server-executed turns without a schema change.
- **Routing path**: `is_model_client_executable(caps, model_id)` in `claude_cli.py` resolves match — returns `(True, family)` iff `caps.enabled` + manifest entry exists for `model_id` + entry's family appears in `caps.families`. Called from `send_message` before the normal provider dispatch (and before the client-exec proxy branch). On match: emits `local_inference_request` SSE via existing `ProxyChannel`, then uses `wait_for_llm_lines` to stream the response back — reuses the full OpenAI-compatible handler path. Fallback policy: **surface error, no server-side retry** — retrying would double latency on failures and mask real problems.
- **Audit**: every transfer writes a `client_inference` row with `args_summary="model=X family=Y"`, `source="chat"`. Every download writes a `client_model_download` row.
- **Desktop (`desktop/local-inference.js`, ~470 LOC)**: fully lazy — no binary bundled, no weights bundled, nothing touched at app launch.
  - Cache: `userData/brain-local-inference/{engine,models}/` keyed by sha256. `state.json` persists `engine_sha` so repeat launches skip re-download.
  - Downloader: resumable `http/https` with streaming sha256 verification, `.partial` sibling files, `Range` requests, restart-from-zero fallback when server refuses a range, `AbortController` support.
  - Engine: fetched once on first use from `/v1/client/engines`; binary `chmod +x` on Unix. **Archive distributions not yet supported** — admin must publish a direct `llama-server` binary URL (or unpack once and host the binary standalone).
  - Spawn: `llama-server` on random free localhost port (`net.createServer.listen(0)`), `waitForEngineReady` polls `/health` with 30s deadline, 10-min idle `SIGTERM` → 3s → `SIGKILL`. Model swap triggers respawn. Single `startupPromise` guards concurrent spawns.
  - Inference: POSTs OpenAI `/v1/chat/completions` with `stream=true` to the local server; forwards raw SSE lines to renderer via `local-inference-chunk` IPC events. Cancel via `activeRequests` map + `req.destroy`.
  - `app.on('before-quit')` kills the child — no orphaned processes.
- **Renderer (`LocalInference` module in `web/index.html`)**: FIFO queue via `Promise` chain (`max_concurrent=1`, matches llama.cpp single-GPU reality). `ensureEngineAndModel()` gates first use. Capability handshake fires on session open, reopen, and after settings toggle save (so changes take effect without re-creating sessions). `handleRequest()` streams llama-server SSE straight to `/v1/chat/proxy-response`.
- **UI**: two new General Settings tabs. **Client Models** (admin) manages the manifest with add/delete form + per-row badges (family, size, sha, auto-download) + read-only engine manifest view showing per-platform URLs. **Local Inference** (per-installation preference) has master toggle + per-family checkboxes + "Download now" prefetch button with live progress bar driven by `onLocalInferenceProgress` callback. Composer shows `local` chip (accent colour) next to model selector when the current model will route client-side; updates on every model switch via `refreshLocalInferenceChip()`.
- **Auth posture**: manifest reads + weight downloads open to any authenticated user (desktop users must be able to pull what the admin blessed). CRUD admin-only. Capabilities + usage scoped to session owner or admin.
- **Known limitations**:
  - Engine archives (zip/tar of llama.cpp releases) not supported — direct binary URL only.
  - Usage reporting lane (`/v1/chat/local-inference-usage`) is currently placeholder since llama-server's streaming protocol doesn't cleanly expose end-of-turn counts. The server's OpenAI response handler ingests the `usage` SSE chunk from the llama-server stream transparently through the proxy channel, so cost tracking already works — the explicit lane is redundant for now. Worth revisiting if we want separate "client-run" cost dashboards.
  - No renderer-side cancel button; the session's existing Stop button aborts the SSE stream from the server side and propagates cleanly through `proxy-response`.

## Desktop App (Electron)

Shell loading the web UI from Brain server and providing CORS-free network via Node IPC. Required for client execution mode on fully air-gapped servers.

- `desktop/main.js`: `BrowserWindow` loads `serverUrl` (default `http://localhost:8420`). IPC handlers `web-fetch`, `exa-search`, `proxy-fetch-stream` using Node `http`/`https` (no CORS)
- `desktop/preload.js`: `contextBridge.exposeInMainWorld('electronAPI', …)` exposes `webFetch`, `exaSearch`, `proxyFetchStream` + stream listeners
- `ClientProxy._execWebFetch/_execExaSearch/_proxyLLM` check `window.electronAPI` first; fall back to browser `fetch()` otherwise
- `_proxyLLMElectron` uses `ipcRenderer.send('proxy-fetch-stream', …)` with `onStreamChunk`/`End`/`Error` relaying chunks to `/v1/chat/proxy-response`
- `nodeFetchWithRedirects()` follows 301/302/303/307/308 up to 5 hops
- `--server=http://host:port` CLI arg. Build: `npm run build:{mac,win,all}`. Run: `cd desktop && npm start`
- `desktop/local-inference.js` (v8.13.0): registers `localInference.*` IPC handlers — lazy llama-server download + spawn + streaming. See the Client-Hosted Local Inference section above for the full lifecycle.

## Agent Teams

Hierarchical delegation model:
- **Team head**: agent with a `team` field in `agent.json` (`{name, description, avatar, members}`)
- **Team members**: agents listed in a head's `team.members`
- **Standalone**: not in any team (excluding main)
- **main**: global orchestrator, never has `team`

**Scoping**:
- `main` → team heads + standalone agents (not members directly)
- Team heads → their members
- Members → peers in same team + their head

API: `GET /v1/teams`, `POST /v1/teams` (create/update/dissolve/move).

## Agent Directory Layout

```
agents/<name>/
  soul.md         # Personality, role, instructions (injected into system prompt)
  agent.json      # {description, display_name, model, avatar, paused, team?, token_config?, limits?, hooks?}
  tools.md        # Optional per-agent tool guide
  mcp.json        # MCP server connections
  gmail.json      # Gmail credentials (not in git)
  skills/         # SKILL.md per installed skill
agents/main/
  chats.db, scheduler.db, context.db, costs.db, traces.db, audit.db, auth.db
```

## Tools

**Source of truth**: `TOOL_DEFINITIONS` in `claude_cli.py` (flat Anthropic shape) — grep/read for the current list. Organized into groups: core (file ops), documents, code_graph, web, email, delegation, git, scheduler, mcp, skills, nodes, context, memory, code_exec.

**Key constraints**:
- `execute_command` runs with no TTY, no stdin, `TERM=dumb` — interactive commands time out. Banned commands listed in `tools.md`.
- Memory is **MemPalace, direct in-process** (see below) — not an MCP server. Tool: `mempalace_query`, `save_chat_to_memory`.

## Server API

Runs on port 8420 (configurable). **Source of truth**: grep `@app.route` / `self.path` dispatch in `server.py`. Key high-value endpoints:
- `POST /v1/chat` — SSE streaming with keepalive
- `POST /v1/chat/answer` — deliver answer to `AskUserQuestion`
- `GET /v1/sessions/<id>/next-prompt` — ghost-text suggestion
- `POST /v1/sessions` — auto-resolves provider from model
- `POST /v1/skills/browse` — ClawHub skill search
- `GET|POST /v1/models/config` — model routing config
- `GET /v1/mempalace/{stats,activity,session-turns}` — palace overview, live activity pulse, memorised-turn set
- `POST /v1/restart` — re-exec the server
- Workers, warmup, artifacts, projects, teams, mcp, costs, tools breakdown — see section above and `server.py`

## Deployment

- Server: launchd daemon (`com.brain-agent.server.plist`)
- Telegram: in-process thread (no separate daemon)
- Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev` (tunnel `itrmp` on 192.168.4.65)

## Concurrency & Thread Safety

Server handles concurrent chat requests, scheduled tasks, delegations, background threads. Non-negotiable invariants:

- **`Session.lock`**: all session field mutations (messages, model, status, streaming, sdk_session_id, summary) must be under `session.lock`
- **`SessionManager.get()`**: uses `_LOADING_SENTINEL` + `threading.Event` to prevent duplicate Session objects for the same `session_id`. Use `peek()` for cache-only reads (no DB load)
- **Thread-local agent context**: every request/background thread **must** set `_thread_local.current_agent` and `_thread_local.mcp_manager`. Never fall back to globals — concurrent requests will bleed
- **Thread-local session context**: `_thread_local.current_session_id` must be set before compaction so context tools can scope correctly
- **`_thread_local.current_user_id`**: propagated in chat workers + delegate tasks — drives MemPalace per-user isolation
- **MCPManager**: `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot
- **Tool dedup**: `reset_tool_dedup()` at start of each chat request
- **Background threads** (`_generate_chat_summary`, scheduler, workflow engine, TaskRunner): set + clean thread-local context in try/finally
- **LLM JSON parsing**: `_extract_json_from_llm()` uses `json.JSONDecoder.raw_decode()` — handles nested objects, markdown fences, surrounding text. Don't hand-roll regex
- **Fallback search**: file reads capped at 32KB to prevent OOM on large files
- **Interactive answers**: atomic under `_pending_answers_lock`; stale queries evicted via `_evict_stale_queries()` (5min TTL)
- **SQLite**: connections via `threading.local()` pools (`_db_conn`, `_sched_conn`, `_cost_conn`, `_context_conn`, `_traces_conn`, `_audit_conn`, `_code_graph_conn`, `_auth_conn`) — **not** dict-keyed-by-ident (that leaks FDs under `ThreadingMixIn`). All ChatDB methods wrapped with `@_db_safe`
- **SSE keepalive**: comments every 5s to prevent browser timeout during tool execution
- **Client proxy SSE**: line buffering carries incomplete lines across TCP chunks — don't drop partial lines

## Key Invariants (hidden, non-obvious)

Things that aren't visible from reading the code but will bite if broken:

- `augmented_messages` strips metadata fields (only `role` + `content` sent to API) — prevents 400s
- Thinking blocks must be preserved in conversation history when the provider requires signed blocks in the tool loop (Anthropic-style)
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- Partial response preservation: `_rollback_messages()` on cancel/error reverts intermediate tool-loop messages *and* saves streamed text + tools to chat history
- **Scheduled tasks**: configurable timeout (default 5 min) via watchdog thread. Scheduler executes due tasks in *parallel* threads, not sequentially. `_run_delegate` uses thread-local `max_tool_rounds` override — no global mutation
- Provider fallback ordering: same provider first, then capabilities, then priority
- Sidebar session list polls after stream end until async LLM summary appears (2s, 30s max) — without this, chat titles never refresh
- Chat summaries generated via Haiku after first response
- Multipart upload: manual boundary parser (Python 3.13+ removed `cgi`) — preserves original filename
- Three-layer hooks: tool pre/post (external subprocess), `after_file_write` (centralized), LLM-level (built-in middleware). External hook: timeout 5s, fail-open on crash, exit 1 = block (pre) or error (post), exit 2 = skip chain. Hook runners cached per agent, invalidated on config save. `allowed_tools` restriction in workflows is enforced (was dead code — don't let it regress)

## Lossless Context Manager

`ContextManager` in `claude_cli.py` with SQLite DAG in `context.db`. Replaces flat compaction. Three-level escalation: leaf summaries → condensation → fallback truncation. Assembly: summaries (highest depth first) + fresh tail (default 16 messages) within token budget. Legacy `_compact_conversation` remains as fallback when disabled.

- Config: `GET/POST /v1/context/config`, `GET /v1/context/stats?session_id=X`
- Context tools: `context_search`, `context_detail`, `context_recall` (drill-back into compacted history)
- Context-fill indicator in footer + manual compact button + LCM badge
- Compaction sends `compacting` / `compacted` SSE for spinner feedback

## Code Structure Graph

`CodeGraph` with Tree-sitter AST parsing, SQLite in `code-graph.db`. 14 languages: Python, JS, TS, TSX, Go, Rust, Java, C, C++, C#, Ruby, Kotlin, Swift, PHP.

- Qualified names: `{file_path}::{ClassName.method}`
- Edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY
- Tools: `code_graph_build` (parse dir), `code_graph_query` (8 query types), `code_graph_impact` (BFS blast-radius via NetworkX), `code_graph_enhance` (LLM summaries + layer classification + guided tour)
- Incremental builds: SHA-256 hash skip, re-parse changed + dependent files. Triggered from `_after_file_write()` via `_maybe_update_code_graph(path)`
- **Enhance**: node summaries (one-line LLM descriptions, batched by file, Haiku), architecture layers (api/service/data/ui/util/test via path+name patterns), guided tour (dependency-ordered with layer grouping)

## Projects (Claude.ai-style)

`ProjectManager` CRUD, `instructions` field in `project.json` injected into system prompt, file upload via multipart to `IngestManager`.

- Project-scoped conversations: `session.project`, `list_sessions(project=X)`, `state.currentProject`
- **Project ID** (v8.18.2): `id` field in `project.json` is a uuid4 hex[:12] assigned on first read and persisted. Used as the MemPalace wing key — renaming the project doesn't strand its drawers, and two same-named projects under different agents never collide. Backfilled lazily; `create_project` mints one upfront.
- Archive: `status: "archived"` in `project.json`, files preserved
- Delete: soft to `.trash/`, recoverable
- **Notes**: `NoteManager` CRUD. AI editing uses `write_file`/`edit_file` (not `EDIT_NOTE` tags). Auto-reload on filesystem changes. Note-AI sessions use `status: note_chat`, hidden from project chat list, persistent per note via localStorage
- Project panel auto-refresh: 5s polling detects filesystem changes from any source

### Project Input Folders + Sync (v8.18.2)

Each project can specify on-disk folders that are mined into its private MemPalace wing on a 30-minute cycle. Combined with manual attachments (`ingested/`), this is what fills the project's memory store.

- **Schema** (`project.json`): `input_folders: [{path, recursive, added_at}]`, `input_folders_last_scan`, `sync_status: {state, last_run_started, last_run_finished, last_files_filed, total_indexed, last_folders_seen, last_error, items: {<key>: {kind, id, state, drawers_filed, error, ...}}}`. `items` keyed `attachment:<src_hash>` or `folder:<abs_path>`.
- **Endpoints** (manage-gated except `sync-status` which is read-gated): `GET/POST /v1/agents/<id>/projects/<name>/input-folders`, `DELETE .../input-folders/<idx>`, `POST .../sync-now`, `GET .../sync-status`. `_handle_project_sync_status` layers `_project_sync_live` (in-flight state) on top of the persisted `sync_status` so the UI sees state changes mid-cycle.
- **Path-traversal guard** in `_project_input_folder_validate`: refuses paths inside `agents/`, `/etc`, `/var`, `/usr`, `/bin`, `/sbin`, `/System`, `/Library/Keychains`. Realpath-resolved before comparison.
- **Daemon** (`mempalace-project-sync` thread, server.py): polls every `mempalace.project_sync.interval_seconds` (default 1800). On every tick: enumerates all `(agent, project)` pairs; processes any in `_project_sync_requests` first (drained via `_project_sync_wakeup`); per project resolves `wing = project__<id>`, ensures `mempalace.yaml` matches the expected wing (rewrites on drift), mines `ingested/` then each input folder via `mp_miner.mine(wing_override=wing)`. Per-folder cap (`max_files_per_folder`, default 5000) refuses runaway home-dir picks at the top level. **Authoritative drawer counts** via `_count_wing_drawers_by_source(wing, source_prefix)` — Chroma metadata `where={"wing": wing}` plus in-Python `source_file.startswith` — and `_count_wing_drawers_total(wing)`. Persists `total_indexed` (cumulative, survives dedup-only re-runs) plus per-item `drawers_filed`. Without this the miner's "Drawers filed: 0" reply on every cycle after the first would clobber the persisted count back to zero.
- **Wing scheme is ID-only** (v8.18.2): `project__<project_id>` (no agent suffix, no project name). See the MemPalace section for the full scheme.
- **Startup wipe**: at thread start the daemon drops every drawer in any `project__*` wing AND clears every project's persisted `sync_status`. Designed for the dev-phase reset the user requested when this feature shipped — the daemon then rebuilds from disk on its first cycle. Idempotent — a second call is a no-op once nothing matches.
- **System prompt** (`_build_system_prompt` claude_cli.py): when `_thread_local.project` is set, prepends a PROJECT MEMORY block listing total indexed drawer count + attachment count + input folder count, with an explicit directive to call `mempalace_query` BEFORE answering project-knowledge questions, plus a PROJECT INPUT FOLDERS block listing every absolute path with a worked example showing how to join the absolute root with `source_file` (relative path, as stored by `mp_miner`) before calling `read_file`/`read_document`. Without this the model would see `source_file=screen.py` and try to read it against the server's cwd.
- **UI** (`web/index.html`): project header gets a `Memory: …` chip — pulses blue when syncing, green when idle, red on error; click triggers `projectSyncNow()`. Right panel grows an "Input folders" section (next to Files) with `+ Add` (opens a stacked filesystem-browser modal `_pifLoadFolder` reusing `/v1/files/tree`, with recursive checkbox), per-folder rows showing path + flag + status pill + remove ×. Files section gets a status pill per attachment. Pills repaint every 5s from `/sync-status` polling via `paintProjectItemPills()` walking `data-pif-pill` nodes — preserves DOM identity (no flicker, no list re-render). Pill colours: indexed (green), syncing (blue, pulsing), pending (grey), error (red).

## Empty-Session Cleanup (v8.18.2)

Sessions are now created lazily on the first send instead of pre-emptively on every model switch / `newChat()` / PII auto-swap. The pre-create logic was originally added to give local models a head-start (the session-create endpoint kicks `_trigger_warmup`), but it left empty rows in `chats.db` whenever the user walked away — those showed up as "Untitled" duplicates in the project chat list. Three changes:

- **Client** (`web/index.html`): the three pre-emptive `ensureSession()` call sites — `newChat()`, the model dropdown's empty-chat branch, and the PII auto-swap path — were dropped. First-token latency stays covered by the warm-pool keeper (for `agent=main, project=''` chats) and the per-model warmup keeper (which doesn't need a session).
- **Server** (`list_sessions`): SQL `WHERE` clause now hides any session with 0 messages older than 60 seconds. Fresh-but-empty sessions (the one being typed into right now) still show.
- **Server startup**: one-shot purge in `main()` deletes empty sessions older than 5 minutes. Idempotent, runs on every start. Cleared 551 historical orphans on the install where the bug was found.

## Cost Tracking & Rate Limiting

- **`CostTracker`** logs every LLM call to `costs.db` (tokens, model, provider, estimated cost). Rates from `_cost_rates` defaults + per-model `cost_input`/`cost_output`
- **`RateLimiter`**: sliding-window per agent (requests/min, tokens/hr, cost/day) from `rate_limits` in `agent.json`
- API: `GET /v1/costs` (agent, hours params), `GET /v1/costs/daily` (agent, days params)

## Remote Nodes

`list_nodes` tool + `GET /v1/nodes` expose remote nodes. `node.py` supports `--install` (launchd plist), `--uninstall`, `--status`. Plist: `~/Library/LaunchAgents/com.brain-agent.node.{name}.plist`, logs to `~/.brain-agent/node-{name}.log`. Quick `GET /v1/nodes` check before long-poll for instant "Connected" feedback. Tool blocks show a purple `node` pill when `node` param is present.

## Per-User Account Settings (v8.17.0)

Personal state separated from the global admin settings. Sidebar bottom-left: clicking the username/avatar opens **Account Settings** (`openUserSettings()`); the chevron beside the theme toggle opens the dropdown (Sign out + admin shortcuts). The dropdown's "Settings" entry was removed for non-admins; admins still see "General settings" as a separate item there.

**Modal tabs**: Profile · Memory · My Schedules · Security.

**Schema** — new `users.preferences` JSON column on `auth.db` (idempotent ALTER, validated keys via `PREFERENCE_DEFAULTS` + `_coerce_pref(key, value)` in `auth.py`):

- `greeting_name` (≤64 chars) — what the agent calls the user
- `job_description` (≤500 chars) — one sentence, surfaced in the first-turn preamble
- `communication_preferences` (≤4000 chars) — per-user soul.md equivalent (tone, style, formatting); surfaced in the first-turn preamble. Refinable via the inline AI button.
- `memory_chats_default` (0|1|2|null) — overrides the server-wide classifier `default_mode` for new chat sessions when set
- `memory_sched_default` (0|1|null) — gate the miner from filing this user's sched-run artifacts when explicitly 0; null/1 keeps default behavior
- `daily_summary_enabled` (bool) — runs the user-profile maintainer once per day
- `daily_summary_hour_local` (0-23) — local-hour anchor

`update_preferences` is merge-update with atomic validation (invalid value → 400, no partial write). Default-valued keys are pruned out of the stored JSON to keep it small. `_user_dict` returns `preferences: {…}` filled with defaults; admin-only `update_user` does NOT touch this column.

**Endpoints** (all self-only or admin):

- `GET /v1/auth/me` — returns user incl. `preferences`
- `POST /v1/auth/profile` — display_name + email only (role/disabled stay admin)
- `POST /v1/auth/preferences` — merge-update preferences
- `POST /v1/auth/password` — unchanged
- `GET/POST /v1/auth/profile-doc`, `POST .../update-now`, `POST .../reset` — see User Profile section

**First-turn preamble** (`claude_cli.send_message` round 0): on the first user message of a session, prepends a `[Context about this user: …]` block carrying greeting + job + comm-prefs (only non-empty lines). Stripped before the wire by `_ALLOWED_MSG_KEYS` filter. Kept OUT of the system prompt so the warm-pool KV-prefix stays user-agnostic across all users — an earlier iteration injected the greeting into `_build_system_prompt` and broke prefix matching for every authenticated turn (reverted).

**Per-user inline AI refinement**: `_us_textarea` helper accepts `{refinable: true, fieldLabel}` to render an inline "Refine with AI" button. `/v1/refine` extended with `purpose: "profile_field"` + optional `field_label` — uses a polish-don't-rewrite system prompt (preserves first-person voice, line breaks, no chat-history context for privacy). After refining the button flips to a one-click Undo. Default refine model lives in `tools_config.json` → `refinement.model`; `mistral-vibe-cli-fast` is the validated choice (cliproxyapi/gemini-2.5-flash silently echoes input verbatim regardless of prompt — known model bug, affects all refine purposes equally).

**My Schedules tab**: `GET /v1/schedule` is now scoped — non-admins see only schedules with their `user_id`; admins see everything. Legacy schedules (empty `user_id`) stay admin-only by design (no silent grant on migration). New `_schedule_owner_check(name)` helper gates pause/resume/delete/run_now/edit/history/delete_run/clear_history; `purge_orphan_history` is admin-only. Schema: new `schedules.user_id TEXT DEFAULT ''` column + `idx_sched_user` index.

**Welcome greeting** (`#welcome-greeting-text`): `refreshWelcomeGreeting()` is auth-aware — reads `state.authUser.preferences.greeting_name → display_name → username`, builds `Good morning, Alex` or anonymous `Good morning` when no auth. Re-runs from `renderUserMenu()` so login + profile saves refresh without a page reload.

## User Profile (Memory from chat history) (v8.17.0)

Auto-maintained per-user context profile, mirrored to MemPalace. The Claude.ai "Gedächtnis aus Chat-Verlauf generieren" equivalent. Replaces the v8.14.0 daily-summary activity log (which produced low-value bullet inventories of titles + run counts).

**Storage**: filesystem is the source of truth.

- File: `agents/main/user_profiles/<uid>.md` (gitignored)
- History: `agents/main/user_profiles/<uid>.history/<ISO-timestamp>.md` — capped at 30 entries; KEPT on Reset by design so users can recover from a hasty rebuild
- MemPalace mirror: `wing=user__<uid>, room=user_profile` (post-8.18.2 ID-only scheme; was `<uid>--main` before), one drawer per `## section` with `source_file=user/<uid>#profile/<slug>`. Mirror is purge-then-add via `_purge_user_profile_drawers` so renamed/removed sections don't linger.

**Schema** — fixed-order Markdown sections, model fills each one or writes `_(none)_`:

```
## Work context
## Personal context
## Top of mind
## Recent months
## Earlier context
## Long-term background
```

Hard rules in `_PROFILE_SYSTEM_PROMPT`: never invent, third-person voice, match the user's predominant language (German chats → German profile), edit existing profile in place rather than rewrite, demote stale "Top of mind" → "Recent months" when no fresh evidence appears, no timestamps, no "as of <date>" markers. Each section 2-6 sentences max.

**Daemon** (`user-profile` thread, replaces `daily-summary`): polls every 30 min. Per-user gate: `daily_summary_enabled` ON + local-hour matches `daily_summary_hour_local` + 23h cooldown via `auth.db.user_daily_summary` cursor table (`last_run_ts`, `last_status`, `last_drawer_id`/`last_profile_path`).

**Per-user worker** (`_profile_run_synchronous`, module level so HTTP + daemon share):

1. `_gather_user_chat_samples` pulls 100 most-recently-active chats from the last 90 days. Per chat: title + first user message + last assistant message, 250 chars each. Total input capped at 12K chars.
2. `_user_profile_run_llm` calls `engine._run_delegate` with `_PROFILE_SYSTEM_PROMPT`. Model resolution via `_profile_pick_model` (refinement → cheapest haiku → cheapest enabled → server default). GDPR auto-fallback to local on PII findings via `gdpr_pick_model_for_background`.
3. `_write_user_profile_atomic` snapshots prior to history dir, writes via tmp + `os.replace`, then `_mirror_user_profile_to_mempalace`.

**Endpoints**:

- `GET /v1/auth/profile-doc` — content + cursor + `enabled` + `hour_local` + `bytes`
- `POST /v1/auth/profile-doc` — manual edit (32KB cap, content must be string)
- `POST /v1/auth/profile-doc/update-now` — synchronous regen for the requesting user; runs in the request thread (~5-60s depending on model). Returns `{result: {status, path, bytes, samples}, cursor}`
- `POST /v1/auth/profile-doc/reset` — delete file + drawers, reset cursor; history dir KEPT

**UI** — Account Settings → Memory tab gains:

- Toggle "Maintain user profile from chat history" + hour picker
- 380px tall editable Markdown textarea (monospace) below
- Buttons: Update now / Reset / Save profile
- Status line: last-run timestamp + bytes + `no_activity` / `filed` / `error:<reason>` state

**Preamble injection**: `claude_cli.send_message` round 0 reads `<uid>.md` (capped 4KB), prepends as a separate `[Auto-maintained user profile (from chat history; treat as background context, not as ground truth for the current request): …]` block on the first user message. Distinct block from the user-set greeting/job/comm-prefs preamble so the model can tell user-set guidance apart from inferred long-form context. Sentinel `_greeting_injected` keeps it idempotent across retries; `_ALLOWED_MSG_KEYS` filter strips it before the wire.

**Startup purge**: legacy `user_daily_summary` drawers dropped from every wing on every server start (idempotent).

**Module-level helpers** (server.py, all callable from HTTP handlers AND the daemon):

- `_user_profile_dir / _path / _history_dir / _read / _write_atomic / _delete`
- `_split_profile_sections` (parses ## headings)
- `_purge_drawers_by_room_and_source(wing, room, source_prefix="")` — note: MemPalace's `tool_list_drawers` summary doesn't include `source_file`, so the `source_prefix` arg is informational; rooms we own (`user_profile`, `user_daily_summary`) are exclusively ours and we purge by (wing, room)
- `_mirror_user_profile_to_mempalace`, `_purge_user_profile_drawers`
- `_PROFILE_SYSTEM_PROMPT`, `_profile_pick_model`, `_gather_user_chat_samples`, `_user_profile_run_llm`, `_profile_run_synchronous`

**KV-cache invariant**: `_build_system_prompt` stays user-agnostic. The earlier iteration that injected greeting into the system prompt was reverted because it broke warm-pool KV-prefix matching for every authenticated turn. Per-user content lives only in the first-user-message preamble, which costs nothing across users.

## MemPalace (Direct Integration)

Memory powered by **MemPalace** imported directly as a Python package — no MCP, no subprocess, no manual `mempalace mine`. One built-in tool queries the palace; two background daemons keep it fresh.

**Vocabulary** (MemPalace's own):
- **Drawer** — atomic verbatim chunk (~800 chars), deterministic content-hash id
- **Closet** — auto-built index layer; packed `topic|entities|→drawer_ids` pointers that boost search ranking
- **Room** — topic bucket (`chat`, `chat_summary`, `chat_attachment`, `reference`, `document`, `artifacts`, …)
- **Wing** — namespace. ID-only scheme as of v8.18.2: `user__<user_id>` / `team__<team_id>` / `project__<project_id>`. Plus bare names for shared content (e.g. `brain_code`).
- **Hall** / **Tunnel** — read-only graph edges (intra-wing / cross-wing; future: automatic tunneling)

**Wing scheme** (v8.18.2 — ID-only, no agent suffix, no name strings):
- `user__<user_id>` — per-user private memory
- `team__<team_id>` — shared across team members
- `project__<project_id>` — strictly isolated project memory (input folders, attachments, prior project chats)
- Bare names (`brain_code`, …) — shared, anyone can read
- **Why ID-only**: agent suffix added complexity without isolation value (a project IS the boundary, mixing `--main` was redundant; same for user wings — agents share their owner's memory by design). Project ids are uuid4 hex[:12] generated on first read of `project.json`, so renaming the project doesn't strand its drawers and same-named projects under different agents never collide. Old scheme `user_id--agent_id` / `team_id--agent_id` / `project__<name>--<agent_id>` is gone.
- **`_resolve_session_wing`** (server.py): priority is project → team → user → empty. Anonymous sessions return `""` and are skipped by chat-sync (no longer pollute the global namespace).
- **`mempalace_query`** (claude_cli.py): when `_thread_local.project` is set, force-scopes to `project__<id>` (refuses if id missing rather than leaking). Otherwise auto-defaults to `user__<current_user_id>`. The visibility filter for unspecified-wing searches drops every `project__*` result (project wings are always private), matches `user__/team__` against the caller's identity, treats untyped wings as shared.
- **One-shot startup wipe**: at `mempalace-project-sync` thread start, every drawer in any `project__*` wing is dropped and every project's `sync_status` is cleared. Daemon rebuilds from disk on its first cycle. Idempotent.
- `save_session` uses `ON CONFLICT` to preserve `user_id` when not explicitly provided
- Future: automatic tunneling for cross-user/team/project sharing

**Chat sync classifier gate** (v7.7.0):
- LLM content gate classifies message pairs before filing. Categories: `fact`, `preference`, `decision`, `reference` (filed) vs `generic`, `refusal`, `chitchat` (skipped)
- `classify_chat_for_memory()` in `claude_cli.py`: non-streaming, `max_tokens: 20`, fail-open
- Per-session memory mode (3-state): `0=off`, `1=on` (save all), `2=auto` (classifier decides). Default from `classifier.default_mode`. Restored on reopen: `/v1/sessions/<id>/messages` returns `save_to_memory`, `openSession()` rehydrates `chat.memoryMode`
- `save_chat_to_memory` tool lets the model explicitly enable saving when the user says "remember this"
- **Per-turn control**: each assistant message has a palace-icon menu with 8 actions — memorise/remove × (complete chat / this response / all above / all below). Dispatches via session-manage actions `memorize_turns` / `purge_turns` accepting `turn_ids: [user_msg_id, …]` or `{scope, anchor_turn_id}`. Items auto-grey when inapplicable. Helpers: `_memorize_mempalace_turns()`, `_purge_mempalace_turns()` in `server.py`. Client cache: `state.memorizedTurns[sessionId]` (Set), refreshed by `refreshMemorizedTurns()` on open + after each action
- **Disable-with-purge prompt**: toggling on/auto → off when drawers exist asks whether to also delete them (`purge_memory` action). Cancel keeps drawers, stops filing new ones
- Config: `mempalace.chat_sync.classifier` (`enabled`, `model`, `min_turns`, `default_mode`, `categories_to_file`)
- API: `GET/POST /v1/mempalace/classifier`
- UI: composer palace icon pulses blue on retrieve, green on store — driven by `/v1/mempalace/activity` + `MempalaceActivityMonitor`. Tracker: `engine.mempalace_activity` with `store_begin/end` wrapping `tool_add_drawer` in chat-sync; `retrieve_begin/end` wrapping `search_memories` in `tool_mempalace_query`

**Session delete cleanup**:
- `delete_session` purges drawers + closets whose `source_file` starts with `session/<sid>` via `_purge_mempalace_session()` (background thread). Also cleans `chat_mempalace_sync` cursor row
- `delete_all` runs the same purge per session
- **Archive** leaves drawers intact (memory persists, session just hidden)

**Query tool** — `mempalace_query` (claude_cli.py):
- In the `memory` tool group (in `DEFAULT_TOOL_GROUPS`)
- Lazy-imports `mempalace.searcher.search_memories`
- Params: `query` (required), `wing`, `room`, `n_results`
- Auto-scopes wing to current user; hybrid BM25+vector with closet ranking boost; returns normalized drawers with `similarity`, `matched_via`, `source_file`, text capped at 2KB
- Reads `config.json` → `mempalace` via `_load_mempalace_config()` (10s cache)
- Adds venv site-packages via `_ensure_mempalace_importable()` (idempotent)

**Daemon 1 — `mempalace-miner`** (server.py startup): runs every `mempalace.mine.interval_seconds` (default 1800s). Fully autonomous artifact ingestion (v8.15.0); the legacy `mempalace.mine.sources[]` flat-config path was retired because it required hand-written `mempalace.yaml` files per source.

- **Discovery**: walks `AGENTS_DIR` and yields every `agents/<id>/artifacts/<date>_<sid_prefix>/` folder. Folder name split decides classification: `sid_prefix` starting with `sched-` → scheduled-task folder; everything else → chat-originated folder.
- **Sched folders**: file only output-role files via direct `tool_add_drawer` calls (no yaml needed). Extension-based classification skips `_ARTIFACT_INTERMEDIATE_EXTS` (py/sh/js/ts/rb/pl/json/jsonl/yaml/yml/toml/ini/cfg/csv/tsv/log). `source_file=session/sched-<run>#artifact/<name>`. Wing = `<agent_id>_artifacts`. Sched **chat content** (reasoning, tool calls) deliberately stays out — there are no `sched-*` rows in `messages` for chat-sync to mirror, by design.
- **Chat folders**: gated on the parent session's `save_to_memory > 0`. When ON, the daemon ensures a `mempalace.yaml` exists in that folder (auto-written with the marker `# managed by brain-agent server.py`; never a manual step) and runs `mp_miner.mine()` over the folder. When OFF, the folder is skipped silently — drawers don't leak past the per-chat toggle.
- **Stale-queue cleanup**: one-shot `_purge_orphan_chroma_queue()` at startup detects HNSW segments missing a `max_seq_id` bootstrap (= Chroma's compactor never knew where to start) and deletes their >24h-old `embeddings_queue` rows. Without this, queue rows pinned to bootstrap-less segments accumulate indefinitely (323 stale closet entries from an April 2026 incident were cleared on the v8.15.0 first run).
- **ChatDB helpers**: `session_memory_modes()` returns `{sid: save_to_memory}` in one query (cycle-scoped, no per-folder DB hit). `session_id_for_prefix()` resolves 8-char folder prefixes to full session ids; sched ids and full-length ids are returned as-is to avoid pointless LIKE lookups.
- **Logging gotcha**: `launchctl` block-buffers daemon stdout when redirected to a file. The plist now sets `PYTHONUNBUFFERED=1` so `[mempalace-miner]` prints reach `server.log` immediately. Without this, daemon errors are invisible for hours/days — that's how the old miner's "No mempalace.yaml found" failures stayed undetected.
- **Not yet implemented**: recursive folder walk for sched (only top-level files in each sched folder are mined; nested subdirectories like `2026-04-23_sched-75/artifacts/` are skipped). Revisit if it bites.

**Daemon 2 — `mempalace-chat-sync`** (server.py startup): runs every `mempalace.chat_sync.interval_seconds` (default 60s). Polls `chats.db` via `ChatDB.mempalace_sessions_needing_sync()` (joins `sessions` → `chat_mempalace_sync` cursor table) and mirrors new content:

- **Chat turns** → `room=chat`, `source_file=session/<sid>#turn/<user_msg_id>`. Turn anchor is the DB id of the user message that opened the turn; all drawers from that turn share the `#turn/<id>` prefix so per-turn purge/memorise targets one turn. Legacy drawers without the suffix still resolve via session-wide purge
- **Session summaries** → `room=chat_summary`, `source_file=session/<sid>#summary`, content-hashed to avoid re-ingest
- **Attachment metadata** (filename/mime/size, not bytes) → `room=chat_attachment`, `source_file=session/<sid>#turn/<id>#attach/<mid>/<filename>`
- **Allowlisted tool_result references** (default: `exa_search`, `web_fetch`, `read_document`) → `room=reference`, `source_file=session/<sid>#turn/<id>#tool/<tname>/<mid>/<idx>`

Uses `mempalace.mcp_server.tool_add_drawer` (the *function*, not the server) for direct in-process Chroma upserts. Reads `MEMPALACE_PALACE_PATH` env var set from `mempalace.palace_path` before import.

**Closet rebuild** per dirty group: after drawer writes, groups by `(wing, room, source_file)` and calls `purge_file_closets` + `build_closet_lines` + `upsert_closet_lines`. Without this, chat memories rank as second-class (drawers search fine but miss the closet boost). Gated by `mempalace.chat_sync.build_closets`.

**Cursor table** (`chat_mempalace_sync` in `chats.db`):

```sql
CREATE TABLE chat_mempalace_sync (
  session_id TEXT PRIMARY KEY,
  last_message_id INTEGER NOT NULL DEFAULT 0,
  last_summary_hash TEXT DEFAULT '',
  updated_at REAL DEFAULT (strftime('%s','now'))
)
```

**Config** (`config.json` → `mempalace`):

```json
{
  "enabled": true,
  "palace_path": "/Users/alexander/.mempalace/brain",
  "venv_site_packages": "/Users/alexander/.mempalace/venv/lib/python3.14/site-packages",
  "mine": { "enabled": true, "interval_seconds": 1800, "sources": [...], "respect_gitignore": true },
  "chat_sync": {
    "enabled": true, "interval_seconds": 60,
    "room": "chat",
    "include_roles": ["user", "assistant"],
    "include_tool_results": ["exa_search", "web_fetch", "read_document"],
    "include_session_summary": true, "attachment_metadata_drawer": true,
    "max_chars_per_message": 8000,
    "build_closets": true, "closet_content_head_chars": 5000
  }
}
```

**Not mined (v1)**: attachment *bytes* (ephemeral, binary), artifact version history (latest is mined via file miner since it's on disk), `tool_result` for tools outside the allowlist (shell output, file reads, git diffs).
