"""MCP server exposing Brain's TOOL_DISPATCH to an upstream that owns its own
agentic loop — today the Claude CLI behind OCP (`ocp/*` models).

WHY THIS EXISTS (and why it is not the sidecar coming back)

The retired `server_lib/tool_mcp.py` served the opposite direction: OUR loop ran
in a subprocess and called back in to run a tool. Here the UPSTREAM owns the
loop. OCP is a text bridge — it never returns `tool_calls` to the client (ADR
0013), because the CLI executes tools itself and continues past them. So the
only way a `ocp/*` turn can use Brain's tools is to hand them to the CLI in the
one shape it accepts: an MCP server it calls on its own.

That inverts the usual trade in our favour. The CLI keeps ONE process for the
whole turn, so N tool rounds cost one process start (~3 s), not N. Routing tool
calls back over the wire instead would make every round its own HTTP request and
its own CLI start.

WHAT THE CALLER MUST DO

`open_turn()` returns (url, token, mcp_config). Pass the config to the CLI
(OCP forwards `CLAUDE_MCP_CONFIG` to `--mcp-config`) and ALWAYS call
`close_turn()` in a `finally` — the token is what scopes tool access to one
turn, and a leaked token is a tool surface left open.

SECURITY POSTURE — read before widening anything here

Every dispatch runs with the FULL authority of the Brain process. The three
things standing between a prompt-injected upstream and the host are:

  1. the bearer token, minted per turn and dropped in `close_turn()`;
  2. `allowed_tools`, pinned at open_turn() from `resolve_active_tools` — the
     model cannot reach a tool the turn did not resolve, even if it knows the
     name;
  3. the loopback bind — this listener must never leave 127.0.0.1.

The GDPR seam rides on (2) as well: request context is rebuilt per dispatch so
tools see the same session, project and pseudonym mapping they would on the
worker thread. A tool dispatched without that context would read real data into
a turn the user anonymised.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain as engine

# MCP wire version we implement. The CLI negotiates this in `initialize`.
_PROTOCOL_VERSION = "2024-11-05"

# Bind is loopback-only BY DESIGN (see module docstring, point 3). Port 0 lets
# the OS pick a free one so several Brain instances can coexist.
_BIND_HOST = "127.0.0.1"

_TURN_TTL_SECONDS = 30 * 60  # backstop only; close_turn() is the real lifecycle


class _Turn:
    """One authorised turn: which tools, whose context, until when."""

    __slots__ = ("token", "tools", "tool_names", "context", "expires_at")

    def __init__(self, token: str, tools: list[dict], context: dict):
        self.token = token
        self.tools = tools
        self.tool_names = {t["name"] for t in tools}
        self.context = context
        self.expires_at = time.monotonic() + _TURN_TTL_SECONDS


_TURNS: dict[str, _Turn] = {}
_TURNS_LOCK = threading.Lock()

_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()


def _lookup_turn(token: str) -> _Turn | None:
    if not token:
        return None
    now = time.monotonic()
    with _TURNS_LOCK:
        turn = _TURNS.get(token)
        if turn is None:
            return None
        if turn.expires_at < now:
            _TURNS.pop(token, None)
            return None
        return turn


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    header = header.strip()
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return header


# ---------- tool schema translation ----------

def _to_mcp_tools(defs: list[dict]) -> list[dict]:
    """Brain tool definitions → MCP `tools/list` entries.

    Accepts BOTH shapes `resolve_active_tools` can return: the OpenAI form
    (`{type:"function", function:{name, description, parameters}}`, which is what
    the chat worker builds) and the Anthropic flat form (`{name, description,
    input_schema}`). MCP wants `inputSchema`. Getting this wrong yields an empty
    tools/list — the upstream then answers from memory and the turn still looks
    successful, so the shape is normalised here rather than assumed.
    """
    out = []
    for d in defs:
        if d.get("type") == "function" and isinstance(d.get("function"), dict):
            fn = d["function"]
            name = fn.get("name") or ""
            description = fn.get("description") or ""
            schema = fn.get("parameters")
        else:
            name = d.get("name") or ""
            description = d.get("description") or ""
            schema = d.get("input_schema") or d.get("inputSchema")
        out.append({
            "name": name,
            "description": description,
            "inputSchema": schema or {"type": "object", "properties": {}},
        })
    return [t for t in out if t["name"]]


# ---------- dispatch ----------

def _dispatch(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool. Returns (text, is_error). Result strings go back verbatim —
    no truncation here; the upstream decides what to do with them."""
    fn = engine.TOOL_DISPATCH.get(name)
    if fn is None:
        # MCP tools configured on the agent (Brain as MCP *client*) are reachable
        # through the same manager the worker uses.
        mgr = engine.get_request_context().mcp_manager or engine._mcp_manager
        if mgr is not None:
            try:
                for td in mgr.get_tool_definitions():
                    if td.get("name") == name:
                        raw = mgr.call_tool(name, args)
                        return _stringify(raw)
            except Exception as e:
                return (json.dumps({"error": f"mcp tool crashed: {type(e).__name__}: {e}"},
                                   ensure_ascii=False), True)
        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False), True

    try:
        raw = fn(args)
    except Exception as e:
        return (json.dumps({
            "error": f"tool crashed: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-2000:],
        }, ensure_ascii=False), True)
    return _stringify(raw)


def _stringify(raw) -> tuple[str, bool]:
    if isinstance(raw, str):
        head = raw.lstrip()[:32]
        return raw, head.startswith('{"error"')
    is_err = isinstance(raw, dict) and "error" in raw and len(raw) <= 4
    return json.dumps(raw, ensure_ascii=False), is_err


def _apply_context(ctx: dict) -> None:
    """Rebuild the per-turn request context on THIS dispatch thread.

    Ported from the retired sidecar endpoint: tool implementations read all of
    this off the context, and a tool running without it would act on the wrong
    session — or, with GDPR anonymisation active, hand real values to a turn the
    user chose to anonymise. Caller wraps this in `with request_context()`.
    """
    tl = engine.get_request_context()
    tl.current_session_id = ctx.get("session_id") or ""
    tl.session_id = tl.current_session_id
    tl.current_turn_id = ctx.get("turn_id") or ""
    tl.current_user_id = ctx.get("user_id") or ""
    tl.current_team_ids = list(ctx.get("team_ids") or [])
    tl.project = ctx.get("project") or ""
    tl.working_dir = ctx.get("working_dir") or None
    tl.code_graph_db = ctx.get("code_graph_db") or None
    tl.note_context = ctx.get("note_context") or None
    tl.plan_mode = bool(ctx.get("plan_mode", False))
    tl.research_mode_override = ctx.get("research_mode_override", None)
    tl.execution_overrides = ctx.get("execution_overrides") or {}
    tl.attachment_image_model = ctx.get("attachment_image_model") or ""
    tl._current_model = ctx.get("model") or None
    tl.current_agent = engine.AgentConfig(ctx.get("agent_id") or "main")
    tl.mcp_manager = engine._mcp_manager

    # GDPR: the mapping id is what read-side tools use to pseudonymise what they
    # return, and what the reply-side deanonymiser reverses.
    gdpr_mid = ctx.get("gdpr_mapping_id") or ""
    tl._gdpr_mapping_id = gdpr_mid
    if gdpr_mid:
        try:
            from handlers.chat import make_gdpr_after_file_write_cb
            tl._gdpr_after_file_write_cb = make_gdpr_after_file_write_cb(
                mapping_id=gdpr_mid, session_id=tl.current_session_id,
                agent_id=ctx.get("agent_id") or "main")
        except Exception:
            tl._gdpr_after_file_write_cb = None
    else:
        tl._gdpr_after_file_write_cb = None

    # Without an event_callback, `_after_file_write` skips artifact registration:
    # files land on disk but no artifacts row and no live SSE update.
    if tl.current_session_id:
        try:
            from handlers.chat import make_artifact_event_callback
            tl.event_callback = make_artifact_event_callback(tl.current_session_id)
        except Exception:
            tl.event_callback = None
    else:
        tl.event_callback = None


# ---------- JSON-RPC handler ----------

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the CLI's chatter out of our logs
        pass

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rpc_error(self, rid, code: int, message: str, status: int = 200) -> None:
        self._reply({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": code, "message": message}}, status)

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        try:
            length = int(self.headers.get("Content-Length") or "0")
            req = json.loads((self.rfile.read(length) if length else b"{}") or b"{}")
        except Exception as e:
            self._rpc_error(None, -32700, f"parse error: {e}", 400)
            return

        method = req.get("method") or ""
        rid = req.get("id")

        # Notifications carry no id and expect no result.
        if method.startswith("notifications/"):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        turn = _lookup_turn(_bearer(self.headers.get("Authorization")))
        if turn is None:
            self._rpc_error(rid, -32001, "unauthorized: invalid or expired turn token", 401)
            return

        if method == "initialize":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "brain-tools", "version": "1"},
            }})
            return

        if method == "tools/list":
            self._reply({"jsonrpc": "2.0", "id": rid,
                         "result": {"tools": turn.tools}})
            return

        if method == "tools/call":
            params = req.get("params") or {}
            name = (params.get("name") or "").strip()
            args = params.get("arguments")
            if not isinstance(args, dict):
                args = {}

            # Enforce the per-turn set: a model can emit any name it has ever
            # seen, and without this a turn that resolved read-only tools could
            # still reach execute_command.
            if name not in turn.tool_names:
                self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps(
                        {"error": f"tool '{name}' is not available in this turn"},
                        ensure_ascii=False)}],
                    "isError": True,
                }})
                return

            with engine.request_context():
                _apply_context(turn.context)
                text, is_err = _dispatch(name, args)

            self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_err,
            }})
            return

        self._rpc_error(rid, -32601, f"method not found: {method}")


# ---------- lifecycle ----------

def _ensure_server() -> ThreadingHTTPServer:
    global _server
    with _server_lock:
        if _server is None:
            srv = ThreadingHTTPServer((_BIND_HOST, 0), _Handler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True,
                             name="tool-mcp-server").start()
            _server = srv
        return _server


def open_turn(*, tools: list[dict], context: dict) -> tuple[str, str, dict]:
    """Authorise one turn. Returns (url, token, mcp_config).

    `tools` is the resolved wire payload from `resolve_active_tools` — the same
    list the model would otherwise see — and doubles as the allow-list.
    ALWAYS pair with `close_turn(token)` in a finally.
    """
    srv = _ensure_server()
    token = secrets.token_urlsafe(32)
    url = f"http://{_BIND_HOST}:{srv.server_address[1]}"
    with _TURNS_LOCK:
        _TURNS[token] = _Turn(token, _to_mcp_tools(tools), context)
        # Opportunistic sweep: close_turn() is the real cleanup, this only stops
        # a crashed caller's tokens from accumulating for the process lifetime.
        now = time.monotonic()
        for t in [k for k, v in _TURNS.items() if v.expires_at < now]:
            _TURNS.pop(t, None)
    mcp_config = {"mcpServers": {"brain": {
        "type": "http", "url": url,
        "headers": {"Authorization": f"Bearer {token}"},
    }}}
    return url, token, mcp_config


def close_turn(token: str) -> None:
    """Revoke a turn's token. Idempotent."""
    if not token:
        return
    with _TURNS_LOCK:
        _TURNS.pop(token, None)


def active_turns() -> int:
    """Open turns — for /v1/status and leak diagnosis."""
    with _TURNS_LOCK:
        return len(_TURNS)
