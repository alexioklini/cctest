---
name: project_right_panel_turn_grouping
description: "Right panel (refs/artifacts/attachments) groups items by turn, collapsible, scroll-synced; artifact→turn anchor via artifact_versions.message_idx"
metadata: 
  node_type: memory
  type: project
  originSessionId: 96b4bb7a-f20a-4250-9b10-7b3c675eac4d
---

2026-05-21: Right panel now groups references / artifacts / attachments into
collapsible per-turn `<details>` sections; scrolling a turn into view in the
message list auto-expands that turn's section in the open panel (collapse
others) and scrolls the panel to it.

**Turn anchor mechanism** — everything keys on a *message-array index* mapped
to a turn via the existing `turnNumForMessageIdx(idx)` (web/js/chat.js):
- Attachments already carried `msgIndex` (client array index of the user msg).
- References: `_referencesByTurn()` walks `chat.messages` once, attributing each
  ref-bearing row (live `tool_result` or assistant `metadata.tools[]`) to the
  current turn; cited/searched split reuses the chat-wide basename rule.
- Artifacts: NEW server field. `artifact_versions.message_idx` (long-existing
  column, was always NULL) is now populated at write time via
  `ChatDB.artifact_message_idx(session_id)` = 0-based array position of the
  latest user message (the producing turn anchor). Surfaced through
  `get_artifacts` (per-version + artifact-level `message_idx` from v1) and the
  live `artifact_updated` SSE payload. Pre-feature artifacts (NULL) fall into an
  "Ohne Zuordnung" bucket.

**Off-by-one gotcha**: server `message_idx` indexes the *persisted* message
list; the client unshifts a synthetic `{role:'compacted'}` divider at index 0
after an LCM compaction. The artifacts renderer adds `+1` (`idxShift`) before
`turnNumForMessageIdx` when that divider is present. Attachments/references are
computed entirely client-side over the shifted array so they need no shift.

**Shared client helper**: `renderTurnGroupedPane(container, pane, {countFor,
itemsFor, ungrouped, emptyAll})` in panels.js renders all three. Reuses
`.refs-section` disclosure chrome + new `.panel-turn-*` CSS. Shows ALL turns
(empty marked "—"). Per-pane manual open/close persists in
`chat._panelOpenTurns[pane]`.

**Scroll-sync**: `initTurnScrollSync()` (IntersectionObserver, root
`#messages-scroll`, observes `.turn-group[data-turn]` only — NOT lcm blocks)
re-attached after every `renderMessages()`. Topmost visible turn →
`syncRightPanelToActiveTurn()` which is authoritative: sets `.open` + writes
`openMap` so re-renders keep focus; manual toggle wins transiently until next
scroll.

Related: [[project_panel_user_close_persist]] (the earlier "stay closed until
reload" guard — `state.userClosedRightPanel`).
