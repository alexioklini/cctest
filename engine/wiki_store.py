"""LLM Wiki store — user-visible, editable markdown wiki with user/team/global
scoping (project-aware), mirrored into MemPalace so every page is searchable.

This replaces the obsolete `MemoryStore` (flat per-scope .md files with degraded
file-scan search). A wiki page is a row in `wiki_pages` (see server_lib/db.py),
pages form a tree via `parent_id`, and each save is mirrored into the matching
MemPalace wing as a single drawer (`source_file=f"wiki/{page_id}"`). Edits/deletes
purge that page's old drawer by exact source match before re-adding, so search
never returns stale page bodies.

Scope → wing:
    user   -> user__<owner_id>
    team   -> team__<team_id>
    global -> "wiki_global"   (a bare shared wing, readable by everyone)
A page may ALSO carry a project_id; that's a tag for filtering/auto-filing, not a
separate wing (project knowledge wings stay reserved for mined documents).

Access (mirrors the C3 wing-visibility gate):
    - global pages: readable by all; writable by anyone (matches memory_shared
      global semantics — the main/global wiki is a shared surface).
    - user pages: readable+writable only by the owner.
    - team pages: readable+writable by members of that team.
All mutation paths fail closed: an unauthorised caller gets PermissionError.
"""

import re
import uuid

from engine.context import get_request_context

GLOBAL_WING = "wiki_global"


def _write_lock():
    """The blocking RLock that serialises ALL MemPalace writes across daemons.
    Reusing it means wiki saves QUEUE with (never race) the miner / project-sync
    writers — same corruption-avoidance contract as everywhere else. Imported
    lazily to avoid an import cycle (server_daemons imports brain at module top;
    wiki_store is only reached at request time, long after both are loaded)."""
    from server_daemons import _palace_write_lock
    return _palace_write_lock


class WikiAccessError(PermissionError):
    """Caller may not read/write the requested page or scope."""


def _slugify(title: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", (title or "").strip().lower())
    safe = re.sub(r"[\s]+", "-", safe)
    return safe[:60] or "page"


def _caller():
    """(user_id, set-of-team-ids) from the current request context."""
    rc = get_request_context()
    uid = rc.current_user_id or ""
    tids = set(rc.current_team_ids or [])
    return uid, tids


def _wing_for(scope: str, owner_id: str, team_id: str,
              project_id: str = "") -> str:
    """Map a page to its MemPalace wing. A page carrying a project_id mirrors
    into that project's CHAT wing (project_chat__<id>) — the wiki is the sole
    feeder for chat-derived project memory. Ingested project KNOWLEDGE
    (project__<id>) is fed separately by project-sync and is never written here.
    Otherwise: global → wiki_global, team → team__<id>, user → user__<owner>.
    """
    if project_id:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id)
        return f"project_chat__{safe}"
    if scope == "global":
        return GLOBAL_WING
    if scope == "team":
        return f"team__{team_id}" if team_id else ""
    return f"user__{owner_id}" if owner_id else ""


def _can_access(scope: str, owner_id: str, team_id: str) -> bool:
    """Read+write gate. global=anyone, user=owner, team=member."""
    uid, tids = _caller()
    if scope == "global":
        return True
    if scope == "user":
        return bool(uid) and owner_id == uid
    if scope == "team":
        return bool(team_id) and team_id in tids
    return False


def _require_access(page: dict):
    if not _can_access(page.get("scope", ""), page.get("owner_id", ""),
                       page.get("team_id", "")):
        raise WikiAccessError(
            f"wiki: page {page.get('id', '')[:8]} (scope={page.get('scope')}) "
            f"is not accessible to the current user")


# ── MemPalace mirror ────────────────────────────────────────────────────────

def _palace_path():
    import os
    import brain as _brain
    cfg = _brain._load_mempalace_config()
    pp = cfg.get("palace_path", "")
    if not pp or not os.path.isdir(pp):
        return None
    # `tool_add_drawer` (mempalace.mcp_server) resolves its palace from the
    # MEMPALACE_PALACE_PATH env, NOT an argument. Seeded once at server boot
    # (server.py main()); defensively ensure it here too (setdefault) so a
    # standalone/early caller never falls back to the stale default
    # (~/.mempalace/palace = a dead chroma palace → "backend resolution failed").
    os.environ.setdefault("MEMPALACE_PALACE_PATH", pp)
    return pp


def _purge_page_drawers(wing: str, page_id: str):
    """Delete the page's existing drawer(s) by exact source_file match, in `wing`.
    Cheap + exact: each page mirrors to exactly one source `wiki/{page_id}`."""
    pp = _palace_path()
    if not pp:
        return
    import brain as _brain
    ok, _ = _brain._ensure_mempalace_importable()
    if not ok:
        return
    src = f"wiki/{page_id}"
    try:
        from mempalace.palace import get_collection as _gc
        col = _gc(pp, create=False)
        if not col:
            return
        res = col.get(where={"wing": wing}, include=["metadatas"])
        ids = [d for d, m in zip(res.get("ids", []), res.get("metadatas", []))
               if (m or {}).get("source_file") == src]
        if ids:
            with _write_lock():
                col.delete(ids=ids)
    except Exception as e:
        print(f"[wiki] purge drawers failed for {page_id[:8]} in {wing}: "
              f"{type(e).__name__}: {e}", flush=True)


def _mirror_page(page: dict):
    """Replace the page's drawer in its wing with the current title+body. Called
    after every create/update so search reflects the live page. Best-effort: a
    palace hiccup never blocks the DB write the user already saw succeed."""
    wing = _wing_for(page.get("scope", ""), page.get("owner_id", ""),
                     page.get("team_id", ""), page.get("project_id", ""))
    if not wing:
        return
    pp = _palace_path()
    if not pp:
        return
    import brain as _brain
    ok, _ = _brain._ensure_mempalace_importable()
    if not ok:
        return
    page_id = page["id"]
    _purge_page_drawers(wing, page_id)
    body = (page.get("body_md") or "").strip()
    if not body:
        return  # empty page → nothing to index (purge already ran)
    title = page.get("title") or "Untitled"
    content = f"# {title}\n\n{body}"
    try:
        from mempalace.mcp_server import tool_add_drawer
        with _write_lock():
            tool_add_drawer(
                wing=wing,
                room="wiki",
                content=content[:8000],
                source_file=f"wiki/{page_id}",
                added_by="brain-wiki",
            )
    except Exception as e:
        print(f"[wiki] mirror failed for {page_id[:8]} in {wing}: "
              f"{type(e).__name__}: {e}", flush=True)


# ── CRUD (access-checked) ────────────────────────────────────────────────────

def _chatdb():
    from server_lib.db import ChatDB
    return ChatDB


def create_page(scope, title, body_md="", parent_id="", project_id="",
                team_id="", source="manual", agent_id="main"):
    """Create a page in `scope`. owner_id is taken from the caller for user
    scope; team_id must be supplied (and the caller a member) for team scope."""
    uid, tids = _caller()
    scope = scope or "user"
    owner_id = uid if scope == "user" else ""
    if scope == "team" and team_id and team_id not in tids:
        raise WikiAccessError(f"wiki: not a member of team {team_id}")
    if not _can_access(scope, owner_id, team_id):
        raise WikiAccessError(f"wiki: cannot create a {scope} page")
    ChatDB = _chatdb()
    page_id = uuid.uuid4().hex[:16]
    # Position: append after current siblings.
    siblings = [p for p in ChatDB.list_wiki_pages(
        scope=scope, owner_id=owner_id or None,
        team_id=team_id or None, project_id=project_id or None)
        if (p.get("parent_id") or "") == (parent_id or "")]
    position = len(siblings)
    ChatDB.create_wiki_page(
        page_id, agent_id, scope, owner_id, team_id, project_id,
        parent_id or "", _slugify(title), title, body_md, position, source, uid)
    page = ChatDB.get_wiki_page(page_id)
    if page:
        ChatDB.add_wiki_version(page_id, title, body_md, uid)
        _mirror_page(page)
    return page


def get_page(page_id):
    page = _chatdb().get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    return page


def list_tree(scope, project_id=None, team_id=None):
    """All accessible pages in a scope as flat rows (UI assembles the tree from
    parent_id/position). For user scope, owner is the caller; team scope filters
    to the given team (membership enforced)."""
    uid, tids = _caller()
    scope = scope or "user"
    if scope == "user":
        rows = _chatdb().list_wiki_pages(scope="user", owner_id=uid or None,
                                         project_id=project_id)
    elif scope == "team":
        if not team_id or team_id not in tids:
            raise WikiAccessError("wiki: not a member of the requested team")
        rows = _chatdb().list_wiki_pages(scope="team", team_id=team_id,
                                         project_id=project_id)
    elif scope == "global":
        rows = _chatdb().list_wiki_pages(scope="global", project_id=project_id)
    else:
        rows = []
    return rows


def update_page(page_id, title=None, body_md=None, **fields):
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    uid, _ = _caller()
    patch = {}
    if title is not None:
        patch["title"] = title
        patch["slug"] = _slugify(title)
    if body_md is not None:
        patch["body_md"] = body_md
    for k in ("parent_id", "position", "project_id", "archived", "source"):
        if k in fields and fields[k] is not None:
            patch[k] = fields[k]
    if not patch:
        return page
    patch["updated_by"] = uid
    ChatDB.update_wiki_page(page_id, **patch)
    fresh = ChatDB.get_wiki_page(page_id)
    if title is not None or body_md is not None:
        ChatDB.add_wiki_version(page_id, fresh.get("title", ""),
                                fresh.get("body_md", ""), uid)
        _mirror_page(fresh)
    return fresh


def move_page(page_id, parent_id="", position=None):
    """Restructure: re-parent and/or reposition. parent_id="" => top level."""
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    if parent_id == page_id:
        raise WikiAccessError("wiki: a page cannot be its own parent")
    patch = {"parent_id": parent_id or ""}
    if position is not None:
        patch["position"] = int(position)
    patch["updated_by"] = _caller()[0]
    ChatDB.update_wiki_page(page_id, **patch)
    return ChatDB.get_wiki_page(page_id)


def delete_page(page_id):
    """Delete a page. Children are re-parented to the deleted page's parent so
    the subtree survives. Purges the page's drawer from its wing."""
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return False
    _require_access(page)
    new_parent = page.get("parent_id") or ""
    for child_id in ChatDB.wiki_children(page_id):
        ChatDB.update_wiki_page(child_id, parent_id=new_parent)
    wing = _wing_for(page.get("scope", ""), page.get("owner_id", ""),
                     page.get("team_id", ""), page.get("project_id", ""))
    if wing:
        _purge_page_drawers(wing, page_id)
    ChatDB.delete_wiki_page(page_id)
    return True
