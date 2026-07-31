---
name: project_qdrant_live_int8
description: LIVE STATE 2026-06-09 — MemPalace vector backend is Qdrant + int8 quant (shipped v9.97.0). Read before touching the vector backend or diagnosing mempalace retrieval.
metadata: 
  node_type: memory
  type: project
  originSessionId: 87eeee2c-1ca4-455e-b206-36ff0727965f
---

2026-06-09 (shipped v9.97.0, committed 3848254, pushed to main): MemPalace's vector backend is now **Qdrant + scalar int8 quantization**, executing the [[project_qdrant_migration_plan]]. Replaces the `sqlite_exact` interim (brute-force exact KNN, no ANN — see [[project_devbox_sqlite_exact_switch]]) which itself replaced corruption-prone embedded ChromaDB. WHY Qdrant: restores a real ANN index without re-exposing the embedded-Chroma HNSW corruption ([[project_chroma_bulk_delete_corruption]]) — Qdrant is WAL-backed/transactional so the concurrent-writer-corrupts-half-flushed-segment mode is structurally impossible.

## Live wiring (this Mac)
- **Qdrant binary v1.18.2** native arm64 (NO Docker — not in brew, downloaded official `qdrant-aarch64-apple-darwin.tar.gz`) at `~/.qdrant/qdrant`, storage `~/.qdrant/storage`, REST `:6333` / gRPC `:6334`. **NOW under launchd** (2026-06-19): `~/Library/LaunchAgents/com.brain-agent.qdrant.plist` (RunAtLoad+KeepAlive, `--config-path ~/.qdrant/config.yaml`, WorkingDirectory `~/.qdrant`, log `~/.qdrant/qdrant.log`) — survives reboot, auto-restarts. config.yaml pins storage_path/snapshots_path + host 127.0.0.1 + http 6333/grpc 6334. (Was manual nohup before; had silently stayed DOWN after a clean SIGTERM shutdown 2026-06-18 19:18 until restarted under launchd 2026-06-19 — that's why it's a supervisor now.)
- **Backend selected via launchd plist `EnvironmentVariables`** (same seam as embedding): `MEMPALACE_BACKEND=qdrant`, `MEMPALACE_QDRANT_URL=http://localhost:6333`, `MEMPALACE_QDRANT_QUANTIZATION=int8`, `MEMPALACE_EMBEDDING_DEVICE=mlx`, `MEMPALACE_EMBEDDING_MODEL=embeddinggemma`. (NOT in repo config.json — that's gitignored + the migration note keeps qdrant conn-config out of it on purpose.)
- **palace_path** = `~/.mempalace/brain-qdrant` (config.json) — holds the `qdrant_backend.json` marker + `knowledge_graph.sqlite3`; **vectors live in the Qdrant service**, not this dir. Collections: `mempalace_db0eee7a22b04148_mempalace_drawers` (+ `_closets`), born quantized (int8 + on_disk).
- **Embeddings stay Brain-side MLX** (`embeddinggemma-300m`); Qdrant needs no GPU. NO Brain hot-path code change — `palace.get_collection` auto-wraps the explicit-embedding backend so `query_texts` embed locally first; `mempalace_glue` query/get/delete unchanged.
- Quant knobs = the venv `backends/qdrant.py` `# BRAIN-PATCH` ([[project_mempalace_venv_patches]] patch 3).

## Validated (don't re-litigate)
- Re-mined fresh (16882 drawers ≈ baseline 16893) rather than copying vectors.
- int8+rescore recall: self-match 1.0000 (5/5). Eval (KG-Real-Policies, gold reused, mistral-medium judge, auto routing): sqlite_exact 0.80 → Qdrant f32 0.79 → **int8 0.84** — all within mistral-small run-to-run variance ⇒ no measurable quality cost, 4× RAM. (The recurring low scores in those runs are mistral-small synthesis flukes — F1_geldwaesche "fabricated", R1 once collapsed to 0.08 then 0.80/0.72 on re-run — NOT backend/retrieval failures; retrieval was correct every time.)

## Gotchas
- **Boot-time `PalaceNotFoundError: .../brain-qdrant/qdrant_backend.json` is HARMLESS** — a read-path query racing the first daemon write before the marker exists. Self-resolves once a daemon writes (create=True). Only worry if it persists AND the marker file is missing.
- **Enabling quant on an existing collection does nothing** — collections must be BORN quantized. Recipe: drop both collections (`DELETE /collections/<name>`) + `rm qdrant_backend.json` + `DELETE FROM chat_mempalace_sync` in agents/main/chats.db + graceful restart → daemons recreate quantized + re-mine.
- **Rollback** = remove the 2 qdrant plist env keys + restart (→ falls back; `brain-sqlite` palace untouched on disk as the sqlite_exact rollback).
- ALWAYS graceful restart (launchctl bootout/bootstrap), NEVER SIGKILL ([[feedback_never_sigkill_brain]]).

## NOT done yet
- **Scale eval** (Phase D′/4.B): synthetic 2.2M–22M vectors, HNSW `m`/`ef_construct`/`ef` tuning, kill-during-write→clean-recovery acceptance. This migration validated CORRECTNESS at 17k only. The 4× RAM lever's real test is at prod size.
- The plist env + venv patch + config.json palace_path are all machine-local/gitignored — re-apply on a fresh clone or mempalace upgrade. (Qdrant launchd plist `com.brain-agent.qdrant` + `~/.qdrant/config.yaml` are also machine-local — re-create on a fresh box.)

Related: [[project_qdrant_migration_plan]], [[project_mempalace_venv_patches]], [[project_devbox_sqlite_exact_switch]], [[project_mlx_embedding_idea]], [[project_chroma_bulk_delete_corruption]], [[feedback_never_sigkill_brain]].
