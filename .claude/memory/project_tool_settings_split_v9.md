---
name: per-tool-settings-topic-a-b-discipline-split
description: "v9.0.x architecture — tools.md replaced by admin-editable per-tool settings (config.json → tool_settings); retrieval discipline (Topic A) lives per-tool, output discipline (Topic B) gated on research_mode."
metadata: 
  node_type: memory
  type: project
  originSessionId: 35d3fa9f-2086-4b4d-ab4a-69a77de4c073
---

**Replaces** the legacy `tools.md` file (deleted) AND the v8.31 conflation
of retrieval+citation discipline in `DEFAULT_PROJECT_INSTRUCTIONS`.

**Storage**: `config.json → tool_settings.<tool_name>` (per-tool record):
- `enabled` (bool, default true) — global kill switch; false hides from EVERY agent
- `deferred` (bool, default false) — per-tool defer override, ORs with per-agent `deferred_tool_groups`
- `description / when_to_use / warnings / examples` (str, default "") — prose injected via `_render_tool_descriptions`
- `applies_with` (list, default []) — all-of gate; tool's prose renders only when every name also active

**Endpoints**: `GET /v1/tools/settings` (admin), `POST /v1/tools/settings` (admin, validates name+bools+applies_with). Persists to config.json + audits.

**Resolver wiring**:
- `_get_agent_tool_names()` filters disabled tools
- `resolve_active_tools()` defer subtraction ORs per-agent groups + per-tool flag
- `_render_tool_descriptions()` defensively skips disabled records

**Topic A / B split** (updated D1-D4):
- **Topic A** (retrieval discipline — search-first, query keyword shape, saving guidance, 3-step flow, read_path how-to, KG decision rule, BINARY DOCUMENTS) lives entirely in `tool_settings.{mempalace_query, read_document, mempalace_kg_search, mempalace_kg_query}.description`. Admin-editable. read_document + KG carry `applies_with: ["mempalace_query"]` so they only render in project-retrieval contexts.
- **Topic B** (output discipline — refuse-on-empty, no-filler precision, per-claim citation) lives in `config.json → research_mode_disciplines.{refusal, precision, citation}` (admin-editable per-section via GET/POST `/v1/research-mode/disciplines`). Renders only when project + research_mode=on. Seeded at startup from `RESEARCH_MODE_DISCIPLINE_DEFAULTS` in brain.py.

Brain.py's `_build_system_prompt` only emits a short "this is a project chat with its own memory store" paragraph for project chats — everything tool-related is in tool config; everything output-disciplinary is in research_mode_disciplines.

**Why split**: pre-split, research_mode=off + project chat had no retrieval discipline (gemma-4-26B eval exposed this — model fabricated answers without search-first guidance). Split fixes that gap while keeping audit-grade citation as a separate opt-in.

**Admin UI**: General Settings → Tools tab. Grouped collapsible registry of all 63 tools across 20 groups (4 ungrouped — pre-existing TOOL_GROUPS gap on memory_*). Per-tool expanded panel: enabled/deferred toggles, optional integration knobs, 4 prose textareas, applies_with multi-select. Two save scopes (Save → /v1/tools/settings, Save integration → /v1/tools/config).

**Migration**: one-shot at startup via `migrate_tool_settings_from_md()` — parses legacy `tools.md` anchored blocks (`<!-- @anchor:tool1,tool2 -->`) into records. Multi-anchor blocks attach to leader with rest in `applies_with`. Idempotent — once `tool_settings` exists in config.json, never re-runs.

**Don't**:
- Re-add `tools.md` — file is deleted
- Hardcode tool prose in `_build_system_prompt` — use the per-tool record
- Hardcode the research-mode disciplines — they're admin-editable per section in config.json
- Conflate retrieval discipline with citation discipline — they're separate axes by design
- Move refuse-on-empty back into Topic A — build/draft chats need to be allowed to fall back on training data when memory is empty (validated by e4b/gemma eval workflows)
