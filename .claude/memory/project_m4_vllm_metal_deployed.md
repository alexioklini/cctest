---
name: project_m4_vllm_metal_deployed
description: "2026-06-19: vllm-metal + Qwen2.5-7B-Instruct-4bit DEPLOYED on Mac mini M4 (192.168.1.214:8012, Anthropic /v1/messages, launchd). All 3 bg-model knobs flipped to local. Forced-tool JSON classifier confirmed live on vLLM 0.23."
metadata: 
  node_type: memory
  type: project
  originSessionId: ff45170d-36dc-4817-bddf-cd90970caa93
---

Deployed the local background-task model per [[project_local_bg_model_vllmmetal_bench]]. LIVE on main, config-only (no code — forced-tool fix was already committed in v9.123.0, NOT uncommitted as that note said).

## What's running on the M4 (192.168.1.214, login alexander / pw apkaiser)
- **vllm-metal 0.3.0.dev20260618155321** (LATEST dev line, vLLM **0.23.0** core — user chose latest over the pinned stable 0.2.0/ef776ca). venv `~/.venv-vllm-metal` (uv-managed CPython 3.12.12 arm64). Install = `curl .../install.sh | bash` (~5 min, builds vLLM core from source w/ Apple clang 17).
- Model **`mlx-community/Qwen2.5-7B-Instruct-4bit`** (4.0 GB, HF cache). Served `--host 0.0.0.0 --port 8012 --enable-auto-tool-choice --tool-call-parser hermes --max-num-seqs 8`.
- **launchd `com.brain-agent.vllm-metal`** (`~/Library/LaunchAgents/`, RunAtLoad+KeepAlive, SoftResourceLimits NumberOfFiles 65536, logs `~/Library/Logs/vllm-metal.log`). Stable, runs=1.
- Verified live: `/v1/messages` returns native Anthropic shape; forced tool_use (tool_choice) → schema-valid 8/8 on the new build; KV 6.45× conc @32k, prefix-cache ~78%.

## ⚠️ venv patch REQUIRED for launchd (lose on every vllm-metal upgrade — like [[project_mempalace_venv_patches]])
`~/.venv-vllm-metal/lib/python3.12/site-packages/vllm_metal/platform.py` — `# BRAIN-PATCH` on BOTH `get_device_total_memory` AND `get_device_available_memory`: wrap `psutil.virtual_memory()` in try/except → fall back to `sysctl -n hw.memsize`. WHY: under launchd, `psutil._psosx.virtual_memory()` calls `host_statistics64(HOST_VM_INFO64)` which fails `"(ipc/mig) array not large enough"` → EngineCore dies on boot, KeepAlive crash-loops. Works fine under interactive SSH (full session), ONLY breaks under launchd. The whole `virtual_memory()` call fails (total+available together), so BOTH methods need the guard. Backups at `platform.py.bak-brain-*`. Consequence: under launchd `available` reports full 25.8 GB (sysctl total) — fine for a dedicated inference box.

## brain-agent wiring (config-only, on the Mac Studio)
- Provider **`Lokal-M4`** in config.json: `base_url http://192.168.1.214:8012/v1`, `type: anthropic`, `is_local: true`, `max_concurrent: 8`. Sidecar talks DIRECT (NO CLIProxyAPI — vllm-metal speaks Anthropic natively; user explicitly confirmed proxy not needed). Per-provider queue [[project_local_bg_model_vllmmetal_bench]].
- Model `Lokal-M4/Qwen2.5-7B-Instruct-4bit` (base_model_id Qwen2.5-7B-Instruct-4bit, profile speed, chat-only, 32k/4k).
- **ALL 3 bg knobs flipped to local** (was Option 1 = prose-only, user then said flip chat_summary too):
  - `chat_summary_model` → Lokal-M4 (drives auto-route classifier + chat summaries + wiki auto-tag/gen/diff-merge — shared knob, `_resolve_classifier_model` reads it)
  - `mempalace.chat_sync.classifier.model` → Lokal-M4 (memory classifier)
  - `tools_config.refinement.model` → Lokal-M4 (refine + soul-chat + translation TONE rewrites — broader than the 2-task note: also read in server_lib/translate/text.py + document.py)
- Auto-route classifier safe on local because **forced-tool JSON is LIVE** (`brain.classify_task_structured` always passes `forced_tool=_route_tool`, reads `forced_tool_input`, free-text fallback for non-forcing providers). The v9.123.0 "uncommitted/not loaded" status in [[project_local_bg_model_vllmmetal_bench]] is STALE — it's committed + in the running 9.163.8 build.

## Restart discipline used (per feedback memories)
brain = launchd `com.brain-agent.server` (pid file absent → launcher restart no-ops). Graceful reload = `launchctl kill SIGTERM gui/501/com.brain-agent.server` (KeepAlive respawns). NEVER SIGKILL [[feedback_never_sigkill_brain]]. Passwordless SSH installed (ed25519 pubkey in M4 authorized_keys) + sshpass for first contact.

## NOT done / open
- **Full KG-Real-Policies routing eval (≥3 reps) on the M4 model NOT run** — the formal gate before fully trusting local routing ([[feedback_eval_single_run_noise]]). Did an 8-case forced-tool probe (JSON 8/8 valid) which proves JSON-reliability on 0.23, NOT routing quality. Worth running eval/run.py wired to Lokal-M4.
- New 0.23 build is UNBENCHMARKED vs the recorded numbers — re-run /tmp/bench_local.py.
- **SEPARATE pre-existing issue on the Studio: Qdrant (:6333) is DOWN** (4884 conn-refused, MemPalace backend, breaking KG/closet project-sync). NOT caused by this work, flagged to user. Needs (re)start.
- gemma-4-e2b does NOT load on vllm-metal (architecture mismatch) — Qwen is the only option here.
