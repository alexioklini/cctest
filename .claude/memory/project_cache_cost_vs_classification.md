---
name: project_cache_cost_vs_classification
description: v9.245.0 cache-token cost dimension + turn-1 classification freeze for cache-priced models (Mistral) — what shipped + how to verify
metadata: 
  node_type: memory
  type: project
  originSessionId: 56077b6e-743b-4e45-bfb9-84379605dab5
---

SHIPPED v9.245.0 (2026-06-30, live-verified, NOT yet committed): cache-aware cost + routing freeze. Origin: user asked whether to skip prompt classification so the prefix stays stable and cached tokens (Mistral ~0.1×) get billed cheaply. Probe ([[reference_mistral_prompt_caching]]) proved Mistral returns cached tokens and CLIProxyAPI maps them to Anthropic `cache_read_input_tokens` end-to-end incl. streaming; the sidecar already captured them; Brain just threw them away.

THREE parts:
1. **Cost measurement** — new `cost_log.cache_read_tokens` column (PRAGMA migration, verified live), per-model `cost_cache_read` rate (default 0.1×cost_input when unset) in `_get_cost_rate`, `_compute_cost(+cache_read_tokens)`, `CostTracker.log_call`/`_log_call_cost`. The FOUR collapse sites that summed cache_read into tokens_in now SPLIT it out (sidecar_proxy round_end/background_call/helpdesk_call + brain.account_background_usage); tokens_in keeps input+cache_creation at FULL price (oMLX reports whole prompt under cache_creation — must NOT discount). breakdown + /v1/costs/breakdown sum cache_read per bucket/model + total_cache_read_tokens.
2. **Freeze** — `brain.model_is_cache_priced(model)` = explicit non-zero cost_cache_read (NOT the 0.1× billing default; operator opt-in). Once an Auto session routes to a cache-priced model, handlers/chat.py records `session._cache_freeze_model`, reuses model+turn-1 toolset and SKIPS the classifier on later turns → byte-stable prefix. `model_should_optimize_tools` returns False for cache-priced (never reshape tools). Non-cache-priced = unchanged (re-classify every turn). User decided all 3 AskUserQuestion defaults: cost_cache_read>0 is the flag; freeze model+tools+skip-classifier; stay frozen on capability shift.
3. **Display** — cache_read flows into live usage event + msg_metadata + done; status bar `⚡ N cached` badge (panels_chats.js, created-on-demand), per-turn stats line (chat_render.js). chat_send.js threads `_liveTurnCached`.

VERIFIED LIVE: migration ran (column present); 13+ real cache-hit rows on CLIProxyAPI/mistral-small-latest (bg calls, 160 cached tok each) billed at exactly 0.1× ($0.00005060 vs $0.00006500 full-price); _compute_cost + model_is_cache_priced unit-checked; JS-gate green (net-globals unchanged 1804); server up 9.245.0 no tracebacks. Test harness: mint PyJWT from config.json auth.jwt_secret for real user (agents/main/auth.db, e.g. admin 17368b7961d3); POST /v1/sessions {model,agent}→session_id; POST /v1/chat. NB /v1/chat is SSE → urllib read() blocks on EOF (worker runs independently per resumable-streaming; rows persist regardless of client read timeout).

OPEN: Task #8 — route ALL bg LLM calls (classifier/summary/profile, currently local for cost) to cache-priced mistral-small: repeated-prefix bg prompts would bill cached ~0.1× (€0.013/Mtok w/ explicit cost_cache_read=0.013). Needs prompt_cache_key stability per bg purpose + measured hit rate first (now measurable via the new column). Cache hits are best-effort/intermittent + MODEL-dependent (mistral-medium cached, mistral-large 0% in probe). Currently NO model has explicit cost_cache_read set → billing uses 0.1× default (measurement live) but freeze is OFF everywhere (conservative). To arm freeze: set cost_cache_read on a model.