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


def _parse_tags(raw) -> list:
    """Parse a tags JSON column into a clean list of short string tags."""
    import json as _json
    if isinstance(raw, list):
        vals = raw
    else:
        try:
            vals = _json.loads(raw or "[]")
        except Exception:
            vals = []
    out, seen = [], set()
    for v in vals:
        t = str(v or "").strip()[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:20]


def _suggest_tags(title: str, body_md: str) -> list:
    """LLM-suggest 1-5 lowercase topic tags from a page's title+body. Best-effort
    — returns [] on any failure or when no background model is available."""
    import json as _json
    import brain as _brain
    from handlers import sidecar_proxy
    text = f"{title}\n\n{body_md}".strip()
    if len(text) < 40:
        return []
    model = ""
    try:
        sc = _brain._server_config() or {}
        m = (sc.get("chat_summary_model") or "").strip()
        if m and _brain._is_model_available(m):
            model = m
    except Exception:
        pass
    if not model:
        model = _brain._background_model_default()
    if not model:
        return []
    rc = get_request_context()
    # Give the model the existing palette so it REUSES a matching tag rather than
    # inventing a near-duplicate (e.g. don't add 'gardening' if 'garden' exists).
    existing = []
    try:
        existing = [t["name"] for t in _chatdb().list_wiki_tags()][:120]
    except Exception:
        existing = []
    reuse_hint = (f" PREFER reusing an existing tag from this list when it fits "
                  f"(do not invent a near-synonym): {', '.join(existing)}."
                  if existing else "")
    sys_p = (
        "Extract 1-5 short topic TAGS for this wiki page. Tags are single "
        "lowercase words or short kebab-case phrases naming the page's topics "
        "(e.g. 'gardening', 'project-x', 'tax-2026'). Keep the page's language."
        + reuse_hint +
        " Return ONLY a JSON array of strings, nothing else.")
    try:
        out = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": text[:8000]}],
            model=model, system_prompt=sys_p, purpose="transform",
            cost_purpose="wiki", max_rounds=1,
            user_id=rc.current_user_id or None, session_id="wiki-tags")
        if not isinstance(out, dict) or out.get("error"):
            return []
        reply = (out.get("reply") or "").strip()
        # Tolerate a code-fence wrap or leading prose; grab the first JSON array.
        m = re.search(r"\[.*\]", reply, re.S)
        if not m:
            return []
        return _parse_tags(_json.loads(m.group(0)))
    except Exception:
        return []


def _apply_auto_tags(page_id: str):
    """Recompute auto-tags for a page and merge them into its tags, preserving
    any user-added tags (auto never deletes a manual tag). Best-effort."""
    ChatDB = _chatdb()
    page = ChatDB.get_wiki_page(page_id)
    if not page:
        return
    import json as _json
    prev_auto = _parse_tags(page.get("auto_tags"))
    cur_tags = _parse_tags(page.get("tags"))
    # Manual tags = current tags minus the previous auto set.
    manual = [t for t in cur_tags if t.lower() not in {a.lower() for a in prev_auto}]
    new_auto = _suggest_tags(page.get("title", ""), page.get("body_md", ""))
    if not new_auto and not prev_auto:
        return  # nothing to do
    # Merged tags = manual ∪ new_auto (manual order first).
    merged, seen = [], set()
    for t in manual + new_auto:
        if t.lower() not in seen:
            seen.add(t.lower())
            merged.append(t)
    ChatDB.update_wiki_page(page_id, tags=_json.dumps(merged),
                            auto_tags=_json.dumps(new_auto))
    _register_tags_in_palette(merged)


def _random_tag_color() -> str:
    """A pleasant, deterministic-ish color for a new auto-tag. Date.now/random
    are fine here (server-side, not warmup-prefix). Picks from a fixed palette."""
    import random
    PALETTE = ["#2563eb", "#16a34a", "#db2777", "#ea580c", "#7c3aed",
               "#0891b2", "#ca8a04", "#dc2626", "#059669", "#9333ea",
               "#0d9488", "#c026d3", "#65a30d", "#e11d48", "#4f46e5"]
    return random.choice(PALETTE)


def _register_tags_in_palette(tags: list):
    """Ensure every tag name has a palette entry (global). New names get a random
    color; existing names are left untouched. Best-effort."""
    if not tags:
        return
    ChatDB = _chatdb()
    for t in tags:
        n = (t or "").strip().lower()
        if not n:
            continue
        try:
            if not ChatDB.wiki_tag_exists(n):
                ChatDB.upsert_wiki_tag(n, _random_tag_color())
        except Exception:
            pass


def _auto_tag_async(page_id: str):
    """Recompute auto-tags off the request path. Captures the caller's identity
    so the background thread's request_context can access the page."""
    import threading
    import brain as _brain
    rc = get_request_context()
    uid = rc.current_user_id or ""
    tids = list(rc.current_team_ids or [])

    def _run():
        try:
            with _brain.request_context():
                r = get_request_context()
                r.current_user_id = uid
                r.current_team_ids = tids
                _apply_auto_tags(page_id)
        except Exception as e:
            print(f"[wiki] auto-tag failed for {page_id[:8]}: "
                  f"{type(e).__name__}: {e}", flush=True)
    threading.Thread(target=_run, daemon=True, name=f"wiki-tag-{page_id[:8]}").start()


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
        return
    # Opt-in KG: for PROJECT-TAGGED pages, also extract triples into the project
    # KG (additive to the project-sync KG). Off unless config.json →
    # mempalace.kg.wiki is true. Background (one LLM call), best-effort.
    if page.get("project_id"):
        _kg_for_wiki_page_async(page, wing, pp)


def _kg_for_wiki_page_async(page: dict, wing: str, palace_path: str):
    """Run KG extraction for ONE project-tagged wiki page in the background.
    Gated on mempalace.kg.enabled AND mempalace.kg.wiki (opt-in, default off).
    Invalidates the page's prior triples first (the drawer id changed on save,
    so old triples would orphan), then runs the post-pass scoped to this page's
    synthetic source_file (wiki/<id>) in per_drawer mode (no file on disk)."""
    import threading
    import brain as _brain
    kg_cfg = (_brain._load_mempalace_config().get("kg") or {})
    if not kg_cfg.get("enabled", True) or not kg_cfg.get("wiki", False):
        return
    page_id = page["id"]
    rc = get_request_context()
    uid = rc.current_user_id or ""
    tids = list(rc.current_team_ids or [])

    def _run():
        try:
            import os as _os
            from engine import kg_extract as _kg
            chats_db = _os.path.join(_brain.AGENTS_DIR, "main", "chats.db")
            src = f"wiki/{page_id}"
            adapter = "brain-wiki-kg"
            # 1. Drop the page's prior triples (exact source_file match) so a
            #    re-saved page replaces — never accumulates — its triples.
            try:
                _kg._invalidate_source_in_kg(palace_path, chats_db, wing, src, adapter)
            except Exception:
                pass
            # 2. Extract from the current drawer. per_drawer mode: the wiki page
            #    is synthetic (no file on disk), so source_file chunking can't
            #    read it — feed the drawer content directly.
            with _brain.request_context():
                r = get_request_context()
                r.current_user_id = uid
                r.current_team_ids = tids
                _kg.run_kg_post_pass(
                    palace_path=palace_path,
                    wing=wing,
                    source_prefix=src,
                    adapter_name=adapter,
                    profile_name=kg_cfg.get("profile", "normative") or "normative",
                    model=kg_cfg.get("extraction_model", "") or "",
                    chats_db_path=chats_db,
                    max_triples_per_drawer=int(kg_cfg.get("max_triples_per_drawer", 12)),
                    max_drawer_chars=int(kg_cfg.get("max_drawer_chars", 6000)),
                    min_confidence=float(kg_cfg.get("min_confidence", 0.5)),
                    chunking_mode="per_drawer",
                    skip_code=True,
                    log_prefix="[wiki.kg]",
                )
        except Exception as e:
            print(f"[wiki.kg] {page_id[:8]} failed: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=_run, daemon=True, name=f"wiki-kg-{page_id[:8]}").start()


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
        if (body_md or "").strip():
            _auto_tag_async(page_id)   # suggest topic tags in the background
        page = _decorate(ChatDB.get_wiki_page(page_id))  # refresh + normalize
    return page


def _decorate(page: dict) -> dict:
    """Normalize a raw DB page row for the client: parse tags JSON into a list
    and add `mirrored` (a non-empty current body means it has a MemPalace drawer)."""
    if not page:
        return page
    page["tags"] = _parse_tags(page.get("tags"))
    page["auto_tags"] = _parse_tags(page.get("auto_tags"))
    page["mirrored"] = bool((page.get("body_md") or "").strip())
    return page


def get_page(page_id):
    page = _chatdb().get_wiki_page(page_id)
    if not page:
        return None
    _require_access(page)
    return _decorate(page)


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
    # Decorate (parse tags, mirrored flag) and drop the heavy body from tree rows
    # — the client fetches body on open. Keeps all grouping/filter fields.
    out = []
    for r in rows:
        _decorate(r)
        r.pop("body_md", None)
        out.append(r)
    return out


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
    # Explicit user tag edit (replaces the tags list; auto_tags untouched).
    if fields.get("tags") is not None:
        import json as _json
        _new_tags = _parse_tags(fields["tags"])
        patch["tags"] = _json.dumps(_new_tags)
        _register_tags_in_palette(_new_tags)   # ensure palette has these names
    if not patch:
        return _decorate(page)
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
        # Recompute auto-tags in the background — never block the save on an LLM.
        _auto_tag_async(page_id)
        fresh = ChatDB.get_wiki_page(page_id)
    return _decorate(fresh)


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
                       project_id="", team_id="", parent_id="", agent_id="main",
                       replace=False):
    """The auto-feeder entry point. If a page already exists for `source_ref`,
    update it and save as a new version (current); else create a fresh page.
    Returns the (current) page. Only the current version lands in MemPalace.

    Two update modes:
      - replace=False (default): LLM diff-MERGE the new source into the existing
        body (preserving manual edits); a no-op merge skips the version. Right
        for an evolving chat that gets re-wikified.
      - replace=True: set the new source AS the body directly (no LLM, no merge),
        ALWAYS appending a new version. Right for a recurring producer — e.g. a
        scheduled run — where every run should be a fresh version of the same
        page, not accumulated into the old one."""
    ChatDB = _chatdb()
    existing = ChatDB.find_wiki_page_by_source(source_ref) if source_ref else None
    if existing:
        _require_access(existing)
        if replace:
            # Fresh version every time — no diff-merge, no no-op skip.
            return update_page(existing["id"], body_md=source_text, _by_human=False,
                               _note=f"new run from {source}")
        merged = _diff_merge(existing.get("body_md", ""), source_text,
                             existing.get("title", title))
        if merged == existing.get("body_md", ""):
            return existing  # nothing new — skip a no-op version
        return update_page(existing["id"], body_md=merged, _by_human=False,
                           _note=f"merged from {source}")
    # First time: the source text IS the initial body.
    return create_page(scope, title, body_md=source_text, parent_id=parent_id,
                       project_id=project_id, team_id=team_id, source=source,
                       source_ref=source_ref, agent_id=agent_id)


def wiki_from_chat(session_id, turn_ids=None, scope=None):
    """Memorize a chat into the wiki: gather the selected turns (or all), LLM-
    organize them into a clean topic-titled page, and upsert by source_ref=
    'session/<sid>' so re-memorizing the same chat re-versions the SAME page
    (diff-merge preserves manual edits). Returns the page, or None if nothing to
    do. Runs as the session's owner (sets the request context).

    This is the wiki-model replacement for the old _memorize_mempalace_turns
    direct-to-wing write: the page is the source of truth, its mirror is the
    searchable projection."""
    import brain as _brain
    from server_lib.db import ChatDB, _project_id_for_name
    from handlers import sidecar_proxy

    info = ChatDB.get_session_info(session_id)
    if not info:
        return None
    user_id = info.get("user_id", "") or ""
    agent_id = info.get("agent_id", "main") or "main"
    project = info.get("project", "") or ""
    project_id = _project_id_for_name(agent_id, project) if project else ""
    # Scope: a project chat tags the page to the project (→ project_chat wing);
    # otherwise the user's own wiki (team chats could pass scope='team').
    page_scope = scope or ("user")

    # Gather the selected turns' text.
    msgs = ChatDB.mempalace_load_new_messages(session_id, 0) or []
    want = set(int(t) for t in (turn_ids or []))
    cur_turn = 0
    parts = []
    for m in msgs:
        mid = int(m.get("id") or 0)
        role = (m.get("role") or "").strip()
        if role == "user":
            cur_turn = mid
        if want and cur_turn not in want:
            continue
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        text = content if isinstance(content, str) else str(content)
        if text.strip():
            parts.append(f"{role.upper()}: {text.strip()[:4000]}")
    if not parts:
        return None
    convo = "\n\n".join(parts)[:24000]

    # Title from the session, body via LLM organization.
    title = (info.get("title") or "Chat-Notiz").strip()[:120]
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

    organized = convo
    if model:
        sys_p = (
            "You curate a personal knowledge wiki. Turn the following conversation "
            "into ONE clean, well-structured markdown wiki page that captures the "
            "durable knowledge, decisions, facts and conclusions — NOT the back-and-"
            "forth. Use headings and bullet points, organized by topic. Drop "
            "pleasantries, dead ends, and meta-chatter. Keep the user's language. "
            "Return ONLY the markdown body (no title line, no code fences).")
        try:
            out = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": f"Conversation:\n\n{convo}"}],
                model=model, system_prompt=sys_p, purpose="transform",
                cost_purpose="wiki", max_rounds=1,
                user_id=user_id or None, session_id=f"wiki-from-chat-{session_id[:8]}")
            if isinstance(out, dict) and not out.get("error"):
                body = (out.get("reply") or "").strip()
                if body.startswith("```"):
                    body = body.split("\n", 1)[-1]
                    if body.rstrip().endswith("```"):
                        body = body.rstrip()[:-3].rstrip()
                if body:
                    organized = body
        except Exception as e:
            print(f"[wiki] from-chat organize failed: {type(e).__name__}: {e}", flush=True)

    # Run as the session owner so the access gate + wing routing are correct.
    with _brain.request_context():
        rc = get_request_context()
        rc.current_user_id = user_id
        try:
            from server_lib import auth as _auth_mod
            rc.current_team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(user_id)] if user_id else []
        except Exception:
            rc.current_team_ids = []
        return upsert_from_source(
            scope=page_scope, title=title, source_text=organized,
            source="chat", source_ref=f"session/{session_id}",
            project_id=project_id, agent_id=agent_id)


def wiki_from_artifact(*, title, body_md, source, source_ref, user_id="",
                       project_id="", scope="user", agent_id="main",
                       replace=False):
    """File a generated artifact (Studio output, scheduled-task result, workflow
    result) into the wiki as a page, upserted by source_ref so a regenerated
    artifact re-versions the same page. Body is taken as-is (already a report);
    no LLM call. Runs as `user_id`. Best-effort — returns the page or None.

    replace=True → every call appends a fresh version of the SAME page (no
    diff-merge, no no-op skip). Use for recurring producers like scheduled runs
    so each run is a new version of one page. Default False = diff-merge.

    A project_id routes the page to the project_chat wing (consistent with the
    rest of the wiki); pass it for project-scoped outputs."""
    if not (body_md or "").strip():
        return None
    import brain as _brain
    with _brain.request_context():
        rc = get_request_context()
        rc.current_user_id = user_id or ""
        try:
            from server_lib import auth as _auth_mod
            rc.current_team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(user_id)] if user_id else []
        except Exception:
            rc.current_team_ids = []
        try:
            return upsert_from_source(
                scope=scope, title=title, source_text=body_md,
                source=source, source_ref=source_ref,
                project_id=project_id, agent_id=agent_id, replace=replace)
        except WikiAccessError as e:
            print(f"[wiki] from-artifact refused ({source_ref}): {e}", flush=True)
            return None


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
