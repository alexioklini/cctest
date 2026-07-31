---
name: project_auto_route_classifier_fixed
description: 2026-06-02 (v9.60.1) auto-route classifier verified end-to-end — 3 bugs fixed; policy eval 0.48→0.86 on mistral-small
metadata: 
  node_type: memory
  type: project
  originSessionId: 1e601934-e5f3-4506-89f6-9aff4d0c3fe3
---

The v9.59.0 LLM classifier path (HANDOVER_classifier_eval.md) is now VERIFIED working. Three bugs fixed in v9.60.1 (commit 10b2b11):

1. **Dual-module config bug (classifier never ran):** `resolve_task_analysis` etc. read `server_config` via bare `import server as _srv_mod` — under launchd that's a SECOND module instance with NO `auto_route` key, so `classifier_mode` always fell back to `keywords`. Same footgun as v9.45.1. Fix: shared `brain._server_config()` singleton helper (`__main__` then `server`). **Rule: never read server_config via bare `import server` in brain.py — use `_server_config()`.** The two GDPR readers already used the correct loop pattern.

2. **Primary pick went cloud→local:** the "NEVER cloud→local" rule lived ONLY in `_fallback_walk`, NOT in `_pick_by_benchmark`'s primary ranking. So a free local model (gemma-4-e2b, bogus v9.56.0 benchmark cap=98) beat cloud models on every research/analysis turn → eval crashed 0.75→0.48. Fix in `_bench_rank_key`: (a) capability is a pure FLOOR not a maximand (old `high`-complexity branch sorted `-cap`, always crowning the single top model — user: "capable enough, not most capable, else only mistral-medium wins"); (b) locality is top tiebreaker among capable: cloud before local, then tps, then cost. Net: capable cloud beats capable local; faster/cheaper capable cloud wins (mistral-small over medium).

3. **Classifier tool steering:** `_STRUCTURED_CLASSIFY_SYSTEM` TOOLS rewritten to explicit WHEN-rules + "list EVERY tool / when unsure include it" + 4 examples. `memory` = DEFAULT retrieval tool for ANY internal/document/policy/standard/prior-context question (NOT web); web only for current/external; files for named/attached file. Generalizes to attached-file chats, not project-only.

**Verified (KG-Real-Policies 15Q, mistral-medium judge):** routing 15/15→mistral-small (cloud), tools 15/15 include memory (was 8/15), brain mean **0.86** (best on record, beats 0.75 keyword baseline; Δ−0.08 vs gold within ±0.09 variance). See [[project_eval_kg_enabled_results]], [[project_brain_vs_opus_eval]].

**STILL OPEN:** gemma's v9.56.0 local benchmark cells read cap~95-100 (the 2-prompt micro-benchmark over-scored a 2B model). Fix #2 makes them harmless for cloud-vs-local, but a re-benchmark with a better/larger prompt set is advisable — the micro-benchmark's judge is too generous to local models.
