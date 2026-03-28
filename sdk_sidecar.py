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

        # Build MCP servers: brain_agent (custom tools) + external (from mcp.json)
        mcp_servers = dict(mcp_configs) if mcp_configs else {}
        if tool_defs:
            brain_mcp = self._build_brain_mcp(tool_defs, server_url, agent_id, session_id)
            mcp_servers["brain_agent"] = brain_mcp

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
