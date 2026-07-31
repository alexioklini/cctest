---
name: engine/ dead modules — phase 2 (SHIPPED v8.30.0)
description: Phase-2 wire-or-delete sweep shipped 2026-05-08; 12 modules + analytics/ deleted (~9918 LOC) + OCR cost-tracking bug fixed
type: project
originSessionId: 3618cf5f-9f56-4e1d-a648-901bc3431c18
---
**Status**: SHIPPED v8.30.0 (2026-05-08).

**What was deleted**: 8 flagged modules (`agents.py`, `cli.py`, `context.py`, `mcp.py`, `models.py`, `provider.py`, `scheduler.py`, `tasks.py` — ~7573 LOC) plus the entire `engine/analytics/` package (`__init__.py`, `audit.py`, `costs.py`, `pii.py`, `quotas.py`, `tracing.py` — ~2345 LOC). Total ~9918 LOC of dead extraction.

**Latent bug fixed**: `engine/doc_convert.py:_log_ocr_cost` was reading `engine.analytics.costs._cost_tracker` (always `None` because nothing instantiated it) — every OCR call silently dropped its cost row. Rewired to brain's live `_cost_tracker`. The `log_ocr` method only existed on the dead analytics CostTracker; ported to brain's `CostTracker` class.

**Why the sweep was wider than originally framed**: the backlog memory listed 8 modules. Verification grep confirmed all 8 had zero external importers, but also caught `engine/analytics/` (which engine/CLAUDE.md had claimed was live). brain.py owns every duplicate (`CostTracker`, `AuditLog`, `TraceManager`, `PIIScanner`, `QuotaManager`, `MCPManager`, `LocalProviderQueue`, `Scheduler`, `AgentConfig`, `ContextManager`, etc.) — the analytics extraction was dead too.

**Final engine/ surface** (live): `tools/`, `memory/`, `kg_extract.py`, `doc_convert.py`, `sync_log.py`, `__init__.py`. Nothing else.

**Verification command** that came back zero on 2026-05-08:
```
grep -rn "from engine\.\(agents\|cli\|context\|mcp\|models\|provider\|scheduler\|tasks\|analytics\)" \
  --include="*.py" . 2>/dev/null | grep -v __pycache__ | grep -v /engine/
```

**Smoke test**: server restarted clean, no boot tracebacks, `_log_ocr_cost` import path verified, `brain.CostTracker.log_ocr` method exists and writes to `cost_log` table.

**Why:** Same image_gen-style trap pattern that motivated v8.28.0 + v8.29.0. Each unkilled fragment was another silent-divergence hazard. After v8.30.0 there are no more dead duplicate-extraction modules in engine/.

**How to apply:** Done. If a future feature gets added to engine/ and silently doesn't fire, check whether brain.py has its own copy first — that's the runtime source of truth via `import brain as engine` in handlers.
