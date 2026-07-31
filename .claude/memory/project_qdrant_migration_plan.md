---
name: project_qdrant_migration_plan
description: Plan + decisions for migrating MemPalace vector backend ChromaDB→Qdrant to kill HNSW corruption + scale to prod (2.2M-22M vectors). Read before working on the vector backend.
metadata: 
  node_type: memory
  type: project
  originSessionId: 873784fc-1686-4cf4-9a18-970747445214
---

2026-06-06: Wrote `QDRANT_MIGRATION_PLAN.md` (repo root) — full migration + dual-eval plan to move MemPalace's vector store from embedded ChromaDB to a dedicated **Qdrant** service. Trigger: the audio-overview hang was ChromaDB HNSW corruption (`Error finding id`) in the project-wing search — same class as [[project_chroma_bulk_delete_corruption]]; embedded Chroma's loose HNSW segment files race the 3 writer daemons. Prod corpus will be **100–1000× current ~21.8k drawers = ~2.2M–22M vectors**, so we need a real ANN service, not embedded files.

**WHY Qdrant (user picked it):** same HNSW algorithm but in a WAL-backed transactional service → the "concurrent writer corrupts half-flushed file" mode is structurally impossible. Plus **quantization** (the big RAM lever) + memory-mapping. Embedding stays Brain-side (MLX) → **Qdrant needs NO GPU**.

**KEY VERIFIED FACTS (read the package, not assumed):**
- Backend swap is **config/env-only on Brain's side** — NO hot-path code change. `palace.get_collection()` auto-wraps explicit-embedding backends in `EmbeddingCollection` (palace.py:101) whose `query()` embeds `query_texts` locally before the backend (embedding_wrapper.py:90-92). Brain uses `palace.get_collection` (mempalace_glue.py:448), so the raw `ValueError("qdrant requires query_embeddings")` (qdrant.py:899) NEVER fires for Brain. ⚠️ An earlier subagent wrongly called this a "BLOCKING" issue — it analyzed the raw backend in isolation; verified false.
- Result shapes portable (QueryResult/GetResult have dict-compat shim → Brain's `res.get("documents")[0]` works). Filter dialect ($eq/$in/$and) supported; Brain never uses $or.
- Palace bound to daemons via env at `server_daemons.py:597` (`setdefault MEMPALACE_PALACE_PATH`) — same seam for `MEMPALACE_BACKEND` + `MEMPALACE_QDRANT_*` + embedding env.

**TWO FOOTGUNS:** (1) embedding fn MUST be identical for mine + query or cosine garbage; on this Mac `embedding_device` MUST be mlx|cpu NEVER auto/coreml (CoreML=100% NaN). (2) `~/.mempalace/config.json` palace_path = `/palace` (unrelated 75k palace) — Brain overrides to `/brain` via env; embedding keys still read from that shared file. Don't touch `/palace`.

**QUANTIZATION DECISION — scalar int8 + rescore + 2× oversampling, originals on_disk.** For 384-dim embeddinggemma/cosine: int8 = 4× RAM cut (22M: ~50GB→~12-16GB, fits Spark 128GB easily), recall ~98-99% via rescore. **Binary REJECTED** — 32× but needs ≥1024-dim to hold recall; 384-dim too low → collapses. PQ = fallback only. See [[project_mempalace_venv_patches]] for the patch (MemPalace's qdrant backend creates a BARE collection, no quant → must venv-patch create_collection + query_points).

**DUAL EVAL:** (A) quality = reuse `eval/run.py` KG-Real-Policies canary, A/B chroma-baseline vs qdrant-test by `--label`, ≥3 reps, no regression on retrieval+citation axes. (B) scale = NEW synthetic harness, 2.2M/11M/22M × {f32,int8,binary} × {ram,on_disk}: p50/95/99 latency, recall@10 vs brute-force, RAM, ingest, **kill-during-write→clean-recovery (the acceptance criterion)**. Quality first (gates correctness), scale second (sizes prod config).

**HW:** RAM floor same for both (HNSW property); Qdrant's quant pushes it down 4-32× + isolates from Brain's heap. No GPU either side. Ties to [[project_dgx_spark_warmup_plan]] (co-locate Qdrant on Spark).

Status as of writing: server STOPPED, local Chroma palace still corrupt (rebuild/sqlite_exact pending separately to unblock dev box). Migration NOT executed beyond plan + this patch. Related: [[project_chroma_bulk_delete_corruption]], [[project_mlx_embedding_idea]], [[feedback_defer_to_users_migration_calls]].
