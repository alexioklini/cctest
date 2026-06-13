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
