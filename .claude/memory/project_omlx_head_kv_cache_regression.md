---
name: project_omlx_head_kv_cache_regression
description: 2026-05-20 oMLX HEAD-f6f4269 broke cross-request KV-prefix reuse — warm prime 3s but first chat turn 22s (full prefill)
metadata: 
  node_type: memory
  type: project
  originSessionId: 454afaf1-3a95-4e92-883a-dcd7ef5b54c8
---

2026-05-20: gemma-4-26B first-chat-reply latency regressed from ~5-6s to ~22s. **Root cause: oMLX, not Brain.**

**Diagnosis chain:** warmup prime steady-state = ~3s (oMLX caches KV prefix), but first real chat turn = 22s ≈ full cold prefill of system+tools prompt → oMLX is NOT reusing the primed cross-request KV prefix. Brain primes correctly; oMLX drops the cache between the warmup request and the first chat request.

**Trigger:** oMLX upgraded to `HEAD-f6f4269` (Homebrew `jundot/omlx`, Cellar dated 2026-05-19 18:50) — a dev build past stable 0.3.8. Timing coincides exactly with the slowdown. oMLX has a history of prefix-cache/first-token regressions: issue #936 (slow gen 0.3.7), #825 (prefix cache breaks tool calling on hybrid models), #1303 (kvcache quant broken 0.3.9rc1).

**Verified NOT Brain's fault:** none of the v9.9.x commits touched `_build_system_prompt` or the warmup payload (`run_model_warmup`); v9.9.8 only changed the local-vs-cloud *gate*. KV prefix is byte-identical to before.

**TRUE ROOT CAUSE (v9.9.9, final):** `_build_system_prompt` injected a per-session line `Session artifact folder: <AGENTS_DIR>/<agent>/artifacts/<date>_<sessionid>`. Warmup primes the KV prefix with NO session (line absent) → real first turn HAS a session (line present) → system-prompt prefix diverged → oMLX couldn't reuse the warm-primed cache → full prefill every first turn (~20s on 26B). Proven: oMLX cache reuse WORKS (identical-prefix repeat reads 2048 cached tokens via `usage.cache_read_input_tokens`, 4.6s→0.5s); warmup-built vs turn-built system prompts hashed differently (16555 vs 16213 bytes). FIX: removed the line from `_build_system_prompt`, moved to first-user-message preamble `brain._artifact_folder_preamble_text(agent_id, session_id)`, prepended in `handlers/chat.py` when `len(session.messages)==0`. Verified warmup-built == turn-built prompt (byte-identical). Diagnostic technique that cracked it: TEMP debug log of tool-count + sysprompt sha in both `run_model_warmup` and the chat worker, compared on one real turn. The legacy `_*_preamble_text` builders look DEAD in the sidecar arch (no live callers) — separate cleanup.

**The cache-size / version theories below were WRONG (corrected after hard evidence):** NOT the oMLX version. The oMLX SSD prefix cache at `/Volumes/Scratch/omlx-cache` had bloated to **221 GB against its 111 GB `max_size` budget**, saturating the write queue → `SSD write queue full, dropping evicted block` fired **2862 times** → newly-primed prefix blocks were dropped before being persisted → every chat turn re-prefilled the full system+tools prompt (~20s). Warmup primed fine; its cached prefix was evicted before the first real turn could reuse it.

**FIX APPLIED 2026-05-20:** `brew services stop omlx` → `rm -rf /Volumes/Scratch/omlx-cache/[0-9a-f] /Volumes/Scratch/omlx-cache/_boundary_snapshots` (kept vision_features) → restart oMLX (`cache_used=0 B`, `existing_files=0` confirmed) → restart Brain to re-warm. Result: 0 write-queue-full warnings post-restart, prefix cache storing boundary snapshots again. Also downgraded HEAD→0.3.8 earlier (incidental, harmless).

**Diagnostic commands:** oMLX reports cache stats per request via `usage.cache_read_input_tokens` / `cache_creation_input_tokens` (curl `/v1/messages` with `x-api-key: brain`). NOTE block_size=1024 — prompts <1024 tokens never cache, so test reuse with a LARGE payload. oMLX log at `/opt/homebrew/var/log/omlx.log`; grep `write queue full` / `PagedSSDCacheManager initialized` (shows cache_used) / `boundary cache snapshot`.

**Recurrence watch:** if the cache re-bloats past 111 GB and thrashes again, durable fix is raising `--paged-ssd-cache-max-size` in the brew plist `ProgramArguments` (volume has 2.3 TiB free) — needs user sign-off (alters service launch). Per-model cache settings: `~/.omlx/model_settings.json` (gemma-4-26B-A4B-it-MLX-4bit entry: turboquant_kv/specprefill both OFF, dflash_in_memory_cache ON).

**SUPERSEDED (was wrong):** downgrade-to-stable theory below — 0.3.8 still showed 20s; version was never the cause.

**Note:** `Lokal` provider `max_concurrent` is now 1 (memory [[project_provider_queue]] / [[project_omlx_acceleration]] said 2 — drifted). oMLX KV/TurboQuant settings live server-side, not in Brain config.json.

---

**RECURRENCE 2026-05-22 — DIFFERENT root cause, two compounding oMLX 0.3.9 issues (NOT Brain, NOT SSD-bloat):**

Symptom returned: first chat reply ~20s on gemma-4-26B; queue showed no local run (expected — sidecar calls oMLX directly, bypassing Brain's `_provider_queue`). Session 98d85572 was a bare `agent=main, project=''` session; warmup logged `prefill done (98d85572, 19653ms)` — i.e. the warmup PRIME itself took 19.6s. SSD cache was 63G (<111G budget), zero recent `write queue full` warnings → SSD-bloat cause did NOT recur.

Hard evidence (replayed real `build_first_turn_prefix` payload, 24KB system+tools, against oMLX):
1. **Anthropic `/v1/messages` endpoint does NO prefix caching in 0.3.9.** Six DIFFERENT user msgs sharing the IDENTICAL 24KB prefix, back-to-back → ALL ~13s, `cache_read=0` / `cache_creation=0` every time. Zero reuse. (OpenAI `/v1/chat/completions` endpoint DID reuse — exact-match repeat 13s→0.7s — but even there only exact full-payload match hit, not prefix; a differing user suffix re-prefilled.)
2. **Endpoint mismatch.** `run_model_warmup` primes `{base_url}/chat/completions` (OpenAI shape, brain.py ~12237). The sidecar (`sidecar/sidecar.py:467` `client.messages.create`) hits `/v1/messages` (Anthropic shape). So warmup primes a DIFFERENT endpoint's cache than the real chat turn uses → even with perfect caching the prime lands on the wrong path.

Brain prefix construction is CORRECT (warmup sometimes hit ~2.8s historically; the 9.9.10 byte-identical-prefix fix still holds). This is purely oMLX 0.3.9 + the warmup-endpoint-vs-sidecar-endpoint split.

**CONFIRMED ROOT CAUSE + FIX (same session, after controlled A/B):** the served model `gemma-4-26B-A4B-it-MLX-4bit` had **`dflash_enabled=true`** in `~/.omlx/model_settings.json`, which DISABLES prefix KV caching on oMLX. Controlled test (6 distinct user msgs sharing identical 24KB prefix via `/v1/messages`):
- DFlash ON  → all ~13s, `cache_read=0` (no reuse).
- DFlash OFF + `turboquant_kv_enabled=true` → turn 2+ = ~1.7s, `cache_read=5120` (full prefix reuse).
Fix: set `dflash_enabled=false` + `turboquant_kv_enabled=true` for that model in `~/.omlx/model_settings.json`, then BOUNCE the oMLX serve subprocess (`kill $(pgrep -f 'omlx.cli serve')` — parent `/Applications/oMLX.app/Contents/MacOS/oMLX` respawns it in ~12s; oMLX reads model_settings only at model-LOAD time, no reload endpoint). Matches the known-good shape in [[project_omlx_acceleration]] (DFlash unavailable, TurboQuant KV on). Config had drifted to DFlash-on / TurboQuant-off. Backup taken at /tmp/omlx_model_settings.preflight.bak.

**Endpoint-mismatch was a RED HERRING (disproven):** warmup primes OpenAI `/chat/completions` (user='.'), sidecar reads Anthropic `/v1/messages` (real user msg) — but oMLX keys its KV cache on the TOKEN PREFIX, not the endpoint, so a warmup prime on the OpenAI endpoint IS reused by the Anthropic-endpoint turn. Verified: warmup prime then a different-user-msg turn → `cache_read=5120`, 1.84s. The OpenAI-shape and Anthropic-shape system prompts are byte-identical (`build_first_turn_prefix` differs only in tool serialization). NO warmup code change needed.

**The "0.3.9 regression" hypothesis was UNSUPPORTED and withdrawn** — never had changelog/issue evidence; oMLX version was never the cause (it's the DFlash flag). Brain prefix construction correct (9.9.10 fix holds). "Queue shows no local run" is expected: chat → sidecar → oMLX directly, bypassing Brain's `_provider_queue`.

**Recurrence watch (NEW):** if first-reply latency spikes again, FIRST check `~/.omlx/model_settings.json` for the served model's `dflash_enabled` — it must be `false`. The oMLX UI or an update can flip it back on. Diagnostic: 6-turn same-prefix test via `/v1/messages` watching `cache_read_input_tokens` (0 = caching dead).
