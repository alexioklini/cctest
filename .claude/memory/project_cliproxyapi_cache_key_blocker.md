---
name: project_cliproxyapi_cache_key_blocker
description: CLIProxyAPI Anthropic-path DROPS prompt_cache_key + injects volatile cch → wrecks Mistral prompt-cache hit rate (25-50% vs 92-95%); the sidecar can only speak the Anthropic path
metadata: 
  node_type: memory
  type: project
  originSessionId: 56077b6e-743b-4e45-bfb9-84379605dab5
---

2026-07-01 — measured, decisive. Prompt caching on Mistral works GREAT with prompt_cache_key, but the sidecar can't reach the path where it works.

MEASURED HIT RATES (25-call A/B, mistral-medium via each path):
- **Mistral DIRECT (api.mistral.ai, OpenAI shape):** 40% no-key → **92% with prompt_cache_key**.
- **CLIProxyAPI /v1/chat/completions (its OpenAI endpoint):** 50% → **95% with prompt_cache_key**.
- **CLIProxyAPI /v1/messages (Anthropic endpoint — the ONLY path the sidecar speaks):** 50% no-key → **28% with prompt_cache_key / 35% with cache_control block** = NO improvement, key/cc structurally lost in translation.

ROOT CAUSE (CLIProxyAPI issue #1592, known/documented): on the Anthropic path CLIProxyAPI injects a per-request volatile `cch` into x-anthropic-billing-header → prefix instability → cache degrades from ~90% to 30-40% on third-party upstreams. Its Anthropic-path caching is cache_control-based (auto-promotes to ttl:1h) and buggy (#1592, #3398). prompt_cache_key (OpenAI field) is simply not mapped on /v1/messages.

ARCHITECTURE BLOCKER: **the sidecar (sidecar/sidecar.py) ONLY has an Anthropic client path (client.messages.create) — no OpenAI client.** So it MUST go through CLIProxyAPI's Anthropic endpoint (api.mistral.ai isn't Anthropic-shape; mistral-direct provider is unreachable by the sidecar).

TWO THINGS RULED OUT (2026-07-01, verified):
- **anthropic Python SDK (0.101.0) has NO prompt_cache_key** — caching is ONLY via cache_control blocks (prompt_cache_key is OpenAI-only). So passing it via extra_body rides the wire but CLIProxyAPI's Anthropic endpoint DROPS it (maps only cache_control). Confirmed by claude-api skill.
- **CLIProxyAPI upgrade 7.2.15→7.2.45 did NOT fix the Anthropic path** — re-tested on 7.2.45: Anthropic path plain 50% / cache_control 40% / prompt_cache_key 55% (all noise, #1592 cch instability persists); OpenAI path still 55%→95% with key. Upgrade done + running (brew, homebrew.mxcl.cliproxyapi).

SO the ONLY route to reliable 95% caching = **add an OpenAI-shape client path to the sidecar** (client.chat.completions) for cache-priced Mistral models → route to CLIProxyAPI's /v1/chat/completions where prompt_cache_key works natively. BIG change: sidecar would speak 2 wire shapes (Anthropic for local oMLX/vllm-metal + everything else, OpenAI for cache-priced Mistral). Alternative = accept ~40-55% best-effort today (still real savings on the hits that land, billed 0.1×, all measured). NOT worth building the OpenAI path without user sign-off.

WHAT SHIPPED ANYWAY (v9.245.0 + follow-ups, works regardless): cost measurement (cache_read column billed 0.1×), freeze (turn-1 model+tool for cache-priced models, verified frozen:true), official rates (cortecs: medium 1.5/7.5/cache0.15, small 0.1275/0.51/cache0.01275), bg calls routed off M4-7B→mistral-small-latest, lcm_recall+user_profile prompt reordering, cost_cache_read UI field in model settings. The prompt_cache_key plumbing (sidecar extra_body + background_call/run_turn params) is IN but INERT on the Anthropic path — harmless, ready if path (a)/(b) lands. See [[project_cache_cost_vs_classification]] + [[reference_mistral_prompt_caching]].

My earlier "99% cached" claim (2026-06-30) was a lucky warm window; sustained best-effort is 25-50%.