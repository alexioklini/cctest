---
name: feedback-inject-server-globals-order
description: server.py:_inject_server_globals() runs at module-load (line ~971); anything defined after that line is invisible to handler modules
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9d285d0b-0a79-4610-97ad-fbc82a5d5cda
---

`server.py` injects its module globals into every `handlers/*.py` module via `_inject_server_globals()` (around line 971). Handler modules rely on this for bare-name lookups like `ChatDB`, `server_config`, `engine`, helper functions, etc.

**The trap:** the inject runs **once, at module-load time, on line 971**. Anything defined further down in `server.py` (functions, constants, classes) is NOT yet in `vars(_srv)` when the inject runs, so it never reaches the handler modules. When a handler later tries to call such a name, it raises `NameError` — usually swallowed by a surrounding `try: ... except Exception: pass` and producing a silent no-op.

**How to apply:**
- Don't add new module-level functions to `server.py` and expect handlers to see them. Define the function in the handler module that calls it.
- If a function MUST live in `server.py` and be callable from a handler, either move the function above line 971, or have the handler `from server import name` explicitly (mind import cycles).
- When investigating a "silent no-op" symptom in a handler, first check whether the referenced name is actually present in the handler's module dict — a `NameError` swallowed by a broad `except` looks identical to the function returning early.

Historical hit:
- 2026-05-17: `_generate_chat_summary` defined at `server.py:2504`, called from `handlers/chat.py`. Inject on line 971 ran before line 2504 existed in the module dict → handler call was a NameError → silently swallowed → chat summaries never generated for anyone. Fix: move the function into `handlers/chat.py` (single source of truth at the call site).

Related: [[feedback-session-agent-vs-agent-id]] — the second bug that this masked.
