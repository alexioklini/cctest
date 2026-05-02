# Extracted from server.py — project, notes, ingest handlers
import datetime
import json
import os
import tempfile

from server_lib import auth as _auth_mod
import brain as engine


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
        visibility = body.get("visibility", "global" if is_admin else "user")
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
        elif visibility not in ("user", "global"):
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
        `require_manage` gates mutations: only owner, team head (for team sessions), or admin."""
        from brain import ChatDB
        info = ChatDB.get_session_info(sid)
        if not info:
            self._send_json({"error": "Session not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        owner_uid = info.get("user_id") or ""
        team_id = info.get("team_id") or ""
        visibility = info.get("visibility") or "user"
        # Admin bypass
        if user["role"] == "admin" or user["id"] == "__system__":
            return info
        # Owner
        if owner_uid and owner_uid == user["id"]:
            return info
        # Legacy anonymous sessions (no owner): allow read by anyone authenticated
        if not owner_uid:
            return info
        # Team-scoped: members can read, only team head can manage
        if visibility == "team" and team_id:
            my_teams = {t["id"]: t for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
            if team_id in my_teams:
                if require_manage and my_teams[team_id]["head_user_id"] != user["id"]:
                    self._send_json({"error": "Only team head or session owner can modify"}, 403)
                    return None
                return info
        self._send_json({"error": "Access to this session is not permitted"}, 403)
        return None

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
        result = engine.ProjectManager.update_project(agent_id, proj_name, body)
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

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

    _PROJECT_INPUT_FOLDER_FORBIDDEN = (
        # Refuse paths that obviously belong to brain itself or sensitive system dirs.
        "/etc", "/var", "/usr", "/bin", "/sbin", "/System", "/Library/Keychains",
    )

    def _project_input_folder_validate(self, raw: str) -> tuple:
        """Return (resolved_path, error). Refuses non-dirs, brain agents/, and
        obviously sensitive system dirs."""
        if not raw or not isinstance(raw, str):
            return "", "Path is required"
        try:
            p = os.path.expanduser(raw.strip())
            p = os.path.realpath(p)
        except (OSError, ValueError) as e:
            return "", f"Invalid path: {e}"
        if not os.path.isdir(p):
            return "", "Path is not a directory or does not exist"
        # Refuse paths inside our agents tree — those are managed elsewhere.
        agents_root = os.path.realpath(engine.AGENTS_DIR)
        if p == agents_root or p.startswith(agents_root + os.sep):
            return "", "Cannot add a folder inside the agents directory"
        for forbidden in self._PROJECT_INPUT_FOLDER_FORBIDDEN:
            if p == forbidden or p.startswith(forbidden + os.sep):
                return "", f"Path is in a protected location ({forbidden})"
        return p, ""

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
            from server import _project_sync_wakeup
            _project_sync_wakeup.set()
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
            from server import _project_sync_wakeup
            _project_sync_wakeup.set()
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
            from server import _project_sync_wakeup
            _project_sync_wakeup.set()
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
            from server import _project_sync_live_status
            live = _project_sync_live_status(agent_id, proj_name)
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
            from server import _project_sync_request
            _project_sync_request(agent_id, proj_name)
        except Exception:
            pass
        self._send_json({"status": "queued", "agent": agent_id, "project": proj_name})

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
