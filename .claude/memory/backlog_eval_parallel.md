---
name: Eval parallel execution for cloud models
description: Add --parallel N flag to eval/run.py to run questions concurrently when using cloud models
type: project
---

Sequential question loop is fine for oMLX (2-slot queue bottleneck) but wastes wall time on cloud runs. With cloud models Brain + Opus gold + Mistral judge can all overlap across questions.

**Why:** thinking=high eval runs take ~60-70 min sequentially; parallel would cut to ~6-8 min.

**How to apply:** Add `--parallel N` flag (default 1) with asyncio or ThreadPoolExecutor semaphore around the per-question loop. Gate parallel default on whether `brain.model` resolves to a local provider.
