# Brain-Agent HTTP API Reference

Server runs on `http://127.0.0.1:8420`. All `/v1/*` paths require auth unless
listed as public. Static files under `/`, `/web/` are public.

## Auth

### Login (get a bearer token)
```
POST /v1/auth/login
Body: {"username": "<u>", "password": "<p>"}
→ {"access_token": "...", "refresh_token": "...", "user": {...}}
```

Default admin credentials in dev: `admin` / `admin` (rotate in prod).

### Use the token
```
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8420/v1/sessions
```

### Other auth endpoints
- `POST /v1/auth/refresh` — exchange refresh token
- `POST /v1/auth/password` — change own password
- `POST /v1/auth/profile` — update display name / email
- `POST /v1/auth/preferences` — merge-update `users.preferences` JSON
  (keys: `greeting_name`, `job_description`, `communication_preferences`,
  `memory_chats_default`, `memory_sched_default`,
  `daily_summary_enabled`, `daily_summary_hour_local`)
- `GET /v1/auth/me` — current user
- `GET /v1/auth/users` — admin: list all users
- `POST /v1/auth/users` — admin: create/update/delete
- `GET /v1/auth/audit` — admin: audit log (RBAC, GDPR, schedule edits, …)
- `GET /v1/auth/permissions` — current user's effective ACL
- `GET /v1/auth/profile-doc` — auto-maintained user profile markdown
- `POST /v1/auth/profile-doc/update-now` — kick the profile daemon
- `POST /v1/auth/profile-doc/reset` — wipe profile (history kept)

## Sessions / Chat

### List & search
- `GET /v1/sessions` — list chats visible to caller (sharing model applies)
- `GET /v1/sessions/search?q=...` — full-text search

### Per-session
- `GET /v1/sessions/<sid>/messages` — full transcript
  (returns `{messages, streaming: bool, streaming_text, streaming_meta}`)
- `GET /v1/sessions/<sid>/inspect` — diagnostic dump (admin)
- `GET /v1/sessions/<sid>/files` — attachments + artifacts
- `GET /v1/sessions/<sid>/next-prompt` — model-suggested follow-up
- `GET /v1/sessions/<sid>/warmup` / `/warmup-status` — warm-pool state
- `POST /v1/sessions/<sid>/warmup` — manually trigger warmup
- `GET /v1/sessions/<sid>/gdpr-maps` — admin: pseudonym maps for this chat
- `GET /v1/sessions/<sid>/gdpr-maps/<id>` — admin: decrypt one map

### Send / control
- `POST /v1/chat` — send message (SSE stream). Body:
  ```
  {
    "session_id": "<sid>",       # optional; null creates a new chat
    "agent": "main",
    "model": "<model_id>",
    "message": "<user text>",
    "files": [...],              # base64 attachments
    "save_to_memory": 0|1|2,
    "research_mode_override": 0|1|null,
    "caveman_mode": 0..3,
    "thinking_level": "off|low|medium|high"
  }
  ```
  Response: SSE events (`text_delta`, `thinking`, `tool_use`, `tool_result`,
  `done`, `error`, …).
- `GET /v1/chat/stream?session_id=<sid>` — re-attach to a live turn (SSE)
- `POST /v1/chat/cancel` — `{session_id}` cancels the active turn
- `POST /v1/chat/answer` — `{session_id, answer}` unblocks `AskUserQuestion`
- `POST /v1/chat/gdpr-recovery` — `{session_id, action}` resolve a
  pre-send PII modal (`block`, `proceed_local`, `proceed_pseudo`, …)

### Manage
- `POST /v1/sessions` — create empty session
  `{agent, model, title?, project?, project_id?}`
- `POST /v1/sessions/manage` — bulk ops:
  `action: "delete" | "archive" | "rename" | "set_visibility" |
   "set_project" | "set_save_to_memory" | "memorize_turns" |
   "purge_turns" | ...`. Body keys depend on action; see `04-recipes.md`.

## Agents

- `GET /v1/agents` — list all agents (admin sees all, others see allowed)
- `GET /v1/agents/<id>` — agent.json + computed metadata (skills, hooks, …)
- `POST /v1/agents/switch` — `{agent_id}` change caller's active agent
- `POST /v1/agents/create` — `{id, display_name, soul?, team?}`
- `POST /v1/agents/delete` — `{id}`
- `POST /v1/agents/rename` — `{old_id, new_id}`
- `POST /v1/agents/<id>/soul-chat` — refine soul.md via LLM
- `GET /v1/agents/<id>/files` — list files in agent dir
- `GET /v1/agents/<id>/file?path=<rel>` — read one file
- `POST /v1/agents/<id>/file` — write one file
- `GET /v1/agents/activity` — recent activity per agent
- `GET /v1/agents/<id>/commands` / `POST` — slash-command definitions
- `GET /v1/agents/<id>/hooks` / `POST` — agent hooks

## Projects

- `GET /v1/agents/<id>/projects` — list projects under an agent
- `GET /v1/agents/<id>/projects/<name>` — project.json + computed
- `POST /v1/agents/<id>/projects` — `{action, name, ...}`
  actions: `create | delete | archive | restore | rename | edit |
  set_research_mode`
- `GET .../projects/<name>/notes` / `POST` — project notes
- `GET .../projects/<name>/docs` — list ingested docs
- `GET/POST .../projects/<name>/input-folders` — list/edit folders
- `POST .../projects/<name>/input-folders/<idx>` — edit/delete one
- `GET .../projects/<name>/sync-status` — current sync state
- `GET .../projects/<name>/sync-runs` — sync history
- `GET .../projects/<name>/sync-runs/<id>` — one run detail
- `POST .../projects/<name>/sync-now` — trigger immediate sync
- `POST .../projects/<name>/full-resync` — wipe wing + re-mine
- `POST .../projects/<name>/sync-cancel` — abort live sync
- `POST .../projects/<name>/ingest` — upload files (multipart)
- `GET .../projects/<name>/image` — project thumbnail
- `GET .../ingested` — list ingested files under an agent

## Scheduler

`GET /v1/schedule` — list visible schedules + currently-running tasks.

`POST /v1/schedule` body shape: `{action, ...}`. Action verbs:

| Action | Body | Effect |
|---|---|---|
| `add` | `{name, task, schedule, agent="main", model?, timeout=300, attachments=[], working_dir?, thinking_level?, caveman_chat?, tool_profile?}` | Create new schedule. `schedule` is a cron expr or `@every 10m` etc. |
| `edit` | `{name, task?, schedule?, model?, timeout?, agent?, new_name?, attachments?, working_dir?, thinking_level?, caveman_chat?, tool_profile?}` | Partial update |
| `pause` / `resume` | `{name}` | Toggle enabled flag |
| `delete` | `{name}` | Remove schedule (history kept) |
| `run_now` | `{name}` | Trigger immediately (synthetic session `sched-<run_id>`) |
| `history` | `{name?, limit=20}` | List past runs |
| `run_detail` | `{run_id}` | Full run record (artifacts, traces, …) |
| `delete_run` | `{run_id}` | Remove one history row (cannot delete a running one) |
| `clear_history` | `{name}` | Wipe history for one schedule |
| `purge_orphan_history` | — | Admin: drop history rows for deleted schedules |

`GET /v1/schedule/running` — currently-executing runs.
`POST /v1/schedule/cancel` — `{name}` abort a running task.
`POST /v1/schedule/upload` — multipart, returns a path you can put in
`attachments[]`. Files go under `agents/main/scheduled_attachments/`.

`tool_profile` values: `""` (research_minimal, default), `"interactive"`
(full interactive tool set). Drives the `purpose` on the LLM call.

## Models / Providers

- `GET /v1/models` — flat list of enabled models (with display name,
  provider, capabilities)
- `GET /v1/models/config` — full per-model config (admin)
- `POST /v1/models/config` — save model config; supports `action: "sync"`
  to pull from provider's `/models`, `action: "full_resync"` to clear
  deletion tombstones first
- `GET /v1/providers` — provider list with status
- `POST /v1/providers` — `{action: "save"|"delete"|"test", ...}`
- `POST /v1/providers/test` — `{base_url, api_key, ...}` probe
- `GET /v1/providers/stats` — per-provider request/error counts

## Costs / Quotas

- `GET /v1/costs?start=&end=&agent=&user=&model=` — flat cost log
- `GET /v1/costs/daily` — aggregated per-day
- `GET /v1/quotas/me` — caller's daily + cycle usage vs limit
- `GET /v1/quotas/config` — admin: server-wide quota config
- `POST /v1/quotas/config` — admin: save quota config
- `GET /v1/quotas/admin/users` — admin: every user's quota state
- `GET /v1/quotas/admin/breakdown?user_id=&model=` — admin: detail

## MemPalace

- `GET /v1/mempalace/stats` — wing/room/drawer counts
- `GET /v1/mempalace/classifier` / `POST` — chat-sync classifier config
- `GET /v1/mempalace/activity` — live miner state
- `GET /v1/mempalace/session-turns?session_id=` — drawer ids per turn
- `GET /v1/mempalace/drawers?wing=&room=&q=&limit=` — list drawers
- `GET /v1/mempalace/kg/stats` — KG global stats
- `GET /v1/mempalace/kg/wing?wing=` — KG per-wing detail
- `GET /v1/mempalace/kg/entity?wing=&entity=` — entity neighborhood
- `GET /v1/mempalace/kg/extraction-log?wing=` — extraction history
- `GET /v1/mempalace/kg/config` / `POST` — extraction config
- `POST /v1/mempalace/kg/reextract` — `{wing, source_file?}` re-run

## Tools (admin)

- `GET /v1/tools/list` — active tool set for caller
- `GET /v1/tools/settings` — every tool + its global record
- `POST /v1/tools/settings` — save one tool's record (per-tool prose,
  enabled, deferred, purposes, applies_with)
- `GET /v1/tools/config` / `POST` — integration knobs (API keys, etc.)
- `GET /v1/tools/status` — per-tool diagnostic
- `GET /v1/tools/breakdown?agent=` — token cost per tool
- `POST /v1/tools/call` — **localhost+nonce only**, sidecar→Brain dispatch
  (do not call manually)

## GDPR / PII

- `POST /v1/attachments/scan` — `{path, mime, ...}` returns PII findings
- `POST /v1/gdpr/scan-text` — `{text}` returns findings
- `GET /v1/gdpr/ner-models` — admin: list spaCy NER model state
- `POST /v1/gdpr/ner-models` — `{action: "load"|"unload", lang}` toggle

## Translation

- `POST /v1/translate/text` — `{text, target, source?, glossary?}` (Mistral)
- `POST /v1/translate/document` — multipart, returns job id
- `POST /v1/translate/media` — audio/video translate
- `POST /v1/translate/detect` — language detect
- `POST /v1/translate/tts` / `GET /v1/translate/tts/voices` — TTS
- `POST /v1/translate/live/start` → SSE `GET /v1/translate/live/<sid>` →
  `POST /v1/translate/live/<sid>/chunk` → `/stop` — mic streaming
- `GET /v1/translate/history` / `GET /v1/translate/history/<id>/file?which=...`
- `POST /v1/translate/glossaries` / `GET /v1/translate/glossaries[/<slug>]`
- `GET /v1/translate/jobs/<id>` / `/result` — job poll

## Skills

- `GET /v1/skills/claude-code` — list Claude Code plugin skills + commands
- `POST /v1/skills/claude-code` — `{action: "enable"|"disable", slug, agent}`
- `POST /v1/skills/claude-code/browse` — list marketplace plugins
- `POST /v1/skills/claude-code/install` — install one plugin
- `POST /v1/skills/install-zip` — multipart zip upload (skill folder)
- `POST /v1/skills/remove` — `{slug, agent}`

## MCP

- `GET /v1/mcp/connections` — current MCP connections
- `GET /v1/mcp/registry` — known servers
- `POST /v1/mcp/connect` / `POST /v1/mcp/disconnect`

## Context Manager (LCM)

- `GET /v1/context/config` — admin: LCM config
- `GET /v1/context/stats?session_id=` — current usage
- `POST /v1/context/compact` — `{session_id, force?}` trigger LCM

## Artifacts

- `GET /v1/artifacts?session_id=&role=` — list
- `GET /v1/artifacts/browse?path=` — directory browse
- `GET /v1/artifacts/<id>/content` — body
- `GET /v1/artifacts/<id>/download` — file download

## Workflows / Workers / Nodes

- `GET /v1/workflows/executions` / `GET /v1/workflows/executions/<id>`
- `GET /v1/workflows/history` / `.../<id>` / `.../<id>/file[-preview]`
- `GET /v1/agents/<id>/workflows` / `POST /v1/agents/<id>/workflows/<wid>/run`
- `POST /v1/workflows/executions/<id>/approve` / `/cancel` / `/upload-file`
- `POST /v1/workflows/history/<id>/promote-session/<sid>` / `/session`
- `GET /v1/workers` / `/v1/workers/recent`
- `GET /v1/nodes` / `POST /v1/nodes` / `/v1/nodes/poll` / `/result` / `/execute`

## Files

- `GET /v1/files/download?path=` / `/files/preview?path=` / `/files/tree?root=`

## Sharing / Teams / Favourites / Channels

- `GET /v1/share?kind=&id=` — visibility info for any sharable object
- `POST /v1/share` — `{kind, id, visibility, team_id?, extra_user_ids?}`
  kinds: `chat | project | schedule | workflow | artifact`
- `POST /v1/share/transfer` — `{kind, id, new_owner_user_id}` admin
- `GET /v1/teams` / `POST /v1/teams` — team CRUD
- `GET /v1/user-teams` — caller's team memberships
- `GET /v1/favourites` / `POST /v1/favourites` / image variant
- `GET /v1/channels` — list team channels

## Services / Notifications / Backup / Status

- `GET /v1/status` — server uptime + version
- `GET /v1/services` — daemon status (mempalace-miner, chat-sync, …)
- `GET /v1/services/log?name=&lines=` — tail a service log
- `POST /v1/services/telegram` / `/services/server` — start/stop/restart
- `POST /v1/restart` — restart Brain (graceful)
- `GET /v1/warmup/status` / `POST /v1/warmup/trigger`
- `GET /v1/sidecar/status` / `POST /v1/sidecar/restart`
- `GET /v1/queue/status` / `POST /v1/queue/cancel`
- `GET /v1/traces` / `/v1/traces/<id>` — LLM-call traces
- `GET /v1/audit` / `/v1/audit/export` — audit log
- `GET /v1/cache/stats` / `POST /v1/cache/clear`
- `GET /v1/notifications` / `/unread` / `POST /notifications/{settings,dismiss,read}`
- `GET /v1/backup/info` / `POST /v1/backup` / `POST /v1/restore`
- `POST /v1/refine` — `{text, purpose}` polish-don't-rewrite LLM call

## Tasks (delegate API)

- `GET /v1/tasks` — delegated tasks visible to caller

## Notes on response shapes

- Errors: `{"error": "<message>"}` with non-2xx status.
- SSE streams: `data: {json}\n\n` lines, `event: <type>` optional.
- Most endpoints return `{...}` JSON; list endpoints return
  `{"<plural_key>": [...]}` (e.g. `{"sessions": [...]}`,
  `{"schedules": [...], "running": [...]}`).
