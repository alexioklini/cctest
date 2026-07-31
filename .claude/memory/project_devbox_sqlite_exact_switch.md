---
name: project_devbox_sqlite_exact_switch
description: Dev box switched from ChromaDB to sqlite_exact backend (2026-06-06) to kill HNSW corruption. CURRENT live state of the MemPalace backend. Qdrant switch planned next.
metadata: 
  node_type: memory
  type: project
  originSessionId: 873784fc-1686-4cf4-9a18-970747445214
---

2026-06-06: Dev-box MemPalace backend switched **ChromaDB → sqlite_exact** to permanently kill the HNSW corruption that was hanging audio overviews (`Error finding id` inside chromadb_rust_bindings during `tool_mempalace_query`). sqlite_exact = exact cosine over float32 blobs, **NO index → corruption structurally impossible**. Fine at dev scale (~17k drawers); NOT for prod (brute-force, linear) — Qdrant is next.

**LIVE STATE NOW:** palace_path = `~/.mempalace/brain-sqlite` (16,884 drawers, re-mined fresh, ABOVE the old 16,370). backend = `sqlite_exact` via `MEMPALACE_BACKEND` env in the launchd plist. Server up, retrieval verified (semantic query returns relevant hits), 0 errors. Old chroma palace `~/.mempalace/brain` (corrupt) kept as rollback + `_quarantine-corrupt-20260606` dirs.

**HOW IT WAS DONE (reusable recipe):** (1) fresh empty palace dir (avoids `BackendMismatchError` — get_collection refuses a dir holding another backend's artifacts); (2) set `MEMPALACE_BACKEND` + `MEMPALACE_EMBEDDING_MODEL=embeddinggemma` + `MEMPALACE_EMBEDDING_DEVICE=mlx` in the plist `EnvironmentVariables` (NOT config.json — its `backend` key only applies when cfg.palace_path matches, and it's `/palace` not our brain dir); (3) repoint Brain config.json palace_path; (4) clear `chat_mempalace_sync` cursors (backed up) → forces full re-mine; (5) graceful restart → 3 daemons re-mine. Dry-run the backend round-trip BEFORE restart.

**LATENCY REALITY:** warm query ≈ 80ms (9ms MLX embed + 71ms exact cosine over 16.9k docs). The scary "7.3s" in testing was MLX model COLD-LOAD in a standalone process (6.5s, paid once) — NOT per-query, NOT the server path. MLX embeddings ARE needed (semantic search = embed query then compare). The 71ms cosine grows LINEARLY with corpus → why sqlite_exact is dev-only.

**NEXT (planned, not done): switch dev box to Qdrant** — `QDRANT_DEVBOX_SWITCH.md` (native macOS binary via Homebrew, NOT Docker; launchd service like sidecar/SearXNG; same fresh-dir + env-seam recipe; quant OFF for first cutover then optional int8). Prod plan + dual eval in [[project_qdrant_migration_plan]]; int8 venv patch already applied ([[project_mempalace_venv_patches]] patch #3).

Related: [[project_chroma_bulk_delete_corruption]], [[project_qdrant_migration_plan]], [[feedback_never_sigkill_brain]], [[feedback_defer_to_users_migration_calls]].
