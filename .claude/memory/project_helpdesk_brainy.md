---
name: project_helpdesk_brainy
description: Brainy — friendly read-only helpdesk bot in chat (v9.21.0); own endpoint + exclusive brain-agent-guide skill + helpdesk purpose
metadata: 
  node_type: memory
  type: project
  originSessionId: 422c2919-78e4-4f94-9634-a41c3955b9ac
---

2026-05-25 (commit 3c16136, v9.21.0): **Brainy 🧠** — standalone read-only helpdesk bot reachable from the running chat, NOT the main agent. Floating buddy bottom-right (toggle via localStorage `brainy-buddy-hidden`) + composer help button (`btn-brainy-help`) → mini-chat modal. Works during main-turn streaming.

**Backend:**
- Exclusive skill: `HELPDESK_ONLY_SKILLS = {"brain-agent-guide"}` in brain.py. `AgentConfig.list_skills()` + `load_skill()` hide/refuse it unless `get_request_context().helpdesk_mode` (new RequestContext field). Flag propagates: helpdesk_call sets `tool_context.helpdesk_mode=True` → `tool_mcp._apply_context` re-applies it on the dispatch thread (where `use_skill` runs).
- New `purpose="helpdesk"` in `resolve_active_tools`/`_VALID_PURPOSES` → forces fixed `_HELPDESK_TOOLS` set (use_skill + 3 new + mempalace_query + read_document), skips defer + purpose-filter (like research_minimal).
- 3 read-only tools in `engine/tools/helpdesk_tools.py` (full 4-site wiring): `helpdesk_session_info` / `helpdesk_user_context` / `helpdesk_user_activity`. Scope from request context; read ChatDB/AuthDB/ProjectManager/_scheduler/_read_user_profile.
- `sidecar_proxy.helpdesk_call()`: STREAMING `run_turn` (not background_call — that's non-streaming) with purpose='helpdesk'. Uses **empty turn session_id** (so no `active_turns` PK collision with the main chat's resumable-stream tracking) and passes the chat session as `helpdesk_session_id`; `_apply_context` maps it to `current_session_id`/`session_id` so the tools see it.
- `handlers/helpdesk.py` (HelpdeskHandlerMixin): POST /v1/helpdesk (SSE: text_delta/tool_call/error/done), GET /v1/helpdesk/history, POST /v1/helpdesk/clear, admin GET/POST /v1/helpdesk/config. `helpdesk_history` table in chats.db (cascade-dropped in delete_session).
- Config: `config.json → helpdesk {enabled,model,max_rounds,system_prompt}` (gitignored, per-machine). Empty model → `_background_model_default()`. Default Brainy persona prompt lives in handlers/helpdesk.py as fallback.

**Frontend:** `web/js/chat_helpdesk.js` (globals `brainy*`, `_genTab_helpdesk`/`_saveHelpdeskConfig` in settings_general_tabs.js). `brainyRefreshBuddy()` called from nav.js chat case. Settings → Tools → 'Brainy' tab. net-globals baseline 945→960.

Follows [[feedback_version_two_places]] (VERSION + CHANGELOG bumped) and [[feedback_german_ui_everywhere]]. Not pushed — committed to main only.
