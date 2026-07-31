---
name: feedback_depth_over_speed
description: "User wants deep analysis and root-cause/architectural solutions, not fast first-plausible answers; trace mechanism with evidence before proposing"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 94888652-c9bc-4cd7-b8da-e14934da5ed4
---

The user does NOT want fast answers. He wants thorough analysis and profound (root-cause, architecturally sound) solutions.

**Why:** Stated explicitly in session 94888652. He values getting the actual mechanism and a durable fix over a quick plausible response. Speed is not the metric; correctness and depth are.

**How to apply:**
- Trace every problem to its actual root cause with evidence (real code path, real data, real failing line / event sequence) before proposing a fix. A hypothesis that merely fits the symptom is not a diagnosis.
- Solve at the single choke point / general property, so edge cases resolve as side effects — never per-caller band-aids. (Reinforces [[feedback_single_fix_point]], [[feedback_no_edge_case_fixes]], [[feedback_analyze_before_code]].)
- For structural questions, lay out options + trade-offs and push back if a simpler/more correct approach exists — don't just patch.
- Verify against reality before declaring done; separate what's proven from what's assumed; surface what wasn't checked.
- Spend the tool calls and reading needed to genuinely understand before answering. Latency is an acceptable cost.
- Pairs with [[feedback_no_model_bias_prove_hallucination]]: no pattern-matched "this is typical of X" causes, no guesses presented as conclusions.

**Repeated failure mode (named so it stops happening):** inferring a cause from a SYMPTOM instead of checking the source. Examples that actually happened and were wrong: "test timed out → the model is too slow / runs locally" (Mistral runs in the CLOUD; the server log showed turns finishing in seconds with error=None — the hang was a client-side SSE reader waiting for stream-EOF instead of the `done` event). The available evidence (the log) sat one grep away and contradicted the claim. RULE: before stating any cause, name the evidence that proves it (a log line, a code line, a measured number). If you haven't looked at it yet, say "Hypothese, noch nicht verifiziert" — never phrase a guess as a finding. A timeout/error code is a symptom, not a root cause.

**Presentation (distinct from depth):** depth belongs in the analysis, NOT the output length. Keep answers concrete and low-noise — he can't read walls of text. For problems/architectural designs, present: concise findings (facts/evidence), options + trade-offs in a TABLE, a recommendation, then let him decide. Use tables or short management-overview bullets over prose. Cut filler and anything that just restates the obvious. Compress the writeup; do not compress the underlying analysis.
