---
name: backlog_chatdb_dedup
description: RESOLVED 2026-05-08 — dead ChatDB copy removed from server_lib/db.py; server.py's ChatDB is the only copy now
type: project
originSessionId: 8bd0d32a-45c8-4fae-b7b3-f588d78ae433
---
RESOLVED 2026-05-08. Removed the duplicate `ChatDB` class from `server_lib/db.py` (was lines 424-1325). Also deleted dead `server_lib/sessions.py` (nothing imported it). Fixed `handlers/favourites.py` to drop its explicit `ChatDB` imports and use the global from `server.py` like all other handlers.

**Why it existed:** The handlers/ extraction refactor moved handler logic out of server.py but left DB classes in server.py. server_lib/db.py was a parallel implementation that diverged silently.

**Current state:** `server.py`'s `ChatDB` is the single authoritative copy. `server_lib/db.py` still exists but only contains `_db_conn`, `_db_safe`, `CHAT_DB`, `_user_wing`, `TranslateHistoryDB` — all genuinely used by server_lib/ helpers.
