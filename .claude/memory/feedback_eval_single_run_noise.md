---
name: feedback_eval_single_run_noise
description: KG-Real-Policies eval on mistral-small is noise-dominated — a single-run brain-mean delta under ~0.05 is NOT signal. Require >=3 reps before claiming any quality change. Read before acting on an eval number.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 87eeee2c-1ca4-455e-b206-36ff0727965f
---

On the `eval/run.py` KG-Real-Policies canary with `--brain-model auto` (routes most questions to **mistral-small**), the brain mean has a run-to-run **stdev of ~0.04** purely from mistral-small synthesis sampling — the SAME config re-run scores anywhere in **0.76–0.85**. So a single-run delta under ~0.05 is INSIDE the noise band and means nothing.

**Why:** mistral-small intermittently collapses on the synthesis turn after correct retrieval — emits a content-free placeholder ("Die Antwort ist vollständig und korrekt") or fabricates, scoring ~0.1–0.5 on a question it usually nails. These collapses land on a DIFFERENT random question each run (R1 once, R2 0.20 next, P1 0.17 next), so any single 15-question run is dominated by which 1–2 questions happened to collapse, not by the change under test.

**The rule:** never claim an eval improvement/regression from one run. Run **>=3 reps**, report **mean ± spread**, and only trust a per-question result that is **consistent across reps** (e.g. R1_multilogin scored 0.63/0.48/0.65 = a REAL weakness; R2 scored 0.20/0.98/0.98 = pure variance).

**Why this is a standing rule — it fooled us THREE times (2026-06-09):**
1. R1 collapsed to 0.08 → looked like an embedding/Qdrant regression → was a one-off synthesis fluke (re-ran 0.80, 0.72).
2. Qdrant int8 scored 0.84 vs f32 0.79 → looked like quant *improved* quality (impossible — rescore only recovers recall) → variance.
3. Tool-gating floor-trim scored 0.84 vs 0.80 single-run → committed it as a "+0.04 win" → **3-rep follow-up = 0.79 ±0.04 = eval-NEUTRAL.** The 0.84 was the top of the band. The floor-trim (v9.98.0) + prompt-affordance fix (v9.98.1) are kept on CORRECTNESS grounds (don't hand a weak model 18 tools incl. execute_command for a one-tool lookup; don't advertise tools the gating strips) — they do NOT regress — but they are NOT the quality lift the single run implied. Changelogs corrected to say so.

**What variance does NOT fix, and the real levers:** the collapses are intrinsic to mistral-small, not the backend/retrieval/prompt (retrieval was verified correct on every collapsed question). No tool/prompt tweak removes them. The actual levers for a real gain: (a) route grounding-heavy questions to mistral-**medium**, or (b) a synthesis-retry when output is detected-empty/placeholder. The one reproducible content weakness to target is **R1_multilogin** (consistent ~0.59 — needs the Datenowner-approval fact + an explicit "nicht spezifiziert" note).

Related: [[project_qdrant_live_int8]], [[project_auto_route_classifier_fixed]], [[feedback_defer_to_users_migration_calls]].
