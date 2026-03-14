#!/usr/bin/env python3
"""Brain Agent HTTP Client — connects to server.py."""

import json
import urllib.request
import urllib.error


class BrainAgentClient:
    """HTTP client for the Brain Agent server."""

    def __init__(self, server_url: str = "http://127.0.0.1:8420"):
        self.server_url = server_url.rstrip("/")
        self.session_id: str | None = None

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{self.server_url}{path}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, data: dict | None = None) -> dict:
        body = json.dumps(data or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.server_url}{path}", data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _delete(self, path: str) -> dict:
        req = urllib.request.Request(f"{self.server_url}{path}", method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # --- Session ---

    def create_session(self, agent: str = "main", model: str | None = None,
                       max_context: int | None = None) -> str:
        data = {"agent": agent}
        if model:
            data["model"] = model
        if max_context:
            data["max_context"] = max_context
        resp = self._post("/v1/sessions", data)
        self.session_id = resp["session_id"]
        return self.session_id

    def delete_session(self):
        if self.session_id:
            try:
                self._delete(f"/v1/sessions/{self.session_id}")
            except Exception:
                pass
            self.session_id = None

    # --- Chat (SSE streaming) ---

    def chat(self, message: str):
        """Send a message and yield SSE events as (event_type, data) tuples.

        Event types: text_delta, tool_call, tool_result, done, error
        Uses raw socket for unbuffered SSE streaming.
        """
        import socket
        from urllib.parse import urlparse

        body = json.dumps({
            "session_id": self.session_id,
            "message": message,
        })

        parsed = urlparse(self.server_url)
        host = parsed.hostname
        port = parsed.port or 80

        sock = socket.create_connection((host, port), timeout=600)
        try:
            # Send HTTP request
            request = (
                f"POST /v1/chat HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            sock.sendall(request.encode("utf-8"))

            # Read response headers
            f = sock.makefile("rb", buffering=0)
            status_line = f.readline().decode("utf-8")
            if "200" not in status_line:
                yield ("error", {"message": f"Server returned: {status_line.strip()}"})
                return
            # Skip headers
            while True:
                header = f.readline().decode("utf-8").strip()
                if not header:
                    break

            # Read SSE events line by line (unbuffered)
            current_event = None
            while True:
                line = f.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        yield (current_event or "message", data)
                    except json.JSONDecodeError:
                        pass
        finally:
            sock.close()

    def cancel(self):
        if self.session_id:
            self._post("/v1/chat/cancel", {"session_id": self.session_id})

    # --- Agents ---

    def list_agents(self) -> list[dict]:
        return self._get("/v1/agents").get("agents", [])

    def switch_agent(self, agent: str, model: str | None = None) -> dict:
        data = {"session_id": self.session_id, "agent": agent}
        if model:
            data["model"] = model
        return self._post("/v1/agents/switch", data)

    # --- Models ---

    def list_models(self) -> list[str]:
        return self._get("/v1/models").get("models", [])

    # --- Status ---

    def status(self) -> dict:
        return self._get("/v1/status")

    # --- Schedule ---

    def list_schedule(self) -> list[dict]:
        return self._get("/v1/schedule").get("schedules", [])

    def schedule_action(self, action: str, **kwargs) -> dict:
        kwargs["action"] = action
        return self._post("/v1/schedule", kwargs)

    # --- Tasks ---

    def list_tasks(self) -> list[dict]:
        return self._get("/v1/tasks").get("tasks", [])

    # --- Connection test ---

    def ping(self) -> bool:
        try:
            self.status()
            return True
        except Exception:
            return False
