# Extracted from server.py — project, notes, ingest handlers
import datetime
import json
import os
import sys
import tempfile

from server_lib import auth as _auth_mod
from server_lib import pathsafe
import brain as engine


def _srv():
    """Return the server module — works whether the process is __main__ or imported."""
    return sys.modules.get("__main__") or sys.modules["server"]


def _filter_known_user_ids(ids):
    """Filter a list of user IDs to only those that exist in the auth DB."""
    if not ids or not isinstance(ids, list):
        return []
    return [uid for uid in ids if _auth_mod.AuthDB.get_user(uid)]


class ProjectsHandlerMixin:
    """Mixin providing project, notes, and ingest HTTP handlers."""

    def _handle_list_projects(self, path: str):
        """GET /v1/agents/{id}/projects"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        # Multi-user: filter projects by user access
        auth_user = getattr(self, '_auth_user', None)
        user_id = None
        user_team_ids = None
        if auth_user and auth_user["id"] != "__system__" and auth_user["role"] != "admin":
            user_id = auth_user["id"]
            user_team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(auth_user["id"])]
        projects = engine.ProjectManager.list_projects(agent_id, user_id=user_id, user_team_ids=user_team_ids)
        self._send_json({"agent": agent_id, "projects": projects})

    def _handle_create_project(self, path: str):
        """POST /v1/agents/{id}/projects"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Project name is required"}, 400)
            return
        auth_user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        is_system = auth_user["id"] == "__system__"
        is_admin = auth_user["role"] == "admin" or is_system
        # Owner: admin can pick anyone; others are forced to self.
        requested_owner = (body.get("owner_user_id") or "").strip()
        if is_admin and requested_owner:
            if not _auth_mod.AuthDB.get_user(requested_owner):
                self._send_json({"error": "Unknown owner_user_id"}, 400)
                return
            owner_uid = requested_owner
        else:
            owner_uid = auth_user["id"] if not is_system else (requested_owner or "")
        visibility = _auth_mod.normalize_visibility(body.get("visibility", "global" if is_admin else "private"))
        owner_tid = body.get("owner_team_id", "")
        # Scope-based gating
        if visibility == "global" and not is_admin:
            self._send_json({"error": "Only admins can create global projects"}, 403)
            return
        if visibility == "team":
            if not owner_tid:
                self._send_json({"error": "owner_team_id required for team-scoped project"}, 400)
                return
            if not is_admin:
                # Caller must be head of that team
                my_teams = _auth_mod.AuthDB.get_user_teams(auth_user["id"])
                if not any(t["id"] == owner_tid and t["head_user_id"] == auth_user["id"] for t in my_teams):
                    self._send_json({"error": "Only the team head can create team-scoped projects"}, 403)
                    return
        elif visibility not in ("private", "users", "global"):
            self._send_json({"error": f"Invalid visibility '{visibility}'"}, 400)
            return
        # Member lists: validated against existing users
        extras = _filter_known_user_ids(body.get("extra_member_user_ids"))
        excluded = _filter_known_user_ids(body.get("excluded_user_ids")) if visibility == "global" else []
        result = engine.ProjectManager.create_project(
            agent_id, name,
            description=body.get("description", ""),
            config={**body, "extra_member_user_ids": extras, "excluded_user_ids": excluded},
            visibility=visibility,
            owner_user_id=owner_uid,
            owner_team_id=owner_tid,
        )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result, 201)

    def _session_access_check(self, sid: str, *, require_manage: bool = False):
        """Load session metadata and verify the caller can access it.
        Returns the session info dict on success; sends 403/404 and returns None on fail.
        `require_manage` gates mutations through the generic can_manage (owner
        or admin — no team-head shortcut, matching the project model)."""
        from server_lib.db import ChatDB, session_share_block
        info = ChatDB.get_session_info(sid)
        if not info:
            self._send_json({"error": "Session not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        block = session_share_block(info)
        # Admin bypass
        if user["role"] == "admin" or user["id"] == "__system__":
            return info
        # Legacy anonymous sessions (no owner): keep the pre-ownership
        # behaviour — readable AND manageable by anyone authed until adopted.
        if not block.get("owner_user_id"):
            return info
        if not _auth_mod.can_access(user, block):
            self._send_json({"error": "Access to this session is not permitted"}, 403)
            return None
        if require_manage and not _auth_mod.can_manage(user, block):
            self._send_json({"error": "Only the chat owner (or admin) can modify"}, 403)
            return None
        return info

    def _project_access_check(self, agent_id: str, proj_name: str, *, require_manage: bool = False):
        """Load project.json, enforce visibility/ownership. Returns project dict on success,
        None after sending 403/404. If require_manage is True, only admin or owner (user or
        team head) can pass — used for PUT/DELETE/ingest/notes-write."""
        project = engine.ProjectManager.get_project(agent_id, proj_name)
        if not project:
            self._send_json({"error": f"Project '{proj_name}' not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_project(user, project):
            self._send_json({"error": "Access to this project is not permitted"}, 403)
            return None
        if require_manage:
            if not _auth_mod.can_manage_project(user, project):
                self._send_json({"error": "Only the project owner (or admin) can modify this project"}, 403)
                return None
        return project

    def _handle_project_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        self._send_json(project)

    def _handle_project_update(self, path: str):
        """PUT /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json()
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        is_admin = user["role"] == "admin" or user["id"] == "__system__"
        # Visibility + team scope are admin-only.
        if not is_admin:
            for locked in ("visibility", "owner_team_id"):
                body.pop(locked, None)
        # Ownership transfer: owner or admin may transfer; new owner must exist.
        if "owner_user_id" in body:
            new_owner = (body.get("owner_user_id") or "").strip()
            if new_owner and not _auth_mod.AuthDB.get_user(new_owner):
                self._send_json({"error": "Unknown owner_user_id"}, 400)
                return
            body["owner_user_id"] = new_owner
        # Validate member lists against the auth DB.
        if "extra_member_user_ids" in body:
            body["extra_member_user_ids"] = _filter_known_user_ids(body["extra_member_user_ids"])
        if "excluded_user_ids" in body:
            # Only meaningful for global; for non-global scopes, drop silently.
            scope = body.get("visibility", project.get("visibility", "global"))
            body["excluded_user_ids"] = (
                _filter_known_user_ids(body["excluded_user_ids"]) if scope == "global" else []
            )
        # Project-level web URLs (fetched + injected into every chat turn in
        # the project; no MemPalace/KG involvement). Normalise to a clean
        # [{url,title}] list so a malformed body can't corrupt project.json.
        if "web_urls" in body:
            _clean = []
            _seen = set()
            for u in (body.get("web_urls") or []):
                if not isinstance(u, dict):
                    continue
                url = (u.get("url") or "").strip()
                if not url or url in _seen:
                    continue
                if not url.lower().startswith(("http://", "https://")):
                    url = "https://" + url
                _seen.add(url)
                _clean.append({"url": url, "title": (u.get("title") or "").strip()})
            body["web_urls"] = _clean
        result = engine.ProjectManager.update_project(agent_id, proj_name, body)
        if "error" in result:
            self._send_json(result, 400)
        else:
            # Auto-kick a sync when the SOURCE set changed (web_urls / input
            # folders are mined into the project wing+KG). Without this, newly
            # added URLs/folders waited up to the 6h scheduled cycle — the user
            # added sources from Research and saw nothing happen.
            # Also kick when the per-project KG method/profile changed:
            # update_project just purged this project's KG cursor, so a sync
            # is needed to re-extract under the new setting (otherwise the
            # switch shows no effect until the next scheduled cycle — the
            # rules→llm "still only 4 triples" surprise).
            if ("web_urls" in body or "input_folders" in body
                    or "kg_method" in body or "kg_profile" in body):
                try:
                    _srv()._project_sync_request(agent_id, proj_name)
                except Exception:
                    pass
            self._send_json(result)

    def _handle_project_image_upload(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/image — multipart upload.
        Stores at agents/<agent>/projects/<name>/.image.<ext> and records the
        relative basename in project.json so list_projects() exposes it.
        """
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return

        from server_lib.favourites import MAX_IMAGE_BYTES, ALLOWED_IMAGE_EXTS

        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
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
            if body_bytes.endswith(b"\r\n"):
                body_bytes = body_bytes[:-2]
            break

        if not filename or not body_bytes:
            self._send_json({"error": "missing file"}, 400)
            return
        ext = os.path.splitext(filename)[1].lower() or ""
        if ext not in ALLOWED_IMAGE_EXTS:
            self._send_json({"error": f"unsupported extension '{ext}'"}, 400)
            return
        if len(body_bytes) > MAX_IMAGE_BYTES:
            self._send_json({"error": "image too large"}, 413)
            return

        pdir = engine.ProjectManager._project_dir(agent_id, proj_name)
        # Remove any prior .image.* file regardless of extension.
        try:
            for entry in os.listdir(pdir):
                if entry.startswith(".image.") and os.path.isfile(os.path.join(pdir, entry)):
                    try:
                        os.unlink(os.path.join(pdir, entry))
                    except OSError:
                        pass
        except OSError:
            pass

        full = os.path.join(pdir, f".image{ext}")
        try:
            with open(full, "wb") as f:
                f.write(body_bytes)
        except OSError as e:
            self._send_json({"error": f"save failed: {e}"}, 500)
            return

        # Record the basename in project.json so list/get expose image_url.
        engine.ProjectManager.update_project(agent_id, proj_name, {"image": f".image{ext}"})
        self._send_json({"status": "ok", "image": f".image{ext}"})

    def _handle_project_image_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/image"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        pdir = engine.ProjectManager._project_dir(agent_id, proj_name)
        try:
            for entry in os.listdir(pdir):
                if entry.startswith(".image.") and os.path.isfile(os.path.join(pdir, entry)):
                    try:
                        os.unlink(os.path.join(pdir, entry))
                    except OSError:
                        pass
        except OSError:
            pass
        engine.ProjectManager.update_project(agent_id, proj_name, {"image": ""})
        self._send_json({"status": "ok"})

    def _handle_project_image_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/image — serve the stored image."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        image_name = (project.get("image") or "").strip()
        if not image_name or not image_name.startswith(".image."):
            self._send_json({"error": "no image"}, 404)
            return
        pdir = engine.ProjectManager._project_dir(agent_id, proj_name)
        full = os.path.join(pdir, image_name)
        try:
            real_dir = os.path.realpath(pdir)
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
        ext = os.path.splitext(image_name)[1].lower()
        ctype = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(blob))
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(blob)

    def _handle_project_code_chats(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/code-chats — list the project's
        code-mode terminal-chats (sessions with status='code_chat'). These are
        kept OUT of the normal project/sidebar chat lists (db.list_sessions
        excludes the status by default) and surfaced only here, under the
        "Terminal-Chats" section of the code-mode bottom workspace."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        pid = project.get("id") or ""
        # Same visibility scoping the normal session list uses.
        auth_user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        visible = _auth_mod.get_visible_user_ids(auth_user)
        vteam = None
        caller_uid = None
        if visible is not None:
            vteam = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(auth_user["id"])]
            caller_uid = auth_user["id"]
        chats = ChatDB.list_sessions(
            agent_id=agent_id, status="code_chat",
            project=proj_name, project_id=pid or None,
            visible_user_ids=visible, visible_team_ids=vteam,
            caller_user_id=caller_uid)
        self._send_json({"sessions": chats})

    def _handle_project_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        result = engine.ProjectManager.delete_project(agent_id, proj_name)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)

    # ── Project Input Folders + Sync ──────────────────────────────────────
    # Adds user-selected on-disk folders to a project. The mempalace-project-sync
    # daemon scans them on a hourly poll and files everything into the project's
    # private MemPalace wing (`project__<name>--<agent>`).

    def _project_input_folder_validate(self, raw: str) -> tuple:
        """Return (resolved_path, error). Refuses non-dirs, brain agents/, and
        obviously sensitive system dirs.

        Skeleton in server_lib.pathsafe; this site's policy = no allowed-roots
        allowlist (any dir not denied is fine), deny the agents/ tree, require
        an existing directory, expanduser. Adapts the shared tuple's None to ""
        to keep this method's historical ("", error) contract for callers.
        """
        rp, err = pathsafe.validate_path(
            raw,
            allowed_roots=None,
            deny_agents_dir=engine.AGENTS_DIR,
            must_be_dir=True,
            expand_user=True,
        )
        if err:
            return "", err
        return rp, ""

    def _handle_project_input_folders_list(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/input-folders"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        folders = project.get("input_folders") or []
        self._send_json({
            "agent": agent_id,
            "project": proj_name,
            "folders": folders,
            "last_scan": project.get("input_folders_last_scan", ""),
        })

    def _handle_project_input_folders_add(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/input-folders
        Body: {path: "/abs/path", recursive: bool}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json() or {}
        resolved, err = self._project_input_folder_validate(body.get("path", ""))
        if err:
            self._send_json({"error": err}, 400)
            return
        recursive = bool(body.get("recursive", True))
        # auto_sync gates whether the scheduled daemon picks this folder up;
        # default true preserves prior behavior. Folders with auto_sync=false
        # still run on explicit "Sync now" so the user can refresh on demand.
        auto_sync = bool(body.get("auto_sync", True))
        folders = list(project.get("input_folders") or [])
        # Dedup by resolved path
        for entry in folders:
            if os.path.realpath(entry.get("path", "")) == resolved:
                self._send_json({"error": "Folder already added"}, 400)
                return
        folders.append({
            "path": resolved,
            "recursive": recursive,
            "auto_sync": auto_sync,
            "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        engine.ProjectManager.update_project(agent_id, proj_name, {
            "input_folders": folders,
        })
        # Wake the project-sync daemon so the user sees activity immediately.
        try:
            _srv()._project_sync_wakeup.set()
        except Exception:
            pass
        # Auto-run the GDPR/classification review synchronously so the tree
        # badges are correct on first render (bounded; the daemon refreshes the
        # remainder on its re-mine pass). Best-effort — never fails the add.
        reviewed = 0
        try:
            reviewed = self._auto_review_folder(
                user=getattr(self, "_auth_user", None) or {},
                folder=resolved, recursive=recursive)
        except Exception as _e:
            print(f"[data_review] folder auto-review failed: {_e}", flush=True)
        self._send_json({"status": "added", "folders": folders,
                         "reviewed": reviewed})

    def _handle_project_input_folders_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/input-folders/{idx}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        # /input-folders/<idx> — last segment is the index
        try:
            idx = int(path.rstrip("/").rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "Invalid folder index"}, 400)
            return
        folders = list(project.get("input_folders") or [])
        if idx < 0 or idx >= len(folders):
            self._send_json({"error": "Folder index out of range"}, 404)
            return
        removed = folders.pop(idx)
        engine.ProjectManager.update_project(agent_id, proj_name, {
            "input_folders": folders,
        })
        try:
            _srv()._project_sync_wakeup.set()
        except Exception:
            pass
        self._send_json({"status": "removed", "folder": removed, "folders": folders})

    def _handle_project_input_folders_update(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/input-folders/{idx}
        Body: {path?: str, recursive?: bool, auto_sync?: bool}
        Partial update — only fields present in the body are touched. Path
        change goes through the same validation as add, so dedup against
        other entries is enforced."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        try:
            idx = int(path.rstrip("/").rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "Invalid folder index"}, 400)
            return
        folders = list(project.get("input_folders") or [])
        if idx < 0 or idx >= len(folders):
            self._send_json({"error": "Folder index out of range"}, 404)
            return
        body = self._read_json() or {}
        entry = dict(folders[idx])
        old_path = entry.get("path", "")
        # Path change is the only field that can fail validation; do it first
        # so we don't write a half-updated row on error.
        path_changed = False
        if "path" in body:
            resolved, err = self._project_input_folder_validate(body.get("path", ""))
            if err:
                self._send_json({"error": err}, 400)
                return
            for j, other in enumerate(folders):
                if j == idx:
                    continue
                if os.path.realpath(other.get("path", "")) == resolved:
                    self._send_json({"error": "Folder already added"}, 400)
                    return
            if os.path.realpath(old_path) != resolved:
                path_changed = True
            entry["path"] = resolved
        if "recursive" in body:
            entry["recursive"] = bool(body.get("recursive"))
        if "auto_sync" in body:
            entry["auto_sync"] = bool(body.get("auto_sync"))
        folders[idx] = entry
        engine.ProjectManager.update_project(agent_id, proj_name, {
            "input_folders": folders,
        })
        try:
            _srv()._project_sync_wakeup.set()
        except Exception:
            pass
        self._send_json({"status": "updated", "folder": entry, "folders": folders})

    def _handle_project_sync_status(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/sync-status"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        st = project.get("sync_status") or {}
        # Layer in live state from the daemon (running flag) so the UI reflects
        # the actual sync currently happening, not just the last persisted row.
        try:
            live = _srv()._project_sync_live_status(agent_id, proj_name)
            if live:
                st = {**st, **live}
        except Exception:
            pass
        # Surface the daemon's polling interval + a derived next-run timestamp
        # so the chip can show "next sync in 4h" without a second config fetch.
        # next_run_at is best-effort: if last_run_finished is missing (project
        # never synced) we leave it empty and the UI shows "soon".
        try:
            interval_s = int((((engine._load_mempalace_config() or {}).get(
                "project_sync") or {}).get("interval_seconds", 21600)))
        except Exception:
            interval_s = 21600
        next_run_at = ""
        last_finished = st.get("last_run_finished") or ""
        if last_finished:
            try:
                # Stored as ISO with timezone; parse, add interval, re-emit.
                dt = datetime.datetime.fromisoformat(last_finished)
                next_run_at = (dt + datetime.timedelta(
                    seconds=interval_s)).isoformat()
            except Exception:
                next_run_at = ""
        self._send_json({
            "agent": agent_id,
            "project": proj_name,
            "status": st,
            "last_scan": project.get("input_folders_last_scan", ""),
            "interval_seconds": interval_s,
            "next_run_at": next_run_at,
        })

    def _handle_project_sync_now(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/sync-now — kick the daemon
        to scan this project on its next tick. Returns immediately; the UI
        polls /sync-status to watch progress."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        try:
            _srv()._project_sync_request(agent_id, proj_name)
        except Exception:
            pass
        self._send_json({"status": "queued", "agent": agent_id, "project": proj_name})

    # ── Code-mode index (codebase-memory) ───────────────────────────────────
    def _code_cache_dir(self, agent_id: str, proj_name: str, project: dict) -> str:
        import os as _os
        pdir = project.get("dir") or engine.ProjectManager._project_dir(agent_id, proj_name)
        return _os.path.join(pdir, ".cbm-cache")

    def _handle_code_index_status(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/code-index/status — per-file index
        state (indexed/stale/not_indexed) + project node/edge counts + live daemon
        state. Code-mode projects only."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        wd = (project.get("working_dir") or "").strip()
        cache = self._code_cache_dir(agent_id, proj_name, project)
        try:
            state = engine.cbm_per_file_state(wd, cache) if wd else {"indexed": False, "files": {}}
        except Exception as e:
            state = {"indexed": False, "files": {}, "error": str(e)}
        try:
            import server_daemons
            live = server_daemons._code_index_status(agent_id, proj_name) or {}
        except Exception:
            live = {}
        self._send_json({"agent": agent_id, "project": proj_name,
                         "code_mode": bool(project.get("code_mode")),
                         "working_dir": wd, "live": live, **state})

    def _handle_code_index_refresh(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/code-index/refresh — queue a
        re-index of the working dir (incremental). UI polls status."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        try:
            import server_daemons
            server_daemons._code_index_request(agent_id, proj_name)
        except Exception:
            pass
        self._send_json({"status": "queued", "agent": agent_id, "project": proj_name})

    def _handle_code_index_rebuild(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/code-index/rebuild — clean & start
        fresh: drop the tenant cache, then re-index. Admin/manage only."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        try:
            import server_daemons
            server_daemons._code_index_request(agent_id, proj_name, force=True)
        except Exception:
            pass
        self._send_json({"status": "rebuilding", "agent": agent_id, "project": proj_name})

    # ── Code-mode interactive terminal (PTY over SSE) ────────────────────────
    def _terminal_project_ctx(self, path):
        """Resolve + access-check a code-mode project from the path. Returns
        (agent_id, proj_name, working_dir) or None (after sending the error)."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return None
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return None
        if not project.get("code_mode"):
            self._send_json({"error": "Terminal nur für Code-Mode-Projekte"}, 400)
            return None
        wd = (project.get("working_dir") or "").strip()
        if not wd or not os.path.isdir(wd):
            self._send_json({"error": "Kein gültiges Arbeitsverzeichnis"}, 400)
            return None
        return agent_id, proj_name, wd

    def _handle_terminal_list(self, path: str):
        """GET .../terminal/sessions — list live sessions for this project."""
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        agent_id, proj_name, _ = ctx
        from server_lib.terminal import terminal_manager
        self._send_json({"sessions": terminal_manager.list(agent_id, proj_name)})

    def _handle_terminal_create(self, path: str):
        """POST .../terminal/sessions — start a new PTY session in working_dir."""
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        agent_id, proj_name, wd = ctx
        from server_lib.terminal import terminal_manager
        try:
            sess = terminal_manager.create(agent_id, proj_name, wd)
        except Exception as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json(sess.info())

    def _handle_terminal_close(self, path: str, sid: str):
        """POST .../terminal/sessions/<sid>/close — kill a session."""
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        from server_lib.terminal import terminal_manager
        ok = terminal_manager.close(sid)
        self._send_json({"closed": ok})

    def _handle_terminal_input(self, path: str, sid: str):
        """POST .../terminal/sessions/<sid>/input {data|resize} — keystrokes or
        a {rows,cols} resize."""
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        from server_lib.terminal import terminal_manager
        sess = terminal_manager.get(sid)
        if not sess:
            self._send_json({"error": "Sitzung nicht gefunden"}, 404)
            return
        body = self._read_json() or {}
        if "rows" in body and "cols" in body:
            try:
                sess.resize(int(body["rows"]), int(body["cols"]))
            except (TypeError, ValueError):
                pass
        data = body.get("data")
        if data is not None:
            sess.write(data.encode("utf-8") if isinstance(data, str) else bytes(data))
        self._send_json({"ok": True})

    def _handle_terminal_run(self, path: str):
        """POST .../terminal/run {command, timeout?} — run a ONE-SHOT shell
        command in the project's working_dir and return {exit_code, output}.

        Backs the terminal-chat `!` command (e.g. `! python forecast.py --region=X`).
        NOT a PTY: no streaming, no stdin/TTY — a request/response exec with the
        same login-shell build + banned-command guard + timeout as the
        execute_command tool, scoped to the code-mode working_dir."""
        import subprocess
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        _agent_id, _proj_name, wd = ctx
        body = self._read_json() or {}
        command = str(body.get("command") or "").strip()
        if not command:
            self._send_json({"error": "Kein Befehl"}, 400)
            return
        # Reuse the execute_command config: banned patterns + default timeout +
        # the login-shell builder (sources the user's profile → full PATH).
        exec_cfg = engine.get_tool_config().get("execute_command", {})
        for b in (exec_cfg.get("banned_commands", []) or []):
            if b and b in command:
                self._send_json({"error": f"Befehl enthält verbotenes Muster '{b}'"}, 400)
                return
        try:
            timeout = int(body.get("timeout") or exec_cfg.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(1, min(timeout, 300))
        from engine.tools.file_tools import _build_shell_command, _strip_ansi
        shell_cmd, shell_flag = _build_shell_command(command)
        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "PAGER": "cat",
                    "COLUMNS": "200", "LINES": "50"})
        try:
            proc = subprocess.run(
                shell_cmd, shell=shell_flag, cwd=wd, env=env,
                stdin=subprocess.DEVNULL, capture_output=True,
                timeout=timeout, start_new_session=True)
        except subprocess.TimeoutExpired as e:
            partial = _strip_ansi((e.stdout or b"").decode("utf-8", "replace"))
            self._send_json({"command": command, "exit_code": -1, "timed_out": True,
                             "output": partial + f"\n--- Zeitüberschreitung nach {timeout}s ---"})
            return
        except Exception as e:
            self._send_json({"error": str(e)}, 400)
            return
        out = _strip_ansi(proc.stdout.decode("utf-8", "replace"))
        err = _strip_ansi(proc.stderr.decode("utf-8", "replace"))
        output = out
        if err:
            output += ("\n--- stderr ---\n" + err) if output else err
        if len(output) > 50000:
            output = output[:50000] + "\n... (gekürzt)"
        self._send_json({"command": command, "exit_code": proc.returncode, "output": output})

    def _handle_terminal_stream(self, path: str, sid: str):
        """GET .../terminal/sessions/<sid>/stream?since=N — SSE stream of PTY
        output bytes (base64 frames) from absolute offset N."""
        ctx = self._terminal_project_ctx(path)
        if not ctx:
            return
        from server_lib.terminal import terminal_manager
        import base64
        from urllib.parse import urlparse, parse_qs
        sess = terminal_manager.get(sid)
        if not sess:
            self._send_json({"error": "Sitzung nicht gefunden"}, 404)
            return
        qs = parse_qs(urlparse(self.path).query)
        try:
            offset = int(qs.get("since", ["0"])[0])
        except (TypeError, ValueError):
            offset = 0
        try:
            import socket as _sock
            self.connection.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # SSE close-after-terminal rule (9.277.0, same as chat SSE): without
        # an explicit close the client never sees end-of-response (SSE has no
        # content framing) and each stream leaks a server thread + socket.
        self.close_connection = True
        ev = sess.subscribe()
        try:
            # initial replay from `since`
            while True:
                chunk, offset = sess.read_since(offset)
                if chunk:
                    frame = base64.b64encode(chunk).decode("ascii")
                    self.wfile.write(f"event: out\ndata: {frame}\n\n".encode())
                    self.wfile.flush()
                if sess._closed:
                    self.wfile.write(b"event: closed\ndata: {}\n\n")
                    self.wfile.flush()
                    break
                # wait for more (or keepalive every 5s)
                if not ev.wait(timeout=5.0):
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                ev.clear()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sess.unsubscribe(ev)

    def _handle_code_index_history(self, path: str):
        """GET .../code-index/history — last N index runs (state/time/duration/
        trigger/nodes/edges), newest first."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        try:
            import server_daemons
            runs = server_daemons._code_index_runs(agent_id, proj_name)
        except Exception:
            runs = []
        self._send_json({"agent": agent_id, "project": proj_name, "runs": runs})

    def _handle_code_index_symbols(self, path: str):
        """GET .../code-index/symbols — editor-support lookups over the code
        index, dispatched by query param:
          ?q=<text>          → fuzzy symbol search (BM25): name/label/file/line
                               (symbol palette + autocomplete)
          ?callers=<symbol>  → inbound callers of a symbol (who-calls)
          ?def=<symbol>      → definition + signature/docstring/caller counts
                               (go-to-definition + hover)
          ?cypher=<query>    → raw read-only Cypher → {columns, rows}
                               (power-user search bar)
        file paths from ?q are repo-RELATIVE; the frontend joins working_dir.
        Code-mode projects only."""
        from urllib.parse import urlparse, parse_qs
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        cache = self._code_cache_dir(agent_id, proj_name, project)
        qs = parse_qs(urlparse(self.path).query)
        q = (qs.get("q", [""])[0] or "").strip()
        callers = (qs.get("callers", [""])[0] or "").strip()
        defn = (qs.get("def", [""])[0] or "").strip()
        cypher = (qs.get("cypher", [""])[0] or "").strip()
        outline = (qs.get("outline", [""])[0] or "").strip()
        usages = (qs.get("usages", [""])[0] or "").strip()
        sql_meta = (qs.get("sql_meta", [""])[0] or "").strip()
        sql_id = (qs.get("sql", [""])[0] or "").strip()
        r_meta = (qs.get("r_meta", [""])[0] or "").strip()
        r_id = (qs.get("r", [""])[0] or "").strip()
        wd = (project.get("working_dir") or "").strip()
        try:
            limit = int(qs.get("limit", ["30"])[0])
        except (TypeError, ValueError):
            limit = 30
        try:
            if sql_meta:
                # Whether to offer SQL analyses + the card list (frontend gates on this).
                out = {"has_sql": engine.cbm_project_has_sql(wd),
                       "analyses": engine.cbm_sql_analyses_meta()}
            elif sql_id:
                out = engine.cbm_sql_analyze(sql_id, wd)
            elif r_meta:
                # Whether to offer R analyses + the card list (frontend gates on this).
                out = {"has_r": engine.cbm_project_has_r(wd),
                       "analyses": engine.cbm_r_analyses_meta()}
            elif r_id:
                out = engine.cbm_r_analyze(r_id, wd)
            elif outline:
                out = engine.cbm_code_outline(cache)
            elif usages:
                out = engine.cbm_code_usages(usages, cache, working_dir=wd)
            elif cypher:
                out = engine.cbm_code_query_raw(cypher, cache)
            elif defn:
                out = engine.cbm_code_def(defn, cache)
            elif callers:
                out = engine.cbm_code_callers(callers, cache)
            else:
                out = engine.cbm_code_symbols(q, cache, limit=limit)
        except Exception as e:
            out = {"error": str(e)}
        self._send_json({"agent": agent_id, "project": proj_name,
                         "working_dir": (project.get("working_dir") or "").strip(),
                         **out})

    def _handle_code_index_graph(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/code-index/graph — architecture /
        graph-view payload for the code index."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        cache = self._code_cache_dir(agent_id, proj_name, project)
        try:
            data = engine.cbm_graph_overview(cache)
        except Exception as e:
            data = {"indexed": False, "error": str(e)}
        self._send_json({"agent": agent_id, "project": proj_name, **data})

    def _handle_project_full_resync(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/full-resync — wipe all
        MemPalace drawers, KG triples, and all sync cursors for this project,
        then queue a fresh sync. Admin only."""
        user = self._require_role("admin")
        if user is None:
            return
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "Project missing id"}, 400)
            return
        wing = f"project__{pid}"
        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")

        import time as _time
        from engine import sync_log as _sync_log

        result: dict = {}
        purge_run_id = _sync_log.start_run(
            chats_db, pid, triggered_by="full_resync_purge")
        purge_actions: list[dict] = []

        def _pa(action: str, **fields):
            purge_actions.append({"action": action, "at": _time.time(), **fields})

        try:
            # 1. Wipe all drawers in the project wing.
            t0 = _time.time()
            try:
                deleted = _srv()._mp.purge_by_prefix(wing=wing, prefix="")
                result["drawers_deleted"] = deleted
                _pa("drawers_purged", deleted=deleted,
                    elapsed_s=round(_time.time() - t0, 2))
            except Exception as e:
                result["drawers_error"] = str(e)
                _pa("drawers_purged", deleted=0, error=str(e),
                    elapsed_s=round(_time.time() - t0, 2))

            # 2. Wipe KG triples + extraction cursors for all prefixes.
            try:
                from engine import kg_extract
                pdir = project.get("dir") or os.path.join(
                    engine.AGENTS_DIR, agent_id, "projects", proj_name)
                prefixes = []
                for p in [pdir] + [
                    (f.get("path") or "") for f in (project.get("input_folders") or [])
                ]:
                    if p:
                        try:
                            r = os.path.realpath(p)
                        except OSError:
                            r = p
                        prefixes.append(r.rstrip(os.sep) + os.sep)
                triples_del = 0
                progress_del = 0
                t0 = _time.time()
                for prefix in prefixes:
                    r = kg_extract.kg_purge_for_scope(
                        palace_path=palace_path,
                        source_prefix=prefix,
                        adapter_name="brain-project-kg",
                        chats_db_path=chats_db,
                        wing=wing,
                    )
                    triples_del += int(r.get("triples_deleted", 0))
                    progress_del += int(r.get("progress_deleted", 0))
                result["triples_deleted"] = triples_del
                result["kg_progress_deleted"] = progress_del
                _pa("kg_triples_purged",
                    triples_deleted=triples_del,
                    progress_cursors_deleted=progress_del,
                    prefixes_count=len(prefixes),
                    elapsed_s=round(_time.time() - t0, 2))

                # 3. Wipe closet regen cursor.
                t0 = _time.time()
                kg_extract.closet_regen_purge_for_scope(
                    chats_db_path=chats_db,
                    palace_wing=wing,
                )
                result["closet_cursor_cleared"] = True
                _pa("closet_cursor_cleared",
                    elapsed_s=round(_time.time() - t0, 2))

                # 4. Wipe doc-convert mtime/size cache by clearing the
                #    .brain-extracted dirs so everything is re-converted.
                import shutil
                converted_cleared = 0
                cleared_files = 0
                t0 = _time.time()
                for entry in [pdir] + [
                    (f.get("path") or "") for f in (project.get("input_folders") or [])
                ]:
                    if not entry:
                        continue
                    extracted = os.path.join(entry, ".brain-extracted")
                    if os.path.isdir(extracted):
                        try:
                            for _root, _dirs, _files in os.walk(extracted):
                                cleared_files += len(_files)
                        except OSError:
                            pass
                        shutil.rmtree(extracted, ignore_errors=True)
                        converted_cleared += 1
                result["brain_extracted_cleared"] = converted_cleared
                _pa("doc_convert_cache_cleared",
                    dirs_removed=converted_cleared,
                    files_removed=cleared_files,
                    elapsed_s=round(_time.time() - t0, 2))

            except Exception as e:
                result["kg_error"] = str(e)
                _pa("kg_purge_error", error=str(e))

            # Persist all purge actions into the run log, then close it.
            _sync_log.log_purge_actions(chats_db, purge_run_id, purge_actions)
            _sync_log.finish_run(chats_db, purge_run_id, "idle", {
                "drawers_deleted": result.get("drawers_deleted", 0),
                "triples_deleted": result.get("triples_deleted", 0),
                "kg_progress_deleted": result.get("kg_progress_deleted", 0),
                "closet_cursor_cleared": result.get("closet_cursor_cleared", False),
                "brain_extracted_cleared": result.get("brain_extracted_cleared", 0),
                "errors": [v for k, v in result.items() if k.endswith("_error")],
            })

            # 5. Queue a fresh sync-now.
            try:
                _srv()._project_sync_request(agent_id, proj_name,
                                             triggered_by="full_resync")
                result["sync_queued"] = True
            except Exception:
                result["sync_queued"] = False

        except Exception as e:
            _sync_log.finish_run(chats_db, purge_run_id, "error", {"error": str(e)})
            self._send_json({"error": str(e)}, 500)
            return

        self._send_json({"status": "full_resync_queued", **result})

    def _handle_project_sync_runs(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/sync-runs?limit=20 — list runs."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"runs": []})
            return
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(path).query)
        limit = int((qs.get("limit") or ["20"])[0])
        chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
        from engine import sync_log as _sync_log
        runs = _sync_log.get_runs(chats_db, pid, limit=limit)
        self._send_json({"runs": runs})

    def _handle_project_sync_run_detail(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/sync-runs/{run_id}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        # Extract run_id from path: .../sync-runs/<id>
        parts = path.split("/sync-runs/", 1)
        if len(parts) < 2:
            self._send_json({"error": "Missing run id"}, 400)
            return
        try:
            run_id = int(parts[1].split("?")[0].rstrip("/"))
        except ValueError:
            self._send_json({"error": "Invalid run id"}, 400)
            return
        chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
        from engine import sync_log as _sync_log
        run = _sync_log.get_run(chats_db, run_id)
        if not run:
            self._send_json({"error": "Run not found"}, 404)
            return
        self._send_json({"run": run})

    def _handle_project_sync_cancel(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/sync-cancel"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "Project missing id"}, 400)
            return
        try:
            _srv()._project_sync_cancel_request(pid)
        except Exception:
            pass
        self._send_json({"status": "cancel_requested", "project_id": pid})

    def _handle_notes(self, path: str, method: str):
        """Handle notes CRUD: /v1/agents/{id}/projects/{name}/notes[/{path...}]"""
        from urllib.parse import unquote
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return

        # ACL: GET requires read access; writes require manage access
        require_manage = method in ("POST", "PUT", "DELETE")
        if self._project_access_check(agent_id, proj_name, require_manage=require_manage) is None:
            return

        # Extract note path: everything after /notes/ (or empty for list)
        # URL pattern: /v1/agents/{id}/projects/{name}/notes[/{path...}]
        parts = path.split("/notes", 1)
        note_path = ""
        if len(parts) > 1:
            note_path = unquote(parts[1].lstrip("/"))

        if method == "GET":
            if not note_path:
                # List all notes
                notes = engine.NoteManager.list_notes(agent_id, proj_name)
                self._send_json({"agent": agent_id, "project": proj_name, "notes": notes})
            else:
                # Get single note
                note = engine.NoteManager.get_note(agent_id, proj_name, note_path)
                if not note:
                    self._send_json({"error": f"Note '{note_path}' not found"}, 404)
                else:
                    self._send_json(note)

        elif method == "POST":
            body = self._read_json()
            note_path = body.get("path", note_path)
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            # Ensure .md extension
            if not note_path.endswith(".md"):
                note_path += ".md"
            content = body.get("content", "")
            action = body.get("action", "")
            if action == "create_folder":
                folder_path = body.get("folder_path", "")
                if not folder_path:
                    self._send_json({"error": "folder_path is required"}, 400)
                    return
                result = engine.NoteManager.create_folder(agent_id, proj_name, folder_path)
                self._send_json(result)
            elif action == "rename":
                new_path = body.get("new_path", "")
                if not new_path:
                    self._send_json({"error": "new_path is required"}, 400)
                    return
                if not new_path.endswith(".md"):
                    new_path += ".md"
                result = engine.NoteManager.rename_note(agent_id, proj_name, note_path, new_path)
                if "error" in result:
                    self._send_json(result, 400)
                else:
                    self._send_json(result)
            else:
                result = engine.NoteManager.create_note(agent_id, proj_name, note_path, content)
                if "error" in result:
                    self._send_json(result, 409)
                else:
                    self._send_json(result, 201)

        elif method == "PUT":
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            body = self._read_json()
            content = body.get("content", "")
            result = engine.NoteManager.update_note(agent_id, proj_name, note_path, content)
            if "error" in result:
                self._send_json(result, 404)
            else:
                self._send_json(result)

        elif method == "DELETE":
            if not note_path:
                self._send_json({"error": "Note path is required"}, 400)
                return
            result = engine.NoteManager.delete_note(agent_id, proj_name, note_path)
            if "error" in result:
                self._send_json(result, 404)
            else:
                self._send_json(result)

    def _handle_agent_ingest(self, path: str):
        """POST /v1/agents/{id}/ingest — ingest file or URL into agent memory."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            result = self._handle_multipart_ingest(agent_id, None)
        else:
            body = self._read_json()
            url = body.get("url", "")
            if url:
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_url(
                    agent_id, url,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
            else:
                # Check for file_path (local file ingestion via JSON)
                file_path = body.get("file_path", "")
                if not file_path:
                    self._send_json({"error": "Provide 'url' or 'file_path'"}, 400)
                    return
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_file(
                    agent_id, file_path,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_project_ingest(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/ingest"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            result = self._handle_multipart_ingest(agent_id, proj_name)
        else:
            body = self._read_json()
            url = body.get("url", "")
            if url:
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_url(
                    agent_id, url, project_name=proj_name,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
            else:
                file_path = body.get("file_path", "")
                if not file_path:
                    self._send_json({"error": "Provide 'url' or 'file_path'"}, 400)
                    return
                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                result = engine.IngestManager.ingest_file(
                    agent_id, file_path, project_name=proj_name,
                    tags=tags,
                    chunk_size=body.get("chunk_size", 1500),
                    chunk_overlap=body.get("chunk_overlap", 200),
                )
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    # ----- Supplementary instruction files (read-on-demand, NEVER mined) -----
    # Owner-uploaded explanatory docs that complement the project Instructions.
    # Stored under <project>/instruction-files/; the system prompt lists their
    # disk paths so the model reads them with read_document on demand (same
    # concept as chat attachments). Distinct from ingested/ (which IS mined).

    _MAX_INSTRUCTION_FILE_BYTES = 25 * 1024 * 1024  # 25 MB/file (attachment-class)

    def _handle_project_instruction_files_list(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/instruction-files"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name) is None:
            return
        cfg = engine.ProjectManager.get_project(agent_id, proj_name) or {}
        self._send_json({"instruction_files": cfg.get("instruction_files", []) or []})

    def _handle_project_instruction_file_upload(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/instruction-files — multipart.
        Saves the file under instruction-files/, pre-builds its .md companion
        (so read_document resolves binaries cleanly), and records it in
        project.json. NEVER mined into memory."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json({"error": "No content"}, 400)
            return
        if length > self._MAX_INSTRUCTION_FILE_BYTES + 8192:
            self._send_json({"error": "file too large (max 25 MB)"}, 413)
            return
        body = self.rfile.read(length)
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
                break
        if not boundary:
            self._send_json({"error": "No boundary in Content-Type"}, 400)
            return
        filename = None
        file_data = None
        for part in body.split(f"--{boundary}".encode()):
            if b"Content-Disposition" not in part:
                continue
            if b"\r\n\r\n" not in part:
                continue
            head, part_body = part.split(b"\r\n\r\n", 1)
            if part_body.endswith(b"\r\n"):
                part_body = part_body[:-2]
            head_text = head.decode("utf-8", errors="replace")
            if 'name="file"' not in head_text:
                continue
            for line in head_text.split("\r\n"):
                if "filename=" in line:
                    filename = line.split("filename=", 1)[1].strip().strip('";')
                    break
            file_data = part_body
            break
        # Keep only the basename — defends against path traversal in the
        # multipart filename.
        filename = os.path.basename(filename or "").strip()
        if not filename or file_data is None:
            self._send_json({"error": "No file uploaded"}, 400)
            return
        if len(file_data) > self._MAX_INSTRUCTION_FILE_BYTES:
            self._send_json({"error": "file too large (max 25 MB)"}, 413)
            return

        idir = engine.ProjectManager._instruction_files_dir(agent_id, proj_name)
        try:
            os.makedirs(idir, exist_ok=True)
        except OSError as e:
            self._send_json({"error": f"mkdir failed: {e}"}, 500)
            return
        dest = os.path.join(idir, filename)
        try:
            with open(dest, "wb") as f:
                f.write(file_data)
        except OSError as e:
            self._send_json({"error": f"save failed: {e}"}, 500)
            return

        # Pre-build the .md companion for binaries so the model's first
        # read_document is instant. Best-effort — text files need nothing, and
        # a conversion failure must not block the upload (the model can still
        # read the original). pdir is the project root so the companion lands
        # in the daemon-standard .brain-extracted/ layout.
        converted = False
        try:
            from engine import doc_convert as _dc
            pdir = engine.ProjectManager._project_dir(agent_id, proj_name)
            md_path, err = _dc.convert_one(dest, project_root=pdir)
            converted = bool(md_path and not err)
        except Exception as _e:
            print(f"[instruction_files] companion build failed: {_e}", flush=True)

        # Record in project.json (dedup by filename — re-upload replaces).
        cfg = engine.ProjectManager.get_project(agent_id, proj_name) or {}
        files = [f for f in (cfg.get("instruction_files") or [])
                 if isinstance(f, dict) and f.get("filename") != filename]
        files.append({
            "filename": filename,
            "size": len(file_data),
            "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        engine.ProjectManager.update_project(
            agent_id, proj_name, {"instruction_files": files})
        self._send_json({"status": "ok", "filename": filename,
                         "converted": converted, "instruction_files": files})

    def _handle_project_init(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/init — Code Mode only.

        Runs an agentic background turn whose cwd is the project's working_dir:
        the agent explores the directory (read/list/grep) and writes a BRAIN.md
        summary at its root. BRAIN.md then serves as the project's plain-markdown
        memory (injected into the system prompt; never mined). Returns
        immediately ({status:'generating'}); the worker runs in a thread."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        if not project.get("code_mode"):
            self._send_json({"error": "init is only available in Code Mode projects"}, 400)
            return
        wd = (project.get("working_dir") or "").strip()
        if not wd or not os.path.isdir(wd):
            self._send_json({"error": "Project has no valid working directory"}, 400)
            return
        user = getattr(self, "_auth_user", _auth_mod.SYNTHETIC_ADMIN)
        uid = user.get("id") or ""
        _srv()._project_init_run(agent_id, proj_name, wd, uid)
        self._send_json({"status": "generating", "working_dir": wd,
                         "brain_md": os.path.join(wd, "BRAIN.md")})

    def _handle_project_init_status(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/init-status — Code Mode only.

        Returns the latest init run's progress so the UI can show a spinner and
        a cancel button: {state: idle|generating|done|error|cancelled, elapsed,
        error?}. `idle` = no run has been started this server process."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name) is None:
            return
        st = _srv()._project_init_status(agent_id, proj_name)
        if not st:
            self._send_json({"state": "idle"})
            return
        self._send_json({
            "state": st.get("status") or "idle",
            "elapsed": st.get("elapsed") or 0,
            "error": st.get("error") or "",
            "working_dir": st.get("working_dir") or "",
        })

    def _handle_project_init_cancel(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/init-cancel — Code Mode only."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        ok = _srv()._project_init_cancel(agent_id, proj_name)
        self._send_json({"status": "cancelling" if ok else "not_running",
                         "cancelled": ok})

    def _handle_project_instruction_file_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/instruction-files/{filename}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        parts = path.split("/")
        # /v1/agents/{id}/projects/{name}/instruction-files/{filename}
        fname = ""
        if len(parts) >= 8:
            from urllib.parse import unquote
            fname = os.path.basename(unquote(parts[7]).strip())
        if not fname:
            self._send_json({"error": "Missing filename"}, 400)
            return
        idir = engine.ProjectManager._instruction_files_dir(agent_id, proj_name)
        dest = os.path.join(idir, fname)
        try:
            if os.path.isfile(dest):
                os.unlink(dest)
        except OSError as e:
            self._send_json({"error": f"delete failed: {e}"}, 500)
            return
        cfg = engine.ProjectManager.get_project(agent_id, proj_name) or {}
        files = [f for f in (cfg.get("instruction_files") or [])
                 if isinstance(f, dict) and f.get("filename") != fname]
        engine.ProjectManager.update_project(
            agent_id, proj_name, {"instruction_files": files})
        self._send_json({"status": "deleted", "filename": fname,
                         "instruction_files": files})

    # ----- AI-generation of project instructions (agentic, review-before-save) -----
    # Replaces the manual workflow (open a chat, attach the reference docs, prompt
    # the agent to write a project-instruction document, paste the result into the
    # project view). The agent reads the project's reference/instruction files +
    # queries its wing/KG + may web-search, then writes the markdown. The result
    # is loaded into the instructions editor for review + Save — NOT auto-applied.

    def _handle_project_generate_instructions(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/generate-instructions
        Body: {prompt}. Spawns the agentic generation worker, returns {gen_id}."""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json() or {}
        user_prompt = str(body.get("prompt") or "").strip()
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        from engine import instruction_gen
        gen_id = instruction_gen.start_generation(
            agent_id=agent_id, project=project, user_prompt=user_prompt,
            user_id=user["id"])
        self._send_json({"gen_id": gen_id, "status": "generating"})

    def _handle_project_instruction_gen_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/instruction-gen/{gen_id}
        Poll target: status + phase + live step log (+ result_md when ready)."""
        from server_lib.db import ChatDB
        from engine import instruction_gen
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        gen_id = path.rstrip("/").split("/")[-1]
        if not agent_id or not proj_name or not gen_id:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        row = ChatDB.get_instruction_gen(gen_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Generation not found"}, 404)
            return
        self._send_json({
            "gen_id": gen_id,
            "status": row.get("status", ""),
            "phase": row.get("phase", "") or "",
            "model": row.get("model", "") or "",
            "error": row.get("error", "") or "",
            # result_md only when ready (avoid streaming a half-baked draft).
            "result_md": row.get("result_md", "") if row.get("status") == "ready" else "",
            "steps": instruction_gen.get_steps(gen_id),
        })

    def _handle_project_instruction_gen_cancel(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/instruction-gen/{gen_id}/cancel"""
        from server_lib.db import ChatDB
        from engine import instruction_gen
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.rstrip("/").split("/")
        gen_id = parts[-2] if len(parts) >= 2 else ""   # …/{gen_id}/cancel
        if not agent_id or not proj_name or not gen_id:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        row = ChatDB.get_instruction_gen(gen_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Generation not found"}, 404)
            return
        instruction_gen.request_cancel(gen_id)
        self._send_json({"gen_id": gen_id, "status": "cancelling"})

    def _handle_multipart_ingest(self, agent_id: str, project_name) -> dict:
        """Parse multipart/form-data upload and ingest the file."""
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        if not content_length:
            return {"error": "No content"}

        # Read the full body
        body = self.rfile.read(content_length)

        # Extract boundary from content-type
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break
        if not boundary:
            return {"error": "No boundary in Content-Type"}

        # Parse multipart parts manually
        delimiter = f"--{boundary}".encode()
        parts = body.split(delimiter)

        filename = None
        file_data = None
        form_fields = {}

        for part in parts:
            if not part or part == b"--\r\n" or part == b"--":
                continue
            # Split headers from body at first double newline
            if b"\r\n\r\n" in part:
                header_block, part_body = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                header_block, part_body = part.split(b"\n\n", 1)
            else:
                continue

            # Strip trailing \r\n from part body
            if part_body.endswith(b"\r\n"):
                part_body = part_body[:-2]

            header_text = header_block.decode("utf-8", errors="replace")
            # Parse Content-Disposition
            field_name = None
            field_filename = None
            for line in header_text.split("\r\n"):
                line = line.strip()
                if line.lower().startswith("content-disposition:"):
                    for item in line.split(";"):
                        item = item.strip()
                        if item.startswith("name="):
                            field_name = item[5:].strip('"').strip("'")
                        elif item.startswith("filename="):
                            field_filename = item[9:].strip('"').strip("'")

            if field_name == "file" and field_filename:
                filename = field_filename
                file_data = part_body
            elif field_name:
                form_fields[field_name] = part_body.decode("utf-8", errors="replace")

        if not filename or file_data is None:
            return {"error": "No file uploaded"}

        # A folder upload (webkitdirectory / drag-dropped folder) sends the
        # browser-set multipart filename as the RELATIVE PATH, e.g.
        # "ATLANTIC Trading/Kündigungsschreiben.pdf". Keep only the basename for
        # the temp file — os.path.join(tmp_dir, "<sub>/<name>") would point at a
        # non-existent subdir and crash the whole upload (FileNotFoundError),
        # and a leading "/" or ".." would be a path-traversal risk. The folder
        # STRUCTURE is preserved separately client-side via source_groups, not
        # via this filename. Handle both / and \ separators defensively.
        filename = os.path.basename(filename.replace("\\", "/")).strip()
        if not filename:
            return {"error": "No file uploaded"}

        # Save to temp file with original filename preserved
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        with open(tmp_path, "wb") as tmp:
            tmp.write(file_data)
        try:
            tags_raw = form_fields.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            chunk_size = int(form_fields.get("chunk_size", "1500"))
            chunk_overlap = int(form_fields.get("chunk_overlap", "200"))
            # A folder import sends rel_path ("Kunde-A/Bericht.pdf") so two
            # same-named files in different groups get distinct source keys
            # (the filename itself is basenamed above for safety, losing the
            # path). Strip any leading "/" or ".." defensively. Absent → the
            # plain basename (single-file upload, unchanged).
            rel_path = (form_fields.get("rel_path", "") or "").replace("\\", "/").strip()
            rel_path = "/".join(p for p in rel_path.split("/") if p and p != "..")
            result = engine.IngestManager.ingest_file(
                agent_id, tmp_path, project_name=project_name,
                tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                source_name=(rel_path or None),
            )
            # Auto-review the uploaded file synchronously BEFORE the temp file
            # is deleted, keyed by the resulting source_hash so the ingested
            # node's badge resolves. Best-effort. Project-uploaded files have
            # no persisted original on disk, so the review's source_ref is the
            # source_hash (reviewer re-fetches its text from the review row).
            try:
                shash = (result or {}).get("source_hash") or ""
                user = getattr(self, "_auth_user", None) or {}
                uid = (user.get("id") or user.get("user_id")
                       or user.get("username") or "")
                if shash and uid and "error" not in (result or {}):
                    from engine import doc_review as _dr
                    _dr.review_file_to_db(
                        tmp_path, user_id=uid, source_kind="project_doc",
                        source_ref=shash, filename=filename)
            except Exception as _e:
                print(f"[data_review] ingest auto-review failed: {_e}",
                      flush=True)
            return result
        finally:
            try:
                os.unlink(tmp_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def _handle_list_ingested(self, path: str):
        """GET /v1/agents/{id}/ingested"""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        docs = engine.IngestManager.list_ingested(agent_id)
        self._send_json({"agent": agent_id, "documents": docs})

    def _handle_project_docs(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/docs"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        if self._project_access_check(agent_id, proj_name) is None:
            return
        docs = engine.IngestManager.list_ingested(agent_id, project_name=proj_name)
        # Annotate each ingested doc with its review badge state (keyed by
        # source_hash for project_doc-kind reviews). Cheap DB reads.
        _ruser = getattr(self, "_auth_user", None) or {}
        _ruid = (_ruser.get("id") or _ruser.get("user_id")
                 or _ruser.get("username") or "")
        if _ruid:
            try:
                from engine import review_state as _rs
                for d in docs or []:
                    sh = d.get("source_hash") or ""
                    if not sh:
                        continue
                    st = _rs.review_state(source_kind="project_doc",
                                          source_ref=sh, user_id=_ruid)
                    d["review"] = st.get("state") if st.get("state") != "none" else None
            except Exception:
                pass
        self._send_json({"agent": agent_id, "project": proj_name, "documents": docs})

    @staticmethod
    def _git_worktree_states(working_dir: str) -> dict:
        """Map {realpath: status_code} for a code-mode project's git worktree.

        One `git status --porcelain` over `working_dir`. Returns {} when the dir
        is not a git repo (or git is missing) — the tree then shows no git dots.
        The porcelain XY columns are (index, worktree); we collapse to a single
        actionable code per file: '?' untracked · 'M' modified (staged or not) ·
        'A' added · 'D' deleted · 'R' renamed · 'U' conflict."""
        import os
        import subprocess
        wd = (working_dir or "").strip()
        if not wd or not os.path.isdir(wd):
            return {}
        # Run git directly (NOT _run_git — it .strip()s the output, which would
        # eat the leading status-column space of the first porcelain record and
        # shift every path by one char). The XY status columns are positional.
        try:
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            proc = subprocess.run(
                ["git", "--no-pager", "status", "--porcelain", "-z"],
                capture_output=True, cwd=wd, env=env, timeout=10,
            )
        except Exception:
            return {}
        if proc.returncode != 0:
            return {}
        out = proc.stdout.decode("utf-8", errors="replace")
        if not out:
            return {}
        states = {}
        # -z uses NUL record separators; a rename record is "XY new\0old".
        records = out.split("\0")
        i = 0
        while i < len(records):
            rec = records[i]
            if len(rec) < 4:
                i += 1
                continue
            xy = rec[:2]
            fname = rec[3:]
            # Rename/copy carries a trailing NUL-separated old path — skip it.
            if xy and xy[0] in ("R", "C"):
                i += 2
            else:
                i += 1
            x, y = (xy[0] if len(xy) > 0 else " "), (xy[1] if len(xy) > 1 else " ")
            if "U" in xy or (x == "D" and y == "D") or (x == "A" and y == "A"):
                c = "U"          # unmerged / conflict
            elif xy == "??":
                c = "?"          # untracked
            elif x == "R" or y == "R":
                c = "R"          # renamed
            elif x == "A" or y == "A":
                c = "A"          # added
            elif x == "D" or y == "D":
                c = "D"          # deleted
            elif x == "M" or y == "M":
                c = "M"          # modified (staged and/or worktree)
            else:
                c = x.strip() or y.strip() or ""
            if fname and c:
                states[os.path.realpath(os.path.join(wd, fname))] = c
        return states

    def _handle_project_folder_tree(self, path: str):
        """GET …/projects/{name}/folder-tree?path=<abs folder> — the REAL,
        read-only subtree of an ingested input folder, each file coloured by its
        MemPalace state (indexed/pending/stale). Lazy: called only when a folder
        node is expanded in the source tree. The folder hierarchy is fixed (the
        user can't regroup files inside it — see the source-tree feature)."""
        import os
        import urllib.parse as _up
        from server_lib.db import _project_wing
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        # The dispatcher strips the query string from `path`; read it off
        # self.path (the raw request line) instead.
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        folder = (qs.get("path", [""])[0] or "").strip()
        if not folder:
            self._send_json({"error": "path is required"}, 400)
            return
        # Security: the requested folder MUST be one of the project's configured
        # input folders (or a descendant) — OR, for a code-mode project, the
        # project's working_dir (or a descendant). Never an arbitrary disk path.
        allowed = [(f.get("path") or "") for f in (project.get("input_folders") or [])
                   if isinstance(f, dict)]
        _code_mode = bool(project.get("code_mode"))
        if _code_mode and (project.get("working_dir") or "").strip():
            allowed.append(project["working_dir"].strip())
        real = os.path.realpath(folder)
        if not any(real == os.path.realpath(c) or real.startswith(os.path.realpath(c) + os.sep)
                   for c in allowed if c):
            self._send_json({"error": "Folder is not within this project's sources"}, 403)
            return
        if not os.path.isdir(real):
            self._send_json({"error": "Folder not found on disk"}, 404)
            return
        # Code-mode folders carry no MemPalace state (no ingest) — skip the wing
        # lookups entirely; the tree just shows the real files.
        indexed = set()
        kg_states = {}
        git_states = {}
        if _code_mode:
            # Per-file git working-tree state for the editor tree. One cheap
            # `git status --porcelain` over the working_dir (NOT `real` — the
            # status is relative to the repo root) → {realpath: code}. The two
            # porcelain columns are XY (index, worktree); we surface the most
            # actionable single code per file: '?' untracked, 'M' modified,
            # 'A' added, 'D' deleted, 'R' renamed, 'C' conflict.
            git_states = self._git_worktree_states(project.get("working_dir") or "")
        if not _code_mode:
            try:
                palace_path = (engine._load_mempalace_config() or {}).get("palace_path", "")
            except Exception:
                palace_path = ""
            wing = _project_wing(project.get("id") or "")
            indexed = engine.indexed_source_files_for_wing(palace_path, wing) if palace_path else set()
            # Per-file KG state (triples / GDPR-skipped) from the extraction
            # cursor, so each file shows mined+kg / mined / skipped / not-mined.
            try:
                from engine import kg_extract as _kg
                chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
                kg_states = _kg.kg_source_states_for_wing(chats_db, wing)
            except Exception:
                kg_states = {}

        # Walk the folder (depth-bounded) → nested {name,type,children|state}.
        _SKIP = {".brain-extracted", ".git", "__pycache__", ".DS_Store"}
        # Code-mode trees auto-refresh on a poll, so additionally skip heavy /
        # vendored dirs (node_modules, venvs, build output) to keep the walk
        # cheap. NOT applied to ingest folders — those may legitimately contain
        # ingested files under dist/build, and aren't polled.
        if _code_mode:
            _SKIP = _SKIP | {
                "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache",
                ".next", "dist", "build", ".cache", "target", ".gradle",
                ".idea", ".tox", ".ruff_cache"}

        # Review badge state per file (GDPR/classification reviewer). Keyed by
        # the file's real path for project_path-kind reviews. Cheap DB reads.
        _ruser = getattr(self, "_auth_user", None) or {}
        _ruid = (_ruser.get("id") or _ruser.get("user_id")
                 or _ruser.get("username") or "")

        def _review_for(fp):
            if not _ruid:
                return None
            try:
                from engine import review_state as _rs
                st = _rs.review_state(source_kind="project_path",
                                      source_ref=os.path.realpath(fp),
                                      user_id=_ruid)
                return st.get("state") if st.get("state") != "none" else None
            except Exception:
                return None

        def _state_for(fp):
            """Return {mined, kg, skip_reason} for one file.
            mined: 'indexed' (drawers present) | 'pending' (not yet mined).
            kg:    'kg' (triples) | 'skipped' (GDPR/classification) |
                   'empty' (extracted, no relations) | 'none' (not extracted)."""
            real_fp = os.path.realpath(fp)
            mined = "indexed" if real_fp in indexed else "pending"
            ks = kg_states.get(real_fp)
            kg = ks["kg"] if ks else "none"
            reason = ks.get("skip_reason", "") if ks else ""
            return {"mined": mined, "kg": kg, "skip_reason": reason}

        def _walk(d, depth=0):
            kids = []
            try:
                entries = sorted(os.listdir(d))
            except OSError:
                return kids
            for name in entries:
                if name in _SKIP or name.startswith("."):
                    continue
                fp = os.path.join(d, name)
                if os.path.isdir(fp):
                    if depth >= 8:   # safety bound on recursion
                        continue
                    kids.append({"name": name, "type": "dir", "path": fp,
                                 "children": _walk(fp, depth + 1)})
                elif os.path.isfile(fp):
                    st = _state_for(fp)
                    # size + mtime for the file-status line (code-mode tree).
                    try:
                        _stt = os.stat(fp)
                        _size = int(_stt.st_size)
                        _mtime = int(_stt.st_mtime)
                    except OSError:
                        _size = _mtime = 0
                    # `state` kept (legacy string) for back-compat; `mined`/`kg`
                    # are the new per-doc fields the project view reads.
                    kids.append({"name": name, "type": "file", "path": fp,
                                 "size": _size, "mtime": _mtime,
                                 "state": st["mined"], "mined": st["mined"],
                                 "kg": st["kg"], "skip_reason": st["skip_reason"],
                                 "review": _review_for(fp),
                                 "git": git_states.get(os.path.realpath(fp), "")})
            return kids

        self._send_json({"path": real, "tree": _walk(real),
                         "has_index": bool(indexed),
                         "has_kg": bool(kg_states)})

    def _handle_project_web_url_states(self, path: str):
        """GET …/projects/{name}/web-url-states → {url: 'indexed'|'pending'} per
        configured web URL. Web URLs are mined as a BATCH (one sync-status item),
        so there's no per-URL state — derive it from each URL's
        web-urls/<slug>.md companion being indexed in MemPalace (same wing match
        as folder files). Lets the source tree show an accurate per-URL dot
        instead of a blanket 'Ausstehend'."""
        import server_daemons
        from server_lib.db import _project_wing
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        try:
            palace_path = (engine._load_mempalace_config() or {}).get("palace_path", "")
        except Exception:
            palace_path = ""
        wing = _project_wing(project.get("id") or "")
        indexed = engine.indexed_source_files_for_wing(palace_path, wing) if palace_path else set()
        pdir = project.get("dir") or ""
        states = server_daemons.weburl_states(pdir, project.get("web_urls") or [], indexed)
        self._send_json({"states": states})

    def _handle_agent_ingested_delete(self, path: str):
        """DELETE /v1/agents/{id}/ingested/{hash}"""
        from urllib.parse import unquote
        agent_id = self._parse_agent_from_path(path)
        parts = path.split("/")
        # URL-decode the hash segment (source_hash = filename stem, may contain
        # non-ASCII) so a stem like "Übersicht" matches; and treat a no-op
        # delete as 404 rather than a false success. (Same fix as the project
        # doc-delete handler.)
        source_hash = unquote(parts[-1]) if len(parts) >= 5 else ""
        if not agent_id or not source_hash:
            self._send_json({"error": "Missing agent or source hash"}, 400)
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash)
        if "error" in result:
            self._send_json(result, 404)
        elif not result.get("deleted"):
            self._send_json({"error": "document not found", **result}, 404)
        else:
            self._send_json(result)

    # ── Output Presets / Studio / Research shared store ──

    @staticmethod
    def _output_to_dict(row: dict) -> dict:
        """Project_outputs row → API shape (drop internal-only fields)."""
        return {
            "output_id": row.get("id"),
            "project_id": row.get("project_id"),
            "kind": row.get("kind"),
            "title": row.get("title"),
            "status": row.get("status"),
            "path": row.get("path"),
            "artifact_id": row.get("artifact_id"),
            "html_artifact_id": row.get("html_artifact_id") or "",
            "citations": row.get("citations") or 0,
            "error": row.get("error") or "",
            "phase": row.get("phase") or "",
            "model": row.get("model") or "",
            "tokens_in": row.get("tokens_in") or 0,
            "tokens_out": row.get("tokens_out") or 0,
            "cost": row.get("cost") or 0,
            "duration_s": row.get("duration_s") or 0,
            "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
            "finished_at": row.get("finished_at"),
        }

    def _handle_project_generate(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/generate
        Body: {kind: study_guide|briefing|faq|timeline, options?: {focus?, length?}}
        Inserts a project_outputs row (status=generating), spawns the generation
        worker, returns {output_id, status}. SHARED endpoint (Output Presets +
        Audio Overview + Research)."""
        from engine import output_presets, output_gen
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json()
        kind = (body.get("kind") or "").strip()
        # audio_overview is a project_outputs kind too, but it runs a DIFFERENT
        # worker (script-gen + two-voice stitch → .mp3) — not a text preset. It's
        # valid here even though it's not in output_presets.PRESETS.
        if kind != "audio_overview" and not output_presets.is_valid_kind(kind):
            self._send_json(
                {"error": f"Unknown kind '{kind}'. Valid: {', '.join(output_presets.PRESETS)}, audio_overview"}, 400)
            return
        # No sources → refuse cleanly (W2). Sources = uploaded/ingested chunks,
        # mined input folders, or project web URLs (all feed the project wing).
        if (not project.get("chunks")
                and not (project.get("web_urls") or [])
                and not (project.get("input_folders") or [])):
            self._send_json({"error": "This project has no sources yet."}, 400)
            return
        opts = body.get("options") or {}
        if not isinstance(opts, dict):
            opts = {}
        length = opts.get("length")
        if length and length not in ("short", "std", "long"):
            self._send_json({"error": f"Invalid length '{length}'"}, 400)
            return
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if kind == "audio_overview":
            from engine import audio_overview
            output_id = audio_overview.start(
                agent_id=agent_id, project=project, user_id=user["id"],
                opts={"focus": (opts.get("focus") or "").strip(),
                      "length": length or "std",
                      "audience": (opts.get("audience") or "").strip(),
                      "host_a_voice": (opts.get("host_a_voice") or "").strip(),
                      "host_b_voice": (opts.get("host_b_voice") or "").strip()})
        else:
            output_id = output_gen.start_generation(
                agent_id=agent_id, project=project, kind=kind,
                opts={"focus": (opts.get("focus") or "").strip(), "length": length or "std"},
                user_id=user["id"])
        self._send_json({"output_id": output_id, "status": "generating"})

    def _handle_project_outputs_list(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/outputs — list this project's
        outputs (UI polls this for generating→ready). Studio extends this."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if not agent_id or not proj_name:
            self._send_json({"error": "Missing agent or project"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        pid = project.get("id") or ""
        rows = ChatDB.list_project_outputs(pid)
        # Also surface output-role artifacts written by this project's CHAT
        # sessions (live join, read-only) so Studio is the one home for project
        # outputs — generated deliverables AND chat-produced files. Intermediates
        # (.py/.csv/.log) are excluded by the role filter; the global Artifacts
        # view still shows everything.
        chat_arts = ChatDB.list_project_output_artifacts(pid)
        self._send_json({
            "agent": agent_id, "project": proj_name,
            "outputs": [self._output_to_dict(r) for r in rows],
            "chat_artifacts": [{
                "artifact_id": a.get("id"), "session_id": a.get("session_id"),
                "name": a.get("name"), "type": a.get("type"),
                "latest_version": a.get("latest_version"),
                "created_at": a.get("created_at"), "updated_at": a.get("updated_at"),
            } for a in chat_arts],
        })

    def _handle_project_output_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/outputs/{output_id} — single
        output status (poll target during generation)."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        output_id = path.rstrip("/").split("/")[-1]
        if not agent_id or not proj_name or not output_id:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        row = ChatDB.get_project_output(output_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Output not found"}, 404)
            return
        self._send_json(self._output_to_dict(row))

    def _resolve_owned_output(self, path: str, *, output_id_index: int = -1):
        """Shared guard for output mutations: parse agent/project/output_id, enforce
        manage, confirm the output belongs to this project. Returns (output_id, row)
        or None after sending an error response."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.rstrip("/").split("/")
        output_id = parts[output_id_index] if len(parts) >= abs(output_id_index) else ""
        if not agent_id or not proj_name or not output_id:
            self._send_json({"error": "Missing parameters"}, 400)
            return None
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return None
        row = ChatDB.get_project_output(output_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Output not found"}, 404)
            return None
        return output_id, row

    def _handle_project_output_rename(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/outputs/{output_id}/rename {title}
        — rename the output row only (the .md file is untouched). Studio W5."""
        from server_lib.db import ChatDB
        resolved = self._resolve_owned_output(path, output_id_index=-2)  # …/<oid>/rename
        if resolved is None:
            return
        output_id, _row = resolved
        title = (self._read_json().get("title") or "").strip()
        if not title:
            self._send_json({"error": "title is required"}, 400)
            return
        ChatDB.update_project_output(output_id, title=title[:300])
        self._send_json({"output_id": output_id, "title": title[:300]})

    def _handle_project_output_archive(self, path: str):
        """POST .../outputs/{output_id}/archive {archived?} — archive/unarchive a
        generated output. Non-destructive: row + .md file survive, just hidden
        from the Studio list. Default action = archive."""
        from server_lib.db import ChatDB
        resolved = self._resolve_owned_output(path, output_id_index=-2)  # …/<oid>/archive
        if resolved is None:
            return
        output_id, _row = resolved
        archived = bool((self._read_json() or {}).get("archived", True))
        ChatDB.set_project_output_archived(output_id, archived)
        self._send_json({"output_id": output_id, "archived": archived, "status": "ok"})

    def _handle_project_output_cancel(self, path: str):
        """POST .../outputs/{output_id}/cancel — cooperative cancel of a running
        generation. The worker stops at its next phase check; an in-flight LLM
        call still completes before the abort takes effect."""
        from server_lib.db import ChatDB
        resolved = self._resolve_owned_output(path, output_id_index=-2)  # …/<oid>/cancel
        if resolved is None:
            return
        output_id, row = resolved
        if row.get("status") != "generating":
            self._send_json({"error": "Output is not generating."}, 409)
            return
        ChatDB.cancel_project_output(output_id)
        self._send_json({"output_id": output_id, "status": "cancelling"})

    def _handle_project_output_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/outputs/{output_id} — delete the
        row, its artifact rows, AND the .md file on disk (no orphans). Studio W6.
        Refuses while still generating (E5 — don't delete a file being written)."""
        from server_lib.db import ChatDB
        resolved = self._resolve_owned_output(path, output_id_index=-1)
        if resolved is None:
            return
        output_id, row = resolved
        if row.get("status") == "generating":
            self._send_json({"error": "Cannot delete an output while it is still generating."}, 409)
            return
        # Remove the artifact rows + the file on disk (best-effort; row delete is
        # the source of truth so a missing file never blocks cleanup).
        art_id = row.get("artifact_id") or ""
        if art_id:
            ChatDB.delete_artifact_rows(art_id)
        fpath = row.get("path") or ""
        if fpath and "/outputs/" in fpath and os.path.isfile(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
        ChatDB.delete_project_output(output_id)
        self._send_json({"output_id": output_id, "status": "deleted"})

    def _resolve_project_chat_artifact(self, path, *, require_manage, art_id_index):
        """Shared guard for the Studio chat-artifact actions: resolve the project
        (with access check) + the artifact, and verify the artifact's session
        actually belongs to THIS project (no cross-project reach). Returns
        (project, artifact_dict) or None (after sending the error)."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        art_id = path.rstrip("/").split("/")[art_id_index]
        project = self._project_access_check(agent_id, proj_name, require_manage=require_manage)
        if project is None:
            return None
        art = ChatDB.get_artifact(art_id)
        if not art:
            self._send_json({"error": "Artifact not found"}, 404)
            return None
        sess = ChatDB.get_session_info(art.get("session_id") or "")
        if not sess or (sess.get("project_id") or "") != (project.get("id") or ""):
            self._send_json({"error": "Artifact not in this project"}, 404)
            return None
        return project, art

    def _handle_project_artifact_archive(self, path: str):
        """POST .../projects/{name}/artifacts/{artifact_id}/archive {archived?}
        — archive/unarchive a project chat artifact from Studio. Non-destructive:
        the file + row survive and stay in the global Artifacts view."""
        from server_lib.db import ChatDB
        resolved = self._resolve_project_chat_artifact(path, require_manage=True, art_id_index=-2)
        if resolved is None:
            return
        _project, art = resolved
        body = self._read_json() or {}
        archived = bool(body.get("archived", True))   # default = archive
        ChatDB.set_artifact_archived(art.get("id"), archived)
        self._send_json({"artifact_id": art.get("id"),
                         "archived": archived, "status": "ok"})

    def _handle_project_artifact_delete(self, path: str):
        """DELETE .../projects/{name}/artifacts/{artifact_id} — delete a project
        chat artifact from Studio: removes the artifact + version rows AND the
        file on disk (best-effort). Row delete is the source of truth."""
        import os
        from server_lib.db import ChatDB
        resolved = self._resolve_project_chat_artifact(path, require_manage=True, art_id_index=-1)
        if resolved is None:
            return
        _project, art = resolved
        fpath = art.get("path") or ""
        ChatDB.delete_artifact_rows(art.get("id"))
        if fpath and os.path.isfile(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
        self._send_json({"artifact_id": art.get("id"), "status": "deleted"})

    # ── Research (Fast + Deep) ──────────────────────────────────────────────

    def _handle_research_backends(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/research/backends — THE active
        search backend (the one enabled search tool), or "" if none (E1 gate)."""
        from engine import deep_research
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        if self._project_access_check(agent_id, proj_name) is None:
            return
        self._send_json({"backend": deep_research.active_backend()})

    def _handle_research_search(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/research/search {topic}
        — Fast Research: search via THE active backend, dedup vs the project's
        web_urls, return rows with an `in_project` flag + trust hint. No import
        here — the FE appends approved URLs via the existing update_project path."""
        from engine import deep_research
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        body = self._read_json()
        topic = (body.get("topic") or "").strip()
        if not topic:
            self._send_json({"error": "topic is required"}, 400)
            return
        if not deep_research.active_backend():
            self._send_json({"error": "No search backend configured."}, 400)
            return
        import time as _time
        _t0 = _time.time()
        existing = {deep_research._norm_url(u.get("url", "")) for u in (project.get("web_urls") or [])}
        backend = deep_research.active_backend()
        results = deep_research._run_search(topic)
        rows = [{
            "title": r["title"], "url": r["link"], "snippet": r.get("snippet", ""),
            "trust_hint": deep_research._trust_hint(r["link"]),
            "in_project": deep_research._norm_url(r["link"]) in existing,
        } for r in results[:30]]   # E8 — cap the SERP
        # Honest execution metadata for the Fast-Research view. NO model/cost —
        # Fast Research makes no LLM call (pure search), so a $0 line would
        # mislead; we surface what's actually true: backend, timing, counts.
        self._send_json({"topic": topic, "results": rows, "result_count": len(rows),
                         "total_found": len(results),
                         "backend": "Exa" if backend == "exa" else "SearXNG" if backend == "searxng" else backend,
                         "duration_s": round(_time.time() - _t0, 2)})

    def _handle_weburl_discover_links(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/web-urls/discover-links
        — Option B: scan the project's configured HTML web_urls for SAME-HOST
        document links (PDF/DOCX/XLSX/…) and return them as PROPOSED sources.
        Nothing is imported — the FE shows the proposals and the user appends
        approved ones via the existing update_project web_urls path. Deliberately
        bounded (depth-1, same host, documents only): NOT a recursive crawler."""
        from engine import web_link_discovery
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        web_urls = project.get("web_urls") or []
        if not web_urls:
            self._send_json({"proposed": [], "scanned": 0, "pages": [],
                             "message": "Keine Web-Adressen im Projekt."})
            return
        import time as _time
        _t0 = _time.time()
        try:
            res = web_link_discovery.discover_document_links(web_urls)
        except Exception as e:
            self._send_json({"error": f"Linksuche fehlgeschlagen: {e}"}, 500)
            return
        res["duration_s"] = round(_time.time() - _t0, 2)
        self._send_json(res)

    def _handle_research_deep(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/research/deep {topic, budget?}
        — spawn the bounded Deep Research loop; returns {run_id, budget}.
        Uses THE active search backend. Progress via GET …/research/runs/<id>."""
        from engine import deep_research
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        body = self._read_json()
        topic = (body.get("topic") or "").strip()
        if not topic:
            self._send_json({"error": "topic is required"}, 400)
            return
        if not deep_research.active_backend():
            self._send_json({"error": "No search backend configured."}, 400)
            return
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        run_id, eff_budget = deep_research.start_research(
            agent_id=agent_id, project=project, topic=topic,
            budget=body.get("budget"), user_id=user["id"])
        self._send_json({"run_id": run_id, "status": "running", "budget": eff_budget})

    def _handle_research_runs_list(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/research/runs — recent runs for
        the project (newest first), so the tab can restore + browse history
        after a reload (the proposed sources live in the DB, not just in JS)."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        runs = ChatDB.list_research_runs(project.get("id") or "", limit=20)
        self._send_json({"runs": [{
            "run_id": r.get("id"), "topic": r.get("topic"), "status": r.get("status"),
            "phase": r.get("phase"), "report_output_id": r.get("report_output_id") or "",
            "created_at": r.get("created_at"), "finished_at": r.get("finished_at"),
        } for r in runs]})

    def _handle_research_run_get(self, path: str):
        """GET /v1/agents/{id}/projects/{name}/research/runs/{run_id} — poll a
        Deep Research run (status, phase, progress, budget, proposed sources)."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        run_id = path.rstrip("/").split("/")[-1]
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return
        row = ChatDB.get_research_run(run_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Research run not found"}, 404)
            return
        # Mark proposed sources already in the project's web_urls as in_project
        # (computed LIVE — the worker stored in_project:False, and the user may
        # have imported some since), so a restored run shows what's left.
        self._send_json(self._research_run_to_dict(row, project.get("web_urls") or []))

    def _handle_research_run_cancel(self, path: str):
        """POST /v1/agents/{id}/projects/{name}/research/runs/{run_id}/cancel
        — cooperative cancel (E3); the worker stops at its next checkpoint."""
        from server_lib.db import ChatDB
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.rstrip("/").split("/")
        run_id = parts[-2] if len(parts) >= 2 else ""   # …/runs/<id>/cancel
        project = self._project_access_check(agent_id, proj_name, require_manage=True)
        if project is None:
            return
        row = ChatDB.get_research_run(run_id)
        if not row or row.get("project_id") != (project.get("id") or ""):
            self._send_json({"error": "Research run not found"}, 404)
            return
        ChatDB.cancel_research_run(run_id)
        self._send_json({"run_id": run_id, "status": "cancelling"})

    @staticmethod
    def _research_run_to_dict(row: dict, project_web_urls=None) -> dict:
        def _j(v, default):
            try:
                return json.loads(v) if v else default
            except (ValueError, TypeError):
                return default
        proposed = _j(row.get("proposed"), [])
        # Re-mark proposed sources that are NOW in the project (the user may have
        # imported some since the run finished) so a restored run dims them. Uses
        # the worker's own URL normaliser for an apples-to-apples match.
        if project_web_urls is not None and isinstance(proposed, list):
            try:
                from engine.deep_research import _norm_url
                have = {_norm_url(u.get("url", "")) for u in project_web_urls
                        if isinstance(u, dict)}
                for s in proposed:
                    if isinstance(s, dict):
                        s["in_project"] = _norm_url(s.get("url", "")) in have
            except Exception:
                pass
        return {
            "run_id": row.get("id"),
            "topic": row.get("topic"),
            "status": row.get("status"),
            "phase": row.get("phase"),
            "progress": _j(row.get("progress"), {}),
            "budget": _j(row.get("budget"), {}),
            "report_output_id": row.get("report_output_id") or "",
            "proposed": proposed,
            "coverage_note": row.get("coverage_note") or "",
            "error": row.get("error") or "",
            "model": row.get("model") or "",
            "tokens_in": row.get("tokens_in") or 0,
            "tokens_out": row.get("tokens_out") or 0,
            "cost": row.get("cost") or 0,
            "duration_s": row.get("duration_s") or 0,
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }

    def _handle_project_doc_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/docs/{hash}"""
        from urllib.parse import unquote
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.split("/")
        # The hash segment is URL-encoded on the wire — a source_hash now equals
        # the filename stem, which can contain non-ASCII (e.g. "Übersicht" →
        # %C3%9C). Without unquote the stored stem never matches → 0 deleted but
        # a false 200 ("said deleted, still there"). Decode before matching.
        source_hash = unquote(parts[-1]) if len(parts) >= 8 else ""
        if not agent_id or not proj_name or not source_hash:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash, project_name=proj_name)
        # delete_ingested returns deleted=0 (no error key) when nothing matched —
        # surface that as a 404 so the client doesn't claim success on a no-op.
        if "error" in result:
            self._send_json(result, 404)
        elif not result.get("deleted"):
            self._send_json({"error": "document not found", **result}, 404)
        else:
            self._send_json(result)
