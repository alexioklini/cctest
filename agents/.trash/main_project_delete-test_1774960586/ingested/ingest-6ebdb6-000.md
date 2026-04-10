---
title: "### Agent SDK (Agentic Loop)"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:33:04.549287+00:00"
chunk_index: 0
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-001.md
    type: next_chunk
---

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
  ├── sdk_sidecar.py (REST API on port 8421, Agent SDK)
  │   ├── POST /query → start query, returns query_id
  │   ├── GET /events/{id}?after=N → poll for new events
  │   └── Runs claude_agent_sdk.query() in a clean process (no claude_cli import)
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
- **Projects view** (`#projects-view`): project management
- **Graph view** (`#graph-view`): knowledge graph visualization
- **Settings/Config modals** (`.modal-overlay`): standard overlays that stack on top

Key patterns:
- Claude.ai design system: Anthropic Sans/Serif/Mono fonts, warm light theme, dark theme toggle
- CSS custom properties (`--bg-*`, `--text-*`, `--accent-*`) for consistent theming across light/dark
- `navigateTo(view)` switches between views by toggling `display:none`
- Tool call blocks: collapsible `div.tool-block` with gear icon, tool name, chevron, expandable args body
- Tool calls persisted in assistant message metadata (`metadata.tools`), reconstructed on session restore via `openSession()`
- Tool display toggle (`state.showToolCalls`) hides/shows tool blocks, persisted to localStorage
- Agent quick-switch buttons below composer on welcome view
- Streaming uses raw socket SSE for unbuffered token-by-token display
- `renderStreamingMessage()` updates in-place during streaming; `renderMessages()` for full re-render
- Artifact panel: resizable right panel (`#artifact-panel`) for viewing generated files with type-aware rendering

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
