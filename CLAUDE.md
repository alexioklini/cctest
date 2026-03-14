# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Repository Structure

- **`claude_cli.py`** — Brain Agent: multi-agent CLI platform (single file, stdlib only)
- **`tools.md`** — Global tool usage guide (loaded into system prompt at runtime)
- **`agents/`** — Per-agent directories with soul.md, agent.json, tools.md, memory.db
- **`inferencer_tools/`** — External tool implementations (exa_search)
- **`ClaudeChat/`** — Native macOS SwiftUI chat app
- **`ClaudeChatElectron/`** — Cross-platform Electron chat app

## Brain Agent (`claude_cli.py`)

Multi-agent agentic CLI. Single file, zero external dependencies (Python stdlib only).

### Running

```bash
python3 claude_cli.py -i                                    # Interactive, default agent
python3 claude_cli.py -i --agent research                   # Specific agent
python3 claude_cli.py -i -t openai --base-url http://host:port/v1 --api-key KEY -m model
python3 claude_cli.py "message"                             # Single message
python3 claude_cli.py -l                                    # List models
```

### Key Architecture

- **Agentic tool loop**: Model calls tools → execute → return results → model continues
- **Tools**: read_file, write_file, edit_file, list_directory, search_files, execute_command, web_fetch, exa_search, memory_store, memory_recall, memory_shared, memory_delete, delegate_task
- **Multi-agent**: Each agent has soul.md (personality), agent.json (config/model), own memory.db
- **Shared memory**: All agents can read/write main agent's memory via memory_shared
- **Agent registry**: All agents see each other's capabilities in their system prompt
- **Context management**: Client-side token estimation, auto-compaction at 75%
- **`tools.md`**: Loaded at runtime into system prompt — edit to teach agent new patterns without code changes

### Important Patterns

- `tools.md` and `soul.md` are injected into the system prompt — they are the primary way to control agent behavior
- `execute_command` runs with no TTY, no stdin, TERM=dumb — interactive commands will timeout
- `EscapeWatcher` uses manual termios (not tty.setraw) to preserve OPOST for correct output rendering
- `_box_top/_box_mid/_box_bot` handle ANSI-aware truncation for terminal-width-adaptive display
- Token estimation uses ~4 chars/token heuristic (no tokenizer dependency)

### Agent Directory Structure

```
agents/<name>/
  soul.md         # Personality, role, instructions
  agent.json      # {"description": "...", "model": null, "max_context": null}
  tools.md        # Optional per-agent tool guide
  memory.db       # SQLite FTS5 index (auto-created)
  *.md            # Memory files
```
