---
name: project_local_bg_model_vllmmetal_bench
description: "2026-06-14: benchmarked Qwen2.5-7B vs Ministral-8B (4-bit MLX) on vllm-metal as the local background-task model (replacing cloud mistral-small for Tier-A bg tasks). Both load + pass; Qwen slightly ahead. M4/24GB feasible."
metadata: 
  node_type: memory
  type: project
  originSessionId: 9cb333ab-267f-474b-99d4-8d91c1d11e0d
---

Goal: run brain-agent's SHORT-input background LLM tasks (chat summary, wiki auto-tag, wiki diff-merge, auto-route classifier, /refine+soul-chat, memory classifier) on a LOCAL model via vllm-metal, replacing cloud `mistral-small-latest`. Target deploy = Mac mini M4 24GB; benchmarked on the M2 Max 32GB (Mac14,13) box to interpolate. User priority: **no UX regression** in brain-agent.

**Why:** save per-turn cloud cost+latency+rate-limits + keep PII local, for the high-volume cheap tasks. KG extraction + translation + LCM + deep-research + agentic delegation stay CLOUD (quality-fragile / long-context / tool-loop — local weak there per [[project_local_model_tool_quality]] + [[project_guided_execution_broken_local]]).

**How to apply:** when wiring this, edit THREE config knobs (no code change needed — verified):
- `config.json → chat_summary_model` → moves 4 tasks at once (chat-summary, wiki auto-tag, wiki diff-merge, **auto-route classifier**). These SHARE this one knob — cannot split without code.
- `tools_config.json → refinement.model` → moves /refine + soul-chat (shared).
- `config.json → mempalace.chat_sync.classifier.model` → memory classifier (its OWN dedicated field, currently mistral-medium-3.5; shares with nothing).
Highest-stakes consumer = auto-route classifier (rides chat_summary_model, gates tools on EVERY chat) → must eval routing before trusting (KG-Real-Policies, ≥3 reps per [[feedback_eval_single_run_noise]]). NOT YET RUN.

## vllm-metal version to use (decided 2026-06-14)
The project ships ONLY dated snapshots, no clean semver stable. Two lines: a stable `0.2.0` line and a dev `0.3.0.dev*` line. GitHub API: BOTH are flagged `prerelease:false`, but the `0.3.0.dev*` carry `.dev` (PEP440 dev builds) and only bump vLLM core 0.21→0.23 (PR #443 = test-only diff otherwise). **USE THE STABLE LINE: `v0.2.0-20260604-074434`** (prerelease:false, commit ef776ca, published 2026-06-04) — wheel asset `vllm_metal-0.2.0-cp312-cp312-macosx_11_0_arm64.whl` (Python 3.12 arm64, vLLM 0.21 core). NOTE: the 0.2.0 currently in ~/.venv-vllm-metal is an OLDER 25-May build (same version string, diff commit) — benchmark ran on THAT; for the M4 reinstall from the June-04 wheel to pin ef776ca. If ever moving to 0.3.0.dev, install to a SEPARATE venv + re-run /tmp/bench_local.py first (vLLM 0.23 core jump = re-verify).

## vllm-metal quant path (KEY, verified 2026-06-14)
vllm-metal (~/.venv-vllm-metal, vLLM 0.21.0 + metal plugin, mlx_lm 0.31.3) loads quantized models THROUGH `mlx_lm.load` → only **MLX-affine 4-bit** (`mode: affine`, like the old oQ3 gemma) or **AWQ-4bit** (repacked to MLX-affine at load by mlx_lm 0.31.3+). **GPTQ rejected. bits!=4 rejected on this path.** Generic CUDA AWQ/GPTQ-marlin kernels do NOT run on Metal. So: use `mlx-community/*-4bit` repos. (Earlier worry that vllm-metal only runs bf16 → WRONG for the mlx_lm path; bf16 worry is moot.) Tool parsers: Qwen=`hermes`, Mistral/Ministral=`mistral`, gemma4=`gemma4`.

## Benchmark (M2 Max 32GB, oMLX stopped to free RAM, both 4-bit, warm, temp=0)
Both `mlx-community/Qwen2.5-7B-Instruct-4bit` + `mlx-community/Ministral-8B-Instruct-2410-4bit` LOAD CLEAN + serve on :8012, engine init ~15s, weights load <1s.

| | Qwen2.5-7B | Ministral-8B |
|---|---|---|
| model_memory | **4.29 GB** | 4.51 GB |
| classifier JSON | ✅ valid, picked `memory`, low | ✅ valid, `memory`, low |
| classifier latency | **~0.45s** | ~0.58s |
| chat summary (DE) | ✅ truly SUMMARIZED 3 Q→1 sentence | ⚠️ just CONCATENATED the 3 questions |
| summary latency | ~0.9s | ~1.0s |
| single-stream tok/s | **~40** | ~35 |
| KV per block | **0.92MB (39× conc@8k)** | 2.36MB (15× conc@8k) |

**Verdict so far: Qwen2.5-7B slightly ahead** — faster, more KV-efficient, and actually summarizes vs Ministral concatenating. Both German-clean, both fit 24GB M4 trivially (~4.5GB weights + modest KV). Latency WELL under the 2s bar for short-output bg tasks even on M2 Max → M4 will be similar/better. **Local won't beat cloud on raw tok/s (cloud mistral-small showed ~4941 agg tok/s w/ their batching) — the win is no-network-hop latency parity on SHORT tasks + free + private.**

## CLASSIFIER GATE — Qwen FAILS it (verified 2026-06-14 on stable ef776ca build)
Re-installed the pinned stable wheel `v0.2.0-20260604-074434` (ef776ca) into ~/.venv-vllm-metal-stable → quick bench IDENTICAL to the old 25-May build (no regression). THEN ran a TARGETED classifier eval (the real `_STRUCTURED_CLASSIFY_SYSTEM` prompt from brain.py, all 15 eval/questions.json, /tmp/clf_eval.py + clf_eval2.py): **Qwen-7B drops the opening `{` on 3/15 → malformed JSON ~20% of turns** (content was right, brace missing — NOT a parser artifact, confirmed with tolerant parser). `classify_task_structured` would fail-open to keyword routing on those. On the 12 that parsed: memory correctly included 12/12, but 3 wrongly added `web` (internal-policy Qs). So **Qwen is fine for PROSE (summary/refine/wiki) but NOT reliable enough as the auto-route classifier** — matches [[project_local_model_tool_quality]] structured-output warning. NOTE eval/run.py is the WRONG tool here (it measures the model ANSWERING policy Qs as the chat model, not classifier routing) — built the targeted probe instead.

**CONSEQUENCE for the shared knob:** `chat_summary_model` drives chat-summary+wiki-tag+wiki-diff-merge AND the auto-route classifier together → can't take the prose win without the classifier risk. Two paths:
- **Option 1 (no code):** leave chat_summary_model on cloud; only move the ISOLATED prose tasks → `tools_config.refinement.model` (refine+soul-chat) + `mempalace.chat_sync.classifier.model` (memory classifier = one-WORD label, not JSON, local-safe). Avoids classifier hazard.
- **Option 2 (small code change, the RIGHT fix):** add a dedicated `auto_route.classifier_model` field so classifier stays cloud while summary+wiki go local. OR enforce JSON via vLLM guided/`response_format` json-schema in classify_task_structured (kills the dropped-brace failure) — also a code change.
DECISION PENDING.

## gemma-4-e2b — DOES NOT LOAD on vllm-metal (verified 2026-06-15)
Tried `mlx-community/gemma-4-e2b-it-4bit` (model_type gemma4, affine 4bit) on vllm-metal stable ef776ca → **hard load failure**: `ValueError: Received 80 parameters not in model` (the `self_attn.{k,v}_proj.{biases,scales}` quantized-attention tensors). vllm-metal even force-cleared the vision tower (text-only backbone) — NOT a multimodal issue, it's the gemma-4 per-layer-headdim architecture mismatch that's dogged gemma-4 on vllm-metal all along (same family as the gemma-26B KV-quant `NotImplementedError`, [[project_omlx_batching_measured]]). So e2b is a NON-STARTER on the chosen engine. Also it'd be the WRONG classifier anyway: routing policy Qs to e2b tanked the eval 0.75→0.48 ([[project_auto_route_classifier_fixed]]), and a 2B is MORE JSON-format-fragile than the 7B that already failed. Conclusion: gemma-4 family needs oMLX, not vllm-metal; and e2b is unfit for the classifier role regardless.

## JSON-SCHEMA FIX — Qwen becomes VIABLE as classifier (verified 2026-06-15)
vllm-metal accepts `response_format={"type":"json_schema","json_schema":{...,"strict":true}}` guided decoding. Re-ran the 15-question classifier eval WITH a schema (task_types/tools/complexity/reasoning, tools enum-constrained): **VALID JSON 15/15** (was 12/15 — dropped-brace failures ELIMINATED), **memory 15/15** (matches the cloud-classifier bar from [[project_auto_route_classifier_fixed]]), avg ~1.22s. Residual: 4/15 ADD `web` alongside memory on internal-policy Qs — non-breaking (memory always present; the prompt says "when unsure include it"), just maybe an unnecessary web search on those 4. → **Guided JSON makes Qwen-7B good enough for the auto-route classifier.** This is the lever that flips Option 2 from "needs cloud classifier" to "local classifier OK IF JSON-enforced".

CODE CHANGE required (Option 2): `classify_task_structured` (brain.py:10065) calls `sidecar_proxy.background_call(...)` — must thread a `response_format` json_schema through to the sidecar/vllm payload. OPEN: does `background_call` accept response_format, and does CLIProxyAPI pass it through to BOTH mistral(cloud) AND local vllm? → decides always-on vs local-only. NOT investigated yet. (Cloud mistral via the sidecar may or may not honor json_schema — if not, apply only when model is_local.)

## ARCHITECTURE: vllm-metal serves NATIVE Anthropic /v1/messages (verified 2026-06-15)
Plan = vllm-metal runs on the SEPARATE M4 mini, brain-agent reaches it via its OWN config.json provider (base_url http://<m4-ip>:8012), NOT via CLIProxyAPI. Confirmed viable: vLLM 0.21 ships `vllm/entrypoints/anthropic/{serving,api_router,protocol}.py` → **`POST /v1/messages` + `/v1/messages/count_tokens`**, mounted by DEFAULT on the metal serve (no flag). Live test returned genuine Anthropic shape `{type:message, content:[{type:text}], stop_reason:end_turn, usage:{input_tokens,output_tokens}}`. So the SIDECAR (Anthropic-native) talks to it DIRECTLY — no wire translation, no CLIProxyAPI hop.

**JSON-enforcement on the Anthropic path = FORCED TOOL-USE, not response_format.** Tested on /v1/messages: `response_format` is IGNORED (free prose); but `tools:[{name:route,input_schema:...}] + tool_choice:{type:tool,name:route}` → clean `{type:tool_use, input:{task_types,tools,complexity}}`, schema-valid, memory correctly picked. So the Qwen classifier fix CARRIES OVER via the Anthropic-idiomatic mechanism the sidecar already speaks. → Option-2 code change for classify_task_structured = request routing JSON as a forced single-tool call (more idiomatic than response_format for this stack), instead of free-text + brittle {...} extraction.

## CONCURRENCY — how many parallel bg tasks (measured M2 Max, 2026-06-15)
Qwen-7B 4-bit, bg-task shape (small prompt + ~40-60 tok out), DISTINCT prompts (no prefix-share skew), --max-num-seqs 16:
| conc | wall | agg tok/s | per-req lat |
|---|---|---|---|
| 1 | 1.17s | 37 | 1.17s |
| 2 | 1.72s | 49 | 1.71s |
| 4 | 2.21s | 69 | 2.05s |
| 8 | 4.53s | 71 | 3.87s |
| 16 | 5.90s | 110 | 5.13s |
- **It REALLY batches** (agg rises 37→69→110, unlike flat oMLX). KV is NOT the limit (budget 19GB → max_tokens_cached 332k, "40.55x concurrency @8k"); COMPUTE is.
- **Sweet spot ~4 concurrent** (per-req stays ~2s, agg ~2×). Beyond: c=8 → 3.9s/req, c=16 → 5.1s/req — degrades hard.
- ANSWER: hard ceiling = whatever --max-num-seqs is set to (KV could allow ~40); PRACTICAL = ~4-6 before per-request latency hurts UX. Set --max-num-seqs ~8.
- NUANCE: auto-route classifier is LATENCY-SENSITIVE (per-turn, user waits, gates tools) → must not queue behind wiki/summary batch. Daemon tasks (chat-sync clf, wiki, summary) are NOT latency-sensitive → c=8+ fine for them.
- ⚠️ M4 mini target is SMALLER (10-core GPU, ~120GB/s vs M2 Max 38-core/400GB/s) → absolute tps LOWER (single-stream maybe ~20-30 not 37), useful parallelism flattens sooner (~2-3 not 4). Shape holds, numbers shrink. Single-user (you) rarely exceeds 2-3 concurrent bg tasks anyway.

## LocalProviderQueue is PER-PROVIDER (keyed by provider NAME) — verified 2026-06-15
`LocalProviderQueue` (brain.py:5655, singleton `_provider_queue` at 5938; the old engine/provider.py was DELETED in 8.30.0 — class lives in brain.py now). `self._slots: dict[str, _ProviderQueueSlot]` keyed by **provider_name** (NOT base_url). Each provider gets its OWN semaphore-backed slot + FIFO waitlist; cap = that provider's `config.json → providers.<name>.max_concurrent` (0/missing = unlimited no-queue; ≥1 = cap N, extras wait FIFO). NO global cross-provider queue.
- CONSEQUENCE for M4 setup: add the vllm-metal box as its OWN NAMED provider (e.g. `Lokal-M4`, base_url http://<m4-ip>:8012) → it gets a SEPARATE queue from the existing oMLX `Lokal` (:8000, max_concurrent=1). Set the vllm-metal provider's `max_concurrent: 8` (matches the sweep; KV allows ~40, useful steady-state ~4-6, 8 = headroom) WITHOUT touching oMLX's serialized 1.
- GOTCHA: key is the NAME string — two providers w/ different names but same base_url = two independent queues (both hammer the same server unaware). Same name = same queue regardless of url. For the M4: one named provider = one queue = `max_concurrent` is THE dial bounding parallel bg tasks against the M4.

## IMPLEMENTED — forced-tool structured output in sidecar (v9.123.0, 2026-06-15, NOT committed)
Built the JSON-reliability fix as a general sidecar mechanism. 3 files:
- `sidecar/sidecar.py` run_turn_streaming: reads `capture_forced_tool` (tool name) + explicit `tool_choice` from req; on that tool_use → captures `.input` → `forced_tool_input`, stop_reason='forced_tool', BREAKS before dispatch (never executes). summary gains forced_tool_input only-when-used.
- `handlers/sidecar_proxy.py` run_turn_blocking + background_call: new `forced_tool` param (Anthropic tool def). Sets tools=[forced_tool], tool_choice {type:tool,name}, capture_forced_tool, allowed_tools=[]. Result surfaces forced_tool_input.
- `brain.classify_task_structured`: requests routing JSON as forced `route` tool (input_schema enums from _TASK_TYPE_TIER/_TASK_TOOL_GROUPS), reads forced_tool_input, FALLBACK to old {...} text extraction if a provider ignores tool_choice (cloud unchanged).
VERIFIED end-to-end vs live vllm-metal/Qwen (edited sidecar imported in .venv_sdk, /tmp/test_forced_tool.py, 15 eval Qs): captured+schema-valid **15/15** (was 12/15), memory 15/15, never dispatched. Normal path regression-checked (/tmp/test_normal_path.py: final_text returned, no forced_tool_input key). py_compile OK ×3.
⚠️ NOT committed, NOT loaded into the RUNNING server (live sidecar :8421 + brain 9.121.0 are pre-edit — needs sidecar restart to take effect). This is the ENABLING mechanism; the config wiring (3 knobs) + provider setup still pending for the M4.

## Status / next
- Feasibility + per-task latency + German quality + 24GB-fit = ANSWERED (Qwen leads).
- NOT DONE: full KG-Real-Policies routing eval (≥3 reps) wired to the local model — the gating test before flipping chat_summary_model. eval harness = `eval/run.py`.
- NOT DONE: config wiring (3 knobs above), launchd plist re-point (plist exists at ~/Library/LaunchAgents/com.brain-agent.vllm-metal.plist, currently set to old oQ3 gemma; was unloaded after the [[project_vllm_metal_migration]] rollback).
- Models cached: ~/.cache/huggingface/hub/models--mlx-community--{Qwen2.5-7B-Instruct-4bit (4.0G), Ministral-8B-Instruct-2410-4bit (4.2G)}.
- Bench scripts: /tmp/bench_local.py (Qwen), /tmp/bench_ministral.py.
- oMLX was STOPPED for this (menubar app + :8000 dead) — RESTART it if the main Lokal provider is needed, OR keep it down if vllm-metal becomes the local backend.
