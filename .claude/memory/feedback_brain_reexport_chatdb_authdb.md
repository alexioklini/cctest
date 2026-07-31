---
name: feedback_brain_reexport_chatdb_authdb
description: "ChatDB and AuthDB are NOT re-exported on `brain` — import from server_lib.db / server_lib.auth. ProjectManager/_scheduler/_read_user_profile ARE on brain."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 422c2919-78e4-4f94-9634-a41c3955b9ac
---

In `engine/tools/*` (and anywhere using the lazy `import brain as _brain` seam), **`_brain.ChatDB` and `_brain.AuthDB` do NOT exist** — they raise `AttributeError`. `ChatDB` lives in `server_lib.db`, `AuthDB` in `server_lib.auth`. Import them directly (both are leaf modules, no import cycle with engine): `from server_lib.db import ChatDB` / `from server_lib.auth import AuthDB`.

These DO resolve on brain (re-exported): `ProjectManager`, `_scheduler`, `_read_user_profile`, `AgentConfig`, `resolve_active_tools`, the `TOOL_*` registries, etc.

**Why:** Caused v9.21.5 bug — Brainy's helpdesk tools (`helpdesk_session_info`/`helpdesk_user_context`/`helpdesk_user_activity`) called `_brain.ChatDB`/`_brain.AuthDB`, threw AttributeError on every call, surfaced as a tool error → Brainy said "kann die Details nicht abrufen" and guessed from the chat title. The AttributeError is silent in a normal import-check; it only fires at tool-dispatch time.

**How to apply:** When a new tool/handler needs DB or auth access, import ChatDB/AuthDB from server_lib, never via `_brain.`. Verify any `_brain.X` ref against `hasattr(brain, 'X')` before relying on it. See [[project_helpdesk_brainy]].
