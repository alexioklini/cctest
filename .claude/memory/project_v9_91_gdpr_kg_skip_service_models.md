---
name: project_v9_91_gdpr_kg_skip_service_models
description: "v9.91.0 (2026-06-07) — GDPR-skip for KG + per-file KG state, unified Service-Modelle panel + fail-loud, Doctor scanner-disabled warn. All 3 HANDOVER_2026-06-07 open items, verified live."
metadata: 
  node_type: memory
  type: project
  originSessionId: 3d4d65e3-5354-4dcb-8e39-d4ae92ced286
---

v9.91.0 (2026-06-07) — built + verified live the 3 open items from `[[HANDOVER_2026-06-07]]`. Restarted ONLY after confirming KG/mining finished (converged 09:42, daemon idle 6h-interval, triples stable 2696). Post-restart KG intact, Doctor 7ok/2warn, clean startup.

**1. GDPR-skip for KG + per-FILE state** (user chose per-file-inside-folders, the larger option):
- `brain.gdpr_would_block_or_anonymise(text, model)` — decision-only probe mirroring `gdpr_pick_model_for_background`'s early-exit ladder WITHOUT swapping/anonymising. Returns `(skip, reason)` reason∈{block,anonymise,classification}. Falls open on errors.
- `engine/kg_extract.py` `_process_source`: per-DOCUMENT skip-gate after frontmatter-strip, before chunk. Skip → `kg_skipped: gdpr_<reason>` progress row (marked done, no retry-loop) + `RunResult.gdpr_skipped`. The PROPER fix for the 2026-06 incident (anonymise gutted policy chunks → empty).
- `kg_extract.kg_source_states_for_wing(db, wing)` → `{source_file: kg|skipped|empty}`, keyed under BOTH the `.brain-extracted` companion AND the derived original-binary path (mirrors `indexed_source_files_for_wing`). The KG progress `source_file` is the COMPANION `.md`, but the folder-tree walks ORIGINAL binaries — must map both or per-file lookup misses.
- `/folder-tree` endpoint (`handlers/projects.py`) now returns per-file `{mined, kg, skip_reason}`. `web/js/panels_project_tree.js` renders per-file KG badge: green KG / amber KG⊘ (skipped, reason tooltip) / grey KG· (mined-no-triples). VERIFIED live on kg-real-policies: 10 indexed+kg, 5 indexed+empty, 4 pending+none.
- Dormant (GDPR disabled) but makes re-enabling safe.

**2. Unified Service-Modelle panel + fail-loud:**
- New `GET/POST /v1/services/models` (`admin_observability.py`) aggregates EVERY service-model slot across TWO files: config.json (default/chat_summary/background_task/kg.extraction_model/ocr) + tools_config.json (text_to_speech/transcribe_audio default_model, via get_tool_config/save_tool_config). Resolve status per slot (ok/unset/missing/disabled). FAIL-LOUD: unknown model/provider → 400, never coerced. VERIFIED: bogus model→400, valid round-trip clean, config.json restored.
- New Settings→Allgemein→**Service-Modelle** tab (full editor, all slots + OCR engine/provider/model dropdowns, red pill on unset).
- `engine/doc_convert.py` `_ocr_config`: removed 4 hardcoded defaults (engine→'none' fail-safe, provider/model→'' w/ guards, dropped `https://api.mistral.ai/v1` base_url fallback). Present-config behavior byte-identical.

**3. Doctor scanner-disabled warn:** `engine/doctor.check_scanners_enabled` → WARN when gdpr_scanner/classification_scanner `enabled=false`. Doctor overall now `warn` (was false `7/7 OK`). VERIFIED live.

GOTCHAS: project ROUTING name (`kg-real-policies`) ≠ display `name` field ("Regelwerk der Bank") — endpoints use the routing/folder name. js_gate net-globals 1224→1232 (bumped baseline same commit). NOT yet committed — see `[[feedback_commit_to_main]]`.
