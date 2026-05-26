"""engine/scheduler.py — Scheduler + task-runner subsystem.

Extracted from brain.py (refactor B2). Owns:
  - the schedule DB pool (`SCHEDULER_DB`, `_sched_db_pool`, `_sched_conn`)
  - `_validate_thinking_level_for_model`
  - `class Scheduler` (CRUD, atomic claim/get_due_tasks, begin/complete_execution,
    poll thread, `_execute_scheduled` prompt-build + sidecar delegate)
  - the `tool_schedule_*` tool functions

Seams:
  - thread-locals come from engine.context (low-level base, no cycle):
    `_thread_local`, `ExecutionContext`, `init_thread_context`,
    `clear_thread_context`. INVARIANT #5: `_execute_scheduled` sets the
    thread context (via init_thread_context) BEFORE the sidecar delegate
    call — order/timing unchanged from the brain.py original.
  - every other brain-runtime symbol is reached lazily via
    `import brain as _brain` inside methods (avoids the import cycle —
    brain imports this module).
  - the sidecar / ChatDB are imported lazily inside the methods that use
    them, matching the established pattern.

The schedule DB (scheduler.db) is SHARED: the schedule-sharing helpers
(`_schedule_get_row` etc.) and the workflow-history subsystem
(`_workflow_history_*`) stay in brain.py and reach `_sched_conn` via the
brain re-export alias. The pool itself lives here.

brain.py re-exports every public symbol defined here so existing callers
(`engine.Scheduler`, `engine._scheduler`, `brain._calc_next_run`, the
tool dispatch table, the characterization tests) resolve unchanged.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import traceback
import urllib.error

from engine.context import (
    ExecutionContext,
    init_thread_context,
    request_context,
)


class _LazyBrain:
    """Lazy proxy to the live `brain` module. A top-level `import brain`
    here would be a cycle (brain imports this module); resolving the
    attribute on first access defers the import until after brain has
    finished loading. Every brain-runtime symbol the scheduler touches
    (AgentConfig, MemoryStore, resolve_provider_for_model, the live
    `_scheduler` instance, `_err`/`_ok`, the notification hook, …) is
    reached through this proxy as `_brain.<name>`.
    """
    __slots__ = ()

    def __getattr__(self, name):
        import brain as _b
        return getattr(_b, name)


_brain = _LazyBrain()


# --- Schedule DB pool ---
# Path is computed from this file's location (engine/ is a subdir of the
# repo root that also holds agents/) so it equals brain.AGENTS_DIR/main/
# scheduler.db without an import-time dependency on brain.
SCHEDULER_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agents", "main", "scheduler.db")

_sched_db_pool = threading.local()


def _sched_conn():
    """Thread-local SQLite connection for the scheduler DB.

    Using threading.local() ensures the connection is released when the
    thread exits — otherwise short-lived HTTP handler threads leak FDs.
    """
    conn = getattr(_sched_db_pool, "conn", None)
    if conn is None:
        conn = sqlite3.connect(SCHEDULER_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _sched_db_pool.conn = conn
    return conn


def _validate_thinking_level_for_model(model: str | None, level: str) -> str | None:
    """Reject a thinking_level the model can't actually use, returning an
    error string when invalid (None = OK).

    Empty `level` ('' = inherit) is always OK.
    Empty/unknown `model` is OK (resolved at fire time — defer to runtime).
    """
    level = (level or "").strip().lower()
    if not level:
        return None
    if level not in ("none", "low", "medium", "high"):
        return f"Invalid thinking_level: {level}"
    if not model:
        return None
    cfg = _brain._models_config.get(model) or {}
    fmt = cfg.get("thinking_format", "none")
    if fmt == "none":
        if level == "none":
            return None  # explicit off on a non-thinking model is harmless
        return f"Model '{model}' does not support reasoning (thinking_format=none)"
    if fmt == "inline_tags" and level in ("low", "medium"):
        return f"Model '{model}' supports thinking on/off only — use 'none' or 'high'"
    if fmt == "mistral_blocks" and level in ("low", "medium"):
        return f"Model '{model}' (Mistral) accepts only 'none' or 'high'"
    return None


class Scheduler:
    """Background task scheduler with cron-like scheduling."""

    def __init__(self):
        os.makedirs(os.path.dirname(SCHEDULER_DB), exist_ok=True)
        self._init_db()
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._running_tasks: dict[str, dict] = {}

    def _init_db(self):
        with _sched_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    task TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    agent TEXT DEFAULT 'main',
                    model TEXT,
                    enabled INTEGER DEFAULT 1,
                    last_run TEXT,
                    next_run TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedule_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    schedule_name TEXT,
                    agent TEXT,
                    task TEXT,
                    status TEXT,
                    result TEXT,
                    started_at TEXT,
                    finished_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (schedule_id) REFERENCES schedules(id)
                )
            """)
            # Migration: add timeout column if missing
            try:
                conn.execute("ALTER TABLE schedules ADD COLUMN timeout INTEGER DEFAULT 300")
            except sqlite3.OperationalError:
                pass
            # Per-task attachments + working dir (v8.16.0). attachments is a
            # JSON list of {name, path, mime, size}; path is absolute on the
            # server's filesystem (under agents/<agent>/scheduled_attachments/
            # <name>/). working_dir is an absolute path the agent should
            # treat as the cwd context for shell/file work; it does not
            # rebase python_exec (artifact folder still wins for that).
            for _ddl in (
                "ALTER TABLE schedules ADD COLUMN attachments TEXT DEFAULT '[]'",
                "ALTER TABLE schedules ADD COLUMN working_dir TEXT",
                # Owner: who created the schedule. Empty string for legacy rows
                # (pre-v8.17.0); the server endpoint backfills those to the org
                # admin lazily so list-by-owner stays consistent.
                "ALTER TABLE schedules ADD COLUMN user_id TEXT DEFAULT ''",
                # Per-task execution knobs: thinking_level (none|low|medium|high,
                # empty string = inherit from model defaults) and caveman_chat
                # (0..3, response-style compression analogous to the chat
                # composer toggle). System-level caveman is intentionally not
                # exposed here — it's a per-model setting tied to KV-prefix
                # stability.
                "ALTER TABLE schedules ADD COLUMN thinking_level TEXT DEFAULT ''",
                "ALTER TABLE schedules ADD COLUMN caveman_chat INTEGER DEFAULT 0",
                # Per-task tool surface (PROMPT_TOOLS_UNIFICATION_PLAN.md
                # Phase B). Empty = default (research_minimal). Valid
                # values listed in _brain._VALID_TOOL_PROFILES.
                "ALTER TABLE schedules ADD COLUMN tool_profile TEXT DEFAULT ''",
                # Generic sharing block (v8.35): visibility one of
                # private|users|team|global; owner is the existing user_id.
                "ALTER TABLE schedules ADD COLUMN visibility TEXT DEFAULT 'private'",
                "ALTER TABLE schedules ADD COLUMN owner_team_id TEXT DEFAULT ''",
                "ALTER TABLE schedules ADD COLUMN extra_member_user_ids TEXT DEFAULT '[]'",
                "ALTER TABLE schedules ADD COLUMN excluded_user_ids TEXT DEFAULT '[]'",
                # Optional project binding. Stores the stable project_id
                # (uuid4 hex[:12] from project.json), mirroring
                # sessions.project_id — survives project renames. Empty
                # string = agent-global task (the historical behavior). When
                # set, the fire-path resolves id → name and runs the task
                # inside that project's context (instructions, MemPalace
                # project__<id> wing, research_mode).
                "ALTER TABLE schedules ADD COLUMN project_id TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(_ddl)
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sched_user ON schedules(user_id)")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sched_project ON schedules(project_id)")
            except sqlite3.OperationalError:
                pass
            # Migration: richer per-run telemetry. Older DBs only have
            # status/result/started_at/finished_at; add fields that make the
            # History view diagnosable (trace pivot, artifact folder pivot,
            # duration/tool count split out of the result blob).
            for _ddl in (
                "ALTER TABLE schedule_history ADD COLUMN duration_ms INTEGER",
                "ALTER TABLE schedule_history ADD COLUMN tool_calls INTEGER DEFAULT 0",
                "ALTER TABLE schedule_history ADD COLUMN trace_id TEXT",
                "ALTER TABLE schedule_history ADD COLUMN artifact_folder TEXT",
                "ALTER TABLE schedule_history ADD COLUMN model TEXT",
                # Denormalised sharing snapshot taken at fire time so a later
                # visibility change on the parent schedule doesn't retro-expose
                # (or retro-hide) old runs. owner_user_id mirrors the schedule's
                # user_id at the time the run started.
                "ALTER TABLE schedule_history ADD COLUMN visibility TEXT DEFAULT ''",
                "ALTER TABLE schedule_history ADD COLUMN owner_user_id TEXT DEFAULT ''",
                "ALTER TABLE schedule_history ADD COLUMN owner_team_id TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(_ddl)
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sched_hist_trace ON schedule_history(trace_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sched_hist_schedule ON schedule_history(schedule_id, finished_at)")
            conn.commit()

    def add(self, name: str, task: str, schedule: str,
            agent: str = "main", model: str | None = None,
            timeout: int = 300,
            attachments: list | None = None,
            working_dir: str | None = None,
            user_id: str = "",
            thinking_level: str = "",
            caveman_chat: int = 0,
            tool_profile: str = "",
            project_id: str = "") -> dict:
        """Add a scheduled task. timeout in seconds (default: 300 = 5 min).

        attachments: list of {name, path, mime, size} dicts. Files must already
        exist on disk (uploaded via /v1/schedule/upload).
        working_dir: optional absolute path injected into the system prompt so
        the agent knows where to operate; agent passes it as `cwd` to shell tools.
        user_id: owner. Server fills this from the authenticated request; empty
        string only for tooling that creates schedules outside the auth path.
        thinking_level: '' (inherit) | 'none' | 'low' | 'medium' | 'high'.
        caveman_chat: 0..3 — chat-style response compression for this task.
        tool_profile: '' (default → research_minimal) | one of _brain._VALID_TOOL_PROFILES.
        project_id: '' (agent-global, historical default) | stable project_id
        (uuid4 hex). When set, the fire-path runs the task inside that project's
        context. Caller (server endpoint) is responsible for validating the id +
        the user's access to the project.
        """
        next_run = self._calc_next_run(schedule)
        if next_run is None:
            return {"error": f"Invalid schedule format: {schedule}"}
        if working_dir:
            working_dir = os.path.expanduser(working_dir)
            if not os.path.isdir(working_dir):
                return {"error": f"Working dir does not exist: {working_dir}"}
        thinking_level = (thinking_level or "").strip().lower()
        if thinking_level not in ("", "none", "low", "medium", "high"):
            return {"error": f"Invalid thinking_level: {thinking_level}"}
        # Reject combinations the chosen model can't honor (mistral wants
        # none/high only, inline_tags wants none/high, format=none rejects
        # any non-empty level except 'none').
        _tl_err = _validate_thinking_level_for_model(model, thinking_level)
        if _tl_err:
            return {"error": _tl_err}
        try:
            caveman_chat = int(caveman_chat or 0)
        except (TypeError, ValueError):
            caveman_chat = 0
        if caveman_chat < 0 or caveman_chat > 3:
            return {"error": "caveman_chat must be between 0 and 3"}
        tool_profile = (tool_profile or "").strip()
        if tool_profile not in _brain._VALID_TOOL_PROFILES:
            return {"error": f"Invalid tool_profile: {tool_profile!r}. Valid: {', '.join(repr(p) for p in _brain._VALID_TOOL_PROFILES)}"}
        project_id = (project_id or "").strip()
        atts_json = json.dumps(attachments or [])
        try:
            with _sched_conn() as conn:
                conn.execute("""
                    INSERT INTO schedules (name, task, schedule, agent, model, next_run, timeout, attachments, working_dir, user_id, thinking_level, caveman_chat, tool_profile, project_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, task, schedule, agent, model, next_run.isoformat(),
                      timeout, atts_json, working_dir, user_id or "",
                      thinking_level, caveman_chat, tool_profile, project_id))
                conn.commit()
            return {"name": name, "schedule": schedule, "agent": agent,
                    "next_run": next_run.isoformat(), "timeout": timeout,
                    "attachments": attachments or [], "working_dir": working_dir,
                    "user_id": user_id or "",
                    "thinking_level": thinking_level,
                    "caveman_chat": caveman_chat,
                    "tool_profile": tool_profile,
                    "project_id": project_id,
                    "status": "created"}
        except sqlite3.IntegrityError:
            return {"error": f"Schedule '{name}' already exists"}

    def _purge_attachment_paths(self, paths: list[str]) -> int:
        """Remove the per-upload uuid parent folders for the given attachment
        paths. Each upload lives in its own folder under
        agents/<agent>/scheduled_attachments/<uuid>/, so removing the parent
        dir of each path is safe and complete. Returns count of folders removed.
        Refuses to touch anything outside scheduled_attachments/ as a guard
        against malformed metadata."""
        import shutil
        removed = 0
        seen: set[str] = set()
        for p in paths:
            if not p:
                continue
            parent = os.path.dirname(os.path.abspath(p))
            if parent in seen:
                continue
            seen.add(parent)
            if "scheduled_attachments" not in parent.split(os.sep):
                continue
            try:
                if os.path.isdir(parent):
                    shutil.rmtree(parent)
                    removed += 1
            except OSError as e:
                print(f"  [WARN] attachment purge failed for {parent}: {e}", flush=True)
        return removed

    def remove(self, name: str) -> dict:
        # Read attachment paths before deleting the row so we can purge files.
        attachment_paths: list[str] = []
        with _sched_conn() as conn:
            row = conn.execute(
                "SELECT attachments FROM schedules WHERE name = ?", (name,)
            ).fetchone()
            if row and row[0]:
                try:
                    atts = json.loads(row[0])
                    attachment_paths = [a.get("path", "") for a in atts if isinstance(a, dict)]
                except (ValueError, TypeError):
                    pass
            r = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
            conn.commit()
            if r.rowcount == 0:
                return {"error": f"Schedule '{name}' not found"}
        purged = self._purge_attachment_paths(attachment_paths)
        return {"name": name, "status": "deleted", "attachments_purged": purged}

    def pause(self, name: str) -> dict:
        with _sched_conn() as conn:
            r = conn.execute("UPDATE schedules SET enabled = 0 WHERE name = ?", (name,))
            conn.commit()
            if r.rowcount == 0:
                return {"error": f"Schedule '{name}' not found"}
        return {"name": name, "status": "paused"}

    def resume(self, name: str) -> dict:
        next_run = None
        with _sched_conn() as conn:
            row = conn.execute("SELECT schedule FROM schedules WHERE name = ?", (name,)).fetchone()
            if not row:
                return {"error": f"Schedule '{name}' not found"}
            next_run = self._calc_next_run(row[0])
            conn.execute("UPDATE schedules SET enabled = 1, next_run = ? WHERE name = ?",
                         (next_run.isoformat() if next_run else None, name))
            conn.commit()
        return {"name": name, "status": "resumed", "next_run": next_run.isoformat() if next_run else None}

    def list_all(self) -> list[dict]:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    def get_task(self, name: str) -> dict | None:
        """Get a single scheduled task by name."""
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM schedules WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    def get_history(self, name: str | None = None, limit: int = 20) -> list[dict]:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            # COALESCE(finished_at, started_at) so in-progress rows sort too.
            if name:
                rows = conn.execute(
                    "SELECT * FROM schedule_history WHERE schedule_name = ? "
                    "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
                    (name, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedule_history "
                    "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
                    (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        """Fetch a single history row by its run_id (= schedule_history.id)."""
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM schedule_history WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_run(self, run_id: int) -> dict:
        """Purge a single historical run: history row + its artifact rows +
        artifact files + empty artifact folder. Refuses to delete a run that's
        still in-flight (status='running') since that would orphan the worker.
        Returns a summary dict for the API."""
        row = self.get_run(run_id)
        if not row:
            return {"error": f"Run {run_id} not found"}
        if row.get("status") == "running":
            return {"error": "Cannot delete a running task; cancel it first"}
        artifacts_removed = 0
        try:
            from server import ChatDB
            artifacts_removed = ChatDB.delete_artifacts_for_session(f"sched-{run_id}") or 0
        except Exception as e:
            print(f"  [WARN] delete_run artifact purge: {e}", flush=True)
        # Remove the artifact folder if it's now empty.
        folder = row.get("artifact_folder")
        if folder:
            agent_id = row.get("agent") or "main"
            folder_path = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
            try:
                if os.path.isdir(folder_path) and not os.listdir(folder_path):
                    os.rmdir(folder_path)
            except OSError:
                pass
        with _sched_conn() as conn:
            conn.execute("DELETE FROM schedule_history WHERE id = ?", (run_id,))
            conn.commit()
        return {"status": "deleted", "run_id": run_id,
                "artifacts_removed": artifacts_removed}

    def delete_history(self, name: str) -> dict:
        """Purge the entire run history for a named schedule: every history row
        (except any still-running one) + all associated artifacts and folders.
        The schedule definition itself stays; only its past runs are wiped."""
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, status, agent, artifact_folder "
                "FROM schedule_history WHERE schedule_name = ?", (name,)
            ).fetchall()
            runs = [dict(r) for r in rows]
        if not runs:
            return {"status": "ok", "runs_removed": 0, "artifacts_removed": 0}
        total_artifacts = 0
        removed_ids: list[int] = []
        for r in runs:
            rid = r["id"]
            if r.get("status") == "running":
                continue  # leave in-flight alone
            try:
                from server import ChatDB
                n = ChatDB.delete_artifacts_for_session(f"sched-{rid}") or 0
                total_artifacts += n
            except Exception:
                pass
            folder = r.get("artifact_folder")
            if folder:
                agent_id = r.get("agent") or "main"
                folder_path = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
                try:
                    if os.path.isdir(folder_path) and not os.listdir(folder_path):
                        os.rmdir(folder_path)
                except OSError:
                    pass
            removed_ids.append(rid)
        if removed_ids:
            placeholders = ",".join("?" * len(removed_ids))
            with _sched_conn() as conn:
                conn.execute(
                    f"DELETE FROM schedule_history WHERE id IN ({placeholders})",
                    removed_ids)
                conn.commit()
        return {"status": "ok", "runs_removed": len(removed_ids),
                "artifacts_removed": total_artifacts}

    def delete_orphan_history(self) -> dict:
        """Purge schedule_history rows whose schedule_name no longer exists in
        the schedules table. Skips in-flight runs. Removes associated artifacts
        and empty artifact folders, mirroring delete_history's per-row cleanup."""
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT h.id, h.status, h.agent, h.artifact_folder, h.schedule_name "
                "FROM schedule_history h "
                "LEFT JOIN schedules s ON s.name = h.schedule_name "
                "WHERE s.name IS NULL"
            ).fetchall()
            orphans = [dict(r) for r in rows]
        if not orphans:
            return {"status": "ok", "runs_removed": 0, "artifacts_removed": 0,
                    "orphan_names": []}
        total_artifacts = 0
        removed_ids: list[int] = []
        orphan_names: set[str] = set()
        for r in orphans:
            rid = r["id"]
            if r.get("status") == "running":
                continue  # never touch in-flight runs
            orphan_names.add(r.get("schedule_name") or "")
            try:
                from server import ChatDB
                n = ChatDB.delete_artifacts_for_session(f"sched-{rid}") or 0
                total_artifacts += n
            except Exception:
                pass
            folder = r.get("artifact_folder")
            if folder:
                agent_id = r.get("agent") or "main"
                folder_path = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
                try:
                    if os.path.isdir(folder_path) and not os.listdir(folder_path):
                        os.rmdir(folder_path)
                except OSError:
                    pass
            removed_ids.append(rid)
        if removed_ids:
            placeholders = ",".join("?" * len(removed_ids))
            with _sched_conn() as conn:
                conn.execute(
                    f"DELETE FROM schedule_history WHERE id IN ({placeholders})",
                    removed_ids)
                conn.commit()
        return {"status": "ok", "runs_removed": len(removed_ids),
                "artifacts_removed": total_artifacts,
                "orphan_names": sorted(n for n in orphan_names if n)}

    def update(self, name: str, fields: dict) -> dict:
        """Edit a scheduled task. Allowed fields: task, schedule, model,
        timeout, agent, new_name. All fields optional; unknown keys ignored.

        Recalculates next_run when `schedule` changes. Renaming is allowed but
        rejected if the new name collides with an existing task.
        """
        allowed = {"task", "schedule", "model", "timeout", "agent",
                   "attachments", "working_dir",
                   "thinking_level", "caveman_chat", "tool_profile",
                   "project_id"}
        updates: dict = {}
        for k in allowed:
            if k not in fields:
                continue
            v = fields[k]
            # None = don't touch this field. Empty string for `model` /
            # `working_dir` means "clear back to default" — translate to SQL NULL.
            if v is None:
                continue
            if k in ("model", "working_dir") and v == "":
                updates[k] = None
            elif k == "attachments":
                # Stored as JSON.
                updates[k] = json.dumps(v if isinstance(v, list) else [])
            elif k == "thinking_level":
                tl = str(v or "").strip().lower()
                if tl not in ("", "none", "low", "medium", "high"):
                    return {"error": f"Invalid thinking_level: {v}"}
                updates[k] = tl
            elif k == "caveman_chat":
                try:
                    cv = int(v)
                except (TypeError, ValueError):
                    return {"error": "caveman_chat must be an integer 0..3"}
                if cv < 0 or cv > 3:
                    return {"error": "caveman_chat must be between 0 and 3"}
                updates[k] = cv
            elif k == "tool_profile":
                tp = str(v or "").strip()
                if tp not in _brain._VALID_TOOL_PROFILES:
                    return {"error": f"Invalid tool_profile: {v!r}. Valid: {', '.join(repr(p) for p in _brain._VALID_TOOL_PROFILES)}"}
                updates[k] = tp
            else:
                updates[k] = v
        new_name = fields.get("new_name")
        if not updates and not new_name:
            return {"error": "No fields to update"}

        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM schedules WHERE name = ?", (name,)).fetchone()
            if not row:
                return {"error": f"Schedule '{name}' not found"}

            if new_name and new_name != name:
                collision = conn.execute(
                    "SELECT 1 FROM schedules WHERE name = ?", (new_name,)
                ).fetchone()
                if collision:
                    return {"error": f"Name '{new_name}' is already in use"}

            # Validate schedule format early so we don't half-commit.
            if "schedule" in updates:
                new_next = self._calc_next_run(updates["schedule"])
                if new_next is None:
                    return {"error": f"Invalid schedule format: {updates['schedule']}"}
                updates["next_run"] = new_next.isoformat()

            if "timeout" in updates:
                try:
                    updates["timeout"] = int(updates["timeout"])
                except (TypeError, ValueError):
                    return {"error": "timeout must be an integer (seconds)"}

            # Cross-field: thinking_level must be honorable by the effective
            # model (the one in this patch, or the existing row's). Run after
            # both fields have been individually validated.
            if "thinking_level" in updates:
                effective_model = updates.get("model")
                if effective_model is None:  # not in this patch — keep current
                    try:
                        effective_model = row["model"]
                    except (KeyError, IndexError):
                        effective_model = None
                _tl_err = _validate_thinking_level_for_model(
                    effective_model, updates["thinking_level"])
                if _tl_err:
                    return {"error": _tl_err}

            if updates.get("working_dir"):
                wd = os.path.expanduser(updates["working_dir"])
                if not os.path.isdir(wd):
                    return {"error": f"Working dir does not exist: {wd}"}
                updates["working_dir"] = wd

            # Diff old vs new attachments — paths that disappear get purged
            # from disk so the user dropping a chip in the edit modal actually
            # frees the bytes. Done before the UPDATE so a failure here just
            # leaves the file in place; data loss only happens on success.
            paths_to_purge: list[str] = []
            if "attachments" in updates:
                try:
                    old_atts = json.loads(row["attachments"] or "[]")
                except (ValueError, TypeError):
                    old_atts = []
                try:
                    new_atts = json.loads(updates["attachments"])
                except (ValueError, TypeError):
                    new_atts = []
                old_paths = {a.get("path", "") for a in old_atts if isinstance(a, dict)}
                new_paths = {a.get("path", "") for a in new_atts if isinstance(a, dict)}
                paths_to_purge = [p for p in (old_paths - new_paths) if p]

            set_clauses = []
            values = []
            for k, v in updates.items():
                set_clauses.append(f"{k} = ?")
                values.append(v)
            if new_name and new_name != name:
                set_clauses.append("name = ?")
                values.append(new_name)
            values.append(name)  # WHERE

            conn.execute(
                f"UPDATE schedules SET {', '.join(set_clauses)} WHERE name = ?",
                values,
            )
            conn.commit()

            if paths_to_purge:
                self._purge_attachment_paths(paths_to_purge)

            # Return the fresh row so the UI can refresh without a second call.
            effective_name = new_name or name
            final = conn.execute(
                "SELECT * FROM schedules WHERE name = ?", (effective_name,)
            ).fetchone()
            return dict(final) if final else {"name": effective_name, "status": "updated"}

    def _calc_next_run(self, schedule: str) -> datetime.datetime | None:
        """Calculate next run time from schedule string."""
        now = datetime.datetime.now()
        s = schedule.strip().lower()

        # every Xm, every Xh, every Xd
        m = re.match(r'every\s+(\d+)\s*(m|min|h|hour|d|day)s?', s)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if unit == 'm':
                return now + datetime.timedelta(minutes=val)
            elif unit == 'h':
                return now + datetime.timedelta(hours=val)
            elif unit == 'd':
                return now + datetime.timedelta(days=val)

        # daily HH:MM
        m = re.match(r'daily\s+(\d{1,2}):(\d{2})', s)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return target

        # weekly DOW HH:MM
        days_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        m = re.match(r'weekly\s+(\w{3})\s+(\d{1,2}):(\d{2})', s)
        if m:
            dow_str, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
            target_dow = days_map.get(dow_str[:3])
            if target_dow is None:
                return None
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days_ahead = (target_dow - now.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            target += datetime.timedelta(days=days_ahead)
            return target

        # once YYYY-MM-DD HH:MM
        m = re.match(r'once\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})', s)
        if m:
            date_str, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
            try:
                target = datetime.datetime.fromisoformat(f"{date_str}T{hour:02d}:{minute:02d}:00")
                return target
            except ValueError:
                return None

        return None

    def _calc_next_from_last(self, schedule: str, last_run: str) -> datetime.datetime | None:
        """Calculate next run based on last run time (for intervals)."""
        s = schedule.strip().lower()
        m = re.match(r'every\s+(\d+)\s*(m|min|h|hour|d|day)s?', s)
        if m:
            try:
                last = datetime.datetime.fromisoformat(last_run)
            except (ValueError, TypeError):
                return self._calc_next_run(schedule)
            val, unit = int(m.group(1)), m.group(2)[0]
            if unit == 'm':
                return last + datetime.timedelta(minutes=val)
            elif unit == 'h':
                return last + datetime.timedelta(hours=val)
            elif unit == 'd':
                return last + datetime.timedelta(days=val)
        return self._calc_next_run(schedule)

    def get_due_tasks(self) -> list[dict]:
        """Atomically claim tasks that are due for execution.

        Two guards against the "task took longer than the poll interval and
        got fired twice" race:
          1. UPDATE-bump `next_run` to 1h in the future inside the SELECT
             transaction, so a second poll tick won't re-claim the same row
             before complete_execution re-computes the real next_run. The
             "1h in the future" placeholder is corrected by complete_execution
             using the actual schedule string, or by the next fire if the
             task never completes (at which point the task IS overdue again
             and re-running is correct).
          2. In-memory: skip any task whose name is in self._running_tasks.
        """
        now_dt = datetime.datetime.now()
        now = now_dt.isoformat()
        claimed: list[dict] = []
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            # Hold the lock between SELECT and UPDATE so two poll threads
            # can't both claim. SQLite serializes writers, so a single
            # transaction is enough.
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute("""
                    SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ?
                """, (now,)).fetchall()
                # Skip tasks that are already running in-memory (belt + braces).
                with self._lock:
                    running_names = set(getattr(self, "_running_tasks", {}).keys())
                placeholder = (now_dt + datetime.timedelta(hours=1)).isoformat()
                for r in rows:
                    name = r["name"]
                    if name in running_names:
                        continue
                    conn.execute(
                        "UPDATE schedules SET next_run = ? WHERE id = ?",
                        (placeholder, r["id"]))
                    claimed.append(dict(r))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return claimed

    def begin_execution(self, schedule_id: int, name: str, agent: str, task: str,
                        model: str | None = None,
                        started_at: str | None = None) -> int:
        """Insert a `running` history row up-front and return its id.

        The returned id is the stable `run_id` used for:
          - synthetic session_id (`sched-<run_id>`) in trace spans
          - artifact folder suffix (`<date>_sched-<run_id>`)
          - /v1/scheduler/runs/<run_id> detail endpoint
        """
        now = datetime.datetime.now()
        start = started_at or now.isoformat()
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            # Snapshot the parent schedule's sharing block at fire time so a
            # later visibility change doesn't retro-expose / retro-hide runs.
            srow = conn.execute("SELECT user_id, visibility, owner_team_id FROM schedules WHERE id = ?",
                                (schedule_id,)).fetchone()
            snap_owner = (srow["user_id"] if srow else "") or ""
            snap_vis = (srow["visibility"] if srow else "") or "private"
            snap_team = (srow["owner_team_id"] if srow else "") or ""
            cur = conn.execute("""
                INSERT INTO schedule_history
                    (schedule_id, schedule_name, agent, task, status, result,
                     started_at, finished_at, model,
                     visibility, owner_user_id, owner_team_id)
                VALUES (?, ?, ?, ?, 'running', '', ?, NULL, ?, ?, ?, ?)
            """, (schedule_id, name, agent, task, start, model or "",
                  snap_vis, snap_owner, snap_team))
            run_id = cur.lastrowid
            conn.commit()
            return int(run_id)

    def complete_execution(self, run_id: int, schedule_id: int, status: str,
                            result: str, started_at: str | None = None,
                            tool_calls: int = 0,
                            trace_id: str | None = None,
                            artifact_folder: str | None = None):
        """Finalize the running history row and update the schedule's next_run.

        Splits duration/tool_calls/trace/artifact into dedicated columns so the
        History view can render them without regex-parsing the result blob.
        The existing "[Duration: Xs | Tools: N]" header is kept inside `result`
        for backwards compatibility with `_gather_recent_schedule_history` and
        the tool_schedule_history tool.
        """
        now = datetime.datetime.now()
        start = started_at or now.isoformat()
        try:
            start_dt = datetime.datetime.fromisoformat(start)
            duration = (now - start_dt).total_seconds()
        except (ValueError, TypeError):
            duration = 0.0
        duration_ms = int(duration * 1000)
        with _sched_conn() as conn:
            conn.execute("""
                UPDATE schedule_history
                SET status = ?,
                    result = ?,
                    finished_at = ?,
                    duration_ms = ?,
                    tool_calls = ?,
                    trace_id = COALESCE(?, trace_id),
                    artifact_folder = COALESCE(?, artifact_folder)
                WHERE id = ?
            """, (status,
                  f"[Duration: {duration:.0f}s | Tools: {tool_calls}]\n\n{result}",
                  now.isoformat(), duration_ms, tool_calls,
                  trace_id, artifact_folder, run_id))

            row = conn.execute("SELECT schedule FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            if row:
                schedule_str = row[0]
                if schedule_str.strip().lower().startswith("once"):
                    conn.execute("UPDATE schedules SET enabled = 0, last_run = ? WHERE id = ?",
                                 (now.isoformat(), schedule_id))
                else:
                    next_run = self._calc_next_from_last(schedule_str, now.isoformat())
                    conn.execute("UPDATE schedules SET last_run = ?, next_run = ? WHERE id = ?",
                                 (now.isoformat(),
                                  next_run.isoformat() if next_run else None,
                                  schedule_id))
            conn.commit()

    # Backwards-compat shim: some call sites (e.g. manual run endpoint) may
    # still insert-and-finalize in one shot. Prefer begin_execution +
    # complete_execution for real scheduled runs so the history row exists
    # while the run is in progress.
    def mark_executed(self, schedule_id: int, name: str, agent: str, task: str,
                      status: str, result: str, started_at: str = None,
                      tool_calls: int = 0,
                      trace_id: str | None = None,
                      artifact_folder: str | None = None,
                      model: str | None = None):
        run_id = self.begin_execution(schedule_id, name, agent, task,
                                      model=model, started_at=started_at)
        self.complete_execution(run_id, schedule_id, status, result,
                                started_at=started_at, tool_calls=tool_calls,
                                trace_id=trace_id, artifact_folder=artifact_folder)

    def start(self):
        """Start the background scheduler thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        """Background loop that checks for due tasks every 30s.
        Each task runs in its own thread to avoid blocking other due tasks."""
        while not self._stop.is_set():
            try:
                due = self.get_due_tasks()
                for task_row in due:
                    if self._stop.is_set():
                        break
                    t = threading.Thread(
                        target=self._execute_scheduled, args=(task_row,),
                        daemon=True, name=f"sched_{task_row.get('name', '?')}")
                    t.start()
            except Exception:
                pass
            self._stop.wait(30)

    def get_running_tasks(self) -> list[dict]:
        """Get currently running scheduled tasks with live stats."""
        with self._lock:
            return [dict(t) for t in self._running_tasks.values()]

    def cancel_running_task(self, name: str) -> bool:
        """Cancel a running scheduled task."""
        with self._lock:
            task = self._running_tasks.get(name)
            if task and task.get("cancel_token"):
                task["cancel_token"].cancel()
                task["status"] = "cancelling"
                return True
        return False

    def _execute_scheduled(self, task_row: dict):
        """Execute a single scheduled task."""
        agent_id = task_row.get("agent", "main")
        task = task_row.get("task", "")
        model = task_row.get("model")
        schedule_id = task_row.get("id")
        name = task_row.get("name", "")

        # Memory summary tasks: clean up orphaned chat indexes, then regenerate prompt with live data
        if name.startswith("_memory_summary_"):
            try:
                _brain._cleanup_orphaned_chat_index_files(agent_id)
            except Exception:
                pass
            try:
                task = _brain._build_memory_summary_prompt(agent_id)
            except Exception:
                pass

        # Use delegation infrastructure
        target = _brain.AgentConfig(agent_id)
        target_memory = _brain.MemoryStore(agent_id, base_dir=target.memory_dir)

        if not model:
            model = target.preferred_model or _brain._delegate_fallback_model or "claude-opus-4-5-20251101"

        # Open a history row up front so we have a stable run_id BEFORE the
        # delegate runs. The run_id seeds the synthetic session_id, which in
        # turn drives:
        #   - trace span session_id column (for joining spans back to the run)
        #   - tool_python_exec / tool_write_file artifact folder (fixes the
        #     "report lands in TMPDIR" bug on scheduled tasks)
        started_at_iso = datetime.datetime.now().isoformat()
        try:
            run_id = self.begin_execution(
                schedule_id, name, agent_id, task,
                model=model, started_at=started_at_iso)
        except Exception:
            run_id = 0  # fall through; worst case we lose the run_id and
                        # the sched-0 folder is reused. Better than crashing
                        # the whole scheduler thread.
        sched_session_id = f"sched-{run_id}" if run_id else f"sched-adhoc-{int(time.time())}"
        artifact_folder_name = f"{datetime.datetime.now().strftime('%Y-%m-%d')}_{sched_session_id}"

        # Track this execution
        cancel_token = _brain.CancelToken()
        run_info = {
            "run_id": run_id,
            "session_id": sched_session_id,
            "name": name,
            "agent": agent_id,
            "task": task[:200],
            "model": model,
            "status": "running",
            "started_at": started_at_iso,
            "tool_calls": 0,
            "tool_log": [],
            "cancel_token": cancel_token,
            "trace_id": None,
        }
        with self._lock:
            if not hasattr(self, '_running_tasks'):
                self._running_tasks = {}
            self._running_tasks[name] = run_info

        # Per-task working_dir overrides the server's cwd. The agent passes
        # this as the `cwd` arg to execute_command; python_exec stays pinned
        # to the artifact folder so file-write tracking still works.
        task_working_dir = task_row.get("working_dir") or None

        # Per-task chat-style response compression level (for _apply_system_prompt_postprocess).
        try:
            _task_caveman = int(task_row.get("caveman_chat") or 0)
        except (TypeError, ValueError):
            _task_caveman = 0

        # Optional project binding. The stored value is the stable project_id;
        # the whole project context (instructions in the system prompt, the
        # MemPalace project__<id> wing, research_mode) keys off the project
        # NAME held in request_context().project — so resolve id → name here
        # and feed both the request_context (below) and _tool_context. If the
        # project no longer exists, fall back to agent-global silently rather
        # than failing the run.
        _task_project_id = (task_row.get("project_id") or "").strip()
        _task_project_name = ""
        if _task_project_id:
            try:
                for _p in _brain.ProjectManager.list_projects(agent_id):
                    if _p.get("id") == _task_project_id:
                        _task_project_name = _p.get("name") or ""
                        break
            except Exception:
                _task_project_name = ""

        # Init thread context before building the system prompt —
        # _brain._build_system_prompt reads current_agent from thread-local for soul.md.
        # Pin user_id so quota / audit lookups attribute correctly. Empty for
        # legacy tasks without owner.
        _sched_ctx_pre = ExecutionContext(
            mode="scheduled",
            agent_id=agent_id,
            session_id=sched_session_id,
            user_id=(task_row.get("user_id") or ""),
            memory_store=target_memory,
        )
        with request_context():
            init_thread_context(_sched_ctx_pre, agent_config=target)

            # Project scope: _build_system_prompt + mempalace_query + research
            # mode all read get_request_context().project (a NAME). Setting it
            # here — before the prompt is built — pulls in the project's
            # instructions, description, research_mode, and scopes tools to the
            # project__<id> wing. Empty when unbound (agent-global, unchanged).
            if _task_project_name:
                from engine.context import get_request_context as _grc
                _grc().project = _task_project_name
                # Project-level web-search lockout (same as the chat worker):
                # a project with `disable_web_search` forces its tasks to work
                # from the project memory, not free web search. Model-
                # independent — prompt instructions alone don't bind on
                # mistral-medium (verified: sched-878/879/880 free-searched
                # despite the retrieval hint). resolve_active_tools subtracts
                # exclude_tools.
                try:
                    _pcfg_lock = _brain.ProjectManager.get_project(
                        agent_id, _task_project_name)
                except Exception:
                    _pcfg_lock = None
                if _pcfg_lock and _pcfg_lock.get("disable_web_search"):
                    # Remove only the 3 dedicated web tools. (Blocking the
                    # shell/python too was the wrong direction — sched-881 used
                    # mempalace successfully WITH the shell present; the curl
                    # detour is mistral-medium non-determinism, not a
                    # consequence of the shell being available.)
                    _grc().exclude_tools = ["web_fetch", "exa_search", "searxng_search"]

            # Build system prompt via the unified builder
            # (PROMPT_TOOLS_UNIFICATION_PLAN.md). Default purpose for scheduled
            # tasks is `research_minimal` (lean prompt); per-schedule
            # `tool_profile='interactive'` opts back into the full agent
            # surface. active_tool_names is computed inline so the per-tool
            # prose blocks match the wire payload (KV-prefix stability).
            #
            # Determine the purpose for this scheduled task. Sources, in order:
            #   1. Memory-summary tasks (Brain-internal) — name prefix forces
            #      `memory_summary` purpose.
            #   2. Project-bound tasks default to `interactive` — they must
            #      behave like a project chat, which means the full tool
            #      surface incl. `mempalace_query`/`read_document`. The lean
            #      `research_minimal` set (write_file/web_fetch/exa_search/
            #      searxng_search only) CANNOT read the project memory, so a
            #      project task on research_minimal would never see the mined
            #      project knowledge and would hallucinate. An explicit
            #      `tool_profile` still wins (e.g. force research_minimal).
            #   3. Per-schedule `tool_profile` field — empty/unset →
            #      `research_minimal` (default for non-project tasks),
            #      `interactive` opts into the full chat surface.
            # Hoisted up here so the prompt and the wire tool list agree.
            if name.startswith("_memory_summary_"):
                _sched_purpose_pre = "memory_summary"
            else:
                _task_profile = (task_row.get("tool_profile") or "").strip()
                if _task_profile:
                    _sched_purpose_pre = "interactive" if _task_profile == "interactive" else "research_minimal"
                elif _task_project_name:
                    # Project-bound + no explicit profile → full chat surface.
                    _sched_purpose_pre = "interactive"
                else:
                    _sched_purpose_pre = "research_minimal"
            if _sched_purpose_pre == "transform":
                _sched_active_names: set[str] = set()
            else:
                # Scheduled tasks go through the same single resolver hierarchy
                # as every other LLM call: global tool_settings → agent-level
                # tool_overrides → purpose filter. The task's owning agent
                # supplies layer 2.
                _sched_active_names = {
                    t.get("name", "") for t in _brain.resolve_active_tools(
                        purpose=_sched_purpose_pre,
                        agent_id=agent_id,
                        discovered_tools=set(),
                        mcp_manager=None,  # MCP listing belongs in the prompt only
                        is_openai_shape=False,
                    )
                }
            system_prompt = _brain._build_system_prompt(
                include_memory_summary=False,
                purpose=_sched_purpose_pre,
                task_name=name,
                task_working_dir=task_working_dir or "",
                active_tool_names=_sched_active_names,
            )

            # Point the agent at the durable attachment paths (no per-run copy).
            # Files live under agents/<agent>/scheduled_attachments/<uuid>/<name>
            # and are persisted with the schedule definition; deletion of the
            # schedule purges them.
            task_message = task
            # Prepend the same artifact-folder preamble the interactive chat
            # adds to its first user message (handlers/chat.py). Without it the
            # task's user message is framed differently than a chat's, and at
            # temperature 0.2 that systematic input difference made the model
            # build a slightly different mempalace_query (chat: "… aktuelle",
            # task: without) → different source weighting. Same framing → same
            # query → same result as the equivalent chat.
            try:
                _pre = _brain._artifact_folder_preamble_text(agent_id, sched_session_id)
                if _pre:
                    task_message = f"{_pre}\n\n{task_message}"
            except Exception:
                pass
            attachments_meta = []
            try:
                atts_raw = task_row.get("attachments") or "[]"
                attachments_meta = json.loads(atts_raw) if isinstance(atts_raw, str) else atts_raw
            except (ValueError, TypeError):
                attachments_meta = []
            if attachments_meta:
                existing_paths = [a.get("path", "") for a in attachments_meta
                                  if a.get("path") and os.path.isfile(a["path"])]
                if existing_paths:
                    paths_list = "\n".join(f"  - {p}" for p in existing_paths)
                    has_docs = any(os.path.splitext(p)[1].lower()
                                   in (".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv")
                                   for p in existing_paths)
                    if has_docs:
                        notice = (f"\n\n[Task attachments on disk. "
                                  f"IMPORTANT: Use the read_document tool (NOT read_file) "
                                  f"for PDF, DOCX, XLSX, PPTX:]\n{paths_list}")
                    else:
                        notice = f"\n\n[Task attachments on disk:]\n{paths_list}"
                    task_message = task_message + notice

            messages = [{"role": "user", "content": task_message}]

            # Get timeout (default 5 min)
            task_timeout = task_row.get("timeout") or 300

            # Run with isolated memory and live tracking
            result_text = ""
            status = "success"

            def on_event(event_type, data):
                if event_type == "tool_call":
                    run_info["tool_calls"] += 1
                    entry = f"{data.get('name','')}({str(data.get('args',{}))[:80]})"
                    run_info["tool_log"].append(entry)
                    if len(run_info["tool_log"]) > 50:
                        run_info["tool_log"] = run_info["tool_log"][-50:]

            # Timeout watchdog — cancels the task after timeout seconds
            def watchdog():
                if not cancel_token._cancelled.wait(task_timeout):
                    cancel_token.cancel()
                    run_info["status"] = "timeout"

            timer = threading.Thread(target=watchdog, daemon=True)
            timer.start()

            try:

                sched_inf = _brain.get_inference_params(model, target.config.get("model_purpose"))
                # Per-task thinking override: '' = inherit model defaults; 'none'
                # explicitly disables; otherwise (low|medium|high) wins over the
                # model's inference.thinking_level so the UI choice takes effect
                # even if the model has a different default.
                _task_thinking = (task_row.get("thinking_level") or "").strip().lower()
                if _task_thinking and _task_thinking != "none":
                    sched_inf = dict(sched_inf)
                    sched_inf["thinking_level"] = _task_thinking
                elif _task_thinking == "none":
                    sched_inf = dict(sched_inf)
                    sched_inf.pop("thinking_level", None)
                    sched_inf["thinking"] = False
                # Sidecar path. Brain resolves provider, builds the tool list +
                # sampling, dispatches tools via /v1/tools/call, and persists the
                # final reply. The agentic loop itself runs in the sidecar process.
                from handlers import sidecar_proxy as _sidecar_proxy
                _sidecar_blocked = False
                _sched_deanon = _brain._identity_deanon
                try:
                    # GDPR policy gate. Build a flat list with system_prompt at
                    # index 0 (if any) followed by string contents in messages
                    # order; rewrite back in place after pseudonymisation.
                    _gdpr_blobs = []
                    _has_sys = bool(system_prompt)
                    if _has_sys:
                        _gdpr_blobs.append(system_prompt)
                    _msg_idx = []
                    for _i, _m in enumerate(messages):
                        _c = _m.get("content") if isinstance(_m, dict) else None
                        if isinstance(_c, str):
                            _gdpr_blobs.append(_c)
                            _msg_idx.append(_i)
                    model, _new_blobs, _sched_deanon = _brain.gdpr_pick_model_for_background(
                        model, _gdpr_blobs, purpose="scheduled")
                    if _new_blobs is not _gdpr_blobs:
                        _ofs = 0
                        if _has_sys:
                            system_prompt = _new_blobs[0]
                            _ofs = 1
                        for _i, _idx in enumerate(_msg_idx):
                            messages[_idx] = {**messages[_idx], "content": _new_blobs[_ofs + _i]}
                except _brain.GDPRBlockedError as _ge:
                    result_text = f"[DELEGATION ERROR] {_ge}"
                    status = "error"
                    _sidecar_blocked = True

                if _sidecar_blocked:
                    pass  # result_text + status already set from the GDPR block above
                else:
                    _prov = _brain.resolve_provider_for_model(model)
                    # Reuse the purpose resolved up front (memory_summary for
                    # `_memory_summary_` name prefix; otherwise per-task
                    # `tool_profile` — `interactive` for full chat surface,
                    # else research_minimal).
                    _sched_purpose = _sched_purpose_pre

                    _tool_context = {
                        "session_id": sched_session_id,
                        "agent_id": agent_id,
                        "user_id": (task_row.get("user_id") or ""),
                        "team_ids": [],
                        # Project NAME (resolved from the schedule's stored
                        # project_id) so the sidecar's per-tool-call context
                        # rebuild scopes mempalace_query to the project wing.
                        # Empty for agent-global tasks.
                        "project": _task_project_name,
                        "note_context": None,
                        "workflow_run_id": "",
                        "plan_mode": False,
                        "research_mode_override": None,
                        "execution_overrides": {},
                        "attachment_image_model": "",
                        "caveman_chat": _task_caveman,
                        "caveman_system": 0,
                        "trace_id": "",
                    }
                    _sampling = {
                        "temperature": sched_inf.get("temperature"),
                        "top_p": sched_inf.get("top_p"),
                        "top_k": sched_inf.get("top_k"),
                        "stop_sequences": sched_inf.get("stop") or sched_inf.get("stop_sequences"),
                    }
                    _max_tokens = int(sched_inf.get("max_tokens") or _brain.get_model_max_output(model))
                    _agent_cfg = target.config or {}
                    _max_rounds = int((_agent_cfg.get("limits") or {}).get("max_tool_rounds", 25) or 25)
                    _thinking_level = sched_inf.get("thinking_level") if sched_inf.get("thinking") is not False else None

                    # Bridge sidecar SSE → on_event so tool_calls + tool_log keep updating.
                    def _sc_event(ev_type, data):
                        if ev_type == "tool_call":
                            on_event("tool_call", data)

                    # Model-level `parallel_tool_calls: false` → Anthropic SDK
                    # `tool_choice.disable_parallel_tool_use=True`. Mistral via
                    # CLIProxyAPI in streaming mode mis-emits parallel tool_use
                    # batches that occasionally drop the final write_file —
                    # sequential tool use sidesteps the issue.
                    _model_cfg = _brain._models_config.get(model, {}) or {}
                    _disable_parallel = _model_cfg.get("parallel_tool_calls", True) is False
                    _result = _sidecar_proxy.run_turn(
                        messages=messages,
                        model=model,
                        api_key=_prov["api_key"],
                        base_url=_prov["base_url"],
                        system_prompt=system_prompt,
                        purpose=_sched_purpose,
                        tool_context=_tool_context,
                        sampling=_sampling,
                        thinking_level=_thinking_level,
                        max_tokens=_max_tokens,
                        max_rounds=_max_rounds,
                        event_callback=_sc_event,
                        cancel_token=cancel_token,
                        timeout_s=float(task_timeout) + 60.0,
                        disable_parallel_tool_use=_disable_parallel,
                    )
                    _sc_err = _result.get("error")
                    _sc_reply = _sched_deanon(_result.get("reply") or "")
                    if _sc_err and not _sc_reply:
                        result_text = f"[DELEGATION ERROR] sidecar: {str(_sc_err)[:300]}"
                        status = "error"
                    elif _sc_err and _sc_reply:
                        result_text = _sc_reply + f"\n\n*(Sidecar error after partial: {str(_sc_err)[:200]})*"
                    else:
                        result_text = _sc_reply
                    run_info["trace_id"] = _result.get("turn_id") or None
                    # Sidecar's tool_calls_total is authoritative; on_event may have
                    # missed start events if the worker died mid-turn.
                    run_info["tool_calls"] = max(
                        run_info["tool_calls"], int(_result.get("tool_calls_total") or 0))

                    # ── Cost + trace logging for the run ──
                    # The interactive chat worker logs token cost (chat.py) and
                    # the run-detail view reads tool/LLM spans from traces.db.
                    # The scheduler fire-path did NEITHER since the SDK
                    # migration (v9.0.0 dropped the native loop that used to do
                    # it), so a scheduled run showed only a tool COUNT — no
                    # token in/out, no cost, no per-tool list in the inspector.
                    # Reconstruct both from run_turn's return: usage_total
                    # (token totals) + tool_events (which tools ran), keyed by
                    # the synthetic sched session id + the turn's trace_id so
                    # run_detail's get_trace(trace_id) picks them up.
                    try:
                        _usage = _result.get("usage_total") or {}
                        _tok_in = (int(_usage.get("input_tokens", 0) or 0)
                                   + int(_usage.get("cache_creation_input_tokens", 0) or 0)
                                   + int(_usage.get("cache_read_input_tokens", 0) or 0))
                        _tok_out = int(_usage.get("output_tokens", 0) or 0)
                        _trace_id = _result.get("turn_id") or None
                        # Cost ledger (cost_log) keyed by the sched session id.
                        if _tok_in or _tok_out:
                            _brain._log_call_cost(
                                model, _tok_in, _tok_out,
                                session_id=sched_session_id,
                                api_key=_prov.get("api_key"))
                        # Spans for the inspector. tool_events is the authoritative
                        # per-tool list; fall back to the on_event tool_log strings.
                        _tm = getattr(_brain, "_trace_manager", None)
                        if _tm and _trace_id:
                            _tool_events = _result.get("tool_events") or []
                            if _tool_events:
                                for _ev in _tool_events:
                                    _nm = (_ev.get("name") if isinstance(_ev, dict) else str(_ev)) or "?"
                                    _summ = ""
                                    if isinstance(_ev, dict):
                                        # Prefer the capped result text the sidecar
                                        # now carries; fall back to a synthesized
                                        # summary (args + size + error flag) so the
                                        # inspector always shows *something* per tool.
                                        _summ = str(_ev.get("result_text")
                                                    or _ev.get("result")
                                                    or _ev.get("result_summary") or "")
                                        if not _summ:
                                            _args = _ev.get("args")
                                            _chars = _ev.get("result_chars")
                                            _bits = []
                                            if _args:
                                                _bits.append(f"args: {str(_args)[:200]}")
                                            if _chars is not None:
                                                _bits.append(f"{_chars} Zeichen")
                                            if _ev.get("is_error"):
                                                _bits.append("FEHLER")
                                            _summ = " · ".join(_bits)
                                    _st = "error" if (isinstance(_ev, dict) and _ev.get("is_error")) else "ok"
                                    _sp = _tm.start_span(
                                        "tool_call", _nm, agent=agent_id,
                                        model=model, trace_id=_trace_id,
                                        session_id=sched_session_id)
                                    # full_result = the complete tool output the
                                    # model received (result_text, up to 100k);
                                    # result_summary stays the short inline
                                    # preview. Lets the inspector expand the full
                                    # result like the chat view.
                                    _full = ""
                                    if isinstance(_ev, dict):
                                        _full = str(_ev.get("result_text") or _ev.get("result") or "")
                                    _tm.end_span(_sp, status=_st, result_summary=_summ,
                                                 full_result=_full)
                            else:
                                for _entry in (run_info.get("tool_log") or []):
                                    _nm = str(_entry).split("(", 1)[0] or "?"
                                    _sp = _tm.start_span(
                                        "tool_call", _nm, agent=agent_id,
                                        model=model, trace_id=_trace_id,
                                        session_id=sched_session_id)
                                    _tm.end_span(_sp, status="ok")
                            # One LLM span carrying the token totals so the
                            # run-detail stats block shows tokens in/out.
                            _llm = _tm.start_span(
                                "llm_call", model, agent=agent_id, model=model,
                                trace_id=_trace_id, session_id=sched_session_id)
                            _tm.end_span(_llm, status="ok",
                                         tokens_in=_tok_in, tokens_out=_tok_out)
                    except Exception as _obs_e:
                        print(f"  [WARN] sched observability log failed: {_obs_e}", flush=True)
                # Check if the loop returned an error string instead of raising.
                # Strip the redundant 'Delegation error: ' prefix and the trailing
                # colon-with-nothing-after that comes from bare-exception args, and
                # add a clear context line so non-interactive consumers (the
                # schedule history view, audit logs, downstream daemons) see what
                # actually went wrong rather than a bare '[DELEGATION ERROR]
                # Delegation error: '.
                if result_text.startswith("Delegation error:"):
                    status = "error"
                    _detail = result_text[len("Delegation error:"):].strip().rstrip(":").strip()
                    if not _detail:
                        _detail = ("Delegation produced no error detail "
                                   "(likely a swallowed cancellation or an "
                                   "exception with empty args). Check "
                                   "server.error.log around this run for the "
                                   "real cause.")
                    result_text = (
                        f"[DELEGATION ERROR] {_detail}\n"
                        f"Tool calls made before failure: {run_info['tool_calls']}."
                    )
            except _brain.TaskCancelled:
                if run_info.get("status") == "timeout":
                    elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(run_info["started_at"])).total_seconds()
                    # Always-present, parseable timeout context for non-interactive
                    # consumers. Don't append to an existing result_text — if the
                    # task got cut mid-tool the partial body has no value and just
                    # clutters the audit row.
                    result_text = (
                        f"[TIMEOUT] Task '{name}' timed out after {elapsed:.0f}s "
                        f"(limit: {task_timeout}s).\n"
                        f"Model: {model}\n"
                        f"Tool calls made before timeout: {run_info['tool_calls']}.\n"
                        f"Increase the task timeout in the schedule editor if the "
                        f"workload legitimately needs more time, or simplify the "
                        f"task prompt."
                    )
                    status = "timeout"
                else:
                    result_text = (
                        f"[CANCELLED] Task '{name}' was cancelled by user or admin.\n"
                        f"Model: {model}\n"
                        f"Tool calls made before cancel: {run_info['tool_calls']}."
                    )
                    status = "cancelled"
            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8")[:500]
                except Exception:
                    pass
                result_text = (
                    f"[HTTP ERROR] {e.code} {e.reason}\n"
                    f"Model: {model}\n"
                    f"Tool calls made before error: {run_info['tool_calls']}.\n"
                    + (f"Response body: {error_body}" if error_body else "")
                )
                status = "error"
            except urllib.error.URLError as e:
                result_text = (
                    f"[CONNECTION ERROR] Could not reach LLM API for model '{model}': "
                    f"{e.reason}\n"
                    f"Tool calls made before error: {run_info['tool_calls']}."
                )
                status = "error"
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                result_text = (
                    f"[ERROR] {type(e).__name__}: {e}\n"
                    f"Model: {model}\n"
                    f"Tool calls made before error: {run_info['tool_calls']}.\n\n"
                    f"Traceback (last 500 chars):\n{tb[-500:]}"
                )
                status = "error"
            finally:
                cancel_token.cancel()  # stop the watchdog if still running
                with self._lock:
                    self._running_tasks.pop(name, None)

        # Only stamp artifact_folder if the folder actually materialised —
        # tasks that made no file writes shouldn't claim a folder.
        _artifact_dir = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", artifact_folder_name)
        _folder_for_row = artifact_folder_name if os.path.isdir(_artifact_dir) else None

        if run_id:
            self.complete_execution(
                run_id, schedule_id, status, result_text,
                started_at=run_info.get("started_at"),
                tool_calls=run_info.get("tool_calls", 0),
                trace_id=run_info.get("trace_id"),
                artifact_folder=_folder_for_row,
            )
        else:
            # begin_execution failed — fall back to insert-and-finalize in one
            # shot so we still record something.
            self.mark_executed(schedule_id, name, agent_id, task, status, result_text,
                               started_at=run_info.get("started_at"),
                               tool_calls=run_info.get("tool_calls", 0),
                               trace_id=run_info.get("trace_id"),
                               artifact_folder=_folder_for_row,
                               model=model)

        # Chain relationship discovery after memory summary completes
        if name.startswith("_memory_summary_") and status == "success":
            rd_cfg = _brain._get_relationship_discovery_config(agent_id)
            if rd_cfg.get("enabled"):
                try:
                    _brain.trigger_relationship_discovery(agent_id)
                except Exception:
                    pass

        # Fire notification hook for task completion/failure
        if _brain._notification_hook:
            try:
                if status in ("error", "timeout"):
                    evt = "task_timeout" if status == "timeout" else "task_failed"
                    sev = "warning" if status == "timeout" else "error"
                    _brain._notification_hook(evt, f"Scheduled task: {name}",
                                       result_text[:300], severity=sev,
                                       agent=agent_id,
                                       metadata={"task_name": name, "status": status,
                                                  "tool_calls": run_info.get("tool_calls", 0)})
                elif status == "success" and not name.startswith("_memory_summary_") \
                        and not name.startswith("_relationship_discovery_"):
                    _brain._notification_hook("task_complete", f"Task completed: {name}",
                                       f"Agent {agent_id} completed '{name}' with {run_info.get('tool_calls', 0)} tool calls.",
                                       severity="info", agent=agent_id,
                                       metadata={"task_name": name})
            except Exception:
                pass



def tool_schedule_list(args: dict) -> str:
    """List all scheduled tasks."""
    if not _brain._scheduler:
        return _brain._err("Scheduler not initialized")
    schedules = _brain._scheduler.list_all()
    return _brain._ok({"schedules": schedules, "count": len(schedules)})


def tool_schedule_history(args: dict) -> str:
    """Get execution history for scheduled tasks."""
    if not _brain._scheduler:
        return _brain._err("Scheduler not initialized")
    name = args.get("name")
    limit = args.get("limit", 20)
    history = _brain._scheduler.get_history(name, limit)
    # Truncate long results
    for h in history:
        if h.get("result") and len(h["result"]) > 500:
            h["result"] = h["result"][:500] + "..."
    return _brain._ok({"history": history, "count": len(history)})
