"""Admin, agent-management, workflow, skills, KG, and system handlers."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import threading
import urllib.request
import urllib.error
import uuid
from urllib.parse import unquote, urlencode


class AdminHandlerMixin:
    """Mixin with admin/agent/workflow/skills/KG/system handler methods."""

    def _handle_agents_activity(self):
        """GET /v1/agents/activity — which agents are currently doing something."""
        activity = {}  # agent_id -> list of activity types

        # 1. Streaming chat sessions
        with sessions._lock:
            for s in sessions._sessions.values():
                if hasattr(s, 'cancel_token') and not s.cancel_token.cancelled:
                    # Check if session has an active worker thread
                    # A session is "streaming" if it was recently active and not cancelled
                    pass

        # Simpler: check which sessions are in streaming state via agentChats client-side
        # Instead, track streaming sessions server-side
        with sessions._lock:
            for s in sessions._sessions.values():
                if not isinstance(s, Session):
                    continue  # skip loading sentinels
                if hasattr(s, '_streaming') and s._streaming:
                    activity.setdefault(s.agent_id, []).append("chat")

        # 2. Running delegated tasks
        if engine._task_runner:
            for t in engine._task_runner.list_tasks():
                if t.get("status") == "running":
                    aid = t.get("agent", "main")
                    if "delegate" not in activity.get(aid, []):
                        activity.setdefault(aid, []).append("delegate")

        # 3. Running scheduled tasks
        if engine._scheduler:
            for r in engine._scheduler.get_running_tasks():
                aid = r.get("agent", "main")
                if "schedule" not in activity.get(aid, []):
                    activity.setdefault(aid, []).append("schedule")

        self._send_json({"activity": activity})

    def _handle_teams_get(self):
        """GET /v1/teams — return team structure."""
        self._send_json(engine.get_team_structure())

    def _handle_teams_post(self):
        """POST /v1/teams — create, update, dissolve, or move teams."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "create":
            members = body.get("members", [])
            head_id = body.get("head", "")
            if not members:
                self._send_json({"error": "members is required (at least one agent)"}, 400)
                return
            if not head_id:
                head_id = members[0]
            # Ensure head is in members
            if head_id not in members:
                members.insert(0, head_id)
            # Validate members exist
            available = engine.list_agents()
            invalid = [m for m in members if m not in available]
            if invalid:
                self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                return
            if head_id not in available:
                self._send_json({"error": f"Head agent '{head_id}' not found"}, 404)
                return
            # Store team config on the head agent
            team_name = body.get("name", "")
            team_desc = body.get("description", "")
            team_avatar = body.get("avatar", "")
            cfg_path = os.path.join(engine.AGENTS_DIR, head_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                team_data = {"members": members, "head": head_id}
                if team_name:
                    team_data["name"] = team_name
                if team_desc:
                    team_data["description"] = team_desc
                if team_avatar:
                    team_data["avatar"] = team_avatar
                cfg["team"] = team_data
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "created", "head": head_id, "members": members})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "update":
            team_id = body.get("team_id", body.get("team_head", ""))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                if not isinstance(cfg.get("team"), dict):
                    self._send_json({"error": f"'{team_id}' does not hold a team config"}, 400)
                    return
                # Validate members exist
                available = engine.list_agents()
                if "members" in body:
                    members = body["members"]
                    invalid = [m for m in members if m not in available]
                    if invalid:
                        self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                        return
                    cfg["team"]["members"] = members
                if "head" in body:
                    new_head = body["head"]
                    # Ensure head is in members
                    if new_head not in cfg["team"].get("members", []):
                        cfg["team"]["members"].insert(0, new_head)
                    cfg["team"]["head"] = new_head
                    # If head changed, need to move team config to new head agent
                    old_head = cfg["team"].get("head", team_id)
                    if new_head != team_id:
                        # Move team config to new head's agent.json
                        new_cfg_path = os.path.join(engine.AGENTS_DIR, new_head, "agent.json")
                        with open(new_cfg_path, "r") as f:
                            new_cfg = json.load(f)
                        new_cfg["team"] = cfg.pop("team")
                        with open(new_cfg_path, "w") as f:
                            json.dump(new_cfg, f, indent=2)
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2)
                        self._send_json({"status": "updated", "team_id": new_head, "head": new_head})
                        return
                if "name" in body:
                    cfg["team"]["name"] = body["name"]
                if "description" in body:
                    cfg["team"]["description"] = body["description"]
                if "avatar" in body:
                    cfg["team"]["avatar"] = body["avatar"]
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "updated", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "dissolve":
            team_id = body.get("team_id", body.get("team_head", body.get("agent", "")))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                cfg.pop("team", None)
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "dissolved", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "move":
            agent_id = body.get("agent", "")
            from_team = body.get("from_team", "")
            to_team = body.get("to_team", "")
            if not agent_id:
                self._send_json({"error": "agent is required"}, 400)
                return
            try:
                # Remove from source team
                if from_team:
                    src_path = os.path.join(engine.AGENTS_DIR, from_team, "agent.json")
                    with open(src_path, "r") as f:
                        src_cfg = json.load(f)
                    if isinstance(src_cfg.get("team"), dict):
                        members = src_cfg["team"].get("members", [])
                        if agent_id in members:
                            members.remove(agent_id)
                            src_cfg["team"]["members"] = members
                        with open(src_path, "w") as f:
                            json.dump(src_cfg, f, indent=2)

                # Add to destination team
                if to_team:
                    dst_path = os.path.join(engine.AGENTS_DIR, to_team, "agent.json")
                    with open(dst_path, "r") as f:
                        dst_cfg = json.load(f)
                    if isinstance(dst_cfg.get("team"), dict):
                        members = dst_cfg["team"].get("members", [])
                        if agent_id not in members:
                            members.append(agent_id)
                            dst_cfg["team"]["members"] = members
                        with open(dst_path, "w") as f:
                            json.dump(dst_cfg, f, indent=2)

                self._send_json({"status": "moved", "agent": agent_id, "from": from_team, "to": to_team})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Workflow Handlers ---

    def _handle_workflow_list(self, path):
        """GET /v1/agents/{id}/workflows — list workflows for an agent.
        Path may also be /v1/agents/{id}/workflows/{name} (single source) or
        /v1/agents/{id}/workflows/tools (tool palette metadata).
        """
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        parts = path.split("/")
        # /v1/agents/{id}/workflows/tools  → tool palette
        if len(parts) >= 6 and parts[5] == "tools":
            palette = []
            for td in engine.TOOL_DEFINITIONS:
                schema = td.get("input_schema") or {}
                props = schema.get("properties") or {}
                required = set(schema.get("required") or [])
                args = []
                for pname, pdef in props.items():
                    args.append({
                        "name": pname,
                        "type": pdef.get("type", "string"),
                        "description": pdef.get("description", ""),
                        "required": pname in required,
                    })
                palette.append({
                    "name": td.get("name"),
                    "description": td.get("description", ""),
                    "args": args,
                    "primary_field": td.get("primary_field", ""),
                })
            self._send_json({"tools": palette})
            return
        # /v1/agents/{id}/workflows/{name} → return source
        if len(parts) >= 6 and parts[5]:
            name = parts[5]
            src = engine.WorkflowEngine.get_workflow_source(agent_id, name)
            if src is None:
                self._send_json({"error": "Workflow not found"}, 404)
                return
            self._send_json({"agent": agent_id, "name": name, "source": src})
            return
        workflows = engine.WorkflowEngine.list_workflows(agent_id)
        self._send_json({"agent": agent_id, "workflows": workflows})

    def _handle_workflow_save(self, path):
        """POST /v1/agents/{id}/workflows — save a workflow definition (text source)."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        name = body.get("name", "")
        # Accept either "source" (new) or "definition" (legacy) field
        source = body.get("source") or body.get("definition") or ""
        if not name:
            self._send_json({"error": "name is required"}, 400)
            return
        if not source:
            self._send_json({"error": "source is required"}, 400)
            return
        # Sanitize filename
        safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", name)[:64]
        if not safe:
            self._send_json({"error": "invalid name"}, 400)
            return
        try:
            fpath = engine.WorkflowEngine.save_workflow(agent_id, safe, source)
            self._send_json({"status": "saved", "name": safe, "path": fpath})
        except Exception as e:
            self._send_json({"error": str(e)}, 400)

    def _handle_workflow_delete(self, path):
        """DELETE /v1/agents/{id}/workflows/{name} — delete a workflow."""
        parts = path.split("/")
        # /v1/agents/{id}/workflows/{name}
        if len(parts) < 6:
            self._send_json({"error": "Invalid path"}, 400)
            return
        agent_id = parts[3]
        wf_name = parts[5]
        if engine.WorkflowEngine.delete_workflow(agent_id, wf_name):
            self._send_json({"status": "deleted", "name": wf_name})
        else:
            self._send_json({"error": "Workflow not found"}, 404)

    def _handle_workflow_run(self, path):
        """POST /v1/agents/{id}/workflows/{name}/run — start a workflow execution."""
        parts = path.split("/")
        if len(parts) < 7:
            self._send_json({"error": "Invalid path"}, 400)
            return
        agent_id = parts[3]
        wf_name = parts[5]
        body = self._read_json()
        variables = body.get("variables", {})
        model = body.get("model")
        trigger_kind = body.get("trigger_kind") or "manual"
        trigger_ref = body.get("trigger_ref") or ""
        # Pull caller identity from auth context
        au = getattr(self, "_auth_user", None) or {}
        user_id = au.get("id") or ""
        user_display = au.get("display_name") or au.get("username") or ""
        try:
            execution = engine.workflow_start(
                agent_id, wf_name, variables, model,
                user_id=user_id, user_display=user_display,
                trigger_kind=trigger_kind, trigger_ref=trigger_ref,
            )
            self._send_json({"execution_id": execution.execution_id, "status": execution.status})
        except Exception as e:
            self._send_json({"error": str(e)}, 400)

    def _handle_workflow_list_executions(self):
        """GET /v1/workflows/executions — list running/recent executions."""
        executions = engine.workflow_list_executions()
        self._send_json({"executions": executions})

    def _handle_workflow_get_execution(self, path):
        """GET /v1/workflows/executions/{id} — execution status with stage results."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        self._send_json(ex.to_dict())

    def _handle_workflow_approve(self, path):
        """POST /v1/workflows/executions/{id}/approve — approve an approval gate."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if ex.status != "waiting_approval":
            self._send_json({"error": f"Execution is not waiting for approval (status: {ex.status})"}, 400)
            return
        body = self._read_json()
        action = body.get("action", "approve")
        if action == "reject":
            ex.reject()
            self._send_json({"status": "rejected", "execution_id": exec_id})
        else:
            ex.approve()
            self._send_json({"status": "approved", "execution_id": exec_id})

    def _handle_workflow_cancel(self, path):
        """POST /v1/workflows/executions/{id}/cancel — cancel execution.

        Live execution → ex.cancel(). If the in-memory execution is gone (server
        restart killed it but the workflow_history row still says 'running'),
        the row is finalised as 'cancelled' so it leaves the running set.
        """
        import datetime
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if ex:
            ex.cancel()
            self._send_json({"status": "cancelled", "execution_id": exec_id})
            return
        # Zombie row — no live execution but DB may still say running.
        from brain import _workflow_history_get, _workflow_history_finalize
        row = _workflow_history_get(exec_id)
        if not row:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if row.get("status") not in ("running", "pending", "waiting_approval"):
            self._send_json({"error": f"Execution already terminal (status: {row.get('status')})"}, 400)
            return
        # RBAC: non-admins can only cancel their own runs.
        au = getattr(self, "_auth_user", None) or {}
        if au.get("role") != "admin":
            owner = row.get("user_id") or ""
            if owner and owner != (au.get("id") or ""):
                self._send_json({"error": "Forbidden"}, 403)
                return
        now_iso = datetime.datetime.now().isoformat()
        try:
            started_at = row.get("started_at") or now_iso
            started_dt = datetime.datetime.fromisoformat(started_at)
            duration_ms = int((datetime.datetime.now() - started_dt).total_seconds() * 1000)
        except Exception:
            duration_ms = 0
        _workflow_history_finalize(
            exec_id, "cancelled", now_iso, duration_ms,
            "Cancelled (no live execution — likely interrupted by server restart)",
            "", row.get("steps_json") or "[]",
        )
        self._send_json({"status": "cancelled", "execution_id": exec_id, "zombie": True})

    def _handle_workflow_history(self, path, query_params=None):
        """GET /v1/workflows/history — list execution history with filtering.
        Query: workflow=<name>, user=<id>, status=<state>, mine=1, limit=N, offset=N.
        Non-admins always get scoped to their own runs (mine=1 implied).
        """
        # `path` arrives without query string — read raw self.path for the qs.
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(getattr(self, "path", ""))
        qs = parse_qs(parsed.query)
        wf_filter = (qs.get("workflow") or [None])[0]
        status_filter = (qs.get("status") or [None])[0]
        user_filter = (qs.get("user") or [None])[0]
        mine = (qs.get("mine") or [""])[0] in ("1", "true", "yes")
        try:
            limit = max(1, min(int((qs.get("limit") or ["50"])[0]), 500))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int((qs.get("offset") or ["0"])[0]))
        except ValueError:
            offset = 0
        # RBAC: admins can see all; non-admins always restricted to their own.
        au = getattr(self, "_auth_user", None) or {}
        is_admin = (au.get("role") == "admin")
        if not is_admin or mine:
            user_filter = au.get("id") or ""
            if not user_filter:
                self._send_json({"executions": []})
                return
        rows = engine._workflow_history_list(
            workflow_name=wf_filter, user_id=user_filter,
            status=status_filter, limit=limit, offset=offset,
        )
        self._send_json({"executions": rows, "limit": limit, "offset": offset})

    def _handle_workflow_history_delete_run(self, path):
        """DELETE /v1/workflows/history/{execution_id} — delete one history row.
        Refuses in-flight runs (caller must cancel first). Non-admins can only
        delete their own runs."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        from brain import _workflow_history_get, _workflow_history_delete_run
        row = _workflow_history_get(exec_id)
        if not row:
            self._send_json({"status": "ok", "deleted": 0})
            return
        au = getattr(self, "_auth_user", None) or {}
        if au.get("role") != "admin":
            owner = row.get("user_id") or ""
            if owner and owner != (au.get("id") or ""):
                self._send_json({"error": "Forbidden"}, 403)
                return
        result = _workflow_history_delete_run(exec_id)
        if result.get("error"):
            self._send_json(result, 400)
            return
        self._send_json(result)

    def _handle_workflow_history_delete_bulk(self):
        """DELETE /v1/workflows/history — bulk purge.
        Query params:
          - workflow=<name> : delete only that workflow's runs
          - mine=1          : restrict to caller's own runs (implicit for non-admins)
        Without `workflow`, deletes every terminal run the caller can see."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(getattr(self, "path", ""))
        qs = parse_qs(parsed.query)
        wf = (qs.get("workflow") or [None])[0]
        mine = (qs.get("mine") or [""])[0] in ("1", "true", "yes")
        au = getattr(self, "_auth_user", None) or {}
        is_admin = (au.get("role") == "admin")
        # Non-admins are always scoped to their own runs.
        scope_user = au.get("id") or ""
        if (not is_admin) or mine:
            if not scope_user:
                self._send_json({"status": "ok", "runs_removed": 0})
                return
        else:
            scope_user = None  # admin & not-mine → unrestricted
        from brain import _workflow_history_delete_for_workflow, _workflow_history_delete_all
        if wf:
            result = _workflow_history_delete_for_workflow(wf, user_id=scope_user)
        else:
            result = _workflow_history_delete_all(user_id=scope_user)
        if result.get("error"):
            self._send_json(result, 400)
            return
        self._send_json(result)

    def _handle_workflow_history_get(self, path):
        """GET /v1/workflows/history/{execution_id} — full row including steps + variables."""
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        row = engine._workflow_history_get(exec_id)
        if not row:
            self._send_json({"error": "Not found"}, 404)
            return
        # RBAC: non-admins can only see own runs
        au = getattr(self, "_auth_user", None) or {}
        if au.get("role") != "admin":
            owner = row.get("user_id") or ""
            if owner and owner != (au.get("id") or ""):
                self._send_json({"error": "Forbidden"}, 403)
                return
        # Decode JSON columns
        for k in ("variables_json", "steps_json"):
            v = row.get(k)
            if isinstance(v, str) and v:
                try:
                    row[k.replace("_json", "")] = json.loads(v)
                except Exception:
                    pass
        self._send_json(row)

    def _handle_workflow_upload_file(self, path):
        """POST /v1/workflows/executions/{id}/upload-file — multipart upload to satisfy
        a paused ask_user_for_file call. Saves the file under /tmp/brain-workflow-uploads/
        and delivers (path, filename, size_bytes) to the waiting workflow thread.
        """
        parts = path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        ex = engine.workflow_get_execution(exec_id)
        if not ex:
            self._send_json({"error": "Execution not found"}, 404)
            return
        # Confirm the workflow is actually waiting for a file
        if exec_id not in engine._workflow_file_pending:
            self._send_json({"error": "Workflow is not waiting for a file upload"}, 400)
            return
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        if not content_length:
            self._send_json({"error": "No content"}, 400)
            return
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "Expected multipart/form-data"}, 400)
            return
        body = self.rfile.read(content_length)
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break
        if not boundary:
            self._send_json({"error": "No boundary"}, 400)
            return
        delimiter = f"--{boundary}".encode()
        chunks = body.split(delimiter)
        filename = None
        file_data = None
        for chunk in chunks:
            if not chunk or chunk in (b"--\r\n", b"--"):
                continue
            if b"\r\n\r\n" in chunk:
                hdr, part_body = chunk.split(b"\r\n\r\n", 1)
            elif b"\n\n" in chunk:
                hdr, part_body = chunk.split(b"\n\n", 1)
            else:
                continue
            if part_body.endswith(b"\r\n"):
                part_body = part_body[:-2]
            header_text = hdr.decode("utf-8", errors="replace")
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
        if not filename or file_data is None:
            self._send_json({"error": "No file uploaded"}, 400)
            return
        # Persist to /tmp/brain-workflow-uploads/<exec_id>/<filename>
        upload_root = os.path.join("/tmp", "brain-workflow-uploads", exec_id)
        os.makedirs(upload_root, exist_ok=True)
        # Sanitize filename (keep extension)
        safe_filename = re.sub(r"[^A-Za-z0-9_\-\. ]+", "_", filename) or "upload"
        full_path = os.path.join(upload_root, safe_filename)
        with open(full_path, "wb") as f:
            f.write(file_data)
        size_bytes = len(file_data)
        delivered = engine.deliver_workflow_file_answer(exec_id, {
            "path": full_path,
            "filename": safe_filename,
            "size_bytes": size_bytes,
        })
        if not delivered:
            self._send_json({"error": "Workflow no longer waiting"}, 400)
            return
        self._send_json({
            "status": "delivered",
            "execution_id": exec_id,
            "path": full_path,
            "filename": safe_filename,
            "size_bytes": size_bytes,
        })

    def _handle_workflow_promote_session(self, path):
        """POST /v1/workflows/history/{exec_id}/promote-session/{sid}

        Flips a hidden workflow_run-bound session to status='active' so it
        appears in the sidebar — the "Save to chats" action from the inline
        workflow detail view. Refuses if the session isn't bound to this
        execution_id (prevents promoting an unrelated session by accident).
        """
        parts = path.split("/")
        # /v1/workflows/history/<exec_id>/promote-session/<sid>
        if len(parts) < 7:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        sid = parts[6]
        info = ChatDB.get_session_info(sid)
        if not info:
            self._send_json({"error": "Session not found"}, 404)
            return
        if (info.get("workflow_run_id") or "") != exec_id:
            self._send_json({"error": "Session is not bound to this workflow run"}, 400)
            return
        au = getattr(self, "_auth_user", None) or {}
        if au.get("role") != "admin":
            owner = info.get("user_id") or ""
            if owner and owner != (au.get("id") or ""):
                self._send_json({"error": "Forbidden"}, 403)
                return
        ChatDB.update_session_status(sid, "active")
        # Re-run the seed pass — idempotent, dedupes by path. Covers the
        # legacy case where a session pre-dating v8.24.3 was created
        # without seeding (the session-create endpoint now handles it on
        # first open, so this is normally a no-op).
        agent_id = info.get("agent_id") or "main"
        artifacts_created, references = self._seed_artifacts_for_run(
            exec_id, sid, agent_id)
        self._send_json({
            "status": "promoted",
            "session_id": sid,
            "execution_id": exec_id,
            "artifacts_created": artifacts_created,
            "references": references,
        })

    def _handle_workflow_get_or_create_session(self, path):
        """POST /v1/workflows/history/<exec_id>/session

        Look up (or create) the caller's bound chat session for this run.
        Used by the inline-detail entry point so re-opening the same run
        doesn't spawn a new session every time. Returns:
          { session_id, status, created: bool }

        Body (optional): { "model": "..." } — model to use when minting a
        fresh session. Required only on first open.
        """
        parts = path.split("/")
        if len(parts) < 6:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        from brain import _workflow_history_get
        row = _workflow_history_get(exec_id)
        if not row:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if not self._workflow_run_can_access(row):
            self._send_json({"error": "Forbidden"}, 403)
            return
        au = getattr(self, "_auth_user", None) or {}
        uid = au.get("id") or ""
        # Look for an existing chat session bound to this run (any status).
        existing = self._lookup_workflow_run_session(exec_id, uid)
        if existing:
            # Re-derive refs/artifacts even on existing-session lookups so
            # the client can re-seed state.chatReferences[sid] on page
            # reload (chatReferences is in-memory only). Idempotent.
            agent_id = existing.get("agent_id") or "main"
            artifacts_seeded, references_seeded = self._seed_artifacts_for_run(
                exec_id, existing["id"], agent_id)
            self._send_json({
                "session_id": existing["id"],
                "status": existing.get("status", "active"),
                "created": False,
                "model": existing.get("model", ""),
                "artifacts": artifacts_seeded,
                "references": references_seeded,
            })
            return
        # No bound session yet — mint one.
        body = self._read_json() if self.headers.get("Content-Length") else {}
        model = body.get("model") or row.get("model") or ""
        if not model:
            # Fall back to the user's default model if any
            try:
                models = engine._models_config or {}
                # Pick the first enabled model as last resort
                for mid, mcfg in models.items():
                    if mcfg.get("enabled", True):
                        model = mid
                        break
            except Exception:
                pass
        if not model:
            self._send_json({"error": "No model available — pick one in chat first"}, 400)
            return
        agent_id = row.get("agent_id") or "main"
        provider = self._resolve_provider(model)
        new_session = sessions.create(
            agent_id=agent_id, model=model,
            api_key=provider["api_key"], base_url=provider["base_url"],
            max_context=engine.get_model_max_context(model),
        )
        new_session.user_id = uid
        new_session.workflow_run_id = exec_id
        new_session.status = "workflow_run"
        ChatDB.save_session(new_session.id, agent_id, model, "",
                            "workflow_run", new_session.created_at,
                            new_session.last_active, "",
                            user_id=uid, workflow_run_id=exec_id)
        # Seed the regular Artifacts + References panels so the workflow's
        # files behave exactly like any other chat — no banner-internal
        # widgets, no special UI. Outputs become artifact rows under this
        # session_id (the regular /v1/artifacts endpoint serves them);
        # inputs are returned in the response and the client populates
        # state.chatReferences[sid] so the References tab lights up.
        artifacts_seeded, references_seeded = self._seed_artifacts_for_run(
            exec_id, new_session.id, agent_id)
        self._send_json({
            "session_id": new_session.id, "status": "workflow_run",
            "created": True, "model": model,
            "artifacts": artifacts_seeded,
            "references": references_seeded,
        })

    def _seed_artifacts_for_run(self, exec_id, session_id, agent_id):
        """For every file the run touched: register output paths as
        artifacts under the bound session, and gather input paths to be
        returned to the client for chatReferences seeding. Idempotent —
        safe to call multiple times for the same session (de-dupes by
        path). Files ≤5MB get a content version snapshot so the artifact
        viewer can render them; bigger files leave content NULL and the
        existing disk-fallback path serves bytes from artifact.path.
        """
        try:
            classified_result = self._workflow_run_paths_classified(exec_id)
        except Exception:
            classified_result = None
        if not classified_result:
            return [], []
        _, classified = classified_result
        # Existing artifact paths under this session — avoid duplicate rows
        # if the user re-opens the same run after we've already seeded.
        existing_paths = set()
        try:
            for art in (ChatDB.get_artifacts(session_id) or []):
                p = art.get("path")
                if p:
                    existing_paths.add(p)
        except Exception:
            pass
        artifacts_out = []
        references_out = []
        SNAPSHOT_CAP = 5 * 1024 * 1024  # 5 MB — matches artifact_versions limit
        for realpath, role in classified.items():
            if not os.path.isfile(realpath):
                continue
            name = os.path.basename(realpath)
            if role == "output":
                if realpath in existing_paths:
                    continue
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else "file"
                art_id = uuid.uuid4().hex[:12]
                ChatDB.create_artifact(art_id, session_id, agent_id, name,
                                       realpath, ext, role="output")
                # Optional content snapshot — gives the viewer something to
                # render without a separate disk-read endpoint, AND survives
                # the underlying file getting deleted from /tmp.
                try:
                    size = os.path.getsize(realpath)
                except OSError:
                    size = 0
                content = None
                if 0 < size <= SNAPSHOT_CAP:
                    try:
                        with open(realpath, "rb") as f:
                            content = f.read()
                    except Exception:
                        content = None
                try:
                    ChatDB.add_artifact_version(art_id, 1, content, size, 0, "created")
                except Exception:
                    pass
                artifacts_out.append({"id": art_id, "name": name, "path": realpath})
            elif role == "input":
                references_out.append({"name": name, "path": realpath})
        return artifacts_out, references_out

    @staticmethod
    def _lookup_workflow_run_session(exec_id, user_id):
        """Find the most recent chat session bound to this workflow run for
        this user. Empty user_id matches legacy/anonymous rows."""
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                if user_id:
                    row = conn.execute(
                        """SELECT * FROM sessions
                            WHERE workflow_run_id = ? AND (user_id = ? OR user_id = '')
                            ORDER BY last_active DESC LIMIT 1""",
                        (exec_id, user_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT * FROM sessions
                            WHERE workflow_run_id = ?
                            ORDER BY last_active DESC LIMIT 1""",
                        (exec_id,),
                    ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    # --- Workflow run file access ---
    #
    # The general /v1/files/download validator restricts paths to the cctest
    # tree, agents/, cwd, and project input folders — workflows often write
    # outputs to /tmp (and uploaded files live under /tmp/brain-workflow-
    # uploads/<exec_id>/) so those would be rejected. We can't loosen the
    # general validator without opening up /tmp blanket, so workflow file
    # access goes through a scoped endpoint that gates on:
    #   1. The execution_id exists and the user can see it (RBAC).
    #   2. The requested path actually appears in steps_json or return_value
    #      for that run — i.e. the workflow demonstrably touched it.
    # That's narrow enough to be safe and broad enough to cover both
    # uploads and outputs without per-tool special-casing.

    # Tools whose path arguments produce reference files (read-only inputs).
    _WF_INPUT_TOOLS = frozenset({
        "read_file", "read_document", "read_attachment",
        "transcribe_audio", "parse_pdf", "parse_docx",
        "ask_user_for_file",
    })
    # Tools whose path arguments produce output files (workflow artifacts).
    _WF_OUTPUT_TOOLS = frozenset({"write_file", "edit_file"})

    def _workflow_run_paths(self, exec_id):
        """Return the set of file paths a workflow run demonstrably touched.

        Pulled from steps_json (every `call`/`call_done`/`error` row's
        `detail` string), plus `return_value` when it looks like a path.
        Heuristic — same regex the frontend uses to render the references
        and artifacts lists, so what the user sees is exactly what they
        can download.

        Returns (row, allowed_paths_set).
        """
        result = self._workflow_run_paths_classified(exec_id)
        if result is None:
            return None, None
        row, classified = result
        return row, set(classified.keys())

    def _workflow_run_paths_classified(self, exec_id):
        """Like _workflow_run_paths but returns role per path.

        Returns (row, {realpath: 'input'|'output'|'unknown'}).
        Used by promote-session to decide which paths to register as
        artifact rows on the freshly-promoted chat session.
        """
        from brain import _workflow_history_get
        row = _workflow_history_get(exec_id)
        if not row:
            return None
        try:
            steps = json.loads(row.get("steps_json") or "[]")
        except Exception:
            steps = []
        arg_re = re.compile(r"""\b(?:path|file|file_path|filename|audio|audio_path|image|image_path|pdf|pdf_path|src|source|input|output|target|dest|content_path)\s*=\s*(['"])((?:\\.|(?!\1).)+)\1""")
        path_re = re.compile(r"""(['"])((?:/|~/)[^'"\n]+)\1""")
        # Two emit shapes: "tool_name(args)" on call lines, "tool_name → result"
        # on call_done lines. The arrow shape is where the actual path lives
        # for tools whose call line uses placeholder ellipses (transcribe_audio,
        # write_file). Without this we'd lose role classification on call_done.
        tool_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|→|->)")
        # path -> best role seen so far. output > input > unknown.
        order = {"unknown": 0, "input": 1, "output": 2}
        classified: dict[str, str] = {}
        def claim(path: str, role: str):
            real = os.path.realpath(os.path.expanduser(path))
            existing = classified.get(real, "unknown")
            if order[role] > order[existing]:
                classified[real] = role
        for s in steps:
            d = (s.get("detail") or "")
            if not d:
                continue
            tm = tool_re.match(d)
            tool = tm.group(1) if tm else ""
            role = ("output" if tool in self._WF_OUTPUT_TOOLS
                    else "input" if tool in self._WF_INPUT_TOOLS
                    else "unknown")
            for m in arg_re.finditer(d):
                claim(m.group(2).replace("\\\\", "\\").replace("\\'", "'").replace('\\"', '"'), role)
            for m in path_re.finditer(d):
                claim(m.group(2), role)
        # return_value as a bare path string → output (it's what the
        # workflow handed back to the caller).
        rv = row.get("return_value")
        if rv:
            try:
                parsed = json.loads(rv) if isinstance(rv, str) else rv
            except Exception:
                parsed = rv
            if isinstance(parsed, str) and (parsed.startswith("/") or parsed.startswith("~/")):
                claim(parsed, "output")
        return row, classified

    def _workflow_run_can_access(self, row):
        au = getattr(self, "_auth_user", None) or {}
        if au.get("role") == "admin":
            return True
        owner = (row or {}).get("user_id") or ""
        if not owner:
            return True  # legacy anonymous run
        return owner == (au.get("id") or "")

    def _handle_workflow_run_file_download(self, path):
        """GET /v1/workflows/history/<exec_id>/file?path=<urlenc>"""
        from urllib.parse import urlparse, parse_qs, quote as _urlq
        parts = path.split("/")
        if len(parts) < 6:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        qs = parse_qs(urlparse(self.path).query)
        requested = qs.get("path", [""])[0]
        if not requested:
            self._send_json({"error": "Missing path"}, 400)
            return
        row, allowed = self._workflow_run_paths(exec_id)
        if row is None:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if not self._workflow_run_can_access(row):
            self._send_json({"error": "Forbidden"}, 403)
            return
        try:
            resolved = os.path.realpath(os.path.expanduser(requested))
        except Exception:
            self._send_json({"error": "Invalid path"}, 400)
            return
        if resolved not in allowed:
            self._send_json({"error": "Path not associated with this run"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        ext = resolved.rsplit(".", 1)[-1].lower() if "." in resolved else ""
        content_types = {
            "md": "text/markdown", "txt": "text/plain", "py": "text/x-python",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "html": "text/html", "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "js": "application/javascript", "ts": "text/typescript",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "svg": "image/svg+xml",
            "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
        }
        ct = content_types.get(ext, "application/octet-stream")
        filename = os.path.basename(resolved)
        inline_exts = {"pdf", "png", "jpg", "jpeg", "gif", "svg",
                       "txt", "md", "html", "json", "csv"}
        disposition = "inline" if ext in inline_exts else "attachment"
        try:
            with open(resolved, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(data))
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{_urlq(filename)}",
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_workflow_run_file_preview(self, path):
        """GET /v1/workflows/history/<exec_id>/file-preview?path=<urlenc>&lines=200

        Same gate as the download endpoint; returns either a small JSON
        envelope with text content (for text files) or a marker telling
        the client to fall back to download for binary types.
        """
        from urllib.parse import urlparse, parse_qs
        parts = path.split("/")
        if len(parts) < 6:
            self._send_json({"error": "Invalid path"}, 400)
            return
        exec_id = parts[4]
        qs = parse_qs(urlparse(self.path).query)
        requested = qs.get("path", [""])[0]
        try:
            max_lines = int(qs.get("lines", ["200"])[0])
        except ValueError:
            max_lines = 200
        if not requested:
            self._send_json({"error": "Missing path"}, 400)
            return
        row, allowed = self._workflow_run_paths(exec_id)
        if row is None:
            self._send_json({"error": "Execution not found"}, 404)
            return
        if not self._workflow_run_can_access(row):
            self._send_json({"error": "Forbidden"}, 403)
            return
        try:
            resolved = os.path.realpath(os.path.expanduser(requested))
        except Exception:
            self._send_json({"error": "Invalid path"}, 400)
            return
        if resolved not in allowed:
            self._send_json({"error": "Path not associated with this run"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            size = os.path.getsize(resolved)
            name = os.path.basename(resolved)
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            image_exts = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"}
            audio_exts = {"wav", "mp3", "m4a", "aac", "ogg", "flac"}
            office_exts = {"pdf", "docx", "xlsx", "pptx", "csv"}
            if ext in image_exts:
                self._send_json({"path": resolved, "name": name, "size": size,
                                 "type": "image", "ext": ext})
                return
            if ext in audio_exts:
                self._send_json({"path": resolved, "name": name, "size": size,
                                 "type": "audio", "ext": ext})
                return
            if ext in office_exts:
                try:
                    if ext == "pdf":
                        content = engine.DocumentParser.parse_pdf(resolved)
                    elif ext == "docx":
                        content = engine.DocumentParser.parse_docx(resolved)
                    elif ext in ("xlsx", "xls"):
                        content = engine.DocumentParser.parse_xlsx(resolved)
                    elif ext == "pptx":
                        content = engine.DocumentParser.parse_pptx(resolved)
                    elif ext == "csv":
                        with open(resolved, "r", errors="replace") as f:
                            content = f.read(50 * 1024)
                    else:
                        content = ""
                    all_lines = content.splitlines()
                    truncated = len(all_lines) > 400
                    self._send_json({
                        "path": resolved, "name": name, "size": size,
                        "type": "document", "ext": ext,
                        "content": "\n".join(all_lines[:400]), "truncated": truncated,
                    })
                except Exception as e:
                    self._send_json({"error": f"Could not parse {ext.upper()}: {e}"}, 500)
                return
            # Plain text / code
            max_bytes = 200 * 1024
            with open(resolved, "r", errors="replace") as f:
                lines = []
                total_bytes = 0
                for i, line in enumerate(f):
                    if i >= max_lines or total_bytes >= max_bytes:
                        truncated = True
                        break
                    lines.append(line)
                    total_bytes += len(line.encode("utf-8"))
                else:
                    truncated = False
            self._send_json({
                "path": resolved, "name": name, "size": size,
                "type": "text", "ext": ext,
                "content": "".join(lines), "truncated": truncated,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Agent file management ---

    def _handle_agent_files(self, path):
        """GET /v1/agents/<id>/files — list agent files."""
        parts = path.split("/")
        agent_id = parts[3]
        agent = engine.AgentConfig(agent_id)
        files = []
        if os.path.isdir(agent.dir):
            for f in sorted(os.listdir(agent.dir)):
                fp = os.path.join(agent.dir, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size": os.path.getsize(fp)})
        skills = agent.list_skills()
        self._send_json({"agent": agent_id, "files": files, "skills": skills})

    def _handle_agent_file_read(self, path):
        """GET /v1/agents/<id>/file?name=soul.md — read a file."""
        from urllib.parse import unquote
        parts = path.split("/")
        agent_id = parts[3]
        # Parse query string
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        filename = unquote(params.get("name", ""))
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        if not os.path.isfile(filepath):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            with open(filepath, "r") as f:
                content = f.read()
            self._send_json({"name": filename, "content": content})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_agent_file_write(self, path):
        """POST /v1/agents/<id>/file — write a file."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        filename = body.get("name", "")
        content = body.get("content", "")
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        try:
            with open(filepath, "w") as f:
                f.write(content)
            self._send_json({"status": "saved", "name": filename})
            if filename.endswith(".md"):
                self._qmd_trigger_update()
            # Re-sync memory summary schedules when agent.json changes
            if filename == "agent.json":
                try:
                    engine.ensure_memory_summary_schedules()
                except Exception:
                    pass
            # Invalidate warm pool if the main agent's system-prompt inputs
            # changed — the pooled KV prefix would no longer match the real
            # first-turn payload.
            if (agent_id == WarmSessionPool.POOL_AGENT
                    and filename in ("soul.md", "agent.json", "tools.md")):
                warm_pool.invalidate_all(f"{agent_id}/{filename} edited")
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_create_agent(self):
        """POST /v1/agents/create — create a new agent."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or ".." in agent_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        agent = engine.AgentConfig(agent_id)  # auto-creates defaults
        cfg_dirty = False
        cfg = agent.config
        for field in ("description", "model", "display_name"):
            if body.get(field):
                cfg[field] = body[field]
                cfg_dirty = True
        if cfg_dirty:
            with open(os.path.join(agent.dir, "agent.json"), "w") as f:
                json.dump(cfg, f, indent=2)
        if body.get("soul"):
            with open(os.path.join(agent.dir, "soul.md"), "w") as f:
                f.write(body["soul"])
        # Register QMD collection for the new agent
        self._qmd_register_collection(agent_id, agent.dir)
        self._send_json({"status": "created", "agent": agent_id})

    def _handle_delete_agent(self):
        """POST /v1/agents/delete — soft-delete an agent (move to .trash)."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or agent_id == "main" or ".." in agent_id:
            self._send_json({"error": "Cannot delete this agent"}, 400)
            return
        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent '{agent_id}' not found"}, 404)
            return
        try:
            trash_dir = os.path.join(engine.AGENTS_DIR, ".trash")
            os.makedirs(trash_dir, exist_ok=True)
            import shutil
            dest = os.path.join(trash_dir, f"{agent_id}_{int(time.time())}")
            shutil.move(agent_dir, dest)
            # Remove QMD collection for deleted agent
            self._qmd_remove_collection(agent_id)
            # Remove scheduled tasks for deleted agent
            try:
                if engine._scheduler:
                    for s in engine._scheduler.list_all():
                        if s.get("agent") == agent_id:
                            engine._scheduler.remove(s["name"])
            except Exception:
                pass
            self._send_json({"status": "deleted", "agent": agent_id, "moved_to": dest})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_rename_agent(self):
        """POST /v1/agents/rename — rename an agent directory and update QMD collection."""
        body = self._read_json()
        old_id = body.get("agent", "")
        new_id = body.get("new_name", "").strip()
        if not old_id or not new_id or ".." in old_id or ".." in new_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        if old_id == new_id:
            self._send_json({"status": "ok", "agent": new_id})
            return
        if old_id == "main":
            self._send_json({"error": "Cannot rename the main agent"}, 400)
            return
        # Validate new_id: alphanumeric + hyphens/underscores only
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_-]+$', new_id):
            self._send_json({"error": "Agent name must be alphanumeric (hyphens/underscores allowed)"}, 400)
            return
        old_dir = os.path.join(engine.AGENTS_DIR, old_id)
        new_dir = os.path.join(engine.AGENTS_DIR, new_id)
        if not os.path.isdir(old_dir):
            self._send_json({"error": f"Agent '{old_id}' not found"}, 404)
            return
        if os.path.exists(new_dir):
            self._send_json({"error": f"Agent '{new_id}' already exists"}, 409)
            return
        try:
            os.rename(old_dir, new_dir)
            # Update QMD: remove old collection, add new one, re-index in background
            if self._is_qmd_running():
                self._qmd_run(["collection", "remove", old_id])
                self._qmd_run(["collection", "add", new_dir, "--name", new_id])
                self._qmd_trigger_update(delay=1.0)
            self._send_json({"status": "renamed", "agent": new_id, "old_name": old_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Skill installation (Claude SKILL.md format, zip upload) ---

    def _handle_install_skill_zip(self):
        """POST /v1/skills/install-zip — install skill from uploaded zip (base64 in JSON)."""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        zip_data_b64 = body.get("zip_data", "")
        skill_name = body.get("name", "")

        if not zip_data_b64:
            self._send_json({"error": "No zip data"}, 400)
            return

        try:
            import base64
            import zipfile
            import io

            zip_bytes = base64.b64decode(zip_data_b64)
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

            # Find SKILL.md in the zip
            skill_md_path = None
            for name in zf.namelist():
                if name.endswith("SKILL.md"):
                    skill_md_path = name
                    break

            if not skill_md_path:
                self._send_json({"error": "No SKILL.md found in zip"}, 400)
                return

            # Determine skill name from path or provided name
            parts = skill_md_path.split("/")
            if not skill_name:
                # Use parent directory name, or filename prefix
                if len(parts) >= 2:
                    skill_name = parts[-2]
                else:
                    skill_name = "imported-skill"

            # Extract all files to agent's skills directory
            agent = engine.AgentConfig(agent_id)
            skill_dir = os.path.join(agent.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)

            # Find the common prefix to strip
            prefix = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

            for zpath in zf.namelist():
                if zpath.endswith("/"):
                    continue
                # Strip prefix to get relative path
                rel = zpath[len(prefix):] if zpath.startswith(prefix) else zpath.split("/")[-1]
                dest = os.path.join(skill_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(zf.read(zpath))

            self._send_json({
                "status": "installed",
                "skill": skill_name,
                "agent": agent_id,
                "files": [n for n in zf.namelist() if not n.endswith("/")],
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_remove_skill(self):
        """POST /v1/skills/remove — remove a skill from an agent."""
        body = self._read_json()
        skill_name = body.get("skill", "")
        agent_id = body.get("agent", "main")
        if not skill_name:
            self._send_json({"error": "Skill name required"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        skill_dir = os.path.join(agent.skills_dir, skill_name)
        if not os.path.isdir(skill_dir):
            self._send_json({"error": f"Skill '{skill_name}' not found"}, 404)
            return
        try:
            import shutil
            shutil.rmtree(skill_dir)
            self._send_json({"status": "removed", "skill": skill_name, "agent": agent_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_cc_skills_list(self):
        """GET /v1/skills/claude-code — list all Claude Code skills/plugins."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        agent_id = query.get("agent", ["main"])[0]

        # Get all CC skills from the scanner
        all_skills = engine.scan_claude_code_skills()

        # Get agent's enabled CC skills list
        agent_cfg = engine.AgentConfig(agent_id)
        agent_cc = agent_cfg.config.get("claude_code_skills", [])

        # Annotate with per-agent enabled state
        for skill in all_skills:
            skill["agent_enabled"] = skill["slug"] in agent_cc

        self._send_json({"skills": all_skills, "agent": agent_id})

    def _handle_cc_skills_manage(self):
        """POST /v1/skills/claude-code — enable/disable CC skill for an agent.
        Body: {agent, slug, enabled}"""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        slug = body.get("slug", "")
        enabled = body.get("enabled", True)

        if not slug:
            self._send_json({"error": "slug required"}, 400)
            return

        agent_cfg = engine.AgentConfig(agent_id)
        config = dict(agent_cfg.config)
        cc_skills = list(config.get("claude_code_skills", []))

        if enabled and slug not in cc_skills:
            cc_skills.append(slug)
        elif not enabled and slug in cc_skills:
            cc_skills.remove(slug)

        config["claude_code_skills"] = cc_skills
        agent_cfg.save_config(config)

        self._send_json({"status": "ok", "agent": agent_id, "slug": slug,
                         "enabled": enabled, "claude_code_skills": cc_skills})

    def _handle_cc_browse(self):
        """POST /v1/skills/claude-code/browse — search CC plugin marketplace.
        Body: {query}"""
        body = self._read_json()
        query = body.get("query", "")
        plugins = engine.browse_claude_code_plugins(query)
        self._send_json({"plugins": plugins, "count": len(plugins)})

    def _handle_cc_install(self):
        """POST /v1/skills/claude-code/install — install a CC plugin.
        Body: {plugin, marketplace}"""
        body = self._read_json()
        plugin_name = body.get("plugin", "")
        marketplace = body.get("marketplace", "claude-plugins-official")
        if not plugin_name:
            self._send_json({"error": "plugin name required"}, 400)
            return
        result = engine.install_claude_code_plugin(plugin_name, marketplace)
        status = 200 if "status" in result else 500
        self._send_json(result, status)

    # --- Service Management ---

    @staticmethod
    def _find_qmd() -> str | None:
        """Find the qmd binary."""
        qmd = shutil.which("qmd")
        if qmd:
            return qmd
        for p in [os.path.expanduser("~/.nvm/versions/node"), "/usr/local/bin", "/opt/homebrew/bin"]:
            if os.path.isdir(p):
                for d in sorted(os.listdir(p), reverse=True):
                    candidate = os.path.join(p, d, "bin", "qmd") if "node" in p else os.path.join(p, "qmd")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    # Debounced QMD update: coalesce rapid file writes into one qmd update+embed run
    _qmd_update_timer: threading.Timer | None = None
    _qmd_update_lock = threading.Lock()

    @classmethod
    def _qmd_trigger_update(cls, delay: float = 2.0) -> None:
        """MemPalace migration: no-op. QMD is no longer used."""
        return

    @staticmethod
    def _qmd_run(args: list, timeout: int = 10) -> bool:
        """MemPalace migration: no-op. QMD is no longer used."""
        return False

    def _qmd_register_collection(self, agent_id: str, agent_dir: str) -> None:
        """Add a QMD collection for an agent if QMD is running and collection doesn't exist.
        Runs qmd update in a background thread so files are indexed promptly."""
        if not self._is_qmd_running():
            return
        existing = {(c["name"] if isinstance(c, dict) else c) for c in self._qmd_collections()}
        if agent_id not in existing:
            self._qmd_run(["collection", "add", agent_dir, "--name", agent_id])
            self._qmd_trigger_update(delay=1.0)

    def _qmd_remove_collection(self, agent_id: str) -> None:
        """Remove a QMD collection for a deleted agent."""
        if not self._is_qmd_running():
            return
        self._qmd_run(["collection", "remove", agent_id])

    @staticmethod
    def _is_qmd_running() -> bool:
        """MemPalace migration: QMD is no longer used; always return False so all
        QMD-dependent code paths short-circuit silently."""
        return False

    @staticmethod
    def _is_telegram_running() -> bool:
        try:
            return _telegram_mod.telegram_service.running
        except AttributeError:
            return False

    @staticmethod
    def _qmd_collections() -> list[dict]:
        try:
            qmd_bin = BrainAgentHandler._find_qmd()
            if not qmd_bin:
                return []
            qmd_env = os.environ.copy()
            qmd_env["PATH"] = os.path.dirname(qmd_bin) + ":" + qmd_env.get("PATH", "")
            r = subprocess.run([qmd_bin, "collection", "list"],
                               capture_output=True, text=True, timeout=5, env=qmd_env)
            if r.returncode != 0:
                return []
            collections = []
            current = None
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(("Collections", "Pattern", "Files", "Updated", "Ignore")) and "(" in line:
                    name = line.split("(")[0].strip()
                    if name:
                        current = {"name": name}
                        collections.append(current)
                elif current and line.startswith("Files:"):
                    current["files"] = line.split(":")[1].strip().split()[0]

            # Enrich with index health stats from QMD SQLite
            try:
                import sqlite3 as _sq3, hashlib as _hl
                idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
                if os.path.isfile(idx_path):
                    conn = _sq3.connect(idx_path, timeout=2)
                    conn.row_factory = _sq3.Row
                    for coll in collections:
                        name = coll["name"]
                        agent_dir = os.path.join(engine.AGENTS_DIR, name)
                        if not os.path.isdir(agent_dir):
                            continue
                        # Build index of QMD docs for this collection
                        rows = conn.execute(
                            "SELECT d.path, d.hash, "
                            "  (SELECT cv.embedded_at FROM content_vectors cv WHERE cv.hash = d.hash LIMIT 1) AS embedded_at "
                            "FROM documents d WHERE d.collection = ? AND d.active = 1",
                            (name,),
                        ).fetchall()
                        qmd_idx = {}
                        for row in rows:
                            qmd_idx[row["path"].lower()] = {"hash": row["hash"], "embedded_at": row["embedded_at"]}

                        # Walk filesystem and compute stats
                        total = 0
                        indexed = 0
                        embedded = 0
                        stale = 0
                        not_indexed = 0
                        for dirpath, _, filenames in os.walk(agent_dir):
                            for fname in filenames:
                                if not fname.endswith(".md"):
                                    continue
                                total += 1
                                fpath = os.path.join(dirpath, fname)
                                rel = os.path.relpath(fpath, agent_dir)
                                # QMD normalizes: lowercase + underscores→hyphens
                                norm = rel.lower().replace("_", "-")
                                idx = qmd_idx.get(norm)
                                if not idx:
                                    not_indexed += 1
                                    continue
                                # Check hash freshness
                                try:
                                    with open(fpath, "rb") as fh:
                                        file_hash = _hl.sha256(fh.read()).hexdigest()
                                    is_current = (file_hash == idx["hash"])
                                except OSError:
                                    is_current = None
                                if is_current:
                                    indexed += 1
                                else:
                                    stale += 1
                                if idx["embedded_at"] and is_current:
                                    embedded += 1

                        coll["total"] = total
                        coll["indexed"] = indexed
                        coll["embedded"] = embedded
                        coll["stale"] = stale
                        coll["not_indexed"] = not_indexed
                    conn.close()
            except Exception:
                pass

            return collections
        except Exception:
            pass
        return []

    def _handle_costs(self):
        """GET /v1/costs?agent=X&hours=24&user_id=Y — cost stats."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        stats = engine._cost_tracker.get_stats(agent=agent, hours=hours, user_id=target_uid)
        self._send_json(stats)

    def _handle_costs_daily(self):
        """GET /v1/costs/daily?agent=X&days=7&user_id=Y — daily breakdown."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        days = int(params.get("days", "7"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        daily = engine._cost_tracker.get_daily(agent=agent, days=days, user_id=target_uid)
        self._send_json({"daily": daily, "days": days, "agent_filter": agent, "user_id": target_uid})

    # --- Per-user cost quotas ---

    def _handle_quota_me(self):
        """GET /v1/quotas/me — current authenticated user's quota state."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            state = engine._quota_manager.get_user_state(user["id"])
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json(state)

    def _handle_quota_config_get(self):
        """GET /v1/quotas/config — admin-only. Full quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        self._send_json(engine._quota_manager.get_config())

    def _handle_quota_config_save(self):
        """POST /v1/quotas/config — admin-only. Update quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        body = self._read_json() or {}
        try:
            saved = engine._quota_manager.save_config(body)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        # Audit log so changes are traceable
        try:
            if engine._audit_log:
                engine._audit_log.log_action(
                    agent="main",
                    action_type="quota_config_save",
                    tool_name="quota",
                    args_summary=f"by={user.get('username','')} cycle={saved.get('billing_cycle')} enforce={saved.get('enforce_red')}",
                    result_status="ok",
                )
        except Exception:
            pass
        self._send_json(saved)

    def _handle_quota_admin_users(self):
        """GET /v1/quotas/admin/users — admin-only. State for every user."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            users = _auth_mod.AuthDB.list_users()
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        out = []
        cfg = engine._quota_manager.get_config()
        for u in users:
            try:
                st = engine._quota_manager.get_user_state(u["id"], cfg=cfg)
            except Exception:
                continue
            out.append({
                "user_id": u["id"],
                "username": u.get("username") or "",
                "display_name": u.get("display_name") or "",
                "role": u.get("role") or "user",
                "disabled": bool(u.get("disabled")),
                "level": st["level"],
                "daily": st["daily"],
                "cycle": st["cycle"],
                "has_override": st["has_override"],
            })
        self._send_json({"users": out, "config": cfg})

    def _handle_quota_admin_breakdown(self):
        """GET /v1/quotas/admin/breakdown?user_id=X&days=N — per-user
        per-model + per-day breakdown for the current cycle. Admin sees
        anyone; non-admin only their own user_id."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager or not engine._cost_tracker:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        from urllib.parse import unquote
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        target_uid = unquote(params.get("user_id", "")) or user["id"]
        if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Insufficient permissions"}, 403)
            return
        try:
            days = max(1, min(365, int(params.get("days", "30"))))
        except ValueError:
            days = 30
        cfg = engine._quota_manager.get_config()
        state = engine._quota_manager.get_user_state(target_uid, cfg=cfg)
        cycle_start_iso = state["cycle"]["starts_at"].replace("T", " ").split("+", 1)[0].split(".")[0]
        per_model = engine._cost_tracker.per_model_user_window(target_uid, cycle_start_iso)
        daily = engine._cost_tracker.get_daily(days=days, user_id=target_uid)
        self._send_json({
            "user_id": target_uid,
            "state": state,
            "per_model": per_model,
            "daily": daily,
            "days": days,
        })

    def _handle_agent_commands_get(self, path):
        """GET /v1/agents/{id}/commands — list custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        agent = engine.AgentConfig(agent_id)
        self._send_json({"commands": agent.load_commands()})

    def _handle_agent_commands_post(self, path):
        """POST /v1/agents/{id}/commands — save custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        body = self._read_json()
        commands = body.get("commands", [])
        agent = engine.AgentConfig(agent_id)
        agent.save_commands(commands)
        self._send_json({"status": "saved", "count": len(commands)})

    # --- Traces & Audit Handlers ---

    def _handle_traces_list(self):
        """GET /v1/traces?agent=X&hours=24&limit=50 — recent traces."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        limit = int(params.get("limit", "50"))
        traces = engine._trace_manager.get_traces(agent=agent, hours=hours, limit=limit)
        self._send_json({"traces": traces, "count": len(traces)})

    def _handle_trace_detail(self, path):
        """GET /v1/traces/{trace_id} — all spans for a trace."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        trace_id = path.split("/")[-1]
        spans = engine._trace_manager.get_trace(trace_id)
        if not spans:
            self._send_json({"error": "Trace not found"}, 404)
            return
        total_duration = sum(s.get("duration_ms", 0) for s in spans)
        total_tokens_in = sum(s.get("tokens_in", 0) for s in spans)
        total_tokens_out = sum(s.get("tokens_out", 0) for s in spans)
        self._send_json({
            "trace_id": trace_id,
            "spans": spans,
            "span_count": len(spans),
            "total_duration_ms": total_duration,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
        })

    def _handle_audit_list(self):
        """GET /v1/audit?agent=X&type=Y&from=Z&limit=50 — audit log."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        action_type = unquote(params.get("type", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        limit = int(params.get("limit", "50"))
        entries = engine._audit_log.query(agent=agent, action_type=action_type,
                                           from_ts=from_ts, limit=limit)
        self._send_json({"entries": entries, "count": len(entries)})

    def _handle_audit_export(self):
        """GET /v1/audit/export?agent=X&format=csv — CSV download."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        to_ts = unquote(params.get("to", "")) or None
        fmt = params.get("format", "csv")
        if fmt == "csv":
            csv_data = engine._audit_log.export_csv(agent=agent, from_ts=from_ts, to_ts=to_ts)
            body = csv_data.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=audit_log.csv")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            entries = engine._audit_log.query(agent=agent, from_ts=from_ts, limit=10000)
            self._send_json({"entries": entries, "count": len(entries)})

    # --- MCP Connection Handlers ---

    def _handle_mcp_list(self):
        """GET /v1/mcp/connections — list all MCP connections."""
        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"connections": []})
            return
        servers = mcp.list_servers()
        self._send_json({"connections": servers})

    def _handle_mcp_connect(self):
        """POST /v1/mcp/connect — connect to a new MCP server at runtime."""
        body = self._read_json()
        url = body.get("url", "")
        name = body.get("name", "")
        transport = body.get("transport", "sse")
        persist = body.get("persist", False)

        if not url or not name:
            self._send_json({"error": "Both 'url' and 'name' are required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            mcp = engine.MCPManager()
            engine._mcp_manager = mcp

        result = mcp.connect_runtime(url, name, transport)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return

        # Persist to mcp.json if requested
        if persist:
            mcp_json_path = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
            try:
                existing = {}
                if os.path.exists(mcp_json_path):
                    with open(mcp_json_path, "r") as f:
                        existing = json.load(f)
                if transport == "stdio":
                    parts = url.split()
                    existing[name] = {"transport": "stdio", "command": parts[0],
                                      "args": parts[1:] if len(parts) > 1 else []}
                else:
                    existing[name] = {"transport": "sse", "url": url}
                with open(mcp_json_path, "w") as f:
                    json.dump(existing, f, indent=2)
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)

        self._send_json(result)

    def _handle_mcp_disconnect(self):
        """POST /v1/mcp/disconnect — disconnect a runtime MCP server."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "'name' is required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"error": "No MCP manager available"}, 400)
            return

        result = mcp.disconnect_runtime(name)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return
        self._send_json(result)

    def _handle_mcp_registry(self):
        """GET /v1/mcp/registry?q=...&limit=... — search official MCP registry."""
        import urllib.request
        import urllib.parse
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        query = params.get("q", [""])[0]
        limit = params.get("limit", ["20"])[0]
        try:
            url = f"https://registry.modelcontextprotocol.io/v0/servers?search={urllib.parse.quote(query)}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "BrainAgent/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            # Normalize into a flat list with install info — dedup by name
            servers = []
            seen = set()
            items = data if isinstance(data, list) else data.get("servers", [])
            for item in items:
                srv = item.get("server", item) if isinstance(item, dict) else item
                if not isinstance(srv, dict):
                    continue
                name = srv.get("name", "")
                if name in seen:
                    continue
                seen.add(name)
                desc = srv.get("description", "")
                repo = srv.get("repository", {})
                repo_url = repo.get("url", "") if isinstance(repo, dict) else ""
                packages = srv.get("packages", [])
                remotes = srv.get("remotes", [])
                pkg = packages[0] if packages else {}
                registry_type = pkg.get("registryType", "")
                identifier = pkg.get("identifier", "")
                transport = pkg.get("transport", {})
                transport_type = transport.get("type", "stdio") if isinstance(transport, dict) else "stdio"
                pkg_args = pkg.get("packageArguments", [])
                env_vars = pkg.get("environmentVariables", [])
                # Build install command from packages or remotes
                if registry_type == "npm":
                    command = "npx"
                    args = ["-y", identifier]
                elif registry_type == "pypi":
                    command = "uvx"
                    args = [identifier]
                elif remotes:
                    remote = remotes[0]
                    transport_type = remote.get("type", "sse")
                    command = remote.get("url", "")
                    args = []
                    registry_type = "remote"
                else:
                    command = identifier
                    args = []
                servers.append({
                    "name": name,
                    "description": desc,
                    "repo_url": repo_url,
                    "registry_type": registry_type,
                    "identifier": identifier,
                    "transport": transport_type,
                    "command": command,
                    "args": args,
                    "env_vars": [{"name": e.get("name",""), "description": e.get("description",""), "required": e.get("isRequired", False)} for e in env_vars],
                    "pkg_args": [{"name": a.get("name",""), "description": a.get("description",""), "required": a.get("isRequired", False), "format": a.get("format","")} for a in pkg_args],
                })
            self._send_json({"servers": servers})
        except Exception as e:
            self._send_json({"error": str(e), "servers": []})

    def _handle_refine(self):
        """POST /v1/refine — refine text with LLM one-shot call.

        Accepts optional `purpose` to swap the system prompt for non-chat
        targets:
          - "" / "chat_prompt"      → rewrite as a clearer chat prompt (default)
          - "profile_field"         → polish a free-text profile entry
                                      (e.g. job_description, comm prefs)
        Anything else falls back to chat_prompt behavior."""
        body = self._read_json()
        text = body.get("text", "") or body.get("content", "")
        context = body.get("context", "general")
        purpose = (body.get("purpose") or "").strip().lower()
        field_label = (body.get("field_label") or "").strip()
        # Optional caveman compression for the *refine LLM call itself*
        # (0 off / 1 lite / 2 full / 3 ultra). Compresses the polish system
        # prompt + appends the chat-style suffix so the refiner produces
        # tighter output without us touching its rules.
        try:
            caveman = int(body.get("caveman") or 0)
        except (TypeError, ValueError):
            caveman = 0
        if caveman not in (0, 1, 2, 3):
            caveman = 0
        if not text:
            self._send_json({"error": "No text provided"}, 400)
            return

        # Find model: request body > tools_config setting > auto-select
        refine_model = body.get("model")
        if not refine_model:
            tc = engine.get_tool_config()
            refine_model = tc.get("refinement", {}).get("model", "")
        if not refine_model and engine._models_config:
            candidates = []
            for mid, cfg in engine._models_config.items():
                if not cfg.get("enabled", True):
                    continue
                ml = mid.lower()
                if "haiku" in ml:
                    score = 0
                elif "sonnet" in ml:
                    score = 1
                else:
                    score = 2 + (cfg.get("cost_input", 0) or 0)
                candidates.append((mid, score))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                refine_model = candidates[0][0]
        if not refine_model:
            refine_model = server_config.get("default_model", "")

        if not refine_model:
            self._send_json({"error": "No model available for refinement"}, 503)
            return

        provider = self._resolve_provider(refine_model)

        # Build context from current session — chat-prompt mode only. Profile
        # polishing must NOT read chat history (privacy, and the polish prompt
        # doesn't need it anyway).
        session_id = body.get("session_id", "")
        agent_id = body.get("agent", "main")
        project = body.get("project", "")
        chat_context = ""

        if purpose != "profile_field":
            # Get agent info
            try:
                agent_cfg = engine.AgentConfig(agent_id)
                soul_summary = (agent_cfg.soul or "")[:200]
                if soul_summary:
                    chat_context += f"Agent: {agent_id} — {soul_summary}\n"
            except Exception:
                pass

            # Get recent conversation for context (last 5 messages)
            if session_id:
                try:
                    s = sessions.get(session_id)
                    if s and s.messages:
                        recent = s.messages[-5:]
                        chat_context += "Recent conversation:\n"
                        for m in recent:
                            role = m.get("role", "?")
                            content = m.get("content", "")
                            if isinstance(content, str):
                                chat_context += f"  [{role}] {content[:150]}\n"
                        chat_context += "\n"
                except Exception:
                    pass

            if project:
                chat_context += f"Active project: {project}\n"

        context_block = ""
        if chat_context:
            context_block = (
                f"\nCONTEXT (use this to make the rewrite more specific and relevant):\n"
                f"{chat_context}\n"
            )

        if purpose == "profile_field":
            # Polish a free-text profile entry. Different rules: the user
            # is describing themselves, not asking the AI a question, so
            # we keep first-person voice and don't re-frame as a request.
            label_hint = f" The field is: {field_label}." if field_label else ""
            instructions = (
                "You are a TEXT POLISHER for a user profile field." + label_hint + " "
                "The user is describing themselves or their preferences. "
                "Your job is to lightly polish what they wrote.\n"
                "CRITICAL RULES:\n"
                "- Output ONLY the polished text, nothing else.\n"
                "- Keep first-person voice (\"I am…\", \"I prefer…\") if present.\n"
                "- Do NOT add new facts, opinions, or content the user didn't write.\n"
                "- Do NOT answer or respond — just clean up what's there.\n"
                "- Fix grammar, spelling, punctuation, awkward phrasing.\n"
                "- Preserve line breaks and paragraph structure when present.\n"
                "- Keep the user's tone (formal/casual) and language.\n"
                "- If the input is already clean, return it unchanged.\n"
                "- No markdown headings, no bullet rewrites unless the input had them."
            )
            request_line = (
                "Polish this profile text (output ONLY the polished "
                "version, preserve line breaks):\n\n" + text
            )
        elif purpose == "soul":
            # Polish an agent's soul.md — its system prompt that defines
            # personality, role, and behavioural rules. Different rules
            # again: this is *imperative second-person* prose addressed to
            # the agent ("You are …", "Your job is …"). We must NOT flip
            # it into first or third person, must NOT change the agent's
            # name/role/tools, and must preserve any embedded code/command
            # examples and section structure.
            instructions = (
                "You are a TEXT POLISHER for an AI agent's soul.md "
                "(its system prompt). The soul defines the agent's "
                "identity, role, and behavioural rules — it is written in "
                "second person ('You are …', 'Your job is …'). Your job "
                "is to lightly polish what the user wrote without "
                "changing meaning.\n"
                "CRITICAL RULES:\n"
                "- Output ONLY the polished soul, nothing else.\n"
                "- Keep second-person voice ('You are …', 'Your job …'). "
                "Do NOT switch to first or third person.\n"
                "- Do NOT change the agent's name, role, or capabilities.\n"
                "- Do NOT add new behaviours, tools, or rules. Do NOT "
                "remove existing rules.\n"
                "- Do NOT answer or respond — just clean up what's there.\n"
                "- Fix grammar, spelling, punctuation, awkward phrasing, "
                "redundancy.\n"
                "- Preserve Markdown structure: headings (#, ##, ###), "
                "bullet lists, numbered lists, blockquotes, horizontal "
                "rules — keep them all.\n"
                "- Preserve code blocks and inline `code` exactly. Do not "
                "rewrite commands, paths, tool names, or examples.\n"
                "- Preserve line breaks and paragraph structure.\n"
                "- Keep the existing tone (terse / verbose / playful / "
                "formal) — do not normalise it.\n"
                "- If the input is already clean, return it unchanged."
            )
            request_line = (
                "Polish this soul.md (output ONLY the polished version, "
                "preserve all Markdown structure and code blocks):\n\n"
                + text
            )
        else:
            instructions = (
                "You are a PROMPT REWRITER for an AI chat system. "
                "The user will give you a draft prompt/message they want to send to an AI assistant. "
                "Your job is to rewrite it into a better, clearer version of the SAME request. "
                "CRITICAL RULES:\n"
                "- Output ONLY the rewritten prompt, nothing else\n"
                "- Do NOT answer the question or fulfill the request — REWRITE it\n"
                "- Do NOT add explanations, analysis, alternatives, or commentary\n"
                "- Do NOT use markdown headings, bullet points, or formatting\n"
                "- The output replaces the user's input in a chat box — it must be a clean prompt\n"
                "- Fix grammar, spelling, punctuation\n"
                "- Make the request clearer and more specific using the context provided\n"
                "- Keep the same intent and language\n"
                "Example: Input: 'whats weather vienna' → Output: 'What is the weather like in Vienna today?'"
                + context_block
            )
            request_line = f"Rewrite this prompt (output ONLY the rewritten version):\n\n{text}"

        # Caveman compression — applied only to the *instructions* block,
        # never to the user's text being polished/rewritten (we don't want
        # to mangle their content). Prepends the system-style compression
        # banner + appends the chat-style suffix so the refiner produces
        # a tighter, more telegraphic rewrite.
        if caveman in (1, 2, 3):
            sys_banner = engine.CAVEMAN_SYSTEM_PROMPTS.get(caveman, "")
            chat_suffix = engine.CAVEMAN_CHAT_PROMPTS.get(caveman, "")
            instructions = (
                sys_banner + engine._caveman_compress_text(instructions, caveman) + chat_suffix
            )
        # Build the wire-level messages: prepend the (possibly compressed)
        # instructions to the user's request-line, since /v1/refine doesn't
        # use _build_system_prompt — the rules HAVE to ride in the user msg.
        messages = [{"role": "user", "content": instructions + "\n\n" + request_line}]

        try:
            result = engine.send_message(
                messages, refine_model, provider["api_key"],
                provider["base_url"],
                silent=True, tools=False,
            )
            self._send_json({"refined": result or text, "model": refine_model,
                             "caveman": caveman})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_soul_chat(self, path):
        """POST /v1/agents/<id>/soul-chat — chat to edit soul.md with LLM."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        message = body.get("message", "").strip()
        soul = body.get("soul", "")
        history = body.get("history", [])

        if not message:
            self._send_json({"error": "No message provided"}, 400)
            return

        # Resolve model (same logic as refine)
        model = None
        tc = engine.get_tool_config()
        model = tc.get("refinement", {}).get("model", "")
        if not model and engine._models_config:
            candidates = []
            for mid, cfg in engine._models_config.items():
                if not cfg.get("enabled", True):
                    continue
                ml = mid.lower()
                if "haiku" in ml:
                    score = 0
                elif "sonnet" in ml:
                    score = 1
                else:
                    score = 2 + (cfg.get("cost_input", 0) or 0)
                candidates.append((mid, score))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                model = candidates[0][0]
        if not model:
            model = server_config.get("default_model", "")
        if not model:
            self._send_json({"error": "No model available"}, 503)
            return

        provider = self._resolve_provider(model)

        system_block = (
            "You are a soul.md editor assistant. The user wants to modify an agent's soul.md file "
            "(system prompt that defines the agent's personality and behavior).\n\n"
            "CURRENT SOUL.MD:\n```\n" + soul + "\n```\n\n"
            "RULES:\n"
            "- Help the user edit, improve, or rewrite the soul.md based on their instructions\n"
            "- When you make changes, output the COMPLETE updated soul.md inside a ```soul\n...\n``` code block\n"
            "- You may also provide brief commentary outside the code block\n"
            "- If the user is just asking a question or discussing (not requesting changes), respond normally without a code block\n"
            "- Preserve existing structure and formatting unless asked to change it\n"
            "- Keep the same voice/style unless the user wants a different one\n"
        )

        messages = [{"role": "user", "content": system_block}, {"role": "assistant", "content": "I understand. I'm ready to help you edit this agent's soul.md. What changes would you like to make?"}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        try:
            result = engine.send_message(
                messages, model, provider["api_key"],
                provider["base_url"],
                silent=True, tools=False,
            )
            self._send_json({"reply": result or "", "model": model})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_services_status(self):
        """GET /v1/services — status of all managed services."""
        uptime = int(time.time() - _server_start_time)
        tg_running = self._is_telegram_running()

        self._send_json({
            "server": {
                "status": "running",
                "version": engine.VERSION,
                "version_date": engine.VERSION_DATE,
                "pid": os.getpid(),
                "uptime_seconds": uptime,
                "sessions": len(sessions.list_all()),
                "agents": engine.list_agents(),
                "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
                "default_provider": next((name for name, p in server_config.get("providers", {}).items() if p.get("default_model") == server_config.get("default_model")), ""),
                "default_model": server_config.get("default_model", ""),
                "attachment_image_model": server_config.get("attachment_image_model", ""),
                "gdpr_scanner": {
                    "enabled": bool(server_config.get("gdpr_scanner", {}).get("enabled", True)),
                    "server_log": bool(server_config.get("gdpr_scanner", {}).get("server_log", True)),
                    "server_block": bool(server_config.get("gdpr_scanner", {}).get("server_block", False)),
                    "default_local_fallback_model": str(server_config.get("gdpr_scanner", {}).get("default_local_fallback_model") or ""),
                    "categories": server_config.get("gdpr_scanner", {}).get("categories") or {
                        cat: {"action": act} for cat, act in engine.PII_DEFAULT_CATEGORY_ACTIONS.items()
                    },
                    "rule_overrides": server_config.get("gdpr_scanner", {}).get("rule_overrides") or {},
                    "email_allowlist": server_config.get("gdpr_scanner", {}).get("email_allowlist") or [],
                },
                "available_tools": sorted(engine.TOOL_DISPATCH.keys()),
            },
            "telegram": {
                "status": "running" if tg_running else "stopped",
                "bot": _telegram_mod.telegram_service.bot_username if tg_running else "",
                "enabled": server_config.get("telegram_enabled", True),
            },
            "channels": _adapters_mod.channel_manager.status() if _adapters_mod.channel_manager else [],
            "nodes": self._get_nodes_summary(),
        })

    def _get_nodes_summary(self):
        """Get a summary of node statuses."""
        with _node_lock:
            total = len(_node_registry)
            connected = sum(1 for info in _node_registry.values() if info["status"] == "connected")
            return {"total": total, "connected": connected}

    def _handle_service_log(self):
        """GET /v1/services/log?name=server|qmd&lines=100 — tail a service log."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        name = params.get("name", "server")
        lines = min(int(params.get("lines", "100")), 500)

        log_paths = {
            "server": os.path.expanduser("~/.brain-agent/server.log"),
            "qmd": os.path.expanduser("~/.brain-agent/qmd.log"),
        }
        path = log_paths.get(name)
        if not path or not os.path.isfile(path):
            self._send_json({"name": name, "lines": [], "error": "Log file not found"})
            return

        try:
            with open(path, "r", errors="replace") as f:
                all_lines = f.readlines()
            tail = [l.rstrip("\n") for l in all_lines[-lines:]]
            self._send_json({"name": name, "lines": tail, "total": len(all_lines)})
        except Exception as e:
            self._send_json({"name": name, "lines": [], "error": str(e)})

    def _handle_telegram_action(self):
        """POST /v1/services/telegram — start/stop/restart/enable/disable Telegram."""
        body = self._read_json()
        action = body.get("action", "")
        svc = _telegram_mod.telegram_service

        if action == "start":
            ok = _start_telegram_service()
            self._send_json({"status": "started" if ok else "error",
                             "running": svc.running, "error": svc.error})

        elif action == "stop":
            svc.stop()
            self._send_json({"status": "stopped", "running": False})

        elif action == "restart":
            svc.stop()
            ok = _start_telegram_service()
            self._send_json({"status": "restarted" if ok else "error",
                             "running": svc.running, "error": svc.error})

        elif action == "enable":
            _set_telegram_enabled(True)
            if not svc.running:
                _start_telegram_service()
            self._send_json({"status": "enabled", "running": svc.running,
                             "enabled": True})

        elif action == "disable":
            _set_telegram_enabled(False)
            svc.stop()
            self._send_json({"status": "disabled", "running": False,
                             "enabled": False})

        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_server_config(self):
        """POST /v1/services/server — update server defaults (default_model, attachment_image_model)."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        result = {}

        # --- Default model ---
        model = body.get("default_model")
        if model:
            providers = server_config.get("providers", {})
            provider_name = None
            mcfg = engine._models_config or {}
            if model in mcfg and mcfg[model].get("provider"):
                provider_name = mcfg[model]["provider"]
            else:
                for pname, p in providers.items():
                    if p.get("default_model") == model:
                        provider_name = pname
                        break
            server_config["default_model"] = model
            if provider_name:
                server_config["api_key"] = providers[provider_name].get("api_key", "")
                server_config["base_url"] = providers[provider_name].get("base_url", "")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                if provider_name:
                    config["default_provider"] = provider_name
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["default_model"] = model
            result["default_provider"] = provider_name or ""

        # --- Attachment image model ---
        if "attachment_image_model" in body:
            aim = body["attachment_image_model"] or ""
            server_config["attachment_image_model"] = aim
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                att_cfg = config.setdefault("attachments", {})
                att_cfg["image_model"] = aim
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["attachment_image_model"] = aim

        # --- GDPR/PII scanner settings ---
        if "gdpr_scanner" in body:
            gs_in = body["gdpr_scanner"]
            if not isinstance(gs_in, dict):
                self._send_json({"error": "gdpr_scanner must be an object"}, 400)
                return
            gs = server_config.setdefault("gdpr_scanner", {})
            for key in ("enabled", "server_log", "server_block"):
                if key in gs_in:
                    gs[key] = bool(gs_in[key])
            if "default_local_fallback_model" in gs_in:
                mid = str(gs_in["default_local_fallback_model"] or "")
                # Validate: must be a known, enabled, local model (empty = disabled)
                if mid:
                    mcfg = (engine._models_config or {}).get(mid) or {}
                    if not mcfg.get("enabled"):
                        self._send_json({"error": f"default_local_fallback_model: unknown or disabled model '{mid}'"}, 400)
                        return
                    if not engine.is_model_local(mid):
                        self._send_json({"error": f"default_local_fallback_model: '{mid}' is not local"}, 400)
                        return
                gs["default_local_fallback_model"] = mid

            # Category actions — only accept known categories + valid actions.
            if "categories" in gs_in:
                cats_in = gs_in["categories"] or {}
                if not isinstance(cats_in, dict):
                    self._send_json({"error": "gdpr_scanner.categories must be an object"}, 400)
                    return
                valid_cats = set(engine.PII_DEFAULT_CATEGORY_ACTIONS.keys())
                out_cats = {}
                for cat, entry in cats_in.items():
                    if cat not in valid_cats:
                        continue
                    action = entry.get("action") if isinstance(entry, dict) else entry
                    if action not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"categories.{cat}.action must be ignore|warn|block"}, 400)
                        return
                    out_cats[cat] = {"action": action}
                # Merge with defaults for any unset categories so save is complete
                for cat, act in engine.PII_DEFAULT_CATEGORY_ACTIONS.items():
                    out_cats.setdefault(cat, {"action": act})
                gs["categories"] = out_cats

            # Rule overrides — reject unknown rule_ids so typos surface.
            if "rule_overrides" in gs_in:
                ovr_in = gs_in["rule_overrides"] or {}
                if not isinstance(ovr_in, dict):
                    self._send_json({"error": "gdpr_scanner.rule_overrides must be an object"}, 400)
                    return
                out_ovr = {}
                valid_rules = set(engine.PII_RULE_CATEGORIES.keys())
                for rid, act in ovr_in.items():
                    if not act:
                        continue
                    if rid not in valid_rules:
                        self._send_json({"error": f"rule_overrides: unknown rule_id '{rid}'"}, 400)
                        return
                    if act not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"rule_overrides[{rid}] must be ignore|warn|block"}, 400)
                        return
                    out_ovr[rid] = act
                gs["rule_overrides"] = out_ovr

            # Email allowlist — strip/lowercase/dedupe. Accept "x@y.com" and
            # "@y.com" patterns; reject anything with internal whitespace.
            if "email_allowlist" in gs_in:
                al_in = gs_in["email_allowlist"] or []
                if not isinstance(al_in, list):
                    self._send_json({"error": "gdpr_scanner.email_allowlist must be a list"}, 400)
                    return
                cleaned: list[str] = []
                seen = set()
                for e in al_in:
                    if not isinstance(e, str):
                        continue
                    s = e.strip().lower()
                    if not s or " " in s or "\t" in s:
                        continue
                    if "@" not in s:
                        self._send_json({"error": f"email_allowlist: '{e}' must contain '@'"}, 400)
                        return
                    if s in seen:
                        continue
                    seen.add(s)
                    cleaned.append(s)
                gs["email_allowlist"] = cleaned

            engine._invalidate_gdpr_cache()
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["gdpr_scanner"] = gs
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["gdpr_scanner"] = gs

        if not result:
            self._send_json({"error": "No valid fields to update"}, 400)
            return
        result["status"] = "saved"
        self._send_json(result)

    # --- Tools config handlers ---

    def _handle_tools_config_get(self):
        """GET /v1/tools/config — return tool config with fallback values merged and sensitive fields masked."""
        cfg = engine.get_tool_config()
        # Merge fallback values so UI shows what's actually in use
        exa_cfg = cfg.get("exa_search", {})
        if not exa_cfg.get("api_key"):
            env_key = os.environ.get("EXA_API_KEY", "")
            if env_key:
                exa_cfg["api_key"] = env_key
                exa_cfg["_source"] = "environment variable"
            else:
                # Check built-in default (hardcoded in tool function)
                exa_cfg["api_key"] = "97dbd594-f7b4-4866-9a8e-6a297e3df576"
                exa_cfg["_source"] = "built-in default"
        gmail_cfg = cfg.get("gmail", {})
        if not gmail_cfg.get("email") or not gmail_cfg.get("app_password"):
            fb = engine._gmail_config()
            if fb:
                if not gmail_cfg.get("email") and fb.get("email"):
                    gmail_cfg["email"] = fb["email"]
                if not gmail_cfg.get("app_password") and fb.get("app_password"):
                    gmail_cfg["app_password"] = fb["app_password"]
                gmail_cfg["_source"] = "gmail.json"
        # Mask sensitive values
        masked = {}
        for tool_name, tool_cfg in cfg.items():
            masked[tool_name] = dict(tool_cfg)
            for key in ("api_key", "app_password"):
                val = masked[tool_name].get(key, "")
                if val and len(val) > 4:
                    masked[tool_name][key] = "*" * (len(val) - 4) + val[-4:]
        self._send_json(masked)

    def _handle_tools_status(self):
        """GET /v1/tools/status — return tool availability and status."""
        self._send_json(engine.get_tool_status())

    def _handle_tools_breakdown(self):
        """GET /v1/tools/breakdown?agent=<id> — per-group token cost of tool definitions."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        agent_id = params.get("agent", "main")
        try:
            agent = engine.AgentConfig(agent_id)
        except Exception as e:
            self._send_json({"error": f"Agent not found: {agent_id} ({e})"}, 404)
            return
        prev_agent = getattr(engine._thread_local, "current_agent", None)
        prev_mcp = getattr(engine._thread_local, "mcp_manager", None)
        try:
            engine._thread_local.current_agent = agent
            # Use the live MCP manager so connected MCP servers are measured.
            engine._thread_local.mcp_manager = engine._mcp_manager
            breakdown = engine.get_tool_breakdown(agent_id)
        finally:
            engine._thread_local.current_agent = prev_agent
            engine._thread_local.mcp_manager = prev_mcp
        self._send_json(breakdown)

    def _handle_tools_config_save(self):
        """POST /v1/tools/config — save tool configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No configuration provided"}, 400)
            return
        # Don't overwrite sensitive fields if masked value is sent
        existing = engine.get_tool_config()
        for tool_name, tool_cfg in body.items():
            for key in ("api_key", "app_password"):
                val = tool_cfg.get(key, "")
                if val and val.startswith("*"):
                    # Masked value — keep existing
                    tool_cfg[key] = existing.get(tool_name, {}).get(key, "")
        result = engine.save_tool_config(body)
        if "error" in result:
            self._send_json(result, 500)
        else:
            self._send_json({"status": "saved", "config": result})

    # --- Hooks handlers ---

    def _handle_hooks_get(self, path: str):
        """GET /v1/agents/{id}/hooks — list hooks for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        try:
            cfg = engine.AgentConfig(agent_id)
            hooks_cfg = cfg.config.get("hooks", {"enabled": False, "timeout": 5000, "scripts": []})
            self._send_json(hooks_cfg)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_hooks_save(self, path: str):
        """POST /v1/agents/{id}/hooks — save hooks config for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        try:
            agent_json_path = os.path.join(engine.AGENTS_DIR, agent_id, "agent.json")
            config = {}
            if os.path.exists(agent_json_path):
                with open(agent_json_path) as f:
                    config = json.load(f)
            config["hooks"] = body
            with open(agent_json_path, "w") as f:
                json.dump(config, f, indent=2)
            # Reload hook runner cache
            with engine._hook_runners_lock:
                engine._hook_runners.pop(agent_id, None)
            self._send_json({"status": "saved", "hooks": body})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- MemPalace handlers ---

    def _handle_mempalace_session_turns(self):
        """GET /v1/mempalace/session-turns?session_id=X — return the set of
        turn_ids currently memorized for this session, parsed from drawer
        source_file prefixes. The UI uses this to grey out menu items that
        would be a no-op (e.g. 'memorize this response' when it's already
        memorized, or 'remove' when nothing was stored)."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        sid = (qs.get("session_id") or [""])[0]
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        turn_ids: set[int] = set()
        legacy_count = 0  # drawers without #turn/<id> suffix
        try:
            mcfg = engine._load_mempalace_config()
            palace_path = mcfg.get("palace_path", "")
            if not palace_path or not os.path.isdir(palace_path):
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            ok, _ = engine._ensure_mempalace_importable()
            if not ok:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            from mempalace.palace import get_collection
            col = get_collection(palace_path, create=False)
            if not col:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            result = col.get(include=["metadatas"])
            prefix = f"session/{sid}"
            for m in result.get("metadatas", []):
                sf = (m.get("source_file") or "")
                if not sf.startswith(prefix):
                    continue
                # Shape: session/<sid> or session/<sid>#turn/<id>[...] or legacy session/<sid>#...
                rest = sf[len(prefix):]
                if rest.startswith("#turn/"):
                    after = rest[len("#turn/"):]
                    tok = after.split("#", 1)[0].split("/", 1)[0]
                    if tok.isdigit():
                        turn_ids.add(int(tok))
                        continue
                legacy_count += 1
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({
            "session_id": sid,
            "turn_ids": sorted(turn_ids),
            "legacy_count": legacy_count,
        })

    def _handle_mempalace_classifier_get(self):
        """GET /v1/mempalace/classifier — return classifier config."""
        mcfg = engine._load_mempalace_config()
        sync_cfg = mcfg.get("chat_sync", {}) or {}
        clf = sync_cfg.get("classifier", {}) or {}
        self._send_json({
            "enabled": clf.get("enabled", False),
            "model": clf.get("model", ""),
            "min_turns": clf.get("min_turns", 0),
            "default_mode": clf.get("default_mode", 0),
            "categories_to_file": clf.get("categories_to_file",
                ["fact", "preference", "decision", "reference"]),
        })

    def _handle_mempalace_classifier_save(self):
        """POST /v1/mempalace/classifier — save classifier config."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            mp = config.setdefault("mempalace", {})
            cs = mp.setdefault("chat_sync", {})
            clf = cs.setdefault("classifier", {})
            if "enabled" in body:
                clf["enabled"] = bool(body["enabled"])
            if "model" in body:
                clf["model"] = str(body["model"])
            if "categories_to_file" in body:
                clf["categories_to_file"] = list(body["categories_to_file"])
            if "min_turns" in body:
                clf["min_turns"] = max(0, int(body["min_turns"]))
            if "default_mode" in body:
                clf["default_mode"] = max(0, min(2, int(body["default_mode"])))
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            engine._mempalace_config_cache = None
            self._send_json({"status": "saved", "classifier": clf})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Knowledge-graph endpoints ─────────────────────────────────────────
    #
    # Project-scoped KG produced by kg_extract.run_kg_post_pass during the
    # project-sync daemon cycle. All wing access is gated by
    # _project_access_check; the global stats endpoint filters by accessible
    # projects when called by a non-admin.

    def _kg_qs(self) -> dict:
        """Flatten URL query string to a single-value dict for KG endpoints."""
        from urllib.parse import parse_qs, urlparse
        raw = parse_qs(urlparse(self.path).query)
        return {k: (v[0] if v else "") for k, v in raw.items()}

    def _kg_resolve_project_from_query(self, params):
        """Pull (agent_id, proj_name, project, prefixes, palace_path) from
        ?agent_id=X&project=Y query params. Sends 400/404/403 on miss and
        returns None. `project` carries the loaded project dict.
        """
        agent_id = (params.get("agent_id") or "").strip()
        proj_name = (params.get("project") or "").strip()
        if not agent_id or not proj_name:
            self._send_json({"error": "agent_id and project required"}, 400)
            return None
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return None
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "project has no id (run a sync first)"}, 400)
            return None
        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "MemPalace palace_path missing"}, 503)
            return None
        # Collect every source_file prefix that belongs to this project.
        pdir = project.get("dir") or os.path.join(
            engine.AGENTS_DIR, agent_id, "projects", proj_name)
        def _norm(p: str) -> str:
            # Resolve symlinks (macOS /tmp → /private/tmp etc.) so prefix
            # filters match what the miner stored.
            try:
                r = os.path.realpath(p)
            except OSError:
                r = p
            if r and not r.endswith(os.sep):
                r += os.sep
            return r
        prefixes = [_norm(pdir)]
        for entry in (project.get("input_folders") or []):
            fp = (entry.get("path") or "").strip()
            if fp:
                prefixes.append(_norm(fp))
        return {
            "agent_id": agent_id,
            "proj_name": proj_name,
            "project": project,
            "wing": _project_wing(pid),
            "prefixes": prefixes,
            "palace_path": palace_path,
            "chats_db_path": os.path.join(engine.AGENTS_DIR, "main", "chats.db"),
        }

    def _handle_kg_stats_global(self):
        """GET /v1/mempalace/kg/stats — aggregate across all accessible
        projects. Admins see everything; non-admins see only projects they
        can access (per _project_access_check)."""
        user = self._require_auth()
        if user is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            self._send_json({"enabled": False, "projects": []})
            return
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "palace_path missing"}, 503)
            return
        kg_cfg = mcfg.get("kg") or {}

        agg_entities = 0
        agg_triples = 0
        per_project = []
        try:
            for agent_id in sorted(os.listdir(engine.AGENTS_DIR)):
                proj_root = os.path.join(engine.AGENTS_DIR, agent_id, "projects")
                if not os.path.isdir(proj_root):
                    continue
                for proj_name in sorted(os.listdir(proj_root)):
                    if proj_name.startswith("."):
                        continue
                    project = engine.ProjectManager.get_project(agent_id, proj_name)
                    if not project or project.get("status") == "archived":
                        continue
                    if not _auth_mod.can_access_project(user, project):
                        continue
                    pid = project.get("id") or ""
                    if not pid:
                        continue
                    pdir = project.get("dir") or os.path.join(
                        proj_root, proj_name)
                    def _norm_p(p: str) -> str:
                        try:
                            r = os.path.realpath(p)
                        except OSError:
                            r = p
                        if r and not r.endswith(os.sep):
                            r += os.sep
                        return r
                    prefixes = [_norm_p(pdir)]
                    for entry in (project.get("input_folders") or []):
                        fp = (entry.get("path") or "").strip()
                        if fp:
                            prefixes.append(_norm_p(fp))
                    proj_entities = 0
                    proj_triples = 0
                    proj_top_predicates = {}
                    for prefix in prefixes:
                        try:
                            s = kg_extract.kg_stats_for_wing(
                                palace_path=palace_path,
                                source_prefix=prefix,
                                adapter_name="brain-project-kg")
                        except Exception:
                            continue
                        proj_entities += int(s.get("entities", 0))
                        proj_triples += int(s.get("triples", 0))
                        for p in s.get("top_predicates", []) or []:
                            k = p.get("predicate", "") or ""
                            if k:
                                proj_top_predicates[k] = (
                                    proj_top_predicates.get(k, 0)
                                    + int(p.get("count", 0)))
                    per_project.append({
                        "agent_id": agent_id,
                        "project": proj_name,
                        "project_id": pid,
                        "wing": _project_wing(pid),
                        "entities": proj_entities,
                        "triples": proj_triples,
                        "top_predicates": [
                            {"predicate": k, "count": v}
                            for k, v in sorted(proj_top_predicates.items(),
                                               key=lambda kv: -kv[1])[:10]
                        ],
                    })
                    agg_entities += proj_entities
                    agg_triples += proj_triples
        except Exception as e:
            self._send_json({"error": f"enumerate failed: {e}"}, 500)
            return
        self._send_json({
            "enabled": kg_cfg.get("enabled", True),
            "extraction_model": kg_cfg.get("extraction_model", ""),
            "profile": kg_cfg.get("profile", "normative"),
            "entities": agg_entities,
            "triples": agg_triples,
            "projects": sorted(per_project,
                               key=lambda p: -p["triples"]),
        })

    def _handle_kg_wing_detail(self, params):
        """GET /v1/mempalace/kg/wing?agent_id=X&project=Y — per-project
        stats + sample triples + recent extraction log."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        # Aggregate across every prefix belonging to the project.
        agg_entities = 0
        agg_triples = 0
        agg_predicates: dict[str, int] = {}
        agg_entities_list: dict[str, dict] = {}
        for prefix in ctx["prefixes"]:
            try:
                s = kg_extract.kg_stats_for_wing(
                    palace_path=ctx["palace_path"],
                    source_prefix=prefix,
                    adapter_name="brain-project-kg")
            except Exception:
                continue
            agg_entities += int(s.get("entities", 0))
            agg_triples += int(s.get("triples", 0))
            for p in s.get("top_predicates", []) or []:
                k = p.get("predicate", "") or ""
                if k:
                    agg_predicates[k] = agg_predicates.get(k, 0) + int(p.get("count", 0))
            for e in s.get("top_entities", []) or []:
                eid = e.get("id", "") or ""
                if not eid:
                    continue
                cur = agg_entities_list.get(eid)
                if cur is None:
                    agg_entities_list[eid] = dict(e)
                else:
                    cur["degree"] = int(cur.get("degree", 0)) + int(e.get("degree", 0))

        # Sample triples — pull a small slice for the UI's "recent triples" list.
        sample_triples = self._kg_sample_triples(
            ctx["palace_path"], ctx["prefixes"], limit=50)

        # Extraction-log rows for this wing.
        try:
            log = kg_extract.list_kg_extraction_log(
                ctx["chats_db_path"], wing=ctx["wing"], limit=25)
        except Exception:
            log = []

        self._send_json({
            "agent_id": ctx["agent_id"],
            "project": ctx["proj_name"],
            "wing": ctx["wing"],
            "prefixes": ctx["prefixes"],
            "entities": agg_entities,
            "triples": agg_triples,
            "top_predicates": [
                {"predicate": k, "count": v}
                for k, v in sorted(agg_predicates.items(),
                                   key=lambda kv: -kv[1])[:30]
            ],
            "top_entities": sorted(agg_entities_list.values(),
                                   key=lambda e: -int(e.get("degree", 0)))[:30],
            "sample_triples": sample_triples,
            "extraction_log": log,
        })

    def _kg_sample_triples(self, palace_path: str, prefixes: list,
                           limit: int = 50) -> list:
        """Pull a small sample of triples (highest-confidence first) for any
        of the project's prefixes. Used by the UI as a quick spot-check."""
        kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
        if not os.path.isfile(kg_path):
            return []
        import sqlite3 as _sql
        conn = _sql.connect(kg_path, timeout=5, check_same_thread=False)
        conn.row_factory = _sql.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            has_adapter = "adapter_name" in cols
            has_drawer = "source_drawer_id" in cols
            scope_clause = " OR ".join(
                ["source_file LIKE ? || '%'"] * len(prefixes))
            params: list = list(prefixes)
            adapter_clause = " AND adapter_name = ? " if has_adapter else " "
            if has_adapter:
                params.append("brain-project-kg")
            sql = (
                "SELECT t.subject AS sub_id, e1.name AS sub_name, "
                "       t.predicate, "
                "       t.object AS obj_id, e2.name AS obj_name, "
                "       t.confidence, t.source_file, t.valid_from, "
                f"       {'t.source_drawer_id' if has_drawer else 'NULL'} AS source_drawer_id "
                "FROM triples t "
                "LEFT JOIN entities e1 ON t.subject = e1.id "
                "LEFT JOIN entities e2 ON t.object = e2.id "
                f"WHERE ({scope_clause}){adapter_clause}"
                "AND t.valid_to IS NULL "
                "ORDER BY t.confidence DESC, t.extracted_at DESC LIMIT ?"
            )
            params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [{
            "subject": r["sub_name"] or r["sub_id"],
            "predicate": r["predicate"],
            "object": r["obj_name"] or r["obj_id"],
            "confidence": r["confidence"],
            "source_file": r["source_file"] or "",
            "source_drawer_id": r["source_drawer_id"] or "",
            "valid_from": r["valid_from"] or "",
        } for r in rows]

    def _handle_kg_entity_detail(self, params):
        """GET /v1/mempalace/kg/entity?agent_id=X&project=Y&name=Z —
        neighborhood for one entity, project-scoped."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        name = (params.get("name") or "").strip()
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        try:
            from mempalace.knowledge_graph import KnowledgeGraph
        except Exception as e:
            self._send_json({"error": f"KG import: {e}"}, 500)
            return
        kg_path = os.path.join(ctx["palace_path"], "knowledge_graph.sqlite3")
        if not os.path.isfile(kg_path):
            self._send_json({"error": "knowledge_graph.sqlite3 missing"}, 404)
            return
        kg = KnowledgeGraph(db_path=kg_path)
        try:
            triples = kg.query_entity(name, direction="both") or []
        except Exception as e:
            self._send_json({"error": f"query_entity: {e}"}, 500)
            return
        finally:
            try: kg.close()
            except Exception: pass
        prefixes = ctx["prefixes"]
        in_scope = []
        for t in triples:
            if not isinstance(t, dict):
                continue
            sf = t.get("source_file", "") or ""
            if not sf:
                continue
            if any(sf.startswith(p) for p in prefixes):
                in_scope.append(t)
        self._send_json({
            "entity": name,
            "project": ctx["proj_name"],
            "wing": ctx["wing"],
            "count": len(in_scope),
            "total_in_kg": len(triples),
            "triples": in_scope,
        })
    def _handle_kg_extraction_log(self, params):
        """GET /v1/mempalace/kg/extraction-log?agent_id=X&project=Y&limit=N
        — recent run log for the project's wing."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        try:
            limit = max(1, min(500, int(params.get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50
        rows = kg_extract.list_kg_extraction_log(
            ctx["chats_db_path"], wing=ctx["wing"], limit=limit)
        self._send_json({
            "wing": ctx["wing"],
            "project": ctx["proj_name"],
            "count": len(rows),
            "rows": rows,
        })

    def _handle_kg_config_get(self):
        """GET /v1/mempalace/kg/config — current KG settings."""
        if self._require_auth() is None:
            return
        mcfg = engine._load_mempalace_config()
        kg_cfg = mcfg.get("kg") or {}
        self._send_json({
            "enabled": kg_cfg.get("enabled", True),
            "extraction_model": kg_cfg.get("extraction_model", ""),
            "profile": kg_cfg.get("profile", "normative"),
            "scopes": kg_cfg.get("scopes") or ["projects"],
            "max_triples_per_drawer": kg_cfg.get("max_triples_per_drawer", 12),
            "min_confidence": kg_cfg.get("min_confidence", 0.5),
            "max_drawer_chars": kg_cfg.get("max_drawer_chars", 6000),
            "regenerate_closets": bool(kg_cfg.get("regenerate_closets", False)),
        })

    def _handle_kg_config_save(self):
        """POST /v1/mempalace/kg/config — save KG settings (admin).
        Invalidates extraction and/or closet cursors when relevant fields change."""
        user = self._require_role("admin")
        if user is None:
            return
        body = self._read_json() or {}
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            cfg_disk = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg_disk = json.load(f)
            mp = cfg_disk.setdefault("mempalace", {})
            kg_old = dict(mp.get("kg") or {})
            kg = mp.setdefault("kg", {})
            if "enabled" in body:
                kg["enabled"] = bool(body["enabled"])
            if "extraction_model" in body:
                m = str(body["extraction_model"] or "").strip()
                if m:
                    models = cfg_disk.get("models") or {}
                    if m not in models:
                        self._send_json(
                            {"error": f"unknown model id: {m}"}, 400)
                        return
                kg["extraction_model"] = m
            if "profile" in body:
                p = str(body["profile"] or "").strip()
                if p not in ("normative", "generic"):
                    self._send_json({"error": f"unknown profile: {p}"}, 400)
                    return
                kg["profile"] = p
            if "max_triples_per_drawer" in body:
                kg["max_triples_per_drawer"] = max(
                    1, min(50, int(body["max_triples_per_drawer"])))
            if "min_confidence" in body:
                kg["min_confidence"] = max(
                    0.0, min(1.0, float(body["min_confidence"])))
            if "max_drawer_chars" in body:
                kg["max_drawer_chars"] = max(
                    500, min(20000, int(body["max_drawer_chars"])))
            if "scopes" in body:
                scopes = list(body["scopes"] or [])
                allowed = {"projects", "scheduled", "chats"}
                kg["scopes"] = [s for s in scopes if s in allowed] or ["projects"]
            if "regenerate_closets" in body:
                kg["regenerate_closets"] = bool(body["regenerate_closets"])
            with open(config_path, "w") as f:
                json.dump(cfg_disk, f, indent=2)
            engine._mempalace_config_cache = None

            # Invalidate cursors for fields that affect extraction quality.
            # Fields that change what triples get extracted → purge KG cursors.
            KG_FIELDS = {"extraction_model", "profile", "max_triples_per_drawer",
                         "min_confidence", "max_drawer_chars", "chunking_mode",
                         "source_chunk_chars"}
            # Fields that affect closet generation → purge closet cursor.
            CLOSET_FIELDS = {"extraction_model", "regenerate_closets"}
            kg_changed = any(kg_old.get(k) != kg.get(k) for k in KG_FIELDS)
            closet_changed = any(kg_old.get(k) != kg.get(k) for k in CLOSET_FIELDS)
            invalidated = {}
            if kg_changed or closet_changed:
                try:
                    from engine import kg_extract
                    chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
                    palace_path = (mp.get("palace_path") or "")
                    # Walk all project wings and purge the relevant cursors.
                    for agent_dir in os.scandir(engine.AGENTS_DIR):
                        if not agent_dir.is_dir():
                            continue
                        proj_root = os.path.join(agent_dir.path, "projects")
                        if not os.path.isdir(proj_root):
                            continue
                        for pdir in os.scandir(proj_root):
                            if not pdir.is_dir():
                                continue
                            pjson = os.path.join(pdir.path, "project.json")
                            if not os.path.exists(pjson):
                                continue
                            try:
                                with open(pjson) as f:
                                    pdata = json.load(f)
                                pid = pdata.get("id") or ""
                                if not pid:
                                    continue
                                wing = f"project__{pid}"
                                if kg_changed:
                                    kg_extract.kg_purge_for_scope(
                                        palace_path=palace_path,
                                        source_prefix="",
                                        adapter_name="brain-project-kg",
                                        chats_db_path=chats_db,
                                        wing=wing,
                                    )
                                if closet_changed:
                                    kg_extract.closet_regen_purge_for_scope(
                                        chats_db_path=chats_db,
                                        palace_wing=wing,
                                    )
                            except Exception:
                                pass
                    invalidated = {
                        "kg_cursors_cleared": kg_changed,
                        "closet_cursors_cleared": closet_changed,
                    }
                except Exception as e:
                    invalidated = {"invalidation_error": str(e)}

            self._send_json({"status": "saved", "kg": kg, **invalidated})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_kg_reextract(self):
        """POST /v1/mempalace/kg/reextract — purge a project's triples and
        kick the daemon to rebuild. Body: {agent_id, project, source_prefix?}.
        Admin or project owner."""
        user = self._require_auth()
        if user is None:
            return
        body = self._read_json() or {}
        agent_id = (body.get("agent_id") or "").strip()
        proj_name = (body.get("project") or "").strip()
        if not agent_id or not proj_name:
            self._send_json({"error": "agent_id and project required"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name,
                                             require_manage=True)
        if project is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        chats_db_path = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "project missing id"}, 400)
            return
        wing = _project_wing(pid)
        # Prefix(es) to purge: either the explicit one from the body, or the
        # union of project_dir + every input_folder. Resolve symlinks so the
        # purge matches what the miner actually stored in source_file
        # (macOS /tmp → /private/tmp, etc.).
        def _norm_p(p: str) -> str:
            try:
                r = os.path.realpath(p)
            except OSError:
                r = p
            if r and not r.endswith(os.sep):
                r += os.sep
            return r
        explicit_prefix = (body.get("source_prefix") or "").strip()
        if explicit_prefix:
            prefixes = [_norm_p(explicit_prefix)]
        else:
            pdir = project.get("dir") or os.path.join(
                engine.AGENTS_DIR, agent_id, "projects", proj_name)
            prefixes = [_norm_p(pdir)]
            for entry in (project.get("input_folders") or []):
                fp = (entry.get("path") or "").strip()
                if fp:
                    prefixes.append(_norm_p(fp))

        total_triples = 0
        total_progress = 0
        for prefix in prefixes:
            try:
                res = kg_extract.kg_purge_for_scope(
                    palace_path=palace_path,
                    source_prefix=prefix,
                    adapter_name="brain-project-kg",
                    chats_db_path=chats_db_path,
                    wing=wing,
                )
                total_triples += int(res.get("triples_deleted", 0))
                total_progress += int(res.get("progress_deleted", 0))
            except Exception as e:
                self._send_json({"error": f"purge {prefix} failed: {e}"}, 500)
                return
        # Kick the project-sync daemon to rebuild.
        try:
            with _project_sync_lock:
                _project_sync_requests.add((agent_id, proj_name))
            _project_sync_wakeup.set()
        except Exception:
            pass
        # Audit-log the manual reextract trigger.
        try:
            _audit_log.log_action(  # type: ignore[name-defined]
                user_id=user.get("user_id", ""),
                action_type="kg_reextract",
                tool_name="mempalace_kg",
                args_summary=f"{agent_id}/{proj_name} prefixes={len(prefixes)}",
                source="api",
            )
        except Exception:
            pass
        self._send_json({
            "status": "purged_and_queued",
            "triples_deleted": total_triples,
            "progress_deleted": total_progress,
            "prefixes": prefixes,
        })

    def _handle_mempalace_stats(self):
        """GET /v1/mempalace/stats — palace overview for admin dashboard."""
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            self._send_json({"enabled": False, "error": "MemPalace disabled in config"})
            return
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"enabled": True, "error": f"Palace path not found: {palace_path}"})
            return

        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"enabled": True, "error": err})
            return

        try:
            from mempalace.mcp_server import tool_status, tool_get_taxonomy, tool_list_tunnels, tool_graph_stats, tool_kg_stats
            from mempalace.palace import get_closets_collection

            status = tool_status()
            taxonomy = tool_get_taxonomy()
            tunnels = tool_list_tunnels()
            graph = tool_graph_stats()

            # Closet count
            closet_count = 0
            try:
                closets_col = get_closets_collection(palace_path, create=False)
                if closets_col:
                    closet_count = closets_col.count()
            except Exception:
                pass

            # Knowledge graph stats
            kg = {}
            try:
                kg = tool_kg_stats()
            except Exception:
                pass

            # Chat sync stats from cursor table
            sync_stats = {"synced_sessions": 0, "total_drawers_filed": 0, "last_sync": None}
            try:
                with _db_conn() as conn:
                    row = conn.execute("""
                        SELECT COUNT(*) as cnt,
                               SUM(last_message_id) as total_msgs,
                               MAX(updated_at) as last_update
                        FROM chat_mempalace_sync
                    """).fetchone()
                    if row:
                        sync_stats["synced_sessions"] = row[0] or 0
                        sync_stats["total_drawers_filed"] = row[1] or 0
                        sync_stats["last_sync"] = row[2]
            except Exception:
                pass

            # Mining config summary
            mine_cfg = mcfg.get("mine", {})
            chat_sync_cfg = mcfg.get("chat_sync", {})

            # Palace file size
            palace_size_mb = 0
            try:
                db_path = os.path.join(palace_path, "chroma.sqlite3")
                if os.path.exists(db_path):
                    palace_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
            except Exception:
                pass

            # WAL recent activity (last 100 entries)
            wal_activity = {"total_ops": 0, "recent_ops": [], "ops_by_type": {}}
            try:
                wal_path = os.path.join(os.path.dirname(palace_path), "wal", "write_log.jsonl")
                if os.path.exists(wal_path):
                    lines = []
                    with open(wal_path, "r") as f:
                        for line in f:
                            lines.append(line)
                    wal_activity["total_ops"] = len(lines)
                    for line in lines[-50:]:
                        try:
                            entry = json.loads(line)
                            wal_activity["recent_ops"].append({
                                "timestamp": entry.get("timestamp", ""),
                                "operation": entry.get("operation", ""),
                                "wing": (entry.get("params") or {}).get("wing", ""),
                                "room": (entry.get("params") or {}).get("room", ""),
                            })
                            op = entry.get("operation", "unknown")
                            wal_activity["ops_by_type"][op] = wal_activity["ops_by_type"].get(op, 0) + 1
                        except (json.JSONDecodeError, KeyError):
                            pass
                    wal_activity["recent_ops"] = wal_activity["recent_ops"][-20:]
            except Exception:
                pass

            # Wing breakdown with user isolation info
            wings_detail = {}
            tax = taxonomy.get("taxonomy", {})
            # Build user_id → display_name lookup
            _user_names = {}
            try:
                for u in _auth_mod.AuthDB.list_users():
                    _user_names[u["id"]] = u.get("display_name") or u.get("username") or u["id"]
            except Exception:
                pass
            for wing_name, rooms in tax.items():
                is_user_scoped = "--" in wing_name
                user_id = wing_name.split("--")[0] if is_user_scoped else None
                wings_detail[wing_name] = {
                    "rooms": rooms,
                    "drawer_count": sum(rooms.values()),
                    "room_count": len(rooms),
                    "user_scoped": is_user_scoped,
                    "user_id": user_id,
                    "user_name": _user_names.get(user_id, user_id) if user_id else None,
                }

            # Hall stats from drawer metadata
            halls = {}
            try:
                all_meta = status.get("_all_meta") or []
                if not all_meta:
                    from mempalace.palace import get_collection as _gc
                    _dcol = _gc(palace_path, create=False)
                    if _dcol:
                        _dr = _dcol.get(include=["metadatas"])
                        all_meta = _dr.get("metadatas", [])
                for m in all_meta:
                    h = m.get("hall", "")
                    if not h:
                        continue
                    if h not in halls:
                        halls[h] = {"count": 0, "rooms": {}}
                    halls[h]["count"] += 1
                    r = m.get("room", "")
                    if r:
                        halls[h]["rooms"][r] = halls[h]["rooms"].get(r, 0) + 1
            except Exception:
                pass

            self._send_json({
                "enabled": True,
                "palace_path": palace_path,
                "palace_size_mb": palace_size_mb,
                "total_drawers": status.get("total_drawers", 0),
                "total_closets": closet_count,
                "halls": halls,
                "wings": wings_detail,
                "wing_count": len(wings_detail),
                "room_count": status.get("total_rooms", len(set(r for rooms in tax.values() for r in rooms))),
                "graph": graph,
                "tunnels": tunnels,
                "knowledge_graph": kg,
                "chat_sync": sync_stats,
                "wal": wal_activity,
                "config": {
                    "mine_enabled": mine_cfg.get("enabled", True),
                    "mine_interval_s": mine_cfg.get("interval_seconds", 1800),
                    "mine_sources": len(mine_cfg.get("sources", [])),
                    "chat_sync_enabled": chat_sync_cfg.get("enabled", True),
                    "chat_sync_interval_s": chat_sync_cfg.get("interval_seconds", 60),
                    "chat_sync_build_closets": chat_sync_cfg.get("build_closets", True),
                },
            })
        except Exception as e:
            self._send_json({"enabled": True, "error": f"Failed to gather stats: {type(e).__name__}: {e}"}, 500)

    def _handle_mempalace_drawers(self):
        """GET /v1/mempalace/drawers?wing=X&room=Y — list drawers for treemap drill-down."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        wing = (params.get("wing") or [None])[0]
        room = (params.get("room") or [None])[0]

        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "Palace not found"}, 404)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"error": err}, 500)
            return

        try:
            from mempalace.palace import get_collection, get_closets_collection
            col = get_collection(palace_path, create=False)
            result = col.get(include=["metadatas", "documents"])
            drawers = []
            for did, meta, doc in zip(result["ids"], result["metadatas"], result["documents"]):
                m_wing = meta.get("wing", "")
                m_room = meta.get("room", "")
                if wing and m_wing != wing:
                    continue
                if room and m_room != room:
                    continue
                drawers.append({
                    "id": did,
                    "wing": m_wing,
                    "room": m_room,
                    "hall": meta.get("hall", ""),
                    "source_file": meta.get("source_file", ""),
                    "filed_at": meta.get("filed_at", ""),
                    "added_by": meta.get("added_by", ""),
                    "text": (doc or "")[:300],
                })
            closets = []
            try:
                ccol = get_closets_collection(palace_path, create=False)
                if ccol:
                    cresult = ccol.get(include=["metadatas", "documents"])
                    for cid, cmeta, cdoc in zip(cresult["ids"], cresult["metadatas"], cresult["documents"]):
                        c_wing = cmeta.get("wing", "")
                        c_room = cmeta.get("room", "")
                        if wing and c_wing != wing:
                            continue
                        if room and c_room != room:
                            continue
                        closets.append({
                            "id": cid,
                            "wing": c_wing,
                            "room": c_room,
                            "source_file": cmeta.get("source_file", ""),
                            "drawer_count": cmeta.get("drawer_count", 0),
                            "text": (cdoc or "")[:300],
                        })
            except Exception:
                pass
            self._send_json({"drawers": drawers, "count": len(drawers), "closets": closets})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Context Management handlers ---

    def _handle_context_config_get(self):
        """GET /v1/context/config — return context management configuration."""
        if not engine._context_manager:
            self._send_json(engine._CONTEXT_CONFIG_DEFAULTS)
            return
        self._send_json(engine._context_manager.get_config())

    def _handle_context_config_save(self):
        """POST /v1/context/config — save context management configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No config provided"}, 400)
            return
        if not engine._context_manager:
            engine._context_manager = engine.ContextManager()
        engine._context_manager.save_config(body)
        self._send_json({"status": "saved", "config": engine._context_manager.get_config()})

    def _handle_context_compact(self):
        """POST /v1/context/compact — manually trigger compaction for a session."""
        body = self._read_json()
        session_id = body.get("session_id", "")
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        if self._session_access_check(session_id, require_manage=True) is None:
            return
        session = sessions.get(session_id)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"}, 500)
            return
        try:
            before = engine._estimate_conversation_tokens(session.messages)
            # Force compaction regardless of threshold
            result = engine._context_manager.check_and_compact(
                session.messages, session.id, session.model,
                session.api_key, session.base_url,
                max_tokens=session.max_context,
                force=True,
            )
            with session.lock:
                session.messages = result[0]
            # Persist: mark old messages as compacted, insert new summary messages
            if result[1]:
                try:
                    with _db_conn() as conn:
                        # Mark ALL existing messages as compacted (preserves originals for search)
                        conn.execute(
                            "UPDATE messages SET compacted = 1 WHERE session_id = ? AND (compacted = 0 OR compacted IS NULL)",
                            (session_id,)
                        )
                        # Insert the new compacted message set (summaries + fresh tail)
                        for msg in session.messages:
                            role = msg.get("role", "user")
                            content = msg.get("content", "")
                            c = json.dumps(content) if not isinstance(content, str) else content
                            meta = json.dumps(msg.get("metadata", {})) if msg.get("metadata") else ""
                            conn.execute(
                                "INSERT INTO messages (session_id, role, content, metadata, compacted) VALUES (?, ?, ?, ?, 0)",
                                (session_id, role, c, meta)
                            )
                        conn.commit()
                except Exception as e:
                    print(f"  [WARN] Compact DB persist: {e}", flush=True)
            after = engine._estimate_conversation_tokens(session.messages)
            stats = engine._context_manager.get_stats(session_id)
            self._send_json({
                "status": "compacted" if result[1] else "no_change",
                "before_tokens": before,
                "after_tokens": after,
                "before_pct": int(before / session.max_context * 100) if session.max_context else 0,
                "after_pct": int(after / session.max_context * 100) if session.max_context else 0,
                "stats": stats,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_context_stats(self):
        """GET /v1/context/stats?session_id=X — context stats for a session."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        session_id = (qs.get("session_id") or [""])[0]
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"})
            return
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        stats = engine._context_manager.get_stats(session_id)
        self._send_json(stats)

    def _handle_expand_command(self):
        """POST /v1/commands/expand — expand a custom command template.
        Body: {agent, command, args}
        Returns: {text: "expanded prompt"}
        """
        body = self._read_json()
        agent_id = body.get("agent", "main")
        cmd_name = body.get("command", "")
        cmd_args = body.get("args", "")
        if not cmd_name:
            self._send_json({"error": "command name required"}, 400)
            return
        agent_cfg = engine.AgentConfig(agent_id)
        for cmd in agent_cfg.load_commands():
            if (cmd.get("name", "").lower() == cmd_name.lower() or
                    cmd.get("slug", "").lower() == cmd_name.lower()):
                expanded = engine.AgentConfig.expand_command(cmd, cmd_args)
                self._send_json({"text": expanded, "format": cmd.get("_format", "brain")})
                return
        self._send_json({"error": f"Command '{cmd_name}' not found"}, 404)

    def _handle_settings_commands(self):
        """POST /v1/settings/commands — enable/disable a built-in slash command."""
        body = self._read_json()
        name = body.get("name", "")
        enabled = body.get("enabled", True)
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        disabled = server_config.get("disabled_commands", [])
        if enabled and name in disabled:
            disabled.remove(name)
        elif not enabled and name not in disabled:
            disabled.append(name)
        server_config["disabled_commands"] = disabled
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            config["disabled_commands"] = disabled
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass
        self._send_json({"status": "ok", "disabled_commands": disabled})

    def _handle_restart(self):
        """POST /v1/restart — restart the server process."""
        self._send_json({"status": "restarting"})
        # Schedule restart after response is sent
        def do_restart():
            time.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do_restart, daemon=True).start()

    # --- Chat answer handler (interactive AskUserQuestion) ---

    def _handle_chat_answer(self):
        """POST /v1/chat/answer — deliver a user answer to a pending ask_user tool call.

        Body shapes:
          {session_id, answer: "..."}                             # single question
          {session_id, answers: {"<question>": "<answer>", ...}}  # batch
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        answer = body.get("answer")
        answers = body.get("answers")
        if not session_id or (answer is None and not isinstance(answers, dict)):
            self._send_json({"error": "session_id and answer/answers are required"}, 400)
            return
        if self._session_access_check(session_id) is None:
            return
        # Normalize answers dict values to strings
        if isinstance(answers, dict):
            answers = {str(k): str(v) for k, v in answers.items() if v is not None}
        from brain import deliver_ask_user_answer
        ok = deliver_ask_user_answer(
            session_id,
            answer=str(answer) if answer is not None else None,
            answers=answers if isinstance(answers, dict) and answers else None,
        )
        if not ok:
            self._send_json({"error": "no pending question for this session"}, 404)
            return
        self._send_json({"delivered": True, "session_id": session_id})

    # --- Notification handlers ---

    def _handle_notifications_list(self):
        """GET /v1/notifications — list recent notifications."""
        if not _notification_manager:
            self._send_json({"notifications": [], "unread": 0})
            return
        notifs = _notification_manager.get_notifications(limit=50)
        unread = _notification_manager.get_unread_count()
        self._send_json({"notifications": notifs, "unread": unread})

    def _handle_notifications_unread(self):
        """GET /v1/notifications/unread — get unread count."""
        count = _notification_manager.get_unread_count() if _notification_manager else 0
        self._send_json({"unread": count})

    def _handle_notifications_settings_post(self):
        """POST /v1/notifications/settings — save notification config."""
        body = self._read_json()
        if not _notification_manager:
            self._send_json({"error": "Notification manager not initialized"}, 500)
            return
        _notification_manager.update_config(body)
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            config["notifications"] = body
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            self._send_json({"error": f"Failed to save: {e}"}, 500)
            return
        self._send_json({"status": "saved"})

    def _handle_notifications_dismiss(self):
        """POST /v1/notifications/dismiss — dismiss notification(s)."""
        body = self._read_json()
        nid = body.get("id")
        if not _notification_manager:
            self._send_json({"error": "Not initialized"}, 500)
            return
        if nid == "all":
            _notification_manager.clear_all()
        elif nid:
            _notification_manager.dismiss(nid)
        self._send_json({"status": "dismissed"})

    def _handle_notifications_read(self):
        """POST /v1/notifications/read — mark notification(s) as read."""
        body = self._read_json()
        nid = body.get("id")  # None = mark all read
        if _notification_manager:
            _notification_manager.mark_read(nid)
        self._send_json({"status": "read"})

    # --- Backup / Restore handlers ---

    def _handle_backup_info(self):
        """GET /v1/backup/info — return what would be backed up."""
        import tarfile as _tarfile
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agents_dir = os.path.join(base, "agents")
        agent_names = engine.list_agents()
        total_files = 0
        total_size = 0
        agent_info = []
        for aname in agent_names:
            adir = os.path.join(agents_dir, aname)
            mems = len([f for f in os.listdir(adir) if f.endswith(".md")]) if os.path.isdir(adir) else 0
            skills_dir = os.path.join(adir, "skills")
            skills = len(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else 0
            agent_info.append({"name": aname, "memories": mems, "skills": skills})
            if os.path.isdir(adir):
                for root, dirs, files in os.walk(adir):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        if not f.endswith((".pyc", ".DS_Store")):
                            fp = os.path.join(root, f)
                            total_files += 1
                            try:
                                total_size += os.path.getsize(fp)
                            except OSError:
                                pass
        self._send_json({
            "agents": agent_info,
            "agent_count": len(agent_names),
            "total_files": total_files,
            "estimated_size_bytes": total_size,
        })

    def _handle_backup_create(self):
        """POST /v1/backup — create a tar.gz backup archive."""
        import tarfile as _tarfile
        import tempfile
        body = self._read_json()
        backup_type = body.get("type", "full")
        target_agent = body.get("agent")
        include_keys = body.get("include_keys", False)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agents_dir = os.path.join(base, "agents")
        backup_dir = os.path.join(base, "backups")
        os.makedirs(backup_dir, exist_ok=True)

        _EXCLUDE = {"__pycache__", ".DS_Store", "node_modules"}
        _EXCLUDE_EXT = {".pyc", ".db-wal", ".db-shm"}

        def _should_exclude(name):
            base_name = os.path.basename(name)
            if base_name in _EXCLUDE:
                return True
            _, ext = os.path.splitext(base_name)
            if ext in _EXCLUDE_EXT:
                return True
            return False

        ts = time.strftime("%Y%m%dT%H%M%S")
        if backup_type == "agent" and target_agent:
            fname = f"{target_agent.lower()}-{ts}.brain-backup.tar.gz"
        else:
            fname = f"backup-{ts}.brain-backup.tar.gz"
        backup_path = os.path.join(backup_dir, fname)

        try:
            with _tarfile.open(backup_path, "w:gz") as tar:
                prefix = f"backup-{ts}"

                # Add config.json (with redacted keys)
                config_path = os.path.join(base, "config.json")
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                    if not include_keys:
                        # Redact API keys
                        for pname, pcfg in config.get("providers", {}).items():
                            if "api_key" in pcfg:
                                pcfg["api_key"] = "REDACTED"
                        if "gmail" in config:
                            for k in list(config["gmail"].keys()):
                                if "password" in k.lower() or "secret" in k.lower():
                                    config["gmail"][k] = "REDACTED"
                    redacted_json = json.dumps(config, indent=2).encode("utf-8")
                    import io
                    info = _tarfile.TarInfo(name=f"{prefix}/config.json")
                    info.size = len(redacted_json)
                    tar.addfile(info, io.BytesIO(redacted_json))

                # Add agents
                agents_to_backup = [target_agent] if (backup_type == "agent" and target_agent) else engine.list_agents()
                for aname in agents_to_backup:
                    adir = os.path.join(agents_dir, aname)
                    if not os.path.isdir(adir):
                        continue
                    for root, dirs, files in os.walk(adir):
                        dirs[:] = [d for d in dirs if d not in _EXCLUDE]
                        for f in files:
                            if _should_exclude(f):
                                continue
                            fp = os.path.join(root, f)
                            arcname = f"{prefix}/agents/{aname}/{os.path.relpath(fp, adir)}"
                            try:
                                tar.add(fp, arcname=arcname)
                            except (OSError, PermissionError):
                                pass

                # Add databases (full backup only)
                if backup_type != "agent":
                    for db_name in ("chats.db", "scheduler.db", "costs.db"):
                        db_path = os.path.join(agents_dir, "main", db_name)
                        if os.path.exists(db_path):
                            # Safe SQLite copy using backup API
                            import sqlite3
                            tmp_db = os.path.join(backup_dir, f"_tmp_{db_name}")
                            try:
                                src = sqlite3.connect(db_path)
                                dst = sqlite3.connect(tmp_db)
                                src.backup(dst)
                                src.close()
                                dst.close()
                                tar.add(tmp_db, arcname=f"{prefix}/databases/{db_name}")
                            except Exception:
                                # Fallback: direct copy
                                tar.add(db_path, arcname=f"{prefix}/databases/{db_name}")
                            finally:
                                try:
                                    os.unlink(tmp_db)
                                except OSError:
                                    pass

            size = os.path.getsize(backup_path)
            self._send_json({
                "status": "created",
                "path": backup_path,
                "filename": fname,
                "size_bytes": size,
                "type": backup_type,
                "agents": agents_to_backup,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    def _handle_restore(self):
        """POST /v1/restore — restore from a backup archive."""
        import tarfile as _tarfile
        body = self._read_json()
        backup_path = body.get("path", "")
        strategy = body.get("strategy", "merge")

        if not backup_path or not os.path.exists(backup_path):
            self._send_json({"error": f"Backup file not found: {backup_path}"}, 400)
            return

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agents_dir = os.path.join(base, "agents")

        try:
            imported = {"agents": [], "memories": 0, "files": 0}
            with _tarfile.open(backup_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the prefix (first directory component)
                prefix = ""
                for m in members:
                    parts = m.name.split("/")
                    if len(parts) > 1:
                        prefix = parts[0]
                        break

                for member in members:
                    if member.isdir():
                        continue
                    parts = member.name.split("/")
                    if len(parts) < 3:
                        continue
                    # Skip config.json on restore (security: may have redacted keys)
                    if parts[-1] == "config.json" and len(parts) == 2:
                        continue

                    if parts[1] == "agents" and len(parts) >= 3:
                        agent_name = parts[2]
                        rel_path = "/".join(parts[3:])
                        dest = os.path.join(agents_dir, agent_name, rel_path)

                        if strategy == "merge" and os.path.exists(dest):
                            continue  # Skip existing files in merge mode

                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        f = tar.extractfile(member)
                        if f:
                            with open(dest, "wb") as out:
                                out.write(f.read())
                            imported["files"] += 1
                            if rel_path.endswith(".md"):
                                imported["memories"] += 1
                            if agent_name not in imported["agents"]:
                                imported["agents"].append(agent_name)

                    elif parts[1] == "databases" and len(parts) >= 3:
                        db_name = parts[2]
                        if strategy == "merge":
                            continue  # Don't overwrite databases in merge mode
                        dest = os.path.join(agents_dir, "main", db_name)
                        f = tar.extractfile(member)
                        if f:
                            with open(dest, "wb") as out:
                                out.write(f.read())
                            imported["files"] += 1

            self._send_json({
                "restored": True,
                "strategy": strategy,
                "imported": imported,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    # --- Nodes API handlers ---

    def _handle_workers_list(self):
        """GET /v1/workers — list workers, optionally filtered by session_id."""
        from execution import get_worker_registry
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        session_id = qs.get("session_id", [None])[0]
        registry = get_worker_registry()
        if session_id:
            workers = registry.list_session(session_id)
        else:
            workers = list(registry._workers.values())
        self._send_json({"workers": [registry.to_status_dict(w) for w in workers]})

    def _handle_workers_recent(self):
        """GET /v1/workers/recent — all workers across sessions (admin view)."""
        from execution import get_worker_registry
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        limit = int(qs.get("limit", [50])[0])
        registry = get_worker_registry()
        with registry._lock:
            all_workers = list(registry._workers.values())
        all_workers.sort(key=lambda w: w.started_at or 0, reverse=True)
        all_workers = all_workers[:limit]
        result = []
        for w in all_workers:
            d = registry.to_status_dict(w)
            d["session_id"] = w.session_id
            d["agent_id"] = w.agent_id
            d["duration"] = w.duration
            result.append(d)
        self._send_json({"workers": result, "total": len(registry._workers)})

    def _handle_worker_answer(self, path):
        """POST /v1/workers/{id}/answer — deliver answer to a worker question."""
        from execution import get_worker_registry
        parts = path.split("/")
        worker_id = parts[3] if len(parts) >= 5 else ""
        body = self._read_json_body()
        if not body:
            self._send_json({"error": "Missing body"}, 400)
            return
        answer = body.get("answer", "")
        if not answer:
            self._send_json({"error": "Missing 'answer' field"}, 400)
            return
        ok = get_worker_registry().answer(worker_id, answer)
        if not ok:
            self._send_json({"error": f"Worker '{worker_id}' not waiting for answer"}, 400)
            return
        self._send_json({"ok": True, "worker_id": worker_id})

    def _handle_nodes_list(self):
        """GET /v1/nodes — list all nodes with status."""
        nodes = []
        with _node_lock:
            for token, info in _node_registry.items():
                cfg = info.get("config", {})
                nodes.append({
                    "name": info["name"],
                    "description": cfg.get("description", ""),
                    "token": token,
                    "status": info["status"],
                    "paused": cfg.get("paused", False),
                    "hostname": info.get("hostname", ""),
                    "os": info.get("os", ""),
                    "tags": cfg.get("tags", []),
                    "allowed_tools": cfg.get("allowed_tools", []),
                    "max_concurrent": cfg.get("max_concurrent", 5),
                    "command_timeout": cfg.get("command_timeout", 300),
                    "last_heartbeat": info.get("last_heartbeat"),
                    "cpu_percent": info.get("cpu_percent"),
                    "mem_used_gb": info.get("mem_used_gb"),
                    "mem_total_gb": info.get("mem_total_gb"),
                    "disk_free_gb": info.get("disk_free_gb"),
                    "uptime_seconds": info.get("uptime_seconds"),
                    "active_commands": info.get("active_commands", 0),
                    "total_commands": info.get("total_commands", 0),
                    "connected_since": info.get("connected_since"),
                })
        self._send_json({"nodes": nodes})

    def _handle_node_poll(self):
        """GET /v1/nodes/poll?token=X — node polls for pending commands."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        token = params.get("token", "")

        with _node_lock:
            info = _node_registry.get(token)
            if not info:
                self._send_json({"error": "Invalid token"}, 401)
                return

            import urllib.parse
            info["status"] = "connected"
            info["last_heartbeat"] = time.time()
            info["hostname"] = urllib.parse.unquote(params.get("hostname", ""))
            info["os"] = urllib.parse.unquote(params.get("os", ""))
            try:
                info["cpu_percent"] = float(params.get("cpu_percent", 0))
                info["mem_used_gb"] = float(params.get("mem_used_gb", 0))
                info["mem_total_gb"] = float(params.get("mem_total_gb", 0))
                info["disk_free_gb"] = float(params.get("disk_free_gb", 0))
                info["uptime_seconds"] = int(params.get("uptime_seconds", 0))
                info["active_commands"] = int(params.get("active_commands", 0))
                info["total_commands"] = int(params.get("total_commands", 0))
            except (ValueError, TypeError):
                pass
            if not info.get("connected_since"):
                info["connected_since"] = time.time()

            if info.get("config", {}).get("paused"):
                self._send_json({"error": "Node is paused"}, 403)
                return

            pending = info.get("pending_commands", [])
            if pending:
                cmd = pending.pop(0)
                self._send_json({"command": cmd})
                return

        # Long-poll: wait up to 30s for a command
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(2)
            with _node_lock:
                info = _node_registry.get(token)
                if not info:
                    break
                pending = info.get("pending_commands", [])
                if pending:
                    cmd = pending.pop(0)
                    self._send_json({"command": cmd})
                    return

        self._send_json({"command": None})

    def _handle_node_result(self):
        """POST /v1/nodes/result — receive command result from node."""
        body = self._read_json()
        token = body.get("token", "")
        command_id = body.get("command_id", "")
        result = body.get("result", {})

        with _node_lock:
            if token not in _node_registry:
                self._send_json({"error": "Invalid token"}, 401)
                return
            entry = _node_commands.get(command_id)
            if entry:
                entry["result"] = result
                entry["result_event"].set()

        self._send_json({"status": "ok"})

    def _handle_nodes_action(self):
        """POST /v1/nodes — add/remove/pause/resume/update a node."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "add":
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "Missing name"}, 400)
                return
            import secrets
            token = f"nd_{secrets.token_hex(16)}"
            cfg = {
                "token": token,
                "description": body.get("description", ""),
                "allowed_tools": body.get("allowed_tools", ["execute_command", "read_file", "write_file", "list_directory"]),
                "tags": body.get("tags", []),
                "max_concurrent": body.get("max_concurrent", 5),
                "command_timeout": body.get("command_timeout", 300),
                "paused": False,
            }
            nodes_cfg = _load_node_config()
            nodes_cfg[name] = cfg
            _save_node_config(nodes_cfg)
            with _node_lock:
                _node_registry[token] = {
                    "name": name, "config": cfg, "status": "disconnected",
                    "last_heartbeat": None, "hostname": "", "os": "",
                    "cpu_percent": None, "mem_used_gb": None, "mem_total_gb": None,
                    "disk_free_gb": None, "uptime_seconds": None,
                    "active_commands": 0, "total_commands": 0,
                    "connected_since": None, "pending_commands": [],
                }
            port = server_config.get("port", 8420)
            install_cmd = f"python3 node.py --install --server http://SERVER_IP:{port} --token {token} --name {name}"
            self._send_json({"ok": True, "token": token, "install_command": install_cmd})

        elif action == "remove":
            name = body.get("name", "")
            nodes_cfg = _load_node_config()
            removed_token = None
            for n, cfg in nodes_cfg.items():
                if n == name:
                    removed_token = cfg.get("token")
                    break
            if name in nodes_cfg:
                del nodes_cfg[name]
                _save_node_config(nodes_cfg)
            if removed_token:
                with _node_lock:
                    _node_registry.pop(removed_token, None)
            self._send_json({"ok": True})

        elif action in ("pause", "resume"):
            name = body.get("name", "")
            paused = action == "pause"
            nodes_cfg = _load_node_config()
            if name in nodes_cfg:
                nodes_cfg[name]["paused"] = paused
                _save_node_config(nodes_cfg)
                with _node_lock:
                    for token, info in _node_registry.items():
                        if info["name"] == name:
                            info["config"]["paused"] = paused
                            break
            self._send_json({"ok": True, "paused": paused})

        elif action == "update":
            name = body.get("name", "")
            nodes_cfg = _load_node_config()
            if name in nodes_cfg:
                for key in ("description", "allowed_tools", "tags", "max_concurrent", "command_timeout"):
                    if key in body:
                        nodes_cfg[name][key] = body[key]
                _save_node_config(nodes_cfg)
                with _node_lock:
                    for token, info in _node_registry.items():
                        if info["name"] == name:
                            info["config"].update(nodes_cfg[name])
                            break
            self._send_json({"ok": True})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_node_execute(self):
        """POST /v1/nodes/execute — submit command to a node (internal)."""
        body = self._read_json()
        node = body.get("node", "")
        tool = body.get("tool", "")
        params = body.get("params", {})
        if not node or not tool:
            self._send_json({"error": "Missing node or tool"}, 400)
            return
        result = _node_submit_command(node, tool, params)
        self._send_json(result)

    # --- Channels API handlers ---

    def _handle_channels_list(self):
        """GET /v1/channels — list all messaging channels."""
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"channels": []})
            return
        self._send_json({"channels": mgr.status()})

    def _handle_channels_action(self):
        """POST /v1/channels — create/remove/update a channel."""
        body = self._read_json()
        action = body.get("action", "create")
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"error": "Channel manager not initialized"}, 500)
            return

        if action == "create":
            ch_id = body.get("id", body.get("name", ""))
            if not ch_id:
                self._send_json({"error": "Missing channel id"}, 400)
                return
            try:
                channel = mgr.create_channel(ch_id, body)
                if body.get("enabled", True):
                    channel.start()
                self._save_channel_config(mgr)
                self._send_json({"ok": True, "channel": channel.status()})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif action == "remove":
            ch_id = body.get("id", "")
            mgr.remove_channel(ch_id)
            self._save_channel_config(mgr)
            self._send_json({"ok": True})

        elif action == "update":
            ch_id = body.get("id", "")
            ch = mgr.channels.get(ch_id)
            if ch:
                for key in ("name", "agent_routing", "allowed_users", "default_model", "enabled"):
                    if key in body:
                        ch.config[key] = body[key]
                self._save_channel_config(mgr)
                self._send_json({"ok": True, "channel": ch.status()})
            else:
                self._send_json({"error": "Channel not found"}, 404)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_channel_lifecycle(self, path: str, action: str):
        """POST /v1/channels/:id/start|stop|restart."""
        parts = path.split("/")
        ch_id = parts[3] if len(parts) > 3 else ""
        mgr = _adapters_mod.channel_manager
        if not mgr:
            self._send_json({"error": "Channel manager not initialized"}, 500)
            return
        ch = mgr.channels.get(ch_id)
        if not ch:
            self._send_json({"error": "Channel not found"}, 404)
            return
        if action == "stop":
            ch.stop()
        elif action == "start":
            ch.start()
        elif action == "restart":
            ch.stop()
            ch.start()
        self._send_json({"ok": True, "channel": ch.status()})

    def _save_channel_config(self, mgr):
        """Persist channel config to config.json."""
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            channels = []
            for ch_id, ch in mgr.channels.items():
                channels.append({"id": ch_id, **ch.config})
            config["channels"] = channels
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Failed to save channel config: {e}", flush=True)

    def _validate_file_path(self, file_path):
        """Validate that a file path is within allowed directories. Returns resolved path or None.
        Allows the cctest tree, agents/, cwd, AND any path under a project's
        input_folders[]. Project input folders are the user-explicit set of
        paths the project has been told to mine, so it's safe to serve files
        from there back to the same authenticated user via /v1/files/download
        — citations from `mempalace_query` / `mempalace_kg_*` resolve to
        absolute paths under those roots."""
        if not file_path:
            return None
        file_path = os.path.expanduser(file_path)
        resolved = os.path.realpath(file_path)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agents_dir = os.path.join(base, "agents")
        cwd = os.getcwd()
        allowed = [base, agents_dir, cwd]
        if any(resolved.startswith(d) for d in allowed):
            return resolved
        # Project input folders — symlink-resolved, deduped. Any project
        # this user can see contributes its input_folder roots.
        try:
            auth_user = getattr(self, '_auth_user', None) or _auth_mod.SYNTHETIC_ADMIN
            uid = auth_user.get("id", "")
            team_ids = []
            if uid and uid != "__system__":
                try:
                    team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(uid)]
                except Exception:
                    pass
            for agent_id in os.listdir(engine.AGENTS_DIR):
                projects = engine.ProjectManager.list_projects(
                    agent_id, user_id=uid or None, user_team_ids=team_ids,
                )
                for proj in projects:
                    for folder in (proj.get("input_folders") or []):
                        p = (folder or {}).get("path", "").strip()
                        if not p:
                            continue
                        root = os.path.realpath(os.path.expanduser(p))
                        if resolved.startswith(root):
                            return resolved
        except Exception:
            pass
        return None

    def _resolve_project_basename(self, raw_path):
        """Best-effort lookup: given a bare basename or relative path
        (the shape MemPalace drawers carry as `source_file`), find a
        matching file under any project input_folders[] the authenticated
        user can see. Strips a trailing `.md` companion suffix
        automatically. Returns the absolute path of the first match, or
        None. First match wins; if multiple projects have a same-named
        file the user gets one of them — better than nothing, and the
        right-panel card already shows the basename so the user can tell.
        """
        if not raw_path or "/" in raw_path[:1]:
            return None
        # Normalise the lookup name: a) strip trailing .md if it sits on
        # top of a known binary extension; b) keep the raw name as-is
        # otherwise (e.g. .md sources are first-class).
        candidates = [raw_path]
        m = re.match(r"^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$", raw_path, re.IGNORECASE)
        if m:
            candidates.insert(0, m.group(1))  # try original binary first
        try:
            auth_user = getattr(self, '_auth_user', None) or _auth_mod.SYNTHETIC_ADMIN
            uid = auth_user.get("id", "")
            team_ids = []
            if uid and uid != "__system__":
                try:
                    team_ids = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(uid)]
                except Exception:
                    pass
            roots = []
            for agent_id in os.listdir(engine.AGENTS_DIR):
                projects = engine.ProjectManager.list_projects(
                    agent_id, user_id=uid or None, user_team_ids=team_ids,
                )
                for proj in projects:
                    for folder in (proj.get("input_folders") or []):
                        p = (folder or {}).get("path", "").strip()
                        if p:
                            roots.append(os.path.realpath(os.path.expanduser(p)))
            roots = list(dict.fromkeys(roots))  # dedupe, keep order
            for root in roots:
                if not os.path.isdir(root):
                    continue
                for cand in candidates:
                    base = os.path.basename(cand)
                    # First: cheap top-level glob
                    direct = os.path.join(root, cand)
                    if os.path.isfile(direct):
                        return os.path.realpath(direct)
                    # Then: recursive basename walk (capped to avoid runaway
                    # scans on misconfigured roots).
                    scanned = 0
                    for dirpath, _dirs, files in os.walk(root):
                        if base in files:
                            return os.path.realpath(os.path.join(dirpath, base))
                        scanned += 1
                        if scanned > 5000:  # safety
                            break
        except Exception:
            return None
        return None

    def _handle_file_download(self):
        """GET /v1/files/download?path=<absolute_path> — serve a file for download."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        file_path = qs.get("path", [""])[0]
        resolved = self._validate_file_path(file_path)
        # If the validator rejected (None) OR returned a path that doesn't
        # exist on disk (because the input was relative + got resolved
        # against the server's CWD into the cctest tree), try
        # project-input-folder basename resolution. MemPalace drawers
        # store `source_file` as a relative path (sometimes the bare
        # basename of a binary that's deeper in a project input folder).
        if not resolved or not os.path.isfile(resolved):
            looked_up = self._resolve_project_basename(file_path)
            if looked_up and os.path.isfile(looked_up):
                resolved = looked_up
        if not resolved:
            self._send_json({"error": "Invalid or disallowed file path"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        ext = resolved.rsplit(".", 1)[-1].lower() if "." in resolved else ""
        content_types = {
            "md": "text/markdown", "txt": "text/plain", "py": "text/x-python",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "html": "text/html", "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "js": "application/javascript", "ts": "text/typescript",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "svg": "image/svg+xml",
        }
        ct = content_types.get(ext, "application/octet-stream")
        filename = os.path.basename(resolved)
        # Render PDFs and images inline so the browser opens them in a new
        # tab instead of force-downloading. Office-binary types stay
        # `attachment` because browsers can't render them — they'd just
        # download with a confusing blob:// URL otherwise.
        inline_exts = {"pdf", "png", "jpg", "jpeg", "gif", "svg",
                       "txt", "md", "html", "json", "csv"}
        disposition = "inline" if ext in inline_exts else "attachment"
        try:
            with open(resolved, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(data))
            # Quote-escape the filename so non-ASCII (German umlauts) and
            # spaces don't break the header. RFC 5987 filename* takes a
            # UTF-8-encoded value.
            from urllib.parse import quote as _urlq
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{_urlq(filename)}",
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_file_preview(self):
        """GET /v1/files/preview?path=<absolute_path>&lines=100 — return file content for preview."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        file_path = qs.get("path", [""])[0]
        max_lines = int(qs.get("lines", ["100"])[0])
        resolved = self._validate_file_path(file_path)
        if not resolved:
            self._send_json({"error": "Invalid or disallowed file path"}, 403)
            return
        if not os.path.isfile(resolved):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            size = os.path.getsize(resolved)
            name = os.path.basename(resolved)
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            image_exts = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"}
            office_exts = {"pdf", "docx", "xlsx", "pptx", "csv"}
            if ext in image_exts:
                self._send_json({
                    "path": resolved, "name": name, "size": size,
                    "type": "image", "ext": ext,
                })
                return
            if ext in office_exts:
                try:
                    if ext == "pdf":
                        content = engine.DocumentParser.parse_pdf(resolved)
                    elif ext == "docx":
                        content = engine.DocumentParser.parse_docx(resolved)
                    elif ext in ("xlsx", "xls"):
                        content = engine.DocumentParser.parse_xlsx(resolved)
                    elif ext == "pptx":
                        content = engine.DocumentParser.parse_pptx(resolved)
                    elif ext == "csv":
                        with open(resolved, "r", errors="replace") as f:
                            content = f.read(50 * 1024)
                    else:
                        content = ""
                    all_lines = content.splitlines()
                    truncated = len(all_lines) > 200
                    self._send_json({
                        "path": resolved, "name": name, "size": size,
                        "type": "document", "ext": ext,
                        "content": "\n".join(all_lines[:200]), "truncated": truncated,
                    })
                except Exception as e:
                    self._send_json({"error": f"Could not parse {ext.upper()}: {e}"}, 500)
                return
            # Plain text / code
            max_bytes = 50 * 1024
            with open(resolved, "r", errors="replace") as f:
                lines = []
                total_bytes = 0
                for i, line in enumerate(f):
                    if i >= max_lines or total_bytes >= max_bytes:
                        truncated = True
                        break
                    lines.append(line)
                    total_bytes += len(line.encode("utf-8"))
                else:
                    truncated = False
            self._send_json({
                "path": resolved, "name": name, "size": size,
                "type": "text",
                "content": "".join(lines), "truncated": truncated,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Code Mode Endpoints ──

    def _handle_file_tree(self):
        """GET /v1/files/tree?path=<dir>&depth=2 — return directory tree for Code mode."""
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(self.path).query)
        dir_path = unquote(qs.get("path", [""])[0])
        max_depth = int(qs.get("depth", ["2"])[0])
        # Empty path defaults to the user's home dir, so the folder picker
        # doesn't need to know where to start.
        if not dir_path:
            dir_path = os.path.expanduser("~")
        else:
            dir_path = os.path.expanduser(dir_path)
        if not os.path.isdir(dir_path):
            self._send_json({"error": "Invalid or missing directory path"}, 400)
            return

        IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
                  ".mypy_cache", ".pytest_cache", ".DS_Store", ".claude", "dist", "build"}

        def _scan(base, depth=0):
            items = []
            try:
                entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                return items
            for entry in entries:
                if entry.name in IGNORE or entry.name.startswith("."):
                    continue
                node = {"name": entry.name, "path": entry.path}
                if entry.is_dir():
                    node["type"] = "dir"
                    if depth < max_depth:
                        node["children"] = _scan(entry.path, depth + 1)
                    else:
                        node["children"] = []
                        node["truncated"] = True
                else:
                    node["type"] = "file"
                    try:
                        node["size"] = entry.stat().st_size
                    except OSError:
                        node["size"] = 0
                items.append(node)
            return items

        tree = _scan(dir_path)
        self._send_json({"path": dir_path, "tree": tree})

    # ── Artifact Endpoints ──

    def _handle_artifacts_list(self):
        """GET /v1/artifacts?session_id=X — list artifacts for a session."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session_id = qs.get("session_id", [""])[0]
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        artifacts = ChatDB.get_artifacts(session_id)
        self._send_json({"artifacts": artifacts})

    def _handle_artifacts_browse(self):
        """GET /v1/artifacts/browse?agent_id=X&limit=N&source=chat|scheduled
        — browse all artifacts across sessions, tagged by source so the UI
        can split the view. Scheduled-task artifacts are identified by
        session_id matching `sched-<run_id>` (set by the scheduler's
        synthetic session context)."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get("agent_id", [None])[0]
        limit = int(qs.get("limit", ["100"])[0])
        source_filter = qs.get("source", [None])[0]  # chat | scheduled | None
        artifacts = ChatDB.get_all_artifacts(agent_id=agent_id, limit=limit)

        # Enrich: source tag + schedule-run summary for scheduled artifacts.
        # Batch-resolve run rows so we don't hit the scheduler DB per-artifact.
        run_ids_needed = set()
        for a in artifacts:
            sid = a.get("session_id") or ""
            if sid.startswith("sched-"):
                a["source"] = "scheduled"
                try:
                    a["run_id"] = int(sid.split("-", 1)[1])
                    run_ids_needed.add(a["run_id"])
                except (ValueError, IndexError):
                    a["run_id"] = None
            else:
                a["source"] = "chat"
                a["run_id"] = None

        run_map: dict = {}
        if run_ids_needed and engine._scheduler:
            for rid in run_ids_needed:
                row = engine._scheduler.get_run(rid)
                if row:
                    run_map[rid] = {
                        "run_id": rid,
                        "schedule_name": row.get("schedule_name"),
                        "status": row.get("status"),
                        "started_at": row.get("started_at"),
                    }
        for a in artifacts:
            if a.get("run_id") in run_map:
                a["schedule_run"] = run_map[a["run_id"]]

        if source_filter in ("chat", "scheduled"):
            artifacts = [a for a in artifacts if a.get("source") == source_filter]

        # Fetch text preview for each text-based artifact
        binary_types = {"image", "document"}
        for a in artifacts:
            if a.get("type") not in binary_types:
                preview = ChatDB.get_artifact_preview(a["id"], max_chars=300)
                a["preview"] = preview
            else:
                a["preview"] = None
        self._send_json({"artifacts": artifacts})

    def _handle_artifact_content(self, path):
        """GET /v1/artifacts/<id>/content?version=N — get artifact version content."""
        from urllib.parse import urlparse, parse_qs
        import base64
        parts = path.split("/")
        # /v1/artifacts/<id>/content
        artifact_id = parts[3] if len(parts) >= 5 else ""
        qs = parse_qs(urlparse(self.path).query)
        version = qs.get("version", [None])[0]

        artifact = ChatDB.get_artifact(artifact_id)
        if not artifact:
            self._send_json({"error": "Artifact not found"}, 404)
            return

        ver_data = ChatDB.get_artifact_content(artifact_id, version)
        if not ver_data:
            self._send_json({"error": "Version not found"}, 404)
            return

        content_raw = ver_data["content"]
        is_binary = artifact["type"] in ("image", "document")

        if content_raw is None:
            # Disk-only fallback (file was > 5MB)
            try:
                with open(artifact["path"], "rb") as f:
                    content_raw = f.read()
            except Exception:
                self._send_json({"error": "Content not available"}, 404)
                return

        if is_binary:
            content_str = base64.b64encode(content_raw if isinstance(content_raw, bytes) else content_raw.encode()).decode()
            encoding = "base64"
        else:
            content_str = content_raw.decode("utf-8", errors="replace") if isinstance(content_raw, bytes) else content_raw
            encoding = "text"

        self._send_json({
            "artifact_id": artifact_id,
            "name": artifact["name"],
            "type": artifact["type"],
            "version": ver_data["version"],
            "content": content_str,
            "encoding": encoding,
            "size": ver_data["size"],
        })

    def _handle_artifact_download(self, path):
        """GET /v1/artifacts/<id>/download?version=N — download artifact content."""
        from urllib.parse import urlparse, parse_qs
        parts = path.split("/")
        artifact_id = parts[3] if len(parts) >= 5 else ""
        qs = parse_qs(urlparse(self.path).query)
        version = qs.get("version", [None])[0]

        artifact = ChatDB.get_artifact(artifact_id)
        if not artifact:
            self._send_json({"error": "Artifact not found"}, 404)
            return

        filename = artifact["name"]
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        content_types = {
            "md": "text/markdown", "txt": "text/plain", "py": "text/x-python",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "html": "text/html", "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "js": "application/javascript", "ts": "text/typescript",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "svg": "image/svg+xml",
        }
        ct = content_types.get(ext, "application/octet-stream")

        # If no version specified, serve disk file
        if not version:
            try:
                with open(artifact["path"], "rb") as f:
                    data = f.read()
            except Exception:
                self._send_json({"error": "File not found on disk"}, 404)
                return
        else:
            ver_data = ChatDB.get_artifact_content(artifact_id, version)
            if not ver_data or ver_data["content"] is None:
                self._send_json({"error": "Version content not available"}, 404)
                return
            data = ver_data["content"] if isinstance(ver_data["content"], bytes) else ver_data["content"].encode()

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path):
        """Serve static files from web/ directory."""
        if path == "/":
            path = "/web/index.html"
        elif not path.startswith("/web/"):
            path = "/web" + path

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filepath = os.path.join(base, path.lstrip("/"))

        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext = filepath.rsplit(".", 1)[-1].lower()
        content_types = {
            "html": "text/html", "css": "text/css", "js": "application/javascript",
            "json": "application/json", "png": "image/png", "svg": "image/svg+xml",
            "ico": "image/x-icon",
            "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf",
        }
        ct = content_types.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        if ext in ("html", "css", "js"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        elif ext in ("woff2", "woff", "ttf"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)
