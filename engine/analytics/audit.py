# Extracted from claude_cli.py — audit trail

import csv
import io
import json
import logging
import os
import sqlite3
import threading

from engine.agents import AGENTS_DIR  # noqa: F401 — needed at module level

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
