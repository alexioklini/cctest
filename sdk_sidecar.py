#!/usr/bin/env python3
"""SDK Sidecar — lean process for Agent SDK streaming.

MUST NOT import claude_cli — that module's side effects break anyio subprocess streaming.
All context (system prompt, provider env) is passed via the HTTP request from the main server.
"""

import json
import os
import socket
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

SIDECAR_PORT = int(os.environ.get("SDK_SIDECAR_PORT", "8421"))


class SidecarHandler(BaseHTTPRequestHandler):
    wbufsize = 0

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/query":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Run async stream directly in the handler thread.
        # Using a fresh event loop avoids anyio cancel scope issues
        # that occur when nesting asyncio.run() inside threads.
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._stream(body))
        finally:
            loop.close()

    def _build_sdk_hooks(self, server_url, agent_id):
        """Build SDK hook callbacks per SDK docs: (input_data, tool_use_id, context).

        PreToolUse: calls server to check if tool should be blocked.
        PostToolUse: fire-and-forget audit call (async mode, non-blocking).
        """
        from claude_agent_sdk import HookMatcher
        import asyncio

        def _call_hooks_endpoint(hook_type, tool_name, args, result=None):
            """Synchronous HTTP call to server hook endpoint."""
            import urllib.request
            payload = json.dumps({
                "agent_id": agent_id,
                "hook_type": hook_type,
                "tool_name": tool_name,
                "args": args,
                "result": result or "",
            }).encode()
            try:
                req = urllib.request.Request(
                    f"{server_url}/v1/hooks/run",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=10)
                return json.loads(resp.read())
            except Exception:
                return {}

        async def pre_hook(input_data, tool_use_id, context):
            """PreToolUse hook — correct signature per SDK docs."""
            tool_name = input_data.get("tool_name", "")
            args = input_data.get("tool_input", {})
            resp = await asyncio.get_event_loop().run_in_executor(
                None, _call_hooks_endpoint, "pre", tool_name, args)
            blocked = resp.get("blocked")
            if blocked:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": blocked,
                    }
                }
            return {}

        async def post_hook(input_data, tool_use_id, context):
            """PostToolUse hook — fire-and-forget (async mode per SDK docs)."""
            tool_name = input_data.get("tool_name", "")
            args = input_data.get("tool_input", {})
            result = input_data.get("tool_response", "")
            if isinstance(result, (dict, list)):
                result = json.dumps(result)
            # Fire-and-forget: don't block the agent loop for audit logging
            asyncio.get_event_loop().run_in_executor(
                None, _call_hooks_endpoint, "post", tool_name, args, str(result)[:51200])
            return {"async_": True}

        return {
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_hook], timeout=15.0)],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post_hook], timeout=15.0)],
        }

    def _build_brain_mcp(self, tool_defs, server_url, agent_id, session_id):
        """Build an in-process MCP server with tools that call back to the main server.

        Uses the @tool decorator pattern from the SDK docs with proper async HTTP
        calls (aiohttp/asyncio) to avoid blocking the event loop during tool execution.
        """
        from claude_agent_sdk import tool, create_sdk_mcp_server
        import asyncio

        async def _call_server_async(name, args, url=server_url, aid=agent_id, sid=session_id):
            """Non-blocking HTTP call to the main server's /v1/tools/call endpoint."""
            payload = json.dumps({
                "name": name, "args": args,
                "agent_id": aid, "session_id": sid,
            }).encode()
            # Use asyncio to run the HTTP call in a thread pool (non-blocking)
            loop = asyncio.get_event_loop()
            def _sync_call():
                import urllib.request
                req = urllib.request.Request(
                    f"{url}/v1/tools/call",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=120)
                return json.loads(resp.read())
            return await loop.run_in_executor(None, _sync_call)

        tools = []
        for td in tool_defs:
            name = td["name"]
            desc = td.get("description", "")
            schema = td.get("input_schema", {"type": "object", "properties": {}})

            # Use @tool decorator as recommended by SDK docs
            @tool(name, desc[:1000], schema)
            async def _handler(args, _name=name):
                try:
                    data = await _call_server_async(_name, args)
                    result_text = data.get("result", data.get("error", "No result"))
                    return {"content": [{"type": "text", "text": str(result_text)}]}
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Tool error: {e}"}], "is_error": True}

            tools.append(_handler)

        return create_sdk_mcp_server("brain_agent", "1.0", tools=tools)

    async def _stream(self, body):
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        from claude_agent_sdk.types import StreamEvent

        self._cancelled = False  # Set by _sse on broken pipe

        message = body.get("message", "")
        model = body.get("model", "claude-sonnet-4-6")
        system_prompt = body.get("system_prompt", "")
        provider_env = body.get("provider_env", {})
        sdk_cfg = body.get("sdk_cfg", {})
        sdk_session_id = body.get("sdk_session_id")
        thinking_level = body.get("thinking_level")
        mcp_configs = body.get("mcp_configs", {})
        tool_defs = body.get("tool_defs", [])
        server_url = body.get("server_url", "http://127.0.0.1:8420")
        agent_id = body.get("agent_id", "main")
        session_id = body.get("session_id")
        allowed_tools = body.get("allowed_tools")

        hooks_enabled = body.get("hooks_enabled", False)

        # Build MCP servers: brain_agent (HTTP MCP for streaming) + external (from mcp.json)
        mcp_servers = dict(mcp_configs) if mcp_configs else {}
        if tool_defs:
            # Use HTTP MCP transport: connects to main server's /mcp endpoint.
            # This enables real-time streaming — in-process MCP (create_sdk_mcp_server)
            # causes the SDK to buffer all events until the turn completes.
            mcp_servers["brain_agent"] = {
                "type": "http",
                "url": f"{server_url}/mcp",
                "headers": {
                    "X-Agent-Id": agent_id,
                    "X-Session-Id": session_id or "",
                },
            }

        # Build allowed_tools: always include MCP tools (as per SDK docs)
        effective_allowed = list(allowed_tools) if allowed_tools else []
        for srv_name in mcp_servers:
            effective_allowed.append(f"mcp__{srv_name}__*")

        opts_kwargs = dict(
            model=model,
            system_prompt=system_prompt,
            mcp_servers=mcp_servers,
            permission_mode=sdk_cfg.get("permission_mode", "bypassPermissions"),
            max_turns=sdk_cfg.get("max_turns", 30),
            env={
                **provider_env,
                # Disable tool search — loads all tool defs into context directly.
                # With ~24 tools this is faster than searching, and avoids the
                # search round-trip that may block streaming.
                "ENABLE_TOOL_SEARCH": "false",
            },
            cwd=body.get("cwd", os.getcwd()),
            include_partial_messages=True,
        )
        if effective_allowed:
            opts_kwargs["allowed_tools"] = effective_allowed

        # Register SDK hooks that call back to server's /v1/hooks/run
        if hooks_enabled and server_url:
            sdk_hooks = self._build_sdk_hooks(server_url, agent_id)
            if sdk_hooks:
                opts_kwargs["hooks"] = sdk_hooks

        # Load Claude Code plugins (skills/commands from ~/.claude)
        cc_plugin_paths = body.get("cc_plugin_paths", [])
        if cc_plugin_paths:
            from claude_agent_sdk import SdkPluginConfig
            opts_kwargs["plugins"] = [
                SdkPluginConfig(type="local", path=p)
                for p in cc_plugin_paths if os.path.isdir(p)
            ]

        options = ClaudeAgentOptions(**opts_kwargs)
        if sdk_session_id:
            options.resume = sdk_session_id

        if thinking_level and thinking_level != "none":
            from claude_agent_sdk import ThinkingConfigEnabled
            budgets = {"low": 2048, "medium": 8192, "high": 32768}
            options.thinking = ThinkingConfigEnabled(
                budget_tokens=budgets.get(thinking_level, 8192),
            )

        full_text = ""
        tool_calls = []
        import time as _t
        _t0 = _t.monotonic()
        _evt_count = 0

        try:
            async for event in query(prompt=message, options=options):
                _evt_count += 1
                _elapsed = _t.monotonic() - _t0
                if isinstance(event, StreamEvent) and _evt_count <= 5:
                    print(f"[sidecar] event#{_evt_count} at {_elapsed:.3f}s type={event.event.get('type','?')}", file=sys.stderr, flush=True)
                elif isinstance(event, ResultMessage) and _evt_count <= 3:
                    print(f"[sidecar] ResultMessage at {_elapsed:.3f}s", file=sys.stderr, flush=True)
                if self._cancelled:
                    break
                if isinstance(event, StreamEvent):
                    raw = event.event
                    evt_type = raw.get("type", "")
                    if evt_type == "content_block_delta":
                        delta = raw.get("delta", {})
                        if delta.get("type") == "text_delta":
                            txt = delta.get("text", "")
                            if txt:
                                self._sse("text_delta", {"text": txt})
                                full_text += txt
                        elif delta.get("type") == "thinking_delta":
                            txt = delta.get("thinking", "")
                            if txt:
                                self._sse("thinking_delta", {"text": txt})
                    elif evt_type == "content_block_start":
                        block = raw.get("content_block", {})
                        if block.get("type") == "tool_use":
                            info = {"name": block.get("name", ""), "args": {}}
                            tool_calls.append(info)
                            self._sse("tool_call", info)

                elif isinstance(event, ResultMessage):
                    if event.result and not full_text:
                        full_text = event.result
                    usage = getattr(event, "usage", None) or {}
                    self._sse("_result", {
                        "text": full_text,
                        "sdk_session_id": getattr(event, "session_id", None),
                        "tokens_in": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                        "tokens_out": usage.get("output_tokens", 0),
                        "cost": getattr(event, "total_cost_usd", 0) or 0,
                        "tools": tool_calls,
                    })
        except Exception as e:
            self._sse("error", {"message": str(e)})

    def _sse(self, event_type, data):
        try:
            payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
            self.wfile.write(payload)
            self.wfile.flush()
            # Also push at the TCP level to ensure immediate delivery
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._cancelled = True


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = ThreadedServer(("127.0.0.1", SIDECAR_PORT), SidecarHandler)
    print(f"SDK Sidecar on http://127.0.0.1:{SIDECAR_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
