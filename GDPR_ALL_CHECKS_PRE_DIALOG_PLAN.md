# Plan: ALL PII detection before the decision dialog

## Goal
Every PII check runs **once, on the server, before the pre-send dialog opens**. The dialog is the *only* place PII is decided (confirm / mark false-positive / choose anonymise-or-local). The worker then **applies the confirmed decision set** — it never re-detects. Consequence (user-confirmed): the MRZ passport/name/DOB become normal, markable-FP findings; the worker becomes decision-driven (no re-scan, no MRZ seed, no fresh scan inside `read_document`).

## Root cause (verified against chat 912d9199)
PII is detected in **four** places today; only #1 runs before the dialog:

| # | Seam | File:line | Runs |
|---|------|-----------|------|
| 1 | Upload/pre-send scan (`extract_attachment_text` + `_pii_scan_text`) | `handlers/chat.py:8313` (`/v1/attachments/scan`), `brain.py:3106` | pre-dialog ✅ |
| 2 | **MRZ seed** (`_ocr_mrz_strip`+`parse_mrz`→`seed_identity_from_mrz`) | `brain.py:4430`, `pseudonymizer.py:1207`; called at `handlers/chat.py:3941` | worker, post-dialog ❌ (the passport) |
| 3 | Worker typed-text re-scan + entity/known-value sweeps | `handlers/chat.py:3957-3984` | worker, post-dialog ❌ |
| 4 | Mid-turn `read_document` (`_gdpr_anon_tool_text`: fresh scan + sweeps) | `brain.py:3290` (esp. 3358) | worker, post-dialog ❌ |

The dialog's per-finding FP flags are recorded to `pii_decisions` but the anonymise path re-derives what to redact by scanning, so FP marks on attachment/MRZ content have no effect. `_filter_pii_false_positives` (`handlers/chat.py:1885`) is wired ONLY into #3's typed-text path, never #2 or #4.

---

## Design

### A. One authoritative pre-send scan (seams #1+#2+#4's detection merged)
Extend the attachment scan worker (`handlers/chat.py:8313 _worker`) so per-attachment detection includes **everything the worker does later**:
1. text extract + `_pii_scan_text` (exists)
2. **MRZ pass**: run `_ocr_mrz_strip`+`parse_mrz` on image/PDF attachments; when a checksum verifies, emit the name / document-number / DOB as **ordinary findings** (new synthetic `rule_id`s: `mrz_name`, `mrz_passport`, `mrz_dob`) with the real surface value, so they render in the dialog with an FP checkbox. Detection only here — no mapping mint yet.
3. Return `findings_full` already carrying these, so the client dialog shows them uniformly.

Rationale: detection must be identical to what the worker would otherwise find later, so the worker finds nothing new.

### B. Dialog decides once; ship the decision set in the send
- `chat_send.js`: **await** `recordPiiDecisions` before `streamChat` (kill the fire-and-forget race at `chat_send.js:368/393`), AND pass the confirmed decision set inline in the `POST /v1/chat` body (new `pii_decisions` field) so the worker has it synchronously — no dependency on the side-table write landing first.
- Decision set = every finding the dialog surfaced: `{rule_id, value, false_positive, action}`.

### C. Decision-driven worker (replaces re-detection in #2/#3/#4)
- **Delete the MRZ seed call** at `handlers/chat.py:3941` (`_gdpr_seed_entities_from_attachments`). MRZ values now enter via the decision set like any finding.
- **Replace the worker typed-text scan** (`handlers/chat.py:3957-3984`) with: build the mapping from the confirmed non-FP decisions (mint fakes for exactly those values). No `_pii_scan_text` call in the worker.
- **`_gdpr_anon_tool_text` (`brain.py:3290`) becomes apply-only**: rewrite occurrences of known mapping originals (+registered variants via `apply_entity_variants`/`apply_known_values`) but **do not run `_pii_scan_text`** and do not mint from fresh detection. FP values are absent from the mapping → left in clear. (Keep the classification gate at 3318 untouched.)
- **Turn-end recorder** (`handlers/chat.py:6118`): preserve `false_positive` per value from the decision set instead of hardcoding 0.

### D. Keep the pattern already present
The web-egress gate (`brain.py:3812`) already reads FP decisions correctly — leave it; it now composes cleanly since the mapping only holds confirmed non-FP values.

---

## Files touched
- `handlers/chat.py` — scan worker (add MRZ detection as findings); worker anonymise block (mapping-from-decisions, drop seed + scan); await decisions; turn-end FP preservation; accept `pii_decisions` in `_handle_chat` body.
- `brain.py` — `_gdpr_anon_tool_text` → apply-only; remove/guard `_gdpr_seed_entities_from_attachments` call site (keep the function, it's reused by tests, but stop calling it in the send path).
- `pseudonymizer.py` — a `seed_from_decision(mapping, rule_id, value)` helper that mints the SAME fakes the current seeds/scan produce (reuse `_registered_id_fake`, `_fake_date`, entity create) so tokens stay stable; MRZ name still gets `_register_entity_variants` so garble/variants collapse.
- `web/js/chat_send.js` — await recordPiiDecisions; add decision set to send body.
- `web/js/panels_gdpr.js` / dialog — render `mrz_*` findings (labels via `PII_RULE_LABELS`); no structural change (they're just findings).
- `engine/pii_ner.py` or label map — add `mrz_name`/`mrz_passport`/`mrz_dob` labels.

## Invariants respected
- KV-cache: `_build_system_prompt` untouched (anonymise clamp still per-turn via `_gdpr_anonymising`). Wire-only injection unchanged.
- Single fix-point ([[feedback_single_fix_point]]): detection consolidated to ONE seam, not spread.
- Fail-loud: if the decision set is missing but scanner is enabled and PII-bearing content is present, the worker must NOT silently send in clear — fall back to refuse/anonymise-all and surface it (never a silent leak). This is the one place we keep a safety check, but it's a *guard*, not a second detector.

## Verification
1. Reproduce chat 912d9199 shape: PDF with uk_nhs/bg_egn/bank_account + MRZ passport → dialog shows **5** findings incl. passport; mark all FP + choose anonymise → ledger shows 5 rows all `false_positive=1`, mapping empty, cloud wire carries cleartext.
2. Mark none FP → 5 anonymised, `false_positive=0`, identical fakes to today (token-stability regression check).
3. Mark 3 FP → exactly those 3 in clear, other 2 anonymised.
4. `read_document` mid-turn adds **no** new mapping entries (assert `len(mapping.forward)` stable across the tool call).
5. Existing GDPR tests green (`test_mrz_entity_seed.py`, `test_pseudonymizer_entities.py`, `test_request_context_isolation.py`); py_compile brain/handlers/db; `/v1/status` up; `web/js/js_gate.sh` green.
6. Local-model turn: no mapping, values in clear (unchanged).

## Skill/changelog upkeep (per CLAUDE.md)
- Update brain-agent-guide `05-internals.md` (GDPR flow) + `01-api.md` (send body `pii_decisions` field).
- VERSION bump in `brain.py` + curated entry in `engine/changelog_curated.py` (admin-audience: "Datenschutz-Prüfung erfasst jetzt ALLE Funde inkl. Ausweis-MRZ vor dem Dialog; Ihre Auswahl wird exakt angewendet").

## Open risk to flag during impl
Token stability: the current MRZ seed registers the passport in two forms (bare + 10-char check-digit) so VIZ and MRZ collapse. `seed_from_decision` for `mrz_passport` must reproduce both registrations, or a passport that also appears in the document body would fragment into two fakes. Verified via test #2 (identical-fakes check).
