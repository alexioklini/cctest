---
name: project_moa_rejected
description: "Hermes-style Mixture-of-Agents evaluated on Brain's cloud models (Mistral+DeepSeek) and REJECTED — no real lift, ~6x token cost"
metadata: 
  node_type: memory
  type: project
  originSessionId: 99e09af6-092d-44ed-b7f8-8fb8ee5f623e
---

2026-06-27: Evaluated integrating Hermes Agent's "Mixture of Agents" (MoA) into Brain
and REJECTED it. Spec: N reference models answer independently (no tools/no system) →
aggregator model gets the drafts as private context → aggregator writes the final
answer with tools. Would fit Brain cleanly as a "virtual provider" (Brain-side
reference fan-out via background_call, aggregator = normal chat path, drafts injected
via the existing wire-only pattern — sidecar/warmup/KV-prefix untouched).

**Decision: do NOT build.** Eval (eval/moa_eval.py, 15 hard questions × 6 arms × 3 reps,
blind-judged by Opus) shows MoA brings NO repeatable lift with Brain's cloud fleet:
- best single baseline = mistral-medium alone 0.893
- best MoA = refs[small,medium,deepseek]→agg=medium 0.900 (+0.008, INSIDE the ±0.051
  spread = noise per [[feedback_eval_single_run_noise]]) at **6.2× tokens, 2× latency**
- deepseek as AGGREGATOR collapses (0.691) — MoA only as good as its aggregator; never
  use the weakest model to synthesize.
- only real (small) win: open synthesis/judgment questions (Fermi +0.08, judgment +0.05);
  on checkable reasoning MoA LOSES (a bad reference draft can derail a model that was right).

Root cause = the Hermes +6pt benchmark came from strong DIVERSE models (gpt-5.5 +
claude-opus). Brain's cloud fleet (mistral-medium/small + deepseek-v4) lacks that
diversity, and Opus/Claude is NOT reachable via Brain's config providers (not in
config.json, not exposed by the CLIProxyAPI gateway).

If ever revisited: only as opt-in for open research/synthesis (Deep Research / Studio),
aggregator = mistral-medium ONLY, never deepseek. Reusable harness + Opus judge-score
cache (keyed by answer hash) live in eval/moa_*.json + eval/results/moa_judge_scores.json.

**REVISED 2026-07-02**: a classification-GATED variant (fan-out only on task_types
where MoA pays; aggregator = auto-route pick) SHIPPED as the 🧬 MoA (Smart) virtual
model in v9.268.0 — see [[project_moa_virtual_model]]. The eval verdict above still
holds for the FIXED-arm setup; the gate encodes its lessons.
