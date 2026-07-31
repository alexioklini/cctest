---
name: feedback_composer_controls_are_source_of_truth
description: "In the web UI, composer toggle buttons (their DOM dataset/innerHTML) are read at send time and OVERRULE chat state — resetting chat.* alone is a half-fix"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 368f9659-fa33-424a-85bc-8a070e7ce730
---

In the Brain web UI, the composer toggle controls (caveman, refine-tier,
memory, thinking, GDPR shield) are the **source of truth at send time** — the
send/refine paths read the button DOM (e.g. `_refineCavemanValue` /
`_refineTierValue` read `btn.dataset.caveman`/`.tier`; status buttons read their
own rendered state) rather than `chat.*`. So a button left showing a stale value
doesn't just look wrong — it gets read back and **overrules** any state reset.

**Why:** Resetting `state.activeChat.*` fields without repainting the controls
is a half-fix. The DOM still holds the old value and wins on the next send,
silently negating the reset (this is exactly the new-chat / open-project
composer-defaults bug, fixed 2026-06-22 commit 71339bd — see
[[project_project_composer_attachment_drop]] for the related project-composer
gotcha).

**How to apply:** Whenever you reset composer/session UI state, ALSO repaint the
controls so DOM and state agree — call `updateStatusBar()` (repaints
caveman/memory/gdpr-shield), `refreshThinkingButton()`, `_refreshWebsuche()`,
`schedulePIIBadgeUpdate()`. The `'welcome'` and `'project-detail'` nav branches
do NOT run `updateChatView()`/`updateStatusBar()` like `'chat'` does, so resets
on those paths must repaint explicitly. Verify by reading the button DOM after
the reset, not just the chat state.
