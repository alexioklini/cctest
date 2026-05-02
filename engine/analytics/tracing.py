# Extracted from claude_cli.py — execution trace manager

import datetime
import json
import logging
import os
import sqlite3
import threading
import time

from engine.agents import AGENTS_DIR  # noqa: F401 — needed at module level

# --- Observability: Trace Manager ---

TRACES_DB = os.path.join(AGENTS_DIR, "main", "traces.db")

_traces_db_pool = threading.local()


def _traces_conn():
    """Thread-local SQLite connection for the traces DB."""
    conn = getattr(_traces_db_pool, "conn", None)
    if conn is None:
        conn = sqlite3.connect(TRACES_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _traces_db_pool.conn = conn
    return conn


class TraceManager:
    """Thread-safe span-based tracing with SQLite persistence."""

    def __init__(self):
        os.makedirs(os.path.dirname(TRACES_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _traces_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_id TEXT,
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_ms INTEGER,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    model TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_trace ON traces(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_parent ON traces(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_type ON traces(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id)")
            conn.commit()

    def start_span(self, span_type: str, name: str, agent: str = "main",
                   model: str = "", parent_id: str | None = None,
                   trace_id: str | None = None,
                   session_id: str | None = None) -> dict:
        """Start a new span. Returns span dict with id, start time, etc."""
        import uuid as _uuid
        span_id = _uuid.uuid4().hex[:16]
        if not trace_id:
            trace_id = _uuid.uuid4().hex[:16]
        now = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        return {
            "id": span_id,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "agent": agent,
            "session_id": session_id,
            "type": span_type,
            "name": name,
            "model": model,
            "status": "ok",
            "started_at": now,
            "_start_time": time.time(),
            "tokens_in": 0,
            "tokens_out": 0,
            "metadata": {},
        }

    def end_span(self, span: dict, status: str = "ok",
                 result_summary: str = "", tokens_in: int = 0, tokens_out: int = 0):
        """End a span: compute duration and persist to DB."""
        now = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        duration_ms = int((time.time() - span.get("_start_time", time.time())) * 1000)
        span["ended_at"] = now
        span["duration_ms"] = duration_ms
        span["status"] = status
        if tokens_in:
            span["tokens_in"] = tokens_in
        if tokens_out:
            span["tokens_out"] = tokens_out
        if result_summary:
            span["metadata"]["result_summary"] = result_summary[:500]
        try:
            with _traces_conn() as conn:
                conn.execute("""
                    INSERT INTO traces (id, trace_id, parent_id, agent, session_id,
                        type, name, status, started_at, ended_at, duration_ms,
                        tokens_in, tokens_out, model, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    span["id"], span["trace_id"], span.get("parent_id"),
                    span["agent"], span.get("session_id"),
                    span["type"], span["name"], span["status"],
                    span["started_at"], span["ended_at"], duration_ms,
                    span.get("tokens_in", 0), span.get("tokens_out", 0),
                    span.get("model", ""),
                    json.dumps(span.get("metadata", {})),
                ))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace write error: {e}")

    def get_traces(self, agent: str | None = None, hours: int = 24,
                   limit: int = 50) -> list[dict]:
        """Get recent root-level traces (parent_id IS NULL)."""
        try:
            with _traces_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE started_at >= datetime('now', ?) AND parent_id IS NULL"
                params: list = [f"-{hours} hours"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                params.append(limit)
                rows = conn.execute(f"""
                    SELECT t.*, (SELECT COUNT(*) FROM traces c WHERE c.trace_id = t.trace_id) as span_count
                    FROM traces t {where}
                    ORDER BY started_at DESC LIMIT ?
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace read error: {e}")
            return []

    def get_trace(self, trace_id: str) -> list[dict]:
        """Get all spans for a trace, ordered by start time."""
        try:
            with _traces_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM traces WHERE trace_id = ? ORDER BY started_at",
                    (trace_id,)
                ).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace read error: {e}")
            return []

    def get_spans_for_session(self, session_id: str) -> list[dict]:
        """Get all spans tied to a session_id (fallback pivot when trace_id
        wasn't captured up-front — e.g. old scheduled runs pre-migration)."""
        try:
            with _traces_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM traces WHERE session_id = ? ORDER BY started_at",
                    (session_id,)
                ).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace read error: {e}")
            return []

    def cleanup(self, retention_days: int = 30):
        """Delete traces older than retention period."""
        try:
            with _traces_conn() as conn:
                conn.execute(
                    "DELETE FROM traces WHERE started_at < datetime('now', ?)",
                    (f"-{retention_days} days",)
                )
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace cleanup error: {e}")


_trace_manager: TraceManager | None = None
