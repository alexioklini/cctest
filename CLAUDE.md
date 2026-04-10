# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Repository Structure

- **`brain.py`** — Gateway CLI: start/stop/restart server, launch frontends
- **`server.py`** — HTTP API server daemon (always-running, launchd managed)
- **`client.py`** — Shared HTTP/SSE client library for frontends
- **`claude_cli.py`** — Core engine: tools, agents, memory, MCP, scheduler, Gmail
- **`tui.py`** — Terminal frontend (Rich + prompt_toolkit)
- **`telegram.py`** — Telegram bot frontend
- **`web/index.html`** — Web UI (single-page app, light/dark theme)
- **`tools.md`** — Global tool usage guide (loaded into system prompt at runtime)
- **`config.json`** — Provider config, server settings, Telegram config (not in git)
- **`agents/`** — Per-agent directories with soul.md, agent.json, skills, memory, mcp

## Architecture

```
brain.py (gateway)
  ├── server.py (daemon on port 8420, launchd managed)
  │   ├── claude_cli.py (engine: 30+ tools, agents, memory, scheduler)
  │   ├── sdk_backend.py (REST polling proxy to sidecar)
  │   ├── /mcp endpoint (MCP JSON-RPC: tools/list, tools/call with hooks)
  │   ├── SQLite: chats.db, scheduler.db
  │   └── MCP server connections
  ├── sdk_sidecar.py (REST API on port 8421, Anthropic Agent SDK — legacy)
  │   └── Runs claude_agent_sdk.query() in a clean process (no claude_cli import)
  ├── pi_sidecar/ (Node.js REST API on port 8422, PI Agent SDK — code mode)
  │   ├── POST /query, GET /events/{id}, POST /cancel/{id}, POST /answer/{id}
  │   ├── PI SDK: createAgentSession() with openai-completions provider
  │   ├── Native tools: read, write, edit, bash (run in Node.js)
  │   └── Brain Agent tools via HTTP → /v1/tools/call
  ├── qmd mcp --http (daemon on port 8181, hybrid memory search)
  │   └── Collections: one per agent → agents/<name>/*.md
  ├── tui.py (terminal client)
  ├── telegram.py (Telegram client)
  └── web/index.html (browser client — Mission Control cockpit)
```

### Web UI (Claude.ai-style)

The web UI uses a sidebar + multi-view layout inspired by Claude.ai:

- **Sidebar** (`#sidebar`): collapsible left panel with agent selector, nav (New Chat, Search, Chats, Projects, Knowledge Graph, Customize), recent sessions
- **Welcome view** (`#welcome-view`): greeting + composer for new conversations
- **Chat view** (`#chat-view`): message history + streaming composer with tool blocks
- **Chats view** (`#chats-view`): searchable session list with All/Archived tabs
- **Projects view** (`#projects-view`): Claude.ai-style project list with card grid, search, Your Projects/Archived tabs, sort dropdown
- **Project detail view** (`#project-detail-view`): back nav, project heading, chat composer, conversation list, right panel (Instructions + Files)
- **Graph view** (`#graph-view`): knowledge graph visualization
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

### Code Mode (PI Agent SDK)

Code mode uses the PI Agent SDK (`@mariozechner/pi-coding-agent`) as the agentic loop via a Node.js sidecar process.
No Brain Agent system prompt or agent configuration is used — PI SDK provides its own minimal prompt + native coding tools.

- **PI Sidecar**: `pi_sidecar/pi_sidecar.ts` — Node.js HTTP server on port 8422, same REST API contract as `sdk_sidecar.py`
- **REST API**: `POST /query` (start), `GET /events/{id}` (poll), `POST /cancel/{id}` (abort), `POST /answer/{id}` (interactive)
- **Provider routing**: all models use `openai-completions` API type; non-OpenAI providers get `compat: { supportsStore: false }` to avoid 422 errors
- **Model `baseUrl`**: must keep `/v1` suffix — PI SDK's OpenAI client appends `/chat/completions` directly
- **Native tools**: PI SDK provides read, write, edit, bash (run in Node.js, no IPC)
- **Brain Agent tools**: proxied via HTTP to `/v1/tools/call` endpoint (memory, web, git, etc.)
- **System prompt**: PI SDK's own 150-word default + `AGENTS.md`/`CLAUDE.md`/`.cursorrules` auto-discovered from project folder up to git root
- **Server routing**: `_use_pi_sdk = session.status == "code"` — inline REST polling to port 8422, same pattern as Anthropic SDK path
- **Auto-start**: server.py starts PI sidecar with watchdog thread alongside SDK sidecar
- **Folder browser GUI**: modal with breadcrumb navigation, lazy-loaded directory listing via `/v1/files/tree?depth=0`, single-click select, double-click navigate, manual path input
- **File preview**: clicking files in tree opens content in diff panel via `/v1/files/preview` (text, code, images)
- **SSE streaming**: uses proper two-line `event: type\ndata: json` format (same parser as main chat)
- **Folder-based projects**: sessions tagged with folder path in `project` field; sidebar shows projects with counts + session list
- **Session management**: editable session titles in toolbar, archive/delete in sidebar
- **Composer**: thinking level, AI refine, tool display toggle, Ask/Auto/Plan mode selector, model dropdown (opens above)
- **Status bar**: shows model, session ID, tokens in/out, speed, context fill — synced via `updateCodeStatusBar()`
- **Diff scoping**: `git diff --stat HEAD -- <dir>` scopes to project subfolder, not entire repo
- **`/init` command**: generates `AGENTS.md` by having PI agent analyze the project structure
- **State**: `codeFolder`, `codeModel`, `codePermission`, `codeThinking`, `codeShowToolCalls`, `codeSessionId`, `codeMessages[]`
- **Views**: `code-welcome` (folder picker + composer), `code-chat` (file tree + messages + diff panel)

### Chat File Attachments

Users can attach files in the chat composer. Routing is dynamic based on model capabilities.

- **Web UI**: all files go to `state._pendingFiles[]` as base64 (images get `.preview` for thumbnails)
- **Unified send path**: browser sends all files as `body.files`; `body.images` accepted for backward compat (Telegram)
- **Server routing**: checks model's `raw_formats` (MIME patterns) to decide per-file:
  - **Multimodal**: MIME matches `raw_formats` + base64 + <20MB → injected as content block (LLM sees raw data)
  - **Disk**: otherwise → saved to `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- **Multimodal format**: Anthropic `image`/`document` blocks, OpenAI/Mistral `image_url` data URIs
- **API type guard**: OpenAI/Mistral only support `image/*` as multimodal (no PDF blocks)
- **`raw_formats`**: per-model MIME pattern list in `KNOWN_MODELS` and `config.json` models section
  - Claude/Qwen/Mistral: `["image/*"]`, Gemini: `["image/*", "application/pdf"]`, text-only: `[]`
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
- **No memory/KG integration**: artifacts excluded from QMD indexing and entity extraction
- **Default write path**: `write_file` with relative path defaults to agent's artifacts session folder
- **SSE event**: `artifact_updated` (enriched `file_created` with `artifact_id`, `artifact_version`, `artifact_type`)
- **DB tables**: `artifacts` (id, session_id, agent_id, name, path, type) + `artifact_versions` (content blob, version, size, action)
- **API**: `GET /v1/artifacts?session_id=X`, `GET /v1/artifacts/<id>/content?version=N`, `GET /v1/artifacts/<id>/download?version=N`
- **Panel UI**: resizable right panel with version selector, type-aware rendering (code=highlight.js, html=iframe, svg=inline, image=img, markdown=rendered), copy/download/source-toggle actions
- **Artifact cards**: in chat messages, artifact files show with coral border and monitor icon, click opens panel instead of preview modal
- **Artifacts browse**: sidebar nav item → full-page view with type filter tabs (All/Code/HTML/Documents/Images/Markdown), agent filter chips, card grid with content previews
- **Browse API**: `GET /v1/artifacts/browse?agent_id=X&limit=N` — returns all artifacts across sessions with text previews
- **Click-through**: clicking a card in browse view opens the source chat session + artifact panel

### Agent SDK (Agentic Loop)

All agents use the Anthropic Agent SDK (Claude Code CLI) as the agentic loop by default.
The SDK provides built-in tools (Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch),
context management, and token-efficient file operations.

- `sdk_sidecar.py`: REST API on port 8421 — POST /query starts a query, GET /events/{id} polls for events
- `sdk_backend.py`: Provider env var builder + REST polling proxy (50ms interval with keepalive comments)
- Must NOT import `claude_cli` in the sidecar — its module side-effects break anyio subprocess streaming
- Server builds system prompt and provider env using `claude_cli`, then hands off to sidecar via REST
- `/mcp` endpoint on server: MCP JSON-RPC (initialize, tools/list, tools/call) — sidecar's SDK connects here for custom tools
- Hooks run server-side in `/mcp` tools/call handler (SDK hook registration causes streaming to buffer — never pass `hooks_enabled: true`)
- Exception: `AskUserQuestion` PreToolUse hook registered only in interactive mode (matcher scoped to single tool)
- SDK routing per provider: `"use_sdk": true/false` in provider config (anthropic default true, openai/mistral always direct loop)
- SDK badge shown in web UI status bar and message footers
- All paths route through SDK: web UI, TUI interactive, CLI one-shot, scheduled tasks, `_run_delegate`
- `query_sync` accepts `tool_defs`, `server_url`, `agent_id`, `session_id`, `cancel_fn`, `sdk_session_id`, `return_metadata`
- All SDK paths fall back to direct API if sidecar is unavailable
- Claude Code skills: `scan_claude_code_skills()` discovers plugins from `~/.claude`, GUI toggle per agent, `SdkPluginConfig` for SDK loading
- Tool args capture: sidecar accumulates `input_json_delta` fragments, re-emits `tool_call` with full args on `content_block_stop`
- Tool call dedup: server and client detect re-emitted tool_calls (same name, now with args) and update in-place
- Interactive mode: `POST /query` accepts `interactive: true`, sidecar registers AskUserQuestion PreToolUse hook
- Interactive answer flow: sidecar emits `user_input_needed` SSE event, blocks on `_pending_answers[query_id]`, unblocked by `POST /answer/{query_id}`
- TUI interactive: `client.chat(..., interactive=True)` enables AskUserQuestion, renders questions with options, sends answers via `client.answer()`

Provider routing for SDK (env vars per provider):
- `cliproxyapi`: Claude models (Max subscription OAuth) + Gemini, Qwen — `ANTHROPIC_BASE_URL=http://127.0.0.1:8317`
- `omlx`: Local Crow models — `ANTHROPIC_BASE_URL=http://127.0.0.1:8000`
- `minimax`: MiniMax M2.5/M2.7 — `ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic`

### Multi-Provider Routing

The server supports multiple LLM providers (config.json). When a model is selected,
the server automatically routes to the correct provider based on which one has that model.
Provider types: `openai` (OpenAI-compatible), `anthropic` (native Anthropic API), and `mistral` (Mistral SDK with Vibe CLI-compatible headers).

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
- Provider-specific inference: `frequency_penalty`/`presence_penalty` (OpenAI/oMLX), `min_p`/`repetition_penalty` (oMLX), `reasoning_effort` (Mistral)
- `cost_input`/`cost_output`: cost per million tokens
- `raw_formats`: list of MIME patterns the model handles natively as multimodal (e.g. `["image/*", "application/pdf"]`)
- `presets`: purpose-based inference overrides (e.g. `coding`, `creative`)
- Note: `max_context` removed from agent config (was unused — always derived from model)

Thinking model auto-recovery: when `finish_reason == "length"` and visible output is <25% of
completion tokens (thinking consumed the budget), `max_tokens` is doubled on retry (capped at
model's `max_context`). Logged to stderr as `[thinking model: boosting max_tokens X → Y]`.

### Mistral Provider (Vibe CLI Integration)

The `mistral` provider type uses the official `mistralai` Python SDK instead of raw HTTP,
replicating the Vibe CLI's API interaction pattern for Pro subscription key compatibility.

- SDK: `from mistralai.client import Mistral` — handles auth, streaming, tool calling natively
- Vibe headers: `user-agent: mistral-client-python/Mistral-Vibe/{version}`, `x-affinity: {session_id}`
- Vibe metadata: `{agent_entrypoint: "cli", client_name: "vibe_cli", ...}` in request body
- Tool format: OpenAI-style function calling (same as `openai` provider type)
- System prompt: inserted as first message (same as `openai` provider type)
- Models: `devstral-small-latest` (Devstral Small), `mistral-vibe-cli-latest` (Devstral 2)
- `_handle_mistral_response()`: streaming response handler via SDK with full agentic tool loop
- Warmup uses SDK `chat.complete()` instead of raw urllib
- `_use_sdk`: always false for mistral — uses Mistral SDK natively via direct agentic loop

### Token Optimization

Per-agent token config in `agent.json` under `token_config`:

```json
{
  "token_config": {
    "tool_groups": ["core", "memory", "context", "web", "delegation", "git", "skills", "nodes", "scheduler"],
    "extra_tools": [],
    "include_tools_guide": true,
    "include_memory_summary": true,
    "memory_summary_cap": 2000,
    "prompt_caching": true,
    "compact_threshold": 0.70,
    "scheduled_task_tools": false
  }
}
```

- `tool_groups`: subset of 13 groups (core, memory, context, web, email, documents, delegation, code_graph, git, scheduler, mcp, skills, nodes). `null` = all tools
- `include_tools_guide`: inject tools.md into system prompt (~400 tokens)
- `include_memory_summary`: inject memory summary on first turn (~500 tokens)
- `memory_summary_cap`: max chars for memory summary injection
- `prompt_caching`: Anthropic `cache_control` on system prompt blocks
- `compact_threshold`: override context compaction threshold (0.0-1.0), null = default 0.60
- `scheduled_task_tools`: include full tool schema in scheduled tasks; memory summary tasks always use memory-only tools
- System prompt cached per-session (60s TTL) to avoid disk I/O on tool loops
- `_filter_tools()` and `_get_agent_tool_names()` handle filtering for both custom loop and SDK paths
- GUI: Tokens tab in agent config modal

### Key Patterns

- `tools.md` and `soul.md` are injected into the system prompt — primary way to control agent behavior
- `execute_command` runs with no TTY, no stdin, TERM=dumb — interactive commands timeout
- SQLite connections use thread-local pools (`_db_conn`, `_sched_conn`) to prevent handle leaks
- All ChatDB methods wrapped with `@_db_safe` — SQLite errors don't crash the server
- SSE keepalive comments sent every 5s to prevent browser timeout during tool execution
- `AbortController` in web UI ensures proper fetch cleanup between messages
- Tool call dedup tracker prevents infinite loops (2 identical calls = hard abort)
- Scheduled tasks have configurable timeout (default 5 min) via watchdog thread
- `_run_delegate` uses thread-local `max_tool_rounds` override (no global mutation) and thread-local memory stores
- Memory uses QMD hybrid search (BM25 + vector + LLM reranking) via HTTP MCP on port 8181
- Markdown files are source of truth for memory; QMD indexes them with per-collection debounced embed after writes
- If QMD is unreachable, memory recall falls back to file-scan substring matching
- QMD docs endpoint returns index health per file: `indexed`, `embedded_at`, `current` (hash match)
- QMD path normalization: QMD lowercases paths and converts underscores to hyphens — `/docs` endpoint mirrors this when matching filesystem paths to index entries
- `/v1/services` returns per-collection health stats: `total`, `indexed`, `embedded`, `stale`, `not_indexed`
- Smart model routing: `init_models_config()` auto-discovers models from providers, `resolve_model()` picks by purpose
- Unified provider resolution: `resolve_provider_for_model(model)` in `claude_cli.py` is the single source of truth for model→provider credential resolution (api_key, base_url, api_type, provider_name). Used by all LLM call paths: interactive chat, PI sidecar, SDK sidecar, `_run_delegate`, warmup, background tasks
- Provider-scoped models: when multiple providers offer the same model ID, entries are stored as `provider/model_id` with a `base_model_id` field. `get_api_model_id(model)` resolves to the actual API model ID
- Mistral model discovery uses SDK (`client.models.list()`) instead of raw HTTP (Vibe CLI auth incompatible with plain Bearer)
- Providers without `/models` endpoint: manually add models via Models tab (model ID + provider + display name)
- Model display format: `displayName (provider)` everywhere — `modelShortName(mid, withProvider)` controls compact vs full
- QMD session reuse: `_qmd_session_lock` prevents concurrent threads from creating duplicate MCP sessions
- QMD health check uses lightweight TCP socket connect (no MCP session created)
- `memory_shared` and `list_all` return full content body, not just metadata
- Telegram runs as an in-process thread, not a separate launchd daemon
- Thread-safe agent context: `_thread_local.current_agent` and `_thread_local.mcp_manager` preferred over globals for concurrent requests
- Session restore resolves provider from model via `_resolve_provider_static()` (no more wrong API key/URL on old chats)
- Provider cache uses `_provider_cache_lock` for thread-safe access
- Memory frontmatter uses `_yaml_escape()` to prevent YAML injection from user content
- Memory filenames include hash suffix to prevent collisions between similar names
- Scheduler executes due tasks in parallel threads instead of sequentially
- Agent activity tracking: `/v1/agents/activity` returns active tasks/chats per agent for UI indicators
- Auto memory creation: heuristic detection (corrections, identity, decisions, references) + LLM extraction via Haiku, runs in background after each response
- Continuous session summarization: memory summary refreshes at 10K tokens, then every 5K during active conversations
- Autodream memory consolidation: chains after relationship discovery in nightly pipeline (Memory Summary → RD → Autodream)
- Autodream passes: dedup (QMD similarity + LLM merge), staleness (frontmatter `last_recalled` + `stale` flags), conflicts (LLM contradiction detection), skill candidates (procedural memory detection)
- Autodream config in agent.json: `autodream: {enabled, stale_threshold_days, dedup_similarity_threshold, max_dedup_merges, max_conflict_checks, report_retention}`
- Memory summary config: `memory_summary: {enabled, frequency, start_time, model}` — `model` overrides default Sonnet for the nightly scheduled task
- Relationship discovery config: `relationship_discovery: {enabled, frequency, start_time, model}` — `model` overrides default Haiku; configurable in GUI (Agent config → Memory tab)
- Token optimization: memory summary injected on `_tool_round==0` only (not per tool-loop call), 3K char cap on injected summary, `read_file` default limit 400 lines, compact threshold 60%, fresh_tail 16
- Background pipeline models: memory summary scheduled tasks → Sonnet, relationship discovery → Haiku; `ensure_*_schedules()` auto-recreates when model changes
- Autodream health report: stored as "Memory Health Report — {date}" memory file (type: system), auto-retained (last N reports)
- `last_recalled` frontmatter field: stamped on recall in background thread, used for staleness detection
- `get_memory_health(agent_id)`: live stats — total, by_type, stale_count, age_distribution, recall_frequency (hot/warm/cold/never), autodream results, health_score
- `GET /v1/agents/<id>/memory-health`: full health dashboard data; `trigger_autodream(agent_id)` for manual runs
- Knowledge graph: auto-discovery (LLM-based, entity extraction, co-recall), graph-aware recall default (1 hop), visualization via Canvas 2D
- Model-aware max_tokens: Opus 32K, Sonnet 16K, Haiku 8K, MiniMax 32K, configurable via `max_output` in models config
- Provider fallback ordering: same provider first, then capabilities, then priority
- Chat file attachments: files created by agents (write_file/edit_file) appear as viewable/downloadable attachments
- `get_model_max_output(model)` returns max output tokens based on model family or config
- Projects (Claude.ai-style): `ProjectManager` CRUD, `instructions` field in project.json injected into system prompt, file upload via multipart to `IngestManager`
- Project-scoped conversations: `session.project` field, `list_sessions(project=X)` filter, `state.currentProject` in web UI
- Project archive: sets `status: "archived"` in project.json, preserves files and QMD collection
- Project delete: soft-delete to `.trash/`, removes QMD collection, recoverable from trash
- Multipart upload: manual boundary parser (Python 3.13+ removed `cgi` module), preserves original filename
- Project notes: NoteManager CRUD, AI editing via write_file/edit_file tools (not EDIT_NOTE tags), auto-reload on filesystem changes
- Chat transcript indexing: chats-indexed/*.md chunks stored in QMD for semantic search
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
- Chat content search: 3-tier (QMD semantic → SQLite title/summary → SQLite message content)
- Chat transcript indexing decoupled from summary generation; backfill runs at startup for unindexed sessions
- Sessions API returns `indexed` field (true/false/null) based on chats-indexed file mtime vs last_active
- `_parse_frontmatter()` skips indented/nested YAML lines to prevent `related:` sub-fields overwriting top-level keys
- Knowledge graph edge resolution: ref files with `/` treated as agent-relative paths (no double-prefix)
- Relationship discovery: two-stage (QMD semantic candidates → LLM full-content classification), scales to large file counts
- QMD query cleanup: strip newlines, quotes, markdown formatting — QMD silently returns empty on multiline queries
- Lossless context: `ContextManager` in `claude_cli.py` with SQLite DAG (`context.db`), replaces flat compaction
- Context config: `GET/POST /v1/context/config`, `GET /v1/context/stats?session_id=X`
- Context assembly: summaries (highest depth first) + fresh tail (default 16 messages) within token budget
- Three-level escalation: leaf summaries → condensation → fallback truncation
- Thread-local `current_session_id` set before compaction for context tools to access
- Legacy `_compact_conversation` remains as fallback when ContextManager is disabled
- Three-layer hooks: tool pre/post (external scripts), after_file_write (centralized pipeline), LLM-level (built-in)
- `HookRunner` loads hooks from `agent.json` `hooks.scripts[]`, runs via subprocess with env vars + stdin JSON
- Hooks timeout (default 5s), fail-open on crash, exit 1 = block (pre) or error (post), exit 2 = skip chain
- `_after_file_write()` centralizes QMD reindex + entity extraction + KG update + file events + external hooks
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
- **Thread-local context**: every request/background thread MUST set `_thread_local.current_agent`, `_thread_local.memory_store`, and `_thread_local.mcp_manager` — never fall back to globals for agent context
- **MCPManager**: all dict access (`clients`, `_tool_to_server`) protected by `self._lock`; iteration uses snapshots
- **MemoryStore._ensured_collections**: guarded by `_ensured_lock` to prevent duplicate QMD collection init
- **Entity index**: `_ensure_entity_index()` checks `_entity_index_initialized` under `_entity_index_lock`
- **QMD session ID**: reads/writes of `_qmd_session_id` under `_qmd_session_lock`
- **Tool dedup**: `reset_tool_dedup()` called at start of each chat request to prevent cross-session false duplicates
- **Background threads**: `_auto_memory_extract`, `_generate_chat_summary`, scheduler, workflow engine, TaskRunner all set/clean thread-local context in try/finally
- **Co-recall dict**: `_recall_cooccurrence` capped at 50K entries to prevent unbounded memory growth
- **QMD queries**: sanitized (strip newlines, quotes, markdown chars) before sending to QMD
- **LLM JSON parsing**: `_extract_json_from_llm()` uses `json.JSONDecoder.raw_decode()` — handles nested objects, markdown fences, surrounding text
- **Entity extraction**: `_RE_CAPITALIZED` requires 3+ char words; `_ENTITY_STOP_PHRASES` filters common false positives
- **Fallback search**: file reads capped at 32KB to prevent OOM on large files
- **SDK sidecar**: pending answers set atomically under `_pending_answers_lock`; stale queries evicted via `_evict_stale_queries()` (5min TTL)

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

**Shared memory scoping:**
- `memory_shared(scope="global")` → main agent's memory (default)
- `memory_shared(scope="team")` → team head's memory

**API:**
- `GET /v1/teams` — team structure
- `POST /v1/teams` — create/update/dissolve/move teams

### Agent Directory Structure

```
agents/<name>/
  soul.md         # Personality, role, instructions
  agent.json      # {description, display_name, model, avatar, max_context, paused}
  tools.md        # Optional per-agent tool guide
  mcp.json        # MCP server connections
  gmail.json      # Gmail credentials (not in git)
  skills/         # Installed skills (SKILL.md per skill)
  *.md            # Memory files (indexed by QMD)
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
Memory: memory_store, memory_recall, memory_shared, memory_delete
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
- `POST /v1/sessions` — auto-resolves provider from model
- `GET /v1/schedule/running` — live task monitoring
- `POST /v1/skills/browse` — searches 7000+ skills from ClawHub
- `GET /v1/services/qmd/docs` — list docs with index health (modified, embedded_at, current)
- `GET /v1/agents/activity` — active tasks/chats per agent
- `GET /v1/agents/<id>/memories` — list all memories with frontmatter and content
- `DELETE /v1/agents/<id>/memories?name=X` — delete a memory by name
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
- QMD: launchd daemon (`com.brain-agent.qmd.plist`, port 8181) or auto-started by `brain.py start`
- oMLX: local MLX inference server (`brew services`, port 8000)
- CLIProxyAPI: local OAuth proxy for Claude models (`brew services`, port 8317)
- Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev`
- Tunnel runs on 192.168.4.65 (tunnel: itrmp)

### oMLX (Local MLX Inference)

Local [oMLX](https://github.com/jundot/omlx) instance on port 8000 serves quantized MLX models
on Apple Silicon. OpenAI-compatible API, no API key needed for local access.

- Models: `~/.omlx/models/` (auto-discovered subdirectories)
- Current model: `Crow-4B-Opus-4.6-Distill` (4-bit quantized, ~2.5GB)
- SSD KV cache: `/Volumes/Scratch/omlx-cache` (100GB max, 8GB hot cache in RAM)
- Admin dashboard: `http://127.0.0.1:8000/admin`
- Service control: `brew services start/stop/restart omlx`
- Convert new models: `/opt/homebrew/opt/omlx/libexec/bin/mlx_lm.convert`

### CLIProxyAPI (Claude OAuth Proxy)

Local [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) instance on port 8317 provides
Claude models via OAuth (no API key costs). Installed via Homebrew, runs as a launchd service.

- Config: `/opt/homebrew/etc/cliproxyapi.conf`
- Auth tokens: `~/.cli-proxy-api/` (Claude, Gemini, Qwen OAuth)
- API key for Brain Agent: `brain-agent`
- Management panel: `http://127.0.0.1:8317/management.html` (secret-key: `brain-agent`)
- Service control: `brew services start/stop/restart cliproxyapi`

### MiniMax (Cloud LLM Provider)

MiniMax provides M2.5 and M2.7 models via an Anthropic-compatible API.

- Base URL: `https://api.minimax.io/anthropic/v1`
- API type: `anthropic`
- No `/models` endpoint — models must be manually configured in `config.json`
- M2.7 always produces thinking blocks (cannot be disabled)
- For coding: use `temperature: 0.2`, `max_tokens: 8192` to avoid thinking consuming the budget

### QMD (Hybrid Memory Search)

[QMD](https://github.com/tobi/qmd) provides on-device hybrid search (BM25 + vector semantic + LLM
reranking) for the memory system. Replaces SQLite FTS5. Indexes `.md` files in agent directories.

- Daemon: `qmd mcp --http --port 8181` (auto-started by `brain.py start`)
- Collections: one per agent (main, Reporter, Researcher)
- Index: `~/.cache/qmd/index.sqlite`
- Embedding model: `embeddinggemma` (328MB, runs locally)
- `brain.py start` auto-starts QMD; `brain.py stop` stops it
- Manual: `qmd status`, `qmd update`, `qmd embed`, `qmd query "search terms"`
- Collection management: `qmd collection add/list/remove/show`
