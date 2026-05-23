"""Tool-MCP endpoint — the sidecar's gateway back into Brain's TOOL_DISPATCH.

The sidecar runs the LLM loop in a separate process. When the model emits a
`tool_use` block, the sidecar POSTs to `/v1/tools/call` here. We re-establish
the per-turn thread-local context (agent, session, user, project, mcp manager,
etc.) on this request thread, dispatch the tool, and return the raw result.

Hard rule: result strings are returned verbatim. No truncation, no summarisation,
no cache stubs. The sidecar passes the result straight back to the model as a
`tool_result` block. Any post-processing (reference extraction, persistence,
SSE forwarding) happens on the chat-worker thread, NOT here.

Per-turn auth: the chat worker mints a nonce, stores it in `register_nonce(...)`,
and forwards the Bearer token to the sidecar as `tool_endpoint_auth`. The
sidecar echoes the header on every /v1/tools/call. Nonces auto-expire after the
turn ends (when `clear_nonce` is called from the worker's `finally`).
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback

import brain as engine


# ---------- Per-turn nonce registry ----------
#
# nonce -> {"session_id": ..., "expires_at": <monotonic timestamp>}

_NONCES: dict[str, dict] = {}
_NONCES_LOCK = threading.Lock()
_NONCE_TTL_SECONDS = 30 * 60  # 30 min hard cap; chat worker clears on finally


def mint_nonce(session_id: str) -> str:
    """Mint a one-time nonce for tool-MCP auth.

    Stored in a process-local map. The chat worker passes this back via
    `Authorization: Bearer <nonce>` on the sidecar's tool dispatch.
    """
    nonce = secrets.token_urlsafe(32)
    with _NONCES_LOCK:
        _NONCES[nonce] = {
            "session_id": session_id,
            "expires_at": time.monotonic() + _NONCE_TTL_SECONDS,
        }
    return nonce


def clear_nonce(nonce: str) -> None:
    if not nonce:
        return
    with _NONCES_LOCK:
        _NONCES.pop(nonce, None)


def _validate_nonce(nonce: str, claimed_session_id: str) -> bool:
    """Return True iff the nonce is known, unexpired, and bound to this session."""
    if not nonce:
        return False
    now = time.monotonic()
    with _NONCES_LOCK:
        entry = _NONCES.get(nonce)
        if not entry:
            return False
        if entry["expires_at"] < now:
            _NONCES.pop(nonce, None)
            return False
        # Defence-in-depth: a leaked nonce must not be reusable across sessions
        if claimed_session_id and entry["session_id"] != claimed_session_id:
            return False
    return True


def _extract_bearer(authorization_header: str | None) -> str:
    if not authorization_header:
        return ""
    parts = authorization_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization_header.strip()  # raw token fallback


# ---------- Context reconstruction ----------

def _apply_context(ctx: dict) -> None:
    """Reinstate the per-turn thread-locals the tool implementations read.

    The chat worker sets these in-process before calling the sidecar; the
    sidecar runs in a different process, so for tool dispatches coming
    BACK from the sidecar we have to rebuild the local set on this
    request-handler thread.
    """
    tl = engine._thread_local
    tl.current_session_id = ctx.get("session_id") or ""
    tl.current_user_id = ctx.get("user_id") or ""
    tl.current_team_ids = list(ctx.get("team_ids") or [])
    tl.project = ctx.get("project") or ""
    tl.note_context = ctx.get("note_context") or None
    tl.workflow_run_id = ctx.get("workflow_run_id") or ""
    tl.plan_mode = bool(ctx.get("plan_mode", False))
    tl.research_mode_override = ctx.get("research_mode_override", None)
    tl.execution_overrides = ctx.get("execution_overrides") or {}
    tl.attachment_image_model = ctx.get("attachment_image_model") or ""
    tl._current_model = ctx.get("model") or None
    # AgentConfig is the single object tool implementations look up via
    # _thread_local.current_agent. Rebuild it here.
    agent_id = ctx.get("agent_id") or "main"
    tl.current_agent = engine.AgentConfig(agent_id)
    # Memory store is per-agent
    try:
        memory_dir = (tl.current_agent.config or {}).get("memory_dir") or ""
        if memory_dir:
            tl.memory_store = engine.MemoryStore(memory_dir)
        else:
            tl.memory_store = None
    except Exception:
        tl.memory_store = None
    # Share MCP manager singleton — same as the chat worker.
    tl.mcp_manager = engine._mcp_manager
    # Caveman flags are response-style only; tools don't read them, but set
    # them anyway for completeness so any tool that introspects gets the same
    # view the worker saw.
    tl.caveman_chat = int(ctx.get("caveman_chat", 0) or 0)
    tl.caveman_system = int(ctx.get("caveman_system", 0) or 0)

    # Transparent anonymisation: install the after_file_write callback on
    # THIS thread (the tool-dispatch thread). brain._after_file_write reads
    # it from the same thread-local. The factory lives in handlers.chat
    # because it needs SessionManager access to resolve the session's
    # live_stream for SSE emission.
    _gdpr_mid = ctx.get("gdpr_mapping_id") or ""
    # Expose the mapping id directly on the thread-local so read-side tools
    # (tool_read_document, tool_read_file, tool_read_attachment) can pull
    # the active mapping from `pseudonymizer.get_mapping(...)` and
    # pseudonymise their returned text via `_gdpr_anon_tool_text`. The
    # streaming deanonymizer on the reply reverses these tokens before the
    # user sees them.
    tl._gdpr_mapping_id = _gdpr_mid or ""
    if _gdpr_mid:
        try:
            from handlers.chat import make_gdpr_after_file_write_cb
            tl._gdpr_after_file_write_cb = make_gdpr_after_file_write_cb(
                mapping_id=_gdpr_mid, session_id=tl.current_session_id or "",
                agent_id=agent_id,
            )
        except Exception:
            tl._gdpr_after_file_write_cb = None
    else:
        tl._gdpr_after_file_write_cb = None

    # Install a minimal event_callback on the tool-dispatch thread. Without
    # it, `brain._after_file_write` skips its `if ecb:` branch and never
    # calls `_register_artifact_version`, so `write_file` / `edit_file` /
    # `python_exec` produce a file on disk but no `artifacts` row and no
    # live `artifact_updated` SSE. The callback forwards into the session's
    # LiveStream so the UI's artifact panel updates live.
    if tl.current_session_id:
        try:
            from handlers.chat import make_artifact_event_callback
            tl.event_callback = make_artifact_event_callback(
                tl.current_session_id)
        except Exception:
            tl.event_callback = None
    else:
        tl.event_callback = None


# ---------- Tool dispatch ----------

def _dispatch(name: str, args: dict) -> tuple[str, bool]:
    """Run a single tool by name. Returns (result_string, is_error).

    Looks up engine.TOOL_DISPATCH (built-in tools) first; falls back to MCP
    tools via the thread-local mcp_manager. Results are stringified — JSON
    for dicts/lists, str(x) otherwise. No truncation, no summary.
    """
    fn = engine.TOOL_DISPATCH.get(name)
    if fn is not None:
        try:
            raw = fn(args)
        except Exception as e:
            return (json.dumps({
                "error": f"tool crashed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            }, ensure_ascii=False), True)
        if isinstance(raw, str):
            return raw, _looks_like_error(raw)
        return json.dumps(raw, ensure_ascii=False), _looks_like_error_dict(raw)

    # MCP tools
    mcp_mgr = getattr(engine._thread_local, "mcp_manager", None) or engine._mcp_manager
    if mcp_mgr is not None:
        try:
            for td in mcp_mgr.get_tool_definitions():
                if td.get("name") == name:
                    raw = mcp_mgr.call_tool(name, args)
                    if isinstance(raw, str):
                        return raw, _looks_like_error(raw)
                    return json.dumps(raw, ensure_ascii=False), _looks_like_error_dict(raw)
        except Exception as e:
            return (json.dumps({
                "error": f"mcp tool crashed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            }, ensure_ascii=False), True)
    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False), True


def _looks_like_error(s: str) -> bool:
    """Heuristic: a tool result whose JSON object starts with `{"error":`
    is the convention engine._err uses. The sidecar uses is_error to tag
    the tool_result block, which surfaces to the model. Avoid false
    positives on long results — only treat compact-leading JSON as an error.
    """
    if not s:
        return False
    head = s.lstrip()[:32]
    return head.startswith('{"error"')


def _looks_like_error_dict(obj) -> bool:
    return isinstance(obj, dict) and "error" in obj and len(obj) <= 4


# ---------- HTTP handler entrypoints (invoked from server.py) ----------

def handle_tools_call(handler) -> None:
    """POST /v1/tools/call — sidecar tool dispatch.

    Request body:
      {
        "name": "...",
        "args": {...},
        "context": {
          "session_id", "agent_id", "user_id", "team_ids", "project",
          "note_context", "workflow_run_id", "plan_mode",
          "research_mode_override", "execution_overrides",
          "attachment_image_model", "model", "caveman_chat", "caveman_system"
        },
        "trace_id": "..."  # optional, echoed
      }

    Response: {"result": <string>, "is_error": bool, "elapsed_ms": int}
    """
    try:
        length = int(handler.headers.get("Content-Length") or "0")
        raw = handler.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception as e:
        handler._send_json({"error": f"invalid JSON: {e}"}, 400)
        return

    name = (body.get("name") or "").strip()
    if not name:
        handler._send_json({"error": "missing tool name"}, 400)
        return

    ctx = body.get("context") or {}
    if not isinstance(ctx, dict):
        handler._send_json({"error": "context must be an object"}, 400)
        return

    # Per-turn nonce check
    nonce = _extract_bearer(handler.headers.get("Authorization"))
    if not _validate_nonce(nonce, ctx.get("session_id") or ""):
        handler._send_json({"error": "unauthorized: invalid or expired tool nonce"}, 401)
        return

    args = body.get("args") or {}
    if not isinstance(args, dict):
        handler._send_json({"error": "args must be an object"}, 400)
        return

    tool_use_id = body.get("tool_use_id") or ""
    turn_id = ctx.get("turn_id") or ""

    t0 = time.time()
    # The sidecar runs in a separate process; for tool dispatches coming BACK
    # we rebuild the per-turn context on THIS handler thread. request_context()
    # owns teardown (token-reset on exit) — no scattered per-attribute clear.
    with engine.request_context():
        _apply_context(ctx)
        result_str, is_error = _dispatch(name, args)
    elapsed_ms = int((time.time() - t0) * 1000)

    # Stash a copy for the chat-worker side proxy so it can fire the
    # downstream tool_result SSE event with the real result text.
    if turn_id:
        try:
            from handlers import sidecar_proxy
            sidecar_proxy.capture_tool_result(turn_id, tool_use_id, name,
                                               result_str, is_error)
        except Exception:
            pass

    handler._send_json({
        "result": result_str,
        "is_error": is_error,
        "elapsed_ms": elapsed_ms,
    })


def handle_tools_list(handler) -> None:
    """GET /v1/tools/list — return Anthropic-shape tool schemas.

    Read-only; no nonce required. Useful for sidecar self-discovery and
    debugging. The chat worker assembles the per-turn tool list itself
    (it knows the agent, the allowed-tool filter, and the MCP filter) and
    sends it inline in the POST /turn payload, so the sidecar never has
    to call this in normal operation.
    """
    try:
        tools = list(engine.TOOL_DEFINITIONS)
        handler._send_json({"tools": tools, "count": len(tools)})
    except Exception as e:
        handler._send_json({"error": str(e)}, 500)
