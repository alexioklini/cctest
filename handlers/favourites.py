"""HTTP handlers for /v1/favourites/*.

Hydration: each row references an item by (item_type, item_id, agent_id).
The GET handler resolves it to {title, updated_at, available} so the
client can render cards without making N follow-up requests. RBAC: a
favourite never broadens visibility — adding to scope=team requires the
underlying item to be visible to that team; scope=general requires admin
AND a globally-visible item.
"""
import os
import sys
import time
import uuid

from server_lib import auth as _auth_mod
from server_lib.favourites import (
    FavouritesDB, ITEM_TYPES, SCOPES,
    IMAGE_DIR, MAX_IMAGE_BYTES, ALLOWED_IMAGE_EXTS,
)
import brain as engine


def _srv():
    return sys.modules.get("__main__") or sys.modules["server"]


# ── Hydration helpers ────────────────────────────────────────────────

def _hydrate_chat(item_id: str, agent_id: str, user, team_ids: list[str], is_admin: bool) -> dict:
    """Resolve a chat or project_chat to display metadata + access check."""
    from server_lib.db import ChatDB
    info = ChatDB.get_session_info(item_id)
    if not info:
        return {"available": False, "title": "(deleted chat)"}
    if not _user_can_see_session(info, user, team_ids, is_admin):
        return {"available": False, "title": "(no access)"}
    title = (info.get("title") or "").strip() or "Untitled chat"
    return {
        "available": True,
        "title": title,
        "updated_at": float(info.get("last_active") or 0),
        "subtitle": info.get("project") or "",
        "visibility": info.get("visibility") or "user",
        "owner_user_id": info.get("user_id") or "",
        "team_id": info.get("team_id") or "",
    }


def _user_can_see_session(info: dict, user: dict, team_ids: list[str], is_admin: bool) -> bool:
    if is_admin:
        return True
    owner = info.get("user_id") or ""
    if not owner:
        return True  # legacy anonymous
    if owner == user["id"]:
        return True
    if (info.get("visibility") or "") == "team":
        tid = info.get("team_id") or ""
        if tid and tid in team_ids:
            return True
    return False


def _hydrate_project(item_id: str, agent_id: str, user, team_ids: list[str], is_admin: bool) -> dict:
    """item_id is the project_id (uuid hex[:12]); we have to find by id."""
    projects = engine.ProjectManager.list_projects(agent_id or "main")
    match = next((p for p in projects if p.get("id") == item_id), None)
    if not match:
        return {"available": False, "title": "(deleted project)"}
    if not is_admin and not _auth_mod.can_access_project(user, match):
        return {"available": False, "title": "(no access)"}
    # mtime of project.json for "updated_at"
    proj_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "agents", agent_id or "main", "projects", match.get("name", ""), "project.json",
    )
    try:
        updated = os.path.getmtime(proj_path)
    except OSError:
        updated = 0.0
    # Surface a URL the client can render directly when the project has its own
    # uploaded image — favourites then reuse it instead of forcing a separate
    # upload onto the favourite row. Cache-busted with mtime so a replaced
    # source image refreshes the favourite card too.
    source_image_url = ""
    if match.get("image"):
        agent = agent_id or "main"
        from urllib.parse import quote as _q
        source_image_url = f"/v1/agents/{_q(agent)}/projects/{_q(match.get('name', ''))}/image?v={int(updated)}"
    return {
        "available": True,
        "title": match.get("name", "Untitled"),
        "updated_at": updated,
        "subtitle": (match.get("description") or "")[:80],
        "visibility": match.get("visibility") or "user",
        "owner_user_id": match.get("owner_user_id") or "",
        "team_id": match.get("owner_team_id") or "",
        "_project_name": match.get("name", ""),  # used for href
        "source_image_url": source_image_url,
        "source_icon": match.get("icon", "") or "",
        "source_color": match.get("color", "") or "",
    }


def _hydrate_workflow(item_id: str, agent_id: str, user, team_ids, is_admin) -> dict:
    workflows = engine.WorkflowEngine.list_workflows(agent_id or "main")
    match = next((w for w in workflows if w.get("name") == item_id), None)
    if not match:
        return {"available": False, "title": "(deleted workflow)"}
    return {
        "available": True,
        "title": item_id,
        "updated_at": float(match.get("mtime") or 0),
        "subtitle": (match.get("description") or "")[:80],
        "visibility": "general",  # workflows aren't user-scoped today
        "owner_user_id": "",
        "team_id": "",
    }


def _hydrate_schedule(item_id: str, agent_id: str, user, team_ids, is_admin) -> dict:
    """item_id = schedule name (UNIQUE)."""
    sched = None
    try:
        sched = engine._scheduler.get_task(item_id) if engine._scheduler else None
    except Exception:
        pass
    if not sched:
        return {"available": False, "title": "(deleted schedule)"}
    owner = (sched.get("user_id") or "").strip()
    if not is_admin and owner and owner != user["id"]:
        return {"available": False, "title": "(no access)"}
    return {
        "available": True,
        "title": item_id,
        "updated_at": _parse_iso(sched.get("next_run")) or _parse_iso(sched.get("updated_at")) or 0,
        "subtitle": sched.get("schedule") or "",
        "visibility": "user" if owner else "general",
        "owner_user_id": owner,
        "team_id": "",
    }


def _parse_iso(val) -> float:
    if not val:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(val)).timestamp()
    except Exception:
        return 0.0


def _hydrate_artifact(item_id: str, agent_id: str, user, team_ids, is_admin) -> dict:
    """item_id = artifact id. Latest version's created_at drives updated_at."""
    from server_lib.db import ChatDB, _db_conn
    art = ChatDB.get_artifact(item_id)
    if not art:
        return {"available": False, "title": "(deleted artifact)"}
    parent_sid = art.get("session_id") or ""
    sess = ChatDB.get_session_info(parent_sid) if parent_sid else None
    if parent_sid and not sess:
        # Artifact row exists but its parent session is gone — opener would
        # 404 trying to load the chat, so flag the favourite as unavailable
        # with a clear hint instead of letting the user click into an error.
        return {
            "available": False,
            "title": art.get("name") or "artifact",
            "subtitle": "(parent chat deleted)",
        }
    if sess and not _user_can_see_session(sess, user, team_ids, is_admin):
        return {"available": False, "title": "(no access)"}
    # Latest version timestamp
    updated = float(art.get("created_at") or 0)
    try:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) FROM artifact_versions WHERE artifact_id = ?",
                (item_id,)
            ).fetchone()
            if row and row[0]:
                updated = float(row[0])
    except Exception:
        pass
    # Image-type artifacts can render themselves as the card background.
    # Cache-busted with the artifact's latest version timestamp.
    source_image_url = ""
    art_type = (art.get("type") or "").lower()
    if art_type == "image":
        source_image_url = f"/v1/artifacts/{art.get('id')}/content?v={int(updated)}"
    return {
        "available": True,
        "title": art.get("name") or "artifact",
        "updated_at": updated,
        "subtitle": art.get("type") or "",
        "visibility": (sess or {}).get("visibility") or "user",
        "owner_user_id": (sess or {}).get("user_id") or "",
        "team_id": (sess or {}).get("team_id") or "",
        "source_image_url": source_image_url,
        "_artifact_session_id": art.get("session_id") or "",
        "_artifact_agent_id": art.get("agent_id") or agent_id or "main",
    }


_HYDRATORS = {
    "chat": _hydrate_chat,
    "project_chat": _hydrate_chat,
    "project": _hydrate_project,
    "workflow": _hydrate_workflow,
    "schedule": _hydrate_schedule,
    "artifact": _hydrate_artifact,
}


# ── Scope authority + visibility checks ──────────────────────────────

def _check_scope_authority(user: dict, scope: str, scope_id: str,
                           item_meta: dict) -> tuple[bool, str]:
    """Verify caller can write to (scope, scope_id) AND that scope doesn't
    broaden the item's visibility. Returns (ok, error_message)."""
    is_admin = user.get("role") == "admin" or user["id"] == "__system__"
    item_vis = (item_meta or {}).get("visibility") or "user"
    item_owner = (item_meta or {}).get("owner_user_id") or ""
    item_team = (item_meta or {}).get("team_id") or ""

    if scope == "user":
        # Always allowed to favourite-for-self if you can see the item.
        if scope_id != user["id"] and not is_admin:
            return False, "user-scope must match caller"
        return True, ""

    if scope == "team":
        if not scope_id:
            return False, "scope_id required for team scope"
        # Caller must be member of that team.
        my_teams = _auth_mod.AuthDB.get_user_teams(user["id"])
        if not is_admin and not any(t["id"] == scope_id for t in my_teams):
            return False, "not a member of that team"
        # Item must be visible to the team. Three cases that satisfy this:
        #   - item is global / general
        #   - item is team-scoped to *this* team
        #   - item is owned by a team member (still individually-scoped, but
        #     the item is theirs to share). We approximate by allowing it if
        #     the item has no team restriction AND no other-team restriction.
        if item_vis in ("global", "general"):
            return True, ""
        if item_vis == "team" and item_team == scope_id:
            return True, ""
        # User-scoped item: only allow if the owner is in the team.
        if item_vis in ("user", "") and item_owner:
            head = next((t for t in my_teams if t["id"] == scope_id), None)
            # Cheap check: if the item's owner is the caller, they can share into their team.
            if item_owner == user["id"]:
                return True, ""
            # Otherwise fall through.
        return False, "item not visible to that team"

    if scope == "general":
        if not is_admin:
            return False, "only admins can pin to everyone"
        # Don't let admins pin private user/team items globally — the
        # user may still see "(no access)" if they aren't entitled.
        if item_vis in ("global", "general"):
            return True, ""
        return False, "item is not globally visible"

    return False, f"unknown scope '{scope}'"


# ── Image upload helpers ─────────────────────────────────────────────

def _save_uploaded_image(fav_id: int, raw: bytes, ext: str) -> str | None:
    """Persist bytes to IMAGE_DIR; returns the stored basename or None."""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    if ext.lower() not in ALLOWED_IMAGE_EXTS:
        return None
    os.makedirs(IMAGE_DIR, exist_ok=True)
    # Random suffix so cache-busting works after replace.
    name = f"{fav_id}-{uuid.uuid4().hex[:8]}{ext.lower()}"
    full = os.path.join(IMAGE_DIR, name)
    try:
        with open(full, "wb") as f:
            f.write(raw)
    except OSError:
        return None
    return name


# ── Mixin ────────────────────────────────────────────────────────────

class FavouritesHandlerMixin:
    """HTTP handlers for /v1/favourites."""

    # ── helpers ──

    def _favourites_caller_context(self):
        """Return (user, team_ids, is_admin) for the current request."""
        user = getattr(self, "_auth_user", _auth_mod.SYNTHETIC_ADMIN)
        is_admin = user.get("role") == "admin" or user["id"] == "__system__"
        teams = _auth_mod.AuthDB.get_user_teams(user["id"])
        return user, [t["id"] for t in teams], is_admin

    # ── GET /v1/favourites ──

    def _handle_favourites_list(self):
        user, team_ids, is_admin = self._favourites_caller_context()
        rows = FavouritesDB.list_visible(user["id"], team_ids, is_admin=False)
        # Note: even admins get the same scope-filtered list here. The admin
        # "see everything" view is a separate panel (out of scope for v1).

        out = []
        for row in rows:
            hydrator = _HYDRATORS.get(row["item_type"])
            meta = hydrator(row["item_id"], row["agent_id"], user, team_ids, is_admin) if hydrator else \
                   {"available": False, "title": "(unknown type)"}
            # Drop rows the caller can no longer see (item revoked).
            # Keep them but mark unavailable so the user can still remove them.
            out.append({
                **row,
                "title": meta.get("title", ""),
                "subtitle": meta.get("subtitle", ""),
                "updated_at": meta.get("updated_at", row["added_at"]),
                "available": meta.get("available", False),
                "_project_name": meta.get("_project_name", ""),
                "source_image_url": meta.get("source_image_url", "") or "",
                "source_icon": meta.get("source_icon", "") or "",
                "source_color": meta.get("source_color", "") or "",
                "_artifact_session_id": meta.get("_artifact_session_id", "") or "",
                "_artifact_agent_id": meta.get("_artifact_agent_id", "") or "",
            })
        # Sort newest-changed first
        out.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        self._send_json({"favourites": out})

    # ── POST /v1/favourites ──

    def _handle_favourites_add(self):
        body = self._read_json()
        scope = (body.get("scope") or "user").strip()
        scope_id = (body.get("scope_id") or "").strip()
        item_type = (body.get("item_type") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        agent_id = (body.get("agent_id") or "main").strip()
        icon = (body.get("icon") or "").strip()
        color = (body.get("color") or "").strip()

        user, team_ids, is_admin = self._favourites_caller_context()

        # Default scope_id when caller picks user-scope without supplying one.
        if scope == "user" and not scope_id:
            scope_id = user["id"]

        if scope not in SCOPES:
            self._send_json({"error": f"invalid scope '{scope}'"}, 400)
            return
        if item_type not in ITEM_TYPES:
            self._send_json({"error": f"invalid item_type '{item_type}'"}, 400)
            return
        if not item_id:
            self._send_json({"error": "item_id required"}, 400)
            return

        # Hydrate the item once: catches "not found" + provides visibility metadata.
        hydrator = _HYDRATORS.get(item_type)
        if not hydrator:
            self._send_json({"error": "no hydrator"}, 400)
            return
        meta = hydrator(item_id, agent_id, user, team_ids, is_admin)
        if not meta.get("available"):
            self._send_json({"error": "item not found or not accessible"}, 404)
            return

        ok, err = _check_scope_authority(user, scope, scope_id, meta)
        if not ok:
            self._send_json({"error": err}, 403)
            return

        result = FavouritesDB.add(
            scope=scope, scope_id=scope_id,
            item_type=item_type, item_id=item_id, agent_id=agent_id,
            added_by=user["id"], icon=icon, color=color,
        )
        if result is None:
            self._send_json({"error": "insert failed"}, 500)
            return
        if "error" in result:
            self._send_json(result, 400)
            return
        # Echo the hydrated form so the client can render immediately.
        self._send_json({
            **result,
            "title": meta.get("title", ""),
            "subtitle": meta.get("subtitle", ""),
            "updated_at": meta.get("updated_at", result["added_at"]),
            "available": True,
        }, 201)

    # ── DELETE /v1/favourites/<id> ──

    def _handle_favourites_remove(self, path: str):
        try:
            fav_id = int(path.split("/")[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "invalid favourite id"}, 400)
            return
        user, _, is_admin = self._favourites_caller_context()
        row = FavouritesDB.get(fav_id)
        if not row:
            self._send_json({"error": "not found"}, 404)
            return
        if not self._can_modify_favourite(row, user, is_admin):
            self._send_json({"error": "not allowed"}, 403)
            return
        FavouritesDB.remove(fav_id)
        self._send_json({"removed": fav_id})

    # ── DELETE /v1/favourites?scope=...&scope_id=... ──

    def _handle_favourites_remove_bulk(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        scope = (qs.get("scope", [""])[0] or "").strip()
        scope_id = (qs.get("scope_id", [""])[0] or "").strip()
        user, team_ids, is_admin = self._favourites_caller_context()
        if scope not in SCOPES:
            self._send_json({"error": "scope query param required"}, 400)
            return
        if scope == "user" and not scope_id:
            scope_id = user["id"]
        # Authorise: same rules as add() for the empty/general meta.
        if scope == "user" and scope_id != user["id"] and not is_admin:
            self._send_json({"error": "can only clear your own favourites"}, 403)
            return
        if scope == "team":
            if not scope_id:
                self._send_json({"error": "scope_id required"}, 400)
                return
            if not is_admin:
                head = next((t for t in _auth_mod.AuthDB.get_user_teams(user["id"])
                            if t["id"] == scope_id), None)
                if not head or head["head_user_id"] != user["id"]:
                    self._send_json({"error": "only team head can clear team favourites"}, 403)
                    return
        if scope == "general" and not is_admin:
            self._send_json({"error": "admin only"}, 403)
            return
        n = FavouritesDB.remove_bulk(scope, scope_id)
        self._send_json({"removed": n})

    # ── PATCH /v1/favourites/<id> (visual fields only) ──

    def _handle_favourites_patch(self, path: str):
        try:
            fav_id = int(path.split("/")[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "invalid favourite id"}, 400)
            return
        body = self._read_json()
        user, _, is_admin = self._favourites_caller_context()
        row = FavouritesDB.get(fav_id)
        if not row:
            self._send_json({"error": "not found"}, 404)
            return
        if not self._can_modify_favourite(row, user, is_admin):
            self._send_json({"error": "not allowed"}, 403)
            return
        icon = body.get("icon")
        color = body.get("color")
        clear_image = bool(body.get("clear_image"))
        kwargs = {}
        if icon is not None:
            kwargs["icon"] = str(icon)[:64]
        if color is not None:
            kwargs["color"] = str(color)[:16]
        if clear_image:
            # Delete the file too.
            from server_lib.favourites import _safe_delete_image
            if row.get("image_path"):
                _safe_delete_image(row["image_path"])
            kwargs["image_path"] = ""
        updated = FavouritesDB.update_visual(fav_id, **kwargs)
        self._send_json(updated or {})

    # ── POST /v1/favourites/<id>/image (multipart) ──

    def _handle_favourites_image_upload(self, path: str):
        try:
            fav_id = int(path.split("/")[-2])
        except (ValueError, IndexError):
            self._send_json({"error": "invalid favourite id"}, 400)
            return
        user, _, is_admin = self._favourites_caller_context()
        row = FavouritesDB.get(fav_id)
        if not row:
            self._send_json({"error": "not found"}, 404)
            return
        if not self._can_modify_favourite(row, user, is_admin):
            self._send_json({"error": "not allowed"}, 403)
            return

        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
        # Parse boundary
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self._send_json({"error": "missing boundary"}, 400)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_IMAGE_BYTES + 4096:
            self._send_json({"error": "payload too large"}, 413)
            return
        raw = self.rfile.read(length)
        # Quick & dirty multipart split — same approach as projects ingest.
        parts = raw.split(b"--" + boundary.encode())
        filename = ""
        body_bytes = b""
        for p in parts:
            if b"Content-Disposition" not in p:
                continue
            head_end = p.find(b"\r\n\r\n")
            if head_end < 0:
                continue
            head = p[:head_end].decode("latin-1", errors="replace")
            if 'name="file"' not in head:
                continue
            for line in head.split("\r\n"):
                if "filename=" in line:
                    filename = line.split("filename=", 1)[1].strip().strip('";')
                    break
            body_bytes = p[head_end + 4:]
            # Trim trailing CRLF before the next boundary marker.
            if body_bytes.endswith(b"\r\n"):
                body_bytes = body_bytes[:-2]
            break

        if not filename or not body_bytes:
            self._send_json({"error": "missing file"}, 400)
            return
        ext = os.path.splitext(filename)[1] or ""
        if ext.lower() not in ALLOWED_IMAGE_EXTS:
            self._send_json({"error": f"unsupported extension '{ext}'"}, 400)
            return
        if len(body_bytes) > MAX_IMAGE_BYTES:
            self._send_json({"error": "image too large"}, 413)
            return

        # Replace any previous image first.
        if row.get("image_path"):
            from server_lib.favourites import _safe_delete_image
            _safe_delete_image(row["image_path"])

        stored = _save_uploaded_image(fav_id, body_bytes, ext)
        if not stored:
            self._send_json({"error": "save failed"}, 500)
            return
        updated = FavouritesDB.update_visual(fav_id, image_path=stored)
        self._send_json(updated or {})

    # ── GET /v1/favourites/image/<filename> ──

    def _handle_favourites_image_get(self, path: str):
        # Auth gate already enforced by caller. Refuse path traversal.
        name = os.path.basename(path.split("/")[-1])
        if not name or name in (".", ".."):
            self._send_json({"error": "invalid name"}, 400)
            return
        full = os.path.join(IMAGE_DIR, name)
        try:
            real_dir = os.path.realpath(IMAGE_DIR)
            real_full = os.path.realpath(full)
            if not real_full.startswith(real_dir + os.sep):
                self._send_json({"error": "not found"}, 404)
                return
            if not os.path.isfile(real_full):
                self._send_json({"error": "not found"}, 404)
                return
            with open(real_full, "rb") as f:
                blob = f.read()
        except OSError:
            self._send_json({"error": "io error"}, 500)
            return
        ext = os.path.splitext(name)[1].lower()
        ctype = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(blob))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(blob)

    # ── access ──

    def _can_modify_favourite(self, row: dict, user: dict, is_admin: bool) -> bool:
        if is_admin:
            return True
        scope = row.get("scope")
        if scope == "user":
            return row.get("scope_id") == user["id"]
        if scope == "team":
            tid = row.get("scope_id")
            heads = [t for t in _auth_mod.AuthDB.get_user_teams(user["id"])
                     if t["id"] == tid]
            if heads and heads[0]["head_user_id"] == user["id"]:
                return True
            # Members can only remove their own contributions.
            return row.get("added_by") == user["id"]
        if scope == "general":
            return False  # admin-only enforced above
        return False
