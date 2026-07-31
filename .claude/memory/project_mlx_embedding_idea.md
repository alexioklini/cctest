---
name: project_mlx_embedding_idea
description: BUILT 2026-06-06 — MemPalace embedding model (embeddinggemma-300m) hosted on MLX (Metal GPU) instead of onnxruntime-CPU; ~14x faster re-embeds. Patch details in project_mempalace_venv_patches.
metadata: 
  node_type: memory
  type: project
  originSessionId: 65999efa-350a-483a-acea-6f09428384a9
---

**BUILT 2026-06-06** (was an idea; user said do it after a 2h CPU re-embed proved too slow). MLX backend for embeddinggemma-300m added as `EmbeddinggemmaMLX` in the venv `embedding.py` (patch #2 in [[project_mempalace_venv_patches]]), config `embedding_device: "mlx"`. Measured **14x speedup** (204 vs 14.5 docs/s) → full brain-palace re-mine ~3min instead of ~2h (14417→14459 drawers). Parity cos(MLX_bf16, ONNX_q8)=0.996, same EF `name()` so swaps are transparent to ChromaDB. Model `mlx-community/embeddinggemma-300m-bf16`; deps `mlx mlx-embeddings` in the brain venv. Original assessment below (kept for rationale).

2026-06-06: user idea during the MemPalace 3.4.0 upgrade — host the embedding model via MLX (like our chat models) for speed. Assessment: **worth it later, not now.**

MemPalace 3.4.0's embedder is a self-contained ChromaDB EmbeddingFunction class (`EmbeddinggemmaONNX` in `mempalace/embedding.py`): implements `name()`, `__call__(input)`, `embed_query`, `embed_documents`; wraps an onnxruntime `InferenceSession`. A sibling `EmbeddinggemmaMLX` producing **384-dim L2-normalized** vectors with the **SAME `name()` string** (`embeddinggemma_300m`) would be a true drop-in — no re-embed, no schema change, reads stay EF-compatible.

**Why it's attractive:** upstream acceleration is ONNX-EP-only (`_PROVIDER_MAP` = cuda/coreml/directml/cpu — no MPS/MLX). The only GPU path (CoreML) is BROKEN on this Mac — produces 100% NaN embeddings (see [[project_mempalace_venv_patches]]) — so we're pinned to onnxruntime-CPU. MLX would be the only real GPU route on Apple Silicon.

**Why NOT now:**
1. It's an OUT-OF-TREE custom EF = another venv patch (re-applied every pip upgrade, like the span patch) + a model-port effort. Adds to the patch-ledger tax.
2. Embedding is a one-time-per-corpus cost, not a hot path. The slow thing is the INITIAL re-embed of ~14k drawers (rebuild-index). Steady state = mining a few new drawers + embedding ONE query per turn → CPU-fine. MLX mainly speeds rebuilds, which are rare.
3. Name-stability is load-bearing: if MLX `name()` differs OR its 384-dim output isn't numerically ≈ the ONNX path, you get a forced full re-embed or silent retrieval drift.

**If we ever build it:** gate behind a parity test — cosine(MLX-vec, ONNX-vec) on the same text must be ≈1.0 before trusting it in place; keep `name()` identical; treat as an optional rebuild accelerator, not the default mining/query embedder. Same pattern as the oMLX chat-model hosting work.
