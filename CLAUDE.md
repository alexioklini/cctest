# CLAUDE.md

These rules apply to every task unless explicitly overridden. Bias: caution over speed on non-trivial work; use judgment on trivial tasks.

## 12 Rules
1. **Think before coding** — state assumptions, ask rather than guess, present interpretations when ambiguous, stop when confused.
2. **Simplicity first** — minimum code, nothing speculative, no single-use abstractions.
3. **Surgical changes** — touch only what you must, don't refactor/reformat adjacent code, match existing style.
4. **Goal-driven** — define success criteria and loop until verified, don't blindly follow steps.
5. **Model only for judgment calls** — classification/drafting/summarization/extraction yes; routing/retries/deterministic transforms no. If code can answer, code answers.
6. **Token budgets** — 4k/task, 30k/session. Summarize and restart near budget; surface the breach.
7. **Surface conflicts, don't average** — pick one pattern (more recent/tested), explain why, flag the other.
8. **Read before you write** — read exports, callers, shared utilities first.
9. **Tests verify intent** — encode WHY, not just WHAT. A test that can't fail on logic change is wrong.
10. **Checkpoint after each step** — summarize done/verified/left; don't continue from a state you can't describe.
11. **Match conventions even if you disagree** — conformance > taste; surface harmful conventions, don't fork silently.
12. **Fail loud** — "completed"/"tests pass" is wrong if anything was skipped. Surface uncertainty.

---

Guidance for Claude Code in this repo. **Non-obvious invariants only** — factual catalogs (tool list, endpoints, config fields) live in the code; grep/read it.

## Keeping the brain-agent-guide skill current

`agents/main/skills/brain-agent-guide/` is the knowledge base **Brainy** (the read-only helpdesk bot) reads to answer users. It is NOT auto-derived from code — it drifts unless maintained. **Standing rule: when a change adds or alters a user-facing feature, an HTTP endpoint, an agent tool, a DB schema, or a UI control, update the matching skill file in the SAME change.** Map: `01-api.md` (endpoints), `02-tools.md` (agent tools + groups/purposes), `03-storage.md` (DB schemas + disk layout), `04-recipes.md` (operator how-tos), `05-internals.md` (architecture/behavior), `06-user-manual.md` (web-UI walkthrough + FAQ, **written in German** to match the UI; tech terms stay English per [[feedback_german_ui_everywhere]]), `SKILL.md` (routing). Bump the version in both places per [[feedback_version_two_places]]. A `git pre-push` hook (`.githooks/pre-push`, enabled via `git config core.hooksPath .githooks` — run once per clone) warns when watched feature code changed without a skill touch; it's a backstop, not a substitute. Override a false positive with `SKILL_DOC_OK=1 git push`.

## Repository Structure

- `launcher.py` — Gateway CLI (start/stop/restart, launch frontends)
- `server.py` — HTTP API daemon (launchd, port 8420)
- `client.py` — Shared HTTP/SSE client library
- `brain.py` — Core orchestration: tool registry **wiring** (`TOOL_GROUPS` + `TOOL_DISPATCH`), runtime classes (AgentConfig, MemoryStore, ProjectManager, MCPManager, TaskRunner, WorkflowEngine, ContextManager, LocalProviderQueue), warmup/first-turn-prefix, tool-resolver, GDPR/PII + classification glue, KG entity-indexing, hooks. Tool impls + schemas + most domains live in `engine/` — brain.py re-exports them so `brain.X` resolves.
- `engine/` — Extracted modules (see `engine/CLAUDE.md`): `loop`, `provider`, `models`, `scheduler`, `tasks`, `quotas`, `context`, `workflow`, `code_graph`, `prompt_build` (`_build_system_prompt`), `model_select` (`MODEL_PROFILES` + `resolve_provider_for_model`), `tool_exec` (dedup/sanitise/compress + `_ok`/`_err`), `tool_schemas` (`TOOL_DEFINITIONS`), `mempalace_glue` (`tool_mempalace_query` + memory/KG), `ingest`, `pii_ner`, `classification`, `doc_convert`, `kg_extract`, `engine/tools/*` (every `tool_*` impl: file/git/gmail/web/translate/delegation/context/misc/ask).
- `handlers/` — HTTP handler modules (see `handlers/CLAUDE.md`)
- `server_lib/` — DB, auth, sessions, notifications, profile helpers
- `tui.py`, `telegram.py` — Terminal + Telegram frontends
- `web/index.html` + `web/js/` — Single-page UI. Global-scope `<script>` files, fixed load order (api → state → utils → nav → sessions → chat_* → panels_* → settings_* → user_admin → monitors → init; init.js loads LAST, only load-time caller). **NO ES modules/bundler** — every fn/var is a browser global; cross-file calls rely on load order (lazy/click-driven, so order only needs every global defined before init.js). Files split per-domain, all <2k LOC (`settings.js`→`settings_*`, `panels.js`→`panels_*`, `chat.js`→`chat_*`). **Gate before editing JS**: `cd web/js && ./js_gate.sh` (ESLint no-undef/no-redeclare + net-globals-count invariant + Playwright smoke w/ zero-console-error; smoke needs dev server up). Moving a fn = relocate one global, count stays constant.
- `desktop/` — Electron shell (CORS-free IPC + lazy llama.cpp host)
- `config.json` — Providers, server, Telegram (gitignored)
- `agents/<name>/` — `soul.md`, `agent.json`, `skills/`, `mcp.json`; SQLite DBs in `agents/main/`

## Architecture

```
launcher.py → server.py (8420)                ┌──────────────────────────┐
                ├── brain.py + engine/        ─┤ sidecar/sidecar.py 8421  │
                │   (wiring, classes, glue,    │  Anthropic Python SDK    │
                │    tool impls/schemas)       │  agentic loop owner      │
                ├── handlers/sidecar_proxy.py ►│                          │
                ├── server_lib/tool_mcp.py ◄───┤  POSTs /v1/tools/call    │
                ├── SQLite (chats, scheduler, context, costs, traces)
                └── MemPalace (direct in-process, no MCP)
```

All chat + non-interactive LLM calls go through the **sidecar** subprocess (separate venv, anthropic 0.101.0). Brain owns tool registry wiring + dispatch, MemPalace/scheduler/projects/MCP routing, runtime classes. The sidecar owns the agentic loop. Providers are plain OpenAI-compatible `config.json` entries; Brain hands the sidecar an Anthropic-shape payload + provider env (CLIProxyAPI translates back to each upstream wire format).

## Agentic Loop (sidecar)

The sidecar (`sidecar/sidecar.py`) owns the loop. Brain never iterates LLM rounds.

- **Interactive chat** (`handlers/chat.py:worker`): builds Anthropic-shape messages from `session.messages`, calls `sidecar_proxy.run_turn()` → `POST :8421/turn` (SSE), drains events through `event_callback`, persists final reply + thinking rows.
- **Background calls** (scheduler, refine, soul-chat, summary, profile, next-prompt, classify, image-describe, ask_llm, memory-extract, promote-skill, KG extract, code-graph summaries, citation re-round, translate/*): all route through `sidecar_proxy.background_call(...)`, a synchronous wrapper around `run_turn_blocking`.
- **Tool dispatch**: sidecar emits `tool_use` → POSTs to Brain `/v1/tools/call` (auth-exempt, nonce-protected, localhost-only). `server_lib/tool_mcp.py` reconstitutes request context from the payload, dispatches to `engine.TOOL_DISPATCH` (or MCP fallback), returns result.
- **Resumable**: Brain attaches a `LiveStream` to the proxy's SSE drain (see below).
- **Cancel**: Brain mints `turn_id` (via `X-Turn-Id`); proxy's `_watch_cancel` thread polls `session.cancel_token`, POSTs `/cancel/<turn_id>`.
- **`AskUserQuestion`**: blocks via `_pending_answers[session_id]` + `Event`, unblocked by `POST /v1/chat/answer`.

Migration record: `SDK_MIGRATION_PLAN.md` + `SDK_MIGRATION_HANDOVER.md` + `SDK_PHASE5_PROGRESS.md`. Native-loop relics (`_run_delegate`, `send_message`, `_handle_openai_response`, `_middleware_*`, guided execution, variance kill-switches, worker-subagent envelopes) deleted in Phase 5 — don't reintroduce.

## Resumable Streaming

Chat worker thread is **not tied to any HTTP connection**. `_handle_chat` opens a `LiveStream` on `session.live_stream` before spawning the worker; worker drives `run_turn(...)`, emits **every** event into it. A `LiveStream` = ordered replay log + subscriber queues: `emit()` appends + fans out; `attach()` returns `(queue, replay_snapshot, already_done)` under one lock (no loss/dup across attach boundary).

- **`POST /v1/chat`** is one subscriber: after `t.start()` calls `_stream_live_to_client(live, worker_thread=t)`.
- **`GET /v1/chat/stream?session_id=X`** re-attaches: replays from turn start, follows live until terminal. Single `idle` if no turn running. **Any number of tabs may attach.** Disconnect NEVER cancels — only `POST /v1/chat/cancel` does.
- **Incremental persistence**: `sessions.streaming_text`/`streaming_meta` hold in-flight reply, written on `text_delta` (~0.4s throttle), cleared in worker `finally`. `GET /messages` returns `streaming: true` + text while live; read only when `_streaming` True → always fresh (reloaded `_streaming` is False, so stale-after-restart never surfaces).
- **Worker `finally` order**: emit `error` if `not live.done` → `_streaming = False` → `live_stream = None` (None means `_streaming` already False so `idle` can't loop) → clear `streaming_text`.
- **Brain-restart recovery**: each turn writes `active_turns(session_id, turn_id, model, started_at)`. Sidecar keeps per-turn event log w/ monotonic `seq` (`/turn/<id>/events?since=N` SSE, 5-min retention). On boot `recover_active_turns_on_boot()` waits for sidecar `/health`, spawns `_recover_one_turn` per row (re-attach, re-stream, persist tagged `metadata.recovered=True`). If sidecar died with Brain → 404 branch promotes partial `streaming_text` tagged `*(Server restart — turn lost)*`.
- **Client**: `buildStreamCallbacks(chat, isActive)` builds the SSE callback map (shared by `API.streamChat` + `API.attachStream`). `openSession()` re-attaches when `GET /messages` says `streaming: true`; on reconnect **drops trailing `thinking` DB rows** (replay re-emits via `thinking_done`) and does NOT pre-seed `streamingText` (replay rebuilds fully — would double).
- In-memory `session.messages` is the conversation handed to sidecar; intermediate tool exchanges stay inside it — only user msg, `thinking` rows, final assistant msg reach DB. `_rollback_messages` fires only on cancel/error.

## Multi-Provider Routing

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}` (chat, delegate, scheduler, warmup, background). Providers are plain OpenAI-compatible `config.json → providers` entries.

**Provider-scoped IDs**: when multiple providers serve one model, stored as `provider/model_id` with `base_model_id`. Historical scoped ids (`OMLX/*`, `Bifrost/*`, `mistral/*`) still route. Bifrost retired 8.5.0.

## Chat File Attachments

Files → `state._pendingFiles[]` as base64, sent as `body.files` (legacy `body.images` for Telegram). Per-file routing checks model `raw_formats` (MIME patterns):
- **Multimodal**: MIME match + base64 + <20MB → OpenAI `image_url` data URI
- **Disk**: else → `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- **Image fallback on non-vision models**: `attachments.image_model` describes via vision LLM; unconfigured → metadata only

## Web Fetch — conversion, headless rendering, provenance

`tool_web_fetch` returns content the LLM sees, tagged with `fetch_method` (surfaced as a chat-view badge): `raw` (non-HTML, or HTML where nothing converted), `markitdown` (our `_html_to_markdown` HTML→md), `crawl4ai` (headless-browser render — itself markdown).
- **Conversion is conditional**: `_html_to_markdown` = `markitdown(text) or text` — on a JS-rendered shell markitdown yields nothing → would fall back to raw. NOT "always markdown" (a past wrong assumption).
- **crawl4ai render service** (own supervised subprocess, like sidecar/SearXNG): `.venv_crawl4ai` (Py 3.13, gitignored) + headless Chromium; `crawl4ai/render_service.py` (stdlib HTTP, port 8422, `POST /render {url}`). `Crawl4aiSupervisor` singleton `crawl4ai_supervisor`, wired in server.py main() + `server_config['crawl4ai']`. Admin `/v1/crawl4ai/{status,restart}`. **Needs `config.json → crawl4ai {auto_start:true,…}`** (gitignored, per-machine) — supervisor no-ops without `auto_start`. Render needs `wait_until='networkidle'+delay` or JS pages return empty.
- **Fallback trigger**: `web_fetch` tries markitdown first; calls crawl4ai only when the converted text is empty (`<30` chars) on an HTML GET — static pages never pay the browser cost. `brain._crawl4ai_render()` degrades gracefully (service down → keep HTTP result).

## Project Web URLs (mined, not injected)

Per-project `project.json → web_urls` [{url,title}] (editor: project-settings 'Web URLs' section; whitelisted in `ProjectManager.update_project`). The project-sync daemon fetches each fresh per cycle (JS-rendered via the crawl4ai fallback) into `pdir/web-urls/weburl-<hash>.md`, **hash-gated** (rewrite→re-mine only on content change), then mines them into the project's MemPalace wing + KG (`_sync_project_web_urls` + sync-loop branch 1b) — reached via `mempalace_query`/KG like any project knowledge. Removed-URL drawers purged via `_is_stale_src` (web-urls/ files are under pdir, so need a file-existence check, not just prefix). **DIFFERENT mechanism** from the per-chat Websuche basket (which is ephemeral per-turn injection) — do not merge them.

## Manual Web Search (Websuche tab)

Human-curated retrieval: the user searches, marks URLs, then the turn works strictly from the marked set.
- **Search-only endpoint** `POST /v1/web/search {query}` → `tool_searxng_search` passthrough (no fetch, no LLM). Any logged-in user (not admin-gated).
- **Basket** (`web/js/panels_websuche.js`): `{url,title,snippet,query,enabled}`, **dedup by url, GLOBAL + localStorage-persisted** (accumulates across searches AND sessions; cleared only by the user). Sources: SERP checkbox · manual URL input · drag&drop (`text/uri-list` + URL-regex on `text/plain`). Per-entry enable/disable (skip but keep) vs remove; bulk enable/disable/clear. Header shows a rough enabled-set token estimate (informational only, no cap — snippet length underestimates full-page weight).
- **Send**: `API.streamChat` reads `webBasketEnabled()` directly (like `state.currentProject`) → `body.web_urls_to_fetch:[{url,title}]`. Basket NOT cleared on send.
- **Server prefetch — TURN-TIME + ephemeral** (`_build_web_sources` in the worker, just before the wire build): fetches each URL `force_fresh=True` (no `read_document` round — local models skip mandated fetches, see [[project_exa_search_only_gemma_fetch_skip]]) and injects the markdown into a **transient wire copy** of the last user message (`_inject_web_preamble_into_wire`, shallow-copies the one message). `session.messages`/DB stay clean — the fetched content NEVER enters history, so every send re-fetches and nothing goes stale (a weather page re-fetches next day instead of replaying yesterday's). Reuses the wire≠stored split the GDPR anonymise path uses. Do NOT concatenate fetched content into the persisted user `message` (the v9.17.0 bug — froze an 80KB page into history + bloated the turn).
- **Per-turn audit/display**: fetched sources recorded as structured `[{title,url,content,error}]` on the assistant turn's `metadata.web_sources` (wire-stripped by `_ALLOWED_MSG_KEYS` → audit-only, never replayed; reaches the client because `load_messages` doesn't filter metadata — the strip is wire-only). Rendered per-turn in the chat view (`renderAssistantMessage` → 'Webquellen dieser Anfrage', each source expandable to full content) AND the session inspector. Distinct fetches show per turn across re-sends.
- **Hard lockout**: when `web_urls_to_fetch` present AND `session.allow_further_web` is False, the worker sets `get_request_context().exclude_tools=["web_fetch","exa_search","searxng_search"]` (the three real web tools — there is NO `web_search` tool). `resolve_active_tools` subtracts `exclude_tools` (generic per-turn mechanism, runs Brain-side — NOT plumbed through the sidecar payload). All non-web tools stay live.
- **Escape hatch** `sessions.allow_further_web` (INTEGER, sticky, default 0): session-persisted checkbox in the Websuche header, **inert when the enabled basket is empty**. When on, lockout is lifted (curated sources still pre-fetched + injected, model may also search/fetch). Manage action `allow_further_web {value}`.

## Artifacts

Files under `agents/<name>/artifacts/<date>_<session_prefix>/` auto-promoted. `write_file` relative path defaults into session's artifact folder.
- Each write/edit → `artifact_versions` row (5MB cap); SSE `artifact_updated`
- **Role**: `_ARTIFACT_INTERMEDIATE_EXTS` (.py/.sh/.js/.json/.csv/.log) → `intermediate`; rest (.md/.html/.pdf/images) → `output`. Browse grid defaults to outputs-only.

## Scheduled Task Runs

Each run = immutable `schedule_history` row (id=run_id) + synthetic `session_id=sched-<run_id>` scoping artifacts + traces.
- **Attachments**: `schedules.attachments` JSON list. Uploaded once, **referenced in place** every fire. `_purge_attachment_paths()` refuses paths without `scheduled_attachments` segment.
- **working_dir**: overrides prompt cwd line. `python_exec` stays pinned to artifact folder by design (file-write tracking depends on it).
- **thinking_level + caveman_chat**: empty `thinking_level` inherits at fire time. `caveman_system` NOT per-task (per-model knob, would invalidate warmup KV prefix). `_validate_thinking_level_for_model` rejects format-mismatched levels.

## Next-Prompt Suggestions

`GET /v1/sessions/<id>/next-prompt` after each turn → dimmed placeholder. Reuses session model + history, `tools=False`, tiny `max_tokens`. **Real cost** — "near-free via prompt cache" claim is dead post-v7.2.0.

## Model Management

Per-model fields in `config.json → models`. `_match_known_model()` seeds from `KNOWN_MODELS`. Manual add: id + provider + display name.

**Optimization profiles** (`MODEL_PROFILES`): sparse overlays, **only request-style knobs** (never resource knobs like warmup — would re-enable user-toggled-off fields). Explicit per-model fields win.
- `speed` (auto local): `deferred_tool_groups=[]` (stable KV prefix > lean-but-shifting), `compact_threshold=0.85`
- `balanced` (auto cloud); `frugal` (cloud-only safe); `custom` (no overlay)
- Profile changes invalidate warm-pool KV prefix.

**Thinking auto-recovery**: on `finish_reason=length` + visible output <25% of completion tokens → `max_tokens` doubles on retry (capped at `max_context`).

**Deletion tombstones**: `config.json → deleted_models`. Honored on startup + every `action: 'sync'`. Only `Full Resync` clears them. Never wire automatic clear.

## Thinking / Reasoning

Sidecar SDK handles reasoning natively — Brain passes `thinking={"type":"enabled","budget_tokens":N}` (or omits) on the payload (`handlers/sidecar_proxy._build_payload`). CLIProxyAPI translates to upstream format (Mistral `reasoning_effort`, OpenAI `reasoning`, Anthropic `thinking`).

For oMLX-direct: warmup must mirror the chat-template `enable_thinking` kwarg byte-for-byte on every non-`none`-reasoning request or KV prefix misses silently (`engine/provider.py` warmup + `_apply_inference_to_payload`).

**Per-model dropdown**: `Off/Low/Medium/High` for cloud reasoning models, `Off/On` for oMLX inline-thinking, hidden for non-reasoning. Stored on session as `thinking_level`; default in `config.json → models.<id>.inference.thinking_level`.

**Persistence**: each round → `role='thinking'` row w/ `metadata.tool_round`. `_ALLOWED_MSG_KEYS`/`_INTERNAL_ROLES` strip thinking rows before wire — UI-only.

## Caveman Mode (Dual)

Two independent settings that compose:
- **System** (`caveman_system` per model, 0–3): compresses system prompt via `_caveman_compress_text()`
- **Chat** (`caveman_mode` in sessions DB, 0–3): appends `CAVEMAN_CHAT_PROMPTS` response-style instruction

Set on request context in chat worker (inside its `with request_context()`). **Cache key for `_build_system_prompt` includes both.**

## Token Optimization

Per-agent `token_config` in `agent.json`:
- `tool_overrides: {<name>: {enabled?, deferred?}}` — per-tool tristate override of global `tool_settings`. Field present = override; absent = inherit.
- `compact_threshold` — float 0–1, override LCM 0.60 default
- `mcp_tool_filter`/`mcp_tool_exclude` — fnmatch, MCP-only

Legacy fields **deprecated**, stripped on save (resolver ignores since v9.0.x): `tool_groups`, `extra_tools`, `deferred_tool_groups`, `include_tools_guide`, `scheduled_task_tools`.

Per-agent `limits`: `max_tool_rounds` (soft cap, hard stop 1.5×), `tool_result_char_limit`, `tool_results_total_tokens`, `context_safety_ratio` (default 0.95). System prompt cached per-session (60s TTL).

## Per-User Cost Quotas

`QuotaManager` singleton (30s config cache). Two axes/user: **Daily** (rolling UTC) + **Cycle** (`monthly`/`weekly`/`yearly` w/ anchor). Worst axis wins.
- **Pre-flight gate** in `send_message` round 0, after GDPR. `is_model_local(model)` always bypasses.
- Modes (`quotas.enforce_red`): `warn_only` (default), `force_local` (silent swap to `default_local_fallback_model`), `hard_block` (`QuotaExceededError`).
- `_log_call_cost` captures `current_user_id`. Empty `user_id` rows = pre-quota legacy. Limit `0` = no limit.

## GDPR / PII Pre-Submit Scanner

71 regex detectors + spaCy German NER, client + server, zero external APIs. `gdpr_pick_model_for_background(...)` is the **single decision point** for non-interactive calls (scan → swap-to-local-fallback / block / warn); hard-block raises pre-LLM only for non-local models. **Rule order in `_pii_rules` is a correctness invariant — never reorder.** Full detail: **[INVARIANTS.md → GDPR / PII](INVARIANTS.md#gdpr--pii-pre-submit-scanner)**.

## Python Code Execution

Opt-in via `code_exec` in `tool_groups`. Subprocess isolation (`sys.executable`), timeout-killed. **Working dir = artifact session folder** — files auto-register; state persists across calls. Auto-artifact fallback: stdout >1K + no files → `output.txt`.

## Document Classification — ARL 20.02.02.06 (v9.6.0)

WPB-policy document-sensitivity detector + enforcement. `ClassificationBlockedError` subclasses `GDPRBlockedError`; classification is enforced via the same single GDPR seam (`gdpr_pick_model_for_background` calls it FIRST). **Strict-always-block invariant** (§1.11). Full detail: **[INVARIANTS.md → Document Classification](INVARIANTS.md#document-classification--arl-20020206-v960)**.

## Provider Concurrency Queue

`LocalProviderQueue` (`engine/provider.py`). `omlx=2` (continuous batching), `cliproxyapi=2` (serialized), cloud=0 (unlimited). Queue key = `provider_name`, not `base_url`.

## Warmup & Warm Session Pool

Full invariants in `engine/CLAUDE.md`. Key rule: warmup payload matches first-turn byte-for-byte (hour-rounded timestamp, same tools, same `stream_options`). `claim()` only fires for bare `{agent:main, project:'', status:'', note_context:''}` sessions.

## Desktop App (Electron)

Shell loading web UI + CORS-free Node IPC. `--server=http://host:port`. Build: `npm run build:{mac,win,all}`.

## Agent Teams

- **Head**: `team` field in `agent.json`. **Members**: agents in head's `team.members`. **Standalone**: not in any team. **main**: global orchestrator, never has `team`.
- Scoping: `main` → heads + standalone (not members). Heads → members. Members → peers + head.

## Tools

Source of truth: `TOOL_DEFINITIONS` (`engine/tool_schemas.py`, Anthropic flat shape, re-exported on brain). Impls in `engine/tools/*` + `engine/mempalace_glue.py`; wiring (`TOOL_GROUPS`, `TOOL_DISPATCH`) in `brain.py`. Groups: core, documents, code_graph, web, email, delegation, git, scheduler, mcp, skills, nodes, context, memory, code_exec. Per-turn resolution: `resolve_active_tools(purpose=...)` — single decision point (chat, scheduler, warmup, background, settings UI).

**Dispatch path**: sidecar `tool_use` → POSTs Brain `/v1/tools/call` (nonce-protected, localhost-only) → `tool_mcp.handle_tools_call` validates nonce, rebuilds context, dispatches to `engine.TOOL_DISPATCH` (or MCP fallback), captures result via `sidecar_proxy.capture_tool_result(...)` → returns result string. **Synchronous by design** — returns before the proxy drains `tool_dispatch_done`; don't make async without rethinking the result-capture handoff.

**Constraints / gotchas**:
- `execute_command`: no TTY/stdin, `TERM=dumb`. Banned commands in its description.
- Memory is MemPalace **direct, not MCP**: `mempalace_query` (+ `save_chat_to_memory`, `mempalace_get_drawer`, `mempalace_list_drawers`).
- **Adding a tool** = 4 sites / 3 files: schema in `TOOL_DEFINITIONS`, `TOOL_GROUPS` (`brain.py`), `tool_*` fn (`engine/tools/<group>.py`, reaches brain via lazy `import brain as _brain`), `TOOL_DISPATCH` entry (`brain.py`). **Dispatch-identity rule**: `TOOL_DISPATCH` value must be a direct fn ref, not a `lambda args: tool_X(args)` forwarder, or the 4-site checks fail.
- **Pre-existing bug**: 4 tools (`memory_delete`/`memory_recall`/`memory_shared`/`memory_persist`) missing from `TOOL_GROUPS` → surface as `(ungrouped)`.

**Per-tool settings, resolution hierarchy, endpoints, admin UI, Topic A/B split**: full detail in **[INVARIANTS.md → Tools](INVARIANTS.md#tools--per-tool-settings--dispatch)**. Key facts: `config.json → tool_settings` is per-tool admin-editable (enabled/deferred/purposes + 4 prose sections + `applies_with`); resolution is global → agent `tool_overrides` → purpose (purpose layer is global-only). Project-flow tool prose (3-step retrieval, `read_path`, KG rule, BINARY DOCUMENTS) lives in tool descriptions, NOT `_build_system_prompt`.

## Server API

Port 8420. Source of truth: grep `@app.route` / `self.path` dispatch in `server.py`. SSE uses 5s keepalive comments.

## Deployment

- Server: launchd daemon (`com.brain-agent.server.plist`). Telegram: in-process thread. Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev`.
- **Log gotcha**: launchd routes both fd1 and fd2 to **`server.error.log`**. All daemon `print()` lands there, NOT `server.log` (startup banner only). Always tail `server.error.log`.

## Concurrency & Thread Safety

- **`Session.lock`**: all field mutations under it.
- **`SessionManager.get()`**: `_LOADING_SENTINEL` + `Event` prevents duplicate Sessions. `peek()` = cache-only.
- **Request context** = typed `RequestContext` in a `contextvars.ContextVar` (`engine/context.py`; Tier-G replaced the old `_thread_local = threading.local()` bag — that name is GONE). Read/write via `get_request_context().<field>` (~40 fields; arbitrary keys in `._dynamic`). **Enter/teardown ONLY via `with request_context(**overrides):`** (push fresh, exit token-resets; total auto teardown). Nested binds stack + pop. `init_thread_context(ExecutionContext)` is a bulk-setter used *inside* a `with`. Sidecar's `/v1/tools/call` rebuilds context per call (`tool_mcp._apply_context`, inside its own `with`).
  - **contextvars bleed invariant**: fresh thread = empty context, so HTTP (`ThreadingMixIn`) + per-task `Thread().start()` are bleed-free. BUT on a **reused thread** (`ThreadPoolExecutor`) a context-set NOT wrapped in `with request_context()` persists to the next task — the one footgun. **Rule: any code setting request context MUST be inside a `with request_context()`; never set bare on a pooled thread.** Guarded by `tests/test_request_context_isolation.py`.
- **SQLite**: connections via `threading.local()` pools (NOT dict-keyed-by-ident — leaks FDs under `ThreadingMixIn`). Separate, correct use of `threading.local()` (DB pooling, untouched by Tier-G). All ChatDB methods `@_db_safe`.
- **Client proxy SSE**: line buffering carries incomplete lines across TCP chunks.

## Key Invariants

- `augmented_messages` strips metadata (only role+content to API) — prevents 400s.
- Lossless compaction: `compacted` column — originals preserved for search, compacted set for conversation.
- `_rollback_messages()` on cancel/error prunes the pre-call user message (sidecar owns intermediate tool messages).
- Provider routing single-sourced through `resolve_provider_for_model(model)`.
- Sidebar list polls after stream end until async summary arrives (2s, 30s max).
- Sidecar is the only LLM execution path. No fallback loop; if `:8421` down, chat returns `*(Sidecar error: …)*` with terminal `done` so clients unblock.

## Lossless Context Manager

`ContextManager` w/ SQLite DAG in `context.db`. Three-level escalation: leaf summaries → condensation → fallback truncation. Assembly: summaries (highest depth first) + fresh tail (default 16 msgs) within budget. Tools: `context_search`/`context_detail`/`context_recall`.

**Manual-only trigger**: worker no longer auto-fires LCM. Status-bar ✂️ (`#status-lcm-btn`) → `triggerLCM()` (`chat_send.js`) → `POST /v1/context/compact` (`handlers/admin.py`) → `engine._context_manager.check_and_compact(..., force=True)`. Warning banner (`panels_chats.js`) shows at ≥60% usage. `compacting`/`compacted` SSE handlers kept but not currently emitted. Summarisation LLM calls route through `sidecar_proxy.background_call`.

## Code Structure Graph

Tree-sitter AST, SQLite `code-graph.db`, 14 langs. Qualified names `{file_path}::{Class.method}`. Edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY. Incremental: SHA-256 skip; `_after_file_write` → `_maybe_update_code_graph(path)`.

## Projects & Project Mode

`ProjectManager` CRUD; `instructions` in `project.json` injected into prompt; multipart upload to `IngestManager`. **Project ID** = uuid4 hex[:12] (MemPalace wing key — renaming doesn't strand drawers). Archive keeps files (`status: archived`); delete soft to `.trash/`.

`research_mode` (bool, per-project + per-session `research_mode_override`) gates the output-format discipline (Topic B: REFUSAL/PRECISION/CITATION + citation validator/re-round). Topic A (retrieval discipline) renders for every chat w/ the tool. Project input folders are mined into the project's private `project__<id>` MemPalace wing by the `mempalace-project-sync` daemon (single-threaded, mtime-gated incremental — the legacy startup-wipe of all `project__*` was removed 2026-04-28; restarts no longer re-wipe/re-mine). Full detail (Topic A/B split, mode ON/OFF behavior, legacy migration, token optimisations, input-folder sync, KV-cache invariants): **[INVARIANTS.md → Projects & Project Mode](INVARIANTS.md#projects--project-mode)**.

## Empty-Session Cleanup

Sessions created lazily on first send. `list_sessions` SQL hides 0-message sessions >60s. Startup purge deletes >5min empty sessions.

## Project Knowledge Graph

LLM-driven document → triples over project input folders + attachments (post-pass after drawer mining); writes to `<palace_path>/knowledge_graph.sqlite3` (NOT `~/.mempalace/...`). `normative` profile = 12 controlled English predicates; `source_file` chunking gives ~70× yield. Agent KG tools auto-scope to `project`, refused outside project context. GPU/schema constraints + invalidation detail: **[INVARIANTS.md → Project Knowledge Graph](INVARIANTS.md#project-knowledge-graph)**.

## Cost Tracking & Rate Limiting

- `CostTracker` logs every LLM call to `costs.db`. Rates from `_cost_rates` + per-model `cost_input/output`.
- `RateLimiter`: sliding-window per agent (requests/min, tokens/hr, cost/day) from `rate_limits` in `agent.json`.

## Per-User Account Settings & User Profile

`users.preferences` JSON on `auth.db` (`greeting_name`/`job_description`/`communication_preferences`, memory defaults, daily-summary). An auto-maintained per-user profile (`agents/main/user_profiles/<uid>.md` + MemPalace mirror) is built by the `user-profile` daemon. **KV-cache invariant**: `_build_system_prompt` stays user-agnostic — all per-user content (preamble, profile) lives ONLY in the first-user-message preamble, stripped by `_ALLOWED_MSG_KEYS` before wire (injecting it into the system prompt broke warm-pool KV-prefix; reverted). Full detail: **[INVARIANTS.md → Per-User Account Settings](INVARIANTS.md#per-user-account-settings)** + **[User Profile](INVARIANTS.md#user-profile-memory-from-chat-history)**.

## MemPalace (Direct Integration)

Imported as Python package — no MCP, no subprocess. **Vocabulary**: Drawer (atomic verbatim chunk ~800 chars) / Closet (index layer boosting search) / Room (topic bucket) / Wing (namespace). **Wing scheme** (ID-only): `user__<uid>` (private), `team__<tid>` (shared), `project__<pid>` (strictly isolated), bare names (shared). `mempalace_query` force-scopes to `project__<id>` when in a project (refuses if id missing rather than leak). Two daemons: `mempalace-miner` (artifact ingestion) + `mempalace-chat-sync` (mirrors chat turns/summaries/attachments/allowlisted tool_results to wings). Full detail (classifier gate, per-turn memorize/purge, closet rebuild, cursors, what's not mined): **[INVARIANTS.md → MemPalace](INVARIANTS.md#mempalace-direct-integration)**.
