---
name: project_apple_fm_classifier_rejected
description: "2026-06-20 — Apple Foundation Model (on-device, macOS 27 \"FM 3\") benched as auto-route classifier on the M4, REJECTED — too slow (2.24s) + unreliable (84.4% valid JSON). Classifier stays cloud mistral-small."
metadata: 
  node_type: memory
  type: project
  originSessionId: 4ba4b9bc-7f50-4609-a70f-dac96a6d1484
---

2026-06-20: benched Apple's on-device Foundation Model (macOS 27 / Tahoe-successor, the ~3B "FM 3" in the Apple Intelligence stack) as a LOCAL auto-route prompt-classifier candidate vs Qwen-7B on the Mac mini M4. **REJECTED.**

**Access path (macOS 27 FM CLI was massively enhanced):** `/usr/bin/fm` ships with macOS 27. Key: `fm serve --host 0.0.0.0 --port 1976` exposes an **OpenAI Chat-Completions** server (`POST /v1/chat/completions`, `GET /v1/models`, `GET /health`) for the on-device model (`model:"system"`; `pcc`=Private Cloud Compute, not available headless). Structured output via `response_format:{type:json_schema}` (FM's equivalent of forced-tool-use). NB: streams SSE by DEFAULT → must send `stream:false`. Also `fm respond --instructions --schema`, `fm token-count`, `fm schema`. FoundationModels.framework + Swift 6.2 also present (SystemLanguageModel.default → .available).

**Bench (classifier-only, 3 reps × 15 cases, eval/bg_tasks_local_eval.py):**
- cloud mistral-small: 100% valid, 100% memory-routing, **0.56s** ✅ (current classifier)
- M4-Qwen-7B: 100% valid, 100% routing, 1.69s
- **Apple-FM: 84.4% valid JSON (38/45), 100% routing WHEN valid, 2.24s** ❌ slowest local + drops JSON
- (3B row 0/45 = artifact, :8013 was down)

**Why rejected:** routing INSTINCT is sound (100% memory-routing on valid outputs — semantically as good as 7B/cloud), but a forced-routing task needs reliable structured output; 84.4% valid + slowest-local latency disqualify it. Likely cause = tight ~4k context (prod classifier prompt = 984 tokens, leaves little headroom; a verbose schema tipped it to a 500 "transcript exceeded context size"). User call: "too slow and unreliable as a replacement."

**State:** fm serve stopped. No config change — classifier stays cloud mistral-small, summary on M4 7B (see [[project_classifier_model_split]]). eval still has the FM call path (_fm_classify_call + _fm_route_schema) for a future re-test on a bigger-context FM. Revisit only if FM context grows substantially.
