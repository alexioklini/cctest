"""Workflow HTTP handlers — extracted from handlers/admin.py (module-split refactor).

Sub-mixin of AdminHandlerMixin. Holds ONLY the /v1/agents/<id>/workflows/*
handler methods + workflow-only private helpers. AdminHandlerMixin inherits
this class, so the combined BrainAgentHandler MRO is unchanged.

Like admin.py, this module references `engine`, `brain`, `client`, `_db_conn`,
`sqlite3`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals(). This module's name is added to that
function's injection list so the names resolve identically to admin.py.
All other helpers (`_send_json`, `_read_json`, `_parse_agent_from_path`, …)
resolve via `self.` against the combined handler class MRO.
"""
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


class AdminWorkflowHandlers:
    """Workflow handler methods (sub-mixin of AdminHandlerMixin)."""

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
            plan_md = engine.WorkflowEngine.get_workflow_plan(agent_id, name)
            self._send_json({"agent": agent_id, "name": name, "source": src,
                             "plan_md": plan_md})
            return
        workflows = engine.WorkflowEngine.list_workflows(agent_id)
        # Filter to workflows the caller can see (generic sharing model).
        # Legacy workflows with no sidecar (owner='') stay all-authenticated.
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            visible = []
            for wf in workflows:
                meta = engine.WorkflowEngine.get_workflow_meta(agent_id, wf["name"])
                blk = engine.WorkflowEngine.workflow_block(meta or {})
                if _auth_mod.can_access(user, blk, legacy_open=True):
                    visible.append(wf)
            workflows = visible
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
            # Optional plan sidecar (<name>.plan.md) — only touched when the
            # field is present; empty string removes the sidecar.
            if "plan_md" in body:
                engine.WorkflowEngine.save_workflow_plan(
                    agent_id, safe, body.get("plan_md") or "")
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

    # ── AI workflow generation (engine/workflow_gen.py) ─────────────────────

    def _handle_workflow_generate(self):
        """POST /v1/workflows/generate — start LLM generation of a workflow
        draft (.flow + plan.md) from a chat, a plan document, or a
        natural-language description. Body:
        {source: {type: 'chat'|'plan'|'nl', session_id? | text?},
         agent_id?, instructions?, attachments?: [{name, text}]}
        → {gen_id}. Poll GET /v1/workflows/generate/<gen_id>."""
        body = self._read_json()
        source = body.get("source") or {}
        kind = source.get("type") or ""
        if kind not in ("chat", "plan", "nl"):
            self._send_json({"error": "source.type must be chat|plan|nl"}, 400)
            return
        agent_id = body.get("agent_id") or "main"
        source_ref = ""
        source_text = ""
        if kind == "chat":
            sid = source.get("session_id") or ""
            info = self._session_access_check(sid)
            if info is None:
                return  # access check already sent the error response
            source_ref = sid
        else:
            source_text = str(source.get("text") or "")
            if not source_text.strip():
                self._send_json({"error": "source.text is required"}, 400)
                return
        attachments = body.get("attachments") or []
        if not isinstance(attachments, list) or len(attachments) > 10:
            self._send_json({"error": "attachments must be a list (max 10)"}, 400)
            return
        au = getattr(self, "_auth_user", None) or {}
        from engine import workflow_gen
        gen_id = workflow_gen.start_generation(
            agent_id=agent_id, source_kind=kind, source_ref=source_ref,
            source_text=source_text,
            instructions=str(body.get("instructions") or ""),
            attachments=attachments, user_id=au.get("id") or "")
        self._send_json({"gen_id": gen_id, "status": "generating"})

    def _workflow_gen_row_checked(self, path):
        """Shared: parse gen_id from path, load row, enforce owner-or-admin."""
        parts = path.split("/")
        # /v1/workflows/generate/<gen_id>[/cancel]
        gen_id = parts[4] if len(parts) > 4 else ""
        from server_lib.db import ChatDB
        row = ChatDB.get_workflow_gen(gen_id)
        if not row:
            self._send_json({"error": "generation not found"}, 404)
            return None
        user = getattr(self, "_auth_user", None) or {}
        if user.get("role") != "admin" and \
                (row.get("created_by") or "") != (user.get("id") or ""):
            self._send_json({"error": "forbidden"}, 403)
            return None
        return row

    def _handle_workflow_generate_get(self, path):
        """GET /v1/workflows/generate/<gen_id> — poll status + result draft."""
        row = self._workflow_gen_row_checked(path)
        if row is None:
            return
        from engine import workflow_gen
        out = {
            "gen_id": row["id"],
            "status": row.get("status") or "",
            "phase": row.get("phase") or "",
            "model": row.get("model") or "",
            "error": row.get("error") or "",
            "source_kind": row.get("source_kind") or "",
            "steps": workflow_gen.get_steps(row["id"]),
        }
        if (row.get("status") or "") in ("ready", "ready_with_warnings"):
            out["flow_source"] = row.get("flow_source") or ""
            out["plan_md"] = row.get("plan_md") or ""
            out["notes"] = row.get("notes") or ""
            out["suggested_name"] = row.get("suggested_name") or ""
            try:
                out["warnings"] = json.loads(row.get("warnings") or "[]")
            except (TypeError, ValueError):
                out["warnings"] = []
        self._send_json(out)

    def _handle_workflow_generate_cancel(self, path):
        """POST /v1/workflows/generate/<gen_id>/cancel — cancel a generation."""
        row = self._workflow_gen_row_checked(path)
        if row is None:
            return
        if (row.get("status") or "") not in ("generating",):
            self._send_json({"error": "generation already finished"}, 400)
            return
        from engine import workflow_gen
        workflow_gen.request_cancel(row["id"])
        self._send_json({"status": "cancelling"})

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
        for k in ("variables_json", "steps_json", "transcript_json", "output_paths_json"):
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
        "read_file", "read_document",
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
        # v9.290.2: RELIABLE output paths — the interpreter records every
        # write_file/edit_file/agent_step output path UNTRUNCATED in
        # output_paths_json. The steps-regex above loses paths whose step
        # detail was truncated at 120 chars (the empty-artifacts bug), so these
        # are the authoritative source for outputs.
        try:
            for p in json.loads(row.get("output_paths_json") or "[]"):
                if p:
                    claim(str(p), "output")
        except Exception:
            pass
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
