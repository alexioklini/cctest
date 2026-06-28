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
  `thinking_level_default` (null|none|low|medium|high),
  `caveman_mode_default` (null|0..3) — per-user new-chat composer defaults,
  null = inherit the global `composer_defaults`,
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
- `GET /v1/sessions/<sid>/next-prompt` — model-suggested follow-up (composer
  ghost-text). Auto-fetched after a turn; ALSO fetched on demand when the user
  presses **Tab** on an empty composer with no ghost showing (reuse precomputed,
  else generate + fill — v9.154.1). **Cached per Session** (in-memory, keyed by a
  conversation signature: msg-count + tail + caveman mode) so a repeat call for
  the same conversation returns `{suggestion, cached:true}` with NO LLM call; a
  new turn regenerates; `?force=1` bypasses (v9.154.2).
- `GET /v1/sessions/<sid>/warmup` / `/warmup-status` — warm-pool state
- `POST /v1/sessions/<sid>/warmup` — manually trigger warmup
- `POST /v1/sessions/<sid>/audio-overview` — generate a two-host podcast (.mp3)
  from THIS CHAT's transcript (the chat-podcast button). Body `{length?:
  short|std|long, focus?: str, force?: bool}`. Synchronous (~1 min); writes
  .mp3 + .md (named after the chat title, e.g. `Podcast — <title>.mp3`) into the
  session artifact folder → `{ok, artifact_id, audio_file, script_file,
  spoken_lines, cost, cached?}`. **Cached**: the result (transcript+length+focus
  hash) is stored on `sessions.chat_audio_overview`; an unchanged chat returns
  the prior podcast with `cached:true` instead of regenerating — `force:true`
  rebuilds. Cost-counted (script-gen LLM + char-billed TTS). Language
  auto-detected (spoken in the chat's language, voice matched if available).
  (The project equivalent is the `audio_overview` kind on
  `.../projects/<name>/generate`.)
- `GET /v1/translate/tts/voices` — list TTS voices `{voices:[{slug,id,name,gender,
  languages,tags}]}`. `POST` (admin) — clone a voice `{name, sample_audio_b64,
  sample_filename?, languages?:[iso], gender?}` → the new voice (auto-used for its
  language). `DELETE /v1/translate/tts/voices/<id>` (admin) — remove a cloned voice.
- `POST /v1/translate/tts` — `{text, voice?, lang?, model?, auto_voice?}` → MP3
  bytes. Voice selection (when `voice` is not pinned): an explicit `lang` (ISO)
  picks a voice tagged for it; else `auto_voice:true` detects the text's language;
  else the configured default. The Translation Text + Live-mic tabs pass `lang`
  per side, so they auto-use language-matched voices.
- `POST /v1/sessions/export` — `{session_id, kind: "summary"|"dump"}` → save a
  markdown export into the session artifact folder (registered as an artifact).
  `dump` = verbatim full chat history (pure transform, no LLM). `summary` = LLM
  synopsis via the configured `chat_summary_model`. → `{status, name, artifact_id}`.
  (Chat-view status-line buttons.)
- `POST /v1/sessions/export-bundle` — `{session_id}` → **SSE**. Builds a complete
  -chat ZIP server-side and streams `progress {percent, stage}` events, then
  `done {token, filename, size}` (or `error {message}`). The zip bundles
  everything the right panel shows: `conversation.md`, `tool-calls.md` (per-turn
  tool input/output), `references.md/json` (web sources), `statistics.md/json`
  (turns/tokens/cost/models/per-tool counts), `inspect.json`, `messages.json`,
  `attachments/` (uploaded files), `artifacts/` (generated files), `README.md`.
  The zip is **downloaded, NOT stored as an artifact**.
- `GET /v1/sessions/export-bundle/download?token=…` — serve the built zip once
  (single-use token, 600s TTL), then delete the temp file. Returns
  `application/zip` as an attachment.
- `GET /v1/sessions/<sid>/gdpr-maps` — admin: pseudonym maps for this chat
- `GET /v1/sessions/<sid>/gdpr-maps/<id>` — admin: decrypt one map

### Helpdesk (Brainy)
Brainy is the read-only helpdesk bot (the floating bubble). Separate
streaming call, per-USER history, fixed read-only tool set. See
`05-internals.md` → "Brainy helpdesk bot".
- `POST /v1/helpdesk` — `{message, session_id?, view_context?}` SSE stream
  (`text_delta`, `tool_call`, `error`, `done`). Any logged-in user.
- `GET /v1/helpdesk/history?before_id=&limit=` → `{messages:[{id,role,
  content,ts,context_label}], has_more}` — newest-first, cursor-paginated,
  per-user. `context_label` = where the turn was asked (badge + replay key).
- `POST /v1/helpdesk/delete` — `{id}` (one row), `{ids:[...]}` (several —
  an exchange is the question row + the answer row, deleted together), or
  `{start_ts, end_ts}` (range); user-scoped.
- `POST /v1/helpdesk/clear` — wipe the caller's Brainy conversation.
- `POST /v1/helpdesk/warmup` — lazy-prime Brainy's KV prefix (helpdesk
  prompt + read-only tools), fired by the frontend when the bubble opens.
  Background fire-and-forget; no-op unless Brainy's model is local +
  warmup-enabled; 90s debounce. Returns `{status: priming|warm|in_flight|
  skipped|disabled}`.
- `GET /v1/helpdesk/config` / `POST /v1/helpdesk/config` — **admin**:
  `{enabled, model, max_rounds, system_prompt}`. Model "Auto" = server
  default. Edited in Settings → Tools → Brainy.

### Manual web search (Websuche)
- `POST /v1/web/search` — `{query, num_results?, force_fresh?}` → `{query,
  results}`. Any logged-in user. Pure `searxng_search` passthrough — no
  fetch, no LLM. Backs the Websuche tab; the curated URLs are sent on the
  next chat as `body.web_urls_to_fetch` (see `POST /v1/chat`). The basket is
  PER SESSION — persisted server-side in `sessions.web_basket` via
  `POST /v1/sessions/manage {action:"web_basket", value:[...]}` and returned
  by `GET /messages` as `web_basket`; a fresh chat starts empty (no cross-chat
  bleed).

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
    "thinking_level": "off|low|medium|high",
    "web_urls_to_fetch": [{"url": "...", "title": "..."}],  # Websuche basket
    "web_abstract_first": false  # fetch each curated source as a ~1500-char abstract instead of the full page
  }
  ```
  When `web_urls_to_fetch` is present, the worker pre-fetches each URL
  fresh at turn time and injects the markdown into a transient wire copy
  of the user message (never persisted). Unless `sessions.allow_further_web`
  is set, the three web tools (`web_fetch`, `exa_search`, `searxng_search`)
  are locked out for that turn so the model works strictly from the
  curated set. Fetched sources are recorded on the assistant turn's
  `metadata.web_sources` (rendered as "Webquellen dieser Anfrage").
  Response: SSE events (`text_delta`, `thinking`, `tool_use`, `tool_result`,
  `done`, `error`, …).
- `GET /v1/chat/stream?session_id=<sid>` — re-attach to a live turn (SSE)
- `POST /v1/chat/cancel` — `{session_id}` cancels the active turn
- `POST /v1/chat/answer` — `{session_id, answer}` unblocks `AskUserQuestion`
- `POST /v1/chat/gdpr-recovery` — `{session_id, action}` resolve a
  pre-send PII modal (`block`, `proceed_local`, `proceed_pseudo`, …)
- `POST /v1/chat/handover` — `{session_id}` generate a handover for a chat.
  The resolved model writes a structured handover doc; returns `{markdown,
  transcript, source_title, artifact_saved}` (transcript = the full verbatim
  source history as a SEPARATE doc; `artifact_saved` = the filename of the
  summary saved as an artifact in the SOURCE session, `""` if the save failed).
  The client shows a progress→preview modal; the user inspects the summary and
  only on approval does the client open a new chat with both docs attached.

### Manage
- `POST /v1/sessions` — create empty session
  `{agent, model, title?, project?, project_id?}`
- `POST /v1/sessions/manage` — bulk ops:
  `action: "delete" | "archive" | "rename" | "set_visibility" |
   "set_project" | "set_save_to_memory" | "caveman_mode" |
   "thinking_level" | "memorize_turns" |
   "purge_turns" | "allow_further_web" | ...`. Body keys depend on action;
   see `04-recipes.md`. `allow_further_web {value}` toggles the sticky
   per-session escape hatch that lifts the Websuche tool lockout.
   `caveman_mode {mode:0..3}` + `thinking_level {level:"none"|"low"|"medium"|"high"}`
   persist the per-session composer toggles (restored on reload).
   `gdpr_details_visible {value}` persists the per-chat "Datenschutz-Details
   sichtbar" shield toggle (GDPR mark overlays + detail block), restored on
   reopen; `gdpr_feedback_ask {value}` the sticky post-turn GDPR feedback opt-in.

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
- `POST .../projects/<name>/ingest` — upload files (multipart) → mined into the wing
- `GET .../projects/<name>/instruction-files` — list supplementary instruction
  files (owner docs that complement the project instructions; NEVER mined — the
  model reads them on demand with read_document, like a chat attachment)
- `POST .../projects/<name>/instruction-files` — upload one (multipart, any type,
  max 25 MB; manage-gated). Stored under instruction-files/, binaries get a .md
  companion; recorded in project.json instruction_files[]
- `DELETE .../projects/<name>/instruction-files/<filename>` — remove one (manage)
- `POST .../projects/<name>/generate-instructions` — `{prompt}` → start an
  agentic KI run that WRITES the project instructions (reads the inlined
  reference/instruction files, queries the project wing/KG, may web-search;
  purpose `instruction_gen`, admin-configurable tool set + model). Returns
  `{gen_id}`. Result is loaded into the editor for review — NOT auto-saved (manage)
- `GET .../projects/<name>/instruction-gen/<gen_id>` — poll: `{status (generating|
  ready|error|cancelled), phase, model, error, result_md (only when ready),
  steps[] (live tool-call log)}`
- `POST .../projects/<name>/instruction-gen/<gen_id>/cancel` — abort the run (manage)
- `GET .../projects/<name>/image` — project thumbnail
- `POST .../projects/<name>/generate` — generate a grounded output from the
  project's sources. Body `{kind: study_guide|briefing|faq|timeline|audio_overview,
  options?: {focus?: str, length?: short|std|long, audience?: str}}` → `{output_id,
  status:"generating"}`. Requires manage; refuses (400) if the project has no
  sources. Runs async + saved as a `project_outputs` row. SHARED endpoint. The
  four text kinds write a cited `.md`; `audio_overview` instead runs a different
  worker — an LLM writes a two-host (Oliver & Jane) English dialogue, then each
  line is voiced via Voxtral TTS and the MP3 segments are concatenated into one
  `.mp3` (phases: gathering → scripting → voicing N/M). The `.mp3` is the output
  artifact; a `.md` script is saved alongside. `audience` only applies to
  `audio_overview`. AUDIO IS ENGLISH-ONLY (TTS voice constraint) regardless of
  source language.
- `GET .../projects/<name>/outputs` — list this project's generated outputs
  (poll for `status` generating→ready/error).
- `GET .../projects/<name>/outputs/<output_id>` — one output's status/metadata.
- `POST .../projects/<name>/outputs/<output_id>/rename {title}` — rename the
  output row only (the `.md` file is untouched). Requires manage.
- `DELETE .../projects/<name>/outputs/<output_id>` — delete the row + its artifact
  rows + the `.md` on disk (no orphans). Refuses (409) while `status=generating`.
  Requires manage. The Studio tab on the project page is the UI for all of these.
- `POST .../projects/<name>/web-urls/discover-links` — scan the project's
  configured HTML `web_urls` for SAME-HOST document links (PDF/DOCX/XLSX/PPTX/
  CSV) and return them as PROPOSALS: `{proposed:[{url,title,ext,found_on,
  in_project}], scanned, pages, duration_s}`. Nothing is imported — the UI (🔗
  on the Web-Adressen source-tree node) shows the proposals and the user appends
  approved ones via the existing `update_project` path. Bounded: depth-1,
  same-host, documents-only, ≤12 pages — NOT a recursive crawler.
- `GET .../projects/<name>/research/backends` — `{backend}` = THE one active
  search tool (`"searxng"` | `"exa"` | `""`). Empty = Research disabled (E1 gate).
  Research uses the single enabled search tool (admin's Tools toggle); no merge.
- `POST .../projects/<name>/research/search {topic}` — Fast Research: search via
  the active backend + dedup vs the project's `web_urls`. Returns `{results:[{title,
  url, snippet, in_project, trust_hint}], total_found}` (SERP capped at 30). No
  import — the UI appends approved URLs via the existing `update_project` path.
- `POST .../projects/<name>/research/deep {topic, budget?}` — spawn the bounded
  Deep Research loop (uses the active backend). Returns `{run_id, budget}`. Manage.
- `GET .../projects/<name>/research/runs/<run_id>` — poll a Deep run: `{status
  running|done|error|cancelled, phase, progress, budget, report_output_id,
  proposed[], coverage_note}`. The report is a `project_outputs` row in Studio.
- `POST .../projects/<name>/research/runs/<run_id>/cancel` — cooperative cancel.
- `GET .../projects/<name>/folder-tree?path=<abs>` — read-only subtree of an
  ingested input folder OR (Code Mode) the project's working_dir. Each file node
  carries `name/path/size/mtime` (+ MemPalace `mined`/`kg` state for non-code).
  Code-Mode file nodes ALSO carry a `git` field — the one-letter working-tree
  status (`M`/`?`/`A`/`D`/`R`/`U`, empty when the working_dir is not a git repo),
  from one `git status --porcelain -z` over the working_dir; the bottom-panel
  file tree colours the name by it. Code-Mode walks additionally skip
  heavy/vendored dirs (node_modules/.venv/dist/build/target/…) so the
  poll-driven auto-refresh stays cheap.
- `POST .../projects/<name>/init` — Code Mode only: run one agentic background
  turn whose cwd is the working_dir; it explores the tree and writes a `BRAIN.md`
  summary at the root (the project's plain-markdown memory). Returns immediately
  `{status:"generating"}`. Manage-gated. One run per project at a time.
- `GET .../projects/<name>/init-status` — Code Mode only: latest init run state
  for the progress display → `{state: idle|generating|done|error|cancelled,
  elapsed, error?}`. `idle` = no run started this server process.
- `POST .../projects/<name>/init-cancel` — Code Mode only: abort an in-flight
  init (cancels the run's sidecar turn) → `{status: cancelling|not_running,
  cancelled}`. Manage-gated.
- `GET .../ingested` — list ingested files under an agent

## Scheduler

`GET /v1/schedule` — list visible schedules + currently-running tasks.
Optional `?project_id=<id>` or `?project=<name>` (+ `?agent=`) filters the list
to one project's tasks (used by the project view's "Geplante Aufgaben" tab);
omitting it returns all visible schedules (the agent-global Zeitplan tab).

`POST /v1/schedule` body shape: `{action, ...}`. Action verbs:

| Action | Body | Effect |
|---|---|---|
| `add` | `{name, task, schedule, agent="main", model?, timeout=300, attachments=[], working_dir?, thinking_level?, caveman_chat?, tool_profile?, project_id?}` | Create new schedule. `schedule` is a cron expr or `@every 10m` etc. `project_id` (stable uuid) binds the task to a project — the run then executes inside that project's context (instructions, MemPalace `project__<id>` wing, research_mode); the server validates the caller may access the project. |
| `edit` | `{name, task?, schedule?, model?, timeout?, agent?, new_name?, attachments?, working_dir?, thinking_level?, caveman_chat?, tool_profile?, project_id?}` | Partial update. `project_id=""` clears a project binding back to agent-global. |
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
  deletion tombstones first, `action: "benchmark"` (optional `model_id` /
  `task_type`) to run the capability+speed benchmark in the background
- `GET /v1/models/benchmark/status` — live benchmark progress
  (`{running, done, total, current_model, errors}`)
- `GET /v1/providers` — provider list with status
- `POST /v1/providers` — `{action: "save"|"delete"|"test", ...}`
- `POST /v1/providers/test` — `{base_url, api_key, ...}` probe
- `GET /v1/providers/stats` — per-provider request/error counts

## Costs / Quotas

- `GET /v1/costs?start=&end=&agent=&user=&model=` — flat cost log
- `GET /v1/costs/daily` — aggregated per-day
- `GET /v1/costs/breakdown?window=&agent=&user_id=` — per-use-case × per-model cost for a time window. `window` ∈ `today|week|7d|30d|180d|365d|ytd|all|cycle|last_cycle` (cycle/last_cycle reuse the quota billing-cycle config). Returns `{window,label,since,until,total_cost,total_calls,by_use_case:[{use_case,cost,calls,tokens_in,tokens_out,by_model:[…]}]}`. Use-cases are display buckets (Chat, Chat-Zusammenfassung, Geplante Aufgaben, Übersetzung, Studio, Deep Research, Audio Overview, Vorlesen, …) collapsed from the raw `cost_log.purpose`; pre-tagging rows show as *Unbekannt (Altdaten)*.
- `GET /v1/quotas/me` — caller's daily + cycle usage vs limit
- `GET /v1/quotas/config` — admin: server-wide quota config
- `POST /v1/quotas/config` — admin: save quota config
- `GET /v1/quotas/admin/users` — admin: every user's quota state
- `GET /v1/quotas/admin/breakdown?user_id=&model=` — admin: detail

## MemPalace

- `GET /v1/mempalace/stats` — wing/room/drawer counts
- `GET /v1/mempalace/classifier` / `POST` — chat-sync classifier config
- `GET /v1/composer/defaults` (any logged-in user) / `POST` (admin) — new-chat
  composer defaults `{thinking_level, caveman_mode, memory_mode}`. thinking +
  caveman live in `config.json → composer_defaults`; memory_mode writes through
  to `mempalace.chat_sync.classifier.default_mode` (single source). Configured
  in General Settings → Server → „Eingabefeld-Standards".
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
- `GET /v1/tools/settings[?agent=<id>]` — every tool + its global record
  (incl. `state` + per-use-case `states` map), plus a `matrix` block:
  per-purpose × tool `{state, tokens}` cells + a per-purpose `summary`
  (active/inactive/deferred counts + realized injection token size). `?agent=`
  folds that agent's `tool_overrides` into the matrix (effective states/sizing).
- `POST /v1/tools/settings` — save one tool's record (per-tool prose,
  scalar `state`, `purposes`, `applies_with`, and optional `states`
  `{<purpose>: state}` per-use-case map — validated against `_VALID_PURPOSES` ×
  `TOOL_STATES`; an empty/absent map keeps the record scalar-only)
- `GET /v1/tools/config` / `POST` — integration knobs (API keys, etc.)
- `GET /v1/tools/status` — per-tool diagnostic
- `GET /v1/tools/breakdown?agent=` — token cost per tool
- `POST /v1/tools/call` — **localhost+nonce only**, sidecar→Brain dispatch
  (do not call manually)
- `GET /v1/tools/result?session_id=&tool_use_id=` — full, **uncapped**
  tool-result text (ownership-checked, path-traversal-guarded). When a
  tool result exceeds the in-context budget (>50KB) it is spilled to
  `<agent>/artifacts/*_<sid>/tool-results/<tool_use_id>.txt`; this serves
  that file as a `text/plain` download. The web UI falls back to it when
  its in-DOM copy is the truncated stub after a reload. Works for
  `sched-*` synthetic sessions. (The per-agent `tool_result_char_limit`
  knob was removed in 9.15.2.)

## GDPR / PII

- `POST /v1/attachments/scan` — `{name, content(b64), media_type}` returns PII findings: aggregated `groups` (count/samples) + per-finding `findings_full` (value/confidence/band/disposition, deduped, cap 200/file + `findings_truncated`) + `worst_disposition` (9.197.0) + `classification` block
- `POST /v1/gdpr/scan-text` — `{text, full?, raw_detection?, name_precision?}` returns findings; `full:true` adds per-finding `value`/`confidence`/`band`/`disposition` + `worst_disposition`. Server-ONLY PII detector for the typed message (browser scanner removed 9.200.0); the pre-send dialog calls it behind a cancellable progress overlay
- `GET /v1/services/status` → `gdpr_scanner.catalog` (9.200.0) — `{rule_categories, category_labels, default_category_actions, rule_labels}`, the static PII catalog the client renders the Settings GDPR panel + chat-view labels from (was the deleted browser `PIIScanner` object)
- `GET /v1/gdpr/ner-models` — admin: list spaCy NER model state
- `POST /v1/gdpr/ner-models` — `{action: "load"|"unload", lang}` toggle
- `POST /v1/gdpr/decisions` — persist per-finding review outcome `{session_id, turn_action, decisions:[{rule_id,value,confidence,band,disposition,false_positive,source,value_hash?}]}` (9.196.0). `value_hash` optional (9.203.0): a caller that only knows the hash (the history modal — never holds cleartext) may pass it explicitly with empty `value`; the explicit hash then wins for the per-session dedupe row.
- `GET /v1/gdpr/decisions?session_id=X` — prior decisions keyed by value_hash (already-analysed + FP-for-chat)
- `GET /v1/gdpr/decisions/stats` — admin: per-rule FP-rate aggregate (global learning)
- `GET /v1/sessions/<id>/pii-history-summary` — server-side PII scan over the session's user+assistant text → `{counts:{label:N}, finding_count, has, worst_action}` (label counts only; feeds the composer history badge)
- `GET /v1/sessions/<id>/pii-decisions-view` — DB-ONLY modal data (9.204.6): one row per DECIDED value (latest decision = current status) from the `pii_decisions` ledger, NO live re-scan → `{items:[{rule_id,label,category,value,masked,value_hash,source,source_label,status,false_positive,turn_action,fake_value,history}], counts:{status:N}, item_count}`. `status` ∈ open|anon|accepted|local|fp. `value` = cleartext (it's the user's own chat; the modal shows it). `source` is NORMALISED — every non-file origin ('', 'message', 'history') collapses to `'history'` so the modal shows ONE "Chat-Verlauf" group (9.204.7); only `file:<name>` stays per-attachment. This is what `openPiiHistoryModal` renders — reading the ledger (not a live scan) avoids the phantom-"open" duplicates a re-scan caused when its string form for a value differed from the stored decision.
- `GET /v1/sessions/<id>/pii-history-detail` — per-finding LIVE history scan WITH source attribution (9.203.0; no longer used by the modal — kept for any caller needing a fresh scan). Scans each message + each attachment SEPARATELY → `{findings:[{rule_id,label,category,action,confidence,masked,value_hash,source,source_label,history}], decision_history:{value_hash:[…]}, counts, finding_count, worst_action, truncated}`. Values masked server-side (cleartext never crosses the wire here); `value_hash` joins to `/v1/gdpr/decisions`. Each finding carries `history` and there's a top-level `decision_history` map (9.204.0): chronological `[{turn_action,false_positive,fake_value,by,by_id,at}]` with resolved display names (empty uid → "System") — the 'who decided what when' trail both GDPR modals render. `decision_history` is returned even when the scanner is disabled (it's independent of detection). Feeds `openPiiHistoryModal` + the pre-send modal's seen-finding trail.

## Document Review (GDPR + Classification reviewer)

Per-document reviewer surfaced in the Data view, the project tree (right-click),
and right-panel attachments. Auth required, NOT admin-gated. Disk files are never
modified — anonymisation is stored + applied in-flight only at the read seam that
already anonymises (see 05-internals).

- `POST /v1/data-review/analyze` — body is multipart upload (one file) OR
  `{agent_id, project, path}` (path validated against the project's input
  folders) OR `{agent_id, project, source_hash}` (resolves an ingested doc's
  local source). Returns `{review_id, filename, status, text, violations:
  [{id, kind: pii|classification, start, end, label, why, excerpt, ...}],
  overrules, anonymised}`. Reuses a prior review when the content hash matches.
- `POST /v1/data-review/overrule` — `{review_id, violation_id, explanation}`
  (or `{..., remove: true}`) — accept a violation with a written reason.
- `POST /v1/data-review/anonymise` — `{review_id}` → builds the reversible
  shape-preserving anonymisation, stores `anon_text` + the encrypted de-anon
  index, sets status `anonymised`.
- `POST /v1/data-review/revert` — `{review_id}` → clears the anonymisation
  (original is used again; overrule history kept unless `drop_overrules`).
- `GET /v1/data-review/<id>` — full review; `GET /v1/data-review/list`;
  `DELETE /v1/data-review/<id>`.
- `GET /v1/data-review/<id>/export` — download a self-contained anonymised copy
  with the review metadata + de-anon index embedded (round-trips back in).
- `POST /v1/data-review/state` — `{refs:[{kind,ref}]}` → batch badge states
  (`none|checked|violations|anonymised`) for tree/attachment badges.

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
- `POST /v1/context/compact` — `{session_id, force?}` trigger LCM (returns
  409 `auto_lcm_active` when the session's model has auto-LCM on)

## Chat cleanup (auto archive + delete)

- `GET /v1/cleanup/config` — auto archive/delete settings (any logged-in user):
  `{enabled, archive_after_days, delete_after_days, run_interval_seconds}`.
- `POST /v1/cleanup/config` (admin) — save `{enabled?, archive_after_days?,
  delete_after_days?}` (day-counts must be ≥0; **0 = that stage disabled**).
  Persists to `config.json → chat_cleanup` AND the live config, so the
  `chat-cleanup` daemon picks it up next cycle (no restart). Whole feature is
  off (default) unless `enabled:true`.
- Behavior: a chat idle ≥ `archive_after_days` that is **purely private** (not
  shared), **not memorized** (no `session/<id>` wiki page, `save_to_memory=0`)
  and **not referenced** (no favourite / unfinished background task / in-flight
  turn / workflow) is auto-archived; anything archived ≥ `delete_after_days`
  (by `archived_at`) is **deleted — including its wiki page + MemPalace drawer**.
  Opening an archived chat does NOT revive it (un-archive via
  `POST /v1/sessions/manage {action:"archive"/"...unarchive"}` / the sidebar).
  Idle is by `last_active`, which is now also bumped when a chat is **opened**
  (active chats only).

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

- `GET /v1/files/download?path=` / `/files/preview?path=` / `/files/zip?path=<dir>` / `POST /files/save {path,content}` (write/create a text file) (zip a directory tree, skips .git/.cbm-cache/venvs) / `/files/tree?root=`

## Sharing / Teams / Favourites / Channels

- `GET /v1/share?kind=&id=` — visibility info for any sharable object
- `POST /v1/share` — `{kind, id, visibility, team_id?, extra_user_ids?}`
  kinds: `chat | project | schedule | workflow | artifact`
- `POST /v1/share/transfer` — `{kind, id, new_owner_user_id}` admin
- `GET /v1/teams` / `POST /v1/teams` — team CRUD
- `GET /v1/user-teams` — caller's team memberships
- `GET /v1/favourites` / `POST /v1/favourites` / image variant.
  `item_type` ∈ `chat | project_chat | project | workflow | schedule |
  artifact | translation`. For `translation`, `item_id` is either a tab name
  (`text|document|audio|live` — pins the tab) or a `translate_history` entry id
  (pins a specific saved translation, owner-scoped).
- `GET /v1/channels` — list team channels

## Feedback (👍/👎 on responses)

Per-user thumbs-up/down (+ optional comment) on any assistant response or
result, across all surfaces. Keyed `UNIQUE(surface, target_id, user_id)` — a
user re-rating the same response upserts their own row.

- `POST /v1/feedback` — submit/upsert. Body `{surface, target_id, session_id?,
  rating, comment?, context_snapshot?}`. `surface` ∈ `chat | brainy | workflow |
  schedule | translation | classification`; `rating` ∈ `up | down`. Any
  authenticated user. 400 on bad surface/rating/missing target_id.
- `GET /v1/feedback/mine?surface=&session_id=` — the caller's own feedback rows
  (used by the UI to restore the highlighted thumb after reload). Each row also
  carries `msg_count` and `unread` (admin replies the user hasn't seen → the
  widget's unread dot).
- `GET /v1/feedback?surface=&rating=` — **admin only** — list all feedback
  (403 for non-admins). Each row is enriched with `user_name` (resolved
  display_name → username → id) and a `thread` array (the conversation).
- `DELETE /v1/feedback/<id>` — **admin only**.

### Feedback conversation (threaded)

Once a feedback row exists, user and admin exchange short one-line messages
(emoji welcome, capped at 300 chars). The feedback row stays the anchor (rating
+ first comment); the back-and-forth lives in `feedback_messages`.

- `GET /v1/feedback/<id>/thread` — messages for one feedback row, oldest first.
  Readable by the **rater** (matching `user_id`) or an **admin**; 403 otherwise,
  404 if the anchor is gone. Returns `{feedback, thread}`.
- `POST /v1/feedback/<id>/message` — body `{text}`. Appends one message;
  `author_role` is derived from the caller (admin → `admin`, else `user`). Only
  the rater + admins may post. Posting marks the thread read for the author and
  bumps the anchor's `updated_at`. Returns `{message, thread}`. 400 on empty
  text / bad role.
- `POST /v1/feedback/<id>/seen` — the rater records having read the thread
  (clears their unread dot).

## Services / Notifications / Backup / Status

- `GET /v1/status` — server uptime + version (+ technical `changelog`)
- `GET /v1/changelog/curated` — **public**: curated end-user version history
  (German, benefit-oriented) for the sidebar version-history modal. Returns
  `{current_version, current_date, entries:[{version,date,title,body,audience,versions}]}`.
  Handmaintained in `engine/changelog_curated.py` (NOT the technical changelog).
- `GET /v1/doctor` — admin: static config-health checks (model→provider
  integrity, provider gaps, MemPalace + KG health, **GDPR/classification
  scanner-disabled warnings**). `POST /v1/doctor/live` adds live probes (test
  embedding, provider credential resolution). Returns `{findings[], summary}`.
- `GET /v1/lib-versions` — admin: installed versions + local install dates of
  the external libraries Brain depends on, probed across all four Python envs
  (server-python, MemPalace-venv, `.venv_sdk`, `.venv_crawl4ai` — shells the
  venv interpreters for theirs). Returns `{python, platform, groups[]}` (each
  group = component → libs with `version`/`installed`/`status`). Read-only;
  `installed` is the pip-install date (dist-info RECORD mtime), NOT a live PyPI
  check. Powers Settings → Allgemein → **Bibliotheken**.
- `GET /v1/services/models` — admin: every service-model slot (default,
  chat-summary, **classifier** (Prompt-Klassifikation/Auto-Routing), fan-out,
  KG-extraction, TTS, transcribe) + OCR, each with a
  resolve status (`ok`/`unset`/`missing`/`disabled`) + the dropdown option
  lists. Also returns a `conversion` block: the per-file-type extractor
  **matrix** (`{ext, markitdown, own_extractor}`) + `markitdown_available` +
  `pdf_engine`. `POST /v1/services/models` — save any subset (model-id strings,
  `''` to unset, an `ocr:{engine,provider,model}` object, or a
  `conversion:{markitdown_exts:[…], pdf_engine:'pymupdf4llm'|'markitdown'|'fitz'}`
  object — exts validated against formats with an own extractor; bad pdf_engine 400).
  **Fail-loud**: an unknown model id or OCR provider is rejected 400 — never
  coerced to a default. Powers Settings → Allgemein → **Service-Modelle** (incl.
  the Dokumentkonvertierungs-Matrix in the read_document/OCR area).
- `GET /v1/doc-styles` — admin: list document style presets (name +
  description) + a YAML `template` + a structured `defaults` object (the
  built-in style shape, used to pre-fill a new preset in the form editor);
  `?name=X` returns one preset's raw `yaml` AND a `parsed` object (the preset
  deep-merged over the defaults, full shape — what the form editor reads).
  `POST /v1/doc-styles {name, yaml}` validates the YAML parses to a dict, writes
  `agents/main/skills/doc-styles/<slug>.yaml` (name sanitised, no traversal);
  `{name, delete:true}` removes it (incl. its logo file). A **logo** can be
  uploaded with the same POST: `{logo_data:<base64>, logo_ext:".png"}` writes
  `<slug>.logo.<ext>` next to the preset (≤5 MB, png/jpg/gif/bmp/webp);
  `{logo_remove:true}` deletes any. `GET /v1/doc-styles?logo=<file>` serves the
  stored logo image (for the editor preview). Presets set fonts/colors/layout +
  **running header/footer/logo** that write_document + render_diagram apply
  (style="<name>"). Powers Settings → Allgemein → **Dokument-Stile** (a WYSIWYG
  form editor — color pickers, font dropdowns, header/footer/logo controls, live
  preview — that builds the YAML; storage stays YAML on disk).
- `GET /v1/services` — daemon status (mempalace-miner, chat-sync, …)
- `GET /v1/services/log?name=&lines=` — tail a service log
- `POST /v1/services/telegram` / `/services/server` — start/stop/restart.
  `/services/server` also persists `config.default_model` (the
  Settings → Server → Standardmodell dropdown; 9.21.4).
- `POST /v1/restart` — restart Brain (graceful)
- `GET /v1/warmup/status` / `POST /v1/warmup/trigger`
- `GET /v1/sidecar/status` / `POST /v1/sidecar/restart`
- `GET /v1/queue/status` / `POST /v1/queue/cancel`
- `GET /v1/searxng/status` / `POST /v1/searxng/restart` — admin: the
  self-hosted SearXNG subprocess (status/pid/uptime/health/breaker).
- `GET /v1/searxng/engines` — admin: last per-engine health snapshot +
  `next_auto_at`. `POST /v1/searxng/test-engines` — run the probe now.
- `GET /v1/crawl4ai/status` / `POST /v1/crawl4ai/restart` — admin: the
  crawl4ai headless-render subprocess (port 8422). No-ops unless
  `config.json → crawl4ai.auto_start` is set.
- `GET /v1/traces` / `/v1/traces/<id>` — LLM-call traces
- `GET /v1/audit` / `/v1/audit/export` — audit log
- `GET /v1/cache/stats` / `POST /v1/cache/clear`
- `GET /v1/notifications` / `/unread` / `POST /notifications/{settings,dismiss,read}`
- `GET /v1/backup/info` / `POST /v1/backup` / `POST /v1/restore`
- `POST /v1/refine` — `{text, purpose, tier, caveman}` one-shot refine LLM call. `caveman` (0–3) is the ONLY input-side caveman application: the refiner is told to write tersely AND the returned `refined` text is rule-compressed (`_caveman_compress_text`) to that level — so the query the user sends is itself caveman (the system prompt + tool descriptions are never compressed; v9.120.0). `purpose` ∈ `chat_prompt`(default)/`scheduled_task`/`soul`/`profile_field`. `tier` ∈ `polish`(default)/`engineer`: **polish** = conservative grammar/clarity cleaner (intent verbatim); **engineer** = intent-extract + restructure, grounded in active model hint + resolved tool names + project instructions (`scheduled_task` adds unattended stop-condition/safeguard discipline; `soul` becomes a structural editor; `profile_field` always falls back to polish). Engineer keeps good drafts unchanged and asks-back on hopelessly-vague drafts rather than inventing scope. Response echoes `tier`.

## LLM Wiki

User-visible, editable markdown wiki with user/team/global scoping (a page may also carry a `project_id`). Every save is mirrored into the matching MemPalace wing so pages are searchable — and the wiki is now the **sole** feeder for chat-derived wings (`user__`/`team__`/`wiki_global`, and `project_chat__<id>` for project-tagged pages). Ingested project knowledge (`project__<id>`) is unaffected. Each request runs as the authenticated caller; access is enforced (global = anyone, user = owner, team = member).

- `GET /v1/wiki/tree?filter=mine|team|global|all&project_id=&team_id=` — flat list of accessible pages (UI builds the tree from `parent_id`/`position`). `filter`: **mine** (my user pages) · **team** (a team's pages; `team_id` optional → all my teams) · **global** (pages for all) · **all** (union of everything accessible to me — default). Legacy `?scope=` accepted.
- `GET /v1/wiki/pages/<id>` — one page (`id, scope, owner_id, team_id, project_id, parent_id, slug, title, body_md, position, source, source_ref, current_version, manually_edited, …`).
- `GET /v1/wiki/pages/<id>/versions` — immutable per-edit snapshots (newest first; each has `version, title, note, created_at/by`).
- `GET /v1/wiki/pages/<id>/versions/<n>` — one historical version (read-only). Only the current version is editable / in MemPalace.
- `POST /v1/wiki/pages` — `{scope, title, body_md?, parent_id?, project_id?, team_id?, source?, source_ref?}` → 201 with the created page.
- `PUT /v1/wiki/pages/<id>` — `{title?, body_md?, project_id?, archived?, tags?}` (a human text change → new version, sets `manually_edited`, re-mirrors; `tags` is an explicit string array — user tags are preserved across auto-tagging). Tree rows + page GET return `tags` (list) + `auto_tags` + `mirrored` (has a MemPalace drawer); tree rows omit `body_md`.
- `POST /v1/wiki/pages/<id>/promote/<n>` — make version `n` current (copied to a new version; re-mirrors). Append-only history.
- `POST /v1/wiki/pages/<id>/move` — `{parent_id?, position?}` restructure (`parent_id:""` = top level).
- `POST /v1/wiki/pages/<id>/generate` — `{kind: summary|podcast, include_children?}` → generates a summary (LLM) or podcast (LLM script + TTS MP3) from the page (+ optional subtree) and saves it as a new CHILD page (synchronous). Podcast links the MP3 artifact via an `[[audio:<artifact_id>]]` token.
- `POST /v1/wiki/pages/<id>/media` — multipart upload of an image/audio/video → stored as an artifact; returns `{artifact_id, kind, snippet}` where `snippet` is an `[[image|audio|video:<artifact_id>]]` token to insert in the page (the UI hydrates it to a real `<img>/<audio>/<video>` via authed blob-fetch).
- `DELETE /v1/wiki/pages/<id>` — delete; children re-parent to the deleted page's parent; the page's drawer is purged from its wing.
- Read-aloud is client-only (reuses `/v1/translate/tts`); no wiki-specific endpoint.

Auto-generated pages (chat/Studio/task/workflow) carry `source`+`source_ref`; the feeder calls `wiki_store.upsert_from_source` which **diff-merges** a changed source into the existing page (preserving manual edits) as a new version rather than forking a duplicate. Only the current version is searchable in MemPalace.

## Tasks (delegate API)

- `GET /v1/tasks` — delegated tasks visible to caller

## Background tasks (Hintergrundaufgaben)

Detached, same-agent runs spawned by the `run_background_task` tool. Any
logged-in user (not admin-gated).

- `GET /v1/background-tasks?session_id=` — list this session's tasks
  (`{tasks: [{id,title,status,turn_id,usage_in,usage_out,tool_calls,
  created_at,finished_at,consumed_at,output_len}]}`; status =
  running|done|cancelled|error). Output body is NOT in the list.
- `POST /v1/background-tasks/cancel` — `{task_id}`. Cancels a running task;
  the partial output is kept and the row goes `cancelled`.
- `POST /v1/background-tasks/cancel-tool` — `{task_id, tool_use_id}`. Cancels
  ONE in-flight tool call of a running task (the task keeps going). For
  subprocess-backed tools (`python_exec`/`execute_command`) the process group is
  SIGKILLed — a real kill; for other tools the sidecar just abandons the wait and
  feeds the loop a synthetic error result. 200 if acted on, 409 otherwise
  (already returned / not live).
- `DELETE /v1/background-tasks?task_id=` — remove a finished/aborted row
  (refuses a still-running task with 409 — cancel it first).
- `GET /v1/background-tasks/<id>/transcript` — SSE. Running → live sidecar
  events; finished (or log purged) → one `text_delta` replay of the stored
  output + a terminal `done`.

The finished result is delivered into the spawning chat's NEXT turn wire-only
(never persisted) — see `05-internals.md`.

## Notes on response shapes

- Errors: `{"error": "<message>"}` with non-2xx status.
- SSE streams: `data: {json}\n\n` lines, `event: <type>` optional.
- Most endpoints return `{...}` JSON; list endpoints return
  `{"<plural_key>": [...]}` (e.g. `{"sessions": [...]}`,
  `{"schedules": [...], "running": [...]}`).
