---
name: project_gdpr_feedback_modal
description: "v9.94-9.95 GDPR — opt-in post-turn feedback MODAL + config-single-source rule set + ipv4 fix + KG-corpus analysis. COMMITTED b352e49"
metadata: 
  node_type: memory
  type: project
  originSessionId: c2aa9c09-c095-4ebd-8097-181daee9d64d
---

**UPDATE: v9.95.0 COMMITTED + PUSHED (b352e49, 2026-06-08).** Full writeup: `HANDOVER_GDPR_FEEDBACK_AND_CORPUS.md`. Beyond the feedback modal (below), two more things landed in the same commit + a live-config tuning:
- **config.json = SINGLE source of truth for the PII rule set.** Was split-brain: code defaults (pii_ner.py) vs a stale April config snapshot (contact/network=warn in config, code default ignore; min_occurrences only in code). Fixed: config now carries the FULL set = code defaults (contact/network→ignore, business_id added, 27 min_occurrences). GUI save uniform (min_occurrences full snapshot blank→1; rule_overrides stays deltas by design). All 10 gdpr fields GUI-reachable.
- **ipv4 context-gated** (pii_ner.py): bare `20.2.4.3` clause numbers matched as IPs; now fires only near IP keyword. _pii_rules order unchanged.
- **KG corpus (58 docs, project f201b24ff6a2):** tool `scripts/scan_kg_policies_gdpr.py` (committed; reads .brain-extracted companions). After reconcile: 58/58 clean. LIVE config (gitignored) adds email=warn + `@wienerprivatbank.com` allowlist → 51 clean / 7 flagged (external vendor emails only). `date` fires on ZERO docs (9.93 killed the 2026-06 incident). **Anonymise does NOT harm KG for the 7** (structural proof: only email VALUES → shape-preserving fakes; vendor + contact-person names [name/org=ignore] + table structure byte-identical; normative profile extracts relationships not emails). NOT a measured triple-diff — standalone import can't resolve cloud provider (sidecar 500, dual-module footgun); needs live-server re-mine if measured proof wanted. Background warn==block (no human); KG skip ONLY if background_pii_action=skip/abort (v9.91 hardwired-skip removed in v9.92).

---

v9.94.0 (2026-06-08) — HANDOVER thread 2 "interactive: choose + watch + remember + feedback". Final shape after one direction change by the user.

**Design (final):** opt-in MODAL feedback, no passive badge.
- I first built an inline status badge (`renderGdprOutcomeBadge`) + redo menu under each reply. User then said "no more badges — we need a modal" → badge fully removed (function + CSS + render insertion).
- Pre-send PII/classification modal (`gdprActionModal`, web/js/panels_gdpr.js) gained checkbox **"Frag mich nachher wies gelaufen ist"** (off by default) → resolves `{verdict, askAfter}`.
- `askAfter` sets sticky per-session flag **`gdpr_feedback_ask`** (mirrors `allow_further_web` end-to-end: sessions DB col + `ChatDB.update_session_gdpr_feedback_ask` + Session field/load + manage action `gdpr_feedback_ask` + GET /messages echo both branches + `API.updateGdprFeedbackAsk`).
- Post-turn: when opted in AND turn carried a GDPR action (`done` SSE has `metadata.gdpr`), `maybeRunGdprFeedback` (chat_send.js) opens **`gdprFeedbackModal`** ("Hat es gepasst?" + per-mode summary + retry buttons for the 2 methods NOT just used + "Passt so" + checked **"Frag mich weiter wies gelaufen ist"**). Unchecking clears `gdpr_feedback_ask`; chosen method still reused via sticky `gdpr_action_pref`.

**CRITICAL retry-clean (user directive "last failed turns must not disturb result"):** `redoTurnAsGdprMode` (chat_render.js) DELETES the discarded turn server-side first (`delete_messages` by msg id, user msg + everything after) BEFORE re-sending — because the server's `session.messages` is the wire source of truth; client-only `chat.messages` slicing would leave the failed attempt on the server and the re-send appends after it. Then forces mode via one-shot `state._gdprActionOverride` consumed by `sendMessage` before the scan/modal.

**`active` gate (important):** modal fires ONLY when `metadata.gdpr.active=true` = THIS turn anonymised the user's OWN input (typed PII `_findings>0` OR an attachment submitted this turn) or swapped the model. It is FALSE when anonymise merely re-pseudonymised prior chat history for the wire — which happens on EVERY turn of a sticky-anonymise session even with clean typed text. Without this gate the modal popped on history-only turns (user-reported bug). local_model + anonymise_failed_local are always active=true.

**metadata.gdpr** (modal data source, set in chat worker handlers/chat.py): mode ∈ anonymise / anonymise_failed_local / local_model; signals = `RequestContext._gdpr_turn_outcome` (auto-torn-down, anonymise paths) + `session._gdpr_local_swap` (set/cleared pre-worker like `_gdpr_pending_action`). tokens_minted recomputed from LIVE mapping at turn end. Rides on `done` SSE + persisted metadata; wire-stripped (audit/display-only).

js_gate net-globals 1232→1235 (redoTurnAsGdprMode + gdprFeedbackModal + maybeRunGdprFeedback). DB migration sessions.gdpr_feedback_ask (additive, confirmed in agents/main/chats.db — NOT ~/.brain-agent/chats.db which is a stale copy). Server live 9.94.0. Skill doc 06-user-manual.md FAQ + SKILL.md 1.38.0.

STILL OPEN from HANDOVER: thread 1 (mining swap-reliability + KG local-extract badge + Doctor check). See [[project_gdpr_skip_policy_and_precision]]. Live PII E2E in browser NOT run (Chrome ext not connected) — logic verified deterministically in Node.
