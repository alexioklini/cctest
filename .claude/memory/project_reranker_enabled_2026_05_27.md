---
name: project_reranker_enabled_2026-05-27
description: "Cross-encoder reranker re-enabled — overturns prior \"off is better\" verdict; brain mean 0.729→0.804"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ca7eafb-960a-4ccc-9737-da9c99b1c5d3
---

2026-05-27: Re-enabled the MemPalace cross-encoder reranker (`config.json → mempalace.reranker.enabled=true`, BAAI/bge-reranker-v2-m3) AND fixed a latent bug that made it inert in project mode.

**The bug:** `engine/mempalace_glue.py` `fetch_n` only widened (×4) when `_needs_user_filter`, which is False in project context. So with reranker on, only `n_results` candidates were fetched and the rerank pass (`pool=raw_results[:top_k_in]`) had nothing extra to re-order — the right doc (vector-rank ~19 for a 5-result query) never entered the pool. Fix: widen `fetch_n` to `top_k_in` (40) when reranker enabled; the final `deduped[:n_results]` trim keeps the wide fetch internal.

**Eval (15Q, mistral-medium-3.5, disc=none, mistral judge, reused Opus gold):** brain mean **0.729→0.804 (+0.075)**, gap to gold halved −0.20→−0.12. Wins brain 1→3.
- Per-bucket: citation +0.24, precision +0.12, retrieval +0.11, multi_doc +0.02, **refusal −0.11**.
- Fixed exactly the wrong-doc-choice failures from [[project_mempalace_multi_source_coverage]] / [[project_eval_snippet_rule_gemma_vs_mistral]]: **C3 +0.50, P2 +0.35, C2 +0.27**.

**The trade-off:** refusal regressed (F1 geldwaesche 0.58→0.27). Reranker surfaces plausible-but-irrelevant passages on out-of-corpus questions → Mistral fabricates instead of refusing. This is exactly what the prior "reranker off" verdict (eval 2026-05-14) was protecting against. Net still clearly positive. **Follow-up not yet done:** a rerank-score floor (drop passages below an absolute cross-encoder score) to claw back F1/F3 refusal discipline.

**Why this overturns 2026-05-14's "off is better":** that verdict predated the v9.34 snippet rule AND the `gold_context.md` room-config fix (`general`→`artifacts`, same session — the wing has one room `artifacts`, 1691 drawers, not `general`/1372). Reranker stays loaded in-memory after ~12s cold-load; skip-gate (≥0.20 filename boost) correctly bypasses it when lexical signal is strong (verified: M1 skipped).

Code change tracked in git (`engine/mempalace_glue.py`); the `enabled=true` flip lives in gitignored `config.json` (persists on disk). Skip-gate is the load-bearing safety: never remove it.
