# Storage Layout & DB Schemas

## On-disk layout

```
agents/
  <agent_id>/                  # one dir per agent (e.g. "main")
    agent.json                 # config: tool_groups, token_config, limits,
                               # rate_limits, team, memory_summary, …
    soul.md                    # agent persona
    mcp.json                   # MCP server definitions
    commands.json              # slash-command definitions
    hooks/                     # per-event hook scripts
    skills/                    # SKILL.md skills (this file lives here)
      <slug>/SKILL.md
    projects/                  # per-project folder
      <project_name>/
        project.json           # id, instructions, research_mode,
                               # input_folders, sync_status
        ingested/              # uploaded files
        .trash/                # soft-deleted
    artifacts/                 # turn output dir tree
      <YYYY-MM-DD>_<sid_prefix>/   # one folder per chat session
    scheduled_attachments/     # files referenced by schedules.attachments
    glossaries/                # translation glossaries
    favourite_images/          # pinned images
    pseudonym.key              # GDPR pseudonym map encryption key

    # SQLite databases (per-agent)
    auth.db                    # users, teams, ACL, audit_log, daily_summary
    chats.db                   # sessions, messages, artifacts, artifact_versions,
                               # active_turns, pseudonym_maps, translate_history,
                               # auth_tokens, channel_members, team_channels,
                               # data_sessions, favourites, reactions,
                               # read_cursors, kg_extraction_log/progress/state,
                               # chat_mempalace_sync, closet_regen_progress,
                               # project_sync_runs, helpdesk_history
    scheduler.db               # legacy / migrating
    schedules.db               # schedules, schedule_history, workflow_history
    costs.db                   # cost_log
    traces.db                  # traces (LLM-call records)
    audit.db                   # admin actions audit
    context.db                 # LCM DAG (nodes, edges, summaries)
    code-graph.db              # tree-sitter parsed code graph
    memory.db                  # legacy memory (pre-MemPalace)

# Outside agents/ — system-level
~/.brain-agent/                # log + cache root
  server.log                   # startup banner only
  server.error.log             # ALL daemon stdout/stderr (launchd quirk)
  telegram.log / .error.log
  pi-sidecar.log               # Anthropic SDK sidecar
  claude-code-*.log
  qmd.log / .error.log
  extracted-cache/             # binary→markdown cache (markitdown/mistral OCR)
  pi-agent/
  chats.db / costs.db          # legacy duplicates (ignore)

config.json                    # repo root: providers, models, server,
                               # tool_settings, gdpr_scanner, quotas, mempalace,
                               # research_mode_disciplines, deleted_models,
                               # default_model, searxng{url}, crawl4ai{auto_start,
                               # url,venv_python}, helpdesk{enabled,model,
                               # max_rounds,system_prompt}  (last three gitignored,
                               # per-machine — supervisors no-op without them)
mempalace.yaml                 # MemPalace palace_path + chat-sync config
sidecar/                       # Anthropic SDK subprocess (separate venv)
searxng/ + .venv_searxng       # self-hosted SearXNG (port 8088), gitignored
crawl4ai/ + .venv_crawl4ai     # headless-render service (port 8422), gitignored
```

## Critical SQLite schemas

### chats.db → sessions
```
id TEXT PK, agent_id TEXT, model TEXT, title TEXT, status TEXT,
created_at REAL, last_active REAL,
project TEXT, summary TEXT, user_id TEXT,
save_to_memory INTEGER (0=off,1=on,2=auto),
caveman_mode INTEGER (0..3),
team_id TEXT, visibility TEXT (user|users|team|global),
project_id TEXT, workflow_run_id TEXT,
research_mode_override INTEGER (NULL=use project default),
streaming_text TEXT, streaming_meta TEXT,
extra_member_user_ids TEXT (JSON list),
excluded_user_ids TEXT (JSON list),
last_system_prompt TEXT, gdpr_action_pref TEXT,
allow_further_web INTEGER (0/1, sticky; lifts the Websuche tool lockout)
```

Status values: `active | archived | note_chat` (note_chat = AI-editing
session for a project note; hidden from chat list).

### chats.db → messages
```
id INTEGER PK AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
created_at REAL, metadata TEXT (JSON),
channel_id TEXT, sender_id TEXT, sender_type TEXT (user|agent|system),
parent_id INTEGER, edited_at REAL, compacted INTEGER
```

Role values: `user | assistant | thinking | tool_call | tool_result |
system`. `compacted=1` means superseded by LCM (kept for search, not for
conversation replay).

`_ALLOWED_MSG_KEYS` strips `metadata`, `thinking`, internal fields before
sending to the LLM.

### schedules.db → schedules
```
id INTEGER PK, name TEXT UNIQUE, task TEXT, schedule TEXT (cron/@every),
agent TEXT (default 'main'), model TEXT, enabled INTEGER,
last_run TEXT, next_run TEXT, created_at TEXT,
timeout INTEGER (default 300),
attachments TEXT (JSON list of paths),
working_dir TEXT, user_id TEXT,
thinking_level TEXT (off|low|medium|high|''),
caveman_chat INTEGER (0..3),
visibility TEXT, owner_team_id TEXT,
extra_member_user_ids TEXT, excluded_user_ids TEXT,
tool_profile TEXT ('' = research_minimal, 'interactive' = full)
```

### schedules.db → schedule_history (one row per run)
Columns include: `id` (= run_id, synthetic session = `sched-<id>`),
`schedule_name`, `status` (running|success|error|cancelled), `started_at`,
`finished_at`, `output`, `error`, `cost`, `tokens_in/out`, `model_used`,
`user_id`, `artifacts` (JSON), `traces` (JSON ids).

### costs.db → cost_log
```
ts REAL, session_id TEXT, agent_id TEXT, user_id TEXT,
model TEXT, provider TEXT,
input_tokens INTEGER, output_tokens INTEGER,
cache_read INTEGER, cache_write INTEGER,
input_cost REAL, output_cost REAL, total_cost REAL,
purpose TEXT (chat|sched|profile|summary|classifier|kg_extract|…),
metadata TEXT
```

Empty `user_id` = pre-quota legacy rows.

### auth.db → users
```
id TEXT PK, username TEXT UNIQUE, password_hash TEXT,
display_name TEXT, email TEXT, role TEXT (admin|poweruser|user),
created_at REAL, last_login REAL,
preferences TEXT (JSON; see PREFERENCE_DEFAULTS),
disabled INTEGER
```

### auth.db → audit_log
`ts REAL, actor_user_id TEXT, action_type TEXT, target TEXT,
metadata TEXT (JSON)`. Action types include `auth_login`,
`schedule_add/edit/delete/run_now`, `pii_blocked`, `pii_detected`,
`pii_auto_fallback`, `tool_settings_save`, `model_config_save`,
`gdpr_ner_models_change`, `research_mode_disciplines_save`, ….

### auth.db → user_teams / user_team_members / *_permissions
Team CRUD + ACL grants (which agents/models a team or user can access).

### chats.db → artifacts / artifact_versions
`artifacts(id, session_id, path, role(intermediate|output), mime, size,
created_at, …)`. `artifact_versions(artifact_id, version, content_blob,
size, created_at)` — 5MB cap per version.

### chats.db → active_turns
`session_id TEXT PK, turn_id TEXT, model TEXT, started_at REAL`.
Used for Brain-restart turn recovery.

### chats.db → pseudonym_maps
Encrypted GDPR pseudonym maps. Decrypt with `pseudonym.key`.
Admin only — see `/v1/sessions/<sid>/gdpr-maps[/<id>]`.

### chats.db → helpdesk_history (Brainy conversation)
```
id INTEGER PK AUTOINCREMENT, session_id TEXT (vestigial, empty),
user_id TEXT, role TEXT, content TEXT, created_at REAL
```
Index `idx_helpdesk_history_user(user_id, id)`. **Per-USER, not
per-session** — Brainy's history follows the user across chats and is NOT
cascade-dropped when a chat session is deleted. Served newest-first +
cursor-paginated by `GET /v1/helpdesk/history`.

### context.db (LCM)
Nodes + edges of the lossless context manager DAG. `nodes(id, session_id,
depth, content, token_count, …)`, `edges(parent_id, child_id, kind)`.

### code-graph.db
Tree-sitter AST snapshots. `files(path, sha256, lang, …)`,
`symbols(qname, file_id, kind, …)`, `edges(src, dst, kind)`.

### MemPalace storage
- Palace root: from `mempalace.yaml → palace_path`
  (typically `~/.mempalace/<palace_name>/`).
- ChromaDB collection per wing; SQLite at `<palace>/knowledge_graph.sqlite3`
  for triples.
- Wing naming: `user__<uid>` / `team__<tid>` / `project__<pid>` / bare
  shared names (e.g. `brain_code`).
- Rooms: `chat | chat_summary | chat_attachment | reference | general |
  artifacts | user_profile | …`.

## File-path conventions used by tools

- Schedule attachments: `agents/main/scheduled_attachments/<file>` —
  referenced by absolute path in `schedules.attachments[]`. NEVER copy
  per-run; same file reused every fire.
- Session artifacts: `agents/main/artifacts/<YYYY-MM-DD>_<sid_prefix>/`.
- Binary→markdown companions: `<dir>/.brain-extracted/<filename>.<ext>.md`
  with `<!-- brain-source: <abs path> -->` link back.
- Chat attachments at upload time: `/tmp/brain-attachments/<sid>/<file>`,
  then promoted into artifacts on send.
- Oversized tool results (>50KB) spill to
  `agents/main/artifacts/<YYYY-MM-DD>_<sid_prefix>/tool-results/<tool_use_id>.txt`
  (the in-context copy is truncated; full text served by
  `GET /v1/tools/result`).
- User profile: `agents/main/user_profiles/<uid>.md` +
  `<uid>.history/<ISO>.md` (capped 30).

## Reading databases safely

All DBs use WAL mode. From inside an agent turn, use:

```
execute_command("sqlite3 -readonly agents/main/chats.db \"SELECT ...\"")
```

Or with python_exec for richer queries:

```python
import sqlite3
con = sqlite3.connect("file:agents/main/chats.db?mode=ro", uri=True)
for row in con.execute("SELECT id, title, last_active FROM sessions ORDER BY last_active DESC LIMIT 10"):
    print(row)
```

Do **not** open read-write from a tool unless explicitly asked — the
server holds locks and concurrent writes can corrupt WAL state.
