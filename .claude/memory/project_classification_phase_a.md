---
name: project-classification-phase-a
description: 2026-05-19 — Phase A shipped — document classification detector + Data view UI per WPB ARL 20.02.02.06. Detect-only; enforcement deferred to Phase B.
metadata: 
  node_type: memory
  type: project
  originSessionId: 217f12ae-21b9-4320-86af-0b196e8c387d
---

Phase A of WPB ARL 20.02.02.06 classification detection — detect-only,
audit/diagnostic. Enforcement (force_local/block at chat seams) is Phase B.

**Why:** WPB ARL 20.02.02.06 says "Bereitstellung im Internet: verboten" for
Intern/Vertraulich/Streng Vertraulich. The bank's LLM use case IS internet
provisioning unless the model is local. Phase A surfaces classification so
the user/admin can see what's classified and verify policy compliance;
Phase B will wire enforcement into the existing GDPR seams.

**What shipped (PR scope, ~970 LOC):**

- `engine/classification.py` — pure detector function
  `detect_classification(text, *, filename, page_texts, cfg, pii_findings)`.
  Three signals: marker regex (Dokumentenklassifizierung/Classification/TLP),
  filename hints, content heuristic (PII + admin-editable keywords).
  Mismatch = `heuristic_rank > marker_rank`. Unmarked is its own state.
  `detect_with_pii()` wrapper lazily pulls `brain._pii_scan_text`.
- `handlers/classification.py` — 9 endpoints: scan-files (multipart),
  scan-folder (path-traversal-guarded), scan-project (walks input_folders
  + ingested/), scans list, scan detail (+`.csv`), scan delete, admin
  config GET/POST. 500-file cap, 50KB evidence cap with progressive trim.
- `server_lib/db.py` — new `ClassificationDB` class + `classification_scans`
  table in chats.db. Non-admin sees only own scans.
- `web/index.html` + `web/css/main.css` + `web/js/classification.js` — Data
  view UI (3 modes: Upload/Folder/Project), filtered table, CSV export,
  scan history. Drag-drop dropzone.
- `web/js/settings.js` — new Classification tab between GDPR and Context,
  admin-editable keywords per sensitivity (internal/confidential/strict)
  + extra regex patterns, "Restore defaults" per group.
- `CLAUDE.md` + `handlers/CLAUDE.md` — docs updated.

**How to apply:** Phase B work plugs the detector into
`_gdpr_anon_tool_text` (single seam covers read_document/read_file) and
`/v1/attachments/scan` (composer block). Reuse `detect_with_pii(text,
cfg=server_config)` — don't write a parallel pipeline. Policy shape:
mirror `gdpr_scanner` config (`enabled`, `server_block`, per-level action
ignore/warn/force_local/block, `default_local_fallback_model`). Strict
always hard-blocks regardless of mode (ARL §1.11 "ohne Zustimmung des
Vorstands ausnahmslos untersagt").

Open Phase B follow-ups: derived-artifact auto-marking (currently
convention-only — `write_file`/`edit_file` should inject
`Dokumentenklassifizierung intern` footer when the parent chat had any
classified attachment in context); Telegram is explicitly out of scope
(web UI only per user decision).

Related: [[project-attachment-anonymisation-redesign]] for the seam
shape this should mirror, [[project-gdpr-granular-config]] for the
per-category config pattern Phase B should clone.
