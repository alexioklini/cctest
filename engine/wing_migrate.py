"""One-time migration from the OLD single shared MemPalace collection to the new
PER-WING collections.

Context: historically every wing's drawers lived in ONE Chroma collection
(`mempalace_drawers`) + one closets collection (`mempalace_closets`). Per-wing
isolation (engine/wing_collections.py) gives each wing its OWN collection so a
fault in one wing can't corrupt another. On the first boot after that change the
per-wing collections are empty and the old shared collection still holds
everything — this module moves the data across, ONCE, idempotently, with no admin
action.

Strategy (decided 2026-06-03 — DIRECT COPY):
  Copy each drawer (and closet) STRAIGHT from the old shared collection into its
  wing's per-wing collection, grouped by the drawer's `wing` metadata. Read
  ids+documents+metadatas from `mempalace_drawers`/`mempalace_closets`, upsert
  into `wd_<wing>`/`wc_<wing>`. This bypasses the miner's mtime/content-hash gate
  entirely (which SKIPS unchanged sources, so a re-mine would NOT refill them) and
  preserves EVERYTHING — chat history, summaries, profiles, unchanged project
  docs — without re-embedding or re-deriving. One in-process pass, converges
  immediately.

  VERIFY-BEFORE-DESTROY: only after every wing's per-wing drawer count is
  >= the count the old shared collection holds for that wing do we DROP the old
  shared collection. If ANY wing falls short, KEEP the old collection and log —
  a half-migrated palace never loses the old data.

Idempotent: a `wing_migrate_state.json` marker beside the palace records the
phase. The copy upserts by deterministic id, so re-running is safe (re-copies
are no-ops). Re-running after completion is a no-op.

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


def _copy_old_collection_to_wings(palace_path: str, kind: str,
                                  legacy_name: str, _palace_write_lock) -> dict:
    """Copy every row from the OLD shared collection (`legacy_name`) into the
    per-wing collections, grouped by each row's `wing` metadata. `kind` is
    "drawers" or "closets". Upserts preserve the original id + document +
    metadata, so the per-wing copy is byte-identical and re-runs are no-ops.
    Returns {wing: copied_count}. Batched to stay under SQLite var limits."""
    import engine.wing_collections as _wc
    from mempalace.palace import get_collection as _gc
    try:
        old = _gc(palace_path, collection_name=legacy_name, create=False)
    except Exception:
        return {}
    if old is None:
        return {}
    try:
        got = old.get(include=["documents", "metadatas"])
    except Exception as e:
        print(f"[wing-migrate] read old {legacy_name} failed: {e}", flush=True)
        return {}
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    # Bucket rows by wing.
    buckets: dict = {}
    for i, _id in enumerate(ids):
        meta = (metas[i] if i < len(metas) else {}) or {}
        wing = meta.get("wing") or ""
        if not wing:
            continue
        buckets.setdefault(wing, {"ids": [], "docs": [], "metas": []})
        buckets[wing]["ids"].append(_id)
        buckets[wing]["docs"].append(docs[i] if i < len(docs) else "")
        buckets[wing]["metas"].append(meta)
    copied: dict = {}
    for wing, b in buckets.items():
        try:
            col = _wc.get_wing_collection(palace_path, wing, create=True, kind=kind)
            if col is None:
                continue
            n = len(b["ids"])
            for off in range(0, n, 1000):  # batch to avoid SQLite var limits
                sl = slice(off, off + 1000)
                with _palace_write_lock:
                    col.upsert(ids=b["ids"][sl], documents=b["docs"][sl],
                               metadatas=b["metas"][sl])
            copied[wing] = n
        except Exception as e:
            print(f"[wing-migrate] copy {kind} wing {wing} failed: {e}", flush=True)
    return copied


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


def migrate_if_needed() -> dict:
    """Entry point (background thread). DIRECT COPY: copy every drawer + closet
    from the old shared collection into the per-wing collections (grouped by
    wing metadata), VERIFY per-wing counts >= old per-wing counts, then DROP the
    old shared collection. Idempotent (upsert by id) + verify-gated (never drops
    on a short copy). Returns a small summary dict.
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

    try:
        from server_daemons import _palace_write_lock
    except Exception:
        import contextlib
        _palace_write_lock = contextlib.nullcontext()

    expected = _old_collection_counts_by_wing(palace_path)
    if not expected:
        # Old collection present but empty → safe to drop.
        _drop_old_collection(palace_path)
        _write_state(palace_path, {"phase": "verified", "ts": time.time()})
        return {"done": True, "empty_old": True}

    print(f"[wing-migrate] DIRECT COPY: {len(expected)} wings, "
          f"{sum(expected.values())} drawers → per-wing collections.", flush=True)
    _write_state(palace_path, {"phase": "copying", "expected": expected,
                               "ts": time.time()})

    import engine.wing_collections as _wc
    d_copied = _copy_old_collection_to_wings(
        palace_path, "drawers", _wc.LEGACY_DRAWERS, _palace_write_lock)
    c_copied = _copy_old_collection_to_wings(
        palace_path, "closets", _wc.LEGACY_CLOSETS, _palace_write_lock)
    print(f"[wing-migrate] copied drawers={sum(d_copied.values())} "
          f"closets={sum(c_copied.values())} across {len(d_copied)} wings.",
          flush=True)

    # VERIFY-BEFORE-DESTROY.
    all_ok, report = _verify(palace_path, expected)
    if all_ok:
        _drop_old_collection(palace_path)
        _write_state(palace_path, {"phase": "verified", "report": report,
                                   "ts": time.time()})
        print("[wing-migrate] VERIFIED — old shared collection dropped. Done.",
              flush=True)
        return {"done": True, "report": report}

    short = {w: r for w, r in report.items() if not r["ok"]}
    detail = ", ".join(f"{w}: {r['have']}/{r['want']}" for w, r in short.items())
    print(f"[wing-migrate] copy did NOT fully verify ({len(short)} wing(s) short: "
          f"{detail}) — KEEPING the old shared collection (no data loss). "
          f"Re-runs on next boot.", flush=True)
    _write_state(palace_path, {"phase": "copying", "expected": expected,
                               "report": report, "ts": time.time()})
    return {"done": False, "kept_old": True, "short": short}
