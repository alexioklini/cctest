---
name: QMD removal backlog
description: Remove all dead QMD code — superseded by MemPalace, spread across 6+ files, needs careful session
type: project
originSessionId: 47be53dd-51ce-4f8a-aa5a-be8e95edd817
---
Strip all QMD (query-memory-daemon) code from the codebase. MemPalace replaced QMD entirely; the index keeper thread in server.py is already commented as dead code.

**Why:** QMD is fully superseded by MemPalace. All memory reads/writes now go through MemPalace. QMD code is dead weight and adds confusion.

**How to apply:** Dedicate a focused session. Files affected:
- `server.py` — `_QMD_PORT`, `_QMD_PID_FILE`, `_qmd_index_keeper()` function (lines ~3929–4105), `_qmd_debounced_embed()` call sites (lines 3061, 3079, 3868)
- `handlers/admin.py` — `_find_qmd()`, `_qmd_trigger_update()`, `_qmd_run()`, `_qmd_register_collection()`, `_qmd_remove_collection()`, `_is_qmd_running()`, `_qmd_collections()`, ~20 call sites
- `engine/memory/store.py` — `_QMD_URL`, `_QMD_HEADERS`, `_qmd_*` globals, `_qmd_rpc()`, `_qmd_init_session()`, `_qmd_ensure_collection()`, `_qmd_debounced_embed()`, `_qmd_query()`, ~40 references
- `engine/memory/autodream.py` — `_qmd_debounced_embed()` call sites, `_QMD_IGNORE_FILES` filter, ~15 references
- `engine/tools/files.py` — `_maybe_qmd_reindex()` function
- `engine/loop.py` — comment reference
- `frontends/client.py` — `get_qmd_docs()` method
- `claude_cli.py` shim — `_qmd_debounced_embed`, `_maybe_qmd_reindex` re-exports
- `packaging/com.brain-agent.qmd.plist` — launchd plist for QMD daemon

Verify: after removal, memory writes in `autodream.py` and `store.py` must still reach MemPalace correctly.
