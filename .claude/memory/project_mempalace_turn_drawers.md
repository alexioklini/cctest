---
name: MemPalace per-turn drawer addressing
description: Chat drawers are keyed per-turn via source_file=session/<sid>#turn/<user_msg_id>, enabling selective memorize/purge of individual responses
type: project
originSessionId: e26b4f37-bd35-40ac-9853-f17b81479b72
---
MemPalace chat drawers are addressed per-turn (as of v8.4 work, 2026-04-19):

- `source_file` pattern is `session/<sid>#turn/<user_msg_id>` — the anchor is the DB id of the user message that opens the turn. Attachments and tool-result refs inherit the turn suffix.
- Legacy drawers (pre-2026-04-19) still use `session/<sid>` with no turn suffix. The session-wide purge still matches them, but they don't appear in the per-turn memorized set surfaced by `GET /v1/mempalace/session-turns`.
- `_purge_mempalace_turns(sid, turn_ids)` and `_memorize_mempalace_turns(sid, turn_ids)` in `server.py` are the helpers. They're exposed via `/v1/sessions/manage` actions `purge_turns` and `memorize_turns` — accept either `turn_ids: [mid, ...]` or `{scope: "all|this|above|below", anchor_turn_id: mid}`.
- Web UI: each assistant message has a palace-icon menu with 8 items. Actions are greyed out unless `memoryMode === 'off'`, and individual items auto-grey when they'd be no-ops (already memorized / nothing to remove).

**Why:** User wanted fine-grained control over what lands in long-term memory, independent of the session-level toggle. Per-turn keying keeps it cheap and lets the UI tell you what's already stored.

**How to apply:** When touching chat-sync in `server.py`, preserve the `#turn/<id>` suffix in any new drawer writes. When adding new memory UI, reuse `refreshMemorizedTurns()` + `state.memorizedTurns[sessionId]` (a Set of turn ids). Don't key by message index or array position — user turn ids are stable across compaction.
