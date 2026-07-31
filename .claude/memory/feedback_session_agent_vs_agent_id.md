---
name: feedback-session-agent-vs-agent-id
description: "Sidecar background_call needs session.agent_id (string), not session.agent (AgentConfig instance) — JSON-serialize crashes silently otherwise"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9d285d0b-0a79-4610-97ad-fbc82a5d5cda
---

When calling `sidecar_proxy.background_call(agent_id=…, …)` (or any place that JSON-serializes the agent identifier), pass `session.agent_id` — NOT `session.agent`.

`session.agent` is the `AgentConfig` **instance** (`server.py:335`). `session.agent_id` is the string id.

**Why:** the sidecar proxy serializes its `tool_context` dict to JSON before sending. An AgentConfig object raises `TypeError: Object of type AgentConfig is not JSON serializable`. The error is usually swallowed by a surrounding `except Exception: pass` (background threads don't want to crash the worker), so the symptom is "nothing happens" — not a visible failure.

**How to apply:** any new background-call call site, double-check the `agent_id=` argument is a string. Same applies if you ever stash agent identity into `_thread_local.current_agent_id`, MCP context dicts, or anything that will be JSON-encoded downstream.

Historical hits:
- 2026-05-16 (v8.40.1): `brain.py:generate_next_prompt_suggestion` — next-prompt silently failed for everyone
- 2026-05-17: `handlers/chat.py:_generate_chat_summary` after moving the function from server.py into the chat handler — same line, copied 1:1 from old server.py code

Related: [[project_unified_background_model_policy]] — background callers go through the sidecar; that path is what surfaces this bug class.
