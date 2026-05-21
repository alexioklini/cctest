# engine/ — Core Agentic Engine

Non-obvious invariants for this package. Root CLAUDE.md has the full architecture picture.

**Runtime source of truth — IMPORTANT.** `brain.py` is the live monolith — everything that handlers/server.py reach via `import brain as engine` runs from `brain.py`'s namespace. The agentic loop, system-prompt builder, tool dispatch, provider routing, and all middleware live in `brain.py`. The `engine/` subtree is a **partial extraction** — only the modules below are actually imported at runtime.

**Live engine/ modules (imported externally):**

| File | Owns | Importers |
|------|------|-----------|
| `tools/image_gen.py` | Image generation via Mistral Conversations API (only live tool extraction) | `brain.py` TOOL_DISPATCH (line ~21845) |
| `kg_extract.py` | LLM-driven triple extraction over project input folders | `handlers/admin.py`, `handlers/projects.py`, `server.py` |
| `doc_convert.py` | the single document-extraction pipeline (markitdown→fallback) for ALL reads + companion `.md` conversion — see "Document Extraction Pipeline" below | `server.py`, `handlers/classification.py`, brain.py via lazy import |
| `sync_log.py` | Sync run log table + helpers | `handlers/projects.py`, `server.py` |

Anything else under `engine/` is dead. brain.py owns the live equivalent.

**Deleted in 8.29.0**: `loop.py` + `constants.py` (~4700 LOC). **8.30.0**: 8 internal modules + `analytics/` package (~9918 LOC). **8.32.0**: rest of `tools/` (`files.py`, `email.py`, `git.py`, `code_graph.py`, `web.py`) and entire `memory/` subpackage (`store.py`, `autodream.py`, `mempalace.py`) — ~9700 LOC. brain.py was already the live source for every symbol: all 11 file/shell/python tools, all 5 gmail tools, both git tools, all 4 code_graph tools, web_fetch (with `_html_to_markdown` that the dead `engine/tools/web.py` was missing), `MemoryStore` class, `_autodream_*` helpers. MemPalace functionality uses the **`mempalace` pip package directly** (`mempalace.searcher`, `mempalace.palace`, `mempalace.knowledge_graph`, `mempalace.closet_llm`) — never `engine.memory.mempalace`. Also dropped: 3 broken `tests/test_worker_phase*.py` files (imported `from engine import execution` — `execution.py` lives at repo root, so they failed at import), entire `backup/` directory (38K LOC of historical snapshots, audit-trail-only).

**Rule going forward**: new tool implementations go in **brain.py only**. Adding a tool means editing 4 sites in brain.py: `TOOL_DEFINITIONS` (~line 421), `TOOL_GROUPS` (~line 1635), the `tool_*` function definition, `TOOL_DISPATCH` (~line 21862). No engine/tools/ directory entries unless the tool is genuinely large enough to warrant its own module (only `image_gen.py` qualifies today).

The invariants below describe brain.py's runtime behavior.

## Agentic Loop (sidecar)

The loop runs in `sidecar/sidecar.py` (separate venv, anthropic 0.101.0).
Brain hands the sidecar an Anthropic-shape payload via
`handlers/sidecar_proxy.run_turn(...)` (interactive) or `background_call(...)`
(scheduler / non-interactive); the sidecar drives rounds, tool calls, and
text deltas back over SSE.

- **Tool dispatch path**: sidecar emits a `tool_use` block → POSTs
  `/v1/tools/call` to Brain → `server_lib/tool_mcp.handle_tools_call`
  rebuilds thread-locals from the sidecar's `context` payload, dispatches
  to `engine.TOOL_DISPATCH` (or MCP fallback), captures the result via
  `sidecar_proxy.capture_tool_result(...)`, returns. Synchronous by
  design — `tool_dispatch_done` SSE event from the sidecar is emitted
  only after dispatch returns.
- **Tool exec pipeline** (per Brain dispatch): built-in pre → external
  pre → execute → built-in post → external post → `_after_file_write`.
- **`AskUserQuestion`** blocks via `_pending_answers[session_id]` +
  `Event`; unblocked by `POST /v1/chat/answer`.
- **Cancel**: Brain mints `turn_id`, passes via `X-Turn-Id`; the proxy's
  `_watch_cancel` thread polls `session.cancel_token` and POSTs
  `/cancel/<turn_id>` to the sidecar.
- **Tool-call dedup** (Brain side): session-scoped (1h TTL, 100 entries).
  1 dup = error, 2 dups = `TaskCancelled`. `reset_tool_dedup()` runs at
  turn start. Exempt: `memory_recall`, `memory_shared`, `delegate_task`,
  `task_status`, `schedule_list`, `schedule_history`. (`read_document` /
  `read_file` are NOT exempt — within-turn double-reads of the same file
  hit dedup, which is the desired guard against tool-loops; the prior
  per-session cache that exempted them was removed in v9.7.0.)
- **Three-layer hooks**: tool pre/post (external subprocess),
  `after_file_write` (centralized). External hook: timeout 5s, fail-open
  on crash, exit 1=block, exit 2=skip chain. `allowed_tools` restriction
  in workflows IS enforced — don't let it regress.

The native-loop machinery (middleware pipeline between rounds, guided
execution, variance kill-switches, worker-subagent envelopes,
`_summarise_tool_result`, `_handle_openai_response`, `send_message`,
`_run_delegate`) was deleted in Phase 5. Don't reintroduce.

## Provider & Warmup (brain.py)

`resolve_provider_for_model(model)` is the **single source of truth** for `{api_key, base_url, provider_name}`.

**Provider concurrency queue** (`LocalProviderQueue`): used by the warmup
path (`run_model_warmup`) to serialize warmup hits against the local
provider's batched-decode capacity. The sidecar owns its own connection
to the provider via CLIProxyAPI/oMLX directly and does NOT participate in
this queue — production rounds bypass `LocalProviderQueue` entirely.

**KV-prefix stability**: warmup payload MUST match first-turn payload byte-for-byte — system prompt timestamp rounded to hour (not minutes), MCP tools attached, tools merged/deduped/sorted, `stream=True` + `stream_options`. Warm pool `claim()` only fires for `{agent:main, project:'', status:'', note_context:''}` — anything else changes system prompt and invalidates prefix.

**Pool invalidation fields** (`_prefix_fields`): warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile.

**oMLX gotcha**: Qwen3/Gemma-4 chat templates default `enable_thinking=true` when kwarg absent. `_apply_inference_to_payload` ALWAYS emits `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`. Warmup must mirror this byte-for-byte or KV prefix misses silently.

## Model Config (brain.py)

`init_models_config` does forward-looking re-detect of thinking format: `'none'` → real format upgrade, never reverse. **Must deep-copy `existing_models`** — shallow copy aliases dicts and silently breaks the diff-based persist gate.

`MODEL_PROFILES` overlays: **only request-style knobs**, never resource knobs (warmup, GPU) — silently re-enables user-toggled-off fields.

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Concurrency & Thread Safety

- **Thread-locals required** for every request/background thread: `current_agent`, `mcp_manager`, `current_session_id`, `current_user_id`, `project`. Never fall back to globals — concurrent requests bleed. The sidecar's `/v1/tools/call` callback re-establishes them per call from the request body.
- **MCPManager** (`brain.py`): `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot.
- **Background threads** (scheduler, TaskRunner, workflow engine): set + clean thread-locals in try/finally before each `sidecar_proxy.background_call(...)` so the dispatch callback sees the right scope.

## Scheduled Tasks (brain.py)

Each run = immutable `schedule_history` row + synthetic `session_id=sched-<run_id>`. Due tasks fire in **parallel**. Per-task `working_dir` overrides system prompt cwd; `python_exec` stays pinned to artifact folder by design — file-write tracking depends on it.

`_validate_thinking_level_for_model` rejects format-mismatched levels at fire time. `caveman_system` deliberately NOT exposed per task (per-model knob, would invalidate warmup KV prefix).

## Document Extraction Pipeline (doc_convert.py) — single choke point

Every document read in Brain funnels through **one** dispatcher,
`engine.doc_convert._do_extract(src, *, use_markitdown, caps, sheet, slides,
pages, include_tables, emit_meta, page_marker)`. Four consumers, one path:

- chat read — `brain.tool_read_document` → `_do_extract(caps=False, …)`
- project mining — `convert_one` → `_do_extract(caps=True)` (defaults)
- PII pre-send scan — `brain.extract_attachment_text` → `_do_extract(caps=False)`
- ARL classification scan — `handlers/classification` → `convert_one`

A fix in any extractor reaches all four at once. Do NOT add a per-consumer
parser — that's the duplication this design removed (v9.10.0; previously 4
independent xlsx readers).

**Two — and only two — tuning surfaces per format**:
1. **markitdown** (subprocess, tried first) — gated by `_MARKITDOWN_EXTS`.
2. **`_extract_*` fallbacks** (your Python) — `_extract_pdf` (fitz +
   pdfplumber for tables), `_extract_xlsx`, `_extract_csv`, `_extract_docx`,
   `_extract_pptx`, `_extract_eml` (stdlib), `_extract_msg`,
   `_extract_markitdown_only` (epub/zip).

**Reorder/disable is per-format and one line**: to force a format onto your
own code (markitdown loses), **remove its ext from `_MARKITDOWN_EXTS`** — it
then never calls markitdown. True fallback *inversion* (own-code-first, then
markitdown) is NOT wired — `_do_extract` is hardcoded markitdown-first →
fallback; the disable-via-set path covers the "markitdown is worse" case
without a logic change. (Empirically: markitdown handles `.eml` but worse
than the stdlib `_extract_eml` — leaks MIME headers — which is why `.eml` is
deliberately NOT in `_MARKITDOWN_EXTS`. `.txt/.md/.html/.json` likewise skip
markitdown — already text.)

**caps invariant**: the `caps`/`sheet`/`slides`/`pages`/`emit_meta`/
`page_marker` knobs all default to mining's prior behavior so the daemon's
companion-`.md` output stays **byte-stable** (changing it would re-embed
every project doc). caps + the knobs only bite on the **fallback** — when
markitdown succeeds, its output is returned verbatim and the knobs are inert.
read paths pass `caps=False` (no row/cell cap); mining/classification keep
`caps=True` (100k rows/sheet, 200 chars/cell).

**Concurrency note**: markitdown runs as `subprocess.run` per call (120s
timeout), with **no concurrency cap** (unlike `LocalProviderQueue`). Under
multi-user document load the likely failure mode is markitdown
process-pressure / timeout, NOT extraction-logic bugs — the fix there is a
concurrency gate, a third lever distinct from the two content surfaces.
Tempfiles (`read_attachment`-style byte spills) use unique names; the ad-hoc
`.md` cache is keyed by `(abs_path, mtime, size)` — concurrent reads of the
same file are safe.

`DocumentParser.parse_{docx,xlsx,pptx}` in brain.py are **thin shims** over
`_do_extract` (kept so `DocumentParser.parse()` + admin file-preview
endpoints work unchanged). `tool_read_attachment` was deleted in v9.10.0
(unreachable — never in TOOL_DISPATCH/DEFINITIONS/GROUPS, store never
populated). Don't reintroduce a bytes-based reader; spill to a tempfile and
use `_do_extract`.

## Knowledge Graph (kg_extract.py / doc_convert.py)

`_run_kg_for(...)` resolves prefix via `os.path.realpath()` — macOS `/tmp` → `/private/tmp`; without this drawer source_files don't match.

Source-change invalidation DELETEs triples rows matching **exact** `source_file` (not LIKE prefix — siblings stay safe).

`doc_convert.py` companion `.md` files live under `<folder>/.brain-extracted/<name>.<ext>.md`. `<!-- brain-source: <abs path> -->` lets agent resolve back to the original binary.
