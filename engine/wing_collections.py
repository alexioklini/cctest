"""Per-wing ChromaDB collection routing for MemPalace fault isolation.

WHY THIS EXISTS
---------------
By default MemPalace stores EVERY wing's drawers in ONE Chroma collection
(`mempalace_drawers`) backed by ONE physical HNSW index segment. Wings are only
a metadata field, filtered at query time. Consequence: a fault in one wing's
data — a bulk delete racing an upsert, an unflushed HNSW + process death — wedges
the single shared segment, and the next boot quarantines + rebuilds it
PALACE-WIDE. One churning project (e.g. a volatile news web-URL re-mined every
cycle) can take down retrieval for every tenant.

This module routes each wing to its OWN collection (its own HNSW index), so a
fault is contained to one wing and recovery rebuilds only that wing — auto-healing
with minimal blast radius and no admin intervention.

SHIPS DARK
----------
ALWAYS ON (no flag). Per-wing isolation is how the palace works, not a toggle —
decided 2026-06-03: a default-off flag would leave the corruption-prone shared
index live by default, the opposite of the goal, and would mean two code paths
forever. A one-time migration (see the migration module) moves existing drawers
from the old shared collection into per-wing collections, verify-before-destroy.

This module is PURE + import-cycle-free: it must NOT `import brain`. It reaches
the mempalace package lazily inside functions (the package's `get_collection`
already accepts a `collection_name`, so per-wing routing needs no package patch on
the read side; only the miner needs the venv patch — see `assert_miner_patch`).
"""

from __future__ import annotations

import hashlib
import os
import re

# Legacy shared collection names — the OLD single-collection world. No longer
# used for runtime routing (per-wing is always on); kept ONLY so the one-time
# migration can read the old shared collection it migrates FROM. These match the
# mempalace package defaults (palace.get_collection / get_closets_collection).
LEGACY_DRAWERS = "mempalace_drawers"
LEGACY_CLOSETS = "mempalace_closets"

# Per-wing collection name prefixes. Distinct first letters keep drawers vs
# closets visibly separate in the on-disk chroma listing.
_DRAWERS_PREFIX = "wd_"   # wing drawers
_CLOSETS_PREFIX = "wc_"   # wing closets

# Chroma collection-name rules (chromadb >=0.5): 3–512 chars, [a-zA-Z0-9._-],
# must start AND end with an alphanumeric. We sanitize the wing into that space
# and append a short content hash whenever sanitization could collide (so two
# distinct wings can NEVER map to the same collection).
_NAME_OK = re.compile(r"[A-Za-z0-9._-]")
_MAX_WING_PART = 480  # leave headroom under 512 for prefix + hash suffix


def _sanitize_wing(wing: str) -> str:
    """Map an arbitrary wing name onto the chroma-legal character set.

    Replaces every illegal char with '-', collapses runs, and trims to a length
    that leaves room for the prefix + hash suffix. Returns the sanitized core
    (WITHOUT prefix/hash) — callers add those. Case is preserved (chroma names
    are case-sensitive, and our wing ids — project__<hex>, user__<hex> — are
    lowercase hex anyway)."""
    out = "".join(c if _NAME_OK.match(c) else "-" for c in (wing or ""))
    out = re.sub(r"-{2,}", "-", out).strip("-._")
    return out[:_MAX_WING_PART]


def _needs_hash(wing: str, sanitized: str) -> bool:
    """A hash suffix is required whenever sanitization was lossy (so the mapping
    stays injective: distinct wings → distinct collections). Lossless = the
    sanitized core equals the original wing (already legal, untrimmed)."""
    return sanitized != (wing or "")


def _wing_core(wing: str) -> str:
    """The collision-free per-wing core: `<sanitized>` when the wing was already
    a legal chroma name, else `<sanitized>_<sha256[:12]>` so any two distinct
    wings (incl. ones that sanitize to the same string) get distinct cores.

    Examples:
      project__f201b24ff6a2 → project__f201b24ff6a2   (already legal)
      user__alex@me.com     → user__alex-me.com_3b1f… (the '@' was lossy → hash)
      ""                    → _<hash>                  (empty → hash-only, never bare)
    """
    s = _sanitize_wing(wing)
    if not s:
        # Degenerate wing (empty/all-illegal): hash-only core, prefixed so it's
        # still a legal name and never collides with a real wing.
        return "x" + hashlib.sha256((wing or "").encode("utf-8")).hexdigest()[:16]
    if _needs_hash(wing, s):
        return f"{s}_{hashlib.sha256((wing or '').encode('utf-8')).hexdigest()[:12]}"
    return s


def wing_to_collection(wing: str) -> tuple[str, str]:
    """Return (drawers_collection_name, closets_collection_name) for a wing.

    The single source of truth for per-wing collection naming. Deterministic,
    injective (distinct wings → distinct collections), and always chroma-legal:
    starts with a letter (the prefix), only legal chars, bounded length.
    """
    core = _wing_core(wing)
    return (_DRAWERS_PREFIX + core, _CLOSETS_PREFIX + core)


# ---------------------------------------------------------------------------
# Required venv patch
# ---------------------------------------------------------------------------
# Per-wing isolation is ALWAYS ON — it is how the palace works, not a toggle
# (decided 2026-06-03: a default-off flag would leave the corruption-prone shared
# index live by default, the opposite of the goal). There is therefore NO config
# flag and NO shared-collection fallback at runtime; the only dependency to verify
# is the vendored miner patch.

_PATCH_CHECK: dict = {"done": False, "ok": False}


def miner_patch_present() -> bool:
    """True iff the vendored mempalace `miner.mine` carries the per-wing
    `collection_name` BRAIN-PATCH. A `pip install --upgrade mempalace` silently
    wipes venv patches (they're gitignored — see [[project_mempalace_venv_patches]]).
    Per-wing writes via the miner DEPEND on this patch, and there is no
    shared-collection fallback, so its absence is a HARD error surfaced loudly at
    startup (`assert_miner_patch`) rather than a silent mis-route. Cached."""
    if _PATCH_CHECK["done"]:
        return _PATCH_CHECK["ok"]
    ok = False
    try:
        import inspect
        from mempalace import miner as _m
        from mempalace import closet_llm as _cl
        mp = inspect.signature(_m.mine).parameters
        cp = inspect.signature(_cl.regenerate_closets).parameters
        ok = ("collection_name" in mp and "closets_collection_name" in mp
              and "collection_name" in cp and "closets_collection_name" in cp)
    except Exception:
        ok = False
    _PATCH_CHECK.update(done=True, ok=ok)
    return ok


def assert_miner_patch() -> None:
    """Fail LOUD at startup if the per-wing miner patch is missing. Called once
    during mempalace init. Per-wing mining cannot work without it and there is no
    fallback, so we refuse to start in a half-isolated state — re-apply the venv
    patch (project_mempalace_venv_patches) and restart."""
    if not miner_patch_present():
        raise RuntimeError(
            "mempalace miner.mine is MISSING the per-wing collection_name patch "
            "(a pip upgrade likely wiped the gitignored venv patch). Per-wing "
            "collection isolation REQUIRES it and has no fallback. Re-apply the "
            "patch per project_mempalace_venv_patches, then restart."
        )


# ---------------------------------------------------------------------------
# The single accessor every read/write/recovery path uses
# ---------------------------------------------------------------------------

def collection_names_for(wing: str, *, kind: str = "drawers") -> str:
    """Resolve the per-wing collection name for (wing, kind). Always per-wing —
    `kind` is "drawers" or "closets". Name-only resolver (no chroma I/O), used by
    callers that drive the package's get_collection themselves + by
    `get_wing_collection`."""
    if kind not in ("drawers", "closets"):
        raise ValueError(f"kind must be 'drawers' or 'closets', got {kind!r}")
    drawers, closets = wing_to_collection(wing)
    return drawers if kind == "drawers" else closets


def get_wing_collection(palace_path: str, wing: str, *, create: bool,
                        kind: str = "drawers"):
    """Open (and optionally create) the per-wing Chroma collection for
    (wing, kind).

    The ONE accessor for per-wing reads/writes. Routes through the mempalace
    package's `get_collection`, which already accepts a `collection_name`. Returns
    the package's collection wrapper, or None if it can't be opened with
    create=False (e.g. a wing never written yet → caller treats as empty, not an
    error)."""
    from mempalace.palace import get_collection as _gc
    name = collection_names_for(wing, kind=kind)
    try:
        return _gc(palace_path, collection_name=name, create=create)
    except Exception:
        if create:
            raise  # a create failure is real — surface it
        return None  # missing collection on a read → empty, not fatal


def add_drawer_to_wing(palace_path: str, wing: str, room: str, content: str,
                       source_file: str = "", added_by: str = "brain") -> dict:
    """File one drawer into the wing's OWN collection — the per-wing analog of the
    package's `tool_add_drawer` (which only ever targets the single shared
    collection via `_config.collection_name`).

    Replicates the package's deterministic-ID + dedup + metadata contract EXACTLY
    so behavior matches: same `drawer_<wing>_<room>_<sha256[:24]>` id (so the same
    content dedups identically), same metadata keys. Returns the same result shape
    as tool_add_drawer: {success, drawer_id?, reason?, error?}.

    Callers should hold the brain `_palace_write_lock` around this (as with
    tool_add_drawer) to serialize against miner/project-sync write phases."""
    from mempalace.mcp_server import sanitize_name, sanitize_content
    from datetime import datetime
    try:
        wing = sanitize_name(wing, "wing")
        room = sanitize_name(room, "room")
        content = sanitize_content(content)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if not content:
        return {"success": False, "error": "empty content"}

    try:
        col = get_wing_collection(palace_path, wing, create=True, kind="drawers")
    except Exception as e:
        return {"success": False, "error": f"open wing collection: {e}"}
    if col is None:
        return {"success": False, "error": "wing collection unavailable"}

    drawer_id = (
        "drawer_" + wing + "_" + room + "_"
        + hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]
    )
    # Idempotency: same deterministic ID already present → no-op (matches package).
    try:
        existing = col.get(ids=[drawer_id], include=[])
        if existing.ids:
            return {"success": True, "reason": "already_exists", "drawer_id": drawer_id}
    except Exception:
        pass  # pre-check best-effort; upsert below is the source of truth
    try:
        col.upsert(
            ids=[drawer_id],
            documents=[content],
            metadatas=[{
                "wing": wing, "room": room,
                "source_file": source_file or "",
                "chunk_index": 0, "added_by": added_by,
                "filed_at": datetime.now().isoformat(),
            }],
        )
        return {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room}
    except Exception as e:
        return {"success": False, "error": str(e)}


def purge_wing_room(palace_path: str, wing: str, room: str,
                    source_prefix: str = "") -> int:
    """Delete every drawer in (wing, room) — optionally only those whose
    source_file startswith `source_prefix` — from the wing's OWN collection.
    Per-wing analog of brain's `_purge_drawers_by_room_and_source`; the bulk
    delete touches only this wing's index. Returns count deleted. Idempotent.

    Caller should hold `_palace_write_lock` (bulk delete = corruption trigger)."""
    col = get_wing_collection(palace_path, wing, create=False, kind="drawers")
    if col is None:
        return 0
    try:
        got = col.get(where={"room": room}, include=["metadatas"])
    except Exception:
        return 0
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    victims = []
    for i, did in enumerate(ids):
        if source_prefix:
            sf = ((metas[i] if i < len(metas) else {}) or {}).get("source_file", "") or ""
            if not sf.startswith(source_prefix):
                continue
        victims.append(did)
    if victims:
        col.delete(ids=victims)
    return len(victims)


def list_wing_collections(palace_path: str) -> list[str]:
    """List the per-wing collection names that physically exist in the palace
    (both drawers + closets). Used by admin/recovery sweeps and migration
    verification. Returns [] on any error (best-effort)."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=palace_path)
        names = [c.name for c in client.list_collections()]
    except Exception:
        return []
    return [n for n in names
            if n.startswith(_DRAWERS_PREFIX) or n.startswith(_CLOSETS_PREFIX)]
