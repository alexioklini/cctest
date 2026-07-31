---
name: project_bgtask_fanout_probe
description: "2026-05-29: pre-build probe — CAN Mistral decompose for bg-task fan-out? Medium=yes/strong, Small=weaker but functional. eval/fanout_probe.py is the seed of the bg-task-architecture eval suite. Findings + known failure modes to fix via prompt."
metadata: 
  node_type: memory
  type: project
  originSessionId: f096ee00-98eb-46f0-bea7-abe0348fc4fe
---

Before building fan-out/join plumbing ([[project_bgtask_fanout_join_spec]]), validated the MODEL-SIDE decision first ("if this fails on the LLM, everything else is useless"). Built `eval/fanout_probe.py` — posts directly to the SIDECAR (:8421/turn) with the REAL production system prompt (2121 chars) + full 29-tool list (run_background_task description swapped for the candidate fan-out prose), runs a tiny local HTTP stub as tool_endpoint to CAPTURE the tool_use calls + return benign "started" results. Zero Brain changes. Scores 5 criteria: decision (fan-out vs single vs inline), count, self-contained prompts, consistent group_id, follow_up present. 5 scenarios (multi_vendor=3, five_topics=5, single_long=1, quick_inline=0, two_docs=2).

**This probe IS the seed of the permanent internal eval suite for the bg-task execute architecture** (user directive) — run before/after any change to tool description / model / join logic. Productize: stabilize scenarios as gold, add --repeat variance, wire a pass threshold.

## Results (2026-05-29, temp 0.2, single run each)
- **mistral-medium-3.5: STRONG.** Nailed both clean fan-outs perfectly — multi_vendor 3/3 + five_topics 5/5, consistent group_id, self-contained 300-870ch prompts, follow_up on every call. quick_inline correct (answered inline, no spurious tasks). Crit pass: decision 3/5, count 4/5, self_contained 3/5, group_id 4/5, follow_up 5/5.
- **mistral-small-latest: WEAKER but functional.** Did fan out, but: (1) often OMITS group_id (5/6 calls None on five_topics), (2) often OMITS follow_up, (3) over-decomposes (single_long → 5 tasks incl. a duplicate with a different `_final` group_id). Crit pass: decision 3/5, count 4/5, self_contained 4/5, group_id 3/5, follow_up 3/5.

## Known failure modes (fixable via prompt, NOT model-blocking)
1. **Model emits the synthesis/join as an EXTRA Nth task** instead of using `follow_up` (medium's multi_vendor put follow_up but small made a 4th "Zusammenfassung" task; small's five_topics made a 6th). → tighten description: "do NOT create a separate task for the combination — that's what follow_up is for."
2. **Over-decomposition of a SINGLE topic** (single_long → 2-5 tasks). Borderline-correct (parallel sub-research is defensible) but produced a malformed empty call on medium. → clarify "fan out only across INDEPENDENT subjects, not aspects of one subject" OR accept it.
3. **group_id / follow_up dropped**, esp. mistral-small. → make them more prominent / give a worked example in the description; consider server-side defaulting (synthesize a group_id if missing when ≥2 calls in one turn — but that's heuristic routing, weigh vs Rule 5).
4. **two_docs scenario was FLAWED** — model correctly refused to fan out because the files don't exist + asked for upload (medium) / empty (small). Fix the scenario (provide docs) before scoring it.

## Corrected-rubric results (2026-05-29, --repeat 3 = 15 runs each)
Rubric fixed per two locked decisions: (1) sub-topic split of ONE subject = acceptable (single_long → fanout_expected, any count>=1 OK); (2) same-turn=one group server-side, so MISSING group_id is NOT a defect, only CONFLICTING explicit ids fail. two_docs scenario fixed (files stated as on-disk).
- **mistral-medium-3.5: decision 12/15, count 12/15, self_contained 12/15, group_id 15/15, follow_up 15/15.** STRONG. follow_up + group_id now perfect after description rewrite (worked example + "do NOT make a separate summary task").
- **mistral-small-latest: decision 13/15, count 13/15, self_contained 11/15, group_id 15/15, follow_up 14/15.** STRONG too — basically on par with medium for this task. group_id 8/15→15/15 once rubric stopped penalizing missing ids (the same-turn-group decision).
- **gemma-4-26B-A4B-it-MLX-4bit (oMLX local): decision 4/15, count 7/15, self_contained 12/15, group_id 15/15, follow_up 15/15.** SHARP LIMITATION — see below.

## gemma-4-26b: understands the tool, REFUSES to parallelize (the key local finding)
gemma uses run_background_task CORRECTLY — self-contained prompt (12/15), follow_up always present (15/15), acknowledges + stops. But it almost always **collapses a multi-subject fan-out into ONE task** ("research AWS, Azure AND GCP" → 1 task covering all three) instead of N parallel calls. five_topics → 1 call every time; multi_vendor → 1 call 2/3 (one run did fire 3). So decision 4/15 is NOT a comprehension failure — it's a failure to emit MULTIPLE parallel tool calls in one turn. quick_inline correct 3/3 (no spurious tasks). two_docs → 0 calls (asked for files / went inline despite on-disk paths).
=> Local model gives you the *offload* benefit (1 long bg task, non-blocking) but NOT the *parallel fan-out* benefit. Cloud Mistral gives both. This matches the broader local-model tool-reliability pattern ([[project_local_model_tool_quality]], [[project_guided_execution_broken_local]]): single flat tool call OK, multiple-coordinated-calls weak.

## Verdict
Model-side is VIABLE on cloud (medium AND small ~on par, both strong). On local gemma-4-26b, fan-out DEGRADES GRACEFULLY to single-offload (still useful, never broken). Build proceeds; the join MUST tolerate N=1 from local as the normal case. Mitigations baked in from this data: same-turn=one-group (server synthesizes group_id), drop empty-prompt calls, follow_up carries combine. Original verdict line kept below.
Model-side is VIABLE — esp. medium. The mechanism is worth building. The gaps are prompt-engineering + light server-side tolerance (default missing group_id, ignore an empty call), NOT fundamental model inability. Contrast with the guided-execution failure ([[project_guided_execution_broken_local]]): that was STRUCTURED plan emission; this FLAT-calls approach works because the model just emits N normal tool calls. Build can proceed; bake the failure-mode tolerances into the join (treat missing group_id + lone-call grouping, drop empty-prompt calls).
