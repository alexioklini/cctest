# MemPalace chat-sync cursor helpers.
# Extracted from server_lib/db.py — self-contained around the
# `chat_mempalace_sync(session_id, last_message_id, last_summary_hash)` table.
# Tracks which messages have been mirrored into MemPalace, per session.
#
# Decoupled from db.py at import time to avoid a circular import: the shared
# connection pool (_db_conn) is reached via a function-local import (call-time,
# not import-time), and the tiny error-swallowing decorator is defined locally.
# db.py imports these functions and re-exposes them as ChatDB staticmethods, so
# existing `ChatDB.mempalace_*` callers are untouched.
import json
import sqlite3


def _db_safe(default=None):
    """Decorator: catch SQLite errors and return default instead of crashing.

    Local copy (kept identical to db._db_safe) so this module imports cleanly
    regardless of which module the interpreter loads first.
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (sqlite3.Error, OSError):
                import traceback
                traceback.print_exc()
                return default() if callable(default) else default
        return wrapper
    return decorator


def _db_conn(db_path=None):
    from server_lib.db import _db_conn as _conn
    return _conn(db_path)


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
