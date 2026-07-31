---
name: project_refactor_tier_c_complete
description: 2026-05-23 — module-extraction refactor COMPLETE through Tier C AND Tier E; brain.py 25182→12334 (−51.0%, more than halved)
metadata: 
  node_type: memory
  type: project
  originSessionId: c030a6eb-c9a0-4b5b-8923-08455579bf7f
---

The big brain.py module-extraction refactor is **fully complete** (Phases 1–4, all of Tier C) as of 2026-05-23. 20 extractions, 0 reverts. brain.py 25,182 → 16,950 LOC (−32.7%). Source of truth = `REFACTOR_REPORT.md` (published HTML at github.com/alexioklini/cctest/blob/main/REFACTOR_REPORT.html).

**Tier C (the eval-gated, KV-cache-sensitive core):**
- **C1** (`f83e72e`): model-select + system-prompt build → `engine/model_select.py` + `engine/prompt_build.py`. Gate: warmup KV-prefix BYTE-IDENTICAL (tool `tools/check_warmup_prefix_stable.py --save/--check`) + eval Δ−0.06.
- **C2** (`9c9bc57`, prereq test `3f87889`): tool-exec layer (dedup, sanitize, compress, microcompact, read-path tracker) → `engine/tool_exec.py`. Gate: `tests/test_tool_exec_characterization.py` 27 cases + eval Δ−0.02.
- **C3** (`100bba2`, prereq test `3b2115d`): MemPalace query glue (`tool_mempalace_query` + force-scope + visibility filter) → `engine/mempalace_glue.py`. Promoted the `_visible` closure to module-level `_wing_visible(wing, own_user, own_teams)`. Gate: `tests/test_mempalace_wing_isolation.py` 9/9 (security) + eval.

**Key learning — eval variance discipline:** C3's first eval run hit brain mean 0.65 (Δ−0.12, over the 0.67 floor). Did NOT instant-revert — confirmed with a re-run (0.79 ≈ 0.77 baseline) + verified retrieval works in-process. The 0.65 was Mistral run-to-run noise (±0.09 mean / ±0.38 max, see [[project_eval_citation_validator_phase1]]) concentrated in R2/F2/C2 (high-variance refusal/citation axes). Lesson: a single eval point near the gate floor is a confirm-re-run trigger, not a revert trigger — but only because the byte-identity/isolation/in-process proofs showed no structural break.

**Tier E (added 2026-05-23 by user request, after a source-vs-report audit confirmed Tiers A–D faithful — 0 surviving dup defs, all modules real):** extracted the remaining data + tool implementations. brain.py 16,950 → **12,334 (−51.0% from baseline, more than halved)**.
- **E2** (`8d45315`): `TOOL_DEFINITIONS` schema data (~1,212 LOC, biggest single block) → `engine/tool_schemas.py`. Pure data; warmup byte-identical (it IS the tool-schema half of the KV prefix). TOOL_GROUPS + TOOL_DISPATCH stayed.
- **E3** (`fe8a0e6`): document/ingest pipeline (DocumentParser/Chunker/IngestManager/IngestWatcher) → `engine/ingest.py`. `_ingest_watcher` singleton stayed (server.py assigns the brain-module attr).
- **E1** (`c3fbc70`, chars-test `5ad37da` 13 cases): the 10 file/shell/python/doc tool bodies → `engine/tools/file_tools.py`. Chars-test values PROBED from live tools (caught the bare-string-current_agent crash). eval Δ−0.07.
- **E4** (`1eb0c39`): the remaining 38 tool_* bodies → grouped `engine/tools/{context_tools,translate_tools,delegation_tools,misc_tools,ask_tools}.py` + folded memory/KG into `mempalace_glue.py`. ask_* blocking-state (`_ask_user_pending` etc.) stayed in brain; only bodies moved. eval Δ−0.05 errors:0.

**Tool-extraction discipline:** the 4-edit-site rule — TOOL_DEFINITIONS (now tool_schemas) + TOOL_GROUPS + TOOL_DISPATCH stay in brain; only the `tool_*` FUNCTION bodies move; verify every TOOL_DISPATCH entry resolves to its moved fn (dispatch-identity). A `lambda args: tool_X(args)` forwarder must become a direct ref for the identity check. New tool modules: lazy `import brain as _brain` (no top-level import brain).

**Caught a test-pollution trap:** the E1 chars-test asserted `script == "script_1.py"` from python_exec, which is order-dependent (the artifact folder's script_N counter accrues across runs) — failed only in the FULL suite, not standalone. Fix: unique per-test session id + assert the `script_\d+\.py` PATTERN. The full-suite gate (not per-file) is what caught it.

Eval gate ran reused-gold per [[feedback_eval_reuse_gold]]; restart server via launchctl per [[feedback_server_restart]] before each eval gate. Warmup byte-identity gate: `tools/check_warmup_prefix_stable.py --save/--check` (re-baseline before each step — timestamp rounds to the hour).
