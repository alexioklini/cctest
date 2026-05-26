# Brain-Agent Internals

Reference for "why does it behave this way" questions and for diagnosing
weird states. Most users don't need this — load it only when a task
requires understanding the moving parts.

## High-level architecture

```
launcher.py → server.py (HTTP API, port 8420)
                ├── brain.py            # tools, providers, MemPalace, scheduler
                ├── engine/             # extracted engine modules
                ├── handlers/           # per-endpoint HTTP handlers
                ├── server_lib/         # DB, auth, sessions, notifications
                │
                ├── sidecar/sidecar.py  (port 8421, separate venv)
                │   └─ Anthropic Python SDK 0.101.0
                │      Owns the agentic loop. Brain never iterates LLM rounds.
                │
                ├── searxng/ (port 8088, separate venv) — self-hosted search
                ├── crawl4ai/ (port 8422, separate venv) — headless render
                ├── SQLite              # chats, schedules, costs, traces, context, …
                └── MemPalace (in-process, NOT MCP)
```

All chat + non-interactive LLM calls go through the sidecar via
`POST http://127.0.0.1:8421/turn` (SSE). Tool calls flow back:
sidecar → `POST /v1/tools/call` to Brain → dispatch → result returned.

## Agentic loop (sidecar)

- **Interactive chat**: `handlers/chat.py:worker` builds Anthropic-shape
  messages → `sidecar_proxy.run_turn()` → sidecar streams events
  (`text_delta`, `thinking`, `tool_use`, `tool_dispatch_done`, `done`).
- **Background calls**: scheduler, refine, soul-chat, summary, profile,
  next-prompt, classifier, image-describe, ask_llm, memory-extract,
  promote-skill, KG extract, code-graph summaries, citation re-round,
  translate/* — all use `sidecar_proxy.background_call()`.
- **Cancel**: Brain mints `turn_id`, passes `X-Turn-Id` header. Proxy's
  `_watch_cancel` thread polls `session.cancel_token` and POSTs
  `/cancel/<turn_id>` to sidecar.
- **`AskUserQuestion`**: blocks via `_pending_answers[session_id] +
  Event`. Unblocked by `POST /v1/chat/answer`.

## Resumable streaming

- Worker thread is **not** tied to any HTTP connection. `_handle_chat`
  opens a `LiveStream` on `session.live_stream` before spawning worker.
- A `LiveStream` is an ordered replay log + subscriber fan-out under one
  lock — `attach()` returns `(queue, replay, already_done)` with no event
  lost or duplicated across the attach boundary.
- `GET /v1/chat/stream?session_id=X` reattaches any number of tabs.
  Client disconnect on the reattach endpoint NEVER cancels the worker.
- Incremental persistence: `sessions.streaming_text` / `streaming_meta`
  written by event_callback (~0.4s throttle); cleared in worker `finally`.
- **Brain-restart recovery**: `active_turns` rows + sidecar's per-turn
  event log (`/turn/<id>/events?since=N`, 5-min retention) let Brain
  re-attach after its own restart. If sidecar also died, partial
  `streaming_text` gets promoted to a persisted message tagged
  `*(Server restart — turn lost)*`.

## Provider routing

`resolve_provider_for_model(model)` is the **single source of truth** for
`{api_key, base_url, provider_name}`. Used by chat, delegate, scheduler,
warmup, background. Providers are plain OpenAI-compatible entries in
`config.json → providers`.

Provider-scoped ids exist when multiple providers serve the same model
(`provider/model_id` with `base_model_id`).

## Provider concurrency queue

`LocalProviderQueue` (engine/provider.py):
- `omlx`: 2 (continuous batching sweet spot)
- `cliproxyapi`: 2 (serialized, no batching)
- cloud: 0 (unlimited)

Key = `provider_name`, not base_url.

## Request context (typed, contextvars)

Per-request state (current user, project, exclude_tools, purpose, …) lives
in a typed `RequestContext` dataclass held in a `contextvars.ContextVar`
(`engine/context.py`). Read/write **only** via `get_request_context().<field>`;
enter/teardown **only** via `with request_context(**overrides):` (the
context manager push/token-resets — automatic teardown). The sidecar's
`/v1/tools/call` rebuilds the context per call (`tool_mcp._apply_context`,
inside its own `with`).

The old `_thread_local = threading.local()` request-state bag is **gone**
(Tier G, 9.12.0). Bleed invariant: a fresh thread starts with empty
context, so HTTP and per-task threads are bleed-free — but never set
request context bare on a pooled (`ThreadPoolExecutor`) thread; always
wrap it in `with request_context()`. (DB-connection pooling still uses
`threading.local()` — a separate, untouched pattern.)

## Supervised subprocesses

Three long-lived helper processes, each its own venv, each managed by a
`ProcessSupervisor` subclass (3-crash-in-60s circuit breaker + HTTP health
probe), admin status/restart endpoints:

- **sidecar** (`:8421`) — the Anthropic SDK agentic loop (above).
- **SearXNG** (`:8088`, `SearxngSupervisor`) — self-hosted metasearch
  backing `searxng_search` + the Websuche tab. URL from
  `config.json → searxng.url` via `_searxng_base_url()`. Per-engine health
  is probed in isolation (each engine's `!shortcut`), states `ok`/`empty`/
  `fail`, auto-refreshed every 4h (`_searxng_engine_health_loop`); the
  snapshot is in-memory only. Admin: `/v1/searxng/{status,restart,engines,
  test-engines}`, monitored in Settings → Server.
- **crawl4ai** (`:8422`, `Crawl4aiSupervisor`) — headless Chromium render
  service, `POST /render {url}` → markdown. No-ops unless
  `config.json → crawl4ai.auto_start`. `brain._crawl4ai_render()` degrades
  gracefully when down. Admin: `/v1/crawl4ai/{status,restart}`.

`web_fetch` fallback chain: markitdown HTML→md first; crawl4ai render only
when the converted text is near-empty (<30 chars) on an HTML GET. Every
result is tagged `fetch_method` (raw/markitdown/crawl4ai), surfaced as a
chat-view badge.

## Manual web search (Websuche) + tool lockout

The Websuche tab is human-curated retrieval. `POST /v1/web/search` is a
pure `searxng_search` passthrough (no fetch, no LLM). The user marks URLs
into a basket that is PER SESSION and persisted server-side
(`sessions.web_basket`, saved via `manage {action:"web_basket"}`, loaded
on session open) — it never bleeds from one chat into the next, a fresh chat
starts empty. On send the enabled entries ride as `body.web_urls_to_fetch`.

- **Turn-time + ephemeral**: the worker fetches each URL `force_fresh=True`
  just before the wire build and injects the markdown into a *transient
  wire copy* of the last user message (`_inject_web_preamble_into_wire`).
  `session.messages`/DB stay clean — every send re-fetches, nothing goes
  stale. Per-turn sources are recorded on `metadata.web_sources`
  (wire-stripped, audit/display only).
- **Hard lockout**: when a curated set is present and
  `sessions.allow_further_web` is off, the worker sets
  `get_request_context().exclude_tools = ["web_fetch","exa_search",
  "searxng_search"]`; `resolve_active_tools` subtracts it (generic
  per-turn mechanism, Brain-side — NOT plumbed through the sidecar
  payload). All non-web tools stay live. There is **no** `web_search` tool.
- **Escape hatch**: `sessions.allow_further_web` (sticky, default 0), inert
  when the basket is empty; when on, curated sources are still pre-fetched
  but the model may also search/fetch.

This is a DIFFERENT mechanism from project `web_urls` (mined into the
project wing/KG by the project-sync daemon) — do not merge them.

## Brainy helpdesk bot

A read-only helpdesk assistant (the floating bubble), separate from the
main chat agent.

- **Streaming call**: `POST /v1/helpdesk` runs a dedicated streaming call
  via `sidecar_proxy.helpdesk_call()` with `purpose='helpdesk'` and an
  empty turn session_id (no collision with main chat). History is
  per-USER in `helpdesk_history` (NOT per-session).
- **Context-filtered replay**: one stored per-user thread, but each turn
  records `context_label` (`project:<name>` else `view:<type>`, from the
  view context). The model turn replays only turns matching the *current*
  context + the most-recent few (cap `_REPLAY_MAX_ROWS=24`) — cutting both
  tokens and cross-context bleed without fragmenting storage. Runs before
  the alternation sanitizer (`_build_helpdesk_messages`, the 9.23.1 fix that
  normalises history to strict user/assistant alternation so a malformed
  thread can't 400 the next send). The label also renders as a per-question
  badge in the UI; it's persisted, so badge + replay survive reload/restart.
- **Exclusive skill**: this `brain-agent-guide` skill is gated to Brainy
  (`HELPDESK_ONLY_SKILLS`) — hidden from normal chat unless helpdesk_mode.
- **Fixed read-only tools** (`_HELPDESK_TOOLS`, 16 tools): `use_skill`, the
  three `helpdesk_*` tools, `mempalace_query`, the read/search/context
  tools, the three web tools, and `code_graph_query`. No write/exec tools.
- **Source reach** (9.27.0): in helpdesk_mode `mempalace_query` additively
  searches the shared `brain_code` wing (mined brain-agent source) on top of
  its normal scope — a separate Chroma query pinned to `brain_code`, so the
  project-isolation force-scope is untouched. `code_graph_query` adds exact
  structure lookups (file_summary / callers_of / …) over the same source.
  See "Reading the brain-agent source" below.
- **Per-turn tool enforcement** (9.22.0): `run_turn`/`run_turn_blocking`
  put the resolved tool names in `tool_context['allowed_tools']`;
  `tool_mcp.handle_tools_call` rejects any `tool_use` not in that list
  before dispatch (generic, all purposes; empty list = no enforcement).
  `use_skill` returns companion-page **absolute** paths (`companion_pages`)
  so Brainy stops guessing relative paths.
- **Project-scoped knowledge** (9.26.0): when the user asks Brainy from
  inside a project, the view context's project NAME is passed through to
  `helpdesk_call(project=…)` → the turn's `tool_context['project']`, so
  Brainy's `mempalace_query` force-scopes to that project's `project__<id>`
  wing — Brainy reads the SAME isolated project knowledge the main agent
  does (e.g. "what is this project about?" answers from the mined docs, not
  just metadata). Outside a project, `project` stays empty and Brainy has
  no project knowledge, as before. Per-project isolation is preserved: it
  only ever sees the project the user is currently in.
- **Config**: `config.json → helpdesk {enabled, model, max_rounds,
  system_prompt}`. Model "Auto" resolves to the server default. Edited in
  Settings → Tools → Brainy.

## Reading the brain-agent source (9.27.0)

In production there is **no source code on disk** — only these skill files.
When a user asks about behaviour these files don't cover (an exact default
value, a field name, an edge case), you can reach the actual brain-agent
source in two complementary ways. **Always try these skill files first** —
they're German, curated, high-signal. Reach for the source only when the
docs genuinely don't answer a precise code-level question.

**STEP 1 — NARROW DOWN with `mempalace_query`.** The brain-agent source is
mined into a shared MemPalace wing called **`brain_code`** by the
source-miner daemon (it clones the public GitHub repo each cycle). In
helpdesk mode your `mempalace_query` AUTOMATICALLY also searches `brain_code`
— so query what you're looking for in plain language ("how many helpdesk
history turns are replayed", "default web_fetch timeout") and you get back
candidate chunks, each with a repo-relative `source_file` (e.g.
`handlers/helpdesk.py`, plus `CLAUDE.md` / `05-internals.md` which often rank
high and themselves point you at the right module).

**Treat these as CANDIDATES, not the final answer.** Code embeddings are
fuzzy — the exact line you want may not be in the top chunk, and the most
relevant file may be at rank 2-3 (or hinted at by a CLAUDE.md chunk). Use the
hits + the repo map below to decide which ONE file actually holds the answer.

**STEP 1b — STRUCTURE lookups with `code_graph_query`** (when you already
know a symbol or file). The brain-agent source is also indexed as a code
structure graph. `code_graph_query` takes `query_type` + `target`:
- `file_summary` + a file path → every function/class/method defined in that
  file (great for "what's in handlers/helpdesk.py" — returns qualified names
  like `HelpdeskHandlerMixin._handle_helpdesk`).
- `callers_of` / `callees_of` + a qualified name → who calls it / what it
  calls. `imports_of` / `importers_of`, `inheritors_of`, `tests_for`.
This is exact (not fuzzy) but relation-based: it answers "what's in this
file" and "what relates to this symbol", NOT "find the file for this plain
constant" — for the latter, lean on `mempalace_query` + the repo map. Paths
in results may carry a local clone prefix; strip to the repo-relative part
(everything after `.brain-source-clone/<wing>/`) before building a GitHub URL.

**STEP 2 — read the FULL, CURRENT file from GitHub.** Once you've identified
the file, fetch it raw with `web_fetch` and read the precise value there
(GitHub `main` is live; the mined index can lag by up to one miner cycle):
`https://raw.githubusercontent.com/alexioklini/cctest/main/<source_file>`
e.g. `.../main/handlers/helpdesk.py`. Use the `source_file` path
`mempalace_query` gave you — do NOT invent paths. You can also list every
path via the git tree:
`https://api.github.com/repos/alexioklini/cctest/git/trees/main?recursive=1`

**Order: query to narrow, then ONE targeted GitHub fetch to confirm.** Don't
fetch file after file blindly — let the query + repo map pick the single file
first, then fetch that one and answer. If the query chunk already shows the
exact value, you may answer from it directly, but for an exact constant the
GitHub raw file is the authoritative, current source.

**Repo map** (where things live — mirrors the repo's own CLAUDE.md):
- `brain.py` — tool wiring (`TOOL_GROUPS`, `TOOL_DISPATCH`), `VERSION` +
  CHANGELOG, runtime classes, the tool resolver, warmup.
- `server.py` — HTTP routes (grep the `self.path` dispatch for endpoints).
- `engine/tool_schemas.py` — `TOOL_DEFINITIONS` (every tool's exact schema).
- `engine/tools/<group>.py` — each tool's implementation (file/git/gmail/
  web/translate/delegation/context/misc/ask, and `helpdesk_tools.py`).
- `engine/` — extracted domains: `loop`, `provider`, `model_select`,
  `mempalace_glue`, `classification`, `pii_ner`, `doc_convert`, `kg_extract`,
  `prompt_build`, `context`, `scheduler`, `quotas`, `workflow`, `code_graph`.
- `handlers/` — HTTP handler modules (`chat.py`, `sessions_handler.py`,
  `projects.py`, `providers.py`, `admin.py`, `auth.py`, `classification.py`,
  `helpdesk.py`, `sidecar_proxy.py`).
- `server_lib/` — DB, auth, sessions, `tool_mcp.py` (dispatch).
- `web/index.html` + `web/js/` — the single-page UI.

**Caveats to tell the user, not hide:**
- The mined `brain_code` index can lag the live build by up to one miner
  cycle; GitHub `main` is current but may itself be a slightly different
  commit than the deployed server. When the exact value matters, prefer the
  GitHub raw fetch and cite as "im aktuellen Quellcode (GitHub main)"; you
  can compare the CHANGELOG top entry in `brain.py` to the running version
  from `GET /v1/status` to flag a mismatch.
- You read source to ANSWER, never to act — you are still read-only.
- Don't paste large code blocks at the user; read it, then explain in plain
  German what it does and cite the file path + the relevant line(s).

## Warmup & warm pool

Warmup payload MUST match first-turn payload byte-for-byte —
hour-rounded timestamp, same tools, same `stream_options`. KV-prefix
misses are silent. `claim()` only fires for bare sessions
(`{agent:main, project:'', status:'', note_context:''}`).

`_build_system_prompt` is user-agnostic to preserve cache hits. Per-user
preamble goes in first-user-message instead.

## GDPR / PII scanner

- 71 regex rules in JS (`web/index.html → PIIScanner`) mirrored in Python
  (`brain.py → _pii_rules` + `_pii_scan_text` + `_pii_scan_bare_identifiers`).
- Phase 1 NER: spaCy `de_core_news_md` adds `name|address|organisation`
  in the `contact` category. Loaded eagerly at startup. Runtime control:
  `GET/POST /v1/gdpr/ner-models`.
- Rule order matters — context-gated rules first, `credit_card` after
  national IDs, `phone` after national IDs.
- Single decision point for non-interactive calls:
  `gdpr_pick_model_for_background(model, texts, purpose)` → scan → audit
  → swap to local fallback / raise / warn.
- `is_model_local()` bypasses the block entirely (data stays on-prem).
- Client interlock: `piiBlockActive(chat)` filters dropdown to local-only
  when scanner enabled + server_block + chat has PII.

## MemPalace integration

Imported as a Python package — no MCP, no subprocess.

- **Wing scheme** (ID-only): `user__<uid>`, `team__<tid>`,
  `project__<pid>`, bare names = shared.
- `_resolve_session_wing` priority: project → team → user → empty.
- `mempalace_query` in a project chat is **force-scoped** to
  `project__<id>` and refuses if id is missing (never leaks).

### Two daemons

1. `mempalace-miner` (every 30 min default): walks `AGENTS_DIR`,
   classifies by folder name (`sched-*` → scheduled artifacts;
   `<sid>` → chat folders). Scheduled folders file output-role files
   only (skips intermediates). Chat folders gated on `save_to_memory > 0`.
2. `mempalace-chat-sync` (every 60s): mirrors:
   - chat turns → `room=chat`
   - session summaries → `room=chat_summary`
   - attachment metadata (NOT bytes) → `room=chat_attachment`
   - allowlisted tool_results (`exa_search`, `web_fetch`, `read_document`)
     → `room=reference`

   Closet rebuild per dirty group (`build_closet_lines + upsert`).

### Knowledge Graph

`kg_extract.py` — profile registry: `normative` (12 controlled English
predicates) for policies/regulations/SOPs, `generic` for prose. Chunking
modes: `source_file` (default, re-chunk markdown) or drawer-grouped.
`inference_max_tokens=8000` (reasoning models exhaust mid-JSON below).

Per-source change invalidation: snapshots `kg_extraction_source_state`,
on diff DELETEs `triples` matching exact source_file + progress rows +
orphan-entity sweep.

KG path: `<palace_path>/knowledge_graph.sqlite3`, NOT
`~/.mempalace/knowledge_graph.sqlite3`.

## Project sync daemon

`mempalace-project-sync` (every 6h default), **single-threaded** —
multi-project cycles strictly sequential. Per project:
1. Ensure `mempalace.yaml` matches expected wing (auto-rewrite).
2. Mine `ingested/`, then each input folder. Per-folder cap default 5000.

`total_indexed` is cumulative (survives dedup-only re-runs).
`auto_sync=false` skipped on scheduled cycles, bypassed on manual sync.
Startup wipe drops every drawer in `project__*` wings AND clears
`sync_status` (needs marker-file gate — see backlog).

## Scheduler

- `engine._scheduler` is a singleton. APScheduler-style cron + `@every`.
- Each run = immutable `schedule_history` row (id = run_id).
- Synthetic `session_id = sched-<run_id>` scopes artifacts + traces.
- Per-task `attachments` are referenced in place; never per-run copies.
  `_purge_attachment_paths()` refuses paths without the
  `scheduled_attachments` segment.
- `working_dir` overrides system prompt cwd line. `python_exec` stays
  pinned to artifact folder by design.
- `tool_profile` drives the call's purpose: `""` → `research_minimal`,
  `"interactive"` → `interactive`. Per-task `thinking_level` empty →
  inherit at fire time. `caveman_chat` is per-task. `caveman_system` is
  NOT exposed per task (would invalidate warmup KV prefix).
- **Project binding** (`schedules.project_id`, optional): a project-bound
  task with no explicit `tool_profile` defaults to purpose `interactive`
  (NOT `research_minimal`) — it must behave like a project chat, and the
  lean research_minimal set (write_file/web_fetch/exa_search/searxng_search)
  lacks `mempalace_query`/`read_document`, so it could not read the project
  memory and would hallucinate. An explicit `tool_profile` still wins. The
  fire-path resolves the stored id → project NAME and sets
  `get_request_context().project` (a name) before building the system
  prompt. That single value pulls in the whole project context — the
  project's `instructions` + description in the prompt, `research_mode`,
  and scopes MemPalace `mempalace_query` to the `project__<id>` wing — the
  same path an interactive project chat uses. The same name also goes into
  the sidecar tool-call `_tool_context["project"]` so per-tool-call context
  rebuilds stay scoped. Empty (or a now-deleted project) → agent-global,
  unchanged. Artifacts stay in the agent-global `sched-<run_id>` folder
  (no project-tagging); the project view's "Geplante Aufgaben" tab just
  filters the schedule list by `project_id`.

## Tool resolution (3-layer)

```
effective_enabled  = global.enabled
effective_deferred = global.deferred
if agent_id:
    o = token_config.tool_overrides.<name>
    if 'enabled'  in o: effective_enabled  = o.enabled
    if 'deferred' in o: effective_deferred = o.deferred
if not effective_enabled: drop
if call.purpose not in global.purposes (when set): drop
if effective_deferred and tool not in discovered_tools: drop (surface via tool_search)
```

Purposes: `interactive | transform | memory_summary | research_minimal |
helpdesk`. Purpose is a property of the call, not the agent — agents
cannot override it. Since 9.22.0 the resolved tool list is also enforced
at dispatch (`tool_context['allowed_tools']`), not just at list-build.

## Cost & quotas

- `CostTracker` logs every LLM call to `costs.db`.
- `QuotaManager` (30s cache). Two axes per user: Daily (rolling UTC) +
  Cycle (`monthly|weekly|yearly` w/ anchor). Worst-axis wins.
- Pre-flight gate in `send_message` round 0, AFTER GDPR.
- `is_model_local()` always bypasses (cost = 0).
- `enforce_red`: `warn_only` (default), `force_local` (silent swap to
  `default_local_fallback_model`), `hard_block` (raises).

## User profile daemon

`user-profile` polls every 30 min. Per-user gate:
`daily_summary_enabled` + local hour matches + 23h cooldown.

Worker: 100 most-recently-active chats from last 90 days → sidecar
background_call with `_PROFILE_SYSTEM_PROMPT`. Atomic write via tmp +
`os.replace`. Mirrors to MemPalace wing `user__<uid>, room=user_profile`,
one drawer per `## section`, purge-then-add.

Schema: Work context / Personal context / Top of mind / Recent months /
Earlier context / Long-term background. Third-person, no timestamps,
2-6 sentences per section.

Preamble injection: round 0 reads `<uid>.md` (4KB cap), prepends
`[Auto-maintained user profile: …]` on first user message. Stripped by
`_ALLOWED_MSG_KEYS` so the LLM sees it but the DB does not.

## Lossless Context Manager (LCM)

`ContextManager` with SQLite DAG in `context.db`. Three-level escalation:
leaf summaries → condensation → fallback truncation. Assembly: summaries
(highest depth first) + fresh tail (default 16 messages) within token
budget.

**Manual-only trigger** — chat worker no longer auto-fires LCM. The
status-bar ✂️ button calls `triggerLCM()` → `POST /v1/context/compact`
with `force=true`. The warning banner shows at ≥60% context usage.

## Tools — adding a new one

4 edit sites in `brain.py`:
1. `TOOL_DEFINITIONS` (~line 540+)
2. `TOOL_GROUPS` (~line 1771)
3. The `tool_*` function
4. `TOOL_DISPATCH` (~line 22580)

Per-tool prose (description/when_to_use/warnings/examples/applies_with)
is added via admin UI → `POST /v1/tools/settings`, NOT in code.

## Common pitfalls

- Daemon stdout/stderr → `server.error.log`, not `server.log`.
- Sidecar must NEVER import `claude_cli` (breaks anyio streaming).
- launchctl kickstart needs >6s before HTTP listener binds.
- `MODEL_PROFILES` overlays must only carry request-style knobs (never
  warmup/GPU/etc. — would silently re-enable user-toggled-off fields).
- Sessions are created lazily on first send. SQL hides 0-message
  sessions older than 60s. Startup purge deletes >5min empty sessions.
- Archive ≠ delete: archived sessions keep their drawers.
- Schedule deletes are tombstoned in `config.deleted_models` — only
  "Full Resync" clears them.
