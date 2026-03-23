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
  │   ├── claude_cli.py (engine: 20+ tools, agents, memory, scheduler)
  │   ├── SQLite: chats.db, scheduler.db
  │   └── MCP server connections
  ├── qmd mcp --http (daemon on port 8181, hybrid memory search)
  │   └── Collections: one per agent → agents/<name>/*.md
  ├── tui.py (terminal client)
  ├── telegram.py (Telegram client)
  └── web/index.html (browser client)
```

### Multi-Provider Routing

The server supports multiple LLM providers (config.json). When a model is selected,
the server automatically routes to the correct provider based on which one has that model.
Provider types: `openai` (OpenAI-compatible) and `anthropic` (native Anthropic API).

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
- Providers without `/models` endpoint: manually-configured models from `_models_config` are included in provider listings
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
- Knowledge graph: auto-discovery (LLM-based, entity extraction, co-recall), graph-aware recall default (1 hop), visualization via Canvas 2D
- Model-aware max_tokens: Opus 32K, Sonnet 16K, Haiku 8K, MiniMax 32K, configurable via `max_output` in models config
- Provider fallback ordering: same provider first, then capabilities, then priority
- Chat file attachments: files created by agents (write_file/edit_file) appear as viewable/downloadable attachments
- `get_model_max_output(model)` returns max output tokens based on model family or config
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
- Context assembly: summaries (highest depth first) + fresh tail (default 32 messages) within token budget
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
Context: context_search, context_detail, context_recall (drill-back into compacted history)
Nodes: list_nodes (remote node status and info)
Schedule: schedule_list, schedule_history
MCP: mcp_* (dynamic, from connected MCP servers)

### Server API

Server runs on port 8420 (configurable). Key endpoints:
- `POST /v1/chat` — SSE streaming with keepalive
- `POST /v1/sessions` — auto-resolves provider from model
- `GET /v1/schedule/running` — live task monitoring
- `POST /v1/skills/browse` — searches 7000+ skills from ClawHub
- `GET /v1/services/qmd/docs` — list docs with index health (modified, embedded_at, current)
- `GET /v1/agents/activity` — active tasks/chats per agent
- `GET /v1/teams` — team structure (heads, members, standalone)
- `POST /v1/teams` — manage teams (create, update, dissolve, move)
- `GET|POST /v1/models/config` — model routing configuration
- `GET /v1/costs` — cost stats (agent, hours params)
- `GET /v1/costs/daily` — daily cost breakdown (agent, days params)
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
