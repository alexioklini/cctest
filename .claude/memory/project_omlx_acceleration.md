---
name: oMLX inference acceleration on Gemma 4 26B
description: SpecPrefill enabled w/ e4b draft, DFlash unavailable for Gemma 4 26B MLX, max_concurrent=2 for continuous batching
type: project
originSessionId: 82d75e1e-7867-41da-8f99-bcbc86081a8b
---
oMLX inference acceleration state for `gemma-4-26b-a4b-it-4bit` (the default model).

**Continuous batching** — set `providers.omlx.max_concurrent=2` in `config.json` (2026-04-25). oMLX supports continuous batching server-side; capacity is throughput-vs-per-request-latency tradeoff. Benchmark numbers in CLAUDE.md *Provider Concurrency Queue* section. Brain code unchanged — `LocalProviderQueue` rebuilds the semaphore lazily on next acquire.

**SpecPrefill** — wired up in `~/.omlx/model_settings.json` for gemma-4-26b with `specprefill_draft_model=/Users/alexander/.omlx/models/gemma-4-e4b-it-4bit` (draft is on disk). Toggle `specprefill_enabled` is currently false; flip via oMLX admin dashboard for ~1.3-1.5× prefill speedup. Speedup target is the global-attention layers (Gemma 4 has hybrid local/global attention, not MoE — modest gain vs the 2-3× reported on actual MoE models).

**DFlash** — **not viable for Gemma 4 26B MLX** as of 2026-04-25:
- `RedHatAI/gemma-4-31B-it-speculator.dflash` exists but is vLLM/BF16, paired with the 31B-it target (not 26B-a4b), can't transfer
- `bstnxbt/dflash-mlx` only ships drafts for Qwen3.5/3.6 (incl. `z-lab/Qwen3.6-35B-A3B-DFlash` — works with the Qwen3.6-35B-A3B-4bit you also have on disk)
- No MLX-format Gemma 4 DFlash draft published anywhere
- Recheck `RedHatAI/*` and `bstnxbt/dflash-mlx` periodically — kaitchup writeup called the 31B draft "preliminary" so 26B + MLX ports may follow

**TurboQuant KV** — already on at 4-bit for the gemma-4-26b. Why peak memory is only 14.7GB at pp4096. Don't disable.

**Why:** save the research trail — DFlash specifically is the kind of thing that'll change in months and we don't want to re-derive "is there a Gemma 4 MLX draft yet" from scratch each time.

**How to apply:** when user asks about local inference speed, prefer SpecPrefill (free, ready) and the existing warmup over chasing DFlash. Recheck DFlash availability if user mentions it explicitly or if they switch their main model to a Qwen3.6 variant (where DFlash *is* viable today via `bstnxbt/dflash-mlx`).
