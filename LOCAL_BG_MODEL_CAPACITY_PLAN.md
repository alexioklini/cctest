# Local background-model capacity plan — 10–15 concurrent users (production)

Drafted **2026-06-16**. Supersedes the SINGLE-USER assumption that ran through
the earlier local-model work (`[[project_local_bg_model_vllmmetal_bench]]`,
`APPLE_FOUNDATION_MODELS_PLAN.md`). **Production target: 10–15 concurrent users.**

## Why concurrency changes everything
Background tasks fire **per user-turn**: the **auto-route classifier** runs on
EVERY turn and is **latency-sensitive** (the user waits — it gates tools), plus
chat-summary / wiki / memory-classifier (daemon, NOT latency-sensitive). With
10–15 active users, bursts of simultaneous background calls go far past the
"~2–4 parallel" budget the earlier single-user plans assumed.

## Hard, MEASURED constraint: the inference ENGINE is the bottleneck, not HW
From `[[project_omlx_batching_measured]]` (measured on M2 Max, gemma-4-26B-A4B):
- **oMLX / MLX do NOT batch for real** — aggregate throughput stays FLAT ~30
  tok/s across c=1/4/8/16 (streams just share a fixed budget). Under 10–15 users
  per-stream collapses to ~2 tok/s. **Unusable as a multi-user server.**
- **vLLM (continuous batching) DOES** — aggregate rises 30→300+; this is what
  multi-tenant needs. vllm-metal on the Mac showed real batching (c=4 → 1.75×),
  but 32GB RAM swapped at c=8+.
- → For 10–15 users you MUST run a **real continuous-batching server (vLLM)**
  on hardware with enough RAM/compute. A single M4 mini (24GB, 10-core GPU,
  ~120GB/s) is **undersized** for this — its Qwen sweet spot was ~2–3 parallel.

## Apple Foundation Models — OUT for the latency-sensitive shared path
Apple's on-device stack (`fm serve`) is built for ONE foreground request, not a
multi-tenant batching server; the 20B MoE's flash↔DRAM expert-swapping penalises
parallelism. **Not a fit for 10–15 concurrent users.** Keep the Apple-FM plan
only as a per-device / privacy experiment, not the production path.

## Decision (user, 2026-06-16): plan for STRONGER / MULTIPLE local machines
Everything stays local (privacy + cost at scale), but on hardware sized for the
load — not a single M4 mini.

### Candidate hardware (capacity, from `[[project_dgx_spark_warmup_plan]]`)
- **DGX Spark (GB10, 128GB unified, ~273GB/s)** — already analysed: with a MoE
  model (gemma-4-26B-A4B / Qwen3.5-A3B class) on vLLM continuous batching,
  **~10–20 users comfortable** (9–13 tok/s/user), usable to ~50. **Covers the
  10–15 target.** Note: low bandwidth (273 < Mac 400) hurts DENSE models but not
  MoE (only ~3–4B active/token) — so pick a MoE.
- **Mac Studio M2/M3 Ultra (800GB/s)** — higher single-stream speed, but the
  Mac batching story depends on vllm-metal maturity (worked, but RAM-bound at
  high concurrency on 32GB; an Ultra with 128–192GB removes the RAM limit).
- **Multiple nodes** — brain-agent's `LocalProviderQueue` is per-provider, so
  several backend boxes = several named providers; could shard load (e.g.
  classifier node vs daemon node).

### Model: pick a MoE for batched multi-user
MoE (gemma-4-26B-A4B ~4B active, Qwen3.x-A3B) gives near-dense quality at
small-model active-compute → far better tokens/s/user under batching than a
dense 7–26B. The earlier Qwen2.5-**7B dense** bench was a single-user pick; for
10–15 users a MoE on vLLM is the right shape.

### Engine: vLLM with continuous batching + prefix caching
- `enable_prefix_caching` → shared system-prompt/tool prefix cached once across
  all users (big win when every turn shares brain-agent's prompt).
- On Spark/CUDA: mature vLLM. On Mac: vllm-metal (verified working, watch RAM).
- This is the from-`[[project_dgx_spark_warmup_plan]]` migration to-do #3.

## Plan of action
1. **Estimate the REAL simultaneous background-call rate** at 10–15 users — how
   often does the latency-sensitive classifier actually fire concurrently? (Not
   every user types at once; daemon tasks can queue.) This sizes the box. Derive
   from current usage/traces if available, else load-model it.
2. **Pick HW** (Spark vs Ultra vs multi-node) against that rate + a tokens/s/user
   floor (e.g. classifier must return < ~2s under peak).
3. **Stand up vLLM** (continuous batching + prefix caching) with a MoE model on
   the chosen box; expose Anthropic `/v1/messages` (vLLM has it natively — see
   `[[project_local_bg_model_vllmmetal_bench]]`) or via a CLIProxyAPI route.
4. **Benchmark the REAL multi-user load** — concurrency sweep c=1..20 with
   DISTINCT prompts + the real brain-agent system prompt + the classifier probe,
   per-stream latency + aggregate, ≥3 reps (`[[feedback_eval_single_run_noise]]`).
   This is the only honest sizing — earlier single-user numbers don't transfer.
5. **Wire as named provider(s)** with `max_concurrent` set from the sweep;
   keep the latency-sensitive classifier on the fastest path, daemon tasks can
   tolerate queueing.
6. **Fallback:** cloud mistral-small stays the overflow / degradation path so a
   local-capacity spike never blocks users.

## Bottom line
At 10–15 users the decision is HARDWARE + ENGINE, not model-download. Measured
fact: only a real continuous-batching server (vLLM) scales; oMLX/MLX and Apple
`fm serve` do not. A single M4 mini is undersized; a Spark (MoE on vLLM) is the
proven-on-paper fit for 10–20 users. Size it against the real concurrent
classifier-call rate, then benchmark the actual multi-user load before
committing.
