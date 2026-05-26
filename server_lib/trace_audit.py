#!/usr/bin/env python3
"""Trace manager + audit-trail subsystem (extracted from brain.py, A5).

Two independent observability subsystems, each owning a thread-local SQLite
DB pool:

- TraceManager  -> traces.db   (span-based LLM/tool call tracing)
- AuditLog      -> audit.db    (append-only tool-action audit trail)

Low-level module: NO top-level `import brain` (server_lib is below brain in
the import DAG). Depends only on stdlib. The runtime singletons
(`_trace_manager`, `_audit_log`) are instantiated by server.py and bound onto
the brain module namespace (`engine._trace_manager = engine.TraceManager()`,
`engine._audit_log = engine.AuditLog()`); brain.py re-exports the symbols
defined here so every `brain._audit_log` / `engine._audit_log` /
`_brain._audit_log` reader resolves through brain's module attribute.
"""

import datetime
import json
import logging
import os
import sqlite3
import threading
import time

# Repo-root/agents/main — matches brain.AGENTS_DIR (computed there from
# brain.py's __file__). This module sits one dir deeper (server_lib/), so go
# up two levels.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(_REPO_ROOT, "agents")


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
                 result_summary: str = "", tokens_in: int = 0, tokens_out: int = 0,
                 full_result: str = ""):
        """End a span: compute duration and persist to DB.

        `result_summary` is the short preview (capped at 500 chars) shown
        inline. `full_result`, when provided, stores the complete tool output
        (capped at 100k to bound DB growth) under metadata.full_result so a
        run-detail inspector can show the expandable full result — the same
        the model received — instead of just the first 500 chars."""
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
        if full_result:
            span["metadata"]["full_result"] = full_result[:100000]
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


# --- Audit Trail ---

AUDIT_DB = os.path.join(AGENTS_DIR, "main", "audit.db")

_audit_db_pool = threading.local()


def _audit_conn():
    """Thread-local SQLite connection for the audit DB."""
    conn = getattr(_audit_db_pool, "conn", None)
    if conn is None:
        conn = sqlite3.connect(AUDIT_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _audit_db_pool.conn = conn
    return conn


_AUDIT_ACTION_MAP = {
    "read_file": "file_read",
    "write_file": "file_write",
    "edit_file": "file_write",
    "execute_command": "command_execute",
    "gmail_send": "email_send",
    "gmail_reply": "email_reply",
    "gmail_inbox": "email_read",
    "gmail_read": "email_read",
    "gmail_search": "email_read",
    "web_fetch": "web_fetch",
    "exa_search": "web_search",
    "memory_store": "memory_store",
    "memory_delete": "memory_delete",
    "memory_recall": "memory_recall",
    "memory_shared": "memory_shared",
    "delegate_task": "delegation",
    "task_cancel": "task_cancel",
    "use_skill": "skill_use",
    "list_directory": "file_read",
    "search_files": "file_read",
    "mcp_connect": "mcp_tool_call",
    "mcp_disconnect": "mcp_tool_call",
    "mcp_servers": "mcp_tool_call",
}


def _audit_summarize_args(tool_name: str, args: dict) -> str:
    """Generate human-readable summary of tool arguments, max 200 chars."""
    if tool_name == "execute_command":
        return args.get("command", "")[:200]
    elif tool_name in ("gmail_send", "gmail_reply"):
        return f"To: {args.get('to', '?')}, Subject: {args.get('subject', '?')}"[:200]
    elif tool_name in ("write_file", "edit_file"):
        content = args.get("content", "")
        return f"{args.get('path', '?')} ({len(content)} bytes)"[:200]
    elif tool_name == "read_file":
        return f"{args.get('path', '?')}"[:200]
    elif tool_name == "delegate_task":
        return f"-> {args.get('agent', '?')}: {args.get('task', '')[:100]}"[:200]
    elif tool_name in ("memory_store", "memory_delete"):
        return f"{args.get('name', '?')}"[:200]
    elif tool_name in ("memory_recall", "memory_shared"):
        return f"query={args.get('query', '?')}"[:200]
    elif tool_name == "exa_search":
        return f"query={args.get('query', '')}"[:200]
    elif tool_name == "web_fetch":
        return f"{args.get('url', '?')}"[:200]
    elif tool_name == "use_skill":
        return f"skill={args.get('skill', '?')}"[:200]
    elif tool_name == "list_directory":
        return f"{args.get('path', '.')}"[:200]
    elif tool_name == "search_files":
        return f"pattern={args.get('pattern', '?')} in {args.get('path', '.')}"[:200]
    else:
        return str(args)[:200]


def _audit_summarize_result(tool_name: str, result_str: str) -> str:
    """Generate human-readable result summary, max 200 chars."""
    try:
        rdata = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return str(result_str)[:200]
    if isinstance(rdata, str):
        return rdata[:200]
    if rdata.get("error"):
        return f"ERROR: {rdata['error']}"[:200]
    if tool_name == "execute_command":
        ec = rdata.get("exit_code", -1)
        return f"exit_code={ec}"[:200]
    if tool_name == "exa_search":
        return f"{rdata.get('result_count', 0)} results"[:200]
    if tool_name in ("memory_recall", "memory_shared"):
        return f"{rdata.get('count', 0)} memories"[:200]
    if tool_name == "delegate_task":
        return f"{rdata.get('agent', '')} responded"[:200]
    return str(rdata)[:200]


class AuditLog:
    """Append-only audit log with SQLite persistence. No UPDATE or DELETE."""

    def __init__(self):
        os.makedirs(os.path.dirname(AUDIT_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _audit_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    action_type TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_summary TEXT,
                    result_summary TEXT,
                    result_status TEXT NOT NULL DEFAULT 'success',
                    duration_ms INTEGER,
                    source TEXT NOT NULL DEFAULT 'chat'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent_time ON audit_log(agent, timestamp)")
            conn.commit()

    def log_action(self, agent: str, action_type: str, tool_name: str,
                   args_summary: str = "", result_summary: str = "",
                   result_status: str = "success", duration_ms: int | None = None,
                   session_id: str | None = None, source: str = "chat"):
        """Insert an audit log entry. Append-only."""
        try:
            with _audit_conn() as conn:
                conn.execute("""
                    INSERT INTO audit_log (agent, session_id, action_type, tool_name,
                        args_summary, result_summary, result_status, duration_ms, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", action_type, tool_name,
                      args_summary[:200], result_summary[:200], result_status,
                      duration_ms, source))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit log error: {e}")

    def query(self, agent: str | None = None, action_type: str | None = None,
              from_ts: str | None = None, limit: int = 50) -> list[dict]:
        """Query audit log entries."""
        try:
            with _audit_conn() as conn:
                conn.row_factory = sqlite3.Row
                where_parts = ["1=1"]
                params: list = []
                if agent:
                    where_parts.append("agent = ?")
                    params.append(agent)
                if action_type:
                    where_parts.append("action_type = ?")
                    params.append(action_type)
                if from_ts:
                    where_parts.append("timestamp >= ?")
                    params.append(from_ts)
                where = " AND ".join(where_parts)
                params.append(limit)
                rows = conn.execute(f"""
                    SELECT * FROM audit_log WHERE {where}
                    ORDER BY timestamp DESC LIMIT ?
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit query error: {e}")
            return []

    def export_csv(self, agent: str | None = None,
                   from_ts: str | None = None,
                   to_ts: str | None = None) -> str:
        """Export audit log as CSV string."""
        try:
            with _audit_conn() as conn:
                conn.row_factory = sqlite3.Row
                where_parts = ["1=1"]
                params: list = []
                if agent:
                    where_parts.append("agent = ?")
                    params.append(agent)
                if from_ts:
                    where_parts.append("timestamp >= ?")
                    params.append(from_ts)
                if to_ts:
                    where_parts.append("timestamp <= ?")
                    params.append(to_ts)
                where = " AND ".join(where_parts)
                rows = conn.execute(f"""
                    SELECT * FROM audit_log WHERE {where}
                    ORDER BY timestamp DESC
                """, params).fetchall()
                import csv
                import io
                output = io.StringIO()
                if rows:
                    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row))
                return output.getvalue()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit export error: {e}")
            return ""


_audit_log: AuditLog | None = None
