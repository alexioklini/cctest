---
name: project-classification-phase-b
description: 2026-05-19 — Phase B shipped — ARL classification enforcement at GDPR seams + composer modal + force_local routing. Subclass trick = zero-touch background coverage.
metadata: 
  node_type: memory
  type: project
  originSessionId: 217f12ae-21b9-4320-86af-0b196e8c387d
---

Phase B of WPB ARL 20.02.02.06 — enforcement. Detection from Phase A
([[project-classification-phase-a]]) now drives routing decisions
(force_local / block) across attachment scan, tool reads, and every
background LLM call.

**Why:** Phase A surfaced classification; Phase B prevents classified
content from reaching non-local models. Strict-always-block is the
ARL §1.11 invariant ("ohne Zustimmung des Vorstands ausnahmslos
untersagt"). Confidential force_local matches §1.10 (eingeschränkter
Personenkreis). Internal stays as warn — §1.9 allows external sharing
under regulated conditions, so soft-surface is correct.

**Architecture key insight — subclass trick (saves ~10 wrap sites):**
`ClassificationBlockedError(GDPRBlockedError)`. Every existing
background caller that already wraps `gdpr_pick_model_for_background`
with `except GDPRBlockedError:` (next-prompt, chat summary, memory
classifier, refine, delegate, scheduler, KG extract, etc.) transparently
picks up classification blocks. The classification check runs FIRST
inside `gdpr_pick_model_for_background`. Zero-touch coverage.

**Files changed (Phase B, ~700 LOC):**

- `engine/classification.py` — fixed regex (removed too-strict `(?!\s+\w)`
  negative lookahead); added `extract_pdf_page_texts()` + `pdf_path` arg
  to `detect_with_pii()` for PDF footer fallback via fitz.
- `brain.py` — `ClassificationBlockedError`, `_CLASSIFICATION_DEFAULTS`,
  `_get_classification_config`, `_classification_effective_action`,
  `_classification_scan_text`, `_classification_gate_tool_text`,
  `classification_pick_model_for_background`. Gate hooked into
  `_gdpr_anon_tool_text` (one seam, covers read_document/read_file/
  python_exec/execute_command). Background gate hooked into the top of
  `gdpr_pick_model_for_background`.
- `handlers/chat.py` — `/v1/attachments/scan` response extended with
  `classification` block.
- `handlers/classification.py` — `/v1/classification/config` GET returns
  + POST persists the `classification_scanner` policy block; strict
  level coerced to `block` server-side regardless of input.
- `server.py` — loads `classification_scanner` from config.json into
  server_config.
- `web/js/files.js` — classification badge on attachment chips.
- `web/js/nav.js` — `classificationBlockActive()` +
  `classificationStrictBlockActive()`; folded into `piiBlockActive()`.
- `web/js/chat.js` — classification gate after PII modal in sendMessage.
- `web/js/panels.js` — `classificationActionModal()` (Cancel-only for
  strict, Cancel+Local for force_local).
- `web/js/settings.js` — Classification tab policy section (Phase B).

**Phase A → B regex fix:** `vertraulich(?!\s+\w)` was too aggressive —
it required no word after vertraulich, which fails for any normal
marker followed by `Verantwortlicher: ...` etc. Removed the lookahead;
the anchor keyword (Dokumentenklassifizierung etc.) provides enough
context.

**Real-world finding (Phase A → reaffirmed Phase B):** the WPB ARL PDF
footer is vector-graphics text — even fitz can't extract
"Dokumentenklassifizierung intern". Footer fallback validated on
synthetic PDFs; the WPB doc still scans as `unmarked + heuristic=
confidential + PII×50` which is correctly the suspicious case.

**How to apply for Phase C:**
- Derived artifact auto-marking: needs `session.classification_taint`
  flag set when any classified attachment is in scope; `_after_file_write`
  reads the flag and injects format-appropriate marker (md footer / docx
  custom property / pdf metadata).
- Three remaining background sites not yet wrapped (same gap as GDPR):
  `_handle_soul_chat`, workflow LLM nodes, warmup test-call. Closing
  these closes both GDPR and classification gaps simultaneously.

**Smoke-tested 2026-05-19:**
- `/v1/classification/config` returns policy block with WPB defaults
- `/v1/attachments/scan` with "Klassifizierung: vertraulich" content
  returns `{marker_level: confidential, effective_action: force_local}`
- Strict marker returns `{marker_level: strict, effective_action: block}`
- Subclass: `issubclass(ClassificationBlockedError, GDPRBlockedError)
  == True`

Related: [[project-classification-phase-a]] (Phase A detector + Data
view), [[project-attachment-anonymisation-redesign]] (the seam this
mirrors), [[feedback-single-fix-point]] (subclass trick = single fix
point for all background callers).
