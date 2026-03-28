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

        def _run():
            import asyncio
            asyncio.run(self._stream(body))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()

    def _build_sdk_hooks(self, server_url, agent_id):
        """Build SDK hook callbacks that call the server's /v1/hooks/run endpoint."""
        from claude_agent_sdk import HookMatcher
        import urllib.request

        def _call_hooks_endpoint(hook_type, tool_name, args, result=None):
            """Synchronous HTTP call to server hook endpoint."""
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

        async def pre_hook(hook_input, tool_name, context):
            """PreToolUse hook — calls server to check if tool should be blocked."""
            import asyncio
            args = hook_input.get("tool_input", {})
            resp = await asyncio.get_event_loop().run_in_executor(
                None, _call_hooks_endpoint, "pre", tool_name or "", args)
            blocked = resp.get("blocked")
            if blocked:
                return {"decision": "block", "reason": blocked}
            return {}

        async def post_hook(hook_input, tool_name, context):
            """PostToolUse hook — calls server for post-processing."""
            import asyncio
            args = hook_input.get("tool_input", {})
            result = hook_input.get("tool_response", "")
            if isinstance(result, (dict, list)):
                result = json.dumps(result)
            await asyncio.get_event_loop().run_in_executor(
                None, _call_hooks_endpoint, "post", tool_name or "", args, str(result)[:51200])
            return {}

        return {
            "PreToolUse": [HookMatcher(matcher="*", hooks=[pre_hook], timeout=15.0)],
            "PostToolUse": [HookMatcher(matcher="*", hooks=[post_hook], timeout=15.0)],
        }

    def _build_brain_mcp(self, tool_defs, server_url, agent_id, session_id):
        """Build an in-process MCP server with tools that call back to the main server."""
        from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
        import urllib.request

        tools = []
        for td in tool_defs:
            name = td["name"]
            desc = td.get("description", "")
            schema = td.get("input_schema", {"type": "object", "properties": {}})

            async def _handler(args, _name=name, _url=server_url, _aid=agent_id, _sid=session_id):
                """Call the main server's /v1/tools/call endpoint."""
                payload = json.dumps({
                    "name": _name, "args": args,
                    "agent_id": _aid, "session_id": _sid,
                }).encode()
                try:
                    req = urllib.request.Request(
                        f"{_url}/v1/tools/call",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp = urllib.request.urlopen(req, timeout=120)
                    data = json.loads(resp.read())
                    result_text = data.get("result", data.get("error", "No result"))
                    return {"content": [{"type": "text", "text": str(result_text)}]}
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Tool error: {e}"}], "is_error": True}

            tools.append(SdkMcpTool(
                name=name, description=desc[:1000],
                input_schema=schema, handler=_handler,
            ))

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

        # Build MCP servers: brain_agent (stdio bridge for streaming) + external (from mcp.json)
        mcp_servers = dict(mcp_configs) if mcp_configs else {}
        if tool_defs:
            # Use stdio MCP bridge instead of in-process MCP to enable real-time streaming
            # (in-process MCP causes the SDK to buffer the entire response)
            bridge_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_bridge.py")
            bridge_args = [sys.executable, bridge_script,
                           "--server-url", server_url,
                           "--agent-id", agent_id]
            if session_id:
                bridge_args.extend(["--session-id", session_id])
            mcp_servers["brain_agent"] = {
                "type": "stdio",
                "command": bridge_args[0],
                "args": bridge_args[1:],
            }

        opts_kwargs = dict(
            model=model,
            system_prompt=system_prompt,
            mcp_servers=mcp_servers,
            permission_mode=sdk_cfg.get("permission_mode", "bypassPermissions"),
            max_turns=sdk_cfg.get("max_turns", 30),
            env=provider_env,
            cwd=body.get("cwd", os.getcwd()),
            include_partial_messages=True,
        )
        if allowed_tools:
            opts_kwargs["allowed_tools"] = allowed_tools

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

        try:
            async for event in query(prompt=message, options=options):
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
            self.wfile.write(f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
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
