---
name: project_panel_user_close_persist
description: Right panel stays closed until page reload once the user deliberately closes it; state.userClosedRightPanel gates auto-open on new refs/artifacts
metadata: 
  node_type: memory
  type: project
  originSessionId: 96b4bb7a-f20a-4250-9b10-7b3c675eac4d
---

2026-05-21: When the user deliberately closes the right panel it stays closed
— no auto-reopening on new references/artifacts until a page reload.

`state.userClosedRightPanel` (web/js/state.js, default false → reset on reload).
- Set TRUE only on deliberate user close: `toggleRightPanel()` →
  `closeRightPanel(true)`, the close button (`onclick="closeRightPanel(true)"`),
  `closeArtifactPanel()`.
- NOT set on programmatic close (session switch calls `closeRightPanel()` with
  no arg).
- Cleared by `openRightPanel()` (any genuine open re-arms auto-open).
- Checked at the auto-open sites in web/js/chat.js: `references` SSE event,
  legacy `tool_result` ref extraction, `artifact_updated` (non-intermediate).
  Badges/registry still update when suppressed; only the panel-open is skipped.

Related: [[project_right_panel_turn_grouping]].
