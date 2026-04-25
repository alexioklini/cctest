#!/usr/bin/env python3
"""Brain Agent Server — HTTP API daemon for multi-frontend access."""

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import queue
import shutil
import signal
import socket
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
import auth as _auth_mod

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


_db_pool = threading.local()


def _db_conn(db_path=None):
    """Get a thread-safe SQLite connection (reused per database path).

    Connections are kept in thread-local storage so they're automatically
    released when the thread exits — critical under ThreadingMixIn, where
    every HTTP request spawns (and discards) its own thread.
    """
    path = db_path or CHAT_DB
    conns = getattr(_db_pool, "conns", None)
    if conns is None:
        conns = {}
        _db_pool.conns = conns
    conn = conns.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conns[path] = conn
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


def _purge_mempalace_session(session_id: str):
    """Remove MemPalace drawers and closets for a deleted session (background thread)."""
    def _do_purge():
        try:
            mcfg = engine._load_mempalace_config()
            if not mcfg.get("enabled", True):
                return
            palace_path = mcfg.get("palace_path", "")
            if not palace_path or not os.path.isdir(palace_path):
                return
            ok, err = engine._ensure_mempalace_importable()
            if not ok:
                return
            from mempalace.palace import get_collection, get_closets_collection

            prefix = f"session/{session_id}"

            # Purge drawers
            col = get_collection(palace_path, create=False)
            if col:
                result = col.get(include=["metadatas"])
                ids_to_delete = [
                    did for did, m in zip(result["ids"], result["metadatas"])
                    if (m.get("source_file") or "").startswith(prefix)
                ]
                if ids_to_delete:
                    col.delete(ids=ids_to_delete)
                    print(f"[mempalace-purge] deleted {len(ids_to_delete)} drawer(s) for session {session_id[:8]}")

            # Purge closets referencing those source files
            ccol = get_closets_collection(palace_path, create=False)
            if ccol:
                result = ccol.get(include=["metadatas"])
                cids = [
                    cid for cid, m in zip(result["ids"], result["metadatas"])
                    if (m.get("source_file") or "").startswith(prefix)
                ]
                if cids:
                    ccol.delete(ids=cids)
                    print(f"[mempalace-purge] deleted {len(cids)} closet(s) for session {session_id[:8]}")
        except Exception as e:
            print(f"[mempalace-purge] error for {session_id[:8]}: {type(e).__name__}: {e}")

    threading.Thread(target=_do_purge, daemon=True, name=f"mp-purge-{session_id[:8]}").start()


def _purge_mempalace_turns(session_id: str, turn_ids: list[int]):
    """Remove drawers/closets filed for specific turns of a session (background).
    A turn_id is the DB id of the user message that opens the turn; drawers for
    that turn carry source_file starting with 'session/<sid>#turn/<tid>'.
    """
    if not turn_ids:
        return
    turn_prefixes = [f"session/{session_id}#turn/{int(t)}" for t in turn_ids]

    def _do_purge():
        try:
            mcfg = engine._load_mempalace_config()
            if not mcfg.get("enabled", True):
                return
            palace_path = mcfg.get("palace_path", "")
            if not palace_path or not os.path.isdir(palace_path):
                return
            ok, _ = engine._ensure_mempalace_importable()
            if not ok:
                return
            from mempalace.palace import get_collection, get_closets_collection

            def _matches(sf: str) -> bool:
                for p in turn_prefixes:
                    if sf == p or sf.startswith(p + "#"):
                        return True
                return False

            col = get_collection(palace_path, create=False)
            if col:
                result = col.get(include=["metadatas"])
                ids_to_delete = [
                    did for did, m in zip(result["ids"], result["metadatas"])
                    if _matches((m.get("source_file") or ""))
                ]
                if ids_to_delete:
                    col.delete(ids=ids_to_delete)
                    print(f"[mempalace-purge] deleted {len(ids_to_delete)} drawer(s) "
                          f"for {len(turn_ids)} turn(s) in session {session_id[:8]}")

            ccol = get_closets_collection(palace_path, create=False)
            if ccol:
                result = ccol.get(include=["metadatas"])
                cids = [
                    cid for cid, m in zip(result["ids"], result["metadatas"])
                    if _matches((m.get("source_file") or ""))
                ]
                if cids:
                    ccol.delete(ids=cids)
        except Exception as e:
            print(f"[mempalace-purge-turns] error for {session_id[:8]}: "
                  f"{type(e).__name__}: {e}")

    threading.Thread(target=_do_purge, daemon=True,
                     name=f"mp-purge-turns-{session_id[:8]}").start()


def _resolve_session_wing(info: dict) -> str:
    """Pick the MemPalace wing for a given session row.

    Priority:
      1. Team-scoped session (visibility='team' + team_id) → `{team_id}--{agent_id}`
      2. User-owned session (user_id)                    → `{user_id}--{agent_id}`
      3. Legacy anonymous                                → bare `{agent_id}`
    """
    agent_id = info.get("agent_id", "main")
    visibility = info.get("visibility", "user")
    team_id = info.get("team_id", "")
    if visibility == "team" and team_id:
        return f"{team_id}--{agent_id}"
    user_id = info.get("user_id", "")
    if user_id:
        return f"{user_id}--{agent_id}"
    return agent_id


def _memorize_mempalace_turns(session_id: str, turn_ids: list[int]):
    """Force-file specific turns to MemPalace, ignoring the session's memory_mode
    and classifier. Reuses the chat-sync loop's schema so the result is identical
    to what the background daemon would produce.
    """
    if not turn_ids:
        return 0
    turn_id_set = set(int(t) for t in turn_ids)
    filed = 0
    try:
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            return 0
        palace_path = mcfg.get("palace_path", "")
        if not palace_path:
            return 0
        ok, _ = engine._ensure_mempalace_importable()
        if not ok:
            return 0
        from mempalace.mcp_server import tool_add_drawer

        info = ChatDB.get_session_info(session_id)
        if not info:
            return 0
        agent_id = info.get("agent_id", "main")
        wing = _resolve_session_wing(info)

        sync_cfg = mcfg.get("chat_sync", {}) or {}
        default_room = sync_cfg.get("room", "chat")
        include_roles = set(sync_cfg.get("include_roles", ["user", "assistant"]))
        max_chars = int(sync_cfg.get("max_chars_per_message", 8000))

        msgs = ChatDB.mempalace_load_new_messages(session_id, 0) or []
        current_turn_id = 0
        for msg in msgs:
            mid = int(msg.get("id") or 0)
            role = (msg.get("role") or "").strip()
            if role == "user":
                current_turn_id = mid
            if current_turn_id not in turn_id_set:
                continue
            if role not in include_roles:
                continue
            content = msg.get("content")
            text = content if isinstance(content, str) else str(content)
            body = f"[{role}] {text}"[:max_chars].strip()
            if not body:
                continue
            engine.mempalace_activity.store_begin()
            try:
                res = tool_add_drawer(
                    wing=wing, room=default_room, content=body,
                    source_file=f"session/{session_id}#turn/{current_turn_id}",
                    added_by="brain-chat-manual")
                if isinstance(res, dict) and res.get("success") and \
                        res.get("reason") != "already_exists":
                    filed += 1
            except Exception:
                pass
            finally:
                engine.mempalace_activity.store_end()
    except Exception as e:
        print(f"[mempalace-memorize-turns] error for {session_id[:8]}: "
              f"{type(e).__name__}: {e}")
    return filed


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
            # Add summary column for LLM-generated chat summaries (migration)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Add compacted flag for lossless context management (migration)
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # ── Artifact tables ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'code',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_session ON artifacts(session_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content BLOB,
                    size INTEGER DEFAULT 0,
                    message_idx INTEGER,
                    action TEXT DEFAULT 'created',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artver_artifact ON artifact_versions(artifact_id)")
            # ── Multi-user migrations ──
            # Add user_id to sessions (migration)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Add user_id to artifacts (migration)
            try:
                conn.execute("ALTER TABLE artifacts ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Add role to artifacts (migration): 'output' = report/deliverable,
            # 'intermediate' = helper script / working data. Default 'output'
            # so pre-existing rows stay visible in the default-filtered view.
            try:
                conn.execute("ALTER TABLE artifacts ADD COLUMN role TEXT DEFAULT 'output'")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_user ON artifacts(user_id)")
            # Add save_to_memory flag (migration)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN save_to_memory INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Add caveman_mode (migration): 0=off, 1=lite, 2=full, 3=ultra
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN caveman_mode INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Add team_id + visibility for session team-scoping
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN team_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                # visibility: 'user' (default - private to owner + admin) or 'team' (team members + admin)
                conn.execute("ALTER TABLE sessions ADD COLUMN visibility TEXT DEFAULT 'user'")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_team ON sessions(team_id)")
            # ── MemPalace chat-sync cursor ──
            # Tracks which messages have already been mirrored into MemPalace,
            # per session. `last_message_id` is the highest messages.id filed so far.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_mempalace_sync (
                    session_id TEXT PRIMARY KEY,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    last_summary_hash TEXT DEFAULT '',
                    updated_at REAL DEFAULT (strftime('%s','now'))
                )
            """)
            conn.commit()

    # ── Artifact CRUD ──

    @staticmethod
    @_db_safe(default=None)
    def create_artifact(artifact_id, session_id, agent_id, name, path, artifact_type, role="output"):
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, session_id, agent_id, name, path, type, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, session_id, agent_id, name, path, artifact_type, role))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def add_artifact_version(artifact_id, version, content, size, message_idx, action):
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO artifact_versions (artifact_id, version, content, size, message_idx, action) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, version, content, size, message_idx, action))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def get_artifacts(session_id):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT a.*, COALESCE(v.latest_version, 0) as latest_version
                FROM artifacts a
                LEFT JOIN (SELECT artifact_id, MAX(version) as latest_version FROM artifact_versions GROUP BY artifact_id) v
                ON a.id = v.artifact_id
                WHERE a.session_id = ?
                ORDER BY a.created_at
            """, (session_id,)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                # Fetch version metadata (no content)
                vers = conn.execute(
                    "SELECT version, size, action, created_at FROM artifact_versions WHERE artifact_id = ? ORDER BY version",
                    (d["id"],)).fetchall()
                d["versions"] = [{"version": v[0], "size": v[1], "action": v[2], "created_at": v[3]} for v in vers]
                results.append(d)
            return results

    @staticmethod
    @_db_safe(default=None)
    def get_artifact_by_path(session_id, path):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT a.*, COALESCE(v.latest_version, 0) as latest_version "
                "FROM artifacts a "
                "LEFT JOIN (SELECT artifact_id, MAX(version) as latest_version FROM artifact_versions GROUP BY artifact_id) v "
                "ON a.id = v.artifact_id "
                "WHERE a.session_id = ? AND a.path = ?",
                (session_id, path)).fetchone()
            return dict(row) if row else None

    @staticmethod
    @_db_safe(default=None)
    def get_artifact_content(artifact_id, version=None):
        with _db_conn() as conn:
            if version:
                row = conn.execute(
                    "SELECT content, version, size, action FROM artifact_versions WHERE artifact_id = ? AND version = ?",
                    (artifact_id, int(version))).fetchone()
            else:
                row = conn.execute(
                    "SELECT content, version, size, action FROM artifact_versions WHERE artifact_id = ? ORDER BY version DESC LIMIT 1",
                    (artifact_id,)).fetchone()
            if not row:
                return None
            return {"content": row[0], "version": row[1], "size": row[2], "action": row[3]}

    @staticmethod
    @_db_safe(default=None)
    def get_artifact(artifact_id):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            vers = conn.execute(
                "SELECT version, size, action, created_at FROM artifact_versions WHERE artifact_id = ? ORDER BY version",
                (artifact_id,)).fetchall()
            d["versions"] = [{"version": v[0], "size": v[1], "action": v[2], "created_at": v[3]} for v in vers]
            return d

    @staticmethod
    @_db_safe(default=list)
    def get_all_artifacts(agent_id=None, limit=100):
        """Get all artifacts across sessions, optionally filtered by agent. Ordered by most recent."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            if agent_id:
                rows = conn.execute("""
                    SELECT a.*, s.title as session_title, s.last_active as session_last_active,
                           COALESCE(v.latest_version, 0) as latest_version,
                           v.latest_created_at
                    FROM artifacts a
                    LEFT JOIN sessions s ON a.session_id = s.id
                    LEFT JOIN (SELECT artifact_id, MAX(version) as latest_version, MAX(created_at) as latest_created_at
                               FROM artifact_versions GROUP BY artifact_id) v ON a.id = v.artifact_id
                    WHERE a.agent_id = ?
                    ORDER BY COALESCE(v.latest_created_at, a.created_at) DESC
                    LIMIT ?
                """, (agent_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT a.*, s.title as session_title, s.last_active as session_last_active,
                           COALESCE(v.latest_version, 0) as latest_version,
                           v.latest_created_at
                    FROM artifacts a
                    LEFT JOIN sessions s ON a.session_id = s.id
                    LEFT JOIN (SELECT artifact_id, MAX(version) as latest_version, MAX(created_at) as latest_created_at
                               FROM artifact_versions GROUP BY artifact_id) v ON a.id = v.artifact_id
                    ORDER BY COALESCE(v.latest_created_at, a.created_at) DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def list_artifacts_for_session(session_id):
        """Artifacts tagged to a specific session_id, with latest-version
        metadata. Used by the Run detail modal to turn file paths into
        openable artifact_ids."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT a.*, v.latest_version, v.latest_size, v.latest_created_at
                FROM artifacts a
                LEFT JOIN (
                    SELECT artifact_id,
                           MAX(version) as latest_version,
                           MAX(created_at) as latest_created_at,
                           (SELECT size FROM artifact_versions av2
                            WHERE av2.artifact_id = av.artifact_id
                            ORDER BY version DESC LIMIT 1) as latest_size
                    FROM artifact_versions av GROUP BY artifact_id
                ) v ON a.id = v.artifact_id
                WHERE a.session_id = ?
                ORDER BY COALESCE(v.latest_created_at, a.created_at) DESC
            """, (session_id,)).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=0)
    def delete_artifacts_for_session(session_id):
        """Delete every artifact (rows + version blobs + files on disk) for a
        given session_id. Returns number of artifact rows removed. Used when
        purging a scheduled run — the synthetic session_id is `sched-<run_id>`.
        """
        count = 0
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, path FROM artifacts WHERE session_id = ?",
                (session_id,)).fetchall()
            for aid, fpath in rows:
                conn.execute("DELETE FROM artifact_versions WHERE artifact_id = ?", (aid,))
                conn.execute("DELETE FROM artifacts WHERE id = ?", (aid,))
                count += 1
                if fpath:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
            conn.commit()
        return count

    @staticmethod
    @_db_safe(default=None)
    def get_artifact_preview(artifact_id, max_chars=300):
        """Get a text preview of the latest version content."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT content FROM artifact_versions WHERE artifact_id = ? ORDER BY version DESC LIMIT 1",
                (artifact_id,)).fetchone()
            if not row or not row[0]:
                return None
            content = row[0]
            if isinstance(content, bytes):
                try:
                    content = content.decode("utf-8", errors="replace")
                except Exception:
                    return None
            return content[:max_chars]

    @staticmethod
    @_db_safe(default=None)
    def save_session(sid, agent_id, model, title, status, created_at, last_active, project="", summary="", user_id=""):
        with _db_conn() as conn:
            conn.execute("""
                INSERT INTO sessions (id, agent_id, model, title, status, created_at, last_active, project, summary, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    agent_id=excluded.agent_id, model=excluded.model, title=excluded.title,
                    status=excluded.status, created_at=excluded.created_at, last_active=excluded.last_active,
                    project=excluded.project,
                    summary=CASE WHEN excluded.summary != '' THEN excluded.summary ELSE sessions.summary END,
                    user_id=CASE WHEN excluded.user_id != '' THEN excluded.user_id ELSE sessions.user_id END
            """, (sid, agent_id, model, title, status, created_at, last_active, project or "", summary or "", user_id or ""))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_user(session_id, user_id):
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET user_id = ? WHERE id = ?", (user_id, session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_save_to_memory(session_id, value):
        """Update memory mode: 0=off, 1=on (explicit save all), 2=auto (classifier decides)."""
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET save_to_memory = ? WHERE id = ?",
                        (int(value), session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_caveman_mode(session_id, value):
        """Update caveman mode: 0=off, 1=lite, 2=full, 3=ultra."""
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET caveman_mode = ? WHERE id = ?",
                        (int(value), session_id))
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
    def load_messages(session_id, include_compacted=False):
        with _db_conn() as conn:
            if include_compacted:
                rows = conn.execute(
                    "SELECT id, role, content, metadata, compacted FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, metadata, compacted FROM messages WHERE session_id = ? AND (compacted = 0 OR compacted IS NULL) ORDER BY id",
                    (session_id,)
                ).fetchall()
            messages = []
            for mid, role, content, metadata, compacted in rows:
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
                if compacted:
                    msg["compacted"] = True
                messages.append(msg)
            return messages

    # ── MemPalace chat-sync cursor helpers ──

    @staticmethod
    @_db_safe(default=dict)
    def session_memory_modes():
        """Return {session_id: save_to_memory} for every session. Used by the
        artifact miner to gate chat-folder ingestion on the per-chat toggle."""
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, COALESCE(save_to_memory, 0) FROM sessions"
            ).fetchall()
            return {sid: int(mode or 0) for sid, mode in rows}

    @staticmethod
    @_db_safe(default=str)
    def session_id_for_prefix(prefix: str) -> str:
        """Resolve an 8-char session-id prefix (as used in artifact folder
        names) to its full session_id. Returns '' if no match (or if prefix
        already looks like a full id, returns as-is)."""
        if not prefix:
            return ""
        # Sched ids are stored full-form ('sched-<run>') in folder names.
        if prefix.startswith("sched-") or len(prefix) >= 32:
            return prefix
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? || '%' LIMIT 1",
                (prefix,)
            ).fetchone()
            return row[0] if row else ""

    @staticmethod
    @_db_safe(default=list)
    def mempalace_sessions_needing_sync():
        """Return sessions whose max(messages.id) > last synced id (or have never been synced).

        Returns a list of dicts: {session_id, agent_id, user_id, summary, last_message_id_filed, max_message_id}.
        Uses a left join so sessions with no prior cursor row show up as last_message_id_filed=0.
        """
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT s.id as session_id,
                       s.agent_id as agent_id,
                       COALESCE(s.user_id, '') as user_id,
                       COALESCE(s.team_id, '') as team_id,
                       COALESCE(s.visibility, 'user') as visibility,
                       COALESCE(s.summary, '') as summary,
                       COALESCE(s.save_to_memory, 0) as save_to_memory,
                       COALESCE(c.last_message_id, 0) as last_message_id_filed,
                       COALESCE(c.last_summary_hash, '') as last_summary_hash,
                       (SELECT MAX(id) FROM messages WHERE session_id = s.id) as max_message_id,
                       (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as message_count
                FROM sessions s
                LEFT JOIN chat_mempalace_sync c ON c.session_id = s.id
                WHERE s.status != 'incognito'
            """).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if (d.get("max_message_id") or 0) > (d.get("last_message_id_filed") or 0):
                    out.append(d)
                elif d.get("summary") and not d.get("last_summary_hash"):
                    # Summary was generated after the last sync — still needs one pass.
                    out.append(d)
            return out

    @staticmethod
    @_db_safe(default=list)
    def mempalace_load_new_messages(session_id, after_id):
        """Return messages with id > after_id for a session, including compacted rows
        (we want a lossless mirror, not a live-context view)."""
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content, metadata FROM messages "
                "WHERE session_id = ? AND id > ? ORDER BY id",
                (session_id, after_id)
            ).fetchall()
            out = []
            for mid, role, content, metadata in rows:
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = content
                meta = None
                if metadata:
                    try:
                        meta = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        meta = None
                out.append({"id": mid, "role": role, "content": parsed, "metadata": meta})
            return out

    @staticmethod
    @_db_safe(default=0)
    def mempalace_last_user_id_before(session_id, before_id):
        """Return the id of the most recent user message in this session with id <= before_id.
        Used by the chat-sync loop to attach orphan assistant/tool rows to the turn
        they belong to when the sync cursor advances past a user message boundary."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'user' AND id <= ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id, before_id)
            ).fetchone()
            return int(row[0]) if row else 0

    @staticmethod
    @_db_safe(default=None)
    def mempalace_update_cursor(session_id, last_message_id, last_summary_hash=None):
        with _db_conn() as conn:
            if last_summary_hash is None:
                conn.execute("""
                    INSERT INTO chat_mempalace_sync (session_id, last_message_id, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                    ON CONFLICT(session_id) DO UPDATE SET
                        last_message_id = excluded.last_message_id,
                        updated_at = excluded.updated_at
                """, (session_id, int(last_message_id)))
            else:
                conn.execute("""
                    INSERT INTO chat_mempalace_sync (session_id, last_message_id, last_summary_hash, updated_at)
                    VALUES (?, ?, ?, strftime('%s','now'))
                    ON CONFLICT(session_id) DO UPDATE SET
                        last_message_id = excluded.last_message_id,
                        last_summary_hash = excluded.last_summary_hash,
                        updated_at = excluded.updated_at
                """, (session_id, int(last_message_id), last_summary_hash))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def list_sessions(agent_id=None, status=None, project=None, visible_user_ids=None, visible_team_ids=None):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            q = ("SELECT s.*, "
                 "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id AND (m.compacted = 0 OR m.compacted IS NULL)) as message_count, "
                 "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id AND m.metadata LIKE '%\"files\"%') as has_attachments "
                 "FROM sessions s WHERE 1=1")
            params = []
            # Multi-user: filter by visible user IDs (None = admin sees all).
            # Visibility matrix:
            #  - user_id IN visible → always visible
            #  - visibility='team' AND team_id IN visible_team_ids → visible
            #  - no user_id (legacy) → visible (legacy anonymous sessions)
            if visible_user_ids is not None:
                placeholders = ",".join("?" * len(visible_user_ids)) or "''"
                team_clause = ""
                if visible_team_ids:
                    tplaceholders = ",".join("?" * len(visible_team_ids))
                    team_clause = f" OR (s.visibility = 'team' AND s.team_id IN ({tplaceholders}))"
                q += (f" AND (s.user_id IN ({placeholders})"
                      f" OR s.user_id = '' OR s.user_id IS NULL"
                      f"{team_clause})")
                params.extend(visible_user_ids)
                if visible_team_ids:
                    params.extend(visible_team_ids)
            if agent_id:
                q += " AND s.agent_id = ?"
                params.append(agent_id)
            if project:
                q += " AND s.project = ?"
                params.append(project)
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
            results = []
            # Build index status cache per agent
            _idx_cache = {}
            for r in rows:
                d = dict(r)
                aid = d.get("agent_id", "")
                sid = d["id"]
                msg_count = d.get("message_count", 0)
                # Determine index status
                if msg_count < 4 or d.get("status") == "incognito":
                    d["indexed"] = None  # not eligible
                else:
                    # Check chats-indexed dir (cache per agent)
                    if aid not in _idx_cache:
                        idx_dir = os.path.join(engine.AGENTS_DIR, aid, "chats-indexed")
                        try:
                            _idx_cache[aid] = {f: os.path.getmtime(os.path.join(idx_dir, f))
                                               for f in os.listdir(idx_dir) if f.endswith(".md")}
                        except (OSError, FileNotFoundError):
                            _idx_cache[aid] = {}
                    idx_files = _idx_cache[aid]
                    prefix = f"chat-{sid}-"
                    chunk_mtimes = [mt for fn, mt in idx_files.items() if fn.startswith(prefix)]
                    if not chunk_mtimes:
                        d["indexed"] = False
                    else:
                        last_indexed = max(chunk_mtimes)
                        last_active = d.get("last_active", 0)
                        d["indexed"] = last_indexed >= (last_active - 5)  # 5s tolerance
                results.append(d)
            # Collect actual file lists for sessions that have attachments
            attach_sids = [d["id"] for d in results if d.get("has_attachments")]
            if attach_sids:
                placeholders = ",".join("?" * len(attach_sids))
                meta_rows = conn.execute(
                    f"SELECT session_id, metadata FROM messages WHERE session_id IN ({placeholders}) AND metadata LIKE '%\"files\"%'",
                    attach_sids
                ).fetchall()
                session_files: dict[str, list] = {}
                seen_keys: dict[str, set] = {}
                for sid2, meta_str in meta_rows:
                    try:
                        meta = json.loads(meta_str) if meta_str else {}
                        for f in (meta.get("files") or []):
                            key = f.get("path") or f.get("name") or str(f)
                            if key not in seen_keys.setdefault(sid2, set()):
                                seen_keys[sid2].add(key)
                                session_files.setdefault(sid2, []).append(f)
                    except (json.JSONDecodeError, TypeError):
                        pass
                for d in results:
                    files = session_files.get(d["id"])
                    if files:
                        d["has_attachments"] = len(files)
                        d["files"] = files
            return results

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
            conn.execute("DELETE FROM chat_mempalace_sync WHERE session_id = ?", (session_id,))
            conn.commit()
        _purge_mempalace_session(session_id)

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
    def delete_last_message(session_id, role="user"):
        """Delete the last message of a given role from a session. Returns True if deleted."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = ? ORDER BY id DESC LIMIT 1",
                (session_id, role)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM messages WHERE id = ?", (row[0],))
                conn.commit()
                return True
        return False

    @staticmethod
    @_db_safe(default=None)
    def repair_session(session_id):
        """Repair a session by ensuring alternating user/assistant messages.
        Removes trailing user messages that have no assistant response."""
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, role FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
            if not rows:
                return 0
            removed = 0
            # Remove consecutive same-role messages from the end
            while len(rows) >= 2 and rows[-1][1] == rows[-2][1]:
                conn.execute("DELETE FROM messages WHERE id = ?", (rows[-1][0],))
                rows.pop()
                removed += 1
            # If last message is user (no assistant response), remove it
            if rows and rows[-1][1] == "user":
                conn.execute("DELETE FROM messages WHERE id = ?", (rows[-1][0],))
                removed += 1
            if removed:
                conn.commit()
            return removed

    @staticmethod
    @_db_safe(default=None)
    def archive_all(agent_id=None):
        with _db_conn() as conn:
            if agent_id:
                conn.execute("UPDATE sessions SET status = 'archived' WHERE agent_id = ? AND status = 'active'", (agent_id,))
            else:
                conn.execute("UPDATE sessions SET status = 'archived' WHERE status = 'active'")
            conn.commit()

    @staticmethod
    @_db_safe(default=[])
    def delete_all(agent_id=None, archived_only=False):
        """Delete all sessions (optionally filtered). Returns list of deleted session IDs."""
        with _db_conn() as conn:
            conditions = []
            params = []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if archived_only:
                conditions.append("status = 'archived'")
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            rows = conn.execute(f"SELECT id FROM sessions{where}", params).fetchall()
            sids = [r[0] for r in rows]
            if sids:
                placeholders = ",".join("?" * len(sids))
                conn.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", sids)
                conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", sids)
                conn.execute(f"DELETE FROM chat_mempalace_sync WHERE session_id IN ({placeholders})", sids)
                conn.commit()
            for sid in sids:
                _purge_mempalace_session(sid)
                try:
                    from execution import get_worker_registry
                    get_worker_registry().abort_session(sid, "session_deleted")
                except Exception:
                    pass
            return sids


class Session:
    """A conversation session with an agent."""

    def __init__(self, agent_id: str = "main", model: str | None = None,
                 api_key: str = "", base_url: str = "",
                 max_context: int = 131072, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        self.model = model or ""
        self.api_key = api_key
        self.base_url = base_url
        self.max_context = max_context
        self.messages: list[dict] = []
        self.cancel_token = engine.CancelToken()
        self.created_at = time.time()
        self.last_active = time.time()
        self.title = ""
        self.status = "active"
        self.lock = threading.Lock()

        self.user_id: str = ""  # Owner user id (for MemPalace wing scoping)
        self.project: str | None = None  # Active project name (for scoped chat)
        self.note_context: str | None = None  # Note content for AI-assisted editing
        self.summary: str = ""  # LLM-generated chat summary for sidebar
        self.sdk_session_id: str | None = None  # Agent SDK session ID for resume
        self._last_summary_at = 0  # Token count at last continuous summary
        self.save_to_memory: bool = False  # User toggle: always file to MemPalace
        self.caveman_mode: int = 0  # 0=off, 1=lite, 2=full, 3=ultra

        # Warmup state
        self._warmup_done = threading.Event()
        self._warmup_done.set()  # default: no warmup needed
        self._warmup_active = False
        self._warmup_cancel = threading.Event()
        self._warmup_lock = threading.Lock()

        # Client-hosted inference capability. Declared by the browser/Electron
        # client via POST /v1/sessions/<id>/capabilities. In-memory, session-
        # scoped, never persisted. Shape: {"enabled": bool, "families": [str],
        # "set_at": float}. Empty/missing means "no client-side inference".
        self.client_capabilities: dict = {}

        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)

    def add_message(self, role: str, content, metadata=None):
        msg = {"role": role, "content": content}
        if metadata:
            msg["metadata"] = metadata
        with self.lock:
            self.messages.append(msg)
            self.last_active = time.time()
            # Auto-title from first user message
            if not self.title and role == "user":
                text = content if isinstance(content, str) else str(content)
                title = text[:80].strip()
                if len(title) > 60:
                    # Cut at last word boundary
                    title = title[:60].rsplit(' ', 1)[0]
                self.title = title
        ChatDB.save_message(self.id, role, content, metadata=metadata)
        ChatDB.save_session(self.id, self.agent_id, self.model, self.title,
                           self.status, self.created_at, self.last_active, self.project or "",
                           user_id=self.user_id)

    def switch_agent(self, agent_id: str, model: str | None = None):
        """Switch this session to a different agent (and optionally model)."""
        new_agent = engine.AgentConfig(agent_id)
        new_memory = engine.MemoryStore(agent_id, base_dir=new_agent.memory_dir)
        with self.lock:
            self.agent_id = agent_id
            self.agent = new_agent
            self.memory = new_memory
            if model:
                self.model = model

    def load_from_db(self):
        """Load messages from database (for restoring sessions)."""
        # Auto-repair corrupted sessions (dangling user messages, consecutive same-role)
        repaired = ChatDB.repair_session(self.id)
        if repaired:
            print(f"  Session {self.id[:12]}: repaired {repaired} dangling message(s)", flush=True)

        db_msgs = ChatDB.load_messages(self.id)
        loaded_messages = [{"role": m["role"], "content": m["content"]} for m in db_msgs]
        info = ChatDB.get_session_info(self.id)
        with self.lock:
            self.messages = loaded_messages
            if info:
                self.title = info.get("title", "")
                self.status = info.get("status", "active")
                self.created_at = info.get("created_at", self.created_at)
                self.last_active = info.get("last_active", self.last_active)
                self.project = info.get("project", "") or None
                self.summary = info.get("summary", "") or ""
                self.user_id = info.get("user_id", "") or ""
                self.save_to_memory = bool(info.get("save_to_memory", 0))
                self.caveman_mode = int(info.get("caveman_mode", 0) or 0)


class SessionManager:
    """Thread-safe session storage with SQLite persistence."""

    _LOADING_SENTINEL = object()  # Sentinel to prevent duplicate DB loads

    def __init__(self):
        self._sessions: dict[str, Session | object] = {}
        self._lock = threading.Lock()
        self._load_events: dict[str, threading.Event] = {}  # session_id -> Event for waiters
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
            if s is self._LOADING_SENTINEL:
                # Another thread is loading this session — wait for it
                evt = self._load_events.get(session_id)
            elif s is not None:
                s.last_active = time.time()
                return s
            else:
                # Mark as loading to prevent duplicate construction
                self._sessions[session_id] = self._LOADING_SENTINEL
                evt = threading.Event()
                self._load_events[session_id] = evt
                s = None

        # If we were waiting on another thread's load
        if s is self._LOADING_SENTINEL:
            evt.wait(timeout=30)
            with self._lock:
                result = self._sessions.get(session_id)
                if result is not self._LOADING_SENTINEL and result is not None:
                    result.last_active = time.time()
                    return result
            return None

        # We are the loader — do DB load outside the lock
        info = ChatDB.get_session_info(session_id)
        if info:
            model = info.get("model", "")
            try:
                prov = BrainAgentHandler._resolve_provider_static(model) if model else {}
            except Exception:
                prov = {}
            loaded = Session(
                agent_id=info["agent_id"], model=model,
                api_key=prov.get("api_key", server_config.get("api_key", "")),
                base_url=prov.get("base_url", server_config.get("base_url", "")),
                max_context=engine.get_model_max_context(model) if model else server_config.get("max_context", 131072),
                session_id=session_id,
            )
            loaded.load_from_db()
            with self._lock:
                self._sessions[session_id] = loaded
                self._load_events.pop(session_id, None)
            evt.set()
            return loaded
        else:
            # Not in DB — remove sentinel
            with self._lock:
                if self._sessions.get(session_id) is self._LOADING_SENTINEL:
                    del self._sessions[session_id]
                self._load_events.pop(session_id, None)
            evt.set()
            return None

    def peek(self, session_id: str) -> Session | None:
        """Get a cached session without triggering DB load or updating last_active."""
        with self._lock:
            s = self._sessions.get(session_id)
            if s is self._LOADING_SENTINEL:
                return None
            return s

    def delete(self, session_id: str) -> bool:
        # Abort all running workers in this session
        try:
            from execution import get_worker_registry
            get_worker_registry().abort_session(session_id, "session_deleted")
        except Exception:
            pass
        with self._lock:
            self._sessions.pop(session_id, None)
        ChatDB.delete_session(session_id)
        return True

    def list_all(self) -> list[dict]:
        return ChatDB.list_sessions()

# --- Server globals ---

sessions = SessionManager()
server_config = {
    "api_key": "",
    "base_url": "http://localhost:8317/v1",
    "default_model": "claude-opus-4-5-20251101",
    "max_context": 131072,
}


# --- Request Handler ---

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# Keeper wake-up: set by callers that change the set of models the keeper should
# consider (model config save, warmup toggle) so the loop re-evaluates
# immediately instead of waiting up to interval_seconds.
_warmup_wakeup = threading.Event()


def _wake_warmup_keeper():
    _warmup_wakeup.set()


def _trigger_warmup(session):
    """Prime the KV cache for a session's model.

    Delegates to engine.run_model_warmup so there's a single payload shape
    (matches the keeper daemon's prime exactly) and the global _warmup_state
    registry gets updated — the UI's green/gray dot reads from that registry.
    """
    with session._warmup_lock:
        if session._warmup_active:
            session._warmup_cancel.set()
            session._warmup_done.wait(timeout=5)

        session._warmup_cancel.clear()
        session._warmup_done.clear()
        session._warmup_active = True

    def _do_warmup():
        try:
            if session._warmup_cancel.is_set():
                return
            mcfg = engine.resolve_model_settings(session.model) or {}
            wcfg = server_config.get("warmup", {}) or {}
            allow_cloud = bool(mcfg.get("warmup_allow_cloud",
                                        wcfg.get("allow_cloud", False)))
            # Session-level warmup is always "full": the user is about to
            # chat with this specific model, so priming its KV prefix is
            # exactly right even if it evicts other warmup models' caches.
            result = engine.run_model_warmup(
                session.model,
                allow_cloud=allow_cloud,
                agent_id=session.agent_id,
                timeout=int(wcfg.get("timeout_seconds", 30)),
                mode="full",
            )
            if session._warmup_cancel.is_set():
                print(f"  [warmup] {session.model} cancelled ({session.id[:8]})")
            elif result.get("ok"):
                print(f"  [warmup] {session.model} prefill done ({session.id[:8]}, {result.get('duration_ms')}ms)")
            else:
                print(f"  [warmup] {session.model} {result.get('state')}: {result.get('error','')}")
        finally:
            session._warmup_active = False
            session._warmup_done.set()
            # Kick the keeper so it can refill the pool now that this model is warm
            try:
                _wake_warmup_keeper()
            except Exception:
                pass

    threading.Thread(target=_do_warmup, daemon=True, name=f"warmup_{session.id[:8]}").start()


# --- Warm Session Pool ---
#
# Keeps at most one pre-built Session per warmup-flagged model, bound to the
# "main" agent and primed with a KV prefix. When the user creates a new chat
# on that model (with agent=main, no project), we hand them the pre-baked
# session instead of cold-starting one, then refill the pool in the background.
#
# Pooled sessions live in chats.db with status="warm_pool" so list_sessions
# naturally hides them from the sidebar (it only surfaces active/archived).

class WarmSessionPool:
    """Thread-safe, model-keyed pool of warm sessions bound to the main agent.

    Depth is configurable (warmup.pool_depth, default 3). All slots for a given
    model share the same KV prefix on the GPU (oMLX deduplicates by prompt
    content), so depth > 1 costs only a handful of KB of server RAM and a few
    extra rows in chats.db — zero GPU cost. The depth exists to absorb
    concurrent claims from multiple users hitting "new chat" at the same time.

    Slot state: a slot is 'ready' as soon as its Session wrapper is created
    (no per-slot prefill — the keeper's initial run_model_warmup already
    primed the KV prefix that every slot will reuse).
    """

    POOL_AGENT = "main"
    POOL_STATUS = "warm_pool"
    DEFAULT_DEPTH = 3

    def __init__(self):
        # model_id -> list[dict(session_id, built_at)]
        self._slots: dict[str, list[dict]] = {}
        # model_id -> int (how many build threads are currently in flight)
        self._building: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def target_depth(cls) -> int:
        """Read the configured pool depth, clamped to [1, 10]."""
        try:
            wcfg = server_config.get("warmup", {}) or {}
            d = int(wcfg.get("pool_depth", cls.DEFAULT_DEPTH))
            return max(1, min(10, d))
        except Exception:
            return cls.DEFAULT_DEPTH

    def _prune_dead(self, model: str):
        """Drop slots whose underlying Session no longer exists. Assumes lock held."""
        slots = self._slots.get(model) or []
        alive = [s for s in slots if s.get("session_id") and sessions.peek(s["session_id"])]
        if alive:
            self._slots[model] = alive
        elif model in self._slots:
            self._slots.pop(model, None)

    def ready_count(self, model: str) -> int:
        with self._lock:
            self._prune_dead(model)
            return len(self._slots.get(model, []))

    def building_count(self, model: str) -> int:
        with self._lock:
            return self._building.get(model, 0)

    def get_state(self, model: str) -> str:
        """Return 'empty' | 'building' | 'ready' | 'full'.

        'full' means the pool has reached target_depth; 'ready' means at least
        one slot is available; 'building' means zero ready but at least one
        build is in flight.
        """
        depth = self.target_depth()
        with self._lock:
            self._prune_dead(model)
            ready = len(self._slots.get(model, []))
            building = self._building.get(model, 0)
        if ready >= depth:
            return "full"
        if ready > 0:
            return "ready"
        if building > 0:
            return "building"
        return "empty"

    def all_states(self) -> dict[str, dict]:
        """Snapshot of all pool states for UI."""
        out = {}
        depth = self.target_depth()
        with self._lock:
            models = set(self._slots.keys()) | set(self._building.keys())
            for m in models:
                self._prune_dead(m)
                slots = self._slots.get(m, [])
                ready = len(slots)
                building = self._building.get(m, 0)
                if ready >= depth:
                    state = "full"
                elif ready > 0:
                    state = "ready"
                elif building > 0:
                    state = "building"
                else:
                    state = "empty"
                # Oldest build time = freshest slot the UI could hand out
                built_at = max((s.get("built_at", 0) for s in slots), default=0)
                out[m] = {
                    "state": state,
                    "ready": ready,
                    "building": building,
                    "target": depth,
                    "built_at": built_at,
                }
        return out

    def claim(self, model: str) -> "Session | None":
        """Pop and return one warm session for a model, if available. None otherwise.

        The caller is responsible for rebinding the session (user_id, status,
        project, etc.) and for triggering a pool refill.
        """
        with self._lock:
            self._prune_dead(model)
            slots = self._slots.get(model) or []
            if not slots:
                return None
            slot = slots.pop(0)  # FIFO: hand out oldest (likely warmest) first
            if not slots:
                self._slots.pop(model, None)
        sid = slot.get("session_id", "")
        if not sid:
            return None
        s = sessions.peek(sid)
        if s is None:
            s = sessions.get(sid)
        return s

    def invalidate_all(self, reason: str = ""):
        """Discard every pooled session (system prompt/agent config changed)."""
        with self._lock:
            victims: list[str] = []
            for slots in self._slots.values():
                for slot in slots:
                    sid = slot.get("session_id")
                    if sid:
                        victims.append(sid)
            self._slots.clear()
        for sid in victims:
            try:
                sessions.delete(sid)
            except Exception:
                pass
        if victims and reason:
            print(f"[warm-pool] invalidated {len(victims)} entries ({reason})")

    def invalidate_model(self, model: str, reason: str = ""):
        """Discard every pooled session for one model."""
        with self._lock:
            slots = self._slots.pop(model, None) or []
        if not slots:
            return
        for slot in slots:
            sid = slot.get("session_id")
            if sid:
                try:
                    sessions.delete(sid)
                except Exception:
                    pass
        if reason:
            print(f"[warm-pool] dropped {model} x{len(slots)} ({reason})")

    def try_build(self, model: str):
        """Top up the pool toward target_depth with background build threads.

        Each call fires one build thread per missing slot (cap at target_depth
        minus currently-building count). Safe to call repeatedly — races are
        guarded by the per-model building counter.
        """
        depth = self.target_depth()
        with self._lock:
            self._prune_dead(model)
            ready = len(self._slots.get(model, []))
            building = self._building.get(model, 0)
            needed = depth - ready - building
            if needed <= 0:
                return
            self._building[model] = building + needed

        def _build_one():
            try:
                provider = BrainAgentHandler._resolve_provider_static(model)
                session = sessions.create(
                    agent_id=self.POOL_AGENT,
                    model=model,
                    api_key=provider["api_key"],
                    base_url=provider["base_url"],
                    max_context=engine.get_model_max_context(model),
                )
                session.status = self.POOL_STATUS
                ChatDB.save_session(
                    session.id, session.agent_id, session.model,
                    session.title, session.status,
                    session.created_at, session.last_active,
                    session.project or "",
                )
                with self._lock:
                    self._slots.setdefault(model, []).append({
                        "session_id": session.id,
                        "built_at": time.time(),
                    })
                    ready_now = len(self._slots[model])
                print(f"[warm-pool] {model}: +1 ready ({session.id[:8]}, total {ready_now}/{depth})")
            except Exception as e:
                print(f"[warm-pool] {model}: build failed — {type(e).__name__}: {e}")
            finally:
                with self._lock:
                    remaining = self._building.get(model, 1) - 1
                    if remaining <= 0:
                        self._building.pop(model, None)
                    else:
                        self._building[model] = remaining

        for _ in range(needed):
            threading.Thread(
                target=_build_one, daemon=True,
                name=f"warm-pool-build-{model[:20]}",
            ).start()


warm_pool = WarmSessionPool()


class BrainAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Brain Agent API."""
    # NOTE: Deliberately using HTTP/1.0 — SSE streaming works because we write
    # directly to the raw TCP socket (self.connection.sendall) with TCP_NODELAY.
    # Disable write buffering for real-time SSE streaming
    wbufsize = 0

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

    # --- Auth Middleware ---

    def _get_auth_user(self) -> dict | None:
        """Extract and validate JWT from Authorization: Bearer header."""
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        payload = _auth_mod.verify_token(token)
        if not payload:
            return None
        user = _auth_mod.AuthDB.get_user(payload["user_id"])
        if not user or user.get("disabled"):
            return None
        return user

    def _require_auth(self) -> dict | None:
        """Returns user dict or sends 401. Bypasses if auth disabled."""
        if not _auth_mod.auth_enabled():
            return _auth_mod.SYNTHETIC_ADMIN
        user = self._get_auth_user()
        if not user:
            self._send_json({"error": "Authentication required"}, 401)
            return None
        return user

    def _require_role(self, *roles) -> dict | None:
        """Require auth + specific role. Returns user or None (sends 403)."""
        user = self._require_auth()
        if not user:
            return None
        if user["role"] not in roles:
            self._send_json({"error": "Insufficient permissions"}, 403)
            return None
        return user

    def _require_capability(self, cap: str) -> dict | None:
        """Require auth + a capability flag. Admin always passes.
        Sends 403 on miss and returns None."""
        user = self._require_auth()
        if not user:
            return None
        if not _auth_mod.has_capability(user, cap):
            self._send_json({"error": f"Capability '{cap}' not granted"}, 403)
            return None
        return user

    # Path patterns that require specific capability flags. Checked by
    # _capability_gate() after _auth_gate() admits the request.
    # Each entry: (method, predicate_fn) → capability name.
    @staticmethod
    def _path_requires_capability(method: str, path: str) -> str | None:
        """Return required capability name for this (method, path), or None."""
        # Projects — any read/write under /v1/agents/<id>/projects* requires allow_projects
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sub = parts[1]
                if sub.startswith("projects") or sub == "ingest" or sub.startswith("ingested"):
                    return "allow_projects"
        return None

    # --- Auth Endpoint Handlers ---

    def _handle_auth_register(self):
        body = self._read_json()
        if not _auth_mod.registration_enabled():
            self._send_json({"error": "Registration is disabled"}, 403)
            return
        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            self._send_json({"error": "Username and password required"}, 400)
            return
        result = _auth_mod.AuthDB.create_user(
            username=username, password=password,
            display_name=body.get("display_name", username),
            email=body.get("email", ""),
        )
        if "error" in result:
            self._send_json(result, 409)
        else:
            token = _auth_mod.generate_token(result)
            self._send_json({"user": result, "token": token}, 201)

    def _handle_auth_login(self):
        body = self._read_json()
        uname = body.get("username", "")
        user = _auth_mod.AuthDB.authenticate(uname, body.get("password", ""))
        ip = self.client_address[0] if self.client_address else ""
        if not user:
            _auth_mod.AuthDB.audit_write(None, "auth.login_failed", target=uname, ip=ip)
            self._send_json({"error": "Invalid credentials"}, 401)
            return
        _auth_mod.AuthDB.update_last_login(user["id"])
        _auth_mod.AuthDB.audit_write(user, "auth.login", target=user["id"], ip=ip)
        token = _auth_mod.generate_token(user)
        self._send_json({"user": user, "token": token})

    def _handle_auth_refresh(self):
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json({"error": "Token required"}, 401)
            return
        new_token = _auth_mod.refresh_token(auth_header[7:])
        if not new_token:
            self._send_json({"error": "Invalid or expired token"}, 401)
            return
        self._send_json({"token": new_token})

    def _handle_auth_me(self):
        user = self._require_auth()
        if not user:
            return
        teams = _auth_mod.AuthDB.get_user_teams(user["id"]) if user["id"] != "__system__" else []
        self._send_json({"user": user, "teams": teams})

    def _handle_auth_password(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        result = _auth_mod.AuthDB.change_password(user["id"], body.get("old_password", ""), body.get("new_password", ""))
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_auth_users_list(self):
        user = self._require_role("admin")
        if not user:
            return
        self._send_json({"users": _auth_mod.AuthDB.list_users()})

    def _handle_auth_users_manage(self):
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "create")
        ip = self.client_address[0] if self.client_address else ""
        if action == "create":
            result = _auth_mod.AuthDB.create_user(
                username=body.get("username", ""),
                password=body.get("password", ""),
                role=body.get("role", "user"),
                display_name=body.get("display_name", ""),
                email=body.get("email", ""),
            )
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.create", target=result.get("id",""),
                                             details={"username": result.get("username"), "role": result.get("role")}, ip=ip)
            status = 409 if "error" in result else 201
            self._send_json(result, status)
        elif action == "update":
            uid = body.get("user_id", "")
            updates = body.get("updates", {})
            result = _auth_mod.AuthDB.update_user(uid, updates)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.update", target=uid,
                                             details={"updates": updates}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "delete":
            uid = body.get("user_id", "")
            if uid == user["id"]:
                self._send_json({"error": "Cannot delete your own account"}, 400)
                return
            _auth_mod.AuthDB.delete_user(uid)
            _auth_mod.AuthDB.audit_write(user, "user.delete", target=uid, ip=ip)
            self._send_json({"ok": True})
        elif action == "reset_password":
            uid = body.get("user_id", "")
            new_pw = body.get("new_password", "")
            result = _auth_mod.AuthDB.admin_reset_password(uid, new_pw)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.reset_password", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "disable":
            uid = body.get("user_id", "")
            if uid == user["id"]:
                self._send_json({"error": "Cannot disable your own account"}, 400)
                return
            result = _auth_mod.AuthDB.update_user(uid, {"disabled": 1})
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.disable", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "enable":
            uid = body.get("user_id", "")
            result = _auth_mod.AuthDB.update_user(uid, {"disabled": 0})
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.enable", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_auth_migrate(self):
        """Assign unowned sessions/artifacts to a user."""
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        target_uid = body.get("user_id", "")
        if not target_uid or not _auth_mod.AuthDB.get_user(target_uid):
            self._send_json({"error": "Valid user_id required"}, 400)
            return
        with _db_conn() as conn:
            c1 = conn.execute("UPDATE sessions SET user_id = ? WHERE user_id = '' OR user_id IS NULL", (target_uid,))
            c2 = conn.execute("UPDATE artifacts SET user_id = ? WHERE user_id = '' OR user_id IS NULL", (target_uid,))
            conn.commit()
            self._send_json({"sessions_updated": c1.rowcount, "artifacts_updated": c2.rowcount})

    def _handle_auth_audit_list(self):
        """GET /v1/auth/audit?limit=200&actor=X&action=Y&since=TS — admin-only."""
        user = self._require_role("admin")
        if not user:
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        try:
            limit = int(params.get("limit", "200"))
        except ValueError:
            limit = 200
        try:
            since = float(params.get("since", "0"))
        except ValueError:
            since = 0.0
        rows = _auth_mod.AuthDB.audit_read(
            limit=min(max(limit, 1), 2000),
            actor=unquote(params.get("actor", "")),
            action=unquote(params.get("action", "")),
            since_ts=since,
        )
        self._send_json({"events": rows, "count": len(rows)})

    # --- Agent / Model ACL Handlers ---

    def _handle_auth_permissions_get(self):
        """GET /v1/auth/permissions?user_id=X — list a user's grants (admin only).
        Without ?user_id returns the caller's own effective grants."""
        user = self._require_auth()
        if not user:
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        target_uid = unquote(params.get("user_id", "")) or user["id"]
        team_id = unquote(params.get("team_id", ""))
        if team_id:
            # Only admin or team head
            if user["role"] != "admin" and user["id"] != "__system__" \
                    and not _auth_mod.can_manage_team(user, team_id):
                self._send_json({"error": "Not authorized"}, 403)
                return
            self._send_json({"team_id": team_id, "grants": _auth_mod.AuthDB.list_team_grants(team_id)})
            return
        if target_uid != user["id"] and user["role"] != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Admin access required"}, 403)
            return
        grants = _auth_mod.AuthDB.list_user_grants(target_uid)
        # Also expose the merged effective set for convenience
        effective_agents = sorted(_auth_mod.AuthDB.get_user_allowed_agents(target_uid))
        effective_models = sorted(_auth_mod.AuthDB.get_user_allowed_models(target_uid))
        self._send_json({
            "user_id": target_uid,
            "grants": grants,
            "effective": {"agents": effective_agents, "models": effective_models},
        })

    def _handle_auth_permissions_manage(self):
        """POST /v1/auth/permissions — admin-only grant/revoke.
        Body: {action: grant|revoke, kind: agent|model, user_id?|team_id?, agent_id?|model_id?}"""
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "")
        kind = body.get("kind", "")
        user_id = body.get("user_id", "")
        team_id = body.get("team_id", "")
        aid = body.get("agent_id", "")
        mid = body.get("model_id", "")
        if kind == "agent":
            if action == "grant":
                result = _auth_mod.AuthDB.grant_agent(user_id=user_id, team_id=team_id, agent_id=aid)
            elif action == "revoke":
                result = _auth_mod.AuthDB.revoke_agent(user_id=user_id, team_id=team_id, agent_id=aid)
            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400); return
        elif kind == "model":
            if action == "grant":
                result = _auth_mod.AuthDB.grant_model(user_id=user_id, team_id=team_id, model_id=mid)
            elif action == "revoke":
                result = _auth_mod.AuthDB.revoke_model(user_id=user_id, team_id=team_id, model_id=mid)
            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400); return
        else:
            self._send_json({"error": f"Unknown kind: {kind} (expected 'agent' or 'model')"}, 400); return
        status = 400 if "error" in result else 200
        if "error" not in result:
            ip = self.client_address[0] if self.client_address else ""
            target = user_id or team_id
            _auth_mod.AuthDB.audit_write(user, f"permission.{action}.{kind}", target=target,
                                          details={"subject": {"user_id": user_id, "team_id": team_id},
                                                   "object": aid if kind == "agent" else mid}, ip=ip)
        self._send_json(result, status)

    # --- User Team Endpoint Handlers ---

    def _handle_user_teams_list(self):
        user = self._require_auth()
        if not user:
            return
        if user["role"] == "admin" or user["id"] == "__system__":
            teams = _auth_mod.AuthDB.list_teams()
        else:
            teams = _auth_mod.AuthDB.get_user_teams(user["id"])
        self._send_json({"teams": teams})

    def _handle_user_teams_manage(self):
        user = self._require_role("poweruser", "admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "")
        ip = self.client_address[0] if self.client_address else ""
        if action == "create":
            result = _auth_mod.AuthDB.create_team(
                name=body.get("name", ""),
                head_user_id=body.get("head_user_id", user["id"]),
                description=body.get("description", ""),
            )
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.create", target=result.get("id",""),
                                             details={"name": result.get("name"),
                                                      "head_user_id": result.get("head_user_id")}, ip=ip)
            status = 400 if "error" in result else 201
            self._send_json(result, status)
        elif action == "update":
            tid = body.get("team_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            updates = body.get("updates", {})
            result = _auth_mod.AuthDB.update_team(tid, updates)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.update", target=tid, details={"updates": updates}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "add_member":
            tid = body.get("team_id", "")
            muid = body.get("user_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            result = _auth_mod.AuthDB.add_team_member(tid, muid)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.add_member", target=tid, details={"member": muid}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "remove_member":
            tid = body.get("team_id", "")
            muid = body.get("user_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            result = _auth_mod.AuthDB.remove_team_member(tid, muid)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.remove_member", target=tid, details={"member": muid}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "dissolve":
            tid = body.get("team_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            _auth_mod.AuthDB.delete_team(tid)
            _auth_mod.AuthDB.audit_write(user, "team.dissolve", target=tid, ip=ip)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Routing ---

    # Paths that don't require auth
    _PUBLIC_GET_PATHS = {"/v1/status", "/v1/auth/me"}
    _PUBLIC_POST_PATHS = {"/v1/auth/login", "/v1/auth/refresh"}

    # Admin-only paths. Entries ending in "/" match as prefix (any subpath is
    # admin-only). Exact paths match exactly. These gate config mutations —
    # only admin can edit server/agent configuration. Per-user resources
    # (sessions, messages, projects, notes, artifacts) are gated separately
    # by ownership/ACL helpers, not by this whitelist.
    _ADMIN_GET_PATHS = {
        "/v1/auth/users",
        # Server & agent config reads that can leak secrets or resource counts
        "/v1/providers",
        "/v1/models/config",
        "/v1/mempalace/classifier",
        "/v1/mempalace/stats",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/services",
        "/v1/channels",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        # Prefixes (trailing slash)
        "/v1/services/log",
        "/v1/traces/",
        "/v1/agents/",  # HOOKS / commands detail — read covered below; see GET filter
    }
    # Actual enforced GET admin prefixes — narrower than above; handled by
    # _is_admin_path() below to keep non-config agent reads (projects, files,
    # activity) open to non-admins. Kept tight.
    _ADMIN_GET_PREFIXES = (
        "/v1/traces/",
    )
    _ADMIN_GET_EXACT = {
        "/v1/auth/users",
        "/v1/auth/audit",
        # Note: /v1/auth/permissions is NOT admin-only at the gate —
        # the handler itself allows non-admins to read their own grants
        # but rejects cross-user lookups. See _handle_auth_permissions_get.
        "/v1/providers",
        "/v1/models/config",
        "/v1/mempalace/classifier",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        "/v1/services",
        "/v1/quotas/config",
        "/v1/quotas/admin/users",
    }

    _ADMIN_POST_EXACT = {
        # Auth admin surface (already had these)
        "/v1/auth/users",
        "/v1/auth/migrate",
        "/v1/auth/permissions",
        "/v1/restart",
        # Server-level config
        "/v1/providers",
        "/v1/providers/test",
        "/v1/models/config",
        "/v1/services/server",
        "/v1/services/telegram",
        "/v1/settings/commands",
        "/v1/mempalace/classifier",
        "/v1/warmup/trigger",
        "/v1/backup",
        "/v1/restore",
        "/v1/mcp/connect",
        "/v1/mcp/disconnect",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/cache/clear",
        "/v1/channels",
        # Agent-level config
        "/v1/agents/create",
        "/v1/agents/delete",
        "/v1/agents/rename",
        # Skills install/remove
        "/v1/skills/install",
        "/v1/skills/install-zip",
        "/v1/skills/remove",
        "/v1/skills/claude-code",
        "/v1/skills/claude-code/install",
        # Command/refine tooling that writes to agent config
        "/v1/commands/expand",
        "/v1/refine",
        # Nodes management (remote workers config)
        "/v1/nodes",
        # Client-hosted local model manifest (admin CRUD). Manifest reads and
        # weight downloads stay open to any authenticated user so non-admin
        # desktop clients can still pull models the admin has blessed.
        "/v1/client/models",
    }
    _ADMIN_POST_PATHS = _ADMIN_POST_EXACT  # backwards compat
    _ADMIN_POST_PREFIXES = (
        # Agent config: file writes, hooks, commands, workflows, soul-chat
        # Matches /v1/agents/<name>/{file,hooks,commands,workflows,...}
        # NOTE: project subpaths and /v1/agents/switch are excluded below.
        # We enforce via _is_admin_path() for fine-grained control.
        "/v1/channels/",
    )

    # Agent-level POST subpaths that are admin-only. Checked against the
    # portion after /v1/agents/<name>/.
    _ADMIN_AGENT_POST_SUBPATHS = (
        "file", "files", "hooks", "commands", "workflows", "soul-chat",
    )

    # Admin-only DELETE (agent workflow/ingest mutations). Regular users
    # can still delete their OWN sessions/projects/notes — those are
    # ownership-checked by their handlers.
    _ADMIN_DELETE_AGENT_SUBPATHS = (
        "workflows/", "ingested/",
    )

    def _is_admin_get(self, path: str) -> bool:
        if path in self._ADMIN_GET_EXACT:
            return True
        for p in self._ADMIN_GET_PREFIXES:
            if path.startswith(p):
                return True
        return False

    def _is_admin_post(self, path: str) -> bool:
        if path in self._ADMIN_POST_EXACT:
            return True
        for p in self._ADMIN_POST_PREFIXES:
            if path.startswith(p):
                return True
        # Agent subpath config mutations: /v1/agents/<name>/{hooks,file,...}
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2 and parts[0] not in ("create", "delete", "rename", "switch", "activity"):
                sub = parts[1]
                # Allow project/ingest/notes/docs subpaths (user-facing, ACL-gated elsewhere)
                if sub.startswith("projects") or sub == "ingest" or sub.startswith("ingested"):
                    return False
                for allowed in self._ADMIN_AGENT_POST_SUBPATHS:
                    if sub == allowed or sub.startswith(allowed + "/"):
                        return True
        return False

    def _is_admin_delete(self, path: str) -> bool:
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sub = parts[1]
                for prefix in self._ADMIN_DELETE_AGENT_SUBPATHS:
                    if sub.startswith(prefix):
                        return True
        return False

    def _auth_gate(self, path: str, public_paths: set, admin_paths: set, method: str = "GET") -> dict | None:
        """Check auth for API paths. Returns user dict or None (response already sent).

        `admin_paths` is kept for backwards-compat with exact-match checks;
        prefix and subpath checks are delegated to _is_admin_{get,post,delete}.
        """
        if path in public_paths:
            return _auth_mod.SYNTHETIC_ADMIN if not _auth_mod.auth_enabled() else (self._get_auth_user() or _auth_mod.SYNTHETIC_ADMIN)
        user = self._require_auth()
        if not user:
            return None
        # Method-specific admin check
        is_admin_required = False
        if method == "GET":
            is_admin_required = self._is_admin_get(path)
        elif method == "POST":
            is_admin_required = self._is_admin_post(path)
        elif method == "DELETE":
            is_admin_required = self._is_admin_delete(path)
        # Legacy exact-match fallthrough
        if not is_admin_required and path in admin_paths:
            is_admin_required = True
        if is_admin_required and user["role"] != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Admin access required"}, 403)
            return None
        # Capability gate (projects, etc.) — admin bypasses via has_capability
        needed_cap = self._path_requires_capability(method, path)
        if needed_cap and not _auth_mod.has_capability(user, needed_cap):
            self._send_json({"error": f"Capability '{needed_cap}' not granted"}, 403)
            return None
        return user

    def do_GET(self):
        path = self.path.split("?")[0]

        # Serve static files without auth
        if path == "/" or path.startswith("/web/"):
            # Fall through to existing static file handling below
            pass
        # Auth endpoints
        elif path == "/v1/auth/me":
            self._handle_auth_me()
            return
        elif path == "/v1/auth/users":
            self._handle_auth_users_list()
            return
        elif path == "/v1/auth/permissions":
            self._handle_auth_permissions_get()
            return
        elif path == "/v1/auth/audit":
            self._handle_auth_audit_list()
            return
        elif path == "/v1/user-teams":
            self._handle_user_teams_list()
            return

        # Auth gate for all /v1/* paths
        if path.startswith("/v1/"):
            user = self._auth_gate(path, self._PUBLIC_GET_PATHS, self._ADMIN_GET_PATHS, method="GET")
            if not user:
                return
            self._auth_user = user

        if path == "/v1/tools/list":
            self._handle_tools_list()
        elif path == "/v1/status":
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
        elif path.startswith("/v1/sessions/search"):
            self._handle_session_search()
        elif path.startswith("/v1/sessions/") and path.endswith("/inspect"):
            self._handle_session_inspect(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/files"):
            self._handle_get_session_files(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/messages"):
            self._handle_get_messages(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/next-prompt"):
            self._handle_next_prompt_suggestion(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/capabilities"):
            self._handle_session_capabilities(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup"):
            sid = path.split("/")[3]
            s = sessions.get(sid)
            self._send_json({"warming_up": s._warmup_active if s else False})
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup-status"):
            # Returns warmup availability + current status for a session
            sid = path.split("/")[3]
            s = sessions.get(sid)
            if not s:
                self._send_json({"warmup": False, "warming_up": False})
            else:
                mcfg = engine.resolve_model_settings(s.model)
                warmup_enabled = bool(mcfg.get("warmup", False))
                self._send_json({"warmup": warmup_enabled, "warming_up": s._warmup_active})
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
        elif path.startswith("/v1/services/log"):
            self._handle_service_log()
        elif path.startswith("/v1/agents/") and path.endswith("/commands"):
            self._handle_agent_commands_get(path)
        elif path == "/v1/costs":
            self._handle_costs()
        elif path == "/v1/costs/daily":
            self._handle_costs_daily()
        elif path == "/v1/quotas/me":
            self._handle_quota_me()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_get()
        elif path == "/v1/quotas/admin/users":
            self._handle_quota_admin_users()
        elif path.startswith("/v1/quotas/admin/breakdown"):
            self._handle_quota_admin_breakdown()
        elif path == "/v1/cache/stats":
            self._send_json(engine._web_cache.stats())
        elif path == "/v1/warmup/status":
            self._handle_warmup_status()
        elif path == "/v1/queue/status":
            self._handle_queue_status()
        elif path == "/v1/config/execution-mode":
            self._handle_execution_mode_get()
        # --- Traces & Audit GET routes ---
        elif path == "/v1/traces" or path.startswith("/v1/traces?"):
            self._handle_traces_list()
        elif path.startswith("/v1/traces/"):
            self._handle_trace_detail(path)
        elif path == "/v1/audit" or path.startswith("/v1/audit?"):
            self._handle_audit_list()
        elif path.startswith("/v1/audit/export"):
            self._handle_audit_export()
        # --- Hooks GET routes ---
        elif path.startswith("/v1/agents/") and path.endswith("/hooks"):
            self._handle_hooks_get(path)
        # --- Context Management GET routes ---
        elif path == "/v1/context/config":
            self._handle_context_config_get()
        elif path.startswith("/v1/context/stats"):
            self._handle_context_stats()
        # --- MemPalace GET routes ---
        elif path == "/v1/mempalace/stats":
            self._handle_mempalace_stats()
        elif path == "/v1/mempalace/classifier":
            self._handle_mempalace_classifier_get()
        elif path == "/v1/mempalace/activity":
            self._send_json(engine.mempalace_activity.snapshot())
        elif path.startswith("/v1/mempalace/session-turns"):
            # GET /v1/mempalace/session-turns?session_id=X — list memorized turn_ids
            # for the session so the UI can grey out non-applicable menu items.
            self._handle_mempalace_session_turns()
        elif path.startswith("/v1/mempalace/drawers"):
            self._handle_mempalace_drawers()
        # --- MCP GET routes ---
        elif path == "/v1/mcp/connections":
            self._handle_mcp_list()
        elif path == "/v1/mcp/registry":
            self._handle_mcp_registry()
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
        elif path == "/v1/skills/claude-code":
            self._handle_cc_skills_list()
        elif path == "/v1/notifications":
            self._handle_notifications_list()
        elif path == "/v1/notifications/unread":
            self._handle_notifications_unread()
        elif path == "/v1/backup/info":
            self._handle_backup_info()
        # --- Workers GET routes ---
        elif path == "/v1/workers":
            self._handle_workers_list()
        elif path == "/v1/workers/recent":
            self._handle_workers_recent()
        # --- Nodes GET routes ---
        elif path == "/v1/nodes":
            self._handle_nodes_list()
        elif path.startswith("/v1/nodes/poll"):
            self._handle_node_poll()
        # --- Client-hosted local model manifest ---
        elif path == "/v1/client/models/manifest":
            self._handle_client_models_manifest()
        elif path == "/v1/client/engines":
            self._handle_client_engines_manifest()
        elif path.startswith("/v1/client/models/") and path.endswith("/weights"):
            self._handle_client_model_weights(path)
        # --- Channels GET routes ---
        elif path == "/v1/channels":
            self._handle_channels_list()
        # --- Tools config GET routes ---
        elif path == "/v1/tools/config":
            self._handle_tools_config_get()
        elif path == "/v1/tools/status":
            self._handle_tools_status()
        elif path == "/v1/tools/breakdown":
            self._handle_tools_breakdown()
        elif path == "/v1/files/download":
            self._handle_file_download()
        elif path == "/v1/files/preview":
            self._handle_file_preview()
        elif path == "/v1/files/tree":
            self._handle_file_tree()
        elif path == "/v1/artifacts":
            self._handle_artifacts_list()
        elif path == "/v1/artifacts/browse":
            self._handle_artifacts_browse()
        elif path.startswith("/v1/artifacts/") and path.endswith("/content"):
            self._handle_artifact_content(path)
        elif path.startswith("/v1/artifacts/") and path.endswith("/download"):
            self._handle_artifact_download(path)
        elif path == "/" or path.startswith("/web/") or path.endswith((".html", ".css", ".js", ".ico", ".woff2", ".woff")):
            self._serve_static(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        # Auth endpoints (public)
        if path == "/v1/auth/login":
            self._handle_auth_login()
            return
        elif path == "/v1/auth/register":
            self._handle_auth_register()
            return
        elif path == "/v1/auth/refresh":
            self._handle_auth_refresh()
            return
        elif path == "/v1/auth/password":
            self._handle_auth_password()
            return
        elif path == "/v1/auth/users":
            self._handle_auth_users_manage()
            return
        elif path == "/v1/auth/migrate":
            self._handle_auth_migrate()
            return
        elif path == "/v1/auth/permissions":
            self._handle_auth_permissions_manage()
            return
        elif path == "/v1/user-teams":
            self._handle_user_teams_manage()
            return

        # Auth gate for all /v1/* paths
        if path.startswith("/v1/") or path == "/mcp":
            user = self._auth_gate(path, self._PUBLIC_POST_PATHS, self._ADMIN_POST_PATHS, method="POST")
            if not user:
                return
            self._auth_user = user

        if path == "/mcp":
            self._handle_mcp_jsonrpc()
        elif path == "/v1/sessions":
            self._handle_create_session()
        elif path == "/v1/chat":
            self._handle_chat()
        elif path == "/v1/chat/cancel":
            self._handle_cancel()
        elif path == "/v1/chat/proxy-response":
            self._handle_proxy_response()
        elif path == "/v1/chat/local-inference-usage":
            self._handle_local_inference_usage()
        elif path == "/v1/chat/proxy-tool-result":
            self._handle_proxy_tool_result()
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
        elif path.startswith("/v1/agents/") and path.endswith("/soul-chat"):
            self._handle_soul_chat(path)
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_write(path)
        elif path == "/v1/schedule":
            self._handle_modify_schedule()
        elif path == "/v1/schedule/upload":
            self._handle_schedule_upload()
        elif path == "/v1/providers":
            self._handle_save_providers()
        elif path == "/v1/providers/test":
            self._handle_test_provider()
        elif path == "/v1/models/config":
            self._handle_models_config_save()
        elif path == "/v1/queue/cancel":
            self._handle_queue_cancel()
        elif path == "/v1/warmup/trigger":
            self._handle_warmup_trigger()
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
        elif path == "/v1/skills/claude-code":
            self._handle_cc_skills_manage()
        elif path == "/v1/skills/claude-code/browse":
            self._handle_cc_browse()
        elif path == "/v1/skills/claude-code/install":
            self._handle_cc_install()
        elif path == "/v1/commands/expand":
            self._handle_expand_command()
        elif path == "/v1/settings/commands":
            self._handle_settings_commands()
        elif path == "/v1/restart":
            self._handle_restart()
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
        elif path == "/v1/mempalace/classifier":
            self._handle_mempalace_classifier_save()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_save()
        elif path == "/v1/cache/clear":
            engine._web_cache.clear()
            self._send_json({"status": "cleared"})
        elif path.startswith("/v1/sessions/") and path.endswith("/capabilities"):
            self._handle_session_capabilities(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup"):
            sid = path.split("/")[3]
            s = sessions.get(sid)
            if not s:
                self._send_json({"error": "Session not found"}, 404)
                return
            body = self._read_json()
            # If model specified, update session model + provider
            new_model = body.get("model")
            if new_model and new_model != s.model:
                s.model = new_model
                try:
                    prov = self._resolve_provider(new_model)
                    s.api_key = prov["api_key"]
                    s.base_url = prov["base_url"]
                except Exception:
                    pass
            # Per-model warmup check
            mcfg = engine.resolve_model_settings(s.model)
            warmup_enabled = bool(mcfg.get("warmup", False))
            if warmup_enabled and not s._warmup_active:
                _trigger_warmup(s)
            self._send_json({"warmup": warmup_enabled, "warming_up": s._warmup_active})
        elif path == "/v1/chat/answer":
            self._handle_chat_answer()
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
        elif path.startswith("/v1/agents/") and path.endswith("/ingest"):
            self._handle_agent_ingest(path)
        # --- Nodes POST routes ---
        elif path == "/v1/nodes":
            self._handle_nodes_action()
        elif path == "/v1/nodes/result":
            self._handle_node_result()
        elif path == "/v1/nodes/execute":
            self._handle_node_execute()
        # --- Client-hosted local model manifest (admin-only CRUD) ---
        elif path == "/v1/client/models":
            self._handle_client_models_admin()
        # --- Tools config POST routes ---
        elif path == "/v1/tools/config":
            self._handle_tools_config_save()
        # --- Hooks POST routes ---
        elif path.startswith("/v1/agents/") and path.endswith("/hooks"):
            self._handle_hooks_save(path)
        # --- Context Management POST routes ---
        elif path == "/v1/context/compact":
            self._handle_context_compact()
        elif path == "/v1/context/config":
            self._handle_context_config_save()
        # --- Channels POST routes ---
        elif path == "/v1/channels":
            self._handle_channels_action()
        elif path.startswith("/v1/channels/") and path.endswith("/start"):
            self._handle_channel_lifecycle(path, "start")
        elif path.startswith("/v1/channels/") and path.endswith("/stop"):
            self._handle_channel_lifecycle(path, "stop")
        elif path.startswith("/v1/channels/") and path.endswith("/restart"):
            self._handle_channel_lifecycle(path, "restart")
        elif path.startswith("/v1/workers/") and path.endswith("/answer"):
            self._handle_worker_answer(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        # Auth gate
        if path.startswith("/v1/"):
            user = self._auth_gate(path, set(), set(), method="PUT")
            if not user:
                return
            self._auth_user = user
        if path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "PUT")
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_update(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        # Auth gate
        if path.startswith("/v1/"):
            user = self._auth_gate(path, set(), set(), method="DELETE")
            if not user:
                return
            self._auth_user = user
        if path.startswith("/v1/sessions/"):
            sid = path.split("/")[-1]
            if self._session_access_check(sid, require_manage=True) is None:
                return
            info = ChatDB.get_session_info(sid)
            if sessions.delete(sid):
                self._send_json({"status": "deleted"})
                # Clean up indexed transcripts and trigger memory summary refresh
                if info:
                    agent = info.get("agent_id", "main")
                    try:
                        _cleanup_chat_index(sid, agent)
                    except Exception:
                        pass
                    try:
                        engine.trigger_memory_summary_refresh(agent)
                    except Exception:
                        pass
            else:
                self._send_json({"error": "Session not found"}, 404)
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-ID, Authorization")
        self.end_headers()

    # --- Tool MCP Endpoints (for SDK sidecar) ---

    # Tools the SDK handles natively — don't expose these
    # Tools the SDK handles natively (Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch)
    # These overlap with our custom tools and should NOT be sent via MCP to avoid duplication
    _SDK_NATIVE_TOOLS = {
        "read_file", "write_file", "edit_file", "list_directory", "search_files",
        "execute_command", "web_fetch",
    }

    def _handle_mcp_jsonrpc(self):
        """POST /mcp — MCP Streamable HTTP endpoint (JSON-RPC).

        Speaks the MCP protocol so the SDK can use this as an HTTP MCP server.
        This avoids in-process MCP (which causes SDK to buffer streaming).
        """
        body = self._read_json()
        msg_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        if method == "initialize":
            self._send_json({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "brain_agent", "version": "1.0"},
                },
            })
        elif method == "notifications/initialized":
            # No response needed for notifications
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            tools = []
            for td in engine.TOOL_DEFINITIONS:
                if td["name"] in self._SDK_NATIVE_TOOLS:
                    continue
                if td["name"] not in engine.TOOL_DISPATCH:
                    continue
                desc = td["description"]
                if isinstance(desc, tuple):
                    desc = " ".join(desc)
                tools.append({
                    "name": td["name"],
                    "description": desc[:1000],
                    "inputSchema": td["input_schema"],
                })
            # Sort for prompt cache stability
            tools.sort(key=lambda t: t.get("name", ""))
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            # Extract agent_id/session_id from request headers or use defaults
            agent_id = self.headers.get("X-Agent-Id", "main")
            session_id = self.headers.get("X-Session-Id")

            if not tool_name or tool_name not in engine.TOOL_DISPATCH:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
                return
            try:
                agent_config = engine.AgentConfig(agent_id)
                engine._thread_local.current_agent = agent_config
                engine._thread_local.memory_store = engine.MemoryStore(
                    agent_id, base_dir=agent_config.memory_dir)
                engine._thread_local.mcp_manager = engine._mcp_manager
                if session_id:
                    engine._thread_local.session_id = session_id
                    engine._thread_local.current_session_id = session_id
                    engine._thread_local.attachment_image_model = server_config.get("attachment_image_model", "")

                # Pre-hooks: check if tool should be blocked
                runner = engine._get_hook_runner(agent_id)
                if runner:
                    blocked = runner.run_pre_hooks(tool_name, tool_args)
                    if blocked:
                        self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                          "result": {"content": [{"type": "text", "text": f"Blocked by hook: {blocked}"}],
                                                      "isError": True}})
                        return

                result = engine.TOOL_DISPATCH[tool_name](tool_args)

                # Post-hooks: audit/transform
                if runner:
                    result = runner.run_post_hooks(tool_name, tool_args, str(result)[:51200])

                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "result": {"content": [{"type": "text", "text": str(result)}]}})
            except Exception as e:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                                              "isError": True}})
        elif method == "ping":
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            if msg_id is not None:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": -32601, "message": f"Unknown method: {method}"}})
            else:
                self.send_response(204)
                self.end_headers()

    def _handle_tools_list(self):
        """GET /v1/tools/list — return tool schemas for MCP registration."""
        tools = []
        for td in engine.TOOL_DEFINITIONS:
            if td["name"] in self._SDK_NATIVE_TOOLS:
                continue
            if td["name"] not in engine.TOOL_DISPATCH:
                continue
            desc = td["description"]
            if isinstance(desc, tuple):
                desc = " ".join(desc)
            tools.append({
                "name": td["name"],
                "description": desc[:1000],
                "input_schema": td["input_schema"],
            })
        self._send_json({"tools": tools})

    # --- Handlers ---

    def _handle_status(self):
        self._send_json({
            "status": "running",
            "version": engine.VERSION,
            "agents": engine.list_agents(),
            "sessions": len(sessions.list_all()),
            "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
            "changelog": [{"version": v, "date": d, "changes": c} for v, d, c in engine.CHANGELOG],
            "disabled_commands": server_config.get("disabled_commands", []),
        })

    def _handle_list_agents(self):
        agents = engine.get_agent_summaries()
        team_structure = engine.get_team_structure()
        # Scope to caller's granted agents. Admins see everything.
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            allowed = _auth_mod.AuthDB.get_user_allowed_agents(user["id"])
            if isinstance(agents, dict):
                agents = {aid: info for aid, info in agents.items() if aid in allowed}
            elif isinstance(agents, list):
                agents = [a for a in agents if (a.get("id") or a.get("name") or "") in allowed]
            # Filter team_structure to only teams whose members the user can still see
            if isinstance(team_structure, dict) and "teams" in team_structure:
                filtered_teams = {}
                for tid, team in (team_structure.get("teams") or {}).items():
                    visible_members = [m for m in (team.get("members") or [])
                                       if (m.get("id") or m.get("name")) in allowed]
                    if visible_members:
                        filtered_teams[tid] = {**team, "members": visible_members}
                team_structure = {**team_structure, "teams": filtered_teams,
                                  "standalone": [a for a in (team_structure.get("standalone") or [])
                                                 if (a.get("id") or a.get("name")) in allowed]}
        self._send_json({"agents": agents, "team_structure": team_structure})

    def _handle_list_models(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        show_all = params.get("all", "").lower() in ("true", "1")

        if engine._models_config and not show_all:
            # Return only enabled models from config
            models = engine.get_enabled_models()
        else:
            models = engine.get_available_models(
                server_config["api_key"], server_config["base_url"])
        # Scope to caller's granted models. Admins see everything.
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            allowed = _auth_mod.AuthDB.get_user_allowed_models(user["id"])
            if isinstance(models, dict):
                models = {mid: info for mid, info in models.items() if mid in allowed}
            elif isinstance(models, list):
                # Entries can be strings or dicts with id/name
                def _mid(m):
                    if isinstance(m, str):
                        return m
                    return m.get("id") or m.get("name") or m.get("model") or ""
                models = [m for m in models if _mid(m) in allowed]
        self._send_json({"models": models})

    def _handle_list_sessions(self):
        # Support ?agent=X&status=active|archived&project=Y
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", ""))
        status = unquote(params.get("status", ""))
        project = unquote(params.get("project", ""))
        # Multi-user: scope to visible user IDs + team-visible sessions
        auth_user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        visible = _auth_mod.get_visible_user_ids(auth_user)
        vteam = None
        if visible is not None:
            vteam = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(auth_user["id"])]
        if agent or project:
            if project:
                all_sessions = ChatDB.list_sessions(agent_id=agent or None, status=status or None, project=project,
                                                   visible_user_ids=visible, visible_team_ids=vteam)
                self._send_json({"sessions": all_sessions})
            else:
                all_sessions = ChatDB.list_sessions(agent_id=agent, status=status or None,
                                                   visible_user_ids=visible, visible_team_ids=vteam)
                self._send_json({"sessions": all_sessions})
        else:
            self._send_json({"sessions": ChatDB.list_sessions(visible_user_ids=visible, visible_team_ids=vteam)})

    def _handle_get_messages(self, path):
        """GET /v1/sessions/<id>/messages"""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        msgs = ChatDB.load_messages(sid)
        resp = {"session_id": sid, "messages": msgs}
        session = sessions.get(sid)
        if session:
            resp["max_context"] = session.max_context
            resp["total_tokens"] = engine._estimate_conversation_tokens(session.messages)
            resp["summary"] = session.summary or ""
            resp["title"] = session.title or ""
            resp["caveman_mode"] = session.caveman_mode
            resp["save_to_memory"] = int(getattr(session, "save_to_memory", 0) or 0)
        else:
            info = ChatDB.get_session_info(sid)
            if info:
                resp["summary"] = info.get("summary", "")
                resp["title"] = info.get("title", "")
                resp["caveman_mode"] = int(info.get("caveman_mode", 0) or 0)
                resp["save_to_memory"] = int(info.get("save_to_memory", 0) or 0)
        self._send_json(resp)

    def _handle_next_prompt_suggestion(self, path):
        """GET /v1/sessions/<id>/next-prompt — generate a "predicted next user message"
        suggestion for the composer ghost-text. Synchronous: calls the LLM using the
        session's current messages (or an override model) and returns the text.
        Returns {"suggestion": "..."} or {"suggestion": null} when disabled/empty.
        """
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"suggestion": None, "error": "session_not_found"}, 404)
            return
        try:
            cfg = engine._get_next_prompt_config(session.agent_id)
            if not cfg.get("enabled", True):
                self._send_json({"suggestion": None, "config": cfg})
                return
            # Set thread-local agent context so LLM call picks up the right config
            engine._thread_local.current_agent = engine.AgentConfig(session.agent_id)
            try:
                text = engine.generate_next_prompt_suggestion(session)
            finally:
                engine._thread_local.current_agent = None
            self._send_json({
                "suggestion": text,
                "model_used": (cfg.get("model") or session.model),
                "config": cfg,
            })
        except Exception as e:
            self._send_json({"suggestion": None, "error": str(e)}, 500)

    def _handle_session_capabilities(self, path):
        """GET/POST /v1/sessions/<id>/capabilities — client-declared local
        inference capability handshake.

        Body (POST): {"enabled": bool, "families": [str, ...]}

        Stored in-memory on the Session. No persistence: on reconnect the
        client is expected to re-declare. Access gated by the same ownership
        check as other per-session routes.
        """
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid session path"}, 400)
            return
        sid = parts[3]
        session = self._session_access_check(sid)
        if session is None:
            return  # _session_access_check already sent the error

        if self.command == "GET":
            self._send_json({
                "session_id": sid,
                "capabilities": session.client_capabilities or {},
            })
            return

        # POST — update capabilities
        body = self._read_json() or {}
        enabled = bool(body.get("enabled", False))
        families_raw = body.get("families", []) or []
        if not isinstance(families_raw, list):
            self._send_json({"error": "families must be a list of strings"}, 400)
            return
        families = []
        for f in families_raw:
            if not isinstance(f, str):
                continue
            fstr = f.strip()
            if fstr and fstr not in families:
                families.append(fstr)

        # Cross-check against the server's manifest — silently drop families
        # the server doesn't publish, so the client can't trick the server
        # into routing to a family that has no corresponding server model.
        known_families = {e.get("family") for e in engine._load_client_models() if e.get("family")}
        accepted = [f for f in families if f in known_families]
        rejected = [f for f in families if f not in known_families]

        with session.lock:
            session.client_capabilities = {
                "enabled": enabled and bool(accepted),
                "families": accepted,
                "set_at": time.time(),
            }

        self._send_json({
            "session_id": sid,
            "capabilities": session.client_capabilities,
            "rejected_families": rejected,
        })

    def _handle_session_inspect(self, path):
        """GET /v1/sessions/<id>/inspect — full session debug view."""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        msgs = ChatDB.load_messages(sid, include_compacted=True)

        # Build system prompt for this session's agent
        system_prompt = ""
        system_tokens = 0
        memory_summary = ""
        memory_tokens = 0
        if session:
            try:
                agent_config = engine.AgentConfig(session.agent_id)
                engine._thread_local.current_agent = agent_config
                engine._thread_local.project = getattr(session, 'project', None)
                engine._thread_local.note_context = getattr(session, 'note_context', None)
                system_prompt = engine._build_system_prompt(include_memory_summary=False)
                system_tokens = len(system_prompt) // 4  # rough estimate
                # Memory summary (injected on first turn, separate from system prompt)
                ms = engine.get_memory_summary(session.agent_id)
                if ms:
                    tc = agent_config.config.get("token_config") or {}
                    cap = tc.get("memory_summary_cap", 3000)
                    memory_summary = ms[:cap] if len(ms) > cap else ms
                    memory_tokens = len(memory_summary) // 4
            except Exception:
                pass

        # Build interaction pairs: user message + assistant response
        interactions = []
        i = 0
        while i < len(msgs):
            m = msgs[i]
            if m["role"] == "user":
                user_msg = m
                # Find matching assistant response
                assistant_msg = None
                j = i + 1
                while j < len(msgs):
                    if msgs[j]["role"] == "assistant":
                        assistant_msg = msgs[j]
                        break
                    j += 1
                meta = (assistant_msg or {}).get("metadata", {})
                content_in = user_msg.get("content", "")
                if isinstance(content_in, list):
                    content_in = " ".join(str(b.get("text", "")) for b in content_in if isinstance(b, dict))
                content_out = (assistant_msg or {}).get("content", "")
                if isinstance(content_out, list):
                    content_out = " ".join(str(b.get("text", "")) for b in content_out if isinstance(b, dict))
                # Extract request payloads (what was actually sent to API)
                payloads = meta.get("request_payloads", [])
                interactions.append({
                    "turn": len(interactions) + 1,
                    "user": {"content": content_in, "tokens_est": len(str(content_in)) // 4},
                    "assistant": {
                        "content": content_out,
                        "tokens_est": len(str(content_out)) // 4,
                        "tokens_in": meta.get("tokens_in", 0),
                        "tokens_out": meta.get("tokens_out", 0),
                        "tokens_total": meta.get("tokens", 0),
                        "duration": meta.get("duration", 0),
                        "model": meta.get("model", ""),
                        "execution_mode": meta.get("execution_mode", "server"),
                        "cost": meta.get("cost", 0),
                        "tools": meta.get("tools", []),
                        "thinking": bool(meta.get("thinking")),
                        "thinking_level": meta.get("thinking_level") or ("none" if meta.get("thinking") is None else None),
                        "caveman_chat": int(meta.get("caveman_chat") or 0),
                        "caveman_system": int(meta.get("caveman_system") or 0),
                        "sdk": meta.get("sdk", False),
                        "request_payloads": payloads,
                    } if assistant_msg else None,
                    "compacted": bool(m.get("compacted")),
                })
                i = (j + 1) if assistant_msg else (i + 1)
            else:
                i += 1

        # Totals
        total_in = sum((ix["assistant"] or {}).get("tokens_in", 0) for ix in interactions if ix.get("assistant"))
        total_out = sum((ix["assistant"] or {}).get("tokens_out", 0) for ix in interactions if ix.get("assistant"))
        total_duration = sum((ix["assistant"] or {}).get("duration", 0) for ix in interactions if ix.get("assistant"))
        total_cost = sum((ix["assistant"] or {}).get("cost", 0) for ix in interactions if ix.get("assistant"))

        self._send_json({
            "session_id": sid,
            "agent": session.agent_id if session else "",
            "model": session.model if session else "",
            "max_context": session.max_context if session else 0,
            "system_prompt": {"content": system_prompt, "tokens_est": system_tokens},
            "memory_summary": {"content": memory_summary, "tokens_est": memory_tokens},
            "interactions": interactions,
            "totals": {
                "turns": len(interactions),
                "tokens_in": total_in,
                "tokens_out": total_out,
                "duration": round(total_duration, 2),
                "cost": round(total_cost, 4),
            },
        })

    def _handle_get_session_files(self, path):
        """GET /v1/sessions/<id>/files — returns all files from all messages (including compacted)"""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        msgs = ChatDB.load_messages(sid, include_compacted=True)
        files = []
        seen = set()
        for m in msgs:
            meta = m.get("metadata") or {}
            for f in (meta.get("files") or []):
                key = f.get("path") or f.get("name") or str(f)
                if key not in seen:
                    seen.add(key)
                    files.append(f)
        self._send_json({"session_id": sid, "files": files})

    def _handle_session_search(self):
        """GET /v1/sessions/search?q=<query>&agent=<agent_id>&limit=20 — deep search across chat content."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        query = (qs.get("q") or [""])[0]
        agent_id = (qs.get("agent") or [""])[0]
        limit = int((qs.get("limit") or ["20"])[0])

        if not query:
            self._send_json({"results": [], "query": ""})
            return

        results = []
        seen_sessions = set()

        # 1. QMD semantic search on chat transcript chunks
        if agent_id:
            try:
                ms = engine.MemoryStore(agent_id)
                qmd_results = ms.recall(query, limit=limit * 2, mem_type="chat_transcript")
                for r in qmd_results:
                    sid = ""
                    # Extract session_id from frontmatter (already parsed into result)
                    fm_path = r.get("file_path", "")
                    # Try to read session_id from the file's frontmatter
                    if fm_path and os.path.exists(fm_path):
                        try:
                            with open(fm_path, "r") as f:
                                raw_head = f.read(500)
                            fm, _ = engine._parse_frontmatter(raw_head)
                            sid = fm.get("session_id", "")
                        except Exception:
                            pass
                    if not sid:
                        # Try to extract from filename: chat-{session_id}-{chunk}.md
                        fname = os.path.basename(fm_path or "")
                        if fname.startswith("chat-") and fname.endswith(".md"):
                            parts = fname[5:].rsplit("-", 1)
                            if len(parts) == 2:
                                sid = parts[0]
                    if sid and sid not in seen_sessions:
                        seen_sessions.add(sid)
                        info = ChatDB.get_session_info(sid)
                        if info:
                            info["match_type"] = "content"
                            info["match_preview"] = (r.get("content", ""))[:150]
                            info["score"] = r.get("score", 0)
                            results.append(info)
            except Exception:
                pass

        # 2. SQLite search on title + summary (for sessions not found by QMD)
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                q = ("SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as message_count "
                     "FROM sessions s WHERE (s.title LIKE ? OR s.summary LIKE ?)")
                params = [f"%{query}%", f"%{query}%"]
                if agent_id:
                    q += " AND s.agent_id = ?"
                    params.append(agent_id)
                q += " ORDER BY s.last_active DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    d = dict(r)
                    if d["id"] not in seen_sessions:
                        seen_sessions.add(d["id"])
                        d["match_type"] = "title" if query.lower() in (d.get("title") or "").lower() else "summary"
                        d["score"] = 0
                        results.append(d)
        except Exception:
            pass

        # 3. SQLite search on message content (catches chats not indexed in QMD)
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                q = ("SELECT DISTINCT m.session_id, m.content FROM messages m "
                     "JOIN sessions s ON s.id = m.session_id "
                     "WHERE m.content LIKE ?")
                params = [f"%{query}%"]
                if agent_id:
                    q += " AND s.agent_id = ?"
                    params.append(agent_id)
                q += " ORDER BY m.created_at DESC LIMIT ?"
                params.append(limit * 3)  # over-fetch since multiple messages per session
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    sid = r["session_id"]
                    if sid in seen_sessions:
                        continue
                    seen_sessions.add(sid)
                    info = ChatDB.get_session_info(sid)
                    if info:
                        # Extract a preview snippet around the match
                        content = r["content"] if isinstance(r["content"], str) else ""
                        idx = content.lower().find(query.lower())
                        if idx >= 0:
                            start = max(0, idx - 40)
                            end = min(len(content), idx + len(query) + 80)
                            preview = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                        else:
                            preview = content[:120]
                        info["match_type"] = "content"
                        info["match_preview"] = preview
                        info["score"] = 0
                        results.append(info)
                        if len(results) >= limit:
                            break
        except Exception:
            pass

        # Sort by score (QMD results) then recency
        results.sort(key=lambda x: (x.get("score", 0), x.get("last_active", 0)), reverse=True)
        # Multi-user: filter search results to sessions the caller can see
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            visible_uids = set(_auth_mod.get_visible_user_ids(user) or [])
            my_team_ids = {t["id"] for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
            def _accessible(r):
                owner = r.get("user_id") or ""
                if not owner:
                    return True  # legacy anonymous
                if owner in visible_uids:
                    return True
                if r.get("visibility") == "team" and r.get("team_id") in my_team_ids:
                    return True
                return False
            results = [r for r in results if _accessible(r)]
        self._send_json({"results": results[:limit], "query": query})

    def _handle_manage_session(self):
        """POST /v1/sessions/manage — archive, unarchive, clear, delete_message"""
        body = self._read_json()
        action = body.get("action", "")
        sid = body.get("session_id", "")
        if sid and self._session_access_check(sid, require_manage=True) is None:
            return

        if action == "set_visibility":
            vis = body.get("visibility", "user")
            team_id = body.get("team_id", "")
            if vis not in ("user", "team"):
                self._send_json({"error": "visibility must be 'user' or 'team'"}, 400); return
            if vis == "team" and not team_id:
                self._send_json({"error": "team_id required for team visibility"}, 400); return
            if vis == "team":
                # Caller must be a member of the target team (admin bypass handled above)
                user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
                if user["role"] != "admin" and user["id"] != "__system__":
                    my_teams = {t["id"] for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
                    if team_id not in my_teams:
                        self._send_json({"error": "You are not a member of that team"}, 403); return
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET visibility = ?, team_id = ? WHERE id = ?",
                             (vis, team_id if vis == "team" else "", sid))
                conn.commit()
            self._send_json({"status": "updated", "session_id": sid, "visibility": vis, "team_id": team_id if vis == "team" else ""})
            return

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
        elif action == "delete_messages":
            # Bulk delete: accepts message_ids (list)
            msg_ids = body.get("message_ids", [])
            if not msg_ids:
                self._send_json({"error": "message_ids required"}, 400)
                return
            s = sessions.get(sid)
            id_set = set(msg_ids)
            # Collect artifact IDs from messages being deleted
            artifact_ids_to_delete = set()
            with _db_conn() as conn:
                placeholders = ",".join("?" * len(msg_ids))
                rows = conn.execute(
                    f"SELECT metadata FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                    [sid] + list(msg_ids)).fetchall()
                for (meta_str,) in rows:
                    if not meta_str:
                        continue
                    try:
                        meta = json.loads(meta_str)
                        for f in meta.get("files", []):
                            aid = f.get("artifact_id")
                            if aid:
                                artifact_ids_to_delete.add(aid)
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Delete messages
                conn.execute(f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                             [sid] + list(msg_ids))
                # Delete orphaned artifacts and their versions + files
                for aid in artifact_ids_to_delete:
                    row = conn.execute("SELECT path FROM artifacts WHERE id = ?", (aid,)).fetchone()
                    conn.execute("DELETE FROM artifact_versions WHERE artifact_id = ?", (aid,))
                    conn.execute("DELETE FROM artifacts WHERE id = ?", (aid,))
                    if row and row[0]:
                        try:
                            os.remove(row[0])
                            # Remove parent dir if empty
                            parent = os.path.dirname(row[0])
                            if parent and os.path.isdir(parent) and not os.listdir(parent):
                                os.rmdir(parent)
                        except OSError:
                            pass
                conn.commit()
            if s:
                with s.lock:
                    s.messages = [m for m in s.messages if m.get("id") not in id_set]
            self._send_json({"status": "deleted", "count": len(msg_ids),
                             "artifacts_deleted": len(artifact_ids_to_delete)})
        elif action == "archive_all":
            agent = body.get("agent")
            ChatDB.archive_all(agent)
            self._send_json({"status": "archived_all"})
        elif action == "delete_all":
            agent = body.get("agent")
            archived_only = body.get("archived_only", False)
            sids = ChatDB.delete_all(agent, archived_only)
            for sid in (sids or []):
                sessions.delete(sid)
                if agent:
                    try:
                        _cleanup_chat_index(sid, agent)
                    except Exception:
                        pass
            self._send_json({"status": "deleted_all", "count": len(sids or [])})
        elif action == "delete":
            # Get agent_id before deleting so we can trigger summary refresh
            info = ChatDB.get_session_info(sid)
            sessions.delete(sid)
            self._send_json({"status": "deleted", "session_id": sid})
            # Clean up indexed transcript files and trigger memory summary refresh
            if info:
                agent = info.get("agent_id", "main")
                try:
                    _cleanup_chat_index(sid, agent)
                except Exception:
                    pass
                try:
                    engine.trigger_memory_summary_refresh(agent)
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
        elif action == "rename":
            title = body.get("title", "").strip()
            if not title:
                self._send_json({"error": "title required"}, 400)
                return
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET summary = ? WHERE id = ?", (title, sid))
                conn.commit()
            s = sessions.get(sid)
            if s:
                with s.lock:
                    s.summary = title
            self._send_json({"status": "renamed", "session_id": sid, "title": title})
        elif action == "save_to_memory":
            # 0=off, 1=on, 2=auto
            mode = body.get("mode", None)
            if mode is None:
                mode = 1 if body.get("value", False) else 0
            mode = max(0, min(2, int(mode)))
            ChatDB.update_session_save_to_memory(sid, mode)
            s = sessions.get(sid)
            if s:
                s.save_to_memory = mode
            self._send_json({"status": "ok", "save_to_memory": mode, "session_id": sid})
        elif action == "purge_memory":
            # Remove every MemPalace drawer/closet filed from this session and
            # reset the sync cursor so re-enabling memory re-ingests from scratch.
            _purge_mempalace_session(sid)
            try:
                with _db_conn() as conn:
                    conn.execute("DELETE FROM chat_mempalace_sync WHERE session_id = ?", (sid,))
                    conn.commit()
            except Exception:
                pass
            self._send_json({"status": "ok", "purged": True, "session_id": sid})
        elif action in ("memorize_turns", "purge_turns"):
            # Body: {turn_ids: [mid, ...]} OR {scope, anchor_turn_id} where
            # scope ∈ {"all","this","above","below"}. turn_ids wins if provided.
            turn_ids = body.get("turn_ids")
            scope = (body.get("scope") or "").strip().lower()
            anchor = int(body.get("anchor_turn_id") or 0)
            resolved: list[int] = []
            if isinstance(turn_ids, list) and turn_ids:
                resolved = [int(t) for t in turn_ids if str(t).isdigit() or isinstance(t, int)]
            elif scope:
                try:
                    with _db_conn() as conn:
                        rows = conn.execute(
                            "SELECT id FROM messages WHERE session_id = ? AND role = 'user' "
                            "ORDER BY id", (sid,)
                        ).fetchall()
                    all_turns = [int(r[0]) for r in rows]
                except Exception:
                    all_turns = []
                if scope == "all":
                    resolved = all_turns
                elif scope == "this":
                    resolved = [anchor] if anchor else []
                elif scope == "above":
                    resolved = [t for t in all_turns if t < anchor]
                elif scope == "below":
                    resolved = [t for t in all_turns if t > anchor]
            if not resolved:
                self._send_json({"status": "ok", "count": 0, "session_id": sid})
                return
            if action == "purge_turns":
                _purge_mempalace_turns(sid, resolved)
                self._send_json({"status": "ok", "purged": len(resolved),
                                 "turn_ids": resolved, "session_id": sid})
            else:
                # memorize — run in background since add_drawer can take a moment
                def _do_mem():
                    try:
                        _memorize_mempalace_turns(sid, resolved)
                    except Exception as e:
                        print(f"[mempalace-memorize-turns] bg error: {e}")
                threading.Thread(target=_do_mem, daemon=True,
                                 name=f"mp-mem-turns-{sid[:8]}").start()
                self._send_json({"status": "ok", "memorizing": len(resolved),
                                 "turn_ids": resolved, "session_id": sid})
        elif action == "caveman_mode":
            mode = max(0, min(3, int(body.get("mode", 0))))
            ChatDB.update_session_caveman_mode(sid, mode)
            s = sessions.get(sid)
            if s:
                s.caveman_mode = mode
            # Invalidate system prompt cache for this session
            for k in list(engine._system_prompt_cache):
                if k.startswith(sid):
                    del engine._system_prompt_cache[k]
            self._send_json({"status": "ok", "caveman_mode": mode, "session_id": sid})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # Cache: model -> provider config (refreshed when providers change)
    # Provider cache now in engine (resolve_provider_for_model)

    @staticmethod
    def _resolve_provider_static(model: str) -> dict:
        """Find the provider that has the given model. Returns {api_key, base_url, provider_name}.
        Thread-safe. Delegates to engine.resolve_provider_for_model()."""
        if engine._models_config:
            model = engine.resolve_model(model)
        return engine.resolve_provider_for_model(model)

    def _resolve_provider(self, model: str) -> dict:
        """Instance method wrapper for _resolve_provider_static."""
        return BrainAgentHandler._resolve_provider_static(model)

    def _handle_create_session(self):
        body = self._read_json()
        model = body.get("model", server_config["default_model"])
        agent_req = body.get("agent", "main")
        # ACL gate: caller must have access to both the agent and the model
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_req):
            self._send_json({"error": f"Access to agent '{agent_req}' not permitted"}, 403)
            return
        if not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        provider = self._resolve_provider(model)
        project_req = body.get("project", "")
        custom_status_req = body.get("status", "")
        note_req = body.get("note_context", "")

        # Warm session pool claim — only when the incoming request matches
        # the pooled shape exactly: agent=main, no project, no custom status,
        # no note context. Any of those change the system prompt / behavior
        # and would make a pre-primed KV prefix invalid.
        model_cfg_claim = engine._models_config.get(model, {})
        pooled = None
        if (model_cfg_claim.get("warmup")
                and agent_req == WarmSessionPool.POOL_AGENT
                and not project_req and not custom_status_req and not note_req):
            pooled = warm_pool.claim(model)
        if pooled is not None:
            session = pooled
            # Promote from warm_pool status to active (visible in sidebar)
            session.status = "active"
            ChatDB.save_session(
                session.id, session.agent_id, session.model,
                session.title, session.status,
                session.created_at, session.last_active,
                session.project or "",
            )
            # Immediately kick off a replacement build
            threading.Thread(
                target=lambda m=model: warm_pool.try_build(m),
                daemon=True, name=f"warm-pool-refill-{model[:16]}",
            ).start()
            print(f"[warm-pool] claimed {model} ({session.id[:8]})")
        else:
            session = sessions.create(
                agent_id=agent_req,
                model=model,
                api_key=provider["api_key"],
                base_url=provider["base_url"],
                max_context=body.get("max_context") or engine.get_model_max_context(model),
            )
        # Stamp user ownership (for MemPalace wing scoping)
        auth_user = getattr(self, '_auth_user', None)
        uid = ""
        if auth_user and auth_user.get("id"):
            if auth_user["id"] != "__system__":
                uid = auth_user["id"]
            else:
                # Auth disabled — resolve to the first real user (typically the sole admin)
                try:
                    users = _auth_mod.AuthDB.list_users()
                    if users:
                        uid = users[0]["id"]
                except Exception:
                    pass
        if uid:
            session.user_id = uid
            ChatDB.update_session_user(session.id, uid)
        # Set default memory mode from classifier config
        mcfg = engine._load_mempalace_config()
        clf_cfg = (mcfg.get("chat_sync", {}) or {}).get("classifier", {}) or {}
        default_mem = int(clf_cfg.get("default_mode", 0))
        if default_mem:
            session.save_to_memory = default_mem
            ChatDB.update_session_save_to_memory(session.id, default_mem)
        project = body.get("project", "")
        if project:
            session.project = project
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, project)
        note_context = body.get("note_context", "")
        if note_context:
            session.note_context = note_context
        # Allow setting custom status (e.g., 'note_chat' to hide from chat lists)
        custom_status = body.get("status", "")
        if custom_status:
            session.status = custom_status
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")
        # Per-model warmup flag
        mcfg = engine.resolve_model_settings(model)
        warmup_enabled = bool(mcfg.get("warmup", False))

        # Claimed pool sessions are already warm — skip the "warmup" status
        # marker (that's for fresh sessions still prefilling) and skip the
        # redundant _trigger_warmup call.
        claimed = pooled is not None

        # Mark warmup sessions so they don't appear in sidebar until first message
        if warmup_enabled and not custom_status and not claimed:
            session.status = "warmup"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "max_context": session.max_context,
            "project": session.project or "",
            "warmup": warmup_enabled,
            "pre_warmed": claimed,
        })

        # Trigger warmup in background (skip if session was claimed from pool)
        if warmup_enabled and not claimed:
            _trigger_warmup(session)

    def _handle_switch_agent(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        agent_id = body.get("agent", "main")
        model = body.get("model")
        # ACL gate for agent + (optional) model change
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_id):
            self._send_json({"error": f"Access to agent '{agent_id}' not permitted"}, 403)
            return
        if model and not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        session.switch_agent(agent_id, model)
        warmup_enabled = False
        if model:
            provider = self._resolve_provider(model)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            mcfg = engine.resolve_model_settings(model)
            warmup_enabled = bool(mcfg.get("warmup", False))
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "warmup": warmup_enabled,
        })
        if warmup_enabled:
            _trigger_warmup(session)

    def _handle_cancel(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        session.cancel_token.cancel()
        self._send_json({"status": "cancelled"})

    def _handle_execution_mode_get(self):
        """GET /v1/config/execution-mode — return execution mode and provider credentials for client proxy."""
        mode = server_config.get("execution_mode", "server")
        result = {"execution_mode": mode}
        if mode == "client":
            providers = server_config.get("providers", {})
            result["providers"] = {}
            for name, prov in providers.items():
                result["providers"][name] = {
                    "api_key": prov.get("api_key", ""),
                    "base_url": prov.get("base_url", ""),
                }
            tcfg = engine.get_tool_config()
            exa_cfg = tcfg.get("exa_search", {})
            result["exa_api_key"] = (exa_cfg.get("api_key", "")
                                     or os.environ.get("EXA_API_KEY", "")
                                     or "97dbd594-f7b4-4866-9a8e-6a297e3df576")
        self._send_json(result)

    def _handle_proxy_response(self):
        """POST /v1/chat/proxy-response — browser sends proxied LLM response chunks."""
        body = self._read_json()
        sid = body.get("session_id", "")
        msg_type = body.get("type", "")
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        channel = engine.get_proxy_channel(sid)
        if msg_type == "chunk":
            data = body.get("data", "")
            channel.feed_llm_line(data)
        elif msg_type == "chunks":
            for line in body.get("lines", []):
                channel.feed_llm_line(line)
        elif msg_type == "done":
            channel.feed_llm_done()
        elif msg_type == "error":
            channel.feed_llm_error(body.get("message", "Unknown error"))
        else:
            self._send_json({"error": f"Unknown type: {msg_type}"}, 400)
            return
        self._send_json({"ok": True})

    def _handle_local_inference_usage(self):
        """POST /v1/chat/local-inference-usage — client reports token usage
        after a client-hosted inference turn completes.

        Body: {session_id, model, family, tokens_in, tokens_out, duration_ms}

        Logged to costs.db with provider="client:<session_id>" and cost=0 (by
        virtue of _compute_cost returning 0 for unknown provider/model combos,
        and client inference genuinely being free from the server's POV —
        electricity on the user's laptop is not our problem). Keeping the
        token counts honest lets dashboards distinguish client- vs server-
        executed turns without a schema change."""
        body = self._read_json() or {}
        sid = body.get("session_id", "")
        model = body.get("model", "")
        tokens_in = int(body.get("tokens_in", 0) or 0)
        tokens_out = int(body.get("tokens_out", 0) or 0)
        if not sid or not model:
            self._send_json({"error": "session_id and model required"}, 400)
            return
        # Authorization: only the session owner (or admin) may report usage.
        s = self._session_access_check(sid)
        if s is None:
            return
        agent_id = s.agent_id or ""
        try:
            if engine._cost_tracker:
                engine._cost_tracker.log_call(
                    agent=agent_id, session_id=sid, model=model,
                    provider=f"client:{sid[:8]}",
                    tokens_in=tokens_in, tokens_out=tokens_out,
                )
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({"ok": True})

    def _handle_proxy_tool_result(self):
        """POST /v1/chat/proxy-tool-result — browser sends proxied web tool results."""
        body = self._read_json()
        sid = body.get("session_id", "")
        tool_call_id = body.get("tool_call_id", "")
        result = body.get("result", "")
        if not sid or not tool_call_id:
            self._send_json({"error": "session_id and tool_call_id required"}, 400)
            return
        channel = engine.get_proxy_channel(sid)
        channel.feed_tool_result(tool_call_id, result)
        self._send_json({"ok": True})

    def _handle_chat(self):
        """Handle chat request with SSE streaming."""
        body = self._read_json()
        sid = body.get("session_id", "")
        message = body.get("message", "")
        model_override = body.get("model")
        chat_mode = body.get("mode", "")
        project_name = body.get("project")  # Optional project scope
        thinking_level = body.get("thinking")  # none, low, medium, high
        # ACL: only owner/team-member/admin can post to the session
        if sid and self._session_access_check(sid) is None:
            return
        # ACL: model override must be permitted
        if model_override:
            user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
            if not _auth_mod.can_access_model(user, model_override):
                self._send_json({"error": f"Access to model '{model_override}' not permitted"}, 403)
                return
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
            provider = self._resolve_provider(model_override)
            with session.lock:
                session.model = model_override
                session.api_key = provider["api_key"]
                session.base_url = provider["base_url"]

        # Auto model selection: if agent uses model="auto", re-resolve per message
        agent_cfg = session.agent.config
        if not model_override and agent_cfg.get("model") == "auto":
            auto_model, auto_purpose = engine.resolve_auto_model_for_task(agent_cfg, message)
            if auto_model and auto_model != session.model:
                provider = self._resolve_provider(auto_model)
                with session.lock:
                    session.model = auto_model
                    session.api_key = provider["api_key"]
                    session.base_url = provider["base_url"]
                    session.max_context = engine.get_model_max_context(auto_model)

        # Reset cancel token
        with session.lock:
            session.cancel_token = engine.CancelToken()
            session._streaming = True

        # --- Unified attachment routing: multimodal vs disk based on model capabilities ---
        import base64 as _b64
        import mimetypes as _mt

        def _guess_mime(filename: str) -> str:
            mt, _ = _mt.guess_type(filename)
            return mt or "application/octet-stream"

        # Collect all attachments from both legacy body.images and body.files
        all_attachments = []
        for img in body.get("images", []):
            all_attachments.append({
                "name": "image",
                "content": img.get("data", ""),
                "encoding": "base64",
                "media_type": img.get("media_type", "image/png"),
            })
        for f in body.get("files", []):
            all_attachments.append({
                "name": f.get("name", "file"),
                "content": f.get("content", "") or f.get("data", ""),
                "encoding": f.get("encoding", "base64"),
                "media_type": f.get("media_type") or f.get("type") or _guess_mime(f.get("name", "file")),
            })

        content_blocks = []
        disk_files = []
        MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20MB

        if all_attachments:
            raw_formats = engine.get_model_raw_formats(session.model)
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)

            for f in all_attachments:
                mime = f["media_type"]
                is_base64 = f["encoding"] == "base64"
                # Check file size (base64 is ~4/3 of raw)
                too_large = is_base64 and len(f["content"]) * 3 // 4 > MAX_INLINE_BYTES
                # OpenAI wire format only supports image/* as multimodal content blocks
                api_blocked = not mime.startswith("image/")

                if (engine._mime_matches(mime, raw_formats)
                        and is_base64 and not too_large and not api_blocked):
                    # Route as multimodal content block — LLM sees raw data as image_url data URI
                    data_uri = f"data:{mime};base64,{f['content']}"
                    content_blocks.append({"type": "image_url", "image_url": {"url": data_uri}})
                else:
                    # Route to disk — agent uses read_document/read_file
                    disk_files.append(f)

        # Build user_content with any multimodal blocks
        if content_blocks:
            content_blocks.append({"type": "text", "text": message})
            user_content = content_blocks
        else:
            user_content = message

        # Save disk-routed files and append notice
        if disk_files:
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)
            os.makedirs(attach_dir, exist_ok=True)
            saved_paths = []
            for f in disk_files:
                fname = f.get("name", "file")
                safe_name = fname.replace("/", "_").replace("\\", "_")
                fpath = os.path.join(attach_dir, safe_name)
                content = f.get("content", "")
                if f.get("encoding") == "base64":
                    with open(fpath, "wb") as fp:
                        fp.write(_b64.b64decode(content))
                else:
                    with open(fpath, "w", errors="replace") as fp:
                        fp.write(content)
                saved_paths.append(fpath)
            paths_list = "\n".join(f"  - {p}" for p in saved_paths)
            has_docs = any(os.path.splitext(p)[1].lower() in (".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv")
                           for p in saved_paths)
            if has_docs:
                notice = (f"\n\n[User attached files saved to disk. "
                          f"IMPORTANT: Use the read_document tool (NOT read_file) to read these — "
                          f"read_document handles PDF, DOCX, XLSX, PPTX and other document formats:]\n{paths_list}")
            else:
                notice = f"\n\n[User attached files saved to disk:]\n{paths_list}"
            message = message + notice
            if isinstance(user_content, str):
                user_content = user_content + notice
            else:
                for block in user_content:
                    if block.get("type") == "text":
                        block["text"] = block["text"] + notice
                        break

        # Promote warmup session to active on first message
        if session.status == "warmup":
            session.status = "active"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        # Add user message (persisted to DB)
        session.add_message("user", user_content)

        # SSE streaming setup (start early so we can send compaction events)
        # Disable Nagle's algorithm for real-time SSE delivery
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()  # Ensure headers are pushed before streaming

        # Wait for warmup if in progress (after SSE headers so client stays connected)
        if session._warmup_active:
            try:
                self.wfile.write(b"event: warmup\ndata: {\"status\":\"waiting\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            completed = session._warmup_done.wait(timeout=30)
            try:
                if completed and not session._warmup_cancel.is_set():
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                else:
                    # Warmup cancelled or timed out — proceed anyway but log it
                    reason = "cancelled" if session._warmup_cancel.is_set() else "timed out"
                    print(f"  [warmup] {session.model} {reason}, proceeding without cache ({session.id[:8]})")
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # Pre-processing: tool result budget + microcompact
        engine._thread_local.current_session_id = session.id
        if len(session.messages) > 4:
            engine._apply_tool_result_budget(session.messages, session_id=session.id,
                                              agent_id=session.agent_id)
            session.messages, _mc_freed = engine._microcompact(session.messages, keep_recent=5)

        # Check context and compact (with SSE progress)
        estimated = engine._estimate_conversation_tokens(session.messages)
        ctx_cfg = engine._context_manager.get_config() if engine._context_manager else {}
        threshold_pct = ctx_cfg.get("compact_threshold", 0.75) if ctx_cfg.get("enabled") else engine.COMPACT_THRESHOLD
        pre_compact_pct = 0
        if estimated >= int(session.max_context * threshold_pct):
            pre_compact_pct = int(estimated / session.max_context * 100)
            sse_line = f"event: compacting\ndata: {json.dumps({'pct': pre_compact_pct, 'tokens': estimated, 'max_tokens': session.max_context})}\n\n"
            try:
                self.wfile.write(sse_line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        session.messages, was_compacted = engine._check_and_compact(
            session.messages, session.model, session.api_key,
            session.base_url,
            max_tokens=session.max_context,
            session_id=session.id,
        )
        if was_compacted:
            new_est = engine._estimate_conversation_tokens(session.messages)
            new_pct = int(new_est / session.max_context * 100)
            sse_line = f"event: compacted\ndata: {json.dumps({'pct': new_pct, 'tokens': new_est, 'old_pct': pre_compact_pct})}\n\n"
            try:
                self.wfile.write(sse_line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        event_queue = queue.Queue()
        created_files = []
        _partial_reply = []  # accumulate text deltas for partial response recovery
        _partial_tools = []  # accumulate tool calls
        _partial_thinking = []  # accumulate thinking blocks
        _thinking_summary = {}  # opaque-reasoning summary (format + reasoning_tokens)
        _usage_totals = {"tokens_in": 0, "tokens_out": 0, "last_tokens_in": 0}  # cumulative across tool rounds; last_tokens_in = most recent round only
        _request_payloads = []  # capture request snapshots per tool round
        def event_callback(event_type, data):
            if event_type == "text_delta":
                _partial_reply.append(data.get("text", ""))
            elif event_type in ("file_created", "artifact_updated"):
                created_files.append(data)
            elif event_type == "thinking_delta":
                _partial_thinking.append(data.get("text", ""))
            elif event_type == "thinking_done":
                # Persist this round's thinking as its own message row so the
                # transcript preserves chronological order: thinking → tool calls →
                # next round's thinking → final assistant text. The engine fires this
                # per tool-round, so multi-round reasoning ends up as multiple rows
                # interleaved with tool_call/tool_result. Skip if no text (opaque path).
                _round_text = data.get("text") or "".join(_partial_thinking)
                _round_text = _round_text.strip()
                if _round_text:
                    _tr = data.get("tool_round")
                    _meta = {"tool_round": _tr} if _tr is not None else None
                    try:
                        session.add_message("thinking", _round_text, metadata=_meta)
                    except Exception as _e:
                        print(f"[thinking-persist] failed: {_e}", flush=True)
                # Reset the accumulator so the next round starts fresh.
                _partial_thinking.clear()
            elif event_type == "thinking_summary":
                _thinking_summary.update(data)
            elif event_type == "tool_call":
                name = data.get("name", "")
                args = data.get("args", {})
                tr = data.get("tool_round")
                # Update existing entry if re-emitted with full args, else append
                if args and _partial_tools and _partial_tools[-1].get("name") == name and not _partial_tools[-1].get("args"):
                    _partial_tools[-1]["args"] = args
                    if tr is not None:
                        _partial_tools[-1]["tool_round"] = tr
                else:
                    entry = {"name": name, "args": args}
                    if tr is not None:
                        entry["tool_round"] = tr
                    _partial_tools.append(entry)
            elif event_type == "tool_result":
                # Attach result to the last matching tool
                # Web tools get higher cap to preserve reference URLs
                tool_name = data.get("name", "")
                cap = 2000 if tool_name in ("exa_search", "web_fetch") else 500
                for t in reversed(_partial_tools):
                    if t["name"] == tool_name and "result" not in t:
                        t["result"] = str(data.get("result", ""))[:cap]
                        break
            elif event_type == "usage":
                _usage_totals["tokens_in"] += data.get("tokens_in", 0)
                _usage_totals["tokens_out"] += data.get("tokens_out", 0)
                _usage_totals["last_tokens_in"] = data.get("tokens_in", 0)
                # Attach per-round actual tokens to the matching request_payload
                _ur = data.get("tool_round")
                if _ur is not None:
                    for _p in _request_payloads:
                        if _p.get("tool_round") == _ur:
                            _p["tokens_in"] = data.get("tokens_in", 0)
                            _p["tokens_out"] = data.get("tokens_out", 0)
                            break
                return  # internal only, don't send to client
            elif event_type == "worker_usage":
                # Worker-side LLM call (e.g. summariser) tokens. Add to turn totals
                # so the status bar reflects the real cost. Forward to client for
                # the worker-flow panel.
                _usage_totals["tokens_in"] += data.get("tokens_in", 0)
                _usage_totals["tokens_out"] += data.get("tokens_out", 0)
                # (fall through so the event reaches the SSE queue)
            elif event_type == "request_payload":
                _request_payloads.append(data)
                return  # internal only, don't send to client
            event_queue.put((event_type, data))

        handler_self = self  # capture for closure

        def _rollback_messages(session, sid, target_count):
            """Rollback session.messages to target_count and remove extras from DB.
            Handles intermediate tool_use/tool_result messages from the agentic loop."""
            with session.lock:
                extras = len(session.messages) - target_count
                if extras <= 0:
                    return
                session.messages = session.messages[:target_count]
            # Delete the extra messages from DB (they were appended by send_message's tool loop)
            try:
                with _db_conn() as conn:
                    # Get all message IDs for this session, ordered by id
                    rows = conn.execute(
                        "SELECT id FROM messages WHERE session_id = ? ORDER BY id",
                        (sid,)
                    ).fetchall()
                    # Keep only the first target_count messages
                    if len(rows) > target_count:
                        ids_to_delete = [r[0] for r in rows[target_count:]]
                        conn.executemany("DELETE FROM messages WHERE id = ?", [(mid,) for mid in ids_to_delete])
                        conn.commit()
            except Exception as e:
                print(f"  [WARN] Message rollback DB cleanup: {e}", flush=True)

        def worker():
            # Set thread-local agent context (thread-safe, no global mutation)
            engine._thread_local.memory_store = session.memory
            agent_config = engine.AgentConfig(session.agent_id)
            engine._thread_local.current_agent = agent_config
            engine._thread_local.current_session_id = sid
            engine._thread_local.current_user_id = session.user_id or ""
            # Team IDs the user belongs to — used for team-scoped MemPalace wing filtering
            try:
                engine._thread_local.current_team_ids = [
                    t["id"] for t in _auth_mod.AuthDB.get_user_teams(session.user_id)
                ] if session.user_id else []
            except Exception:
                engine._thread_local.current_team_ids = []

            # Reset per-request state (prevents cross-session leaks in pooled threads)
            engine.reset_tool_dedup()

            # Use shared MCP manager (singleton from main())
            engine._thread_local.mcp_manager = engine._mcp_manager

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

            # Set caveman modes: chat-level (session toggle) + system-level (model config)
            engine._thread_local.caveman_chat = session.caveman_mode
            model_cfg = engine.resolve_model_settings(session.model) if engine._models_config else {}
            engine._thread_local.caveman_system = int(model_cfg.get("caveman_system", 0) or 0)

            # Set worker subagent execution overrides from agent config
            engine._thread_local.execution_overrides = agent_config.config.get("execution_overrides") or {}

            # Set attachment image model for read_attachment vision support
            engine._thread_local.attachment_image_model = server_config.get("attachment_image_model", "")

            # Set current model for worker summariser (cache reuse)
            engine._thread_local._current_model = session.model

            # Client-hosted inference capability: if the browser/Electron has
            # declared it can serve this model's family locally, plumb that
            # decision into the engine so send_message can route to the client
            # instead of calling the LLM directly. See phases 2-3 for details.
            engine._thread_local.client_capabilities = dict(session.client_capabilities or {})

            # Snapshot message count for rollback on failure
            _msg_count_before = len(session.messages)
            _req_start = time.time()

            try:
                # --- Standard backend ---
                # Use detected purpose from auto-resolve, or fall back to agent's fixed purpose
                purpose = session.agent.config.get("model_purpose")
                if not purpose and session.agent.config.get("model") == "auto":
                    purpose = engine.classify_task_purpose(message)
                inf_params = engine.get_inference_params(session.model, purpose)
                # Apply thinking level from request — only when the model supports thinking.
                _model_cfg = engine._models_config.get(session.model, {}) or {}
                _tfmt = _model_cfg.get("thinking_format", "none")
                if thinking_level and thinking_level != "none" and _tfmt != "none":
                    _THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 32768}
                    inf_params["thinking"] = True
                    inf_params["thinking_budget"] = _THINKING_BUDGETS.get(thinking_level, 8192)
                    # Provider-facing reasoning toggle. Engine's _apply_inference_to_payload maps this
                    # per thinking_format: reasoning_effort for mistral_blocks/reasoning_field/openai_opaque,
                    # chat_template_kwargs.enable_thinking for oMLX inline_tags variants, etc.
                    inf_params["thinking_level"] = thinking_level
                else:
                    inf_params.pop("thinking", None)
                    inf_params.pop("thinking_budget", None)
                    inf_params.pop("thinking_level", None)
                # If thinking-mode flipped vs what the warmup keeper primed,
                # kick off a background re-prime so the *next* turn's KV
                # prefix matches. Current turn still pays the cold cost.
                # No-op when model isn't warmup-flagged or has thinking_format=none.
                _wants_thinking = bool(inf_params.get("thinking"))
                engine.maybe_reprime_for_thinking(session.model, _wants_thinking,
                                                  agent_id=session.agent_id)
                reply = engine.send_message_with_fallback(
                    session.messages, session.model, session.api_key,
                    session.base_url,
                    silent=True, escape_watcher=session.cancel_token,
                    event_callback=event_callback,
                    provider_resolver=handler_self._resolve_provider,
                    inference_params=inf_params,
                    purpose=purpose,
                    session_id=sid,
                )
                if reply:
                    # Compute cost before saving
                    session_cost = None
                    if engine._cost_tracker:
                        try:
                            sc = engine._cost_tracker.get_session_cost(sid)
                            session_cost = round(sc.get("cost", 0.0), 4)
                        except Exception:
                            pass
                    # Build metadata: model, tokens, cost, files, tools, duration, usage
                    _req_duration = round(time.time() - _req_start, 2)
                    msg_metadata = {}
                    msg_metadata["model"] = session.model
                    msg_metadata["execution_mode"] = server_config.get("execution_mode", "server")
                    msg_metadata["duration"] = _req_duration
                    msg_metadata["tokens_in"] = _usage_totals["tokens_in"]
                    msg_metadata["tokens_out"] = _usage_totals["tokens_out"]
                    msg_metadata["last_tokens_in"] = _usage_totals["last_tokens_in"]
                    if _request_payloads:
                        msg_metadata["request_payloads"] = _request_payloads
                    fb_model = getattr(engine._thread_local, '_fallback_model_used', None)
                    if fb_model:
                        msg_metadata["model"] = fb_model
                        msg_metadata["original_model"] = session.model
                    msg_metadata["tokens"] = engine._estimate_conversation_tokens(session.messages)
                    if session_cost is not None:
                        msg_metadata["cost"] = session_cost
                    if created_files:
                        msg_metadata["files"] = created_files
                    if _partial_tools:
                        msg_metadata["tools"] = _partial_tools
                    # Leftover thinking deltas that never got a thinking_done (truncated
                    # stream / error before flush). Persist as a fallback thinking row
                    # rather than losing the content.
                    thinking_leftover = "".join(_partial_thinking).strip()
                    if thinking_leftover:
                        try:
                            session.add_message("thinking", thinking_leftover,
                                                 metadata={"tool_round": None, "fallback": True})
                        except Exception:
                            msg_metadata["thinking"] = thinking_leftover  # legacy fallback
                        _partial_thinking.clear()
                    if _thinking_summary:
                        msg_metadata["thinking_summary"] = _thinking_summary
                    # Per-turn state snapshot: thinking level requested + caveman modes applied
                    if thinking_level:
                        msg_metadata["thinking_level"] = thinking_level
                    _cav_chat = int(getattr(engine._thread_local, "caveman_chat", 0) or 0)
                    _cav_sys = int(getattr(engine._thread_local, "caveman_system", 0) or 0)
                    if _cav_chat:
                        msg_metadata["caveman_chat"] = _cav_chat
                    if _cav_sys:
                        msg_metadata["caveman_system"] = _cav_sys
                    session.add_message("assistant", reply, metadata=msg_metadata or None)
                    done_data = {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                        "max_context": session.max_context,
                        "model": session.model,
                        "execution_mode": server_config.get("execution_mode", "server"),
                        "duration": _req_duration,
                        "tokens_in": _usage_totals["tokens_in"],
                        "tokens_out": _usage_totals["tokens_out"],
                        "last_tokens_in": _usage_totals["last_tokens_in"],
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

                    # Generate chat summary (background, for sidebar display)
                    try:
                        if len(session.messages) >= 2 and not session.summary:
                            threading.Thread(
                                target=_generate_chat_summary,
                                args=(session,),
                                daemon=True,
                                name=f"chat_summary_{sid}"
                            ).start()
                    except Exception:
                        pass

                    # Index chat transcript for content search (4+ messages, every 4th message or first time)
                    try:
                        msg_count = len(session.messages)
                        if msg_count >= 4 and (msg_count % 4 == 0 or not os.path.isdir(
                                os.path.join(engine.AGENTS_DIR, session.agent_id, "chats-indexed"))):
                            threading.Thread(
                                target=_index_chat_transcript,
                                args=(session,),
                                daemon=True,
                                name=f"chat_index_{sid}"
                            ).start()
                    except Exception:
                        pass
                else:
                    # Empty reply — rollback all intermediate messages from tool loop
                    _rollback_messages(session, sid, _msg_count_before)
                    event_queue.put(("done", {"text": "", "tokens": 0, "model": session.model}))
            except engine.TaskCancelled:
                # Save partial response if any text was streamed
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += "\n\n*(Cancelled)*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": "Cancelled"}))
            except SystemExit as e:
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += f"\n\n*(Engine error: exit code {e.code})*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": f"Engine fatal error (exit code {e.code})"}))
            except Exception as e:
                import traceback
                traceback.print_exc()
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += f"\n\n*(Error: {str(e)[:200]})*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": str(e)}))
            finally:
                with session.lock:
                    session._streaming = False
                # Clean up thread-local state
                engine._thread_local.current_agent = None
                engine._thread_local.mcp_manager = None
                engine._thread_local.memory_store = None
                engine._thread_local.plan_mode = False
                engine._thread_local.caveman_chat = 0
                engine._thread_local.caveman_system = 0
                engine._thread_local.execution_overrides = {}
                engine._thread_local._current_model = None
                engine._thread_local.client_capabilities = None
                engine.cleanup_proxy_channel(sid)
                event_queue.put(None)  # sentinel


        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Stream events to client with keepalive (chunked encoding for HTTP/1.1)
        try:
            while True:
                try:
                    event = event_queue.get(timeout=5)
                except queue.Empty:
                    # If worker thread died, stop waiting
                    if not t.is_alive() and event_queue.empty():
                        try:
                            sse_err = f'event: error\ndata: {json.dumps({"message": "Server worker terminated unexpectedly"})}\n\n'
                            self.wfile.write(sse_err.encode("utf-8")); self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    # Send keepalive comment to prevent browser timeout
                    try:
                        self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                    continue
                if event is None:
                    break
                event_type, data = event
                sse_line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(sse_line.encode("utf-8")); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
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

    def _handle_schedule_upload(self):
        """Persist a scheduled-task attachment under
        agents/<agent>/scheduled_attachments/<unique>/<filename>.
        Returns {name, path, mime, size} that the UI passes back in the
        `attachments` array on add/edit. We deliberately decouple upload
        from schedule creation: the user can upload before naming the task,
        and the server cleans up orphan folders on a sweep (TODO)."""
        import mimetypes as _mt
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
        if not content_length:
            self._send_json({"error": "No content"}, 400)
            return
        body = self.rfile.read(content_length)
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break
        if not boundary:
            self._send_json({"error": "No boundary in Content-Type"}, 400)
            return
        delimiter = f"--{boundary}".encode()
        parts = body.split(delimiter)
        filename = None
        file_data = None
        form_fields = {}
        for part in parts:
            if not part or part == b"--\r\n" or part == b"--":
                continue
            if b"\r\n\r\n" in part:
                header_block, part_body = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                header_block, part_body = part.split(b"\n\n", 1)
            else:
                continue
            if part_body.endswith(b"\r\n"):
                part_body = part_body[:-2]
            header_text = header_block.decode("utf-8", errors="replace")
            field_name = None
            field_filename = None
            for line in header_text.split("\r\n"):
                line = line.strip()
                if line.lower().startswith("content-disposition:"):
                    for item in line.split(";"):
                        item = item.strip()
                        if item.startswith("name="):
                            field_name = item[5:].strip('"').strip("'")
                        elif item.startswith("filename="):
                            field_filename = item[9:].strip('"').strip("'")
            if field_name == "file" and field_filename:
                filename = field_filename
                file_data = part_body
            elif field_name:
                form_fields[field_name] = part_body.decode("utf-8", errors="replace")
        if not filename or file_data is None:
            self._send_json({"error": "No file uploaded"}, 400)
            return
        agent_id = form_fields.get("agent", "main") or "main"
        # Sanitize agent_id and filename to keep us inside AGENTS_DIR.
        if "/" in agent_id or ".." in agent_id:
            self._send_json({"error": "Invalid agent id"}, 400)
            return
        safe_name = filename.replace("/", "_").replace("\\", "_")
        unique = uuid.uuid4().hex[:12]
        store_dir = os.path.join(engine.AGENTS_DIR, agent_id,
                                 "scheduled_attachments", unique)
        try:
            os.makedirs(store_dir, exist_ok=True)
            dst = os.path.join(store_dir, safe_name)
            with open(dst, "wb") as fp:
                fp.write(file_data)
        except OSError as e:
            self._send_json({"error": f"Save failed: {e}"}, 500)
            return
        mime = _mt.guess_type(safe_name)[0] or "application/octet-stream"
        self._send_json({
            "name": safe_name,
            "path": dst,
            "mime": mime,
            "size": len(file_data),
        })

    def _handle_modify_schedule(self):
        body = self._read_json()
        action = body.get("action", "list")
        if not engine._scheduler:
            self._send_json({"error": "Scheduler not running"}, 500)
            return

        if action == "add":
            atts = body.get("attachments") or []
            if not isinstance(atts, list):
                atts = []
            wd = body.get("working_dir") or None
            if isinstance(wd, str):
                wd = wd.strip() or None
            result = engine._scheduler.add(
                body.get("name", ""), body.get("task", ""),
                body.get("schedule", ""), body.get("agent", "main"),
                body.get("model"), timeout=int(body.get("timeout", 300)),
                attachments=atts, working_dir=wd,
            )
            self._send_json(result)
        elif action == "pause":
            self._send_json(engine._scheduler.pause(body.get("name", "")))
        elif action == "resume":
            self._send_json(engine._scheduler.resume(body.get("name", "")))
        elif action == "delete":
            self._send_json(engine._scheduler.remove(body.get("name", "")))
        elif action == "run_now":
            name = body.get("name", "")
            task_row = engine._scheduler.get_task(name) if hasattr(engine._scheduler, 'get_task') else None
            if task_row:
                t = threading.Thread(target=engine._scheduler._execute_scheduled, args=(task_row,), daemon=True, name=f"sched_now_{name}")
                t.start()
                self._send_json({"status": "triggered", "name": name})
            else:
                self._send_json({"error": f"Task '{name}' not found"}, 404)
        elif action == "history":
            self._send_json({"history": engine._scheduler.get_history(
                body.get("name"), body.get("limit", 20))})
        elif action == "delete_run":
            # Purge a single historical run (row + its artifacts + files).
            try:
                run_id = int(body.get("run_id") or 0)
            except (TypeError, ValueError):
                run_id = 0
            if not run_id:
                self._send_json({"error": "run_id is required"}, 400)
                return
            res = engine._scheduler.delete_run(run_id)
            if isinstance(res, dict) and res.get("error"):
                self._send_json(res, 400 if "Cannot delete" in res["error"] else 404)
            else:
                self._send_json(res)
        elif action == "clear_history":
            # Wipe every historical run for a named schedule (schedule row kept).
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return
            self._send_json(engine._scheduler.delete_history(name))
        elif action == "purge_orphan_history":
            # Wipe history rows whose schedule no longer exists.
            self._send_json(engine._scheduler.delete_orphan_history())
        elif action == "edit":
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return
            fields = {k: body.get(k) for k in
                      ("task", "schedule", "model", "timeout", "agent",
                       "new_name", "attachments", "working_dir")
                      if k in body}
            res = engine._scheduler.update(name, fields)
            if isinstance(res, dict) and res.get("error"):
                self._send_json(res, 400)
            else:
                self._send_json(res)
        elif action == "run_detail":
            # Full detail for one historical run: the row itself, associated
            # trace spans (joined via session_id=sched-<run_id>), and any
            # artifacts the run produced.
            try:
                run_id = int(body.get("run_id") or 0)
            except (TypeError, ValueError):
                run_id = 0
            if not run_id:
                self._send_json({"error": "run_id is required"}, 400)
                return
            row = engine._scheduler.get_run(run_id)
            if not row:
                self._send_json({"error": f"Run {run_id} not found"}, 404)
                return
            session_id = f"sched-{run_id}"
            spans = []
            try:
                tm = engine._trace_manager
                if tm:
                    trace_id = row.get("trace_id")
                    if trace_id:
                        spans = tm.get_trace(trace_id)
                    elif hasattr(tm, "get_spans_for_session"):
                        spans = tm.get_spans_for_session(session_id)
                    # Pre-migration fallback: old runs have neither trace_id
                    # nor sched-<id> session tag. Pivot by time window +
                    # agent instead. schedule_history.started_at/finished_at
                    # are naive-local; traces.started_at is UTC (Z suffix).
                    # Convert local -> UTC using the system's current offset,
                    # pad by 30s on both sides, and drop spans that clearly
                    # belong to a concurrent interactive chat.
                    if not spans and row.get("started_at"):
                        try:
                            import datetime as _dt
                            from datetime import timezone as _tz
                            def _local_to_utc_z(iso: str) -> str:
                                dt = _dt.datetime.fromisoformat(iso)
                                if dt.tzinfo is None:
                                    dt = dt.astimezone()  # attach local tz
                                return (dt.astimezone(_tz.utc)
                                          .isoformat(timespec="milliseconds")
                                          .replace("+00:00", "Z"))

                            start_utc_z = _local_to_utc_z(row["started_at"])
                            end_iso = row.get("finished_at") or _dt.datetime.now().isoformat()
                            end_utc_z = _local_to_utc_z(end_iso)
                            # Pad ±30s to absorb clock jitter / rounding.
                            s_dt = _dt.datetime.fromisoformat(start_utc_z.replace("Z","+00:00")) - _dt.timedelta(seconds=30)
                            e_dt = _dt.datetime.fromisoformat(end_utc_z.replace("Z","+00:00")) + _dt.timedelta(seconds=30)
                            s_bound = s_dt.isoformat(timespec="milliseconds").replace("+00:00","Z")
                            e_bound = e_dt.isoformat(timespec="milliseconds").replace("+00:00","Z")
                            with engine._traces_conn() as conn:
                                import sqlite3 as _sq
                                conn.row_factory = _sq.Row
                                sp = conn.execute(
                                    "SELECT * FROM traces WHERE agent = ? "
                                    "AND started_at >= ? AND started_at <= ? "
                                    "ORDER BY started_at",
                                    (row.get("agent") or "main", s_bound, e_bound),
                                ).fetchall()
                                spans = [dict(s) for s in sp]
                            # Drop spans that belong to a concurrent
                            # interactive chat (any session_id that's neither
                            # empty nor a sched-<n> tag).
                            if spans:
                                spans = [s for s in spans
                                         if not s.get("session_id")
                                         or str(s.get("session_id", "")).startswith("sched-")]
                        except Exception:
                            pass
            except Exception as e:
                spans = []
                row["_trace_error"] = str(e)
            # Prefer the artifacts table (indexed by session_id=sched-<run_id>)
            # over the folder listing — those rows carry the artifact_id the
            # client needs to open them via openArtifactFromBrowse. Folder
            # scanning stays as a fallback for runs that wrote a file but
            # never went through _after_file_write (shouldn't happen post-fix).
            artifacts: list = []
            try:
                art_rows = ChatDB.list_artifacts_for_session(session_id) \
                    if hasattr(ChatDB, "list_artifacts_for_session") \
                    else []
                if not art_rows:
                    # Fallback: query by session_id directly
                    all_a = ChatDB.get_all_artifacts(agent_id=row.get("agent"), limit=500)
                    art_rows = [a for a in all_a if a.get("session_id") == session_id]
                for a in art_rows:
                    try:
                        size = a.get("latest_size") or (os.path.getsize(a["path"]) if os.path.isfile(a["path"]) else 0)
                    except OSError:
                        size = 0
                    artifacts.append({
                        "id": a.get("id"),
                        "name": a.get("name"),
                        "path": a.get("path"),
                        "size": size,
                        "type": a.get("type"),
                        "role": a.get("role", "output"),
                        "latest_version": a.get("latest_version", 1),
                    })
            except Exception:
                artifacts = []

            # Fallback to folder scan if nothing was registered.
            if not artifacts:
                folder = row.get("artifact_folder")
                if folder:
                    agent_id = row.get("agent") or "main"
                    folder_path = os.path.join(
                        engine.AGENTS_DIR, agent_id, "artifacts", folder)
                    if os.path.isdir(folder_path):
                        for fname in sorted(os.listdir(folder_path)):
                            fpath = os.path.join(folder_path, fname)
                            if os.path.isfile(fpath):
                                try:
                                    size = os.path.getsize(fpath)
                                except OSError:
                                    size = 0
                                artifacts.append({
                                    "id": None,  # unregistered file — not openable via panel
                                    "name": fname,
                                    "size": size,
                                    "path": fpath,
                                })
            self._send_json({
                "run": row,
                "session_id": session_id,
                "spans": spans,
                "artifacts": artifacts,
            })
        else:
            self._send_json({"schedules": engine._scheduler.list_all()})

    def _handle_list_providers(self):
        providers = server_config.get("providers", {})
        models_cfg = engine._models_config or {}
        result = []
        for name, p in providers.items():
            # Use already-known models from config instead of fetching from provider
            all_models = [mid for mid, mcfg in models_cfg.items()
                          if mcfg.get("provider") == name]
            enabled_models = [mid for mid, mcfg in models_cfg.items()
                              if mcfg.get("provider") == name and mcfg.get("enabled", True)]
            result.append({
                "name": name,
                "base_url": p.get("base_url", ""),
                "api_key": p.get("api_key", "")[:4] + "***" if p.get("api_key") else "",
                "type": p.get("type", "openai"),
                "default_model": p.get("default_model", ""),
                "use_sdk": p.get("use_sdk", True),
                "models": all_models,
                "model_count": len(all_models),
                "enabled_count": len(enabled_models),
                "status": "connected" if all_models else "no models",
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

    # --- Client-hosted local model manifest ---
    # Server declares GGUF weights that Electron clients may download and run
    # locally. Family string is the routing key (server oMLX + client GGUF with
    # matching family are treated as one model for session routing).

    def _handle_client_models_manifest(self):
        """GET /v1/client/models/manifest — return the list of client-eligible
        models. Any authenticated user can read this. Strips absolute paths;
        clients never learn where weights live on the server, only that they
        exist and what their sha256/size are. """
        entries = engine._load_client_models()
        out = []
        for e in entries:
            out.append({
                "id": e.get("id"),
                "family": e.get("family"),
                "sha256": e.get("sha256", ""),
                "size_bytes": int(e.get("size_bytes") or 0),
                "auto_download": bool(e.get("auto_download", False)),
                "download_path": f"/v1/client/models/{e.get('id')}/weights",
            })
        self._send_json({"models": out})

    def _handle_client_engines_manifest(self):
        """GET /v1/client/engines — per-platform llama.cpp binary URLs.

        Admin publishes entries in config.json → client_engines:
        {
          "darwin-arm64": {"url": "...", "sha256": "..."},
          "win32-x64":    {"url": "...", "sha256": "..."},
          "linux-x64":    {"url": "...", "sha256": "..."}
        }

        URL may point to an internal mirror for air-gapped deployments.
        No server-hardcoded URLs — we refuse to invent defaults so a
        misconfigured server never silently fetches from the public
        internet when the admin assumed air-gap."""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    cfg = json.load(f)
            engines = cfg.get("client_engines", {}) or {}
            # Strip any fields beyond what the Electron client needs.
            out = {}
            for key, entry in engines.items():
                if not isinstance(entry, dict):
                    continue
                u = entry.get("url", "")
                sha = entry.get("sha256", "")
                if u and sha:
                    out[key] = {"url": u, "sha256": sha}
            self._send_json({"engines": out})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_client_model_weights(self, path: str):
        """GET /v1/client/models/<id>/weights — stream GGUF bytes. Supports
        HTTP Range for resumable downloads. Any authenticated user may pull
        weights the admin has listed."""
        # Parse id from path
        rest = path[len("/v1/client/models/"):]
        model_id = rest.rsplit("/weights", 1)[0]
        if not model_id or "/" in model_id or ".." in model_id:
            self._send_json({"error": "invalid model id"}, 400)
            return
        entry = engine.get_client_model(model_id)
        if not entry:
            self._send_json({"error": "model not found"}, 404)
            return
        gguf_path = entry.get("gguf_path", "")
        if not gguf_path or not os.path.isfile(gguf_path):
            self._send_json({"error": "weights file missing on server"}, 404)
            return

        try:
            total = os.path.getsize(gguf_path)
        except OSError as e:
            self._send_json({"error": f"stat failed: {e}"}, 500)
            return

        # Parse Range: bytes=start-end
        range_header = self.headers.get("Range", "") or self.headers.get("range", "")
        start, end = 0, total - 1
        is_partial = False
        if range_header.startswith("bytes="):
            try:
                spec = range_header[len("bytes="):].split("-", 1)
                if spec[0].strip():
                    start = int(spec[0])
                if len(spec) > 1 and spec[1].strip():
                    end = int(spec[1])
                if start < 0 or end >= total or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                is_partial = True
            except ValueError:
                self._send_json({"error": "invalid Range header"}, 400)
                return

        length = end - start + 1
        status = 206 if is_partial else 200
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if is_partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            # sha256 header lets the client verify without a separate call
            if entry.get("sha256"):
                self.send_header("X-Model-SHA256", entry["sha256"])
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return

        # Audit log the download (once per request, before streaming).
        # Only log the first chunk of a ranged download so large files don't
        # flood audit with one row per MiB — 'start == 0' is our "fresh download
        # or full fetch" heuristic.
        if start == 0:
            try:
                user = getattr(self, "_auth_user", None) or {}
                engine._audit_log.log_action(
                    agent=user.get("id", "anonymous"),
                    action_type="client_model_download",
                    tool_name="client_models",
                    source="weight_stream",
                    args_summary=f"model={model_id} bytes={total}",
                )
            except Exception:
                pass

        # Stream in 1 MiB chunks
        chunk = 1024 * 1024
        try:
            with open(gguf_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    buf = f.read(min(chunk, remaining))
                    if not buf:
                        break
                    try:
                        self.wfile.write(buf)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    remaining -= len(buf)
        except OSError as e:
            try:
                print(f"[client-models] weight stream error: {e}", file=sys.stderr, flush=True)
            except Exception:
                pass

    def _handle_client_models_admin(self):
        """POST /v1/client/models — admin-only CRUD for the manifest.

        Actions:
          - save: replace full list
          - add: insert/update one entry by id
          - delete: remove one entry by id
        """
        body = self._read_json()
        action = body.get("action", "save")
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

        def _hash_and_size(p: str) -> tuple[str, int] | None:
            if not p or not os.path.isfile(p):
                return None
            try:
                h = hashlib.sha256()
                size = 0
                with open(p, "rb") as f:
                    while True:
                        b = f.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                        size += len(b)
                return h.hexdigest(), size
            except OSError:
                return None

        def _validate(entry: dict) -> tuple[bool, str]:
            for k in ("id", "family", "gguf_path"):
                if not entry.get(k):
                    return False, f"missing field: {k}"
            if "/" in entry["id"] or ".." in entry["id"]:
                return False, "id must be a simple slug (no '/' or '..')"
            if not os.path.isfile(entry["gguf_path"]):
                return False, f"gguf_path does not exist: {entry['gguf_path']}"
            return True, ""

        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            current = list(cfg.get("client_models", []) or [])

            if action == "save":
                entries = body.get("client_models", [])
                if not isinstance(entries, list):
                    self._send_json({"error": "client_models must be a list"}, 400)
                    return
                cleaned = []
                for e in entries:
                    ok, msg = _validate(e)
                    if not ok:
                        self._send_json({"error": f"invalid entry: {msg}"}, 400)
                        return
                    # Recompute hash/size unless the caller supplied them AND
                    # the file size hasn't changed.
                    hs = _hash_and_size(e["gguf_path"])
                    if hs is None:
                        self._send_json({"error": f"cannot hash {e['gguf_path']}"}, 500)
                        return
                    e["sha256"], e["size_bytes"] = hs
                    e["auto_download"] = bool(e.get("auto_download", False))
                    cleaned.append(e)
                cfg["client_models"] = cleaned

            elif action == "add":
                entry = body.get("model", {}) or {}
                ok, msg = _validate(entry)
                if not ok:
                    self._send_json({"error": msg}, 400)
                    return
                hs = _hash_and_size(entry["gguf_path"])
                if hs is None:
                    self._send_json({"error": f"cannot hash {entry['gguf_path']}"}, 500)
                    return
                entry["sha256"], entry["size_bytes"] = hs
                entry["auto_download"] = bool(entry.get("auto_download", False))
                # Replace by id if exists, else append
                current = [x for x in current if x.get("id") != entry["id"]]
                current.append(entry)
                cfg["client_models"] = current

            elif action == "delete":
                model_id = body.get("id", "")
                if not model_id:
                    self._send_json({"error": "id required"}, 400)
                    return
                cfg["client_models"] = [x for x in current if x.get("id") != model_id]

            else:
                self._send_json({"error": f"unknown action: {action}"}, 400)
                return

            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)

            server_config["client_models"] = cfg.get("client_models", [])
            engine._invalidate_client_models_cache()

            self._send_json({
                "status": "ok",
                "action": action,
                "client_models": cfg.get("client_models", []),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_test_provider(self):
        """POST /v1/providers/test — test provider connection."""
        body = self._read_json()
        # If only name is provided, look up provider config
        name = body.get("name")
        if name and not body.get("base_url"):
            providers = server_config.get("providers", {})
            p = providers.get(name, {})
            base_url = p.get("base_url", "")
            api_key = p.get("api_key", "")
        else:
            base_url = body.get("base_url", "")
            api_key = body.get("api_key", "")
        try:
            models = engine.get_available_models(api_key, base_url)
            self._send_json({
                "status": "ok",
                "models": len(models),
                "model_count": len(models),
                "model_list": models,
            })
        except Exception as e:
            self._send_json({
                "status": "error",
                "error": str(e),
                "models": [],
            })

    def _handle_models_config_get(self):
        """GET /v1/models/config — return models configuration.

        Each model entry is annotated with `is_local` (derived from the resolved
        provider's base_url) so the web UI can filter without re-implementing
        the local-URL matcher.
        """
        models = {}
        for mid, cfg in (engine._models_config or {}).items():
            entry = dict(cfg)
            try:
                entry["is_local"] = engine.is_model_local(mid)
            except Exception:
                entry["is_local"] = False
            models[mid] = entry
        self._send_json({
            "models": models,
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

            # Snapshot pre-save warmup flags + KV-prefix-relevant fields so we
            # can invalidate pool entries for models whose warmup was turned
            # off, or whose system-prompt-shaping config changed enough that
            # the pooled KV prefix is suspect.
            _prefix_fields = ("warmup", "warmup_mode", "enabled", "max_context",
                              "warmup_allow_cloud", "parallel_tool_calls",
                              "caveman_system", "provider", "base_model_id",
                              "profile")
            prev_model_snapshot = {
                mid: {k: cfg.get(k) for k in _prefix_fields}
                for mid, cfg in (engine._models_config or {}).items()
            }

            tombstones = list(config.get("deleted_models", []) or [])

            if action == "save":
                models = body.get("models", {})
                config["models"] = models
                engine._models_config = dict(models)
                # Any model id present in the new dict is, by definition, no
                # longer deleted — strip from tombstones (handles manual re-add).
                if tombstones:
                    tombstones = [mid for mid in tombstones if mid not in models]
                    config["deleted_models"] = tombstones

            elif action == "update":
                model_id = body.get("model_id", "")
                model_cfg = body.get("config", {})
                if not model_id:
                    self._send_json({"error": "model_id required"}, 400)
                    return
                config.setdefault("models", {})
                config["models"][model_id] = model_cfg
                engine._models_config[model_id] = model_cfg
                # Re-adding/updating an id revives it from the tombstone list.
                if model_id in tombstones:
                    tombstones.remove(model_id)
                    config["deleted_models"] = tombstones

            elif action == "delete":
                # User-initiated single-model delete. Removes from active config
                # AND tombstones the id so init_models_config doesn't auto-rediscover
                # it on next startup or sync.
                model_id = body.get("model_id", "")
                if not model_id:
                    self._send_json({"error": "model_id required"}, 400)
                    return
                config.setdefault("models", {}).pop(model_id, None)
                engine._models_config.pop(model_id, None)
                if model_id not in tombstones:
                    tombstones.append(model_id)
                config["deleted_models"] = tombstones

            elif action == "resync_provider":
                # Full user-initiated resync of one provider:
                #   1) drop ALL models attributed to that provider
                #   2) clear tombstones for those ids (incl. provider-scoped form)
                #   3) re-discover from /models endpoint
                # Never runs automatically — UI button only.
                prov_name = body.get("provider", "")
                if not prov_name:
                    self._send_json({"error": "provider required"}, 400)
                    return
                all_providers = server_config.get("providers", {})
                if prov_name not in all_providers:
                    self._send_json({"error": f"unknown provider: {prov_name}"}, 400)
                    return
                models_dict = config.setdefault("models", {})
                # Identify everything tied to this provider, in either bare or
                # provider-scoped form.
                cleared_ids: set[str] = set()
                for mid, mcfg in list(models_dict.items()):
                    if (mcfg or {}).get("provider") == prov_name:
                        cleared_ids.add(mid)
                        # Also collect the bare id behind a scoped key, since
                        # tombstones can appear in either form.
                        base = (mcfg or {}).get("base_model_id")
                        if base:
                            cleared_ids.add(base)
                        del models_dict[mid]
                        engine._models_config.pop(mid, None)
                # Clear tombstones for those ids + any "<provider>/..." scoped tombstones.
                tombstones = [
                    mid for mid in tombstones
                    if mid not in cleared_ids and not mid.startswith(f"{prov_name}/")
                ]
                config["deleted_models"] = tombstones
                # Re-discover this provider's models (synchronously — user clicked
                # a button and is waiting). Persist + clear caches.
                providers_subset = {prov_name: all_providers[prov_name]}
                synced = engine.init_models_config(
                    providers_subset, models_dict,
                    all_providers=all_providers,
                    deleted_models=tombstones,
                )
                config["models"] = synced
                engine._models_config = dict(synced)
                engine.clear_provider_cache()

            elif action == "sync":
                # Run sync in background thread — return immediately
                sync_provider = body.get("provider")  # optional: sync single provider
                def _bg_sync(provider_filter=None):
                    try:
                        all_providers = server_config.get("providers", {})
                        if provider_filter:
                            providers = {k: v for k, v in all_providers.items() if k == provider_filter}
                        else:
                            providers = all_providers
                        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                        with open(cfg_path) as f:
                            cfg = json.load(f)
                        existing = cfg.get("models", {})
                        deleted = cfg.get("deleted_models", [])
                        synced = engine.init_models_config(
                            providers, existing,
                            all_providers=all_providers,
                            deleted_models=deleted,
                        )
                        cfg["models"] = synced
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2)
                        engine.clear_provider_cache()
                    except Exception as e:
                        import traceback
                        print(f"[sync] error: {e}")
                        traceback.print_exc()
                threading.Thread(target=_bg_sync, args=(sync_provider,), daemon=True).start()
                self._send_json({"status": "syncing"})
                return

            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400)
                return

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            # Clear provider cache since model config changed
            engine.clear_provider_cache()

            # Invalidate warm-pool slots for models whose warmup flag flipped
            # off OR whose KV-prefix-relevant config changed (system prompt,
            # tool set, context size, provider all invalidate the primed prefix).
            # Also drop the cached _warmup_state so the keeper re-primes.
            new_warmup_models: set[str] = set()
            for mid, prev in prev_model_snapshot.items():
                now_cfg = engine._models_config.get(mid, {}) or {}
                now = {k: now_cfg.get(k) for k in _prefix_fields}
                was_on = bool(prev.get("warmup"))
                now_on = bool(now.get("warmup"))
                if was_on and not now_on:
                    warm_pool.invalidate_model(mid, reason="warmup flag off")
                    engine.set_warmup_state(mid, state="idle", last_error="")
                elif now_on and prev != now:
                    warm_pool.invalidate_model(mid, reason="config changed")
                    engine.set_warmup_state(mid, state="idle",
                                             last_warmup_ts=0, last_error="")
            # Newly-enabled warmup models (weren't in prev snapshot)
            for mid, cfg in (engine._models_config or {}).items():
                if cfg.get("warmup") and mid not in prev_model_snapshot:
                    new_warmup_models.add(mid)

            # Poke keeper so it re-evaluates immediately instead of waiting up
            # to interval_seconds (default 30s) — the set of models to prime
            # may have just changed.
            _wake_warmup_keeper()

            self._send_json({"status": "saved", "models": dict(engine._models_config)})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_warmup_status(self):
        """GET /v1/warmup/status — per-model warmup state snapshot for UI indicators.

        Hold-forever semantics: a model is 'warm' from the moment it's primed
        (or first used) and stays warm until something external evicts it.
        We don't age warm states back to cold on a TTL timer.
        """
        states = engine.all_warmup_states()
        pool_states = warm_pool.all_states()
        wcfg = server_config.get("warmup", {}) or {}
        now = time.time()
        out = {}
        any_warming = False
        any_pool_building = False
        for mid, _raw_cfg in engine._models_config.items():
            cfg = engine.resolve_model_settings(mid)
            if not cfg.get("warmup"):
                continue
            st = states.get(mid, {
                "state": "idle", "last_warmup_ts": 0, "last_used_ts": 0,
                "last_error": "", "next_due_ts": 0,
            })
            last = max(st.get("last_warmup_ts", 0), st.get("last_used_ts", 0))
            age = (now - last) if last else None
            effective = st.get("state", "idle")
            if effective == "warming":
                any_warming = True
            pool = pool_states.get(mid, {
                "state": "empty", "ready": 0, "building": 0,
                "target": WarmSessionPool.target_depth(), "built_at": 0,
            })
            if pool.get("state") == "building":
                any_pool_building = True
            desired_mode = (cfg.get("warmup_mode") or "full").lower()
            if desired_mode not in ("full", "minimal"):
                desired_mode = "full"
            out[mid] = {
                "state": effective,
                "last_warmup_ts": st.get("last_warmup_ts", 0),
                "last_used_ts": st.get("last_used_ts", 0),
                "last_error": st.get("last_error", ""),
                "age_seconds": age,
                "enabled": True,
                "display_name": cfg.get("display_name", mid),
                "provider": cfg.get("provider", ""),
                "mode": st.get("mode", ""),
                "desired_mode": desired_mode,
                "pool_state": pool.get("state", "empty"),
                "pool_built_at": pool.get("built_at", 0),
                "ready": pool.get("ready", 0),
                "building": pool.get("building", 0),
                "target": pool.get("target", WarmSessionPool.target_depth()),
            }
        self._send_json({
            "models": out,
            "any_warming": any_warming,
            "any_pool_building": any_pool_building,
            "enabled": wcfg.get("enabled", True),
            "interval_seconds": int(wcfg.get("interval_seconds", 30)),
        })

    def _handle_queue_status(self):
        """GET /v1/queue/status — snapshot of per-provider concurrency queue.

        Returns active + waiting tickets per provider for the UI modal. Only
        providers with max_concurrent > 0 in config.json get a queue slot; others
        are omitted from the output (they don't gate concurrency).
        """
        try:
            snap = engine.get_provider_queue().snapshot_all()
        except Exception as e:
            self._send_json({"error": str(e), "providers": {}}, 200)
            return
        providers = snap.get("providers", {})
        # Augment with configured max_concurrent for every provider (even idle)
        # so the UI can display capacity even when no tickets are in flight.
        try:
            cfg_providers = (server_config.get("providers") or {})
        except Exception:
            cfg_providers = {}
        for pname, pcfg in cfg_providers.items():
            mc = int(pcfg.get("max_concurrent", 0) or 0)
            if mc <= 0:
                continue
            if pname not in providers:
                providers[pname] = {
                    "provider": pname,
                    "max_concurrent": mc,
                    "active_count": 0,
                    "waiting_count": 0,
                    "active": [],
                    "waiting": [],
                }
        any_waiting = any(p.get("waiting_count", 0) > 0 for p in providers.values())
        any_active = any(p.get("active_count", 0) > 0 for p in providers.values())
        self._send_json({
            "providers": providers,
            "any_waiting": any_waiting,
            "any_active": any_active,
        })

    def _handle_queue_cancel(self):
        """POST /v1/queue/cancel — admin-only. Cancel a queued or running ticket.

        Body: {ticket_id: str, reason?: str}
        Waiting tickets are dropped from the waitlist (~instant).
        Running tickets: fires the ticket's cancel_token, which the SSE stream
        loop in _handle_openai_response checks every incoming chunk — aborts
        at the next byte or keepalive.
        """
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        ticket_id = (body.get("ticket_id") or "").strip()
        reason = (body.get("reason") or "").strip()
        if not ticket_id:
            self._send_json({"error": "ticket_id required"}, 400)
            return
        result = engine.get_provider_queue().cancel_ticket(
            ticket_id, reason=reason or f"by admin {user.get('username','?')}"
        )
        # Audit log the action for accountability
        try:
            if _audit_log:
                _audit_log.log_action(
                    agent=None,
                    action_type="queue_cancel",
                    tool_name="queue",
                    args_summary=f"ticket={ticket_id} state={result.get('state','?')}",
                    result_summary=f"provider={result.get('provider','?')} session={result.get('session_id','')}",
                    result_status="ok" if result.get("ok") else "error",
                    session_id=result.get("session_id") or None,
                    source=f"admin:{user.get('username','?')}",
                )
        except Exception:
            pass
        if not result.get("ok"):
            self._send_json(result, 404)
            return
        self._send_json(result)

    def _handle_warmup_trigger(self):
        """POST /v1/warmup/trigger — manually warm a specific model. Body: {model}."""
        body = self._read_json()
        mid = body.get("model", "")
        if not mid:
            self._send_json({"error": "model required"}, 400)
            return
        if not engine._models_config.get(mid):
            self._send_json({"error": "unknown model"}, 404)
            return
        cfg = engine.resolve_model_settings(mid)
        wcfg = server_config.get("warmup", {}) or {}
        allow_cloud = bool(cfg.get("warmup_allow_cloud",
                                   wcfg.get("allow_cloud", False)))

        def _run():
            try:
                engine.run_model_warmup(
                    mid, allow_cloud=allow_cloud, agent_id="main",
                    timeout=int(wcfg.get("timeout_seconds", 30)),
                )
            except Exception as e:
                print(f"[warmup-trigger] {mid}: {e}")

        threading.Thread(target=_run, daemon=True, name=f"warmup-trigger-{mid[:12]}").start()
        self._send_json({"status": "triggered", "model": mid})

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
        # Multi-user: filter projects by user access
        auth_user = getattr(self, '_auth_user', None)
        user_id = None
        user_team_ids = None
        if auth_user and auth_user["id"] != "__system__" and auth_user["role"] != "admin":
            user_id = auth_user["id"]
            user_team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(auth_user["id"])]
        projects = engine.ProjectManager.list_projects(agent_id, user_id=user_id, user_team_ids=user_team_ids)
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
        # Multi-user: stamp visibility and ownership
        auth_user = getattr(self, '_auth_user', None)
        owner_uid = auth_user["id"] if auth_user and auth_user["id"] != "__system__" else ""
        result = engine.ProjectManager.create_project(
            agent_id, name,
            description=body.get("description", ""),
            config=body,
            visibility=body.get("visibility", "global"),
            owner_user_id=owner_uid,
            owner_team_id=body.get("owner_team_id", ""),
        )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result, 201)

    def _session_access_check(self, sid: str, *, require_manage: bool = False) -> dict | None:
        """Load session metadata and verify the caller can access it.
        Returns the session info dict on success; sends 403/404 and returns None on fail.
        `require_manage` gates mutations: only owner, team head (for team sessions), or admin."""
        info = ChatDB.get_session_info(sid)
        if not info:
            self._send_json({"error": "Session not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        owner_uid = info.get("user_id") or ""
        team_id = info.get("team_id") or ""
        visibility = info.get("visibility") or "user"
        # Admin bypass
        if user["role"] == "admin" or user["id"] == "__system__":
            return info
        # Owner
        if owner_uid and owner_uid == user["id"]:
            return info
        # Legacy anonymous sessions (no owner): allow read by anyone authenticated
        if not owner_uid:
            return info
        # Team-scoped: members can read, only team head can manage
        if visibility == "team" and team_id:
            my_teams = {t["id"]: t for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
            if team_id in my_teams:
                if require_manage and my_teams[team_id]["head_user_id"] != user["id"]:
                    self._send_json({"error": "Only team head or session owner can modify"}, 403)
                    return None
                return info
        self._send_json({"error": "Access to this session is not permitted"}, 403)
        return None

    def _project_access_check(self, agent_id: str, proj_name: str, *, require_manage: bool = False) -> dict | None:
        """Load project.json, enforce visibility/ownership. Returns project dict on success,
        None after sending 403/404. If require_manage is True, only admin or owner (user or
        team head) can pass — used for PUT/DELETE/ingest/notes-write."""
        project = engine.ProjectManager.get_project(agent_id, proj_name)
        if not project:
            self._send_json({"error": f"Project '{proj_name}' not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_project(user, project):
            self._send_json({"error": "Access to this project is not permitted"}, 403)
            return None
        if require_manage:
            if user["role"] != "admin" and user["id"] != "__system__":
                owner_uid = project.get("owner_user_id", "")
                owner_tid = project.get("owner_team_id", "")
                if not _auth_mod.can_delete_resource(user, owner_uid, owner_tid):
                    self._send_json({"error": "Only project owner (or admin) can modify this project"}, 403)
                    return None
        return project

    def _handle_project_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        self._send_json(project)

    def _handle_project_update(self, path: str):
        """PUT /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json()
        # Non-admins cannot change visibility/ownership — only admins can reassign
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user["role"] != "admin" and user["id"] != "__system__":
            for locked in ("visibility", "owner_user_id", "owner_team_id"):
                body.pop(locked, None)
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
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
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

        # ACL: GET requires read access; writes require manage access
        require_manage = method in ("POST", "PUT", "DELETE")
        if self._project_access_check(agent_id, proj_name, require_manage=require_manage) is None:
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
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
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
        import tempfile
        import email.parser
        import email.policy
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        if not content_length:
            return {"error": "No content"}

        # Read the full body
        body = self.rfile.read(content_length)

        # Extract boundary from content-type
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break
        if not boundary:
            return {"error": "No boundary in Content-Type"}

        # Parse multipart parts manually
        delimiter = f"--{boundary}".encode()
        parts = body.split(delimiter)

        filename = None
        file_data = None
        form_fields = {}

        for part in parts:
            if not part or part == b"--\r\n" or part == b"--":
                continue
            # Split headers from body at first double newline
            if b"\r\n\r\n" in part:
                header_block, part_body = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                header_block, part_body = part.split(b"\n\n", 1)
            else:
                continue

            # Strip trailing \r\n from part body
            if part_body.endswith(b"\r\n"):
                part_body = part_body[:-2]

            header_text = header_block.decode("utf-8", errors="replace")
            # Parse Content-Disposition
            field_name = None
            field_filename = None
            for line in header_text.split("\r\n"):
                line = line.strip()
                if line.lower().startswith("content-disposition:"):
                    for item in line.split(";"):
                        item = item.strip()
                        if item.startswith("name="):
                            field_name = item[5:].strip('"').strip("'")
                        elif item.startswith("filename="):
                            field_filename = item[9:].strip('"').strip("'")

            if field_name == "file" and field_filename:
                filename = field_filename
                file_data = part_body
            elif field_name:
                form_fields[field_name] = part_body.decode("utf-8", errors="replace")

        if not filename or file_data is None:
            return {"error": "No file uploaded"}

        # Save to temp file with original filename preserved
        suffix = os.path.splitext(filename)[1]
        import tempfile
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        with open(tmp_path, "wb") as tmp:
            tmp.write(file_data)
        try:
            tags_raw = form_fields.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            chunk_size = int(form_fields.get("chunk_size", "1500"))
            chunk_overlap = int(form_fields.get("chunk_overlap", "200"))
            result = engine.IngestManager.ingest_file(
                agent_id, tmp_path, project_name=project_name,
                tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            )
            return result
        finally:
            try:
                os.unlink(tmp_path)
                os.rmdir(tmp_dir)
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
        if self._project_access_check(agent_id, proj_name) is None:
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
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash, project_name=proj_name)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)

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
                if not isinstance(s, Session):
                    continue  # skip loading sentinels
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
            # Invalidate warm pool if the main agent's system-prompt inputs
            # changed — the pooled KV prefix would no longer match the real
            # first-turn payload.
            if (agent_id == WarmSessionPool.POOL_AGENT
                    and filename in ("soul.md", "agent.json", "tools.md")):
                warm_pool.invalidate_all(f"{agent_id}/{filename} edited")
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
        cfg_dirty = False
        cfg = agent.config
        for field in ("description", "model", "display_name"):
            if body.get(field):
                cfg[field] = body[field]
                cfg_dirty = True
        if cfg_dirty:
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
            # Remove scheduled tasks for deleted agent
            try:
                if engine._scheduler:
                    for s in engine._scheduler.list_all():
                        if s.get("agent") == agent_id:
                            engine._scheduler.remove(s["name"])
            except Exception:
                pass
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

    def _handle_cc_skills_list(self):
        """GET /v1/skills/claude-code — list all Claude Code skills/plugins."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        agent_id = query.get("agent", ["main"])[0]

        # Get all CC skills from the scanner
        all_skills = engine.scan_claude_code_skills()

        # Get agent's enabled CC skills list
        agent_cfg = engine.AgentConfig(agent_id)
        agent_cc = agent_cfg.config.get("claude_code_skills", [])

        # Annotate with per-agent enabled state
        for skill in all_skills:
            skill["agent_enabled"] = skill["slug"] in agent_cc

        self._send_json({"skills": all_skills, "agent": agent_id})

    def _handle_cc_skills_manage(self):
        """POST /v1/skills/claude-code — enable/disable CC skill for an agent.
        Body: {agent, slug, enabled}"""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        slug = body.get("slug", "")
        enabled = body.get("enabled", True)

        if not slug:
            self._send_json({"error": "slug required"}, 400)
            return

        agent_cfg = engine.AgentConfig(agent_id)
        config = dict(agent_cfg.config)
        cc_skills = list(config.get("claude_code_skills", []))

        if enabled and slug not in cc_skills:
            cc_skills.append(slug)
        elif not enabled and slug in cc_skills:
            cc_skills.remove(slug)

        config["claude_code_skills"] = cc_skills
        agent_cfg.save_config(config)

        self._send_json({"status": "ok", "agent": agent_id, "slug": slug,
                         "enabled": enabled, "claude_code_skills": cc_skills})

    def _handle_cc_browse(self):
        """POST /v1/skills/claude-code/browse — search CC plugin marketplace.
        Body: {query}"""
        body = self._read_json()
        query = body.get("query", "")
        plugins = engine.browse_claude_code_plugins(query)
        self._send_json({"plugins": plugins, "count": len(plugins)})

    def _handle_cc_install(self):
        """POST /v1/skills/claude-code/install — install a CC plugin.
        Body: {plugin, marketplace}"""
        body = self._read_json()
        plugin_name = body.get("plugin", "")
        marketplace = body.get("marketplace", "claude-plugins-official")
        if not plugin_name:
            self._send_json({"error": "plugin name required"}, 400)
            return
        result = engine.install_claude_code_plugin(plugin_name, marketplace)
        status = 200 if "status" in result else 500
        self._send_json(result, status)

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
        """MemPalace migration: no-op. QMD is no longer used."""
        return

    @staticmethod
    def _qmd_run(args: list, timeout: int = 10) -> bool:
        """MemPalace migration: no-op. QMD is no longer used."""
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
        """MemPalace migration: QMD is no longer used; always return False so all
        QMD-dependent code paths short-circuit silently."""
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

    def _handle_costs(self):
        """GET /v1/costs?agent=X&hours=24&user_id=Y — cost stats."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        stats = engine._cost_tracker.get_stats(agent=agent, hours=hours, user_id=target_uid)
        self._send_json(stats)

    def _handle_costs_daily(self):
        """GET /v1/costs/daily?agent=X&days=7&user_id=Y — daily breakdown."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        days = int(params.get("days", "7"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        daily = engine._cost_tracker.get_daily(agent=agent, days=days, user_id=target_uid)
        self._send_json({"daily": daily, "days": days, "agent_filter": agent, "user_id": target_uid})

    # --- Per-user cost quotas ---

    def _handle_quota_me(self):
        """GET /v1/quotas/me — current authenticated user's quota state."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            state = engine._quota_manager.get_user_state(user["id"])
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json(state)

    def _handle_quota_config_get(self):
        """GET /v1/quotas/config — admin-only. Full quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        self._send_json(engine._quota_manager.get_config())

    def _handle_quota_config_save(self):
        """POST /v1/quotas/config — admin-only. Update quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        body = self._read_json() or {}
        try:
            saved = engine._quota_manager.save_config(body)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        # Audit log so changes are traceable
        try:
            if engine._audit_log:
                engine._audit_log.log_action(
                    agent="main",
                    action_type="quota_config_save",
                    tool_name="quota",
                    args_summary=f"by={user.get('username','')} cycle={saved.get('billing_cycle')} enforce={saved.get('enforce_red')}",
                    result_status="ok",
                )
        except Exception:
            pass
        self._send_json(saved)

    def _handle_quota_admin_users(self):
        """GET /v1/quotas/admin/users — admin-only. State for every user."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            users = _auth_mod.AuthDB.list_users()
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        out = []
        cfg = engine._quota_manager.get_config()
        for u in users:
            try:
                st = engine._quota_manager.get_user_state(u["id"], cfg=cfg)
            except Exception:
                continue
            out.append({
                "user_id": u["id"],
                "username": u.get("username") or "",
                "display_name": u.get("display_name") or "",
                "role": u.get("role") or "user",
                "disabled": bool(u.get("disabled")),
                "level": st["level"],
                "daily": st["daily"],
                "cycle": st["cycle"],
                "has_override": st["has_override"],
            })
        self._send_json({"users": out, "config": cfg})

    def _handle_quota_admin_breakdown(self):
        """GET /v1/quotas/admin/breakdown?user_id=X&days=N — per-user
        per-model + per-day breakdown for the current cycle. Admin sees
        anyone; non-admin only their own user_id."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager or not engine._cost_tracker:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        from urllib.parse import unquote
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        target_uid = unquote(params.get("user_id", "")) or user["id"]
        if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Insufficient permissions"}, 403)
            return
        try:
            days = max(1, min(365, int(params.get("days", "30"))))
        except ValueError:
            days = 30
        cfg = engine._quota_manager.get_config()
        state = engine._quota_manager.get_user_state(target_uid, cfg=cfg)
        cycle_start_iso = state["cycle"]["starts_at"].replace("T", " ").split("+", 1)[0].split(".")[0]
        per_model = engine._cost_tracker.per_model_user_window(target_uid, cycle_start_iso)
        daily = engine._cost_tracker.get_daily(days=days, user_id=target_uid)
        self._send_json({
            "user_id": target_uid,
            "state": state,
            "per_model": per_model,
            "daily": daily,
            "days": days,
        })

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

    def _handle_mcp_registry(self):
        """GET /v1/mcp/registry?q=...&limit=... — search official MCP registry."""
        import urllib.request
        import urllib.parse
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        query = params.get("q", [""])[0]
        limit = params.get("limit", ["20"])[0]
        try:
            url = f"https://registry.modelcontextprotocol.io/v0/servers?search={urllib.parse.quote(query)}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "BrainAgent/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            # Normalize into a flat list with install info — dedup by name
            servers = []
            seen = set()
            items = data if isinstance(data, list) else data.get("servers", [])
            for item in items:
                srv = item.get("server", item) if isinstance(item, dict) else item
                if not isinstance(srv, dict):
                    continue
                name = srv.get("name", "")
                if name in seen:
                    continue
                seen.add(name)
                desc = srv.get("description", "")
                repo = srv.get("repository", {})
                repo_url = repo.get("url", "") if isinstance(repo, dict) else ""
                packages = srv.get("packages", [])
                remotes = srv.get("remotes", [])
                pkg = packages[0] if packages else {}
                registry_type = pkg.get("registryType", "")
                identifier = pkg.get("identifier", "")
                transport = pkg.get("transport", {})
                transport_type = transport.get("type", "stdio") if isinstance(transport, dict) else "stdio"
                pkg_args = pkg.get("packageArguments", [])
                env_vars = pkg.get("environmentVariables", [])
                # Build install command from packages or remotes
                if registry_type == "npm":
                    command = "npx"
                    args = ["-y", identifier]
                elif registry_type == "pypi":
                    command = "uvx"
                    args = [identifier]
                elif remotes:
                    remote = remotes[0]
                    transport_type = remote.get("type", "sse")
                    command = remote.get("url", "")
                    args = []
                    registry_type = "remote"
                else:
                    command = identifier
                    args = []
                servers.append({
                    "name": name,
                    "description": desc,
                    "repo_url": repo_url,
                    "registry_type": registry_type,
                    "identifier": identifier,
                    "transport": transport_type,
                    "command": command,
                    "args": args,
                    "env_vars": [{"name": e.get("name",""), "description": e.get("description",""), "required": e.get("isRequired", False)} for e in env_vars],
                    "pkg_args": [{"name": a.get("name",""), "description": a.get("description",""), "required": a.get("isRequired", False), "format": a.get("format","")} for a in pkg_args],
                })
            self._send_json({"servers": servers})
        except Exception as e:
            self._send_json({"error": str(e), "servers": []})

    def _handle_refine(self):
        """POST /v1/refine — refine text with LLM one-shot call."""
        body = self._read_json()
        text = body.get("text", "") or body.get("content", "")
        context = body.get("context", "general")
        if not text:
            self._send_json({"error": "No text provided"}, 400)
            return

        # Find model: request body > tools_config setting > auto-select
        refine_model = body.get("model")
        if not refine_model:
            tc = engine.get_tool_config()
            refine_model = tc.get("refinement", {}).get("model", "")
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
                provider["base_url"],
                silent=True, tools=False,
            )
            self._send_json({"refined": result or text, "model": refine_model})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_soul_chat(self, path):
        """POST /v1/agents/<id>/soul-chat — chat to edit soul.md with LLM."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        message = body.get("message", "").strip()
        soul = body.get("soul", "")
        history = body.get("history", [])

        if not message:
            self._send_json({"error": "No message provided"}, 400)
            return

        # Resolve model (same logic as refine)
        model = None
        tc = engine.get_tool_config()
        model = tc.get("refinement", {}).get("model", "")
        if not model and engine._models_config:
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
                model = candidates[0][0]
        if not model:
            model = server_config.get("default_model", "")
        if not model:
            self._send_json({"error": "No model available"}, 503)
            return

        provider = self._resolve_provider(model)

        system_block = (
            "You are a soul.md editor assistant. The user wants to modify an agent's soul.md file "
            "(system prompt that defines the agent's personality and behavior).\n\n"
            "CURRENT SOUL.MD:\n```\n" + soul + "\n```\n\n"
            "RULES:\n"
            "- Help the user edit, improve, or rewrite the soul.md based on their instructions\n"
            "- When you make changes, output the COMPLETE updated soul.md inside a ```soul\n...\n``` code block\n"
            "- You may also provide brief commentary outside the code block\n"
            "- If the user is just asking a question or discussing (not requesting changes), respond normally without a code block\n"
            "- Preserve existing structure and formatting unless asked to change it\n"
            "- Keep the same voice/style unless the user wants a different one\n"
        )

        messages = [{"role": "user", "content": system_block}, {"role": "assistant", "content": "I understand. I'm ready to help you edit this agent's soul.md. What changes would you like to make?"}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        try:
            result = engine.send_message(
                messages, model, provider["api_key"],
                provider["base_url"],
                silent=True, tools=False,
            )
            self._send_json({"reply": result or "", "model": model})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_services_status(self):
        """GET /v1/services — status of all managed services."""
        uptime = int(time.time() - _server_start_time)
        tg_running = self._is_telegram_running()

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
                "attachment_image_model": server_config.get("attachment_image_model", ""),
                "execution_mode": server_config.get("execution_mode", "server"),
                "client_proxy_tools": server_config.get("client_proxy_tools", engine._CLIENT_PROXY_TOOLS_DEFAULT),
                "gdpr_scanner": {
                    "enabled": bool(server_config.get("gdpr_scanner", {}).get("enabled", True)),
                    "server_log": bool(server_config.get("gdpr_scanner", {}).get("server_log", True)),
                    "server_block": bool(server_config.get("gdpr_scanner", {}).get("server_block", False)),
                    "default_local_fallback_model": str(server_config.get("gdpr_scanner", {}).get("default_local_fallback_model") or ""),
                    "categories": server_config.get("gdpr_scanner", {}).get("categories") or {
                        cat: {"action": act} for cat, act in engine.PII_DEFAULT_CATEGORY_ACTIONS.items()
                    },
                    "rule_overrides": server_config.get("gdpr_scanner", {}).get("rule_overrides") or {},
                    "email_allowlist": server_config.get("gdpr_scanner", {}).get("email_allowlist") or [],
                },
                "available_tools": sorted(engine.TOOL_DISPATCH.keys()),
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
        """POST /v1/services/server — update server defaults (default_model, attachment_image_model)."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        result = {}

        # --- Default model ---
        model = body.get("default_model")
        if model:
            providers = server_config.get("providers", {})
            provider_name = None
            mcfg = engine._models_config or {}
            if model in mcfg and mcfg[model].get("provider"):
                provider_name = mcfg[model]["provider"]
            else:
                for pname, p in providers.items():
                    if p.get("default_model") == model:
                        provider_name = pname
                        break
            server_config["default_model"] = model
            if provider_name:
                server_config["api_key"] = providers[provider_name].get("api_key", "")
                server_config["base_url"] = providers[provider_name].get("base_url", "")
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
            result["default_model"] = model
            result["default_provider"] = provider_name or ""

        # --- Attachment image model ---
        if "attachment_image_model" in body:
            aim = body["attachment_image_model"] or ""
            server_config["attachment_image_model"] = aim
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                att_cfg = config.setdefault("attachments", {})
                att_cfg["image_model"] = aim
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["attachment_image_model"] = aim

        # --- Execution mode ---
        if "execution_mode" in body:
            mode = body["execution_mode"]
            if mode not in ("server", "client"):
                self._send_json({"error": "execution_mode must be 'server' or 'client'"}, 400)
                return
            server_config["execution_mode"] = mode
            engine._execution_mode_cache = mode
            engine._execution_mode_cache_time = time.time()
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["execution_mode"] = mode
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["execution_mode"] = mode

        # --- Client proxy tools ---
        if "client_proxy_tools" in body:
            tools_list = body["client_proxy_tools"]
            if not isinstance(tools_list, list):
                self._send_json({"error": "client_proxy_tools must be a list"}, 400)
                return
            server_config["client_proxy_tools"] = tools_list
            engine._client_proxy_tools_cache = set(tools_list) if tools_list else None
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["client_proxy_tools"] = tools_list
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["client_proxy_tools"] = tools_list

        # --- GDPR/PII scanner settings ---
        if "gdpr_scanner" in body:
            gs_in = body["gdpr_scanner"]
            if not isinstance(gs_in, dict):
                self._send_json({"error": "gdpr_scanner must be an object"}, 400)
                return
            gs = server_config.setdefault("gdpr_scanner", {})
            for key in ("enabled", "server_log", "server_block"):
                if key in gs_in:
                    gs[key] = bool(gs_in[key])
            if "default_local_fallback_model" in gs_in:
                mid = str(gs_in["default_local_fallback_model"] or "")
                # Validate: must be a known, enabled, local model (empty = disabled)
                if mid:
                    mcfg = (engine._models_config or {}).get(mid) or {}
                    if not mcfg.get("enabled"):
                        self._send_json({"error": f"default_local_fallback_model: unknown or disabled model '{mid}'"}, 400)
                        return
                    if not engine.is_model_local(mid):
                        self._send_json({"error": f"default_local_fallback_model: '{mid}' is not local"}, 400)
                        return
                gs["default_local_fallback_model"] = mid

            # Category actions — only accept known categories + valid actions.
            if "categories" in gs_in:
                cats_in = gs_in["categories"] or {}
                if not isinstance(cats_in, dict):
                    self._send_json({"error": "gdpr_scanner.categories must be an object"}, 400)
                    return
                valid_cats = set(engine.PII_DEFAULT_CATEGORY_ACTIONS.keys())
                out_cats = {}
                for cat, entry in cats_in.items():
                    if cat not in valid_cats:
                        continue
                    action = entry.get("action") if isinstance(entry, dict) else entry
                    if action not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"categories.{cat}.action must be ignore|warn|block"}, 400)
                        return
                    out_cats[cat] = {"action": action}
                # Merge with defaults for any unset categories so save is complete
                for cat, act in engine.PII_DEFAULT_CATEGORY_ACTIONS.items():
                    out_cats.setdefault(cat, {"action": act})
                gs["categories"] = out_cats

            # Rule overrides — reject unknown rule_ids so typos surface.
            if "rule_overrides" in gs_in:
                ovr_in = gs_in["rule_overrides"] or {}
                if not isinstance(ovr_in, dict):
                    self._send_json({"error": "gdpr_scanner.rule_overrides must be an object"}, 400)
                    return
                out_ovr = {}
                valid_rules = set(engine.PII_RULE_CATEGORIES.keys())
                for rid, act in ovr_in.items():
                    if not act:
                        continue
                    if rid not in valid_rules:
                        self._send_json({"error": f"rule_overrides: unknown rule_id '{rid}'"}, 400)
                        return
                    if act not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"rule_overrides[{rid}] must be ignore|warn|block"}, 400)
                        return
                    out_ovr[rid] = act
                gs["rule_overrides"] = out_ovr

            # Email allowlist — strip/lowercase/dedupe. Accept "x@y.com" and
            # "@y.com" patterns; reject anything with internal whitespace.
            if "email_allowlist" in gs_in:
                al_in = gs_in["email_allowlist"] or []
                if not isinstance(al_in, list):
                    self._send_json({"error": "gdpr_scanner.email_allowlist must be a list"}, 400)
                    return
                cleaned: list[str] = []
                seen = set()
                for e in al_in:
                    if not isinstance(e, str):
                        continue
                    s = e.strip().lower()
                    if not s or " " in s or "\t" in s:
                        continue
                    if "@" not in s:
                        self._send_json({"error": f"email_allowlist: '{e}' must contain '@'"}, 400)
                        return
                    if s in seen:
                        continue
                    seen.add(s)
                    cleaned.append(s)
                gs["email_allowlist"] = cleaned

            engine._invalidate_gdpr_cache()
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["gdpr_scanner"] = gs
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["gdpr_scanner"] = gs

        if not result:
            self._send_json({"error": "No valid fields to update"}, 400)
            return
        result["status"] = "saved"
        self._send_json(result)

    # --- Tools config handlers ---

    def _handle_tools_config_get(self):
        """GET /v1/tools/config — return tool config with fallback values merged and sensitive fields masked."""
        cfg = engine.get_tool_config()
        # Merge fallback values so UI shows what's actually in use
        exa_cfg = cfg.get("exa_search", {})
        if not exa_cfg.get("api_key"):
            env_key = os.environ.get("EXA_API_KEY", "")
            if env_key:
                exa_cfg["api_key"] = env_key
                exa_cfg["_source"] = "environment variable"
            else:
                # Check built-in default (hardcoded in tool function)
                exa_cfg["api_key"] = "97dbd594-f7b4-4866-9a8e-6a297e3df576"
                exa_cfg["_source"] = "built-in default"
        gmail_cfg = cfg.get("gmail", {})
        if not gmail_cfg.get("email") or not gmail_cfg.get("app_password"):
            fb = engine._gmail_config()
            if fb:
                if not gmail_cfg.get("email") and fb.get("email"):
                    gmail_cfg["email"] = fb["email"]
                if not gmail_cfg.get("app_password") and fb.get("app_password"):
                    gmail_cfg["app_password"] = fb["app_password"]
                gmail_cfg["_source"] = "gmail.json"
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

    def _handle_tools_breakdown(self):
        """GET /v1/tools/breakdown?agent=<id> — per-group token cost of tool definitions."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        agent_id = params.get("agent", "main")
        try:
            agent = engine.AgentConfig(agent_id)
        except Exception as e:
            self._send_json({"error": f"Agent not found: {agent_id} ({e})"}, 404)
            return
        prev_agent = getattr(engine._thread_local, "current_agent", None)
        prev_mcp = getattr(engine._thread_local, "mcp_manager", None)
        try:
            engine._thread_local.current_agent = agent
            # Use the live MCP manager so connected MCP servers are measured.
            engine._thread_local.mcp_manager = engine._mcp_manager
            breakdown = engine.get_tool_breakdown(agent_id)
        finally:
            engine._thread_local.current_agent = prev_agent
            engine._thread_local.mcp_manager = prev_mcp
        self._send_json(breakdown)

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

    # --- Hooks handlers ---

    def _handle_hooks_get(self, path: str):
        """GET /v1/agents/{id}/hooks — list hooks for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        try:
            cfg = engine.AgentConfig(agent_id)
            hooks_cfg = cfg.config.get("hooks", {"enabled": False, "timeout": 5000, "scripts": []})
            self._send_json(hooks_cfg)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_hooks_save(self, path: str):
        """POST /v1/agents/{id}/hooks — save hooks config for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        try:
            agent_json_path = os.path.join(engine.AGENTS_DIR, agent_id, "agent.json")
            config = {}
            if os.path.exists(agent_json_path):
                with open(agent_json_path) as f:
                    config = json.load(f)
            config["hooks"] = body
            with open(agent_json_path, "w") as f:
                json.dump(config, f, indent=2)
            # Reload hook runner cache
            with engine._hook_runners_lock:
                engine._hook_runners.pop(agent_id, None)
            self._send_json({"status": "saved", "hooks": body})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- MemPalace handlers ---

    def _handle_mempalace_session_turns(self):
        """GET /v1/mempalace/session-turns?session_id=X — return the set of
        turn_ids currently memorized for this session, parsed from drawer
        source_file prefixes. The UI uses this to grey out menu items that
        would be a no-op (e.g. 'memorize this response' when it's already
        memorized, or 'remove' when nothing was stored)."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        sid = (qs.get("session_id") or [""])[0]
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        turn_ids: set[int] = set()
        legacy_count = 0  # drawers without #turn/<id> suffix
        try:
            mcfg = engine._load_mempalace_config()
            palace_path = mcfg.get("palace_path", "")
            if not palace_path or not os.path.isdir(palace_path):
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            ok, _ = engine._ensure_mempalace_importable()
            if not ok:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            from mempalace.palace import get_collection
            col = get_collection(palace_path, create=False)
            if not col:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            result = col.get(include=["metadatas"])
            prefix = f"session/{sid}"
            for m in result.get("metadatas", []):
                sf = (m.get("source_file") or "")
                if not sf.startswith(prefix):
                    continue
                # Shape: session/<sid> or session/<sid>#turn/<id>[...] or legacy session/<sid>#...
                rest = sf[len(prefix):]
                if rest.startswith("#turn/"):
                    after = rest[len("#turn/"):]
                    tok = after.split("#", 1)[0].split("/", 1)[0]
                    if tok.isdigit():
                        turn_ids.add(int(tok))
                        continue
                legacy_count += 1
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({
            "session_id": sid,
            "turn_ids": sorted(turn_ids),
            "legacy_count": legacy_count,
        })

    def _handle_mempalace_classifier_get(self):
        """GET /v1/mempalace/classifier — return classifier config."""
        mcfg = engine._load_mempalace_config()
        sync_cfg = mcfg.get("chat_sync", {}) or {}
        clf = sync_cfg.get("classifier", {}) or {}
        self._send_json({
            "enabled": clf.get("enabled", False),
            "model": clf.get("model", ""),
            "min_turns": clf.get("min_turns", 0),
            "default_mode": clf.get("default_mode", 0),
            "categories_to_file": clf.get("categories_to_file",
                ["fact", "preference", "decision", "reference"]),
        })

    def _handle_mempalace_classifier_save(self):
        """POST /v1/mempalace/classifier — save classifier config."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            mp = config.setdefault("mempalace", {})
            cs = mp.setdefault("chat_sync", {})
            clf = cs.setdefault("classifier", {})
            if "enabled" in body:
                clf["enabled"] = bool(body["enabled"])
            if "model" in body:
                clf["model"] = str(body["model"])
            if "categories_to_file" in body:
                clf["categories_to_file"] = list(body["categories_to_file"])
            if "min_turns" in body:
                clf["min_turns"] = max(0, int(body["min_turns"]))
            if "default_mode" in body:
                clf["default_mode"] = max(0, min(2, int(body["default_mode"])))
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            engine._mempalace_config_cache = None
            self._send_json({"status": "saved", "classifier": clf})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_mempalace_stats(self):
        """GET /v1/mempalace/stats — palace overview for admin dashboard."""
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            self._send_json({"enabled": False, "error": "MemPalace disabled in config"})
            return
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"enabled": True, "error": f"Palace path not found: {palace_path}"})
            return

        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"enabled": True, "error": err})
            return

        try:
            from mempalace.mcp_server import tool_status, tool_get_taxonomy, tool_list_tunnels, tool_graph_stats, tool_kg_stats
            from mempalace.palace import get_closets_collection

            status = tool_status()
            taxonomy = tool_get_taxonomy()
            tunnels = tool_list_tunnels()
            graph = tool_graph_stats()

            # Closet count
            closet_count = 0
            try:
                closets_col = get_closets_collection(palace_path, create=False)
                if closets_col:
                    closet_count = closets_col.count()
            except Exception:
                pass

            # Knowledge graph stats
            kg = {}
            try:
                kg = tool_kg_stats()
            except Exception:
                pass

            # Chat sync stats from cursor table
            sync_stats = {"synced_sessions": 0, "total_drawers_filed": 0, "last_sync": None}
            try:
                with _db_conn() as conn:
                    row = conn.execute("""
                        SELECT COUNT(*) as cnt,
                               SUM(last_message_id) as total_msgs,
                               MAX(updated_at) as last_update
                        FROM chat_mempalace_sync
                    """).fetchone()
                    if row:
                        sync_stats["synced_sessions"] = row[0] or 0
                        sync_stats["total_drawers_filed"] = row[1] or 0
                        sync_stats["last_sync"] = row[2]
            except Exception:
                pass

            # Mining config summary
            mine_cfg = mcfg.get("mine", {})
            chat_sync_cfg = mcfg.get("chat_sync", {})

            # Palace file size
            palace_size_mb = 0
            try:
                db_path = os.path.join(palace_path, "chroma.sqlite3")
                if os.path.exists(db_path):
                    palace_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
            except Exception:
                pass

            # WAL recent activity (last 100 entries)
            wal_activity = {"total_ops": 0, "recent_ops": [], "ops_by_type": {}}
            try:
                wal_path = os.path.join(os.path.dirname(palace_path), "wal", "write_log.jsonl")
                if os.path.exists(wal_path):
                    lines = []
                    with open(wal_path, "r") as f:
                        for line in f:
                            lines.append(line)
                    wal_activity["total_ops"] = len(lines)
                    for line in lines[-50:]:
                        try:
                            entry = json.loads(line)
                            wal_activity["recent_ops"].append({
                                "timestamp": entry.get("timestamp", ""),
                                "operation": entry.get("operation", ""),
                                "wing": (entry.get("params") or {}).get("wing", ""),
                                "room": (entry.get("params") or {}).get("room", ""),
                            })
                            op = entry.get("operation", "unknown")
                            wal_activity["ops_by_type"][op] = wal_activity["ops_by_type"].get(op, 0) + 1
                        except (json.JSONDecodeError, KeyError):
                            pass
                    wal_activity["recent_ops"] = wal_activity["recent_ops"][-20:]
            except Exception:
                pass

            # Wing breakdown with user isolation info
            wings_detail = {}
            tax = taxonomy.get("taxonomy", {})
            # Build user_id → display_name lookup
            _user_names = {}
            try:
                for u in _auth_mod.AuthDB.list_users():
                    _user_names[u["id"]] = u.get("display_name") or u.get("username") or u["id"]
            except Exception:
                pass
            for wing_name, rooms in tax.items():
                is_user_scoped = "--" in wing_name
                user_id = wing_name.split("--")[0] if is_user_scoped else None
                wings_detail[wing_name] = {
                    "rooms": rooms,
                    "drawer_count": sum(rooms.values()),
                    "room_count": len(rooms),
                    "user_scoped": is_user_scoped,
                    "user_id": user_id,
                    "user_name": _user_names.get(user_id, user_id) if user_id else None,
                }

            # Hall stats from drawer metadata
            halls = {}
            try:
                all_meta = status.get("_all_meta") or []
                if not all_meta:
                    from mempalace.palace import get_collection as _gc
                    _dcol = _gc(palace_path, create=False)
                    if _dcol:
                        _dr = _dcol.get(include=["metadatas"])
                        all_meta = _dr.get("metadatas", [])
                for m in all_meta:
                    h = m.get("hall", "")
                    if not h:
                        continue
                    if h not in halls:
                        halls[h] = {"count": 0, "rooms": {}}
                    halls[h]["count"] += 1
                    r = m.get("room", "")
                    if r:
                        halls[h]["rooms"][r] = halls[h]["rooms"].get(r, 0) + 1
            except Exception:
                pass

            self._send_json({
                "enabled": True,
                "palace_path": palace_path,
                "palace_size_mb": palace_size_mb,
                "total_drawers": status.get("total_drawers", 0),
                "total_closets": closet_count,
                "halls": halls,
                "wings": wings_detail,
                "wing_count": len(wings_detail),
                "room_count": status.get("total_rooms", len(set(r for rooms in tax.values() for r in rooms))),
                "graph": graph,
                "tunnels": tunnels,
                "knowledge_graph": kg,
                "chat_sync": sync_stats,
                "wal": wal_activity,
                "config": {
                    "mine_enabled": mine_cfg.get("enabled", True),
                    "mine_interval_s": mine_cfg.get("interval_seconds", 1800),
                    "mine_sources": len(mine_cfg.get("sources", [])),
                    "chat_sync_enabled": chat_sync_cfg.get("enabled", True),
                    "chat_sync_interval_s": chat_sync_cfg.get("interval_seconds", 60),
                    "chat_sync_build_closets": chat_sync_cfg.get("build_closets", True),
                },
            })
        except Exception as e:
            self._send_json({"enabled": True, "error": f"Failed to gather stats: {type(e).__name__}: {e}"}, 500)

    def _handle_mempalace_drawers(self):
        """GET /v1/mempalace/drawers?wing=X&room=Y — list drawers for treemap drill-down."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        wing = (params.get("wing") or [None])[0]
        room = (params.get("room") or [None])[0]

        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "Palace not found"}, 404)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"error": err}, 500)
            return

        try:
            from mempalace.palace import get_collection, get_closets_collection
            col = get_collection(palace_path, create=False)
            result = col.get(include=["metadatas", "documents"])
            drawers = []
            for did, meta, doc in zip(result["ids"], result["metadatas"], result["documents"]):
                m_wing = meta.get("wing", "")
                m_room = meta.get("room", "")
                if wing and m_wing != wing:
                    continue
                if room and m_room != room:
                    continue
                drawers.append({
                    "id": did,
                    "wing": m_wing,
                    "room": m_room,
                    "hall": meta.get("hall", ""),
                    "source_file": meta.get("source_file", ""),
                    "filed_at": meta.get("filed_at", ""),
                    "added_by": meta.get("added_by", ""),
                    "text": (doc or "")[:300],
                })
            closets = []
            try:
                ccol = get_closets_collection(palace_path, create=False)
                if ccol:
                    cresult = ccol.get(include=["metadatas", "documents"])
                    for cid, cmeta, cdoc in zip(cresult["ids"], cresult["metadatas"], cresult["documents"]):
                        c_wing = cmeta.get("wing", "")
                        c_room = cmeta.get("room", "")
                        if wing and c_wing != wing:
                            continue
                        if room and c_room != room:
                            continue
                        closets.append({
                            "id": cid,
                            "wing": c_wing,
                            "room": c_room,
                            "source_file": cmeta.get("source_file", ""),
                            "drawer_count": cmeta.get("drawer_count", 0),
                            "text": (cdoc or "")[:300],
                        })
            except Exception:
                pass
            self._send_json({"drawers": drawers, "count": len(drawers), "closets": closets})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Context Management handlers ---

    def _handle_context_config_get(self):
        """GET /v1/context/config — return context management configuration."""
        if not engine._context_manager:
            self._send_json(engine._CONTEXT_CONFIG_DEFAULTS)
            return
        self._send_json(engine._context_manager.get_config())

    def _handle_context_config_save(self):
        """POST /v1/context/config — save context management configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No config provided"}, 400)
            return
        if not engine._context_manager:
            engine._context_manager = engine.ContextManager()
        engine._context_manager.save_config(body)
        self._send_json({"status": "saved", "config": engine._context_manager.get_config()})

    def _handle_context_compact(self):
        """POST /v1/context/compact — manually trigger compaction for a session."""
        body = self._read_json()
        session_id = body.get("session_id", "")
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        if self._session_access_check(session_id, require_manage=True) is None:
            return
        session = sessions.get(session_id)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"}, 500)
            return
        try:
            before = engine._estimate_conversation_tokens(session.messages)
            # Force compaction regardless of threshold
            result = engine._context_manager.check_and_compact(
                session.messages, session.id, session.model,
                session.api_key, session.base_url,
                max_tokens=session.max_context,
                force=True,
            )
            with session.lock:
                session.messages = result[0]
            # Persist: mark old messages as compacted, insert new summary messages
            if result[1]:
                try:
                    with _db_conn() as conn:
                        # Mark ALL existing messages as compacted (preserves originals for search)
                        conn.execute(
                            "UPDATE messages SET compacted = 1 WHERE session_id = ? AND (compacted = 0 OR compacted IS NULL)",
                            (session_id,)
                        )
                        # Insert the new compacted message set (summaries + fresh tail)
                        for msg in session.messages:
                            role = msg.get("role", "user")
                            content = msg.get("content", "")
                            c = json.dumps(content) if not isinstance(content, str) else content
                            meta = json.dumps(msg.get("metadata", {})) if msg.get("metadata") else ""
                            conn.execute(
                                "INSERT INTO messages (session_id, role, content, metadata, compacted) VALUES (?, ?, ?, ?, 0)",
                                (session_id, role, c, meta)
                            )
                        conn.commit()
                except Exception as e:
                    print(f"  [WARN] Compact DB persist: {e}", flush=True)
            after = engine._estimate_conversation_tokens(session.messages)
            stats = engine._context_manager.get_stats(session_id)
            self._send_json({
                "status": "compacted" if result[1] else "no_change",
                "before_tokens": before,
                "after_tokens": after,
                "before_pct": int(before / session.max_context * 100) if session.max_context else 0,
                "after_pct": int(after / session.max_context * 100) if session.max_context else 0,
                "stats": stats,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_context_stats(self):
        """GET /v1/context/stats?session_id=X — context stats for a session."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        session_id = (qs.get("session_id") or [""])[0]
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"})
            return
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        stats = engine._context_manager.get_stats(session_id)
        self._send_json(stats)

    def _handle_expand_command(self):
        """POST /v1/commands/expand — expand a custom command template.
        Body: {agent, command, args}
        Returns: {text: "expanded prompt"}
        """
        body = self._read_json()
        agent_id = body.get("agent", "main")
        cmd_name = body.get("command", "")
        cmd_args = body.get("args", "")
        if not cmd_name:
            self._send_json({"error": "command name required"}, 400)
            return
        agent_cfg = engine.AgentConfig(agent_id)
        for cmd in agent_cfg.load_commands():
            if (cmd.get("name", "").lower() == cmd_name.lower() or
                    cmd.get("slug", "").lower() == cmd_name.lower()):
                expanded = engine.AgentConfig.expand_command(cmd, cmd_args)
                self._send_json({"text": expanded, "format": cmd.get("_format", "brain")})
                return
        self._send_json({"error": f"Command '{cmd_name}' not found"}, 404)

    def _handle_settings_commands(self):
        """POST /v1/settings/commands — enable/disable a built-in slash command."""
        body = self._read_json()
        name = body.get("name", "")
        enabled = body.get("enabled", True)
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        disabled = server_config.get("disabled_commands", [])
        if enabled and name in disabled:
            disabled.remove(name)
        elif not enabled and name not in disabled:
            disabled.append(name)
        server_config["disabled_commands"] = disabled
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            config["disabled_commands"] = disabled
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass
        self._send_json({"status": "ok", "disabled_commands": disabled})

    def _handle_restart(self):
        """POST /v1/restart — restart the server process."""
        self._send_json({"status": "restarting"})
        # Schedule restart after response is sent
        def do_restart():
            time.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do_restart, daemon=True).start()

    # --- Chat answer handler (interactive AskUserQuestion) ---

    def _handle_chat_answer(self):
        """POST /v1/chat/answer — deliver a user answer to a pending ask_user tool call.

        Body shapes:
          {session_id, answer: "..."}                             # single question
          {session_id, answers: {"<question>": "<answer>", ...}}  # batch
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        answer = body.get("answer")
        answers = body.get("answers")
        if not session_id or (answer is None and not isinstance(answers, dict)):
            self._send_json({"error": "session_id and answer/answers are required"}, 400)
            return
        if self._session_access_check(session_id) is None:
            return
        # Normalize answers dict values to strings
        if isinstance(answers, dict):
            answers = {str(k): str(v) for k, v in answers.items() if v is not None}
        from claude_cli import deliver_ask_user_answer
        ok = deliver_ask_user_answer(
            session_id,
            answer=str(answer) if answer is not None else None,
            answers=answers if isinstance(answers, dict) and answers else None,
        )
        if not ok:
            self._send_json({"error": "no pending question for this session"}, 404)
            return
        self._send_json({"delivered": True, "session_id": session_id})

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

    def _handle_workers_list(self):
        """GET /v1/workers — list workers, optionally filtered by session_id."""
        from execution import get_worker_registry
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        session_id = qs.get("session_id", [None])[0]
        registry = get_worker_registry()
        if session_id:
            workers = registry.list_session(session_id)
        else:
            workers = list(registry._workers.values())
        self._send_json({"workers": [registry.to_status_dict(w) for w in workers]})

    def _handle_workers_recent(self):
        """GET /v1/workers/recent — all workers across sessions (admin view)."""
        from execution import get_worker_registry
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        limit = int(qs.get("limit", [50])[0])
        registry = get_worker_registry()
        with registry._lock:
            all_workers = list(registry._workers.values())
        all_workers.sort(key=lambda w: w.started_at or 0, reverse=True)
        all_workers = all_workers[:limit]
        result = []
        for w in all_workers:
            d = registry.to_status_dict(w)
            d["session_id"] = w.session_id
            d["agent_id"] = w.agent_id
            d["duration"] = w.duration
            result.append(d)
        self._send_json({"workers": result, "total": len(registry._workers)})

    def _handle_worker_answer(self, path):
        """POST /v1/workers/{id}/answer — deliver answer to a worker question."""
        from execution import get_worker_registry
        parts = path.split("/")
        worker_id = parts[3] if len(parts) >= 5 else ""
        body = self._read_json_body()
        if not body:
            self._send_json({"error": "Missing body"}, 400)
            return
        answer = body.get("answer", "")
        if not answer:
            self._send_json({"error": "Missing 'answer' field"}, 400)
            return
        ok = get_worker_registry().answer(worker_id, answer)
        if not ok:
            self._send_json({"error": f"Worker '{worker_id}' not waiting for answer"}, 400)
            return
        self._send_json({"ok": True, "worker_id": worker_id})

    def _handle_nodes_list(self):
        """GET /v1/nodes — list all nodes with status."""
        nodes = []
        with _node_lock:
            for token, info in _node_registry.items():
                cfg = info.get("config", {})
                nodes.append({
                    "name": info["name"],
                    "description": cfg.get("description", ""),
                    "token": token,
                    "status": info["status"],
                    "paused": cfg.get("paused", False),
                    "hostname": info.get("hostname", ""),
                    "os": info.get("os", ""),
                    "tags": cfg.get("tags", []),
                    "allowed_tools": cfg.get("allowed_tools", []),
                    "max_concurrent": cfg.get("max_concurrent", 5),
                    "command_timeout": cfg.get("command_timeout", 300),
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
            install_cmd = f"python3 node.py --install --server http://SERVER_IP:{port} --token {token} --name {name}"
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
        """GET /v1/files/preview?path=<absolute_path>&lines=100 — return file content for preview."""
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
            name = os.path.basename(resolved)
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            image_exts = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"}
            office_exts = {"pdf", "docx", "xlsx", "pptx", "csv"}
            if ext in image_exts:
                self._send_json({
                    "path": resolved, "name": name, "size": size,
                    "type": "image", "ext": ext,
                })
                return
            if ext in office_exts:
                try:
                    if ext == "pdf":
                        content = engine.DocumentParser.parse_pdf(resolved)
                    elif ext == "docx":
                        content = engine.DocumentParser.parse_docx(resolved)
                    elif ext in ("xlsx", "xls"):
                        content = engine.DocumentParser.parse_xlsx(resolved)
                    elif ext == "pptx":
                        content = engine.DocumentParser.parse_pptx(resolved)
                    elif ext == "csv":
                        with open(resolved, "r", errors="replace") as f:
                            content = f.read(50 * 1024)
                    else:
                        content = ""
                    all_lines = content.splitlines()
                    truncated = len(all_lines) > 200
                    self._send_json({
                        "path": resolved, "name": name, "size": size,
                        "type": "document", "ext": ext,
                        "content": "\n".join(all_lines[:200]), "truncated": truncated,
                    })
                except Exception as e:
                    self._send_json({"error": f"Could not parse {ext.upper()}: {e}"}, 500)
                return
            # Plain text / code
            max_bytes = 50 * 1024
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
                "path": resolved, "name": name, "size": size,
                "type": "text",
                "content": "".join(lines), "truncated": truncated,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Code Mode Endpoints ──

    def _handle_file_tree(self):
        """GET /v1/files/tree?path=<dir>&depth=2 — return directory tree for Code mode."""
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(self.path).query)
        dir_path = unquote(qs.get("path", [""])[0])
        max_depth = int(qs.get("depth", ["2"])[0])
        # Empty path defaults to the user's home dir, so the folder picker
        # doesn't need to know where to start.
        if not dir_path:
            dir_path = os.path.expanduser("~")
        else:
            dir_path = os.path.expanduser(dir_path)
        if not os.path.isdir(dir_path):
            self._send_json({"error": "Invalid or missing directory path"}, 400)
            return

        IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
                  ".mypy_cache", ".pytest_cache", ".DS_Store", ".claude", "dist", "build"}

        def _scan(base, depth=0):
            items = []
            try:
                entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                return items
            for entry in entries:
                if entry.name in IGNORE or entry.name.startswith("."):
                    continue
                node = {"name": entry.name, "path": entry.path}
                if entry.is_dir():
                    node["type"] = "dir"
                    if depth < max_depth:
                        node["children"] = _scan(entry.path, depth + 1)
                    else:
                        node["children"] = []
                        node["truncated"] = True
                else:
                    node["type"] = "file"
                    try:
                        node["size"] = entry.stat().st_size
                    except OSError:
                        node["size"] = 0
                items.append(node)
            return items

        tree = _scan(dir_path)
        self._send_json({"path": dir_path, "tree": tree})

    # ── Artifact Endpoints ──

    def _handle_artifacts_list(self):
        """GET /v1/artifacts?session_id=X — list artifacts for a session."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session_id = qs.get("session_id", [""])[0]
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        artifacts = ChatDB.get_artifacts(session_id)
        self._send_json({"artifacts": artifacts})

    def _handle_artifacts_browse(self):
        """GET /v1/artifacts/browse?agent_id=X&limit=N&source=chat|scheduled
        — browse all artifacts across sessions, tagged by source so the UI
        can split the view. Scheduled-task artifacts are identified by
        session_id matching `sched-<run_id>` (set by the scheduler's
        synthetic session context)."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get("agent_id", [None])[0]
        limit = int(qs.get("limit", ["100"])[0])
        source_filter = qs.get("source", [None])[0]  # chat | scheduled | None
        artifacts = ChatDB.get_all_artifacts(agent_id=agent_id, limit=limit)

        # Enrich: source tag + schedule-run summary for scheduled artifacts.
        # Batch-resolve run rows so we don't hit the scheduler DB per-artifact.
        run_ids_needed = set()
        for a in artifacts:
            sid = a.get("session_id") or ""
            if sid.startswith("sched-"):
                a["source"] = "scheduled"
                try:
                    a["run_id"] = int(sid.split("-", 1)[1])
                    run_ids_needed.add(a["run_id"])
                except (ValueError, IndexError):
                    a["run_id"] = None
            else:
                a["source"] = "chat"
                a["run_id"] = None

        run_map: dict = {}
        if run_ids_needed and engine._scheduler:
            for rid in run_ids_needed:
                row = engine._scheduler.get_run(rid)
                if row:
                    run_map[rid] = {
                        "run_id": rid,
                        "schedule_name": row.get("schedule_name"),
                        "status": row.get("status"),
                        "started_at": row.get("started_at"),
                    }
        for a in artifacts:
            if a.get("run_id") in run_map:
                a["schedule_run"] = run_map[a["run_id"]]

        if source_filter in ("chat", "scheduled"):
            artifacts = [a for a in artifacts if a.get("source") == source_filter]

        # Fetch text preview for each text-based artifact
        binary_types = {"image", "document"}
        for a in artifacts:
            if a.get("type") not in binary_types:
                preview = ChatDB.get_artifact_preview(a["id"], max_chars=300)
                a["preview"] = preview
            else:
                a["preview"] = None
        self._send_json({"artifacts": artifacts})

    def _handle_artifact_content(self, path):
        """GET /v1/artifacts/<id>/content?version=N — get artifact version content."""
        from urllib.parse import urlparse, parse_qs
        import base64
        parts = path.split("/")
        # /v1/artifacts/<id>/content
        artifact_id = parts[3] if len(parts) >= 5 else ""
        qs = parse_qs(urlparse(self.path).query)
        version = qs.get("version", [None])[0]

        artifact = ChatDB.get_artifact(artifact_id)
        if not artifact:
            self._send_json({"error": "Artifact not found"}, 404)
            return

        ver_data = ChatDB.get_artifact_content(artifact_id, version)
        if not ver_data:
            self._send_json({"error": "Version not found"}, 404)
            return

        content_raw = ver_data["content"]
        is_binary = artifact["type"] in ("image", "document")

        if content_raw is None:
            # Disk-only fallback (file was > 5MB)
            try:
                with open(artifact["path"], "rb") as f:
                    content_raw = f.read()
            except Exception:
                self._send_json({"error": "Content not available"}, 404)
                return

        if is_binary:
            content_str = base64.b64encode(content_raw if isinstance(content_raw, bytes) else content_raw.encode()).decode()
            encoding = "base64"
        else:
            content_str = content_raw.decode("utf-8", errors="replace") if isinstance(content_raw, bytes) else content_raw
            encoding = "text"

        self._send_json({
            "artifact_id": artifact_id,
            "name": artifact["name"],
            "type": artifact["type"],
            "version": ver_data["version"],
            "content": content_str,
            "encoding": encoding,
            "size": ver_data["size"],
        })

    def _handle_artifact_download(self, path):
        """GET /v1/artifacts/<id>/download?version=N — download artifact content."""
        from urllib.parse import urlparse, parse_qs
        parts = path.split("/")
        artifact_id = parts[3] if len(parts) >= 5 else ""
        qs = parse_qs(urlparse(self.path).query)
        version = qs.get("version", [None])[0]

        artifact = ChatDB.get_artifact(artifact_id)
        if not artifact:
            self._send_json({"error": "Artifact not found"}, 404)
            return

        filename = artifact["name"]
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        content_types = {
            "md": "text/markdown", "txt": "text/plain", "py": "text/x-python",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "html": "text/html", "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "js": "application/javascript", "ts": "text/typescript",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "svg": "image/svg+xml",
        }
        ct = content_types.get(ext, "application/octet-stream")

        # If no version specified, serve disk file
        if not version:
            try:
                with open(artifact["path"], "rb") as f:
                    data = f.read()
            except Exception:
                self._send_json({"error": "File not found on disk"}, 404)
                return
        else:
            ver_data = ChatDB.get_artifact_content(artifact_id, version)
            if not ver_data or ver_data["content"] is None:
                self._send_json({"error": "Version content not available"}, 404)
                return
            data = ver_data["content"] if isinstance(ver_data["content"], bytes) else ver_data["content"].encode()

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

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
            "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf",
        }
        ct = content_types.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        if ext == "html":
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        elif ext in ("woff2", "woff", "ttf"):
            # Content-addressable file names — safe to cache long.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
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


def _index_chat_transcript(session):
    """Store chat transcript as QMD-indexed .md chunks for semantic search.
    Only indexes non-trivial chats (>=4 messages, not note sessions)."""
    if len(session.messages) < 4:
        return
    # Skip note-editing sessions
    if getattr(session, 'note_context', None):
        return
    # Skip incognito sessions
    if getattr(session, 'status', '') == 'incognito':
        return

    agent_id = session.agent_id
    session_id = session.id

    # Build transcript text
    lines = []
    for m in session.messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str) and content:
            lines.append(f"**{role}**: {content}")
        elif isinstance(content, list):
            # Handle multi-part content (text blocks)
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            if text_parts:
                lines.append(f"**{role}**: {' '.join(text_parts)}")

    if not lines:
        return

    transcript = "\n\n".join(lines)

    # Store as chunked .md files in agents/{agent}/chats-indexed/
    chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
    os.makedirs(chats_dir, exist_ok=True)

    # Use DocumentChunker to split into manageable chunks
    chunks = engine.DocumentChunker.chunk(transcript, chunk_size=2000, chunk_overlap=300)
    if not chunks:
        return

    # Remove old chunks for this session
    prefix = f"chat-{session_id}"
    try:
        for existing in os.listdir(chats_dir):
            if existing.startswith(prefix) and existing.endswith(".md"):
                os.remove(os.path.join(chats_dir, existing))
    except OSError:
        pass

    # Write new chunks
    title = session.title or "Untitled chat"
    summary = session.summary or ""
    import datetime as _dt_idx
    now_iso = _dt_idx.datetime.now().isoformat()

    for chunk in chunks:
        idx = chunk["index"]
        fname = f"{prefix}-{idx:03d}.md"
        fpath = os.path.join(chats_dir, fname)

        fm_lines = ["---"]
        fm_lines.append(f'name: "{engine._yaml_escape(f"{title} (part {idx+1}/{chunk["total"]})")}"')
        fm_lines.append('type: chat_transcript')
        fm_lines.append(f'description: "{engine._yaml_escape(summary)}"')
        fm_lines.append(f'session_id: {session_id}')
        fm_lines.append(f'agent: {agent_id}')
        fm_lines.append(f'chunk_index: {idx}')
        fm_lines.append(f'total_chunks: {chunk["total"]}')
        fm_lines.append(f'created_at: "{now_iso}"')
        if session.project:
            fm_lines.append(f'project: "{engine._yaml_escape(session.project)}"')
        fm_lines.append("---")
        fm_lines.append("")

        try:
            with open(fpath, "w") as f:
                f.write("\n".join(fm_lines) + "\n" + chunk["text"])
        except OSError:
            continue

        # Entity extraction for knowledge graph participation
        try:
            entities = engine._extract_entities(chunk["text"])
            if entities:
                engine._update_entity_index(agent_id, fname, entities)
        except Exception:
            pass

    # Trigger QMD reindex for this agent
    engine._qmd_debounced_embed(agent_id)


def _cleanup_chat_index(session_id: str, agent_id: str):
    """Remove indexed transcript files when a session is deleted."""
    chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
    if not os.path.isdir(chats_dir):
        return
    prefix = f"chat-{session_id}"
    removed = False
    try:
        for fname in os.listdir(chats_dir):
            if fname.startswith(prefix) and fname.endswith(".md"):
                os.remove(os.path.join(chats_dir, fname))
                removed = True
    except OSError:
        pass
    if removed:
        engine._qmd_debounced_embed(agent_id)


def _generate_chat_summary(session):
    """Generate a short LLM summary of a chat session for sidebar display."""
    if not engine._delegate_api_key or len(session.messages) < 2:
        return
    # Set thread-local context for this background thread
    engine._thread_local.current_agent = session.agent
    engine._thread_local.memory_store = None  # No memory injection — summary must be based only on chat content
    # Build a condensed view of the conversation (first + last few messages)
    msgs = session.messages
    sample = []
    for m in msgs[:3]:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Extract text from multipart content blocks
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(parts)
        if isinstance(content, str) and content.strip():
            sample.append(f"[{role}] {content[:200]}")
    if len(msgs) > 3:
        for m in msgs[-2:]:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(parts)
            if isinstance(content, str) and content.strip():
                sample.append(f"[{role}] {content[:200]}")

    if not sample:
        return  # No text content to summarize

    prompt = (
        "Summarize this conversation in ONE short sentence (max 60 chars). "
        "Focus on the topic/task, not greetings. Output ONLY the summary, nothing else. "
        "Base your summary ONLY on the conversation content below.\n\n"
        + "\n".join(sample)
    )
    try:
        # Use cheapest model
        model = None
        if engine._models_config:
            for mid, cfg in engine._models_config.items():
                if cfg.get("enabled", True) and "haiku" in mid.lower():
                    model = mid
                    break
            if not model:
                for mid, cfg in sorted(engine._models_config.items(), key=lambda x: x[1].get("cost_input", 999)):
                    if cfg.get("enabled", True):
                        model = mid
                        break
        if not model:
            return

        # GDPR auto-fallback: chat content may contain PII and the summariser
        # model is usually a cheap cloud Haiku. Reroute to the local fallback
        # when findings exist. In hard-block mode without a local route, skip
        # the summary — the session just keeps its existing/derived title.
        try:
            model = engine.gdpr_pick_model_for_background(
                model, sample, purpose="chat_summary")
        except engine.GDPRBlockedError:
            return
        except Exception:
            pass

        result = engine._run_delegate(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt="Output only a brief summary sentence. No quotes, no prefix.",
            memory_store=None,
            inference_params={"max_tokens": 80, "temperature": 0.1},
        )
        if result and not result.startswith("Delegation error") and "There's an issue with the selected model" not in result:
            summary = result.strip().strip('"').strip("'")[:80]
            with session.lock:
                session.summary = summary
            # Persist to DB
            ChatDB.save_session(session.id, session.agent_id, session.model,
                                session.title, session.status, session.created_at,
                                session.last_active, session.project or "", summary)
    except Exception:
        pass
    finally:
        engine._thread_local.current_agent = None
        engine._thread_local.memory_store = None


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
    parser.add_argument("-m", "--model", default=provider.get("default_model", ""))
    parser.add_argument("--max-context", type=int, default=file_config.get("max_context", 131072))
    args = parser.parse_args()

    # Store all providers and settings
    server_config["disabled_commands"] = file_config.get("disabled_commands", [])
    server_config["providers"] = providers
    server_config["api_key"] = args.api_key
    server_config["base_url"] = args.base_url
    server_config["default_model"] = args.model
    server_config["max_context"] = args.max_context
    server_config["port"] = args.port
    server_config["telegram_enabled"] = file_config.get("telegram", {}).get("enabled", True)
    attachments_cfg = file_config.get("attachments", {})
    server_config["attachment_image_model"] = attachments_cfg.get("image_model", "")
    server_config["execution_mode"] = file_config.get("execution_mode", "server")
    server_config["client_proxy_tools"] = file_config.get("client_proxy_tools", engine._CLIENT_PROXY_TOOLS_DEFAULT)
    server_config["gdpr_scanner"] = file_config.get("gdpr_scanner", {}) or {}

    # Initialize models config
    existing_models = file_config.get("models")
    deleted_models = file_config.get("deleted_models", [])
    if providers:
        synced = engine.init_models_config(providers, existing_models,
                                           deleted_models=deleted_models)
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

    # Initialize auth system
    _auth_mod.init_auth(file_config)
    if _auth_mod.auth_enabled():
        print(f"Auth: enabled (registration: {'open' if _auth_mod.registration_enabled() else 'closed'})")
    else:
        print("Auth: disabled (single-user mode)")

    exec_mode = server_config.get("execution_mode", "server")
    if exec_mode == "client":
        print(f"Execution mode: CLIENT (LLM calls + web tools proxied through browser)")
    else:
        print(f"Execution mode: server (default)")

    # Initialize engine globals
    engine._delegate_api_key = args.api_key
    engine._delegate_base_url = args.base_url
    engine._delegate_fallback_model = args.model

    # Start scheduler
    engine._scheduler = engine.Scheduler()
    engine._scheduler.start()

    # Initialize lossless context manager
    engine._context_manager = engine.ContextManager()
    print(f"Context manager: {engine.CONTEXT_DB} (lossless, {'enabled' if engine._context_manager.get_config().get('enabled') else 'disabled'})")

    # Initialize cost tracking and rate limiting
    engine._cost_tracker = engine.CostTracker()
    engine._rate_limiter = engine.RateLimiter()
    engine._quota_manager = engine.QuotaManager()
    print(f"Cost tracking: {engine.COST_DB}")
    _q_cfg = engine._quota_manager.get_config()
    print(f"Quotas: {'enabled' if _q_cfg.get('enabled') else 'disabled'} "
          f"({_q_cfg.get('billing_cycle')}, enforce_red={_q_cfg.get('enforce_red')})")

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

    # Initialize shared MCP manager (singleton used by all request handlers + Web UI)
    engine._mcp_manager = engine.MCPManager()
    main_mcp = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
    mcp_count = engine._mcp_manager.load_config(main_mcp)
    print(f"MCP: loaded {mcp_count} server(s) from mcp.json")

    # Backfill: index any unindexed chat transcripts (runs once at startup)
    def _cleanup_orphaned_chat_indexes():
        """Remove chats-indexed files for sessions that no longer exist in the DB."""
        try:
            with _db_conn() as conn:
                existing_sids = {row[0] for row in conn.execute("SELECT id FROM sessions")}
            for agent_dir in os.listdir(engine.AGENTS_DIR):
                chats_dir = os.path.join(engine.AGENTS_DIR, agent_dir, "chats-indexed")
                if not os.path.isdir(chats_dir):
                    continue
                removed = 0
                for fname in os.listdir(chats_dir):
                    if not fname.startswith("chat-") or not fname.endswith(".md"):
                        continue
                    sid = fname.split("-", 1)[1].rsplit("-", 1)[0]
                    if sid not in existing_sids:
                        try:
                            os.remove(os.path.join(chats_dir, fname))
                            removed += 1
                        except OSError:
                            pass
                if removed:
                    print(f"Chat index cleanup: removed {removed} orphaned files for {agent_dir}", flush=True)
                    engine._qmd_debounced_embed(agent_dir)
        except Exception as e:
            print(f"[WARN] Chat index orphan cleanup: {e}", flush=True)

    def _backfill_chat_index():
        """Find sessions with 4+ messages that have no indexed transcript files and index them."""
        time.sleep(10)  # let server fully start
        _cleanup_orphaned_chat_indexes()
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT s.id, s.agent_id, "
                    "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as msg_count "
                    "FROM sessions s WHERE s.status != 'incognito'"
                ).fetchall()
            indexed = 0
            for r in rows:
                if r["msg_count"] < 4:
                    continue
                sid = r["id"]
                agent_id = r["agent_id"]
                chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
                prefix = f"chat-{sid}"
                # Skip if already indexed
                if os.path.isdir(chats_dir):
                    if any(f.startswith(prefix) for f in os.listdir(chats_dir)):
                        continue
                # Load session into memory to index
                session = sessions.get(sid)
                if not session:
                    # Load from DB
                    info = ChatDB.get_session_info(sid)
                    if not info:
                        continue
                    session = ChatSession(info["agent_id"], info.get("model", ""), info.get("project", ""))
                    session.id = sid
                    session.title = info.get("title", "")
                    session.summary = info.get("summary", "")
                    session.status = info.get("status", "active")
                    # Load messages
                    with _db_conn() as conn2:
                        conn2.row_factory = sqlite3.Row
                        msgs = conn2.execute(
                            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                            (sid,)
                        ).fetchall()
                    with session.lock:
                        session.messages = [{"role": m["role"], "content": m["content"]} for m in msgs]
                try:
                    _index_chat_transcript(session)
                    indexed += 1
                except Exception:
                    pass
            if indexed:
                print(f"Chat index backfill: indexed {indexed} sessions", flush=True)
        except Exception as e:
            print(f"[WARN] Chat index backfill: {e}", flush=True)

    threading.Thread(target=_backfill_chat_index, daemon=True, name="chat_index_backfill").start()

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

    # MemPalace migration: QMD index keeper thread disabled; memory layer is now
    # mempalace MCP. The _qmd_index_keeper() function above is dead code and will
    # be removed in C8 cleanup.

    # Start server
    server = ThreadingHTTPServer((args.host, args.port), BrainAgentHandler)
    print(f"Brain Agent Server v{engine.VERSION}")
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"API: {args.base_url}")
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


    # File change watcher for SDK — detect file writes that bypass _after_file_write
    _file_mtimes: dict[str, float] = {}

    def _file_change_watcher():
        """Poll agent dirs for .md file changes and trigger post-write pipeline.
        Catches files created/modified by the SDK subprocess which bypasses _after_file_write."""
        import glob as _glob
        # Initial scan
        for agent_id in engine.list_agents():
            agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
            for path in _glob.glob(os.path.join(agent_dir, "*.md")):
                try:
                    _file_mtimes[path] = os.path.getmtime(path)
                except OSError:
                    pass
        while True:
            time.sleep(10)  # Check every 10 seconds
            try:
                for agent_id in engine.list_agents():
                    agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
                    for path in _glob.glob(os.path.join(agent_dir, "*.md")):
                        try:
                            mtime = os.path.getmtime(path)
                        except OSError:
                            continue
                        prev = _file_mtimes.get(path)
                        if prev is None or mtime > prev:
                            _file_mtimes[path] = mtime
                            if prev is not None:
                                # File was modified — trigger post-write pipeline
                                try:
                                    engine._after_file_write(path, action="modified", agent_id=agent_id)
                                except Exception:
                                    pass
            except Exception:
                pass

    threading.Thread(target=_file_change_watcher, daemon=True, name="file-change-watcher").start()

    # MemPalace background miner — fully autonomous artifact ingestion.
    # Three rules (per user 2026-04-25):
    #   1. Scheduled-task artifacts: only output-role files (skip intermediate
    #      scripts/json/etc), regardless of any toggle. Never mines sched chat
    #      content (no session/messages rows exist for sched-* runs).
    #   2. Chat-originated artifacts: only when parent session has
    #      save_to_memory > 0. When ON, mine all files in that folder.
    #   3. mempalace.yaml is managed automatically per agent — server creates
    #      and refreshes it; user never touches it.
    _MEMPALACE_YAML_MARKER = "# managed by brain-agent server.py — do not edit\n"

    def _mempalace_yaml_for_artifacts(wing: str) -> str:
        # Default-room "general" satisfies miner.detect_room fallback. Rooms
        # field must be a list per miner spec, even if minimal.
        return (
            _MEMPALACE_YAML_MARKER
            + "wing: " + wing + "\n"
            + "rooms:\n"
            + "  - name: artifacts\n"
            + "    description: Files produced during chats and tasks\n"
            + "    keywords: [report, output, document]\n"
            + "  - name: general\n"
            + "    description: Fallback room\n"
            + "    keywords: [general]\n"
        )

    def _ensure_mempalace_yaml(project_dir: str, wing: str) -> bool:
        """Write a mempalace.yaml if missing or if the brain-managed marker is gone.
        Returns True if the file is present (existing or freshly written)."""
        try:
            yaml_path = os.path.join(project_dir, "mempalace.yaml")
            existing_ok = False
            if os.path.isfile(yaml_path):
                try:
                    with open(yaml_path, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(200)
                    if head.startswith(_MEMPALACE_YAML_MARKER) or "wing:" in head:
                        existing_ok = True
                except Exception:
                    existing_ok = False
            if existing_ok:
                return True
            os.makedirs(project_dir, exist_ok=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(_mempalace_yaml_for_artifacts(wing))
            return True
        except Exception as e:
            print(f"[mempalace-miner] failed to write yaml in {project_dir}: {e}", flush=True)
            return False

    def _purge_orphan_chroma_queue(palace_path: str):
        """One-shot cleanup: remove embeddings_queue rows whose target segment
        has no max_seq_id bootstrap (= compactor never saw them, never will).
        Safe — these have been dead since 2026-04-19 and don't affect new writes."""
        try:
            db_path = os.path.join(palace_path, "chroma.sqlite3")
            if not os.path.isfile(db_path):
                return
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            # Find segments without a max_seq_id row — those are the orphans
            orphan_segments = [r[0] for r in cur.execute(
                "SELECT s.id FROM segments s "
                "LEFT JOIN max_seq_id m ON m.segment_id = s.id "
                "WHERE m.seq_id IS NULL"
            ).fetchall()]
            if not orphan_segments:
                con.close()
                return
            # Each segment belongs to a collection; queue rows reference the
            # collection via topic suffix (last 36 chars = collection UUID).
            collection_uuids = [r[0] for r in cur.execute(
                "SELECT DISTINCT collection FROM segments WHERE id IN ({})".format(
                    ",".join("?" * len(orphan_segments))
                ),
                orphan_segments,
            ).fetchall()]
            total = 0
            for cuid in collection_uuids:
                # Only purge rows older than 24h — leave today's writes alone
                cutoff = time.time() - 86400
                n = cur.execute(
                    "DELETE FROM embeddings_queue "
                    "WHERE topic LIKE ? AND strftime('%s', created_at) < ?",
                    (f"%{cuid}", str(int(cutoff))),
                ).rowcount
                total += n
            con.commit()
            con.close()
            if total:
                print(f"[mempalace-miner] purged {total} stale queue row(s) "
                      f"(orphan segments: {len(orphan_segments)})", flush=True)
        except Exception as e:
            print(f"[mempalace-miner] queue cleanup failed: {type(e).__name__}: {e}", flush=True)

    def _list_chat_artifact_folders():
        """Iterate per-agent artifact folders. Yields tuples
        (agent_id, folder_path, folder_name, kind) where kind is 'sched' or 'chat'."""
        try:
            for agent_id in os.listdir(engine.AGENTS_DIR):
                agent_root = os.path.join(engine.AGENTS_DIR, agent_id)
                if not os.path.isdir(agent_root):
                    continue
                if agent_id.startswith(".") or agent_id == "main" and False:
                    pass
                artifacts_root = os.path.join(agent_root, "artifacts")
                if not os.path.isdir(artifacts_root):
                    continue
                for folder_name in os.listdir(artifacts_root):
                    folder_path = os.path.join(artifacts_root, folder_name)
                    if not os.path.isdir(folder_path):
                        continue
                    # Folder names are <date>_<sid_prefix>. sched-task folders
                    # use the sched-* prefix as the sid (claude_cli.py L11319).
                    parts = folder_name.split("_", 1)
                    sid_part = parts[1] if len(parts) > 1 else parts[0]
                    kind = "sched" if sid_part.startswith("sched-") else "chat"
                    yield (agent_id, folder_path, folder_name, kind)
        except Exception as e:
            print(f"[mempalace-miner] discovery error: {type(e).__name__}: {e}", flush=True)

    def _file_text_or_none(path: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
        """Read a file as text (utf-8, errors=replace). Returns None for
        binary blobs we can't reasonably treat as text."""
        try:
            size = os.path.getsize(path)
            if size <= 0 or size > max_bytes:
                return None
            with open(path, "rb") as f:
                blob = f.read()
            # Heuristic: skip if >5% null bytes
            if blob.count(b"\x00") > max(1, len(blob) // 20):
                return None
            return blob.decode("utf-8", errors="replace")
        except Exception:
            return None

    def _mempalace_miner_loop():
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            print("[mempalace-miner] disabled (mempalace.enabled = false)", flush=True)
            return
        mine_cfg = mcfg.get("mine", {}) or {}
        if not mine_cfg.get("enabled", True):
            print("[mempalace-miner] disabled (mempalace.mine.enabled = false)", flush=True)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            print(f"[mempalace-miner] {err}", flush=True)
            return
        try:
            from mempalace import miner as mp_miner
            from mempalace.mcp_server import tool_add_drawer
        except Exception as e:
            print(f"[mempalace-miner] import failed: {e}", flush=True)
            return

        palace_path = mcfg.get("palace_path", "")
        interval = int(mine_cfg.get("interval_seconds", 1800))
        respect_git = bool(mine_cfg.get("respect_gitignore", True))

        # One-shot: purge orphaned queue rows from earlier runs
        _purge_orphan_chroma_queue(palace_path)

        # Small startup delay so we don't compete with initial provider probes.
        time.sleep(15)

        intermediate_exts = engine._ARTIFACT_INTERMEDIATE_EXTS

        while True:
            try:
                mcfg2 = engine._load_mempalace_config()
                if not mcfg2.get("enabled", True):
                    return
                mine2 = mcfg2.get("mine") or {}
                # Build a session_id → save_to_memory map once per cycle
                memory_modes = ChatDB.session_memory_modes() or {}

                drawers_filed = 0
                folders_seen = 0
                folders_skipped_chat = 0
                folders_sched = 0

                for agent_id, folder_path, folder_name, kind in _list_chat_artifact_folders():
                    folders_seen += 1
                    # Reconstruct session_id from folder name. For chat folders
                    # we only have the first 8 chars of the session_id; do a
                    # prefix lookup. For sched-* folders the prefix already is
                    # the full sched-<run_id> form.
                    parts = folder_name.split("_", 1)
                    sid_prefix = parts[1] if len(parts) > 1 else parts[0]

                    if kind == "sched":
                        folders_sched += 1
                        # Sched: file only output-role files (extension-based)
                        for fname in os.listdir(folder_path):
                            fpath = os.path.join(folder_path, fname)
                            if not os.path.isfile(fpath):
                                continue
                            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                            if ext in intermediate_exts:
                                continue
                            text = _file_text_or_none(fpath)
                            if not text:
                                continue
                            wing = f"{agent_id}_artifacts"
                            try:
                                res = tool_add_drawer(
                                    wing=wing,
                                    room="artifacts",
                                    content=text[:8000],
                                    source_file=f"session/{sid_prefix}#artifact/{fname}",
                                    added_by="brain-miner-sched",
                                )
                                if isinstance(res, dict) and res.get("success") \
                                   and res.get("reason") != "already_exists":
                                    drawers_filed += 1
                            except Exception as ex:
                                print(f"[mempalace-miner] sched add_drawer failed "
                                      f"{fname}: {ex}", flush=True)
                        continue

                    # Chat: gate on the parent session's save_to_memory toggle.
                    full_sid = ChatDB.session_id_for_prefix(sid_prefix)
                    mem_mode = memory_modes.get(full_sid, 0) if full_sid else 0
                    if mem_mode <= 0:
                        folders_skipped_chat += 1
                        continue

                    # Memory ON: ensure yaml + run miner over the folder.
                    wing = f"{agent_id}_artifacts"
                    if not _ensure_mempalace_yaml(folder_path, wing):
                        continue
                    buf = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(buf):
                            mp_miner.mine(
                                project_dir=folder_path,
                                palace_path=palace_path,
                                wing_override=wing,
                                agent="brain-miner-chat",
                                respect_gitignore=False,
                            )
                        out = buf.getvalue().strip()
                        for line in out.splitlines():
                            s = line.strip()
                            if s.startswith("Drawers filed"):
                                # "Drawers filed: N" — pull the integer
                                try:
                                    drawers_filed += int(s.split(":")[-1].strip().split()[0])
                                except Exception:
                                    pass
                                break
                    except SystemExit:
                        # miner.load_config calls sys.exit on bad yaml — should not
                        # happen now that we always write one, but be defensive.
                        pass
                    except Exception as e:
                        print(f"[mempalace-miner] chat folder {folder_name}: "
                              f"{type(e).__name__}: {e}", flush=True)

                print(f"[mempalace-miner] cycle: filed={drawers_filed} folders={folders_seen} "
                      f"(sched={folders_sched} chat-skip={folders_skipped_chat})", flush=True)
            except Exception as e:
                print(f"[mempalace-miner] cycle error: {type(e).__name__}: {e}", flush=True)

            next_interval = int(((mcfg2.get("mine") or {}).get("interval_seconds", interval)))
            time.sleep(max(60, next_interval))

    threading.Thread(target=_mempalace_miner_loop, daemon=True, name="mempalace-miner").start()

    def _extract_references_from_tool_payload(tool_name, payload):
        """Turn a tool_result payload into a list of {title, url, snippet} dicts.

        Mirrors web/index.html's `extractReferencesFromToolResult` but server-side.
        Defensive against both raw dicts and JSON-string payloads.
        """
        if payload is None:
            return []
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                # Plain text — treat the whole thing as a single "snippet" record
                # only if it looks non-trivial. For web_fetch raw HTML we'd still
                # want to ingest, so take the first 4k chars.
                txt = payload.strip()
                if not txt:
                    return []
                return [{"title": tool_name, "url": "", "snippet": txt[:4000]}]
        if not isinstance(payload, dict):
            return []

        refs = []
        # exa_search shape: {query, results: [{title, link, snippet}, ...]}
        if tool_name == "exa_search" or "results" in payload:
            for r in payload.get("results", []) or []:
                if not isinstance(r, dict):
                    continue
                refs.append({
                    "title": r.get("title", "") or r.get("name", ""),
                    "url": r.get("link", "") or r.get("url", ""),
                    "snippet": (r.get("snippet", "") or r.get("highlight", ""))[:3000],
                })
        # web_fetch shape: {url, status, length, content}
        elif tool_name == "web_fetch" or "content" in payload:
            content = payload.get("content", "") or payload.get("text", "")
            if content:
                refs.append({
                    "title": payload.get("title", "") or tool_name,
                    "url": payload.get("url", ""),
                    "snippet": content[:4000],
                })
        # read_document shape: varies by parser; usually {pages|sheets|text}
        elif tool_name == "read_document":
            text = (
                payload.get("text")
                or payload.get("content")
                or json.dumps(payload, ensure_ascii=False)[:4000]
            )
            refs.append({
                "title": payload.get("filename", "") or payload.get("path", "") or "document",
                "url": payload.get("path", ""),
                "snippet": text[:4000],
            })
        return refs

    # MemPalace chat-sync daemon — mirrors chat turns, session summaries,
    # attachment metadata, and allowlisted tool_result references from
    # chats.db into MemPalace drawers. Rebuilds closets per (wing, room,
    # source_file) group so chat memories rank on par with mined code.
    def _mempalace_chat_sync_loop():
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            return
        sync_cfg = mcfg.get("chat_sync", {}) or {}
        if not sync_cfg.get("enabled", True):
            print("MemPalace chat-sync: disabled")
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            print(f"MemPalace chat-sync: {err}")
            return
        # Ensure downstream mempalace.mcp_server uses the right palace path.
        palace_path = mcfg.get("palace_path", "")
        if palace_path:
            os.environ.setdefault("MEMPALACE_PALACE_PATH", palace_path)
        try:
            from mempalace.mcp_server import tool_add_drawer
            from mempalace.miner import detect_hall
            from mempalace.palace import (
                get_collection as _get_drawers_col,
                get_closets_collection,
                build_closet_lines,
                purge_file_closets,
                upsert_closet_lines,
            )
        except Exception as e:
            print(f"MemPalace chat-sync: import failed: {e}")
            return

        # Small delay so we don't fight the miner on cold start.
        time.sleep(20)

        while True:
            try:
                mcfg2 = engine._load_mempalace_config()
                if not mcfg2.get("enabled", True):
                    return
                sync_cfg2 = mcfg2.get("chat_sync", {}) or {}
                if not sync_cfg2.get("enabled", True):
                    time.sleep(60)
                    continue

                include_roles = set(sync_cfg2.get("include_roles", ["user", "assistant"]))
                include_tool_results = set(sync_cfg2.get("include_tool_results", []) or [])
                max_chars = int(sync_cfg2.get("max_chars_per_message", 8000))
                include_summary = bool(sync_cfg2.get("include_session_summary", True))
                do_attach_meta = bool(sync_cfg2.get("attachment_metadata_drawer", True))
                do_closets = bool(sync_cfg2.get("build_closets", True))
                closet_head = int(sync_cfg2.get("closet_content_head_chars", 5000))
                default_room = sync_cfg2.get("room", "chat")

                # Classifier gate config
                clf_cfg = sync_cfg2.get("classifier", {}) or {}
                clf_enabled = bool(clf_cfg.get("enabled", False))
                clf_model = clf_cfg.get("model", "")
                clf_file_categories = set(clf_cfg.get("categories_to_file",
                    ["fact", "preference", "decision", "reference"]))
                clf_min_turns = int(clf_cfg.get("min_turns", 0))

                closets_col = None
                if do_closets:
                    try:
                        closets_col = get_closets_collection(palace_path, create=True)
                    except Exception as ce:
                        print(f"[mempalace-chat-sync] closets collection unavailable: {ce}")
                        closets_col = None

                pending = ChatDB.mempalace_sessions_needing_sync() or []
                total_new = 0
                for session_row in pending:
                    sid = session_row["session_id"]
                    agent_id = session_row.get("agent_id") or "main"
                    session_user_id = session_row.get("user_id") or ""
                    after_id = int(session_row.get("last_message_id_filed") or 0)
                    max_msg_id = int(session_row.get("max_message_id") or 0)
                    # save_to_memory: 0=off, 1=on (save all), 2=auto (classifier/min_turns)
                    mem_mode = int(session_row.get("save_to_memory") or 0)
                    msg_count = int(session_row.get("message_count") or 0)

                    # Off: skip entirely
                    if mem_mode == 0:
                        ChatDB.mempalace_update_cursor(sid, max_msg_id)
                        continue

                    # Auto/On: check min_turns (on=1 bypasses, auto=2 respects)
                    if mem_mode == 2 and clf_min_turns > 0 and msg_count < clf_min_turns:
                        ChatDB.mempalace_update_cursor(sid, max_msg_id)
                        continue

                    # Wing resolution:
                    #   team-scoped session → team_id--agent_id (shared across team members)
                    #   user-owned          → user_id--agent_id (private to user)
                    #   legacy anonymous    → bare agent_id
                    wing = _resolve_session_wing(session_row)

                    new_messages = ChatDB.mempalace_load_new_messages(sid, after_id) or []
                    # Per (wing, room, source_file) → list[(drawer_id, text)] for closet rebuild.
                    dirty_groups: dict[tuple, list] = {}

                    def _file_drawer(w, r, content, source_file):
                        if not content:
                            return False
                        content = content[:max_chars]
                        engine.mempalace_activity.store_begin()
                        try:
                            try:
                                res = tool_add_drawer(
                                    wing=w,
                                    room=r,
                                    content=content,
                                    source_file=source_file,
                                    added_by="brain-chat-sync",
                                )
                            except Exception as ex:
                                print(f"[mempalace-chat-sync] add_drawer failed: {ex}")
                                return False
                        finally:
                            engine.mempalace_activity.store_end()
                        if not isinstance(res, dict) or not res.get("success"):
                            return False
                        if res.get("reason") == "already_exists":
                            return False  # don't count dedup hits toward closet rebuild
                        # Stamp hall metadata (tool_add_drawer doesn't support it natively)
                        drawer_id = res.get("drawer_id", "")
                        if drawer_id:
                            try:
                                hall = detect_hall(content)
                                dcol = _get_drawers_col(palace_path, create=False)
                                if dcol and hall:
                                    existing = dcol.get(ids=[drawer_id], include=["metadatas", "documents"])
                                    if existing and existing["ids"]:
                                        meta = dict(existing["metadatas"][0])
                                        meta["hall"] = hall
                                        dcol.upsert(ids=[drawer_id], documents=existing["documents"], metadatas=[meta])
                            except Exception:
                                pass  # non-critical
                        group_key = (w, r, source_file)
                        dirty_groups.setdefault(group_key, []).append(
                            (drawer_id, content)
                        )
                        return True

                    new_last_id = after_id

                    # Build classifier skip-set: message IDs to skip based on LLM classification.
                    # Skip classifier entirely if user toggled save_to_memory on this session.
                    _clf_skip_ids: set[int] = set()
                    if clf_enabled and clf_model and mem_mode == 2:
                        i = 0
                        while i < len(new_messages):
                            m = new_messages[i]
                            m_role = (m.get("role") or "").strip()
                            m_id = int(m.get("id") or 0)
                            # Pair user+assistant for classification
                            if m_role == "user" and i + 1 < len(new_messages):
                                nxt = new_messages[i + 1]
                                nxt_role = (nxt.get("role") or "").strip()
                                nxt_id = int(nxt.get("id") or 0)
                                if nxt_role == "assistant":
                                    u_text = str(m.get("content") or "")[:2000]
                                    a_text = str(nxt.get("content") or "")[:2000]
                                    category = engine.classify_chat_for_memory(
                                        u_text, a_text, clf_model)
                                    if category and category not in clf_file_categories:
                                        _clf_skip_ids.add(m_id)
                                        _clf_skip_ids.add(nxt_id)
                                        print(f"[mempalace-classifier] skip ({category}): "
                                              f"{u_text[:60]}", flush=True)
                                    i += 2
                                    continue
                            i += 1

                    # Track the current turn's anchor user-message id. Every drawer
                    # filed from this turn (user, assistant, attachment, tool result)
                    # inherits this id in its source_file so per-turn purge/memorise
                    # can target one turn without touching neighbours.
                    current_turn_id = 0
                    # Seed with the last user id already in chats.db up to after_id so
                    # orphan assistant messages (e.g. when the sync cursor advanced
                    # mid-turn) still attach to their originating turn.
                    try:
                        prior_last_user_id = ChatDB.mempalace_last_user_id_before(sid, after_id)
                    except Exception:
                        prior_last_user_id = 0
                    current_turn_id = int(prior_last_user_id or 0)

                    for msg in new_messages:
                        mid = int(msg.get("id") or 0)
                        new_last_id = max(new_last_id, mid)
                        role = (msg.get("role") or "").strip()
                        content = msg.get("content")
                        meta = msg.get("metadata") or {}

                        # New user message opens a new turn.
                        if role == "user":
                            current_turn_id = mid

                        turn_suffix = f"#turn/{current_turn_id}" if current_turn_id else ""

                        # Normal chat turns.
                        if role in include_roles:
                            if mid in _clf_skip_ids:
                                continue
                            if isinstance(content, str):
                                text = content
                            else:
                                try:
                                    text = json.dumps(content, ensure_ascii=False)
                                except Exception:
                                    text = str(content)
                            body = f"[{role}] {text}".strip()
                            if body:
                                source_file = f"session/{sid}{turn_suffix}"
                                if _file_drawer(wing, default_room, body, source_file):
                                    total_new += 1

                        # Attachment metadata.
                        if do_attach_meta and isinstance(meta, dict):
                            files = meta.get("files") or []
                            if isinstance(files, list):
                                for f in files:
                                    if not isinstance(f, dict):
                                        continue
                                    fname = f.get("name") or f.get("filename") or "unknown"
                                    fmime = f.get("mime") or f.get("type") or "application/octet-stream"
                                    fsize = f.get("size") or 0
                                    body = (
                                        f"[attachment from {role or 'message'} "
                                        f"in session {sid}#{mid}]\n"
                                        f"filename: {fname}\n"
                                        f"mime: {fmime}\n"
                                        f"size: {fsize} bytes"
                                    )
                                    source_file = f"session/{sid}{turn_suffix}#attach/{mid}/{fname}"
                                    if _file_drawer(wing, "chat_attachment", body, source_file):
                                        total_new += 1

                        # Tool-result references (allowlisted tools only).
                        if role == "tool" and include_tool_results and isinstance(content, (list, dict, str)):
                            # Brain stores tool results in several shapes; try to
                            # extract (tool_name, payload) pairs defensively.
                            tool_entries = []
                            if isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict):
                                        tname = item.get("name") or item.get("tool_name") or ""
                                        payload = item.get("content") or item.get("result") or item
                                        tool_entries.append((tname, payload))
                            elif isinstance(content, dict):
                                tname = content.get("name") or content.get("tool_name") or ""
                                payload = content.get("content") or content.get("result") or content
                                tool_entries.append((tname, payload))
                            elif isinstance(meta, dict) and meta.get("tool_name"):
                                tool_entries.append((meta.get("tool_name"), content))

                            for tname, payload in tool_entries:
                                if tname not in include_tool_results:
                                    continue
                                # Parse payload into a list of reference records.
                                refs = _extract_references_from_tool_payload(tname, payload)
                                for idx, ref in enumerate(refs):
                                    ref_body = (
                                        f"[{tname} result from session {sid}#{mid}]\n"
                                        f"title: {ref.get('title','')}\n"
                                        f"url: {ref.get('url','')}\n\n"
                                        f"{ref.get('snippet','')}"
                                    )
                                    source_file = f"session/{sid}{turn_suffix}#tool/{tname}/{mid}/{idx}"
                                    if _file_drawer(wing, "reference", ref_body, source_file):
                                        total_new += 1

                    # Session summary — low-frequency text worth indexing separately.
                    summary_hash = ""
                    if include_summary:
                        summary = (session_row.get("summary") or "").strip()
                        if summary:
                            summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
                            if summary_hash != (session_row.get("last_summary_hash") or ""):
                                body = f"[session summary for {sid}]\n{summary}"
                                source_file = f"session/{sid}#summary"
                                if _file_drawer(wing, "chat_summary", body, source_file):
                                    total_new += 1

                    # Rebuild closets per dirty group.
                    if closets_col is not None and dirty_groups:
                        for (w, r, source_file), items in dirty_groups.items():
                            drawer_ids = [did for did, _ in items if did]
                            if not drawer_ids:
                                continue
                            concatenated = "\n\n".join(txt for _, txt in items)[:closet_head]
                            try:
                                purge_file_closets(closets_col, source_file)
                                lines = build_closet_lines(
                                    source_file=source_file,
                                    drawer_ids=drawer_ids,
                                    content=concatenated,
                                    wing=w,
                                    room=r,
                                )
                                if lines:
                                    closet_id_base = (
                                        f"{w}_{r}_"
                                        + hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:12]
                                    )
                                    upsert_closet_lines(
                                        closets_col,
                                        closet_id_base,
                                        lines,
                                        {"source_file": source_file, "wing": w, "room": r},
                                    )
                            except Exception as ce:
                                print(f"[mempalace-chat-sync] closet rebuild failed for {source_file}: {ce}")

                    # Advance cursor even if nothing new was filed (all dedup'd) —
                    # otherwise we keep re-scanning the same tail forever.
                    ChatDB.mempalace_update_cursor(
                        sid,
                        max(new_last_id, max_msg_id),
                        last_summary_hash=summary_hash or session_row.get("last_summary_hash") or "",
                    )

                if total_new:
                    print(f"[mempalace-chat-sync] filed {total_new} new drawer(s) across {len(pending)} session(s)")
            except Exception as e:
                print(f"[mempalace-chat-sync] cycle error: {type(e).__name__}: {e}")

            next_interval = int((engine._load_mempalace_config().get("chat_sync") or {}).get("interval_seconds", 60))
            time.sleep(max(15, next_interval))

    threading.Thread(target=_mempalace_chat_sync_loop, daemon=True, name="mempalace-chat-sync").start()

    # Warmup keeper — fires minimal prefill requests at models flagged with
    # warmup=true so their first real turn lands on a warm KV cache. Runs
    # sequentially (max_concurrent default 1) to avoid saturating the local
    # gateway; respects per-model warmup_ttl_seconds (default 60s).
    def _warmup_keeper_loop():
        # Small startup delay so we don't race provider probes on boot
        time.sleep(5)
        while True:
            try:
                wcfg = server_config.get("warmup", {}) or {}
                if not wcfg.get("enabled", True):
                    time.sleep(30)
                    continue
                interval = int(wcfg.get("interval_seconds", 30))
                allow_cloud_global = bool(wcfg.get("allow_cloud", False))
                max_concurrent = int(wcfg.get("max_concurrent", 1))

                # Snapshot models with warmup=true. Each model picks its own
                # warmup_mode ("full" default | "minimal"). Full primes the
                # KV prefix so first-token latency is ~5-6s; minimal only
                # loads weights. If multiple full-prime models together
                # exceed GPU memory, oMLX will evict as needed — that's a
                # user-managed tradeoff, we don't second-guess it here.
                #
                # Only re-prime models whose state is idle/cold/failed or
                # whose configured mode changed since last prime.
                now = time.time()
                candidates = []
                for mid, _raw_cfg in list(engine._models_config.items()):
                    cfg = engine.resolve_model_settings(mid)
                    if not cfg.get("warmup"):
                        continue
                    if not cfg.get("enabled", True):
                        continue
                    desired_mode = (cfg.get("warmup_mode") or "full").lower()
                    if desired_mode not in ("full", "minimal"):
                        desired_mode = "full"
                    st = engine.get_warmup_state(mid)
                    state_name = st.get("state", "idle")
                    if state_name in ("warming", "skipped_cloud"):
                        continue
                    if state_name == "warm":
                        prev_mode = st.get("mode", "full")
                        if prev_mode == desired_mode:
                            engine.set_warmup_state(mid, next_due_ts=0)
                            continue
                        # Mode flipped — fall through to re-prime.
                    last = max(st.get("last_warmup_ts", 0), st.get("last_used_ts", 0))
                    age = now - last if last else 10 ** 9
                    candidates.append((age, mid, cfg, desired_mode))

                # Oldest first; cap to max_concurrent per cycle
                candidates.sort(key=lambda t: t[0], reverse=True)
                for _, mid, cfg, desired_mode in candidates[:max_concurrent]:
                    allow_cloud = bool(cfg.get("warmup_allow_cloud", allow_cloud_global))
                    t0 = time.time()
                    result = engine.run_model_warmup(
                        mid,
                        allow_cloud=allow_cloud,
                        agent_id="main",
                        timeout=int(wcfg.get("timeout_seconds", 30)),
                        mode=desired_mode,
                    )
                    dur = int((time.time() - t0) * 1000)
                    st_name = result.get("state", "?")
                    if result.get("ok"):
                        print(f"[warmup-keeper] {mid}: warm ({desired_mode}, {dur}ms)")
                    elif st_name == "skipped_cloud":
                        pass
                    else:
                        err = result.get("error", "?")
                        print(f"[warmup-keeper] {mid}: failed — {err}")

                # Second pass — top up the warm session pool toward target
                # depth. Only build for models that are fully warm (weights +
                # KV prefix primed). try_build is a no-op when the pool is
                # already full.
                for mid, _raw_cfg in list(engine._models_config.items()):
                    cfg = engine.resolve_model_settings(mid)
                    if not cfg.get("warmup") or not cfg.get("enabled", True):
                        continue
                    st = engine.get_warmup_state(mid)
                    if st.get("state") != "warm":
                        continue
                    warm_pool.try_build(mid)

                # Wait for either the interval or an explicit wake-up (model
                # config change, manual warmup trigger, etc.). wait() returns
                # True when the event was set — we consume it and re-run.
                woke = _warmup_wakeup.wait(timeout=max(5, interval))
                if woke:
                    _warmup_wakeup.clear()
            except Exception as e:
                print(f"[warmup-keeper] loop error: {type(e).__name__}: {e}")
                time.sleep(30)

    threading.Thread(target=_warmup_keeper_loop, daemon=True, name="warmup-keeper").start()

    def _save_chat_to_memory_callback(session_id: str) -> dict:
        """Enable save_to_memory on a session and trigger immediate sync."""
        # Set mode=1 (on) on session
        s = sessions.get(session_id)
        if s:
            s.save_to_memory = 1
        ChatDB.update_session_save_to_memory(session_id, 1)
        # Reset sync cursor to re-sync from beginning
        ChatDB.mempalace_update_cursor(session_id, 0)
        # Trigger sync in background thread
        def _do_sync():
            try:
                mcfg = engine._load_mempalace_config()
                if not mcfg.get("enabled", True):
                    return
                palace_path = mcfg.get("palace_path", "")
                if not palace_path:
                    return
                sync_cfg = mcfg.get("chat_sync", {}) or {}
                max_chars = int(sync_cfg.get("max_chars_per_message", 8000))
                default_room = sync_cfg.get("room", "chat")
                include_roles = set(sync_cfg.get("include_roles", ["user", "assistant"]))
                ok, _ = engine._ensure_mempalace_importable()
                if not ok:
                    return
                from mempalace.mcp_server import tool_add_drawer
                info = ChatDB.get_session_info(session_id)
                if not info:
                    return
                wing = _resolve_session_wing(info)
                msgs = ChatDB.mempalace_load_new_messages(session_id, 0) or []
                filed = 0
                current_turn_id = 0
                for msg in msgs:
                    mid = int(msg.get("id") or 0)
                    role = (msg.get("role") or "").strip()
                    if role == "user":
                        current_turn_id = mid
                    turn_suffix = f"#turn/{current_turn_id}" if current_turn_id else ""
                    if role not in include_roles:
                        continue
                    content = msg.get("content")
                    text = content if isinstance(content, str) else str(content)
                    body = f"[{role}] {text}"[:max_chars]
                    if body.strip():
                        engine.mempalace_activity.store_begin()
                        try:
                            res = tool_add_drawer(wing=wing, room=default_room,
                                                  content=body, source_file=f"session/{session_id}{turn_suffix}",
                                                  added_by="brain-chat-sync")
                            if res.get("success") and res.get("reason") != "already_exists":
                                filed += 1
                        except Exception:
                            pass
                        finally:
                            engine.mempalace_activity.store_end()
                max_id = max((int(m.get("id") or 0) for m in msgs), default=0)
                if max_id:
                    ChatDB.mempalace_update_cursor(session_id, max_id)
                print(f"[mempalace-sync] immediate: filed {filed} drawer(s) for {session_id[:8]}", flush=True)
            except Exception as e:
                print(f"[mempalace-sync] immediate sync error: {e}", flush=True)
        threading.Thread(target=_do_sync, daemon=True).start()
        return {"saved": True, "session_id": session_id}

    engine._save_chat_to_memory_callback = _save_chat_to_memory_callback

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
