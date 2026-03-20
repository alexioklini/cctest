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
        self.max_context: int | None = None

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

    def _put(self, path: str, data: dict | None = None) -> dict:
        body = json.dumps(data or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.server_url}{path}", data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
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
        self.max_context = resp.get("max_context")
        return self.session_id

    def delete_session(self):
        if self.session_id:
            try:
                self._delete(f"/v1/sessions/{self.session_id}")
            except Exception:
                pass
            self.session_id = None

    def list_sessions(self, agent: str = "main", status: str = "active") -> list[dict]:
        return self._get(f"/v1/sessions?agent={agent}&status={status}").get("sessions", [])

    def get_session_messages(self, session_id: str) -> list[dict]:
        return self._get(f"/v1/sessions/{session_id}/messages").get("messages", [])

    def session_action(self, action: str, **kwargs) -> dict:
        kwargs["action"] = action
        if self.session_id and "session_id" not in kwargs:
            kwargs["session_id"] = self.session_id
        return self._post("/v1/sessions/manage", kwargs)

    # --- Chat (SSE streaming) ---

    def chat(self, message: str, mode: str | None = None,
             project: str | None = None):
        """Send a message and yield SSE events as (event_type, data) tuples.

        Event types: text_delta, tool_call, tool_result, tool_output, done, error
        Uses raw socket for unbuffered SSE streaming.
        """
        import socket
        from urllib.parse import urlparse

        payload = {
            "session_id": self.session_id,
            "message": message,
        }
        if mode:
            payload["mode"] = mode
        if project:
            payload["project"] = project
        body = json.dumps(payload)

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

    def create_agent(self, agent_id: str, description: str = "") -> dict:
        return self._post("/v1/agents/create", {"agent_id": agent_id, "description": description})

    def delete_agent(self, agent_id: str) -> dict:
        return self._post("/v1/agents/delete", {"agent_id": agent_id})

    def pause_agent(self, agent_id: str, paused: bool = True) -> dict:
        return self._post("/v1/agents/pause", {"agent_id": agent_id, "paused": paused})

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

    def cancel_task(self, task_id: str) -> dict:
        return self._post("/v1/tasks/cancel", {"task_id": task_id})

    # --- Providers ---

    def list_providers(self) -> list[dict]:
        return self._get("/v1/providers").get("providers", [])

    def provider_action(self, action: str, **kwargs) -> dict:
        kwargs["action"] = action
        return self._post("/v1/providers", kwargs)

    # --- Services ---

    def get_services(self) -> dict:
        return self._get("/v1/services")

    # --- Teams ---

    def get_teams(self) -> dict:
        return self._get("/v1/teams")

    def team_action(self, action: str, **kwargs) -> dict:
        kwargs["action"] = action
        return self._post("/v1/teams", kwargs)

    # --- Skills ---

    def skills_browse(self, query: str, agent: str = "main") -> dict:
        return self._post("/v1/skills/browse", {"query": query, "agent": agent})

    def skills_install(self, url: str, agent: str = "main") -> dict:
        return self._post("/v1/skills/install", {"url": url, "agent": agent})

    def skills_remove(self, slug: str, agent: str = "main") -> dict:
        return self._post("/v1/skills/remove", {"slug": slug, "agent": agent})

    def skills_list(self, agent: str = "main") -> dict:
        return self._get(f"/v1/skills?agent={agent}")

    # --- Memory ---

    def get_memory_summary(self, agent: str = "main") -> dict:
        return self._get(f"/v1/agents/{agent}/memory-summary")

    def memory_summary_action(self, agent: str, action: str) -> dict:
        return self._post(f"/v1/agents/{agent}/memory-summary", {"action": action})

    # --- QMD ---

    def get_qmd_docs(self, collection: str | None = None) -> dict:
        path = "/v1/services/qmd/docs"
        if collection:
            path += f"?collection={collection}"
        return self._get(path)

    def qmd_action(self, action: str, collection: str | None = None) -> dict:
        data = {"action": action}
        if collection:
            data["collection"] = collection
        return self._post("/v1/services/qmd", data)

    # --- Projects ---

    def list_projects(self, agent: str) -> list[dict]:
        return self._get(f"/v1/agents/{agent}/projects").get("projects", [])

    def create_project(self, agent: str, name: str, description: str = "",
                       config: dict | None = None) -> dict:
        data = {"name": name, "description": description}
        if config:
            data.update(config)
        return self._post(f"/v1/agents/{agent}/projects", data)

    def get_project(self, agent: str, name: str) -> dict:
        return self._get(f"/v1/agents/{agent}/projects/{name}")

    def update_project(self, agent: str, name: str, config: dict) -> dict:
        return self._put(f"/v1/agents/{agent}/projects/{name}", config)

    def delete_project(self, agent: str, name: str) -> dict:
        return self._delete(f"/v1/agents/{agent}/projects/{name}")

    # --- Ingest ---

    def ingest_file(self, agent: str, file_path: str,
                    project: str | None = None,
                    chunk_size: int = 1500, chunk_overlap: int = 200,
                    tags: list[str] | None = None) -> dict:
        """Ingest a file via multipart upload. Falls back to JSON with path."""
        data: dict = {
            "file_path": file_path,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }
        if tags:
            data["tags"] = tags
        path = f"/v1/agents/{agent}/projects/{project}/ingest" if project else f"/v1/agents/{agent}/ingest"
        return self._post(path, data)

    def ingest_url(self, agent: str, url: str,
                   project: str | None = None,
                   chunk_size: int = 1500, chunk_overlap: int = 200,
                   tags: list[str] | None = None) -> dict:
        data: dict = {
            "url": url,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }
        if tags:
            data["tags"] = tags
        path = f"/v1/agents/{agent}/projects/{project}/ingest" if project else f"/v1/agents/{agent}/ingest"
        return self._post(path, data)

    def list_ingested(self, agent: str, project: str | None = None) -> list[dict]:
        path = f"/v1/agents/{agent}/projects/{project}/docs" if project else f"/v1/agents/{agent}/ingested"
        return self._get(path).get("documents", [])

    def delete_ingested(self, agent: str, source_hash: str,
                        project: str | None = None) -> dict:
        path = f"/v1/agents/{agent}/projects/{project}/ingested/{source_hash}" if project else f"/v1/agents/{agent}/ingested/{source_hash}"
        return self._delete(path)

    # --- Watch Folders ---

    def list_watch_folders(self, agent: str, project: str | None = None) -> list[dict]:
        path = f"/v1/agents/{agent}/projects/{project}/watch" if project else f"/v1/agents/{agent}/watch"
        return self._get(path).get("folders", [])

    def add_watch_folder(self, agent: str, path_str: str,
                         pattern: str = "*", recursive: bool = False,
                         project: str | None = None,
                         tags: list[str] | None = None) -> dict:
        data: dict = {"path": path_str, "pattern": pattern, "recursive": recursive}
        if tags:
            data["tags"] = tags
        api_path = f"/v1/agents/{agent}/projects/{project}/watch" if project else f"/v1/agents/{agent}/watch"
        return self._post(api_path, data)

    def remove_watch_folder(self, agent: str, path_str: str,
                            project: str | None = None) -> dict:
        api_path = f"/v1/agents/{agent}/projects/{project}/watch" if project else f"/v1/agents/{agent}/watch"
        return self._post(api_path, {"action": "remove", "path": path_str})

    # --- Workflows ---

    def list_workflows(self, agent: str) -> list[dict]:
        return self._get(f"/v1/agents/{agent}/workflows").get("workflows", [])

    def save_workflow(self, agent: str, name: str, definition: str) -> dict:
        return self._post(f"/v1/agents/{agent}/workflows", {"name": name, "definition": definition})

    def delete_workflow(self, agent: str, name: str) -> dict:
        return self._delete(f"/v1/agents/{agent}/workflows/{name}")

    def run_workflow(self, agent: str, name: str, variables: dict,
                     model: str | None = None) -> dict:
        data: dict = {"variables": variables}
        if model:
            data["model"] = model
        return self._post(f"/v1/agents/{agent}/workflows/{name}/run", data)

    def get_executions(self) -> list[dict]:
        return self._get("/v1/workflows/executions").get("executions", [])

    def get_execution(self, execution_id: str) -> dict:
        return self._get(f"/v1/workflows/executions/{execution_id}")

    def approve_workflow(self, execution_id: str, action: str = "approve") -> dict:
        return self._post(f"/v1/workflows/executions/{execution_id}/approve", {"action": action})

    def cancel_workflow(self, execution_id: str) -> dict:
        return self._post(f"/v1/workflows/executions/{execution_id}/cancel")

    # --- Connection test ---

    def ping(self) -> bool:
        try:
            self.status()
            return True
        except Exception:
            return False
