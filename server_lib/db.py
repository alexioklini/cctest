# Single source of truth for chat-DB helpers (re-exported by server.py for
# back-compat with handler mixins that still resolve names from server's globals).
import json
import os
import re
import sqlite3
import threading
import time
import uuid

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



# --- Node Manager (in-memory registry for remote nodes) ---
# Extracted to server_lib/node_registry.py. Re-exported here (and from there
# by server.py) so handler mixins resolving names via globals keep working.
from server_lib.node_registry import (  # noqa: F401
    _node_registry,
    _node_commands,
    _node_lock,
    _load_node_config,
    _save_node_config,
    _init_node_registry,
    _node_submit_command,
)


# --- MemPalace wing/purge helpers ---
# Re-exported by server.py for handler mixins. _mp singleton stays in server.py;
# helpers reach it via lazy `from server import _mp`.

def _purge_mempalace_session(session_id: str):
    """Remove MemPalace drawers and closets for a deleted session (background thread)."""
    def _do_purge():
        from server import _mp
        try:
            if not _mp.ready:
                return
            prefix = f"session/{session_id}"
            pp = _mp.palace_path
            if not pp or not os.path.isdir(pp):
                return
            col = _mp.get_collection(create=False)
            if col:
                result = col.get(include=["metadatas"])
                ids_to_delete = [
                    did for did, m in zip(result["ids"], result["metadatas"])
                    if (m.get("source_file") or "").startswith(prefix)
                ]
                if ids_to_delete:
                    col.delete(ids=ids_to_delete)
                    print(f"[mempalace-purge] deleted {len(ids_to_delete)} drawer(s) for session {session_id[:8]}")
            ccol = _mp.get_closets_col(create=False)
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
        from server import _mp
        try:
            if not _mp.ready:
                return
            pp = _mp.palace_path
            if not pp or not os.path.isdir(pp):
                return

            def _matches(sf: str) -> bool:
                return any(sf == p or sf.startswith(p + "#") for p in turn_prefixes)

            col = _mp.get_collection(create=False)
            if col:
                result = col.get(include=["metadatas"])
                ids_to_delete = [
                    did for did, m in zip(result["ids"], result["metadatas"])
                    if _matches(m.get("source_file") or "")
                ]
                if ids_to_delete:
                    col.delete(ids=ids_to_delete)
                    print(f"[mempalace-purge] deleted {len(ids_to_delete)} drawer(s) "
                          f"for {len(turn_ids)} turn(s) in session {session_id[:8]}")
            ccol = _mp.get_closets_col(create=False)
            if ccol:
                result = ccol.get(include=["metadatas"])
                cids = [
                    cid for cid, m in zip(result["ids"], result["metadatas"])
                    if _matches(m.get("source_file") or "")
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
        import brain as engine
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
        import brain as engine
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
        from server import _mp
        import brain as engine
        if not _mp.ready:
            return 0

        info = ChatDB.get_session_info(session_id)
        if not info:
            return 0
        agent_id = info.get("agent_id", "main")
        wing = _resolve_session_wing(info)
        if not wing:
            return 0  # anonymous session — don't pollute the global namespace

        _sync_cfg = (engine._load_mempalace_config().get("chat_sync") or {})
        default_room = _sync_cfg.get("room", "chat")
        include_roles = set(_sync_cfg.get("include_roles", ["user", "assistant"]))
        max_chars = int(_sync_cfg.get("max_chars_per_message", 8000))

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
                res = _mp.add_drawer(
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


def session_share_block(info: dict) -> dict:
    """Map a session-info row to the generic five-field sharing block.
    Sessions reuse `user_id` as the owner. JSON list columns are decoded."""
    def _list(v):
        if isinstance(v, list):
            return v
        if not v:
            return []
        try:
            d = json.loads(v)
            return d if isinstance(d, list) else []
        except Exception:
            return []
    return {
        "owner_user_id": info.get("user_id") or "",
        "visibility": info.get("visibility") or "user",
        "owner_team_id": info.get("team_id") or "",
        "extra_member_user_ids": _list(info.get("extra_member_user_ids")),
        "excluded_user_ids": _list(info.get("excluded_user_ids")),
    }


# MemPalace chat-sync cursor helpers extracted here. Imported AFTER _db_conn /
# _db_safe are defined above so mempalace_sync's `from server_lib.db import
# _db_conn, _db_safe` resolves (db.py is already in sys.modules at this point).
from server_lib import mempalace_sync as _mp_sync  # noqa: E402


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
            # Per-artifact visibility override (generic sharing model). Empty
            # string = inherit the parent session/project/run's visibility.
            # When set, may only NARROW (validated at the handler).
            try:
                conn.execute("ALTER TABLE artifacts ADD COLUMN visibility_override TEXT DEFAULT ''")
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
            # Per-session research-mode override: NULL=use project default,
            # 0=force off, 1=force on. Sticky across turns of the same session
            # (mirrors save_to_memory). Toggled from composer or settings.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN research_mode_override INTEGER DEFAULT NULL")
            except sqlite3.OperationalError:
                pass
            # Manual-web-search escape hatch: when the user supplies a curated
            # source set, web_search/web_fetch are hard-disabled for the turn.
            # This sticky per-session flag (0=locked, 1=allow) lets the user
            # opt back into additional autonomous web access on top of the
            # curated sources. Default 0 (locked). Only relevant when the turn
            # carries enabled curated URLs.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN allow_further_web INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Per-session Websuche basket: the user-curated set of web sources
            # (JSON list of {url,title,snippet,query,enabled}). Stored per
            # session so it never leaks between chats — a fresh chat starts
            # empty. Empty string = no basket. Replaces the old global
            # localStorage basket (which bled sources across sessions).
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN web_basket TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Add team_id + visibility for session team-scoping
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN team_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                # visibility: one of private (alias of legacy 'user') / users /
                # team / global. See server_lib/auth.VISIBILITY_VALUES.
                conn.execute("ALTER TABLE sessions ADD COLUMN visibility TEXT DEFAULT 'user'")
            except sqlite3.OperationalError:
                pass
            # Generic sharing block: individual grants (always widens) and
            # individual exclusions (only meaningful when visibility='global').
            # JSON-encoded list of user ids; '[]' default.
            for _col in ("extra_member_user_ids", "excluded_user_ids"):
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {_col} TEXT DEFAULT '[]'")
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
            # workflow_run_id: link to workflow_history.execution_id for chat
            # sessions created from the inline workflow detail view.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN workflow_run_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_workflow_run ON sessions(workflow_run_id)")
            # streaming_text / streaming_meta: in-flight assistant reply for the
            # currently-running turn. Updated incrementally as deltas arrive so a
            # client reopening a streaming chat (or the chat surviving a server
            # restart mid-stream) can render the partial text. Cleared (set to '')
            # when the turn finalizes and the real assistant message is persisted.
            for _col in ("streaming_text", "streaming_meta"):
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {_col} TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
            # last_system_prompt: the verbatim system prompt that was sent to
            # the model on the most recent turn of this session. Overwritten
            # per turn (no history). Read by the session inspector so the UI
            # shows the actual wire prompt instead of a freshly-rebuilt one
            # (rebuilding always lies a little — different timestamp,
            # different active tool set if config changed since the turn).
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN last_system_prompt TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # Transparent anonymisation sticky preference (step 6.2). When set,
            # the GDPR modal is skipped and the stored choice is forwarded as
            # body.gdpr_action on every send for this session. Empty = ask each
            # time. Allowed values: '', 'anonymise', 'local_model', 'continue'.
            # 'cancel' is NEVER persisted — it's a one-shot abort verdict, not
            # a preference (would brick the chat).
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN gdpr_action_pref TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
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
            # ── Active turns ──
            # Records sidecar turns that are in flight. On Brain startup the
            # recovery thread scans this table and, for each row, asks the
            # sidecar's GET /turn/<id>/events to replay missed events into a
            # fresh LiveStream. Rows are deleted when the proxy finishes
            # draining the turn (success, error, or cancel).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_turns (
                    session_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)
            # ── Pseudonym maps (transparent anonymisation) ──
            # AES-GCM-encrypted mapping of {original PII value → pseudonym}
            # per chat turn. Created when the user opts into "Anonymise &
            # continue" on the GDPR modal; read by the chat worker to
            # de-anonymise the LLM reply and any files the LLM produces.
            # Encryption is at-rest only; the key sits next to the DB and is
            # protected primarily by "data never leaves the machine".
            # `session_id` lets `delete_session` cascade-drop. `turn_id` is
            # informational (audit) — not unique, since the same map can be
            # extended within a turn.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pseudonym_maps (
                    mapping_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL DEFAULT '',
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pseudonym_maps_session "
                "ON pseudonym_maps(session_id)"
            )
            # ── Document classification scan history ──
            # One row per scan (upload / folder walk / project sweep). The
            # detector itself is in engine/classification.py and is invoked
            # via handlers/classification.py — this table only persists the
            # results so users can revisit past scans. `summary_json` carries
            # aggregate counts; `evidence_json` carries per-file details
            # (marker excerpts + mismatch reasons), capped server-side at
            # ~50KB so a runaway scan can't bloat chats.db.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS classification_scans (
                    scan_id       TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL DEFAULT '',
                    created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
                    source_kind   TEXT NOT NULL,
                    source_label  TEXT NOT NULL DEFAULT '',
                    file_count    INTEGER NOT NULL DEFAULT 0,
                    summary_json  TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_classification_scans_user "
                "ON classification_scans(user_id, created_at DESC)"
            )
            # ── Helpdesk ("Brainy") conversation history ──
            # PER-USER personal assistant: one continuous conversation per user,
            # carried across all views/sessions (keyed by user_id, NOT session).
            # Private to the user — never shared by project/team; admins read it
            # only via the audit log. Kept separate from `messages` so it never
            # enters the main chat history / wire. The session_id column is a
            # vestige (kept for schema stability) and is left empty.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS helpdesk_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT '',
                    user_id    TEXT NOT NULL DEFAULT '',
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_helpdesk_history_user "
                "ON helpdesk_history(user_id, id)"
            )
            # Context label = where the user was when this turn was asked
            # (e.g. "project:<name>", "view:translation"). Display-neutral; its
            # purpose is context-filtered REPLAY — the model turn prefers turns
            # from the user's current context + the most-recent few, so an old
            # unrelated thread neither bleeds in nor costs tokens. Storage stays
            # one per-user thread (delete/pagination/grouping unchanged). NULL on
            # legacy rows → treated as "matches any context".
            try:
                conn.execute("ALTER TABLE helpdesk_history ADD COLUMN context_label TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # ── Background tasks ──
            # Detached, same-agent/same-config agentic runs spawned mid-turn via
            # the run_background_task tool. The full `output` lives here only; it
            # is injected wire-only into the spawning session's NEXT turn (then
            # marked consumed_at) so it never enters chat history / the wire on
            # later turns. status: running|done|cancelled|error.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id           TEXT PRIMARY KEY,
                    session_id   TEXT NOT NULL,
                    agent_id     TEXT NOT NULL DEFAULT 'main',
                    model        TEXT NOT NULL DEFAULT '',
                    title        TEXT NOT NULL DEFAULT '',
                    prompt       TEXT NOT NULL DEFAULT '',
                    status       TEXT NOT NULL DEFAULT 'running',
                    turn_id      TEXT NOT NULL DEFAULT '',
                    output       TEXT NOT NULL DEFAULT '',
                    error        TEXT NOT NULL DEFAULT '',
                    usage_in     INTEGER DEFAULT 0,
                    usage_out    INTEGER DEFAULT 0,
                    tool_calls   INTEGER DEFAULT 0,
                    created_at   REAL DEFAULT (strftime('%s','now')),
                    finished_at  REAL,
                    consumed_at  REAL,
                    -- Fan-out / join (v9.47.0). group_id links calls the model
                    -- emitted together; follow_up is the recombine instruction;
                    -- group_done_at is the atomic single-flight join marker;
                    -- parent_task_id is the nesting guard. All NULL = standalone
                    -- single task (the pre-9.47 behaviour, unchanged).
                    group_id      TEXT,
                    follow_up     TEXT,
                    group_done_at REAL,
                    parent_task_id TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bgtask_session ON background_tasks(session_id, created_at)")
            # Additive migrations for the fan-out columns (existing DBs) — MUST run
            # before the group index, which references group_id.
            for _col, _decl in (("group_id", "TEXT"), ("follow_up", "TEXT"),
                                ("group_done_at", "REAL"), ("parent_task_id", "TEXT")):
                try:
                    conn.execute(f"ALTER TABLE background_tasks ADD COLUMN {_col} {_decl}")
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bgtask_group ON background_tasks(session_id, group_id)")
            # Crash reconcile: any task still 'running' at boot lost its thread on
            # the previous shutdown — mark it errored so the panel never shows a
            # zombie running forever.
            conn.execute(
                "UPDATE background_tasks SET status='error', "
                "error='Server restart — task lost', "
                "finished_at=strftime('%s','now') WHERE status='running'")
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
                # Fetch version metadata (no content). message_idx anchors each
                # version to the turn that produced it (latest user message's
                # array position at write time) so the UI groups by turn.
                vers = conn.execute(
                    "SELECT version, size, action, created_at, message_idx FROM artifact_versions WHERE artifact_id = ? ORDER BY version",
                    (d["id"],)).fetchall()
                d["versions"] = [{"version": v[0], "size": v[1], "action": v[2], "created_at": v[3], "message_idx": v[4]} for v in vers]
                # Artifact-level anchor: the creating turn (first version).
                d["message_idx"] = d["versions"][0]["message_idx"] if d["versions"] else None
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
    @_db_safe(default=None)
    def set_artifact_visibility_override(artifact_id, override):
        with _db_conn() as conn:
            conn.execute("UPDATE artifacts SET visibility_override = ? WHERE id = ?",
                         (override or "", artifact_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def get_artifact_with_parent_block(artifact_id):
        """Resolve an artifact to (parent_block, visibility_override, parent_label).
        The parent is the producing session (or, for sched-<run> synthetic
        sessions, the schedule). Returns None if the artifact doesn't exist."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
        override = d.get("visibility_override") or ""
        sid = d.get("session_id") or ""
        if sid.startswith("sched-"):
            # synthetic scheduled-run session — parent is the schedule run/def
            import brain as engine
            run_id = sid[len("sched-"):]
            try:
                sconn = engine._sched_conn()
                sconn.row_factory = sqlite3.Row
                hr = sconn.execute("SELECT * FROM schedule_history WHERE id = ?", (run_id,)).fetchone()
            except Exception:
                hr = None
            if hr:
                hr = dict(hr)
                vis = hr.get("visibility") or ""
                if vis:
                    blk = {"owner_user_id": hr.get("owner_user_id") or "",
                           "visibility": vis, "owner_team_id": hr.get("owner_team_id") or "",
                           "extra_member_user_ids": [], "excluded_user_ids": []}
                    return blk, override, f"scheduled run #{run_id}"
                # no snapshot — fall back to the live schedule
                name = hr.get("schedule_name") or ""
                srow = engine._schedule_get_row(name) if name else None
                if srow:
                    return engine._schedule_share_block(srow), override, f"schedule “{name}”"
            return ({"owner_user_id": "", "visibility": "private", "owner_team_id": "",
                     "extra_member_user_ids": [], "excluded_user_ids": []}, override, f"scheduled run #{run_id}")
        info = ChatDB.get_session_info(sid)
        if not info:
            return ({"owner_user_id": d.get("user_id") or "", "visibility": "private",
                     "owner_team_id": "", "extra_member_user_ids": [], "excluded_user_ids": []},
                    override, "(deleted parent)")
        label = (info.get("title") or "").strip() or "untitled chat"
        return session_share_block(info), override, f"“{label}”"

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

    # ── Background-task CRUD ──

    @staticmethod
    @_db_safe(default=None)
    def create_background_task(task_id, session_id, agent_id, model, title, prompt,
                               group_id=None, follow_up=None, parent_task_id=None):
        """Insert a running task. group_id/follow_up are the fan-out fields
        (NULL = standalone). parent_task_id is set when spawned from inside a
        background run (nesting guard — caller refuses to spawn at depth>0)."""
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO background_tasks "
                "(id, session_id, agent_id, model, title, prompt, status, "
                " group_id, follow_up, parent_task_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (task_id, session_id, agent_id, model, title, prompt,
                 group_id or None, follow_up or None, parent_task_id or None))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def set_background_task_turn(task_id, turn_id):
        """Record the sidecar turn_id once the run has started (enables cancel +
        live transcript attach)."""
        with _db_conn() as conn:
            conn.execute(
                "UPDATE background_tasks SET turn_id=? WHERE id=?", (turn_id, task_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def finish_background_task(task_id, status, output="", error="",
                              usage_in=0, usage_out=0, tool_calls=0):
        """Terminal write: status in done|cancelled|error. `output` holds the
        run's full final text (incl. partial on cancel)."""
        with _db_conn() as conn:
            conn.execute(
                "UPDATE background_tasks SET status=?, output=?, error=?, "
                "usage_in=?, usage_out=?, tool_calls=?, finished_at=strftime('%s','now') "
                "WHERE id=?",
                (status, output, error, usage_in, usage_out, tool_calls, task_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def list_background_tasks(session_id):
        """All tasks for a session (panel display), newest first. Excludes the
        full `output`/`prompt` blobs to keep the list light."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, session_id, agent_id, model, title, status, turn_id, error, "
                "usage_in, usage_out, tool_calls, created_at, finished_at, consumed_at, "
                "group_id, follow_up, length(output) AS output_len "
                "FROM background_tasks WHERE session_id=? ORDER BY created_at DESC",
                (session_id,)).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=None)
    def get_background_task(task_id):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    @_db_safe(default=list)
    def pop_unconsumed_background_tasks(session_id):
        """Return finished (done|cancelled) tasks not yet folded into a turn, and
        mark them consumed in the same transaction. The caller injects their
        `output` wire-only into the next turn; consumed_at guarantees each task's
        output reaches the model exactly once."""
        # group_id IS NULL → standalone tasks only. Grouped (fan-out) tasks are
        # delivered via the group path (claim_background_group + the
        # pop_undelivered_groups injection floor), never here — prevents double
        # delivery.
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, status, output, error FROM background_tasks "
                "WHERE session_id=? AND group_id IS NULL "
                "AND status IN ('done','cancelled') AND consumed_at IS NULL "
                "ORDER BY finished_at",
                (session_id,)).fetchall()
            tasks = [dict(r) for r in rows]
            if tasks:
                conn.execute(
                    "UPDATE background_tasks SET consumed_at=strftime('%s','now') "
                    "WHERE session_id=? AND group_id IS NULL "
                    "AND status IN ('done','cancelled') AND consumed_at IS NULL",
                    (session_id,))
                conn.commit()
            return tasks

    @staticmethod
    @_db_safe(default=None)
    def claim_background_group(group_id):
        """ATOMIC join + single-flight. Returns the group's member rows (for
        delivery) IFF: the group exists, EVERY member is terminal
        (done|cancelled|error), and no one has claimed it yet — and in the SAME
        transaction stamps group_done_at so a concurrent finisher's claim returns
        None. This is the "last finisher delivers exactly once" guarantee, enforced
        by the DB rather than by thread timing.

        Returns: list[dict] of members (id,title,status,output,error,follow_up,
        session_id) on a winning claim; None if not-all-terminal or already claimed.

        NOTE: a single task spawned without an explicit group is a group-of-one
        (the runner assigns every task a group_id at spawn — see background_tasks).
        """
        if not group_id:
            return None
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            # Single-statement claim: stamp group_done_at only if unclaimed AND
            # no member is still running. UPDATE...WHERE NOT EXISTS(running) is
            # atomic under SQLite's write lock — exactly one concurrent caller
            # gets rowcount==1.
            cur = conn.execute(
                "UPDATE background_tasks SET group_done_at=strftime('%s','now') "
                "WHERE group_id=? AND group_done_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM background_tasks b2 "
                "                WHERE b2.group_id=? AND b2.status='running')",
                (group_id, group_id))
            if cur.rowcount == 0:
                conn.rollback()
                return None  # still running, or someone else already claimed it
            rows = conn.execute(
                "SELECT id, session_id, title, status, output, error, follow_up "
                "FROM background_tasks WHERE group_id=? ORDER BY created_at",
                (group_id,)).fetchall()
            conn.commit()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def list_background_groups(session_id):
        """Group rollup for the panel: one row per group_id with member counts +
        aggregate status. Standalone tasks (group_id NULL) are omitted here — the
        panel lists those via list_background_tasks as before."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT group_id, COUNT(*) AS total, "
                "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running, "
                "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
                "SUM(CASE WHEN status IN ('error','cancelled') THEN 1 ELSE 0 END) AS failed, "
                "MIN(created_at) AS created_at, MAX(follow_up) AS follow_up "
                "FROM background_tasks WHERE session_id=? AND group_id IS NOT NULL "
                "GROUP BY group_id ORDER BY created_at DESC",
                (session_id,)).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def sweep_stalled_groups(deadline_secs):
        """Group-level partial-delivery guard. Per-task _TIMEOUT_S (1h) bounds the
        absolute worst case, but a group shouldn't wait an hour on one straggler
        once its other members are done. This finds groups that are PARTIALLY done
        (≥1 member terminal) AND still have a running member whose run started more
        than `deadline_secs` ago, and force-marks those stragglers status='error'
        (error='Gruppen-Timeout — Teilergebnis geliefert') so the group becomes
        fully terminal and the normal claim path can deliver it as a partial.

        Returns the affected [(session_id, group_id)] so the caller can claim +
        deliver each. Idempotent: a group already fully terminal isn't touched
        (no running members to mark)."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            # Groups with ≥1 terminal member AND ≥1 running member older than the
            # deadline, not yet group-claimed.
            stalled = conn.execute(
                "SELECT DISTINCT g.session_id, g.group_id FROM background_tasks g "
                "WHERE g.group_id IS NOT NULL AND g.group_done_at IS NULL "
                "AND EXISTS (SELECT 1 FROM background_tasks t WHERE t.group_id=g.group_id "
                "            AND t.status IN ('done','cancelled','error')) "
                "AND EXISTS (SELECT 1 FROM background_tasks r WHERE r.group_id=g.group_id "
                "            AND r.status='running' "
                "            AND r.created_at < strftime('%s','now') - ?)",
                (int(deadline_secs),)).fetchall()
            affected = [(r["session_id"], r["group_id"]) for r in stalled]
            for _sid, gid in affected:
                conn.execute(
                    "UPDATE background_tasks SET status='error', "
                    "error='Gruppen-Timeout — Teilergebnis geliefert', "
                    "finished_at=strftime('%s','now') "
                    "WHERE group_id=? AND status='running'", (gid,))
            if affected:
                conn.commit()
            return affected

    @staticmethod
    @_db_safe(default=None)
    def mark_group_consumed(group_id):
        """Stamp consumed_at on every member of a delivered group, so the
        next-turn injection floor (pop_undelivered_groups) won't re-deliver it.
        Called after a successful proactive group delivery."""
        if not group_id:
            return
        with _db_conn() as conn:
            conn.execute(
                "UPDATE background_tasks SET consumed_at=strftime('%s','now') "
                "WHERE group_id=? AND consumed_at IS NULL", (group_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def pop_undelivered_groups(session_id):
        """Next-turn injection FLOOR for fan-out groups: groups that are fully
        terminal (claimed — group_done_at set) but whose proactive delivery never
        fired (busy-bail), so members are still consumed_at IS NULL. Returns the
        member rows grouped, marks them consumed in the same transaction.
        Ensures a group completing while the user is mid-turn is delivered on
        their NEXT turn rather than silently lost."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, group_id, title, status, output, error, follow_up "
                "FROM background_tasks "
                "WHERE session_id=? AND group_id IS NOT NULL "
                "AND group_done_at IS NOT NULL AND consumed_at IS NULL "
                "ORDER BY group_id, created_at",
                (session_id,)).fetchall()
            members = [dict(r) for r in rows]
            if members:
                conn.execute(
                    "UPDATE background_tasks SET consumed_at=strftime('%s','now') "
                    "WHERE session_id=? AND group_id IS NOT NULL "
                    "AND group_done_at IS NOT NULL AND consumed_at IS NULL",
                    (session_id,))
                conn.commit()
            return members

    @staticmethod
    @_db_safe(default=0)
    def count_unconsumed_background_tasks(session_id):
        """Finished (done|cancelled) tasks not yet folded into a turn. A non-
        consuming PEEK — lets delivery check 'is there anything?' before claiming
        the idle gate, so a 'busy' bail never consumes (and thus never loses)
        tasks. (error-status tasks are delivered via the group path, not this
        standalone pop, so they're excluded here to match pop_unconsumed.)"""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE session_id=? AND "
                "group_id IS NULL AND status IN ('done','cancelled') AND consumed_at IS NULL",
                (session_id,)).fetchone()
            return row[0] if row else 0

    @staticmethod
    @_db_safe(default=0)
    def count_active_background_tasks(session_id):
        """Tasks worth a top-bar badge: running, or finished-but-not-yet-consumed."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE session_id=? AND "
                "(status='running' OR (status IN ('done','cancelled') AND consumed_at IS NULL))",
                (session_id,)).fetchone()
            return row[0] if row else 0

    @staticmethod
    @_db_safe(default=False)
    def delete_background_task(task_id):
        with _db_conn() as conn:
            cur = conn.execute("DELETE FROM background_tasks WHERE id=?", (task_id,))
            conn.commit()
            return cur.rowcount > 0

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
    def update_session_share(session_id, *, visibility=None, team_id=None,
                             extra_member_user_ids=None, excluded_user_ids=None,
                             owner_user_id=None):
        """Update the generic sharing block on a session. Only the kwargs
        passed (non-None) are written. List args are JSON-encoded."""
        import json as _json
        sets, params = [], []
        if visibility is not None:
            sets.append("visibility = ?"); params.append(visibility)
        if team_id is not None:
            sets.append("team_id = ?"); params.append(team_id)
        if extra_member_user_ids is not None:
            sets.append("extra_member_user_ids = ?"); params.append(_json.dumps(list(extra_member_user_ids)))
        if excluded_user_ids is not None:
            sets.append("excluded_user_ids = ?"); params.append(_json.dumps(list(excluded_user_ids)))
        if owner_user_id is not None:
            sets.append("user_id = ?"); params.append(owner_user_id)
        if not sets:
            return
        params.append(session_id)
        with _db_conn() as conn:
            conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
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
    def update_session_research_mode_override(session_id, value):
        """Per-session research-mode override.

        value: None  -> clear override (use project default)
               True  -> force research mode ON for this session
               False -> force research mode OFF for this session
        """
        if value is None:
            stored = None
        else:
            stored = 1 if value else 0
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET research_mode_override = ? WHERE id = ?",
                        (stored, session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_allow_further_web(session_id, value):
        """Per-session 'allow further web search/fetch' flag (manual-search
        escape hatch). value: truthy -> 1 (allow), falsy -> 0 (locked)."""
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET allow_further_web = ? WHERE id = ?",
                        (1 if value else 0, session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_web_basket(session_id, basket_json):
        """Persist the per-session Websuche basket. basket_json is a JSON
        string (list of {url,title,snippet,query,enabled}); '' clears it."""
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET web_basket = ? WHERE id = ?",
                        (basket_json or '', session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def update_session_gdpr_action_pref(session_id, value):
        """Transparent-anonymisation sticky preference (step 6.2).

        value: ''  -> clear (ask each send)
               'anonymise' / 'local_model' / 'continue' -> remember.

        'cancel' is rejected — it would brick the chat. Unknown values are
        coerced to ''.
        """
        if value not in ("anonymise", "local_model", "continue"):
            value = ""
        with _db_conn() as conn:
            conn.execute("UPDATE sessions SET gdpr_action_pref = ? WHERE id = ?",
                        (value, session_id))
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
            cur = conn.execute("INSERT INTO messages (session_id, role, content, metadata) VALUES (?, ?, ?, ?)",
                               (session_id, role, c, meta))
            conn.commit()
            return cur.lastrowid

    @staticmethod
    @_db_safe(default=None)
    def update_message(message_id, content=None, metadata=None):
        """In-place update of a message row. Used to persist a streaming
        assistant reply incrementally (write the row early, UPDATE its content
        as deltas arrive, finalize metadata on finish_reason)."""
        sets, vals = [], []
        if content is not None:
            c = json.dumps(content) if not isinstance(content, str) else content
            sets.append("content = ?"); vals.append(c)
        if metadata is not None:
            sets.append("metadata = ?"); vals.append(json.dumps(metadata) if metadata else "")
        if not sets:
            return
        vals.append(message_id)
        with _db_conn() as conn:
            conn.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def delete_message(message_id):
        with _db_conn() as conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def set_streaming_text(session_id, text, metadata=None):
        """Persist the in-flight assistant reply for a running turn.
        Pass text='' (and metadata=None) to clear once the turn finalizes."""
        meta = json.dumps(metadata) if metadata else ""
        with _db_conn() as conn:
            conn.execute(
                "UPDATE sessions SET streaming_text = ?, streaming_meta = ? WHERE id = ?",
                (text or "", meta, session_id))
            conn.commit()

    @staticmethod
    @_db_safe(default=tuple)
    def get_streaming_text(session_id):
        """Return (text, metadata_dict). ('' , None) if no in-flight reply."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT streaming_text, streaming_meta FROM sessions WHERE id = ?",
                (session_id,)).fetchone()
        if not row or not row[0]:
            return ("", None)
        meta = None
        if row[1]:
            try:
                meta = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                meta = None
        return (row[0], meta)

    @staticmethod
    @_db_safe(default=None)
    def set_active_turn(session_id, turn_id, model):
        """Record that a sidecar turn is in flight for this session. On Brain
        restart the recovery thread reads this and re-attaches to the sidecar."""
        with _db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_turns (session_id, turn_id, model, started_at) "
                "VALUES (?, ?, ?, strftime('%s','now'))",
                (session_id, turn_id, model or ""))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def clear_active_turn(session_id, turn_id=None):
        """Delete the active-turn row when the proxy finishes draining the turn.
        If turn_id is given, only clear if it matches (avoids racing a new turn
        that started for the same session)."""
        with _db_conn() as conn:
            if turn_id:
                conn.execute(
                    "DELETE FROM active_turns WHERE session_id = ? AND turn_id = ?",
                    (session_id, turn_id))
            else:
                conn.execute("DELETE FROM active_turns WHERE session_id = ?",
                             (session_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def list_active_turns():
        """Return [(session_id, turn_id, model, started_at), ...] for all
        rows. Called on Brain startup by the recovery thread."""
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT session_id, turn_id, model, started_at FROM active_turns"
            ).fetchall()
        return [tuple(r) for r in rows]

    # ── Pseudonym maps (transparent anonymisation) ──

    @staticmethod
    @_db_safe(default=None)
    def save_pseudonym_map(mapping_id, session_id, turn_id, nonce, ciphertext):
        """Upsert an encrypted pseudonym mapping. `nonce` and `ciphertext` are
        raw bytes from `pseudonymizer.encrypt_mapping`. On conflict the row's
        ciphertext + updated_at are refreshed; created_at is preserved."""
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO pseudonym_maps "
                "(mapping_id, session_id, turn_id, nonce, ciphertext, updated_at) "
                "VALUES (?, ?, ?, ?, ?, strftime('%s','now')) "
                "ON CONFLICT(mapping_id) DO UPDATE SET "
                "  nonce = excluded.nonce, "
                "  ciphertext = excluded.ciphertext, "
                "  turn_id = excluded.turn_id, "
                "  updated_at = strftime('%s','now')",
                (mapping_id, session_id, turn_id or "", nonce, ciphertext))
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def load_pseudonym_map(mapping_id):
        """Return `(nonce_bytes, ciphertext_bytes)` for `mapping_id`, or None
        if the row is missing."""
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT nonce, ciphertext FROM pseudonym_maps WHERE mapping_id = ?",
                (mapping_id,)).fetchone()
        if not row:
            return None
        return (bytes(row[0]), bytes(row[1]))

    @staticmethod
    @_db_safe(default=list)
    def list_pseudonym_maps_for_session(session_id):
        """Return `[(mapping_id, turn_id, created_at), ...]` — used by the
        chat-reload path to figure out which maps to rehydrate so historical
        messages stay de-anonymised."""
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT mapping_id, turn_id, created_at "
                "FROM pseudonym_maps WHERE session_id = ? "
                "ORDER BY created_at",
                (session_id,)).fetchall()
        return [tuple(r) for r in rows]

    @staticmethod
    @_db_safe(default=None)
    def delete_pseudonym_map(mapping_id):
        """Drop a single mapping row (e.g. after a failed turn rolls back, or
        when an admin explicitly purges history)."""
        with _db_conn() as conn:
            conn.execute("DELETE FROM pseudonym_maps WHERE mapping_id = ?",
                         (mapping_id,))
            conn.commit()

    @staticmethod
    @_db_safe(default=0)
    def purge_orphan_pseudonym_maps(max_age_seconds=None):
        """Cleanup pass. Drops maps whose session no longer exists, and
        (if `max_age_seconds` is set) maps older than that threshold whose
        session is no longer active in `active_turns`.

        Returns the number of rows deleted. Called from boot recovery so
        stale maps from interrupted turns don't accumulate."""
        with _db_conn() as conn:
            # Sessions that have been deleted but whose maps somehow survived
            # (shouldn't happen because delete_session cascades — defensive).
            deleted = conn.execute(
                "DELETE FROM pseudonym_maps WHERE session_id NOT IN "
                "(SELECT id FROM sessions)"
            ).rowcount or 0
            if max_age_seconds is not None and max_age_seconds > 0:
                cutoff = int(time.time()) - int(max_age_seconds)
                deleted += conn.execute(
                    "DELETE FROM pseudonym_maps "
                    "WHERE updated_at < ? AND session_id NOT IN "
                    "(SELECT session_id FROM active_turns)",
                    (cutoff,)).rowcount or 0
            conn.commit()
        return deleted

    @staticmethod
    @_db_safe(default=list)
    def load_messages(session_id, include_compacted=False):
        with _db_conn() as conn:
            if include_compacted:
                rows = conn.execute(
                    "SELECT id, role, content, metadata, compacted, created_at FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, metadata, compacted, created_at FROM messages WHERE session_id = ? AND (compacted = 0 OR compacted IS NULL) ORDER BY id",
                    (session_id,)
                ).fetchall()
            messages = []
            for mid, role, content, metadata, compacted, created_at in rows:
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
                if created_at is not None:
                    msg["created_at"] = created_at
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

    # MemPalace chat-sync cursor methods extracted to server_lib/mempalace_sync.py.
    # Thin staticmethod wrappers delegate so ChatDB.mempalace_* callers are untouched.
    mempalace_sessions_needing_sync = staticmethod(_mp_sync.mempalace_sessions_needing_sync)
    mempalace_load_new_messages = staticmethod(_mp_sync.mempalace_load_new_messages)
    mempalace_last_user_id_before = staticmethod(_mp_sync.mempalace_last_user_id_before)
    mempalace_update_cursor = staticmethod(_mp_sync.mempalace_update_cursor)

    @staticmethod
    @_db_safe(default=list)
    def list_sessions(agent_id=None, status=None, project=None, visible_user_ids=None, visible_team_ids=None, project_id=None, caller_user_id=None):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            q = ("SELECT s.*, "
                 "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id AND (m.compacted = 0 OR m.compacted IS NULL)) as message_count, "
                 "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id AND m.metadata LIKE '%\"files\"%') as has_attachments "
                 "FROM sessions s WHERE 1=1")
            params = []
            # Multi-user: filter by visible user IDs (None = admin sees all).
            # Visibility matrix (generic sharing model):
            #  - user_id IN visible → always visible (owner / team-head's members)
            #  - visibility='team' AND team_id IN visible_team_ids → visible
            #  - visibility='global' → visible (then post-filtered for exclusions)
            #  - caller ∈ extra_member_user_ids → visible (post-filtered, JSON col)
            #  - no user_id (legacy) → visible (legacy anonymous sessions)
            _post_filter_grants = False
            if visible_user_ids is not None:
                placeholders = ",".join("?" * len(visible_user_ids)) or "''"
                team_clause = ""
                if visible_team_ids:
                    tplaceholders = ",".join("?" * len(visible_team_ids))
                    team_clause = f" OR (s.visibility = 'team' AND s.team_id IN ({tplaceholders}))"
                # Over-fetch: include global + anything that *might* carry an
                # extra-grant for the caller (cheap JSON LIKE pre-filter), then
                # decode JSON in Python below to confirm.
                grant_clause = ""
                if caller_user_id:
                    grant_clause = " OR s.extra_member_user_ids LIKE ?"
                    _post_filter_grants = True
                q += (f" AND (s.user_id IN ({placeholders})"
                      f" OR s.user_id = '' OR s.user_id IS NULL"
                      f" OR s.visibility = 'global'"
                      f"{team_clause}{grant_clause})")
                params.extend(visible_user_ids)
                if visible_team_ids:
                    params.extend(visible_team_ids)
                if caller_user_id:
                    params.append(f'%"{caller_user_id}"%')
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
            # Confirm the JSON over-fetch + drop global rows that exclude the
            # caller. Admin (visible_user_ids is None) skips this entirely.
            if visible_user_ids is not None:
                vset = set(visible_user_ids)
                vteams = set(visible_team_ids or [])
                def _row_visible(d):
                    owner = d.get("user_id") or ""
                    if not owner:
                        return True
                    if owner in vset:
                        return True
                    vis = d.get("visibility") or "user"
                    extras = []
                    raw = d.get("extra_member_user_ids")
                    if raw:
                        try:
                            extras = json.loads(raw) if isinstance(raw, str) else (raw or [])
                        except Exception:
                            extras = []
                    if caller_user_id and caller_user_id in extras:
                        return True
                    if vis == "team":
                        return (d.get("team_id") or "") in vteams
                    if vis == "global":
                        excl = []
                        rawx = d.get("excluded_user_ids")
                        if rawx:
                            try:
                                excl = json.loads(rawx) if isinstance(rawx, str) else (rawx or [])
                            except Exception:
                                excl = []
                        return not (caller_user_id and caller_user_id in excl)
                    return False
                rows = [r for r in rows if _row_visible(dict(r))]
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
                        import brain as engine
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
            # Transparent-anonymisation maps: drop with the session — keeping
            # them would orphan encrypted blobs nobody can act on.
            conn.execute("DELETE FROM pseudonym_maps WHERE session_id = ?", (session_id,))
            # NOTE: helpdesk_history is per-USER, not per-session — deliberately
            # NOT dropped here (deleting a chat must not wipe Brainy's history).
            conn.commit()
        _purge_mempalace_session(session_id)

    # ── Helpdesk ("Brainy") history ──
    # PER-USER, not per-session: Brainy is a personal assistant with ONE
    # continuous conversation per user, carried across all views/sessions.
    # Private to the user (not shared by project/team; admins read it only via
    # the audit log). The legacy `session_id` column is kept for the schema but
    # is no longer the key — left empty.

    @staticmethod
    @_db_safe(default=list)
    def load_helpdesk_history(user_id, limit=400):
        """Return the user's Brainy conversation, oldest first (incl.
        context_label for context-filtered replay)."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, created_at, context_label FROM helpdesk_history "
                "WHERE user_id = ? ORDER BY id ASC LIMIT ?",
                (user_id or "", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def load_helpdesk_history_page(user_id, before_id=None, limit=20):
        """Paginated, NEWEST-first page of the user's Brainy history (for the UI
        — distinct from load_helpdesk_history which is oldest-first for building
        the model turn). `before_id` is a cursor: return rows with id < before_id
        (older than what's already shown). Includes id + created_at so the client
        can paginate, group by time, and delete. Returns up to `limit` rows
        newest-first; the caller reverses for display."""
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            if before_id:
                rows = conn.execute(
                    "SELECT id, role, content, created_at, context_label FROM helpdesk_history "
                    "WHERE user_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                    (user_id or "", int(before_id), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, created_at, context_label FROM helpdesk_history "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id or "", int(limit)),
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=False)
    def delete_helpdesk_message(user_id, msg_id):
        """Delete a single Brainy row (scoped to the user, so one user can't
        delete another's). Returns True if a row was removed."""
        with _db_conn() as conn:
            cur = conn.execute(
                "DELETE FROM helpdesk_history WHERE user_id = ? AND id = ?",
                (user_id or "", int(msg_id)),
            )
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    @_db_safe(default=0)
    def delete_helpdesk_range(user_id, start_ts, end_ts):
        """Delete all of the user's Brainy rows with created_at in [start_ts,
        end_ts) — the group-delete path. Returns the number of rows removed."""
        with _db_conn() as conn:
            cur = conn.execute(
                "DELETE FROM helpdesk_history WHERE user_id = ? "
                "AND created_at >= ? AND created_at < ?",
                (user_id or "", float(start_ts), float(end_ts)),
            )
            conn.commit()
            return cur.rowcount

    @staticmethod
    @_db_safe(default=None)
    def append_helpdesk_message(user_id, role, content, context_label=""):
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO helpdesk_history (session_id, user_id, role, content, context_label) "
                "VALUES ('', ?, ?, ?, ?)",
                (user_id or "", role, content or "", context_label or ""),
            )
            conn.commit()

    @staticmethod
    @_db_safe(default=None)
    def clear_helpdesk_history(user_id):
        with _db_conn() as conn:
            conn.execute("DELETE FROM helpdesk_history WHERE user_id = ?", (user_id or "",))
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
    def artifact_message_idx(session_id):
        """0-based array position of the most recent user message in this
        session — the anchor of the turn currently producing artifacts.

        Artifacts are written mid-turn, before the assistant reply is
        persisted, so the latest user message already on disk is the
        producing turn's opening message. The client builds its message
        array ordered by id, so this position maps to a turn via
        `turnNumForMessageIdx`. Returns None when no user message exists
        yet (the caller stores NULL, the client falls back to 'ungrouped').
        """
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role IN ('user','human') "
                "ORDER BY id DESC LIMIT 1",
                (session_id,)
            ).fetchone()
            if not row:
                return None
            pos = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id <= ?",
                (session_id, row[0])
            ).fetchone()
            return (int(pos[0]) - 1) if pos and pos[0] else None

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
            else:
                # Global archive — never touch project-linked sessions.
                conditions.append("(project IS NULL OR project = '')")
                conditions.append("(project_id IS NULL OR project_id = '')")
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
            else:
                # Global unarchive — never touch project-linked sessions.
                conditions.append("(project IS NULL OR project = '')")
                conditions.append("(project_id IS NULL OR project_id = '')")
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
            else:
                # Global delete — never touch project-linked sessions.
                conditions.append("(project IS NULL OR project = '')")
                conditions.append("(project_id IS NULL OR project_id = '')")
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


class ClassificationDB:
    """Persist document-classification scan results to chats.db.

    Schema in CHAT_DB → classification_scans. See engine/classification.py
    for the detector that produces these results.
    """

    @staticmethod
    @_db_safe(default=None)
    def insert(*, scan_id: str, user_id: str, source_kind: str,
               source_label: str, file_count: int,
               summary_json: str, evidence_json: str) -> None:
        with _db_conn() as conn:
            conn.execute(
                "INSERT INTO classification_scans "
                "(scan_id, user_id, source_kind, source_label, file_count, "
                " summary_json, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scan_id, user_id or "", source_kind, source_label or "",
                 int(file_count), summary_json, evidence_json),
            )
            conn.commit()

    @staticmethod
    @_db_safe(default=list)
    def list_for_user(user_id: str, *, admin: bool = False,
                       limit: int = 100) -> list:
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            if admin:
                rows = conn.execute(
                    "SELECT scan_id, user_id, created_at, source_kind, "
                    "source_label, file_count, summary_json "
                    "FROM classification_scans "
                    "ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT scan_id, user_id, created_at, source_kind, "
                    "source_label, file_count, summary_json "
                    "FROM classification_scans "
                    "WHERE user_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (user_id or "", int(limit)),
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    @_db_safe(default=None)
    def get(scan_id: str, user_id: str, *, admin: bool = False):
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            if admin:
                row = conn.execute(
                    "SELECT * FROM classification_scans WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM classification_scans "
                    "WHERE scan_id = ? AND user_id = ?",
                    (scan_id, user_id or ""),
                ).fetchone()
            return dict(row) if row else None

    @staticmethod
    @_db_safe(default=None)
    def delete(scan_id: str, user_id: str, *, admin: bool = False) -> None:
        with _db_conn() as conn:
            if admin:
                conn.execute(
                    "DELETE FROM classification_scans WHERE scan_id = ?",
                    (scan_id,),
                )
            else:
                conn.execute(
                    "DELETE FROM classification_scans "
                    "WHERE scan_id = ? AND user_id = ?",
                    (scan_id, user_id or ""),
                )
            conn.commit()
