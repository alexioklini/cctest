---
name: brain.py is the single source of truth for tools and the agentic loop
description: After v8.28.0 / v8.29.0 / v8.30.0 / v8.32.0 sweeps, brain.py is authoritative — all duplicate engine/ extractions are deleted, no shadow copies left
type: feedback
originSessionId: 43c03572-be37-4026-bdea-9c0dd81764e1
---
**v8.32.0 (2026-05-10) — final dead-extraction sweep**: deleted everything in `engine/tools/` except `image_gen.py`, plus the entire `engine/memory/` subpackage. Combined with prior sweeps, the engine/ surface is now reduced to 4 live files only:
- `engine/tools/image_gen.py` (the only live tool-extraction; imported at brain.py:21845)
- `engine/kg_extract.py` (importers: handlers/admin.py, handlers/projects.py, server.py)
- `engine/doc_convert.py` (importers: server.py, brain.py via lazy import)
- `engine/sync_log.py` (importers: handlers/projects.py, server.py)

Anything else under `engine/` is gone. Don't go looking for `engine/loop.py`, `engine/constants.py`, `engine/memory/store.py`, `engine/tools/files.py`, `engine/tools/web.py`, `engine/agents.py`, `engine/scheduler.py`, `engine/analytics/*`, etc. — all deleted. brain.py owns the live equivalents.

**Adding a new tool** (the rule going forward): edit brain.py in exactly four places:
1. `TOOL_DEFINITIONS` (around line 421) — schema
2. `TOOL_GROUPS` (around line 1635) — group membership
3. The `tool_*` function definition itself
4. `TOOL_DISPATCH` (around line 21862) — function mapping

Plus `agents/<name>/agent.json` → `token_config.tool_groups` to expose the group to that agent.

**Don't create files under `engine/tools/`** for new tools unless they're genuinely large (only `image_gen.py` qualifies today). Inline tools belong in brain.py.

**Why:** every previous "extraction" of brain.py's behavior into `engine/` produced silent shadow copies that drifted (image_gen 8.27.0 incident, the dead `_log_ocr_cost` cost-tracking path, the missing `_html_to_markdown` step in `engine/tools/web.py`, etc.). Import-graph audits don't catch parallel data/function definitions that nobody imports. The structural fix is to refuse to extract — keep one source of truth in brain.py.

**How to apply:** when changing tool behavior, agentic-loop behavior, scheduler/MCP/PII/cost behavior, edit brain.py. If a memory or doc references `engine/X.py` for `X != image_gen|kg_extract|doc_convert|sync_log`, treat that reference as historical and verify before acting on it.
