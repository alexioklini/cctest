---
name: project_contextual_retrieval
description: "Contextual Retrieval implementation — per-chunk LLM context prefix before embedding, verbatim original kept for citation; Brain-side miner fork + retrieval patch"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9d232b00-be49-4464-9583-7c2306933fb0
---

2026-05-27: Implemented Anthropic's Contextual Retrieval to attack the model-independent wrong-document-choice gap (P2/C2/C3, see [[project_eval_snippet_rule_gemma_vs_mistral]]).

**Design (the non-obvious part):** retrieval returns the embedded Chroma `documents` field verbatim to the model AS the citation source (`mempalace_glue.py` `text = doc`). So you cannot naively embed `context+original` — it would pollute citations. Solution: embed `<context>\n\n<original>` into `documents` (what Chroma embeds) but store the verbatim `original` in metadata `original_text`; patched `tool_mempalace_query` to return `meta["original_text"] or doc`. Non-contextual drawers (no original_text) fall back cleanly.

**Files:**
- `engine/contextual_miner.py` — forks `mempalace.miner` process_file loop into Brain (decision: "fork into Brain", not monkeypatch the venv). Reuses upstream `scan_project`/`chunk_text`/`detect_room`/`_build_drawer_metadata`/`build_closet_lines`/`mine_lock` etc. (all importable as `miner.X`). Per chunk: `sidecar_proxy.background_call(model=mistral-small-latest)` generates a 1-2 sentence German context sentence.
- `engine/mempalace_glue.py` ~line 314 — the `text = meta.get("original_text") or doc` patch.
- `server_daemons.py` ~line 1956 — daemon swaps `mp_miner.mine` → `contextual_miner.mine_contextual` when `mempalace.contextual_retrieval.enabled` AND proj dir-name (proj_name = `kg-real-policies`) in `.projects` allowlist.
- `config.json → mempalace.contextual_retrieval {enabled, model, projects}`.

**Live miner is at** `~/.mempalace/venv/lib/python3.14/site-packages/mempalace/miner.py` (NOT the packaging/ build copies). Server is homebrew framework python; mempalace path injected by `engine._ensure_mempalace_importable()` from config `venv_site_packages`.

**Cost:** ~1 mistral-small call per chunk at mine time (~2.3 drawers/s through the provider queue, ~1451 chunks ≈ 10-12 min). Re-mine of policy project (wing `project__f201b24ff6a2`, project display name "Regelwerk der Bank").

**WARNING:** to force a re-mine you must purge old drawers (mtime-gated skip), but do NOT bulk-delete a live collection — see [[project_chroma_bulk_delete_corruption]]. Stop server first or it corrupts the hnsw segment.

**Eval result: NEGATIVE — REGRESSED, do not ship.** Full 15Q (mistral-medium-3.5, mistral judge, 2026-05-27, dir `20260527T181306_disc-none_contextual-retrieval`): brain mean **0.72** vs reranker-on baseline **0.80** → **−0.08**. Hurt the exact wrong-doc questions it targeted: **C3 isms_ziele crashed ~1.0→0.2** (Brain FAILED to retrieve the ISMS-Ziele doc at all and refused "nicht spezifiziert", though the doc is in the corpus + was found fine at baseline). P2 barely moved (0.45). F2 refusal 0.25, R3 0.58 — broad damage.

**Mechanism (why it failed):** embedding `<german-context-prefix>\n\n<original>` DILUTED the vector. Every chunk now opens with boilerplate ("Dieser Ausschnitt stammt aus dem Dokument X, das Y regelt…"), pulling all chunks toward each other in vector space and drowning out the query-specific content. Query "ISMS-Ziele" no longer locks onto the right chunk. Contextual Retrieval made retrieval BLANDER, not sharper — opposite of Anthropic's reported result. Likely causes: (a) prefix too long relative to ~800c chunk, (b) German boilerplate phrasing too uniform across chunks, (c) the all-MiniLM embedder weights the prefix heavily, (d) interaction with the already-on reranker + filename-boost that baseline tuned around.

**v2 (compact-tag prefix `Dokument: X | Abschnitt: Y | Thema: kw`, max_tokens=48) — also NEGATIVE but informative.** Full 15Q (dir `20260527T185159_disc-none_contextual-v2`): brain **0.77** vs baseline 0.80 → **−0.04** (beats v1's 0.72). The compact tag CONFIRMED the dilution hypothesis: it fully fixed v1's collapses — **C3 0.20→1.00, P2 0.45→0.80** (the wrong-doc questions recovered to baseline). BUT it still perturbs retrieval, just relocating the damage: **F2_kreditvergabe −0.58** (refusal→answers — contextual chunk surfaced answerable-looking content), **R1 −0.23**. Net still below baseline. CONCLUSION ACROSS BOTH DESIGNS: index-time context injection (any form) does NOT beat the reranker-on 0.80 baseline — it shuffles which questions win/lose around a slightly-lower mean. The embedding is the wrong lever; touching it is inherently high-variance. **Next bets (lower risk, don't touch embedding): BM25+RRF hybrid (lexical channel for literal terms like ARL-codes/ISMS-Ziele), Qwen3-Reranker-4B swap (upgrade the proven +0.08 lever), rerank score-floor (fixes refusal F1/F2 by refusing when top score low).**

**Decision: revert** the daemon swap (keep flag default-off) + the retrieval `original_text` patch is harmless to leave (no-op without contextual drawers) but should be reverted for cleanliness. Re-mine policy project normally to restore baseline. Adds to the long list of prompt/index tweaks that don't beat the reranker-on baseline — the −0.20 gap is stubbornly retrieval/citation and NOT fixable by index-time context injection of this form. See [[project_eval_snippet_rule_gemma_vs_mistral]], [[project_reranker_enabled_2026_05_27]], [[project_doc_refinement_eval]] (same lesson: window-based chunker can't be helped by prose injection).
