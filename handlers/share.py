"""Generic sharing / visibility model — HTTP handlers.

One endpoint family for chats, projects, scheduled tasks, workflows and
artifacts. The five-field block (owner_user_id, visibility, owner_team_id,
extra_member_user_ids, excluded_user_ids) is loaded per item-type, then
`server_lib.auth.can_access` / `can_manage` decide read / manage.

Routes (all under the standard /v1/* auth gate):
  GET  /v1/share?item_type=X&item_id=Y[&agent_id=A]   → current block + caller_can_manage
  POST /v1/share          body {item_type,item_id,agent_id?, visibility, owner_team_id,
                                 extra_member_user_ids, excluded_user_ids,
                                 visibility_override}        → update ACL (owner/admin)
  POST /v1/share/transfer body {item_type,item_id,agent_id?, new_owner_user_id} → transfer
"""
import json
import os
import sys

from server_lib import auth as _auth_mod
from server_lib import db as _db
import brain as engine


def _srv():
    return sys.modules.get("__main__") or sys.modules["server"]


ITEM_TYPES = ("chat", "project_chat", "project", "schedule", "workflow", "artifact")
# Item types that have a transferable owner (artifacts inherit their parent's).
TRANSFERABLE = ("chat", "project_chat", "project", "schedule", "workflow")


# ── per-item-type block loaders / savers ─────────────────────────────
# Each loader returns (block, ctx) where ctx is opaque state the saver
# needs; or (None, error_string). Block keys: owner_user_id, visibility,
# owner_team_id, extra_member_user_ids, excluded_user_ids.

def _load_chat(item_id: str, agent_id: str):
    info = _db.ChatDB.get_session_info(item_id)
    if not info:
        return None, "Chat not found"
    return _db.session_share_block(info), {"info": info}


def _save_chat(item_id: str, ctx: dict, block: dict):
    _db.ChatDB.update_session_share(
        item_id,
        visibility=block["visibility"],
        team_id=block.get("owner_team_id", ""),
        extra_member_user_ids=block.get("extra_member_user_ids", []),
        excluded_user_ids=block.get("excluded_user_ids", []),
    )


def _transfer_chat(item_id: str, ctx: dict, new_owner: str):
    _db.ChatDB.update_session_share(item_id, owner_user_id=new_owner)


def _project_block(cfg: dict) -> dict:
    return {
        "owner_user_id": cfg.get("owner_user_id", "") or "",
        "visibility": _auth_mod.normalize_visibility(cfg.get("visibility", "global")),
        "owner_team_id": cfg.get("owner_team_id", "") or "",
        "extra_member_user_ids": cfg.get("extra_member_user_ids", []) or [],
        "excluded_user_ids": cfg.get("excluded_user_ids", []) or [],
    }


def _load_project(item_id: str, agent_id: str):
    # item_id may be either the stable project id (uuid hex[:12], as the
    # favourites/header system uses) or the folder name.
    aid = agent_id or "main"
    cfg = engine.ProjectManager.get_project(aid, item_id)
    folder_name = item_id
    if not cfg:
        for p in engine.ProjectManager.list_projects(aid):
            if p.get("id") == item_id:
                folder_name = p["name"]
                cfg = engine.ProjectManager.get_project(aid, folder_name)
                break
    if not cfg:
        return None, "Project not found"
    return _project_block(cfg), {"agent_id": aid, "name": folder_name, "cfg": cfg}


def _save_project(item_id: str, ctx: dict, block: dict):
    engine.ProjectManager.update_project(ctx["agent_id"], ctx["name"], {
        "visibility": block["visibility"],
        "owner_team_id": block.get("owner_team_id", ""),
        "extra_member_user_ids": block.get("extra_member_user_ids", []),
        "excluded_user_ids": block.get("excluded_user_ids", []),
    })


def _transfer_project(item_id: str, ctx: dict, new_owner: str):
    engine.ProjectManager.update_project(ctx["agent_id"], ctx["name"], {"owner_user_id": new_owner})


def _load_schedule(item_id: str, agent_id: str):
    row = engine._schedule_get_row(item_id)
    if not row:
        return None, "Schedule not found"
    return engine._schedule_share_block(row), {"name": item_id, "row": row}


def _save_schedule(item_id: str, ctx: dict, block: dict):
    engine._schedule_update_share(item_id,
                                  visibility=block["visibility"],
                                  owner_team_id=block.get("owner_team_id", ""),
                                  extra_member_user_ids=block.get("extra_member_user_ids", []),
                                  excluded_user_ids=block.get("excluded_user_ids", []))


def _transfer_schedule(item_id: str, ctx: dict, new_owner: str):
    engine._schedule_update_share(item_id, owner_user_id=new_owner)


def _load_workflow(item_id: str, agent_id: str):
    meta = engine.WorkflowEngine.get_workflow_meta(agent_id or "main", item_id)
    if meta is None:
        return None, "Workflow not found"
    return engine.WorkflowEngine.workflow_block(meta), {"agent_id": agent_id or "main", "name": item_id, "meta": meta}


def _save_workflow(item_id: str, ctx: dict, block: dict):
    engine.WorkflowEngine.update_workflow_meta(ctx["agent_id"], ctx["name"], {
        "visibility": block["visibility"],
        "owner_team_id": block.get("owner_team_id", ""),
        "extra_member_user_ids": block.get("extra_member_user_ids", []),
        "excluded_user_ids": block.get("excluded_user_ids", []),
    })


def _transfer_workflow(item_id: str, ctx: dict, new_owner: str):
    engine.WorkflowEngine.update_workflow_meta(ctx["agent_id"], ctx["name"], {"owner_user_id": new_owner})


_LOADERS = {
    "chat": _load_chat,
    "project_chat": _load_chat,
    "project": _load_project,
    "schedule": _load_schedule,
    "workflow": _load_workflow,
}
_SAVERS = {
    "chat": _save_chat,
    "project_chat": _save_chat,
    "project": _save_project,
    "schedule": _save_schedule,
    "workflow": _save_workflow,
}
_TRANSFERRERS = {
    "chat": _transfer_chat,
    "project_chat": _transfer_chat,
    "project": _transfer_project,
    "schedule": _transfer_schedule,
    "workflow": _transfer_workflow,
}


class ShareHandlerMixin:
    """Mixin: generic /v1/share endpoints."""

    def _share_user(self):
        return getattr(self, "_auth_user", _auth_mod.SYNTHETIC_ADMIN)

    # ── GET /v1/share ────────────────────────────────────────────────
    def _handle_share_get(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        item_type = (q.get("item_type", [""])[0] or "").strip()
        item_id = (q.get("item_id", [""])[0] or "").strip()
        agent_id = (q.get("agent_id", [""])[0] or "").strip()
        if item_type == "artifact":
            return self._handle_share_artifact_get(item_id)
        loader = _LOADERS.get(item_type)
        if not loader or not item_id:
            self._send_json({"error": "bad item_type/item_id"}, 400); return
        block, ctx = loader(item_id, agent_id)
        if block is None:
            self._send_json({"error": ctx}, 404); return
        user = self._share_user()
        legacy_open = item_type in ("chat", "project_chat", "workflow")
        if not _auth_mod.can_access(user, block, legacy_open=legacy_open):
            self._send_json({"error": "not permitted"}, 403); return
        can_mng = _auth_mod.can_manage(user, block)
        # Hydrate user display names for the ACL summary.
        def _names(ids):
            out = []
            for uid in ids or []:
                u = _auth_mod.AuthDB.get_user(uid)
                out.append({"id": uid, "display_name": (u or {}).get("display_name") or uid})
            return out
        owner = block.get("owner_user_id") or ""
        owner_u = _auth_mod.AuthDB.get_user(owner) if owner else None
        team = _auth_mod.AuthDB.get_team(block.get("owner_team_id")) if block.get("owner_team_id") else None
        self._send_json({
            "item_type": item_type, "item_id": item_id, "agent_id": agent_id,
            "owner_user_id": owner,
            "owner_display_name": (owner_u or {}).get("display_name") or owner,
            "visibility": block.get("visibility"),
            "owner_team_id": block.get("owner_team_id") or "",
            "owner_team_name": (team or {}).get("name") or "",
            "extra_members": _names(block.get("extra_member_user_ids")),
            "excluded": _names(block.get("excluded_user_ids")),
            "caller_can_manage": can_mng,
            "transferable": item_type in TRANSFERABLE,
        })

    # ── POST /v1/share (update ACL) ──────────────────────────────────
    def _handle_share_update(self):
        body = self._read_json() or {}
        item_type = (body.get("item_type") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        agent_id = (body.get("agent_id") or "").strip()
        if item_type == "artifact":
            return self._handle_share_artifact_update(body)
        loader = _LOADERS.get(item_type); saver = _SAVERS.get(item_type)
        if not loader or not item_id:
            self._send_json({"error": "bad item_type/item_id"}, 400); return
        cur, ctx = loader(item_id, agent_id)
        if cur is None:
            self._send_json({"error": ctx}, 404); return
        user = self._share_user()
        # Legacy owner-less chats/workflows: a non-admin manage attempt claims
        # ownership (with a flag in the response so the UI can surface it).
        claimed = False
        if not cur.get("owner_user_id"):
            if item_type in ("chat", "project_chat", "workflow"):
                # only if the caller could read it
                if not _auth_mod.can_access(user, cur, legacy_open=True):
                    self._send_json({"error": "not permitted"}, 403); return
                if user["id"] != "__system__":
                    cur["owner_user_id"] = user["id"]
                    if item_type in ("chat", "project_chat"):
                        _db.ChatDB.update_session_share(item_id, owner_user_id=user["id"])
                    else:
                        engine.WorkflowEngine.update_workflow_meta(ctx["agent_id"], ctx["name"], {"owner_user_id": user["id"]})
                    claimed = True
            else:
                # schedules: only admin may adopt
                if not (user["role"] == "admin" or user["id"] == "__system__"):
                    self._send_json({"error": "owner-less schedule — only an admin can adopt it"}, 403); return
        if not _auth_mod.can_manage(user, cur):
            self._send_json({"error": "only the owner (or admin) can change sharing"}, 403); return
        # Build the new block from the request, validate.
        vis = _auth_mod.normalize_visibility(body.get("visibility", cur.get("visibility")))
        if vis not in _auth_mod.VISIBILITY_VALUES:
            self._send_json({"error": f"invalid visibility '{vis}'"}, 400); return
        owner_team_id = (body.get("owner_team_id") or "").strip()
        if vis == "team":
            if not owner_team_id:
                self._send_json({"error": "owner_team_id required for team visibility"}, 400); return
            # caller must be a member of that team (admin bypass)
            if not (user["role"] == "admin" or user["id"] == "__system__"):
                my = {t["id"] for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
                if owner_team_id not in my:
                    self._send_json({"error": "you are not a member of that team"}, 403); return
        extras = [u for u in (body.get("extra_member_user_ids") or []) if _auth_mod.AuthDB.get_user(u)]
        excluded = [u for u in (body.get("excluded_user_ids") or []) if _auth_mod.AuthDB.get_user(u)]
        new_block = _auth_mod.normalize_share_block({
            "owner_user_id": cur.get("owner_user_id") or "",
            "visibility": vis,
            "owner_team_id": owner_team_id,
            "extra_member_user_ids": extras,
            "excluded_user_ids": excluded,
        })
        saver(item_id, ctx, new_block)
        # favourites cleanup: a narrowed item can orphan team/general pins.
        try:
            self._share_cleanup_favourites(item_type, item_id, agent_id, new_block)
        except Exception:
            pass
        _auth_mod.AuthDB.audit_write(user, "share_update",
                                     target=f"{item_type}:{item_id}",
                                     details={"visibility": vis, "owner_team_id": owner_team_id,
                                              "extra": extras, "excluded": excluded,
                                              "claimed_ownership": claimed})
        self._send_json({"status": "updated", **new_block, "claimed_ownership": claimed})

    # ── POST /v1/share/transfer ──────────────────────────────────────
    def _handle_share_transfer(self):
        body = self._read_json() or {}
        item_type = (body.get("item_type") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        agent_id = (body.get("agent_id") or "").strip()
        new_owner = (body.get("new_owner_user_id") or "").strip()
        if item_type not in TRANSFERABLE or not item_id:
            self._send_json({"error": "bad item_type/item_id"}, 400); return
        if not new_owner or not _auth_mod.AuthDB.get_user(new_owner):
            self._send_json({"error": "unknown new_owner_user_id"}, 400); return
        loader = _LOADERS.get(item_type); transferrer = _TRANSFERRERS.get(item_type)
        cur, ctx = loader(item_id, agent_id)
        if cur is None:
            self._send_json({"error": ctx}, 404); return
        user = self._share_user()
        if not _auth_mod.can_manage(user, cur):
            self._send_json({"error": "only the owner (or admin) can transfer"}, 403); return
        # For agent-scoped items, the new owner must have access to the agent.
        if agent_id and not _auth_mod.can_access_agent(_auth_mod.AuthDB.get_user(new_owner), agent_id):
            self._send_json({"error": "the new owner has no access to this agent"}, 400); return
        transferrer(item_id, ctx, new_owner)
        # Drop the new owner from extras / excluded.
        reload_block, ctx2 = loader(item_id, agent_id)
        if reload_block is not None:
            cleaned = _auth_mod.normalize_share_block({**reload_block, "owner_user_id": new_owner})
            _SAVERS.get(item_type)(item_id, ctx2, cleaned)
        _auth_mod.AuthDB.audit_write(user, "ownership_transfer",
                                     target=f"{item_type}:{item_id}",
                                     details={"old_owner": cur.get("owner_user_id") or "",
                                              "new_owner": new_owner})
        self._send_json({"status": "transferred", "new_owner_user_id": new_owner})

    # ── artifact arm (narrow-only override, no transfer) ─────────────
    def _handle_share_artifact_get(self, artifact_id: str):
        res = _db.ChatDB.get_artifact_with_parent_block(artifact_id) if hasattr(_db.ChatDB, "get_artifact_with_parent_block") else None
        if not res:
            self._send_json({"error": "artifact not found"}, 404); return
        parent_block, override, parent_label = res
        user = self._share_user()
        parent_vis = _auth_mod.normalize_visibility(parent_block.get("visibility"))
        eff = _auth_mod.normalize_visibility(override) if override else parent_vis
        if not _auth_mod.can_access(user, {**parent_block, "visibility": eff}, legacy_open=True):
            self._send_json({"error": "not permitted"}, 403); return
        self._send_json({
            "item_type": "artifact", "item_id": artifact_id,
            "parent_label": parent_label,
            "parent_visibility": parent_vis,
            "visibility_override": override or "",
            "effective_visibility": eff,
            "caller_can_manage": _auth_mod.can_manage(user, parent_block),
            "transferable": False,
        })

    def _handle_share_artifact_update(self, body: dict):
        artifact_id = (body.get("item_id") or "").strip()
        res = _db.ChatDB.get_artifact_with_parent_block(artifact_id) if hasattr(_db.ChatDB, "get_artifact_with_parent_block") else None
        if not res:
            self._send_json({"error": "artifact not found"}, 404); return
        parent_block, _override, _label = res
        user = self._share_user()
        if not _auth_mod.can_manage(user, parent_block):
            self._send_json({"error": "only the parent's owner (or admin) can restrict this artifact"}, 403); return
        override = _auth_mod.normalize_visibility(body.get("visibility_override", "")) if body.get("visibility_override") else ""
        if override:
            if override not in _auth_mod.VISIBILITY_VALUES:
                self._send_json({"error": "invalid visibility_override"}, 400); return
            # narrow-only: 'private' always allowed; otherwise must equal parent.
            if override != "private" and override != _auth_mod.normalize_visibility(parent_block.get("visibility")):
                self._send_json({"error": "an artifact override can only narrow (private) or match the parent"}, 400); return
        _db.ChatDB.set_artifact_visibility_override(artifact_id, override)
        _auth_mod.AuthDB.audit_write(user, "share_update", target=f"artifact:{artifact_id}",
                                     details={"visibility_override": override})
        self._send_json({"status": "updated", "visibility_override": override})

    # ── favourites cleanup helper ────────────────────────────────────
    def _share_cleanup_favourites(self, item_type, item_id, agent_id, block):
        from server_lib.favourites import FavouritesDB
        vis = block.get("visibility")
        # Which favourite scopes remain valid for this item?
        bad_scopes = []
        if vis != "global":
            bad_scopes.append("general")
        if vis != "team":
            bad_scopes.append("team")
        # 'user' scope is always fine for the owner.
        removed = 0
        for sc in bad_scopes:
            removed += FavouritesDB.remove_by_item_scope(item_type, item_id, agent_id, sc)
        return removed
