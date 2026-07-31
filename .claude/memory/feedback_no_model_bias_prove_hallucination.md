---
name: feedback_no_model_bias_prove_hallucination
description: "Don't default to blaming local/non-Anthropic models; never claim hallucination without proving it against actual retrieved content + actual output"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 94888652-c9bc-4cd7-b8da-e14934da5ed4
---

Do not carry a bias toward Anthropic/cloud models over local or non-Anthropic models. Judge each specific run on its specific evidence, not on "it's a local model so it probably failed."

Never claim a model hallucinated unless proven with facts: show the actual model output is ungrounded against the actual retrieved content. An inference from model identity, or a separate repro run that behaved differently, is NOT proof.

Never open with a pattern-matched diagnosis like "this is typical behavior of <model/tool/system>." Such phrases assert a cause before the evidence is in. State what was actually observed; if the cause is unknown, say so and name what would need checking. No plausible-sounding guesses presented as conclusions.

**Why:** In session 94888652 I diagnosed chat 98d85572 as "gemma-4-26B hallucinated an exa.ai URL / skipped the fetch," backed by a repro that showed a *different* behavior (prose-narrated fetch-skip). The actual facts (`rounds=3 tools=2`, a real web_fetch, a coherent grounded answer, exa.ai being a legitimate URL #2 in the results) showed normal correct operation. I reached for "local model failed" as the default and mislabeled it.

**How to apply:**
- A hallucination claim requires: the actual output + the actual source content it should match, shown to disagree. No hand-waving.
- A repro only counts as evidence if it reproduces THE specific behavior in question — not a different failure of the same model.
- When something looks off, trace it to a concrete fact (the exact URL passed, the exact tool result, the exact response text) before naming a cause.
- Apply the same evidentiary standard to every model regardless of vendor or local/cloud.
