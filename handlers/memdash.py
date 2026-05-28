"""handlers/memdash.py — MemPalace Dashboard integration (admin-gated).

Serves the vendored third-party MemPalace Dashboard frontend (web/memdash/,
github.com/epinethrone/mempalace-frontend, MIT) and reimplements its `/api/*`
surface as Brain handlers backed by Brain's IN-PROCESS MemPalace — pointed at
Brain's palace (`~/.mempalace/brain`), NOT the dashboard default
(`~/.mempalace/palace`). No separate port / no dashboard login: routing +
admin RBAC live in server.py (`/memdash/*` is admin-gated like `/v1/...`).

Design (mirrors the dashboard's own mempalace_dashboard/server.py so the
frontend sees byte-compatible response shapes):
  - READ side (`/palace`, `/search`, `/system`, `/export`) reads the palace +
    KG sqlite DBs directly (same SQL the dashboard uses), against the BRAIN
    palace DBs.
  - LAB + WRITE side calls `mempalace.mcp_server.tool_*` directly in-process
    (the dashboard shells out to a subprocess; we don't need to — Brain already
    imports these tools, see server.py:114). The env var MEMPALACE_PALACE_PATH
    is bound to the brain palace at import (same setdefault the chat-sync daemon
    does in server_daemons.py) so the tools resolve the right palace.
  - drafts/versions/credentials are dashboard-only file features. Phase 1:
    drafts are STUBBED (empty / disabled); the version log is file-backed under
    agents/main/memdash/ so update/delete/restore undo still works; the
    credentials/login/logout/session endpoints are replaced by Brain auth
    (`/api/session` returns synthetic-authed).

Concurrent-write caveat: writes hit the live brain palace that Brain's daemons
mine — accepted risk (see memory project_chroma_bulk_delete_corruption). These
in-process calls run in the SAME process as the daemons, so they are no worse
than Brain's existing in-process writes.

Re-apply-on-upgrade: when the vendored frontend is updated, re-apply the
BRAIN-PATCH edits in web/memdash/app.js (API prefix + Brain auth token). If the
dashboard's server.py response shapes change, mirror them here.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime
from urllib.parse import parse_qs, urlparse

# `engine` is brain (server.py does `import brain as engine`). The mempalace
# config helpers we use (_load_mempalace_config / _ensure_mempalace_importable)
# are re-exported on brain. brain is already imported by the time server.py
# imports this handler, so this is a cheap sys.modules lookup, not a new load.
import brain as engine

# Validation regexes mirrored from the dashboard server.py.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TUNNEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")

_SERVER_STARTED_AT = datetime.now()


def _brain_palace_path() -> str:
    """Brain's palace dir (the one WITH the span-patched KG), from config.json."""
    cfg = engine._load_mempalace_config()
    return cfg.get("palace_path", "") or ""


def _bind_palace_env() -> None:
    """Point mempalace.mcp_server's module-level _config at the brain palace.

    Same `setdefault` the chat-sync daemon does (server_daemons.py) — idempotent;
    a no-op if the daemon already set it. Without this the tools default to
    ~/.mempalace/palace (the #1 correctness risk in the plan)."""
    pp = _brain_palace_path()
    if pp:
        os.environ.setdefault("MEMPALACE_PALACE_PATH", pp)


def _palace_db() -> str:
    return os.path.join(_brain_palace_path(), "chroma.sqlite3")


def _kg_db() -> str:
    return os.path.join(_brain_palace_path(), "knowledge_graph.sqlite3")


def _versions_log() -> str:
    """Version-undo log, file-backed under agents/main/ (NOT ~/.mempalace)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "agents", "main", "memdash")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "versions.jsonl")


# ---------------------------------------------------------------------------
# Direct-SQLite reads (mirror the dashboard's read_drawers/read_triples)
# ---------------------------------------------------------------------------

def _content_etag(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def _extract_title(content: str, fallback: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped[:80]
    return fallback


# Wings whose full drawer bodies are NOT shipped in the default /api/palace
# payload — they are too large to render in one DOM pass and would freeze the
# browser. `brain_code` alone is ~11.9k drawers of indexed source. They still
# appear in the overview (stats + wing buttons, from the cheap count query) and
# their drawers load on demand when the wing is explicitly requested
# (/api/palace?wing=brain_code). Tune here as the palace grows.
_HEAVY_WINGS = {"brain_code"}


def _drawer_wing_room_counts() -> "Counter":
    """(wing, room) -> count over ALL drawers — cheap GROUP BY, no content.

    Powers the overview/wing buttons even for wings whose bodies we omit, so
    the stats stay accurate regardless of which bodies are shipped."""
    db = _palace_db()
    counts: Counter = Counter()
    if not os.path.isfile(db):
        return counts
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            select w.string_value as wing, r.string_value as room, count(*) as n
            from embeddings e
            join embedding_metadata w on w.id = e.id and w.key = 'wing'
            join embedding_metadata r on r.id = e.id and r.key = 'room'
            where e.embedding_id like 'drawer_%'
            group by w.string_value, r.string_value
            """
        ).fetchall()
        con.close()
        for row in rows:
            counts[(row["wing"] or "unknown", row["room"] or "unknown")] = row["n"]
    except sqlite3.OperationalError:
        return Counter()
    return counts


def _read_drawers(wings: "set[str] | None" = None) -> list[dict]:
    """Full drawer records (with content). If `wings` is given, only drawers in
    those wings are returned (a metadata join keeps it cheap) — used to load a
    heavy wing's bodies on demand without shipping the whole palace."""
    db = _palace_db()
    if not os.path.isfile(db):
        return []
    wing_filter = ""
    if wings is not None:
        if not wings:
            return []
        placeholders = ",".join("?" for _ in wings)
        wing_filter = f"""
            and e.id in (
                select wm.id from embedding_metadata wm
                where wm.key = 'wing' and wm.string_value in ({placeholders})
            )"""
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            f"""
            select e.id, e.embedding_id, em.key,
                   coalesce(em.string_value, em.int_value, em.float_value, em.bool_value) as value
            from embeddings e
            join embedding_metadata em on em.id = e.id
            where e.embedding_id like 'drawer_%'
            {wing_filter}
            order by e.id
            """,
            tuple(wings) if wings is not None else (),
        )]
        con.close()
    except sqlite3.OperationalError:
        return []

    by_id: dict[int, dict] = {}
    for row in rows:
        item = by_id.setdefault(row["id"], {
            "id": row["id"], "drawer_id": row["embedding_id"],
            "wing": "unknown", "room": "unknown", "title": "Untitled",
            "content": "", "source_file": "", "filed_at": "", "added_by": "",
            "metadata": {},
        })
        key = row["key"]
        value = row["value"]
        if key == "chroma:document":
            item["content"] = value or ""
            item["title"] = _extract_title(item["content"], item["drawer_id"])
        elif key in item:
            item[key] = value or ""
        else:
            item["metadata"][key] = value

    for item in by_id.values():
        item["etag"] = _content_etag(item.get("content", ""))
    return list(by_id.values())


def _read_triples() -> list[dict]:
    db = _kg_db()
    if not os.path.isfile(db):
        return []
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            """
            select id, subject, predicate, object, valid_from, valid_to,
                   confidence, source_drawer_id, extracted_at
            from triples
            order by extracted_at desc, subject, predicate
            """
        )]
        con.close()
        return rows
    except sqlite3.OperationalError:
        return []


def _build_palace_payload(only_wing: str | None = None) -> dict:
    """Overview + drawer bodies.

    Stats and the wing/room tree are ALWAYS computed from the cheap full
    GROUP-BY count (every wing, accurate), so the overview is complete. Drawer
    BODIES are shipped only for non-heavy wings — except when `only_wing` names
    a wing, in which case exactly that wing's bodies are returned (on-demand
    load of a heavy wing like brain_code). This keeps the default payload small
    enough for the browser to render without freezing.

    The response carries `heavy_wings` (omitted-by-default wing names) and
    `loaded_wings` (whose bodies ARE in `drawers`) so the frontend knows which
    wings it must fetch on demand."""
    counts = _drawer_wing_room_counts()
    triples = _read_triples()

    # Build the full wing/room tree from counts (every wing present).
    wings: dict[str, dict] = {}
    for (wing, room), n in counts.items():
        wings.setdefault(wing, {"name": wing, "count": 0, "rooms": {}})
        wings[wing]["count"] += n
        wings[wing]["rooms"][room] = {"name": room, "count": n}
    for wing in wings.values():
        wing["rooms"] = sorted(wing["rooms"].values(), key=lambda r: r["name"])

    total_drawers = sum(counts.values())

    # Decide which wings' bodies to ship.
    all_wings = set(wings.keys())
    if only_wing is not None:
        load_wings = {only_wing} & all_wings
    else:
        load_wings = all_wings - _HEAVY_WINGS
    drawers = _read_drawers(wings=load_wings) if load_wings else []

    active_facts = sum(1 for t in triples if not t.get("valid_to"))
    return {
        "stats": {
            "drawers": total_drawers, "wings": len(wings),
            "rooms": len(counts), "facts": len(triples),
            "activeFacts": active_facts,
        },
        "wings": sorted(wings.values(), key=lambda w: w["name"]),
        "drawers": drawers,
        "triples": triples,
        "heavy_wings": sorted(w for w in _HEAVY_WINGS if w in all_wings),
        "loaded_wings": sorted(load_wings),
    }


# Cap search result cards so a broad query (e.g. "e") can't return ~13k full
# drawers and re-create the render freeze. Matches beyond this are truncated
# with a flag the frontend can surface.
_SEARCH_CAP = 500


def _search_payload(query: str) -> dict:
    q = (query or "").strip().lower()
    triples = _read_triples()
    if not q:
        # Empty query = the overview list; mirror the default palace bodies
        # (non-heavy wings only) rather than every drawer.
        drawers = _read_drawers(wings=set(_drawer_wing_room_counts()) - _HEAVY_WINGS) or []
        return {"drawers": drawers, "triples": triples}

    def hit(*values) -> bool:
        return any(q in str(v or "").lower() for v in values)

    matched: list[dict] = []
    truncated = False
    for d in _read_drawers():
        if hit(d["title"], d["content"], d["wing"], d["room"], d["source_file"], d["drawer_id"]):
            if len(matched) >= _SEARCH_CAP:
                truncated = True
                break
            matched.append(d)
    return {
        "drawers": matched,
        "triples": [t for t in triples if hit(
            t["subject"], t["predicate"], t["object"], t["source_drawer_id"])],
        "truncated": truncated,
        "cap": _SEARCH_CAP,
    }


def _file_size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _system_info() -> dict:
    uname = platform.uname()
    palace_bytes = _file_size(_palace_db())
    kg_bytes = _file_size(_kg_db())
    uptime = datetime.now() - _SERVER_STARTED_AT
    return {
        "repo_url": "https://github.com/epinethrone/mempalace-frontend",
        "host": {"name": uname.node, "os": uname.system,
                 "release": uname.release, "arch": uname.machine},
        "python": platform.python_version(),
        "port": 8420,
        "palace_home": _brain_palace_path(),
        "db_bytes": {"palace": palace_bytes, "knowledge_graph": kg_bytes,
                     "total": palace_bytes + kg_bytes},
        "uptime_seconds": int(uptime.total_seconds()),
        "started_at": _SERVER_STARTED_AT.isoformat(timespec="seconds"),
    }


def _build_export() -> dict:
    drawers = _read_drawers()
    triples = _read_triples()
    return {
        "format": "mempalace-export", "format_version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "host": platform.uname().node, "palace_home": _brain_palace_path(),
        "counts": {"drawers": len(drawers), "facts": len(triples)},
        "drawers": drawers, "facts": triples,
    }


# ---------------------------------------------------------------------------
# In-process MCP-tool dispatch (replaces the dashboard's subprocess mcp_call)
# ---------------------------------------------------------------------------

def _mcp(tool_name: str, **kwargs):
    """Call mempalace.mcp_server.tool_* in-process, against the brain palace.

    Drops None kwargs (matches the dashboard's mcp_call). Normalises list/scalar
    returns to a dict the way the dashboard did ({'items': [...]} / {'value': x})
    so the frontend's renderers see the same shape."""
    _bind_palace_env()
    ok, err = engine._ensure_mempalace_importable()
    if not ok:
        raise RuntimeError(err)
    import mempalace.mcp_server as mcp
    fn = getattr(mcp, tool_name, None)
    if fn is None:
        raise RuntimeError(f"MemPalace tool not available: {tool_name}")
    payload = {k: v for k, v in kwargs.items() if v is not None}
    result = fn(**payload)
    if isinstance(result, list):
        return {"items": result}
    if not isinstance(result, dict):
        return {"value": result}
    return result


# ---------------------------------------------------------------------------
# Version-undo log (file-backed, agents/main/memdash/versions.jsonl)
# ---------------------------------------------------------------------------

def _log_version(action: str, drawer: dict, *, note: str = "") -> None:
    record = {
        "action": action, "logged_at": datetime.now().isoformat(timespec="seconds"),
        "drawer_id": drawer.get("drawer_id"), "wing": drawer.get("wing"),
        "room": drawer.get("room"), "title": drawer.get("title"),
        "content": drawer.get("content", ""), "source_file": drawer.get("source_file", ""),
        "added_by": drawer.get("added_by", ""), "filed_at": drawer.get("filed_at", ""),
        "note": note,
    }
    with open(_versions_log(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_versions(limit: int = 200) -> list[dict]:
    path = _versions_log()
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Write helpers (validate then call the in-process tool)
# ---------------------------------------------------------------------------

def _validate_memory(payload: dict) -> tuple[str, str, str, str]:
    wing = str(payload.get("wing", "")).strip()
    room = str(payload.get("room", "")).strip()
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not _NAME_RE.match(wing):
        raise ValueError("Wing must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens.")
    if not _NAME_RE.match(room):
        raise ValueError("Room must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens.")
    if len(title) > 120:
        raise ValueError("Title must be 120 characters or fewer.")
    if len(content) < 10:
        raise ValueError("Content must be at least 10 characters.")
    if len(content) > 12000:
        raise ValueError("Content must be 12,000 characters or fewer.")
    return wing, room, title, content


def _file_memory(payload: dict) -> dict:
    wing, room, title, content = _validate_memory(payload)
    heading = title or _extract_title(content, f"{wing}/{room} memory")
    body = content if content.lstrip().startswith("#") else f"# {heading}\n\n{content}"
    result = _mcp("tool_add_drawer", wing=wing, room=room, content=body,
                  source_file=f"mempalace-dashboard:{datetime.now().isoformat(timespec='seconds')}",
                  added_by="mempalace-dashboard")
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "MemPalace rejected the write.")
    return {"success": True, "result": result, "wing": wing, "room": room, "title": heading}


def _update_memory(payload: dict) -> dict:
    drawer_id = str(payload.get("drawer_id", "")).strip()
    if not drawer_id.startswith("drawer_"):
        raise ValueError("Invalid drawer id.")
    current = next((d for d in _read_drawers() if d["drawer_id"] == drawer_id), None)
    if not current:
        raise ValueError("Drawer not found.")
    expected_etag = str(payload.get("etag", "")).strip()
    if expected_etag and expected_etag != current["etag"]:
        raise ValueError("Memory changed since you opened it. Reload to see the latest version.")
    new_content = payload.get("content")
    new_wing = payload.get("wing")
    new_room = payload.get("room")
    if new_content is not None:
        new_content = str(new_content)
        if len(new_content.strip()) < 10:
            raise ValueError("Content must be at least 10 characters.")
        if len(new_content) > 12000:
            raise ValueError("Content must be 12,000 characters or fewer.")
    if new_wing is not None:
        new_wing = str(new_wing).strip()
        if not _NAME_RE.match(new_wing):
            raise ValueError("Wing must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens.")
    if new_room is not None:
        new_room = str(new_room).strip()
        if not _NAME_RE.match(new_room):
            raise ValueError("Room must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens.")
    if new_content is None and new_wing is None and new_room is None:
        raise ValueError("Nothing to update — provide content, wing, or room.")
    _log_version("update-before", current, note="set " + ",".join(
        k for k, v in {"content": new_content, "wing": new_wing, "room": new_room}.items() if v is not None))
    result = _mcp("tool_update_drawer", drawer_id=drawer_id,
                  content=new_content, wing=new_wing, room=new_room)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or f"MemPalace rejected update for {drawer_id}.")
    return {"success": True, "result": result, "drawer_id": drawer_id}


def _drawers_for_delete(payload: dict) -> tuple[str, list[dict]]:
    scope = str(payload.get("scope", "")).strip()
    drawers = _read_drawers()
    if scope == "drawer":
        drawer_id = str(payload.get("drawer_id", "")).strip()
        if not drawer_id.startswith("drawer_"):
            raise ValueError("Invalid drawer id.")
        return "memory", [d for d in drawers if d["drawer_id"] == drawer_id]
    if scope == "room":
        wing = str(payload.get("wing", "")).strip()
        room = str(payload.get("room", "")).strip()
        if not _NAME_RE.match(wing) or not _NAME_RE.match(room):
            raise ValueError("Invalid wing or room.")
        return f"room {wing}/{room}", [d for d in drawers if d["wing"] == wing and d["room"] == room]
    if scope == "wing":
        wing = str(payload.get("wing", "")).strip()
        if not _NAME_RE.match(wing):
            raise ValueError("Invalid wing.")
        return f"wing {wing}", [d for d in drawers if d["wing"] == wing]
    raise ValueError("Delete scope must be drawer, room, or wing.")


def _delete_memories(payload: dict) -> dict:
    if str(payload.get("confirm", "")).strip() != "DELETE":
        raise ValueError("Delete requires confirmation value DELETE.")
    label, drawers = _drawers_for_delete(payload)
    if not drawers:
        raise ValueError(f"No drawers found for {label}.")
    results = []
    for d in drawers:
        _log_version("delete", d, note=f"scope={label}")
        result = _mcp("tool_delete_drawer", drawer_id=d["drawer_id"])
        if not result.get("success"):
            raise RuntimeError(result.get("error") or f"MemPalace rejected delete for {d['drawer_id']}.")
        results.append({"drawer_id": d["drawer_id"], "result": result})
    return {"success": True, "deleted": len(results), "target": label, "results": results}


def _rename_scope(payload: dict) -> dict:
    scope = str(payload.get("scope", "")).strip()
    new_name = str(payload.get("new_name", "")).strip()
    if not _NAME_RE.match(new_name):
        raise ValueError("New name must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens.")
    drawers = _read_drawers()
    if scope == "wing":
        wing = str(payload.get("wing", "")).strip()
        if not _NAME_RE.match(wing):
            raise ValueError("Invalid wing.")
        if wing == new_name:
            return {"success": True, "renamed": 0, "target": f"wing {wing}", "noop": True}
        matches = [d for d in drawers if d["wing"] == wing]
        if not matches:
            raise ValueError(f"No drawers found in wing {wing}.")
        results = []
        for d in matches:
            _log_version("rename-before", d, note=f"wing {wing} -> {new_name}")
            results.append({"drawer_id": d["drawer_id"],
                            "result": _mcp("tool_update_drawer", drawer_id=d["drawer_id"], wing=new_name)})
        return {"success": True, "renamed": len(results), "target": f"wing {wing} -> {new_name}", "scope": "wing"}
    if scope == "room":
        wing = str(payload.get("wing", "")).strip()
        room = str(payload.get("room", "")).strip()
        if not _NAME_RE.match(wing) or not _NAME_RE.match(room):
            raise ValueError("Invalid wing or room.")
        if room == new_name:
            return {"success": True, "renamed": 0, "target": f"room {wing}/{room}", "noop": True}
        matches = [d for d in drawers if d["wing"] == wing and d["room"] == room]
        if not matches:
            raise ValueError(f"No drawers found in room {wing}/{room}.")
        results = []
        for d in matches:
            _log_version("rename-before", d, note=f"room {wing}/{room} -> {wing}/{new_name}")
            results.append({"drawer_id": d["drawer_id"],
                            "result": _mcp("tool_update_drawer", drawer_id=d["drawer_id"], room=new_name)})
        return {"success": True, "renamed": len(results), "target": f"room {wing}/{room} -> {wing}/{new_name}", "scope": "room"}
    raise ValueError("Rename scope must be wing or room.")


def _add_fact(payload: dict) -> dict:
    def field(name, value, max_len=200):
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{name} is required.")
        if len(value) > max_len:
            raise ValueError(f"{name} must be {max_len} characters or fewer.")
        return value
    subject = field("Subject", payload.get("subject", ""))
    predicate = field("Predicate", payload.get("predicate", ""))
    obj = field("Object", payload.get("object", ""))
    valid_from = str(payload.get("valid_from", "")).strip() or None
    source_drawer_id = str(payload.get("source_drawer_id", "")).strip()
    if source_drawer_id and not source_drawer_id.startswith("drawer_"):
        raise ValueError("source_drawer_id must start with 'drawer_'.")
    result = _mcp("tool_kg_add", subject=subject, predicate=predicate, object=obj,
                  valid_from=valid_from, source_drawer_id=source_drawer_id or None)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "MemPalace rejected the fact.")
    return {"success": True, "result": result, "subject": subject, "predicate": predicate, "object": obj}


def _invalidate_fact(payload: dict) -> dict:
    def field(name, value):
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{name} is required.")
        return value
    subject = field("Subject", payload.get("subject", ""))
    predicate = field("Predicate", payload.get("predicate", ""))
    obj = field("Object", payload.get("object", ""))
    ended = str(payload.get("ended", "")).strip() or None
    result = _mcp("tool_kg_invalidate", subject=subject, predicate=predicate, object=obj, ended=ended)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "MemPalace rejected the invalidation.")
    return {"success": True, "result": result}


def _restore_version(payload: dict) -> dict:
    drawer_id = str(payload.get("drawer_id", "")).strip()
    logged_at = str(payload.get("logged_at", "")).strip()
    if not drawer_id:
        raise ValueError("drawer_id is required to restore.")
    records = _read_versions(limit=2000)
    record = next((r for r in records if r.get("drawer_id") == drawer_id and r.get("logged_at") == logged_at), None)
    if not record:
        record = next((r for r in records if r.get("drawer_id") == drawer_id and r.get("action") in ("delete", "update-before")), None)
    if not record:
        raise ValueError("No version found for that drawer.")
    wing = str(record.get("wing") or "").strip()
    room = str(record.get("room") or "").strip()
    content = record.get("content") or ""
    if not _NAME_RE.match(wing) or not _NAME_RE.match(room):
        raise ValueError("Stored wing/room are invalid; cannot restore automatically.")
    result = _mcp("tool_add_drawer", wing=wing, room=room, content=content,
                  source_file=f"mempalace-dashboard:restore:{drawer_id}:{datetime.now().isoformat(timespec='seconds')}",
                  added_by="mempalace-dashboard")
    _log_version("restore", record, note=f"restored from {logged_at}")
    return {"success": True, "result": result, "wing": wing, "room": room}


def _delete_version(payload: dict) -> dict:
    drawer_id = str(payload.get("drawer_id", "")).strip()
    logged_at = str(payload.get("logged_at", "")).strip()
    if not drawer_id and not logged_at:
        raise ValueError("drawer_id and logged_at are required.")
    path = _versions_log()
    if not os.path.isfile(path):
        return {"success": True, "removed": 0}
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if rec.get("drawer_id") == drawer_id and rec.get("logged_at") == logged_at:
            removed += 1
            continue
        kept.append(line)
    if kept:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")
    else:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"success": True, "removed": removed}


def _clear_versions(payload: dict) -> dict:
    if str(payload.get("confirm", "")).strip() != "CLEAR":
        raise ValueError("Clear requires confirmation value CLEAR.")
    path = _versions_log()
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return {"success": True}


# ---------------------------------------------------------------------------
# Lab endpoint wrappers (query-string parsing mirrors the dashboard)
# ---------------------------------------------------------------------------

def _qs1(query: dict, key: str, default=None):
    values = query.get(key) or []
    if not values:
        return default
    v = values[0]
    return v if v not in (None, "") else default


def _import_palace(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Missing export payload — upload the JSON file produced by Export data.")
    if data.get("format") != "mempalace-export":
        raise ValueError("File doesn't look like a MemPalace export (missing format marker).")
    incoming_drawers = data.get("drawers") or []
    incoming_facts = data.get("facts") or data.get("triples") or []
    if not isinstance(incoming_drawers, list) or not isinstance(incoming_facts, list):
        raise ValueError("Export payload is malformed: drawers/facts must be lists.")
    existing = _read_drawers()
    seen = {(d.get("wing", ""), d.get("room", ""), (d.get("content") or "").strip()) for d in existing}
    added_d = skipped_d = 0
    drawer_errors: list[dict] = []
    ts = datetime.now().isoformat(timespec="seconds")
    for entry in incoming_drawers:
        if not isinstance(entry, dict):
            skipped_d += 1
            continue
        wing = str(entry.get("wing", "")).strip()
        room = str(entry.get("room", "")).strip()
        content = str(entry.get("content", "") or "").strip()
        if not wing or not room or len(content) < 10 or not _NAME_RE.match(wing) or not _NAME_RE.match(room):
            skipped_d += 1
            continue
        key = (wing, room, content)
        if key in seen:
            skipped_d += 1
            continue
        try:
            _mcp("tool_add_drawer", wing=wing, room=room, content=content,
                 source_file=f"mempalace-import:{ts}", added_by="mempalace-dashboard")
            seen.add(key)
            added_d += 1
        except (RuntimeError, ValueError) as exc:
            drawer_errors.append({"drawer_id": entry.get("drawer_id"), "error": str(exc)})
    existing_triples = _read_triples()
    active = {(t.get("subject"), t.get("predicate"), t.get("object")) for t in existing_triples if not t.get("valid_to")}
    added_f = skipped_f = 0
    fact_errors: list[dict] = []
    for entry in incoming_facts:
        if not isinstance(entry, dict):
            skipped_f += 1
            continue
        subject = str(entry.get("subject", "")).strip()
        predicate = str(entry.get("predicate", "")).strip()
        obj = str(entry.get("object", "")).strip()
        if not subject or not predicate or not obj:
            skipped_f += 1
            continue
        key = (subject, predicate, obj)
        if key in active:
            skipped_f += 1
            continue
        try:
            _mcp("tool_kg_add", subject=subject, predicate=predicate, object=obj,
                 valid_from=str(entry.get("valid_from", "")).strip() or None)
            active.add(key)
            added_f += 1
        except (RuntimeError, ValueError) as exc:
            fact_errors.append({"fact": f"{subject} {predicate} {obj}", "error": str(exc)})
    return {
        "success": True,
        "added": {"drawers": added_d, "facts": added_f},
        "skipped": {"drawers": skipped_d, "facts": skipped_f},
        "errors": {"drawers": drawer_errors, "facts": fact_errors},
    }


class MemDashHandlerMixin:
    """HTTP mixin for /memdash/api/* — dispatched from server.py (admin-gated).

    The two entry points (`_handle_memdash_get` / `_handle_memdash_post`) are
    called by server.py's do_GET/do_POST AFTER the admin RBAC gate; they parse
    the path themselves (everything under /memdash/api/)."""

    def _memdash_respond(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _memdash_read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 5_000_000:
            raise ValueError("Request body is too large.")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def _handle_memdash_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            # Synthetic session — Brain's admin gate already passed.
            if path == "/memdash/api/session":
                self._memdash_respond({"authenticated": True, "username": "brain-admin",
                                       "credentials_required": False})
                return
            if path == "/memdash/api/palace":
                self._memdash_respond(_build_palace_payload(only_wing=_qs1(query, "wing")))
                return
            if path == "/memdash/api/search":
                self._memdash_respond(_search_payload(_qs1(query, "q", "")))
                return
            if path == "/memdash/api/system":
                self._memdash_respond(_system_info())
                return
            if path == "/memdash/api/export":
                self._memdash_respond(_build_export())
                return
            if path == "/memdash/api/settings":
                # Credentials are Brain's, not the dashboard's.
                self._memdash_respond({"credentials_configured": True, "username": "brain-admin"})
                return
            if path == "/memdash/api/drafts":
                # Drafts inbox is a dashboard-only file feature — stubbed in Phase 1.
                if "id" in query:
                    self._memdash_respond({"success": False, "error": "Drafts are disabled in the Brain-integrated dashboard."}, status=400)
                    return
                self._memdash_respond({"drafts": []})
                return
            if path == "/memdash/api/versions":
                self._memdash_respond({"versions": _read_versions()})
                return
            # ---- Lab read endpoints (in-process tool calls) ----
            if path == "/memdash/api/kg/query":
                entity = (_qs1(query, "entity") or "").strip()
                if not entity:
                    raise ValueError("entity is required.")
                self._memdash_respond(_mcp("tool_kg_query", entity=entity,
                                           direction=_qs1(query, "direction"), as_of=_qs1(query, "as_of")))
                return
            if path == "/memdash/api/kg/stats":
                self._memdash_respond(_mcp("tool_kg_stats"))
                return
            if path == "/memdash/api/kg/timeline":
                self._memdash_respond(_mcp("tool_kg_timeline", entity=_qs1(query, "entity")))
                return
            if path == "/memdash/api/graph/stats":
                self._memdash_respond(_mcp("tool_graph_stats"))
                return
            if path == "/memdash/api/taxonomy":
                self._memdash_respond(_mcp("tool_get_taxonomy"))
                return
            if path == "/memdash/api/checkpoint":
                self._memdash_respond(_mcp("tool_memories_filed_away"))
                return
            if path == "/memdash/api/aaak-spec":
                self._memdash_respond(_mcp("tool_get_aaak_spec"))
                return
            if path == "/memdash/api/diary":
                agent = (_qs1(query, "agent_name") or "").strip()
                if not agent:
                    raise ValueError("agent_name is required.")
                try:
                    last_n = max(1, min(int(_qs1(query, "last_n", "10") or "10"), 200))
                except (TypeError, ValueError):
                    last_n = 10
                self._memdash_respond(_mcp("tool_diary_read", agent_name=agent, last_n=last_n, wing=_qs1(query, "wing")))
                return
            if path == "/memdash/api/tunnels":
                self._memdash_respond(_mcp("tool_list_tunnels", wing=_qs1(query, "wing")))
                return
            if path == "/memdash/api/tunnels/find":
                self._memdash_respond(_mcp("tool_find_tunnels", wing_a=_qs1(query, "wing_a"), wing_b=_qs1(query, "wing_b")))
                return
            if path == "/memdash/api/tunnels/follow":
                wing = (_qs1(query, "wing") or "").strip()
                room = (_qs1(query, "room") or "").strip()
                if not wing or not room:
                    raise ValueError("wing and room are required.")
                self._memdash_respond(_mcp("tool_follow_tunnels", wing=wing, room=room))
                return
            if path == "/memdash/api/traverse":
                start_room = (_qs1(query, "start_room") or "").strip()
                if not start_room:
                    raise ValueError("start_room is required.")
                try:
                    max_hops = max(1, min(int(_qs1(query, "max_hops", "2") or "2"), 5))
                except (TypeError, ValueError):
                    max_hops = 2
                self._memdash_respond(_mcp("tool_traverse_graph", start_room=start_room, max_hops=max_hops))
                return
            if path == "/memdash/api/hooks":
                self._memdash_respond(_mcp("tool_hook_settings"))
                return
        except (ValueError, RuntimeError) as exc:
            self._memdash_respond({"success": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:  # tool blew up — surface, don't 500-blank
            self._memdash_respond({"success": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)
            return
        self._memdash_respond({"success": False, "error": "Not found"}, status=404)

    def _handle_memdash_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._memdash_read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._memdash_respond({"success": False, "error": str(exc) or "Invalid JSON."}, status=400)
            return
        try:
            # Auth endpoints are replaced by Brain auth.
            if path in ("/memdash/api/login", "/memdash/api/logout", "/memdash/api/settings/credentials"):
                self._memdash_respond({"success": True})
                return
            # Drafts are stubbed/disabled.
            if path in ("/memdash/api/drafts", "/memdash/api/drafts/update",
                        "/memdash/api/drafts/delete", "/memdash/api/drafts/commit"):
                self._memdash_respond({"success": False, "error": "Drafts are disabled in the Brain-integrated dashboard."}, status=400)
                return
            if path == "/memdash/api/memories":
                self._memdash_respond(_file_memory(payload), status=201)
                return
            if path == "/memdash/api/memories/update":
                self._memdash_respond(_update_memory(payload))
                return
            if path == "/memdash/api/delete":
                self._memdash_respond(_delete_memories(payload))
                return
            if path == "/memdash/api/rename":
                self._memdash_respond(_rename_scope(payload))
                return
            if path == "/memdash/api/import":
                self._memdash_respond(_import_palace(payload))
                return
            if path == "/memdash/api/facts":
                self._memdash_respond(_add_fact(payload), status=201)
                return
            if path == "/memdash/api/facts/invalidate":
                self._memdash_respond(_invalidate_fact(payload))
                return
            if path == "/memdash/api/versions/restore":
                self._memdash_respond(_restore_version(payload))
                return
            if path == "/memdash/api/versions/delete":
                self._memdash_respond(_delete_version(payload))
                return
            if path == "/memdash/api/versions/clear":
                self._memdash_respond(_clear_versions(payload))
                return
            # ---- Lab write endpoints ----
            if path == "/memdash/api/diary":
                agent = str(payload.get("agent_name", "")).strip()
                entry = str(payload.get("entry", "")).strip()
                if not agent:
                    raise ValueError("agent_name is required.")
                if not entry:
                    raise ValueError("entry is required.")
                self._memdash_respond(_mcp("tool_diary_write", agent_name=agent, entry=entry,
                                           topic=str(payload.get("topic", "")).strip() or "general",
                                           wing=str(payload.get("wing", "")).strip() or None), status=201)
                return
            if path == "/memdash/api/tunnels":
                required = ("source_wing", "source_room", "target_wing", "target_room")
                args = {}
                for key in required:
                    value = str(payload.get(key, "")).strip()
                    if not value:
                        raise ValueError(f"{key} is required.")
                    if not _NAME_RE.match(value):
                        raise ValueError(f"{key} contains invalid characters.")
                    args[key] = value
                self._memdash_respond(_mcp("tool_create_tunnel",
                                           label=str(payload.get("label", "")).strip() or None,
                                           source_drawer_id=str(payload.get("source_drawer_id", "")).strip() or None,
                                           target_drawer_id=str(payload.get("target_drawer_id", "")).strip() or None,
                                           **args), status=201)
                return
            if path == "/memdash/api/tunnels/delete":
                tunnel_id = str(payload.get("tunnel_id", "")).strip()
                if not tunnel_id:
                    raise ValueError("tunnel_id is required.")
                if not _TUNNEL_ID_RE.match(tunnel_id):
                    raise ValueError("tunnel_id contains invalid characters.")
                self._memdash_respond(_mcp("tool_delete_tunnel", tunnel_id=tunnel_id))
                return
            if path == "/memdash/api/check-duplicate":
                content = str(payload.get("content", "")).strip()
                if not content:
                    raise ValueError("content is required.")
                try:
                    threshold = max(0.0, min(float(payload.get("threshold", 0.9)), 1.0))
                except (TypeError, ValueError):
                    threshold = 0.9
                self._memdash_respond(_mcp("tool_check_duplicate", content=content, threshold=threshold))
                return
            if path == "/memdash/api/hooks":
                kwargs = {}
                if "silent_save" in payload:
                    kwargs["silent_save"] = bool(payload.get("silent_save"))
                if "desktop_toast" in payload:
                    kwargs["desktop_toast"] = bool(payload.get("desktop_toast"))
                if not kwargs:
                    raise ValueError("Provide silent_save and/or desktop_toast.")
                self._memdash_respond(_mcp("tool_hook_settings", **kwargs))
                return
            if path == "/memdash/api/sync":
                self._memdash_respond(_mcp("tool_sync", apply=bool(payload.get("apply", False)),
                                           wing=str(payload.get("wing", "")).strip() or None,
                                           project_dir=str(payload.get("project_dir", "")).strip() or None))
                return
            if path == "/memdash/api/reconnect":
                self._memdash_respond(_mcp("tool_reconnect"))
                return
        except (ValueError, RuntimeError) as exc:
            self._memdash_respond({"success": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            self._memdash_respond({"success": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)
            return
        self._memdash_respond({"success": False, "error": "Not found"}, status=404)
