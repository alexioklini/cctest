---
name: project_omlx_kept_vllm_metal_gemma4_blockers
description: "2026-06-20 — attempted to replace oMLX with vllm-metal serving gemma-4-12B on the Mac Studio M2; REVERTED (kept oMLX) because gemma-4 fallback needs large context that vllm-metal's RAM-KV can't hold. Documents the full gemma-4-on-vllm-metal fix chain + why oMLX stays."
metadata: 
  node_type: memory
  type: project
  originSessionId: 9f681e8a-9ddb-429b-978b-21404a184b71
---

2026-06-20, Mac Studio M2 Max (32GB, runs Brain+Qdrant+sidecar). User wanted: retire oMLX, install vllm-metal (matching M4) serving one gemma-4-12B as the local fallback, flip Brain's oMLX knobs to it. OUTCOME: **REVERTED — oMLX kept**, and instead extended sparkdash to monitor/control oMLX.

WHY REVERTED (the decisive constraint): the GDPR/classification **fallback model must be a drop-in for a cloud model MID-SESSION**, so it needs the FULL context a real chat carries. **oMLX has SSD-tiered KV cache by default** → serves 128k–256k on 32GB. **vllm-metal reserves the KV pool in RAM up front for max_model_len** (gemma-4-12B @ 256k = ~84GiB fp16 / ~24GiB 4bit) → on a Brain-co-resident 32GB box it only boots at ~32k context, which would fail nearly every session. vllm-metal has `--kv-offloading-*` but only to CPU(=same RAM), no SSD tiering. So the context gap is architectural, not a flag. KEEP oMLX for any large-context/fallback role.

Brain config was NEVER touched (user rule: don't flip until it works; it never met the bar). gdpr/classification/ocr knobs still → gemma-4-26B-A4B-it-MLX-4bit on oMLX (Lokal provider, localhost:8000).

THE gemma-4-12B-on-vllm-metal FIX CHAIN (it DID eventually serve TEXT — useful if ever revisited; weights from ~/.omlx/models/ or HF mlx-community):
1. Official install: `curl -fsSL https://raw.githubusercontent.com/vllm-project/vllm-metal/main/install.sh | bash` → vllm 0.23.0(from source) + latest vllm-metal dev wheel (`0.3.0.dev2026MMDD...`, NOT on PyPI — PyPI maxes at 0.1.0; M4's exact build came from this same install.sh via uv). Compiles vllm core (~3min, needs Xcode CLT).
2. transformers must be **5.12.1** (M4 parity) — fixes `gemma4_unified` arch not recognized.
3. Serve from a COMPLETE LOCAL DIR (not HF repo id) so transformers reads injected files.
4. Add `video_preprocessor_config.json` = `{"video_processor_type":"Gemma4VideoProcessor","processor_class":"Gemma4UnifiedProcessor"}` — transformers' Gemma4UnifiedProcessor builds a VIDEO sub-processor for a non-video model; NO gemma-4 repo (incl Google) ships this file.
5. Add `vision_config.num_soft_tokens: 280` to config.json — older mlx-community checkpoints (qat/OptiQ in ~/.omlx) lack it; vllm-metal gemma4_unified.py needs it. Newer mlx-community 12B (bf16/8bit) already have it (=280).
6. **platform.py BRAIN-PATCH** (same as [[project_m4_vllm_metal_deployed]]): wrap BOTH `psutil.virtual_memory()` calls (`get_device_total_memory`/`get_device_available_memory`) in try/except → `sysctl -n hw.memsize` fallback. psutil host_statistics64 dies on this OS. Re-apply on every vllm-metal upgrade.
7. `--max-model-len 32768` (256k default needs 84GiB KV).
RESULT: text generates fine on both /v1/messages + /v1/chat/completions. But IMAGE input crashes the engine ("Multimodal encoder dispatch ... adapter not forward_ready") — vllm-metal lists gemma-4 under TEXT-only models; only Qwen3-VL/PaddleOCR-VL do image. So gemma-4 vision is NOT supported on vllm-metal anyway.

WHAT SHIPPED: sparkdash (dgxdash) now monitors+controls oMLX — GET /api/omlx/{status,models}, POST /api/omlx/models/<id>/(load|unload), proxied with the `brain` inference key (oMLX API: /api/status, /v1/models/status, /v1/models/{id}/{load,unload}; admin config behind /admin/api/login). Deployed as launchd com.brain-agent.sparkdash on :8015 (M2). Committed 9badaba. The venv was left UPGRADED to 0.23.0+patch (harmless, M4-matching) per user.
