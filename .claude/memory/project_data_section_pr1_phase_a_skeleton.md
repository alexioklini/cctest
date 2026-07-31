---
name: Data Workbench PR1 — Phase A skeleton shipped
description: First Data-section PR landed 2026-05-12 — DuckDB-per-session store, data_query tool, upload/tables endpoints, web view. Charts (data_render_chart) deliberately deferred. Lays the dependency floor for §16/§17.
type: project
originSessionId: 06afca0c-0054-451b-ac00-7cc2404dae23
---
**Shipped 2026-05-12** (PR1 of the Data-section plan in `data-section-plan.html` / kickoff `project_data_section_16_17_kickoff.md`). Scope-cut Phase A: the DuckDB store + `data_query` + upload + web shell — **charts (`data_render_chart`, vl-convert, vega-embed) deliberately deferred to a later PR** (the chart half isn't a dependency for §16/§17).

**Why this ordering:** §16 (anonymise) + §17 (file-scan) sit on Phase A's DuckDB store + the `data_workbench` thread-local + the upload plumbing — not on charts. User confirmed sequencing: PR1 (this) → PR2 (`data_anonymise` tabular + manual GUI + deanonymise, **openpyxl roundtrip for xlsx-preserve**, OOXML-skeleton refactor) → PR3 (`data_scan_files` + §17 triage) → PR1b (charts + 10Q eval canary) → PR4 (docx/pptx/PDF format arms — pymupdf for PDF redaction, reuse translate's OOXML path for docx/pptx) → PR5 (GDPR-triggered `auto_anonymise`, held until FP rate on real bank tables is known).

**What landed (files):**
- `server_lib/db.py` — `DataSessionDB` (`data_sessions` table), `.init()` called from `SessionManager.__init__`.
- `engine/tools/data_viz.py` — `tool_data_query` (read-only SQL against `_data.duckdb`; `register_as` is the one permitted CREATE). `engine/tools/data_viz_prompts.py` — `DATA_WORKBENCH_PROMPT`.
- `brain.py` — registered `data_viz` group + `data_query` (4 sites: `TOOL_DEFINITIONS`, `TOOL_GROUPS`, import near `tool_generate_image`, `TOOL_DISPATCH`). `_build_system_prompt` injects `DATA_WORKBENCH_PROMPT` when `_thread_local.data_workbench`. `_get_agent_tool_names` adds `data_viz` group only when `data_workbench` set.
- `handlers/chat.py` — worker sets `_thread_local.data_workbench` from `session.is_data_workbench`; cleared in `finally`.
- `server.py` — `Session.is_data_workbench` attr (re-derived on load from `DataSessionDB.get(sid) is not None`); `DataVizHandlerMixin` wired (import + handler-class base + inject list); `/v1/data/*` routes in `do_GET`/`do_POST`.
- `handlers/data_viz.py` — `POST /v1/data/sessions` (create real Session + flag + DataSessionDB row), `GET /v1/data/sessions` (list, RBAC-scoped), `POST .../upload` (xlsx→openpyxl-per-sheet→pandas→DuckDB; csv→`read_csv_auto`; 71-detector scan over ≤20-row sample, flagged cols masked `«PII»`), `GET .../tables`. Audit line `data_upload`.
- `web/index.html` + `web/css/main.css` + `web/js/data.js` + `web/js/nav.js` — `data-view` (sidebar peer of Translation): session picker, upload zone, table grid with sample preview, "Chat with this data →" → `openSession(sid)`. The conversation uses the **ordinary `/v1/chat` endpoint** — a workbench session IS a real chat session.
- `CLAUDE.md` — new "## Data Workbench" section (DuckDB-in-artifact-folder invariant, `data_workbench` gating, dep policy).

**Key design decisions:**
- A workbench session is a *real* `Session` (via `SessionManager`), not a synthetic one — so it inherits the full chat loop (compaction, warmup, quota, GDPR pre-flight, artifacts, miner) for free. `is_data_workbench` is the only marker.
- DuckDB file = `agents/<agent>/artifacts/<date>_<sid_prefix>/_data.duckdb` — same folder `python_exec` runs in. Tool/handler resolve via `_get_artifact_session_folder(sid)`, NOT process cwd (daemon never chdir's).
- `data_viz` is workbench-only — NOT in `DEFAULT_TOOL_GROUPS` or `main`'s `token_config.tool_groups`. Gating via `data_workbench` thread-local keeps the warm-pool KV prefix workbench-agnostic for ordinary chats (mirrors `project` gating).
- Workbench message history is the ordinary `sessions`/`messages` DB rows (it's a real chat) — no separate store needed.

**Deps:** `pip install --break-system-packages duckdb` (homebrew python 3.14, externally-managed, no `requirements.txt` — other deps `pymupdf`/`python-docx`/`python-pptx`/`pandas`/`openpyxl`/`jsonschema` already present). `vl-convert-python` to be added with PR1b (charts).

**Note re `feedback_brain_tool_duplication.md`:** that memory is now stale on the "3 places in brain.py + engine mirror" point — per v8.32.0, brain.py is the single source of truth; engine/ no longer mirrors tool defs. New tool = 4 edit sites in brain.py only (`TOOL_DEFINITIONS`, `TOOL_GROUPS`, `tool_*` fn / import, `TOOL_DISPATCH`).

**Smoke-tested 2026-05-12:** create workbench → upload csv → tables listed (IBAN flagged when present; `email`/`contact` category is `ignore` in this deployment's config, so emails aren't flagged — correct, reuses the same config) → chat turn "find the row with highest amount" → model called `data_query`, answered "Bob" correctly. Server imports clean, web JS syntax-checks, no errors in `server.error.log`.

**Open follow-ups noted in the plan but not built:** `data_anonymise` + §16.0 GUI, `data_scan_files` + §17 triage, charts + `eval/data/` 10Q canary, `data_transform`/`data_verify`/`data_compare`/`data_report`, History/Reports UI, `auto_anonymise`.
