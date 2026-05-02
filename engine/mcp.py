# Extracted from claude_cli.py — MCP stdio/SSE client and manager
#
# Cross-module deps (still live in claude_cli.py):
#   - VERSION: version string constant
#   - get_tool_config(): reads tools_config.json (defined further down in claude_cli.py)
#
# Stdlib imports used in this module:

import json
import os
import subprocess
import threading
import time
import urllib.request


# --- MCP Client ---

class MCPStdioClient:
    """MCP client over stdio — launches a subprocess and communicates via JSON-RPC."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.process = None
        self._request_id = 0
        self._lock = threading.Lock()
        self.tools: list[dict] = []

    def start(self) -> bool:
        """Start the MCP server subprocess."""
        try:
            run_env = os.environ.copy()
            if self.env:
                run_env.update(self.env)
            # Use login shell to resolve commands installed via nvm/brew/etc.
            # Same approach as _build_shell_command() for execute_command.
            _exec_cfg = get_tool_config().get("execute_command", {})
            use_login_shell = _exec_cfg.get("login_shell", True)
            if use_login_shell:
                shell_path = _exec_cfg.get("shell_path", "") or os.environ.get("SHELL", "/bin/zsh")
                full_cmd = " ".join([self.command] + self.args)
                cmd = [shell_path, "-l", "-c", full_cmd]
            else:
                cmd = [self.command] + self.args
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=run_env, start_new_session=True,
            )
            # Initialize
            resp = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "brain-agent", "version": VERSION},
            })
            if resp and not resp.get("error"):
                # Send initialized notification
                self._send_notification("notifications/initialized", {})
                # List tools
                tools_resp = self._send_request("tools/list", {})
                if tools_resp and tools_resp.get("result"):
                    self.tools = tools_resp["result"].get("tools", [])
                return True
            # Capture stderr for diagnostics
            self._last_error = ""
            if self.process and self.process.stderr:
                try:
                    self._last_error = self.process.stderr.read(2000).decode("utf-8", errors="replace")
                except Exception:
                    pass
            if self.process:
                self.process.terminate()
                self.process = None
            return False
        except Exception as e:
            self._last_error = str(e)
            if self.process:
                try:
                    self.process.terminate()
                except Exception:
                    pass
                self.process = None
            return False

    def stop(self):
        """Stop the subprocess."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the MCP server. Returns JSON string."""
        resp = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if resp and resp.get("result"):
            content = resp["result"].get("content", [])
            texts = []
            images = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        images.append(block)
                    else:
                        texts.append(json.dumps(block))
                elif isinstance(block, str):
                    texts.append(block)
            payload = {"result": "\n".join(texts)}
            if images:
                payload["_mcp_images"] = images
            return json.dumps(payload)
        elif resp and resp.get("error"):
            return json.dumps({"error": resp["error"].get("message", str(resp["error"]))})
        return json.dumps({"error": "No response from MCP server"})

    def _send_request(self, method: str, params: dict) -> dict | None:
        with self._lock:
            self._request_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            return self._send_and_receive(msg)

    def _send_notification(self, method: str, params: dict):
        with self._lock:
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            self._write(msg)

    def _send_and_receive(self, msg: dict) -> dict | None:
        try:
            self._write(msg)
            return self._read()
        except Exception:
            return None

    def _write(self, msg: dict):
        if not self.process or not self.process.stdin:
            return
        data = json.dumps(msg)
        self.process.stdin.write(f"{data}\n".encode("utf-8"))
        self.process.stdin.flush()

    def _read(self, timeout: int = 30) -> dict | None:
        if not self.process or not self.process.stdout:
            return None
        import select as _select
        # Read lines until we get a JSON-RPC response (skip notifications)
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            ready, _, _ = _select.select([self.process.stdout], [], [], remaining)
            if not ready:
                return None  # Timeout
            line = self.process.stdout.readline()
            if not line:
                return None
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if "id" in msg:  # It's a response
                    return msg
                # Skip notifications
            except json.JSONDecodeError:
                continue
        return None  # Timeout


class MCPSSEClient:
    """MCP client over SSE/HTTP — connects to a running server."""

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self._request_id = 0
        self.tools: list[dict] = []

    def start(self) -> bool:
        """Initialize connection and list tools."""
        try:
            resp = self._post("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "brain-agent", "version": VERSION},
            })
            if resp and not resp.get("error"):
                self._post("notifications/initialized", {}, is_notification=True)
                tools_resp = self._post("tools/list", {})
                if tools_resp and tools_resp.get("result"):
                    self.tools = tools_resp["result"].get("tools", [])
                return True
            return False
        except Exception:
            return False

    def stop(self):
        pass  # No cleanup needed for HTTP

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the MCP server."""
        resp = self._post("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if resp and resp.get("result"):
            content = resp["result"].get("content", [])
            texts = []
            images = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        images.append(block)
                    else:
                        texts.append(json.dumps(block))
                elif isinstance(block, str):
                    texts.append(block)
            payload = {"result": "\n".join(texts)}
            if images:
                payload["_mcp_images"] = images
            return json.dumps(payload)
        elif resp and resp.get("error"):
            return json.dumps({"error": resp["error"].get("message", str(resp["error"]))})
        return json.dumps({"error": "No response from MCP server"})

    def _post(self, method: str, params: dict, is_notification: bool = False) -> dict | None:
        self._request_id += 1
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if not is_notification:
            msg["id"] = self._request_id

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.headers)

        try:
            data = json.dumps(msg).encode("utf-8")
            req = urllib.request.Request(
                f"{self.url}/message" if "/message" not in self.url else self.url,
                data=data, headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                if body.strip():
                    return json.loads(body)
                return {} if is_notification else None
        except Exception:
            return None


def _mcp_prefixed_name(server_name: str, raw_tool_name: str) -> str:
    """Build the LLM-facing tool name for an MCP tool.

    Strips a redundant leading "<server>_" from the raw tool name so servers
    that already namespace their tools (e.g., mempalace returns "mempalace_search")
    don't end up as "mcp_mempalace_mempalace_search".
    """
    redundant = f"{server_name}_"
    if raw_tool_name.startswith(redundant):
        raw_tool_name = raw_tool_name[len(redundant):]
    return f"mcp_{server_name}_{raw_tool_name}"


def _mcp_raw_name(server_name: str, prefixed_name: str, client_tools: list) -> str:
    """Reverse of _mcp_prefixed_name: return the raw tool name the server expects.

    Needed because the prefix-stripping above hides "<server>_" from the wire name.
    We look up the matching raw name in the client's tool list so dispatch still works.
    """
    prefix = f"mcp_{server_name}_"
    if not prefixed_name.startswith(prefix):
        return prefixed_name
    suffix = prefixed_name[len(prefix):]
    # First: exact match against raw tool names
    raw_names = [t.get("name", "") for t in client_tools]
    if suffix in raw_names:
        return suffix
    # Second: server-prefixed form (the one we stripped)
    server_prefixed = f"{server_name}_{suffix}"
    if server_prefixed in raw_names:
        return server_prefixed
    return suffix  # give up; let the server reject it


class MCPManager:
    """Manages MCP server connections for an agent. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.clients: dict[str, MCPStdioClient | MCPSSEClient] = {}
        self._tool_to_server: dict[str, str] = {}  # tool_name -> server_name

    def load_config(self, config_path: str) -> int:
        """Load MCP servers from a mcp.json config file. Returns count of servers started."""
        if not os.path.exists(config_path):
            return 0
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        if isinstance(config, dict) and isinstance(config.get("mcpServers"), dict):
            config = config["mcpServers"]

        count = 0
        for name, cfg in config.items():
            transport = cfg.get("transport", "stdio")
            if transport == "stdio":
                client = MCPStdioClient(
                    name=name,
                    command=cfg.get("command", ""),
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
            elif transport in ("sse", "http"):
                client = MCPSSEClient(
                    name=name,
                    url=cfg.get("url", ""),
                    headers=cfg.get("headers"),
                )
            else:
                continue

            if client.start():
                with self._lock:
                    self.clients[name] = client
                    for tool in client.tools:
                        tool_name = _mcp_prefixed_name(name, tool["name"])
                        self._tool_to_server[tool_name] = name
                count += 1
        return count

    def get_tool_definitions(self) -> list[dict]:
        """Get all MCP tool definitions in Anthropic format."""
        defs = []
        with self._lock:
            clients_snapshot = list(self.clients.items())
        for server_name, client in clients_snapshot:
            for tool in client.tools:
                prefixed_name = _mcp_prefixed_name(server_name, tool["name"])
                defs.append({
                    "name": prefixed_name,
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {
                        "type": "object", "properties": {}, "required": [],
                    }),
                })
        return defs

    def get_tool_definitions_openai(self) -> list[dict]:
        """Get all MCP tool definitions in OpenAI format."""
        defs = []
        for td in self.get_tool_definitions():
            defs.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": {
                        "type": td["input_schema"].get("type", "object"),
                        "properties": td["input_schema"].get("properties", {}),
                        "required": td["input_schema"].get("required", []),
                    },
                },
            })
        return defs

    def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Call an MCP tool by its prefixed name."""
        with self._lock:
            server_name = self._tool_to_server.get(prefixed_name)
            client = self.clients.get(server_name) if server_name else None
        if not server_name or not client:
            return json.dumps({"error": f"MCP tool '{prefixed_name}' not found"})
        original_name = _mcp_raw_name(server_name, prefixed_name, list(client.tools))
        return client.call_tool(original_name, arguments)

    def is_mcp_tool(self, name: str) -> bool:
        with self._lock:
            return name in self._tool_to_server

    def list_servers(self) -> list[dict]:
        """List all connected MCP servers and their tools."""
        result = []
        with self._lock:
            clients_snapshot = list(self.clients.items())
        for name, client in clients_snapshot:
            result.append({
                "name": name,
                "transport": "stdio" if isinstance(client, MCPStdioClient) else "sse",
                "tools": [t["name"] for t in client.tools],
                "tool_count": len(client.tools),
            })
        return result

    def connect_runtime(self, url: str, name: str, transport: str = "sse") -> dict:
        """Connect to an MCP server at runtime. Returns status dict with discovered tools."""
        with self._lock:
            if name in self.clients:
                return {"error": f"Server '{name}' is already connected"}
        if transport == "stdio":
            parts = url.split()
            client = MCPStdioClient(name=name, command=parts[0], args=parts[1:] if len(parts) > 1 else [])
        else:
            client = MCPSSEClient(name=name, url=url)

        if client.start():
            with self._lock:
                self.clients[name] = client
                for tool in client.tools:
                    tool_name = _mcp_prefixed_name(name, tool["name"])
                    self._tool_to_server[tool_name] = name
            return {
                "status": "connected",
                "name": name,
                "transport": transport,
                "url": url,
                "tools": [{"name": t["name"], "description": t.get("description", "")} for t in client.tools],
                "tool_count": len(client.tools),
            }
        detail = getattr(client, '_last_error', '') or ''
        msg = f"Failed to connect to MCP server '{name}' at {url}"
        if detail:
            msg += f"\nDetail: {detail[:500]}"
        return {"error": msg}

    def disconnect_runtime(self, name: str) -> dict:
        """Disconnect a runtime MCP server."""
        with self._lock:
            if name not in self.clients:
                return {"error": f"Server '{name}' is not connected"}
            client = self.clients.pop(name)
            to_remove = [k for k, v in self._tool_to_server.items() if v == name]
            for k in to_remove:
                del self._tool_to_server[k]
        client.stop()
        return {"status": "disconnected", "name": name}

    def stop_all(self):
        """Stop all MCP server connections."""
        with self._lock:
            clients_to_stop = list(self.clients.values())
            self.clients.clear()
            self._tool_to_server.clear()
        for client in clients_to_stop:
            client.stop()


# Global MCP manager
_mcp_manager: MCPManager | None = None
