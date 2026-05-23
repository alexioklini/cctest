"""Cost and quota admin handlers.

Sub-mixin of AdminHandlerMixin (handlers/admin.py module-split refactor). Holds
ONLY this area's `_handle_*` methods (+ area-only private helpers).
AdminHandlerMixin inherits this class, so the combined BrainAgentHandler MRO is
unchanged.

Like admin.py, this module references `engine`, `brain`, `client`, `_db_conn`,
`sqlite3`, `subprocess`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals(). This module's name is in that function's
injection list. All other helpers (`_send_json`, `_read_json`,
`_parse_agent_from_path`, …) resolve via `self.` against the combined MRO.
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


class AdminCostsHandlers:
    """Cost and quota admin handlers."""

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
