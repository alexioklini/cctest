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
        self._send_json({"status": "added", "folders": folders})

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
            result = engine.IngestManager.ingest_file(
                agent_id, tmp_path, project_name=project_name,
                tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            )
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
        self._send_json({"agent": agent_id, "project": proj_name, "documents": docs})

    def _handle_agent_ingested_delete(self, path: str):
        """DELETE /v1/agents/{id}/ingested/{hash}"""
        agent_id = self._parse_agent_from_path(path)
        parts = path.split("/")
        source_hash = parts[-1] if len(parts) >= 5 else ""
        if not agent_id or not source_hash:
            self._send_json({"error": "Missing agent or source hash"}, 400)
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash)
        if "error" in result:
            self._send_json(result, 404)
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
            "citations": row.get("citations") or 0,
            "error": row.get("error") or "",
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
        if not output_presets.is_valid_kind(kind):
            self._send_json(
                {"error": f"Unknown kind '{kind}'. Valid: {', '.join(output_presets.PRESETS)}"}, 400)
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
        rows = ChatDB.list_project_outputs(project.get("id") or "")
        self._send_json({
            "agent": agent_id, "project": proj_name,
            "outputs": [self._output_to_dict(r) for r in rows],
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
        existing = {deep_research._norm_url(u.get("url", "")) for u in (project.get("web_urls") or [])}
        results = deep_research._run_search(topic)
        rows = [{
            "title": r["title"], "url": r["link"], "snippet": r.get("snippet", ""),
            "trust_hint": deep_research._trust_hint(r["link"]),
            "in_project": deep_research._norm_url(r["link"]) in existing,
        } for r in results[:30]]   # E8 — cap the SERP
        self._send_json({"topic": topic, "results": rows, "result_count": len(rows),
                         "total_found": len(results)})

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
        self._send_json(self._research_run_to_dict(row))

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
    def _research_run_to_dict(row: dict) -> dict:
        def _j(v, default):
            try:
                return json.loads(v) if v else default
            except (ValueError, TypeError):
                return default
        return {
            "run_id": row.get("id"),
            "topic": row.get("topic"),
            "status": row.get("status"),
            "phase": row.get("phase"),
            "progress": _j(row.get("progress"), {}),
            "budget": _j(row.get("budget"), {}),
            "report_output_id": row.get("report_output_id") or "",
            "proposed": _j(row.get("proposed"), []),
            "coverage_note": row.get("coverage_note") or "",
            "error": row.get("error") or "",
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }

    def _handle_project_doc_delete(self, path: str):
        """DELETE /v1/agents/{id}/projects/{name}/docs/{hash}"""
        agent_id = self._parse_agent_from_path(path)
        proj_name = self._parse_project_from_path(path)
        parts = path.split("/")
        source_hash = parts[-1] if len(parts) >= 8 else ""
        if not agent_id or not proj_name or not source_hash:
            self._send_json({"error": "Missing parameters"}, 400)
            return
        if self._project_access_check(agent_id, proj_name, require_manage=True) is None:
            return
        result = engine.IngestManager.delete_ingested(agent_id, source_hash, project_name=proj_name)
        if "error" in result:
            self._send_json(result, 404)
        else:
            self._send_json(result)
