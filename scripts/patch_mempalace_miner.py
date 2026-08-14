#!/usr/bin/env python3
"""Apply (or revert) the BRAIN-PATCH for mempalace miner partial-write rollback.

WHY (mempalace-review finding M6):
  mempalace's miner.py `process_file` stamps `source_mtime` on EVERY chunk and
  writes chunks in sub-batches of DRAWER_UPSERT_BATCH_SIZE (1000). If a
  sub-batch upsert fails (e.g. the embedding/model pass dies), the file is
  left half-indexed WITH the current mtime — and Brain's project-sync
  pre-filter (`server_daemons.py`, mtime-gated) then skips that file on every
  later cycle, leaving a PERMANENTLY TRUNCATED index until the file's mtime
  changes.

  The patch wraps each sub-batch upsert: on failure it deletes the file's
  partial drawers (best-effort) and re-raises, so the mtime never matches a
  partial index and the next cycle re-mines the whole file. Safe direction:
  worst case the file is fully un-indexed (re-mined), never half-indexed.

  This is an EXTERNAL pip package (installed in the mempalace venv), so the
  patch lives in site-packages — the same model as the Qdrant quantization
  patch (backends/qdrant.py, # BRAIN-PATCH). RE-APPLY after every mempalace
  upgrade: `python3 scripts/patch_mempalace_miner.py`.

Usage:
  python3 scripts/patch_mempalace_miner.py          # apply (idempotent)
  python3 scripts/patch_mempalace_miner.py --revert # remove the patch
"""
import os
import sys

MARKER = "# BRAIN-PATCH (mempalace-review M6, 2026-08-05): roll back partial"

OLD = """            assert_no_collisions(list(zip(batch_ids, batch_metas)), collection)
            collection.upsert(
                documents=batch_docs,
                ids=batch_ids,
                metadatas=batch_metas,
            )
            drawers_added += len(batch_docs)
            all_metas.extend(batch_metas)
"""

NEW = """            assert_no_collisions(list(zip(batch_ids, batch_metas)), collection)
            # BRAIN-PATCH (mempalace-review M6, 2026-08-05): roll back partial
            # writes. source_mtime is stamped per chunk, so a failure after
            # some sub-batches leaves the file half-indexed WITH the current
            # mtime — Brain's project-sync pre-filter then skips the file
            # forever (permanently truncated index). Delete this file's
            # drawers so the mtime never matches a partial index; the next
            # cycle re-mines the whole file. Best-effort cleanup, then
            # re-raise so the caller knows the file failed. Re-apply on every
            # mempalace upgrade (see CLAUDE.md -> MemPalace venv patches).
            try:
                collection.upsert(
                    documents=batch_docs,
                    ids=batch_ids,
                    metadatas=batch_metas,
                )
            except Exception:
                try:
                    collection.delete(where={"source_file": source_file})
                except Exception:
                    pass
                raise
            drawers_added += len(batch_docs)
            all_metas.extend(batch_metas)
"""


def find_miner_path() -> str | None:
    candidates = []
    # 1) Resolve via the running interpreter (matches how Brain imports it).
    try:
        import mempalace  # noqa: F401
        candidates.append(os.path.join(os.path.dirname(mempalace.__file__), "miner.py"))
    except Exception:
        pass
    # 2) The launchd daemon interpreter's site-packages under ~/.mempalace.
    venv_root = os.path.expanduser("~/.mempalace/venv")
    if os.path.isdir(venv_root):
        for sp in os.listdir(venv_root):
            sp_full = os.path.join(venv_root, sp)
            if not sp.startswith("lib") or not os.path.isdir(sp_full):
                continue
            for st in os.listdir(sp_full):
                if not st.startswith("python"):
                    continue
                spath = os.path.join(sp_full, st, "site-packages", "mempalace", "miner.py")
                if os.path.isfile(spath):
                    candidates.append(spath)
    seen = set()
    for c in candidates:
        if c not in seen and os.path.isfile(c):
            seen.add(c)
    return next(iter(seen), None)


def apply_patch(src: str) -> str:
    """Return the patched source. Raises ValueError if the anchor isn't unique."""
    if MARKER in src:
        return src  # already applied — idempotent
    if src.count(OLD) != 1:
        raise ValueError(
            f"expected anchor not found exactly once ({src.count(OLD)}) — "
            "mempalace version changed? Refusing to patch.")
    return src.replace(OLD, NEW)


def revert_patch(src: str) -> str:
    """Return the reverted (original) source. Raises ValueError if the
    patched body doesn't match what this script installed."""
    if MARKER not in src:
        return src  # not applied — idempotent
    if src.count(NEW) != 1:
        raise ValueError(
            f"BRAIN-PATCH marker found but body differs ({src.count(NEW)}) — "
            "refusing to auto-revert.")
    return src.replace(NEW, OLD)


def main() -> int:
    revert = "--revert" in sys.argv
    path = find_miner_path()
    if not path:
        print("ERROR: mempalace/miner.py not found (is the mempalace venv installed?)", file=sys.stderr)
        return 2
    src = open(path, encoding="utf-8").read()
    if revert:
        try:
            src = revert_patch(src)
        except ValueError as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 2
        print(f"{path}: patch REVERTED.")
    else:
        try:
            src = apply_patch(src)
        except ValueError as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 2
        if MARKER not in src:
            print(f"{path}: patch already applied — nothing to do.")
            return 0
        print(f"{path}: patch APPLIED.")
    open(path, "w", encoding="utf-8").write(src)
    import py_compile
    py_compile.compile(path, doraise=True)
    print(f"{path}: py_compile OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
