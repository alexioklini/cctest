"""Skill-generation HTTP handlers — parallel to handlers/admin_workflows.py.

Sub-mixin of AdminHandlerMixin. Holds the /v1/skills/generate* + /v1/skills/save
handler methods. Like admin_workflows.py, this module references `engine`,
`brain`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals() (this module's name is added to that list).
All other helpers (`_send_json`, `_read_json`, `_session_access_check`, …)
resolve via `self.` against the combined handler class MRO.

The deliverable is a PER-USER skill (SKILL.md + skill.meta.json under
agents/<agent>/user_skills/<slug>/), owned by the caller and shared via the
generic 5-field block. Generation mirrors workflow_gen: async gen-row → poll →
review-then-save. See engine/skill_gen.py + CLAUDE.md.
"""
from __future__ import annotations

import json


class AdminSkillsGenHandlers:

    def _handle_skill_generate(self):
        """POST /v1/skills/generate — start LLM generation of a skill draft
        (SKILL.md body) from a chat, a plan document, or a natural-language
        description. Body:
        {source: {type: 'chat'|'plan'|'nl', session_id? | text?},
         agent_id?, instructions?, attachments?: [{name, text}]}
        → {gen_id}. Poll GET /v1/skills/generate/<gen_id>."""
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
        from engine import skill_gen
        gen_id = skill_gen.start_generation(
            agent_id=agent_id, source_kind=kind, source_ref=source_ref,
            source_text=source_text,
            instructions=str(body.get("instructions") or ""),
            attachments=attachments, user_id=au.get("id") or "")
        self._send_json({"gen_id": gen_id, "status": "generating"})

    def _skill_gen_row_checked(self, path):
        """Shared: parse gen_id from path, load row, enforce owner-or-admin."""
        parts = path.split("/")
        # /v1/skills/generate/<gen_id>[/cancel]
        gen_id = parts[4] if len(parts) > 4 else ""
        from server_lib.db import ChatDB
        row = ChatDB.get_skill_gen(gen_id)
        if not row:
            self._send_json({"error": "generation not found"}, 404)
            return None
        user = getattr(self, "_auth_user", None) or {}
        if user.get("role") != "admin" and \
                (row.get("created_by") or "") != (user.get("id") or ""):
            self._send_json({"error": "forbidden"}, 403)
            return None
        return row

    def _handle_skill_generate_get(self, path):
        """GET /v1/skills/generate/<gen_id> — poll status + result draft."""
        row = self._skill_gen_row_checked(path)
        if row is None:
            return
        from engine import skill_gen
        out = {
            "gen_id": row["id"],
            "status": row.get("status") or "",
            "phase": row.get("phase") or "",
            "model": row.get("model") or "",
            "error": row.get("error") or "",
            "source_kind": row.get("source_kind") or "",
            "steps": skill_gen.get_steps(row["id"]),
        }
        if (row.get("status") or "") in ("ready", "ready_with_warnings"):
            out["slug"] = row.get("slug") or ""
            out["display_name"] = row.get("display_name") or ""
            out["description"] = row.get("description") or ""
            out["body_md"] = row.get("body_md") or ""
            out["notes"] = row.get("notes") or ""
            try:
                out["warnings"] = json.loads(row.get("warnings") or "[]")
            except (TypeError, ValueError):
                out["warnings"] = []
        self._send_json(out)

    def _handle_skill_generate_cancel(self, path):
        """POST /v1/skills/generate/<gen_id>/cancel — cancel a generation."""
        row = self._skill_gen_row_checked(path)
        if row is None:
            return
        if (row.get("status") or "") not in ("generating",):
            self._send_json({"error": "generation already finished"}, 400)
            return
        from engine import skill_gen
        skill_gen.request_cancel(row["id"])
        self._send_json({"status": "cancelling"})

    def _handle_skill_save(self):
        """POST /v1/skills/save — persist a reviewed skill draft as a per-user
        skill (SKILL.md + skill.meta.json). Owner = caller. Body:
        {agent_id?, slug, display_name, description, body_md,
         visibility?, owner_team_id?, extra_member_user_ids?, excluded_user_ids?,
         source_kind?, source_ref?} → {status, slug}."""
        body = self._read_json()
        agent_id = body.get("agent_id") or "main"
        slug = str(body.get("slug") or "")
        display_name = str(body.get("display_name") or "")
        description = str(body.get("description") or "")
        body_md = str(body.get("body_md") or "")
        if not body_md.strip():
            self._send_json({"error": "body_md is required"}, 400)
            return
        au = getattr(self, "_auth_user", None) or {}
        owner = au.get("id") or ""
        if not owner:
            self._send_json({"error": "authentication required"}, 401)
            return
        share = {}
        for k in ("visibility", "owner_team_id", "extra_member_user_ids",
                  "excluded_user_ids"):
            if k in body:
                share[k] = body[k]
        # A team share requires the caller to actually be in that team.
        if share.get("visibility") == "team":
            tid = share.get("owner_team_id") or ""
            from server_lib.auth import AuthDB
            if not tid or not any(
                    t["id"] == tid for t in AuthDB.get_user_teams(owner)):
                self._send_json(
                    {"error": "owner_team_id required and caller must be a "
                              "member of that team"}, 400)
                return
        res = engine.AgentConfig.save_user_skill(
            agent_id, slug=slug, display_name=display_name,
            description=description, body_md=body_md, owner_user_id=owner,
            source_kind=str(body.get("source_kind") or ""),
            source_ref=str(body.get("source_ref") or ""),
            share=share or None)
        if res.get("error"):
            self._send_json(res, 400)
            return
        self._send_json(res)
