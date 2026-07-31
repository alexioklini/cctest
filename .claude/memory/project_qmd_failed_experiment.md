---
name: project_qmd_failed_experiment
description: qmd (tobi/qmd + Qwen3-Embedding) tried as a MemPalace alternative and rejected — eval was worse; fully reverted. Read before suggesting qmd again.
metadata: 
  node_type: memory
  type: project
  originSessionId: df4a3267-b48f-4c45-866c-8b0ac73cdafd
---

2026-05-28: Tested **qmd** (tobi/qmd — local BM25 + Qwen3-Embedding-0.6B + LLM rerank, CLI-driven) as a swappable retrieval backend alternative to MemPalace+KG. **Result: worse than MemPalace, fully reverted and uninstalled.**

**Eval** (KG-Real-Policies, 15Q, Mistral Medium 3.5, reused gold from `eval/results/20260527T210239_*`):
- qmd backend: **brain mean 0.58**, wins gold 13 / brain 1 / tie 1, Δ −0.35 vs gold.
- MemPalace baseline for comparison: ~0.73–0.80 (reranker-enabled 0.804).
- Same failure mode as MemPalace (wrong-document-choice on retrieval/citation) but **worse**, PLUS a new refusal regression (F1 0.25 — qmd surfaced plausible-but-irrelevant passages for an out-of-corpus topic → model fabricated instead of refusing). Worst buckets: R1/R3 0.2 (wrong doc), C2 0.33, M1 0.2.

**Likely causes** (if ever revisited): qmd's `--json` snippet is narrow (~300 chars) so read-back depends on the model; Qwen3-0.6B embedding space wasn't tuned to this German bank-policy corpus the way MemPalace's bge stack + cross-encoder reranker was. Eval result dirs kept under `eval/results/*qmd*` as the record.

**CORRECTION (2026-05-29):** "fully reverted" was wrong — there were TWO qmd integrations, and only ONE was reverted. (1) qmd as the MemPalace/project-retrieval BACKEND = the experiment, reverted via git checkout (below). (2) A SEPARATE, OLDER qmd-in-MemoryStore integration (agent memory recall/store: `_qmd_query`, `_qmd_debounced_embed`, `_qmd_rpc`, `_qmd_ensure_collection`, MemoryStore.reindex, port-8181 MCP) survived + kept a `com.brain-agent.qmd` launchd service alive. v9.49.1 neutered ALL of #2's entry points to No-Ops (recall→file-scan), unloaded+archived the launchd plist, killed the proc, removed config tool_settings.qmd_query. So qmd is NOW actually gone. Note: this leftover did NOT cause the 2026-05-29 eval=0.50 — that was a corrupt chromadb store (see [[feedback_compile_check_brain_py]] / the rebuild below), separate from qmd.

**Teardown done** (backend-experiment, #1): backend switched back to mempalace; qmd backend code reverted via git checkout (was v9.44.0, back to 9.43.1 — engine/qmd_backend.py deleted, the qmd_query tool/4-sites, resolve_active_tools swap, server.py extraction, daemon mirror seams, /v1/retrieval/config endpoints + admin tab, skill docs, version all gone); `retrieval` block removed from config.json; npm `@tobilu/qmd` uninstalled; `~/.cache/qmd` (2.4GB models+index), `~/.config/qmd`, `~/.mempalace/qmd_corpus`, `~/.local/bin/qmd` wrapper all deleted. MemPalace palace (`~/.mempalace/brain`) untouched.

Related: [[project_reranker_enabled_2026_05_27]] (the MemPalace ceiling this was meant to beat), [[project_brain_vs_opus_eval]] (the harness).
