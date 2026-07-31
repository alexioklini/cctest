---
name: Local provider concurrency queue
description: Per-provider FIFO queue in claude_cli.py (LocalProviderQueue) serializes LLM calls for providers with config.json max_concurrent > 0. oMLX=1, cliproxyapi=2.
type: project
originSessionId: be1dfc30-47bd-4749-9c7e-06e7878d6960
---
**What it does**: `LocalProviderQueue` in claude_cli.py caps concurrent `/chat/completions` calls per provider via a semaphore + strict-FIFO waitlist. Added 2026-04-23.

**Why**: local LLM gateways (oMLX, cliproxyapi) can't process two requests in parallel even when multiple models are loaded — the GPU serializes internally and a second request blocks the first. Without this queue, concurrent chats / scheduled delegates / warmup stepped on each other, causing stalls and occasional 500s from cliproxyapi.

**How to apply**:
- Opt-in per provider via `providers.<name>.max_concurrent` in `config.json` (0 = unlimited = no queue). Defaults: omlx=1, cliproxyapi=2; all cloud providers = 0.
- Wrapped call sites: `send_message` main chat, `_run_delegate`, `run_model_warmup`, `classify_chat_for_memory`. `send_message_with_fallback` → covers `_summarise_tool_result` and `generate_next_prompt_suggestion` transitively.
- SSE events emitted to UI: `queue_wait` (position changes), `queue_acquired`, `queue_released`. Web UI shows per-turn "Waiting in queue, position N" banner (renderStreamingMessage) and a status-bar pill that opens a modal listing active+waiting tickets.
- API: `GET /v1/queue/status` → `{providers: {name: {max_concurrent, active_count, waiting_count, active[], waiting[]}}}`.
- Config is re-read on every acquire (no cache), so editing max_concurrent in config.json takes effect for the next call.

**Scope — slot held only during HTTP call, not during tool work** (changed from initial design):
- `send_message` and `_run_delegate` use manual `__enter__`/`__exit__` on `acquire_if` and pass a `release_slot` callback into `_handle_openai_response`.
- The handler calls `release_slot()` right after the SSE `for line in response:` loop fully drains (before any tool dispatch). The outer `try/finally` in the caller is a safety net — if the handler never reaches the release point (cancel, exception), the slot still frees.
- Why: heavy tools (exa_search, python_exec, workers) route through a worker subagent whose summariser does a nested `send_message`. If the slot stayed held across tool work, the nested call would deadlock waiting for the same slot. Also: holding the slot during 30s of tool execution blocks every other chat from the gateway even though the gateway is idle.
- Net effect: two concurrent chats using tools overlap. Only simultaneous HTTP `/chat/completions` calls serialize.

**Non-obvious invariants**:
- Warmup goes through the queue too (label="warmup"). Without this, the keeper would fire primes while a user chat is mid-stream and cliproxyapi would queue/block upstream or 500.
- Cancellation via `cancel_token` works while waiting: ticket is removed from the deque and semaphore is never acquired. Tested.
- The queue key is provider_name (not base_url). If two providers in config share a base_url, they'd have independent queues — re-evaluate if that ever happens.
- Timeout default 300s for chat/delegate, 2× HTTP timeout for warmup/classifier (they're smaller bursts).
- **No thread-local re-entrancy**: tried briefly (2026-04-23), reverted. HTTP-scoped release is simpler and gives better throughput.
