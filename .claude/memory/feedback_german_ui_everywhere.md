---
name: feedback_german_ui_everywhere
description: All user-facing UI text must be German; from now on respond in German too. Keep established technical terms English.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 585c8748-1ef6-4ee0-ba42-98a05558854c
---

All user-facing text in the web UI (views, dialogs, buttons, headlines, descriptions, placeholders, tooltips, toasts, confirm/alert messages, error strings) MUST be German. System-prompt text sent to the LLM, console.log/debug strings, code identifiers, and API paths stay as-is. From 2026-05-25 onward, also converse with the user in German.

**Keep these technical terms in English** (do not translate): Agent, Workflow, Token, Provider, Cache, MCP, KG, Caveman, Warmup, Knowledge Graph.

**Why:** The app is a German-language product; mixed-language UI is unprofessional. The user explicitly asked for full German UI + German conversation going forward.

**How to apply:** When adding/editing any UI string in `web/index.html` or `web/js/*.js`, write it in German. Gate JS edits with [[project_summary]]'s `web/js/js_gate.sh`.
