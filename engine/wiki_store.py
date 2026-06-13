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
                team_id="", source="manual", source_ref="", agent_id="main"):
    """Create a page in `scope`. owner_id is taken from the caller for user
    scope; team_id must be supplied (and the caller a member) for team scope.
    `source`/`source_ref` link an auto-generated page to its origin object."""
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
        parent_id or "", _slugify(title), title, body_md, position, source, uid,
        source_ref=source_ref)
    page = ChatDB.get_wiki_page(page_id)
    if page:
        note = "initial" if source == "manual" else f"created from {source}"
        ChatDB.add_wiki_version(page_id, title, body_md, uid, note=note)
        _mirror_page(page)
        page = ChatDB.get_wiki_page(page_id)  # refresh current_version
    return page


def get_page(page_id):
    page = _chatdb().get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    return page


def list_tree(filter_mode="all", project_id=None, team_id=None):
    """Accessible pages as flat rows (UI assembles the tree from parent_id/
    position). Filter modes:
      - 'mine'   → the caller's own user-scope pages
      - 'team'   → a specific team's pages (team_id required, membership enforced)
                   or, if no team_id, every team the caller belongs to
      - 'global' → 'pages for all' (global scope)
      - 'all'    → union of everything accessible to the caller (own + teams +
                   global). DEFAULT.
    `project_id` further filters to pages tagged with that project, if given.
    """
    uid, tids = _caller()
    ChatDB = _chatdb()
    mode = filter_mode or "all"
    if mode == "mine":
        rows = ChatDB.list_wiki_pages(scope="user", owner_id=uid or None,
                                      project_id=project_id)
    elif mode == "team":
        if team_id:
            if team_id not in tids:
                raise WikiAccessError("wiki: not a member of the requested team")
            rows = ChatDB.list_wiki_pages(scope="team", team_id=team_id,
                                          project_id=project_id)
        else:
            rows = [p for t in tids for p in
                    ChatDB.list_wiki_pages(scope="team", team_id=t,
                                           project_id=project_id)]
    elif mode == "global":
        rows = ChatDB.list_wiki_pages(scope="global", project_id=project_id)
    else:  # 'all'
        rows = ChatDB.list_wiki_pages_for_user(uid, list(tids))
        if project_id is not None:
            rows = [r for r in rows if (r.get("project_id") or "") == project_id]
    return rows


def update_page(page_id, title=None, body_md=None, _by_human=True, _note="",
                **fields):
    """Edit the CURRENT page. A human edit stamps manually_edited=1 (so the
    re-wikify merge knows to preserve it). Any title/body change appends a new
    version (which becomes current) and re-mirrors to MemPalace. Only the current
    version is ever editable — old versions are read-only (view/promote only)."""
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
    content_changed = title is not None or body_md is not None
    if content_changed and _by_human:
        patch["manually_edited"] = 1
    patch["updated_by"] = uid
    ChatDB.update_wiki_page(page_id, **patch)
    fresh = ChatDB.get_wiki_page(page_id)
    if content_changed:
        ChatDB.add_wiki_version(page_id, fresh.get("title", ""),
                                fresh.get("body_md", ""), uid,
                                note=_note or ("manual edit" if _by_human else "auto update"))
        _mirror_page(fresh)
        fresh = ChatDB.get_wiki_page(page_id)
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


def get_version(page_id, version):
    """Read-only fetch of a historical version. Access-checked via the page."""
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    return ChatDB.get_wiki_version(page_id, version)


def promote_version(page_id, version):
    """Make an old version current: copy its title+body to the live page and
    APPEND it as a brand-new version (current is always MAX(version)). History
    is append-only — nothing is overwritten. Re-mirrors the now-current body to
    MemPalace (only the current version is ever in MemPalace)."""
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    ver = ChatDB.get_wiki_version(page_id, version)
    if not ver:
        raise WikiAccessError(f"wiki: version {version} not found for page")
    uid, _ = _caller()
    ChatDB.update_wiki_page(page_id, title=ver["title"], slug=_slugify(ver["title"]),
                            body_md=ver["body_md"], updated_by=uid)
    fresh = ChatDB.get_wiki_page(page_id)
    ChatDB.add_wiki_version(page_id, fresh["title"], fresh["body_md"], uid,
                            note=f"restored from v{version}")
    _mirror_page(fresh)
    return ChatDB.get_wiki_page(page_id)


# ── Auto-feeder support: source upsert + LLM diff-merge re-wikify ────────────

def _diff_merge(existing_body: str, source_text: str, title: str) -> str:
    """LLM merge: fold the new/changed content from `source_text` into
    `existing_body`, preserving manual edits and structure. Returns the merged
    markdown. Falls back to the existing body on any failure (never destructive).
    """
    import brain as _brain
    from handlers import sidecar_proxy
    rc = get_request_context()
    # Prefer the configured small background model (chat_summary_model); else the
    # server default. Empty → no model available → keep the existing body.
    model = ""
    try:
        _sc = _brain._server_config() or {}
        _csm = (_sc.get("chat_summary_model") or "").strip()
        if _csm and _brain._is_model_available(_csm):
            model = _csm
    except Exception:
        pass
    if not model:
        model = _brain._background_model_default()
    if not model:
        return existing_body
    sys_p = (
        "You maintain a personal knowledge wiki. You are given an EXISTING wiki "
        "page (markdown) and NEW source material. Fold only the genuinely new or "
        "changed information from the source into the existing page. PRESERVE the "
        "existing structure, headings, and any manual edits; do not drop content "
        "that is still valid; do not duplicate facts already present. Keep the "
        "same language as the existing page. Return ONLY the full merged markdown "
        "body — no preamble, no code fences.")
    user_p = (f"# Page title: {title}\n\n## EXISTING PAGE\n{existing_body}\n\n"
              f"## NEW SOURCE MATERIAL\n{source_text}\n\n"
              "Return the merged markdown body.")
    try:
        out = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": user_p}],
            model=model, system_prompt=sys_p, purpose="transform",
            cost_purpose="wiki", max_rounds=1,
            user_id=rc.current_user_id or None, session_id="wiki-merge")
        if not isinstance(out, dict) or out.get("error"):
            return existing_body
        merged = (out.get("reply") or "").strip()
        # Strip accidental code-fence wrap.
        if merged.startswith("```"):
            merged = merged.split("\n", 1)[-1]
            if merged.rstrip().endswith("```"):
                merged = merged.rstrip()[:-3].rstrip()
        return merged or existing_body
    except Exception as e:
        print(f"[wiki] diff-merge failed: {type(e).__name__}: {e}", flush=True)
        return existing_body


def upsert_from_source(scope, title, source_text, source, source_ref,
                       project_id="", team_id="", parent_id="", agent_id="main"):
    """The auto-feeder entry point. If a page already exists for `source_ref`,
    diff-merge the new source into it and save as a new version (current). Else
    create a fresh page from the source. Returns the (current) page.

    Respects manual edits: the merge prompt is told to preserve them, and the
    new version is tagged so history shows where it came from. Only the current
    version lands in MemPalace (via update_page/create_page → _mirror_page)."""
    ChatDB = _chatdb()
    existing = ChatDB.find_wiki_page_by_source(source_ref) if source_ref else None
    if existing:
        _require_access(existing)
        merged = _diff_merge(existing.get("body_md", ""), source_text,
                             existing.get("title", title))
        if merged == existing.get("body_md", ""):
            return existing  # nothing new — skip a no-op version
        return update_page(existing["id"], body_md=merged, _by_human=False,
                           _note=f"merged from {source}")
    # First time: the source text IS the initial body (no existing page to merge).
    return create_page(scope, title, body_md=source_text, parent_id=parent_id,
                       project_id=project_id, team_id=team_id, source=source,
                       source_ref=source_ref, agent_id=agent_id)


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
