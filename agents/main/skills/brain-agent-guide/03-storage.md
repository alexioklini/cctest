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
        project.json           # id, instructions, instruction_files,
                               # research_mode, input_folders, sync_status
        ingested/              # uploaded files, one chunk per file named
                               #   <original-filename-stem>__NNN.md (the source
                               #   name with its extension dropped; NNN = chunk
                               #   index). Legacy ingests kept the older
                               #   ingest-<hash>-NNN.md scheme and still resolve.
                               #   Ingest min_chunk_size=1 → even a very short
                               #   real document (a note/template, < 400 chars)
                               #   is stored as one chunk; only a 0-char
                               #   extraction is rejected (v9.160.8).
                               #   Folder import sends rel_path so same-named
                               #   files in different groups get distinct keys
                               #   (Bericht / Bericht-2), no overwrite (v9.160.9).
        web-urls/              # mined project web_urls, one .md per URL named
                               #   <url-slug>_<YYYY-MM-DD-HHMM>.md (a -<hash8>
                               #   is inserted before the _ ONLY when two URLs
                               #   slugify the same). timestamp = last CONTENT
                               #   change/mine; file kept when content unchanged
        instruction-files/     # supplementary instruction files: owner-uploaded
                               #   docs (any type) that complement project.json
                               #   `instructions`. NEVER mined — the system prompt
                               #   lists their disk paths and the model reads them
                               #   on demand with read_document (like a chat
                               #   attachment). Tracked in project.json
                               #   instruction_files[{filename,size,added_at}];
                               #   binaries get a .brain-extracted/ .md companion.
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
                               # data_sessions, favourites, feedback, reactions,
                               # read_cursors, kg_extraction_log/progress/state,
                               # chat_mempalace_sync, closet_regen_progress,
                               # project_sync_runs, helpdesk_history,
                               # background_tasks
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
thinking_level TEXT ('' = unset → default at send; else none|low|medium|high; per-session, restored on reload),
team_id TEXT, visibility TEXT (user|users|team|global),
project_id TEXT, workflow_run_id TEXT,
research_mode_override INTEGER (NULL=use project default),
streaming_text TEXT, streaming_meta TEXT,
extra_member_user_ids TEXT (JSON list),
excluded_user_ids TEXT (JSON list),
last_system_prompt TEXT, gdpr_action_pref TEXT,
allow_further_web INTEGER (0/1, sticky; lifts the Websuche tool lockout),
web_basket TEXT (JSON list of curated Websuche sources
  {url,title,snippet,query,enabled} — PER SESSION, never shared across
  chats; '' = empty),
chat_audio_overview TEXT (JSON cache of the chat-podcast button's last
  Audio Overview {content_hash,artifact_id,audio_file,script_file,
  spoken_lines,cost}; lets a re-click on an unchanged chat replay instead
  of regenerating; '' = none)
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
tool_profile TEXT ('' = research_minimal, 'interactive' = full),
project_id TEXT ('' = agent-global; else stable project uuid — the
  fire-path resolves id→name and runs the task inside that project's
  context: instructions, MemPalace project__<id> wing, research_mode)
```

### schedules.db → schedule_history (one row per run)
Columns include: `id` (= run_id, synthetic session = `sched-<id>`),
`schedule_name`, `status` (running|success|error|cancelled), `started_at`,
`finished_at`, `output`, `error`, `cost`, `tokens_in/out`, `model_used`,
`user_id`, `artifacts` (JSON), `traces` (JSON ids).

### costs.db → cost_log
```
id INTEGER PK, agent TEXT, session_id TEXT, user_id TEXT,
model TEXT, provider TEXT, key_name TEXT,
tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
tool_round INTEGER,
purpose TEXT,        -- use-case tag — EVERY LLM call writes one (chat|
                     -- chat_summary|next_prompt|scheduled|background_task|
                     -- delegate_task|studio|deep_research|audio_overview|
                     -- read_aloud|translate_*|lang_detect|helpdesk|soul_chat|
                     -- refine|ask_llm|kg_extract|code_graph_summary|lcm_*|
                     -- memory_*|relationship_discovery|user_profile|
                     -- citation_reround|auto_route_classify|ocr|…);
                     -- '' = pre-tagging legacy. $0/local + zero-usage calls
                     -- are logged too (audit completeness).
created_at TEXT      -- UTC, sqlite datetime('now')
```

Empty `user_id` = pre-quota legacy rows. Empty `purpose` = pre-v9.89.0 legacy
rows (the column is additive; one row per LLM round). OCR rows stash pages and
TTS rows stash chars in `tokens_in` with an explicit `cost_usd` (char/page-billed,
not token-billed). The `/v1/costs/breakdown` endpoint groups by `(purpose, model)`
and collapses `purpose` into display use-case buckets.

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
size, created_at, message_idx)` — 5MB cap per version. `message_idx` is the
**1-based producing-turn number** ("Anfrage N") used by the right-panel
artifact grouping (count of user messages at write time — NOT an array
position; a position would drift on the client, whose message array expands
the in-memory tool rows that never reach the DB).

### chats.db → active_turns
`session_id TEXT PK, turn_id TEXT, model TEXT, started_at REAL`.
Used for Brain-restart turn recovery.

### chats.db → pseudonym_maps
Encrypted GDPR pseudonym maps. Decrypt with `pseudonym.key`.
Admin only — see `/v1/sessions/<sid>/gdpr-maps[/<id>]`.
Also holds the de-anon index for a `data_reviews` anonymisation (session id
`review:<review_id>`).

### chats.db → pii_decisions (per-finding interactive review, 9.196.0)
```
decision_id PK, session_id, user_id, turn_id, created_at,
rule_id, value_hash (sha256(rule|value), per-session dedupe), raw_value (capped 512),
confidence REAL, band, disposition, turn_action, false_positive INTEGER, source
```
One row per PII finding the user reviewed in the pre-send dialog. Drives:
(1) "already analysed" — a decided value isn't re-asked; (2) FP-for-chat — a
`false_positive=1` value skips anonymisation server-side
(`handlers/chat._filter_pii_false_positives`); (3) global learning —
`ChatDB.pii_decision_stats()` = per-rule FP-rate. Methods:
`record_pii_decisions` / `get_session_pii_decisions` / `pii_decision_stats` /
`delete_session_pii_decisions`. Endpoints under `/v1/gdpr/decisions`.

### chats.db → data_reviews (GDPR + classification document reviews)
```
review_id TEXT PK, user_id TEXT, created_at/updated_at REAL,
content_hash TEXT (reuse key — re-open/re-upload finds the prior review),
source_kind TEXT (upload|project_path|project_doc|attachment),
source_ref TEXT (path | source_hash), filename TEXT,
status TEXT (reviewed|anonymised), text TEXT (capped 512KB, the ORIGINAL),
anon_text TEXT (the stored anonymised version shipped to the LLM),
violations_json TEXT, overrules_json TEXT, anon_mapping_id TEXT (→ pseudonym_maps)
```
Per-document review state for the reviewer (Data view / project tree /
attachments). `review_id` is derived deterministically from
(source_kind, source_ref, user_id) for on-disk files, so a re-mine resolves to
the same row. Badges (`engine/review_state.py`) read state from here. The disk
file is never modified.

### chats.db → helpdesk_history (Brainy conversation)
```
id INTEGER PK AUTOINCREMENT, session_id TEXT (vestigial, empty),
user_id TEXT, role TEXT, content TEXT, created_at REAL,
context_label TEXT (where the turn was asked: "project:<name>" |
                    "view:<type>"; ''/NULL = legacy/any)
```
Index `idx_helpdesk_history_user(user_id, id)`. **Per-USER, not
per-session** — Brainy's history follows the user across chats and is NOT
cascade-dropped when a chat session is deleted. Served newest-first +
cursor-paginated by `GET /v1/helpdesk/history`. `context_label` (written on
both rows of an exchange) drives the per-question UI badge AND
context-filtered replay (see `05-internals.md` → Brainy).

### chats.db → background_tasks (Hintergrundaufgaben)
```
id TEXT PK, session_id TEXT, agent_id TEXT, model TEXT, title TEXT,
prompt TEXT, status TEXT (running|done|cancelled|error), turn_id TEXT,
output TEXT (full final text — incl. partial on cancel), error TEXT,
usage_in INTEGER, usage_out INTEGER, tool_calls INTEGER (count),
tool_events TEXT (JSON per-tool list [{name,args,tool_use_id,result,is_error,elapsed_ms}]
                  — assistant.metadata.tools[] shape; drives the panel's tool cards
                  live AND after reload),
group_id TEXT, follow_up TEXT, group_done_at REAL, parent_task_id TEXT (fan-out/join),
created_at REAL, finished_at REAL, consumed_at REAL
```
Index `idx_bgtask_session(session_id, created_at)`. Rows are written by
`engine/background_tasks.py` (the detached runner). `consumed_at` is set when
the finished `output` has been folded into a chat turn (wire-only) — guarantees
each result reaches the model exactly once and never re-enters history. At boot,
any leftover `running` row is reconciled to `error` ("Server restart — task
lost") so the panel shows no zombie. See `05-internals.md` → Background tasks.

### chats.db → project_outputs (generated outputs — Output Presets / Studio / Research)
```
id TEXT PK (uuid), agent_id TEXT, project_id TEXT (uuid hex — the MemPalace wing key),
kind TEXT (study_guide|briefing|faq|timeline|audio_overview|research_report|…),
title TEXT (editable), path TEXT (the saved .md / .mp3 on disk),
artifact_id TEXT (links artifact_versions so Studio can open/version it),
opts TEXT (JSON — the generation options; makes regenerate reproducible),
status TEXT (generating|ready|error), error TEXT, citations INTEGER (count of [Quelle: …]),
created_at REAL, created_by TEXT (user_id), finished_at REAL
```
Index `idx_project_outputs_project(project_id, created_at)`. ONE row per output a
project generates. Inserted `generating` by `POST …/projects/<name>/generate`,
flipped to `ready`/`error` by the daemon worker (`engine/output_gen.py`). The .md
lives under `agents/<agent>/projects/<name>/outputs/<kind>-<id>.md` and is
registered as an artifact under synthetic session `output-<id>`. At boot any
leftover `generating` row is reconciled to `error` ("Server restart — generation
lost"). SHARED store (browsed by Studio; Audio Overview + Deep Research write to it
too — Deep Research saves its report here as `kind=research_report`). See
`05-internals.md` → Output generation.

### chats.db → research_runs (Deep Research run record)
```
id TEXT PK, agent_id TEXT, project_id TEXT, topic TEXT,
status TEXT (running|done|error|cancelled), phase TEXT (planning|searching|reading|writing|done),
progress TEXT (JSON {subqueries,candidates,fetched,kept}), budget TEXT (JSON {fetches,tokens,rounds}),
report_output_id TEXT (→ the project_outputs research_report row), proposed TEXT (JSON [{title,url,snippet,in_project,trust_hint}]),
coverage_note TEXT, error TEXT, cancel INTEGER (cooperative-cancel flag),
created_at REAL, created_by TEXT, finished_at REAL
```
Index `idx_research_runs_project(project_id, created_at)`. ONE row per Deep Research
run (`engine/deep_research.py`). The RUN record; the report itself is a
`project_outputs` row (`kind=research_report`). The worker polls `cancel` at each
checkpoint (E3). Boot reconcile flips a leftover `running` row to `error`. See
`05-internals.md` → Deep Research.

### chats.db → wiki_pages / wiki_page_versions (LLM Wiki)
```
wiki_pages: id TEXT PK (uuid hex16), agent_id TEXT, scope TEXT (user|team|global),
  owner_id TEXT, team_id TEXT, project_id TEXT (optional tag), parent_id TEXT (''=top level),
  slug TEXT, title TEXT, body_md TEXT (live markdown = current version), position INTEGER,
  source TEXT (manual|chat|studio|task|workflow|activity), source_ref TEXT (origin object,
  e.g. 'session/<id>' — re-version key so a changed source updates the SAME page),
  manually_edited INTEGER (a human touched it → merge preserves), current_version INTEGER
  (= MAX(version)), archived INTEGER, created_at/by, updated_at/by
wiki_page_versions: id PK, page_id TEXT, version INTEGER, title TEXT, body_md TEXT,
  note TEXT ('manual edit'|'merged from chat'|'restored from vN'|'created from <source>'),
  created_at/by
```
Only the CURRENT version (MAX) is editable + mirrored to MemPalace. Promote copies an old
version to a new current version (append-only). Re-wikify of a changed source LLM-diff-merges
into the existing page as a new version (`wiki_store.upsert_from_source`).
Indexes `idx_wiki_scope(scope,owner_id,team_id,project_id)`, `idx_wiki_parent(parent_id,position)`,
`idx_wiki_versions(page_id,version)`. Pages form a tree via `parent_id`/`position`. User-visible,
editable markdown wiki — and the **sole feeder** for chat-derived MemPalace wings: every save mirrors
the page into its wing (`user__`/`team__`/`wiki_global`, or `project_chat__<id>` when `project_id` set)
as one drawer `source_file=wiki/<id>`, replacing the old direct chat-sync writes (that daemon is
retired) and the obsolete `MemoryStore` .md files. Ingested project knowledge (`project__<id>`) is
unaffected. CRUD in `engine/wiki_store.py` (access-checked: global=anyone, user=owner, team=member);
endpoints `/v1/wiki/*` (see `01-api.md`). See `05-internals.md` → LLM Wiki.

### chats.db → feedback (👍/👎 on responses)
```
id INTEGER PK AUTOINCREMENT, surface TEXT, target_id TEXT,
session_id TEXT (''=none, e.g. Brainy), user_id TEXT, rating TEXT ('up'|'down'),
comment TEXT, context_snapshot TEXT (short copy of the rated response/title),
created_at REAL, updated_at REAL,
UNIQUE(surface, target_id, user_id)
```
Indexes `idx_fb_surface(surface)`, `idx_fb_rating(rating)`. `surface` ∈
`chat | brainy | workflow | schedule | translation | classification`;
`target_id` is that surface's stable id (chat=message id, brainy=helpdesk_history
id, workflow=execution_id, schedule=run id, translation=entry id,
classification=scan_id). **Per-user** — the UNIQUE key means a user re-rating the
same response overwrites their own row (created_at preserved). Written by
`POST /v1/feedback`; admin reads via `GET /v1/feedback`.

### chats.db → feedback_messages + feedback_seen (threaded conversation)
```
feedback_messages: id INTEGER PK, feedback_id INTEGER (FK→feedback.id),
  author_role TEXT ('user'|'admin'), author_user_id TEXT, text TEXT,
  created_at REAL                       -- index idx_fbmsg_fid(feedback_id)
feedback_seen: feedback_id INTEGER, user_id TEXT, last_seen_at REAL,
  UNIQUE(feedback_id, user_id)
```
The feedback row is the **anchor** (rating + first comment); each further
one-line message (emoji welcome, ≤300 chars) is a `feedback_messages` row.
`author_role` distinguishes the rater (`user`) from an admin reply (`admin`).
`feedback_seen` is a per-user read cursor: an admin message newer than
`last_seen_at` counts as unread → the widget's unread dot. A new message bumps
the anchor's `updated_at` (re-sorts it to the top of the admin list). Written by
`POST /v1/feedback/<id>/message` + `/seen`; read via `/thread`.

### context.db (LCM)
Nodes + edges of the lossless context manager DAG. `nodes(id, session_id,
depth, content, token_count, …)`, `edges(parent_id, child_id, kind)`.

### code-graph.db
Tree-sitter AST snapshots. `files(path, sha256, lang, …)`,
`symbols(qname, file_id, kind, …)`, `edges(src, dst, kind)`.

### MemPalace storage
- Palace root: from `mempalace.yaml → palace_path`
  (typically `~/.mempalace/<palace_name>/`).
- Vector store: a **Qdrant** service (native process on `localhost:6333`,
  WAL-backed ANN, scalar int8 quantization) — selected via the `MEMPALACE_BACKEND`
  env. ONE shared collection per type (`mempalace_drawers` / `mempalace_closets`)
  for all wings, filtered by a `wing` metadata field. Embeddings are computed
  Brain-side (MLX, `embeddinggemma-300m`); Qdrant stores only the vectors.
  SQLite at `<palace>/knowledge_graph.sqlite3` for triples. (Earlier backends —
  embedded ChromaDB, then a brute-force `sqlite_exact` interim — were retired to
  escape ChromaDB HNSW corruption.)
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
