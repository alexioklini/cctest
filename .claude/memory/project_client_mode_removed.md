---
name: Client mode + client-hosted local inference removed
description: Air-gap-mode infrastructure (LLM/tool browser proxy + Electron-hosted llama.cpp + Stage-1 ambient proxy) completely removed 2026-05-05 — server has internet, never needed
type: project
originSessionId: d774de5a-e5dc-40fe-bb4a-a5d4884cb6c7
---
Removed 2026-05-05 after the user clarified the server can reach the internet directly. Air-gap mode was never needed.

**What was deleted:**

1. **Client execution mode** (originally v7.6.0) — `execution_mode: client` config flag, `proxy_request` / `proxy_tool` SSE events, `ProxyChannel` class, `_proxy_channels` registry, `get_proxy_channel` / `cleanup_proxy_channel`, `_get_execution_mode` / `_get_client_proxy_tools`, `client_proxy_tools` config, browser-side `ClientProxy` module in `web/js/api.js`, `/v1/chat/proxy-response` + `/v1/chat/proxy-tool-result` endpoints, `_handle_execution_mode_get` + `/v1/config/execution-mode`, all execution-mode badges (status bar, inspector, message metadata).

2. **Client-hosted local inference** (originally v8.13.0) — `client_models` + `client_engines` manifests, `is_model_client_executable`, `_load_client_models` + cache, `local_inference_request` SSE event, `Session.client_capabilities`, `/v1/sessions/<id>/capabilities` handshake, `/v1/client/models/*` endpoints (manifest, weights stream, admin CRUD), `/v1/client/engines`, `/v1/chat/local-inference-usage`, browser-side `LocalInference` module, Electron `desktop/local-inference.js`, llama.cpp lifecycle (lazy download, sha256 cache, FIFO queue), composer "local" chip, Settings → Client Models tab, Settings → Local Inference tab.

3. **Stage-1 ambient proxy** (built earlier this session, never shipped) — `_AmbientClient`, `pick_ambient_client_for_user`, `make_ambient_event_callback`, `register_ambient_client`, `unregister_ambient_client`, `/v1/clients/ambient-stream`, `/v1/clients/proxy-response`, `/v1/clients/proxy-tool-result`, `handlers/clients.py`, browser-side ambient SSE listener, query-string `access_token` fallback in `_get_auth_user`.

**What stayed (intentionally):**

- `is_model_local()` + `_is_local_base_url()` — still used by GDPR auto-fallback and quota bypass.
- `default_local_fallback_model` config — used by GDPR scanner's auto-fallback when PII detected.
- `_thread_local.current_user_id` pinning in scheduler / next-prompt / chat-summary / classifier / profile daemon — useful for cost attribution and audit (kept).
- `classify_chat_for_memory` rewrite to use `send_message_with_fallback` — net cleanup, kept.

**Why:** plain `urlopen` to provider endpoints works directly from the server. No proxy hop, no per-tab ambient channel, no headless agent client. One less moving part.

**How to apply:** if anything in the future needs "browser as compute device" semantics, re-design from scratch — the prior architecture had subtle ownership and lifecycle bugs (Stage 1 ambient routing was an attempt to patch one of them). CLAUDE.md changelog entries 7.6.0 and 8.13.0 are kept as historical record but the features they describe no longer exist.

**Files touched in this removal pass:**
- `brain.py` (proxy classes, manifest loaders, send_message + _run_delegate proxy branches, `_execute_tool_inner` proxy gate)
- `engine/loop.py`, `engine/models.py`, `engine/execution.py` (mirror cleanup)
- `engine/scheduler.py` (no behavior change beyond keeping user_id pinning for cost tracking)
- `server.py` (route registrations, config loading, banner print, `_get_auth_user` query-string fallback)
- `handlers/chat.py` (proxy handlers, execution_mode metadata, client_capabilities thread-local)
- `handlers/sessions_handler.py` (capabilities handshake, inspector field)
- `handlers/providers.py` (4 client-models handlers)
- `handlers/admin.py` (Server-tab read + write paths)
- `handlers/clients.py` (deleted)
- `server_lib/sessions.py` (drop client_capabilities field)
- `web/js/api.js` (deleted ClientProxy + LocalInference, ~500 LOC)
- `web/js/init.js`, `panels.js`, `chat.js`, `sessions.js`, `nav.js`, `settings.js` (callsite cleanup)
- `web/index.html` (status badge + composer chip removed)
- `web/css/main.css` (chip styling)
- `desktop/main.js`, `desktop/preload.js` (IPC + module load), `desktop/local-inference.js` (deleted)
- `config.json`, `config.example.json` (`execution_mode`, `client_proxy_tools`, `client_models`, `client_engines` keys removed)
- `CLAUDE.md`, `handlers/CLAUDE.md` (architecture sections removed)
