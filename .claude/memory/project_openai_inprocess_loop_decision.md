---
name: project_openai_inprocess_loop_decision
description: "2026-07-01 DECISION — go Option C (drop Anthropic SDK + sidecar subprocess, run in-process OpenAI-shape loop in Brain) to unlock reliable 95% Mistral prompt caching. Handover being written."
metadata: 
  node_type: memory
  type: project
  originSessionId: 56077b6e-743b-4e45-bfb9-84379605dab5
---

2026-07-01 — user DECIDED on **Option C** for the caching architecture, after the prompt-cache investigation ([[project_cliproxyapi_cache_key_blocker.md]]).

THE THREE OPTIONS THAT WERE ON THE TABLE:
- A (status quo): keep sidecar + Anthropic SDK; accept ~40-55% best-effort caching (CLIProxyAPI Anthropic-path bug #1592, confirmed on 7.2.45). Everything already built (v9.245.0 freeze/measurement/routing/rates/UI + inert prompt_cache_key plumbing) works here.
- B: keep sidecar process, swap Anthropic SDK→OpenAI loop inside it. 95% caching, drops CLIProxyAPI translation. Medium rewrite.
- **C (CHOSEN): drop BOTH the Anthropic SDK AND the sidecar subprocess; run an in-process OpenAI-shape streaming loop in a Brain thread → CLIProxyAPI /v1/chat/completions (95% cache w/ prompt_cache_key) + local servers (oMLX/vllm-metal already speak OpenAI — warmup path proves it).**

WHY C IS COHERENT (from 2 audits):
- prompt_cache_key works ONLY on OpenAI shape: CLIProxyAPI OpenAI endpoint 55%→95% with key; Anthropic endpoint stuck 40-55% (drops the key / buggy cache_control). anthropic SDK has NO prompt_cache_key (cache_control-only). Verified live.
- The scary Anthropic-shape dependencies are NON-issues: thinking-block SIGNATURES are never persisted/replayed cross-turn (thinking rows dropped on wire between turns, sidecar_proxy.py:102-103); cache_control not used (purged v5.7-7.2). So OpenAI reasoning (reasoning_content delta) won't break anything.
- Sidecar's PRIMARY justification = "hosts the Anthropic SDK agentic loop." Drop the SDK → hand-write the loop anyway → the subprocess boundary's main reason evaporates. In-process = a direct engine.TOOL_DISPATCH[name](args) call instead of the cross-process /v1/tools/call HTTP round-trip + nonce + context-rebuild (tool_mcp.py) — big deletion.
- All 3 chat providers currently serve NATIVE Anthropic /v1/messages (deliberate unification): CLIProxyAPI (translation IS its product), Lokal-M4/vllm-metal (_comment: native /v1/messages), oMLX (_AccumulatedMessage exists for its Anthropic-endpoint quirks). C flips them to their OpenAI /v1/chat/completions endpoints.

THE ONE REAL TRADE (keep in mind for handover): crash isolation. A malformed huge provider stream / OOM in the loop currently can't take Brain down (separate process). In-process it can — handover must address bounding stream size + exception isolation per turn.

DELIVERABLE DONE: **handover written to `OPENAI_INPROCESS_LOOP_HANDOVER.md`** (repo root) — implement C in a fresh session from that doc. Boundary audit confirmed verdict (b): subprocess is ~700 LOC of SDK-venv artifact; every stated reason is anthropic-SDK-specific (anyio poisoning, venv isolation); resumability is 100% Brain-side + the sidecar restart-resume NEVER fires in prod (supervisor kills sidecar with Brain → always 404 branch, SDK_PHASE5_PROGRESS.md:365-367); no concurrency benefit (both ThreadingMixIn). Only real trade = crash isolation (mitigate: per-turn try/except + bound stream). In-process tool dispatch = direct engine.TOOL_DISPATCH[name](args), deletes tool_mcp.py(365)+nonce+_apply_context(90)+capture+SSE-translation+double-drain. Grounded in file:line, staged, with fallback + verify gates + the 95%-cache verification harness (mint PyJWT from config.json auth.jwt_secret, real user agents/main/auth.db admin 17368b7961d3, POST /v1/sessions then /v1/chat). See [[project_cache_cost_vs_classification]] for what's already shipped/committed (9.245.0, commit f9790f45).