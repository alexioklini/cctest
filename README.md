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
                │  │Memory    │ │──▶ MCP servers
                │  │MCP       │ │──▶ Gmail, Exa, tools
                │  │Sessions  │ │
                │  └──────────┘ │
                └───────────────┘
```

## Features

- **Multi-agent system** — agents with individual personalities (`soul.md`), avatars, memory, model preferences
- **20+ built-in tools** — file ops, shell, search, web fetch, Gmail, memory, delegation, scheduling
- **Web UI** — professional dark/light theme, agent cards, chat history, skill browser, scheduler management
- **Telegram bot** — streaming responses, HTML formatting, per-chat sessions
- **TUI** — Rich + prompt_toolkit, Claude Code-style interface
- **Skills system** — on-demand SKILL.md loading, browse/install from ClawHub (7000+ skills), URL/zip install
- **MCP support** — connect to MCP servers (stdio + SSE) for external tool integrations
- **Persistent chat history** — SQLite-backed, survives restarts, archive/restore sessions
- **Task scheduler** — timed/recurring execution with history, per-agent
- **Context window management** — auto-compaction at 75%, token tracking
- **Background processing** — async agent delegation, task status/cancel
- **Shared memory** — all agents can read/write main agent's memory
- **Provider management** — multiple LLM providers, live model discovery, test connections

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
  claude_cli.py         # Core engine: tools, agents, memory, MCP, scheduler
  config.json           # Provider config (not in git)
  config.example.json   # Config template
  tools.md              # Global tool usage guide
  web/
    index.html          # Web UI (single-page app)
  agents/
    main/               # Default orchestrator agent
      soul.md           # Personality, role, instructions
      agent.json        # Config: description, model, avatar, max_context
      mcp.json          # MCP server connections (global)
      gmail.json        # Gmail credentials (not in git)
      skills/           # Installed skills
        github/SKILL.md
        word-docx/SKILL.md
      memory.db         # SQLite FTS5 memory
      chats.db          # Chat history (sessions + messages)
      scheduler.db      # Scheduled tasks + history
    research/           # Example specialized agent
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
| `memory_recall` | Search agent's memory (BM25) |
| `memory_shared` | Read/write main agent's shared memory |
| `memory_delete` | Delete a memory |
| `delegate_task` | Delegate to another agent (sync/async) |
| `task_status` | Check background task status |
| `task_cancel` | Cancel running background task |
| `use_skill` | Load skill instructions on demand |
| `schedule_list` | List scheduled tasks |
| `schedule_history` | View execution history |
| `mcp_*` | Any tool from connected MCP servers |

## Web UI

Access at `http://127.0.0.1:8420/` after starting the server.

- **Agent cards** — always visible on top, click to chat or configure
- **Chat** — streaming responses, markdown rendering, syntax highlighting, thinking spinner
- **Chat history** — session bar with previous chats, archive/restore/delete
- **Agent config** — modal with tabs: Soul, Settings (avatar, model, display name), Skills, MCP, Schedule
- **Skill browser** — search 7000+ skills from ClawHub, install from URL or zip
- **Scheduler** — create/edit/pause/resume/delete tasks with user-friendly time picker
- **Settings** — add/edit/test/delete LLM providers
- **Light/dark theme** — toggle with sun/moon icon, saved to localStorage
- **Pixel art avatars** — retro adventure game style, 10 presets + custom upload

## Multi-Agent System

Each agent has:
- **`soul.md`** — personality, role, capabilities
- **`agent.json`** — config: display name, description, model, avatar, max_context
- **Own memory** — isolated SQLite FTS5 store
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
| POST | `/v1/restart` | Restart server |

## Changelog

| Version | Date | Changes |
|---|---|---|
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
