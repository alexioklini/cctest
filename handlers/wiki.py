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
        """GET /v1/wiki/tree?scope=user|team|global&project_id=&team_id="""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        from urllib.parse import parse_qs
        q = parse_qs(qs)
        scope = (q.get("scope", ["user"])[0]) or "user"
        project_id = q.get("project_id", [""])[0] or None
        team_id = q.get("team_id", [""])[0] or None
        cm = self._with_wiki_ctx()
        try:
            rows = wiki_store.list_tree(scope, project_id=project_id, team_id=team_id)
            self._send_json({"scope": scope, "pages": rows})
        except wiki_store.WikiAccessError as e:
            self._send_json({"error": str(e)}, 403)
        finally:
            cm.__exit__(None, None, None)

    # ── single page ──

    def _handle_wiki_get(self, path: str):
        """GET /v1/wiki/pages/{id}  (+ optional /versions suffix handled here)"""
        page_id = path.rstrip("/").split("/")[-1]
        want_versions = path.rstrip("/").endswith("/versions")
        if want_versions:
            page_id = path.rstrip("/").split("/")[-2]
        cm = self._with_wiki_ctx()
        try:
            page = wiki_store.get_page(page_id)
            if not page:
                self._send_json({"error": "Page not found"}, 404)
                return
            if want_versions:
                from server_lib.db import ChatDB
                self._send_json({"versions": ChatDB.list_wiki_versions(page_id)})
            else:
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
