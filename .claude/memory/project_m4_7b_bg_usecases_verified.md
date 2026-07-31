---
name: project_m4_7b_bg_usecases_verified
description: "v9.177.0 (2026-06-20) — systematic test of all 7 background use-cases flipped to local M4-7B (Qwen2.5-7B) vs the cloud model they replaced; 6/7 equal-or-better, user-profile hallucination fixed by a prompt grounding edit"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9f681e8a-9ddb-429b-978b-21404a184b71
---

2026-06-20 (v9.177.0): The 7 background LLM use-cases pointed at `Lokal-M4/Qwen2.5-7B-Instruct-4bit` (vLLM @ 192.168.1.214:8012, native Anthropic /v1/messages) were each tested against the cloud model they replaced (mostly mistral-small) by driving the LIVE sidecar `/turn?stream=false` directly with the PRODUCTION prompts. Harness: `eval/m4_7b_usecase_eval.py` (keep — user wants ongoing local-model validation).

The 7 (per LLM_CALL_CATALOG.md line ~237): next-prompt(#4, already fixed 9.176.0), chat-summary(#7), wiki-gate(#8), user-profile(#10), code-graph(#16), refine(#5), lang-detect-fallback(#21).

RESULT: 6/7 local equal-or-BETTER than cloud:
- chat-summary: M4 covered MORE topics than cloud (cloud truncated). 2.6s vs 0.5s.
- wiki-gate: SAVE/SKIP both correct.
- code-graph: M4 MORE format-compliant (terse, no markdown) than cloud.
- refine (Polish+Engineer): M4 preserved intent better; on the casual-lookup case M4 did NOT over-strictify where mistral-small DID add "präzise/official source" (the exact regression [[project_two_tier_refine]] hardened against). 2.5s — under perceptible-wait.
- lang-detect: correct de/en/fr/nl/it/es incl. short/mixed.

ONLY regression = user-profile (#10): M4-7B hallucinated plausible-but-unsaid background ("long-standing interest","spanning model deployment","several projects") in ~½ of runs while mistral-small stayed grounded. Cause: "Never invent facts" was buried mid-rule-list, under-weighted by the small model.
FIX (server.py `_PROFILE_SYSTEM_PROMPT`): lead with an emphatic GROUNDING block (only explicit facts; empty section body = exactly `_(none)_`) + an explicit CAPTURE clause (a stated preference/decision IS an explicit fact) so grounding doesn't also drop real preferences. Verified 5/5 clean on M4; cloud unchanged (was already correct). Pure prompt edit, no routing change.

Latency: all M4 cases slower than cloud (profile ~11s vs 1.5s, summary 2.6 vs 0.5) but all except refine are async background tasks → irrelevant; refine ~2.5s is foreground but tolerable.

gemma-4-12B (oMLX) tested as a larger local profile alternative — also 5/5 once oMLX was serving (see [[project_omlx_gemma12b_threadgroup_crash]]), but user DECIDED to KEEP user_profile_model on M4-7B (stated final state; no reason to add oMLX dependency when equal).

DISCIPLINE that paid off: per [[feedback_eval_single_run_noise]], the first single profile run looked worse than it was (dropped-fact was variance + my abbreviated prompt); only 3-5 reps revealed the real signal (hallucination, repeatable). Always rep before asserting a model regression.
