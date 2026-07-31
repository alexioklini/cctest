---
name: Sync run log + history modal — implementation plan
description: Full plan for project_sync_runs table, SyncRunLogger, cancel signal, history modal UI, and full-resync drawer wipe fix
type: project
originSessionId: 5de3c08d-5772-4668-855c-d46bd22bcd1f
---
## Status: PLANNED — not started. Start fresh session with this plan.

## What triggered this
- GUI showed nothing during a sync because KG runs after state flips to idle
- full_resync drawer wipe was broken (wrong _mp reference + used project name not ID)
- User wants: detailed sync log, history modal, cancel running sync

## 1. DB: `project_sync_runs` table (chats.db)

```sql
CREATE TABLE project_sync_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id   TEXT NOT NULL,
  started_at   REAL NOT NULL,
  finished_at  REAL,
  triggered_by TEXT NOT NULL DEFAULT 'scheduled',  -- 'scheduled'|'manual'|'full_resync'
  state        TEXT NOT NULL DEFAULT 'running',     -- 'running'|'idle'|'error'|'cancelled'
  log          TEXT NOT NULL DEFAULT '{}',          -- JSON, updated live
  summary      TEXT NOT NULL DEFAULT '{}'           -- JSON, written at end
);
CREATE INDEX idx_sync_runs_project ON project_sync_runs(project_id, started_at DESC);
```

`log` JSON (updated live per step per folder):
```json
{
  "steps": {
    "doc_convert":   { "started_at", "finished_at", "files_converted", "files_skipped", "files_removed", "errors": [] },
    "indexing":      { "started_at", "finished_at", "drawers_created", "drawers_updated", "drawers_deleted", "files_filed", "errors": [] },
    "closet_rerank": { "started_at", "finished_at", "sources_seen", "sources_reranked", "triggered": bool, "errors": [] },
    "kg_extraction": { "started_at", "finished_at", "chunks_seen", "chunks_new", "chunks_skipped", "triples_added", "triples_removed", "errors": [] }
  },
  "folders": [ { "path", "steps": { ... same ... } } ]
}
```

`summary` JSON (written once at end):
```json
{
  "total_files", "files_converted", "files_skipped", "files_removed",
  "drawers_total", "drawers_created", "drawers_updated", "drawers_deleted",
  "closets_total", "closets_reranked",
  "triples_total", "triples_added", "triples_removed",
  "elapsed_s", "final_state", "errors": []
}
```

## 2. New file: `engine/sync_log.py` — `SyncRunLogger` class

Methods:
- `start_run(project_id, triggered_by)` → run_id
- `step_start(run_id, step_name, folder=None)`
- `step_update(run_id, step_name, folder=None, **fields)` — incremental JSON merge
- `step_finish(run_id, step_name, folder=None, **fields)`
- `finish_run(run_id, state, summary)`
- `cancel_run(run_id)`
- `get_runs(project_id, limit=20)` → list
- `get_run(run_id)` → dict

All writes: `UPDATE project_sync_runs SET log=? WHERE id=?` with JSON merge. Safe from concurrent reads.
DB path: `agents/main/chats.db` (same as rest of project data).

## 3. server.py changes

- **Cancel signal**: add `_project_sync_cancel: set[str]` (keyed by project_id).
  - Add helper `_project_sync_cancel_request(project_id)` and `_project_sync_cancel_check(project_id)`.
  - Check after each major step (doc_convert, miner, KG per-folder, closet regen).
  - On match: `sync_log.cancel_run(run_id)`, set `last_error="cancelled"`, break inner folder loop, clear signal.
- **Logger wiring**: create run at start of per-project block, call step_start/step_finish/finish_run.
- **Stats to collect** (already partially available, need to thread through):
  - doc_convert: `files_converted`, `files_skipped`, `files_removed` — from `doc_convert.convert_folder()` return value (currently unused)
  - indexing: `drawers_created` from miner stdout "Drawers filed: N", `drawers_deleted` from stale sweep
  - closet_regen: `sources_seen`, `sources_reranked`, `triggered` from `run_closet_regen_incremental` return
  - kg: already has `res.drawers_processed`, `res.drawers_skipped`, `res.triples_extracted`, `res.errors`

## 4. handlers/projects.py changes

- **Fix full_resync drawer wipe**: use `from server import _mp` + `pid` (not name):
  ```python
  from server import _mp
  deleted = _mp.purge_by_prefix(wing=f"project__{pid}", prefix="")
  ```
- **New endpoints**:
  - `GET /v1/agents/:agent/projects/:name/sync-runs?limit=20` → list
  - `GET /v1/agents/:agent/projects/:name/sync-runs/:id` → detail
  - `POST /v1/agents/:agent/projects/:name/sync-cancel` → sets cancel signal by project_id

## 5. server.py routing

Add routes:
```python
elif path ends with "/sync-runs": _handle_project_sync_runs(path)
elif "/sync-runs/" in path: _handle_project_sync_run_detail(path)
elif path ends with "/sync-cancel": _handle_project_sync_cancel(path)
```

## 6. Frontend: modal + history

**index.html additions:**
- "History" button next to "Sync now" (admin-only, same gate as KG button)
- Modal `#sync-history-modal` with close button, run list container, and loading state

**panels.js additions:**
- `projectSyncHistory()` — opens modal, fetches runs
- `_renderSyncRuns(runs)` — accordion list: each row = date + triggered_by badge + state badge + elapsed + drawer/triple delta
- Click row → expand accordion: step table (step | started | elapsed | key stats | errors), folder breakdown, summary block
- Cancel button on running row → `POST /sync-cancel`, disables button, re-polls
- Auto-poll running row every 3s: `GET /sync-runs/:id`, re-render expanded section
- State badge colors: running=blue pulse, idle=green, error=red, cancelled=grey

## 7. Files touched summary
| File | Change |
|------|--------|
| `engine/sync_log.py` | NEW |
| `server.py` | Cancel set + helpers, logger wiring in daemon, 3 new routes |
| `handlers/projects.py` | Fix wipe, 3 new handlers |
| `web/index.html` | History button + modal skeleton |
| `web/js/panels.js` | Modal functions, accordion render, cancel, poll |

## 8. full_resync: log the wipe phase

When `triggered_by="full_resync"`, the log JSON includes a `wipe` step BEFORE the sync steps:
```json
{
  "steps": {
    "wipe": {
      "started_at", "finished_at",
      "drawers_deleted",
      "triples_deleted",
      "kg_progress_rows_cleared",
      "closet_cursor_cleared",
      "brain_extracted_dirs_cleared",
      "errors": []
    },
    "doc_convert": { ... },
    ...
  }
}
```
This lets the user verify every wipe stage completed before re-indexing began.

## Key invariants to respect
- Project identified by `project_id` (uuid4 hex[:12]) throughout — never by name
- Cancel signal keyed by `project_id` not `(agent, name)` — projects have stable IDs
- SyncRunLogger must not throw — all DB calls wrapped in try/except, daemon must not crash
- Logger writes are JSON-merge atomic per run_id — no cross-run corruption
- doc_convert return value: check what `convert_folder()` currently returns — may need to add stats return
- miner stdout parse: "Drawers filed: N" already parsed, also check for "Drawers updated/deleted" lines
- closet_regen: `run_closet_regen_incremental` already returns `{sources_seen, sources_stale, regen_triggered, elapsed_s}` — use directly
- KG: `res` is a dataclass with `drawers_processed`, `drawers_skipped`, `triples_extracted`, `errors`, `error_msg`, `elapsed_s`

**Why:** User needs visibility into what the sync daemon is actually doing — currently a black box. Cancel needed because long syncs (KG extraction ~10min) can't be interrupted. Drawer wipe by ID not name prevents wipe of wrong project if name is reused.
