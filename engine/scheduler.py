# Extracted from claude_cli.py — Scheduler class and scheduling engine
#
# Cross-module deps (all resolved from claude_cli.py at runtime):
#   AGENTS_DIR, _thread_local, _models_config, _delegate_fallback_model
#   _notification_hook
#   CAVEMAN_CHAT_PROMPTS
#   AgentConfig, MemoryStore, CancelToken
#   _run_delegate, get_inference_params
#   _cleanup_orphaned_chat_index_files, _build_memory_summary_prompt
#   _get_relationship_discovery_config, trigger_relationship_discovery
#   _get_autodream_config, trigger_autodream
#   TaskCancelled

import datetime
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error

from engine.agents import AGENTS_DIR  # noqa: F401 — needed at module level

# --- Scheduler ---

SCHEDULER_DB = os.path.join(AGENTS_DIR, "main", "scheduler.db")

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
    cfg = _models_config.get(model) or {}
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
            caveman_chat: int = 0) -> dict:
        """Add a scheduled task. timeout in seconds (default: 300 = 5 min).

        attachments: list of {name, path, mime, size} dicts. Files must already
        exist on disk (uploaded via /v1/schedule/upload).
        working_dir: optional absolute path injected into the system prompt so
        the agent knows where to operate; agent passes it as `cwd` to shell tools.
        user_id: owner. Server fills this from the authenticated request; empty
        string only for tooling that creates schedules outside the auth path.
        thinking_level: '' (inherit) | 'none' | 'low' | 'medium' | 'high'.
        caveman_chat: 0..3 — chat-style response compression for this task.
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
        atts_json = json.dumps(attachments or [])
        try:
            with _sched_conn() as conn:
                conn.execute("""
                    INSERT INTO schedules (name, task, schedule, agent, model, next_run, timeout, attachments, working_dir, user_id, thinking_level, caveman_chat)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, task, schedule, agent, model, next_run.isoformat(),
                      timeout, atts_json, working_dir, user_id or "",
                      thinking_level, caveman_chat))
                conn.commit()
            return {"name": name, "schedule": schedule, "agent": agent,
                    "next_run": next_run.isoformat(), "timeout": timeout,
                    "attachments": attachments or [], "working_dir": working_dir,
                    "user_id": user_id or "",
                    "thinking_level": thinking_level,
                    "caveman_chat": caveman_chat,
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
            from server_lib.db import ChatDB
            artifacts_removed = ChatDB.delete_artifacts_for_session(f"sched-{run_id}") or 0
        except Exception as e:
            print(f"  [WARN] delete_run artifact purge: {e}", flush=True)
        # Remove the artifact folder if it's now empty.
        folder = row.get("artifact_folder")
        if folder:
            agent_id = row.get("agent") or "main"
            folder_path = os.path.join(AGENTS_DIR, agent_id, "artifacts", folder)
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
                from server_lib.db import ChatDB
                n = ChatDB.delete_artifacts_for_session(f"sched-{rid}") or 0
                total_artifacts += n
            except Exception:
                pass
            folder = r.get("artifact_folder")
            if folder:
                agent_id = r.get("agent") or "main"
                folder_path = os.path.join(AGENTS_DIR, agent_id, "artifacts", folder)
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
                from server_lib.db import ChatDB
                n = ChatDB.delete_artifacts_for_session(f"sched-{rid}") or 0
                total_artifacts += n
            except Exception:
                pass
            folder = r.get("artifact_folder")
            if folder:
                agent_id = r.get("agent") or "main"
                folder_path = os.path.join(AGENTS_DIR, agent_id, "artifacts", folder)
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
                   "thinking_level", "caveman_chat"}
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
            cur = conn.execute("""
                INSERT INTO schedule_history
                    (schedule_id, schedule_name, agent, task, status, result,
                     started_at, finished_at, model)
                VALUES (?, ?, ?, ?, 'running', '', ?, NULL, ?)
            """, (schedule_id, name, agent, task, start, model or ""))
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
                  f"[Duration: {duration:.0f}s | Tools: {tool_calls}]\n\n{result[:10000]}",
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
                _cleanup_orphaned_chat_index_files(agent_id)
            except Exception:
                pass
            try:
                task = _build_memory_summary_prompt(agent_id)
            except Exception:
                pass

        # Use delegation infrastructure
        target = AgentConfig(agent_id)
        target_memory = MemoryStore(agent_id, base_dir=target.memory_dir)

        if not model:
            model = target.preferred_model or _delegate_fallback_model or "claude-opus-4-5-20251101"

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
        artifact_folder_name = f"{datetime.datetime.now().strftime('%Y-%m-%d')}_{sched_session_id[:8]}"

        # Track this execution
        cancel_token = CancelToken()
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

        # Build system prompt
        import platform
        # Per-task working_dir overrides the server's cwd. The agent passes
        # this as the `cwd` arg to execute_command; python_exec stays pinned
        # to the artifact folder so file-write tracking still works.
        task_working_dir = task_row.get("working_dir") or None
        cwd = task_working_dir or os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{agent_id}' executing a scheduled task: '{name}'.\n"
            f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "IMPORTANT RULES FOR SCHEDULED TASKS:\n"
            "- This is a NON-INTERACTIVE run. There is no user watching and no one\n"
            "  will answer clarifying questions. Do NOT ask for confirmation, do NOT\n"
            "  offer menus of next steps, do NOT end with 'would you like me to...'.\n"
            "- Execute every step the task describes, end-to-end. If the task says\n"
            "  'send an email', you MUST call the email-sending tool before finishing\n"
            "  — writing the file is not enough. If the task says 'post to slack',\n"
            "  actually post. Follow through on every verb.\n"
            "- When the task asks you to email a report or document, write the full\n"
            "  content to a file first (write_file saves into the session artifact\n"
            "  folder), then call gmail_send with the file path in `attachments` and\n"
            "  a SHORT summary in `body`. Do not paste the whole report into the\n"
            "  email body.\n"
            "- Complete the task QUICKLY and CONCISELY. You have a limited number of tool calls.\n"
            "- Do NOT repeat the same tool call. If a search returns results, use them — don't search again.\n"
            "- Do NOT loop. One search per topic is enough. Summarize what you found.\n"
            "- Provide a concise result summary within 3-5 tool calls maximum.\n"
            "- If you can't find what you need in 2 searches, summarize what you have and stop.\n"
            "- Your final assistant message is a log entry, not a reply to a user.\n"
            "  Keep it short, state what you did and the outcome.\n\n"
        )
        if task_working_dir:
            system_prompt += (
                f"WORKING DIRECTORY: This task should operate inside {task_working_dir}.\n"
                "When you call `execute_command`, pass `cwd` set to that path (or a\n"
                "subdirectory) unless the task says otherwise. File operations should\n"
                "be relative to that directory.\n\n"
            )
        # MemPalace migration: scheduled-task memory summary injection removed.
        tcfg = target.config.get("token_config", {})
        if tools_guide and tcfg.get("include_tools_guide", True):
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        # Per-task chat-style response compression: same prompt suffix the
        # composer toggle applies to interactive chat. Appended last so it
        # follows the tools guide and isn't accidentally compressed by a
        # future system-prompt postprocessor.
        try:
            _task_caveman = int(task_row.get("caveman_chat") or 0)
        except (TypeError, ValueError):
            _task_caveman = 0
        if _task_caveman and _task_caveman in CAVEMAN_CHAT_PROMPTS:
            system_prompt += CAVEMAN_CHAT_PROMPTS[_task_caveman]

        # Point the agent at the durable attachment paths (no per-run copy).
        # Files live under agents/<agent>/scheduled_attachments/<uuid>/<name>
        # and are persisted with the schedule definition; deletion of the
        # schedule purges them.
        task_message = task
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
            # Set thread-local context for tools that need agent/memory.
            # The synthetic session id anchors tool workdirs (python_exec,
            # write_file) and trace spans to this specific run.
            _thread_local.current_agent = target
            _thread_local.memory_store = target_memory
            _thread_local.delegate_agent_id = agent_id
            _thread_local.current_session_id = sched_session_id
            _thread_local.session_id = sched_session_id
            # Pin owner so client-mode ambient proxy can pick a tab of the
            # task's user. Empty string for legacy tasks with no owner — the
            # ambient picker will return None and the call falls through to
            # direct urlopen (which fails fast on air-gapped servers; Stage 2
            # adds a persistent admin agent client as the final fallback).
            _thread_local.current_user_id = (task_row.get("user_id") or "")
            # Clear any stale trace id from a previous run on this thread so
            # _handle_openai_response installs a fresh one that we capture
            # after the delegate returns.
            _thread_local.trace_id = None

            sched_inf = get_inference_params(model, target.config.get("model_purpose"))
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
            # Memory summary tasks only need memory tools, not the full 39-tool schema
            sched_tools = tcfg.get("scheduled_task_tools", True)
            if name.startswith("_memory_summary_"):
                sched_tools = "memory_only"
            result_text = _run_delegate(messages, model, system_prompt,
                                        memory_store=target_memory,
                                        cancel_token=cancel_token,
                                        event_callback=on_event,
                                        inference_params=sched_inf,
                                        tools=sched_tools,
                                        session_id=sched_session_id) or ""
            # Capture trace id set by _handle_openai_response so the History
            # detail view can pivot from schedule_history.run_id → traces.
            run_info["trace_id"] = getattr(_thread_local, 'trace_id', None)
            # Check if _run_delegate returned an error string instead of raising
            if result_text.startswith("Delegation error:"):
                status = "error"
                result_text = f"[DELEGATION ERROR] {result_text}"
        except TaskCancelled:
            if run_info.get("status") == "timeout":
                elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(run_info["started_at"])).total_seconds()
                result_text = (result_text or "") + f"\n\n[TIMEOUT] Task timed out after {elapsed:.0f}s (limit: {task_timeout}s). Tool calls made: {run_info['tool_calls']}."
                status = "timeout"
            else:
                result_text = (result_text or "") + f"\n\n[CANCELLED] Task was cancelled. Tool calls made: {run_info['tool_calls']}."
                status = "cancelled"
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")[:500]
            except Exception:
                pass
            result_text = f"[HTTP ERROR] {e.code} {e.reason}\n{error_body}"
            status = "error"
        except urllib.error.URLError as e:
            result_text = f"[CONNECTION ERROR] Could not reach LLM API: {e.reason}"
            status = "error"
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            result_text = f"[ERROR] {type(e).__name__}: {e}\n\n{tb[-500:]}"
            status = "error"
        finally:
            cancel_token.cancel()  # stop the watchdog if still running
            # Clean up thread-local state
            _thread_local.current_agent = None
            _thread_local.memory_store = None
            _thread_local.delegate_agent_id = None
            _thread_local.current_session_id = None
            _thread_local.session_id = None
            _thread_local.current_user_id = ""
            _thread_local.trace_id = None
            with self._lock:
                self._running_tasks.pop(name, None)

        # Only stamp artifact_folder if the folder actually materialised —
        # tasks that made no file writes shouldn't claim a folder.
        _artifact_dir = os.path.join(AGENTS_DIR, agent_id, "artifacts", artifact_folder_name)
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
            rd_cfg = _get_relationship_discovery_config(agent_id)
            if rd_cfg.get("enabled"):
                try:
                    trigger_relationship_discovery(agent_id)
                except Exception:
                    pass

        # Chain autodream after relationship discovery completes
        if name.startswith("_relationship_discovery_") and status == "success":
            ad_cfg = _get_autodream_config(agent_id)
            if ad_cfg.get("enabled"):
                try:
                    trigger_autodream(agent_id)
                except Exception:
                    pass

        # Fire notification hook for task completion/failure
        if _notification_hook:
            try:
                if status in ("error", "timeout"):
                    evt = "task_timeout" if status == "timeout" else "task_failed"
                    sev = "warning" if status == "timeout" else "error"
                    _notification_hook(evt, f"Scheduled task: {name}",
                                       result_text[:300], severity=sev,
                                       agent=agent_id,
                                       metadata={"task_name": name, "status": status,
                                                  "tool_calls": run_info.get("tool_calls", 0)})
                elif status == "success" and not name.startswith("_memory_summary_") \
                        and not name.startswith("_relationship_discovery_"):
                    _notification_hook("task_complete", f"Task completed: {name}",
                                       f"Agent {agent_id} completed '{name}' with {run_info.get('tool_calls', 0)} tool calls.",
                                       severity="info", agent=agent_id,
                                       metadata={"task_name": name})
            except Exception:
                pass


# Global scheduler instance
_scheduler: Scheduler | None = None

# Notification hook — set by server.py to dispatch notifications
_notification_hook = None
