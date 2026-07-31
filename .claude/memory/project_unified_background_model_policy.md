---
name: unified-background-model-policy
description: "2026-05-17 — all background LLM auto-picks now use single policy (configured → server default_model). Dropped every \"cheapest haiku → cheapest enabled\" scan and hardcoded Anthropic fallback id."
metadata: 
  node_type: memory
  type: project
  originSessionId: 60a6bac4-188b-4b35-b05f-befa1c3d976b
---

Background LLM model resolution unified to a single rule across every auto-pick site.

**Why:** This install has no Anthropic models. The old "cheapest haiku → cheapest enabled" heuristic plus hardcoded `claude-haiku-4-5-20251001` fallbacks meant background work silently routed to whichever cheap model happened to be configured (or skipped entirely). User wanted predictable behaviour: admin picks the install default, every background path uses that.

**How to apply:**
- Policy: configured-model-for-this-use-case → server `default_model`. No haiku heuristic. No cheapest-by-cost scan. No hardcoded Anthropic ids.
- When adding a new background LLM caller, call `engine._background_model_default()` for the fallback. If the use case has its own admin setting, validate with `engine._is_model_available()` first, then drop to the helper.
- Don't reintroduce ad-hoc "look for haiku in the model id" scans — they're wrong on Anthropic-less installs.
- Two sites still deviate intentionally: vision-fallback for image attachments (picks first enabled image-capable model — server default is a chat model, no good) and transcription (needs audio-capable model). See [[llm-call-catalog]] for the full list.

## Helpers in brain.py

- `_is_model_available(mid)` — configured + enabled check.
- `_background_model_default()` — returns `_delegate_fallback_model` (server's `default_model`) when available, else `""`.
- `_resolve_model_with_fallback(primary, fallback, hardcoded_default=None)` — primary → fallback → server default → empty. `hardcoded_default` arg is kept for back-compat but defaults to `_background_model_default()`.

## Sites updated

- `server.py:_generate_chat_summary` — per-chat synopsis.
- `server.py:_profile_pick_model` — user-profile daemon.
- `brain.py:_auto_memory_extract` — auto-memory extraction.
- `brain.py` code-graph node-summary path.
- `brain.py:_find_cheapest_model` — now just returns server default.
- `brain.py:_resolve_autodream_model` — autodream.
- `brain.py:_resolve_summary_model` (LCM ContextManager).
- `brain.py` relationship discovery (live + scheduled).
- `handlers/admin.py` `/refine` endpoint.
- `handlers/admin.py` `/chat-prompt` endpoint.
- LCM `ContextManager` config defaults: `summary_model` and `summary_model_fallback` are now empty strings (were `gemini-2.5-flash` / `claude-haiku-4-5-20251001`).

## What was NOT touched

- Cost-rate tables (`_cost_rates`) for Anthropic Haiku models — these are pricing metadata, not auto-pick logic.
- `_MAX_OUTPUT_DEFAULTS["haiku"]` — output-cap table keyed by family name, not a pick.

Related: [[llm-call-catalog]], [[chat-summary-model-setting]].
