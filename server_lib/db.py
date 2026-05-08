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
