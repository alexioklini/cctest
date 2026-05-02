#!/usr/bin/env python3
"""Minimal MCP stdio server for testing Brain Agent's MCP infrastructure.
No external dependencies — pure stdlib JSON-RPC over stdin/stdout.
"""
import json
import sys
import os
import datetime


def send(msg: dict):
    """Write a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle_request(msg: dict) -> dict | None:
    """Handle a JSON-RPC request. Returns response or None for notifications."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "test-mcp-server", "version": "1.0.0"},
            },
        }

    elif method == "notifications/initialized":
        return None  # Notification, no response

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo back the input message. For testing MCP connectivity.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "Message to echo back"},
                            },
                            "required": ["message"],
                        },
                    },
                    {
                        "name": "server_info",
                        "description": "Return server status and environment info.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                    {
                        "name": "add",
                        "description": "Add two numbers together.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number", "description": "First number"},
                                "b": {"type": "number", "description": "Second number"},
                            },
                            "required": ["a", "b"],
                        },
                    },
                ]
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        if tool_name == "echo":
            message = args.get("message", "")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Echo: {message}"}],
                },
            }
        elif tool_name == "server_info":
            info = {
                "server": "test-mcp-server",
                "version": "1.0.0",
                "pid": os.getpid(),
                "python": sys.version,
                "cwd": os.getcwd(),
                "time": datetime.datetime.now().isoformat(),
            }
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(info, indent=2)}],
                },
            }
        elif tool_name == "add":
            a = args.get("a", 0)
            b = args.get("b", 0)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"{a} + {b} = {a + b}"}],
                },
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

    else:
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        return None


def main():
    """Main loop: read JSON-RPC messages from stdin, dispatch, respond."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(msg)
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
