# LLM Wiki HTTP handlers — user/team/global markdown wiki, project-aware.
# CRUD + restructure + version history. Each request runs inside a
# request_context carrying the caller's user/team ids so engine.wiki_store can
# enforce its access gate (global=anyone, user=owner, team=member).
from server_lib import auth as _auth_mod
import brain as engine
from engine import wiki_store


class WikiHandlerMixin:
    """Mixin providing /v1/wiki/* HTTP handlers."""

    def _wiki_ctx(self):
        """(user_id, team_ids) for the authenticated caller. System/admin acts
        as itself; access checks treat global as open, so admins reach global
        pages, and reach user/team pages only when they ARE that user/member —
        same as any caller (admin override for personal wikis is intentionally
        not granted here)."""
        user = getattr(self, "_auth_user", _auth_mod.SYNTHETIC_ADMIN)
        uid = user["id"] if user and user["id"] != "__system__" else ""
        tids = []
        if uid:
            tids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(uid)]
        return uid, tids

    def _with_wiki_ctx(self):
        """Context manager entering a request_context with the caller's ids."""
        uid, tids = self._wiki_ctx()
        cm = engine.request_context()
        cm.__enter__()
        rc = engine.get_request_context()
        rc.current_user_id = uid
        rc.current_team_ids = tids
        return cm

    # ── list / tree ──

    # ── Tag palette (global) ──

    def _handle_wiki_tags_get(self):
        """GET /v1/wiki/tags — the global tag palette [{name,color}]."""
        from server_lib.db import ChatDB
        self._send_json({"tags": ChatDB.list_wiki_tags()})

    def _handle_wiki_tags_save(self):
        """POST /v1/wiki/tags {name, color} — create or recolor a tag.
        No-op-friendly: an existing name just gets its color updated (never a
        duplicate). Any logged-in user may manage the shared palette."""
        if self._require_auth() is None:
            return
        from server_lib.db import ChatDB
        body = self._read_json() or {}
        name = (body.get("name") or "").strip().lower()
        color = (body.get("color") or "").strip() or "#888888"
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        ChatDB.upsert_wiki_tag(name, color)
        self._send_json({"status": "saved", "name": name, "color": color})

    def _handle_wiki_tag_delete(self, path: str):
        """DELETE /v1/wiki/tags/<name> — remove a tag from the palette (pages keep
        the name; it just renders neutral until re-added)."""
        if self._require_auth() is None:
            return
        from urllib.parse import unquote
        from server_lib.db import ChatDB
        name = unquote(path.rstrip("/").split("/")[-1])
        ChatDB.delete_wiki_tag(name)
        self._send_json({"deleted": True, "name": name.strip().lower()})

    def _handle_wiki_tag_rename(self):
        """POST /v1/wiki/tags/rename {old, new} — rename a tag in the palette AND
        on every page that uses it (all scopes)."""
        if self._require_auth() is None:
            return
        from server_lib.db import ChatDB
        body = self._read_json() or {}
        res = ChatDB.rename_wiki_tag(body.get("old"), body.get("new"))
        if isinstance(res, dict) and res.get("error"):
            self._send_json(res, 400)
            return
        self._send_json(res or {"status": "ok"})

    def _handle_wiki_tree(self, path: str):
        """GET /v1/wiki/tree?filter=mine|team|global|all&project_id=&team_id=

        filter: mine (my pages) · team (team pages) · global (pages for all) ·
        all (everything accessible to me — default)."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        from urllib.parse import parse_qs
        q = parse_qs(qs)
        # Back-compat: accept legacy ?scope= too.
        filter_mode = (q.get("filter", q.get("scope", ["all"]))[0]) or "all"
        project_id = q.get("project_id", [""])[0] or None
        team_id = q.get("team_id", [""])[0] or None
        cm = self._with_wiki_ctx()
        try:
            rows = wiki_store.list_tree(filter_mode, project_id=project_id, team_id=team_id)
            self._send_json({"filter": filter_mode, "pages": rows})
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    # ── single page ──

    def _handle_wiki_get(self, path: str):
        """GET /v1/wiki/pages/{id}
           GET /v1/wiki/pages/{id}/versions           — list versions
           GET /v1/wiki/pages/{id}/versions/{n}       — one version (read-only)"""
        # /v1/wiki/pages/<id>[/versions[/<n>]]
        segs = path.rstrip("/").split("/")
        # segs: ['', 'v1', 'wiki', 'pages', '<id>', ...]
        try:
            base = segs.index("pages")
        except ValueError:
            self._send_json({"error": "Bad path"}, 400)
            return
        page_id = segs[base + 1] if len(segs) > base + 1 else ""
        tail = segs[base + 2:]  # [] | ['versions'] | ['versions', '<n>']
        cm = self._with_wiki_ctx()
        try:
            from server_lib.db import ChatDB
            if tail and tail[0] == "versions":
                if len(tail) >= 2:  # specific version
                    page = wiki_store.get_page(page_id)
                    if not page:
                        self._send_json({"error": "Page not found"}, 404)
                        return
                    ver = ChatDB.get_wiki_version(page_id, tail[1])
                    self._send_json(ver or {"error": "Version not found"},
                                    200 if ver else 404)
                else:  # version list
                    page = wiki_store.get_page(page_id)
                    if not page:
                        self._send_json({"error": "Page not found"}, 404)
                        return
                    self._send_json({"versions": ChatDB.list_wiki_versions(page_id)})
            else:
                page = wiki_store.get_page(page_id)
                if not page:
                    self._send_json({"error": "Page not found"}, 404)
                    return
                self._send_json(page)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_search(self):
        """GET /v1/wiki/search?q=&limit= — semantic knowledge search for the
        global search modal (v9.306.0). Two blocks, both scoped to the caller:
        `wiki` (tool_wiki_read query mode — cross-wing wiki pages, resolved to
        page id + title) and `memory` (tool_mempalace_query on the caller's own
        wing — chat memories/artifacts/docs). Read-only, LLM-free."""
        import json as _json
        import os as _os
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        query = (qs.get("q") or [""])[0].strip()
        try:
            limit = min(max(int((qs.get("limit") or ["6"])[0] or 6), 1), 20)
        except ValueError:
            limit = 6
        if not query:
            self._send_json({"wiki": [], "memory": [], "query": ""})
            return
        cm = self._with_wiki_ctx()
        try:
            from engine import mempalace_glue
            wiki_hits = []
            try:
                d = _json.loads(mempalace_glue.tool_wiki_read(
                    {"query": query, "limit": limit}))
                for dr in (d.get("drawers") or []):
                    sf = str(dr.get("source_file") or "")
                    pid = sf.split("/", 1)[1] if sf.startswith("wiki/") else ""
                    if not pid:
                        continue
                    page = wiki_store.get_page(pid)
                    if not page:
                        continue  # stale mirror (deleted page)
                    wiki_hits.append({
                        "page_id": pid,
                        "title": page.get("title") or "(ohne Titel)",
                        "scope": page.get("scope") or "",
                        "snippet": (dr.get("text") or "")[:240],
                        "similarity": dr.get("similarity", 0),
                    })
            except Exception:
                pass
            memory_hits = []
            try:
                d = _json.loads(mempalace_glue.tool_mempalace_query(
                    {"query": query, "n_results": limit}))
                for dr in (d.get("drawers") or []):
                    sf = str(dr.get("source_file") or "")
                    if sf.startswith("wiki/"):
                        continue  # already in the wiki block
                    memory_hits.append({
                        "source": _os.path.basename(sf.rstrip("/")) or sf or "(Erinnerung)",
                        "wing": dr.get("wing") or "",
                        "snippet": (dr.get("text") or "")[:240],
                        "similarity": dr.get("similarity", 0),
                    })
            except Exception:
                pass
            self._send_json({"wiki": wiki_hits, "memory": memory_hits, "query": query})
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_from_message(self):
        """POST /v1/wiki/from-message {session_id, message_id} — save ONE assistant
        reply as a wiki page (the explicit per-message save button, v9.303.0).
        scope=user, project-tagged when the session belongs to a project. The
        stable source_ref message/<id> keeps re-saves idempotent: saving the same
        answer again re-versions the SAME page (replace=True — deterministic, no
        LLM merge) instead of duplicating it. Distinct from the per-turn memorize
        actions (MemPalace mirror) and the session-level auto-filing
        (wiki_from_chat, one LLM-organised page per session)."""
        from server_lib.db import ChatDB, _project_id_for_name
        body = self._read_json() or {}
        sid = (body.get("session_id") or "").strip()
        try:
            mid = int(body.get("message_id"))
        except (TypeError, ValueError):
            mid = 0
        if not sid or not mid:
            self._send_json({"error": "session_id and message_id required"}, 400)
            return
        info = self._session_access_check(sid)
        if info is None:
            return
        msg = next((m for m in (ChatDB.mempalace_load_new_messages(sid, 0) or [])
                    if m.get("id") == mid and m.get("role") == "assistant"), None)
        content = ((msg or {}).get("content") or "").strip()
        if not content:
            self._send_json({"error": "Assistant message not found"}, 404)
            return
        agent_id = info.get("agent_id") or "main"
        project_id = ""
        if info.get("project"):
            try:
                project_id = _project_id_for_name(agent_id, info["project"]) or ""
            except Exception:
                project_id = ""
        # Title: first non-empty line of the reply (headings unwrapped), else the
        # session title — a per-message page needs a per-message name.
        title = ""
        for line in content.splitlines():
            line = line.strip().lstrip("#").strip().strip("*").strip()
            if line:
                title = line[:80]
                break
        title = title or (info.get("title") or "").strip() or "Chat-Antwort"
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.upsert_from_source(
                scope="user", title=title, source_text=content, source="chat",
                source_ref=f"message/{mid}", project_id=project_id,
                agent_id=agent_id, replace=True)
            if not page:
                self._send_json({"error": "save failed"}, 500)
                return
            self._send_json({"status": "ok", "page_id": page.get("id"),
                             "title": page.get("title") or title})
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_create(self, path: str):
        """POST /v1/wiki/pages  {scope, title, body_md?, parent_id?, project_id?, team_id?}"""
        body = self._read_json()
        title = (body.get("title") or "").strip()
        if not title:
            self._send_json({"error": "title is required"}, 400)
            return
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.create_page(
                scope=body.get("scope", "user"),
                title=title,
                body_md=body.get("body_md", ""),
                parent_id=body.get("parent_id", ""),
                project_id=body.get("project_id", ""),
                team_id=body.get("team_id", ""),
                source=body.get("source", "manual"),
                source_ref=body.get("source_ref", ""),
            )
            self._send_json(page or {"error": "create failed"}, 201 if page else 500)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_update(self, path: str):
        """PUT /v1/wiki/pages/{id}  {title?, body_md?, project_id?, archived?}"""
        page_id = path.rstrip("/").split("/")[-1]
        body = self._read_json()
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.update_page(
                page_id,
                title=body.get("title"),
                body_md=body.get("body_md"),
                project_id=body.get("project_id"),
                archived=body.get("archived"),
                tags=body.get("tags"),
            )
            if not page:
                self._send_json({"error": "Page not found"}, 404)
                return
            self._send_json(page)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_move(self, path: str):
        """POST /v1/wiki/pages/{id}/move  {parent_id?, position?}"""
        page_id = path.rstrip("/").split("/")[-2]
        body = self._read_json()
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.move_page(
                page_id,
                parent_id=body.get("parent_id", ""),
                position=body.get("position"),
            )
            if not page:
                self._send_json({"error": "Page not found"}, 404)
                return
            self._send_json(page)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_promote(self, path: str):
        """POST /v1/wiki/pages/{id}/promote/{version} — make an old version the
        current one (copied to a new version; re-mirrored to MemPalace)."""
        segs = path.rstrip("/").split("/")
        try:
            base = segs.index("pages")
            page_id = segs[base + 1]
            version = segs[segs.index("promote") + 1]
        except (ValueError, IndexError):
            self._send_json({"error": "Bad path — expected /pages/<id>/promote/<n>"}, 400)
            return
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.promote_version(page_id, version)
            if not page:
                self._send_json({"error": "Page not found"}, 404)
                return
            self._send_json(page)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_generate(self, path: str):
        """POST /v1/wiki/pages/{id}/generate {kind: summary|podcast, include_children?}
        Generates a summary or podcast from the page (+ optional subtree) and
        saves it as a new CHILD page. Synchronous (LLM/TTS) — the UI shows a
        spinner; returns the created page."""
        page_id = path.rstrip("/").split("/")[-2]
        body = self._read_json()
        kind = (body.get("kind") or "summary").strip().lower()
        include_children = bool(body.get("include_children"))
        cm = self._with_wiki_ctx()
        try:
            from engine import wiki_gen
            if kind == "podcast":
                res = wiki_gen.generate_podcast(page_id, include_children)
            else:
                res = wiki_gen.generate_summary(page_id, include_children)
            if isinstance(res, dict) and res.get("error"):
                self._send_json(res, 400)
                return
            self._send_json(res)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        except Exception as e:
            import traceback; traceback.print_exc()
            self._send_json({"error": f"generation failed: {e}"}, 500)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_media(self, path: str):
        """POST /v1/wiki/pages/{id}/media (multipart) — upload image/audio/video,
        store as an artifact, return {artifact_id, kind, snippet} to insert."""
        page_id = path.rstrip("/").split("/")[-2]
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json({"error": "expected multipart/form-data"}, 400)
            return
        clen = int(self.headers.get("Content-Length", "0") or 0)
        if not clen:
            self._send_json({"error": "empty body"}, 400)
            return
        raw = self.rfile.read(clen)
        from handlers.classification import _parse_multipart_files
        files, _fields, err = _parse_multipart_files(ctype, raw)
        if err or not files:
            self._send_json({"error": err or "no file"}, 400)
            return
        f = files[0]
        cm = self._with_wiki_ctx()
        try:
            from engine import wiki_gen
            res = wiki_gen.save_media(page_id, f.get("name", "media"), f.get("bytes", b""))
            if res.get("error"):
                self._send_json(res, 400)
                return
            self._send_json(res)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    def _handle_wiki_delete(self, path: str):
        """DELETE /v1/wiki/pages/{id}"""
        page_id = path.rstrip("/").split("/")[-1]
        cm = self._with_wiki_ctx()
        try:
            ok = wiki_store.delete_page(page_id)
            self._send_json({"deleted": bool(ok)}, 200 if ok else 404)
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)
