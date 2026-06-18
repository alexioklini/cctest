"""SyncRunLogger — persistent log for project-sync daemon runs.

Each sync cycle for a project creates one row in `project_sync_runs` (chats.db).
The `log` column is updated live as steps execute; `summary` is written once at
the end. All DB operations are wrapped in try/except so a logging failure never
crashes the daemon.
"""

import json
import os
import sqlite3
import threading
import time

_lock = threading.Lock()


def _db(chats_db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(chats_db_path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _ensure_table(chats_db_path: str):
    try:
        with _db(chats_db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS project_sync_runs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id   TEXT    NOT NULL,
                    started_at   REAL    NOT NULL,
                    finished_at  REAL,
                    triggered_by TEXT    NOT NULL DEFAULT 'scheduled',
                    state        TEXT    NOT NULL DEFAULT 'running',
                    log          TEXT    NOT NULL DEFAULT '{}',
                    summary      TEXT    NOT NULL DEFAULT '{}'
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_runs_project
                ON project_sync_runs(project_id, started_at DESC)
            """)
            con.commit()
    except Exception as e:
        print(f"[sync_log] ensure_table failed: {e}", flush=True)


_table_ensured: set[str] = set()


def _maybe_ensure(chats_db_path: str):
    if chats_db_path not in _table_ensured:
        _ensure_table(chats_db_path)
        _table_ensured.add(chats_db_path)


def start_run(chats_db_path: str, project_id: str,
              triggered_by: str = "scheduled") -> int | None:
    """Insert a new running row and return its id (or None on failure)."""
    _maybe_ensure(chats_db_path)
    try:
        with _lock, _db(chats_db_path) as con:
            cur = con.execute(
                "INSERT INTO project_sync_runs "
                "(project_id, started_at, triggered_by, state, log, summary) "
                "VALUES (?, ?, ?, 'running', '{}', '{}')",
                (project_id, time.time(), triggered_by),
            )
            con.commit()
            return cur.lastrowid
    except Exception as e:
        print(f"[sync_log] start_run failed: {e}", flush=True)
        return None


def _merge_log(chats_db_path: str, run_id: int,
               step: str, folder: str | None, **fields):
    """Merge **fields into log.steps[step] (or log.folders[i].steps[step])."""
    try:
        with _lock, _db(chats_db_path) as con:
            row = con.execute(
                "SELECT log FROM project_sync_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not row:
                return
            log = json.loads(row["log"] or "{}")
            if folder is None:
                steps = log.setdefault("steps", {})
                entry = steps.setdefault(step, {})
                entry.update(fields)
            else:
                folders = log.setdefault("folders", [])
                fe = next((f for f in folders if f.get("path") == folder), None)
                if fe is None:
                    fe = {"path": folder, "steps": {}}
                    folders.append(fe)
                entry = fe.setdefault("steps", {}).setdefault(step, {})
                entry.update(fields)
            con.execute(
                "UPDATE project_sync_runs SET log=? WHERE id=?",
                (json.dumps(log), run_id),
            )
            con.commit()
    except Exception as e:
        print(f"[sync_log] _merge_log failed (run={run_id} step={step}): {e}",
              flush=True)


def step_start(chats_db_path: str, run_id: int,
               step: str, folder: str | None = None):
    _merge_log(chats_db_path, run_id, step, folder,
               started_at=time.time(), errors=[])


def step_update(chats_db_path: str, run_id: int,
                step: str, folder: str | None = None, **fields):
    _merge_log(chats_db_path, run_id, step, folder, **fields)


def step_finish(chats_db_path: str, run_id: int,
                step: str, folder: str | None = None, **fields):
    _merge_log(chats_db_path, run_id, step, folder,
               finished_at=time.time(), **fields)


def finish_run(chats_db_path: str, run_id: int,
               state: str, summary: dict):
    try:
        with _lock, _db(chats_db_path) as con:
            con.execute(
                "UPDATE project_sync_runs "
                "SET finished_at=?, state=?, summary=? WHERE id=?",
                (time.time(), state, json.dumps(summary), run_id),
            )
            con.commit()
    except Exception as e:
        print(f"[sync_log] finish_run failed (run={run_id}): {e}", flush=True)


def cancel_run(chats_db_path: str, run_id: int):
    try:
        with _lock, _db(chats_db_path) as con:
            con.execute(
                "UPDATE project_sync_runs "
                "SET finished_at=?, state='cancelled' WHERE id=?",
                (time.time(), run_id),
            )
            con.commit()
    except Exception as e:
        print(f"[sync_log] cancel_run failed (run={run_id}): {e}", flush=True)


def reconcile_orphans(chats_db_path: str) -> int:
    """Close any run still in state='running' — a process can only have ONE
    live project-sync run at a time (single daemon thread), so on boot every
    'running' row is a leftover from a crash / restart / pre-finally-fix orphan.
    Mark them 'error' with finished_at=now so the UI stops showing phantom
    "mining in progress". Returns the number of rows closed. Idempotent.
    Safe to call at boot — the daemon hasn't started its first run yet."""
    try:
        with _lock, _db(chats_db_path) as con:
            cur = con.execute(
                "UPDATE project_sync_runs "
                "SET finished_at=COALESCE(finished_at, ?), state='error', "
                "summary=json_set(COALESCE(NULLIF(summary,''),'{}'), "
                "'$.final_state','error','$.reconciled',1) "
                "WHERE state='running'",
                (time.time(),),
            )
            con.commit()
            return cur.rowcount or 0
    except Exception as e:
        print(f"[sync_log] reconcile_orphans failed: {e}", flush=True)
        return 0


def get_runs(chats_db_path: str, project_id: str, limit: int = 20) -> list[dict]:
    _maybe_ensure(chats_db_path)
    try:
        with _db(chats_db_path) as con:
            rows = con.execute(
                "SELECT id, project_id, started_at, finished_at, "
                "triggered_by, state, summary "
                "FROM project_sync_runs "
                "WHERE project_id=? "
                "ORDER BY started_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[sync_log] get_runs failed: {e}", flush=True)
        return []


def last_completed_at(chats_db_path: str, project_id: str) -> float | None:
    """Epoch seconds of this project's most recent SUCCESSFULLY-finished sync run,
    or None if it has never completed one. Used to gate scheduled passes so a
    server restart doesn't re-trigger a not-yet-due sync (the interval survives
    restarts). A successful pass finishes 'idle' (the daemon's success state,
    incl. dedup-only cycles that filed nothing); 'error'/'cancelled'/unfinished
    don't count, so those retry on the next pass."""
    _maybe_ensure(chats_db_path)
    try:
        with _db(chats_db_path) as con:
            row = con.execute(
                "SELECT finished_at FROM project_sync_runs "
                "WHERE project_id=? AND state='idle' AND finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return float(row["finished_at"]) if row and row["finished_at"] else None
    except Exception as e:
        print(f"[sync_log] last_completed_at failed: {e}", flush=True)
        return None


def log_purge_actions(chats_db_path: str, run_id: int, actions: list[dict]):
    """Append purge action records to log.purge_actions for a full-resync run.

    Each entry in `actions` is a dict with at least {"action": str} plus any
    numeric fields (deleted, elapsed_s, error, …).
    """
    try:
        with _lock, _db(chats_db_path) as con:
            row = con.execute(
                "SELECT log FROM project_sync_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not row:
                return
            log = json.loads(row["log"] or "{}")
            bucket = log.setdefault("purge_actions", [])
            bucket.extend(actions)
            con.execute(
                "UPDATE project_sync_runs SET log=? WHERE id=?",
                (json.dumps(log), run_id),
            )
            con.commit()
    except Exception as e:
        print(f"[sync_log] log_purge_actions failed (run={run_id}): {e}",
              flush=True)


def get_run(chats_db_path: str, run_id: int) -> dict | None:
    _maybe_ensure(chats_db_path)
    try:
        with _db(chats_db_path) as con:
            row = con.execute(
                "SELECT id, project_id, started_at, finished_at, "
                "triggered_by, state, log, summary "
                "FROM project_sync_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if row:
                d = dict(row)
                d["log"] = json.loads(d.get("log") or "{}")
                d["summary"] = json.loads(d.get("summary") or "{}")
                return d
            return None
    except Exception as e:
        print(f"[sync_log] get_run failed: {e}", flush=True)
        return None
