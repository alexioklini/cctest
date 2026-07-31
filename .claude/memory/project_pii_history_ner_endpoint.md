---
name: project-pii-history-ner-endpoint
description: "2026-05-19 — composer \"Personenbezogene Daten im Verlauf\" button now unions server-side NER findings with the client regex scan via GET /v1/sessions/<id>/pii-history-summary"
metadata: 
  node_type: memory
  type: project
  originSessionId: 22620a72-629b-4be0-9f54-e2f83900b63c
---

The composer history-PII badge (`btn-pii-history` in web/index.html) used to be regex-only because `piiHistoryHasFindings()` (web/js/nav.js) ran the client-side `PIIScanner` over `chat.messages`. That meant soft-PII detected only by spaCy German NER (`name`, `address`, `organisation` — see `engine/pii_ner.py`) never surfaced in the badge even though the server-side anonymise/scan pipeline saw them. Concrete symptom: chat `2096e5a6` (user wrote "Alexander Klinsky, Springenfelserbengrund 11, 1220 Wien") had no button, while chat `9441d8b4` (alice@example.com + IBAN) did.

**Fix** (2026-05-19, files changed: handlers/sessions_handler.py, server.py, web/js/api.js, web/js/nav.js):

- New `GET /v1/sessions/<id>/pii-history-summary` runs `engine._pii_scan_text()` (regex + bare-id + spaCy NER, full pipeline) over the session's user+assistant text mirrored the same way as the client's `piiHistoryText` (no tool_use/tool_result, attachment metadata included). Returns `{counts: {<label>: N}, has, worst_action}` keyed by human-readable label so the popover can fold counts into the existing chip rendering.
- Client side: `piiHistoryHasFindings()` returns the local regex result immediately and fires `API.getSessionPiiHistorySummary(chat.sessionId)` fire-and-forget. When it returns, `_piiHistoryMergeAndCache(chat)` unions the two count maps (max-per-label so server's >= local for shared rule_ids) and calls `updatePIIBadge()` to re-render. New per-chat fields: `_piiHistoryCountsLocal`, `_piiHistoryCountsServer`, `_piiHistoryWorstLocal/Server`, `_piiHistoryServerScanLen`, `_piiHistoryServerInFlight`.
- Stale guard: cache key is `chat.messages.length`; if a new turn lands while a fetch is in flight, the next badge tick re-fires.

**Why:** Browser scanner stays regex-only by design (per CLAUDE.md GDPR section — keeps the 120MB spaCy model out of the wire). NER findings need a server roundtrip; the badge has to surface them or the user gets inconsistent UX between draft (pre-send modal sees NER) and history (badge didn't).

**How to apply:** Any future client-side PII feature that needs to see name/address/organisation has to fetch from this endpoint (or another that runs `_pii_scan_text` server-side). Don't try to ship spaCy to the browser.
