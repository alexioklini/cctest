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
- `GET /v1/sessions` — list chats visible to caller (sharing model applies).
  Sorted by last MODIFICATION (newest message), not by last access — merely
  opening a chat no longer reshuffles the list.
- `GET /v1/sessions/active` — IDs of sessions with a live chat turn running (the
  in-memory `_streaming` set). Drives the "läuft gerade" list pills; returns bare
  IDs only.
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
    "web_abstract_first": false,  # fetch each curated source as a ~1500-char abstract instead of the full page
    "pinned_sources_to_read": [{"key": "...", "name": "..."}],  # Quellen-Pinning (project chats, v9.305.0)
    "gdpr_action": "anonymise|local_model|continue",  # pre-send dialog verdict
    "pii_decisions": [{"rule_id": "...", "value": "...", "false_positive": false, "action": "anonymise|send|local"}]  # 9.383.0
  }
  ```
  `pii_decisions` (9.383.0) is this turn's per-finding decision set from the
  pre-send dialog. The worker is DECISION-DRIVEN: it builds the
  pseudonymisation mapping from the ledger (`pii_decisions` table) merged
  with this inline set — it never re-detects. On a FIRST send (no session_id
  when the dialog ran) the inline set is the only channel; the server also
  persists it into the ledger. FP-marked values are purged from a reused
  mapping and stay in the clear. An anonymise turn arriving with NO
  decisions and an empty mapping triggers a fail-loud guard fallback scan
  (never a silent cleartext egress).
  When `web_urls_to_fetch` is present, the worker pre-fetches each URL
  fresh at turn time and injects the markdown into a transient wire copy
  of the user message (never persisted). Unless `sessions.allow_further_web`
  is set, the three web tools (`web_fetch`, `exa_search`, `searxng_search`)
  are locked out for that turn so the model works strictly from the
  curated set. Fetched sources are recorded on the assistant turn's
  `metadata.web_sources` (rendered as "Webquellen dieser Anfrage").
  `pinned_sources_to_read` (Quellen-Pinning, project chats only) works the
  same ephemeral wire seam for PROJECT documents: each `key` is resolved
  against `GET .../projects/<name>/sources` (keys the enumerator doesn't
  yield are ignored — no raw paths), the documents' FULL text (cap 12
  sources / 60k chars each, overflow noted) is injected wire-only, and the
  set used is recorded on `metadata.pinned_sources` [{key,name,chars,error}].
  The pinned set persists per session (`sessions.pinned_sources`, manage
  action `pinned_sources`; echoed by GET /messages as `pinned_sources`) —
  unlike the Websuche lockout, pinning never disables tools.
  Response: SSE events (`text_delta`, `thinking`, `tool_use`, `tool_result`,
  `done`, `error`, …).
- `GET /v1/chat/stream?session_id=<sid>` — re-attach to a live turn (SSE)
- `POST /v1/chat/cancel` — `{session_id}` cancels the active turn AND (Stopp-
  cascade, 2026-07-13) every background subagent THAT turn spawned
  (`spawn_turn_id` match; earlier turns' detached tasks keep running — they
  have their own Stopp). Returns `{status, subagents_cancelled}`.
- `POST /v1/chat/pause` — `{session_id}` soft-pause the running turn at the next
  round boundary (current round + in-flight tool finish first). Emits `paused`.
- `POST /v1/chat/resume` — `{session_id}` resume a paused turn. Emits `resumed`.
- `POST /v1/chat/inject` — `{session_id, message}` splice a clarification into
  the RUNNING turn; the model sees it next round (emits `injected_pending` then
  `injected_message {round,text}`). Distinct from the message queue. The web UI
  renders the lifecycle as cards in the right panel's Aktivität tab.
- `POST /v1/chat/btw` — `{session_id, message}` ask a side question answered in a
  SEPARATE thread (web UI: the right panel's "Zwischenfragen" tab) without
  touching the running turn. Grounded in live turn state
  (current round, active tool + elapsed, completed steps). Runs as an independent
  background call; emits `btw_start` then `btw_done {btw_id, answer}`.
- Message queue persists per session (`sessions.message_queue`, manage action
  `message_queue`, returned by `GET /messages`) — messages typed while a turn
  streams, auto-sent as normal turns when it finishes.
- Goal-Modus (v9.256.0): while a session carries an ACTIVE goal
  (`sessions.goal_status='active'`, set via manage action `goal`), EVERY send
  runs a post-turn judge loop server-side — after each persisted reply an LLM
  judge (`engine/goal_judge.py`, model `config.goal_judge_model`) checks the
  reply against the goal; unmet → the continue-instruction is persisted as a
  visible user message (`metadata.goal_continue`) and the turn re-runs, until
  fulfilled / judged impossible / iteration cap. New SSE events:
  `goal_judge_start {iteration, max}` · `goal_verdict {fulfilled, status:
  active|fulfilled|capped|judge_error, iteration, max, reasoning,
  instruction (v9.267.0, only on status=active: the continue-instruction)}` ·
  `goal_continue {text, iteration, max, assistant_text, text_rounds}`
  (= iteration boundary: client closes the current assistant bubble). The
  single terminal `done` carries `goal {status, iteration, max, reasoning}`.
  `fulfilled` auto-ends the loop (badge ✓ until the goal is cleared/replaced).
- `POST /v1/chat/answer` — `{session_id, answer}` unblocks `AskUserQuestion`.
  Also accepts `{task_id, answer}` (v9.312.5) to answer a BACKGROUND sub-agent
  blocked on `ask_user`: the pending slot is keyed on the task (not the session),
  so several sub-agents can ask at once without colliding with each other or with
  the chat's own question. ACL is still checked against the task's session. The
  open question itself is exposed on `GET /v1/background-tasks/running` as
  `pending_question` (the poller the subagent tree already uses).
- `POST /v1/chat/plan-review` — `{session_id, action: approve|clarify, plan?,
  executor?, message?}` resolves a pending MoA delegate-plan review (9.285.0;
  fired only on `body.interactive=true` turns; SSE `moa_plan_review` /
  `moa_plan_review_done`); executor is enabled+ACL-validated
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
   `goal {goal:"<text>", goal_max_iterations?:1..10}` sets the Goal-Modus goal
   (non-empty → status `active`, iteration reset; empty → clears everything;
   re-setting a fulfilled/capped goal re-arms it). Echoed by `GET /messages` as
   `goal_text/goal_status/goal_iteration/goal_max_iterations` and by the
   session list (sidebar 🎯 pill).

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
- GDPR project presets (`gdpr_preset`, v9.341.0–9.347.0) were REMOVED in
  9.348.0 — privacy posture is governed by ONE central rule set (Settings →
  GDPR: rule/category actions + confidence bands), identical in every chat,
  project or not. A legacy `gdpr_preset` field in project.json is ignored and
  stripped on the next save. `POST /v1/services/server` accepts
  `gdpr_scanner.web_egress` (`refuse|allow` — v9.386.0, legacy `ask`/
  `block_group` normalise to `refuse` on load), surfaced back in the services
  GET.
- `GET .../projects/<name>/notes` / `POST` — project notes
- `GET .../projects/<name>/docs` — list ingested docs
- `GET .../projects/<name>/sources` — flat list of the project's individual
  pinnable sources (uploads · input-folder files · mined web URLs) as
  `{sources:[{key,name,kind}]}` — the keys the Quellen-Pinning UI stores and
  `POST /v1/chat` `pinned_sources_to_read` resolves (v9.305.0)
- `GET/POST .../projects/<name>/input-folders` — list/edit folders
- `POST .../projects/<name>/input-folders/<idx>` — edit/delete one
- `GET .../projects/<name>/sync-status` — current sync state
- `GET .../projects/<name>/sync-runs` — sync history
- `GET .../projects/<name>/sync-runs/<id>` — one run detail
- `POST .../projects/<name>/sync-now` — trigger immediate sync
- `POST .../projects/<name>/full-resync` — wipe wing + re-mine
- `POST .../projects/<name>/sync-cancel` — abort live sync
- `POST .../projects/<name>/ingest` — upload files (multipart). Since 9.324.0
  ASYNC: the request only stages the bytes and returns immediately with
  `{status:"queued", source_hash}` (the key is reserved at stage time); a
  server-side worker pool (2 threads) runs the extraction — incl. OCR for
  scanned PDFs — in the background, then kicks project-sync. Unsupported file
  types still fail synchronously. (Previously extraction ran inline and a
  scanned PDF blew past the Cloudflare tunnel's ~100s limit → HTTP 524.)
- `GET .../projects/<name>/ingest-status` — background extraction jobs:
  `{jobs: {key: {state: queued|extracting|done|error|cancelled, filename,
  error, chunks}}, pending}`
- `DELETE .../projects/<name>/ingest-jobs/<key>` — terminate one extraction
  (queued dies instantly; in-flight is flagged and its result discarded).
  On an already-terminal job this dismisses the status entry.
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
- (removed) the per-project **design-system** feature — the `design-system/generate`
  endpoint, `project.json → design_system` field, and its wire injection — was
  taken out. Document styling is handled globally, not per project. Design turns
  still exist: `body.design_context: true` on `POST /v1/chat` (set while the
  design canvas is active on an HTML artifact) injects only the deck/export
  convention wire-only, so drafts stay PPTX/PDF-exportable.
- `GET .../projects/<name>/image` — project thumbnail
- `POST .../projects/<name>/generate` — generate a grounded output from the
  project's sources. Body `{kind: study_guide|briefing|faq|timeline|audio_overview
  |custom:<preset-id>, options?: {focus?: str, length?: short|std|long, audience?:
  str}}` → `{output_id, status:"generating"}`. Requires manage; refuses (400) if
  the project has no sources. Runs async + saved as a `project_outputs` row.
  SHARED endpoint. The text kinds write a cited `.md`; `audio_overview` instead
  runs a different worker — an LLM writes a spoken script, then each line is
  voiced via Voxtral TTS and the MP3 segments are concatenated into one `.mp3`
  (phases: gathering → scripting → voicing N/M). The `.mp3` is the output
  artifact; a `.md` script is saved alongside. `audio_overview` extra options
  (v9.304.0): `audience` (target-listener pitch), `lang` (ISO-639-1, one of the
  9 Voxtral languages; empty = auto-detect from the corpus — a German project
  yields a German episode), and `speakers` (1–4 of `{name?, voice?, persona?}`;
  empty voices are filled from the language-matched roster incl. cloned voices;
  1 speaker = monologue, 2 = dialogue, 3–4 = panel). Legacy
  `host_a_voice`/`host_b_voice` still map onto the first two speakers.
  `custom:<id>` kinds are user-defined presets (below); a preset with
  `per_source: true` runs once PER project source (ingested uploads, input-folder
  files, mined web URLs; cap 40) and files one project-tagged wiki page per
  source (stable `source_ref studio-preset/<id>/<key>` → a re-run re-versions
  the pages) plus ONE combined report row (v9.302.0).
- Custom Studio presets ("Transformations", v9.302.0) — global, stored in
  `config.json → studio_presets`, live-mirrored:
  - `GET /v1/studio/presets` → `{presets:[{id,label,title_prefix,instructions,
    per_source,owner_user_id,created_at}]}` (built-ins are client-side).
  - `POST /v1/studio/presets {label, instructions, title_prefix?, per_source?}`
    → `{status, preset}` (201). Any logged-in user; cap 50 presets.
  - `PUT /v1/studio/presets/<id>` — partial update; owner or admin only (403).
  - `DELETE /v1/studio/presets/<id>` — owner or admin only. Existing outputs and
    wiki pages survive a delete.
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
  init (cancels the run's in-flight LLM turn) → `{status: cancelling|not_running,
  cancelled}`. Manage-gated.
- `GET .../ingested` — list ingested files under an agent
- `POST /v1/files/rename {path, to}` / `POST /v1/files/delete {path}` /
  `POST /v1/files/mkdir {path}` — file-tree editing for Code Mode (used by the
  working-dir tree's context menu + drag-drop). `rename` = rename OR move (`to`
  is a bare new name or a destination path; refuses to overwrite). `delete` is a
  SOFT delete — moves the target into `.brain-trash/<timestamp>__name` at the
  project root (recoverable; never a hard `rm`). `mkdir` creates a folder. All
  share the same `_validate_file_path` allowed-roots gate as `save`/`preview`
  (incl. the code-mode working_dir). NOTE: `_validate_file_path` treats the
  synthetic-admin sentinel (`__system__`, auth-disabled) as see-all and skips
  non-agent entries in `agents/` — fixing a latent bug that blocked writes into a
  code-mode working_dir.
- `GET .../projects/<name>/code-chats` — Code Mode: list the project's
  **Terminal-Chats** — sessions with `status='code_chat'` created by the
  bottom-workspace terminal-chat. These are deliberately EXCLUDED from the normal
  session/project chat lists (`ChatDB.list_sessions` filters the status out by
  default), so they surface ONLY here, under the "Terminal-Chats" section of the
  code-mode bottom panel. Returns `{sessions:[…]}` in the standard list shape
  (id/title/last_active/model/message_count). Each terminal-chat is a regular
  session (created via `POST /v1/sessions {status:'code_chat', project}`) and
  runs through the normal `POST /v1/chat` turn pipeline — same streaming, project
  instructions, code-graph tools.
- `POST .../projects/<name>/terminal/run {command, timeout?}` — Code Mode: run a
  ONE-SHOT shell command in the project's working_dir and return
  `{command, exit_code, output}`. Backs the terminal-chat **`!`** command (e.g.
  `! python forecast.py --region=X`). NOT a PTY (no streaming/stdin/TTY) — a
  request/response exec sharing the `execute_command` config: banned-pattern
  guard (`rm -rf /`/`mkfs`/`dd if=`), login-shell build (full PATH), timeout
  (default 30s, max 300), ANSI-strip, 50k output cap. Code-mode-gated via the
  same access check as the PTY terminal endpoints.

## Scheduler

`GET /v1/schedule` — list visible schedules + currently-running tasks.
Optional `?project_id=<id>` or `?project=<name>` (+ `?agent=`) filters the list
to one project's tasks (used by the project view's "Geplante Aufgaben" tab);
omitting it returns all visible schedules (the agent-global Zeitplan tab).

`POST /v1/schedule` body shape: `{action, ...}`. Action verbs:

| Action | Body | Effect |
|---|---|---|
| `add` | `{name, task, schedule, agent="main", model?, timeout=300, attachments=[], working_dir?, thinking_level?, caveman_chat?, tool_profile?, project_id?, goal?, goal_max_iterations?}` | Create new schedule. `schedule` is a cron expr or `@every 10m` etc. `project_id` (stable uuid) binds the task to a project — the run then executes inside that project's context (instructions, MemPalace `project__<id>` wing, research_mode); the server validates the caller may access the project. `goal` (Goal-Modus) makes the run judge each turn against the goal and auto-continue until met / impossible / `goal_max_iterations` (0 = admin default) / <30s timeout budget left; result gets a German `Ziel: …` suffix and `schedule_history.goal_iterations` records the count. |
| `edit` | `{name, task?, schedule?, model?, timeout?, agent?, new_name?, attachments?, working_dir?, thinking_level?, caveman_chat?, tool_profile?, project_id?, goal?, goal_max_iterations?}` | Partial update. `project_id=""` clears a project binding back to agent-global; `goal=""` turns Goal-Modus off for the task. |
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
- `GET /v1/models/config` — full per-model config (admin; also
  `benchmark_official: {aa_key_set}` — whether the Artificial-Analysis API
  key is configured, never the key itself)
- `POST /v1/models/config` — save model config; supports `action: "sync"`
  to pull from provider's `/models`, `action: "full_resync"` to clear
  deletion tombstones first, `action: "benchmark"` (optional `model_id` /
  `task_type`) to run the benchmark in the background — capability % from
  official leaderboards (Artificial Analysis + LMArena, cached 24h), speed
  from the internal seed run; uncovered models fall back to the internal
  prompt+judge benchmark (see 05-internals "Model benchmark")
- `GET /v1/models/benchmark/status` — live benchmark progress
  (`{running, done, total, current_model, current_task, cells_done,
  cells_total, errors}`; a cell = model × task type, so single-model runs
  still show movement; `current_task` shows "Leaderboard-Daten laden…"
  during the source fetch, source errors land in `errors`)
- `GET /v1/providers` — provider list with status
- `POST /v1/providers` — `{action: "save"|"delete"|"test", ...}`
- `POST /v1/providers/test` — `{base_url, api_key, ...}` probe
- `GET /v1/providers/stats` — per-provider request/error counts

## Costs / Quotas

- `GET /v1/costs?start=&end=&agent=&user=&model=` — flat cost log
- `GET /v1/costs/daily` — aggregated per-day
- `GET /v1/costs/breakdown?window=&agent=&user_id=` — per-use-case × per-model cost for a time window. `window` ∈ `today|week|7d|30d|180d|365d|ytd|all|cycle|last_cycle` (cycle/last_cycle reuse the quota billing-cycle config). Returns `{window,label,since,until,total_cost,total_cost_list,total_cache_savings,total_calls,by_use_case:[{use_case,cost,cost_list,cache_savings,calls,tokens_in,tokens_out,cache_read_tokens,by_model:[…]}]}` — `cost` = verrechnet (Flat-Modelle loggen $0 — `flat_plan:true` ODER `coding_plan`-Verknüpfung mit Plan-Typ ≠ credit, aufgelöst via `model_is_flat_plan`; Fix 9.284.3), `cost_list` = dieselbe Nutzung zum API-Listenpreis (zur Lesezeit aus den Tokens gerechnet, rückwirkend), `cache_savings` = Ersparnis durch Prompt-Caching. Use-cases are display buckets (Chat, Chat-Zusammenfassung, Geplante Aufgaben, Übersetzung, Studio, Deep Research, Audio Overview, Vorlesen, …) collapsed from the raw `cost_log.purpose`; pre-tagging rows show as *Unbekannt (Altdaten)*.
- `GET /v1/costs/rates` — admin: die editierbare Preistabelle. Liefert `{rates, builtin, unpriced}` — `rates` = `config.json → cost_rates` (was der Admin pflegt), `builtin` = die eingebaute Seed-Tabelle (`engine._cost_rates`, read-only), `unpriced` = `[{id,provider,display_name,enabled}]` aller **Cloud**-Modelle, die nirgends einen Preis finden und darum still $0 buchen (lokale Modelle + unit-billed OCR/TTS/STT sind ausgenommen — $0 bzw. Seiten-/Zeichen-/Minutenpreis ist dort korrekt).
- `POST /v1/costs/rates` {rates} — admin: ersetzt die Tabelle **vollständig** (kein Merge — der Client sendet, was er gerade editiert hat, sonst wäre Löschen unmöglich). Preise in $ pro 1 Mio. Token, Schlüssel = exakte Modell-ID **oder Präfix**. Wirkt sofort (mtime-Cache), kein Neustart.
- **Preis-Auflösung (Reihenfolge, erster Treffer gewinnt)**: `models.<id>.cost_input/cost_output` (Modelle-Grid) → `config.json → cost_rates` (diese Tabelle) → `engine._cost_rates` (Code-Seed) → `{0,0,0}`. Bei Präfix-Treffern gewinnt der **längste** (`_match_rate_table`) — sonst würde `gpt-4.1` ein `gpt-4.1-mini` überschatten (Bug bis 9.313.0).
- `GET /v1/plans/usage` — Coding-Plan-/Guthaben-Schätzung aus dem eigenen cost_log (keine Anbieter-Quota-API). Nur Pläne mit verknüpften Modellen (Modell-Link **oder Provider-Vorgabe** — aufgelöst via `brain.resolve_model_plan_id`). Flat-Pläne: pro Fenster (`session_5h` = echtes Anbieter-Session-Fenster mit fester Reset-Uhrzeit, aus Ledger-Zeitstempeln verkettet · `weekly` = 7-Tage-Zyklus ab Abo-Anker · `monthly`; legacy `rolling_*` unterstützt; alles geclippt auf `since` = Plan-Aktivierung) `{limit_tokens,used_est,pct,resets_at}` (gewichtete Tokens, `count.cached` z. B. 0.67 bei Z.ai); Credit-Konten: `{balance_usd,used_usd,remaining_usd,pct}` seit `anchor` (Aufladedatum).
- **Plan-Verknüpfung, zwei Ebenen** (`brain.resolve_model_plan_id` — DER eine Seam, den sowohl die Abrechnung `model_is_flat_plan` als auch die Dashboard-Zuordnung `_plan_models` benutzen): `models.<id>.coding_plan` (Modell-Link) sticht `providers.<name>.coding_plan` (Vorgabe fürs ganze Konto). Feld **abwesend** = erbt die Provider-Vorgabe; Sentinel `coding_plan:"none"` = nimmt das Modell explizit **aus** der Vorgabe heraus.
- `POST /v1/plans/save` {plan} / `POST /v1/plans/delete` {plan_id} — admin: Plan-Objekt anlegen/ändern (Upsert per id; type `flat|credit`) bzw. löschen (löst auch die Modell-Verknüpfungen **und die Provider-Vorgaben** — ein hängender Default würde sonst still weiterbuchen).
- `POST /v1/plans/calibrate` — admin: `{plan_id,window_kind,dashboard_pct}` re-fittet ein Fenster-Limit aus dem echten Anbieter-Dashboard-%; `{plan_id,balance_usd[,anchor]}` = Credit-Aufladung.
- `GET /v1/quotas/me` — caller's daily + cycle usage vs limit
- `GET /v1/quotas/config` — admin: server-wide quota config
- `POST /v1/quotas/config` — admin: save quota config
- `GET /v1/quotas/admin/users` — admin: every user's quota state
- `GET /v1/quotas/admin/breakdown?user_id=&model=` — admin: detail

## MemPalace

- `GET /v1/mempalace/stats` — wing/room/drawer counts
- `GET /v1/mempalace/classifier` / `POST` — chat-sync classifier config
- `GET /v1/composer/defaults` (any logged-in user) / `POST` (admin) — new-chat
  composer defaults `{thinking_level, caveman_mode, memory_mode,
  goal_mode_enabled, goal_max_iterations}`. thinking + caveman + the two
  Goal-Modus knobs live in `config.json → composer_defaults`
  (`goal_mode_enabled` false hides the 🎯 button AND disables the server loop;
  `goal_max_iterations` = default iteration cap 1..10, per-session/task
  override wins); memory_mode writes through to
  `mempalace.chat_sync.classifier.default_mode` (single source). Configured
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
- `GET /v1/data-sources` (admin, v9.363.0; erweitert 9.368–9.375) — db_query/
  rest_query sources with MASKED DSNs (`dsn_set`/`dsn_masked`, the password
  never leaves the server), `access_mode` (ro|rw), `context_preview`
  (none|head|full), `guide {md, skill, auto_generated_at}` (Steckbrief —
  full md only on this admin endpoint), REST fields (`base_url`,
  `allowed_paths`, `auth` with `secret_set` only) + the access policy
  (`access {enabled, roles, teams, users}`, missing config block reported
  as admins-only) + team/user/role lists for the grant pickers +
  `wired_types` (postgres, mssql, rest)
- `POST /v1/data-sources` (admin) — `{action: save_source|delete_source|
  save_access|generate_guide, ...}`; `save_source` takes `source {name,
  type, access_mode, context_preview, guide{md, skill}, dsn|env_key,
  options{…}}` (REST: `base_url`, `auth`, `allowed_paths`,
  `options{timeout_s, max_response_kb}`) + `original_name` (rename; empty
  `dsn`/auth-secret on edit keeps the stored secret; `guide.auto_generated_at`
  survives while md is unchanged); `generate_guide {name}` (v9.374.0) reads
  the live schema (tables/columns/types/FKs/row estimates; REST: skeleton
  from allowed_paths) into a curatable Markdown Steckbrief, persists it as
  `guide.md` + `auto_generated_at` and echoes `md`. Persists to config.json
  AND the live server_config — no restart needed
- `GET /v1/data-sources/available` (v9.371.0, NOT admin — any authenticated
  user, filtered on the db_query access policy) — `{name, type, access_mode,
  guide_set}` only, NEVER dsn/env_key/guide-md; feeds the project-settings
  section + right-panel picker (empty list renders as a hint there)
- `GET /v1/data-sources/<name>/tables` (v9.371.0, policy-gated like
  `available`) — table list for the pickers (`information_schema`, 5 s
  connect timeout, offline source → clean error text, never a 500); REST
  sources answer with `allowed_paths` + `kind:'paths'` (no discovery call)
- `GET /v1/tools/status` — per-tool diagnostic
- `GET /v1/tools/breakdown?agent=` — token cost per tool
- `GET /v1/tools/result?session_id=&tool_use_id=` — full, **uncapped**
  tool-result text (ownership-checked, path-traversal-guarded). When a
  tool result exceeds the in-context budget (>50KB) it is spilled to
  `<agent>/artifacts/*_<sid>/tool-results/<tool_use_id>.txt`; this serves
  that file as a `text/plain` download. The web UI falls back to it when
  its in-DOM copy is the truncated stub after a reload. Works for
  `sched-*` synthetic sessions. (The per-agent `tool_result_char_limit`
  knob was removed in 9.15.2.)

## GDPR / PII

- `POST /v1/attachments/scan` — `{name, content(b64), media_type}` returns PII findings: aggregated `groups` (count/samples) + per-finding `findings_full` (value/confidence/band/disposition, deduped, cap 200/file + `findings_truncated`) + `worst_disposition` (9.197.0) + `classification` block. Since 9.383.0 image/PDF attachments additionally get the checksum-validated MRZ pass (pages 1-3): a verified read emits `mrz_name`/`mrz_passport`/`mrz_dob` findings (real surface value, FP-markable like any finding; placed BEFORE the cap) — an MRZ hit upgrades an otherwise unscannable photo (`media`) to `scanned: true`
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
- `POST /v1/translate/media` — audio/video translate (multipart; optional
  `transcribe_model` = an `audio_transcription`-capable model id, else the
  tools-config default)
- `POST /v1/translate/detect` — language detect
- `POST /v1/translate/tts` / `GET /v1/translate/tts/voices` — TTS
- `POST /v1/translate/live/start` → SSE `GET /v1/translate/live/<sid>` →
  `POST /v1/translate/live/<sid>/chunk` → `/stop` — mic streaming; start body
  takes optional `transcribe_model` (same semantics as media)
- `GET /v1/translate/stt-models` — `{models:[{id,display_name,local}], default}`;
  enabled `audio_transcription` models + the transcribe_audio default (feeds
  the STT-Modell dropdowns in the Audio/Video + Live tabs)
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

### KI-Skill-Generierung (v9.294.0) — Skill aus Chat/Plan erstellen

Parallel zur KI-Workflow-Generierung, aber das Ergebnis ist ein **per-user
Skill** (SKILL.md), der wie ein Chat geteilt werden kann. Jeder eingeloggte
Nutzer darf generieren (nicht admin-gated).

- `POST /v1/skills/generate` — body `{source:{type:"chat"|"plan"|"nl",
  session_id?|text?}, agent_id?, instructions?, attachments?:[{name,text}]}`
  → `{gen_id, status:"generating"}`. Async; Quelle `chat` wird access-checked.
- `GET /v1/skills/generate/<gen_id>` — Poll: `{status: generating|ready|
  ready_with_warnings|error, phase, steps, ...}`; bei ready zusätzlich
  `{slug, display_name, description, body_md, notes, warnings}`. RBAC
  owner-or-admin.
- `POST /v1/skills/generate/<gen_id>/cancel` — laufende Generierung abbrechen.
- `POST /v1/skills/save` — geprüften Entwurf persistieren: body `{agent_id?,
  slug, display_name, description, body_md, visibility?, owner_team_id?,
  extra_member_user_ids?, excluded_user_ids?, source_kind?, source_ref?}`
  → `{status:"saved", slug}`. Owner = Aufrufer; Team-Sichtbarkeit erfordert
  Team-Mitgliedschaft. Schreibt `agents/<agent>/user_skills/<slug>/SKILL.md`
  + `skill.meta.json`.
- `GET /v1/skills/match?task=<text>&agent_id=` — die dem Aufrufer SICHTBAREN
  Skills, die zu `task` passen (find_skills-Ranking: semantisch + Keyword),
  `{matches:[{slug,name,description,score,matched_via}]}`. Nutzt das
  Workflow-Generieren-Modal, um einen Skill zum Referenzieren anzubieten.
- Sharing: `GET/POST /v1/share?item_type=skill&item_id=<slug>&agent_id=` nutzt
  denselben generischen Block wie Chats/Workflows.
- `POST /v1/workflows/generate` akzeptiert zusätzlich (v9.294.2) `skill_ref`
  (vorhandenen Skill via `agent_step skill="…"` referenzieren, kein Inline-Plan)
  und `extract_skill` (Methode zuerst als NEUEN Skill auslagern, dann
  referenzieren).

## MCP

- `GET /v1/mcp/connections` — current MCP connections
- `GET /v1/mcp/registry` — known servers
- `POST /v1/mcp/connect` / `POST /v1/mcp/disconnect`

## Context Manager (LCM)

- `GET /v1/context/config` — admin: LCM config
- `GET /v1/context/stats?session_id=` — current usage
- `POST /v1/context/compact` — `{session_id, force?}` trigger LCM (returns
  409 `auto_lcm_active` when the session's model has auto-LCM on)

## Classifier probe (admin debug)

- `POST /v1/admin/classify` (admin) — `{message}` or `{messages: [...]}` (batch
  cap 50) → `{results: [{message, analysis, scratchpad_choice}]}`. Runs the
  PRODUCTION prompt classifier (`resolve_task_analysis` — same model/config the
  chat worker uses) inside the running server and reports the scratchpad choice
  it would drive. Use it to measure classifier discrimination on a question set
  before wiring any classifier-driven routing (added for the calibrate-routing
  investigation, v9.299.0).

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
- `GET /v1/artifacts/browse?agent_id=&limit=&source=&context=` — cross-session
  artifact grid. `source` = `chat|scheduled|translation` (origin tag).
  `context` = `chat|project` splits by whether the originating session belongs to
  a project (drives the Startseite- vs. Projekte-Artefakte views); absent = all.
  Rows carry `session_project`/`session_project_id` for the split.
- `GET /v1/artifacts/<id>/content` — body
- `GET /v1/artifacts/<id>/download` — file download
- `GET /v1/artifacts/<id>/export?format=pdf|pptx|docx&version=N` — (v9.353.0,
  Design-Modus Phase C; docx v9.360.0) export an HTML artifact. `pdf` =
  print-accurate Chromium render (crawl4ai render service `POST /pdf`);
  `pptx` = image slides, one `<section data-slide>` = one 16:9 slide
  (screenshots via `POST /screenshot` + python-pptx; pixel-accurate,
  deliberately NOT editable in PowerPoint); `docx` = editable Word document
  (htmldocx, in-process, no render service): headings/tables/lists/raster
  images survive as real Word structure, CSS layout does not. **PDF
  artifacts** additionally export to `docx` (v9.361.0, pdf2docx —
  layout-faithful, same engine as the translate feature); any other format
  on a PDF → 400. Other non-HTML → 400; no `data-slide` sections on pptx →
  422 with guidance; render service down/unconfigured on pdf/pptx → 503 (no
  fallback); htmldocx/pdf2docx missing on docx → 503; pdf2docx conversion
  failure (image-only scans) → 422

## Workflows / Workers / Nodes

- `GET /v1/workflows/executions` / `GET /v1/workflows/executions/<id>`
- `GET /v1/workflows/history` / `.../<id>` / `.../<id>/file[-preview]`
- `GET /v1/agents/<id>/workflows` / `POST /v1/agents/<id>/workflows/<wid>/run`
- `POST /v1/workflows/executions/<id>/approve` / `/cancel` / `/upload-file`
- `POST /v1/workflows/executions/<id>/pause` / `/resume` (v9.291.2) —
  kooperative Pause: greift am nächsten Top-Level-Statement, ein laufender
  agent_step-LLM-Turn läuft erst zu Ende. `to_dict()`/GET liefert `paused`.
- `POST /v1/workflows/history/<id>/promote-session/<sid>` / `/session`
- `POST /v1/workflows/generate` — KI-Workflow-Generierung (v9.290.0): body
  `{source: {type: chat|plan|nl, session_id?|text?}, agent_id?, instructions?,
  attachments?: [{name, text}] (≤10)}` → `{gen_id}`. Erzeugt einen
  `.flow`-Entwurf + `plan.md` aus einem Chat (bevorzugt den freigegebenen
  MoA-Plan `ausfuehrungsplan.md` + pinnt den Executor als MODEL-Header), einem
  Plan-Markdown oder einer NL-Beschreibung. Draft-only — nichts wird
  automatisch gespeichert.
- `GET /v1/workflows/generate/<gen_id>` — Poll: `{status: generating|ready|
  ready_with_warnings|error|cancelled, phase, steps[], …}`; bei ready zusätzlich
  `flow_source`, `plan_md`, `notes`, `warnings[]`, `suggested_name`. RBAC:
  owner-or-admin. `POST .../<gen_id>/cancel` bricht ab.
- `POST /v1/agents/<id>/workflows` akzeptiert seit v9.290.0 optional `plan_md`
  (Plan-Sidecar `<name>.plan.md`); der Einzel-GET
  `/v1/agents/<id>/workflows/<name>` liefert `plan_md` mit.
- `GET /v1/workers` / `/v1/workers/recent`
- `GET /v1/nodes` / `POST /v1/nodes` / `/v1/nodes/poll` / `/result` / `/execute`

## Files

- `GET /v1/files/download?path=` / `/files/preview?path=` (returns content + `size`/`mtime`) / `/files/stat?path=` (just `{mtime,size}` — cheap poll for the editor auto-reload) / `/files/xlsx-grid?path=&sheet=&rows=500` (v9.263.0 — a spreadsheet as STRUCTURED grid JSON `{sheets:[{name,header,rows,total_rows,truncated,sheet_title,row_nums}]}` + `size`/`mtime`, parsed by the agent's xlsx-toolset loader incl. multi-table split + merged-header composition; xlsx/xlsm/csv/tsv, caps 2000 rows/100 cols, 30MB; feeds the UI table preview. `sheet_title`+`row_nums` (v9.264.0) map grid cells back to absolute sheet coordinates for the inline edit) / `POST /files/xlsx-cell {path, sheet, row, col, value, mtime?}` (v9.264.0 — write ONE cell of an existing workbook, 1-based absolute coordinates; `mtime` from xlsx-grid enables the 409 conflict check; value coercion: empty→leer, Zahlen typisiert, `=`-Präfix bleibt Formel; keep_vba für .xlsm; kein artifact-version churn — same policy as /files/save) / `GET /files/xlsm-vba?path=` (v9.265.0 — VBA module sources of a macro-enabled file as `{modules:[{name,code}]}` via `doc_convert.list_vba_modules` (oletools, never executed); xlsm/xls/xlsb/docm/pptm; feeds the ⚙-module tabs in the grid viewer, read-only + .bas export — writing VBA back needs Excel by design) / `GET /files/file-diff?path_a=&path_b=` bzw. `?path=&git=head` (v9.318.0 — die zwei SEITEN eines Text-Diffs als `{a, b, label_a, label_b}` für den Diff-Tab im Bottom-Panel; `git=head` liest Seite A aus `git show HEAD:<rel>` im Repo der Datei, neue/untracked Datei → leere Seite A; Guards: `_validate_file_path`, 5 MB, Binär-NUL-Check; die Ausrichtung rechnet der CLIENT via CodeMirror MergeView) / `/files/zip?path=<dir>` / `POST /files/save {path,content}` (write/create a text file) / `POST /files/open-external {path}` (open in the host's default app — Word/Excel/PDF/…, `_validate_file_path`-gated, OS opener detached) (zip a directory tree, skips .git/.cbm-cache/venvs) / `/files/tree?root=`

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
  KG-extraction, **goal_judge_model** (Goal-Modus Ziel-Prüfung; empty = server
  default model), TTS, transcribe) + OCR, each with a
  resolve status (`ok`/`unset`/`missing`/`disabled`) + the dropdown option
  lists. Also returns a `conversion` block: the per-file-type extractor
  **matrix** (`{ext, markitdown, own_extractor}`) + `markitdown_available` +
  `pdf_engine`. `POST /v1/services/models` — save any subset (model-id strings,
  `''` to unset, an `ocr:{engine,provider,model}` object, or a
  `conversion:{markitdown_exts:[…], pdf_engine:'pymupdf4llm'|'markitdown'|'fitz'}`
  object — exts validated against formats with an own extractor; bad pdf_engine 400).
  **Fail-loud**: an unknown model id or OCR provider is rejected 400 — never
  coerced to a default. Powers Settings → Allgemein → **Service-Modelle** (incl.
  the Dokumentkonvertierungs-Matrix in the read_document/OCR area). Saves take
  effect **immediately** (9.294.3: the handler mirrors the slots into the live
  server config — no restart needed); only the Telegram model still requires a
  restart of the Telegram service.
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
  Settings → Server → Standardmodell dropdown; 9.21.4), plus
  `chat_summary_model`, `classifier_model`, `auto_route_classifier_mode`,
  (9.279.0) `model_sync_auto_enable` (bool — seed default for NEWLY
  discovered models on provider sync; `false` = new catalog models arrive
  DISABLED and must be enabled in the Models tab; toggle lives in the
  Provider tab header, current value in `GET /v1/providers →
  model_sync_auto_enable`),
  `gdpr_scanner{…}`, (9.275.0) `benchmark_aa_api_key` (Artificial-Analysis
  API key for the official-leaderboard benchmark; empty string clears it;
  stored in `config.benchmark_official.artificialanalysis_api_key`) and
  (9.268.0) `moa{enabled, task_pools (9.269.0 matrix:
  {task_type: [model ids]}, replaces the legacy reference_pool +
  gate_task_types pair), task_modes (9.271.0: {task_type: 'answer'|'plan'}),
  max_references, reference_max_tokens,
  reference_timeout_s}` — the MoA virtual model. Task types are validated
  against the classifier enum, models against known+enabled models (400 on
  typos); empty task_pools columns are dropped. `GET /v1/services →
  server.moa` returns the effective blob incl. `task_type_vocab`;
  `GET /v1/status → moa_enabled` gates the composer's 🧬 entry.
- `POST /v1/restart` — restart Brain (graceful)
- `GET /v1/warmup/status` / `POST /v1/warmup/trigger`
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

- `GET /v1/wiki/tree?filter=mine|team|global|all&project_id=&team_id=&q=` — flat list of accessible pages (UI builds the tree from `parent_id`/`position`). `filter`: **mine** (my user pages) · **team** (a team's pages; `team_id` optional → all my teams) · **global** (pages for all) · **all** (union of everything accessible to me — default). Legacy `?scope=` accepted. `q` (optional): a literal, case-insensitive substring filter matched over **title + tags + body text** (the body is matched server-side before being stripped from the row payload) — this is the wiki-tree search box, distinct from the semantic `GET /v1/wiki/search`.
- `GET /v1/wiki/pages/<id>` — one page (`id, scope, owner_id, team_id, project_id, parent_id, slug, title, body_md, position, source, source_ref, current_version, manually_edited, …`).
- `GET /v1/wiki/pages/<id>/versions` — immutable per-edit snapshots (newest first; each has `version, title, note, created_at/by`).
- `GET /v1/wiki/pages/<id>/versions/<n>` — one historical version (read-only). Only the current version is editable / in MemPalace.
- `POST /v1/wiki/pages` — `{scope, title, body_md?, parent_id?, project_id?, team_id?, source?, source_ref?}` → 201 with the created page.
- `GET /v1/wiki/search?q=&limit=` — semantic knowledge search for the global
  search modal (v9.306.0): `{wiki:[{page_id,title,scope,snippet,similarity}],
  memory:[{source,wing,snippet,similarity}]}`. `wiki` = cross-wing wiki pages
  (caller's user/team wings + wiki_global); `memory` = the caller's own
  MemPalace wing (wiki hits deduped out). Read-only, LLM-free. The modal pairs
  it with `GET /v1/sessions/search` (chat full-text).
- `POST /v1/wiki/from-message` — `{session_id, message_id}` → save ONE assistant
  reply as a wiki page (`{status, page_id, title}`; the per-message bookmark
  button, v9.303.0). Session access-checked; scope=user, project-tagged when the
  session belongs to a project. `source_ref message/<id>` keeps re-saves
  idempotent — the same reply re-versions the same page (no LLM merge).
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

- `GET /v1/background-tasks/running` — all RUNNING tasks across sessions
  (`{tasks: [{id, session_id, title, model, created_at}]}`; non-admins see
  only their own sessions'). Feeds the left-sidebar subagent tree (9.312.0).
- `GET /v1/background-tasks?session_id=` — list this session's tasks
  (`{tasks: [{id,title,status,turn_id,usage_in,usage_out,tool_calls,
  created_at,finished_at,consumed_at,spawn_turn_id,retry_of,output_len}]}`;
  status = running|done|cancelled|error|timeout|empty — `timeout` = the
  enforced 1h wall-clock limit fired (partial kept), `empty` = finished
  without error but with no output; both retryable via the
  `retry_background_task` tool). Output body is NOT in the list.
- `POST /v1/background-tasks/cancel` — `{task_id}`. Cancels a running task;
  the partial output is kept and the row goes `cancelled`.
- `POST /v1/background-tasks/cancel-session` — `{session_id}`. Cancels ALL
  running background tasks of one chat (Termchat-Spinner „alle stoppen").
  Returns `{cancelled: n}`.
- `POST /v1/background-tasks/cancel-tool` — `{task_id, tool_use_id}`. Cancels
  ONE in-flight tool call of a running task (the task keeps going). For
  subprocess-backed tools (`python_exec`/`execute_command`) the process group is
  SIGKILLed — a real kill; for other tools the loop just abandons the wait and
  feeds the loop a synthetic error result. 200 if acted on, 409 otherwise
  (already returned / not live).
- `DELETE /v1/background-tasks?task_id=` — remove a finished/aborted row
  (refuses a still-running task with 409 — cancel it first).
- `GET /v1/background-tasks/<id>/transcript` — SSE. Running → attaches to the
  runner's per-task LiveStream (replay + follow, 5s keepalives): a leading
  `request {title,prompt}`, then Brain-vocabulary events (`text_delta`,
  `thinking_start/delta/done`, `tool_call`, `tool_result` — result VIEW capped
  at 4000 chars, `result_chars` carries the true length —, `usage`) up to the
  terminal `done {status,error,usage,tool_calls}`. Finished (or Brain restarted
  mid-run) → stored replay: the persisted `tool_events` as
  `tool_call`/`tool_result` pairs (result view capped at 4000 chars like
  live), then one `text_delta` of the output + `done` — so a reloaded
  Subagenten-Karte renders like the live view (9.312.0). The leading
  `request` event also carries `model` (the actual executing model). (Before
  9.308.0 the live branch proxied the deleted sidecar and silently degraded to
  the stored replay.)

The finished result is delivered into the spawning chat's NEXT turn wire-only
(never persisted) — see `05-internals.md`.

## Notes on response shapes

- Errors: `{"error": "<message>"}` with non-2xx status.
- SSE streams: `data: {json}\n\n` lines, `event: <type>` optional.
- Most endpoints return `{...}` JSON; list endpoints return
  `{"<plural_key>": [...]}` (e.g. `{"sessions": [...]}`,
  `{"schedules": [...], "running": [...]}`).
