---
name: reference_mistral_prompt_caching
description: "Mistral API prompt-caching mechanics (opt-in via prompt_cache_key, 0.1x cached read, usage.prompt_tokens_details.cached_tokens) — the upstream Brain targets"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 56077b6e-743b-4e45-bfb9-84379605dab5
---

Mistral la Plateforme prompt caching (verified 2026-06-30 from docs.mistral.ai/studio-api/conversations/advanced/prompt-caching):

- **Opt-in, NOT automatic**: must set the SAME `prompt_cache_key` (stable app-level id — conversation/session/workflow id) on requests likely to share a prefix. Not Anthropic-style `cache_control` blocks, and not purely prefix-automatic — you pass a key.
- **Cached read price = 10% of standard input price (0.1×, 90% discount).** No separate cache-WRITE premium documented (unlike Anthropic's 1.25×/2×).
- **Usage field returned: `usage.prompt_tokens_details.cached_tokens`** (Mistral/OpenAI shape) — NOT Anthropic's `cache_read_input_tokens`/`cache_creation_input_tokens`. Translation through CLIProxyAPI is the open question for Brain.
- **64-token cache blocks**; prompts <64 prompt tokens never hit; `cached_tokens` is a multiple of 64.
- TTL not documented. Model list not exhaustive in docs (examples use mistral-large-latest).

PROBE RESULTS (2026-06-30, live, scratchpad/probe_mistral_cache.py) — END-TO-END VERIFIED:
- **mistral-direct (raw OpenAI):** `usage.prompt_tokens_details.cached_tokens` IS returned; saw 2800/2825 cached (~99%) on mistral-medium-latest. INTERMITTENT/best-effort: hit on burst calls #2,#3,#5 but 0 on #1,#4 (identical request). **mistral-large-latest cached 0/3 — caching is MODEL-DEPENDENT.** Did NOT need prompt_cache_key to engage (automatic prefix cache); key didn't force it either.
- **CLIProxyAPI (Anthropic /messages shape):** TRANSLATES correctly → Mistral `cached_tokens` becomes Anthropic **`cache_read_input_tokens`** AND subtracts it out of `input_tokens` (saw input_tokens=9, cache_read_input_tokens=2816). Works in BOTH non-streaming and STREAMING (the production path) — verified streamed message_delta carried `cache_read_input_tokens:2800, input_tokens:25` on a hit. Field is ABSENT (not 0) when no hit; sidecar defaults to 0 so that's fine.
- So: **the sidecar ALREADY captures cache_read_input_tokens for Mistral** (sidecar.py:655-656 keeps exactly that key). No upstream blocker. The loss is purely Brain-side: 4 collapse sites re-add it into tokens_in and bill flat. Cache savings (0.1×) are real, currently UNREALIZED in the ledger AND invisible.
- CLIProxyAPI appears to inject/derive its own cache behavior — no prompt_cache_key plumbing needed from Brain.

Implication for Brain: the "third cost property" is fully capturable today. Fix = stop collapsing cache_read at the 4 sites + add per-model cost_cache_read. The classification-vs-cache tension is real ONLY because cloud models vary tools per turn (prefix churn). See [[project_cache_cost_vs_classification]].