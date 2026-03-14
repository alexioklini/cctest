# Brain Agent

A multi-agent CLI platform for interacting with LLM APIs. Single-file Python implementation with zero external dependencies (stdlib only).

## Features

- **Multi-agent system** — multiple agents with individual personalities (`soul.md`), memory, and model preferences
- **11 built-in tools** — file ops, shell, search, web fetch, web search, memory, delegation
- **Persistent memory** — per-agent SQLite FTS5 keyword search + shared memory across agents
- **Agent delegation** — agents can send tasks to specialized agents in separate contexts
- **Context window management** — auto-compaction at 75% to prevent overflow
- **Interactive TUI** — spinner, markdown rendering, input history, escape to cancel
- **Dynamic rendering** — adapts to terminal width, no garbled output
- **Zero dependencies** — Python stdlib only, connects to any OpenAI-compatible or Anthropic API

## Quick Start

```bash
# Interactive mode with default agent
python3 claude_cli.py -i --base-url http://localhost:8317/v1 --api-key YOUR_KEY

# With a specific model and API type
python3 claude_cli.py -i -m model-name -t openai --base-url http://192.168.1.221:8081/v1

# Start as a specific agent
python3 claude_cli.py -i --agent research

# Single message mode
python3 claude_cli.py "your message"

# List available models
python3 claude_cli.py -l
```

## Architecture

```
brain-agent/
  claude_cli.py       # Single-file implementation (~2800 lines)
  tools.md            # Global tool usage guide (injected into system prompt)
  agents/
    main/             # Default orchestrator agent
      soul.md         # Personality, role, instructions
      agent.json      # Config: description, model preference, max_context
      tools.md        # Per-agent tool overrides (optional)
      memory.db       # SQLite FTS5 memory index
      *.md            # Memory files (human-readable)
    research/         # Example specialized agent
      soul.md
      agent.json
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
| `delegate_task` | Send a task to another agent |

## Multi-Agent System

Each agent has:
- **`soul.md`** — defines personality, role, capabilities, guidelines
- **`agent.json`** — metadata: description, preferred model, max context
- **Own memory** — isolated SQLite FTS5 store
- **Shared memory** — all agents can read/write the main agent's memory

Agents are aware of each other via the **agent registry** injected into every system prompt. Each agent sees the capabilities of all other agents and can decide whether to handle a task or delegate it.

### Commands

| Command | Description |
|---|---|
| `/agent` | List agents, switch active agent |
| `/agent <name>` | Switch to (or create) an agent |
| `/new` | Start a new conversation |
| `/model` | Switch model |
| `/models` | List available models |
| `/tools` | Toggle tool call display (press `o` to reveal hidden) |
| `Esc` | Cancel current request |
| `exit` | Quit |

### Keyboard

- **Up/Down** — input history
- **Left/Right** — cursor movement
- **Ctrl+A/E** — beginning/end of line
- **Ctrl+W** — delete word backward
- **Ctrl+U** — clear line

## Context Window Management

- Estimates tokens client-side (~4 chars/token)
- Status bar shows usage: `467/131k`
- Auto-compacts at 75% by summarizing older messages
- Configurable via `--max-context`

## Configuration

### CLI Flags

```
--base-url    API endpoint (default: http://localhost:8317/v1)
--api-key     API key for authentication
-m, --model   Model to use
-t, --api-type  anthropic or openai (default: anthropic)
--agent       Agent to start with (default: main)
--max-context Max context tokens (default: 131072)
-i            Interactive mode
-l            List models
```

### Per-Agent Config (`agent.json`)

```json
{
  "description": "Research and analysis specialist",
  "model": "qwen-coder-48b",
  "max_context": 32768
}
```

## Changelog

| Version | Date | Changes |
|---|---|---|
| 0.8.0 | 2026-03-14 | Multi-agent system with soul.md, delegation, shared memory |
| 0.7.0 | 2026-03-14 | Persistent memory with SQLite FTS5, per-agent isolation |
| 0.6.0 | 2026-03-14 | Context window management with auto-compaction |
| 0.5.0 | 2026-03-14 | Full agent toolkit: file ops, shell, search, web fetch |
| 0.4.0 | 2026-03-14 | Escape to cancel, dynamic rendering, startup greeting |
| 0.3.0 | 2026-03-13 | Exa web search tool with agentic tool-use loop |
| 0.2.0 | 2026-03-12 | Interactive TUI with spinner, markdown, model switching |
| 0.1.0 | 2026-03-10 | Initial release — streaming chat, model fallback |
