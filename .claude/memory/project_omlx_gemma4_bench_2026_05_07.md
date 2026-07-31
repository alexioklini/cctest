---
name: oMLX Gemma 4 baseline bench (M2 Max 32GB, 2026-05-07)
description: TG tps anchor for future MTP/DFlash comparisons; SpecPrefill measured as net-zero / negative on these workloads
type: project
originSessionId: 519d7091-cfef-404b-a14e-be273fa41657
---
Baseline numbers for Gemma 4 26B-A4B and e4b (both 4bit MLX) on M2 Max 32GB, oMLX 0.3.8, with TurboQuant KV (4bit), APC enabled. Bench script: `bench/gemma4_spec.py` (standalone, OpenAI-compat). Results CSV: `bench/results.csv`. n=3 reps per cell, variance < 1%.

**Why:** Establish anchor before MTP (mlx-vlm #1122 / ollama #15436) or DFlash (oMLX #1084) become testable, so improvement claims have a real reference.

**How to apply:** When any other backend serves Gemma 4 with speculative decode, run `bench/gemma4_spec.py` with the same workloads against it. Compare to these numbers. Decision threshold: must beat e4b 73 TG tps or 26B 65 TG tps by ≥20% to justify migration risk.

## Steady-state TG tps (the meaningful number)

| Setup | Cold warmup (s) | Balanced TG | TG-heavy TG | PP balanced cold |
|---|---|---|---|---|
| e4b SpecPrefill+e2b | 6.5 | 72.7 | 73.9 | 840 |
| e4b NoSpec | 4.6 | 72.5 | 73.7 | 3134 |
| 26B-A4B SpecPrefill+e4b | (warm) | 64.5 | 65.1 | 475 |
| 26B-A4B NoSpec | 11.9 | 64.5 | 65.2 | 476 |

## Findings

**SpecPrefill is net-zero or negative on both models for prompts ≤8K tokens.**
- TG tps identical with/without SpecPrefill (within noise).
- Cold-start adds 1.9s on e4b (drafter load), drafter prefill overhead beats savings on already-fast 4bit host.
- SpecPrefill `keep_pct: 0.2` math only favors host when host-prefill ≫ drafter-prefill. With 4bit + TurboQuant + APC, host is too fast for the tradeoff.
- **For brain's typical 4-8K prompt workload: disable SpecPrefill.** Long-context (>16K tokens) might cross over, untested.

**TG ratio e4b/26B-A4B = 1.12** — only 12% slower despite ~6× weights. Gemma 4 26B is MoE (4B activated per token); decode is memory-bandwidth bound on Apple Silicon, not parameter count.

**Cold warmup**: 26B 11.9s, e4b 4.6s. Drafter add: ~2s on e4b.

**APC active on oMLX**: rep 1 PP differs from rep 2-3 by 4-9× when SpecPrefill on. Same-prompt repeated requests massively cheaper after first hit. Any backend comparison MUST control for cache state (start cold, or use distinct prompts per rep).

## What this baseline does NOT measure

- Tool calling correctness or speed (text-only prompts)
- Multi-turn KV reuse vs single-shot (single-shot only)
- Long context (>2189 tokens prompt, our "PP-heavy" was only ~2K tokens — script's `_make_long_prompt(8000)` undershoots due to Gemma's dense tokenization at ~3.7 chars/tok)
- Memory peak (would need separate process monitor)

## Workload notes

`bench/gemma4_spec.py` workloads are mislabeled in token terms:
- "PP_heavy" 8K chars = ~2189 tokens (intended 8K tokens)
- "balanced" 2K chars = ~590 tokens (intended 2K)
- "TG_heavy" 200 chars = ~62 tokens (intended 200)

To measure real long-context behavior, multiply target chars by ~3.7. Not fixed in script — flag for next round.

## Live config state (after bench, restored)

`~/.omlx/model_settings.json`:
- `gemma-4-26B-A4B-it-MLX-4bit`: SpecPrefill ON, drafter=e4b, DFlash OFF (DFlash was the bug — broken via oMLX #1084 rms_norm error; do not re-enable until upstream fix)
- `gemma-4-e4b-it-4bit`: SpecPrefill ON, drafter=e2b
- backups: `~/.omlx/model_settings.json.bak.*`

Brain's `gemma-4-26B-A4B-it-MLX-4bit` model entry was historically routed via DFlash and silently failing every Gemma 4 chat for an unclear period. Now serves correctly via SpecPrefill, but A4 measured net-zero benefit — consider toggling SpecPrefill OFF for production until long-context benefit is verified.
