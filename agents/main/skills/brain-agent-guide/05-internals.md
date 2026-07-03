# Brain-Agent Internals

Reference for "why does it behave this way" questions and for diagnosing
weird states. Most users don't need this — load it only when a task
requires understanding the moving parts.

## High-level architecture

```
launcher.py → server.py (HTTP API, port 8420)
                ├── brain.py            # tool wiring, providers, MemPalace, scheduler
                ├── engine/             # extracted engine modules
                │   └── llm_loop.py     # IN-PROCESS OpenAI agentic loop
                │       httpx SSE → {base_url}/chat/completions;
                │       tool_use → engine.TOOL_DISPATCH[name](args) (direct call)
                ├── handlers/           # per-endpoint HTTP handlers
                ├── server_lib/         # DB, auth, sessions, notifications
                ├── searxng/ (port 8088, separate venv) — self-hosted search
                ├── crawl4ai/ (port 8422, separate venv) — headless render + Scrapling stealth
                ├── SQLite              # chats, schedules, costs, traces, context, …
                └── MemPalace (in-process, NOT MCP)
```

All chat + non-interactive LLM calls run **in-process** via
`engine/llm_loop.py` (the Anthropic-SDK sidecar subprocess was deleted in
v9.247.0). Tool calls are dispatched directly on the loop's thread via
`engine.TOOL_DISPATCH[name](args)` — no HTTP hop, no nonce.

## Agentic loop (in-process)

- **Interactive chat**: `handlers/chat.py:worker` →
  `sidecar_proxy.run_turn()` (legacy module name — there is no sidecar) →
  `engine.llm_loop.run_loop(...)` on the worker thread, which streams
  `httpx` SSE from `{base_url}/chat/completions` and emits the Brain event
  vocabulary (`text_delta`, `thinking_*`, `tool_call`, `tool_result`,
  `usage`, `done`).
- **Background calls**: scheduler, refine, soul-chat, summary, profile,
  next-prompt, classifier, image-describe, ask_llm, memory-extract,
  promote-skill, KG extract, code-graph summaries, citation re-round,
  translate/* — all use `sidecar_proxy.background_call()` →
  `run_turn_blocking` → `run_loop` (drains to a final dict).
- **Cancel**: interactive turns poll `session.cancel_token` between rounds
  AND a watcher thread closes the stream socket mid-generation. Background
  turns register a `turn_id → Event` in `sidecar_proxy`;
  `sidecar_proxy.cancel_turn(turn_id)` trips it.
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
- **Brain-restart recovery**: the in-process loop dies WITH Brain (no
  external process holds the turn), so on boot
  `recover_active_turns_on_boot()` promotes any persisted partial
  `streaming_text` to a message tagged `*(Server restart — turn lost)*`
  and clears the `active_turns` row.

## Multi-round answer text — accumulate + chronological interleave

- The loop ACCUMULATES visible answer text across rounds
  (`text_segments` → `final_text = "\n\n".join(...)`). Interleaved-reasoning
  models (e.g. mistral-medium) emit answer text in several rounds
  (text → tool → text); overwriting per round would drop the early text
  (the old 7d44ab98 bug). `_visible_text` still strips whitespace/`<eos>`
  junk so a junk final round can't clobber a real answer.
- The split is surfaced as `summary.text_segments` `[{round,text}]` →
  persisted on the assistant turn as `metadata.text_rounds` (display-only,
  wire-stripped; the message `content` stays the full joined reply for
  history). The chat view interleaves answer-text segments with tool cards
  in `_seq` order so a turn renders text → tool → text exactly as it ran
  (not all tools above one answer block). LIVE: `_commitStreamingSegment`
  freezes the current streamed text as an `assistant_segment` row on each
  tool call. RELOAD: `sessions.js` rebuilds the same rows from
  `metadata.text_rounds` (one shared `_seq` counter for segments+tools).
  `assistant_segment` is client-only — never persisted, never on the wire.
- NOTE: mistral-medium via CLIProxyAPI emits NO separate thinking blocks
  (no `thinking_done` → no `thinking` rows persisted); that's a
  provider-format gap, not a data-loss bug.

## Provider routing

`resolve_provider_for_model(model)` is the **single source of truth** for
`{api_key, base_url, provider_name}`. Used by chat, delegate, scheduler,
warmup, background. Providers are plain OpenAI-compatible entries in
`config.json → providers`.

Provider-scoped ids exist when multiple providers serve the same model
(`provider/model_id` with `base_model_id`).

### Auto model routing

When the composer model is `✨ Smart (Cloud)` / `✨ Smart (Lokal)` (or an agent
has `model: "auto-cloud"` / `"auto-local"`; legacy `"auto"` = Cloud), the turn's
model is picked per-message by `resolve_auto_model_for_task` (brain.py): classify
the message into one of 5 purposes (coding / analysis / creative / agentic /
fast), then `_resolve_auto_model_tiered` maps the purpose to a tier
(coding/analysis → first reasoning model, fast → cheapest/local, else
highest-priority) within the caller's ACL + attachment-capability set.

**Cloud vs Lokal** is the ONLY difference between the two Smart modes: a `pool`
argument (`"cloud"`/`"local"`) constrains the candidate set to cloud-only or
local-only models — classification and tiering are identical. Empty intersection
falls back to the full enabled set (same never-starve rule as the ACL filter), so
a box with no local model still routes. The composer directive is persisted as
`auto-cloud`/`auto-local` (restored after each turn so a reopened Smart (Lokal)
session comes back as Lokal). Under a GDPR local-only lock the dropdown hides
Smart (Cloud) but keeps Smart (Lokal) (its pool already guarantees a local pick).

**Classifier mode** (`config.json → auto_route.classifier_mode`, default
`keywords`; Settings → Server → Auto-Routing) selects how intent is classified
(dispatcher `resolve_task_analysis`, with `resolve_task_purpose` a back-compat
shim returning only the purpose string):
- `keywords` — regex keyword heuristics (`classify_task_purpose`); zero cost/latency.
- `llm` — a **structured** LLM analysis (`classify_task_structured`, via
  `sidecar_proxy.background_call`, `max_tokens=200`, 25s timeout) on the model
  `_resolve_classifier_model()` picks: the dedicated `classifier_model` knob if
  set+enabled (Settings → Service-Modelle → **Prompt-Klassifikation**), else
  `chat_summary_model`, else the cheapest/local model; **falls back to keywords**
  on None/error/timeout. (The split lets the classifier run on a fast/accurate
  cloud model while summaries stay on a local model — same-knob meant flipping
  one moved both.)
- `hybrid` — keywords first, structured LLM only when keywords find no strong signal.

The structured analysis returns JSON `{task_types[], tools[], complexity,
reasoning}` over two closed vocabularies that map onto existing machinery:
- **task_types** {coding, math, research, analysis, reporting, creative,
  orchestration, agentic, fast} → a tier via `_TASK_TYPE_TIER`; the dominant
  (strongest-tier) type is collapsed to one of the 5 legacy purposes
  (`_purpose_from_task_types`) so the tier map + picker work unchanged.
- **tools** {python, bash, files, web, memory, email, git, code_graph,
  delegation, scheduler, translation, image_gen, audio, skills} → real
  `TOOL_GROUPS` names (`_TASK_TOOL_GROUPS`).

**Model pick precedence** (`_resolve_auto_model_tiered`, highest first):
1. **Attachments** — restrict to models whose `raw_formats` match, but ONLY for
   raw `image/*` uploads (the only MIME sent raw; PDF/docx/… are converted to
   markdown and readable by any text model, so they don't narrow the pool).
2. **ACL** — narrow to the caller's allowed models.
3. **Benchmark ranking** (the measured path) — if any candidate has a benchmark
   for the turn's first task_type, rank **capable → cloud → capability-band →
   cheap** (`_bench_rank_key`, intelligence-per-buck since v9.276.0). Capability
   acts twice, both times bucketed, never as a raw maximand: (a) **FLOOR** — a
   model either clears a complexity-adjusted floor (base 50, `high` +20, `low`
   −20) or not; (b) **BAND** — among floor-qualified candidates the strongest
   one anchors a band (`_cap_band_width`: `high` 5, `medium` 15, `low` 25
   percentile points); models inside the band ("nearly as smart as the best")
   sort ahead of those below it, and WITHIN the band the **cheapest**
   (`cost_input+cost_output`) wins — so a near-frontier model at a tenth of the
   frontier price beats both the frontier model and a half-as-capable one at
   the same price. Raw capability only breaks exact cost ties (equal price →
   the smarter model). Among the capable set, **CLOUD sorts ahead of LOCAL**
   (the same `never cloud→local` rule the fallback walk enforces, applied at
   the primary pick), and speed is a late tiebreak (BUCKETED throughput,
   `_tps_bucket`, rel-width 0.15 — noise can't decide; the buck ranks before
   the tok/s). Complexity moves BOTH the floor and the band width.
   `bench_cell_value` reads `override ?? measured`.
   See **Model benchmark** below.
4. **Tier heuristic** (no benchmark for the task) — the purpose's baseline tier
   (`_PURPOSE_TIER`) **shifted by complexity** along `_TIER_LADDER` [fast,
   default, reasoning]: `high` up, `low` down, `medium` same. Then `reasoning` →
   first `thinking_format != none`; `default` → highest-priority (cloud
   `default_model`); `fast` → cheapest CLOUD (`_pick_cheapest_cloud`), local last.

**Fallback walk** (`_fallback_walk`) when the ranked pick leaves the allowed
pool (disabled / not in ACL between benchmark and use): prefer the SAME model
family (`model_family` — mistral/gemma/qwen/claude/…, nearest capability), then
SAME locality (cloud→cloud / local→local), **NEVER cloud→local**, else the
configured `default_model`, else the first candidate.

### MoA virtual model (🧬 "Experten-Gremium", Mixture of Agents, v9.268.0)

User-facing name since 9.271.0: **Experten-Gremium** (composer entry
"🧬 Experten-Gremium", cards "Experte" + GREMIUM badge, Settings section
"Experten-Gremium (MoA)"). Internal ids deliberately UNCHANGED: directive
`moa`, config block `moa.*`, card kind `moa_reference`, cost_purpose
`moa_reference`, `/v1/status → moa_enabled`.

`moa` is a FOURTH composer directive next to `auto`/`auto-cloud`/`auto-local`
(`_parse_auto_directive` → cloud pool; NOT a `config.json → models` entry). It
rides the full Smart (Cloud) path — the auto-routed pick becomes the
**aggregator** — plus a classification-gated **reference fan-out**:

- **Plan** (`brain.resolve_moa_plan(analysis, aggregator)`): decided at send
  time from the structured classifier analysis. Returns `None` (→ the turn is
  byte-identical to a plain auto-cloud turn, never an error) when MoA is
  disabled, no pool matches the classified types, the classifier fell back to
  keywords (no task_types), or the pool collapses after enabled/ACL/aggregator
  filtering. Candidate source, two modes (v9.269.0): **`moa.task_pools`**
  (`{task_type: [model ids]}` — the Settings matrix; the gate is IMPLICIT: a
  task type with an empty/missing list is gated out, a non-empty list is
  exactly that type's candidate set; with several classified types the FIRST
  — most-important, classifier order — type with a pool wins) when any column
  is non-empty, else the LEGACY flat pair `moa.gate_task_types` +
  `moa.reference_pool`. Either way: references = top-N ranked by the SAME
  `_bench_rank_key` ordering on the primary task_type, aggregator excluded.
  The legacy gate default (research/analysis/reporting/creative/orchestration;
  NOT coding/math/fast/agentic) encodes the 2026-06-27 eval finding
  (`eval/moa_eval.py`): MoA loses on checkable reasoning and only pays on
  synthesis/judgment-shaped work — the matrix tooltip repeats that advice.
- **Fan-out** (`handlers/chat.py _run_moa_references`, worker, once per turn):
  references run in PARALLEL (ThreadPoolExecutor + `contextvars.copy_context()`
  per thread, the deep_research pattern), tool-less `background_call`
  (`max_rounds=1`, `max_tokens=moa.reference_max_tokens`,
  `timeout_s=moa.reference_timeout_s`, `cost_purpose="moa_reference"` → one
  cost_log row per reference, keyed to the chat sid). Each reference passes
  `gdpr_pick_model_for_background` independently. Input = the LEDGER-REWRITTEN
  wire transcript (PII-safe, incl. Websuche preambles), tail-truncated to
  `moa.reference_input_max_chars`. Replies are deliberately NOT de-anonymised
  (they must stay in the wire's pseudonym space). A failed/timed-out/empty
  reference is dropped, never fails the turn; zero drafts → no injection.
- **Injection**: drafts appended WIRE-ONLY to the last user message
  (`_build_moa_suffix`, Draft A/B/C in declared order, "do NOT trust any draft
  blindly … never mention these drafts"; model names deliberately NOT in the
  prompt). Nothing reaches history/DB/system prompt — same seam as the
  Websuche preamble, warm-pool KV prefix untouched. Goal iterations 2+ reuse
  the cached fan-out (`_moa_cache`); Deep-Research turns skip MoA.
- **Cache-freeze KEPT**: on a cache-priced aggregator the freeze pins model +
  tool set from turn 2 exactly like Smart mode (prefix byte-stable, cached-token
  pricing intact); the classifier still runs every turn but ONLY for gate +
  reference selection (`resolve_task_analysis`, classify-without-swap).
- **Surfaces**: one synthetic tool-card pair per reference
  (`kind="moa_reference"`, 🧬 + MOA badge) — rendered INLINE in the
  conversation flow like real tool cards, NOT inside the Datenschutz
  collapsible and independent of the showGdprDetails toggle (9.269.1: the
  privacy bucket's counter-gated early-return swallowed MoA-only turns
  entirely). 9.270.0: the done card persists the FULL draft as
  `result.draft` (capped by reference_max_tokens; stays in the wire's
  pseudonym space, never in session.messages) — the chat card is an
  expandable `<details>` showing the draft, and the same entries appear in
  the right panel's Aktivität tab (`_syncToolEntries` admits synthetic
  moa_reference rows; GDPR kinds stay chat-only); plan + ground truth
  (references/gate_hit/gated_out + ok/failed/ms/models) ride
  `auto_route.moa` into the done event + `msg_metadata.auto_route`.
  Dropdown entry `🧬 MoA (Smart)` is gated on `/v1/status → moa_enabled` and
  hidden under the GDPR local-only lock (references are cloud).
- **Draft mode** (`moa.task_modes {task_type: "answer"|"plan"}`, v9.271.0;
  defaults research/orchestration/agentic → "plan"): what references RETURN.
  "answer" = full candidate answer (Hermes original; content ensembling — the
  eval-backed win on synthesis/judgment). "plan" = APPROACH only (steps,
  sources, verification, structure, pitfalls; the reference system prompt
  FORBIDS answering so stale parametric knowledge can't smuggle in wrong
  facts) — for tool-heavy types where tool-less references can't answer but
  can advise; the aggregator suffix then says "pick the best combination of
  these approaches and EXECUTE it with your tools". Mode rides
  `resolve_moa_plan → plan.mode`, the cards (args/result.mode, "· Ansatz")
  and `auto_route.moa.mode`.
- **Fixed orchestrator** (`moa.task_aggregators {task_type: model_id}`,
  v9.274.0; missing/"auto" = auto-route pick, the default): pins WHO
  synthesizes per task type. When the fan-out gates in on that type,
  `resolve_moa_plan` returns `plan.aggregator` and the send handler switches
  the turn to that model (references exclude it, so it never advises
  itself). Wins over the cache-freeze too: on a frozen session the turn
  switches, and if the fixed model is itself cache-priced the freeze moves
  onto it. Invalid/disabled/ACL-blocked values silently fall back to auto.
- **Config** `config.json → moa` {enabled, task_pools (the matrix),
  task_modes, task_aggregators, max_references, reference_max_tokens (600),
  reference_timeout_s (60), reference_input_max_chars (24000); legacy:
  reference_pool, gate_task_types}
  — Settings → Server → "MoA (Mixture of Agents)" renders a scrollable
  model × task_type checkbox MATRIX (rows = enabled cloud models, columns =
  the 9 classifier task_types; first open without task_pools seeds from
  legacy pool × gate; header rows below the column titles pick the per-column
  contribution mode (Antwort/Ansatz) and orchestrator (Auto/fixed model)).
  Saved via `POST /v1/services/server {moa:{…}}`, which
  validates task_types against the classifier enum and models against enabled
  models (a typo would otherwise silently disable MoA); empty columns are
  dropped on save.
- **Limits**: scheduler add/update reject `model="moa"` (fire-time coerces a
  legacy row to `auto`); references see text only (images stay
  aggregator-only); quota pre-flight covers only the aggregator (reference
  costs post-hoc, like Deep Research); first token waits for the slowest
  reference (the cards make the wait visible).

### Model benchmark (capability + speed ranking)

Since v9.275.0 the two halves of a benchmark cell come from DIFFERENT places:

- **Capability % = OFFICIAL leaderboards** (`engine/bench_official.py`):
  **Artificial Analysis** Data API (intelligence/coding/math/agentic indices;
  needs the free API key in `config.json → benchmark_official.
  artificialanalysis_api_key`, saved via the Models-tab input; their ToS
  require the attribution line the GUI shows) and **LMArena** category Elo
  (coding/math/hard_prompts/instruction_following/creative_writing/multi_turn/
  overall) from the official HF dataset `lmarena-ai/leaderboard-dataset`
  (CC-BY-4.0, no auth). Per task type a source preference chain
  (`TASK_SOURCE_MAP`: checkable skills → AA indices; taste/format tasks →
  Arena Elo). The stored capability is the model's **PERCENTILE within the
  full leaderboard distribution** (pool-independent; mid-field commercial
  ≈55, frontier ≈90 — calibrated to the router's floor 50 ±20). Model
  identity resolves by normalized-name matching (provider prefix, `-latest`,
  quant/instruct suffixes stripped): version-PINNED ids match exactly;
  rolling ALIAS ids (`-latest`) pick the NEWEST family entry — AA
  `release_date`, else the YYMM date token in the name (9.275.1: AA parks
  its oldest release under the bare family slug, so an exact hit must not
  short-circuit an alias). A per-model `official_names {artificialanalysis,
  lmarena}` override (the "Zuordnung" inputs in the GUI) wins when the
  auto-match is wrong. Fetched payloads cache in `agents/main/bench_official_cache.json`
  (24h TTL per source; fetch failure → stale cache → internal fallback).
- **Speed = INTERNAL seed test** (`engine/model_bench.py`,
  `measure_only=True`): the tiered prompt set (`BENCH_TASKS`) still runs on
  YOUR hardware/providers to measure real mean throughput (`tps`,
  tokens/sec), but no scoring happens for officially-covered cells.

**Internal fallback**: models/tasks absent from every official source (local
fine-tunes, oMLX models, brand-new releases) run the full legacy cell —
answer + HYBRID scoring (deterministic `check`s exact/regex/all/`pyfunc`
sandbox, else the **server `default_model`** as LLM judge, which is why a
default model is still required to start a run) — tagged `source:"internal"`.
Persists to `config.json → models.<id>.benchmark.<task> = {measured,
override?}` with `measured = {capability, tps, n, ts, source, raw?,
official_name?}`. An admin **override** (editable cap%/tps in the same table)
wins over `measured` at routing time and survives the next benchmark run
(which only rewrites `measured`). Endpoints: `POST /v1/models/config
{action:"benchmark", model_id?, task_type?}` (background, admin) + `GET
/v1/models/benchmark/status` (live progress; first phase shows
"Leaderboard-Daten laden…"); `POST /v1/services/server {benchmark_aa_api_key}`
saves/clears the AA key, `GET /v1/models/config → benchmark_official.
aa_key_set` (admin) reports key presence. No benchmark for a task → the tier
heuristic (step 4) applies, so the feature ships dark.

**Classifier-driven tool DEFERRAL** (a SEPARATE axis from model selection):
tool optimization runs whenever the per-agent flag `token_config.optimize_tools`
(default **ON**, edited in the agent's **Token-Optimierung** tab) is on AND the
model is safe to reshape — INDEPENDENT of whether the turn auto-routed. On
concrete-model turns the worker calls `resolve_task_analysis` purely to populate
the needed tool groups; `session.model`/provider are NOT touched (model routing
stays gated to Smart/first-turn).

The reshape gate is `model_should_optimize_tools(model)` (NOT the old
`model_maintains_warm_prefix`): optimize iff there is **no warm KV prefix to
protect** — i.e. a **cloud** model, OR a **local model with warmup DISABLED**
(it is never warmed, so nothing to lose; this is the case the old gate wrongly
skipped), OR a model warmed in **`warmup_mode: "minimal"`**. A **full-mode**
warmup-ENABLED model (local or cloud) is left untouched — keyed on warmup
*config*, not transient warm state, so a momentarily-cold full-mode warmup model
isn't optimized into a trimmed prefix that the next warm turn diverges from.
Tools are part of the warm KV prefix (the tool schemas serialize into the prompt
before the first message), so varying them per turn would invalidate it → full
prefill (~20 s) — that is *why* full-mode warmup models are exempt, not an
arbitrary rule.

**`minimal`-mode is the exception that gives you both:** a minimal-mode prime
(`run_model_warmup` mode="minimal") sends NO system prompt and NO tools — just a
1-token user message — so it keeps the model **weights** hot but primes no
tool-bearing prefix. There is nothing for per-turn reshaping to invalidate, so
the gate returns **True** (optimize) even though `warmup: true`. This is the
config for a single local model you want kept warm (~weights-load latency, not
full prefill) AND classifier-trimmed per turn — e.g. `gemma-4-12B-it-qat-4bit`
with `{warmup: true, warmup_mode: "minimal"}`. The gate resolves `warmup_mode`
exactly as the keeper does (default `"full"`; any non-`minimal` value → `full`),
so gate and keeper stay in lockstep.

When the gate passes, `classifier_tool_deferral(model, tool_groups)` returns
`(defer_extra, undefer)` which `resolve_active_tools` folds into its defer set:
un-needed groups are **deferred OUT** of the initial prompt but stay
**`tool_search`-discoverable** (a misclassification is recoverable mid-turn — NOT
excluded), and the analysis's **needed** groups are **UN-DEFERRED** into the
prompt even if statically deferred. The never-strip floor
(`_TOOL_GATING_NEVER_STRIP_TOOLS` = `tool_search` + `ask_user`) keeps only those
two structural tools always in-prompt; everything else (incl. the file/shell
cluster) is classifier-gated. When the flag is OFF, or the model is
warmup-protected, `_auto_tool_groups` is left `None` (no classifier cost, static
deferral stands). No-signal → static deferral stands (fail-open).

**Cache-priced models freeze routing to turn 1.** A model with an explicit
non-zero `cost_cache_read` (per-model config) is "cache-priced" —
`brain.model_is_cache_priced(model)` is the single trigger. The point: such a
provider (e.g. Mistral via CLIProxyAPI) serves a byte-identical prompt prefix from
its own cache at ~0.1×, so the prefix must stay stable across turns. Two effects:
(1) `model_should_optimize_tools` returns **False** for a cache-priced model (never
reshape its tool set per turn — same KV-prefix-stability reason as full-mode warmup).
(2) Once an Auto session routes to a cache-priced model, `handlers/chat.py` records
`session._cache_freeze_model` and on every later Auto turn **reuses that model + the
turn-1 tool set and SKIPS the classifier entirely** (no per-turn classifier LLM
call) — the spinner shows `frozen: true`. The freeze sticks for the session even if
a later turn's content would route elsewhere (by design — maximizes cache hits).
Non-cache-priced models are unchanged: re-classify every Auto turn. The realized
saving is visible — `cache_read_tokens` flows into the live usage event + turn
metadata, rendered as a `⚡ N cached` badge in the status bar and per-turn stats.

**Per-turn classification modal**: a turn with a classification persists its
decision on the assistant turn's `metadata.auto_route` (analysis + chosen model +
reason + `tool_gating` from `brain.classifier_gating_decision`), surviving reload
like `metadata.web_sources`. A compass chip opens `openClassificationModal(idx)`
(chat_render.js) showing detected task types, needed tool families, complexity,
the model decision + why, and the tool-deferral decision (`Im Prompt` vs
`Zurückgestellt (per tool_search abrufbar)`, or why it was a no-op for a
warm/local model).

LLM and hybrid **fail open to keywords** — a failed classifier call or slow local model
never blocks a turn. Config-wise the mode set is unchanged (still
`keywords|llm|hybrid`, default `keywords`, ships dark); only the `llm`/`hybrid`
internals got richer.

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
context manager push/token-resets — automatic teardown). Background
(blocking) turns rebuild the context via `sidecar_proxy._apply_bg_context`
inside their own `with request_context()`.

The old `_thread_local = threading.local()` request-state bag is **gone**
(Tier G, 9.12.0). Bleed invariant: a fresh thread starts with empty
context, so HTTP and per-task threads are bleed-free — but never set
request context bare on a pooled (`ThreadPoolExecutor`) thread; always
wrap it in `with request_context()`. (DB-connection pooling still uses
`threading.local()` — a separate, untouched pattern.)

## Supervised subprocesses

Two long-lived helper processes, each its own venv, each managed by a
`ProcessSupervisor` subclass (3-crash-in-60s circuit breaker + HTTP health
probe), admin status/restart endpoints (the LLM loop is NOT one of them —
it runs in-process since v9.247.0):

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
  gracefully when down. Admin: `/v1/crawl4ai/{status,restart}`. The SAME
  service also serves `POST /render_stealth {url}` — a Scrapling
  `StealthyFetcher` render (stealth Firefox, Cloudflare-Turnstile bypass) whose
  HTML is converted to markdown with crawl4ai's own generator (so the output
  format matches `/render`). `brain._crawl4ai_render_stealth()` calls it as a
  SECOND fallback; Scrapling is a soft dependency in the same `.venv_crawl4ai`
  (absent → endpoint reports unavailable, `/render` unaffected).

`web_fetch` content handling: the response Content-Type / URL extension is
checked FIRST. A non-HTML FILE (PDF/DOCX/XLSX/PPTX/CSV/image — by URL ext,
Content-Type, or `%PDF` magic bytes) is ingested like an uploaded file:
documents go through the shared `doc_convert._do_extract` pipeline
(fitz/pdfplumber + OCR) tagged `fetch_method=document`, images through the
vision describer tagged `image`. This is what stops a direct `…/foo.pdf`
link from returning raw `%PDF…` binary (the v9.139.0 fix — the project
web-url miner was storing that garbage). HTML/text/JSON keep the
markitdown→crawl4ai chain: markitdown HTML→md first; crawl4ai render fires
on an HTML GET when the converted text is THIN (<600 chars — v9.99.2 raised
this from <30 so a consent-wall teaser triggers it, not just an empty shell)
OR when the final URL is a consent/cookie interstitial (`/consent`, `/tcf/`,
`cookie`, `datenschutz/zustimmung`). The render is only taken when it's
strictly longer than the HTTP result (guards against a render that itself
hits the wall). If the content is STILL thin (<600 chars) after the crawl4ai
render — the page is behind real anti-bot/Cloudflare protection that headless
Chromium can't pass — a SECOND fallback fires: `/render_stealth` (Scrapling
StealthyFetcher), again kept only if strictly longer, tagged
`fetch_method=scrapling`. Every result is tagged `fetch_method`
(raw/markitdown/crawl4ai/scrapling/document/image/academic), surfaced as a
chat-view badge.

## Document extraction (read_document + mining)

One pipeline (`engine.doc_convert._do_extract`) serves chat read_document,
project mining, PII scan, classification, AND project file upload/ingestion
(`IngestManager.ingest_file` → `DocumentParser.parse_*` are thin shims over
`_do_extract` — including `parse_pdf`, fixed 9.157.1; before that PDF used bare
fitz with no OCR, so a scanned PDF failed the whole project import, and
`.eml/.msg` weren't accepted at all). So an uploaded project file extracts
EXACTLY like a chat attachment. Per file type it tries
**markitdown first** OR goes straight to Brain's own `_extract_*`. That split is
**config-driven** (was a hardcoded constant): `config.json →
conversion.markitdown_exts` (editable per type in Settings → Service-Modelle).
Own-code: `.xlsx/.xls` (footer-group recovery — markitdown loses member↔group),
`.csv/.tsv`, `.eml` (markitdown leaks MIME headers). `.epub/.zip` are forced
markitdown (no own extractor).

**PDF has its own engine** (`conversion.pdf_engine`, default **pymupdf4llm**):
`pymupdf4llm` (a fitz wrapper — renders tables/layout to clean markdown, best on
financial reports; verified on the WPB Konzernbilanz) | `markitdown` | `fitz`
(plain `page.get_text`, flat). Backend tag = the engine that produced the text.

**Timeout + deterministic fallback (pymupdf4llm path):** pymupdf4llm's layout
analysis can hang for MINUTES at 100% CPU on a big, table-dense PDF that fitz
reads in 0.1s (chat 4aad5750: a 37-page list; web_fetch returned EMPTY). The
analysis is CPU-bound and UNINTERRUPTIBLE from Python — a daemon-thread timeout
can only *abandon* it, leaving it to peg a core indefinitely (the 9.156.x "server
down": one web-fetched PDF froze a chat turn for minutes while the server stayed
HTTP-reachable for light GETs). So pymupdf4llm now runs in a HARD-KILLABLE
SUBPROCESS (`_pymupdf4llm_subprocess`): `subprocess.run(timeout=_PDF_EXTRACT_TIMEOUT_SECS=60)`
SIGKILLs the child on timeout and RECLAIMS the CPU. `_do_extract` calls
`_extract_pdf_pymupdf4llm` directly (the subprocess is the single timeout
authority; it raises `_ExtractTimeout` → fitz). ONE whole-doc subprocess call for
all sizes (~1.8s for 18 pp; the old per-page loop spawned N subprocesses ≈8.6s —
pure overhead now that the whole call is bounded). Chain:
**pymupdf4llm (subprocess) → (timeout OR empty) → fitz get_text → (empty = true scan) → OCR.**
"Empty" here also counts a pymupdf4llm output that is ONLY its image-placeholder
lines (`**==> picture [W x H] intentionally omitted <==**`) — a scanned page emits
one per embedded image (100+), so the raw line count looks substantial but holds
zero text; `_pymupdf4llm_is_blank()` strips those before the emptiness check so
image-only PDFs actually reach OCR (fixed 9.157.1). It ALSO strips empty
markdown headers (bare `##`/`###` with no text) — pymupdf4llm sometimes emits a
run of those for a text-PDF whose body it failed to lift, and without this the
first such line read as "real content" so the fitz fallback never fired (fixed
9.160.7: a Wiener-Privatbank letter gave 77 chars of empty headers + a picture
placeholder while fitz lifted the full 1578-char text). A header WITH text stays
real content.
markitdown is deliberately SKIPPED here — it bottoms out on pdfminer just like
pymupdf4llm (≈same hang on the same input) AND gives no quality fitz can't
deliver faster, so falling to it just doubled the stall. The fitz/pdfplumber
LEGACY path (when `pdf_engine` != pymupdf4llm) keeps its thread-based
`_run_with_timeout` (different extractor; bare fitz is GIL-releasing + sub-second).
(markitdown is still the primary path for `.docx/.pptx/.epub/.zip` and when
pdf_engine is explicitly set to `markitdown`.) LICENSE: pymupdf4llm/PyMuPDF is AGPL-3.0
(Artifex) — fitz was already in use, so no new exposure.

**Live tool progress** (`engine.context.report_tool_progress(phase, pct?,
current/total?, note)`): any tool can emit a `tool_progress` SSE (auto-tagged
with the dispatch `tool_use_id`) → the live tool card shows a phase label +
optional % bar while it runs (cleared by `tool_result`). Display-only, never
persisted; allow-listed in `make_artifact_event_callback`. Consumers: PDF
extraction (pymupdf4llm/fitz/OCR page i/N + phase switches), web_fetch
(Abrufen/Rendern), python_exec/execute_command (Läuft). The final extraction
BACKEND is also shown as a durable badge (read_document `backend` field;
web_fetch `fetch_method=document:<backend>`).

**Truncation invariants** (the fc3fa95b 561k-token incident): mining fetches
web-urls with `max_length=10_000_000` (mining → disk + chunked embedding, NOT an
LLM context, so the per-turn 50k cap was wrong — it had silently cut a 524k-char
PDF before its balance sheet). The chat `web_fetch` keeps its 50k per-turn cap
(protects context; abstract mode was removed v9.125.0 — always full content).
**`read_document` has NO size cap** — it returns the extracted content VERBATIM
(tool_mcp hard rule: no truncation/summary); the only ceiling on a big read is
the model's context window. (`_apply_tool_result_budget`'s disk-spill+preview is
dead on the chat path — the loop owns the ephemeral tool exchange, results
never live in `session.messages`; CHANGELOG 9.46.5.) `read_document(pages=…)`
only applies to PDFs; on `.md/.txt` it returns a `note` (use offset/limit)
instead of silently ignoring.

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
  per-turn mechanism, resolved from the worker's request context). All non-web tools stay live. There is **no** `web_search` tool.
- **Escape hatch**: `sessions.allow_further_web` (sticky, default 0), inert
  when the basket is empty; when on, curated sources are still pre-fetched
  but the model may also search/fetch.

No version history is kept for project sources: a CHANGED file is re-mined
(content-hash dedup) and its KG triples invalidated (`_invalidate_source_in_kg`
on mtime/size shift). KG triples for a DELETED file are dropped two ways: the
per-source loop purges a gone file that still has drawers, AND a drawer-
INDEPENDENT orphan sweep (`_purge_orphan_kg_sources`, run at the start of
`run_kg_post_pass`, scoped to source_prefix+adapter) drops triples whose
absolute source_file no longer exists — covering the case where the file's
drawers were already purged (UI delete), so the per-source loop never sees it
(without the sweep those triples orphan forever → "0 files but N relations").
A DELETED file's drawers are purged by `_is_stale_src`
— which flags a drawer stale when its source_file is outside every current
input-folder/pdir prefix OR is an absolute path whose file no longer exists
(covers a single deleted file in a still-configured folder; synthetic markers
like `session/...#...` are never path-checked, so chat/profile/summary
memory is never purged). So a query only ever sees the current state of each
source — no stale/old-version noise.

This is a DIFFERENT mechanism from project `web_urls` (mined into the
project wing/KG by the project-sync daemon) — do not merge them.

**Code Mode (project.code_mode + working_dir).** A per-project toggle that
turns the project into a working-directory agent instead of a MemPalace-backed
one. When on: NO ingest / NO MemPalace; the chat's cwd IS the project's
`working_dir` (a user-picked path, validated to exist). File tools read/edit/
create THERE — `apply_domain_context` sets `ctx.working_dir` and excludes the
MemPalace tools (mempalace_query, save_chat_to_memory, mempalace_get_drawer,
mempalace_list_drawers, read_document); `_resolve_artifact_dir` + execute_command/
python_exec cwd prefer working_dir, and `_resolve_under_cwd` (file_tools.py)
resolves RELATIVE paths in read_file/list_directory/search_files under
working_dir (non-code-mode keeps process-cwd abspath). `BRAIN.md` at the
working-dir root is the project memory — plain markdown, NEVER mined, injected
verbatim into the system prompt (`_build_system_prompt` code-mode branch;
cache-key folds working_dir + BRAIN.md mtime). `init` (POST
…/projects/<name>/init, or the user typing "init" in chat) runs an agentic
background turn (engine/code_init.py → background_call purpose=interactive,
cwd=working_dir) that explores the dir and writes BRAIN.md — one agentic pass
(selective key-file reads, like Claude Code's /init), not per-file. working_dir
flows to the loop's per-tool-call context via tool_context. code_mode is FIXED AT CREATION — two
overview buttons ("Neues Projekt" / "Neues Code-Projekt"); create_project reads
code_mode+working_dir, and code_mode is NOT in the update_project whitelist
(immutable; working_dir stays editable). UI: code projects render a distinct
`</>` glyph + "Code-Projekt" label in the overview; the detail panel shows a
Code Mode section (working-dir /v1/files/tree picker + "generate BRAIN.md"
button + a recursive collapse/expand file tree of the working_dir, refreshed on
open / dir-set / init / after each turn) only for code projects, and hides ALL
the MemPalace-only sections (Projektmodus, Quellen/ingest, Wissensgraph, Speicher
& Abgleich). The folder-tree endpoint allows a code project's working_dir (or
descendants) and skips the wing/KG status lookups there. The grounded-answer /
citation discipline (+ its validator) is SKIPPED in code mode — chat.py checks
`get_request_context().working_dir` before the discipline branch; a code project
has no curated sources to cite, and read_file/list/search are work tools, not
grounding-retrieval (was wrongly flagging "N von M ohne Quellenangabe"). The
chat RIGHT PANEL in a code-mode chat shows Anhänge + Aktivität +
**Arbeitsverzeichnis** (artifacts/references/websuche hidden via
updateWorkdirTabVisibility, panels_workdir.js): split pane — recursive
working_dir tree on top, inline file viewer below (text/code hljs, md rendered,
img blob, pdf iframe) + size/mtime status line + download. folder-tree returns
size+mtime per file; /v1/files/download's _validate_file_path also allows a code
project's working_dir.

**Source-group context stamped into drawers (per-customer separation).** When an
ingested file is assigned to a virtual source group (`project.json`
`source_groups.files.assign`: source_hash → group_id — e.g. one group per
customer, built by the folder-upload picker/drop or manual grouping in the
source tree), the project-sync daemon stamps the group's FULL path into the
file's body BEFORE mining (`_apply_group_prefixes` in `server_daemons.py`, run
just before `mp_miner.mine` on `ingested/`). A marker line
`> [Projekt-Gruppe: Kunde A / Verträge]` is repeated densely (before each
paragraph + every ~600 chars inside long paragraphs, well under the miner's
~800-char chunk window) so EVERY resulting drawer carries it — a
`mempalace_query` hit then self-identifies its group and the LLM never conflates
Kunde A with Kunde B. Patch-free (no mempalace-venv change): the marker lives in
the drawer TEXT, not a queryable metadata field, so there's no query-time filter
or UI selector — the context rides the content. Idempotent + mtime-gated:
rewrites only when the desired marker differs (so the miner's mtime-skip is
preserved); re-grouping a file changes `assign` → next sync rewrites the marker
→ that file is re-mined with the new context. Ungrouped files get NO marker
(and any stale marker from a prior grouping is stripped).

**Project-sync cadence + restart gate.** The daemon runs its first pass ~25s
after boot, then sleeps `mempalace.project_sync.interval_seconds` (default
21600s/6h) between cycles. It keeps no in-memory clock, so to stop a RESTART from
re-triggering a not-yet-due sync it gates each SCHEDULED project on
`sync_log.last_completed_at(project_id)` (newest `state='idle'` run in
`project_sync_runs`): if `now - last_completed < interval`, skip this cycle. A
manual "Sync now" always runs; a never-synced project always runs;
error/cancelled runs don't count (they retry next pass). So the interval now
survives restarts (v9.153.1). [The April 2026 change only removed the destructive
startup-WIPE — the incremental boot pass was never disabled until this gate.]

**Fast no-change gate + incremental changed-file path** (v9.189.3–.6). At the
TOP of each per-project iteration (after the web-URL sync, before any phase) the
daemon computes a source FINGERPRINT — a pure `os.stat` walk over
`ingested/` + input folders + `web-urls/`, sha1 of sorted `path|mtime_ns|size`,
no Qdrant/DB/network. If it equals the last successful cycle's
`sync_status.source_fingerprint` AND state is `idle`, the WHOLE project is skipped
in ~0s (`skipped_unchanged=true`) — no mining, KG, or closet work. This is the
common case at the 6h cadence over hundreds of projects. When the fingerprint
DIFFERS, only the changed data is touched: (1) mining pre-filters via ONE
wing-scoped `get(where={wing})` `{source_file: mtime}` map (NOT a whole-corpus
scan) and hands `mine()` only the changed files — the file paths are normalised
with `os.fspath` because `scan_project` yields `PosixPath` while drawer
`source_file` keys are `str`, and a `PosixPath`≠`str` key mismatch silently made
the pre-filter pass EVERY file for years (the ~264s-per-1-file-change bug, fixed
v9.189.6); (2) KG skips unchanged sources via the stable `sha1(source_file)`
cursor key (v9.189.2); (3) closet regen rebuilds ONLY the changed sources
(`_regen_closets_parallel(only_sources=…)`, idempotent per-source purge+upsert)
instead of the whole wing. Net: a 1-file change syncs in ~seconds across all
phases (was ~270–285s). A KG-method/profile toggle purges cursors → forces a
full rebuild by design. `Full Resync` has its own path and is unaffected.
The stored fingerprint is RE-STAT'd at successful completion (not the
start-of-iteration value): on folder/binary projects doc_convert regenerates the
`.brain-extracted/*.md` companions DURING the sync, moving their mtimes after the
fingerprint was sampled — re-stat'ing captures the settled tree so the very next
no-change cycle skips immediately instead of wasting one full catch-up cycle
(v9.189.7). A failed/cancelled run keeps the start fp so it never wrongly skips.
**Folder/binary projects (external recursive `input_folders` of PDF/DOCX) +
web-urls now route through the SAME batched/pre-filtered path as `ingested/`
(v9.189.8)** — they previously used a raw `mp_miner.mine()` that bypassed the
pre-filter. `scan_project` returns the `.brain-extracted/*.md` companions (not
the binaries), which match the stored `source_file` keys so the pre-filter
engages. Three rebuild-cost rules now hold: (a) an EMPTY scan (e.g. `ingested/`
with only `mempalace.yaml`) does NOT call `mine()` at all — an empty mine still
ran the wing-wide entity-link rebuild (the old 168s ghost step); (b) `_mine_batched`
calls `mine()` ONCE over all changed files, not per-25-batch, so the wing-wide
entity-link rebuild (hallways + topic/entity tunnels — a full recompute, cost ∝
wing size, ~100-165s) runs at most once per folder instead of once per batch;
(c) a `# BRAIN-PATCH` in `mempalace/miner.py` gates that rebuild on
`total_drawers > 0`, so a touch-only / identical-re-mine that files 0 drawers
skips it entirely (FTS5 validation moved to its own `if not dry_run` block so it
still always runs). A REAL content change still pays one wing-wide rebuild — it's
an upstream full recompute, not a delta. See [[project_mempalace_venv_patches]].

**Project `web_urls` refresh is cost-gated** (not re-fetched every cycle).
Per-URL state lives in `web-urls/.fetch-state.json`. A URL is (A) SKIPPED with
no network if its on-disk copy is younger than `project_sync.web_url_refresh_seconds`
(default 6h); when due, (B) a validator-bearing URL gets a conditional GET
(`If-None-Match`/`If-Modified-Since`) and a 304 reuses the on-disk copy with no
body download / no re-mine; (C) a URL whose server gives NO ETag/Last-Modified is
SKIPPED until `project_sync.web_url_max_stale_seconds` (default 24h) since its last
real body fetch. Two un-equal ceilings, both measured against `last_full_fetch`
(the last real 200 body; a 304 never bumps it): validator URLs are trusted (304 =
certain no-change) and only force a full RE-VERIFY every
`project_sync.web_url_reverify_seconds` (default 7d, safety net vs sticky ETag /
conversion drift); no-validator URLs force a full fetch every 24h. `refresh=0` ⇒
always re-fetch; `reverify=0` ⇒ trust 304 indefinitely; `max_stale=0` ⇒ trust
no-validator URLs fully. The content hash-gate is still the final re-mine backstop.

## Background tasks (detached, Claude-Desktop-style)

The `run_background_task(title, prompt)` tool spins off a long, output-heavy
run WITHOUT blocking the chat. Mechanics (`engine/background_tasks.py`,
`BackgroundTaskRunner`):

- **Spawn**: the tool inserts a `running` row (`background_tasks` table) and
  starts a daemon thread, returning a `task_id` immediately. The spawning chat
  turn ends normally — nothing waits.
- **Same agent/config**: the thread replicates the session's system prompt +
  tools via `build_first_turn_prefix(model, agent_id)` and runs a fresh
  `sidecar_proxy.background_call(...)` with a pre-minted `turn_id` (so Stopp can
  cancel via `sidecar_proxy.cancel_turn(turn_id)` — the same in-process
  mechanism chat uses). It passes the SAME `gdpr_pick_model_for_background` gate as every
  background call (no bypass).
- **Model offload (per-model fan-out model)**: the chat model's registry entry
  may carry `config.json → models.<id>.background_task_model` — a (usually
  cheaper) model its fanned-out leaf tasks run on. The decompose/orchestrate
  reasoning stays on the chat model; only the `run_background_task` leaf runs
  swap. Resolved in `_resolve_fanout_model` before the DB row is written (so the
  panel, GDPR pick, and loop call all see the leaf model). Empty/unset, or a
  target that's missing/disabled, leaves the leaf on the chat model. The special
  value `"auto"` intent-routes per leaf: the sub-task's prompt is classified via
  `resolve_task_purpose` (see *Auto model routing* below) and the best-fitting
  enabled model is picked with `_resolve_auto_model_tiered` — same intent
  routing the composer's `✨ Auto` uses, applied to each fanned-out leaf. On swap the
  `thinking_level` is smart-matched to the leaf model's reasoning granularity
  (`_match_thinking_level`): on/off models (inline_tags/mistral_blocks) collapse
  low/medium/high → `high`; non-reasoning models drop to model default; full
  reasoning models keep the level verbatim.
- **Cost logging**: after the run, the worker calls `_log_call_cost(model, …)`
  keyed by the **actually executing** model — the fan-out offload swap and any
  GDPR force-local swap are already applied — so an offloaded leaf is billed at
  the cheaper model's rate, and a `cost_log` row lands in `costs.db` like a chat
  turn (was missing before v9.51.0 — bg-task LLM calls were unbilled).
- **Result return — auto-delivery**: when a task finishes, the runner's
  `finally` calls `handlers.chat.deliver_background_results(session_id)`.
  - If the chat is **idle** (no turn streaming), it auto-fires a delivery turn:
    appends a real user-role message built from `_build_background_task_preamble`
    (the full output + "fahre fort"), opens a `LiveStream` on the session and
    runs the SAME `run_session_turn` the HTTP path uses — so any open browser tab
    renders it live via the resumable-streaming seam. Idle-gate + single-flight
    (`_bg_delivery_inflight`) prevent double-delivery; no loop because
    `pop_unconsumed_background_tasks` marks `consumed_at`, so the delivery turn
    finds nothing on its own completion.
  - If a turn **is** streaming, delivery no-ops and the in-flight turn's
    next-turn injection picks the result up instead: `run_session_turn` calls
    `_build_background_task_preamble` → `pop_unconsumed` and injects the output
    wire-only via `_inject_web_preamble_into_wire` (same ephemeral seam as
    Websuche), so it reaches the model once, never enters `session.messages`/DB,
    and drops out of context after that turn — like a tool result.
  Either path consumes the task exactly once.
- **Cancel = partial kept**: Stopp trips a flag + cancels the in-flight turn; the
  worker stores whatever output it had and marks the row `cancelled`.
- **Panel**: the "Hintergrundaufgaben" right-panel tab + top-bar pill
  (`web/js/panels_background.js`) poll `GET /v1/background-tasks` every 2s while
  ≥1 task runs (no new SSE channel). "Transkript anzeigen" hits the transcript
  endpoint (live loop SSE while running, stored replay once finished).
- **Boot reconcile**: a `running` row whose thread died on shutdown is set to
  `error` at startup so the panel never shows a zombie.

Differs from `delegate_task` (targets ANOTHER agent, can block for the result).

## Output generation (Output Presets / Studio / Deep Research)

One SHARED pipeline turns a project's sources into a grounded, cited document
saved to the `project_outputs` store (`03-storage.md`). Built once, reused by
every generator (presets now; Audio Overview + Deep Research later) — do NOT fork
a second generation/storage path.

- **Endpoint** `POST /v1/agents/<a>/projects/<name>/generate {kind, options}`
  (`handlers/projects.py:_handle_project_generate`): validates `kind` +
  project-membership (manage), refuses if the project has no sources, inserts a
  `project_outputs` row `status=generating`, spawns the worker, returns
  `{output_id, status:"generating"}`. The UI polls `GET …/outputs[/<id>]`.
- **Worker** (`engine/output_gen.py`, daemon thread — fresh thread = bleed-free
  contextvars): gathers sources via `tool_mempalace_query` inside a
  `with request_context(project=<name>)` (project-scoped, top-25 drawers, a
  coverage note if truncated — never a silent cut) → ONE
  `sidecar_proxy.background_call(purpose="transform", project=<name>,
  model=_background_model_default())` with the preset prompt → writes the cited
  `.md` under `…/projects/<name>/outputs/` → registers it as an artifact (synthetic
  session `output-<id>`, so the existing artifact-content endpoint opens it) →
  flips the row `ready`/`error` and records the `[Quelle: …]` citation count.
- **Prompts** (`engine/output_presets.py`): four canned grounded prompts
  (study_guide · briefing · faq · timeline) + a shared GROUNDING discipline
  mirroring research-mode Topic B — cite verbatim, use ONLY the retrieved sources,
  omit-don't-invent (Timeline says "no datable events" rather than fabricating).
  Stored in code for v1 (admin-tunable config is a deferred item).
- **Boot reconcile**: a `generating` row whose thread died on shutdown is set to
  `error` at startup (mirrors background tasks).
- **UI** (`web/js/panels_studio.js`): a "Studio" tab on the project detail page.
  Hosts the GENERATE panel (four preset cards + Fokus/Länge → `…/generate`) and a
  BROWSE view (outputs grouped by `kind` with counts; open `.md` in a modal via
  `getArtifactContent` + `renderMarkdown`; ⋯ menu = rename/regenerate/download/
  delete). A 2.5s poll live-updates `generating→ready` (mirrors
  `panels_background.js`), stopping when nothing generates or the tab is left.
  Rename = `…/outputs/<id>/rename`; delete = `DELETE …/outputs/<id>` (row + file).

## LLM Wiki (the agent's memory + a user-visible knowledge base)

`engine/wiki_store.py` over `wiki_pages`/`wiki_page_versions` (see `03-storage.md`).
A user-visible, editable markdown page tree (UI: `panels_wiki.js`, tools: `wiki_*`,
API: `/v1/wiki/*`). Every saved CURRENT version is mirrored into the page's
MemPalace wing (`user__`/`team__`/`wiki_global`, or `project_chat__<id>` if
project-tagged) as one drawer `source_file=wiki/<id>` — so the wiki is the **sole
feeder** for chat-derived wings (the old `mempalace-chat-sync` daemon is retired).

- **Versioning**: every edit appends an immutable version; `current_version`=MAX is
  the only editable + searchable one. `promote_version` copies an old version to a
  new current (append-only). `source_ref` ties an auto-generated page to its origin
  so a changed source re-versions the SAME page (no forking).
- **Auto-feeders** (all via `upsert_from_source` → diff-merge preserves manual
  edits, no-op merge skips a version):
  - **chat memorize** (`wiki_from_chat`): the 'merken' action LLM-organizes the
    selected turns into one topic-titled page, `source_ref=session/<sid>`. ALSO
    automatic — when a session has `save_to_memory>0`, the chat worker re-wikifies
    it in the background after each turn (debounced ≥90s, first turn always
    fires). This is the replacement for the retired mempalace-chat-sync daemon.
  - **Studio/Research outputs** (`wiki_from_artifact` from `output_gen.save_report_output`):
    files every generated report, `source_ref=output/<id>`.
  - **profile/activity** (`wiki_from_artifact` from `_write_user_profile_atomic`):
    the auto-maintained user profile as a 'Profil & Aktivität' page,
    `source_ref=user-profile/<uid>`.
- `wiki_read(query)` searches across ALL the caller's accessible wings (user +
  teams + global) and merges — `mempalace_query` alone defaults to the user wing.
- **Opt-in KG** (`mempalace.kg.wiki`, default OFF): when on, a PROJECT-tagged
  wiki page also gets KG triples extracted into the project's
  `knowledge_graph.sqlite3` after each save (`_kg_for_wiki_page_async` →
  `run_kg_post_pass`, adapter `brain-wiki-kg`, `source_file=wiki/<id>`, prior
  triples invalidated first so a re-save replaces). user/team/global wiki KG not
  built (no project KG scope). The scheduled-results→wiki feeder (`schedules.wiki_file`)
  files each run as a fresh VERSION of one page (`source_ref=schedule/<id>`,
  `replace=True` — no diff-merge).
- **KG extraction method (LLM vs rule-based) + profile are configurable PER
  SCOPE** (v9.118.0). `run_kg_post_pass(method=...)` takes `llm` (default — one
  LLM call per chunk, model = `mempalace.kg.extraction_model`, can be cloud) or
  `rules` (`engine/kg_rules.py` — NO LLM: its own spaCy `de_core_news_md`
  pipeline + a German/English relational-cue lexicon emit generic-profile
  triples; fully local, so it skips the GDPR model-swap/pre-scan seam entirely).
  Rule output is OPEN lowercase predicates only → the `generic` profile is forced
  for `rules`. Config split: `mempalace.kg.method`+`profile` = the project-wide
  DEFAULT (overridable per project via `project.json → kg_method`/`kg_profile`,
  empty = inherit; resolved in `server_daemons._run_kg_for`); `mempalace.kg.wiki`
  + `wiki_method` + `wiki_profile` = the INDEPENDENT wiki knobs (read in
  `wiki_store._kg_for_wiki_page_async`). Admin sets the wiki + project-default
  knobs in General Settings → Knowledge Graph; per-project overrides live in the
  project view ('Wissensgraph (KG)' section). `POST /v1/mempalace/kg/config`
  validates all four and coerces a `rules` method's profile → `generic`.

## Deep Research (the bounded agentic loop)

The marquee feature (`engine/deep_research.py`). Two modes on the project's
"Research" tab, one import seam:
- **Backend (single)** — `active_backend()` returns THE one search tool Research
  uses, or `""`. A tool counts only when BOTH configured (exa: API key; searxng:
  base URL) AND enabled in `tool_settings.<tool>.enabled` — the SAME toggle that
  gates the chat agent's web search. There is NO merge and no per-run choice: the
  admin enables exactly one search tool; enabling both is a config problem the
  admin owns (searxng wins the tiebreak). `""` ⇒ the Research tab is disabled (E1).
- **Fast** — `POST …/research/search` runs the enabled backends, dedups vs
  `web_urls`, returns a SERP; the UI appends approved URLs via `update_project`
  (the sync daemon mines them). No background task.
- **Deep** — `POST …/research/deep` spawns a daemon-thread loop tracked in
  `research_runs`. DETERMINISTIC orchestration (CLAUDE.md rule 5); the LLM is used
  at exactly THREE judgment points: (1) decompose topic → ≤8 sub-questions, (2)
  rank/select the fetched candidates, (3) grounded cited synthesis. Plain code does
  search (searxng/exa, merged+deduped), `web_fetch` of top candidates within the
  FETCH budget, dedup, and budget accounting.
- **Concurrent I/O**: the per-sub-question searches AND the candidate fetches each
  fan out over a BOUNDED `ThreadPoolExecutor` (the loop itself stays deterministic —
  this is plain-code parallelism, NOT model-driven fan-out / delegation). Worker
  threads run inside `contextvars.copy_context().copy().run(...)` so the request
  scope propagates (fresh pool threads start empty). Dedup/merge + `fetched`/
  `fetches_used` are mutated ONLY in the parent as futures complete (no lock). Caps:
  `config.json → research.{fetch_workers,search_workers}` (default 4 each, clamped
  1–16). The FETCH cap is the main protection for the crawl4ai render service (an
  uncapped `ThreadingHTTPServer`) — keep it modest on a single box, raise on Spark.
- **Grounding**: synthesis prepends `render_research_mode_disciplines()` (REFUSAL/
  PRECISION/CITATION) so the report cites verbatim `[Quelle: …]` and omits rather
  than invents. Saved via `output_gen.save_report_output(kind=research_report)` —
  the SHARED path, so Studio browses the report with zero new code.
- **Tunable breadth/cost**: all knobs read from `config.json → research.*` (absent ⇒
  prior defaults). `max_subqueries` (8, the real sub-question ceiling — `rounds` is
  pinned at it), `results_per_query` (8, candidates per sub-question search), and the
  budget defaults `fetches`/`tokens`/`rounds` (the per-run API budget still overrides
  these). Candidate breadth ≈ `min(rounds, max_subqueries) × results_per_query`.
  Single reader `_research_int(key, default, lo, hi)`. The `tokens` synthesis ceiling
  (`_fit_corpus`) stays the backstop — widen discovery + tokens together or _fit_corpus
  just drops the extra. (NOT a link-following crawler: Deep Research IS the relevance-
  driven 'go wide on a topic' mechanism; outbound-link crawling optimises author
  navigation, not relevance.)
- **Bounded + visible**: budget default 60 fetches / 80k tok / 8 rounds; the loop
  stops at the cap and the report states bounded coverage (W8 — never silent). The
  UI shows phase + budget live (2.5s poll). The `tokens` budget is enforced at
  synthesis by `_fit_corpus()` — it packs the rank-ordered sources until the budget
  is spent and drops the rest (stated in the coverage note), so the synthesis prompt
  never overflows the model context (overflow → empty completion → 'Empty report').
- **Safety**: every LLM call routes through `gdpr_pick_model_for_background` (E5);
  cooperative cancel via the `research_runs.cancel` flag (E3); degrades if one
  backend fails (E2); boot reconcile flips a leftover `running` run to `error`.
- Sources are **proposed, never auto-imported** — the user approves a subset, which
  appends to `web_urls` like Fast.

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
  tools, the three web tools, and the code-intelligence tools
  (`code_search`/`code_trace`/`code_query`/`code_snippet`). No write/exec tools.
- **Source reach** (9.27.0): in helpdesk_mode `mempalace_query` additively
  searches the shared `brain_code` wing (mined brain-agent source) on top of
  its normal scope — a separate Chroma query pinned to `brain_code`, so the
  project-isolation force-scope is untouched. `code_search`/`code_trace` add
  exact code lookups (find symbol / callers / callees) over the same source.
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

**STEP 1b — CODE lookups with the code-intelligence tools** (when you already
know a symbol, or want to find one). The brain-agent source is also indexed by
the codebase-memory engine:
- `code_search` → find a symbol/function: `query` (BM25 natural language),
  `name_pattern` (regex), or `semantic_query` (keyword array, embedding search).
  Best when you don't know the exact name.
- `code_trace(function_name, direction=inbound|outbound)` → callers / callees.
- `code_query(cypher)` → exact structural questions (e.g. all functions in a
  file: `MATCH (n) WHERE n.file_path =~ '.*helpdesk.*' RETURN n.name`).
- `code_snippet(qualified_name)` → read a symbol's source.
`code_search` is good for discovery (fuzzy + exact); `code_trace`/`code_query`
are exact relation lookups. For "find the file for this plain constant", lean
on `mempalace_query` + the repo map. Paths in results may carry a local clone
prefix; strip to the repo-relative part (everything after
`.brain-source-clone/<wing>/`) before building a GitHub URL.

**STEP 2 — read the FULL, CURRENT file from GitHub.** Once you've identified
the file, fetch it raw with `web_fetch` and read the precise value there
(GitHub `main` is live; the mined index can lag by up to one miner cycle):
`https://raw.githubusercontent.com/alexioklini/cctest/main/<source_file>`
e.g. `.../main/handlers/helpdesk.py`. Use the `source_file` path
`mempalace_query` gave you — do NOT invent paths. (v9.40.0: when the URL is a
GitHub-raw URL for a file you just found via `mempalace_query`, `web_fetch`
fetches the full live file but returns ONLY the matched region(s) to you —
`fetch_method` shows `+brain_code_regions`. You get the current code without
the whole module; small files or many-match files come back whole.) You can
also list every path via the git tree:
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

- **SERVER-ONLY detection (9.200.0)**: the browser-side `PIIScanner` (the
  ~70 JS regex rules + Luhn/Mod11 validators) was DELETED. PII is detected
  exclusively in Python (`engine/pii_ner.py → _pii_rules` + `_pii_scan_text` +
  `_pii_scan_bare_identifiers` + spaCy NER). At SEND time (9.205.0) the client
  runs `runCancellableGdprScan(text, files)`: it scans the typed text via
  `POST /v1/gdpr/scan-text` AND each deferred attachment via
  `POST /v1/attachments/scan` under ONE cancellable progress overlay (the heavy
  extract/OCR/NER is the attachment scan, so progress+cancel live there). Files
  are NO LONGER scanned at attach time (was: background scan on attach, marked
  `scan.state='pending'`; now `'deferred'`, scanned on send). The rule catalog the
  Settings panel + chat-view labels render (rule→category, German category +
  rule labels) is served in `gdpr_scanner.catalog` (`/v1/services/status`) from
  `PII_RULE_CATEGORIES` / `PII_CATEGORY_LABELS` / `PII_DEFAULT_CATEGORY_ACTIONS`
  / `PII_RULE_LABELS` (`engine/pii_ner.py`); the client caches it in
  `state.gdprCatalog`. No browser as-you-type draft scan anymore — the composer
  shield badge reflects the (server-async) history scan AND whether the chat
  carries prior decisions.
- **Composer shield + history modal (9.203.0)**: the shield button
  (`btn-pii-history`) shows when `piiHistoryHasFindings(chat)` (async server
  scan) OR `chat._piiDecisions` is non-empty — so it survives a reload even when
  the live scan finds nothing (anonymised stored text / scanner disabled).
  `decisionsHas` is computed BEFORE the `state.piiScannerEnabled===false`
  early-return in `updatePIIBadge` (9.203.1) — else a scanner-off chat with prior
  decisions stays hidden (the 9.203.0 fix missed this).
  `openSession` re-fires `schedulePIIBadgeUpdate()` after decisions load (the
  reload-button fix). **Click** opens `openPiiHistoryModal()` (`panels_gdpr.js`).
  9.204.0: the modal uses the SAME `.pii-card` structure + General-Settings
  tokens as the pre-send modal (they look identical). Source groups are
  default-COLLAPSED (head shows count + status-mix minichips), with a per-group
  50-row render budget + "+N weitere" (simple virtualisation) for chats with
  hundreds of findings; search/filter force-expand. Each finding has a "Verlauf"
  toggle rendering the shared `_piiRenderHistoryBlock(history)` — the who/what/
  when trail from `decision_history` (resolved display names + timestamps). The
  pre-send modal's SEEN findings get the same toggle (trail lazily fetched via
  `getSessionPiiHistoryDetail`, looked up by `_piiValueHash` = client sha256
  rule|value). `ChatDB.get_session_pii_decision_history` returns the full
  chronological trail per value_hash (collapsing consecutive identical events).
  a large `.modal-content.x-wide` modal. 9.204.6: it loads ONLY
  `GET …/pii-decisions-view` (DB-only, one row per decided value with status +
  trail) — NOT a live re-scan. The prior live-scan + `/v1/gdpr/decisions` merge
  produced phantom "open" duplicates: the live scan's string form for a value
  (e.g. a space-formatted phone number in an assistant reply, or an IBAN with
  surrounding markdown) differed from the stored decision's, so the value_hash
  join missed and the value showed twice (once decided, once "open"). Reading the
  ledger means every row has a status by definition. Trade-off: never-decided PII
  (e.g. newly detectable after a rule fix) does NOT appear here — the modal is a
  decision/audit view; new PII is handled in the pre-send dialog. Groups by
  source, search + status filter + **bulk** mark-FP / accept / reset →
  `POST /v1/gdpr/decisions` (explicit `value_hash`). Anonymise is NOT offered
  here (needs a send-time pseudonym map) — only shown/resettable. 9.204.1: the
  hover popover is retired — the shield opens the modal on CLICK only (native
  `title` tooltip on hover).
- NER: spaCy adds `name|address|organisation`. `name` stays `contact`/ignore;
  `address` → `personal`/warn but ONLY when a person name is adjacent (~120ch);
  `organisation` → `business_id`/ignore. Runtime control: `GET/POST /v1/gdpr/ner-models`.
- **Precision controls (9.93.0)**:
  - **min_occurrences** (`PII_DEFAULT_MIN_OCCURRENCES` / `_pii_min_occurrences` /
    config `gdpr_scanner.min_occurrences`): a rule yields nothing unless ≥N
    DISTINCT matched values appear, counted per WHOLE document (gates the whole
    rule). Default 1; GUI-editable per rule. Applied as a post-pass in `_pii_scan_text`.
  - **Context gates**: `date` is not PII alone — since 9.205.1 it fires ONLY
    near a birth/life-event keyword (`_date_has_birth_context`:
    geboren/Geburtstag/geb. am/born/heirat/…); person-NAME proximity alone is NO
    LONGER enough (a document date next to a signature was a systematic FP). Old
    `dob` rule merged in. `address` fires only with IDENTIFYING specificity: a
    house number ANCHORED immediately after the street name
    (`_ADDR_HOUSE_NO`, "Seestraße 27") or a PLZ — NOT a loose number in the
    window (§/Abs./Nr./Art. reference numbers are excluded via `_ADDR_REF_NUM`),
    and a lone abstract German noun tagged LOC is dropped (`_ADDR_NOUN_SUFFIX`:
    -vorhaben(s)/-ung/-konzept/…).
  - **Local model → no anonymisation, no marks (9.205.2)**: a local model never
    sends data off the machine, so anonymisation is pointless. Server-side
    (`handlers/chat.py`): the sticky auto-anonymise path is suppressed when the
    turn's model `is_model_local` (also when the modal choice is `local_model` →
    swapped to the local fallback); the mapping is NOT rehydrated and the
    `_apply_pii_decisions_to_wire` history pass is skipped → the local model gets
    the REAL, unmodified data (text + history + attachments). An EXPLICIT
    user `gdpr_action` is still honoured. Client-side (`chat_render.js`):
    `_gdprMarksVisible()` gates all three highlight paths on the active model
    being non-local — amber + red marks disappear while a local model is
    selected; `selectModel` re-renders on a locality change so they toggle
    immediately. Prior cloud turns' stored decisions are untouched. And (9.205.4)
    the entire PRE-SEND scan in `chat_send.js` is skipped for a local model
    (`!isModelLocal(chat.model) && chat.model !== 'auto-local'`) — no text scan,
    no attachment scan, no decision modal (a local-model turn was still
    PII-scanning its attachment because the gate only checked
    `piiScannerEnabled`, not the model). **Readiness caveat**: `isModelLocal`
    reads `state.modelsConfig`, EMPTY for a few seconds after a server (re)start
    until `/v1/models/config` is fully up — during which isModelLocal falsely
    returns non-local. `state.modelsConfigReady` (init.js) tracks this;
    `ConnectionMonitor` (monitors.js) re-fetches config every 2s while warming
    (then 10s) and paints the status dot AMBER "wird bereit" → GREEN "verbunden";
    the send path holds with a hint while `modelsConfigReady` is false rather
    than acting on unknown locality.
  - **NER-precision gate (9.193.0, config `gdpr_scanner.name_precision_gate`,
    default ON)**: tightens the three dominant spaCy FP modes. `name` is accepted
    only with person-evidence — an adjacent honorific (Herr/Frau/Dr./Mag./Prof.)
    OR ≥2 capitalised tokens none of which looks like a German common noun
    (noun-suffix) or a known tech word; a lone token is never a name
    (`_passes_name_precision_gate`). `organisation` drops a curated legal/internal
    abbreviation stoplist (ARL/DSG/DSGVO/UWG/…) + KI-/IT-/EU- concept prefixes
    while keeping real product names like SWIFT/ELBA (`_passes_org_precision_gate`).
    `address` requires identifying specificity — a house number or postal code in
    the span's trailing context (`Seestraße 27, 8002 Zürich` keeps; bare
    `Wien`/`Österreich`/`Hamburg` drop) (`_passes_address_precision_gate`).
    Measured on the policy corpus: precision 0.07→0.16, name-precision 0.15→~0.89,
    no real-PII recall loss. PDF line-breaks inside an NER span
    (`Alexander\n\nKlinsky`) are collapsed to one space. NER-only — regex/checksum
    rules untouched. (Full eval: `eval/pii_eval/`.)
  - **Confidence-threshold bands (9.195.0)**: PII enforcement runs off the
    confidence score, not the removed `server_block`. Two global edges
    (`gdpr_scanner.confidence_lower` 0.50 / `confidence_upper` 0.85) split every
    finding into three bands: `<lower` → **ignore** (silent); `lower..upper` →
    **ask** (user picks ignore/anonymise/local); `≥upper` → act on the rule's
    configured action (`block`→anonymise/fallback, `warn`→ask, `ignore`→nothing).
    Single seam `_pii_resolve_disposition(finding,cfg)` → ignore|ask|anonymise;
    `_pii_band`, `_pii_worst_disposition` (replaces worst-ACTION in
    `gdpr_pick_model_for_background`). Per-rule/category action governs ONLY the
    high band. **min_occurrences NO LONGER GATES** (everywhere) — count feeds the
    score via per-rule count-points `_pii_count_points` (c_lo→SCORE_LO,
    c_hi→SCORE_HI; seeded from min_occurrences, config `count_points`
    overridable; e.g. email (3,7): 1-2×→ignore, 3-6×→ask, ≥7×→act). Background
    (no user): `background_ask_action` resolves the mid band. `server_block`
    removed; audio-gate now `block_unscannable_on_cloud`. Classification keeps
    its own `server_block` + strict-always-block (§1.11) — untouched.
    `/v1/gdpr/scan-text` returns `band`+`disposition` per finding + `worst_disposition`.
  - **Confidence score (9.194.0)**: every PII finding carries `confidence`
    (0..1) and every `detect_classification` result carries `confidence` — an
    evidence-based, deterministic score (NOT a calibrated P(correct)) for a
    future threshold ladder (ignore<ask<anonymise/fallback; thresholds NOT yet
    wired). PII (`_pii_confidence`): a rule-class prior (checksum 0.98 / secret
    0.95 / email 0.92 / context-anchored 0.82 / gated-NER 0.72 / bare 0.45)
    moved by two dynamic signals — the per-file **occurrence count** (more
    distinct hits → higher, +≤0.15) and the **context distance** (NER date/
    address: closer person/birth anchor → higher, ±0.15; gates now return the
    gap via `_name_distance`/`_birth_context_distance`). Checksum/secret/email
    are rigid (distance ignored, corroborate upward only; secrets floored 0.90).
    Classification (`_classification_confidence`): per-page marker high/med/low
    × coverage → 0.65–0.95; filename-only 0.45; heuristic-only 0.40; marker/
    content mismatch −≤0.25. PII `confidence` is in the full-mode scan endpoint.
  - **`business_id`** category (default ignore) — company IDs (`br_cnpj`,
    `tax_id_ctx`, `organisation`) are not personal data.
  - `dk_cpr` keyword-anchored; `generic_secret_assignment` entropy bar = len≥24
    AND ≥10 distinct chars.
- Rule order matters — context-gated rules first, `credit_card`/`phone` after
  national IDs (do NOT reorder `_pii_rules`).
- Single decision point for non-interactive calls:
  `gdpr_pick_model_for_background(model, texts, purpose)` → scan → audit →
  anonymise / swap to local / **skip** (succeed empty, `GDPRSkipError`) / abort,
  per `gdpr_scanner.background_pii_action`. KG mining makes a whole-document
  decision in `_process_source` (full-doc scan → correct per-doc min_occurrences).
- `is_model_local()` bypasses the block entirely (data stays on-prem).
- Client interlock: `piiBlockActive(chat)` filters dropdown to local-only
  when scanner enabled + server_block + chat has PII.

## MemPalace integration

Imported as a Python package — no MCP, no subprocess.

- **Wing scheme** (ID-only): `user__<uid>`, `team__<tid>`,
  `project__<pid>`, bare names = shared.
- **Vector backend = Qdrant** (pluggable via `MEMPALACE_BACKEND` env; native
  service on `localhost:6333`, WAL-backed transactional ANN, scalar int8
  quantization for 4× RAM with rescore-preserved recall). Embeddings are computed
  Brain-side via MLX (`embeddinggemma-300m`) — Qdrant needs no GPU. Quant + on_disk
  knobs are a `# BRAIN-PATCH` on the vendored `backends/qdrant.py` (gitignored
  venv — re-apply after any mempalace upgrade).
- **SINGLE SHARED COLLECTION**: all wings share ONE collection
  `mempalace_drawers` (+ `mempalace_closets`), filtered by a `wing` metadata field;
  `mempalace_query`/`_query_wings` does one query over it with a wing filter.
  (Per-wing collections were tried in v9.62.0 and REVERTED 2026-06-03 — they added
  dead complexity and did not fix the corruption below.)
- **The recurring "HNSW corruption on restart" — HISTORICAL (ChromaDB-only; gone on Qdrant).**
  Superseded by the Qdrant migration: the embedded-Chroma in-process HNSW segment
  files that raced the writer daemons no longer exist, so this whole failure mode is
  structurally impossible now. Kept here because it explains *why* the backend moved.
  Original ROOT CAUSE (mitigated v9.70.0 on Chroma, before the move):
  Symptom: after a restart a query raises `InternalError: Error finding id` and a
  broad query returns only a fraction of the drawers. It was NOT chromadb failing
  to persist, NOT per-wing, NOT embeddings. The bug is in the vendored MemPalace
  `backends/chroma.py` `quarantine_stale_hnsw()`: chromadb 1.5.7 writes
  `index_metadata.pickle` with `dimensionality=None` even for a COMPLETE segment
  (the real dim is in `header.bin`), and the validator wrongly treated
  "labels present + dimensionality None" as corruption → it quarantined the good
  segment (renamed it `.corrupt-…`) on every open, leaving an empty replacement →
  "Error finding id" → rebuild loop. It was dormant for weeks because at the old
  `hnsw:batch_size=50000` the big collection rarely flushed a pickle, so the
  validator never ran; the per-wing-era `batch_size→100` change made it flush a
  pickle every compaction, exposing the bug. FIX = a `# BRAIN-PATCH` in that file
  so a populated segment with `dimensionality=None` is NOT quarantined (only a
  PRESENT-but-invalid dim is). This patch lives in the gitignored venv — re-apply
  after any `pip install --upgrade mempalace`. Runtime recovery for a genuinely
  wedged segment stays with `_try_rebuild_palace` (fires on the real corruption
  signal; a full rebuild is ~366s/13k drawers so it is NOT used at boot).
- `_resolve_session_wing` priority: project → team → user → empty.
- `mempalace_query` in a project chat is **force-scoped** to
  `project__<id>` and refuses if id is missing (never leaks).
- **Per-drawer snippet rule** (universal — every caller, no use-case
  branching, structural): a `mempalace_query` drawer whose content lives in a
  readable file on disk (project docs, brain_code, AND artifacts — the
  synthetic `session/<sid>#artifact/<name>` marker is resolved to
  `agents/<agent>/artifacts/<date>_<sid>/<name>`) has its `text` OMITTED and
  `content_via:"read_document"` — the model MUST call `read_document`, so it
  can't answer from a partial snippet (the documented hallucination cause).
  Drawers with NO file behind them (chat turns `#turn/`, summaries `#summary`,
  user-profile sections `#profile/`) keep their `text` in FULL (no truncation —
  the drawer IS the only copy) and carry `content_via:"snippet"`. The
  `read_hint` explains this and, when >1 readable doc, lists them so the model
  reads each before summarising. Replaces the old always-`[:2000]`-snippet
  behavior.
- `include_chat_history=true` searches the chat wing `project_chat__<id>`
  **plus** the knowledge wing `project__<id>` (`wing $in [...]`), never the
  chat wing alone. The project chat wing is often empty (chat-sync may not
  have run), and searching it alone returns 0 hits → the model falls back to
  free web access (the v9.31.x curl symptom). Including the knowledge wing
  guarantees the curated source documents are always reachable. Both wings
  belong to the same project, so the C3 visibility gate is not weakened.

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

**Method/profile-change invalidation (9.184.0)**: the per-drawer cursor
self-invalidates ONLY on source-file change — NOT on a KG method/profile
change. Both edit paths now purge the cursor explicitly so a switch actually
re-extracts: the GLOBAL `POST /v1/mempalace/kg/config` (KG_FIELDS in
`_handle_kg_config_save`) and the PER-PROJECT override
(`ProjectManager.update_project` compares old vs new `kg_method`/`kg_profile`;
on change → `kg_purge_for_scope` + `closet_regen_purge_for_scope` for the
project wing, and the PUT handler kicks `_project_sync_request`). Before
9.184.0 the per-project path had no invalidation — a `rules→llm` flip kept the
stale rules-era triples (every chunk already marked done → LLM skipped all).

**GDPR policy + KG (9.92.0)**: KG extraction obeys `gdpr_scanner.background_pii_action`
like every other non-interactive caller — it does NOT hardwire its own behaviour
(the v9.91.0 hardwired pre-check was removed). The policy has **four** values:
`anonymise` (default — pseudonymise→send→de-anonymise; destructive on PII-dense
docs, the 2026-06 incident), `swap_to_local` (extract on the local fallback,
full text stays on host), `skip` (don't extract — succeed empty), `abort` (raise
+ refuse). `skip` raises `brain.GDPRSkipError` (a `GDPRBlockedError` subclass, so
the ~20 existing `except GDPRBlockedError:` sites soft-return for free); KG marks
the whole document done with `kg_skipped: gdpr_skip` (cursor advances → no
retry-loop), counted in `RunResult.gdpr_skipped`, NOT an error. The 3 sites that
map a block to an error status (background_tasks, scheduler, KG) add a narrow
`except GDPRSkipError` → complete-empty instead of error.
Per-file KG state (`kg_extract.kg_source_states_for_wing` → `kg|skipped|empty`)
is exposed on the project `/folder-tree` endpoint and rendered as a colour-coded
KG badge per file in the source tree (green KG / amber KG⊘ skipped / grey KG·
mined-no-triples). Dormant while GDPR is disabled.

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

## Goal-Modus (post-turn judge loop, v9.256.0)

A per-session (or per-scheduled-task) GOAL the server judges every turn
against, auto-continuing until fulfilled — like Claude Code's `/goal`.

- **Judge**: `engine/goal_judge.py::judge()` — one `background_call` with
  `forced_tool=goal_verdict` → validated dict `{fulfilled, impossible,
  reasoning, continue_instruction}` (no JSON parsing). Model:
  `config.goal_judge_model` → server default. GDPR-gated
  (`gdpr_pick_model_for_background`, purpose `goal_judge`); cost rows tagged
  `cost_purpose=goal_judge`. Input is CAPPED (goal + last user msg ~2k +
  reply tail ~12k) — never the full transcript. `impossible=true` (legitimate
  refusal / objectively unreachable) ends the loop WITHOUT forcing a
  continuation — the citation-re-round lesson (never bully a correct refusal
  into hallucination). **Judge errors are always terminal** (no retry) — an
  unreliable judge must not be able to spin the loop.
- **Chat loop** (`handlers/chat.py` worker): the turn body
  (wire-build → `run_turn` → persist) runs in a `while True`; after each
  persisted assistant message the judge runs; a continue verdict persists the
  `continue_instruction` as a VISIBLE user message
  (`metadata.goal_continue/goal_iteration/goal_reasoning`) and re-enters the
  loop. Exactly ONE terminal `done` (carries `goal {status,iteration,max,
  reasoning}`); boundaries are SSE `goal_judge_start` / `goal_verdict` /
  `goal_continue`. The web client mirrors these into `chat.turnActivity`
  (chat_turncontrol.js) and renders them as cards in the right panel's
  Aktivität tab (planned/running judge, verdict, extra iterations) next to
  the tool calls; injections render there too. Invariants: `_msg_count_before` is re-snapshotted before
  each continue message (cancel/error in iteration N rolls back ONLY N);
  per-iteration callback state (`_partial_*`, created_files,
  `_turn_created_files`, streaming_text) is cleared at the boundary; the
  Websuche fetch from iteration 1 is CACHED and re-injected (no re-fetch per
  pass); the PII wire-rewrite runs every pass (audit event only on pass 1);
  the aggregate-cost fallback logs token DELTAS (never cumulative). Turn
  error / empty reply / cancel → break without judging. Deep-Research turns
  are exempt. `AskUserQuestion` inside an iteration blocks as usual — the
  loop simply waits.
- **Caps**: `gmax = session.goal_max_iterations or
  composer_defaults.goal_max_iterations or 5`, hard ceiling
  `GOAL_ITER_HARD_CAP=10`. Kill switch:
  `composer_defaults.goal_mode_enabled=false` disables button AND loop.
- **Lifecycle**: manage action `goal` arms (`goal_status='active'`); while
  active EVERY send loops; `fulfilled` auto-ends judging (badge ✓ until the
  goal is cleared or re-set — re-setting re-arms); `capped` = impossible or
  budget exhausted; `judge_error`/cancel leave the goal armed.
- **Scheduler variant**: `_execute_scheduled` wraps `run_turn` in
  `for _gi in 1..gmax` when `schedules.goal` is set. The judge sees the RAW
  (still-pseudonymised) reply; continuation appends assistant+user messages
  in the same token space; only the FINAL `result_text` is de-anonymised.
  Guards: stop on turn error, `cancel_token`, or <30s of timeout budget
  left. German result suffix (`Ziel: erreicht nach N Iteration(en)` /
  `nicht erreicht (Limit N)` / `Ziel-Prüfung fehlgeschlagen`);
  `schedule_history.goal_iterations` records the count.

## Scheduler

- `engine._scheduler` is a singleton. APScheduler-style cron + `@every`.
- Each run = immutable `schedule_history` row (id = run_id).
- Synthetic `session_id = sched-<run_id>` scopes artifacts + traces.
- **Cost + tool/LLM spans** are logged after the run's `run_turn` returns
  (keyed by `sched-<run_id>` + the turn's trace_id): token cost into
  `cost_log` via `_log_call_cost`, and one `tool_call` span per executed
  tool + one `llm_call` span carrying token in/out into `traces.db` via
  `_trace_manager`. Without this the run-detail inspector showed only a tool
  COUNT — no token in/out, no cost, no per-tool list (regression since the
  v9.0.0 SDK migration dropped the native loop that used to log these). Each
  tool span's `result_summary` is filled from `tool_events[].result_text` —
  a copy of each tool's output the loop carries for non-streaming
  consumers — so the run-detail inspector shows what each tool returned, not
  just its name. The span stores BOTH a 500-char `result_summary` (inline
  preview) and `full_result` (the complete output the model received, capped
  100k); the inspector expands the full result like the chat view, so a
  multi-hit mempalace_query no longer looks truncated to the first hit.
- Per-task `attachments` are referenced in place; never per-run copies.
  `_purge_attachment_paths()` refuses paths without the
  `scheduled_attachments` segment.
- `working_dir` overrides system prompt cwd line. `python_exec` stays
  pinned to artifact folder by design.
- `tool_profile` drives the call's purpose: `""` → `research_minimal`,
  `"interactive"` → `interactive`. Per-task `thinking_level` empty →
  inherit at fire time. `caveman_chat` is per-task. `caveman_system` is
  NOT exposed per task (would invalidate warmup KV prefix).
- **Caveman is OUTPUT-only + wire-injected (v9.121.0)**: it touches NOTHING in
  the system prompt or tool descriptions. The terse-response-style instruction
  (`CAVEMAN_CHAT_PROMPTS`) is appended as a trailing WIRE-ONLY suffix on the last
  user message at turn time (`handlers/chat.py` `_append_to_wire_user`;
  `engine/scheduler.py` on `task_message`) — never persisted, so the warm-pool KV
  prefix stays byte-stable and nothing caveman enters history. Level: `caveman_chat`
  (per-session 🪨 toggle) else `caveman_system` (per-model default). Input-side
  compression happens ONLY during refinement (`/v1/refine` rule-compresses the
  refined query text). Warmup needs no caveman handling.
- **Shared domain logic (no parallel impl)**: a scheduled task runs the SAME
  domain logic as a chat in its domain. Two shared functions on `brain` do it:
  `apply_domain_context(agent_id, project|project_id, user_id,
  research_mode_override, base_exclude_tools)` sets project scope (+resolves
  project_id→name), team_ids, research_mode_override and the
  `disable_web_search` web-tool lockout on the request context. When that
  lockout is on, `_build_system_prompt` also appends a **CLOSED CORPUS** notice
  (the agent answers ONLY from curated/inspected project sources — each web
  source is a single saved page, not a crawl — and must say so rather than imply
  broader web analysis; KV-safe, gated on the per-project flag already in the
  prompt cache key). Project `web_urls` mining is deliberately single-page (NOT a
  link-following crawler): depth for a locked project comes from running Deep
  Research BEFORE locking (search relevance + the inspect→approve gate), keeping
  the corpus known/inspected.
  `build_tool_context(session_id, agent_id, user_id, gdpr_mapping_id)`
  snapshots the context into the tool_context dict for `run_turn`. Both the
  chat worker (`handlers/chat.py`) and the scheduler fire-path
  (`engine/scheduler.py`) call them — so the two never drift. The scheduler
  has NO own project/team/web-lockout logic anymore. (What stays per-path:
  message history vs single task message, system-prompt build, GDPR-anonymise
  timing — structurally different, no domain logic in them.)
- **Same message framing as a chat**: the fire-path prepends the same
  artifact-folder preamble (`_artifact_folder_preamble_text`) to the task's
  user message that `handlers/chat.py` adds to a chat's first message.
  Without it the task framed the question differently than an equivalent
  chat, and at temperature 0.2 (near-deterministic) that made the model build
  a slightly different mempalace_query — shifting source weighting on a wing
  with uneven per-source drawer counts. Same framing → same query → same
  result as the chat.
- **No chat history on scheduled runs**: a `sched-<run_id>` session is fresh
  and isolated, so the project chat wing (`project_chat__`) is always empty.
  `mempalace_query` force-ignores `include_chat_history` on scheduled runs
  (always hits the project KNOWLEDGE wing `project__`). Without this, a model
  that set `include_chat_history=true` searched the empty chat wing → 0 hits
  → free web fallback (curl via execute_command) — the v9.31.x webnews
  symptom.
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
  the per-tool-call context (`tool_context["project"]`) so context
  rebuilds stay scoped. Empty (or a now-deleted project) → agent-global,
  unchanged. Artifacts stay in the agent-global `sched-<run_id>` folder
  (no project-tagging); the project view's "Geplante Aufgaben" tab just
  filters the schedule list by `project_id`.

## Citation discipline (dynamic, classifier-driven)

The research-mode discipline (REFUSAL + PRECISION + per-claim CITATION,
`render_research_mode_disciplines()` — always all three together) + the citation
validator. **v9.272.0 — two-lane semantics + fabrication strip** (fix for the
two dominant failure modes the 2026-07-03 production eval exposed: refusing
general-knowledge questions "for lack of sources", and decorating answers with
invented `[Quelle: …]` brackets):
- The REFUSAL text now has TWO LANES: (a) source-bound claims (documents,
  policies, compliance, live data) stay strictly evidence-only; (b) GENERAL
  KNOWLEDGE (textbook concepts, standards, estimates from stated assumptions)
  is answered normally WITHOUT brackets + a one-line "beruht auf allgemeinem
  Fachwissen" note — refusing a task because retrieval came up empty is
  explicitly forbidden when lane (b) can answer. CITATION starts with an
  anti-fabrication hardline (brackets ONLY for actually-retrieved content;
  an invented file/quote/statistic is worse than an uncited sentence).
  Defaults in brain.py; the byte-identical copies saved in
  `config.json → research_mode_disciplines` were lifted in the same change.
- **Deterministic fabrication strip** (`brain.strip_fabricated_citations`,
  called in the worker's validator block): when a turn provably retrieved
  NOTHING (no retrieval tool call, no curated web sources, zero verified
  quotes) but the reply carries brackets, the brackets are string-stripped
  (claims stay), an honest reload-stable note is appended, and
  `metadata.citation_validation.fabricated_stripped=N` records it. No LLM, no
  re-round — structurally immune to the v8.40.0 re-round failure (which
  REWROTE refusals into fake citations). One verified quote or any retrieval
  signal → no strip (the existing warning path handles partial grounding).

TWO mutually-exclusive modes, chosen by the auto-route classifier mode
(`brain.classifier_is_llm()` = mode in {llm, hybrid}):

- **LLM / hybrid mode → DYNAMIC (effective-tools-driven).** The trigger is the
  turn's RESOLVED active tool set, NOT the classifier's intent:
  `brain.turn_has_retrieval_tools(active_tool_names)` is true when the live tools
  include any of `_RETRIEVAL_TOOLS = {mempalace_query, searxng_search, exa_search,
  web_fetch, read_document, read_file}`. (Keying off the classifier guess would be
  wrong — the classifier only REDUCES tools via deferral, so it could suppress
  discipline on a turn that does retrieve.) When a retrieval tool is live, the chat
  worker injects the discipline as a WIRE-ONLY preamble
  (`_inject_web_preamble_into_wire`) — NOT the system prompt, so the warm-pool KV
  prefix stays byte-stable (works for warm/local too). Applies to ANY chat, project
  or not. In this mode the per-project `research_mode` flag + the composer 🔬
  override are DISABLED (`prompt_build` forces `_research_mode=False`; the UI
  disables the checkbox + hides the button).
- **Keyword mode (default) → MANUAL only.** No dynamic trigger. The per-project
  `research_mode` flag / per-session override is the ONLY control and renders the
  discipline in the SYSTEM PROMPT (`engine/prompt_build.py`), as before.
- **Validator gate**: `session._citation_discipline_active` (set per turn in both
  modes). `validate_citations_in_response` verifies quotes against the session's
  sources + appends a fidelity warning past the threshold (>30% uncited OR ≥2
  unverified) — in any chat. **Verifiable sources = files read via read_document
  THIS turn ∪ the on-disk companions of `mempalace_query` drawers this turn**
  (v9.145.0 — a memory-grounded answer with no read_document is now verifiable;
  before, it flagged everything "source not in session reads"). Matching is
  **quote-first**: the named source is tried, but if the label doesn't resolve
  (the model's "Jahresfinanzbericht 2025" won't match a slugified `www-…-20_….md`)
  the verbatim quote is searched across ALL session sources — found = verified.
  Only truly-absent quotes (e.g. `…`-stitched table values) stay flagged. Web
  quotes verify as unverified (not file-backed) — chips link those out instead.
- **Per-claim scope = bullets + TABLE rows + multi-fact PROSE** (v9.144.0). The
  CITATION DISCIPLINE requires a citation on every factual unit, not one per
  block: each bullet, each table DATA row (a final "Quelle" column; `s.o.`/`siehe
  oben` for rows sharing a passage's quote; derived/Δ columns noted once), and
  each prose/FAZIT sentence that packs several figures (one bracket per fact). The
  validator counts all three — table rows (header row above `|---|` + label rows
  skipped) and ≥2-number prose lines feed `uncited_claims`/`claim_total`.

## Tool resolution (3-layer)

A tool has ONE canonical status, `state ∈ {active, inactive, deferred}` —
not two independent booleans. `enabled`/`deferred` are DERIVED from it
(`_tool_state_to_flags`): active→(on, not-deferred) · inactive→(off) ·
deferred→(on, deferred). The impossible `enabled:false + deferred:true`
combination is unrepresentable.

```
state = global.state                       # tool_settings.<name>.state, default 'active'
if agent_id:
    o = token_config.tool_overrides.<name>
    if o has a status key (state | legacy enabled/deferred):
        state = collapse(o)                # override REPLACES global state
enabled  = state != 'inactive'
deferred = state == 'deferred'
if not enabled: drop
if call.purpose not in global.purposes (when set): drop
if deferred and tool not in discovered_tools: drop (surface via tool_search)
```

(`resolve_tool_state` is the seam; `_global_tool_enabled` / `resolve_tool_*`
derive from it.) On disk, records carry only `state` — the legacy
`{enabled, deferred}` pair was forward-migrated away at boot
(`migrate_tool_settings_to_state` + `migrate_agent_tool_overrides_to_state`,
idempotent; both read the old booleans as a fallback so an un-migrated record
still resolves correctly). UI:
- **General Settings → Tools** (global): one **Status** dropdown —
  Aktiv · Inaktiv · Aufgeschoben.
- **Agent Settings → Tokens** (per-agent override): the same 3 + **Standard
  (erben)** = no `tool_overrides` entry → inherits global. A legacy partial
  override is shown as the state it resolves to live and saved as `{state}`.

`tool_search` tracks discovered tools on `RequestContext._discovered_tools`.
That field defaults to None and is only initialized by the chat worker — so
non-interactive callers (scheduler, background) had it None, and `tool_search`
crashed with `'NoneType' has no attribute 'add'` whenever it returned hits on
a scheduled run. `_tool_search` now initializes the set defensively, so the
tool works in every execution context.

Purposes: `interactive | transform | memory_summary | research_minimal |
helpdesk`. Purpose is a property of the call, not the agent — agents
cannot override it. Since 9.22.0 the resolved tool list is also enforced
at dispatch (`tool_context['allowed_tools']`), not just at list-build.

## Cost & quotas

- `CostTracker` logs every LLM call to `costs.db` (`cost_log`), one row per round.
- **Use-case tagging + COMPLETE coverage** (v9.89.0 + v9.90.0): EVERY LLM call
  writes one `cost_log` row, including $0 local/free calls and zero-usage calls —
  so the breakdown is a full audit (a $0 row that should cost = rate-config gap; a
  missing row = logging gap). The central seam is `sidecar_proxy.background_call`
  (logs once per call via `account_cost=True`; `False` only for non-billable
  benchmarking). Direct `run_turn` paths log themselves: chat (per-round),
  scheduler, `helpdesk_call`, the citation re-round. `_log_call_cost` no longer
  skips `tokens==0`. **`cost_purpose` is SEPARATE from `purpose`**: `purpose` stays
  one of the 5 `_VALID_PURPOSES` (drives `resolve_active_tools`); the cost tag is
  `background_call(cost_purpose=…)` → context `cost_purpose` → `purpose`. ~27 call
  sites now covered (chat, chat_summary, next_prompt, auto_route_classify,
  scheduled, background_task, delegate_task, studio, deep_research, audio_overview,
  read_aloud, translate_*, lang_detect, helpdesk, soul_chat, refine, ask_llm,
  kg_extract, code_graph_summary, lcm_*, memory_*, relationship_discovery,
  user_profile, citation_reround, ocr). Sites needing usage numbers for display
  call `account_background_usage(…, log=False)` (compute-only — no double row).
  `GET /v1/costs/breakdown?window=…` groups by `(purpose, model)` → display buckets;
  cycle/last_cycle reuse `QuotaManager.cycle_window`. No backfill — pre-v9.89.0 rows
  bucket as *Unbekannt (Altdaten)*.
- `QuotaManager` (30s cache). Two axes per user: Daily (rolling UTC) +
  Cycle (`monthly|weekly|yearly` w/ anchor). Worst-axis wins.
- Pre-flight gate in `send_message` round 0, AFTER GDPR.
- `is_model_local()` always bypasses (cost = 0).
- `enforce_red`: `warn_only` (default), `force_local` (silent swap to
  `default_local_fallback_model`), `hard_block` (raises).

## User profile daemon

`user-profile` polls every 30 min. Per-user gate:
`daily_summary_enabled` + local hour matches + 23h cooldown.

Worker: 100 most-recently-active chats from last 90 days →
`sidecar_proxy.background_call` with `_PROFILE_SYSTEM_PROMPT`. Atomic write via tmp +
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

**Two modes, decided PER-MODEL** via `config.json → models.<id>.auto_lcm`
(default ON; checkbox in General Settings → Service-Modelle; read via
`resolve_model_settings(model).get("auto_lcm", True)`).

- **Auto-LCM (default)** — when the FINAL resolved model (after auto-route +
  GDPR fallback; the chat worker reads `session.model`/`max_context` AFTER the
  in-worker GDPR swap) has `auto_lcm` on, `ContextManager.auto_balance(session,
  max_context, emit=)` runs BEFORE every turn. It computes fill from the
  UNCOMPRESSED history (`load_messages(include_compacted=True)` minus
  `lcm_inserted` rows) ÷ `max_context`: over `compact_threshold` →
  `check_and_compact(force=True)` (reuses prior summary blocks, keeps a fresh
  tail); comfortably under + old summaries exist → BIDIRECTIONALLY expand
  (restore originals). Persisted via the SHARED `lcm_persist_compaction` /
  `lcm_restore_originals` helpers (`compacted=1` originals + `lcm_inserted`
  tags) — the same path the manual button uses. Manual compaction is BLOCKED
  (the endpoint returns 409 `auto_lcm_active`; status-bar button disabled;
  warning banner suppressed). If still over threshold after max compaction →
  SSE `auto_lcm_over_threshold` → decision modal (retry / new chat / new chat
  with handover). The compaction LEVEL is recorded on the turn's
  `metadata.lcm_state` {before/after tokens+pct, saved_pct, turns_compressed/
  total, still_over} → status-line badge + the in-chat compacted block header;
  in auto mode the compacted block renders thinking-style (lighter/italic,
  `.lcm-auto`, no restore button).
- **Manual (auto_lcm off)** — chat worker does NOT auto-fire. The status-bar
  button calls `triggerLCM()` → `POST /v1/context/compact` with `force=true`;
  the warning banner shows at ≥60% usage.

**Handover** — available in ANY chat (composer button + the over-threshold
modal). `POST /v1/chat/handover {session_id}` → the chat's resolved model
writes a structured handover doc; the endpoint returns it PLUS a second doc
with the full verbatim source transcript (so the new chat works from the
summary and opens the history only when it needs detail). The summary is ALSO
saved server-side as an artifact (`Übergabe-<ts>.md`, role=output) in the
SOURCE session's artifact folder — `_generate_handover_document` pins
`current_session_id` to the source session before calling
`_save_handover_artifact` → `_register_artifact_version`; `artifact_saved` in
the response is the filename. The client shows a progress→preview modal
(`_showHandoverModal` in `chat_send.js`): an indeterminate progress bar while
the doc generates, then the rendered summary MD with Übernehmen/Abbrechen.
Only on approval does it open a new chat, attach BOTH .md files, and seed a
"continue where we left off" prompt. On cancel nothing else happens — the
artifact is already saved in the source chat. No zip bundle is attached (the
model can't read inside a zip; the zip-bundle export is a separate feature).

## Tools — adding a new one

4 edit sites in `brain.py`:
1. `TOOL_DEFINITIONS` (~line 540+)
2. `TOOL_GROUPS` (~line 1771)
3. The `tool_*` function
4. `TOOL_DISPATCH` (~line 22580)

Per-tool prose (description/when_to_use/warnings/examples/applies_with)
is added via admin UI → `POST /v1/tools/settings`, NOT in code.

## Chat auto-archive + auto-delete (`chat-cleanup` daemon)

`server_daemons._chat_cleanup_loop` (registered in `server.py`, thread name
`chat-cleanup`) runs two independent, config-gated stages each cycle (interval
`run_interval_seconds`, min 300s). Config is read LIVE from
`config.json → chat_cleanup` via `engine._server_config()`, so GUI edits apply
without a restart. The whole feature is OFF unless `enabled:true` (default off,
opt-in). Either day-count `=0` disables that stage.

- **Archive** (`archive_after_days`): `ChatDB.list_auto_archivable(cutoff)`
  returns ids that are `status='active'`, idle (`last_active < cutoff`), have
  ≥1 message, are **purely private** (`visibility='user'`, no `team_id`/
  `extra_member_user_ids`), **not memorized** (`save_to_memory=0` AND no
  `wiki_pages.source_ref='session/<id>'`), and **not referenced** (no
  `favourites` row of type chat/project_chat, no unfinished `background_tasks`,
  no `active_turns`, no `workflow_run_id`, no `streaming_text`). All exclusions
  are `NOT EXISTS` subqueries in one SQL (everything lives in `chats.db`).
  `archive_session` flips status + stamps `archived_at`. Conservative by design.
- **Delete** (`delete_after_days`): `list_auto_deletable(cutoff)` =
  `status='archived' AND archived_at < cutoff`. The daemon calls
  `srv.sessions.delete(sid)` → `ChatDB.delete_session`, which now ALSO removes
  the chat's wiki page + its MemPalace drawer via
  `wiki_store.delete_page_for_session` (access-gate-free, daemon-internal). The
  delete clock uses the exact `archived_at` column (N days after archiving,
  independent of the archive window). Rows archived before this column existed
  have `archived_at=NULL` → never auto-delete until re-archived.
- **Access semantics**: `last_active` was previously persisted only on
  message-send. `SessionManager.get()` now also persists it on chat OPEN
  (throttled ~5 min via `ChatDB.touch_last_active`), but ONLY for active chats
  — opening an archived chat does NOT revive it or reset its delete clock (the
  UPDATE is `status='active'`-guarded). To keep an archived chat, un-archive it.

## Common pitfalls

- Daemon stdout/stderr → `server.error.log`, not `server.log`.
- Restart ONLY via graceful SIGTERM (`launchctl kill SIGTERM …`), never a
  hard kill — SIGKILL corrupts in-flight MemPalace writes.
- After a restart the HTTP listener needs >6s to bind.
- `MODEL_PROFILES` overlays must only carry request-style knobs (never
  warmup/GPU/etc. — would silently re-enable user-toggled-off fields).
- Sessions are created lazily on first send. SQL hides 0-message
  sessions older than 60s. Startup purge deletes >5min empty sessions.
- Archive ≠ delete: archived sessions keep their drawers (but the
  `chat-cleanup` daemon auto-deletes them after `delete_after_days`, and
  deleting a chat now also drops its wiki — see "Chat auto-archive").
- Schedule deletes are tombstoned in `config.deleted_models` — only
  "Full Resync" clears them.
