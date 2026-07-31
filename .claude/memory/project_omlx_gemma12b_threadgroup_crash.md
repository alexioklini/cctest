---
name: project_omlx_gemma12b_threadgroup_crash
description: "gemma-4-12B on oMLX can 500 with \"Thread group size (1024) > maximum (640)\" Metal-kernel error — inference returns empty while warm-pool still reports the model \"ready\"; surfaces as sidecar EMPTY_GIVEUP_TEXT with usage tokens=0"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9f681e8a-9ddb-429b-978b-21404a184b71
---

2026-06-20: While testing whether gemma-4-12B-it-qat-4bit (oMLX, `Lokal` provider @ http://localhost:8000/v1, api_key `brain`) is a better local model than M4-7B for the user-profile daemon, EVERY inference request returned the sidecar's `EMPTY_GIVEUP_TEXT` ("No response was returned. Please modify your request or change the model.", sidecar/sidecar.py:595) with `usage={input_tokens:0, output_tokens:0}` — even "2+2".

ROOT CAUSE (in `~/.omlx/logs/server.log`, NOT a brain bug, NOT model quality):
`ValueError: Thread group size (1024) is greater than the maximum allowed threads per threadgroup (640)` from mlx_lm/generate.py → oMLX returns `POST /v1/chat/completions → 500 (unhandled)` with empty body. A Metal kernel limit some quant/model+oMLX-build combos violate (same family as [[project_omlx_head_kv_cache_regression]]).

TRAP: the warm-pool log says "gemma-4-12B-it-qat-4bit: +1 ready (total 10/10)" and `/v1/models` LISTS the model — but "ready" only means it LOADED, not that it can GENERATE. Don't trust warm-pool readiness as proof of working inference; do a direct generation smoke (`curl .../v1/chat/completions -H "authorization: Bearer brain"` — note key is `brain`, not `dummy`) and check usage tokens != 0.

RESOLUTION: the user changed the oMLX model config; after oMLX reloaded the model, generation worked (PONG, finish_reason=stop, no crash). Then both gemma-12B AND post-fix M4-7B scored 5/5 grounded on the profile case (~11s each), on par. DECISION: kept user_profile_model on M4-7B (the stated final state, see [[project_classifier_model_split.md]] context) — no reason to add an oMLX dependency to the daily daemon when both are equal.

Eval harness `eval/m4_7b_usecase_eval.py` documents this and includes a GEMMA12 provider for the profile case.
