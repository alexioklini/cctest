---
name: GDPR auto-route to local model — SHIPPED v8.10.0
description: The GDPR local-model routing feature (was backlog) shipped 2026-04-23 as v8.10.0. See CLAUDE.md GDPR section for the live design; this file documents what was built vs the original plan.
type: project
originSessionId: be8df3f6-00cf-47c1-95d0-76fb1e645728
---
**Status:** shipped 2026-04-23 as v8.10.0 on top of v8.9.0's `LocalProviderQueue` (the queue-first prerequisite).

**What shipped vs original plan:**
- ✅ Local inference queue: `LocalProviderQueue` in claude_cli.py — per-provider semaphore + strict-FIFO waitlist, `GET /v1/queue/status`, admin cancel, UI pill + modal. Shipped v8.9.0.
- ✅ Auto-route background/worker calls to local: `gdpr_pick_model_for_background()` in claude_cli.py is the single hook. Called by `generate_next_prompt_suggestion`, `classify_chat_for_memory`, `_summarise_tool_result`, `_run_delegate`, `_generate_chat_summary`.
- ✅ Settings surface: `gdpr_scanner.default_local_fallback_model` in config.json, Settings → Server GDPR card dropdown filtered to `is_local=true` models only.
- ✅ Client interlock: composer model dropdown reduces to local-only when PII in draft OR loaded history; `piiEnsureLocalModel()` auto-swaps; refuse-with-toast when block is on and no local is selectable.
- ✅ Refuse path: `GDPRBlockedError` sentinel (subclasses RuntimeError) lets each caller skip cleanly.
- ✅ Three-event audit trail: `pii_detected` (every finding), `pii_auto_fallback` (swap), `pii_blocked` (refusal).

**Not shipped (out of scope for this feature):**
- Queue-depth banner in the composer specifically tied to PII-forced-local swaps (the general queue banner covers this already).
- "Route to a second local model if primary is overloaded" fallback chain — user picks one fallback model; if it's OOM/down the block fires normally.
- Team-level policy (different fallback per agent/team). Single global fallback today.

**How to apply:** if the user reports PII still leaking to cloud, check: (1) scanner enabled? (2) is the call site listed above covered by `gdpr_pick_model_for_background`? (3) is `is_model_local(model)` returning the right answer? The audit log tells the story — filter by `tool_name=gdpr_scanner` and look at action_type transitions.
