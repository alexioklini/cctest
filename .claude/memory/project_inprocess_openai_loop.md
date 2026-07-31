---
name: project_inprocess_openai_loop
description: "The sidecar subprocess + Anthropic SDK were DELETED (v9.246-9.247, 2026-07-01); LLM loop is now in-process OpenAI-shape via engine/llm_loop.py. Cache payoff realized."
metadata: 
  node_type: memory
  type: project
  originSessionId: 19a63b76-a5d5-4d6c-97e7-5cfe08c73ffc
---

2026-07-01: Executed OPENAI_INPROCESS_LOOP_HANDOVER.md stages 0-5 in one session (committed to main: 5de4e842 flag-gated loop, c75d9e50 event_callback fix, c0a4859c sidecar deletion). Brain VERSION 9.245.0 → **9.247.0**.

**WHY**: Mistral prompt caching via CLIProxyAPI bills cached tokens at 0.1× and only hits reliably (~95%) on the OpenAI `/v1/chat/completions` path (`prompt_cache_key` works there); the Anthropic `/v1/messages` sidecar path capped at 40-55%. Once on OpenAI shape, the Anthropic-SDK sidecar subprocess had no reason to exist → loop moved in-process.

**WHAT SHIPPED**:
- NEW `engine/llm_loop.py` `run_loop(...)`: hand-written OpenAI streaming agentic loop. Runs on the caller's thread. Tool dispatch = DIRECT `engine.TOOL_DISPATCH[name](args)` (no nonce/HTTP/context-rebuild). Emits the Brain event vocab the chat worker already consumes.
- `handlers/sidecar_proxy.py` (kept the module NAME, gutted internals): `run_turn`/`run_turn_blocking`/`background_call`/`helpdesk_call` now drive the in-process loop. `_apply_bg_context` rebuilds context for background turns; `_build_tool_list_openai`; in-process `cancel_turn` via a turn_id→Event registry.
- DELETED: `sidecar/` dir, `.venv_sdk`, `server_lib/tool_mcp.py`, nonce layer, `SidecarSupervisor` (ProcessSupervisor base + SearXNG/crawl4ai stay in the same file), `/v1/tools/{call,list}` + `/v1/sidecar/*` routes, `_translate_anthropic_event`, `_TOOL_RESULT_CAPTURE`, the anthropic dep from live code.

**NON-OBVIOUS FACTS / GOTCHAS**:
- The loop hits `{base_url}/chat/completions` regardless of provider `type` — CLIProxyAPI (`:8317/v1`, config says `type:anthropic`) serves BOTH paths on the same host. So NO provider config flip was needed. `type` field is now vestigial for chat.
- CRITICAL FIX (c75d9e50): in-process tools dispatch on the WORKER thread, whose request context has NO `event_callback` by default (only the old sidecar `/v1/tools/call` thread set one via `tool_mcp._apply_context`). Without installing one, `_after_file_write` skips artifact registration AND `ask_user`/`ask_user_for_file` emit `user_input_needed` into a None cb → hang till 300s timeout (the v9.101.12 bug). `run_turn` now installs `make_artifact_event_callback(sid)` + the GDPR after_file_write hook, restores prior values in finally.
- Mid-stream cancel: a watcher thread closes the response socket on `is_cancelled()` (parity w/ old sidecar `_watch_cancel`) — between-round polling alone can't break a blocking urllib read.
- Cache split preserved (v9.245.0): `cache_read_tokens` from `usage.prompt_tokens_details.cached_tokens`, kept SEPARATE from full-price `tokens_in`. `usage_total` uses Anthropic-shape keys (input_tokens/cache_read_input_tokens) so background_call/helpdesk_call cost extraction is unchanged.
- Boot recovery (`recover_active_turns_on_boot`) simplified: the old sidecar-event-log re-attach ALWAYS hit its 404 branch in prod (sidecar died with Brain). Now just promotes partial streaming_text + clears the row.

**VERIFIED LIVE** (mistral-medium-3.5, mistral-small-latest, Lokal-M4/Qwen2.5-7B): interactive chat, tool calls (read_file/write_file end-to-end), cache hits 76/61/80/99% on warm turns, auto-route classifier (forced-tool) + async summary + cost logging, mid-stream cancel, artifact metadata.files persist, ask_user blocks correctly. `import server` + py_compile all-modules OK; test_bgtask_group_claim 14/14.

**REMAINING / WATCH**: no long soak was done before deletion (user chose to proceed). If a regression surfaces there's NO sidecar fallback anymore — the fix is forward (patch llm_loop.py), not reverting to the sidecar. Note the cache is best-effort/intermittent + model-dependent (turns 1-2 often cold; mistral-large caches 0%). See [[project_cache_cost_vs_classification]], [[project_cliproxyapi_cache_key_blocker]].
