# MemPalace: ChromaDB → Qdrant Migration + Dual-Eval Plan

**Status:** PROPOSED — nothing executed. Server currently stopped.
**Date:** 2026-06-06
**Author:** Claude (Opus 4.8) for Alexander
**Goal:** Eliminate the HNSW file-corruption failure mode and scale the vector store to the
production corpus (100–1000× current ≈ 2.2M–22M vectors) by moving MemPalace's vector
backend from embedded ChromaDB to a dedicated Qdrant service — validated by a quality eval
(does retrieval stay correct?) and a scale eval (does it hold up at production size?) before
any production cutover.

---

## 0. Why (one paragraph)

The corruption we hit is structural: ChromaDB runs **embedded inside Brain's process** and
persists its HNSW index as loose segment files (`data_level0.bin` / `link_lists.bin` /
`index_metadata.pickle`) flushed asynchronously. Three writer daemons + chat queries race on
those files; a crash or concurrent write mid-flush leaves an inconsistent graph →
`Error finding id` / SIGSEGV. Every mitigation we have (`_palace_write_lock`, the quarantine
validator, the no-SIGKILL rule, periodic rebuilds) *manages* this; none removes it. Qdrant
runs the **same HNSW algorithm** but inside a transactional, WAL-backed service process, so
the "concurrent writer corrupts a half-flushed file" mode cannot occur. It also adds
**quantization** (4×–32× RAM reduction) and **memory-mapping**, which is what makes the
22M-vector end affordable. Embedding stays Brain-side on MLX, so **Qdrant needs no GPU.**

---

## 1. Verified facts this plan rests on

These were confirmed by reading the installed package + Brain code (not assumed):

| Fact | Evidence |
|---|---|
| Backend selection is **config/env only**, no Brain code change to *select* | `palace.resolve_backend_name()` honors explicit arg → config → `MEMPALACE_BACKEND` env → disk marker → default `chroma` (registry.py:183) |
| Brain's `query_texts=` calls **work on Qdrant** (NOT the blocker an earlier analysis claimed) | `palace.get_collection()` wraps explicit-embedding backends in `EmbeddingCollection` (palace.py:101); its `query()` embeds `query_texts` locally before the backend call (embedding_wrapper.py:90-92). Brain uses `palace.get_collection` (mempalace_glue.py:448), so it gets the wrapper. The raw `ValueError("qdrant requires query_embeddings")` (qdrant.py:899) only fires on direct-backend calls Brain never makes. |
| Brain's result indexing is **portable** | Qdrant returns `QueryResult`/`GetResult` dataclasses with a dict-compat shim (`.get()`, `["ids"]`), matching Brain's `chroma_res.get("documents")[0]` pattern (base.py `_DictCompatMixin`). |
| The metadata filter dialect Brain uses is **supported** | Brain uses `$eq`, `$in`, `$and` for wing/room scoping; all supported by qdrant.py. Brain never uses `$or` (the one Qdrant only does via local fallback). |
| Embedding is **Brain-side**, 384-dim `embeddinggemma_300m` on MLX | `get_embedding_function()` → dim 384; the wrapper embeds before sending vectors to the DB. |
| Palace path is bound to the daemons via env | `server_daemons.py:597` `os.environ.setdefault("MEMPALACE_PALACE_PATH", palace_path)` — same seam where backend + Qdrant env belong. |

### ⚠️ Two footguns to design around (found during verification)

1. **Embedding-config consistency.** Embedding model/device is read from
   `~/.mempalace/config.json` (`embedding_model: embeddinggemma`, `embedding_device: mlx`),
   **not** set by Brain via env. The mine path and the query path **must use the identical
   embedding function** or vectors won't match (cosine garbage). On this Mac, `embedding_device`
   MUST be `mlx` or `cpu` — **never `auto`/`coreml`** (CoreML EP = 100% NaN embeddings here, the
   documented hard gotcha). The migration re-mine and all queries must run with the same
   `MEMPALACE_EMBEDDING_MODEL=embeddinggemma` + `MEMPALACE_EMBEDDING_DEVICE=mlx`.

2. **Two palace configs disagree on path.** `~/.mempalace/config.json` `palace_path` =
   `/Users/alexander/.mempalace/palace` (an *unrelated* 75k-drawer palace). Brain overrides to
   `/Users/alexander/.mempalace/brain` via the env setdefault. Embedding settings, however, are
   read from that file regardless of path. Keep this in mind: set palace path via env, but the
   embedding keys come from the shared file. Do NOT touch the `/palace` palace.

---

## 2. Hardware requirements (Qdrant vs ChromaDB)

Embedding is Brain-side (MLX) → **neither DB needs a GPU.** RAM is the driver and it's an
HNSW property, so the *floor* is the same; Qdrant's quantization is what lowers it.

| Corpus (384-dim) | float32 vectors | + HNSW (~1.5×) | Practical RAM, unquantized | Qdrant int8 | Qdrant binary |
|---|---|---|---|---|---|
| 21.8k (today) | ~34 MB | ~50 MB | trivial | trivial | trivial |
| 2.2M (100×) | ~3.4 GB | ~5 GB | 8–16 GB | ~2–4 GB | ~0.5–1 GB |
| 22M (1000×) | ~34 GB | ~50 GB | 64–96 GB | ~12–16 GB | ~2–4 GB |

- **ChromaDB:** index lives *inside Brain's process* → competes with Brain's heap; float32 only
  (no quantization lever). Only HW advantage: no extra service.
- **Qdrant:** own process (isolated RAM); **scalar/binary quantization 4×–32×** + `on_disk`
  memory-mapping to page vectors from SSD. CPU: 4–8 cores comfortable into the millions.
- **DGX Spark (128 GB unified):** 22M fits unquantized with headroom; with int8 it's a rounding
  error. Spark is well-matched to a **co-located** Qdrant (localhost, no network hop).

**Takeaway:** same RAM floor, but Qdrant lets you push it down 4–32× and isolates it from Brain
— at the cost of running one service. That, plus crash-safety, is the case for Qdrant beyond
just fixing corruption.

---

## 3. Migration plan

### Phase A — Stand up Qdrant (no Brain changes yet)

1. **Install client + service.**
   - `~/.mempalace/venv/bin/pip install qdrant-client` (the backend's only missing dep).
   - Run Qdrant as a service. Dev/local: Docker (`qdrant/qdrant`, ports 6333/6334) or the
     native binary. Prod: container on the app host (co-located → localhost, no TLS hop) or a
     managed instance.
   - Pin a version; record it (Qdrant storage format is version-sensitive).

2. **Decide deployment shape** (affects ops, not code):
   - **Local/dev + DGX Spark:** co-located, `http://localhost:6333`, no api_key.
   - **Prod multi-host:** dedicated host, api_key set, `MEMPALACE_QDRANT_API_KEY`.

3. **Smoke test the service** independent of Brain: create a throwaway collection, upsert a
   handful of 384-dim vectors, query, delete. Confirms the service + client work before
   involving MemPalace.

### Phase B — Wire MemPalace to Qdrant (config + the env seam)

The backend is config-selected; the work is making sure **both** the daemons (write) and the
tool path (query) pick Qdrant **and** the same embedding fn.

4. **Set the selection + connection + embedding env at the daemon seam.** At
   `server_daemons.py:597` (and any other place that binds the palace), alongside the existing
   `MEMPALACE_PALACE_PATH` setdefault, set:
   ```
   MEMPALACE_BACKEND=qdrant
   MEMPALACE_QDRANT_URL=http://localhost:6333     # or prod URL
   MEMPALACE_QDRANT_API_KEY=...                   # prod only
   MEMPALACE_EMBEDDING_MODEL=embeddinggemma
   MEMPALACE_EMBEDDING_DEVICE=mlx                 # NEVER auto/coreml on this Mac
   ```
   Prefer driving these from `config.json → mempalace` (add `backend` + `qdrant_*` keys, which
   `MempalaceConfig` already reads) so it's declarative, not hard-coded. The launchd plist
   `EnvironmentVariables` block is the other valid home for the prod values.

5. **No change needed** to `mempalace_glue.py` query/get/delete logic (verified §1). The only
   code touch is the env/config wiring in step 4 + the skill-doc + CLAUDE.md updates (the
   architecture diagram says "ChromaDB"; per the standing skill-currency rule this must change
   in the same commit, version bumped in both places).

### Phase C — Move the data (re-mine, don't migrate vectors)

6. **Re-mine fresh rather than copy the Chroma index.** Per prior practice
   ([[feedback_defer_to_users_migration_calls]]) the palace is re-derivable and a fresh re-mine
   converges cleaner than a data-preserving copy. With the backend = qdrant env in place:
   - Point a **scratch** palace path at a new dir (don't overwrite `/brain` until validated).
   - Clear the chat-sync cursors so the daemons re-mine from live sources, OR run the miner
     against the project input folders directly.
   - Let the three daemons populate Qdrant. On MLX (~204 docs/s) the current 21.8k is ~minutes;
     a production re-mine is sized accordingly (plan an offline window).
   - For the **scale eval** (§4.B) we generate synthetic vectors instead of re-mining 22M real
     docs — see below.

7. **Keep Chroma as the rollback.** Don't delete `~/.mempalace/brain` (Chroma) until the eval
   passes and a soak period elapses. Rollback = unset `MEMPALACE_BACKEND` (→ chroma) + restart.

### Phase D — Configure for scale (prod only)

8. **Enable scalar int8 quantization** (the chosen level — see §4.C). Enable AFTER the quality
   eval passes unquantized, so "quantization recall loss" is isolated from "backend behavior."
9. **Tune HNSW build params** (`m`, `ef_construct`, search `ef`) against the scale eval's
   recall/latency curve — the production-size knobs, not defaults.

### Phase D′ — The quantization patch (DECIDED: patch the MemPalace backend)

MemPalace's Qdrant backend creates a **bare** collection — `{"vectors": {"size": N,
"distance": "Cosine"}}` (qdrant.py `_QdrantClient.create_collection`, ~line 404) — with **no
quantization, no `on_disk`, no HNSW tuning**, talking raw Qdrant REST (not qdrant-client). So
int8 must be injected via a **venv patch**, joining the tracked patch set (span +
MLX → now +qdrant-quant) that must be re-applied on every `pip install --upgrade mempalace`
([[project_mempalace_venv_patches]]). Mark every edit `# BRAIN-PATCH`.

**Two edit sites:**

1. **Collection create** (`_QdrantClient.create_collection`, ~qdrant.py:404) — extend the PUT
   body so new collections are born quantized + originals on disk:
   ```python
   body={
       "vectors": {"size": int(dimension), "distance": "Cosine", "on_disk": True},
       "quantization_config": {
           "scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}
       },
   }
   ```
   `on_disk:True` keeps full float32 vectors memory-mapped on SSD (used only for rescore);
   `always_ram:True` keeps the small int8 index hot in RAM. Small-thing-hot, big-thing-cold.

2. **Search params** (`_QdrantClient.query_points`, ~qdrant.py:442) — add rescore + oversampling
   so int8 search recovers near-float32 recall:
   ```python
   body = {
       "query": vector, "limit": int(limit),
       "with_payload": True, "with_vector": bool(with_vector),
       "params": {"quantization": {"rescore": True, "oversampling": 2.0}},
   }
   ```

**Make it config-driven, not hard-coded** if practical: read level/quantile/oversampling from
`config.json → mempalace.qdrant` so the scale eval can A/B float32 vs int8 vs (rejected) binary
without re-patching. Default OFF until the quality eval passes, then ON for prod.

**Patch backup discipline:** save clean + patched `qdrant.py` next to the existing
`~/.mempalace/span_patch_backup/*` so the diff is re-derivable after an upgrade.

---

## 4. Dual evaluation

Two **different** questions, two evals. Do not conflate them.

### 4.A Quality eval — "does retrieval stay correct?" (reuse `eval/run.py`)

The existing **KG-Real-Policies** harness is the tool. It's backend-agnostic by construction:
the backend is whatever the venv/config points at, so the *same* harness runs against either.

- **Harness:** `eval/run.py` — 15-question canary, Brain (live, with reranker) vs. Opus-4.7
  gold (vanilla MemPalace MCP), judged by Mistral Medium 3.5. Axes (eval/rubric.md): retrieval,
  precision, citation, refusal, composition.
- **Corpus:** the fixed test project (58 German bank-policy PDFs, wing `project__f201b24ff6a2`,
  ~1,691 drawers). Re-mine this project into the Qdrant scratch palace.
- **A/B procedure:**
  1. **Baseline (Chroma):** `BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --label chroma-baseline`
     → `eval/results/<ts>_disc-none_chroma-baseline/summary.md`. (Run on the *rebuilt* Chroma
     palace, not the corrupt one, so the baseline is fair.)
  2. **Switch backend** (Phase B env) + re-mine the test project into Qdrant.
  3. **Candidate (Qdrant):** `... python3 eval/run.py --label qdrant-test` → its own summary.
  4. **Compare** the two `summary.md` files; optionally re-judge both under one rubric with
     `eval/judge_mistral.py` for an apples-to-apples score.
- **What MUST stay constant** between runs: questions.json, rubric.md, brain-model
  (`CLIProxyAPI/mistral-medium-3.5`), gold model, judge model, **embedding fn**. Only the
  backend varies.
- **Pass bar:** Qdrant retrieval/precision/citation scores **≥ Chroma baseline within eval
  variance** (these evals are noisy — treat <~0.05 as noise; require no regression on the
  retrieval + citation axes specifically, since those are the ones a backend swap could move).
  Run ≥3 reps each to separate signal from variance (prior eval discipline).
- **Also enable the reranker** path in both runs (it's part of "Brain as deployed") so we test
  the real stack, not a stripped one.

### 4.B Scale eval — "does it hold at production size?" (new, synthetic)

`eval/run.py` validates *correctness* on ~1.7k drawers; it says nothing about 22M-vector
*latency/RAM/recall*. This is a separate, new harness (no LLM, pure retrieval mechanics):

- **Build:** generate N synthetic 384-dim vectors (N ∈ {2.2M, 11M, 22M}) — random unit vectors
  are fine for latency/RAM; for **recall** measurement, plant known nearest-neighbor clusters
  so we have ground truth (compute true top-k by brute force on a sample, compare to ANN top-k).
- **Load** them into Qdrant via the same `EmbeddingCollection`/backend path Brain uses (so we
  test the real write path, not the raw client).
- **Measure, per N and per config (unquantized / int8 / binary; in-RAM / on_disk):**
  - **p50/p95/p99 query latency** under single + concurrent load (simulate daemon writes
    happening during queries — the exact condition that corrupted Chroma).
  - **Recall@k** vs. brute-force ground truth (does ANN miss real neighbors? does quantization
    hurt?).
  - **Resident RAM** of the Qdrant process at each N/config (validate the §2 table empirically).
  - **Ingest throughput** + behavior under a kill-during-write (prove no corruption — the whole
    point: SIGTERM/SIGKILL Qdrant mid-ingest, restart, confirm clean recovery).
- **Pass bar:** p95 query latency acceptable for chat UX (target < ~150 ms at the planned N,
  with quantization if needed); recall@10 ≥ ~0.95 vs. brute force; RAM within the target host's
  budget; **zero corruption after kill-during-write** (the acceptance criterion that justifies
  the whole migration).
- **Output:** a latency/recall/RAM matrix across N × config → picks the production quantization
  + HNSW params for Phase D.

### 4.C Quantization level — DECIDED: scalar int8 + rescore + 2× oversampling, originals on disk

For **384-dim `embeddinggemma`, cosine, 2.2M–22M vectors**:

| Level | RAM vs f32 (@22M) | Recall | Verdict |
|---|---|---|---|
| None (f32) | 1× (~50 GB) | 100% | baseline only — too much RAM at top end |
| **Scalar int8** ★ | **4× less (~12–16 GB)** | **~98–99% w/ rescore** | **CHOSEN** |
| Product (PQ) | 8–16× less | ~90–95%, slower | fallback only if int8 won't fit |
| Binary | 32× less (~2 GB) | ~95% **only at ≥1024-dim** | **REJECTED** — 384-dim too low, recall collapses |

**Rationale:** int8's 4× RAM cut puts 22M at ~12–16 GB (fits Spark's 128 GB with huge headroom,
fits modest cloud boxes). Recall stays ~98–99% because Qdrant searches the int8 index fast then
**rescores** top candidates against full-precision vectors (kept `on_disk`, so not in RAM).
**Binary is ruled out by the model:** 1-bit-per-dim needs high-dim (1024+) to preserve recall;
at 384 dims it loses too much. This is why quantization choice is model-dependent, not universal.

**The scale eval (4.B) must empirically confirm** int8 recall@10 ≥ ~0.95 on the real-distribution
vectors before prod cutover — random synthetic vectors *understate* quantization recall (real
embeddings cluster, which int8+rescore handles better than uniform noise; but verify, don't
assume). Test f32 vs int8 (vs binary, to *prove* it regresses) in the N×config matrix.

### Eval sequencing

Quality eval first (cheap, decisive on correctness) → if it regresses, stop and diagnose before
investing in scale testing. Scale eval second (sizes the prod config). Both must pass before
cutover.

---

## 5. Rollback

- **Backend swap is reversible by config:** unset `MEMPALACE_BACKEND` (or set `chroma`) +
  restart → Brain is back on the Chroma palace at `~/.mempalace/brain` (kept intact through the
  whole migration).
- **No vector data is destroyed** by the migration (re-mine writes to a *scratch* path; Chroma
  palace untouched until soak passes).
- Keep the Chroma palace + the `_quarantine-corrupt-20260606` segments until ≥1 week clean on
  Qdrant.

---

## 6. Risks / open questions

| Risk | Mitigation |
|---|---|
| Qdrant backend in this MemPalace version is less battle-tested than Chroma | Quality eval gates correctness; scale eval gates the kill-during-write recovery claim explicitly. |
| Embedding-fn mismatch between mine and query → silent cosine garbage | Pin `MEMPALACE_EMBEDDING_MODEL/DEVICE` identically for daemons + tool path; NaN-check vectors before re-mine (the CoreML trap). |
| `EmbeddingCollection` wrapper not applied on some Brain call site → raw `query_texts` ValueError | Audit every `get_collection`/`_gc` call in `mempalace_glue.py` confirms all go through `palace.get_collection` (the wrapping entry point). Re-verify after any refactor. |
| Quantization recall loss | Eval unquantized first; enable int8 only after measuring recall delta on the scale eval. |
| Operational weight of a new service in prod | Co-locate on the app host (localhost); snapshot-based backups; document in deployment runbook. |
| Skill-doc / CLAUDE.md drift (architecture says "ChromaDB") | Update `03-storage.md`, `05-internals.md`, CLAUDE.md MemPalace section + version bump in the same commit (standing rule). |

---

## 7. Concrete next actions (in order)

1. [ ] Rebuild OR sqlite_exact the **local** Chroma palace so the dev box works again + gives a
       fair quality-eval baseline. (Independent of the migration; unblocks daily use.)
2. [ ] Phase A: `pip install qdrant-client`; run Qdrant locally; service smoke test.
3. [ ] Phase B: add `backend` + `qdrant_*` + embedding env at the daemon seam (config-driven).
4. [ ] Phase C: re-mine the **test project** into a Qdrant scratch palace (NOT `/brain`).
5. [ ] Eval 4.A: Chroma baseline vs Qdrant candidate, ≥3 reps, compare retrieval/citation axes.
6. [ ] Eval 4.B: build the synthetic scale harness; run N×config matrix incl. kill-during-write.
7. [ ] Phase D: pick quantization + HNSW params from 4.B.
8. [ ] Cutover: point `/brain`'s daemons at Qdrant, full re-mine, soak ≥1 week, keep Chroma as
       rollback. Update skill docs + CLAUDE.md + version.
