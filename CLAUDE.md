# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Repository Structure

- **`brain.py`** — Gateway CLI: start/stop/restart server, launch frontends
- **`server.py`** — HTTP API server daemon (always-running, launchd managed)
- **`client.py`** — Shared HTTP/SSE client library for frontends
- **`claude_cli.py`** — Core engine: tools, agents, MCP, scheduler, Gmail
- **`tui.py`** — Terminal frontend (Rich + prompt_toolkit)
- **`telegram.py`** — Telegram bot frontend
- **`web/index.html`** — Web UI (single-page app, light/dark theme)
- **`tools.md`** — Global tool usage guide (loaded into system prompt at runtime)
- **`config.json`** — Provider config, server settings, Telegram config (not in git)
- **`agents/`** — Per-agent directories with soul.md, agent.json, skills, mcp

## Architecture

```
brain.py (gateway)
  ├── server.py (daemon on port 8420, launchd managed)
  │   ├── claude_cli.py (engine: 30+ tools, agents, scheduler,
  │   │                  native agentic loop, Lossless Context Manager)
  │   ├── /mcp endpoint (MCP JSON-RPC: tools/list, tools/call with hooks)
  │   ├── SQLite: chats.db, scheduler.db, context.db, costs.db
  │   └── MCP server connections (memory via MemPalace MCP server)
  ├── telegram.py (Telegram client, in-process thread)
  └── web/index.html (browser client — Mission Control cockpit)
```

All chat goes through the native Python agentic loop in `claude_cli.py`.
No SDK sidecars. All providers are OpenAI-compatible (Bifrost, Kilo).

### Web UI (Claude.ai-style)

The web UI uses a sidebar + multi-view layout inspired by Claude.ai:

- **Sidebar** (`#sidebar`): collapsible left panel with agent selector, nav (New Chat, Search, Chats, Projects, Customize), recent sessions
- **Welcome view** (`#welcome-view`): greeting + composer for new conversations
- **Chat view** (`#chat-view`): message history + streaming composer with tool blocks
- **Chats view** (`#chats-view`): searchable session list with All/Archived tabs
- **Projects view** (`#projects-view`): Claude.ai-style project list with card grid, search, Your Projects/Archived tabs, sort dropdown
- **Project detail view** (`#project-detail-view`): back nav, project heading, chat composer, conversation list, right panel (Instructions + Files)
- **Settings/Config modals** (`.modal-overlay`): standard overlays that stack on top

Key patterns:
- Claude.ai design system: Anthropic Sans/Serif/Mono fonts, warm light theme, dark theme toggle
- CSS custom properties (`--bg-*`, `--text-*`, `--accent-*`) for consistent theming across light/dark
- `navigateTo(view)` switches between views by toggling `display:none`
- Tool call blocks: collapsible `div.tool-block` with human-readable descriptions (`toolDescribe()`), args table, merged tool_call+tool_result, timing badge
- `toolDescribe(name, args)` maps 30+ tool names to readable descriptions (e.g., `exa_search` → `Searching the web for "query"`)
- `renderToolArgsTable()` displays args as key-value table instead of raw JSON
- Tool call + result merged into single block: args table + "Response" section when expanded, timing in header
- Spinning gear icon while tool is running, green checkmark when complete
- Timestamps (`_ts`) on tool_call/tool_result messages for duration calculation
- Tool calls persisted in assistant message metadata (`metadata.tools`), reconstructed on session restore via `openSession()`
- Tool display toggle (`state.showToolCalls`) hides/shows tool blocks, persisted to localStorage
- References panel: Le Chat-style sources panel for web references from `exa_search`/`web_fetch` tool results
- Reference badges: favicon + title chips at bottom of assistant messages, click opens sources panel
- Sources panel (`#references-panel`): resizable right panel with card grid, webpage screenshot previews via Microlink API
- `extractReferencesFromToolResult()` parses JSON (with regex fallback for truncated results), extracts title/link/domain
- `collectChatReferences()` aggregates all references per session; `getReferencesForMessage(idx)` scopes to preceding tools
- Reference cards: click opens URL in new tab; preview uses `api.microlink.io` screenshot thumbnails with lazy loading
- Agent quick-switch buttons below composer on welcome view
- Streaming uses raw socket SSE for unbuffered token-by-token display
- `renderStreamingMessage()` updates in-place during streaming; `renderMessages()` for full re-render
- Artifact panel: resizable right panel (`#artifact-panel`) for viewing generated files with type-aware rendering
- References panel: resizable right panel (`#references-panel`) for web source cards with screenshot previews

### Chat File Attachments

Users can attach files in the chat composer. Routing is dynamic based on model capabilities.

- **Web UI**: all files go to `state._pendingFiles[]` as base64 (images get `.preview` for thumbnails)
- **Unified send path**: browser sends all files as `body.files`; `body.images` accepted for backward compat (Telegram)
- **Server routing**: checks model's `raw_formats` (MIME patterns) to decide per-file:
  - **Multimodal**: MIME matches `raw_formats` + base64 + <20MB → injected as content block (LLM sees raw data)
  - **Disk**: otherwise → saved to `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- **Multimodal format**: OpenAI `image_url` data URIs
- **`raw_formats`**: per-model MIME pattern list in `KNOWN_MODELS` and `config.json` models section
  - Vision models: `["image/*"]`, PDF-capable: `["image/*", "application/pdf"]`, text-only: `[]`
  - Editable in Models tab detail panel, saved via `POST /v1/models/config`
  - `get_model_raw_formats(model)` and `_mime_matches(mime, patterns)` helpers in `claude_cli.py`
- **Fallback for images**: when model lacks vision, `attachment_image_model` describes via vision LLM; if unconfigured, returns metadata only
- **Image model**: configurable vision model (`config.json` → `attachments.image_model`), selectable in Settings → Server → Attachments
- **Settings hint**: warning shown when default model has no `image/*` in `raw_formats` and no `attachment_image_model` configured
- **Server config**: `GET /v1/services` returns `attachment_image_model`; `POST /v1/services/server` accepts `attachment_image_model`
- **Requirement**: `documents` tool group must be in agent's `token_config.tool_groups` for `read_document` to be available

### Artifacts

Files generated during chat are treated as artifacts when written to `agents/<name>/artifacts/`.

- **Location-based promotion**: any file under `artifacts/` = artifact; everything else = regular file
- **Session-scoped folders**: `agents/<name>/artifacts/<date>_<session_prefix>/`
- **Content snapshots**: each write/edit creates a version in SQLite `artifact_versions` table (content blob, capped at 5MB)
- **Default write path**: `write_file` with relative path defaults to agent's artifacts session folder
- **SSE event**: `artifact_updated` (enriched `file_created` with `artifact_id`, `artifact_version`, `artifact_type`)
- **DB tables**: `artifacts` (id, session_id, agent_id, name, path, type) + `artifact_versions` (content blob, version, size, action)
- **API**: `GET /v1/artifacts?session_id=X`, `GET /v1/artifacts/<id>/content?version=N`, `GET /v1/artifacts/<id>/download?version=N`
- **Panel UI**: resizable right panel with version selector, type-aware rendering (code=highlight.js, html=iframe, svg=inline, image=img, markdown=rendered), copy/download/source-toggle actions
- **Artifact cards**: in chat messages, artifact files show with coral border and monitor icon, click opens panel instead of preview modal
- **Artifacts browse**: sidebar nav item → full-page view with type filter tabs (All/Code/HTML/Documents/Images/Markdown), agent filter chips, card grid with content previews
- **Browse API**: `GET /v1/artifacts/browse?agent_id=X&limit=N` — returns all artifacts across sessions with text previews
- **Click-through**: clicking a card in browse view opens the source chat session + artifact panel

### Next-Prompt Suggestions (Ghost Text)

Claude Code-style composer ghost text: after each assistant response, the web UI fetches a
short predicted-next-user-message and shows it as dimmed placeholder text in the chat input.
Tab or → accepts into the textarea, Enter on empty input accepts + sends, Escape or typing dismisses.

- **Endpoint**: `GET /v1/sessions/<id>/next-prompt` — synchronous; runs a small LLM call reusing
  the session's messages with a meta-instruction to predict the next user message under N words
- **Cache reuse**: by default uses the session's current model so the call hits the same prompt
  cache as the main chat (near-free). An override model can be set, at the cost of breaking cache reuse
- **Config** in `agent.json` → `next_prompt_suggestions`:
  - `enabled` (bool, default true) — feature toggle
  - `model` (string, default empty) — override model ID; empty = reuse session model
  - `max_words` (int, default 15) — soft cap on suggestion length
- **Config**: edit `agent.json` directly (no dedicated UI after mempalace migration)
- **Client module**: `NextPrompt` in `web/index.html` — fetch-token stale guard, active-session
  guard, dismiss-on-type, placeholder-based rendering (no overlay positioning)
- **Engine**: `generate_next_prompt_suggestion(session)` in `claude_cli.py` — strips metadata
  fields from messages, resolves provider via `resolve_provider_for_model`, calls
  `send_message_with_fallback` with `tools=False` and a tiny max_tokens budget

### Agentic Loop (Native)

All agents use the native Python agentic loop in `claude_cli.py`. OpenAI-compatible
API shape across the board — no SDK sidecars, no Anthropic wire format, no Mistral
SDK. Streaming is handled directly with `urllib` + raw socket SSE.

- `send_message()` + `send_message_with_fallback()`: top-level entry point with
  provider fallback, retry with exponential backoff, and transient-error classification
- `_handle_openai_response()`: streaming response handler, tool-call aggregation,
  multi-round tool loop, usage accounting, partial-response preservation on cancel/error
- `_MIDDLEWARE_PIPELINE`: `_middleware_cancel_check`, `_middleware_tool_result_budget`,
  `_middleware_microcompact`, `_middleware_compress_old`, `_middleware_compaction` —
  runs between tool rounds to keep context lean
- `_run_middleware(messages, ...)`: sequential pipeline runner, short-circuits on cancel
- Tool calls fire through `_execute_tool()`: built-in pre → external pre → execute →
  built-in post → external post → `_after_file_write()` side-effects
- Interactive mode: `AskUserQuestion` tool blocks the loop via `_pending_answers[session_id]`
  and an `Event`, unblocked by `POST /v1/chat/answer`
- Partial response recovery: on cancel/error, streamed text + tool calls are saved to
  chat history via `_rollback_messages()` so the user sees what got produced

### Multi-Provider Routing

The server supports multiple OpenAI-compatible providers via `config.json`. When a
model is selected, `resolve_provider_for_model(model)` in `claude_cli.py` resolves
the credentials — this is the single source of truth used by chat, delegate,
scheduler, warmup, and all background LLM calls.

Current providers: **Bifrost** (local gateway on port 7777) and **Kilo** (cloud
gateway). Both are OpenAI-compatible (`/v1/chat/completions`). Anthropic and
Mistral wire formats are no longer supported — the `api_type` concept was
removed in v7.3.0 (Purge B).

### Model Management

Models have a configurable `display_name` (default = shortname derived from model ID). All UI
surfaces show models as `displayName (provider)` — selectors, dropdowns, status bar, spinners.

- `display_name`: user-editable label, persisted in config.json `models` section
- Models tab: grouped by provider (collapsible sections), sorted by display name within each
- Per-model detail panel: gear button expands settings grid for each model
- Manual model add: model ID + provider + optional display name (for providers without `/models` endpoint)
- `modelShortName(modelId, withProvider=true)`: returns `display_name (provider)` or compact form
- `_match_known_model()` sets `display_name`, `max_output`, `inference`, `raw_formats` defaults from `KNOWN_MODELS`
- `KNOWN_MODELS`: family defaults for claude, gemini, qwen, crow, llama, mistral, minimax, devstral

Per-model config fields (all in config.json `models` section, editable in Models tab):
- `max_context`: context window size (hard max for compaction)
- `max_output`: max output tokens per response (thinking models need higher values)
- `inference`: base inference params (`temperature`, `top_p`, `top_k`, `max_tokens`)
- Provider-specific inference: `frequency_penalty`/`presence_penalty`, `min_p`/`repetition_penalty`, `reasoning_effort` (varies by model)
- `cost_input`/`cost_output`: cost per million tokens
- `raw_formats`: list of MIME patterns the model handles natively as multimodal (e.g. `["image/*", "application/pdf"]`)
- `presets`: purpose-based inference overrides (e.g. `coding`, `creative`)
- Note: `max_context` removed from agent config (was unused — always derived from model)

Thinking model auto-recovery: when `finish_reason == "length"` and visible output is <25% of
completion tokens (thinking consumed the budget), `max_tokens` is doubled on retry (capped at
model's `max_context`). Logged to stderr as `[thinking model: boosting max_tokens X → Y]`.

### Token Optimization

Per-agent token config in `agent.json` under `token_config`:

```json
{
  "token_config": {
    "tool_groups": ["core", "context", "web", "delegation", "git", "skills", "nodes", "scheduler"],
    "extra_tools": [],
    "include_tools_guide": true,
    "compact_threshold": 0.70,
    "scheduled_task_tools": false,
    "mcp_tool_filter": null,
    "mcp_tool_exclude": null
  }
}
```

- `tool_groups`: subset of groups (core, context, web, email, documents, delegation, code_graph, git, scheduler, mcp, skills, nodes). `null` = all tools
- `include_tools_guide`: inject tools.md into system prompt (~400 tokens)
- `compact_threshold`: override context compaction threshold (0.0-1.0), null = default 0.60
- `scheduled_task_tools`: include full tool schema in scheduled tasks
- `mcp_tool_filter`: list of exact names or fnmatch globs (e.g. `"mcp_mempalace_*"`); only matching MCP tools are sent. `null` = all allowed
- `mcp_tool_exclude`: list applied after `mcp_tool_filter`; matching tools are dropped
- System prompt cached per-session (60s TTL) to avoid disk I/O on tool loops
- `_filter_tools()` and `_get_agent_tool_names()` handle filtering for both custom loop and SDK paths
- GUI: Tokens tab in agent config modal with **Tool Definition Cost** card and **Measure** button

### Per-Agent Runtime Limits

Optional `limits` block in `agent.json` overrides global defaults for a specific agent:

```json
"limits": {
  "max_tool_rounds": 15,
  "tool_result_char_limit": 30000,
  "tool_results_total_tokens": 50000,
  "context_safety_ratio": 0.95
}
```

- `max_tool_rounds`: soft cap; after this, `tools=False` is passed to the next round. **Hard stop** at `1.5 * max_tool_rounds` terminates the loop entirely.
- `tool_result_char_limit`: individual tool result truncation point (per `_sanitize_tool_result`)
- `tool_results_total_tokens`: accumulated tool-result budget per turn before `_compress_old_tool_results` kicks in
- `context_safety_ratio`: pre-flight check in `send_message` raises `RuntimeError` if estimated prompt tokens exceed `max_context * ratio` (default 0.95), avoiding provider 400s
- Resolved via `_get_agent_limits()` with defaults from `AGENT_LIMITS_DEFAULTS`

### Session Cost Soft Warnings

Global `cost_limits.max_session_cost_usd` in `config.json`, editable via Settings → Server → Cost Limits in the web UI.

- Status bar shows `$ X.XX` next to the context-fill bar whenever the session has any cost data
- 70% of limit → amber warning triangle
- 90% of limit → red triangle + **one-time modal per session** (localStorage key `cost-warning-shown:<session_id>`)
- No hard abort — this is purely advisory
- 0 or missing limit → status bar still shows cost, no warnings
- Per-call cost comes from the `done` SSE event's `cost` field (already computed by `CostTracker.get_session_cost`)
- When the model has no pricing in `_cost_rates`, status bar shows `$0.00` with a tooltip explaining the rate is unknown

### Tool Definition Cost Measurement

`GET /v1/tools/breakdown?agent=<id>` returns per-group and per-tool token cost with schema decomposition:

```json
{
  "groups": [{"name", "source", "tool_count", "tokens",
              "name_tokens", "desc_tokens", "schema_tokens",
              "tools": [{"name", "tokens", "name_tokens", "desc_tokens", "schema_tokens", "param_count"}]}],
  "total_tokens": N, "builtin_tokens": N, "mcp_tokens": N,
  "max_context": N, "model": "…",
  "deferrable_mcp": {"deferred": bool, "tokens_saved_if_deferred": N, "threshold": N}
}
```

- `source`: `"builtin"` for brain-internal tools, `"mcp"` for MCP server tools (grouped by server via `MCPManager._tool_to_server`)
- Schema decomposition (`name_tokens` + `desc_tokens` + `schema_tokens`) lets callers see *why* a tool is expensive
- UI marks tools with schema >60% of total as ⚠ (amber) — these are the cheapest to reduce
- UI **Measure** button in Tokens tab fetches the breakdown on demand
- Per-tool checkboxes in the MCP rows write `token_config.mcp_tool_filter` directly via **Save selection as filter**

### OpenAI-Only Wire Format (v7.2.0)

Brain is now OpenAI-wire only. All providers (Bifrost, Kilo, future additions) must be OpenAI-compatible.

- `_handle_anthropic_response`, `_handle_mistral_response`, and mistral SDK helpers (`_VIBE_VERSION`, `_get_mistral_vibe_*`, `_create_mistral_client`) were removed in v7.2.0
- `send_message` and `_run_delegate` always build OpenAI chat/completions payloads and always call `_handle_openai_response`
- `make_headers`, `get_available_models`, `_apply_inference_to_payload`, `list_models` all collapsed to single-format paths
- `api_type` parameter fully removed from all function signatures (Purge B, v7.3.0). Providers return only `{api_key, base_url, provider_name}`.
- `TOOL_DEFINITIONS` (Anthropic flat shape) is retained as **internal source of truth** for tool lookups/display; `TOOL_DEFINITIONS_OPENAI` is derived from it for the wire format

### Key Patterns

- `tools.md` and `soul.md` are injected into the system prompt — primary way to control agent behavior
- `execute_command` runs with no TTY, no stdin, TERM=dumb — interactive commands timeout
- SQLite connections use thread-local pools (`_db_conn`, `_sched_conn`) to prevent handle leaks
- All ChatDB methods wrapped with `@_db_safe` — SQLite errors don't crash the server
- SSE keepalive comments sent every 5s to prevent browser timeout during tool execution
- `AbortController` in web UI ensures proper fetch cleanup between messages
- Tool call dedup tracker prevents infinite loops (2 identical calls = hard abort)
- Scheduled tasks have configurable timeout (default 5 min) via watchdog thread
- `_run_delegate` uses thread-local `max_tool_rounds` override (no global mutation)
- Memory is provided by the MemPalace MCP server (see MemPalace section below) — no built-in memory tools
- Smart model routing: `init_models_config()` auto-discovers models from providers, `resolve_model()` picks by purpose
- Unified provider resolution: `resolve_provider_for_model(model)` in `claude_cli.py` is the single source of truth for model→provider credential resolution (api_key, base_url, provider_name). Used by all LLM call paths: chat, `_run_delegate`, warmup, background tasks
- Provider-scoped models: when multiple providers offer the same model ID, entries are stored as `provider/model_id` with a `base_model_id` field. `get_api_model_id(model)` resolves to the actual API model ID
- Providers without `/models` endpoint: manually add models via Models tab (model ID + provider + display name)
- Model display format: `displayName (provider)` everywhere — `modelShortName(mid, withProvider)` controls compact vs full
- Telegram runs as an in-process thread, not a separate launchd daemon
- Thread-safe agent context: `_thread_local.current_agent` and `_thread_local.mcp_manager` preferred over globals for concurrent requests
- Session restore resolves provider from model via `_resolve_provider_static()` (no more wrong API key/URL on old chats)
- Provider cache uses `_provider_cache_lock` for thread-safe access
- Scheduler executes due tasks in parallel threads instead of sequentially
- Agent activity tracking: `/v1/agents/activity` returns active tasks/chats per agent for UI indicators
- Token optimization: `read_file` default limit 400 lines, compact threshold 60%, fresh_tail 16
- Model-aware max_tokens: configurable per model via `max_output` in models config
- Provider fallback ordering: same provider first, then capabilities, then priority
- Chat file attachments: files created by agents (write_file/edit_file) appear as viewable/downloadable attachments
- `get_model_max_output(model)` returns max output tokens based on model family or config
- Projects (Claude.ai-style): `ProjectManager` CRUD, `instructions` field in project.json injected into system prompt, file upload via multipart to `IngestManager`
- Project-scoped conversations: `session.project` field, `list_sessions(project=X)` filter, `state.currentProject` in web UI
- Project archive: sets `status: "archived"` in project.json, preserves files
- Project delete: soft-delete to `.trash/`, recoverable from trash
- Multipart upload: manual boundary parser (Python 3.13+ removed `cgi` module), preserves original filename
- Project notes: NoteManager CRUD, AI editing via write_file/edit_file tools (not EDIT_NOTE tags), auto-reload on filesystem changes
- LLM chat summaries: generated via Haiku after first response, shown in sidebar
- Project panel auto-refresh: 5s polling detects filesystem changes from any source
- Note AI sessions: status `note_chat`, hidden from project chat list, persistent per note via localStorage
- Agent teams: hierarchical team structure with team heads orchestrating members
- Cost tracking: `CostTracker` logs every LLM call to `costs.db` (tokens, model, provider, estimated cost)
- Rate limiting: `RateLimiter` with sliding-window per agent (requests/min, tokens/hr, cost/day) from `rate_limits` in agent.json
- Cost rates from `_cost_rates` defaults + `cost_input`/`cost_output` fields in `_models_config`
- `list_nodes` tool queries `GET /v1/nodes` to let agents discover available remote nodes
- `node.py` supports `--install` (launchd plist), `--uninstall`, `--status` for macOS daemon management
- Node plist: `~/Library/LaunchAgents/com.brain-agent.node.{name}.plist`, logs to `~/.brain-agent/node-{name}.log`
- Node connectivity: quick `GET /v1/nodes` check before entering long-poll loop for instant "Connected" feedback
- Sidebar session list polls after stream end until async LLM summary appears (2s interval, 30s max)
- Chat content search: 2-tier (SQLite title/summary → SQLite message content)
- Lossless context: `ContextManager` in `claude_cli.py` with SQLite DAG (`context.db`), replaces flat compaction
- Context config: `GET/POST /v1/context/config`, `GET /v1/context/stats?session_id=X`
- Context assembly: summaries (highest depth first) + fresh tail (default 16 messages) within token budget
- Three-level escalation: leaf summaries → condensation → fallback truncation
- Thread-local `current_session_id` set before compaction for context tools to access
- Legacy `_compact_conversation` remains as fallback when ContextManager is disabled
- Three-layer hooks: tool pre/post (external scripts), after_file_write (centralized pipeline), LLM-level (built-in)
- `HookRunner` loads hooks from `agent.json` `hooks.scripts[]`, runs via subprocess with env vars + stdin JSON
- Hooks timeout (default 5s), fail-open on crash, exit 1 = block (pre) or error (post), exit 2 = skip chain
- `_after_file_write()` centralizes file events + external hooks
- `_execute_tool()` orchestrates: built-in pre → external pre → execute → built-in post → external post
- Workflow `allowed_tools` restriction now enforced (was dead code)
- Hook runners cached per agent, invalidated on config save
- GET/POST `/v1/agents/{id}/hooks` for hook management
- Compaction sends SSE events (`compacting`, `compacted`) for spinner feedback

- Universal File Intelligence: DocumentParser extended with parse_xlsx, parse_pptx, parse_csv, parse_image, parse_svg
- read_document: format-aware reader dispatching by extension (PDF pages, XLSX sheets, PPTX slides, images, CSV)
- write_document: markdown → DOCX/XLSX/PPTX/PDF conversion
- edit_document: targeted edits (DOCX replace_text, XLSX update_cell/add_row, PPTX update_slide/add_slide)
- IngestManager auto-handles all new formats via DocumentParser.parse() dispatch
- Code Structure Graph: `CodeGraph` class with Tree-sitter AST parsing, SQLite storage (`code-graph.db`)
- 14 languages: Python, JS, TS, TSX, Go, Rust, Java, C, C++, C#, Ruby, Kotlin, Swift, PHP
- Qualified names: `{file_path}::{ClassName.method}`, edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY
- `code_graph_build`: parse directory, `code_graph_query`: 8 query types, `code_graph_impact`: BFS blast-radius via NetworkX
- `_maybe_update_code_graph(path)` in `_after_file_write()` for incremental updates on source file changes
- Incremental builds: SHA-256 file hash skip, re-parse only changed + dependent files
- `code_graph_enhance` tool: generate LLM summaries, classify architecture layers, generate guided tour
- Node summaries: one-line LLM descriptions per function/class (batched by file, uses Haiku)
- Architecture layers: api/service/data/ui/util/test classification via path+name pattern matching
- Guided tour: dependency-ordered walkthrough with layer grouping, key classes, reading order
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- Context fill indicator in footer: token estimate / max context with color-coded progress bar
- Manual compact button + LCM badge in footer
- Session corruption fix: `_rollback_messages()` reverts all intermediate tool-loop messages on failure
- Partial response preservation: on cancel/error, save streamed text + tools to chat history
- Message metadata: model, tokens, cost, tools, thinking persisted per assistant message, restored on load
- `augmented_messages` strips metadata fields (only role+content sent to API) — prevents 400 errors
- Extended thinking: `thinking` param in inference_params, budget levels (low=2K, med=8K, high=32K)
- Thinking blocks: `content_block_start/delta/stop` with `thinking_delta` + `signature_delta` capture
- Thinking blocks preserved in conversation history (required by Anthropic API for tool loops)
- Thinking UI: collapsible purple block in chat, "Thinking deeply..." spinner, persisted in metadata
- Model display: shown in inline thinking indicator + spinner bar, updates on fallback
- Remote node badge: purple pill on tool blocks when `node` param present
- Resizable sidebars: drag handles on right edge of left sidebar, left edge of project panel, persisted to localStorage
- Stream timing state (`_streamStartTime`, `_streamTimerInterval`) scoped per-agent chat, not global
- Stream generation counter (`_streamGen`): safety net and catch block check generation to avoid stale microtasks from a completed stream killing a newer stream's spinner

### Concurrency & Thread Safety

The server handles multiple concurrent chat requests, scheduled tasks, delegations, and background threads. Key invariants:

- **Session.lock**: all session field mutations (messages, model, status, streaming flag, sdk_session_id, summary) must be under `session.lock`
- **SessionManager.get()**: uses `_LOADING_SENTINEL` + `threading.Event` to prevent duplicate Session objects for the same session_id; `peek()` for lightweight cache-only reads (no DB load)
- **Thread-local context**: every request/background thread MUST set `_thread_local.current_agent` and `_thread_local.mcp_manager` — never fall back to globals for agent context
- **MCPManager**: all dict access (`clients`, `_tool_to_server`) protected by `self._lock`; iteration uses snapshots
- **Tool dedup**: `reset_tool_dedup()` called at start of each chat request to prevent cross-session false duplicates
- **Background threads**: `_generate_chat_summary`, scheduler, workflow engine, TaskRunner all set/clean thread-local context in try/finally
- **LLM JSON parsing**: `_extract_json_from_llm()` uses `json.JSONDecoder.raw_decode()` — handles nested objects, markdown fences, surrounding text
- **Fallback search**: file reads capped at 32KB to prevent OOM on large files
- **Interactive answers**: pending answers set atomically under `_pending_answers_lock`; stale queries evicted via `_evict_stale_queries()` (5min TTL)

### Agent Teams

Agents can be organized into teams with a hierarchical delegation model:

- **Team head**: An agent with a `team` field in `agent.json` containing `members` list
- **Team members**: Agents listed in a team head's `team.members` array
- **Standalone agents**: Agents not in any team (excluding main)
- **main**: Global orchestrator, never has a `team` field

```json
// Example team head agent.json
{
  "description": "Research team lead",
  "display_name": "Research Lead",
  "model": "claude-sonnet-4-6",
  "team": {
    "name": "Research Team",
    "description": "Handles research and analysis tasks",
    "avatar": "🔬",
    "members": ["Researcher", "crow"]
  }
}
```

**Delegation scoping:**
- `main` → team heads + standalone agents (not members directly)
- Team heads → their team members
- Team members → peers in same team + their team head

**API:**
- `GET /v1/teams` — team structure
- `POST /v1/teams` — create/update/dissolve/move teams

### Agent Directory Structure

```
agents/<name>/
  soul.md         # Personality, role, instructions
  agent.json      # {description, display_name, model, avatar, paused}
  tools.md        # Optional per-agent tool guide
  mcp.json        # MCP server connections (incl. MemPalace)
  gmail.json      # Gmail credentials (not in git)
  skills/         # Installed skills (SKILL.md per skill)
  chats.db        # Chat history (in main agent dir)
  scheduler.db    # Scheduled tasks (in main agent dir)
```

### Tools (30+)

File ops: read_file, write_file, edit_file, list_directory, search_files
Documents: read_document, write_document, edit_document (PDF/DOCX/XLSX/PPTX/CSV/images)
Code graph: code_graph_build, code_graph_query, code_graph_impact (AST-based, 14 languages)
Shell: execute_command (non-interactive only, see tools.md for banned commands)
Web: web_fetch, exa_search
Gmail: gmail_inbox, gmail_read, gmail_search, gmail_send, gmail_reply
Memory: provided by MemPalace MCP server (mcp_* tools)
Agents: delegate_task, task_status, task_cancel
Skills: use_skill
Git: git_command (status, diff, log, branch, commit, stash, blame, show, tag, remote)
GitHub: github_command (PRs, issues, repo, releases, workflows, API via gh CLI)
Context: context_search, context_detail, context_recall (drill-back into compacted history)
Nodes: list_nodes (remote node status and info)
Schedule: schedule_list, schedule_history
MCP: mcp_* (dynamic, from connected MCP servers)

### Server API

Server runs on port 8420 (configurable). Key endpoints:
- `POST /v1/chat` — SSE streaming with keepalive
- `POST /v1/chat/answer` — deliver user answer to interactive AskUserQuestion
- `GET /v1/sessions/<id>/next-prompt` — predicted next-user-message for composer ghost text
- `POST /v1/sessions` — auto-resolves provider from model
- `GET /v1/schedule/running` — live task monitoring
- `POST /v1/skills/browse` — searches 7000+ skills from ClawHub
- `GET /v1/agents/activity` — active tasks/chats per agent
- `POST /v1/agents/<id>/soul-chat` — AI-assisted soul.md editing
- `GET /v1/teams` — team structure (heads, members, standalone)
- `POST /v1/teams` — manage teams (create, update, dissolve, move)
- `GET|POST /v1/models/config` — model routing configuration
- `GET /v1/mcp/registry` — list available MCP server templates
- `GET /v1/costs` — cost stats (agent, hours params)
- `GET /v1/costs/daily` — daily cost breakdown (agent, days params)
- `GET /v1/sessions?project=X` — list sessions filtered by project
- `GET /v1/agents/<id>/projects` — list projects with instructions, doc_count, status
- `POST /v1/agents/<id>/projects` — create project (name, description)
- `GET/PUT/DELETE /v1/agents/<id>/projects/<name>` — project CRUD (instructions field editable)
- `POST /v1/agents/<id>/projects/<name>/ingest` — multipart file upload to project knowledge base
- `GET /v1/agents/<id>/projects/<name>/docs` — list ingested documents
- `DELETE /v1/agents/<id>/projects/<name>/docs/<hash>` — remove ingested document
- `POST /v1/restart` — re-execs the server process

### Deployment

- Server: launchd daemon (`com.brain-agent.server.plist`)
- Telegram: in-process thread (started/stopped via server, no separate daemon)
- Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev`
- Tunnel runs on 192.168.4.65 (tunnel: itrmp)

### Providers

All providers are OpenAI-compatible. Current providers:

- **Bifrost** — local gateway on `http://localhost:7777/v1`
- **Kilo** — cloud gateway on `https://api.kilo.ai/api/gateway/v1`

Provider configs live in `config.json` under `providers`. Each entry has `api_key`
and `base_url`; `type` defaults to `openai` if omitted.

### MemPalace (Memory via MCP)

All memory is now provided by the **MemPalace** MCP server, not by built-in engine code.
Agents recall and store memories by calling its `mcp_*` tools; nothing in `claude_cli.py`
reads or writes memory files directly, and there are no HTTP endpoints for memory
management in `server.py`.

- **Wiring**: configured in each agent's `mcp.json` as an MCP server entry; loaded at
  agent init alongside other MCP servers
- **Tools**: exposed as `mcp_*` tools filtered through the normal MCP tool pipeline
  (pre/post hooks, dedup, budget middleware)
- **No built-in memory tools**: `memory_store`, `memory_recall`, `memory_shared`,
  `memory_delete`, entity extraction, autodream, relationship discovery, memory-summary
  injection, QMD hybrid search, and the Knowledge Graph view are all removed
- **No built-in background pipelines**: the nightly Memory Summary → Relationship
  Discovery → Autodream chain, `_auto_memory_extract`, and QMD reindex on file-write
  are all no-ops in the current server; any consolidation now happens inside MemPalace
- **Agent directories**: `agents/<name>/` still holds `soul.md`, `agent.json`, skills,
  and mcp.json, but the loose top-level `*.md` memory files were removed in the
  mempalace migration (see commits tagged `mempalace migration C*`)

Historical note: the previous system (QMD + MemoryStore + autodream + KG) lived in
`claude_cli.py` and `server.py` and was torn down across C1–C10 of the mempalace
migration. See `git log --grep "mempalace"` for the migration sequence.
