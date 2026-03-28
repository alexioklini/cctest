#!/usr/bin/env python3
"""Stdio MCP bridge — exposes Brain Agent tools to the SDK via stdio transport.

Receives MCP protocol messages on stdin, dispatches tool calls to the server's
/v1/tools/call HTTP endpoint, and returns results on stdout.

Usage: python3 mcp_bridge.py --server-url http://127.0.0.1:8420 --agent-id main

The sidecar spawns this as an stdio MCP server so the SDK can stream text
in real-time while tools remain available (in-process MCP causes SDK to buffer).
"""

import json
import sys
import os
import urllib.request


def _call_server(server_url, name, args, agent_id, session_id=None):
    """Call the main server's /v1/tools/call endpoint."""
    payload = json.dumps({
        "name": name, "args": args,
        "agent_id": agent_id, "session_id": session_id,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{server_url}/v1/tools/call",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        return data.get("result", data.get("error", "No result"))
    except Exception as e:
        return f"Tool error: {e}"


def _get_tool_defs(server_url):
    """Fetch tool definitions from the server."""
    try:
        req = urllib.request.Request(f"{server_url}/v1/tools/list")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return data.get("tools", [])
    except Exception:
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8420")
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()

    tool_defs = _get_tool_defs(args.server_url)

    # MCP stdio protocol: read JSON-RPC messages from stdin, write to stdout
    # Use Content-Length header framing (LSP-style)
    def read_message():
        """Read a JSON-RPC message from stdin."""
        headers = {}
        while True:
            line = sys.stdin.readline()
            if not line or line.strip() == "":
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip().lower()] = val.strip()
        length = int(headers.get("content-length", 0))
        if length == 0:
            return None
        body = sys.stdin.read(length)
        return json.loads(body)

    def write_message(msg):
        """Write a JSON-RPC message to stdout."""
        body = json.dumps(msg)
        sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
        sys.stdout.flush()

    # Main loop
    while True:
        try:
            msg = read_message()
        except (EOFError, KeyboardInterrupt):
            break
        if msg is None:
            break

        msg_id = msg.get("id")
        method = msg.get("method", "")

        if method == "initialize":
            write_message({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "brain_agent", "version": "1.0"},
                },
            })
        elif method == "notifications/initialized":
            pass  # No response needed
        elif method == "tools/list":
            tools = []
            for td in tool_defs:
                tools.append({
                    "name": td["name"],
                    "description": (td.get("description", "") or "")[:1000],
                    "inputSchema": td.get("input_schema", {"type": "object", "properties": {}}),
                })
            write_message({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": tools},
            })
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            result = _call_server(args.server_url, tool_name, tool_args,
                                   args.agent_id, args.session_id)
            write_message({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                },
            })
        elif method == "ping":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            # Unknown method
            if msg_id is not None:
                write_message({
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                })


if __name__ == "__main__":
    main()
