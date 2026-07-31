---
name: project_sidecar_dropped_provider_queue
description: 2026-05-22 — SDK sidecar migration silently dropped chat from LocalProviderQueue; re-added whole-turn acquire in sidecar_proxy.run_turn
metadata: 
  node_type: memory
  type: project
  originSessionId: c74ca574-7cc2-417b-a199-243d4414b3a4
---

2026-05-22: the SDK/sidecar migration (commit `fdcb655`, "delete native loop core") removed the chat path's `_provider_queue.acquire_if(...)` along with `send_message`/`_handle_openai_response`. Post-migration ONLY `run_model_warmup` acquired the queue — **live chat hit oMLX ungated**, so a chat turn and the warmup keeper could fire concurrent requests at the serialised local wire (exactly what the 8.9.0 queue was built to prevent). The user caught this: "the queue was implemented to handle local inference no matter if sidecar is involved or not."

**Fix (live):** wrapped the SSE-drain block in `handlers/sidecar_proxy.py:run_turn` with `engine.get_provider_queue().acquire_if(_provider_name, label=purpose, ...)`. Provider name via `engine.resolve_provider_for_model(model)["provider_name"]`. No-op for cloud (max_concurrent<=0). Added `except engine.TaskCancelled: cancelled=True` so a queue-cancel (Stop / admin) is a clean cancel, not a "(Sidecar error)" reply. Emits the same `queue_wait`/`queue_acquired`/`queue_released` SSE events the UI banner expects.

**Design decision — WHOLE-TURN scope (not per-wire):** the old native loop released the slot the instant the SSE drained, before tool execution, so nested calls couldn't deadlock. The sidecar gives Brain no per-round wire markers, so per-wire release isn't feasible without new sidecar SSE events. Whole-turn hold is coarser (a long tool-using turn holds the slot across its rounds) but is the safe default on `max_concurrent=1`. **Deliberately did NOT add acquire to `run_turn_blocking`** (background/tool-triggered calls e.g. delegate at brain.py:12489) — doing so would deadlock: an outer interactive turn holding the only slot + a tool that fires a nested `background_call` on the same provider. Tradeoff: those nested background calls stay ungated, so a tool-using turn can still produce 2 concurrent oMLX requests (outer next round + nested bg). Acceptable for the dominant case (first-turn latency, simple Q&A, warmup-vs-chat contention) which is fully gated and deadlock-free.

**Gotcha that wasted time:** `resolve_provider_for_model(model)["provider_name"]` returns `"default"` (→ queue no-op, `_resolve_max_concurrent("default")==0`) UNLESS `brain._models_config` is populated. It's loaded at server startup; a throwaway `python3 -c "import brain; ..."` process has it EMPTY → resolves to "default" and looks like the queue never engages. In the running server `_models_config[gemma].provider == "Lokal"` → resolves to "Lokal" → `max_concurrent=1` → queue engages. The served oMLX provider key is **`Lokal`** (config.json providers), is_local=true. See [[project_provider_queue]].

Related: oMLX prefix-cache fix same day in [[project_omlx_head_kv_cache_regression]] (dflash_enabled=true was killing prefix KV reuse — separate root cause for the 20s first reply).
