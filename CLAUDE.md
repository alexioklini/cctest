# CLAUDE.md

# CLAUDE.md — 12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 — Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 — Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 — Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.


Guidance for Claude Code in this repo. **Non-obvious invariants only** — what's not derivable from the code. Factual catalogs (full tool list, endpoint list, every config field) live in the code; grep/read it.


## Repository Structure

- `launcher.py` — Gateway CLI (start/stop/restart, launch frontends)
- `server.py` — HTTP API daemon (launchd-managed, port 8420)
- `client.py` — Shared HTTP/SSE client library
- `brain.py` — Core engine: tools, agents, MCP, scheduler, agentic loop
- `engine/` — Extracted engine modules (loop, provider, models, scheduler, tasks, tools, …) — see `engine/CLAUDE.md`
- `handlers/` — HTTP handler modules extracted from server.py — see `handlers/CLAUDE.md`
- `server_lib/` — DB, auth, sessions, notifications, profile helpers
- `tui.py`, `telegram.py` — Terminal + Telegram frontends
- `web/index.html` — Single-page web UI (`web/js/` split into api/chat/files/settings/… modules)
- `desktop/` — Electron shell (CORS-free IPC + lazy llama.cpp host)
- `tools.md` — Global tool-usage guide (loaded into system prompt)
- `config.json` — Providers, server, Telegram (gitignored)
- `agents/<name>/` — `soul.md`, `agent.json`, `skills/`, `mcp.json`; SQLite DBs in `agents/main/`

## Architecture

```
launcher.py → server.py (port 8420)                ┌──────────────────────────┐
                ├── brain.py  # tools, providers, ─┤ sidecar/sidecar.py 8421  │
                │             # MemPalace, KG,     │  Anthropic Python SDK    │
                │             # scheduler, LCM     │  agentic loop owner      │
                ├── handlers/sidecar_proxy.py ────►│                          │
                ├── server_lib/tool_mcp.py ◄──HTTP─┤  POSTs /v1/tools/call    │
                ├── SQLite      # chats, scheduler, context, costs, traces, …
                └── MemPalace   # direct in-process, no MCP
```

All chat + non-interactive LLM calls go through the **sidecar** subprocess
(separate venv, anthropic 0.101.0). Brain owns tools, MemPalace, scheduler,
projects, MCP routing; the sidecar owns the agentic loop. Providers are
plain OpenAI-compatible entries in `config.json` — Brain hands the sidecar
an Anthropic-shape payload + provider env (CLIProxyAPI translates back to
each upstream wire format).

## Agentic Loop (sidecar)

The sidecar (`sidecar/sidecar.py`) owns the loop. Brain never iterates over
LLM rounds itself.

- **Interactive chat** (`handlers/chat.py:worker`): builds Anthropic-shape
  messages from `session.messages`, calls `sidecar_proxy.run_turn()` →
  `POST http://127.0.0.1:8421/turn` (SSE), drains events through
  `event_callback` (built by `build_chat_event_callback`), persists final
  reply + thinking rows.
- **Background calls** (scheduler, refine, soul-chat, summary, profile,
  next-prompt, classify, image-describe, ask_llm, memory-extract,
  promote-skill, KG extract, code-graph summaries, citation re-round,
  translate/*): all route through `sidecar_proxy.background_call(...)`,
  a thin synchronous wrapper around `run_turn_blocking`.
- **Tool dispatch**: sidecar emits `tool_use` blocks → POSTs to Brain at
  `/v1/tools/call` (auth-exempt, nonce-protected, localhost-only). Handler
  in `server_lib/tool_mcp.py` reconstitutes thread-locals from the
  sidecar's `context` payload, dispatches to `engine.TOOL_DISPATCH` (or
  MCP fallback), returns the result. Sidecar then continues the loop.
- **Resumable**: see "Resumable Streaming" below — Brain attaches a
  `LiveStream` to the proxy's SSE drain so `GET /v1/chat/stream` reattach
  works exactly as it did pre-sidecar.
- **Cancel**: Brain mints `turn_id`, passes via `X-Turn-Id`. The proxy's
  `_watch_cancel` thread polls `session.cancel_token` and POSTs
  `/cancel/<turn_id>` to the sidecar.
- **`AskUserQuestion`** still blocks via `_pending_answers[session_id]` +
  `Event`; unblocked by `POST /v1/chat/answer`. The sidecar dispatches the
  tool via `/v1/tools/call`, the handler blocks until the answer lands.

The full record of the migration lives in `SDK_MIGRATION_PLAN.md` +
`SDK_MIGRATION_HANDOVER.md` + `SDK_PHASE5_PROGRESS.md`. Native-loop relics
(`_run_delegate`, `send_message`, `_handle_openai_response`, all
`_middleware_*` between rounds, guided execution, variance kill-switches,
worker-subagent envelopes) were deleted in Phase 5 — don't reintroduce.

## Resumable Streaming (decoupled from HTTP connection)

The chat **worker thread is not tied to any HTTP connection**. `_handle_chat`
opens a `LiveStream` on `session.live_stream` before spawning the worker; the
worker drives `sidecar_proxy.run_turn(...)`, which translates the sidecar's
SSE stream into Brain's `event_callback` vocabulary and emits **every** event
into the `LiveStream`. A `LiveStream` is an ordered replay log + a set of
subscriber queues — `emit()` appends to the log AND fans out to current
subscribers; `attach()` returns `(queue, replay_snapshot, already_done)`
under the same lock (no event lost / no dup across the attach boundary).

- **Originating `POST /v1/chat`** connection is just one subscriber: after `t.start()` it calls `_stream_live_to_client(live, worker_thread=t)` — replays the (usually empty) snapshot, then drains its queue until terminal `done`/`error` or worker death.
- **`GET /v1/chat/stream?session_id=X`** (handler in `handlers/chat.py`) re-attaches: replays the buffer from turn start, then follows live events until terminal. Emits a single `idle` event if no turn is running (chat idle, or the turn finished between the client's `GET /messages` and this call). **Any number of tabs may attach concurrently.** Client disconnect here NEVER cancels the worker — only `POST /v1/chat/cancel` (`session.cancel_token.cancel()`, which the proxy's `_watch_cancel` thread relays to the sidecar) does.
- **Incremental persistence**: `sessions.streaming_text` / `streaming_meta` columns hold the in-flight assistant reply, written by `event_callback` on `text_delta` (throttled ~0.4s), cleared in the worker's `finally`. `GET /v1/sessions/<id>/messages` returns `streaming: true` + `streaming_text` while a turn is live. Read only when `_streaming` is True → always fresh within a turn (a stale value after a restart-mid-stream is never surfaced because the reloaded `Session._streaming` is False).
- **Worker `finally` invariants**: emit `error` if `not live.done` (covers a worker that died without a terminal event), then `session._streaming = False`, then `session.live_stream = None` (order matters — when `live_stream` is None, `_streaming` is already False, so `GET /chat/stream`'s `idle` path can't loop), then clear `streaming_text`.
- **Brain-restart recovery (Phase 5 step 1c)**: each running turn writes a row to `active_turns(session_id, turn_id, model, started_at)`. The sidecar keeps a per-turn event log with monotonic `seq` numbers (`/turn/<id>/events?since=N` SSE endpoint, 5-min retention). On Brain boot, `recover_active_turns_on_boot()` waits for the sidecar `/health`, then spawns one `_recover_one_turn` daemon per row: re-attaches the `LiveStream`, re-streams from the sidecar's event log, persists the recovered assistant message tagged `metadata.recovered=True`. If the sidecar died with Brain (current state, until the sidecar moves to its own launchd plist) the row falls into the catastrophic 404 branch, which promotes the partial `streaming_text` into a persisted message tagged `*(Server restart — turn lost)*`.
- **Client** (`web/js/`): `buildStreamCallbacks(chat, isActive)` builds the SSE callback map, shared by `API.streamChat` (originating send) and `API.attachStream` (reconnect). `openSession()` re-attaches when `GET /messages` reports `streaming: true`; on reconnect it **drops trailing `thinking` DB rows** (the live replay re-emits them via `thinking_done`) and does **not** pre-seed `streamingText` from `streaming_text` (replay rebuilds it fully — pre-seeding would double it). `API.abortStreamAttach()` on leaving a chat (harmless to the worker).
- The session's in-memory `session.messages` list is the conversation that gets handed to the sidecar; intermediate tool exchanges happen entirely inside the sidecar process — only the user msg, `thinking` rows from `event_callback`, and the final assistant msg reach the DB. `_rollback_messages` only fires on cancel/error to prune the user message that was appended pre-call.

## Multi-Provider Routing

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}`. Used by chat, delegate, scheduler, warmup, background. Providers are plain OpenAI-compatible entries in `config.json` → `providers`.

**Provider-scoped IDs**: when multiple providers serve the same model, entries stored as `provider/model_id` with `base_model_id`. Historical scoped ids (`OMLX/*`, `Bifrost/*`, `mistral/*`) still route. Bifrost retired 8.5.0 (dropped nested reasoning blocks).

## Chat File Attachments

Files go to `state._pendingFiles[]` as base64; sent as `body.files` (legacy `body.images` for Telegram).

Per-file routing checks model `raw_formats` (MIME pattern list):
- **Multimodal**: MIME match + base64 + <20MB → OpenAI `image_url` data URI
- **Disk**: otherwise → `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- **Image fallback on non-vision models**: `attachments.image_model` describes via vision LLM; unconfigured → metadata only

## Artifacts

Files written under `agents/<name>/artifacts/<date>_<session_prefix>/` are auto-promoted. `write_file` with relative path defaults into the session's artifact folder.

- Each write/edit → row in `artifact_versions` (5MB cap); SSE `artifact_updated`
- **Role classification**: `_ARTIFACT_INTERMEDIATE_EXTS` (.py/.sh/.js/.json/.csv/.log/etc.) → `intermediate`; rest (.md/.html/.pdf/images) → `output`. Browse grid defaults to outputs-only.

## Scheduled Task Runs

Each run = immutable `schedule_history` row (id=run_id) + synthetic `session_id=sched-<run_id>` scoping artifacts + traces.

- **Per-task attachments**: `schedules.attachments` JSON list. Uploaded once, **referenced in place** every fire (no per-run copy). `_purge_attachment_paths()` refuses paths without `scheduled_attachments` segment.
- **Per-task working_dir**: overrides system prompt cwd line. **`python_exec` stays pinned to artifact folder by design** — file-write tracking depends on it.
- **Per-task `thinking_level` + `caveman_chat`**: empty `thinking_level` inherits at fire time. `caveman_system` deliberately NOT exposed per task (per-model knob, would invalidate warmup KV prefix). `_validate_thinking_level_for_model` rejects format-mismatched levels.

## Next-Prompt Suggestions

`GET /v1/sessions/<id>/next-prompt` after each turn → dimmed placeholder. Reuses session model + history, `tools=False`, tiny `max_tokens`. **Real cost** — earlier "near-free via prompt cache" claim was Anthropic-wire specific and is dead post-v7.2.0.

## Model Management

Per-model fields in `config.json` → `models`. `_match_known_model()` seeds from `KNOWN_MODELS`. Manual add: id + provider + display name (for providers without `/models`).

**Optimization profiles** (`MODEL_PROFILES`): sparse overlays, **only request-style knobs** (never resource knobs like warmup — would silently re-enable user-toggled-off fields). Explicit per-model fields still win.
- `speed` (auto for local): `deferred_tool_groups=[]` (stable KV prefix > lean-but-shifting), `compact_threshold=0.85`
- `balanced` (auto for cloud)
- `frugal`: cloud-only safe
- `custom`: no overlay

Profile changes invalidate warm-pool KV prefix.

**Thinking model auto-recovery**: on `finish_reason=length` + visible output <25% of completion tokens, `max_tokens` doubles on retry (capped at `max_context`).

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Thinking / Reasoning

The Anthropic SDK in the sidecar handles reasoning natively — Brain just
passes `thinking={"type": "enabled", "budget_tokens": N}` (or omits it) on
the wire payload built in `handlers/sidecar_proxy._build_payload`. CLIProxyAPI
translates to whatever format the upstream model expects (Mistral's
`reasoning_effort`, OpenAI's `reasoning`, Anthropic-direct's `thinking`).

For oMLX-direct: warmup must still mirror the chat-template `enable_thinking`
kwarg byte-for-byte on every request whose model has non-`none` reasoning,
or KV prefix misses silently. Live wiring is in `engine/provider.py` warmup
payload + `_apply_inference_to_payload` (used by warmup; production
inference goes through the sidecar).

**Per-model dropdown** in the composer / model settings: shape is
`Off / Low / Medium / High` for cloud reasoning models, `Off / On` for
oMLX inline-thinking models, hidden entirely for non-reasoning models.
Stored on the session as `thinking_level`; per-model default in
`config.json → models.<id>.inference.thinking_level`.

**Persistence**: each round → `role='thinking'` row with
`metadata.tool_round`. `_ALLOWED_MSG_KEYS` / `_INTERNAL_ROLES` strip
`thinking` rows before wire — UI-only.

## Caveman Mode (Dual)

Two settings, independent, compose:
- **System** (`caveman_system` per model, 0–3): compresses system prompt via `_caveman_compress_text()`
- **Chat** (`caveman_mode` in sessions DB, 0–3): appends `CAVEMAN_CHAT_PROMPTS` response-style instruction

Thread-locals set in chat worker, cleaned in `finally`. **Cache key for `_build_system_prompt` includes both.**

## Token Optimization

Per-agent `token_config` in `agent.json`:
- `tool_overrides: {<tool_name>: {enabled?, deferred?}, ...}` — per-tool tristate override of the global `tool_settings` flags. Field present = override; field absent = inherit. Empty/missing dict = no overrides.
- `compact_threshold` — float 0–1, override of LCM's 0.60 default
- `scheduled_task_tools` — bool, gates the tool schema in scheduled task prompts
- `mcp_tool_filter` / `mcp_tool_exclude` — fnmatch patterns, MCP-only filtering

Legacy fields **deprecated** and stripped on next save (resolver ignored them since v9.0.x):
- `tool_groups`, `extra_tools`, `deferred_tool_groups` — replaced by per-tool `tool_overrides` + global `tool_settings.purposes`
- `include_tools_guide` — prose injection is always-on now

Per-agent `limits`: `max_tool_rounds` (soft cap, hard stop at 1.5×), `tool_result_char_limit`, `tool_results_total_tokens`, `context_safety_ratio` (default 0.95).

System prompt cached per-session (60s TTL).

## Per-User Cost Quotas

`QuotaManager` singleton (30s config cache). Two axes per user: **Daily** (rolling, UTC) + **Cycle** (`monthly`/`weekly`/`yearly` w/ anchor). Worst axis wins.

- **Pre-flight gate** in `send_message` round 0, after GDPR. `is_model_local(model)` always bypasses.
- Modes (`quotas.enforce_red`): `warn_only` (default), `force_local` (silent swap to `default_local_fallback_model`), `hard_block` (`QuotaExceededError`).
- `_log_call_cost` captures `_thread_local.current_user_id`. Empty `user_id` rows are pre-quota legacy.
- Limit `0` = "no limit" on that axis.

## GDPR / PII Pre-Submit Scanner

71 regex detectors, client + server side. Zero external APIs.

**Two mirrored implementations** (must stay in sync): `PIIScanner` in `web/index.html`; `_pii_rules()` + `_pii_scan_text()` + `_pii_scan_bare_identifiers()` in `brain.py`.

**Three rule tiers** (first-match-wins, overlap suppression): cloud secrets/API keys → national IDs with checksums (~30 countries) → context-fallback + bare-identifier heuristic.

**Rule-order invariants**:
- Context-gated rules (DE Steuer-ID, NL BSN, HU TAJ) before generic bare-digit rules
- `credit_card` AFTER all national-ID checksum rules (RO CNP, KR RRN are 13-digit Luhn-passing)
- `phone` AFTER national IDs (`XXX-XXX-XXXX`-shaped SIN/NHS would steal phone slot)
- `credit_card` regex has `(?<![+\d])` so `+CC...` phone prefixes don't match

**Overlap suppression**: successful matches claim spans; failed validations DON'T (lets weaker rules re-scan inside an invalid IBAN — why Aadhaar/PESEL/Steuer-ID are context-gated).

**Routing**: hard-block raises pre-LLM **only when model is non-local** — local bypasses block (data stays on-prem). `gdpr_pick_model_for_background(model, texts, purpose)` is the **single decision point** for non-interactive calls: scan → audit `pii_detected` → swap to `default_local_fallback_model` if configured (audit `pii_auto_fallback`) → else if `server_block` raise `GDPRBlockedError` (audit `pii_blocked`) → else warn-only.

**Client local interlock**: `piiBlockActive(chat)` filters model dropdown to local-only when `server_block=true` + scanner enabled + (draft or loaded history has PII). Auto-swaps via `piiEnsureLocalModel()`.

**`is_local`**: `is_model_local()` → `_is_local_base_url()` matches localhost/127/0.0/RFC1918.

**Config** (`gdpr_scanner`): master toggle, `server_log`, `server_block`, `default_local_fallback_model`, 8 per-category actions (`ignore`/`warn`/`block`), `rule_overrides`, `email_allowlist`. `block` downgraded to `warn` when `server_block` master is off. `PII_RULE_CATEGORIES` + `PII_DEFAULT_CATEGORY_ACTIONS` mirrored as `PIIScanner.ruleCategories` in web UI.

**Not detected**: personal names, addresses, ICD codes, generic passport/license without context.

## Python Code Execution

Opt-in via `code_exec` in `tool_groups`. Subprocess isolation (`sys.executable`), timeout-killed. **Working dir = artifact session folder** — files written auto-register as artifacts; state persists across calls.

- **Auto-artifact fallback**: stdout >1K chars + no files written → saved as `output.txt`; preview shows head+tail

## Data View

Sidebar entry only — the `#data-view` container is currently an empty placeholder (nav case in `web/js/nav.js`). The Data Workbench feature (DuckDB-per-session, `data_viz` tools, anonymisation, file scan, chart builder) was removed; the menu entry stays so the view can be re-populated later.

## Provider Concurrency Queue

`LocalProviderQueue` in `engine/provider.py`. Key numbers: `omlx=2` (continuous batching sweet spot), `cliproxyapi=2` (serialized, no batching), cloud=0 (unlimited). Queue key is `provider_name`, not `base_url`.

## Warmup & Warm Session Pool

Full invariants in `engine/CLAUDE.md`. Key rule: warmup payload must match first-turn payload byte-for-byte — hour-rounded timestamp, same tools, same `stream_options`. `claim()` only fires for bare `{agent:main, project:'', status:'', note_context:''}` sessions.

## Desktop App (Electron)

Shell loading web UI + CORS-free Node IPC. `--server=http://host:port` CLI arg. Build: `npm run build:{mac,win,all}`.

## Agent Teams

- **Team head**: `team` field in `agent.json`
- **Members**: agents listed in head's `team.members`
- **Standalone**: not in any team
- **main**: global orchestrator, never has `team`

Scoping: `main` → heads + standalone (not members directly). Heads → their members. Members → peers + head.

## Tools

Source of truth: `TOOL_DEFINITIONS` in `brain.py` (Anthropic flat shape).
Groups: core, documents, code_graph, web, email, delegation, git, scheduler,
mcp, skills, nodes, context, memory, code_exec. Resolution per turn lives
in `resolve_active_tools(purpose=...)` — single decision point for chat,
scheduler, warmup, sidecar background calls, and the settings UI.

**Dispatch path** (sidecar architecture):
1. Sidecar emits an Anthropic `tool_use` block.
2. Sidecar POSTs to Brain `POST /v1/tools/call` (auth-exempt,
   nonce-protected via `sidecar.tool_endpoint_internal`, localhost-only).
3. `server_lib/tool_mcp.handle_tools_call` validates the nonce, rebuilds
   thread-locals from the sidecar's `context` payload (current_agent,
   mcp_manager, current_session_id, current_user_id, project), dispatches
   to `engine.TOOL_DISPATCH` (or MCP fallback for unknown names), captures
   the result via `sidecar_proxy.capture_tool_result(turn_id, tool_use_id, ...)`
   so the proxy's downstream `tool_dispatch_done` SSE event has the body.
4. Returns the result string to the sidecar. Sidecar continues the loop.

Tool dispatch is **synchronous** by design — `handle_tools_call` returns to
the sidecar before the proxy's translator drains `tool_dispatch_done`. Don't
make dispatch async without rethinking the result-capture handoff.

### Per-tool settings (admin-editable, global)

`config.json → tool_settings` is a per-tool admin-editable record keyed by
tool name. Loaded into `engine._tool_settings` at startup; mirrored onto
`server_config["tool_settings"]`. Schema per record:

```
{
  enabled:      bool   # default True. Global kill switch — false hides the
                       # tool from EVERY agent unless the agent overrides
                       # via tool_overrides (see hierarchy below). Server-
                       # internal callers (Brain dispatching its own
                       # tool_*() calls) are unaffected.
  deferred:     bool   # default False. Hide from initial tool list; expose
                       # via tool_search only.
  purposes:     list[str]  # default []. Allowed call purposes for this
                       # tool: any of `interactive`, `transform`,
                       # `memory_summary`, `research_minimal`. Empty = all
                       # purposes. Seeded at first startup from current
                       # behavior (interactive for every tool;
                       # research_minimal for tools flagged minimal=True;
                       # memory_summary for _MEMORY_SUMMARY_TOOLS).
  description:  str    # prose injected into the system prompt when the
  when_to_use:  str    # tool is in the active set. All four sections are
  warnings:     str    # rendered under `## <tool_name> / ### <Section>`
  examples:     str    # by `_render_tool_descriptions`. Empty = omitted.
  applies_with: list   # all-of gate — tool's prose renders only when
                       # every name in this list is also active.
}
```

Defaults: `enabled=true`, `deferred=false`, `purposes=[]`, all prose empty.
Adding a prose record never accidentally hides or defers the tool. The
renderer skips records where `enabled=false` defensively, even if a stale
active set somehow contains the tool.

**Resolution hierarchy** (per LLM call, every tool):
```
  effective_enabled  = global.enabled
  effective_deferred = global.deferred
  if agent_id:
      override = token_config.tool_overrides.<name>
      if 'enabled'  in override: effective_enabled  = override.enabled
      if 'deferred' in override: effective_deferred = override.deferred
  if not effective_enabled: drop
  if call.purpose not in global.purposes (when set): drop
  if effective_deferred and tool not in discovered_tools: drop (surface via tool_search)
```

The purpose layer is **global-only** — agents cannot override it (the
purpose of a call is a property of the call, not the agent).

**Scheduled tasks** bypass the agent-override layer entirely. They run
through the resolver with `agent_id=None` so only `global` + `purposes`
apply. Wire path: `_execute_scheduled` sets `tool_resolver_agent_id=None`
in the sidecar's `tool_context`; the proxy's `_build_tool_list` reads
that key when present. The agent's `agent_id` still travels in
`tool_context` for tool dispatch (audit, MemPalace wing scoping).

**Endpoints**:
- `GET /v1/tools/settings` — admin-only. Returns all 63 tools (sorted)
  merged with their settings + reverse-indexed `group` string + canonical
  `purposes` list at the top level. Tools without a record get safe
  defaults so the UI can render a single affordance per tool.
- `POST /v1/tools/settings` — admin-only. Saves one tool's record
  atomically. Validates: `name` must exist in `TOOL_DISPATCH`, every
  entry in `applies_with` must be a known tool, no self-reference,
  `enabled`/`deferred` must be bool, every entry in `purposes` must be
  in `_VALID_PURPOSES`. Persists to `config.json`. Audited via
  `engine._audit_log` (`action_type=tool_settings_save`).
- `GET /v1/tools/breakdown?agent=<id>` — admin-only. Per-tool token cost
  decomposition (name / description / schema). Surfaced in General
  Settings → Tools tab as the "Tool definition cost" header + per-row
  `Nt` token badge.

The legacy `tools.md` file is gone — its anchored blocks were one-shot
migrated into `tool_settings` records on first server startup post-migration
(see `migrate_tool_settings_from_md` in `brain.py`). Anchor → leading-tool
mapping; multi-anchor blocks (`exa_search,web_fetch`) attach to the leader
with the rest going into `applies_with`.

**Admin UI** (`web/js/settings.js` — General Settings → Tools tab):
grouped collapsible registry showing all 63 tools. Per-tool expanded
panel exposes: enabled/deferred toggles, group label (read-only),
optional integration knobs (for the ~13 tools with `tool_config` entries
— API keys, timeouts, model selectors), 4 prose textareas, applies_with
multi-select. Two save scopes: `Save` → POST `/v1/tools/settings`,
`Save integration` → POST `/v1/tools/config` (legacy endpoint, single-key
body for atomic per-tool replacement).

**Tool prose vs project disciplines** (Topic A / B split, v9.0.x):
- **Topic A — retrieval discipline** (search-first, query keyword shape,
  saving guidance) lives in `tool_settings.mempalace_query.description`.
  Renders for EVERY chat that has the tool, regardless of project mode.
  Admin-editable.
- **Topic B — output-format discipline** (refuse-on-empty, no-filler
  precision, per-claim citation) lives in the `DEFAULT_PROJECT_INSTRUCTIONS`
  constant in `brain.py`. Renders only when project + research_mode is on.
  Hardcoded (Brain behavior, not user-editable). See "Project Mode" below.

**Constraints**:
- `execute_command`: no TTY, no stdin, `TERM=dumb`. Banned commands in
  `tool_settings.execute_command.description`.
- Memory is MemPalace **direct, not MCP**. Tool: `mempalace_query`
  (+ `save_chat_to_memory`, `mempalace_get_drawer`, `mempalace_list_drawers`).
- **Adding a new tool** = 4 edit sites in `brain.py`: `TOOL_DEFINITIONS`,
  `TOOL_GROUPS`, the `tool_*` function, `TOOL_DISPATCH`. Per-tool prose
  is added later via the admin UI, not in code.
- **Pre-existing**: 4 tools (`memory_delete`, `memory_recall`, `memory_shared`,
  `memory_persist`) are missing from `TOOL_GROUPS` — surface as
  `(ungrouped)` in the admin UI. Brain.py bug, separate fix.

## Server API

Port 8420. Source of truth: grep `@app.route` / `self.path` dispatch in `server.py`. SSE streams use 5s keepalive comments.

## Deployment

- Server: launchd daemon (`com.brain-agent.server.plist`)
- Telegram: in-process thread
- Public: Cloudflare Zero Trust tunnel → `brain.alexklinsky.dev`
- **Log files (debugging gotcha)**: launchd routes both fd1 and fd2 to **`server.error.log`**. All daemon `print()` lands there, NOT `server.log` (which only gets startup banner). Always tail `server.error.log`.

## Concurrency & Thread Safety

- **`Session.lock`**: all field mutations under it
- **`SessionManager.get()`**: `_LOADING_SENTINEL` + `Event` prevents duplicate Sessions for same id. `peek()` for cache-only reads.
- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id`. Never fall back to globals — concurrent requests bleed.
- **SQLite**: connections via `threading.local()` pools — **not** dict-keyed-by-ident (leaks FDs under `ThreadingMixIn`). All ChatDB methods wrapped with `@_db_safe`.
- **Client proxy SSE**: line buffering carries incomplete lines across TCP chunks.

## Key Invariants

- `augmented_messages` strips metadata fields (only `role`+`content` to API) — prevents 400s
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- `_rollback_messages()` on cancel/error prunes the user message that was appended pre-call (the sidecar owns intermediate tool messages — they never reach Brain's `session.messages` mid-turn)
- Provider routing is single-sourced through `resolve_provider_for_model(model)` — every chat / scheduler / warmup / background path
- Sidebar list polls after stream end until async LLM summary arrives (2s, 30s max)
- The sidecar is the only LLM execution path. Brain has no fallback loop; if `127.0.0.1:8421` is down, chat returns `*(Sidecar error: …)*` as the assistant reply (with terminal `done` so HTTP clients unblock)

## Lossless Context Manager

`ContextManager` with SQLite DAG in `context.db`. Three-level escalation:
leaf summaries → condensation → fallback truncation. Assembly: summaries
(highest depth first) + fresh tail (default 16 messages) within token budget.
Tools: `context_search`, `context_detail`, `context_recall`.

**Manual-only trigger** (Phase 5 step 8). The chat worker no longer auto-fires
LCM at turn start. The status-bar ✂️ button (`#status-lcm-btn` in
`web/index.html`) calls `triggerLCM()` (`web/js/chat.js`) which POSTs to
`/v1/context/compact` (`handlers/admin.py:_handle_context_compact`); that
calls `engine._context_manager.check_and_compact(..., force=True)` directly.
The LCM warning banner (`web/js/panels.js:170`) shows at ≥60% context usage
to prompt the user. The `compacting` / `compacted` SSE event handlers are
kept in `chat.js` for future re-introduction but aren't currently emitted.

Summarisation LLM calls (`ContextManager.summarize_chunk`, `condense`,
`recall`) route through `sidecar_proxy.background_call` like every other
non-interactive LLM call.

## Code Structure Graph

Tree-sitter AST parsing, SQLite in `code-graph.db`. 14 langs. Qualified names `{file_path}::{ClassName.method}`. Edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY. Incremental: SHA-256 hash skip; `_after_file_write` triggers `_maybe_update_code_graph(path)`.

## Projects

`ProjectManager` CRUD; `instructions` field in `project.json` injected into system prompt; multipart file upload to `IngestManager`.

- **Project ID**: `id` field in `project.json` is uuid4 hex[:12], assigned on first read. **MemPalace wing key** — renaming doesn't strand drawers, same-named projects under different agents don't collide. `create_project` mints upfront; backfilled lazily for legacy.
- Archive: `status: "archived"` (files preserved). Delete: soft to `.trash/`.
- **Notes**: AI editing uses `write_file`/`edit_file` (not tag-based). Note-AI sessions use `status: note_chat`, hidden from project chat list.

### Project Mode: `research_mode` (v8.31.0, split v9.0.x)

`project.json.research_mode` (bool) gates the **output-format discipline**
(Topic B — refuse-on-empty, precision, per-claim citation) independently
of the `instructions` field. Per-session `sessions.research_mode_override`
(sticky NULL/0/1) layers on top — set from the composer button or session
settings; null = use project default.

Effective mode = `session.research_mode_override if not None else project.research_mode`. Resolution lives in `_build_system_prompt` (sets `_thread_local.research_mode_override` upstream so `handlers/chat.py` reads the same value when gating the citation validator + re-round).

**The split** (Topic A / B, v9.0.x):

| Discipline | Source | Gating |
|---|---|---|
| Search-first ("memory IS the answer") | `tool_settings.mempalace_query.description` | tool present in active set |
| Query discipline (short keywords, drop fillers) | `tool_settings.mempalace_query.description` | tool present |
| Saving guidance (`save_chat_to_memory`) | `tool_settings.mempalace_query.description` | tool present |
| **Refuse-on-empty** | `DEFAULT_PROJECT_INSTRUCTIONS` (brain.py) | project + research_mode |
| **Precision discipline** (no plausible-sounding filler) | `DEFAULT_PROJECT_INSTRUCTIONS` | project + research_mode |
| **Per-claim citation** (verbatim quotes per bullet) | `DEFAULT_PROJECT_INSTRUCTIONS` | project + research_mode |

Topic A (retrieval discipline) is admin-editable per-tool — see the
"Per-tool settings" subsection of "Tools" above. Topic B is hardcoded
Brain behavior toggled by `research_mode`.

**Research mode ON** (Q&A / policy-reproduction / compliance projects):
- Strict `PROJECT MEMORY` block — mandatory 3-step flow (`mempalace_query` → `read_document(path=read_path)` → answer); REFUSE-on-error.
- KG decision rule (`mempalace_kg_search` first if research_mode AND `kg.enabled`).
- `DEFAULT_PROJECT_INSTRUCTIONS` discipline block (REFUSAL / PRECISION / CITATION) injected DIRECTLY by Brain.
- Server-side citation validator + synchronous re-round on threshold violation (>30% uncited or ≥2 unverified quotes), gated by `mempalace.citation_reround.enabled`.

**Research mode OFF** (codegen / drafting / build-with-context projects):
- Soft `PROJECT MEMORY` block — "memory is available, use `mempalace_query` when relevant", no forced flow.
- KG hint block skipped (model can still discover the tools from definitions).
- `DEFAULT_PROJECT_INSTRUCTIONS` NOT injected — model can correctly fall back on training-data framing for build/draft workflows.
- Citation validator + re-round skipped entirely.
- **Topic A still active** — the model still gets search-first + query-discipline guidance via the `mempalace_query` tool description.

**Owner `instructions` field is purely additive** in both modes — appended verbatim after the mode-specific blocks. Never used as a fallback for the disciplines (that was the v8.23 behavior; replaced because it conflated owner intent with Brain behavior). Editor lost the "Load default" button + helper text.

**Migration for legacy projects**: `_project_research_mode(cfg)` infers default when `research_mode` field is absent — empty `instructions` → True (preserves v8.23 behavior); non-empty `instructions` → False (owner already overrode the disciplines). Owners flip the per-project default in the project settings panel.

**Composer button** (`btn-research-mode`, hidden in non-project chats): two-state cycle — project default ↔ override-opposite-of-default. Clicking installs `not project_default` as `session.research_mode_override`; clicking again clears (back to default). Sticky across turns of the same session, mirrors `save_to_memory` semantics.

Brain mechanics (3-step flow text body when ON, BINARY DOCUMENTS block, `read_path` vs `read_path_original`) STAY in system prompt regardless of mode — infrastructure facts.

`DEFAULT_PROJECT_INSTRUCTIONS` constant in `brain.py` is the Topic-B discipline text; injected only when research_mode is on. Endpoint `/v1/projects/default-instructions` removed.

**Anti-room-name-guessing**: `mempalace_query` `room` parameter description enumerates real rooms (`general`/`artifacts`/`chat`/`chat_summary`/`chat_attachment`/`reference`) and forbids invention. Earlier permissive list got used as valid vocab by models.

**Sampling defaults for Mistral Small** (gitignored): `temperature: 0.2`, `top_p: 0.85`. `0` rejected by provider; `0.1` showed no improvement.

### Token Optimisations (v8.23)

- **Per-session read_document/read_file cache** (`_read_doc_cache`): keyed by `(session_id, abs_path)`. Repeat full-shape read in same chat → compact stub. Invalidation: `os.stat()` on every lookup; `_after_file_write()` explicit. Pagination args bypass entirely. TTL 1h, max 64 entries/session, LRU-by-turn eviction. `read_document`/`read_file` added to `_DEDUP_EXEMPT` — cache is the smarter dedup.
- **Per-session project preamble**: dynamic project state moved out of `_build_system_prompt` into per-session preamble injected at round 0 first-user-message via `_project_preamble_text(agent_id, project_name)`. KV-prefix stays project-agnostic; ~1KB saved per request on no-cache providers.

### Project Input Folders + Sync

Each project specifies on-disk folders mined into its private MemPalace wing. With manual attachments (`ingested/`), this is project memory.

- **Schema** (`project.json`): `input_folders: [{path, recursive, auto_sync, added_at}]`, `sync_status: {state, last_run_*, total_indexed, total_files, total_triples, items: {<key>: {kind, id, state, drawers_filed, error}}}`. Live snapshot `_project_sync_live[(agent, project)]` carries cycle progress.
- **Path-traversal guard**: realpath-resolved; refuses paths inside `agents/`, `/etc`, `/var`, `/usr`, `/bin`, `/sbin`, `/System`, `/Library/Keychains`.
- **Daemon** (`mempalace-project-sync`): polls every 6h default. Per project: ensure `mempalace.yaml` matches expected wing (auto-rewrite), mine `ingested/` then each input folder. Per-folder cap default 5000. **`total_indexed` cumulative** (survives dedup-only re-runs). **Single-threaded** — multi-project cycles strictly sequential; long projects block all others. `auto_sync=false` skipped on scheduled cycles, bypassed for manual "Sync now".
- **Startup wipe**: drops every drawer in any `project__*` wing AND clears every project's `sync_status`. Currently runs on every restart — needs marker-file gate (see backlog).
- **System prompt** when `_thread_local.project` is set: prepends PROJECT MEMORY block + PROJECT INPUT FOLDERS block (absolute paths + path-join example to resolve `source_file` against folder root before `read_file`/`read_document`).

## Empty-Session Cleanup

Sessions created lazily on first send (not on model switch / `newChat()` / PII swap). `list_sessions` SQL hides 0-message sessions older than 60s. Server startup one-shot purge deletes >5min empty sessions.

## Project Knowledge Graph

LLM-driven document → triples extraction over project input folders + attachments. Post-pass after drawer mining; writes to MemPalace's KG (`<palace_path>/knowledge_graph.sqlite3`).

- `kg_extract.py` — Profile registry (`normative` for policies/regulations/SOPs; `generic` for prose). Two chunking modes: **`source_file`** (default) re-chunks original markdown at `source_chunk_chars` (3500) — ~70× yield improvement. `inference_max_tokens=8000` (reasoning models exhaust mid-JSON below this).
- `doc_convert.py` — pre-mine binary→companion `.md` under `<folder>/.brain-extracted/<name>.<ext>.md`. Idempotent via `(mtime, size)` frontmatter. `<!-- brain-source: <abs path> -->` lets agent resolve back to original binary.
- **`normative` vocabulary**: 12 controlled English predicates (`requires`, `forbids`, `permits`, `defines`, `cites`, `applies_to`, `effective_from`, `supersedes`, `responsible_party`, `condition`, `exception`, `penalty`). Subjects/objects verbatim in source language. Off-vocab allowed as escape hatch.

**Daemon hook**: `_run_kg_for(...)` resolves prefix via `os.path.realpath()` (macOS `/tmp` → `/private/tmp`, without this drawer source_files don't match).

**Optional closet regen**: replaces regex closets with LLM-generated topic lines. **Incremental** via `closet_regen_progress` cursor on per-source `(mtime, size)` — 400 unchanged PDFs costs ms not 400 LLM calls.

**Source-change invalidation**: snapshots `kg_extraction_source_state`. On diff → DELETEs `triples` rows matching **exact** `source_file` (not LIKE prefix — siblings stay safe) + progress rows + orphan-entity sweep.

**Agent KG tools**: auto-scope to caller's project via `_thread_local.project`. Refused outside project context.

### Known constraints

1. **GPU memory tradeoff**: 26B chat warmpool (~22GB) + e4b extraction (~5GB) doesn't fit oMLX's 25.6GB cap. Either raise cap, set 26B `warmup: false` + `oMLX max_concurrent: 1`, or use cloud (`gemini-2.5-flash`).
2. **MemPalace KG schema 3.3.0 vs 3.3.3**: 3.3.0 lacks `source_drawer_id`+`adapter_name`. `kg_extract` falls back via `TypeError` to legacy 5-arg `add_triple`.
3. **MemPalace KG path**: `<palace_path>/knowledge_graph.sqlite3`, NOT `~/.mempalace/knowledge_graph.sqlite3` (`KnowledgeGraph()` no-arg default).
4. **Code skipped**: `_is_code_path()` matches code extensions — folded into Brain's code graph instead.

## Cost Tracking & Rate Limiting

- `CostTracker` logs every LLM call to `costs.db`. Rates from `_cost_rates` defaults + per-model `cost_input/output`.
- `RateLimiter`: sliding-window per agent (requests/min, tokens/hr, cost/day) from `rate_limits` in `agent.json`.

## Per-User Account Settings

Personal state separated from global admin settings.

**Schema** — `users.preferences` JSON on `auth.db` (validated via `PREFERENCE_DEFAULTS` + `_coerce_pref`):
- `greeting_name` (≤64), `job_description` (≤500), `communication_preferences` (≤4000) — surfaced in first-turn preamble
- `memory_chats_default` (0|1|2|null) — overrides server-wide classifier `default_mode`
- `memory_sched_default` (0|1|null) — gates miner from filing user's sched-run artifacts
- `daily_summary_enabled` (bool), `daily_summary_hour_local` (0-23)

`update_preferences` is merge-update with atomic validation. Default-valued keys pruned. `update_user` (admin) doesn't touch this column.

**First-turn preamble**: prepends `[Context about this user: …]` block. **Kept OUT of system prompt** — earlier iteration injecting greeting into `_build_system_prompt` broke warm-pool KV-prefix matching for every authenticated turn (reverted). Stripped before wire by `_ALLOWED_MSG_KEYS`.

**Refinement**: `/v1/refine` extended with `purpose: "profile_field"`. Polish-don't-rewrite (preserves first-person voice). `mistral-vibe-cli-fast` validated; cliproxyapi/gemini-2.5-flash silently echoes input (known model bug).

**My Schedules tab**: non-admins see only own schedules; legacy schedules (empty `user_id`) stay admin-only. `_schedule_owner_check(name)` gates mutating ops.

## User Profile (Memory from chat history)

Auto-maintained per-user context profile, mirrored to MemPalace.

- **Storage**: `agents/main/user_profiles/<uid>.md` (filesystem source of truth, gitignored) + `<uid>.history/<ISO>.md` (capped 30, KEPT on Reset). MemPalace mirror: `wing=user__<uid>, room=user_profile`, one drawer per `## section`, purge-then-add.
- **Schema**: fixed sections — Work context / Personal context / Top of mind / Recent months / Earlier context / Long-term background. `_PROFILE_SYSTEM_PROMPT` rules: never invent, third-person, match user's language, edit in place, demote stale Top→Recent, no timestamps, 2-6 sentences/section.
- **Daemon** (`user-profile`): polls every 30 min. Per-user gate: `daily_summary_enabled` + local hour matches + 23h cooldown via `auth.db.user_daily_summary`.
- **Worker** (`_profile_run_synchronous`): 100 most-recently-active chats from last 90 days, `sidecar_proxy.background_call` with `_PROFILE_SYSTEM_PROMPT`, model picked by `_profile_pick_model` (refinement → cheapest haiku → cheapest enabled → default). GDPR auto-fallback to local on findings. Atomic write via tmp + `os.replace`.
- **Preamble injection**: round 0 reads `<uid>.md` (4KB cap), prepends `[Auto-maintained user profile: …]` on first user message. Stripped by `_ALLOWED_MSG_KEYS`.
- **KV-cache invariant**: `_build_system_prompt` stays user-agnostic. Per-user content lives ONLY in first-user-message preamble.

## MemPalace (Direct Integration)

Memory powered by MemPalace, imported as Python package — no MCP, no subprocess.

**Vocabulary**: **Drawer** = atomic verbatim chunk (~800 chars, content-hash id). **Closet** = index layer (topic|entities|→drawer_ids) boosting search ranking. **Room** = topic bucket (`chat`, `chat_summary`, `chat_attachment`, `reference`, `general`, `artifacts`, …). **Wing** = namespace. **Hall**/**Tunnel** = graph edges (future).

**Wing scheme** — ID-only:
- `user__<user_id>` — per-user private
- `team__<team_id>` — shared across team members
- `project__<project_id>` — strictly isolated project memory
- Bare names (`brain_code`, …) — shared, anyone reads

`_resolve_session_wing` priority: project → team → user → empty. Anonymous sessions return `""` and skipped by chat-sync.

`mempalace_query`: when `_thread_local.project` set, **force-scopes** to `project__<id>` (refuses if id missing rather than leak). Otherwise defaults to `user__<current_user_id>`. Visibility filter for unspecified-wing searches: drops `project__*` (always private), matches `user__/team__` against caller, treats untyped as shared.

**Chat sync classifier gate**: LLM classifies message pairs before filing. `fact`/`preference`/`decision`/`reference` filed vs `generic`/`refusal`/`chitchat` skipped. Non-streaming, `max_tokens: 20`, fail-open. Per-session 3-state mode: `0=off`, `1=on`, `2=auto`. `save_chat_to_memory` tool lets model explicitly enable on "remember this". Per-turn control via palace-icon menu (memorise/remove × complete/this/above/below) → `memorize_turns`/`purge_turns` accepting `turn_ids` or `{scope, anchor_turn_id}`. Disable-with-purge prompt when toggling on/auto → off and drawers exist.

**Session delete cleanup**: `delete_session` purges drawers + closets where `source_file LIKE session/<sid>%`. **Archive leaves drawers intact** (memory persists).

**Daemon 1 — `mempalace-miner`** (default 1800s): autonomous artifact ingestion. Walks `AGENTS_DIR`, classifies by folder name: `sched-` prefix → scheduled; else chat.
- **Sched folders**: file only output-role files via direct `tool_add_drawer`. Skips intermediates. `source_file=session/sched-<run>#artifact/<name>`. Wing = `<agent_id>_artifacts`. Sched chat content (reasoning, tool calls) deliberately stays out.
- **Chat folders**: gated on parent session `save_to_memory > 0`. Daemon ensures `mempalace.yaml` exists (marker `# managed by brain-agent server.py`).
- **Stale-queue cleanup**: one-shot `_purge_orphan_chroma_queue()` at startup detects HNSW segments missing `max_seq_id` and deletes >24h-old `embeddings_queue` rows.
- **Logging**: plist sets `PYTHONUNBUFFERED=1` so `[mempalace-miner]` reaches log immediately.

**Daemon 2 — `mempalace-chat-sync`** (default 60s): mirrors to wings:
- Chat turns → `room=chat`, `source_file=session/<sid>#turn/<user_msg_id>` (turn anchor = DB id of opening user message)
- Session summaries → `room=chat_summary`, content-hashed
- Attachment metadata (filename/mime/size, NOT bytes) → `room=chat_attachment`
- Allowlisted tool_results (default `exa_search`, `web_fetch`, `read_document`) → `room=reference`

Uses `mempalace.mcp_server.tool_add_drawer` (function, not server). Reads `MEMPALACE_PALACE_PATH` env from `mempalace.palace_path` before import.

**Closet rebuild** per dirty group: `purge_file_closets` + `build_closet_lines` + `upsert_closet_lines`. Without this, chat memories miss closet boost. Gated by `mempalace.chat_sync.build_closets`.

**Cursor**: `chat_mempalace_sync (session_id PK, last_message_id, last_summary_hash, updated_at)` in chats.db.

**Not mined**: attachment bytes, artifact version history (latest is on disk), tool_result outside allowlist.
