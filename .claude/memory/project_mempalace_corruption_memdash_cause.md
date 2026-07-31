---
name: project_mempalace_corruption_memdash_cause
description: "Recurring MemPalace HNSW \"Error finding id\" corruption — likely-cause analysis (memdash writer) + auto-recovery fix + 3.3.5 lock mitigation"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6fb125f5-5c4f-4d34-a423-10b585b4448c
---

2026-06-02: The recurring chromadb HNSW corruption (`mempalace_query: InternalError: Error finding id`, sqlite intact but HNSW segment wedged, 49 `.drift-*` snapshots in `~/.mempalace/brain/`) was diagnosed during a policy-eval run that scored brain ~0.46 (all 15 queries empty-retrieval) — NOT a routing/deferral regression, purely the broken index.

**Likely cause (user's hypothesis, half-confirmed):** the **memdash dashboard** (v9.44.0, 2026-05-28 — see [[project_memdash_integration_plan]]) added an INTERACTIVE writer to the live brain palace. First `.drift-*` snapshot is **2026-05-29**, the next day; all 49 are 05-29→. memdash delete/edit buttons call `_mcp("tool_delete_drawer"/"tool_update_drawer")` → `col.delete()/upsert()` on the same palace the daemons mine. `handlers/memdash.py:27` even flags it: *"writes hit the live brain palace … accepted risk (see [[project_chroma_bulk_delete_corruption]])"* — but its reasoning ("no worse than existing in-process writes") was WRONG: daemon writes are serialized by `mine_palace_lock`; safety was the LOCK not the process. A delete racing a miner upsert wedges HNSW (thread-unsafe in chromadb 0.6.3 / hnswlib on macOS ARM).

**Mitigation already in the package:** MemPalace **3.3.5** `backends/chroma.py` now wraps EVERY write (`add`/`upsert`/`delete`) in `_write_lock()` → `mine_palace_lock` (lines 876/885/1049). So the classic delete-vs-upsert race is largely CLOSED now. Today's residual corruption is most plausibly an **interrupted HNSW flush from an unclean shutdown** (eval writing chat-sync drawers while server killed/restarted) — locks don't protect a kill between mutation and periodic flush.

**Fix shipped (v9.59.x, engine/mempalace_glue.py):** AUTO-RECOVERY at the query seam. On `col.query` raising an HNSW-corruption marker (`_is_hnsw_corruption`), `_try_rebuild_palace(palace_path)` calls `mempalace.repair.rebuild_index` (rebuilds HNSW from the durable sqlite), then retries the query ONCE. Lock-serialized (`_mempalace_rebuild_lock`) + 120s cooldown per palace so a burst of failing queries triggers ONE rebuild. Fail-safe (never raises; returns original error if rebuild/retry fails). This self-heals regardless of which trigger wedged the index.

**Manual repair recipe (server stopped):** `launchctl bootout gui/$(id -u)/com.brain-agent.server` → restore from `chroma.sqlite3.backup` if a prior rebuild left sqlite at 0 → `PYTHONPATH=~/.mempalace/venv/lib/python3.14/site-packages python3 -c "from mempalace import repair; repair.rebuild_index(palace_path='~/.mempalace/brain')"` (FOREGROUND — it holds `mine_palace_lock` via `MineAlreadyRunning`; don't double-run; takes minutes for ~13k drawers) → verify `repair.status(...)` shows `diverged=False` → restart. The live mempalace venv: `~/.mempalace/venv/lib/python3.14/site-packages` (config.json → mempalace.venv_site_packages); NOT importable from system python.

**Prevention follow-ups NOT yet done:** (1) gate memdash writes behind Brain's `_project_sync_lock` (defense-in-depth); (2) flush HNSW on graceful shutdown so a clean stop never leaves an unflushed segment.
