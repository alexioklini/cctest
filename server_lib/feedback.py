"""User feedback: per-user 👍/👎 ratings (+ optional comment) on any assistant
response or result, across every surface (chat, brainy/helpdesk, workflow runs,
scheduled runs, translations, classification scans).

Storage lives in `chats.db` alongside sessions/favourites. A feedback row is
keyed by (surface, target_id, user_id) — a user re-rating the same response
UPSERTs their own row. `context_snapshot` stores a short copy of the rated
response/title so an admin can examine feedback without reconstructing the
original surface.
"""
import sqlite3
import time

from server_lib.db import _db_conn, _db_safe


SURFACES = (
    "chat",
    "brainy",
    "workflow",
    "schedule",
    "translation",
    "classification",
)

RATINGS = ("up", "down")

# A feedback row is the thread anchor (rating + first comment). Further
# back-and-forth lives in feedback_messages, one row per one-line message.
MSG_ROLES = ("user", "admin")
MSG_CAP = 300

_KEYS = ("id", "surface", "target_id", "session_id", "user_id", "rating",
         "comment", "context_snapshot", "created_at", "updated_at")

_MSG_KEYS = ("id", "feedback_id", "author_role", "author_user_id", "text",
             "created_at")


class FeedbackDB:
    """SQLite persistence for feedback rows."""

    @staticmethod
    def init():
        with _db_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    surface TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    rating TEXT NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    context_snapshot TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(surface, target_id, user_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_surface ON feedback(surface)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_rating ON feedback(rating)")
            # Threaded conversation hanging off a feedback row. author_role
            # distinguishes the original rater ('user') from an admin reply
            # ('admin'); author_user_id keeps the concrete author for display.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feedback_id INTEGER NOT NULL,
                    author_role TEXT NOT NULL,
                    author_user_id TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fbmsg_fid ON feedback_messages(feedback_id)")
            # Per-user read cursor so the rater gets an unread dot when an admin
            # replies. last_seen_at is a Unix timestamp; a message newer than it
            # counts as unread.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback_seen (
                    feedback_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    last_seen_at REAL NOT NULL,
                    UNIQUE(feedback_id, user_id)
                )
            """)
            conn.commit()

    # ── Mutations ──

    @staticmethod
    @_db_safe(default=None)
    def upsert(surface: str, target_id: str, session_id: str, user_id: str,
               rating: str, comment: str = "", context_snapshot: str = "") -> dict | None:
        """Insert or overwrite this user's feedback on one response. Returns the
        row dict. A re-rating updates rating/comment/snapshot/updated_at but
        preserves the original created_at."""
        if surface not in SURFACES:
            return {"error": f"invalid surface '{surface}'"}
        if rating not in RATINGS:
            return {"error": f"invalid rating '{rating}'"}
        if not target_id:
            return {"error": "target_id required"}
        now = time.time()
        with _db_conn() as conn:
            cur = conn.execute("""
                INSERT INTO feedback
                    (surface, target_id, session_id, user_id, rating,
                     comment, context_snapshot, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(surface, target_id, user_id) DO UPDATE SET
                    rating = excluded.rating,
                    comment = excluded.comment,
                    context_snapshot = excluded.context_snapshot,
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
            """, (surface, target_id, session_id or "", user_id or "", rating,
                  comment or "", context_snapshot or "", now, now))
            conn.commit()
            fb_id = cur.lastrowid
            # On a conflict-update, lastrowid points at the conflicting row's
            # rowid in SQLite, but be defensive and look it up by the key.
            if not fb_id:
                row = conn.execute("""
                    SELECT id FROM feedback
                    WHERE surface=? AND target_id=? AND user_id=?
                """, (surface, target_id, user_id or "")).fetchone()
                fb_id = row[0] if row else None
        return FeedbackDB.get(fb_id) if fb_id else None

    @staticmethod
    @_db_safe(default=None)
    def get(fb_id: int) -> dict | None:
        with _db_conn() as conn:
            row = conn.execute(f"""
                SELECT {', '.join(_KEYS)} FROM feedback WHERE id = ?
            """, (fb_id,)).fetchone()
        if not row:
            return None
        return dict(zip(_KEYS, row))

    @staticmethod
    @_db_safe(default=False)
    def remove(fb_id: int) -> bool:
        """Delete a single feedback row. Returns True if a row was removed."""
        with _db_conn() as conn:
            cur = conn.execute("DELETE FROM feedback WHERE id = ?", (fb_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── Thread (back-and-forth conversation) ──

    @staticmethod
    @_db_safe(default=None)
    def add_message(feedback_id: int, author_role: str, author_user_id: str,
                    text: str) -> dict | None:
        """Append one one-line message to a feedback thread. Returns the row.
        Verifies the feedback anchor exists; rejects bad role / empty text."""
        if author_role not in MSG_ROLES:
            return {"error": f"invalid author_role '{author_role}'"}
        text = (text or "").strip()[:MSG_CAP]
        if not text:
            return {"error": "text required"}
        now = time.time()
        with _db_conn() as conn:
            anchor = conn.execute(
                "SELECT id FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
            if not anchor:
                return {"error": "feedback not found"}
            cur = conn.execute("""
                INSERT INTO feedback_messages
                    (feedback_id, author_role, author_user_id, text, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (feedback_id, author_role, author_user_id or "", text, now))
            # Bump the anchor's updated_at so a fresh reply re-sorts it to the
            # top of the admin list and the changed-time reflects activity.
            conn.execute("UPDATE feedback SET updated_at = ? WHERE id = ?",
                         (now, feedback_id))
            conn.commit()
            msg_id = cur.lastrowid
        return FeedbackDB._get_message(msg_id) if msg_id else None

    @staticmethod
    @_db_safe(default=None)
    def _get_message(msg_id: int) -> dict | None:
        with _db_conn() as conn:
            row = conn.execute(f"""
                SELECT {', '.join(_MSG_KEYS)} FROM feedback_messages WHERE id = ?
            """, (msg_id,)).fetchone()
        return dict(zip(_MSG_KEYS, row)) if row else None

    @staticmethod
    @_db_safe(default=list)
    def thread(feedback_id: int) -> list[dict]:
        """All thread messages for one feedback row, oldest first."""
        with _db_conn() as conn:
            rows = conn.execute(f"""
                SELECT {', '.join(_MSG_KEYS)} FROM feedback_messages
                WHERE feedback_id = ? ORDER BY id
            """, (feedback_id,)).fetchall()
        return [dict(zip(_MSG_KEYS, r)) for r in rows]

    @staticmethod
    @_db_safe(default=False)
    def mark_seen(feedback_id: int, user_id: str) -> bool:
        """Record that this user has read the thread up to now."""
        now = time.time()
        with _db_conn() as conn:
            conn.execute("""
                INSERT INTO feedback_seen (feedback_id, user_id, last_seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(feedback_id, user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """, (feedback_id, user_id or "", now))
            conn.commit()
        return True

    @staticmethod
    @_db_safe(default=int)
    def unread_count(feedback_id: int, user_id: str) -> int:
        """Admin messages newer than the user's read cursor (the unread dot)."""
        with _db_conn() as conn:
            seen = conn.execute(
                "SELECT last_seen_at FROM feedback_seen WHERE feedback_id = ? AND user_id = ?",
                (feedback_id, user_id or "")).fetchone()
            since = seen[0] if seen else 0.0
            row = conn.execute("""
                SELECT COUNT(*) FROM feedback_messages
                WHERE feedback_id = ? AND author_role = 'admin' AND created_at > ?
            """, (feedback_id, since)).fetchone()
        return row[0] if row else 0

    # ── Reads ──

    @staticmethod
    @_db_safe(default=list)
    def list(surface: str | None = None, rating: str | None = None) -> list[dict]:
        """Admin list of all feedback, newest-changed first, optionally filtered
        by surface and/or rating."""
        clauses, params = [], []
        if surface:
            clauses.append("surface = ?"); params.append(surface)
        if rating:
            clauses.append("rating = ?"); params.append(rating)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with _db_conn() as conn:
            rows = conn.execute(f"""
                SELECT {', '.join(_KEYS)} FROM feedback{where}
                ORDER BY updated_at DESC
            """, params).fetchall()
        out = [dict(zip(_KEYS, r)) for r in rows]
        # Attach each row's thread so the admin tab can render + reply inline.
        for r in out:
            r["thread"] = FeedbackDB.thread(r["id"])
        return out

    @staticmethod
    @_db_safe(default=list)
    def find_mine(user_id: str, surface: str | None = None,
                  session_id: str | None = None) -> list[dict]:
        """The caller's own feedback rows, optionally scoped by surface and
        session_id — used to restore the highlighted-thumb state on reload."""
        clauses, params = ["user_id = ?"], [user_id or ""]
        if surface:
            clauses.append("surface = ?"); params.append(surface)
        if session_id:
            clauses.append("session_id = ?"); params.append(session_id)
        with _db_conn() as conn:
            rows = conn.execute(f"""
                SELECT {', '.join(_KEYS)} FROM feedback
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
            """, params).fetchall()
        out = [dict(zip(_KEYS, r)) for r in rows]
        # Carry the message count + unread (admin replies the user hasn't seen)
        # so the widget can show a thread badge / unread dot without a second
        # round-trip per item.
        for r in out:
            r["msg_count"] = len(FeedbackDB.thread(r["id"]))
            r["unread"] = FeedbackDB.unread_count(r["id"], user_id or "")
        return out
