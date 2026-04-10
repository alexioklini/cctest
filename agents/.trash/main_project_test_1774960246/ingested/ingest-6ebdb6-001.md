---
title: "### Key Patterns"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:26:44.658401+00:00"
chunk_index: 1
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-000.md
    type: prev_chunk
  - file: ingest-6ebdb6-002.md
    type: next_chunk
  - file: ingest-6ebdb6-000.md
    type: same_source
---

### Key Patterns

Artifact cards**: in chat messages, artifact files show with coral border and monitor icon, click opens panel instead of preview modal
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
- Opt out per agent: `"agent_sdk": {"enabled": false}` in agent.json falls back to custom loop
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
Provider types: `openai` (OpenAI-compatible) and `anthropic` (native Anthropic API).

### Key Patterns
