---
name: project_gdpr_skip_policy_and_precision
description: "v9.92-9.93 (2026-06-07) — GDPR 'skip' policy (4th background_pii_action) + KG de-hardwired; then full ~70-rule PII precision pass (per-rule min_occurrences, date/address context gates, business_id category). Verified live."
metadata: 
  node_type: memory
  type: project
  originSessionId: 3d4d65e3-5354-4dcb-8e39-d4ae92ced286
---

GDPR predictability + precision work, driven by user (continuation of v9.91 KG-skip). All verified live; server 9.93.0, KG intact 2696.

**v9.92.0 — 'skip' policy + KG de-hardwired:**
- `gdpr_scanner.background_pii_action` now has 4 values: anonymise / swap_to_local / **skip** / abort. `skip` = on PII+cloud-model, DON'T make the call, succeed EMPTY (NOT error). Via new `brain.GDPRSkipError(GDPRBlockedError)` — subclass trick = all ~20 existing `except GDPRBlockedError:` sites soft-return for free.
- 3 sites that map block→error status got a narrow `except GDPRSkipError` → quiet-complete (background_tasks status=done, scheduler status=success, KG kg_skipped not error).
- KG DE-HARDWIRED: removed v9.91's hardwired pre-check + `gdpr_would_block_or_anonymise()`. KG obeys the policy like every background caller.
- Config validate (admin_config) + readback (admin_artifacts) accept 'skip'. UI dropdown + helper text.

**v9.93.0 — PII detection precision (reviewed ALL ~70 rules one-by-one with user):**
- NEW MECHANISM `min_occurrences` (engine/pii_ner.PII_DEFAULT_MIN_OCCURRENCES + brain._pii_min_occurrences + config gdpr_scanner.min_occurrences): rule yields nothing unless ≥N DISTINCT values; counted per WHOLE document (gates whole rule); GUI per-rule field; default 1. Post-pass in _pii_scan_text. Values: date10, jp_mynumber10; 13 IDs=5; ~13 loose/financial=3.
- CONTEXT GATES: `date` not PII alone — fires only near birth/life-event keyword OR spaCy person name (~120ch); `dob` MERGED in. `address` (spaCy LOC) → personal/warn, only when person-name-adjacent. Helpers `_name_within`, `_date_has_birth_context`, `_BIRTH_CONTEXT_RE`.
- NEW CATEGORY `business_id` (default ignore): br_cnpj, tax_id_ctx (whole rule), spaCy organisation → not personal data.
- dk_cpr keyword-anchored (CPR|CPR-nr|CPR-nummer|personnummer); generic_secret_assignment bar raised len>=24 AND >=10 distinct chars.
- KG mining: whole-doc GDPR decision in _process_source (scans full file_text → correct per-doc min_occ counting), policy-driven.
- UI: per-rule min_occurrences input + business_id category; client utils.js ruleCategories/categoryLabels mirrored; readback merges defaults UNDER saved (so new fields surface).

GOTCHAS:
- **py_compile does NOT catch module-level NameError** — `_BIRTH_CONTEXT_RE` used `_re` at module scope but `re` was only imported locally in functions → server crash-looped on boot, compile was green. FIX: added `import re as _re` at pii_ner module top. LESSON: runtime-import the module (`importlib exec_module`) after edits, not just py_compile. (Reinforces [[feedback_compile_check_brain_py]].)
- _pii_rules ORDER is a correctness invariant — only edited rule bodies in place, never reordered.
- LIVE-VERIFIED: enabled GDPR temporarily, POST /v1/gdpr/scan-text with 12 distinct policy dates (no person) → 0 date findings (the incident scenario, fixed). Restored GDPR=disabled.

NEXT (user's stated roadmap, NOT yet built): (2) non-interactive local-fallback reliability, (3) interactive choose+watch+remember + feedback loop (did anonymise work? retry/switch mode). NOT committed yet ([[feedback_commit_to_main]]).
