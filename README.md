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
                │   server.py   │
                │  ┌──────────┐ │
                │  │Scheduler │ │  server mode: server ──▶ LLM providers
                │  │/mcp (MCP)│ │  client mode: browser ◀─proxy─▶ LLM providers
                │  │Hooks     │ │
                │  │Sessions  │ │
                │  └──────────┘ │
                └───────┬───────┘
                        │──▶ MemPalace (in-process, auto-mined)
                        └──▶ other MCP servers, Gmail, Exa, tools
```

## Features

### Core
- **Multi-agent system** — agents with personalities (`soul.md`), avatars, teams, model preferences
- **30+ built-in tools** — file ops, shell, search, web, Gmail, delegation, scheduling, memory, MCP
- **MemPalace memory (direct)** — `mempalace_query` tool searches long-term memory in-process (no MCP subprocess). Background daemons auto-mine source code, artifacts, chat history, web references, and attachment metadata into MemPalace drawers with closet index rebuilds
- **Projects** — per-agent scoped workspaces with documents, watched folders, and chat scoping
- **Agent workflows** — YAML-defined multi-step pipelines with approval gates and variable substitution
- **Custom slash commands** — user-defined prompt templates with `{{variable}}` interpolation

### Frontends
- **Desktop app** — Electron shell (macOS + Windows) wrapping the web UI. CORS-free `web_fetch`, `exa_search`, and LLM proxy streaming via Node.js IPC — solves the browser CORS limitation in client execution mode for air-gapped servers
- **Web UI** — Claude.ai-style sidebar layout with multi-view navigation (Chat, Chats, Projects, Artifacts, Scheduled, Customize), light/dark themes with Anthropic fonts
- **TUI** — Rich + prompt_toolkit, 50+ slash commands, autocomplete
- **Multi-messaging** — adapter framework for Telegram + future Discord/Slack channels
- **Remote nodes** — lightweight agents on remote machines with centralized management, launchd install, settings UI

### Intelligence
- **MemPalace memory** — single `mempalace_query` tool with hybrid BM25+vector+closet ranking. `mempalace-miner` daemon auto-mines configured source dirs every 30 min; `mempalace-chat-sync` daemon mirrors chat turns, summaries, references, and attachment metadata every 60s. No manual mining, no MCP subprocess
- **Project notes** — markdown notes with AI-assisted editing (uses write_file/edit_file tools), folder organization, auto-reload
- **Document ingestion (RAG)** — PDF, DOCX, HTML, URL parsing with auto-chunking and watched folders
- **LLM chat summaries** — auto-generated one-line summaries for sidebar display
- **Caveman mode (dual)** — two independent compression levels (0-3): system-level (per-model, compresses system prompt text) and chat-level (per-session toggle, controls response verbosity). Chat mode persists across sessions
- **Plan mode** — read-only analysis that disables write tools
- **LLM input refinement** — context-aware prompt improvement before sending
- **Multi-modal** — image upload with vision model support
- **Chat file attachments** — files created by agents appear as viewable/downloadable attachments in chat and sidebar
- **Scheduled-task attachments + working directory** — per-task file attachments (uploaded once, referenced in place on every fire — no per-run copy) and an optional working directory the agent passes as `cwd` to shell tools. Server-side folder picker (modal with breadcrumb + subfolder list, defaults to `$HOME`). Cleanup is automatic: deleting a schedule rmtrees its per-upload folders; removing an attachment chip in the edit modal purges the orphaned file. `python_exec` stays pinned to the artifact folder by design

### Infrastructure
- **GDPR / PII scanner + granular category policies + local-model routing** — every outgoing chat message, attachment, and chat-history scan is checked against 71 offline regex detectors (national IDs with real checksums, cloud secrets, context-fallback heuristics), grouped into 8 semantic categories (secrets, national_id, national_id_ctx, financial, contact, network, personal, bare_id). Each category takes an action — `ignore` (skip), `warn` (confirmation modal), `block` (refuse unless local model) — with per-rule overrides on top. Email allowlist suppresses trusted addresses or whole domains (`@company.com` pattern). When a block-severity finding fires on a cloud model, background calls (next-prompt suggestions, chat summary, memory classifier, worker summariser, scheduler delegates, agent-to-agent tasks) auto-route to `gdpr_scanner.default_local_fallback_model`; the composer UI reduces the model dropdown to local-only, auto-swaps the chat's model, and refuses cloud sends. Three audit action types (`pii_detected`, `pii_auto_fallback`, `pii_blocked`) give a full trail. Dedicated **Settings → GDPR** tab
- **Client execution mode** — for air-gapped servers where the browser has internet but the server doesn't. LLM calls and web tools (configurable) are proxied through the browser via SSE events; local tools run on the server. Desktop app recommended for CORS-free proxy execution. Configurable in Settings → Server → Execution Mode
- **Multi-provider routing** — OpenAI-compatible gateways (Bifrost local, Kilo cloud); single source of truth via `resolve_provider_for_model()`
- **Model warmup + warm session pool** — opt-in per model. Background keeper primes KV prefix (system + tools + first user token) against the provider so first-token latency drops from ~15s to 2-3s on prompt-cache-capable local runtimes (oMLX). Per-model `warmup_mode: full|minimal` trade-off (full primes KV prefix; minimal loads weights only). Pre-built session pool (depth 1-10) absorbs concurrent "new chat" claims. Status-bar pool indicator opens a modal with per-model state, mode chip, progress, last error, and "Warm now" button
- **Provider fallback** — exponential backoff retry with ordered fallback chains, message history rollback on mid-tool-loop failures, transient SSE error detection
- **Token optimization** — per-agent tool group filtering, built-in tool group deferral (rarely-used groups loaded on-demand via tool_search, saving ~1,760 tokens/request), per-agent MCP tool allow/deny patterns, MCP redundant-prefix stripping, system prompt caching (60s TTL), compact threshold override, scheduled task tool restriction, tools.md trimmed to essentials, context fill display (last-round prompt size) with manual compact button
- **Tool definition cost measurement** — `/v1/tools/breakdown` endpoint with per-group + per-tool schema decomposition (name/description/schema split), surfaced in agent settings → Tokens tab
- **Runtime limits** — per-agent `limits` block (max tool rounds, tool result size caps, context safety ratio); hard stop at 1.5× soft cap prevents runaway tool loops; pre-flight context guardrail rejects requests before they hit the provider
- **Cost tracking + Rate limiting** — per-agent spend monitoring, budgets, throttling; session cost soft warnings with amber/red thresholds and one-time modal at 90% of configurable global limit; built-in rate table for OpenAI/Mistral/Gemini/Grok/DeepSeek
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

# Or use the desktop app (recommended for air-gapped/client execution mode)
cd desktop && npm start
# Custom server: npm start -- --server=http://your-server:8420

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
  claude_cli.py         # Core engine: tools, agents, MCP, scheduler
  config.json           # Provider config (not in git)
  config.example.json   # Config template
  tools.md              # Global tool usage guide
  desktop/
    main.js             # Electron main process (IPC handlers, CORS-free fetch)
    preload.js          # contextBridge: window.electronAPI
    package.json        # Build config (mac + win)
  web/
    index.html          # Web UI (single-page app)
  agents/
    main/               # Default orchestrator agent
      soul.md           # Personality, role, instructions
      agent.json        # Config: description, model, avatar, rate_limits
      commands.json     # Custom slash commands
      mcp.json          # MCP server connections (includes MemPalace memory server)
      gmail.json        # Gmail credentials (not in git)
      skills/           # Installed skills
      workflows/        # YAML workflow definitions
      projects/         # Per-project scoped workspaces
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
| `read_document` | Format-aware reader (PDF/DOCX/XLSX/PPTX/CSV/images) |
| `write_document` | Create DOCX/XLSX/PPTX/PDF from markdown content |
| `edit_document` | Targeted edits to office documents |
| `code_graph_build` | Build AST-based code structure graph (14 languages) |
| `code_graph_query` | Query callers, callees, imports, tests, inheritors |
| `code_graph_impact` | Blast-radius analysis for changed files |
| `git_command` | Git operations (status, diff, log, branch, commit, stash, blame, tag) |
| `github_command` | GitHub via gh CLI (PRs, issues, repo, releases, workflows, API) |
| `web_fetch` | Fetch URL content |
| `exa_search` | Web search via Exa AI |
| `gmail_inbox` | List recent emails from Gmail |
| `gmail_read` | Read email by ID (body, attachments) |
| `gmail_search` | Search with Gmail syntax |
| `gmail_send` | Send email |
| `gmail_reply` | Reply preserving threading |
| `delegate_task` | Delegate to another agent (sync/async) |
| `task_status` | Check background task status |
| `task_cancel` | Cancel running background task |
| `use_skill` | Load skill instructions on demand |
| `context_search` | Search compacted conversation history by keyword |
| `context_detail` | Expand a summary to see original messages |
| `context_recall` | Deep recall from compacted history via sub-LLM |
| `list_nodes` | List registered remote nodes with status and resource usage |
| `schedule_list` | List scheduled tasks |
| `schedule_history` | View execution history |
| `mcp_*` | Any tool from connected MCP servers |
| `mcp_connect` | Connect to MCP server at runtime |
| `mcp_disconnect` | Disconnect runtime MCP server |
| `mcp_servers` | List all MCP connections and tools |

## Web UI

Access at `http://127.0.0.1:8420/` after starting the server.

- **Claude.ai-style layout** — collapsible sidebar with agent selector, nav links, recent sessions; multi-view main area
- **Chat** — streaming responses with tool call blocks (expandable args), markdown, image upload, plan mode toggle
- **Tool display** — collapsible tool blocks with full args, persisted across reloads, toggle to hide/show
- **Interactive agents** — agents can ask clarifying questions (AskUserQuestion) in TUI with selectable options
- **Chats browser** — searchable list with All/Archived tabs
- **Agent config** — modal with tabs: Soul (AI-assisted editing), Agent, Skills, Hooks, Schedule, MCP, Tokens
- **Settings dashboard** — vertical nav with grouped sections: System, Agents, Monitoring, Data
- **Cost display** — per-session and per-message cost in status bar
- **Light/dark theme** — Anthropic Sans/Serif/Mono fonts, warm palette, toggle saved to localStorage

## Multi-Agent System

Each agent has:
- **`soul.md`** — personality, role, capabilities
- **`agent.json`** — config: display name, description, model, avatar
- **Memory** — provided by the MemPalace MCP server wired per-agent in `mcp.json`
- **Own skills** — agent-specific + inherits main's global skills
- **Own MCP servers** — agent-specific + inherits main's global servers

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
    "kilo": {
      "base_url": "https://api.kilo.ai/api/gateway/v1",
      "api_key": "...",
      "type": "openai"
    },
    "omlx": {
      "base_url": "http://localhost:8000/v1",
      "api_key": "brain",
      "type": "openai"
    },
    "cliproxyapi": {
      "base_url": "http://localhost:8317/v1",
      "api_key": "brain-agent",
      "type": "openai"
    },
    "mistral-experimental": {
      "base_url": "https://api.mistral.ai/v1",
      "api_key": "...",
      "type": "openai"
    },
    "mistral-vibe": {
      "base_url": "https://api.mistral.ai/v1",
      "api_key": "...",
      "type": "openai"
    }
  },
  "default_provider": "kilo",
  "telegram": {"bot_token": "...", "allowed_users": [123456]},
  "warmup": {
    "enabled": true,
    "interval_seconds": 30,
    "max_concurrent": 1,
    "pool_depth": 3,
    "allow_cloud": false,
    "timeout_seconds": 30
  },
  "models": {
    "gemma-4-26b-a4b-it-4bit": {"warmup": true, "warmup_mode": "full"},
    "gemma-4-e2b-it-4bit":     {"warmup": true, "warmup_mode": "full"}
  }
}
```

**Warmup:** per-model `warmup: true` + `warmup_mode: "full"|"minimal"` opt-in. Full mode primes the KV prefix (system prompt + tools + first user token) so the user's first real turn reuses cached prefill → 2-3s first response on local models. Minimal mode only loads weights (~10-15s first response) when multiple models compete for GPU memory. The keeper daemon re-primes whenever state is idle/cold/failed or mode flips; a threading.Event wakes it immediately on config change. Warm session pool (depth 1-10) pre-builds session objects so `POST /v1/sessions` can hand one out without cold-start overhead. Status bar **Pool** indicator opens a modal with per-model state, progress, last error, and "Warm now" button. Starvation fix: failed primes bump `last_warmup_ts` so a perpetually OOM-ing model doesn't monopolize the keeper slot.

**Multi-provider routing:** `resolve_provider_for_model(model)` in `claude_cli.py` is the single source of truth for mapping a model ID to provider credentials. All paths (chat, delegate, scheduler, warmup) use it.

All providers are OpenAI-compatible (`/v1/chat/completions`). Brain connects to each upstream directly so reasoning payloads (nested Mistral `thinking[]`, oMLX `reasoning_content`) survive the wire:

- **oMLX** — local Apple-Silicon inference (`http://localhost:8000/v1`), for Gemma/Qwen/Crow etc. with reasoning via `chat_template_kwargs.enable_thinking`.
- **cliproxyapi** — local Gemini proxy (`http://localhost:8317/v1`) for Gemini 2.5 with `reasoning_effort`.
- **mistral-experimental** — Mistral API with the free experimental key (all models, rate limited).
- **mistral-vibe** — Mistral API with the paid Le Chat Vibe key (coding models, higher token limits).
- **kilo** — OpenAI-compatible cloud gateway for general use.

> Earlier releases (through 8.4.x) routed everything through a Bifrost gateway. Bifrost's response transformer silently drops unknown fields, so `reasoning_content` and nested `thinking[]` arrays never reached Brain. 8.5.0 splits Bifrost apart into the four upstreams above so reasoning text is preserved end-to-end.

### `agent.json`

```json
{
  "display_name": "Research Assistant",
  "description": "Deep research and analysis",
  "model": "model-name",
  "avatar": "scientist"
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
| GET | `/v1/mcp/registry` | MCP server templates |
| POST | `/v1/agents/{id}/soul-chat` | AI-assisted soul.md editing |
| POST | `/v1/chat/answer` | Answer agent's AskUserQuestion |

## Changelog

| Version | Date | Changes |
|---|---|---|
| 8.16.0 | 2026-04-25 | **Scheduled-task attachments + working directory**. Two pieces of context the agent previously had no way to receive on a scheduled run: file attachments (durable, picked once, referenced on every fire) and a per-task working directory (the cwd the agent should pass to `execute_command`). DB: new `schedules.attachments` (JSON list of `{name,path,mime,size}`) + `working_dir` columns via idempotent ALTER. Engine: `_execute_scheduled` appends a disk-files notice listing the stored absolute paths so the agent reads them in place — no per-run `/tmp` copy. `working_dir` overrides the `Current working directory:` line in the system prompt and adds a directive to pass it as `cwd=` to `execute_command`; `python_exec` stays pinned to the artifact folder by design (file-write tracking depends on it). Cleanup: `Scheduler.remove()` rmtrees each per-upload uuid folder under `agents/<agent>/scheduled_attachments/<uuid>/`; `Scheduler.update()` diffs old vs new attachment paths and purges orphans (chip-removal in the edit modal frees bytes). New `POST /v1/schedule/upload` (multipart, auth-gated) saves files and returns metadata. `/v1/schedule` `add`/`edit` accept `attachments[]` + `working_dir`. `GET /v1/files/tree` empty-path defaults to `$HOME` so the folder picker has somewhere to start. UI: create + edit modals get a multi-file picker (uploads on selection, removable chips) and a read-only working-dir input + **Browse…** button → stacked folder-picker modal (breadcrumb + subfolder list, ↑ .. to go up). Bug fix: `_schedUploadFiles` now sends `Authorization: Bearer <token>` and uses `BASE_URL` — bare `fetch()` was hitting the global `/v1/*` auth gate and 401-ing |
| 8.12.0 | 2026-04-24 | **Granular GDPR settings** — dedicated Settings → GDPR tab replaces the 4-checkbox card. Scanner rules are grouped into 8 semantic categories (secrets, national_id, national_id_ctx, financial, contact, network, personal, bare_id), each with a per-category action: `ignore` (skip rule entirely — no scan, no log), `warn` (confirmation modal), `block` (refuse unless local model active). Per-rule overrides (`rule_overrides: {rule_id: action}`) win over category actions. New **email allowlist** suppresses findings for trusted addresses (full or `@domain` patterns, case-insensitive). `PII_RULE_CATEGORIES` + `PII_DEFAULT_CATEGORY_ACTIONS` in `claude_cli.py` are the single source of truth, mirrored as `PIIScanner.ruleCategories`/`defaultCategoryActions` in `web/index.html`. New `_pii_effective_action()` resolves rule_overrides > category > default and downgrades `block` → `warn` when the `server_block` master switch is off (back-compat). New `_pii_worst_action()` returns `block > warn > ignore`; main-chat `send_message` and `gdpr_pick_model_for_background` now refuse only when the scan's worst action is `block` — so warn-only categories (emails, IPs) never raise `RuntimeError`. `POST /v1/services/server` validates input: unknown `rule_id` → 400, unknown category silently dropped, actions must be `ignore|warn|block`, allowlist entries must contain `@` with no whitespace. UI: three sections — master switches (enabled, server_log, server_block, fallback model), email allowlist textarea, collapsible category rows with inline action dropdowns and per-rule override dropdowns. Single Save button commits everything; Reset-to-defaults restores category actions without touching master switches or allowlist. Server tab now shows a one-line GDPR status chip with Configure → link. Client `applyGdprConfigToScanner(gs)` is the single entry point that syncs `PIIScanner.policy` + `state.pii*` from the server response. `piiBlockActive` gates on `scan.worstAction === 'block'` instead of any finding, so composer model-filtering only kicks in for true block-severity findings |
| 8.11.0 | 2026-04-24 | **Scheduled task run management + artifact role classification + cooler neutral themes**. Scheduled tasks view: each task card is now an inline `<details>` accordion that lazy-loads run history — no more two-modal-deep drilldown. Per-run actions: **Open** (loads the existing read-only chat view), **Details** (stats + tool timeline modal), **Delete** (purges the history row + every artifact it produced, files included; refuses on `status='running'`). Card-level **Clear all history** wipes every past run for a schedule while keeping the schedule itself. Readonly banner on the scheduled-run chat view enriched with a collapsible task-prompt block, model label, and an inline Delete-run button. **Artifact role classification**: new `role` column on `artifacts` (`output` default / `intermediate`) set at registration time by extension heuristic — `.py/.sh/.json/.csv/.log/.yaml/...` files are working/helper files; `.md/.html/.pdf/.docx/images` are deliverables. Filter chip in the artifacts browse grid (`Outputs only` / `Show working files`, outputs-only default) plus a dynamic filter in the right-side artifact list that only appears when the session has intermediates. Scheduled runs that previously buried the actual report under 4–6 debug JSONs now surface the deliverable by default. Backend: `Scheduler.delete_run(run_id)` + `delete_history(name)`, new `ChatDB.delete_artifacts_for_session()` helper, two new `/v1/scheduler` actions (`delete_run`, `clear_history`). UI theme: shifted both dark and light palettes away from the warm cream/brown cast (`#faf9f5` → `#fafafa`, `#1f1e1c` → `#1a1a1c`, etc.) to cool near-neutral greys matching Claude Code desktop; accent brand orange unchanged |
| 8.10.0 | 2026-04-23 | **GDPR local-model routing** (follow-up to 8.8.0's scanner). When a finding fires on a cloud model the chat is rerouted to a local model instead of being blocked. New `gdpr_scanner.default_local_fallback_model` config and Settings dropdown (local-only, server-validated). New `gdpr_pick_model_for_background()` is the single hook for non-interactive LLM calls (next-prompt, chat summary, memory classifier, worker summariser, scheduler + delegate + agent-to-agent tasks) — detect → audit (`pii_detected`) → swap (`pii_auto_fallback`) → refuse-with-sentinel (`pii_blocked`). `GDPRBlockedError` inherits `RuntimeError` so each caller skips cleanly. Client: `piiBlockActive(chat)` now scans draft AND loaded chat history (cached by message count, invalidated on open / user-push / stream-done / new-chat); model dropdown reduces to `is_local` entries with an amber header; auto-swap to the configured fallback the moment PII is seen; send refuses-with-toast when block is on and no local is selectable. Server-side `send_message` main-chat gate now checks `is_model_local(model)` before raising — fixes regression where selecting a local model from the restricted dropdown still got blocked. `GET /v1/models/config` exposes per-model `is_local` so the client doesn't duplicate URL parsing. `is_model_local(model)` is the authoritative resolver |
| 8.9.0 | 2026-04-23 | **Per-provider concurrency queue** for local LLM gateways (oMLX, CLIProxyAPI). Local runtimes can't actually process two `/chat/completions` in parallel — GPU serializes internally — so without coordination concurrent chats + delegates + warmup primes fought for the wire. `LocalProviderQueue` with per-provider semaphore + strict-FIFO waitlist, opt-in via `providers.<name>.max_concurrent` (0 = no queue; seeded oMLX=1, cliproxyapi=2). Wrapped every HTTP call site (`send_message`, `_run_delegate`, `run_model_warmup`, `classify_chat_for_memory`; next-prompt + tool summariser transitive). Slot held only during wire time — released before tool dispatch so local tool work doesn't block the gateway. New `GET /v1/queue/status`, admin-only `POST /v1/queue/cancel` (audit `queue_cancel`), SSE `queue_wait`/`queue_acquired`/`queue_released`. Status-bar Queue pill shows `N/M` or `N+W/M`; modal lists active + waiting tickets per provider with per-row Cancel for admins; per-turn inline banner in the streaming bubble |
| 8.8.0 | 2026-04-23 | **GDPR / PII pre-submit scanner** — 71 offline regex detectors in three tiers: (1) Tier 1 national IDs with real checksums (UK NINO+NHS mod-11, NL BSN 11-proef, BE mod-97, PL PESEL, SE personnummer Luhn, DK CPR, NO fødselsnummer, CH AHV EAN-13, RO CNP, IN Aadhaar Verhoeff, JP My Number, KR RRN, SG NRIC, US SSN, BR CPF+CNPJ, CA SIN, etc.); (2) Tier 2 cloud secrets (AWS, GitHub, Slack, Google, Stripe, OpenAI, Anthropic, Twilio, SendGrid, Mailgun, JWT, Azure, PEM private keys, basic-auth URLs, entropy-gated `api_key = "..."`); (3) Tier 3 context-fallback + bare-identifier heuristic — fire on keyword + number-shape even when checksum fails. Two mirrored implementations (JS `PIIScanner`, Python `_pii_rules/_pii_scan_text/_pii_scan_bare_identifiers`), 58/58 positive-fixture parity, 0 false positives on prose. Overlap suppression, credit-card runs after national IDs (13-digit Luhn collisions), context-gated rules (DE Steuer-ID, NL BSN) beat bare-digit rules of the same length. Pre-submit modal (640px amber-gradient banner, shield, redacted monospace samples, session suppression), composer inline badge, server-side mirror in `send_message` at `_tool_round == 0`, audit rows as `pii_detected`, optional hard-block mode. Settings card in Server tab |
| 8.6.0 | 2026-04-20 | **Warmup overhaul — first-response latency from 15s → 2-3s** on local models. Root cause was a silent KV-cache miss: warmup prime payload didn't match the real first-turn payload byte-for-byte, so oMLX's prompt cache never hit. Four drift sources fixed: (1) system prompt contained minute-precision timestamp that differed between warmup and real request — rounded to the hour in `_build_system_prompt` so prefixes stay byte-stable; (2) warmup payload omitted MCP tools that the real payload includes — now attaches the process-global MCPManager; (3) `stream` flag differed (False vs True) — now identical; (4) per-session `_trigger_warmup` was a divergent payload copy — deleted, replaced with a thin delegation to `engine.run_model_warmup` so both paths share one code path. `run_model_warmup` now bumps `last_warmup_ts` on failure so a perpetually OOM-failing model doesn't monopolize the `max_concurrent=1` keeper slot via oldest-first sort. New per-model `warmup_mode` config (`"full"` default / `"minimal"`): full primes KV prefix; minimal loads weights only — trade-off is the user's call, no auto-selection. Keeper re-primes when mode flips, won't evict warm models otherwise. Config-save wakes the keeper via `threading.Event` so new warmup flags take effect immediately instead of waiting up to 30s. Pool invalidation extended to any KV-prefix-relevant field change (warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id). New UI: status-bar **Pool** indicator with aggregate ready/target; click opens a modal listing each warmup-enabled model with state badge, progress bar, desired + actual mode chips (with `⟲` marker if re-prime pending), `last_warmup_ts` age, `last_error` box, and per-model "Warm now" button. Modal body live-refreshes via `WarmupMonitor._render()`. Models tab detail panel adds a Warmup Mode dropdown. Log format: `[warmup-keeper] <model>: warm (<mode>, <ms>ms)` |
| 8.5.0 | 2026-04-20 | **Thinking blocks + direct providers + loop/UI fixes**: per-model `thinking_format` (none / inline_tags / reasoning_field / mistral_blocks / openai_opaque) auto-detected from the model id; engine parses every non-opaque format from the stream and emits `thinking_start`/`delta`/`done`; each round of reasoning becomes its own `role='thinking'` message row so the transcript keeps chronological order (thinking → tool → thinking → tool → final answer) and `tool_round` is stamped onto every tool event so reload interleaves correctly. Opaque reasoning renders a "Thought for N tokens" badge from `usage.completion_tokens_details.reasoning_tokens`. Bifrost rip-and-replace: 4 direct providers (oMLX, cliproxyapi, mistral-experimental, mistral-vibe) so reasoning payloads aren't stripped by the gateway; 125 existing sessions keep working via scoped model ids + `base_model_id`. Loop safety: diminishing-returns guard stops plateauing models (last 2 rounds < 500 completion tokens each from round ≥ 3). Tool dedup is session-scoped (keyed by `session_id`, 1h TTL) so worker subagents and threadpool batches can no longer miss duplicate calls; parent thread-local context propagated into worker threads. UI fixes: tool_call/tool_result and every worker.* handler now re-append the `.msg-streaming` div so the thinking panel + partial text don't flicker out when tools fire; thinking panel renders live during streaming; tool-round badges removed from the thinking header. Worker-wrapped web tools (exa_search, web_fetch) attach extracted `references[]` to the envelope so the References panel repopulates |
| 8.1.0 | 2026-04-16 | **Desktop app (Electron)**: macOS + Windows builds wrapping the web UI. CORS-free `web_fetch`, `exa_search`, and LLM proxy streaming via Node.js IPC — fixes client execution mode for fully air-gapped servers where browser `fetch()` is CORS-blocked. `window.electronAPI` bridge with graceful fallback (web UI unchanged in regular browsers). Build: `cd desktop && npm run build:all` |
| 8.0.0 | 2026-04-15 | **MemPalace migration (C1–C10)**: memory moved fully to an external MCP server. Removed all built-in memory code paths: `memory_store`/`recall`/`shared`/`delete` tools, QMD hybrid search daemon and indexer, knowledge graph view + auto-discovery + relationship discovery, autodream consolidation, auto memory extraction, memory summary injection, continuous summarization, entity index, and all `/v1/agents/{id}/memories`, `/memory-health`, `/memory-summary`, `/graph*`, `/v1/services/qmd*` HTTP endpoints. Web UI Memory tab and Knowledge Graph view stripped. Old per-agent `*.md` memory files deleted from disk (soul.md preserved). Memory is now entirely agent-driven via MemPalace `mcp_*` tool calls |
| 7.1.0 | 2026-04-08 | Next-Prompt Suggestions (Claude Code-style composer ghost text) |
| 7.0.0 | 2026-04-02 | Native agentic loop restored; PI and Anthropic SDK sidecars removed |
| 5.3.0 | 2026-03-31 | Claude.ai-style web UI rewrite (sidebar + multi-view, Anthropic fonts, warm themes). Tool call blocks with full args persist across reloads. Interactive mode: agents ask questions via AskUserQuestion with TUI support. New endpoints: memory CRUD, soul.md AI editing, MCP registry. Sidecar captures input_json_delta for tool args |
| 5.2.0 | 2026-03-29 | Mission Control cockpit — dashboard-first UI with agent cards, cost feed, team badges, session cache |
| 5.1.0 | 2026-03-28 | Real-time streaming + Claude Code skills. Sidecar rewritten as REST API for true token-by-token streaming. MCP tools via /mcp JSON-RPC endpoint. Hooks moved server-side (SDK hooks caused buffering). Claude Code plugin GUI: browse, install, toggle 121 plugins per agent. SDK audit: @tool decorator, allowed_tools, correct hook signatures |
| 5.0.0 | 2026-03-28 | Full SDK migration — HTTP MCP server (24 tools), chat summaries, file watcher, rate limiting, model fallback, trace spans, audit logging, background tasks through SDK, TUI + CLI + scheduled tasks via sidecar with direct-API fallback |
| 4.2.0 | 2026-03-23 | Code graph: LLM node summaries, architecture layers, guided tours, code_graph_enhance tool. Lossless compaction with compacted flag, context fill indicator, manual compact, LCM footer |
| 4.1.0 | 2026-03-23 | Chat stability: session corruption fix, partial response preservation, metadata persistence, thinking level control, extended thinking, model display, remote node badges, resizable sidebars |
| 4.0.0 | 2026-03-23 | Universal File Intelligence (XLSX/PPTX/CSV/image/SVG, read/write/edit document tools) + Code Structure Graph (Tree-sitter AST, 14 languages, blast-radius analysis) |
| 3.7.0 | 2026-03-23 | Three-layer hooks: tool pre/post + after_file_write pipeline, external shell scripts, HookRunner, centralized file-write pipeline, hooks UI, workflow restriction enforced, compaction SSE |
| 3.6.0 | 2026-03-22 | Lossless context management: DAG-based hierarchical summarization, context_search/detail/recall tools, configurable fresh tail, summary model, condensation, settings UI |
| 3.5.0 | 2026-03-22 | Chat content search (SQLite + QMD), index status indicators, transcript backfill, KG search fix, frontmatter nested YAML fix, edge path fix, two-stage relationship discovery (QMD + LLM), project panel deep search |
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
