---
name: project_gemma4_12b_unified_blocker
description: "gemma-4-12B-it-4bit (model_type gemma4_unified) RESOLVED — needs oMLX >=0.4.2.dev1 (mlx_vlm 0.6.1); now installed, served at 256k, benchmarked"
metadata: 
  node_type: memory
  type: project
  originSessionId: cc41552c-148e-4147-bf1d-0dd6b3acaac4
---

2026-06-04: Added `mlx-community/gemma-4-12B-it-4bit` (instruction-tuned, 4bit, ~10GB, dense 12B) to brain-agent. **RESOLVED** — the model is `model_type: gemma4_unified` (the new encoder-free unified multimodal arch, `Gemma4UnifiedForConditionalGeneration`).

**The blocker + fix:** oMLX **0.3.9** bundled **mlx_vlm 0.5.0**, which only knows `gemma4`/`gemma3`/`gemma3n`/`gemma4_assistant` → 12B load failed with `No module named 'mlx_vlm.speculative.drafters.gemma4_unified'`. `gemma4_unified` support requires **mlx_vlm >=0.6.0**. oMLX is `jundot/omlx` on GitHub; support landed in **oMLX 0.4.2.dev1** (released 2026-06-04, *"Updated mlx-vlm to 0.6.1 with Gemma4 Unified (12B) support"*) — NOT in latest stable 0.4.1. GitHub issue #1645 tracks it. **Upgraded** to 0.4.2.dev1 (DMG `oMLX-0.4.2.dev1-macos26-tahoe.dmg`, sha256 fb09cb0d…; old 0.3.9 backed up at `/Applications/oMLX-0.3.9-backup.app`). New bundle layout: `Contents/Resources/Python/framework-mlx-base/...` (was `Contents/Python/framework-mlx-framework/...`). The 3 existing gemma4 models (e2b/e4b/26B-A4B, all `model_type: gemma4`) regression-tested OK on the new build. 12B loads in ~8-10s and generates correctly.

**Config:** 12B is in `config.json` + live registry, provider Lokal, **max_context 256000** (per user — Google markets Gemma4 at 256k; note the model's `config.json` `max_position_embeddings`=131072 and the 262144 in it is vocab_size, so 256k relies on context extension like the 26B which has a true 262144). oMLX `model_settings.json` max_context_window=262144. `~/.omlx/models/gemma-4-12B-it-4bit/` holds a real copy; the sync auto-discovered HF-cache dupes `mlx-community--gemma-4-12B-it-4bit` + `bearzi--gemma-4-26B-A4B-it-oQ3` which I disabled.

**⚠️ oMLX 0.4.2.dev1 does NOT auto-start its server on app launch (behavior change from 0.3.9).** After install + relaunch, :8000 stayed down — the dev build needs a manual menubar "Start Server", OR start headless: `~/.local/bin/omlx serve --base-path /Users/alexander/.omlx --port 8000`. **Current state: started HEADLESS via nohup — will NOT survive a reboot** (the launchd `com.omlx.app` job only does `open -a oMLX`, which no longer starts the server). To make it durable, either fix the menubar autostart pref or change the launchd job to run `omlx serve` directly. oMLX restart note: the dev build's `osascript ... quit` throws a -128 "user cancelled" (a confirm dialog) — kill processes (`pkill -f "omlx.cli serve"` + `pkill -x oMLX`, SIGKILL ok for oMLX, NOT brain per [[feedback_never_sigkill_brain]]) then `open -a oMLX`, then start the server.

**Benchmark mechanics:** `engine/model_bench.py` (`benchmark_model`), trigger `POST /v1/models/config {action:"benchmark", model_id}` (admin-gated), judge = server `default_model` (mistral-medium-3.5), persists `config.json -> models.<id>.benchmark.<task>.measured` + live registry, 9 task types. Mint a JWT in-process from `config.json -> auth.jwt_secret` (HS256, claims user_id/username/role/exp) to authenticate without the password. Do NOT call `model_bench` in a bare separate process — `background_call` there returns sidecar HTTP 500; drive the HTTP endpoint so it runs inside the live server.

**4-model benchmark result (2026-06-04, all 9 tasks, judge mistral-medium-3.5):** mean capability — e2b 93.4, e4b 89.9, **12B 93.8**, 26B-A4B 94.3. Mean throughput (tok/s) — e2b 132.6, e4b 87.3, **12B 18.6**, 26B-A4B 47.6. The 12B matches the larger 26B on capability but is the SLOWEST (~18.6 tok/s, ~2.5× slower than the 26B MoE — dense 12B has no MoE speedup; slower still at 256k ctx). e4b remains the odd one (coding 75, agentic 72, below e2b).
