# Brain Agent

A multi-agent CLI platform for interacting with LLM APIs. Rich + prompt_toolkit TUI with a Claude Code-inspired interface.

## Features

- **Multi-agent system** — agents with individual personalities (`soul.md`), memory, and model preferences
- **16+ built-in tools** — file ops, shell, search, web fetch, memory, delegation, scheduling
- **MCP support** — connect to MCP servers (stdio + SSE) for external tool integrations
- **Skills system** — on-demand SKILL.md loading, per-agent + global (OpenClaw-compatible)
- **Persistent memory** — per-agent SQLite FTS5 keyword search + shared memory across agents
- **Agent delegation** — async background execution, task status/cancel
- **Task scheduler** — timed/recurring execution with history
- **Context window management** — auto-compaction at 75% to prevent overflow
- **Rich TUI** — markdown rendering, inline tool display, tab completion, arrow-key menus
- **Status bar** — agent, model, context usage always visible

## Quick Start

```bash
# Rich TUI (recommended)
python3 tui.py -i --base-url http://localhost:8081/v1 --api-key KEY -t openai -m MODEL

# Legacy TUI (raw ANSI, no dependencies)
python3 claude_cli.py -i --base-url http://localhost:8081/v1 --api-key KEY -t openai -m MODEL

# Start as a specific agent
python3 tui.py -i --agent research

# Single message mode
python3 tui.py "your message"

# List available models
python3 tui.py -l
```

### Dependencies

```bash
pip3 install rich prompt_toolkit   # for tui.py
# claude_cli.py has zero dependencies (stdlib only)
```

## Architecture

```
brain-agent/
  tui.py              # Rich + prompt_toolkit frontend
  claude_cli.py       # Backend: tools, agents, memory, scheduler, MCP, API
  tools.md            # Global tool usage guide (injected into system prompt)
  agents/
    main/             # Default orchestrator agent
      soul.md         # Personality, role, instructions
      agent.json      # Config: description, model, max_context
      tools.md        # Per-agent tool overrides (optional)
      mcp.json        # MCP server connections (global — available to all agents)
      skills/         # Global skills
        github/SKILL.md
      memory.db       # SQLite FTS5 memory index
      *.md            # Memory files (human-readable)
    research/         # Example specialized agent
      soul.md
      agent.json
      mcp.json        # Agent-specific MCP servers
      skills/         # Agent-specific skills
      memory.db
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
| `memory_store` | Save to agent's own memory |
| `memory_recall` | Search agent's own memory (BM25) |
| `memory_shared` | Read/write main agent's shared memory |
| `memory_delete` | Delete a memory |
| `delegate_task` | Delegate to another agent (sync or async) |
| `task_status` | Check background task status |
| `task_cancel` | Cancel a running background task |
| `use_skill` | Load a skill's instructions on demand |
| `schedule_list` | List scheduled tasks |
| `schedule_history` | View execution history |
| `mcp_*` | Any tool from connected MCP servers |

## MCP Support

Connect to external tool servers using the [Model Context Protocol](https://modelcontextprotocol.io/).

### Configuration (`mcp.json`)

```json
{
  "filesystem": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/alexander"]
  },
  "remote-search": {
    "transport": "sse",
    "url": "http://192.168.4.65:3000/mcp"
  }
}
```

### Hierarchy

- `agents/main/mcp.json` — **global**: loaded for all agents
- `agents/<agent>/mcp.json` — **agent-specific**: loaded on top of global

### Transports

| Transport | How it works | Use case |
|---|---|---|
| **stdio** | Spawns subprocess, JSON-RPC over stdin/stdout | Local servers (filesystem, git, sqlite) |
| **SSE/HTTP** | Connects to running server over HTTP | Remote servers, shared services |

MCP tools appear as `mcp_<server>_<tool>` and are called like any built-in tool.

## Multi-Agent System

Each agent has:
- **`soul.md`** — personality, role, capabilities, guidelines
- **`agent.json`** — config: description, preferred model, max context
- **Own memory** — isolated SQLite FTS5 store
- **Own skills** — agent-specific SKILL.md files
- **Own MCP servers** — agent-specific server connections
- **Shared memory** — all agents can read/write the main agent's memory

Agents see each other via the **agent registry** in the system prompt and can delegate tasks.

### Agent Config (`agent.json`)

```json
{
  "description": "Research and analysis specialist",
  "model": "qwen-coder-48b",
  "max_context": 32768
}
```

## Skills

On-demand knowledge loading using OpenClaw-compatible `SKILL.md` format:

```yaml
---
name: github
description: "Interact with GitHub using the gh CLI"
---
# Instructions and commands here...
```

- `agents/main/skills/` — global, available to all agents
- `agents/<agent>/skills/` — agent-specific, overrides globals

The model calls `use_skill("github")` to load instructions into context when needed.

## Scheduler

```
/schedule              — list all tasks
/schedule add          — create (interactive)
/schedule pause NAME   — pause
/schedule resume NAME  — resume
/schedule delete NAME  — remove
/schedule history      — execution log
```

Schedule formats: `every 5m`, `every 2h`, `daily 09:00`, `weekly mon 09:00`, `once 2026-03-20 14:00`

## Commands

| Command | Description |
|---|---|
| `/help` | Show help |
| `/new` | Start new conversation |
| `/agent [name]` | Switch agent (arrow-key menu) |
| `/model [name]` | Switch model |
| `/models` | List & select models |
| `/tools` | Toggle tool display |
| `/schedule` | Manage scheduled tasks |
| `Ctrl+C` | Cancel current request |
| `Ctrl+D` | Quit |
| `Tab` | Autocomplete commands |
| `↑ / ↓` | Input history |

## Context Window Management

- Client-side token estimation (~4 chars/token)
- Status bar shows: `2k/131k`
- Auto-compacts at 75% by summarizing older messages
- Configurable via `--max-context`

## CLI Flags

```
--base-url      API endpoint (default: http://localhost:8317/v1)
--api-key       API key
-m, --model     Model to use
-t, --api-type  anthropic or openai (default: anthropic)
--agent         Agent to start with (default: main)
--max-context   Max context tokens (default: 131072)
-i              Interactive mode
-l              List models
```

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.1.0 | 2026-03-14 | MCP support: stdio + SSE transports, per-agent + global servers |
| 1.0.0 | 2026-03-14 | Task scheduler, background threads per agent, async delegation |
| 0.9.0 | 2026-03-14 | Skills system: on-demand SKILL.md loading |
| 0.8.0 | 2026-03-14 | Multi-agent system with soul.md, delegation, shared memory |
| 0.7.0 | 2026-03-14 | Persistent memory with SQLite FTS5, per-agent isolation |
| 0.6.0 | 2026-03-14 | Context window management with auto-compaction |
| 0.5.0 | 2026-03-14 | Full agent toolkit: file ops, shell, search, web fetch |
| 0.4.0 | 2026-03-14 | Escape to cancel, dynamic rendering, startup greeting |
| 0.3.0 | 2026-03-13 | Exa web search tool with agentic tool-use loop |
| 0.2.0 | 2026-03-12 | Interactive TUI with spinner, markdown, model switching |
| 0.1.0 | 2026-03-10 | Initial release — streaming chat, model fallback |
