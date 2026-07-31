---
name: Local MLX Inference (oMLX)
description: Local oMLX server on port 8000 serving quantized MLX models on Apple Silicon, replaced distributed inferencer
type: reference
related_to: [infra_deployment, project_sdk_gap_plan, feedback_omlx_anthropic, project_summary, project_token_fixes]
---

Local inference now uses oMLX (v0.2.13) instead of the old distributed inferencer app.

- **oMLX**: Homebrew-installed (`jundot/omlx/omlx`), runs as brew service on port 8000
- **Model**: `Crow-4B-Opus-4.6-Distill` (4-bit quantized, Qwen 3.5-based, distilled from Claude Opus 4.6)
- **Models dir**: `~/.omlx/models/` (auto-discovered subdirectories)
- **API**: Supports both OpenAI and Anthropic API formats; Brain Agent uses `type: "anthropic"`
- **Admin**: `http://127.0.0.1:8000/admin`
- **Convert new models**: `/opt/homebrew/opt/omlx/libexec/bin/mlx_lm.convert`

**Previous setup (retired):** Distributed inferencer app linking multiple Macs at 192.168.1.221:8081, OpenAI-compatible API.

**Constraint confirmed:** oMLX uses anthropic API type by design (feedback_omlx_anthropic) — do not change to openai.

**How to apply:** Default provider is oMLX. When discussing local inference, reference oMLX not the old distributed setup.
