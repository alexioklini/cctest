#!/usr/bin/env python3
"""Brain Agent Server — HTTP API daemon for multi-frontend access."""

import argparse
import json
import os
import queue
import sys
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import claude_cli as engine

# --- Session Management ---

class Session:
    """A conversation session with an agent."""

    def __init__(self, agent_id: str = "main", model: str | None = None,
                 api_key: str = "", base_url: str = "", api_type: str = "anthropic",
                 max_context: int = 131072):
        self.id = uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        self.model = model or "claude-opus-4-5-20251101"
        self.api_key = api_key
        self.base_url = base_url
        self.api_type = api_type
        self.max_context = max_context
        self.messages: list[dict] = []
        self.cancel_token = engine.CancelToken()
        self.created_at = time.time()
        self.last_active = time.time()
        self.lock = threading.Lock()

        # Initialize agent
        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)

    def switch_agent(self, agent_id: str, model: str | None = None):
        self.agent_id = agent_id
        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)
        if model:
            self.model = model
        elif self.agent.preferred_model:
            self.model = self.agent.preferred_model
        self.messages = []


class SessionManager:
    """Thread-safe session storage."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, **kwargs) -> Session:
        session = Session(**kwargs)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.last_active = time.time()
            return s

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [{
                "id": s.id,
                "agent": s.agent_id,
                "model": s.model,
                "messages": len(s.messages),
                "created_at": s.created_at,
                "last_active": s.last_active,
            } for s in self._sessions.values()]


# --- Server globals ---

sessions = SessionManager()
server_config = {
    "api_key": "",
    "base_url": "http://localhost:8317/v1",
    "api_type": "anthropic",
    "default_model": "claude-opus-4-5-20251101",
    "max_context": 131072,
}


# --- Request Handler ---

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class BrainAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Brain Agent API."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _get_session(self) -> Session | None:
        sid = self.headers.get("X-Session-ID", "")
        if not sid:
            body = self._read_json() if self.headers.get("Content-Length") else {}
            sid = body.get("session_id", "")
        return sessions.get(sid) if sid else None

    # --- Routing ---

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/v1/status":
            self._handle_status()
        elif path == "/v1/agents":
            self._handle_list_agents()
        elif path == "/v1/models":
            self._handle_list_models()
        elif path == "/v1/sessions":
            self._handle_list_sessions()
        elif path == "/v1/schedule":
            self._handle_list_schedule()
        elif path == "/v1/tasks":
            self._handle_list_tasks()
        elif path == "/v1/providers":
            self._handle_list_providers()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/v1/sessions":
            self._handle_create_session()
        elif path == "/v1/chat":
            self._handle_chat()
        elif path == "/v1/chat/cancel":
            self._handle_cancel()
        elif path == "/v1/agents/switch":
            self._handle_switch_agent()
        elif path == "/v1/schedule":
            self._handle_modify_schedule()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/v1/sessions/"):
            sid = path.split("/")[-1]
            if sessions.delete(sid):
                self._send_json({"status": "deleted"})
            else:
                self._send_json({"error": "Session not found"}, 404)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-ID")
        self.end_headers()

    # --- Handlers ---

    def _handle_status(self):
        self._send_json({
            "status": "running",
            "version": engine.VERSION,
            "agents": engine.list_agents(),
            "sessions": len(sessions.list_all()),
            "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
            "changelog": [{"version": v, "date": d, "changes": c} for v, d, c in engine.CHANGELOG[:5]],
        })

    def _handle_list_agents(self):
        self._send_json({"agents": engine.get_agent_summaries()})

    def _handle_list_models(self):
        models = engine.get_available_models(
            server_config["api_key"], server_config["base_url"], server_config["api_type"])
        self._send_json({"models": models})

    def _handle_list_sessions(self):
        self._send_json({"sessions": sessions.list_all()})

    def _handle_create_session(self):
        body = self._read_json()
        session = sessions.create(
            agent_id=body.get("agent", "main"),
            model=body.get("model", server_config["default_model"]),
            api_key=server_config["api_key"],
            base_url=server_config["base_url"],
            api_type=server_config["api_type"],
            max_context=body.get("max_context", server_config["max_context"]),
        )
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
        })

    def _handle_switch_agent(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        agent_id = body.get("agent", "main")
        model = body.get("model")
        session.switch_agent(agent_id, model)
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
        })

    def _handle_cancel(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        session.cancel_token.cancel()
        self._send_json({"status": "cancelled"})

    def _handle_chat(self):
        """Handle chat request with SSE streaming."""
        body = self._read_json()
        sid = body.get("session_id", "")
        message = body.get("message", "")
        session = sessions.get(sid)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not message:
            self._send_json({"error": "No message"}, 400)
            return

        # Reset cancel token
        session.cancel_token = engine.CancelToken()

        # Add user message
        session.messages.append({"role": "user", "content": message})

        # Check context and compact
        session.messages, _ = engine._check_and_compact(
            session.messages, session.model, session.api_key,
            session.base_url, session.api_type,
            max_tokens=session.max_context,
        )

        # SSE streaming setup
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        event_queue = queue.Queue()

        def event_callback(event_type, data):
            event_queue.put((event_type, data))

        def worker():
            # Set thread-local memory for tools
            engine._thread_local.memory_store = session.memory

            # Temporarily set agent globals for system prompt building
            old_agent = engine._current_agent
            old_mcp = engine._mcp_manager
            engine._current_agent = session.agent

            # Load MCP for this agent
            mcp = engine.MCPManager()
            main_mcp = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
            mcp.load_config(main_mcp)
            if session.agent_id != "main":
                mcp.load_config(session.agent.mcp_config_path)
            engine._mcp_manager = mcp

            try:
                reply = engine.send_message_with_fallback(
                    session.messages, session.model, session.api_key,
                    session.base_url, session.api_type,
                    silent=True, escape_watcher=session.cancel_token,
                    event_callback=event_callback,
                )
                if reply:
                    session.messages.append({"role": "assistant", "content": reply})
                    event_queue.put(("done", {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                    }))
                else:
                    event_queue.put(("done", {"text": "", "tokens": 0}))
            except engine.TaskCancelled:
                session.messages.pop()  # remove user message
                event_queue.put(("error", {"message": "Cancelled"}))
            except Exception as e:
                event_queue.put(("error", {"message": str(e)}))
            finally:
                engine._current_agent = old_agent
                engine._mcp_manager = old_mcp
                mcp.stop_all()
                event_queue.put(None)  # sentinel

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Stream events to client
        try:
            while True:
                try:
                    event = event_queue.get(timeout=300)
                except queue.Empty:
                    break
                if event is None:
                    break
                event_type, data = event
                sse_line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(sse_line.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_list_schedule(self):
        if engine._scheduler:
            self._send_json({"schedules": engine._scheduler.list_all()})
        else:
            self._send_json({"schedules": []})

    def _handle_modify_schedule(self):
        body = self._read_json()
        action = body.get("action", "list")
        if not engine._scheduler:
            self._send_json({"error": "Scheduler not running"}, 500)
            return

        if action == "add":
            result = engine._scheduler.add(
                body.get("name", ""), body.get("task", ""),
                body.get("schedule", ""), body.get("agent", "main"),
                body.get("model"),
            )
            self._send_json(result)
        elif action == "pause":
            self._send_json(engine._scheduler.pause(body.get("name", "")))
        elif action == "resume":
            self._send_json(engine._scheduler.resume(body.get("name", "")))
        elif action == "delete":
            self._send_json(engine._scheduler.remove(body.get("name", "")))
        elif action == "history":
            self._send_json({"history": engine._scheduler.get_history(
                body.get("name"), body.get("limit", 20))})
        else:
            self._send_json({"schedules": engine._scheduler.list_all()})

    def _handle_list_providers(self):
        providers = server_config.get("providers", {})
        result = []
        for name, p in providers.items():
            # Try to fetch models dynamically
            models = []
            try:
                models = engine.get_available_models(
                    p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
            except Exception:
                pass
            result.append({
                "name": name,
                "base_url": p.get("base_url", ""),
                "type": p.get("type", "openai"),
                "default_model": p.get("default_model", ""),
                "models": models,
            })
        self._send_json({"providers": result})

    def _handle_list_tasks(self):
        if engine._task_runner:
            tasks = engine._task_runner.list_tasks()
            for t in tasks:
                if t.get("result") and len(t["result"]) > 500:
                    t["result"] = t["result"][:500] + "..."
            self._send_json({"tasks": tasks})
        else:
            self._send_json({"tasks": []})


# --- Main ---

def _load_config_file() -> dict:
    """Load config.json if it exists."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def main():
    # Load config.json for defaults
    file_config = _load_config_file()
    providers = file_config.get("providers", {})
    default_provider = file_config.get("default_provider", "")
    provider = providers.get(default_provider, {}) if default_provider else {}
    srv_cfg = file_config.get("server", {})

    parser = argparse.ArgumentParser(description=f"Brain Agent Server v{engine.VERSION}")
    parser.add_argument("--host", default=srv_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=srv_cfg.get("port", 8420))
    parser.add_argument("--api-key", default=provider.get("api_key", ""))
    parser.add_argument("--base-url", default=provider.get("base_url", "http://localhost:8317/v1"))
    parser.add_argument("-t", "--api-type", choices=["anthropic", "openai"],
                        default=provider.get("type", "anthropic"))
    parser.add_argument("-m", "--model", default=provider.get("default_model", ""))
    parser.add_argument("--max-context", type=int, default=file_config.get("max_context", 131072))
    args = parser.parse_args()

    # Store all providers for multi-provider support
    server_config["providers"] = providers
    server_config["api_key"] = args.api_key
    server_config["base_url"] = args.base_url
    server_config["api_type"] = args.api_type
    server_config["default_model"] = args.model
    server_config["max_context"] = args.max_context

    # Initialize engine globals
    engine._delegate_api_key = args.api_key
    engine._delegate_base_url = args.base_url
    engine._delegate_api_type = args.api_type
    engine._delegate_fallback_model = args.model

    # Start scheduler
    engine._scheduler = engine.Scheduler()
    engine._scheduler.start()

    # Start task runner
    engine._task_runner = engine.TaskRunner()

    # Initialize main agent
    engine._current_agent = engine.AgentConfig("main")
    engine._memory_store = engine.MemoryStore("main", base_dir=engine._current_agent.memory_dir)

    # Start server
    server = ThreadingHTTPServer((args.host, args.port), BrainAgentHandler)
    print(f"Brain Agent Server v{engine.VERSION}")
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"API: {args.base_url} ({args.api_type})")
    print(f"Model: {args.model}")
    print(f"Agents: {', '.join(engine.list_agents())}")
    if engine._scheduler:
        n = len(engine._scheduler.list_all())
        if n:
            print(f"Scheduled tasks: {n}")
    print()
    print("Endpoints:")
    print("  GET  /v1/status         — server health")
    print("  POST /v1/sessions       — create session")
    print("  POST /v1/chat           — send message (SSE stream)")
    print("  POST /v1/chat/cancel    — cancel request")
    print("  GET  /v1/agents         — list agents")
    print("  POST /v1/agents/switch  — switch agent")
    print("  GET  /v1/models         — list models")
    print("  GET  /v1/schedule       — scheduled tasks")
    print("  POST /v1/schedule       — manage schedules")
    print("  GET  /v1/tasks          — background tasks")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if engine._scheduler:
            engine._scheduler.stop()
        if engine._mcp_manager:
            engine._mcp_manager.stop_all()
        server.server_close()


if __name__ == "__main__":
    main()
