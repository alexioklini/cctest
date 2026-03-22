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
import telegram as _telegram_mod
import adapters as _adapters_mod
_QMD_PID_FILE = os.path.expanduser("~/.cache/qmd/mcp.pid")

import claude_cli as engine
import notifications as _notif_mod

# --- Notification Manager (initialized in main()) ---
_notification_manager: _notif_mod.NotificationManager | None = None

# --- Node Manager (in-memory registry for remote nodes) ---

_node_registry: dict[str, dict] = {}  # token -> node info
_node_commands: dict[str, dict] = {}  # command_id -> {command, result_event, result}
_node_lock = threading.Lock()


def _load_node_config() -> dict:
    """Load nodes config from config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("nodes", {})
    except Exception:
        return {}


def _save_node_config(nodes: dict):
    """Save nodes config to config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        config["nodes"] = nodes
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Failed to save node config: {e}", flush=True)


def _init_node_registry():
    """Initialize node registry from config."""
    global _node_registry
    nodes_cfg = _load_node_config()
    with _node_lock:
        for name, cfg in nodes_cfg.items():
            token = cfg.get("token", "")
            if token:
                _node_registry[token] = {
                    "name": name,
                    "config": cfg,
                    "status": "disconnected",
                    "last_heartbeat": None,
                    "hostname": "",
                    "os": "",
                    "cpu_percent": None,
                    "mem_used_gb": None,
                    "mem_total_gb": None,
                    "disk_free_gb": None,
                    "uptime_seconds": None,
                    "active_commands": 0,
                    "total_commands": 0,
                    "connected_since": None,
                    "pending_commands": [],
                }


def _node_submit_command(node_selector: str, tool: str, params: dict) -> dict:
    """Submit a command to a remote node. Returns the result."""
    with _node_lock:
        target_node = None
        target_token = None

        if node_selector.startswith("tag:"):
            tag = node_selector[4:]
            candidates = []
            for token, info in _node_registry.items():
                cfg = info.get("config", {})
                if tag in cfg.get("tags", []) and info["status"] == "connected" and not cfg.get("paused"):
                    if tool in cfg.get("allowed_tools", []):
                        candidates.append((token, info))
            if candidates:
                candidates.sort(key=lambda x: x[1].get("active_commands", 0))
                target_token, target_node = candidates[0]
        else:
            for token, info in _node_registry.items():
                if info["name"] == node_selector:
                    target_token = token
                    target_node = info
                    break

        if not target_node:
            return {"error": f"Node '{node_selector}' not found"}
        if target_node["status"] != "connected":
            return {"error": f"Node '{node_selector}' is not connected"}
        cfg = target_node.get("config", {})
        if cfg.get("paused"):
            return {"error": f"Node '{node_selector}' is paused"}
        if tool not in cfg.get("allowed_tools", []):
            return {"error": f"Tool '{tool}' not allowed on node '{node_selector}'"}

        command_id = uuid.uuid4().hex[:12]
        cmd = {"id": command_id, "tool": tool, "params": params}
        result_event = threading.Event()
        _node_commands[command_id] = {"command": cmd, "result_event": result_event, "result": None}
        target_node["pending_commands"].append(cmd)

    timeout = params.get("timeout", 120)
    if result_event.wait(timeout=timeout + 5):
        with _node_lock:
            entry = _node_commands.pop(command_id, {})
            return entry.get("result", {"error": "No result"})
    else:
        with _node_lock:
            _node_commands.pop(command_id, None)
        return {"error": f"Timeout waiting for node '{node_selector}'"}

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
            # Add project column if missing (migration)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN project TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Add metadata column for file attachments etc (migration)
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN metadata TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def save_session(sid, agent_id, model, title, status, created_at, last_active, project=""):
        with _db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions (id, agent_id, model, title, status, created_at, last_active, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (sid, agent_id, model, title, status, created_at, last_active, project or ""))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def save_message(session_id, role, content, metadata=None):
        c = json.dumps(content) if not isinstance(content, str) else content
        meta = json.dumps(metadata) if metadata else ""
        with _db_conn() as conn:
            conn.execute("INSERT INTO messages (session_id, role, content, metadata) VALUES (?, ?, ?, ?)",
                         (session_id, role, c, meta))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def load_messages(session_id):
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
            messages = []
            for mid, role, content, metadata in rows:
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = content
                msg = {"id": mid, "role": role, "content": parsed}
                if metadata:
                    try:
                        meta = json.loads(metadata)
                        if meta:
                            msg["metadata"] = meta
                    except (json.JSONDecodeError, TypeError):
                        pass
                messages.append(msg)
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
                if status == 'all':
                    pass  # No filter — return all statuses
                elif status == 'active':
                    # Include incognito sessions alongside active ones
                    q += " AND s.status IN ('active', 'incognito')"
                else:
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

        self.project: str | None = None  # Active project name (for scoped chat)
        self.note_context: str | None = None  # Note content for AI-assisted editing
        self._last_summary_at = 0  # Token count at last continuous summary

        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)

    def add_message(self, role: str, content, metadata=None):
        msg = {"role": role, "content": content}
        if metadata:
            msg["metadata"] = metadata
        self.messages.append(msg)
        self.last_active = time.time()
        # Auto-title from first user message
        if not self.title and role == "user":
            text = content if isinstance(content, str) else str(content)
            self.title = text[:60].strip()
        ChatDB.save_message(self.id, role, content, metadata=metadata)
        ChatDB.save_session(self.id, self.agent_id, self.model, self.title,
                           self.status, self.created_at, self.last_active, self.project or "")

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
            self.project = info.get("project", "") or None


class SessionManager:
    """Thread-safe session storage with SQLite persistence."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        ChatDB.init()

    def create(self, **kwargs) -> Session:
        session = Session(**kwargs)
        ChatDB.save_session(session.id, session.agent_id, session.model,
                           session.title, session.status, session.created_at, session.last_active,
                           session.project or "")
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
            model = info.get("model", "")
            # Resolve provider from model to get correct API key/URL/type
            try:
                prov = BrainAgentHandler._resolve_provider_static(model) if model else {}
            except Exception:
                prov = {}
            s = Session(
                agent_id=info["agent_id"], model=model,
                api_key=prov.get("api_key", server_config.get("api_key", "")),
                base_url=prov.get("base_url", server_config.get("base_url", "")),
                api_type=prov.get("api_type", server_config.get("api_type", "anthropic")),
                max_context=engine.get_model_max_context(model) if model else server_config.get("max_context", 131072),
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
        elif path == "/v1/models/config":
            self._handle_models_config_get()
        elif path == "/v1/agents/activity":
            self._handle_agents_activity()
        elif path == "/v1/workflows/executions":
            self._handle_workflow_list_executions()
        elif path.startswith("/v1/workflows/executions/"):
            self._handle_workflow_get_execution(path)
        elif path.startswith("/v1/agents/") and "/workflows" in path:
            self._handle_workflow_list(path)
        elif path == "/v1/teams":
            self._handle_teams_get()
        elif path == "/v1/services":
            self._handle_services_status()
        elif path.startswith("/v1/services/qmd/docs"):
            self._handle_qmd_docs()
        elif path.startswith("/v1/services/log"):
            self._handle_service_log()
        elif path.startswith("/v1/agents/") and path.endswith("/memory-summary"):
            self._handle_memory_summary_get(path)
        elif path.startswith("/v1/agents/") and path.endswith("/commands"):
            self._handle_agent_commands_get(path)
        elif path == "/v1/costs":
            self._handle_costs()
        elif path == "/v1/costs/daily":
            self._handle_costs_daily()
        elif path == "/v1/cache/stats":
            self._send_json(engine._web_cache.stats())
        # --- Traces & Audit GET routes ---
        elif path == "/v1/traces" or path.startswith("/v1/traces?"):
            self._handle_traces_list()
        elif path.startswith("/v1/traces/"):
            self._handle_trace_detail(path)
        elif path == "/v1/audit" or path.startswith("/v1/audit?"):
            self._handle_audit_list()
        elif path.startswith("/v1/audit/export"):
            self._handle_audit_export()
        # --- MCP GET routes ---
        elif path == "/v1/mcp/connections":
            self._handle_mcp_list()
        # --- Projects & Ingestion GET routes ---
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes" in path:
            self._handle_notes(path, "GET")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs" in path:
            self._handle_project_docs(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_get(path)
        elif path.startswith("/v1/agents/") and path.endswith("/projects"):
            self._handle_list_projects(path)
        elif path.startswith("/v1/agents/") and path.endswith("/ingested"):
            self._handle_list_ingested(path)
        elif path.startswith("/v1/agents/") and path.endswith("/graph/stats"):
            self._handle_agent_graph_stats(path)
        elif path.startswith("/v1/agents/") and path.endswith("/graph"):
            self._handle_agent_graph(path)
        elif path == "/v1/notifications":
            self._handle_notifications_list()
        elif path == "/v1/notifications/unread":
            self._handle_notifications_unread()
        elif path == "/v1/backup/info":
            self._handle_backup_info()
        # --- Nodes GET routes ---
        elif path == "/v1/nodes":
            self._handle_nodes_list()
        elif path.startswith("/v1/nodes/poll"):
            self._handle_node_poll()
        # --- Channels GET routes ---
        elif path == "/v1/channels":
            self._handle_channels_list()
        # --- Tools config GET routes ---
        elif path == "/v1/tools/config":
            self._handle_tools_config_get()
        elif path == "/v1/tools/status":
            self._handle_tools_status()
        elif path == "/v1/files/download":
            self._handle_file_download()
        elif path == "/v1/files/preview":
            self._handle_file_preview()
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
        elif path == "/v1/agents/rename":
            self._handle_rename_agent()
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_write(path)
        elif path == "/v1/schedule":
            self._handle_modify_schedule()
        elif path == "/v1/providers":
            self._handle_save_providers()
        elif path == "/v1/providers/test":
            self._handle_test_provider()
        elif path == "/v1/models/config":
            self._handle_models_config_save()
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
        elif path.startswith("/v1/services/qmd/docs"):
            self._handle_qmd_doc_save()
        elif path == "/v1/teams":
            self._handle_teams_post()
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/approve"):
            self._handle_workflow_approve(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/cancel"):
            self._handle_workflow_cancel(path)
        elif path.startswith("/v1/agents/") and "/workflows/" in path and "/run" in path:
            self._handle_workflow_run(path)
        elif path.startswith("/v1/agents/") and "/workflows" in path:
            self._handle_workflow_save(path)
        elif path == "/v1/services/telegram":
            self._handle_telegram_action()
        elif path == "/v1/services/server":
            self._handle_server_config()
        elif path.startswith("/v1/agents/") and path.endswith("/memory-summary"):
            self._handle_memory_summary_post(path)
        elif path == "/v1/cache/clear":
            engine._web_cache.clear()
            self._send_json({"status": "cleared"})
        elif path == "/v1/notifications/settings":
            self._handle_notifications_settings_post()
        elif path == "/v1/notifications/dismiss":
            self._handle_notifications_dismiss()
        elif path == "/v1/notifications/read":
            self._handle_notifications_read()
        elif path == "/v1/backup":
            self._handle_backup_create()
        elif path == "/v1/restore":
            self._handle_restore()
        elif path == "/v1/refine":
            self._handle_refine()
        elif path.startswith("/v1/agents/") and path.endswith("/commands"):
            self._handle_agent_commands_post(path)
        # --- MCP POST routes ---
        elif path == "/v1/mcp/connect":
            self._handle_mcp_connect()
        elif path == "/v1/mcp/disconnect":
            self._handle_mcp_disconnect()
        # --- Projects & Ingestion POST routes ---
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes" in path:
            self._handle_notes(path, "POST")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/ingest" in path:
            self._handle_project_ingest(path)
        elif path.startswith("/v1/agents/") and path.endswith("/projects"):
            self._handle_create_project(path)
        elif path.startswith("/v1/agents/") and path.endswith("/graph/discover"):
            self._handle_agent_graph_discover(path)
        elif path.startswith("/v1/agents/") and path.endswith("/ingest"):
            self._handle_agent_ingest(path)
        # --- Nodes POST routes ---
        elif path == "/v1/nodes":
            self._handle_nodes_action()
        elif path == "/v1/nodes/result":
            self._handle_node_result()
        elif path == "/v1/nodes/execute":
            self._handle_node_execute()
        # --- Tools config POST routes ---
        elif path == "/v1/tools/config":
            self._handle_tools_config_save()
        # --- Channels POST routes ---
        elif path == "/v1/channels":
            self._handle_channels_action()
        elif path.startswith("/v1/channels/") and path.endswith("/start"):
            self._handle_channel_lifecycle(path, "start")
        elif path.startswith("/v1/channels/") and path.endswith("/stop"):
            self._handle_channel_lifecycle(path, "stop")
        elif path.startswith("/v1/channels/") and path.endswith("/restart"):
            self._handle_channel_lifecycle(path, "restart")
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "PUT")
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_update(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/v1/sessions/"):
            sid = path.split("/")[-1]
            info = ChatDB.get_session_info(sid)
            if sessions.delete(sid):
                self._send_json({"status": "deleted"})
                # Trigger memory summary refresh to purge deleted chat insights
                if info:
                    try:
                        engine.trigger_memory_summary_refresh(info.get("agent_id", "main"))
                    except Exception:
                        pass
            else:
                self._send_json({"error": "Session not found"}, 404)
        elif path.startswith("/v1/services/qmd/docs"):
            self._handle_qmd_doc_delete()
        # --- Projects & Ingestion DELETE routes ---
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "DELETE")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs/" in path:
            self._handle_project_doc_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_delete(path)
        elif path.startswith("/v1/agents/") and "/ingested/" in path:
            self._handle_agent_ingested_delete(path)
        elif path.startswith("/v1/agents/") and "/workflows/" in path:
            self._handle_workflow_delete(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
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
        agents = engine.get_agent_summaries()
        self._send_json({"agents": agents, "team_structure": engine.get_team_structure()})

    def _handle_list_models(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        show_all = params.get("all", "").lower() in ("true", "1")

        if engine._models_config and not show_all:
            # Return only enabled models from config
            models = engine.get_enabled_models()
        else:
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
            # Get agent_id before deleting so we can trigger summary refresh
            info = ChatDB.get_session_info(sid)
            sessions.delete(sid)
            self._send_json({"status": "deleted", "session_id": sid})
            # Trigger memory summary refresh to purge deleted chat insights
            if info:
                try:
                    engine.trigger_memory_summary_refresh(info.get("agent_id", "main"))
                except Exception:
                    pass
        elif action == "incognito":
            # Mark session as incognito — excluded from memory summary
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET status = 'incognito' WHERE id = ?", (sid,))
                conn.commit()
            s = sessions.get(sid)
            if s:
                s.status = "incognito"
            self._send_json({"status": "incognito", "session_id": sid})
        elif action == "un_incognito":
            # Revert incognito session back to active
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET status = 'active' WHERE id = ?", (sid,))
                conn.commit()
            s = sessions.get(sid)
            if s:
                s.status = "active"
            self._send_json({"status": "active", "session_id": sid})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # Cache: model -> provider config (refreshed when providers change)
    _provider_cache: dict[str, dict] = {}
    _provider_cache_time: float = 0
    _provider_cache_lock = threading.Lock()

    @staticmethod
    def _resolve_provider_static(model: str) -> dict:
        """Find the provider that has the given model. Returns {api_key, base_url, api_type}.
        Thread-safe. Can be called without a handler instance."""
        if engine._models_config:
            model = engine.resolve_model(model)

        # Check cache first (refresh every 60s)
        now = time.time()
        with BrainAgentHandler._provider_cache_lock:
            if model in BrainAgentHandler._provider_cache and now - BrainAgentHandler._provider_cache_time < 60:
                return BrainAgentHandler._provider_cache[model].copy()

        providers = server_config.get("providers", {})
        result = None

        # Fast path: check models config for provider hint
        model_cfg = engine.get_model_info(model)
        if model_cfg.get("provider"):
            prov_name = model_cfg["provider"]
            p = providers.get(prov_name)
            if p:
                result = {"api_key": p.get("api_key", ""), "base_url": p.get("base_url", ""),
                          "api_type": p.get("type", "openai"), "provider_name": prov_name}

        if not result:
            for name, p in providers.items():
                prov = {"api_key": p.get("api_key", ""), "base_url": p.get("base_url", ""),
                        "api_type": p.get("type", "openai"), "provider_name": name}
                if p.get("default_model") == model:
                    result = prov
                    break
                try:
                    models = engine.get_available_models(p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
                    with BrainAgentHandler._provider_cache_lock:
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

        with BrainAgentHandler._provider_cache_lock:
            BrainAgentHandler._provider_cache[model] = result
        return result

    def _resolve_provider(self, model: str) -> dict:
        """Instance method wrapper for _resolve_provider_static."""
        return BrainAgentHandler._resolve_provider_static(model)

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
            max_context=body.get("max_context") or engine.get_model_max_context(model),
        )
        project = body.get("project", "")
        if project:
            session.project = project
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, project)
        note_context = body.get("note_context", "")
        if note_context:
            session.note_context = note_context
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "max_context": session.max_context,
            "project": session.project or "",
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
        chat_mode = body.get("mode", "")
        project_name = body.get("project")  # Optional project scope
        session = sessions.get(sid)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not message:
            self._send_json({"error": "No message"}, 400)
            return

        # Custom command expansion
        if message.startswith("/"):
            agent = engine.AgentConfig(session.agent_id)
            custom_cmds = agent.load_commands()
            cmd_word = message.split()[0][1:]  # strip / and get first word
            for cmd in custom_cmds:
                if cmd.get("name", "").lower() == cmd_word.lower():
                    template = cmd.get("template", "")
                    # Replace {{input}} with rest of message
                    rest = message[len(cmd_word) + 1:].strip()
                    message = template.replace("{{input}}", rest)
                    break

        # If model changed, re-resolve provider
        if model_override and model_override != session.model:
            session.model = model_override
            provider = self._resolve_provider(model_override)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            session.api_type = provider["api_type"]

        # Auto model selection: if agent uses model="auto", re-resolve per message
        agent_cfg = session.agent.config
        if not model_override and agent_cfg.get("model") == "auto":
            auto_model, auto_purpose = engine.resolve_auto_model_for_task(agent_cfg, message)
            if auto_model and auto_model != session.model:
                session.model = auto_model
                provider = self._resolve_provider(auto_model)
                session.api_key = provider["api_key"]
                session.base_url = provider["base_url"]
                session.api_type = provider["api_type"]
                session.max_context = engine.get_model_max_context(auto_model)

        # Reset cancel token
        session.cancel_token = engine.CancelToken()
        session._streaming = True

        # --- Multimodal: construct content blocks if images present ---
        images = body.get("images", [])
        if images:
            content_blocks = []
            for img in images:
                if session.api_type == "openai":
                    # OpenAI format: image_url with data URI
                    data_uri = f"data:{img.get('media_type', 'image/png')};base64,{img['data']}"
                    content_blocks.append({"type": "image_url", "image_url": {"url": data_uri}})
                else:
                    # Anthropic format: image source block
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.get("media_type", "image/png"),
                            "data": img["data"],
                        },
                    })
            content_blocks.append({"type": "text", "text": message})
            user_content = content_blocks
        else:
            user_content = message

        # Add user message (persisted to DB)
        session.add_message("user", user_content)

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
        created_files = []

        def event_callback(event_type, data):
            if event_type == "file_created":
                created_files.append(data)
            event_queue.put((event_type, data))

        handler_self = self  # capture for closure

        def worker():
            # Set thread-local agent context (thread-safe, no global mutation)
            engine._thread_local.memory_store = session.memory
            agent_config = engine.AgentConfig(session.agent_id)
            engine._thread_local.current_agent = agent_config

            # Load MCP for this agent (thread-local)
            mcp = engine.MCPManager()
            main_mcp = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
            mcp.load_config(main_mcp)
            if session.agent_id != "main":
                mcp.load_config(session.agent.mcp_config_path)
            engine._thread_local.mcp_manager = mcp

            # Set plan mode if requested
            engine._thread_local.plan_mode = (chat_mode == "plan")

            # Set project scope if provided
            if project_name:
                session.project = project_name
                engine._thread_local.project = project_name
            else:
                engine._thread_local.project = session.project  # Use session's existing project

            # Set note context for AI-assisted note editing
            if session.note_context:
                engine._thread_local.note_context = session.note_context
            else:
                engine._thread_local.note_context = None

            # Set globals as fallback for code that hasn't been migrated yet
            old_agent = engine._current_agent
            old_mcp = engine._mcp_manager
            engine._current_agent = agent_config
            engine._mcp_manager = mcp

            try:
                # Use detected purpose from auto-resolve, or fall back to agent's fixed purpose
                purpose = session.agent.config.get("model_purpose")
                if not purpose and session.agent.config.get("model") == "auto":
                    purpose = engine.classify_task_purpose(message)
                inf_params = engine.get_inference_params(session.model, purpose)
                reply = engine.send_message_with_fallback(
                    session.messages, session.model, session.api_key,
                    session.base_url, session.api_type,
                    silent=True, escape_watcher=session.cancel_token,
                    event_callback=event_callback,
                    provider_resolver=handler_self._resolve_provider,
                    inference_params=inf_params,
                    purpose=purpose,
                    session_id=sid,
                )
                if reply:
                    # Build metadata for file attachments
                    msg_metadata = {"files": created_files} if created_files else None
                    session.add_message("assistant", reply, metadata=msg_metadata)
                    # Include session cost in done event
                    session_cost = None
                    if engine._cost_tracker:
                        try:
                            sc = engine._cost_tracker.get_session_cost(sid)
                            session_cost = round(sc.get("cost", 0.0), 4)
                        except Exception:
                            pass
                    done_data = {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                        "model": session.model,
                    }
                    if session_cost is not None:
                        done_data["cost"] = session_cost
                    # Include fallback model info if a fallback was used
                    fb_model = getattr(engine._thread_local, '_fallback_model_used', None)
                    if fb_model:
                        done_data["fallback_model"] = fb_model
                        done_data["original_model"] = session.model
                    # Include file attachments
                    if created_files:
                        done_data["files"] = created_files
                    event_queue.put(("done", done_data))

                    # Continuous session summarization: refresh memory summary at token thresholds
                    try:
                        token_count = engine._estimate_conversation_tokens(session.messages)
                        last_summary_tokens = getattr(session, '_last_summary_at', 0)
                        threshold = 10000 if last_summary_tokens == 0 else last_summary_tokens + 5000
                        if token_count >= threshold:
                            session._last_summary_at = token_count
                            engine.trigger_memory_summary_refresh(session.agent_id)
                    except Exception:
                        pass

                    # Auto-memory extraction: check if response contains memorable info
                    try:
                        am_cfg = engine._get_auto_memory_config(session.agent_id)
                        min_msg_len = am_cfg.get("min_message_length", 20)
                        if am_cfg.get("enabled", True) and reply and message and len(message) > min_msg_len:
                            threading.Thread(
                                target=engine._auto_memory_extract,
                                args=(session.agent_id, message, reply[:1000]),
                                daemon=True,
                                name=f"auto_memory_{session.agent_id}"
                            ).start()
                    except Exception:
                        pass
                else:
                    event_queue.put(("done", {"text": "", "tokens": 0, "model": session.model}))
            except engine.TaskCancelled:
                # Remove user message from in-memory list and DB
                if session.messages and session.messages[-1].get("role") == "user":
                    session.messages.pop()
                event_queue.put(("error", {"message": "Cancelled"}))
            except SystemExit as e:
                event_queue.put(("error", {"message": f"Engine fatal error (exit code {e.code})"}))
            except Exception as e:
                import traceback
                traceback.print_exc()
                event_queue.put(("error", {"message": str(e)}))
            finally:
                session._streaming = False
                engine._current_agent = old_agent
                engine._mcp_manager = old_mcp
                # Clean up thread-local state
                engine._thread_local.current_agent = None
                engine._thread_local.mcp_manager = None
                engine._thread_local.memory_store = None
                engine._thread_local.plan_mode = False
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
            # Include manually-configured models mapped to this provider
            models_cfg = engine._models_config or {}
            for mid, mcfg in models_cfg.items():
                if mcfg.get("provider") == name and mid not in models and mcfg.get("enabled", True):
                    models.append(mid)
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

    def _handle_models_config_get(self):
        """GET /v1/models/config — return models configuration."""
        self._send_json({
            "models": dict(engine._models_config),
            "capabilities": list(engine.CAPABILITY_VALUES),
        })

    def _handle_models_config_save(self):
        """POST /v1/models/config — save/update/sync models configuration."""
        body = self._read_json()
        action = body.get("action", "save")
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)

            if action == "save":
                models = body.get("models", {})
                config["models"] = models
                engine._models_config = dict(models)

            elif action == "update":
                model_id = body.get("model_id", "")
                model_cfg = body.get("config", {})
                if not model_id:
                    self._send_json({"error": "model_id required"}, 400)
                    return
                config.setdefault("models", {})
                config["models"][model_id] = model_cfg
                engine._models_config[model_id] = model_cfg

            elif action == "sync":
                providers = server_config.get("providers", {})
                existing = config.get("models", {})
                synced = engine.init_models_config(providers, existing)
                config["models"] = synced

            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400)
                return

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            # Clear provider cache since model config changed
            BrainAgentHandler._provider_cache.clear()

            self._send_json({"status": "saved", "models": dict(engine._models_config)})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

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

    # ─── Projects & Ingestion Handlers ─────────────────────────────

    def _parse_agent_from_path(self, path: str) -> str:
        """Extract agent_id from /v1/agents/{id}/..."""
        parts = path.split("/")
        # /v1/agents/{id}/...
        if len(parts) >= 4:
            return parts[3]
        return ""

    def _parse_project_from_path(self, path: str) -> str:
        """Extract project name from /v1/agents/{id}/projects/{name}/..."""
        parts = path.split("/")
        # /v1/agents/{id}/projects/{name}...
        if len(parts) >= 6:
            return parts[5]
        return ""

    def _handle_list_projects(self, path: str):
        """GET /v1/agents/{id}/projects"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        projects = engine.ProjectManager.list_projects(agent_id)
        self._send_json({"agent": agent_id, "projects": projects})

    def _handle_create_project(self, path: str):
        """POST /v1/agents/{id}/projects"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Project name is required"}, 400)
            return
        result = engine.ProjectManager.create_project(
            agent_id, name,
            description=body.get("description", ""),
            config=body,
        )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result, 201)

    def _handle_project_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = engine.ProjectManager.get_project(agent_id, proj_name)
        if not project:
            self._send_json({"error": f"Project '{proj_name}' not found"}, 404)
            return
        self._send_json(project)

    def _handle_project_update(self, path: str):
        """PUT /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        body = self._read_json()
        result = engine.ProjectManager.update_project(agent_id, proj_name, body)
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_project_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        result = engine.ProjectManager.delete_project(agent_id, proj_name)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)

    def _handle_notes(self, path: str, method: str):
        """Handle notes CRUD: /v1/agents/{id}/projects/{name}/notes[/{path...}]"""
        from urllib.parse import unquote
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return

        # Extract note path: everything after /notes/ (or empty for list)
        # URL pattern: /v1/agents/{id}/projects/{name}/notes[/{path...}]
        parts = path.split("/notes", 1)
        note_path = ""
        if len(parts) > 1:
            note_path = unquote(parts[1].lstrip("/"))

        if method == "GET":
            if not note_path:
                # List all notes
                notes = engine.NoteManager.list_notes(agent_id, proj_name)
                self._send_json({"agent": agent_id, "project": proj_name, "notes": notes})
            else:
                # Get single note
                note = engine.NoteManager.get_note(agent_id, proj_name, note_path)
                if not note:
                    self._send_json({"error": f"Note '{note_path}' not found"}, 404)
                else:
                    self._send_json(note)

        elif method == "POST":
            body = self._read_json()
            note_path = body.get("path", note_path)
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            # Ensure .md extension
            if not note_path.endswith(".md"):
                note_path += ".md"
            content = body.get("content", "")
            action = body.get("action", "")
            if action == "create_folder":
                folder_path = body.get("folder_path", "")
                if not folder_path:
                    self._send_json({"error": "folder_path is required"}, 400)
                    return
                result = engine.NoteManager.create_folder(agent_id, proj_name, folder_path)
                self._send_json(result)
            elif action == "rename":
                new_path = body.get("new_path", "")
                if not new_path:
                    self._send_json({"error": "new_path is required"}, 400)
                    return
                if not new_path.endswith(".md"):
                    new_path += ".md"
                result = engine.NoteManager.rename_note(agent_id, proj_name, note_path, new_path)
                if "error" in result:
                    self._send_json(result, 400)
                else:
                    self._send_json(result)
            else:
                result = engine.NoteManager.create_note(agent_id, proj_name, note_path, content)
                if "error" in result:
                    self._send_json(result, 409)
                else:
                    self._send_json(result, 201)

        elif method == "PUT":
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            body = self._read_json()
            content = body.get("content", "")
            result = engine.NoteManager.update_note(agent_id, proj_name, note_path, content)
            if "error" in result:
                self._send_json(result, 404)
            else:
                self._send_json(result)

        elif method == "DELETE":
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            result = engine.NoteManager.delete_note(agent_id, proj_name, note_path)
            if "error" in result:
                self._send_json(result, 404)
            else:
                self._send_json(result)

    def _handle_agent_ingest(self, path: str):
        """POST /v1/agents/{id}/ingest — ingest file or URL into agent memory."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            result = self._handle_multipart_ingest(agent_id, None)
        else:
            body = self._read_json()
            url = body.get("url", "")
            if url:
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_url(
                    agent_id, url,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
            else:
                # Check for file_path (local file ingestion via JSON)
                file_path = body.get("file_path", "")
                if not file_path:
                    self._send_json({"error": "Provide 'url' or 'file_path'"}, 400)
                    return
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_file(
                    agent_id, file_path,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_project_ingest(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/ingest"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            result = self._handle_multipart_ingest(agent_id, proj_name)
        else:
            body = self._read_json()
            url = body.get("url", "")
            if url:
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_url(
                    agent_id, url, project_name=proj_name,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
            else:
                file_path = body.get("file_path", "")
                if not file_path:
                    self._send_json({"error": "Provide 'url' or 'file_path'"}, 400)
                    return
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_file(
                    agent_id, file_path, project_name=proj_name,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_multipart_ingest(self, agent_id: str, project_name: str | None) -> dict:
        """Parse multipart/form-data upload and ingest the file."""
        import cgi
        import tempfile
        content_type = self.headers.get("Content-Type", "")
        # Parse multipart form data
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        try:
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ)
        except Exception as e:
            return {"error": f"Failed to parse upload: {e}"}
        file_item = form["file"] if "file" in form else None
        if not file_item or not file_item.filename:
            return {"error": "No file uploaded"}
        # Save to temp file
        suffix = os.path.splitext(file_item.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_item.file.read())
            tmp_path = tmp.name
        try:
            tags_raw = form.getvalue("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            chunk_size = int(form.getvalue("chunk_size", "1500"))
            chunk_overlap = int(form.getvalue("chunk_overlap", "200"))
            result = engine.IngestManager.ingest_file(
                agent_id, tmp_path, project_name=project_name,
                tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            )
            # Replace tmp path source with original filename
            if "source" in result:
                result["source"] = file_item.filename
            return result
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _handle_list_ingested(self, path: str):
        """GET /v1/agents/{id}/ingested"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        docs = engine.IngestManager.list_ingested(agent_id)
        self._send_json({"agent": agent_id, "documents": docs})

    def _handle_project_docs(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/docs"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        docs = engine.IngestManager.list_ingested(agent_id, project_name=proj_name)
        self._send_json({"agent": agent_id, "project": proj_name, "documents": docs})

    def _handle_agent_ingested_delete(self, path: str):
        """DELETE /v1/agents/{id}/ingested/{hash}"""
        agent_id = self._parse_agent_from_path(path)
        parts = path.split("/")
        source_hash = parts[-1] if len(parts) >= 5 else ""
        if not agent_id or not source_hash:
            self._send_json({"error": "Missing agent or source hash"}, 400)
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)

    def _handle_project_doc_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/docs/{hash}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.split("/")
        source_hash = parts[-1] if len(parts) >= 8 else ""
        if not agent_id or not proj_name or not source_hash:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash, project_name=proj_name)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)

    def _handle_agent_graph(self, path: str):
        """GET /v1/agents/{id}/graph?project=<optional> — knowledge graph of agent memories."""
        from urllib.parse import urlparse, parse_qs
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return

        qs = parse_qs(urlparse(self.path).query)
        project = (qs.get("project") or [""])[0]

        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
            return

        # Determine directories to scan
        scan_dirs = []  # (dir_path, source_label)
        scan_dirs.append((agent_dir, "agent"))
        if project:
            proj_dir = os.path.join(agent_dir, "projects", project)
            if os.path.isdir(proj_dir):
                scan_dirs.append((proj_dir, f"project:{project}"))
            proj_ingest = os.path.join(proj_dir, "ingested")
            if os.path.isdir(proj_ingest):
                scan_dirs.append((proj_ingest, f"project:{project}"))
        # Also scan agent-level ingested dir
        agent_ingest = os.path.join(agent_dir, "ingested")
        if os.path.isdir(agent_ingest):
            scan_dirs.append((agent_ingest, "agent"))

        nodes = []
        edges = []
        seen_ids = set()
        ignore_files = {"soul.md", "tools.md"}

        for scan_dir, source_label in scan_dirs:
            for dirpath, _, filenames in os.walk(scan_dir):
                # Skip nested agent subdirs that aren't ingested/projects
                rel_to_agent = os.path.relpath(dirpath, agent_dir)
                if rel_to_agent not in (".", "ingested") and not rel_to_agent.startswith("projects"):
                    if rel_to_agent not in ("skills",):
                        continue

                for fname in sorted(filenames):
                    if not fname.endswith(".md") or fname in ignore_files:
                        continue
                    fpath = os.path.join(dirpath, fname)
                    rel = os.path.relpath(fpath, agent_dir)

                    if rel in seen_ids:
                        continue
                    seen_ids.add(rel)

                    try:
                        stat = os.stat(fpath)
                        with open(fpath, "r", errors="replace") as f:
                            raw = f.read(2000)  # frontmatter + start of body
                        fm, body = engine._parse_frontmatter(raw)
                    except Exception:
                        continue

                    # Determine type and name
                    mem_type = fm.get("type", "general")
                    is_ingested = fname.startswith("ingest-") or fm.get("source", "")
                    if is_ingested:
                        mem_type = "ingested"
                    name = fm.get("name") or fm.get("title") or fname.replace(".md", "").replace("_", " ").replace("-", " ")

                    # Source info
                    node_source = source_label
                    if is_ingested and fm.get("source"):
                        node_source = fm["source"]

                    # Chunk info
                    chunk_index = None
                    total_chunks = None
                    try:
                        ci = fm.get("chunk_index", "")
                        if ci != "":
                            chunk_index = int(ci)
                        tc = fm.get("total_chunks", "")
                        if tc != "":
                            total_chunks = int(tc)
                    except (ValueError, TypeError):
                        pass

                    description = fm.get("description", "").strip('"').strip("'")
                    # Content snippet for search (first 300 chars of body)
                    snippet = body[:300].replace("\n", " ").strip() if body else ""

                    nodes.append({
                        "id": rel,
                        "name": name.strip('"').strip("'"),
                        "type": mem_type,
                        "description": description,
                        "snippet": snippet,
                        "source": node_source,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "chunk_index": chunk_index,
                        "total_chunks": total_chunks,
                    })

                    # Extract edges from 'related' frontmatter
                    import re as _re
                    related_files = _re.findall(r'file:\s*(\S+\.md)', raw)
                    related_types = _re.findall(r'type:\s*(prev_chunk|next_chunk|same_source|references|same_topic|depends_on|contradicts|extends|co_recalled|same_folder)', raw)
                    for i, ref_file in enumerate(related_files):
                        edge_type = related_types[i] if i < len(related_types) else "references"
                        # Resolve ref_file relative to the same directory
                        if os.path.dirname(rel):
                            ref_rel = os.path.join(os.path.dirname(rel), ref_file)
                        else:
                            ref_rel = ref_file
                        edges.append({
                            "from": rel,
                            "to": ref_rel,
                            "type": edge_type,
                        })

        # Detect sequential ingested chunks that might lack explicit related links
        # Group by source hash prefix
        ingest_groups = {}
        for node in nodes:
            if node["type"] == "ingested" and node["chunk_index"] is not None:
                # Extract source hash from filename pattern: ingest-{hash}-{idx}.md
                base = os.path.basename(node["id"])
                parts = base.split("-")
                if len(parts) >= 3:
                    src_hash = parts[1]
                    ingest_groups.setdefault(src_hash, []).append(node)

        existing_edges = {(e["from"], e["to"]) for e in edges}
        for src_hash, group in ingest_groups.items():
            group.sort(key=lambda n: n["chunk_index"] or 0)
            for i in range(len(group) - 1):
                edge_key = (group[i]["id"], group[i + 1]["id"])
                if edge_key not in existing_edges:
                    edges.append({
                        "from": group[i]["id"],
                        "to": group[i + 1]["id"],
                        "type": "next_chunk",
                    })
                    existing_edges.add(edge_key)

        self._send_json({
            "agent": agent_id,
            "project": project or None,
            "nodes": nodes,
            "edges": edges,
        })

    def _handle_agent_graph_stats(self, path: str):
        """GET /v1/agents/{id}/graph/stats — knowledge graph statistics."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        try:
            stats = engine.get_graph_stats(agent_id)
            self._send_json(stats)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_agent_graph_discover(self, path: str):
        """POST /v1/agents/{id}/graph/discover — trigger relationship discovery."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
            return
        try:
            engine.trigger_relationship_discovery(agent_id)
            self._send_json({"status": "discovery_started", "agent": agent_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_agents_activity(self):
        """GET /v1/agents/activity — which agents are currently doing something."""
        activity = {}  # agent_id -> list of activity types

        # 1. Streaming chat sessions
        with sessions._lock:
            for s in sessions._sessions.values():
                if hasattr(s, 'cancel_token') and not s.cancel_token.cancelled:
                    # Check if session has an active worker thread
                    # A session is "streaming" if it was recently active and not cancelled
                    pass

        # Simpler: check which sessions are in streaming state via agentChats client-side
        # Instead, track streaming sessions server-side
        with sessions._lock:
            for s in sessions._sessions.values():
                if hasattr(s, '_streaming') and s._streaming:
                    activity.setdefault(s.agent_id, []).append("chat")

        # 2. Running delegated tasks
        if engine._task_runner:
            for t in engine._task_runner.list_tasks():
                if t.get("status") == "running":
                    aid = t.get("agent", "main")
                    if "delegate" not in activity.get(aid, []):
                        activity.setdefault(aid, []).append("delegate")

        # 3. Running scheduled tasks
        if engine._scheduler:
            for r in engine._scheduler.get_running_tasks():
                aid = r.get("agent", "main")
                if "schedule" not in activity.get(aid, []):
                    activity.setdefault(aid, []).append("schedule")

        self._send_json({"activity": activity})

    def _handle_teams_get(self):
        """GET /v1/teams — return team structure."""
        self._send_json(engine.get_team_structure())

    def _handle_teams_post(self):
        """POST /v1/teams — create, update, dissolve, or move teams."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "create":
            members = body.get("members", [])
            head_id = body.get("head", "")
            if not members:
                self._send_json({"error": "members is required (at least one agent)"}, 400)
                return
            if not head_id:
                head_id = members[0]
            # Ensure head is in members
            if head_id not in members:
                members.insert(0, head_id)
            # Validate members exist
            available = engine.list_agents()
            invalid = [m for m in members if m not in available]
            if invalid:
                self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                return
            if head_id not in available:
                self._send_json({"error": f"Head agent '{head_id}' not found"}, 404)
                return
            # Store team config on the head agent
            team_name = body.get("name", "")
            team_desc = body.get("description", "")
            team_avatar = body.get("avatar", "")
            cfg_path = os.path.join(engine.AGENTS_DIR, head_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                team_data = {"members": members, "head": head_id}
                if team_name:
                    team_data["name"] = team_name
                if team_desc:
                    team_data["description"] = team_desc
                if team_avatar:
                    team_data["avatar"] = team_avatar
                cfg["team"] = team_data
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "created", "head": head_id, "members": members})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "update":
            team_id = body.get("team_id", body.get("team_head", ""))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                if not isinstance(cfg.get("team"), dict):
                    self._send_json({"error": f"'{team_id}' does not hold a team config"}, 400)
                    return
                # Validate members exist
                available = engine.list_agents()
                if "members" in body:
                    members = body["members"]
                    invalid = [m for m in members if m not in available]
                    if invalid:
                        self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                        return
                    cfg["team"]["members"] = members
                if "head" in body:
                    new_head = body["head"]
                    # Ensure head is in members
                    if new_head not in cfg["team"].get("members", []):
                        cfg["team"]["members"].insert(0, new_head)
                    cfg["team"]["head"] = new_head
                    # If head changed, need to move team config to new head agent
                    old_head = cfg["team"].get("head", team_id)
                    if new_head != team_id:
                        # Move team config to new head's agent.json
                        new_cfg_path = os.path.join(engine.AGENTS_DIR, new_head, "agent.json")
                        with open(new_cfg_path, "r") as f:
                            new_cfg = json.load(f)
                        new_cfg["team"] = cfg.pop("team")
                        with open(new_cfg_path, "w") as f:
                            json.dump(new_cfg, f, indent=2)
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2)
                        self._send_json({"status": "updated", "team_id": new_head, "head": new_head})
                        return
                if "name" in body:
                    cfg["team"]["name"] = body["name"]
                if "description" in body:
                    cfg["team"]["description"] = body["description"]
                if "avatar" in body:
                    cfg["team"]["avatar"] = body["avatar"]
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "updated", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "dissolve":
            team_id = body.get("team_id", body.get("team_head", body.get("agent", "")))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                cfg.pop("team", None)
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "dissolved", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "move":
            agent_id = body.get("agent", "")
            from_team = body.get("from_team", "")
            to_team = body.get("to_team", "")
            if not agent_id:
                self._send_json({"error": "agent is required"}, 400)
                return
            try:
                # Remove from source team
                if from_team:
                    src_path = os.path.join(engine.AGENTS_DIR, from_team, "agent.json")
                    with open(src_path, "r") as f:
                        src_cfg = json.load(f)
                    if isinstance(src_cfg.get("team"), dict):
                        members = src_cfg["team"].get("members", [])
                        if agent_id in members:
                            members.remove(agent_id)
                            src_cfg["team"]["members"] = members
                        with open(src_path, "w") as f:
                            json.dump(src_cfg, f, indent=2)

                # Add to destination team
                if to_team:
                    dst_path = os.path.join(engine.AGENTS_DIR, to_team, "agent.json")
                    with open(dst_path, "r") as f:
                        dst_cfg = json.load(f)
                    if isinstance(dst_cfg.get("team"), dict):
                        members = dst_cfg["team"].get("members", [])
                        if agent_id not in members:
                            members.append(agent_id)
                            dst_cfg["team"]["members"] = members
                        with open(dst_path, "w") as f:
                            json.dump(dst_cfg, f, indent=2)

                self._send_json({"status": "moved", "agent": agent_id, "from": from_team, "to": to_team})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Workflow Handlers ---

    def _handle_workflow_list(self, path):
        """GET /v1/agents/{id}/workflows — list workflows for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        workflows = engine.WorkflowEngine.list_workflows(agent_id)
        self._send_json({"agent": agent_id, "workflows": workflows})

    def _handle_workflow_save(self, path):
        """POST /v1/agents/{id}/workflows — save a workflow definition."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        name = body.get("name", "")
        definition = body.get("definition", "")
        if not name:
            self._send_json({"error": "name is required"}, 400)
            return
        if not definition:
            self._send_json({"error": "definition is required"}, 400)
            return
        try:
            fpath = engine.WorkflowEngine.save_workflow(agent_id, name, definition)
            self._send_json({"status": "saved", "path": fpath})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_workflow_delete(self, path):
        """DELETE /v1/agents/{id}/workflows/{name} — delete a workflow."""
        parts = path.split("/")
        # /v1/agents/{id}/workflows/{name}
        if len(parts) < 6:
            self._send_json({"error": "Invalid path"}, 400)
            return
        agent_id = parts[3]
        wf_name = parts[5]
        if engine.WorkflowEngine.delete_workflow(agent_id, wf_name):
            self._send_json({"status": "deleted", "name": wf_name})
        else:
            self._send_json({"error": "Workflow not found"}, 404)

    def _handle_workflow_run(self, path):
        """POST /v1/agents/{id}/workflows/{name}/run — start a workflow execution."""
        parts = path.split("/")
        if len(parts) < 7:
            self._send_json({"error": "Invalid path"}, 400)
            return
        agent_id = parts[3]
        wf_name = parts[5]
        body = self._read_json()
        variables = body.get("variables", {})
        model = body.get("model")
        try:
            execution = engine.workflow_start(agent_id, wf_name, variables, model)
            self._send_json({"execution_id": execution.execution_id, "status": execution.status})
        except Exception as e:
            self._send_json({"error": str(e)}, 400)

    def _handle_workflow_list_executions(self):
        """GET /v1/workflows/executions — list running/recent executions."""
        executions = engine.workflow_list_executions()
        self._send_json({"executions": executions})

    def _handle_workflow_get_execution(self, path):
        """GET /v1/workflows/executions/{id} — execution status with stage results."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        self._send_json(ex.to_dict())

    def _handle_workflow_approve(self, path):
        """POST /v1/workflows/executions/{id}/approve — approve an approval gate."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if ex.status != "waiting_approval":
            self._send_json({"error": f"Execution is not waiting for approval (status: {ex.status})"}, 400)
            return
        body = self._read_json()
        action = body.get("action", "approve")
        if action == "reject":
            ex.reject()
            self._send_json({"status": "rejected", "execution_id": exec_id})
        else:
            ex.approve()
            self._send_json({"status": "approved", "execution_id": exec_id})

    def _handle_workflow_cancel(self, path):
        """POST /v1/workflows/executions/{id}/cancel — cancel execution."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        ex.cancel()
        self._send_json({"status": "cancelled", "execution_id": exec_id})

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
            if filename.endswith(".md"):
                self._qmd_trigger_update()
            # Re-sync memory summary schedules when agent.json changes
            if filename == "agent.json":
                try:
                    engine.ensure_memory_summary_schedules()
                except Exception:
                    pass
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
        # Register QMD collection for the new agent
        self._qmd_register_collection(agent_id, agent.dir)
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
            # Remove QMD collection for deleted agent
            self._qmd_remove_collection(agent_id)
            self._send_json({"status": "deleted", "agent": agent_id, "moved_to": dest})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_rename_agent(self):
        """POST /v1/agents/rename — rename an agent directory and update QMD collection."""
        body = self._read_json()
        old_id = body.get("agent", "")
        new_id = body.get("new_name", "").strip()
        if not old_id or not new_id or ".." in old_id or ".." in new_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        if old_id == new_id:
            self._send_json({"status": "ok", "agent": new_id})
            return
        if old_id == "main":
            self._send_json({"error": "Cannot rename the main agent"}, 400)
            return
        # Validate new_id: alphanumeric + hyphens/underscores only
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_-]+$', new_id):
            self._send_json({"error": "Agent name must be alphanumeric (hyphens/underscores allowed)"}, 400)
            return
        old_dir = os.path.join(engine.AGENTS_DIR, old_id)
        new_dir = os.path.join(engine.AGENTS_DIR, new_id)
        if not os.path.isdir(old_dir):
            self._send_json({"error": f"Agent '{old_id}' not found"}, 404)
            return
        if os.path.exists(new_dir):
            self._send_json({"error": f"Agent '{new_id}' already exists"}, 409)
            return
        try:
            os.rename(old_dir, new_dir)
            # Update QMD: remove old collection, add new one, re-index in background
            if self._is_qmd_running():
                self._qmd_run(["collection", "remove", old_id])
                self._qmd_run(["collection", "add", new_dir, "--name", new_id])
                self._qmd_trigger_update(delay=1.0)
            self._send_json({"status": "renamed", "agent": new_id, "old_name": old_id})
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

    # Debounced QMD update: coalesce rapid file writes into one qmd update+embed run
    _qmd_update_timer: threading.Timer | None = None
    _qmd_update_lock = threading.Lock()

    @classmethod
    def _qmd_trigger_update(cls, delay: float = 2.0) -> None:
        """Schedule qmd update+embed after `delay` seconds, cancelling any pending one."""
        with cls._qmd_update_lock:
            if cls._qmd_update_timer is not None:
                cls._qmd_update_timer.cancel()
            def _run():
                cls._qmd_run(["update"], timeout=120)
                cls._qmd_run(["embed"], timeout=300)
            cls._qmd_update_timer = threading.Timer(delay, _run)
            cls._qmd_update_timer.daemon = True
            cls._qmd_update_timer.start()

    @staticmethod
    def _qmd_run(args: list, timeout: int = 10) -> bool:
        """Run a qmd command. Returns True on success."""
        qmd_bin = BrainAgentHandler._find_qmd()
        if not qmd_bin:
            return False
        try:
            env = os.environ.copy()
            env["PATH"] = os.path.dirname(qmd_bin) + ":" + env.get("PATH", "")
            r = subprocess.run([qmd_bin] + args, capture_output=True, text=True,
                               timeout=timeout, env=env)
            return r.returncode == 0
        except Exception:
            return False

    def _qmd_register_collection(self, agent_id: str, agent_dir: str) -> None:
        """Add a QMD collection for an agent if QMD is running and collection doesn't exist.
        Runs qmd update in a background thread so files are indexed promptly."""
        if not self._is_qmd_running():
            return
        existing = {(c["name"] if isinstance(c, dict) else c) for c in self._qmd_collections()}
        if agent_id not in existing:
            self._qmd_run(["collection", "add", agent_dir, "--name", agent_id])
            self._qmd_trigger_update(delay=1.0)

    def _qmd_remove_collection(self, agent_id: str) -> None:
        """Remove a QMD collection for a deleted agent."""
        if not self._is_qmd_running():
            return
        self._qmd_run(["collection", "remove", agent_id])

    @staticmethod
    def _is_qmd_running() -> bool:
        """Check if QMD is reachable with a lightweight socket connect (no session created)."""
        import socket
        try:
            with socket.create_connection(("localhost", _QMD_PORT), timeout=1):
                return True
        except (OSError, socket.timeout):
            return False

    @staticmethod
    def _is_telegram_running() -> bool:
        try:
            return _telegram_mod.telegram_service.running
        except AttributeError:
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
            if r.returncode != 0:
                return []
            collections = []
            current = None
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(("Collections", "Pattern", "Files", "Updated", "Ignore")) and "(" in line:
                    name = line.split("(")[0].strip()
                    if name:
                        current = {"name": name}
                        collections.append(current)
                elif current and line.startswith("Files:"):
                    current["files"] = line.split(":")[1].strip().split()[0]

            # Enrich with index health stats from QMD SQLite
            try:
                import sqlite3 as _sq3, hashlib as _hl
                idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
                if os.path.isfile(idx_path):
                    conn = _sq3.connect(idx_path, timeout=2)
                    conn.row_factory = _sq3.Row
                    for coll in collections:
                        name = coll["name"]
                        agent_dir = os.path.join(engine.AGENTS_DIR, name)
                        if not os.path.isdir(agent_dir):
                            continue
                        # Build index of QMD docs for this collection
                        rows = conn.execute(
                            "SELECT d.path, d.hash, "
                            "  (SELECT cv.embedded_at FROM content_vectors cv WHERE cv.hash = d.hash LIMIT 1) AS embedded_at "
                            "FROM documents d WHERE d.collection = ? AND d.active = 1",
                            (name,),
                        ).fetchall()
                        qmd_idx = {}
                        for row in rows:
                            qmd_idx[row["path"].lower()] = {"hash": row["hash"], "embedded_at": row["embedded_at"]}

                        # Walk filesystem and compute stats
                        total = 0
                        indexed = 0
                        embedded = 0
                        stale = 0
                        not_indexed = 0
                        for dirpath, _, filenames in os.walk(agent_dir):
                            for fname in filenames:
                                if not fname.endswith(".md"):
                                    continue
                                total += 1
                                fpath = os.path.join(dirpath, fname)
                                rel = os.path.relpath(fpath, agent_dir)
                                # QMD normalizes: lowercase + underscores→hyphens
                                norm = rel.lower().replace("_", "-")
                                idx = qmd_idx.get(norm)
                                if not idx:
                                    not_indexed += 1
                                    continue
                                # Check hash freshness
                                try:
                                    with open(fpath, "rb") as fh:
                                        file_hash = _hl.sha256(fh.read()).hexdigest()
                                    is_current = (file_hash == idx["hash"])
                                except OSError:
                                    is_current = None
                                if is_current:
                                    indexed += 1
                                else:
                                    stale += 1
                                if idx["embedded_at"] and is_current:
                                    embedded += 1

                        coll["total"] = total
                        coll["indexed"] = indexed
                        coll["embedded"] = embedded
                        coll["stale"] = stale
                        coll["not_indexed"] = not_indexed
                    conn.close()
            except Exception:
                pass

            return collections
        except Exception:
            pass
        return []

    def _handle_qmd_docs(self):
        """GET /v1/services/qmd/docs?collection=<name>[&file=<filename>] — list or read indexed docs."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        collection = (qs.get("collection") or [""])[0]
        filename = (qs.get("file") or [""])[0]

        if not collection:
            self._send_json({"error": "collection required"}, 400)
            return

        agent_dir = os.path.join(engine.AGENTS_DIR, collection)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Collection dir not found: {agent_dir}"}, 404)
            return

        if filename:
            fpath, err = self._qmd_safe_path(collection, filename)
            if err:
                self._send_json({"error": err}, 400)
                return
            if not os.path.isfile(fpath):
                self._send_json({"error": "File not found"}, 404)
                return
            try:
                with open(fpath, "r", errors="replace") as f:
                    content = f.read()
                self._send_json({"file": filename, "collection": collection, "content": content})
            except OSError as e:
                self._send_json({"error": str(e)}, 500)
        else:
            # List all .md files recursively (matches QMD pattern **/*.md)
            files = []
            # Load QMD index data for this collection
            qmd_index = {}  # rel_path -> {hash, embedded_at}
            try:
                import sqlite3 as _sqlite3
                idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
                if os.path.isfile(idx_path):
                    conn = _sqlite3.connect(idx_path, timeout=2)
                    conn.row_factory = _sqlite3.Row
                    rows = conn.execute(
                        "SELECT d.path, d.hash, "
                        "  (SELECT cv.embedded_at FROM content_vectors cv WHERE cv.hash = d.hash LIMIT 1) AS embedded_at "
                        "FROM documents d WHERE d.collection = ? AND d.active = 1",
                        (collection,),
                    ).fetchall()
                    for r in rows:
                        qmd_index[r["path"].lower()] = {
                            "hash": r["hash"],
                            "embedded_at": r["embedded_at"],
                        }
                    conn.close()
            except Exception:
                pass  # Index unavailable — degrade gracefully
            try:
                import hashlib as _hashlib
                for dirpath, _, filenames in os.walk(agent_dir):
                    for fname in sorted(filenames):
                        if fname.endswith(".md"):
                            fpath = os.path.join(dirpath, fname)
                            rel = os.path.relpath(fpath, agent_dir)
                            stat = os.stat(fpath)
                            entry = {
                                "name": rel,
                                "size": stat.st_size,
                                "modified": stat.st_mtime,
                            }
                            # QMD normalizes paths: lowercase + underscores→hyphens
                            idx = qmd_index.get(rel.lower().replace("_", "-"))
                            if idx:
                                # Compute current file hash to compare with indexed hash
                                try:
                                    with open(fpath, "rb") as fh:
                                        file_hash = _hashlib.sha256(fh.read()).hexdigest()
                                    entry["indexed"] = True
                                    entry["embedded_at"] = idx["embedded_at"]
                                    entry["current"] = (file_hash == idx["hash"])
                                except OSError:
                                    entry["indexed"] = True
                                    entry["embedded_at"] = idx["embedded_at"]
                                    entry["current"] = None
                            else:
                                entry["indexed"] = False
                            files.append(entry)
                files.sort(key=lambda f: f["name"])
            except OSError as e:
                self._send_json({"error": str(e)}, 500)
                return
            self._send_json({"collection": collection, "files": files})

    def _qmd_safe_path(self, collection: str, filename: str):
        """Resolve and validate a file path within a collection. Returns fpath or None."""
        agent_dir = os.path.join(engine.AGENTS_DIR, collection)
        if not os.path.isdir(agent_dir):
            return None, "Collection not found"
        fpath = os.path.normpath(os.path.join(agent_dir, filename))
        if not fpath.startswith(agent_dir + os.sep) or not fpath.endswith(".md"):
            return None, "Invalid filename"
        return fpath, None

    def _handle_qmd_doc_save(self):
        """POST /v1/services/qmd/docs — save content to a file."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        collection = (qs.get("collection") or [""])[0]
        filename = (qs.get("file") or [""])[0]
        if not collection or not filename:
            self._send_json({"error": "collection and file required"}, 400)
            return
        fpath, err = self._qmd_safe_path(collection, filename)
        if err:
            self._send_json({"error": err}, 400)
            return
        body = self._read_json()
        content = body.get("content", "")
        try:
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w") as f:
                f.write(content)
            self._send_json({"status": "saved", "file": filename})
            self._qmd_trigger_update()
        except OSError as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_qmd_doc_delete(self):
        """DELETE /v1/services/qmd/docs — delete a file."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        collection = (qs.get("collection") or [""])[0]
        filename = (qs.get("file") or [""])[0]
        if not collection or not filename:
            self._send_json({"error": "collection and file required"}, 400)
            return
        # Protect non-memory files: soul.md and agent.json are managed elsewhere
        if filename in ("soul.md", "agent.json", "mcp.json", "gmail.json"):
            self._send_json({"error": f"{filename} cannot be deleted here"}, 403)
            return
        fpath, err = self._qmd_safe_path(collection, filename)
        if err:
            self._send_json({"error": err}, 400)
            return
        if not os.path.isfile(fpath):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            os.remove(fpath)
            self._send_json({"status": "deleted", "file": filename})
            self._qmd_trigger_update()
        except OSError as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_memory_summary_get(self, path):
        """GET /v1/agents/<id>/memory-summary — get memory summary content, config, and relationship discovery status."""
        parts = path.split("/")
        agent_id = parts[3]
        try:
            summary = engine.get_memory_summary(agent_id)
            config = engine._get_memory_summary_config(agent_id)
            rd_config = engine._get_relationship_discovery_config(agent_id)
            rd_stats = engine.get_graph_stats(agent_id) if hasattr(engine, 'get_graph_stats') else {}
            am_config = engine._get_auto_memory_config(agent_id)
            self._send_json({
                "agent": agent_id,
                "summary": summary or "",
                "config": config,
                "auto_memory": am_config,
                "relationship_discovery": {
                    "config": rd_config,
                    "stats": rd_stats,
                },
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_memory_summary_post(self, path):
        """POST /v1/agents/<id>/memory-summary — update, reset, or refresh memory summary."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        action = body.get("action", "")

        if action == "update":
            # Direct edit of the summary content
            content = body.get("content", "")
            ms = engine.MemoryStore(agent_id)
            ms.store("Memory Summary", content,
                     description="Auto-generated synthesis of recent conversations and task executions, updated periodically",
                     mem_type="general")
            self._send_json({"status": "updated", "agent": agent_id})
        elif action == "reset":
            result = engine.reset_memory_summary(agent_id)
            self._send_json(result)
        elif action == "refresh":
            engine.trigger_memory_summary_refresh(agent_id)
            self._send_json({"status": "refresh_scheduled", "agent": agent_id})
        elif action == "discover":
            engine.trigger_relationship_discovery(agent_id)
            self._send_json({"status": "discovery_scheduled", "agent": agent_id})
        elif action == "toggle_discovery":
            enabled = body.get("enabled", True)
            # Update agent.json
            agent_cfg = engine.AgentConfig(agent_id)
            cfg = agent_cfg.config
            rd = cfg.get("relationship_discovery", {})
            if not isinstance(rd, dict):
                rd = {}
            rd["enabled"] = bool(enabled)
            cfg["relationship_discovery"] = rd
            cfg_path = os.path.join(engine.AGENTS_DIR, agent_id, "agent.json")
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
            engine.ensure_relationship_discovery_schedules()
            self._send_json({"status": "updated", "enabled": bool(enabled), "agent": agent_id})
        elif action == "toggle_auto_memory":
            enabled = body.get("enabled", True)
            # Update agent.json
            agent_cfg = engine.AgentConfig(agent_id)
            cfg = agent_cfg.config
            am = cfg.get("auto_memory", {})
            if not isinstance(am, dict):
                am = {}
            am["enabled"] = bool(enabled)
            cfg["auto_memory"] = am
            cfg_path = os.path.join(engine.AGENTS_DIR, agent_id, "agent.json")
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
            self._send_json({"status": "updated", "enabled": bool(enabled), "agent": agent_id})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_costs(self):
        """GET /v1/costs?agent=X&hours=24 — cost stats."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        stats = engine._cost_tracker.get_stats(agent=agent, hours=hours)
        self._send_json(stats)

    def _handle_costs_daily(self):
        """GET /v1/costs/daily?agent=X&days=7 — daily breakdown."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        days = int(params.get("days", "7"))
        daily = engine._cost_tracker.get_daily(agent=agent, days=days)
        self._send_json({"daily": daily, "days": days, "agent_filter": agent})

    def _handle_agent_commands_get(self, path):
        """GET /v1/agents/{id}/commands — list custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        agent = engine.AgentConfig(agent_id)
        self._send_json({"commands": agent.load_commands()})

    def _handle_agent_commands_post(self, path):
        """POST /v1/agents/{id}/commands — save custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        body = self._read_json()
        commands = body.get("commands", [])
        agent = engine.AgentConfig(agent_id)
        agent.save_commands(commands)
        self._send_json({"status": "saved", "count": len(commands)})

    # --- Traces & Audit Handlers ---

    def _handle_traces_list(self):
        """GET /v1/traces?agent=X&hours=24&limit=50 — recent traces."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        limit = int(params.get("limit", "50"))
        traces = engine._trace_manager.get_traces(agent=agent, hours=hours, limit=limit)
        self._send_json({"traces": traces, "count": len(traces)})

    def _handle_trace_detail(self, path):
        """GET /v1/traces/{trace_id} — all spans for a trace."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        trace_id = path.split("/")[-1]
        spans = engine._trace_manager.get_trace(trace_id)
        if not spans:
            self._send_json({"error": "Trace not found"}, 404)
            return
        total_duration = sum(s.get("duration_ms", 0) for s in spans)
        total_tokens_in = sum(s.get("tokens_in", 0) for s in spans)
        total_tokens_out = sum(s.get("tokens_out", 0) for s in spans)
        self._send_json({
            "trace_id": trace_id,
            "spans": spans,
            "span_count": len(spans),
            "total_duration_ms": total_duration,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
        })

    def _handle_audit_list(self):
        """GET /v1/audit?agent=X&type=Y&from=Z&limit=50 — audit log."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        action_type = unquote(params.get("type", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        limit = int(params.get("limit", "50"))
        entries = engine._audit_log.query(agent=agent, action_type=action_type,
                                           from_ts=from_ts, limit=limit)
        self._send_json({"entries": entries, "count": len(entries)})

    def _handle_audit_export(self):
        """GET /v1/audit/export?agent=X&format=csv — CSV download."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        to_ts = unquote(params.get("to", "")) or None
        fmt = params.get("format", "csv")
        if fmt == "csv":
            csv_data = engine._audit_log.export_csv(agent=agent, from_ts=from_ts, to_ts=to_ts)
            body = csv_data.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=audit_log.csv")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            entries = engine._audit_log.query(agent=agent, from_ts=from_ts, limit=10000)
            self._send_json({"entries": entries, "count": len(entries)})

    # --- MCP Connection Handlers ---

    def _handle_mcp_list(self):
        """GET /v1/mcp/connections — list all MCP connections."""
        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"connections": []})
            return
        servers = mcp.list_servers()
        self._send_json({"connections": servers})

    def _handle_mcp_connect(self):
        """POST /v1/mcp/connect — connect to a new MCP server at runtime."""
        body = self._read_json()
        url = body.get("url", "")
        name = body.get("name", "")
        transport = body.get("transport", "sse")
        persist = body.get("persist", False)

        if not url or not name:
            self._send_json({"error": "Both 'url' and 'name' are required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            mcp = engine.MCPManager()
            engine._mcp_manager = mcp

        result = mcp.connect_runtime(url, name, transport)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return

        # Persist to mcp.json if requested
        if persist:
            mcp_json_path = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
            try:
                existing = {}
                if os.path.exists(mcp_json_path):
                    with open(mcp_json_path, "r") as f:
                        existing = json.load(f)
                if transport == "stdio":
                    parts = url.split()
                    existing[name] = {"transport": "stdio", "command": parts[0],
                                      "args": parts[1:] if len(parts) > 1 else []}
                else:
                    existing[name] = {"transport": "sse", "url": url}
                with open(mcp_json_path, "w") as f:
                    json.dump(existing, f, indent=2)
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)

        self._send_json(result)

    def _handle_mcp_disconnect(self):
        """POST /v1/mcp/disconnect — disconnect a runtime MCP server."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "'name' is required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"error": "No MCP manager available"}, 400)
            return

        result = mcp.disconnect_runtime(name)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return
        self._send_json(result)

    def _handle_refine(self):
        """POST /v1/refine — refine text with LLM one-shot call."""
        body = self._read_json()
        text = body.get("text", "")
        context = body.get("context", "general")
        if not text:
            self._send_json({"error": "No text provided"}, 400)
            return

        # Find fastest/cheapest model — prefer Haiku > Sonnet > cheapest
        refine_model = body.get("model")
        if not refine_model and engine._models_config:
            candidates = []
            for mid, cfg in engine._models_config.items():
                if not cfg.get("enabled", True):
                    continue
                ml = mid.lower()
                if "haiku" in ml:
                    score = 0
                elif "sonnet" in ml:
                    score = 1
                else:
                    score = 2 + (cfg.get("cost_input", 0) or 0)
                candidates.append((mid, score))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                refine_model = candidates[0][0]
        if not refine_model:
            refine_model = server_config.get("default_model", "")

        if not refine_model:
            self._send_json({"error": "No model available for refinement"}, 503)
            return

        provider = self._resolve_provider(refine_model)

        # Build context from current session
        session_id = body.get("session_id", "")
        agent_id = body.get("agent", "main")
        project = body.get("project", "")
        chat_context = ""

        # Get agent info
        try:
            agent_cfg = engine.AgentConfig(agent_id)
            soul_summary = (agent_cfg.soul or "")[:200]
            if soul_summary:
                chat_context += f"Agent: {agent_id} — {soul_summary}\n"
        except Exception:
            pass

        # Get recent conversation for context (last 5 messages)
        if session_id:
            try:
                s = sessions.get(session_id)
                if s and s.messages:
                    recent = s.messages[-5:]
                    chat_context += "Recent conversation:\n"
                    for m in recent:
                        role = m.get("role", "?")
                        content = m.get("content", "")
                        if isinstance(content, str):
                            chat_context += f"  [{role}] {content[:150]}\n"
                    chat_context += "\n"
            except Exception:
                pass

        if project:
            chat_context += f"Active project: {project}\n"

        context_block = ""
        if chat_context:
            context_block = (
                f"\nCONTEXT (use this to make the rewrite more specific and relevant):\n"
                f"{chat_context}\n"
            )

        system_prompt = (
            "You are a PROMPT REWRITER for an AI chat system. "
            "The user will give you a draft prompt/message they want to send to an AI assistant. "
            "Your job is to rewrite it into a better, clearer version of the SAME request. "
            "CRITICAL RULES:\n"
            "- Output ONLY the rewritten prompt, nothing else\n"
            "- Do NOT answer the question or fulfill the request — REWRITE it\n"
            "- Do NOT add explanations, analysis, alternatives, or commentary\n"
            "- Do NOT use markdown headings, bullet points, or formatting\n"
            "- The output replaces the user's input in a chat box — it must be a clean prompt\n"
            "- Fix grammar, spelling, punctuation\n"
            "- Make the request clearer and more specific using the context provided\n"
            "- Keep the same intent and language\n"
            "Example: Input: 'whats weather vienna' → Output: 'What is the weather like in Vienna today?'"
            + context_block
        )
        messages = [{"role": "user", "content": f"Rewrite this prompt (output ONLY the rewritten version):\n\n{text}"}]

        try:
            result = engine.send_message(
                messages, refine_model, provider["api_key"],
                provider["base_url"], provider["api_type"],
                silent=True, tools=False,
            )
            self._send_json({"refined": result or text, "model": refine_model})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

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
                "default_provider": next((name for name, p in server_config.get("providers", {}).items() if p.get("default_model") == server_config.get("default_model")), ""),
                "default_model": server_config.get("default_model", ""),
            },
            "qmd": {
                "status": "running" if qmd_running else "stopped",
                "port": _QMD_PORT,
                "collections": collections,
            },
            "telegram": {
                "status": "running" if tg_running else "stopped",
                "bot": _telegram_mod.telegram_service.bot_username if tg_running else "",
                "enabled": server_config.get("telegram_enabled", True),
            },
            "channels": _adapters_mod.channel_manager.status() if _adapters_mod.channel_manager else [],
            "nodes": self._get_nodes_summary(),
        })

    def _get_nodes_summary(self):
        """Get a summary of node statuses."""
        with _node_lock:
            total = len(_node_registry)
            connected = sum(1 for info in _node_registry.values() if info["status"] == "connected")
            return {"total": total, "connected": connected}

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
        """POST /v1/services/telegram — start/stop/restart/enable/disable Telegram."""
        body = self._read_json()
        action = body.get("action", "")
        svc = _telegram_mod.telegram_service

        if action == "start":
            ok = _start_telegram_service()
            self._send_json({"status": "started" if ok else "error",
                             "running": svc.running, "error": svc.error})

        elif action == "stop":
            svc.stop()
            self._send_json({"status": "stopped", "running": False})

        elif action == "restart":
            svc.stop()
            ok = _start_telegram_service()
            self._send_json({"status": "restarted" if ok else "error",
                             "running": svc.running, "error": svc.error})

        elif action == "enable":
            _set_telegram_enabled(True)
            if not svc.running:
                _start_telegram_service()
            self._send_json({"status": "enabled", "running": svc.running,
                             "enabled": True})

        elif action == "disable":
            _set_telegram_enabled(False)
            svc.stop()
            self._send_json({"status": "disabled", "running": False,
                             "enabled": False})

        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_server_config(self):
        """POST /v1/services/server — update server defaults (default_model)."""
        body = self._read_json()
        model = body.get("default_model")
        if not model:
            self._send_json({"error": "default_model required"}, 400)
            return
        # Find which provider has this model
        providers = server_config.get("providers", {})
        provider_name = None
        # Check models config for provider mapping
        mcfg = engine._models_config or {}
        if model in mcfg and mcfg[model].get("provider"):
            provider_name = mcfg[model]["provider"]
        else:
            for pname, p in providers.items():
                if p.get("default_model") == model:
                    provider_name = pname
                    break
        # Update server_config in memory
        server_config["default_model"] = model
        if provider_name:
            server_config["api_key"] = providers[provider_name].get("api_key", "")
            server_config["base_url"] = providers[provider_name].get("base_url", "")
            server_config["api_type"] = providers[provider_name].get("type", "anthropic")
        # Persist to config.json
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            if provider_name:
                config["default_provider"] = provider_name
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({"status": "saved", "default_model": model,
                         "default_provider": provider_name or ""})

    # --- Tools config handlers ---

    def _handle_tools_config_get(self):
        """GET /v1/tools/config — return tool config with sensitive values masked."""
        cfg = engine.get_tool_config()
        # Mask sensitive values
        masked = {}
        for tool_name, tool_cfg in cfg.items():
            masked[tool_name] = dict(tool_cfg)
            for key in ("api_key", "app_password"):
                val = masked[tool_name].get(key, "")
                if val and len(val) > 4:
                    masked[tool_name][key] = "*" * (len(val) - 4) + val[-4:]
        self._send_json(masked)

    def _handle_tools_status(self):
        """GET /v1/tools/status — return tool availability and status."""
        self._send_json(engine.get_tool_status())

    def _handle_tools_config_save(self):
        """POST /v1/tools/config — save tool configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No configuration provided"}, 400)
            return
        # Don't overwrite sensitive fields if masked value is sent
        existing = engine.get_tool_config()
        for tool_name, tool_cfg in body.items():
            for key in ("api_key", "app_password"):
                val = tool_cfg.get(key, "")
                if val and val.startswith("*"):
                    # Masked value — keep existing
                    tool_cfg[key] = existing.get(tool_name, {}).get(key, "")
        result = engine.save_tool_config(body)
        if "error" in result:
            self._send_json(result, 500)
        else:
            self._send_json({"status": "saved", "config": result})

    def _handle_restart(self):
        """POST /v1/restart — restart the server process."""
        self._send_json({"status": "restarting"})
        # Schedule restart after response is sent
        def do_restart():
            time.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do_restart, daemon=True).start()

    # --- Notification handlers ---

    def _handle_notifications_list(self):
        """GET /v1/notifications — list recent notifications."""
        if not _notification_manager:
            self._send_json({"notifications": [], "unread": 0})
            return
        notifs = _notification_manager.get_notifications(limit=50)
        unread = _notification_manager.get_unread_count()
        self._send_json({"notifications": notifs, "unread": unread})

    def _handle_notifications_unread(self):
        """GET /v1/notifications/unread — get unread count."""
        count = _notification_manager.get_unread_count() if _notification_manager else 0
        self._send_json({"unread": count})

    def _handle_notifications_settings_post(self):
        """POST /v1/notifications/settings — save notification config."""
        body = self._read_json()
        if not _notification_manager:
            self._send_json({"error": "Notification manager not initialized"}, 500)
            return
        _notification_manager.update_config(body)
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            config["notifications"] = body
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            self._send_json({"error": f"Failed to save: {e}"}, 500)
            return
        self._send_json({"status": "saved"})

    def _handle_notifications_dismiss(self):
        """POST /v1/notifications/dismiss — dismiss notification(s)."""
        body = self._read_json()
        nid = body.get("id")
        if not _notification_manager:
            self._send_json({"error": "Not initialized"}, 500)
            return
        if nid == "all":
            _notification_manager.clear_all()
        elif nid:
            _notification_manager.dismiss(nid)
        self._send_json({"status": "dismissed"})

    def _handle_notifications_read(self):
        """POST /v1/notifications/read — mark notification(s) as read."""
        body = self._read_json()
        nid = body.get("id")  # None = mark all read
        if _notification_manager:
            _notification_manager.mark_read(nid)
        self._send_json({"status": "read"})

    # --- Backup / Restore handlers ---

    def _handle_backup_info(self):
        """GET /v1/backup/info — return what would be backed up."""
        import tarfile as _tarfile
        base = os.path.dirname(os.path.abspath(__file__))
        agents_dir = os.path.join(base, "agents")
        agent_names = engine.list_agents()
        total_files = 0
        total_size = 0
        agent_info = []
        for aname in agent_names:
            adir = os.path.join(agents_dir, aname)
            mems = len([f for f in os.listdir(adir) if f.endswith(".md")]) if os.path.isdir(adir) else 0
            skills_dir = os.path.join(adir, "skills")
            skills = len(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else 0
            agent_info.append({"name": aname, "memories": mems, "skills": skills})
            if os.path.isdir(adir):
                for root, dirs, files in os.walk(adir):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        if not f.endswith((".pyc", ".DS_Store")):
                            fp = os.path.join(root, f)
                            total_files += 1
                            try:
                                total_size += os.path.getsize(fp)
                            except OSError:
                                pass
        self._send_json({
            "agents": agent_info,
            "agent_count": len(agent_names),
            "total_files": total_files,
            "estimated_size_bytes": total_size,
        })

    def _handle_backup_create(self):
        """POST /v1/backup — create a tar.gz backup archive."""
        import tarfile as _tarfile
        import tempfile
        body = self._read_json()
        backup_type = body.get("type", "full")
        target_agent = body.get("agent")
        include_keys = body.get("include_keys", False)

        base = os.path.dirname(os.path.abspath(__file__))
        agents_dir = os.path.join(base, "agents")
        backup_dir = os.path.join(base, "backups")
        os.makedirs(backup_dir, exist_ok=True)

        _EXCLUDE = {"__pycache__", ".DS_Store", "node_modules"}
        _EXCLUDE_EXT = {".pyc", ".db-wal", ".db-shm"}

        def _should_exclude(name):
            base_name = os.path.basename(name)
            if base_name in _EXCLUDE:
                return True
            _, ext = os.path.splitext(base_name)
            if ext in _EXCLUDE_EXT:
                return True
            return False

        ts = time.strftime("%Y%m%dT%H%M%S")
        if backup_type == "agent" and target_agent:
            fname = f"{target_agent.lower()}-{ts}.brain-backup.tar.gz"
        else:
            fname = f"backup-{ts}.brain-backup.tar.gz"
        backup_path = os.path.join(backup_dir, fname)

        try:
            with _tarfile.open(backup_path, "w:gz") as tar:
                prefix = f"backup-{ts}"

                # Add config.json (with redacted keys)
                config_path = os.path.join(base, "config.json")
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                    if not include_keys:
                        # Redact API keys
                        for pname, pcfg in config.get("providers", {}).items():
                            if "api_key" in pcfg:
                                pcfg["api_key"] = "REDACTED"
                        if "gmail" in config:
                            for k in list(config["gmail"].keys()):
                                if "password" in k.lower() or "secret" in k.lower():
                                    config["gmail"][k] = "REDACTED"
                    redacted_json = json.dumps(config, indent=2).encode("utf-8")
                    import io
                    info = _tarfile.TarInfo(name=f"{prefix}/config.json")
                    info.size = len(redacted_json)
                    tar.addfile(info, io.BytesIO(redacted_json))

                # Add agents
                agents_to_backup = [target_agent] if (backup_type == "agent" and target_agent) else engine.list_agents()
                for aname in agents_to_backup:
                    adir = os.path.join(agents_dir, aname)
                    if not os.path.isdir(adir):
                        continue
                    for root, dirs, files in os.walk(adir):
                        dirs[:] = [d for d in dirs if d not in _EXCLUDE]
                        for f in files:
                            if _should_exclude(f):
                                continue
                            fp = os.path.join(root, f)
                            arcname = f"{prefix}/agents/{aname}/{os.path.relpath(fp, adir)}"
                            try:
                                tar.add(fp, arcname=arcname)
                            except (OSError, PermissionError):
                                pass

                # Add databases (full backup only)
                if backup_type != "agent":
                    for db_name in ("chats.db", "scheduler.db", "costs.db"):
                        db_path = os.path.join(agents_dir, "main", db_name)
                        if os.path.exists(db_path):
                            # Safe SQLite copy using backup API
                            import sqlite3
                            tmp_db = os.path.join(backup_dir, f"_tmp_{db_name}")
                            try:
                                src = sqlite3.connect(db_path)
                                dst = sqlite3.connect(tmp_db)
                                src.backup(dst)
                                src.close()
                                dst.close()
                                tar.add(tmp_db, arcname=f"{prefix}/databases/{db_name}")
                            except Exception:
                                # Fallback: direct copy
                                tar.add(db_path, arcname=f"{prefix}/databases/{db_name}")
                            finally:
                                try:
                                    os.unlink(tmp_db)
                                except OSError:
                                    pass

            size = os.path.getsize(backup_path)
            self._send_json({
                "status": "created",
                "path": backup_path,
                "filename": fname,
                "size_bytes": size,
                "type": backup_type,
                "agents": agents_to_backup,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    def _handle_restore(self):
        """POST /v1/restore — restore from a backup archive."""
        import tarfile as _tarfile
        body = self._read_json()
        backup_path = body.get("path", "")
        strategy = body.get("strategy", "merge")

        if not backup_path or not os.path.exists(backup_path):
            self._send_json({"error": f"Backup file not found: {backup_path}"}, 400)
            return

        base = os.path.dirname(os.path.abspath(__file__))
        agents_dir = os.path.join(base, "agents")

        try:
            imported = {"agents": [], "memories": 0, "files": 0}
            with _tarfile.open(backup_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the prefix (first directory component)
                prefix = ""
                for m in members:
                    parts = m.name.split("/")
                    if len(parts) > 1:
                        prefix = parts[0]
                        break

                for member in members:
                    if member.isdir():
                        continue
                    parts = member.name.split("/")
                    if len(parts) < 3:
                        continue
                    # Skip config.json on restore (security: may have redacted keys)
                    if parts[-1] == "config.json" and len(parts) == 2:
                        continue

                    if parts[1] == "agents" and len(parts) >= 3:
                        agent_name = parts[2]
                        rel_path = "/".join(parts[3:])
                        dest = os.path.join(agents_dir, agent_name, rel_path)

                        if strategy == "merge" and os.path.exists(dest):
                            continue  # Skip existing files in merge mode

                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        f = tar.extractfile(member)
                        if f:
                            with open(dest, "wb") as out:
                                out.write(f.read())
                            imported["files"] += 1
                            if rel_path.endswith(".md"):
                                imported["memories"] += 1
                            if agent_name not in imported["agents"]:
                                imported["agents"].append(agent_name)

                    elif parts[1] == "databases" and len(parts) >= 3:
                        db_name = parts[2]
                        if strategy == "merge":
                            continue  # Don't overwrite databases in merge mode
                        dest = os.path.join(agents_dir, "main", db_name)
                        f = tar.extractfile(member)
                        if f:
                            with open(dest, "wb") as out:
                                out.write(f.read())
                            imported["files"] += 1

            self._send_json({
                "restored": True,
                "strategy": strategy,
                "imported": imported,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    # --- Nodes API handlers ---

    def _handle_nodes_list(self):
        """GET /v1/nodes — list all nodes with status."""
        nodes = []
        with _node_lock:
            for token, info in _node_registry.items():
                cfg = info.get("config", {})
                nodes.append({
                    "name": info["name"],
                    "description": cfg.get("description", ""),
                    "status": info["status"],
                    "paused": cfg.get("paused", False),
                    "hostname": info.get("hostname", ""),
                    "os": info.get("os", ""),
                    "tags": cfg.get("tags", []),
                    "allowed_tools": cfg.get("allowed_tools", []),
                    "last_heartbeat": info.get("last_heartbeat"),
                    "cpu_percent": info.get("cpu_percent"),
                    "mem_used_gb": info.get("mem_used_gb"),
                    "mem_total_gb": info.get("mem_total_gb"),
                    "disk_free_gb": info.get("disk_free_gb"),
                    "uptime_seconds": info.get("uptime_seconds"),
                    "active_commands": info.get("active_commands", 0),
                    "total_commands": info.get("total_commands", 0),
                    "connected_since": info.get("connected_since"),
                })
        self._send_json({"nodes": nodes})

    def _handle_node_poll(self):
        """GET /v1/nodes/poll?token=X — node polls for pending commands."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        token = params.get("token", "")

        with _node_lock:
            info = _node_registry.get(token)
            if not info:
                self._send_json({"error": "Invalid token"}, 401)
                return

            import urllib.parse
            info["status"] = "connected"
            info["last_heartbeat"] = time.time()
            info["hostname"] = urllib.parse.unquote(params.get("hostname", ""))
            info["os"] = urllib.parse.unquote(params.get("os", ""))
            try:
                info["cpu_percent"] = float(params.get("cpu_percent", 0))
                info["mem_used_gb"] = float(params.get("mem_used_gb", 0))
                info["mem_total_gb"] = float(params.get("mem_total_gb", 0))
                info["disk_free_gb"] = float(params.get("disk_free_gb", 0))
                info["uptime_seconds"] = int(params.get("uptime_seconds", 0))
                info["active_commands"] = int(params.get("active_commands", 0))
                info["total_commands"] = int(params.get("total_commands", 0))
            except (ValueError, TypeError):
                pass
            if not info.get("connected_since"):
                info["connected_since"] = time.time()

            if info.get("config", {}).get("paused"):
                self._send_json({"error": "Node is paused"}, 403)
                return

            pending = info.get("pending_commands", [])
            if pending:
                cmd = pending.pop(0)
                self._send_json({"command": cmd})
                return

        # Long-poll: wait up to 30s for a command
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(2)
            with _node_lock:
                info = _node_registry.get(token)
                if not info:
                    break
                pending = info.get("pending_commands", [])
                if pending:
                    cmd = pending.pop(0)
                    self._send_json({"command": cmd})
                    return

        self._send_json({"command": None})

    def _handle_node_result(self):
        """POST /v1/nodes/result — receive command result from node."""
        body = self._read_json()
        token = body.get("token", "")
        command_id = body.get("command_id", "")
        result = body.get("result", {})

        with _node_lock:
            if token not in _node_registry:
                self._send_json({"error": "Invalid token"}, 401)
                return
            entry = _node_commands.get(command_id)
            if entry:
                entry["result"] = result
                entry["result_event"].set()

        self._send_json({"status": "ok"})

    def _handle_nodes_action(self):
        """POST /v1/nodes — add/remove/pause/resume/update a node."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "add":
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "Missing name"}, 400)
                return
            import secrets
            token = f"nd_{secrets.token_hex(16)}"
            cfg = {
                "token": token,
                "description": body.get("description", ""),
                "allowed_tools": body.get("allowed_tools", ["execute_command", "read_file", "write_file", "list_directory"]),
                "tags": body.get("tags", []),
                "max_concurrent": body.get("max_concurrent", 5),
                "command_timeout": body.get("command_timeout", 300),
                "paused": False,
            }
            nodes_cfg = _load_node_config()
            nodes_cfg[name] = cfg
            _save_node_config(nodes_cfg)
            with _node_lock:
                _node_registry[token] = {
                    "name": name, "config": cfg, "status": "disconnected",
                    "last_heartbeat": None, "hostname": "", "os": "",
                    "cpu_percent": None, "mem_used_gb": None, "mem_total_gb": None,
                    "disk_free_gb": None, "uptime_seconds": None,
                    "active_commands": 0, "total_commands": 0,
                    "connected_since": None, "pending_commands": [],
                }
            port = server_config.get("port", 8420)
            install_cmd = f"python3 node.py --server http://SERVER_IP:{port} --token {token} --name {name}"
            self._send_json({"ok": True, "token": token, "install_command": install_cmd})

        elif action == "remove":
            name = body.get("name", "")
            nodes_cfg = _load_node_config()
            removed_token = None
            for n, cfg in nodes_cfg.items():
                if n == name:
                    removed_token = cfg.get("token")
                    break
            if name in nodes_cfg:
                del nodes_cfg[name]
                _save_node_config(nodes_cfg)
            if removed_token:
                with _node_lock:
                    _node_registry.pop(removed_token, None)
            self._send_json({"ok": True})

        elif action in ("pause", "resume"):
            name = body.get("name", "")
            paused = action == "pause"
            nodes_cfg = _load_node_config()
            if name in nodes_cfg:
                nodes_cfg[name]["paused"] = paused
                _save_node_config(nodes_cfg)
                with _node_lock:
                    for token, info in _node_registry.items():
                        if info["name"] == name:
                            info["config"]["paused"] = paused
                            break
            self._send_json({"ok": True, "paused": paused})

        elif action == "update":
            name = body.get("name", "")
            nodes_cfg = _load_node_config()
            if name in nodes_cfg:
                for key in ("description", "allowed_tools", "tags", "max_concurrent", "command_timeout"):
                    if key in body:
                        nodes_cfg[name][key] = body[key]
                _save_node_config(nodes_cfg)
                with _node_lock:
                    for token, info in _node_registry.items():
                        if info["name"] == name:
                            info["config"].update(nodes_cfg[name])
                            break
            self._send_json({"ok": True})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_node_execute(self):
        """POST /v1/nodes/execute — submit command to a node (internal)."""
        body = self._read_json()
        node = body.get("node", "")
        tool = body.get("tool", "")
        params = body.get("params", {})
        if not node or not tool:
            self._send_json({"error": "Missing node or tool"}, 400)
            return
        result = _node_submit_command(node, tool, params)
        self._send_json(result)

    # --- Channels API handlers ---

    def _handle_channels_list(self):
        """GET /v1/channels — list all messaging channels."""
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"channels": []})
            return
        self._send_json({"channels": mgr.status()})

    def _handle_channels_action(self):
        """POST /v1/channels — create/remove/update a channel."""
        body = self._read_json()
        action = body.get("action", "create")
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"error": "Channel manager not initialized"}, 500)
            return

        if action == "create":
            ch_id = body.get("id", body.get("name", ""))
            if not ch_id:
                self._send_json({"error": "Missing channel id"}, 400)
                return
            try:
                channel = mgr.create_channel(ch_id, body)
                if body.get("enabled", True):
                    channel.start()
                self._save_channel_config(mgr)
                self._send_json({"ok": True, "channel": channel.status()})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif action == "remove":
            ch_id = body.get("id", "")
            mgr.remove_channel(ch_id)
            self._save_channel_config(mgr)
            self._send_json({"ok": True})

        elif action == "update":
            ch_id = body.get("id", "")
            ch = mgr.channels.get(ch_id)
            if ch:
                for key in ("name", "agent_routing", "allowed_users", "default_model", "enabled"):
                    if key in body:
                        ch.config[key] = body[key]
                self._save_channel_config(mgr)
                self._send_json({"ok": True, "channel": ch.status()})
            else:
                self._send_json({"error": "Channel not found"}, 404)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_channel_lifecycle(self, path: str, action: str):
        """POST /v1/channels/:id/start|stop|restart."""
        parts = path.split("/")
        ch_id = parts[3] if len(parts) > 3 else ""
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"error": "Channel manager not initialized"}, 500)
            return
        ch = mgr.channels.get(ch_id)
        if not ch:
            self._send_json({"error": "Channel not found"}, 404)
            return
        if action == "stop":
            ch.stop()
        elif action == "start":
            ch.start()
        elif action == "restart":
            ch.stop()
            ch.start()
        self._send_json({"ok": True, "channel": ch.status()})

    def _save_channel_config(self, mgr):
        """Persist channel config to config.json."""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            channels = []
            for ch_id, ch in mgr.channels.items():
                channels.append({"id": ch_id, **ch.config})
            config["channels"] = channels
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Failed to save channel config: {e}", flush=True)

    def _validate_file_path(self, file_path):
        """Validate that a file path is within allowed directories. Returns resolved path or None."""
        if not file_path:
            return None
        file_path = os.path.expanduser(file_path)
        resolved = os.path.realpath(file_path)
        base = os.path.dirname(os.path.abspath(__file__))
        agents_dir = os.path.join(base, "agents")
        cwd = os.getcwd()
        allowed = [base, agents_dir, cwd]
        if any(resolved.startswith(d) for d in allowed):
            return resolved
        return None

    def _handle_file_download(self):
        """GET /v1/files/download?path=<absolute_path> — serve a file for download."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        file_path = qs.get("path", [""])[0]
        resolved = self._validate_file_path(file_path)
        if not resolved:
            self._send_json({"error": "Invalid or disallowed file path"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        ext = resolved.rsplit(".", 1)[-1].lower() if "." in resolved else ""
        content_types = {
            "md": "text/markdown", "txt": "text/plain", "py": "text/x-python",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "html": "text/html", "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "js": "application/javascript", "ts": "text/typescript",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "svg": "image/svg+xml",
        }
        ct = content_types.get(ext, "application/octet-stream")
        filename = os.path.basename(resolved)
        try:
            with open(resolved, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(data))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_file_preview(self):
        """GET /v1/files/preview?path=<absolute_path>&lines=100 — return file content as text."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        file_path = qs.get("path", [""])[0]
        max_lines = int(qs.get("lines", ["100"])[0])
        resolved = self._validate_file_path(file_path)
        if not resolved:
            self._send_json({"error": "Invalid or disallowed file path"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            size = os.path.getsize(resolved)
            max_bytes = 50 * 1024  # 50KB limit
            with open(resolved, "r", errors="replace") as f:
                lines = []
                total_bytes = 0
                for i, line in enumerate(f):
                    if i >= max_lines or total_bytes >= max_bytes:
                        truncated = True
                        break
                    lines.append(line)
                    total_bytes += len(line.encode("utf-8"))
                else:
                    truncated = False
            self._send_json({
                "path": resolved,
                "name": os.path.basename(resolved),
                "size": size,
                "content": "".join(lines),
                "truncated": truncated,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

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


# --- Telegram service helpers ---

def _start_telegram_service() -> bool:
    """Start the in-process Telegram bot using config.json settings."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception:
        return False
    tg = config.get("telegram", {})
    token = tg.get("bot_token", "")
    if not token:
        print("Telegram: no bot_token in config.json", flush=True)
        return False
    allowed = tg.get("allowed_users")
    port = server_config.get("port", 8420)
    server_url = f"http://127.0.0.1:{port}"
    default_model = tg.get("model") or server_config.get("default_model", "")
    return _telegram_mod.telegram_service.start(
        token=token, server_url=server_url,
        allowed_users=allowed,
        default_model=default_model,
    )


def _set_telegram_enabled(enabled: bool):
    """Persist telegram.enabled to config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        config.setdefault("telegram", {})["enabled"] = enabled
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        server_config["telegram_enabled"] = enabled
    except Exception as e:
        print(f"Telegram: failed to save enabled={enabled}: {e}", flush=True)


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
    server_config["port"] = args.port
    server_config["telegram_enabled"] = file_config.get("telegram", {}).get("enabled", True)

    # Initialize models config
    existing_models = file_config.get("models")
    if providers:
        synced = engine.init_models_config(providers, existing_models)
        if not existing_models and synced:
            # First run: persist auto-discovered models to config.json
            try:
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["models"] = synced
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception:
                pass
    elif existing_models:
        engine._models_config = dict(existing_models)

    # Initialize engine globals
    engine._delegate_api_key = args.api_key
    engine._delegate_base_url = args.base_url
    engine._delegate_api_type = args.api_type
    engine._delegate_fallback_model = args.model

    # Start scheduler
    engine._scheduler = engine.Scheduler()
    engine._scheduler.start()

    # Initialize cost tracking and rate limiting
    engine._cost_tracker = engine.CostTracker()
    engine._rate_limiter = engine.RateLimiter()
    print(f"Cost tracking: {engine.COST_DB}")

    # Initialize tracing and audit trail
    engine._trace_manager = engine.TraceManager()
    engine._audit_log = engine.AuditLog()
    print(f"Tracing: {engine.TRACES_DB}")
    print(f"Audit log: {engine.AUDIT_DB}")

    # Initialize notification manager
    global _notification_manager
    notif_config = file_config.get("notifications", {"enabled": True, "channels": {"in_app": {"enabled": True, "min_severity": "info"}}})
    _notification_manager = _notif_mod.NotificationManager(notif_config)
    # Wire notification hook into engine (for scheduler events etc.)
    def _notif_hook(event_type, title, message, severity="info", agent=None, metadata=None):
        if _notification_manager:
            _notification_manager.notify(event_type, title, message, severity=severity,
                                          agent=agent, metadata=metadata)
    engine._notification_hook = _notif_hook
    print(f"Notifications: {'enabled' if notif_config.get('enabled', False) else 'disabled'}")

    # Ensure memory summary schedules for all agents
    try:
        engine.ensure_memory_summary_schedules()
    except Exception as e:
        print(f"[WARN] Memory summary schedule init: {e}")

    # Ensure relationship discovery schedules for all agents
    try:
        engine.ensure_relationship_discovery_schedules()
    except Exception as e:
        print(f"[WARN] Relationship discovery schedule init: {e}")

    # Start task runner
    engine._task_runner = engine.TaskRunner()

    # Start ingest watcher (watched folders auto-ingestion)
    engine._ingest_watcher = engine.IngestWatcher()
    engine._ingest_watcher.start()
    print("Ingest watcher: started (30s poll)")

    # Initialize main agent
    engine._current_agent = engine.AgentConfig("main")
    engine._memory_store = engine.MemoryStore("main", base_dir=engine._current_agent.memory_dir)

    # Unified QMD index keeper: collection registration, file watching, and embedding health
    def _qmd_index_keeper():
        """Single background loop that keeps QMD fully in sync automatically.
        - Waits for QMD to become available (retries on startup)
        - Registers missing collections
        - Detects file changes via mtime polling (fast, every 5s)
        - Every 30s deep-checks index integrity: stale hashes, missing embeddings
        - Runs update+embed as needed — no manual intervention required
        """
        import sqlite3 as _sqlite3
        import hashlib as _hashlib

        agents_dir = engine.AGENTS_DIR
        idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
        last_mtime_snap: dict[str, float] = {}
        FAST_INTERVAL = 5       # seconds between mtime polls
        DEEP_INTERVAL = 30      # seconds between full integrity checks
        last_deep_check = 0.0
        qmd_was_running = False

        def _mtime_snapshot() -> dict[str, float]:
            snap = {}
            try:
                for root, dirs, files in os.walk(agents_dir):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for fname in files:
                        if fname.endswith(".md"):
                            fpath = os.path.join(root, fname)
                            try:
                                snap[fpath] = os.path.getmtime(fpath)
                            except OSError:
                                pass
            except Exception:
                pass
            return snap

        def _backfill_collections():
            """Register any agent dirs and project dirs missing from QMD."""
            existing = {(c["name"] if isinstance(c, dict) else c)
                        for c in BrainAgentHandler._qmd_collections()}
            added = False
            for agent_id in engine.list_agents():
                if agent_id not in existing:
                    agent_dir = os.path.join(agents_dir, agent_id)
                    if os.path.isdir(agent_dir):
                        BrainAgentHandler._qmd_run(["collection", "add", agent_dir, "--name", agent_id])
                        print(f"QMD: registered collection '{agent_id}'")
                        added = True
                # Also register project collections
                projects_dir = os.path.join(agents_dir, agent_id, "projects")
                if os.path.isdir(projects_dir):
                    for proj_name in os.listdir(projects_dir):
                        proj_dir = os.path.join(projects_dir, proj_name)
                        if not os.path.isdir(proj_dir) or proj_name.startswith("."):
                            continue
                        col_name = f"{agent_id}/{proj_name}"
                        if col_name not in existing:
                            BrainAgentHandler._qmd_run(["collection", "add", proj_dir, "--name", col_name])
                            print(f"QMD: registered project collection '{col_name}'")
                            added = True
            return added

        def _deep_check() -> tuple[bool, bool]:
            """Check index for stale content and missing embeddings.
            Returns (needs_update, needs_embed)."""
            needs_update = False
            needs_embed = False
            if not os.path.isfile(idx_path):
                return False, False
            try:
                conn = _sqlite3.connect(idx_path, timeout=2)
                # Pending embeddings
                pending = conn.execute(
                    "SELECT COUNT(*) FROM documents d "
                    "WHERE d.active = 1 AND NOT EXISTS "
                    "(SELECT 1 FROM content_vectors cv WHERE cv.hash = d.hash)"
                ).fetchone()[0]
                if pending > 0:
                    needs_embed = True
                # Build indexed lookup
                indexed = {}
                for row in conn.execute(
                    "SELECT collection, path, hash FROM documents WHERE active = 1"
                ).fetchall():
                    indexed[(row[0].lower(), row[1].lower())] = row[2]
                conn.close()
            except Exception:
                return False, False

            # Compare every .md on disk against the index
            try:
                for agent_id in engine.list_agents():
                    agent_dir = os.path.join(agents_dir, agent_id)
                    if not os.path.isdir(agent_dir):
                        continue
                    for root, dirs, files in os.walk(agent_dir):
                        dirs[:] = [d for d in dirs if not d.startswith('.')]
                        for fname in files:
                            if not fname.endswith(".md"):
                                continue
                            fpath = os.path.join(root, fname)
                            rel = os.path.relpath(fpath, agent_dir)
                            key = (agent_id.lower(), rel.lower())
                            if key not in indexed:
                                needs_update = True
                            else:
                                try:
                                    with open(fpath, "rb") as fh:
                                        if _hashlib.sha256(fh.read()).hexdigest() != indexed[key]:
                                            needs_update = True
                                except OSError:
                                    pass
                            if needs_update:
                                break
                        if needs_update:
                            break
                    if needs_update:
                        break
            except Exception:
                pass
            return needs_update, needs_embed

        # --- Main loop ---
        last_mtime_snap = _mtime_snapshot()
        while True:
            time.sleep(FAST_INTERVAL)
            try:
                running = BrainAgentHandler._is_qmd_running()

                # QMD just came up (or first time): backfill + full sync
                if running and not qmd_was_running:
                    print("QMD: index keeper — QMD detected, syncing...")
                    if _backfill_collections():
                        BrainAgentHandler._qmd_run(["update"], timeout=120)
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    else:
                        # Still do a deep check on first connect
                        nu, ne = _deep_check()
                        if nu:
                            BrainAgentHandler._qmd_run(["update"], timeout=120)
                            BrainAgentHandler._qmd_run(["embed"], timeout=300)
                        elif ne:
                            BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    last_mtime_snap = _mtime_snapshot()
                    last_deep_check = time.time()
                    qmd_was_running = True
                    continue

                qmd_was_running = running
                if not running:
                    continue

                # Fast path: mtime change detection
                current_snap = _mtime_snapshot()
                if current_snap != last_mtime_snap:
                    last_mtime_snap = current_snap
                    BrainAgentHandler._qmd_trigger_update(delay=1.0)
                    last_deep_check = time.time()  # skip deep check right after trigger
                    continue

                # Periodic deep integrity check
                now = time.time()
                if now - last_deep_check >= DEEP_INTERVAL:
                    last_deep_check = now
                    # Also check for new agent collections
                    _backfill_collections()
                    nu, ne = _deep_check()
                    if nu:
                        BrainAgentHandler._qmd_run(["update"], timeout=120)
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    elif ne:
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
            except Exception:
                pass

    threading.Thread(target=_qmd_index_keeper, daemon=True, name="qmd-index-keeper").start()

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
    print("  GET  /v1/agents         — list agents (with team metadata)")
    print("  POST /v1/agents/switch  — switch agent")
    print("  GET  /v1/teams          — team structure")
    print("  POST /v1/teams          — manage teams")
    print("  GET  /v1/models         — list models")
    print("  GET  /v1/schedule       — scheduled tasks")
    print("  POST /v1/schedule       — manage schedules")
    print("  GET  /v1/costs          — cost stats")
    print("  GET  /v1/costs/daily    — daily cost breakdown")
    print("  GET  /v1/tasks          — background tasks")
    print("  GET  /v1/agents/{id}/graph    — knowledge graph of agent memories")
    print("  GET  /v1/agents/{id}/workflows — list workflows")
    print("  POST /v1/agents/{id}/workflows — save workflow")
    print("  POST /v1/agents/{id}/workflows/{name}/run — run workflow")
    print("  GET  /v1/workflows/executions  — list executions")
    # Initialize remote nodes registry
    _init_node_registry()
    nodes_cfg = _load_node_config()
    if nodes_cfg:
        print(f"Remote nodes: {', '.join(nodes_cfg.keys())}")

    # Initialize channel manager (multi-messaging frontends)
    port = server_config.get("port", 8420)
    _adapters_mod.channel_manager = _adapters_mod.ChannelManager(f"http://127.0.0.1:{port}")
    _adapters_mod.channel_manager.load_from_config(file_config)

    # Auto-start Telegram bot if enabled (legacy support)
    if server_config.get("telegram_enabled", True):
        def _start_tg():
            time.sleep(1)
            _start_telegram_service()
        threading.Thread(target=_start_tg, daemon=True, name="telegram-start").start()
    else:
        print("Telegram: disabled in config")

    # Start enabled messaging channels
    def _start_channels():
        time.sleep(2)
        if _adapters_mod.channel_manager:
            _adapters_mod.channel_manager.start_all_enabled()
            n = len(_adapters_mod.channel_manager.channels)
            if n:
                print(f"Messaging channels: {n} loaded")
    threading.Thread(target=_start_channels, daemon=True, name="channels-start").start()

    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        _telegram_mod.telegram_service.stop()
        if _adapters_mod.channel_manager:
            _adapters_mod.channel_manager.stop_all()
        if engine._scheduler:
            engine._scheduler.stop()
        if engine._mcp_manager:
            engine._mcp_manager.stop_all()
        server.server_close()


if __name__ == "__main__":
    main()
