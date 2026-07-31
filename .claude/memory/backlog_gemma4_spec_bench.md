---
name: Gemma 4 26B speculative-decode bench plan
description: 3-setup × 3-workload × 3-metric benchmark to decide which speculative method to integrate; standalone script, no brain integration
type: project
originSessionId: 519d7091-cfef-404b-a14e-be273fa41657
---
Decision plan: which speculative-decoding approach to back Gemma 4 26B-A4B-it 4bit with on M2 Max 32GB. Test SpecPrefill (oMLX), MTP (Google's drafters), DFlash (z-lab's drafters) as drop-in replacements solving the same problem.

**Why:** All three are different techniques for the same goal — small drafter accelerates decode/prefill via parallel verification by big model. Apples-to-apples comparison is meaningful. SpecPrefill emphasizes PP, MTP/DFlash emphasize TG, so a workload-shape sweep matters.

**How to apply:** When mlx-vlm #1122 closes (or ollama #15436/#15596 close, or llama.cpp #22673/#22105 merge), run the bench *before* committing to a brain migration. Engine isolated from brain middleware.

**3 setups (run as upstream bugs close):**
- A — oMLX SpecPrefill + gemma-4-E4B drafter — RUN NOW, baseline lives
- B — mlx-vlm MTP + google/gemma-4-26B-A4B-it-assistant — wait #1122
- C — mlx-vlm DFlash + z-lab/gemma-4-26B-A4B-it-DFlash — wait #1122 + #1084
- Bonus — ollama MTP (#15980 merged) — different engine, same drafter as B → isolates engine vs drafter

**3 workload shapes (same prompts across setups):**
- PP-heavy: 8K prompt → 100 token response  (favors SpecPrefill)
- Balanced: 2K prompt → 800 token response  (brain's typical)
- TG-heavy: 200 prompt → 2000 token response (favors MTP/DFlash)

**3 metrics per cell:**
- Warmup speed: server-cold to first-token-ready (weight load + draft load + KV warm)
- PP tps: prompt processing throughput
- TG tps: sustained decode throughput

5 reps per cell for variance bars. CSV output. Memory constraint on 32GB Mac: only ONE inferencer in memory at a time — stop oMLX before starting ollama/mlx-vlm.

**Bench script lives in:** `bench/gemma4_spec.py` (standalone, hits `/v1/chat/completions` directly, no brain code touched).

**Decision threshold:** if winner < 20% faster than oMLX SpecPrefill on the balanced workload, migration not worth integration risk. Brain's `eval/` 15Q canary is the second-stage gate (run after integration on the chosen winner).
