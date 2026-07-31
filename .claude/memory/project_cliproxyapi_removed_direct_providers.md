---
name: project_cliproxyapi_removed_direct_providers
description: v9.278.0 (2026-07-04) CLIProxyAPI REMOVED — cloud models direct via Kilo + mistral-direct; cache only measurable on real multi-turn sessions
metadata: 
  node_type: memory
  type: project
  originSessionId: 75f57458-d4ce-42db-8505-76dd2449f679
---

v9.278.0 (2026-07-04, commit 977d0cff): **CLIProxyAPI (:8317) completely removed** — it had only been needed for the deleted sidecar's Anthropic path and was just proxying two API-key providers. Cloud models now DIRECT:

- **Kilo** provider (kilo.ai/api/openrouter, openrouter dialect): glm-5.2, kimi-k2.6, deepseek-v4-pro/-flash, gemma-cloud. Brain-side ids UNCHANGED (sessions/benchmarks/costs intact); upstream id in `base_model_id` (`z-ai/glm-5.2`, `moonshotai/kimi-k2.6`, `deepseek/*:discounted`). `get_api_model_id` resolves via base_model_id.
- **mistral-direct** (api.mistral.ai, the vibe-cli key — SAME key CLIProxyAPI used): all Mistral models. Enabled scoped entries `CLIProxyAPI/mistral-{medium-3.5,small-latest}` migrated with all settings+benchmarks onto the PLAIN ids; upstream alias medium-3.5→`mistral-medium-latest`. All 16 service-model refs + 305 sessions-DB rows remapped (`CLIProxyAPI/` prefix stripped).

**Gotchas:**
- cliproxyapi.conf YAML values are QUOTED — extracting the Kilo key with `\S+` captured the quotes → Kilo 401 `PAID_MODEL_AUTH_REQUIRED`. Strip quotes.
- **Upstream prompt caches (Kilo AND Mistral) only engage on real conversation shapes**: synthetic identical-prompt probes (with prompt_cache_key, streaming or not, 3.5k tokens) ALWAYS report cached=0 on both upstreams. Real 3-turn sessions: glm turn 3 cache_read=2176, mistral-medium turn 3 cache_read=5008. NEVER judge cache behavior via curl repeats.
- `reasoning_effort:"none"` (the [[9.277.1]] thinking-off fix) is honored by Kilo direct — verified all 4 models.
- LocalProviderQueue `cliproxyapi=2` serialization gone → cloud turns unqueued/parallel.
- cliproxyapi brew service STOPPED but still installed (rollback: config.json.bak-pre-kilo-*, `brew services start cliproxyapi`). Its gemini/qwen OAuth creds (~/.cli-proxy-api/) unused by Brain.
- Pre-commit hook regenerates config.example.json from config.json with keys redacted (verified YOUR_API_KEY).

Follow-ups same day: composer trimmed to 7 chat models (337 auto-seeded catalog entries deleted+tombstoned; whisper/voxtral/OCR service models kept — audio/tts-only, invisible in composer). v9.279.0: `model_sync_auto_enable` knob (config + Provider-tab toggle, set FALSE here) — new catalog models now seed DISABLED, so future Kilo/Mistral catalog additions can't re-flood the composer. User confirmed stable 2026-07-04.

Related: [[project_inprocess_openai_loop]] (the sidecar deletion that made this possible).
