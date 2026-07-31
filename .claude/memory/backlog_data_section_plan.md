---
name: Data Section (Data-Formulator-style AI viz) implementation plan
description: Proposed new "Data" section like Translation — AI data-viz workbench reusing Brain's engine; full plan in repo HTML, not yet implemented
type: project
originSessionId: 149414c4-2c78-4286-a1c8-eadf27e58781
---
Proposed feature: a **"Data" section** (sidebar peer of Translation/Projects), an in-house Data-Formulator-style AI data-visualization workbench. **Not yet implemented** — design only.

Full plan: `data-section-plan.html` in repo root (self-contained HTML w/ SVG mockups, sequence diagram, reuse map, phased delivery, file-by-file change list).

**Why:** bank's risk/finance/regulatory-reporting teams want chat-driven chart building. Rejected embedding `microsoft/data-formulator` (MIT, ~15.5k★) as a companion app — its own LLM config would bypass Brain's GDPR scanner / quota / provider routing / audit. Bank data must stay inside Brain's safety envelope. So: port the *concept*, reuse the engine.

**How to apply:** when this feature comes up, the core constraint is **reuse, don't duplicate** — new code is only `web/js/data.js`, `handlers/data_viz.py`, two tools (`data_render_chart` + optional `data_query`), `engine/tools/data_viz_prompts.py` (~700 LOC vendored from Data Formulator's MIT chart-semantics + DataAgent prompts), `DataSessionDB` in `server_lib/db.py`, and a per-session DuckDB file dropped *inside the existing artifact folder* (cwd of `python_exec` already → bare relative path). Zero changes to: agentic loop, sandbox, GDPR scanner, quota, provider router, artifact pipeline, miner, MemPalace — they're called, not modified. System-prompt "DATA WORKBENCH" block gated by a `_thread_local.data_workbench` flag (mirrors `project` gating) so KV prefix stays workbench-agnostic for normal chats. Tool defs must go in all 3 of brain.py's TOOL_DEFINITIONS/TOOL_GROUPS/TOOL_DISPATCH + engine mirror.

**Phases:** A = Workbench MVP (upload xlsx→DuckDB, text→chart via send_message loop, Vega-Lite render, editable spec textarea). B = Reports/History/Memory (pin chart→HTML artifact, History tab mirroring TranslateHistoryDB, insight→MemPalace). C = live sources (read-only Postgres/MSSQL, schema+sample scanned before reaching model). D = polish (optional drag-shelf as prompt-composer shortcut, only if used). Out of scope: their workspace/data-lake/Azure-Blob layer, their code-signing/sandbox (Brain has its own).

Eval: new `eval/data/` 10Q canary (5 chart asks, 3 follow-ups, 2 deliberate ambiguities that should trigger `clarify`), judged vs hand-built gold — same harness pattern as existing `eval/`.
