---
title: "### QMD (Hybrid Memory Search)"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:26:44.658401+00:00"
chunk_index: 5
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-004.md
    type: prev_chunk
  - file: ingest-6ebdb6-000.md
    type: same_source
---

### QMD (Hybrid Memory Search)

y scoping:**
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
