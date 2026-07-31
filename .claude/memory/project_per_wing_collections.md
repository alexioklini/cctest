---
name: project_per_wing_collections
description: "v9.62.0 per-wing MemPalace collections — REVERTED in v9.70.0 (2026-06-03). Back to ONE shared collection; per-wing didn't fix the corruption (was a quarantine-validator bug). Historical record."
metadata: 
  node_type: memory
  type: project
  originSessionId: d59e8b4b-32e8-4797-9d95-d5bf69512161
---

## ⚠️ REVERTED in v9.70.0 (2026-06-03) — this describes a SHIPPED-THEN-REVERTED experiment

**Current production state: ONE shared ChromaDB collection** (`mempalace_drawers` / `mempalace_closets`), wing is a metadata filter — NOT per-wing. The files below (`engine/wing_collections.py`, `engine/wing_migrate.py`, `scripts/wing_fresh_reset.py`, the two wing tests, `PER_WING_COLLECTIONS_PLAN.md`) are DELETED. The vendored `miner.py` / `closet_llm.py` `collection_name` params are no longer needed.

**Why reverted:** per-wing did NOT fix the recurring "HNSW corruption on restart." The true root cause was a quarantine-validator bug in the vendored MemPalace 3.3.5 `backends/chroma.py` — `quarantine_stale_hnsw()` wrongly rejected a complete segment whose `index_metadata.pickle` had `dimensionality=None` (legitimate for a full segment under chromadb 1.5.7). Per-wing's `batch_size 50000→100` only UNMASKED it (made the pickle flush every compaction). The real fix is venv patch #6 (`dim is not None` guard) — see [[project_mempalace_venv_patches]]. Per-wing added dead complexity on top, so it was reverted to the single shared collection that had run stably for weeks. The `_palace_write_lock` cross-daemon write serialization (added alongside, [[project_mempalace_review_findings]]) was KEPT.

Concurrency design now: one shared collection + one global `_palace_write_lock` (RLock) serializing all miner/chat-sync/project-sync writes (`server_daemons.py:62`).

---
*Historical record of the per-wing experiment follows (do not treat as current):*

**v9.62.0 (2026-06-03): per-wing MemPalace collections — SHIPPED + LIVE + migrated. [LATER REVERTED — see above]**

ROOT CAUSE fixed: all wings' drawers lived in ONE shared chroma collection = ONE HNSW index, so a delete/churn in any wing (e.g. the web-news URL re-mine each project-sync cycle — [[project_manual_web_search_websuche]] / web_urls) mutated the index ALL wings shared; a bulk-delete racing an upsert or unflushed-HNSW+death wedged that single segment → next boot quarantined + rebuilt the WHOLE palace ([[project_chroma_bulk_delete_corruption]]).

FIX: each wing → its OWN collection (`wd_<wing>` drawers / `wc_<wing>` closets), own HNSW index. A fault is contained to one wing + auto-heals from that wing's own sqlite via per-collection `rebuild_index`; other wings unaffected. **No flag — always on** (a default-off flag would leave the corruption-prone path live by default; decided mid-build, [[feedback_defer_to_users_migration_calls]]).

KEY USER FRAMING: frequent re-indexing of changing content is a first-class palace-wide workload, NEVER throttled — per-wing makes the churn SAFE, doesn't suppress it. Web-news URLs stay as-is in production.

CODE: `engine/wing_collections.py` (mapper wing→`wd_`/`wc_` name: chroma-legal, injective via hash-on-lossy; `get_wing_collection`, `add_drawer_to_wing`, `purge_wing_room`, `palace_overview` for admin, `assert_miner_patch` HARD startup guard — NO fallback). `engine/wing_migrate.py` (one-time direct sqlite COPY old→per-wing, verify-before-drop). Query: `engine/mempalace_glue._query_wings` (per-wing query+merge, one wing's failure isolated) + `_rebuild_wings` (heal only the bad wing). ~25 write/read/recovery sites routed in server_daemons.py / server.py / engine/kg_extract.py / handlers/admin_observability.py. VENDOR PATCHES (re-apply on pip upgrade, [[project_mempalace_venv_patches]]): miner.mine + closet_llm.regenerate_closets `collection_name` params.

TESTS: tests/test_wing_collections.py (mapping/always-on/patch-guard), tests/test_wing_fault_isolation.py (HEADLINE — corrupt wing A → wing B still queries, A self-heals, no admin: PASSES).

MIGRATION LESSON: re-mine does NOT refill unchanged wings (miner mtime/SHA gate skips them). Tried direct-copy (stalled on a few short wings), then did what the user advised from the start — PURGE ALL + remine fresh (`scripts/wing_fresh_reset.py`: drop all collections + clear chat/closet/KG cursors, server stopped via `launchctl bootout`, restart via `bootstrap`). Converged clean. MemPalace is re-derivable so purge+fresh is the clean path, not data loss.

OPERATIONAL NOTES: graceful restart = `launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server` ([[feedback_never_sigkill_brain]]) — NOT `kickstart` without -k (no-op on a running service). Orphan `.drift-*`/`.corrupt-*` quarantine dirs accumulate over restarts → periodically `rm -rf ~/.mempalace/brain/*.drift-* *.corrupt-*` (dead segments, data in sqlite; verify live VECTOR-scope segments still have dirs after). User wings are small ON PURPOSE: 810/811 sessions have save_to_memory=0 so chat isn't memorized.

PRE-EXISTING (not per-wing): macrumors web-url KG extraction logs errors=10-13 (the LLM extractor fails on messy news-page chunks); a one-time boot AttributeError 'ChatDB has no attribute mempalace_sessions_needing_sync' (startup race, self-recovers). DEFERRED: none critical — admin dashboard reads now per-wing too.
