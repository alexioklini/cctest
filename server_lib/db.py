# Extracted from server.py — database helpers, node registry, ChatDB
import datetime
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid

import brain as engine

# --- Notification Manager (initialized in main()) ---
_notification_manager = None

# --- Node Manager (in-memory registry for remote nodes) ---

_node_registry: dict = {}  # token -> node info
_node_commands: dict = {}  # command_id -> {command, result_event, result}
_node_lock = threading.Lock()


def _load_node_config() -> dict:
    """Load nodes config from config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("nodes", {})
    except Exception:
        return {}


def _save_node_config(nodes: dict):
    """Save nodes config to config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
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

CHAT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents", "main", "chats.db")


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


def _purge_mempalace_turns(session_id: str, turn_ids: list):
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


def _project_wing(project_id: str) -> str:
    """Wing name for project KNOWLEDGE memory. ID-only — no agent, no name.
    This wing holds ONLY mined input-folder content and ingested attachments.
    Chat-derived drawers go to `_project_chat_wing()` instead, so wrong answers
    in past chats never rank above the underlying source documents.
    Project IDs are globally unique (uuid4 hex[:12]) so collisions across
    agents are impossible. Renaming a project doesn't strand its drawers.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id or "")
    return f"project__{safe}"


def _project_chat_wing(project_id: str) -> str:
    """Wing name for chat content originating in a project session. Separate
    from `_project_wing()` so `mempalace_query` reads of project knowledge
    never surface conversational drawers (chat turns, summaries, attachment
    metadata, tool-result references). The user's per-turn "Memorise this"
    action and the chat-sync daemon both write here.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id or "")
    return f"project_chat__{safe}"


def _user_wing(user_id: str) -> str:
    return f"user__{user_id}"


def _team_wing(team_id: str) -> str:
    return f"team__{team_id}"


def _project_id_for_name(agent_id: str, project_name: str) -> str:
    """Resolve agent_id + project directory name → stable project_id (uuid4
    hex[:12] from project.json). Returns "" if the project isn't found.
    Used by session-create + session-list endpoints to translate the legacy
    name parameter into the canonical id used by the `sessions.project_id`
    filter. Cheap: ProjectManager.get_project() is a JSON read."""
    if not project_name:
        return ""
    try:
        proj = engine.ProjectManager.get_project(agent_id or "main", project_name)
        if proj and proj.get("id"):
            return proj["id"]
    except Exception:
        pass
    return ""


def _resolve_session_wing(info: dict) -> str:
    """Pick the MemPalace wing for chat-derived content from a session.
    ID-only scheme.

    Priority:
      1. Project-scoped session (project set)              → `project_chat__{project_id}`
      2. Team-scoped session (visibility='team' + team_id) → `team__{team_id}`
      3. User-owned session (user_id)                      → `user__{user_id}`
      4. Legacy anonymous                                  → empty (skip)

    Note: project chats land in `project_chat__<id>`, NOT `project__<id>`.
    The latter is reserved for mined project knowledge (input folders +
    ingested attachments) so retrieval of project documents is never
    contaminated by past conversation turns or summaries.
    """
    project = info.get("project", "") or ""
    if project:
        # `project` here is the directory name (not the id) because that's
        # how sessions reference their project today. Resolve to the id.
        agent_id = info.get("agent_id", "main") or "main"
        proj = engine.ProjectManager.get_project(agent_id, project)
        if proj and proj.get("id"):
            return _project_chat_wing(proj["id"])
        # Project lookup failed — fall through to user/team to avoid
        # silently writing into a wing the model can't query.
    visibility = info.get("visibility", "user")
    team_id = info.get("team_id", "")
    if visibility == "team" and team_id:
        return _team_wing(team_id)
    user_id = info.get("user_id", "")
    if user_id:
        return _user_wing(user_id)
    return ""


def _memorize_mempalace_turns(session_id: str, turn_ids: list):
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
        if not wing:
            return 0  # anonymous session — don't pollute the global namespace

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
            try:
                # project_id: stable uuid4 hex[:12] from project.json. Replaces the
                # display-name `project` column as the join key. Storing both
                # because the name still drives breadcrumbs / sidebar labels;
                # the id is the source of truth for filtering. Backfilled at
                # startup from agents/<id>/projects/<name>/project.json.
                conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_team ON sessions(team_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_project_id ON sessions(project_id)")
            # workflow_run_id: link a chat session to a workflow_history row so
            # follow-up questions in the inline workflow detail view get the
            # run's compact summary (source + steps + return) injected as a
            # first-turn preamble. Sessions with status='workflow_run' are
            # hidden from the sidebar until promoted via "Save to chats".
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN workflow_run_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_workflow_run ON sessions(workflow_run_id)")
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
            # ── Translation history ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS translate_history (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_lang TEXT NOT NULL DEFAULT '',
                    target_lang TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    artifact_path TEXT NOT NULL DEFAULT '',
                    created_at REAL DEFAULT (strftime('%s','now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_translate_history_user ON translate_history(user_id, created_at DESC)")
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
    def save_session(sid, agent_id, model, title, status, created_at, last_active, project="", summary="", user_id="", project_id="", workflow_run_id=""):
        # `project` is the legacy directory-name column (kept for back-compat
        # display + summaries elsewhere). `project_id` is the canonical filter
        # column — uuid4 hex[:12] from project.json. Resolve here when caller
        # only passed a name so every save normalises both columns.
        if project and not project_id:
            project_id = _project_id_for_name(agent_id, project)
        with _db_conn() as conn:
            conn.execute("""
                INSERT INTO sessions (id, agent_id, model, title, status, created_at, last_active, project, project_id, summary, user_id, workflow_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    agent_id=excluded.agent_id, model=excluded.model, title=excluded.title,
                    status=excluded.status, created_at=excluded.created_at, last_active=excluded.last_active,
                    project=excluded.project,
                    project_id=CASE WHEN excluded.project_id != '' THEN excluded.project_id ELSE sessions.project_id END,
                    summary=CASE WHEN excluded.summary != '' THEN excluded.summary ELSE sessions.summary END,
                    user_id=CASE WHEN excluded.user_id != '' THEN excluded.user_id ELSE sessions.user_id END,
                    workflow_run_id=CASE WHEN excluded.workflow_run_id != '' THEN excluded.workflow_run_id ELSE sessions.workflow_run_id END
            """, (sid, agent_id, model, title, status, created_at, last_active, project or "", project_id or "", summary or "", user_id or "", workflow_run_id or ""))
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
    def update_session_workflow_run_id(session_id, workflow_run_id):
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET workflow_run_id = ? WHERE id = ?",
                        (workflow_run_id or "", session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_status(session_id, status):
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET status = ? WHERE id = ?",
                        (status, session_id))
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
                       COALESCE(s.project, '') as project,
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
    def list_sessions(agent_id=None, status=None, project=None, visible_user_ids=None, visible_team_ids=None, project_id=None):
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
            # Filter by stable project_id when available (handler resolves
            # name → id once), fall back to legacy `project` (name) for
            # callers that haven't been updated yet. Once project_id is
            # populated everywhere the legacy branch can be dropped.
            if project_id:
                q += " AND s.project_id = ?"
                params.append(project_id)
            elif project:
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
            else:
                # Default sidebar view excludes ephemeral statuses that have
                # their own surfaces: note_chat (project notes editor) and
                # workflow_run (inline workflow detail view follow-ups).
                # Only filtered when the caller didn't ask for a specific
                # status — explicit filtering still works for diagnostics.
                q += " AND s.status NOT IN ('note_chat', 'workflow_run')"
            # Hide orphan empty sessions: ensureSession() pre-creates a row
            # whenever a model is switched or a fresh chat is opened, even
            # if the user never types anything. Those linger as 0-message
            # rows in the DB and would otherwise pollute the session list
            # (especially noticeable on project-detail because every project
            # visit creates one). Hide rows with no messages older than 60s,
            # keep the freshly-created one so the active chat doesn't blink
            # out of the list mid-creation.
            stale_cutoff = time.time() - 60
            q += (" AND (((SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id "
                  "AND (m.compacted = 0 OR m.compacted IS NULL)) > 0) "
                  "OR s.last_active >= ?)")
            params.append(stale_cutoff)
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
                session_files: dict = {}
                seen_keys: dict = {}
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
    def archive_all(agent_id=None, project=None, project_id=None):
        with _db_conn() as conn:
            conditions = ["status = 'active'"]
            params = []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            elif project is not None:
                conditions.append("project = ?")
                params.append(project)
            where = " WHERE " + " AND ".join(conditions)
            conn.execute(f"UPDATE sessions SET status = 'archived'{where}", params)
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def unarchive_all(agent_id=None, project=None, project_id=None):
        with _db_conn() as conn:
            conditions = ["status = 'archived'"]
            params = []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            elif project is not None:
                conditions.append("project = ?")
                params.append(project)
            where = " WHERE " + " AND ".join(conditions)
            conn.execute(f"UPDATE sessions SET status = 'active'{where}", params)
            conn.commit()

    @staticmethod
    @_db_safe(default=[])
    def delete_all(agent_id=None, archived_only=False, project=None, project_id=None):
        """Delete all sessions (optionally filtered). Returns list of deleted session IDs."""
        with _db_conn() as conn:
            conditions = []
            params = []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if archived_only:
                conditions.append("status = 'archived'")
            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            elif project is not None:
                conditions.append("project = ?")
                params.append(project)
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


class TranslateHistoryDB:
    """Persist translation history (text, document, media, live) to chats.db."""

    @staticmethod
    @_db_safe(default=None)
    def add(*, entry_id: str, user_id: str, type: str, title: str,
            source_lang: str, target_lang: str, result_json: str,
            artifact_path: str = "") -> None:
        with _db_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO translate_history
                   (id, user_id, type, title, source_lang, target_lang, result_json, artifact_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, user_id, type, title, source_lang, target_lang,
                 result_json, artifact_path),
            )
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def list_for_user(user_id: str, limit: int = 200) -> list:
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, user_id, type, title, source_lang, target_lang,
                          result_json, artifact_path, created_at
                   FROM translate_history
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def list_all(limit: int = 500) -> list:
        """Admin-only — return every entry across all users for RBAC."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, user_id, type, title, source_lang, target_lang,
                          result_json, artifact_path, created_at
                   FROM translate_history
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=None)
    def get(entry_id: str, user_id: str, *, admin: bool = False):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            if admin:
                row = conn.execute(
                    "SELECT * FROM translate_history WHERE id = ?",
                    (entry_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM translate_history WHERE id = ? AND user_id = ?",
                    (entry_id, user_id),
                ).fetchone()
            return dict(row) if row else None

    @staticmethod
    @_db_safe(default=None)
    def delete(entry_id: str, user_id: str, *, admin: bool = False) -> None:
        with _db_conn() as conn:
            if admin:
                conn.execute("DELETE FROM translate_history WHERE id = ?", (entry_id,))
            else:
                conn.execute(
                    "DELETE FROM translate_history WHERE id = ? AND user_id = ?",
                    (entry_id, user_id),
                )
            conn.commit()
