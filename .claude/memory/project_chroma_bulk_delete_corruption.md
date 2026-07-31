---
name: project_chroma_bulk_delete_corruption
description: "Bulk col.delete(where={wing}) racing concurrent daemon writes wedges the macOS chromadb 0.6.3 hnsw segment; data survives in sqlite but index is unrecoverable in-place"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9d232b00-be49-4464-9583-7c2306933fb0
---

2026-05-27: While re-mining the policy project for Contextual Retrieval, I ran a bulk `collection.delete(where={'wing': 'project__<id>'})` to force a full re-mine, WHILE the project-sync daemon was concurrently writing to the same Chroma collection. This wedged the hnsw segment: every `count()`/`query()` then raised `InternalError: Error sending backfill request to compactor: Failed to apply logs to the hnsw segment writer`. A server restart did NOT recover it (corruption is persisted in the segment binaries, not in-memory).

**Why:** macOS ARM + chromadb 0.6.3 has a known-fragile hnsw update path. Upstream `mempalace.miner.process_file` deliberately does a PER-FILE `delete(where={source_file})` UNDER `mine_lock(source_file)` precisely to avoid concurrent hnsw mutation. A wing-wide bulk delete racing the daemon's inserts violates that and corrupts the shared segment for ALL wings (not just the target).

**How to apply:**
- NEVER bulk-`delete(where=...)` a live MemPalace Chroma collection while a daemon may be writing. Stop the server first (`launchctl bootout gui/$UID/com.brain-agent.server`, not just SIGTERM — launchd respawns it), or purge per-file under the lock.
- The sqlite layer (`embeddings`, `embedding_metadata` tables) survives — `PRAGMA integrity_check` = ok, all rows intact. But chroma 0.6.3 CANNOT rebuild a queryable hnsw from sqlite-only (copying just chroma.sqlite3 to a fresh dir → `count()` works but `query()` fails "Error finding id"). So in-place index recovery is not available; full re-mine from source is the reliable fix.
- Chat wings (`user__`/`team__`/`project_chat__`) re-sync cursor lives in chats.db (`ChatDB.mempalace_update_cursor`), NOT chroma — a chroma wipe does NOT auto-rebuild them (cursor says "already synced"). 576 sessions → resetting cursors = expensive re-summarization. Weigh before wiping.
- Full palace backup before any destructive op: `cp -a ~/.mempalace/brain ~/.mempalace/brain.backup-<ts>`.

Related: [[project_drawer_path_resolution_fix]] (the macOS hnsw segfault note in process_file's delete+insert comment is the same fragility).
