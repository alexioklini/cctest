---
name: feedback_version_two_places
description: "Bumping the app version requires editing brain.py VERSION constant, NOT just the CHANGELOG"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 87cdf066-0218-4d0f-b29b-d5f238839a87
---

The served app version comes from `brain.py:4 → VERSION = "X.Y.Z"` (the status endpoint serves `engine.VERSION`; the UI shows it). This is SEPARATE from the `CHANGELOG = [...]` list right below it.

**Why:** I bumped the CHANGELOG top entry on three consecutive feature commits (9.17, 9.18, 9.19) but never touched the `VERSION` constant, so the running server kept reporting 9.16.0 — the user noticed the stale version in the UI.

**How to apply:** when bumping version, edit BOTH `VERSION = "X.Y.Z"` (brain.py line 4) AND add the `("X.Y.Z", date, "...")` CHANGELOG entry. They must match. A backend restart is needed for the new VERSION to show. Related: [[feedback_commit_directly_to_main]].
