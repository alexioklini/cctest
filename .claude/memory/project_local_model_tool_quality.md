---
name: local model tool-use quality ranking
description: On oMLX, gemma-4-26b-a4b-it-4bit handles tool calling noticeably better than Qwen3.6-35B-A3B-Opus-4.7-Distill-4bit despite being smaller
type: project
originSessionId: dc90b168-03e4-4f63-8ae4-ee4226a77bd8
---
For tool-heavy workflows on the local oMLX provider, **gemma-4-26b-a4b-it-4bit outperforms Qwen3.6-35B-A3B-Opus-4.7-Distill-4bit**, even though the Qwen is ~35B vs gemma's ~26B. User-observed 2026-04-21.

**Why:** Tool-use quality on these models is bounded more by tool-calling fine-tuning than by raw capacity. The Qwen distill is tuned for chat/reasoning; gemma-4-26b-it has stronger tool-call JSON conformance and fewer malformed call attempts in practice.

**How to apply:**
- When suggesting a local default for agent work (tool loops, delegation, code tasks), prefer gemma-4-26b-a4b-it-4bit over the Qwen distill
- The Qwen distill is still fine for pure chat / reasoning where no tools are involved
- Reality-check against the current config before recommending — models can be swapped/deleted and user preferences shift; this is a data point, not a permanent rule
