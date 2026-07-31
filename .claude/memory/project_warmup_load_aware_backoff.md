---
name: warmup-load-aware-backoff
description: 2026-05-15 — warmup keeper now defers when its target provider has live user traffic (eval runs no longer collide with keeper-triggered prefills)
metadata: 
  node_type: memory
  type: project
  originSessionId: 6a44543c-73d9-47db-bace-21bca96ec48d
---

## Problem
During an eval run with a local model (oMLX, max_concurrent=2), the
warmup keeper would re-fire mid-run because some other warmup-flagged
model's `next_due_ts` came up. The 26B prime-fill enqueued behind the
eval's chat ticket, blocking the next real turn for ~13s and producing
500s / cascading timeouts on subsequent eval questions.

## Fix
Three coordinated changes:

1. **`_ProviderQueueSlot.last_release_ts`** — new field, updated in the
   ticket-release `finally` block in `LocalProviderQueue.acquire_if`
   (brain.py around L12652) only when `ticket.label != "warmup"`. So it
   tracks "real user load just finished at T."

2. **`LocalProviderQueue.provider_busy(name, grace_seconds=15.0)`** —
   returns True if any of: a non-warmup ticket is active, any ticket
   waits in FIFO, or `now - last_release_ts < grace_seconds`. Providers
   without a queue (cloud, max_concurrent=0) always return False.

3. **`_warmup_keeper_loop`** (server.py) — for every candidate model,
   resolve its provider via `engine.resolve_provider_for_model(mid)`
   and skip if `pq.provider_busy(provider_name, grace)`. Applied to
   both passes (prime + warm_pool.try_build). Grace window configurable
   via `config.json → warmup.load_grace_seconds` (default 15s).

## Why this works
The keeper polls every `warmup.interval_seconds` (default 30s) and is
also woken explicitly on config changes / chat completion. Skipping a
busy provider just costs one cycle; the next cycle re-checks. During an
eval run where chat tickets land every few seconds, the grace window
keeps `provider_busy=True` continuously until the run ends, then the
keeper picks up immediately after the final release + grace.

The per-session `_trigger_warmup` (called when a user opens a session)
is **deliberately not gated** — the user is explicitly asking for that
model to be warm-by-send-time and is willing to pay the latency.

## Config
```json
{ "warmup": { "load_grace_seconds": 15 } }
```
0 disables the grace window (busy=True only on active/waiting tickets).

## Verification
After restart, the keeper logs `[warmup-keeper] deferred (provider busy):
[(model, provider), ...]` whenever it skips a cycle. Idle restart on
2026-05-15 17:31 produced the expected `[warmup-keeper] gemma-4-26B...:
warm (full, 13551ms)` with no deferred line because the provider was idle.
