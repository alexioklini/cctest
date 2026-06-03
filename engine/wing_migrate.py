"""One-time migration from the OLD single shared MemPalace collection to the new
PER-WING collections.

Context: historically every wing's drawers lived in ONE Chroma collection
(`mempalace_drawers`) + one closets collection (`mempalace_closets`). Per-wing
isolation (engine/wing_collections.py) gives each wing its OWN collection so a
fault in one wing can't corrupt another. On the first boot after that change the
per-wing collections are empty and the old shared collection still holds
everything — this module moves the data across, ONCE, idempotently, with no admin
action.

Strategy (decided 2026-06-03):
  * FILE-derived wings (project__<id>, brain_code, *_artifacts, project web-urls)
    re-mine themselves on the daemons' normal cycles into the per-wing
    collections — nothing to do here except let them run; the miner's content
    hash dedups, so a re-mine into an empty per-wing collection just fills it.
  * CHAT-derived wings (project_chat__<id>, user__<id> chat turns, summaries) are
    NOT on disk — re-derived by RESETTING the chat-sync cursors so the chat-sync
    daemon re-files every turn from the durable chat DB into the per-wing
    collections. Same for closet-regen + KG progress cursors so those rebuild
    per-wing too.
  * VERIFY-BEFORE-DESTROY: only after every wing's per-wing drawer count is
    >= the count the old shared collection's sqlite holds for that wing do we
    DROP the old shared collection. If ANY wing falls short, KEEP the old
    collection and log — a half-migrated palace never loses the old data.

Idempotent: a `wing_migrate_state.json` marker beside the palace records the
phase. Re-running after completion is a no-op. Re-running mid-flight resumes.

Runs in a background thread at startup (see server.py main()); never blocks boot.
"""

from __future__ import annotations

import json
import os
import time


_STATE_FILE = "wing_migrate_state.json"
# Phases: "" (not started) -> "cursors_reset" (chat re-derive kicked, file wings
# re-mining) -> "verified" (counts ok, old collection dropped) = DONE.


def _palace_path() -> str:
    try:
        import brain as _brain
        return (_brain._load_mempalace_config() or {}).get("palace_path", "")
    except Exception:
        return ""


def _state_path(palace_path: str) -> str:
    return os.path.join(palace_path, _STATE_FILE)


def _read_state(palace_path: str) -> dict:
    try:
        with open(_state_path(palace_path)) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_state(palace_path: str, state: dict) -> None:
    try:
        tmp = _state_path(palace_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _state_path(palace_path))
    except Exception as e:
        print(f"[wing-migrate] could not write state: {e}", flush=True)


def _old_collection_counts_by_wing(palace_path: str) -> dict:
    """Per-wing drawer counts in the OLD shared collection, read straight from
    its metadata (the ground truth we must reach before dropping it). Returns
    {} when the old collection is absent (already migrated/fresh install)."""
    import engine.wing_collections as _wc
    from mempalace.palace import get_collection as _gc
    try:
        col = _gc(palace_path, collection_name=_wc.LEGACY_DRAWERS, create=False)
    except Exception:
        return {}
    if col is None:
        return {}
    try:
        got = col.get(include=["metadatas"])
    except Exception:
        return {}
    counts: dict = {}
    for m in (got.get("metadatas") or []):
        w = (m or {}).get("wing") or ""
        if w:
            counts[w] = counts.get(w, 0) + 1
    return counts


def _old_collection_exists(palace_path: str) -> bool:
    import engine.wing_collections as _wc
    try:
        import chromadb
        client = chromadb.PersistentClient(path=palace_path)
        return any(c.name == _wc.LEGACY_DRAWERS for c in client.list_collections())
    except Exception:
        return False


def _reset_chat_cursors() -> None:
    """Drop chat-sync + closet-regen + KG progress cursors so those daemons
    re-derive every chat/summary/closet/triple into the per-wing collections
    from the durable chat DB. The raw chat turns are untouched — only the
    'already filed' bookkeeping is cleared, so nothing is lost."""
    from server_lib.db import _db_conn
    with _db_conn() as c:
        for tbl in ("chat_mempalace_sync", "closet_regen_progress",
                    "kg_extraction_progress", "kg_extraction_source_state"):
            try:
                c.execute(f"DELETE FROM {tbl}")
            except Exception as e:
                print(f"[wing-migrate] reset {tbl}: {e}", flush=True)
        c.commit()


def _drop_old_collection(palace_path: str) -> None:
    import engine.wing_collections as _wc
    import chromadb
    client = chromadb.PersistentClient(path=palace_path)
    for name in (_wc.LEGACY_DRAWERS, _wc.LEGACY_CLOSETS):
        try:
            client.delete_collection(name)
            print(f"[wing-migrate] dropped old shared collection {name}", flush=True)
        except Exception as e:
            print(f"[wing-migrate] drop {name}: {e}", flush=True)


def _verify(palace_path: str, expected: dict) -> tuple[bool, dict]:
    """For each wing, is the per-wing drawer count >= what the old collection
    held? Returns (all_ok, per_wing_report)."""
    import engine.wing_collections as _wc
    report: dict = {}
    all_ok = True
    for wing, want in expected.items():
        col = _wc.get_wing_collection(palace_path, wing, create=False, kind="drawers")
        have = 0
        if col is not None:
            try:
                have = col.count()
            except Exception:
                have = 0
        report[wing] = {"want": want, "have": have, "ok": have >= want}
        if have < want:
            all_ok = False
    return all_ok, report


def migrate_if_needed(*, verify_attempts: int = 30, verify_interval_s: float = 60.0
                      ) -> dict:
    """Entry point (background thread). Returns a small summary dict.

    Phase 1 (once): record the old per-wing target counts, reset chat cursors so
    chat wings re-derive, and let the file-wing daemons re-mine. Mark
    'cursors_reset'.
    Phase 2 (polled): periodically VERIFY per-wing counts have caught up; once
    every wing meets its target, DROP the old shared collection and mark
    'verified' (DONE). If the old collection doesn't exist (fresh install / already
    migrated), mark DONE immediately.
    """
    palace_path = _palace_path()
    if not palace_path or not os.path.isdir(palace_path):
        return {"skipped": "no palace"}

    state = _read_state(palace_path)
    if state.get("phase") == "verified":
        return {"done": True, "already": True}

    # Nothing to migrate if the old shared collection isn't there.
    if not _old_collection_exists(palace_path):
        _write_state(palace_path, {"phase": "verified", "note": "no old collection",
                                   "ts": time.time()})
        return {"done": True, "nothing_to_migrate": True}

    # Phase 1: snapshot targets + reset chat cursors (once).
    if state.get("phase") != "cursors_reset":
        expected = _old_collection_counts_by_wing(palace_path)
        print(f"[wing-migrate] starting: {len(expected)} wings to migrate "
              f"({sum(expected.values())} drawers). Resetting chat cursors; file "
              f"wings re-mine on daemon cycles.", flush=True)
        _reset_chat_cursors()
        state = {"phase": "cursors_reset", "expected": expected, "ts": time.time()}
        _write_state(palace_path, state)

    expected = state.get("expected") or {}
    if not expected:
        _drop_old_collection(palace_path)
        _write_state(palace_path, {"phase": "verified", "ts": time.time()})
        return {"done": True, "empty_expected": True}

    # Phase 2: poll until per-wing counts catch up, then drop the old collection.
    for attempt in range(1, verify_attempts + 1):
        all_ok, report = _verify(palace_path, expected)
        if all_ok:
            _drop_old_collection(palace_path)
            _write_state(palace_path, {"phase": "verified", "report": report,
                                       "ts": time.time()})
            print("[wing-migrate] VERIFIED all wings migrated — old shared "
                  "collection dropped. Done.", flush=True)
            return {"done": True, "report": report}
        short = {w: r for w, r in report.items() if not r["ok"]}
        detail = ", ".join(f"{w}: {r['have']}/{r['want']}" for w, r in short.items())
        print(f"[wing-migrate] attempt {attempt}/{verify_attempts}: "
              f"{len(short)} wing(s) still catching up: {detail}", flush=True)
        time.sleep(verify_interval_s)

    # Did not converge in the allotted window — KEEP the old collection (safety)
    # and leave the marker at 'cursors_reset' so a later boot resumes verifying.
    print("[wing-migrate] did NOT converge within the window — KEEPING the old "
          "shared collection (no data loss). Will resume verifying on next boot.",
          flush=True)
    return {"done": False, "kept_old": True}
