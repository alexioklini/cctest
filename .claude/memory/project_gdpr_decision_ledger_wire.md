---
name: project_gdpr_decision_ledger_wire
description: "GDPR wire-history protection is now DETERMINISTIC, driven by the pii_decisions ledger (incl. fake_value), not the live mapping — fixes prior-anonymised values leaking after a 'continue' turn"
metadata: 
  node_type: memory
  type: project
  originSessionId: 047a0eef-23ec-4b86-8e36-b641484453cd
---

v9.201.0 (2026-06-24): fixed a real PII leak (chat 6f034721) — a session that anonymised the email early, then chose 'continue' for a NEW finding (phone) at turn 5, sent the already-anonymised email to the cloud IN CLEAR from turn 5 on. ROOT CAUSE: the wire-history pseudonymisation (`_pseudonymize_history_for_wire`) was gated on the LIVE mapping (`session._gdpr_mapping_id`), which `_handle_chat` set to None on any continue/local turn.

FIX = user's deterministic model: findings + decisions are persisted (append-only `pii_decisions` ledger); before send, decide per value from the ledger — reuse prior anonymisation / keep original.
- NEW column `pii_decisions.fake_value` (additive) holds original→pseudonym for `turn_action='anonymise'` rows. Recorded server-side at TURN-END from the COMPLETE `_mapping.forward` (worker `finally`, handlers/chat.py) → covers typed text + attachments + mid-turn read_document/read_file PII. Ledger is self-contained (no pseudonym_maps decrypt).
- NEW `handlers/chat._apply_pii_decisions_to_wire(messages, decisions)` REPLACES the scan+mint history pass: replaces each anonymise-decision value with its fake in the wire copy (user+assistant) every turn — no scan, no mint, INDEPENDENT of a live mapping. accepted/FP/local→original; unseen→untouched; longest-original-first.
- continue/local WITH a prior mapping now rehydrates the mapping (so the REPLY de-anonymiser is active for echoed fakes) but `_gdpr_pending_action` stays empty so the NEW typed value is NOT anonymised (honours 'continue'). Skipped on explicit shield opt-out (`_gdpr_skip_auto`).
- History popover (panels_gdpr.js) now ALWAYS shows the complete server history count + decision detail as an ADDITIONAL section (was either/or → prior PII disappeared when a new finding was decided).

Tests: tests/test_gdpr_decision_wire.py (7). The old `_pseudonymize_history_for_wire` (scan+mint) still exists but is no longer the live wire path. `get_session_pii_decisions` returns `fake_value`. Live-verified anonymise→continue keeps email anonymised, phone clear. See [[project_gdpr_feedback_modal]] [[project_gdpr_skip_policy_and_precision]].
