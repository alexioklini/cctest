---
name: project_manual_web_search_websuche
description: "Manual web-search (\"Websuche\") feature — human curates URLs, server pre-fetches, web tools hard-disabled via exclude_tools"
metadata: 
  node_type: memory
  type: project
  originSessionId: 87cdf066-0218-4d0f-b29b-d5f238839a87
---

2026-05-24: Built the manual web-search / human-curated-retrieval feature ("Websuche" right-panel tab). User searches → marks URLs → sends a prompt; server pre-fetches the marked URLs and the model works strictly from that set.

**Design decisions (all user-chosen):** pre-fetched server-side (not forced tool calls); dedicated right-panel tab (not composer dropdown); hard-enforced web lockout; sticky cross-search/cross-session basket; full content (no cap); budget guard = informational token estimate only; manual URL add + drag&drop; per-session allow-further-web escape hatch checkbox, inert when basket empty.

**Implementation (5 steps, all verified, js_gate PASS, resolver unit-test PASS):**
1. `exclude_tools` field on `RequestContext` (engine/context.py); `resolve_active_tools` subtracts it (brain.py, applied to built-in tools + again after MCP merge). Generic per-turn mechanism, runs **Brain-side** — NOT plumbed through the sidecar payload (the resolver runs in sidecar_proxy._build_tool_list on the Brain side, reading the worker's request context). `POST /v1/web/search` = SearXNG passthrough, no fetch/LLM, any logged-in user.
2. `sessions.allow_further_web` (INTEGER default 0, sticky) — mirrors caveman_mode pattern exactly: DB migration + setter (db.py), Session init+load (server.py), info echo (sessions_handler.py), manage action `allow_further_web {value}`, client load into `chat.allowFurtherWeb` (sessions.js).
3. `_handle_chat` (handlers/chat.py): reads `body.web_urls_to_fetch`, pre-fetches each via `tool_web_fetch`, prepends markdown preamble to `message`. `web_locked = web_urls present AND not allow_further_web`. Worker sets `exclude_tools` when locked. Preamble is part of user `message` → stripped from wire/DB by `_ALLOWED_MSG_KEYS` like any text.
4. Websuche tab: new `web/js/panels_websuche.js` (basket = GLOBAL localStorage, dedup by url, {url,title,snippet,query,enabled}); Google-style SERP; manual add; drag&drop; bulk enable/disable/clear; token estimate; allow-further-web checkbox. Wired into panels_right.js (counts/badges/switch), index.html (tab+pane), main.css.
5. `API.streamChat` reads `webBasketEnabled()` directly (like state.currentProject) → `body.web_urls_to_fetch`. Basket NOT cleared on send. `exclude_tools` decided server-side (authoritative), client doesn't send it.

Why pre-fetch not tool-calls: local models skip mandated web_fetch — see [[project_exa_search_only_gemma_fetch_skip]]. Backend changes need a server restart to load. net-globals baseline 913→937.

**v9.18.0 fix (2026-05-24): fetched content must be EPHEMERAL.** v9.17.0 concatenated fetched page text into the persisted USER message → 80KB weather page frozen into history, re-send tomorrow replayed yesterday's stale page. Fix: fetch at TURN time (`_build_web_sources` in worker, `force_fresh=True`), inject ONLY into a transient wire copy of the last user msg (`_inject_web_preamble_into_wire`, shallow-copy) — session.messages/DB stay clean, every send re-fetches. Structured sources `[{title,url,content,error}]` recorded on assistant `metadata.web_sources` (wire-stripped by _ALLOWED_MSG_KEYS = audit-only; reaches client because load_messages doesn't filter metadata — strip is wire-only). Shown per-turn in chat view (renderAssistantMessage → 'Webquellen dieser Anfrage', each source expandable to FULL content like a web_fetch result) + session inspector. GENERAL LESSON: per-turn fetched/dynamic content goes on metadata + transient wire copy, NEVER into the persisted user message — else it freezes into replayed history. Chat 352e5696 msg 8283 still has pre-fix 80KB inline (historic, not self-healed).
