#!/usr/bin/env python3
"""Brain Agent Server — HTTP API daemon for multi-frontend access."""

import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

_server_start_time = time.time()
_QMD_PORT = 8181
_TELEGRAM_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.brain-agent.telegram.plist")
_QMD_PID_FILE = os.path.expanduser("~/.cache/qmd/mcp.pid")

import claude_cli as engine

# --- Session Management with SQLite persistence ---

import sqlite3

CHAT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "main", "chats.db")


_db_pool_lock = threading.Lock()
_db_pool: dict[str, sqlite3.Connection] = {}


def _db_conn(db_path=None):
    """Get a thread-safe SQLite connection (reused per database path)."""
    path = db_path or CHAT_DB
    tid = f"{path}:{threading.current_thread().ident}"
    with _db_pool_lock:
        conn = _db_pool.get(tid)
        if conn is None:
            conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            _db_pool[tid] = conn
    return conn


def _db_safe(default=None):
    """Decorator: catch SQLite errors and return default instead of crashing."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (sqlite3.Error, OSError) as e:
                import traceback
                traceback.print_exc()
                return default() if callable(default) else default
        return wrapper
    return decorator


class ChatDB:
    """SQLite persistence for chat sessions and messages."""

    @staticmethod
    def init():
        os.makedirs(os.path.dirname(CHAT_DB), exist_ok=True)
        with _db_conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    model TEXT,
                    title TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at REAL,
                    last_active REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id)")
            # Add status column if missing (migration)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN status TEXT DEFAULT 'active'")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def save_session(sid, agent_id, model, title, status, created_at, last_active):
        with _db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions (id, agent_id, model, title, status, created_at, last_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sid, agent_id, model, title, status, created_at, last_active))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def save_message(session_id, role, content):
        c = json.dumps(content) if not isinstance(content, str) else content
        with _db_conn() as conn:
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                         (session_id, role, c))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def load_messages(session_id):
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
            messages = []
            for mid, role, content in rows:
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = content
                messages.append({"id": mid, "role": role, "content": parsed})
            return messages

    @staticmethod
    @_db_safe(default=list)
    def list_sessions(agent_id=None, status=None):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            q = "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as message_count FROM sessions s WHERE 1=1"
            params = []
            if agent_id:
                q += " AND s.agent_id = ?"
                params.append(agent_id)
            if status:
                q += " AND s.status = ?"
                params.append(status)
            q += " ORDER BY s.last_active DESC"
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=None)
    def get_session_info(session_id):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    @_db_safe(default=None)
    def archive_session(session_id):
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET status = 'archived' WHERE id = ?", (session_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def unarchive_session(session_id):
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET status = 'active' WHERE id = ?", (session_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def delete_session(session_id):
        with _db_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def clear_messages(session_id):
        with _db_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def delete_message(message_id):
        with _db_conn() as conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def archive_all(agent_id=None):
        with _db_conn() as conn:
            if agent_id:
                conn.execute("UPDATE sessions SET status = 'archived' WHERE agent_id = ? AND status = 'active'", (agent_id,))
            else:
                conn.execute("UPDATE sessions SET status = 'archived' WHERE status = 'active'")
            conn.commit()


class Session:
    """A conversation session with an agent."""

    def __init__(self, agent_id: str = "main", model: str | None = None,
                 api_key: str = "", base_url: str = "", api_type: str = "anthropic",
                 max_context: int = 131072, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        self.model = model or ""
        self.api_key = api_key
        self.base_url = base_url
        self.api_type = api_type
        self.max_context = max_context
        self.messages: list[dict] = []
        self.cancel_token = engine.CancelToken()
        self.created_at = time.time()
        self.last_active = time.time()
        self.title = ""
        self.status = "active"
        self.lock = threading.Lock()

        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)

    def add_message(self, role: str, content):
        self.messages.append({"role": role, "content": content})
        self.last_active = time.time()
        # Auto-title from first user message
        if not self.title and role == "user":
            text = content if isinstance(content, str) else str(content)
            self.title = text[:60].strip()
        ChatDB.save_message(self.id, role, content)
        ChatDB.save_session(self.id, self.agent_id, self.model, self.title,
                           self.status, self.created_at, self.last_active)

    def switch_agent(self, agent_id: str, model: str | None = None):
        """Switch this session to a different agent (and optionally model)."""
        self.agent_id = agent_id
        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)
        if model:
            self.model = model

    def load_from_db(self):
        """Load messages from database (for restoring sessions)."""
        db_msgs = ChatDB.load_messages(self.id)
        self.messages = [{"role": m["role"], "content": m["content"]} for m in db_msgs]
        info = ChatDB.get_session_info(self.id)
        if info:
            self.title = info.get("title", "")
            self.status = info.get("status", "active")
            self.created_at = info.get("created_at", self.created_at)
            self.last_active = info.get("last_active", self.last_active)


class SessionManager:
    """Thread-safe session storage with SQLite persistence."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        ChatDB.init()

    def create(self, **kwargs) -> Session:
        session = Session(**kwargs)
        ChatDB.save_session(session.id, session.agent_id, session.model,
                           session.title, session.status, session.created_at, session.last_active)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.last_active = time.time()
                return s
        # Try loading from DB
        info = ChatDB.get_session_info(session_id)
        if info:
            s = Session(
                agent_id=info["agent_id"], model=info.get("model", ""),
                api_key=server_config.get("api_key", ""),
                base_url=server_config.get("base_url", ""),
                api_type=server_config.get("api_type", "anthropic"),
                session_id=session_id,
            )
            s.load_from_db()
            with self._lock:
                self._sessions[session_id] = s
            return s
        return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            self._sessions.pop(session_id, None)
        ChatDB.delete_session(session_id)
        return True

    def list_all(self) -> list[dict]:
        return ChatDB.list_sessions()

    def list_for_agent(self, agent_id: str, status: str | None = None) -> list[dict]:
        return ChatDB.list_sessions(agent_id=agent_id, status=status)


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
    allow_reuse_address = True


class BrainAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Brain Agent API."""

    def log_message(self, format, *args):
        """Log requests to stdout."""
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        print(f"  {ts} {self.command} {self.path}", flush=True)

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
        elif path.startswith("/v1/agents/") and "/files" in path:
            self._handle_agent_files(path)
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_read(path)
        elif path == "/v1/models":
            self._handle_list_models()
        elif path == "/v1/sessions":
            self._handle_list_sessions()
        elif path.startswith("/v1/sessions/") and path.endswith("/messages"):
            self._handle_get_messages(path)
        elif path == "/v1/schedule":
            self._handle_list_schedule()
        elif path == "/v1/tasks":
            self._handle_list_tasks()
        elif path == "/v1/schedule/running":
            self._handle_running_tasks()
        elif path == "/v1/providers":
            self._handle_list_providers()
        elif path == "/v1/services":
            self._handle_services_status()
        elif path.startswith("/v1/services/log"):
            self._handle_service_log()
        elif path == "/" or path.startswith("/web/") or path.endswith((".html", ".css", ".js", ".ico")):
            self._serve_static(path)
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
        elif path == "/v1/sessions/manage":
            self._handle_manage_session()
        elif path == "/v1/agents/switch":
            self._handle_switch_agent()
        elif path == "/v1/agents/create":
            self._handle_create_agent()
        elif path == "/v1/agents/delete":
            self._handle_delete_agent()
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_write(path)
        elif path == "/v1/schedule":
            self._handle_modify_schedule()
        elif path == "/v1/providers":
            self._handle_save_providers()
        elif path == "/v1/providers/test":
            self._handle_test_provider()
        elif path == "/v1/skills/browse":
            self._handle_browse_skills()
        elif path == "/v1/skills/install":
            self._handle_install_skill()
        elif path == "/v1/skills/install-zip":
            self._handle_install_skill_zip()
        elif path == "/v1/schedule/cancel":
            self._handle_cancel_scheduled()
        elif path == "/v1/skills/remove":
            self._handle_remove_skill()
        elif path == "/v1/restart":
            self._handle_restart()
        elif path == "/v1/services/qmd":
            self._handle_qmd_action()
        elif path == "/v1/services/telegram":
            self._handle_telegram_action()
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
        # Support ?agent=X&status=active|archived
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", ""))
        status = unquote(params.get("status", ""))
        if agent:
            self._send_json({"sessions": sessions.list_for_agent(agent, status or None)})
        else:
            self._send_json({"sessions": sessions.list_all()})

    def _handle_get_messages(self, path):
        """GET /v1/sessions/<id>/messages"""
        parts = path.split("/")
        sid = parts[3]
        msgs = ChatDB.load_messages(sid)
        self._send_json({"session_id": sid, "messages": msgs})

    def _handle_manage_session(self):
        """POST /v1/sessions/manage — archive, unarchive, clear, delete_message"""
        body = self._read_json()
        action = body.get("action", "")
        sid = body.get("session_id", "")

        if action == "archive":
            ChatDB.archive_session(sid)
            with sessions._lock:
                sessions._sessions.pop(sid, None)
            self._send_json({"status": "archived", "session_id": sid})
        elif action == "unarchive":
            ChatDB.unarchive_session(sid)
            self._send_json({"status": "unarchived", "session_id": sid})
        elif action == "clear":
            ChatDB.clear_messages(sid)
            s = sessions.get(sid)
            if s:
                s.messages = []
            self._send_json({"status": "cleared", "session_id": sid})
        elif action == "delete_message":
            msg_id = body.get("message_id")
            if msg_id:
                ChatDB.delete_message(msg_id)
                # Also remove from in-memory session
                s = sessions.get(sid)
                if s:
                    s.messages = [m for m in s.messages if m.get("id") != msg_id]
                self._send_json({"status": "deleted", "message_id": msg_id})
            else:
                self._send_json({"error": "message_id required"}, 400)
        elif action == "archive_all":
            agent = body.get("agent")
            ChatDB.archive_all(agent)
            self._send_json({"status": "archived_all"})
        elif action == "delete":
            sessions.delete(sid)
            self._send_json({"status": "deleted", "session_id": sid})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # Cache: model -> provider config (refreshed when providers change)
    _provider_cache: dict[str, dict] = {}
    _provider_cache_time: float = 0

    def _resolve_provider(self, model: str) -> dict:
        """Find the provider that has the given model. Returns {api_key, base_url, api_type}."""
        # Check cache first (refresh every 60s)
        now = time.time()
        if model in BrainAgentHandler._provider_cache and now - BrainAgentHandler._provider_cache_time < 60:
            return BrainAgentHandler._provider_cache[model]

        providers = server_config.get("providers", {})
        result = None

        for name, p in providers.items():
            prov = {"api_key": p.get("api_key", ""), "base_url": p.get("base_url", ""),
                    "api_type": p.get("type", "openai"), "provider_name": name}
            # Check default model match
            if p.get("default_model") == model:
                result = prov
                break
            # Check model list
            try:
                models = engine.get_available_models(p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
                for m in models:
                    BrainAgentHandler._provider_cache[m] = prov
                BrainAgentHandler._provider_cache_time = now
                if model in models:
                    result = prov
                    break
            except Exception:
                pass

        if not result:
            result = {"api_key": server_config.get("api_key", ""),
                      "base_url": server_config.get("base_url", ""),
                      "api_type": server_config.get("api_type", "openai"),
                      "provider_name": "default"}

        BrainAgentHandler._provider_cache[model] = result
        return result

    def _handle_create_session(self):
        body = self._read_json()
        model = body.get("model", server_config["default_model"])
        provider = self._resolve_provider(model)
        session = sessions.create(
            agent_id=body.get("agent", "main"),
            model=model,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            api_type=provider["api_type"],
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
        if model:
            provider = self._resolve_provider(model)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            session.api_type = provider["api_type"]
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
        model_override = body.get("model")
        session = sessions.get(sid)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not message:
            self._send_json({"error": "No message"}, 400)
            return

        # If model changed, re-resolve provider
        if model_override and model_override != session.model:
            session.model = model_override
            provider = self._resolve_provider(model_override)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            session.api_type = provider["api_type"]

        # Reset cancel token
        session.cancel_token = engine.CancelToken()

        # Add user message (persisted to DB)
        session.add_message("user", message)

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
            # Create fresh AgentConfig to pick up newly installed skills/config
            old_agent = engine._current_agent
            old_mcp = engine._mcp_manager
            engine._current_agent = engine.AgentConfig(session.agent_id)

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
                    session.add_message("assistant", reply)
                    event_queue.put(("done", {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                        "model": session.model,
                    }))
                else:
                    event_queue.put(("done", {"text": "", "tokens": 0, "model": session.model}))
            except engine.TaskCancelled:
                session.messages.pop()  # remove user message
                event_queue.put(("error", {"message": "Cancelled"}))
            except SystemExit as e:
                event_queue.put(("error", {"message": f"Engine fatal error (exit code {e.code})"}))
            except Exception as e:
                import traceback
                traceback.print_exc()
                event_queue.put(("error", {"message": str(e)}))
            finally:
                engine._current_agent = old_agent
                engine._mcp_manager = old_mcp
                mcp.stop_all()
                event_queue.put(None)  # sentinel

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Stream events to client with keepalive
        try:
            while True:
                try:
                    event = event_queue.get(timeout=5)
                except queue.Empty:
                    # If worker thread died, stop waiting
                    if not t.is_alive() and event_queue.empty():
                        try:
                            sse_err = f'event: error\ndata: {json.dumps({"message": "Server worker terminated unexpectedly"})}\n\n'
                            self.wfile.write(sse_err.encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        break
                    # Send keepalive comment to prevent browser timeout
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    continue
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
            schedules = engine._scheduler.list_all()
            running = engine._scheduler.get_running_tasks()
            running_names = {r["name"] for r in running}
            # Mark running tasks
            for s in schedules:
                s["is_running"] = s.get("name", "") in running_names
            self._send_json({"schedules": schedules, "running": [
                {k: v for k, v in r.items() if k != "cancel_token"} for r in running
            ]})
        else:
            self._send_json({"schedules": [], "running": []})

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
                body.get("model"), timeout=int(body.get("timeout", 300)),
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
            models = []
            try:
                models = engine.get_available_models(
                    p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
            except Exception:
                pass
            result.append({
                "name": name,
                "base_url": p.get("base_url", ""),
                "api_key": p.get("api_key", "")[:4] + "***" if p.get("api_key") else "",
                "type": p.get("type", "openai"),
                "default_model": p.get("default_model", ""),
                "models": models,
                "model_count": len(models),
                "status": "connected" if models else "unreachable",
            })
        self._send_json({"providers": result})

    def _handle_save_providers(self):
        """POST /v1/providers — save provider config."""
        body = self._read_json()
        action = body.get("action", "save")

        if action == "save":
            # Save all providers to config.json
            providers = body.get("providers", {})
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["providers"] = providers
                if body.get("default_provider"):
                    config["default_provider"] = body["default_provider"]
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                # Update server config in memory
                server_config["providers"] = providers
                self._send_json({"status": "saved"})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "add":
            name = body.get("name", "")
            provider = body.get("provider", {})
            if not name:
                self._send_json({"error": "Provider name required"}, 400)
                return
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                # If _keep_key is set, preserve existing api_key
                if provider.pop("_keep_key", False):
                    existing = config.get("providers", {}).get(name, {})
                    provider["api_key"] = existing.get("api_key", "")
                config.setdefault("providers", {})[name] = provider
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.setdefault("providers", {})[name] = provider
                self._send_json({"status": "added", "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "delete":
            name = body.get("name", "")
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config.get("providers", {}).pop(name, None)
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.get("providers", {}).pop(name, None)
                self._send_json({"status": "deleted", "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_test_provider(self):
        """POST /v1/providers/test — test provider connection."""
        body = self._read_json()
        base_url = body.get("base_url", "")
        api_key = body.get("api_key", "")
        api_type = body.get("type", "openai")
        try:
            models = engine.get_available_models(api_key, base_url, api_type)
            self._send_json({
                "status": "connected",
                "models": models,
                "model_count": len(models),
            })
        except Exception as e:
            self._send_json({
                "status": "error",
                "error": str(e),
                "models": [],
            })

    def _handle_running_tasks(self):
        """GET /v1/schedule/running — list currently executing scheduled tasks."""
        if engine._scheduler:
            running = engine._scheduler.get_running_tasks()
            # Remove cancel_token from response (not serializable)
            for r in running:
                r.pop("cancel_token", None)
            self._send_json({"running": running})
        else:
            self._send_json({"running": []})

    def _handle_cancel_scheduled(self):
        """POST /v1/schedule/cancel — cancel a running scheduled task."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Task name required"}, 400)
            return
        if engine._scheduler and engine._scheduler.cancel_running_task(name):
            self._send_json({"status": "cancelling", "name": name})
        else:
            self._send_json({"error": f"Task '{name}' not running"}, 404)

    def _handle_list_tasks(self):
        if engine._task_runner:
            tasks = engine._task_runner.list_tasks()
            for t in tasks:
                if t.get("result") and len(t["result"]) > 500:
                    t["result"] = t["result"][:500] + "..."
            self._send_json({"tasks": tasks})
        else:
            self._send_json({"tasks": []})

    # --- Agent file management ---

    def _handle_agent_files(self, path):
        """GET /v1/agents/<id>/files — list agent files."""
        parts = path.split("/")
        agent_id = parts[3]
        agent = engine.AgentConfig(agent_id)
        files = []
        if os.path.isdir(agent.dir):
            for f in sorted(os.listdir(agent.dir)):
                fp = os.path.join(agent.dir, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size": os.path.getsize(fp)})
        skills = agent.list_skills()
        self._send_json({"agent": agent_id, "files": files, "skills": skills})

    def _handle_agent_file_read(self, path):
        """GET /v1/agents/<id>/file?name=soul.md — read a file."""
        from urllib.parse import unquote
        parts = path.split("/")
        agent_id = parts[3]
        # Parse query string
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        filename = unquote(params.get("name", ""))
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        if not os.path.isfile(filepath):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            with open(filepath, "r") as f:
                content = f.read()
            self._send_json({"name": filename, "content": content})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_agent_file_write(self, path):
        """POST /v1/agents/<id>/file — write a file."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        filename = body.get("name", "")
        content = body.get("content", "")
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        try:
            with open(filepath, "w") as f:
                f.write(content)
            self._send_json({"status": "saved", "name": filename})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_create_agent(self):
        """POST /v1/agents/create — create a new agent."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or ".." in agent_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        agent = engine.AgentConfig(agent_id)  # auto-creates defaults
        if body.get("description"):
            cfg = agent.config
            cfg["description"] = body["description"]
            with open(os.path.join(agent.dir, "agent.json"), "w") as f:
                json.dump(cfg, f, indent=2)
        if body.get("soul"):
            with open(os.path.join(agent.dir, "soul.md"), "w") as f:
                f.write(body["soul"])
        self._send_json({"status": "created", "agent": agent_id})

    def _handle_delete_agent(self):
        """POST /v1/agents/delete — soft-delete an agent (move to .trash)."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or agent_id == "main" or ".." in agent_id:
            self._send_json({"error": "Cannot delete this agent"}, 400)
            return
        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent '{agent_id}' not found"}, 404)
            return
        try:
            trash_dir = os.path.join(engine.AGENTS_DIR, ".trash")
            os.makedirs(trash_dir, exist_ok=True)
            import shutil
            dest = os.path.join(trash_dir, f"{agent_id}_{int(time.time())}")
            shutil.move(agent_dir, dest)
            self._send_json({"status": "deleted", "agent": agent_id, "moved_to": dest})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Static file serving ---

    # --- Skill browsing & installation ---

    SKILL_REPO = "openclaw/skills"
    SKILL_AUTHORS = ["steipete"]  # known skill authors to browse

    # Cache for the full skill tree (refreshed every 10 minutes)
    _skill_tree_cache = None
    _skill_tree_time = 0

    def _get_skill_tree(self):
        """Fetch full skill tree from GitHub (cached 10 min)."""
        now = time.time()
        if BrainAgentHandler._skill_tree_cache and now - BrainAgentHandler._skill_tree_time < 600:
            return BrainAgentHandler._skill_tree_cache

        url = f"https://api.github.com/repos/{self.SKILL_REPO}/git/trees/main?recursive=1"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Brain-Agent",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        skills = {}  # (author, name) -> True
        for item in data.get("tree", []):
            p = item.get("path", "")
            if p.endswith("/SKILL.md") and p.startswith("skills/"):
                parts = p.split("/")
                if len(parts) >= 3:
                    skills[(parts[1], parts[2])] = True

        BrainAgentHandler._skill_tree_cache = skills
        BrainAgentHandler._skill_tree_time = now
        return skills

    def _handle_browse_skills(self):
        """POST /v1/skills/browse — search all 7000+ skills from GitHub."""
        body = self._read_json()
        search = body.get("search", "").lower().strip()

        if not search or len(search) < 2:
            self._send_json({"error": "Search term must be at least 2 characters", "skills": []})
            return

        try:
            tree = self._get_skill_tree()

            # Filter by name match
            matches = []
            for (author, name) in tree:
                if search in name.lower():
                    matches.append((author, name))
            matches.sort(key=lambda x: x[1])

            # Limit results and fetch metadata for top matches
            skills = []
            for author, name in matches[:30]:
                display_name = name
                description = ""
                version = ""

                # Fetch SKILL.md frontmatter for description
                try:
                    skill_url = f"https://raw.githubusercontent.com/{self.SKILL_REPO}/main/skills/{author}/{name}/SKILL.md"
                    skill_req = urllib.request.Request(skill_url, headers={"User-Agent": "Brain-Agent"})
                    with urllib.request.urlopen(skill_req, timeout=5) as sresp:
                        content = sresp.read().decode("utf-8")
                        import re as _re
                        fm = _re.match(r'^---\s*\n(.*?)\n---', content, _re.DOTALL)
                        if fm:
                            for line in fm.group(1).split("\n"):
                                if line.strip().startswith("name:"):
                                    display_name = line.split(":", 1)[1].strip().strip('"').strip("'")
                                elif line.strip().startswith("description:"):
                                    description = line.split(":", 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass

                skills.append({
                    "name": name,
                    "display_name": display_name,
                    "author": author,
                    "description": description,
                    "version": version,
                })

            self._send_json({"skills": skills, "count": len(skills), "total_in_repo": len(tree)})
        except Exception as e:
            self._send_json({"error": str(e), "skills": []})

    def _handle_install_skill(self):
        """POST /v1/skills/install — install a skill from GitHub to an agent."""
        body = self._read_json()
        skill_name = body.get("skill", "")
        author = body.get("author", "steipete")
        agent_id = body.get("agent", "main")

        if not skill_name:
            self._send_json({"error": "Skill name required"}, 400)
            return

        try:
            # Fetch SKILL.md
            skill_url = f"https://raw.githubusercontent.com/{self.SKILL_REPO}/main/skills/{author}/{skill_name}/SKILL.md"
            req = urllib.request.Request(skill_url, headers={"User-Agent": "Brain-Agent"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                skill_content = resp.read().decode("utf-8")

            # Install to agent's skills directory
            agent = engine.AgentConfig(agent_id)
            skill_dir = os.path.join(agent.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)
            skill_path = os.path.join(skill_dir, "SKILL.md")
            with open(skill_path, "w") as f:
                f.write(skill_content)

            # Also fetch _meta.json if available
            try:
                meta_url = f"https://raw.githubusercontent.com/{self.SKILL_REPO}/main/skills/{author}/{skill_name}/_meta.json"
                meta_req = urllib.request.Request(meta_url, headers={"User-Agent": "Brain-Agent"})
                with urllib.request.urlopen(meta_req, timeout=5) as mresp:
                    meta_content = mresp.read().decode("utf-8")
                    with open(os.path.join(skill_dir, "_meta.json"), "w") as f:
                        f.write(meta_content)
            except Exception:
                pass

            self._send_json({
                "status": "installed",
                "skill": skill_name,
                "agent": agent_id,
                "path": skill_path,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_install_skill_zip(self):
        """POST /v1/skills/install-zip — install skill from uploaded zip (base64 in JSON)."""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        zip_data_b64 = body.get("zip_data", "")
        skill_name = body.get("name", "")

        if not zip_data_b64:
            self._send_json({"error": "No zip data"}, 400)
            return

        try:
            import base64
            import zipfile
            import io

            zip_bytes = base64.b64decode(zip_data_b64)
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

            # Find SKILL.md in the zip
            skill_md_path = None
            for name in zf.namelist():
                if name.endswith("SKILL.md"):
                    skill_md_path = name
                    break

            if not skill_md_path:
                self._send_json({"error": "No SKILL.md found in zip"}, 400)
                return

            # Determine skill name from path or provided name
            parts = skill_md_path.split("/")
            if not skill_name:
                # Use parent directory name, or filename prefix
                if len(parts) >= 2:
                    skill_name = parts[-2]
                else:
                    skill_name = "imported-skill"

            # Extract all files to agent's skills directory
            agent = engine.AgentConfig(agent_id)
            skill_dir = os.path.join(agent.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)

            # Find the common prefix to strip
            prefix = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

            for zpath in zf.namelist():
                if zpath.endswith("/"):
                    continue
                # Strip prefix to get relative path
                rel = zpath[len(prefix):] if zpath.startswith(prefix) else zpath.split("/")[-1]
                dest = os.path.join(skill_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(zf.read(zpath))

            self._send_json({
                "status": "installed",
                "skill": skill_name,
                "agent": agent_id,
                "files": [n for n in zf.namelist() if not n.endswith("/")],
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_remove_skill(self):
        """POST /v1/skills/remove — remove a skill from an agent."""
        body = self._read_json()
        skill_name = body.get("skill", "")
        agent_id = body.get("agent", "main")
        if not skill_name:
            self._send_json({"error": "Skill name required"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        skill_dir = os.path.join(agent.skills_dir, skill_name)
        if not os.path.isdir(skill_dir):
            self._send_json({"error": f"Skill '{skill_name}' not found"}, 404)
            return
        try:
            import shutil
            shutil.rmtree(skill_dir)
            self._send_json({"status": "removed", "skill": skill_name, "agent": agent_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Service Management ---

    @staticmethod
    def _find_qmd() -> str | None:
        """Find the qmd binary."""
        qmd = shutil.which("qmd")
        if qmd:
            return qmd
        for p in [os.path.expanduser("~/.nvm/versions/node"), "/usr/local/bin", "/opt/homebrew/bin"]:
            if os.path.isdir(p):
                for d in sorted(os.listdir(p), reverse=True):
                    candidate = os.path.join(p, d, "bin", "qmd") if "node" in p else os.path.join(p, "qmd")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    @staticmethod
    def _is_qmd_running() -> bool:
        try:
            req = urllib.request.Request(
                f"http://localhost:{_QMD_PORT}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "check", "version": "1.0"}}}).encode(),
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def _is_telegram_running() -> bool:
        try:
            r = subprocess.run(["launchctl", "list", "com.brain-agent.telegram"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and '"PID"' in r.stdout:
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def _qmd_collections() -> list[dict]:
        try:
            qmd_bin = BrainAgentHandler._find_qmd()
            if not qmd_bin:
                return []
            qmd_env = os.environ.copy()
            qmd_env["PATH"] = os.path.dirname(qmd_bin) + ":" + qmd_env.get("PATH", "")
            r = subprocess.run([qmd_bin, "collection", "list"],
                               capture_output=True, text=True, timeout=5, env=qmd_env)
            if r.returncode == 0:
                collections = []
                current = None
                for line in r.stdout.split("\n"):
                    line = line.strip()
                    # Parse lines like: "main (qmd://main/)"
                    if line and not line.startswith(("Collections", "Pattern", "Files", "Updated", "Ignore")) and "(" in line:
                        name = line.split("(")[0].strip()
                        if name:
                            current = {"name": name}
                            collections.append(current)
                    elif current and line.startswith("Files:"):
                        current["files"] = line.split(":")[1].strip().split()[0]
                return collections
        except Exception:
            pass
        return []

    def _handle_services_status(self):
        """GET /v1/services — status of all managed services."""
        uptime = int(time.time() - _server_start_time)
        qmd_running = self._is_qmd_running()
        tg_running = self._is_telegram_running()

        collections = self._qmd_collections() if qmd_running else []

        self._send_json({
            "server": {
                "status": "running",
                "version": engine.VERSION,
                "version_date": engine.VERSION_DATE,
                "pid": os.getpid(),
                "uptime_seconds": uptime,
                "sessions": len(sessions.list_all()),
                "agents": engine.list_agents(),
                "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
            },
            "qmd": {
                "status": "running" if qmd_running else "stopped",
                "port": _QMD_PORT,
                "collections": collections,
            },
            "telegram": {
                "status": "running" if tg_running else "stopped",
                "label": "com.brain-agent.telegram",
            },
        })

    def _handle_service_log(self):
        """GET /v1/services/log?name=server|qmd&lines=100 — tail a service log."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        name = params.get("name", "server")
        lines = min(int(params.get("lines", "100")), 500)

        log_paths = {
            "server": os.path.expanduser("~/.brain-agent/server.log"),
            "qmd": os.path.expanduser("~/.brain-agent/qmd.log"),
        }
        path = log_paths.get(name)
        if not path or not os.path.isfile(path):
            self._send_json({"name": name, "lines": [], "error": "Log file not found"})
            return

        try:
            with open(path, "r", errors="replace") as f:
                all_lines = f.readlines()
            tail = [l.rstrip("\n") for l in all_lines[-lines:]]
            self._send_json({"name": name, "lines": tail, "total": len(all_lines)})
        except Exception as e:
            self._send_json({"name": name, "lines": [], "error": str(e)})

    def _handle_qmd_action(self):
        """POST /v1/services/qmd — start/stop/reindex QMD."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "start":
            if self._is_qmd_running():
                self._send_json({"status": "already_running"})
                return
            qmd_bin = self._find_qmd()
            if not qmd_bin:
                self._send_json({"error": "qmd not installed"}, 500)
                return
            base = os.path.dirname(os.path.abspath(__file__))
            log = open(os.path.expanduser("~/.brain-agent/qmd.log"), "a")
            # Ensure qmd's node version is first in PATH
            qmd_env = os.environ.copy()
            qmd_node_dir = os.path.dirname(qmd_bin)
            qmd_env["PATH"] = qmd_node_dir + ":" + qmd_env.get("PATH", "")
            subprocess.Popen(
                [qmd_bin, "mcp", "--http", "--port", str(_QMD_PORT)],
                stdout=log, stderr=log, env=qmd_env,
                start_new_session=True, cwd=base)
            for _ in range(10):
                time.sleep(0.5)
                if self._is_qmd_running():
                    self._send_json({"status": "started"})
                    return
            self._send_json({"status": "starting"})

        elif action == "stop":
            if os.path.exists(_QMD_PID_FILE):
                try:
                    with open(_QMD_PID_FILE) as f:
                        pid = int(f.read().strip())
                    os.kill(pid, signal.SIGTERM)
                    self._send_json({"status": "stopped"})
                    return
                except Exception:
                    pass
            try:
                r = subprocess.run(["lsof", "-ti", f"tcp:{_QMD_PORT}"],
                                   capture_output=True, text=True, timeout=3)
                for pid_str in r.stdout.strip().split("\n"):
                    if pid_str.strip():
                        os.kill(int(pid_str), signal.SIGTERM)
            except Exception:
                pass
            self._send_json({"status": "stopped"})

        elif action == "reindex":
            collection = body.get("collection")
            qmd_bin = self._find_qmd()
            if not qmd_bin:
                self._send_json({"error": "qmd not installed"}, 500)
                return
            reindex_env = os.environ.copy()
            reindex_env["PATH"] = os.path.dirname(qmd_bin) + ":" + reindex_env.get("PATH", "")
            def do_reindex():
                subprocess.run([qmd_bin, "update"], capture_output=True, timeout=30, env=reindex_env)
                if collection:
                    subprocess.run([qmd_bin, "embed", "-c", collection], capture_output=True, timeout=60, env=reindex_env)
                else:
                    subprocess.run([qmd_bin, "embed"], capture_output=True, timeout=60, env=reindex_env)
            threading.Thread(target=do_reindex, daemon=True).start()
            self._send_json({"status": "reindexing", "collection": collection or "all"})

        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_telegram_action(self):
        """POST /v1/services/telegram — start/stop/restart Telegram."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "start":
            subprocess.run(["launchctl", "load", _TELEGRAM_PLIST],
                           capture_output=True, timeout=5)
            time.sleep(1)
            self._send_json({"status": "started", "running": self._is_telegram_running()})

        elif action == "stop":
            subprocess.run(["launchctl", "unload", _TELEGRAM_PLIST],
                           capture_output=True, timeout=5)
            time.sleep(1)
            self._send_json({"status": "stopped", "running": self._is_telegram_running()})

        elif action == "restart":
            subprocess.run(["launchctl", "unload", _TELEGRAM_PLIST],
                           capture_output=True, timeout=5)
            time.sleep(1)
            subprocess.run(["launchctl", "load", _TELEGRAM_PLIST],
                           capture_output=True, timeout=5)
            time.sleep(1)
            self._send_json({"status": "restarted", "running": self._is_telegram_running()})

        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_restart(self):
        """POST /v1/restart — restart the server process."""
        self._send_json({"status": "restarting"})
        # Schedule restart after response is sent
        def do_restart():
            time.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do_restart, daemon=True).start()

    def _serve_static(self, path):
        """Serve static files from web/ directory."""
        if path == "/":
            path = "/web/index.html"
        elif not path.startswith("/web/"):
            path = "/web" + path

        base = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(base, path.lstrip("/"))

        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext = filepath.rsplit(".", 1)[-1].lower()
        content_types = {
            "html": "text/html", "css": "text/css", "js": "application/javascript",
            "json": "application/json", "png": "image/png", "svg": "image/svg+xml",
            "ico": "image/x-icon",
        }
        ct = content_types.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


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
