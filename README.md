# Brain Agent

A multi-agent AI platform with CLI, Web UI, and Telegram frontends. Client-server architecture with persistent chat history, scheduled tasks, skill ecosystem, and MCP support.

## Architecture

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Web UI    │  │    TUI      │  │  Telegram   │
│  (browser)  │  │  (terminal) │  │   (bot)     │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        │ HTTP/SSE
                ┌───────┴───────┐
                │   server.py   │  ← always-running daemon
                │  ┌──────────┐ │
                │  │Scheduler │ │──▶ LLM API (oMLX / Claude)
                │  │Memory    │ │──▶ QMD (hybrid search, port 8181)
                │  │          │ │──▶ MCP servers
                │  │MCP       │ │──▶ Gmail, Exa, tools
                │  │Sessions  │ │
                │  └──────────┘ │
                └───────────────┘
```

## Features

### Core
- **Multi-agent system** — agents with personalities (`soul.md`), avatars, teams, memory, model preferences
- **30+ built-in tools** — file ops, shell, search, web, Gmail, memory, delegation, scheduling, MCP
- **Projects** — per-agent scoped workspaces with documents, watched folders, and chat scoping
- **Agent workflows** — YAML-defined multi-step pipelines with approval gates and variable substitution
- **Custom slash commands** — user-defined prompt templates with `{{variable}}` interpolation

### Frontends
- **Web UI** — sidebar with Projects + Chats sections, slash command popup, plan mode, image upload, knowledge map
- **TUI** — Rich + prompt_toolkit, 50+ slash commands, autocomplete
- **Multi-messaging** — adapter framework for Telegram + future Discord/Slack channels
- **Remote nodes** — lightweight agents on remote machines with centralized management, launchd install, settings UI

### Intelligence
- **Knowledge graph memory** — QMD hybrid search (BM25 + vector + LLM reranking) with relationship traversal and auto-discovery
- **Auto memory** — automatic memory creation from conversations (corrections, decisions, preferences) via background LLM extraction
- **Continuous summarization** — memory summary refreshes at token thresholds during active conversations
- **Knowledge graph visualization** — interactive force-directed canvas with search, filtering, relationship discovery
- **Project notes** — markdown notes with AI-assisted editing (uses write_file/edit_file tools), folder organization, auto-reload
- **Document ingestion (RAG)** — PDF, DOCX, HTML, URL parsing with auto-chunking and watched folders
- **Chat transcript indexing** — conversations indexed in QMD for semantic search across all chat history
- **LLM chat summaries** — auto-generated one-line summaries for sidebar display
- **Plan mode** — read-only analysis that disables write tools
- **LLM input refinement** — context-aware prompt improvement before sending
- **Multi-modal** — image upload with vision model support
- **Chat file attachments** — files created by agents appear as viewable/downloadable attachments in chat and sidebar

### Infrastructure
- **Multi-provider routing** — auto-routing across Anthropic, OpenAI-compatible, MiniMax, local oMLX
- **Provider fallback** — exponential backoff retry with ordered fallback chains
- **Cost tracking + Rate limiting** — per-agent spend monitoring, budgets, throttling
- **Observability** — span-based tracing for LLM calls and tool execution
- **Audit trail** — append-only log of all agent actions, searchable, CSV export
- **Notifications** — webhook, email (SMTP), in-app alerts for task events
- **Backup / export / import** — portable archives for migration
- **Web result caching** — LRU cache for web_fetch/exa_search with TTL
- **Streaming tool output** — real-time stdout/stderr during command execution

## Quick Start

```bash
# 1. Configure providers
cp config.example.json config.json
# Edit config.json with your LLM provider URL, API key, model

# 2. Start server + open web UI
python3 brain.py start
open http://127.0.0.1:8420

# Or use the TUI
python3 brain.py tui

# Or Telegram bot
python3 brain.py telegram

# Other commands
python3 brain.py status      # server health
python3 brain.py stop        # stop server
python3 brain.py restart     # restart server
python3 brain.py config      # show config
python3 brain.py providers   # list providers + models
```

### Dependencies

```bash
pip3 install rich prompt_toolkit   # for tui.py
# server.py and claude_cli.py use stdlib only
```

## File Structure

```
brain-agent/
  brain.py              # Gateway CLI: start/stop/restart, launch frontends
  server.py             # HTTP API server (daemon)
  client.py             # Shared HTTP/SSE client library
  tui.py                # Terminal frontend (Rich + prompt_toolkit)
  telegram.py           # Telegram bot frontend
  adapters.py           # Multi-messaging adapter framework (Telegram, Discord, Slack)
  notifications.py      # Notification manager (webhook, email, in-app)
  node.py               # Remote node agent (runs on remote machines)
  claude_cli.py         # Core engine: tools, agents, memory, MCP, scheduler
  config.json           # Provider config (not in git)
  config.example.json   # Config template
  tools.md              # Global tool usage guide
  web/
    index.html          # Web UI (single-page app)
  agents/
    main/               # Default orchestrator agent
      soul.md           # Personality, role, instructions
      agent.json        # Config: description, model, avatar, max_context, rate_limits
      commands.json     # Custom slash commands
      mcp.json          # MCP server connections (global)
      gmail.json        # Gmail credentials (not in git)
      skills/           # Installed skills
      workflows/        # YAML workflow definitions
      projects/         # Per-project scoped workspaces
      *.md              # Memory files (indexed by QMD)
      chats.db          # Chat history
      scheduler.db      # Scheduled tasks
      costs.db          # Cost tracking
      traces.db         # Observability traces
      audit.db          # Audit trail
    Researcher/         # Example specialized agent
      soul.md
      agent.json
      skills/
```

## Tools

| Tool | Description |
|---|---|
| `read_file` | Read file contents with optional line range |
| `write_file` | Create or overwrite files |
| `edit_file` | Search/replace editing in files |
| `list_directory` | List files/dirs with glob patterns |
| `search_files` | Regex search across files |
| `execute_command` | Run shell commands (non-interactive) |
| `web_fetch` | Fetch URL content |
| `exa_search` | Web search via Exa AI |
| `gmail_inbox` | List recent emails from Gmail |
| `gmail_read` | Read email by ID (body, attachments) |
| `gmail_search` | Search with Gmail syntax |
| `gmail_send` | Send email |
| `gmail_reply` | Reply preserving threading |
| `memory_store` | Save to agent's memory |
| `memory_recall` | Search agent's memory (QMD hybrid search) |
| `memory_shared` | Read/write main agent's shared memory |
| `memory_delete` | Delete a memory |
| `delegate_task` | Delegate to another agent (sync/async) |
| `task_status` | Check background task status |
| `task_cancel` | Cancel running background task |
| `use_skill` | Load skill instructions on demand |
| `list_nodes` | List registered remote nodes with status and resource usage |
| `schedule_list` | List scheduled tasks |
| `schedule_history` | View execution history |
| `mcp_*` | Any tool from connected MCP servers |
| `mcp_connect` | Connect to MCP server at runtime |
| `mcp_disconnect` | Disconnect runtime MCP server |
| `mcp_servers` | List all MCP connections and tools |

## Web UI

Access at `http://127.0.0.1:8420/` after starting the server.

- **Collapsible sidebar** — agent list, sessions, quick actions; expand/collapse with Ctrl+B
- **Chat** — streaming responses, markdown, image upload (drag-and-drop, paste), plan mode toggle
- **Slash command popup** — type `/` for autocomplete menu with built-in + custom commands
- **Project tabs** — switch between project-scoped contexts per agent
- **Agent config** — modal with tabs: Soul, Settings, Skills, MCP, Schedule, Projects, Workflows, Commands, Memory
- **Settings dashboard** — vertical nav with grouped sections: System, Agents, Monitoring, Data
- **Workflow runner** — stage pipeline visualization with approval gates
- **Notifications** — bell icon with badge, dropdown for recent alerts
- **Streaming tool output** — live terminal panel for command execution
- **Cost display** — per-session and per-message cost in status bar
- **Light/dark theme** — toggle with sun/moon icon, saved to localStorage
- **Mobile responsive** — sidebar as overlay drawer on small screens

## Multi-Agent System

Each agent has:
- **`soul.md`** — personality, role, capabilities
- **`agent.json`** — config: display name, description, model, avatar, max_context
- **Own memory** — QMD-indexed markdown files with hybrid search (BM25 + vector + reranking)
- **Own skills** — agent-specific + inherits main's global skills
- **Own MCP servers** — agent-specific + inherits main's global servers
- **Shared memory** — all agents can read/write main's memory

Agents see each other via the **agent registry** and can delegate tasks. Pause/delete agents (except main). Custom display names and pixel art avatars.

## Skills

OpenClaw-compatible `SKILL.md` format. Three ways to install:

1. **Search** — browse 7000+ skills from ClawHub repository
2. **URL** — paste `https://clawhub.ai/author/skill-name`
3. **Zip** — upload a `.zip` containing `SKILL.md`

Skills are loaded on-demand via `use_skill("slug")` to keep the system prompt lean.

## Gmail Integration

```bash
# 1. Enable 2FA on Google account
# 2. Create App Password: https://myaccount.google.com/apppasswords
# 3. Create config:
cat > agents/main/gmail.json << 'EOF'
{"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}
EOF
```

Then: "Show my last 5 emails", "Search for emails from john@example.com", "Send email to jane about the meeting"

## MCP Support

```json
// agents/main/mcp.json (global) or agents/<agent>/mcp.json
{
  "filesystem": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
  },
  "remote": {
    "transport": "sse",
    "url": "http://host:3000/mcp"
  }
}
```

## Scheduler

Create via Web UI or TUI (`/schedule add`):
- **Every X minutes/hours** — `every 5m`, `every 2h`
- **Daily at time** — `daily 15:30`
- **Weekly** — `weekly mon 09:00`
- **Once** — `once 2026-03-20 14:00`

Each task runs with a specified agent and model in its own context. Results stored in history.

## Configuration

### `config.json`

```json
{
  "server": {"host": "0.0.0.0", "port": 8420},
  "providers": {
    "omlx": {
      "base_url": "http://127.0.0.1:8000/v1",
      "api_key": "",
      "type": "openai",
      "default_model": "Crow-4B-Opus-4.6-Distill"
    },
    "claude": {
      "base_url": "http://127.0.0.1:8317/v1",
      "api_key": "brain-agent",
      "type": "anthropic",
      "default_model": "claude-opus-4-6"
    }
  },
  "default_provider": "omlx",
  "max_context": 131072,
  "telegram": {"bot_token": "...", "allowed_users": [123456]}
}
```

**Multi-provider routing:** The server automatically routes API calls to the correct provider based on which model is selected. No manual switching needed — select `claude-opus-4-6` and it routes to Claude via CLIProxyAPI, select `Crow-4B-Opus-4.6-Distill` and it routes to the local oMLX server.

**oMLX (Local MLX Inference):** A local [oMLX](https://github.com/jundot/omlx) instance on port 8000 serves quantized MLX models on Apple Silicon. Models are stored in `~/.omlx/models/` and auto-discovered. Install: `brew install jundot/omlx/omlx`, start: `brew services start omlx`. Admin dashboard at `http://127.0.0.1:8000/admin`.

**CLIProxyAPI (Claude OAuth Proxy):** A local [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) instance on port 8317 provides Claude models via OAuth — no API key costs. Also serves Gemini and Qwen models from stored OAuth tokens. Install: `brew install cliproxyapi`, login: `cliproxyapi -claude-login`, start: `brew services start cliproxyapi`. Management panel at `http://127.0.0.1:8317/`.
```

### `agent.json`

```json
{
  "display_name": "Research Assistant",
  "description": "Deep research and analysis",
  "model": "model-name",
  "avatar": "scientist",
  "max_context": 32768
}
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/status` | Server health |
| GET | `/v1/agents` | List agents |
| GET | `/v1/models` | List models |
| GET | `/v1/providers` | List providers + models |
| GET | `/v1/sessions?agent=X` | List chat sessions |
| GET | `/v1/sessions/<id>/messages` | Load session messages |
| GET | `/v1/schedule` | Scheduled tasks |
| GET | `/v1/tasks` | Background tasks |
| POST | `/v1/sessions` | Create session |
| POST | `/v1/sessions/manage` | Archive/unarchive/clear/delete |
| POST | `/v1/chat` | Send message (SSE stream) |
| POST | `/v1/chat/cancel` | Cancel request |
| POST | `/v1/agents/switch` | Switch agent |
| POST | `/v1/agents/create` | Create agent |
| POST | `/v1/agents/delete` | Delete agent (soft) |
| POST | `/v1/schedule` | Manage schedules |
| POST | `/v1/providers` | Add/edit/delete providers |
| POST | `/v1/providers/test` | Test provider connection |
| POST | `/v1/skills/browse` | Search skills repository |
| POST | `/v1/skills/install` | Install from ClawHub |
| POST | `/v1/skills/install-zip` | Install from zip |
| POST | `/v1/skills/remove` | Remove skill |
| GET | `/v1/agents/activity` | Active agent tasks/chats |
| GET | `/v1/models/config` | Model routing configuration |
| POST | `/v1/models/config` | Save model routing config |
| GET | `/v1/services/qmd/docs` | List/read QMD indexed documents with index health |
| POST | `/v1/services/qmd/docs` | Save document (auto-triggers reindex) |
| DELETE | `/v1/services/qmd/docs` | Delete document |
| POST | `/v1/agents/rename` | Rename agent |
| POST | `/v1/restart` | Restart server |
| GET/POST | `/v1/agents/{id}/projects` | Project CRUD |
| POST | `/v1/agents/{id}/ingest` | Document ingestion |
| GET/POST | `/v1/agents/{id}/workflows` | Workflow definitions |
| POST | `/v1/agents/{id}/workflows/{name}/run` | Run workflow |
| GET | `/v1/workflows/executions` | Workflow execution status |
| GET/POST | `/v1/agents/{id}/commands` | Custom slash commands |
| GET | `/v1/costs` | Cost tracking stats |
| GET | `/v1/traces` | Observability traces |
| GET | `/v1/audit` | Audit trail |
| GET | `/v1/notifications` | In-app notifications |
| GET | `/v1/cache/stats` | Web result cache stats |
| POST | `/v1/refine` | LLM input refinement |
| POST | `/v1/backup` | Create backup archive |
| POST | `/v1/restore` | Restore from backup |
| GET/POST | `/v1/channels` | Multi-messaging channels |
| GET/POST | `/v1/nodes` | Remote node management |
| GET/POST | `/v1/mcp/connections` | Dynamic MCP connections |

## Changelog

| Version | Date | Changes |
|---|---|---|
| 3.4.0 | 2026-03-22 | Remote nodes: list_nodes tool, node settings UI (token, tools, concurrency, timeout), node.py launchd install/uninstall/status, connection logging, dynamic sidebar refresh on async summary |
| 3.3.0 | 2026-03-22 | Project notes with AI editing via tools, chat transcript QMD indexing, LLM chat summaries, deep search in sidebar, project panel search + counts + auto-refresh, chat attachments in sidebar, prompt refinement in notes |
| 3.2.0 | 2026-03-22 | Project Notes system, 3-column layout (sidebar + center + project panel), note editor with formatting toolbar and AI chat, notes in knowledge graph |
| 3.1.0 | 2026-03-21 | Auto memory creation, continuous session summarization, knowledge graph visualization + auto-discovery, chat file attachments, model-aware max_tokens, sidebar redesign (Projects + Chats), Tools settings, improved fallback ordering |
| 3.0.0 | 2026-03-20 | Provider fallback, backup/export, notifications, observability + audit trail, dynamic MCP client, multi-modal (vision), remote nodes, multi-messaging adapter framework |
| 2.1.0 | 2026-03-20 | Agent workflows (YAML stages + approval gates), Web UI sidebar layout, mobile responsive |
| 2.0.0 | 2026-03-20 | Projects, document ingestion (RAG), watched folders, knowledge graph memory, chat scoping |
| 1.7.0 | 2026-03-20 | Plan mode, web caching, streaming tool output, cost tracking + rate limiting, custom slash commands, LLM refinement |
| 1.6.0 | 2026-03-20 | TUI feature parity (50+ slash commands), slash command popup menus in TUI and Web UI |
| 1.5.3 | 2026-03-20 | Thread-safe agent context, fix old chat provider resolution, per-collection QMD debounce, YAML-safe frontmatter, concurrent scheduler |
| 1.5.2 | 2026-03-20 | Fix memory summary refresh (direct execution), fix QMD index path normalization, QMD collection health stats in settings |
| 1.5.1 | 2026-03-18 | MiniMax provider, Add Model UI, QMD session leak fix, in-process Telegram, lightweight QMD health check |
| 1.5.0 | 2026-03-18 | Settings dashboard (Server/QMD/Models/Telegram/Providers), agent activity indicators, QMD document browser with index health, smart model routing |
| 1.4.0 | 2026-03-17 | QMD hybrid memory search (BM25 + vector + LLM reranking), SSE error handling, server resilience |
| 1.3.0 | 2026-03-16 | oMLX local inference with Crow-4B-Opus-4.6-Distill model, replaces distributed inferencer |
| 1.2.1 | 2026-03-16 | Local CLIProxyAPI OAuth proxy for Claude models (no API key costs) |
| 1.2.0 | 2026-03-16 | Multi-provider routing, scheduler dashboard, Gmail tools, SQLite resilience, Cloudflare deployment |
| 1.1.0 | 2026-03-15 | Web UI, chat history, skill browser, avatars, light/dark theme |
| 1.1.0 | 2026-03-14 | MCP support: stdio + SSE transports, per-agent + global servers |
| 1.0.0 | 2026-03-14 | Client-server architecture, Telegram bot, background tasks, scheduler |
| 0.9.0 | 2026-03-14 | Skills system: on-demand SKILL.md loading |
| 0.8.0 | 2026-03-14 | Multi-agent system with soul.md, delegation, shared memory |
| 0.7.0 | 2026-03-14 | Persistent memory with SQLite FTS5, per-agent isolation |
| 0.6.0 | 2026-03-14 | Context window management with auto-compaction |
| 0.5.0 | 2026-03-14 | Full agent toolkit: file ops, shell, search, web fetch |
| 0.4.0 | 2026-03-14 | TUI with spinner, markdown rendering, input history |
| 0.3.0 | 2026-03-13 | Exa web search tool with agentic tool-use loop |
| 0.2.0 | 2026-03-12 | Interactive TUI with model switching |
| 0.1.0 | 2026-03-10 | Initial release — streaming chat, model fallback |
