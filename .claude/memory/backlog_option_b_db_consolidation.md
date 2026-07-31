---
name: backlog_option_b_db_consolidation
description: SHIPPED 2026-05-08 as v8.26.0 — server_lib/db.py is now the single source for chat-DB helpers; server.py re-exports them so handler mixins keep working
type: project
originSessionId: 8bd0d32a-45c8-4fae-b7b3-f588d78ae433
---
SHIPPED v8.26.0 (2026-05-08, commits `101e80b` → `7dec01b`). Follow-up to v8.25.7 cleanup — the `_db_conn`/`_db_safe` fork between server.py and server_lib/db.py is gone, plus the implicit fork via globals-resolution for ChatDB / mempalace helpers / node registry.

## What landed (4 commits, all green smoke-tested)

**M1 (`101e80b`)** — `_db_conn`, `_db_safe`, `CHAT_DB` deleted from server.py (~40 LOC), replaced with `from server_lib.db import …`. Handler mixins keep working via re-export → server.py globals.

**M2 (`1924b11`)** — `class ChatDB` (~880 LOC) cut from server.py and pasted into server_lib/db.py above TranslateHistoryDB. server.py re-exports. `engine/loop.py` + `engine/scheduler.py` (4 sites) inline imports rewired to `server_lib.db`. ChatDB's 3 cross-module references (`_purge_mempalace_session`, `_project_id_for_name`, `engine.AGENTS_DIR`) were initially lazy in-method imports — collapsed to bare-name resolution after M3 made the helpers in-module.

**M3 (`15abeed`)** — 9 mempalace helpers + node registry (state + 4 functions) moved from server.py to server_lib/db.py. server.py re-exports both groups. `MemPalaceClient` singleton (`_mp`) **deliberately stays in server.py** — moved helpers reach it via lazy `from server import _mp` inside function bodies (avoids a much larger move involving every `_mp` consumer). Required adding `re` and `uuid` to server_lib/db.py imports; required fixing `__file__` path in `_load/_save_node_config` to add `..` (server_lib is one level deeper than repo root).

**M4 (`7dec01b`)** — last 2 `from server import _db_conn` sites in engine/context.py rewired to server_lib.db. Version bumped to 8.26.0 with a full changelog entry summarizing the four milestones.

## Final state

- `git grep "from server import" engine/ handlers/ server_lib/` returns **zero hits** except the intentional `_mp` lazy imports inside server_lib/db.py (3 sites — `_purge_mempalace_session`, `_purge_mempalace_turns`, `_memorize_mempalace_turns`).
- `_db_conn`, `_db_safe`, `CHAT_DB`, `ChatDB`, mempalace helpers, node registry — each lives in exactly one file (server_lib/db.py).
- server.py: 6735 → 5512 lines.
- server_lib/db.py: 127 → 1366 lines.
- Smoke battery green at each restart: chat send, session delete (exercises full lazy `_mp` path), `/v1/sessions`, `/v1/favourites`, `/v1/translate/history`, `/v1/projects`, `/v1/nodes`.

## Patterns established (for future moves)

- **Re-export shim** in server.py keeps handler mixins working unchanged. Cost: server.py imports things it never uses directly. Worth it — alternative is touching every handler module.
- **Lazy in-method imports** for cross-module references (`from server import _mp`, `import brain as engine`) avoid circular-import hazards when called from background threads / late-bound resolution.
- **`__file__` fix when relocating path-aware helpers**: add `..` when moving from repo root into `server_lib/`.

## What's NOT done (deliberately out of scope)

- `MemPalaceClient` (~145 LOC) stays in server.py. Pulling it out would touch every `_mp` consumer (12 lines in server.py).
- `brain.py` still has 7 inline `from server import ChatDB / _db_conn` sites — the plan only flagged engine/, handlers/, server_lib/. brain.py is repo root, not under engine/. They keep working via re-export and were left alone to keep M4 focused.
- handler mixins still resolve names via globals-inheritance (`_node_registry`, `ChatDB`, etc.). Updating them to do their own imports was the "cleaner but bigger" Option B variant — not taken.
