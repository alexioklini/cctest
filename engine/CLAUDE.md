# engine/ — Core Agentic Engine

Non-obvious invariants for this package. Root CLAUDE.md has the full architecture picture.

**Runtime source of truth — IMPORTANT.** As of the 2026-05-23 module-extraction
refactor (Tiers A–E, see `REFACTOR_REPORT.md`), `engine/` is now the live home
for most domains; `brain.py` (≈12.3k LOC, down from 25.2k) is the **orchestration
+ re-export layer**. Extracted symbols are defined in `engine/` and re-exported on
`brain` (`brain.X is engine.<mod>.X`), so handlers/server.py reaching them via
`import brain as engine` still resolve — but the live code runs from the engine
module. **Invariant: engine modules must NOT top-level `import brain`** (cycle —
brain imports them for the re-export); they reach brain runtime via lazy
`import brain as _brain` inside functions, and request state via
`from engine.context import get_request_context` (Tier-G; the old `_thread_local` is gone).

**Live engine/ modules:**

| File | Owns |
|------|------|
| `context.py` | `RequestContext` (contextvars-backed) + `get_request_context()` + `request_context()` ctx-manager + `ExecutionContext` (the shared request-state base; cycle-free, no `import brain`) |
| `tool_schemas.py` | `TOOL_DEFINITIONS` (Anthropic schema list) + OPENAI mirror + indices |
| `tool_exec.py` | tool-exec helpers: `_ok`/`_err`, dedup (`_check_tool_dedup`), sanitise/compress/microcompact, read-path tracker, `_get_artifact_session_folder` |
| `prompt_build.py` | `_build_system_prompt` + postprocess + the `*_preamble_text` helpers (+ per-session prompt cache) |
| `model_select.py` | `MODEL_PROFILES`, `resolve_provider_for_model`, provider cache + key-pools |
| `mempalace_glue.py` | `tool_mempalace_query` + `_wing_visible` (project-wing isolation) + memory/KG tools |
| `scheduler.py` / `quotas.py` / `workflow.py` / `code_graph.py` | scheduler+task-runner · CostTracker/QuotaManager/RateLimiter · workflow lexer/parser/interpreter · tree-sitter code graph |
| `ingest.py` | DocumentParser/Chunker/IngestManager/IngestWatcher (`_ingest_watcher` singleton stays in brain) |
| `tools/*` | every `tool_*` implementation: `file_tools` (file/shell/python/doc), `git_tools`, `gmail_tools`, `image_gen`, `context_tools`, `translate_tools`, `delegation_tools`, `misc_tools` (web_fetch/exa_search/searxng_search/use_skill/mcp/nodes), `ask_tools` |
| `pii_ner.py` / `classification.py` / `doc_convert.py` / `kg_extract.py` / `sync_log.py` | GDPR regex+NER · ARL classification · doc-extraction pipeline · KG triple extraction · sync log |

What STAYS in `brain.py`: tool registry **wiring** (`TOOL_GROUPS`, `TOOL_DISPATCH`),
runtime classes (AgentConfig, ProjectManager, MCPManager, TaskRunner,
WorkflowEngine, ContextManager, LocalProviderQueue), warmup/`build_first_turn_prefix`,
the tool-resolver (`resolve_active_tools`), GDPR/PII + classification config glue,
KG entity-indexing, the ask_* blocking-state (`_ask_user_pending`/`deliver_*`),
hooks, and `DEFAULT_PROJECT_INSTRUCTIONS`.

**Adding a new tool** = 4 sites across 3 files: schema dict in `TOOL_DEFINITIONS`
(`engine/tool_schemas.py`), `TOOL_GROUPS` (`brain.py`), the `tool_*` function (in
the matching `engine/tools/<group>.py`, reaching brain runtime via lazy `_brain`),
and a `TOOL_DISPATCH` entry (`brain.py`) — a **direct ref** to the function (not a
`lambda` forwarder, so the dispatch-identity check passes). MemPalace uses the
**`mempalace` pip package directly** (`mempalace.searcher`/`.palace`/
`.knowledge_graph`/`.closet_llm`).

**Historical (pre-refactor cleanup, kept for the record):** v8.29.0 deleted
`loop.py`+`constants.py` (~4700 LOC); v8.30.0 deleted 8 internal modules +
`analytics/` (~9918 LOC); v8.32.0 deleted the then-dead duplicate `tools/`
(`files.py`/`email.py`/`git.py`/`code_graph.py`/`web.py`) + `memory/` subpackage
(~9700 LOC) — at that time brain.py was the live source and those engine copies
had drifted. The 2026-05-23 refactor reversed that direction deliberately:
single-sourced the logic back INTO `engine/` with brain re-exporting (0 surviving
duplicate defs, verified by `refactor_gate.sh` Gate-2). Also long-dropped: 3 broken
`tests/test_worker_phase*.py`, the `backup/` snapshots.

The invariants below describe brain.py's runtime behavior.

## Agentic Loop (in-process)

The loop runs IN-PROCESS in `engine/llm_loop.py:run_loop(...)` on the caller's
thread (the Anthropic-SDK sidecar subprocess was deleted in v9.247.0 — see
`OPENAI_INPROCESS_LOOP_HANDOVER.md`). `handlers/sidecar_proxy` (legacy module
name — no sidecar) builds the tool list + provider params and drives the loop:
`run_turn(...)` (interactive, streaming) / `background_call(...)` →
`run_turn_blocking(...)` (non-interactive). The loop streams OpenAI
`/v1/chat/completions` SSE, drives rounds + tool calls, and emits the Brain
event vocabulary via `event_callback`.

- **Tool dispatch path**: the loop parses a `tool_call` from the stream and
  calls `engine.TOOL_DISPATCH[name](args)` DIRECTLY on its own thread (which
  holds the `RequestContext`) — or the MCP fallback (`llm_loop.dispatch_tool`).
  No HTTP, no nonce, no context rebuild. Result goes back as an OpenAI
  `role:"tool"` message. Background turns rebuild context first via
  `sidecar_proxy._apply_bg_context` (inside `with request_context()`).
- **Tool exec pipeline** (per dispatch): built-in pre → external
  pre → execute → built-in post → external post → `_after_file_write`.
- **`AskUserQuestion`** blocks via `_pending_answers[session_id]` +
  `Event`; unblocked by `POST /v1/chat/answer`. `run_turn` installs
  `make_artifact_event_callback` on the worker context so the emit reaches the
  LiveStream (else the tool hangs — the v9.101.12 failure mode).
- **Cancel**: interactive turns poll `session.cancel_token` between rounds AND a
  watcher thread closes the stream socket mid-generation; background turns use a
  `turn_id → Event` registry in `sidecar_proxy` (`cancel_turn` trips it,
  `run_turn_blocking` polls it via `is_cancelled`).
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
path (`run_model_warmup`) AND by interactive turns (`sidecar_proxy.run_turn`
wraps the in-process loop in `acquire_if`) to serialize hits against the local
provider's batched-decode capacity. No-op for cloud (`max_concurrent<=0`).
Whole-turn scope (held across all rounds incl. tool execution).

**KV-prefix stability**: warmup payload MUST match first-turn payload byte-for-byte — system prompt timestamp rounded to hour (not minutes), MCP tools attached, tools merged/deduped/sorted, `stream=True` + `stream_options`. Warm pool `claim()` only fires for `{agent:main, project:'', status:'', note_context:''}` — anything else changes system prompt and invalidates prefix.

**Pool invalidation fields** (`_prefix_fields`): warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id, profile.

**oMLX gotcha**: Qwen3/Gemma-4 chat templates default `enable_thinking=true` when kwarg absent. `_apply_inference_to_payload` ALWAYS emits `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`. Warmup must mirror this byte-for-byte or KV prefix misses silently.

## Model Config (brain.py)

`init_models_config` does forward-looking re-detect of thinking format: `'none'` → real format upgrade, never reverse. **Must deep-copy `existing_models`** — shallow copy aliases dicts and silently breaks the diff-based persist gate.

`MODEL_PROFILES` overlays: **only request-style knobs**, never resource knobs (warmup, GPU) — silently re-enables user-toggled-off fields.

**Deletion tombstones**: `config.json` → `deleted_models: []`. Honored on startup AND every `action: 'sync'`. Only `Full Resync` clears tombstones. Never wire automatic clear path.

## Concurrency & Thread Safety

- **Request context = `RequestContext` in a `contextvars.ContextVar`** (`engine/context.py`), read/written via `get_request_context().<field>`, entered + torn down ONLY via `with request_context(**overrides):` (token-reset = automatic total teardown). The old `_thread_local = threading.local()` bag + its name are GONE (Tier-G). Interactive turns run the tool dispatch on the worker thread that already set the context; background turns rebuild it via `sidecar_proxy._apply_bg_context` inside their own `with request_context()`. See root CLAUDE.md §"Concurrency & Thread Safety" for the contextvars reused-thread bleed invariant (never set request context bare on a pooled thread).
- **MCPManager** (`brain.py`): `clients`, `_tool_to_server` under `self._lock`; iteration via snapshot.
- **Background threads** (scheduler, TaskRunner, workflow engine): wrap each task's work in `with request_context(...)` (or `with request_context(): init_thread_context(...)`) so the dispatch callback sees the right scope and teardown is automatic.

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
