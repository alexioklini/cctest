# Extracted from server.py — auth and permission handlers
import json
import brain as engine
from server_lib import auth as _auth_mod


class AuthHandlerMixin:
    """Mixin for BrainAgentHandler providing auth/permission endpoints."""

    def _handle_auth_register(self):
        body = self._read_json()
        if not _auth_mod.registration_enabled():
            self._send_json({"error": "Registration is disabled"}, 403)
            return
        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            self._send_json({"error": "Username and password required"}, 400)
            return
        result = _auth_mod.AuthDB.create_user(
            username=username, password=password,
            display_name=body.get("display_name", username),
            email=body.get("email", ""),
        )
        if "error" in result:
            self._send_json(result, 409)
        else:
            token = _auth_mod.generate_token(result)
            self._send_json({"user": result, "token": token}, 201)

    def _handle_auth_login(self):
        body = self._read_json()
        uname = body.get("username", "")
        user = _auth_mod.AuthDB.authenticate(uname, body.get("password", ""))
        ip = self.client_address[0] if self.client_address else ""
        if not user:
            _auth_mod.AuthDB.audit_write(None, "auth.login_failed", target=uname, ip=ip)
            self._send_json({"error": "Invalid credentials"}, 401)
            return
        _auth_mod.AuthDB.update_last_login(user["id"])
        _auth_mod.AuthDB.audit_write(user, "auth.login", target=user["id"], ip=ip)
        token = _auth_mod.generate_token(user)
        self._send_json({"user": user, "token": token})

    def _handle_auth_refresh(self):
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json({"error": "Token required"}, 401)
            return
        new_token = _auth_mod.refresh_token(auth_header[7:])
        if not new_token:
            self._send_json({"error": "Invalid or expired token"}, 401)
            return
        self._send_json({"token": new_token})

    def _handle_auth_me(self):
        user = self._require_auth()
        if not user:
            return
        teams = _auth_mod.AuthDB.get_user_teams(user["id"]) if user["id"] != "__system__" else []
        self._send_json({"user": user, "teams": teams})

    def _handle_auth_password(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        result = _auth_mod.AuthDB.change_password(user["id"], body.get("old_password", ""), body.get("new_password", ""))
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json(result)

    def _handle_auth_profile(self):
        """Self-service profile edit: display_name, email. Role and disabled
        flags stay admin-only via _handle_auth_users_manage."""
        user = self._require_auth()
        if not user:
            return
        body = self._read_json() or {}
        updates = {k: body[k] for k in ("display_name", "email") if k in body}
        if not updates:
            self._send_json({"error": "No fields to update"}, 400)
            return
        result = _auth_mod.AuthDB.update_self_profile(user["id"], updates)
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json({"user": result})

    def _handle_auth_preferences(self):
        """Self-service preferences edit. POST merges values; only validated keys
        in PREFERENCE_KEYS are persisted."""
        user = self._require_auth()
        if not user:
            return
        body = self._read_json() or {}
        prefs = body.get("preferences") if isinstance(body.get("preferences"), dict) else body
        result = _auth_mod.AuthDB.update_preferences(user["id"], prefs or {})
        if "error" in result:
            self._send_json(result, 400)
        else:
            self._send_json({"user": result})

    # ── User profile document (the big "memory from chat history" md file) ─

    def _handle_auth_profile_doc_get(self):
        """GET /v1/auth/profile-doc — return the user's profile file content
        plus daemon-cursor metadata."""
        user = self._require_auth()
        if not user:
            return
        uid = user["id"]
        content = _read_user_profile(uid)
        try:
            cursor = _auth_mod.AuthDB.get_daily_summary_cursor(uid)
        except Exception:
            cursor = {"last_run_ts": 0, "last_status": "", "last_drawer_id": ""}
        prefs = user.get("preferences") or {}
        self._send_json({
            "content": content,
            "exists": bool(content),
            "bytes": len(content.encode("utf-8")) if content else 0,
            "cursor": cursor,
            "enabled": bool(prefs.get("daily_summary_enabled")),
            "hour_local": int(prefs.get("daily_summary_hour_local") or 6),
        })

    def _handle_auth_profile_doc_post(self):
        """POST /v1/auth/profile-doc — manual edit. Body: {content: "..."}.
        Cap at 32KB to keep one user from blowing up the filesystem; daemon-
        generated profiles are typically 2-4KB."""
        user = self._require_auth()
        if not user:
            return
        body = self._read_json() or {}
        content = body.get("content")
        if content is None:
            self._send_json({"error": "Missing content"}, 400)
            return
        if not isinstance(content, str):
            self._send_json({"error": "content must be a string"}, 400)
            return
        if len(content.encode("utf-8")) > 32 * 1024:
            self._send_json({"error": "Profile too large (max 32KB)"}, 400)
            return
        res = _write_user_profile_atomic(user["id"], content, source="manual")
        if res.get("error"):
            self._send_json(res, 500)
            return
        self._send_json({"status": "ok", "bytes": res.get("bytes"),
                          "prior_kept": res.get("prior_kept")})

    def _handle_auth_profile_doc_update_now(self):
        """POST /v1/auth/profile-doc/update-now — kick off an immediate daemon
        run for the requesting user. Runs in the request thread (not async)
        so the UI can show the result. The actual LLM call is fire-and-wait
        with capped tokens; expect ~5-15s on cloud, ~30-60s on local."""
        user = self._require_auth()
        if not user:
            return
        # We need _user_profile_update_for_user, which lives inside main(). Run
        # the same logic inline by calling the storage helpers directly + the
        # generator helper exposed as engine state. Since the daemon worker
        # itself isn't reachable from here, replicate its body inline.
        uid = user["id"]
        now = time.time()
        try:
            cursor = _auth_mod.AuthDB.get_daily_summary_cursor(uid)
        except Exception:
            cursor = {"last_run_ts": 0}
        try:
            since_ts = float(cursor.get("last_run_ts") or 0)
        except (TypeError, ValueError):
            since_ts = 0
        # Run synchronously (capped tokens). Reuses the same helpers the
        # daemon does, so behavior is identical.
        try:
            res = _profile_run_synchronous(user, since_ts=since_ts, now=now)
        except Exception as e:
            print(f"[profile] update-now uid={uid} failed: {type(e).__name__}: {e}", flush=True)
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        # Refresh cursor for the caller
        try:
            cursor = _auth_mod.AuthDB.get_daily_summary_cursor(uid)
        except Exception:
            pass
        self._send_json({"result": res, "cursor": cursor})

    def _handle_auth_profile_doc_reset(self):
        """POST /v1/auth/profile-doc/reset — delete the profile file +
        MemPalace drawers. The next daemon run (or update-now) will rebuild
        from scratch from the last 90 days of chat samples."""
        user = self._require_auth()
        if not user:
            return
        uid = user["id"]
        res = _delete_user_profile(uid)
        # Reset cursor so the next daemon fire treats this as first-time.
        try:
            _auth_mod.AuthDB.set_daily_summary_cursor(uid, 0, "reset", "")
        except Exception:
            pass
        if res.get("error"):
            self._send_json(res, 500)
            return
        self._send_json({"status": "reset", "removed": res.get("removed", False)})

    def _handle_auth_users_list(self):
        user = self._require_role("admin")
        if not user:
            return
        self._send_json({"users": _auth_mod.AuthDB.list_users()})

    def _handle_auth_users_lookup(self):
        """GET /v1/auth/users/lookup — minimal user directory for any
        authenticated caller. Used by member-pickers (projects, teams).
        Returns id, username, display_name only — no role/email/disabled."""
        user = self._require_auth()
        if not user:
            return
        rows = _auth_mod.AuthDB.list_users()
        out = [{"id": u["id"], "username": u.get("username", ""),
                "display_name": u.get("display_name", "")}
               for u in rows if not u.get("disabled")]
        self._send_json({"users": out})

    def _handle_auth_users_manage(self):
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "create")
        ip = self.client_address[0] if self.client_address else ""
        if action == "create":
            result = _auth_mod.AuthDB.create_user(
                username=body.get("username", ""),
                password=body.get("password", ""),
                role=body.get("role", "user"),
                display_name=body.get("display_name", ""),
                email=body.get("email", ""),
            )
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.create", target=result.get("id",""),
                                             details={"username": result.get("username"), "role": result.get("role")}, ip=ip)
            status = 409 if "error" in result else 201
            self._send_json(result, status)
        elif action == "update":
            uid = body.get("user_id", "")
            updates = body.get("updates", {})
            result = _auth_mod.AuthDB.update_user(uid, updates)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.update", target=uid,
                                             details={"updates": updates}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "delete":
            uid = body.get("user_id", "")
            if uid == user["id"]:
                self._send_json({"error": "Cannot delete your own account"}, 400)
                return
            _auth_mod.AuthDB.delete_user(uid)
            _auth_mod.AuthDB.audit_write(user, "user.delete", target=uid, ip=ip)
            self._send_json({"ok": True})
        elif action == "reset_password":
            uid = body.get("user_id", "")
            new_pw = body.get("new_password", "")
            result = _auth_mod.AuthDB.admin_reset_password(uid, new_pw)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.reset_password", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "disable":
            uid = body.get("user_id", "")
            if uid == user["id"]:
                self._send_json({"error": "Cannot disable your own account"}, 400)
                return
            result = _auth_mod.AuthDB.update_user(uid, {"disabled": 1})
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.disable", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "enable":
            uid = body.get("user_id", "")
            result = _auth_mod.AuthDB.update_user(uid, {"disabled": 0})
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "user.enable", target=uid, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_auth_migrate(self):
        """Assign unowned sessions/artifacts to a user."""
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        target_uid = body.get("user_id", "")
        if not target_uid or not _auth_mod.AuthDB.get_user(target_uid):
            self._send_json({"error": "Valid user_id required"}, 400)
            return
        with _db_conn() as conn:
            c1 = conn.execute("UPDATE sessions SET user_id = ? WHERE user_id = '' OR user_id IS NULL", (target_uid,))
            c2 = conn.execute("UPDATE artifacts SET user_id = ? WHERE user_id = '' OR user_id IS NULL", (target_uid,))
            conn.commit()
            self._send_json({"sessions_updated": c1.rowcount, "artifacts_updated": c2.rowcount})

    def _handle_auth_audit_list(self):
        """GET /v1/auth/audit?limit=200&actor=X&action=Y&since=TS — admin-only."""
        user = self._require_role("admin")
        if not user:
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        try:
            limit = int(params.get("limit", "200"))
        except ValueError:
            limit = 200
        try:
            since = float(params.get("since", "0"))
        except ValueError:
            since = 0.0
        rows = _auth_mod.AuthDB.audit_read(
            limit=min(max(limit, 1), 2000),
            actor=unquote(params.get("actor", "")),
            action=unquote(params.get("action", "")),
            since_ts=since,
        )
        self._send_json({"events": rows, "count": len(rows)})

    # --- Agent / Model ACL Handlers ---

    def _handle_auth_permissions_get(self):
        """GET /v1/auth/permissions?user_id=X — list a user's grants (admin only).
        Without ?user_id returns the caller's own effective grants."""
        user = self._require_auth()
        if not user:
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        target_uid = unquote(params.get("user_id", "")) or user["id"]
        team_id = unquote(params.get("team_id", ""))
        if team_id:
            # Only admin or team head
            if user["role"] != "admin" and user["id"] != "__system__" \
                    and not _auth_mod.can_manage_team(user, team_id):
                self._send_json({"error": "Not authorized"}, 403)
                return
            self._send_json({"team_id": team_id, "grants": _auth_mod.AuthDB.list_team_grants(team_id)})
            return
        if target_uid != user["id"] and user["role"] != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Admin access required"}, 403)
            return
        grants = _auth_mod.AuthDB.list_user_grants(target_uid)
        # Also expose the merged effective set for convenience
        effective_agents = sorted(_auth_mod.AuthDB.get_user_allowed_agents(target_uid))
        effective_models = sorted(_auth_mod.AuthDB.get_user_allowed_models(target_uid))
        self._send_json({
            "user_id": target_uid,
            "grants": grants,
            "effective": {"agents": effective_agents, "models": effective_models},
        })

    def _handle_auth_permissions_manage(self):
        """POST /v1/auth/permissions — admin-only grant/revoke.
        Body: {action: grant|revoke, kind: agent|model, user_id?|team_id?, agent_id?|model_id?}"""
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "")
        kind = body.get("kind", "")
        user_id = body.get("user_id", "")
        team_id = body.get("team_id", "")
        aid = body.get("agent_id", "")
        mid = body.get("model_id", "")
        if kind == "agent":
            if action == "grant":
                result = _auth_mod.AuthDB.grant_agent(user_id=user_id, team_id=team_id, agent_id=aid)
            elif action == "revoke":
                result = _auth_mod.AuthDB.revoke_agent(user_id=user_id, team_id=team_id, agent_id=aid)
            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400); return
        elif kind == "model":
            if action == "grant":
                result = _auth_mod.AuthDB.grant_model(user_id=user_id, team_id=team_id, model_id=mid)
            elif action == "revoke":
                result = _auth_mod.AuthDB.revoke_model(user_id=user_id, team_id=team_id, model_id=mid)
            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400); return
        else:
            self._send_json({"error": f"Unknown kind: {kind} (expected 'agent' or 'model')"}, 400); return
        status = 400 if "error" in result else 200
        if "error" not in result:
            ip = self.client_address[0] if self.client_address else ""
            target = user_id or team_id
            _auth_mod.AuthDB.audit_write(user, f"permission.{action}.{kind}", target=target,
                                          details={"subject": {"user_id": user_id, "team_id": team_id},
                                                   "object": aid if kind == "agent" else mid}, ip=ip)
        self._send_json(result, status)

    # --- User Team Endpoint Handlers ---

    def _handle_user_teams_list(self):
        user = self._require_auth()
        if not user:
            return
        if user["role"] == "admin" or user["id"] == "__system__":
            teams = _auth_mod.AuthDB.list_teams()
        else:
            teams = _auth_mod.AuthDB.get_user_teams(user["id"])
        self._send_json({"teams": teams})

    def _handle_user_teams_manage(self):
        user = self._require_role("poweruser", "admin")
        if not user:
            return
        body = self._read_json()
        action = body.get("action", "")
        ip = self.client_address[0] if self.client_address else ""
        if action == "create":
            result = _auth_mod.AuthDB.create_team(
                name=body.get("name", ""),
                head_user_id=body.get("head_user_id", user["id"]),
                description=body.get("description", ""),
            )
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.create", target=result.get("id",""),
                                             details={"name": result.get("name"),
                                                      "head_user_id": result.get("head_user_id")}, ip=ip)
            status = 400 if "error" in result else 201
            self._send_json(result, status)
        elif action == "update":
            tid = body.get("team_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            updates = body.get("updates", {})
            result = _auth_mod.AuthDB.update_team(tid, updates)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.update", target=tid, details={"updates": updates}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "add_member":
            tid = body.get("team_id", "")
            muid = body.get("user_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            result = _auth_mod.AuthDB.add_team_member(tid, muid)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.add_member", target=tid, details={"member": muid}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "remove_member":
            tid = body.get("team_id", "")
            muid = body.get("user_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            result = _auth_mod.AuthDB.remove_team_member(tid, muid)
            if "error" not in result:
                _auth_mod.AuthDB.audit_write(user, "team.remove_member", target=tid, details={"member": muid}, ip=ip)
            status = 400 if "error" in result else 200
            self._send_json(result, status)
        elif action == "dissolve":
            tid = body.get("team_id", "")
            if not _auth_mod.can_manage_team(user, tid):
                self._send_json({"error": "Not authorized to manage this team"}, 403)
                return
            _auth_mod.AuthDB.delete_team(tid)
            _auth_mod.AuthDB.audit_write(user, "team.dissolve", target=tid, ip=ip)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Path-based admin check helpers ---

    # Paths that don't require auth
    _PUBLIC_GET_PATHS = {"/v1/status", "/v1/auth/me"}
    _PUBLIC_POST_PATHS = {"/v1/auth/login", "/v1/auth/refresh"}

    # Admin-only paths. Entries ending in "/" match as prefix (any subpath is
    # admin-only). Exact paths match exactly. These gate config mutations —
    # only admin can edit server/agent configuration. Per-user resources
    # (sessions, messages, projects, notes, artifacts) are gated separately
    # by ownership/ACL helpers, not by this whitelist.
    _ADMIN_GET_PATHS = {
        "/v1/auth/users",
        # Server & agent config reads that can leak secrets or resource counts
        "/v1/providers",
        # NOTE: /v1/models/config is intentionally NOT here. Non-admin users
        # need to read the model list to populate the chat model dropdown;
        # the handler filters to allowed_models + strips sensitive fields.
        "/v1/mempalace/classifier",
        "/v1/mempalace/stats",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/services",
        "/v1/channels",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        # Prefixes (trailing slash)
        "/v1/services/log",
        "/v1/traces/",
        "/v1/agents/",  # HOOKS / commands detail — read covered below; see GET filter
    }
    # Actual enforced GET admin prefixes — narrower than above; handled by
    # _is_admin_path() below to keep non-config agent reads (projects, files,
    # activity) open to non-admins. Kept tight.
    _ADMIN_GET_PREFIXES = (
        "/v1/traces/",
    )
    _ADMIN_GET_EXACT = {
        "/v1/auth/users",
        "/v1/auth/audit",
        # Note: /v1/auth/permissions is NOT admin-only at the gate —
        # the handler itself allows non-admins to read their own grants
        # but rejects cross-user lookups. See _handle_auth_permissions_get.
        "/v1/providers",
        # /v1/models/config is open to non-admins; handler filters by
        # per-user allowed_models and strips sensitive fields.
        "/v1/mempalace/classifier",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        "/v1/services",
        "/v1/quotas/config",
        "/v1/quotas/admin/users",
    }

    _ADMIN_POST_EXACT = {
        # Auth admin surface (already had these)
        "/v1/auth/users",
        "/v1/auth/migrate",
        "/v1/auth/permissions",
        "/v1/restart",
        # Server-level config
        "/v1/providers",
        "/v1/providers/test",
        "/v1/models/config",
        "/v1/services/server",
        "/v1/services/telegram",
        "/v1/settings/commands",
        "/v1/mempalace/classifier",
        "/v1/warmup/trigger",
        "/v1/backup",
        "/v1/restore",
        "/v1/mcp/connect",
        "/v1/mcp/disconnect",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/cache/clear",
        "/v1/channels",
        # Agent-level config
        "/v1/agents/create",
        "/v1/agents/delete",
        "/v1/agents/rename",
        # Skills install/remove
        "/v1/skills/install-zip",
        "/v1/skills/remove",
        "/v1/skills/claude-code",
        "/v1/skills/claude-code/install",
        # Command tooling that writes to agent config
        "/v1/commands/expand",
        # NOTE: /v1/refine is intentionally NOT here — it's a stateless
        # one-shot LLM call that returns refined text and writes nothing.
        # Used by every authenticated user for: composer chat-prompt
        # rewriting, profile-field polishing (job description, comm prefs,
        # long-form profile), and scheduled-task prompt refinement. Quota
        # gating in send_message handles cost; auth gate covers identity.
        # Nodes management (remote workers config)
        "/v1/nodes",
        # Client-hosted local model manifest (admin CRUD). Manifest reads and
        # weight downloads stay open to any authenticated user so non-admin
        # desktop clients can still pull models the admin has blessed.
        "/v1/client/models",
    }
    _ADMIN_POST_PATHS = _ADMIN_POST_EXACT  # backwards compat
    _ADMIN_POST_PREFIXES = (
        # Agent config: file writes, hooks, commands, workflows, soul-chat
        # Matches /v1/agents/<name>/{file,hooks,commands,workflows,...}
        # NOTE: project subpaths and /v1/agents/switch are excluded below.
        # We enforce via _is_admin_path() for fine-grained control.
        "/v1/channels/",
    )

    # Agent-level POST subpaths that are admin-only. Checked against the
    # portion after /v1/agents/<name>/.
    _ADMIN_AGENT_POST_SUBPATHS = (
        "file", "files", "hooks", "commands", "workflows", "soul-chat",
    )

    # Admin-only DELETE (agent workflow/ingest mutations). Regular users
    # can still delete their OWN sessions/projects/notes — those are
    # ownership-checked by their handlers.
    _ADMIN_DELETE_AGENT_SUBPATHS = (
        "workflows/", "ingested/",
    )

    def _is_admin_get(self, path: str) -> bool:
        if path in self._ADMIN_GET_EXACT:
            return True
        for p in self._ADMIN_GET_PREFIXES:
            if path.startswith(p):
                return True
        return False

    def _is_admin_post(self, path: str) -> bool:
        if path in self._ADMIN_POST_EXACT:
            return True
        for p in self._ADMIN_POST_PREFIXES:
            if path.startswith(p):
                return True
        # Agent subpath config mutations: /v1/agents/<name>/{hooks,file,...}
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2 and parts[0] not in ("create", "delete", "rename", "switch", "activity"):
                sub = parts[1]
                # Allow project/ingest/notes/docs subpaths (user-facing, ACL-gated elsewhere)
                if sub.startswith("projects") or sub == "ingest" or sub.startswith("ingested"):
                    return False
                for allowed in self._ADMIN_AGENT_POST_SUBPATHS:
                    if sub == allowed or sub.startswith(allowed + "/"):
                        return True
        return False

    def _is_admin_delete(self, path: str) -> bool:
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sub = parts[1]
                for prefix in self._ADMIN_DELETE_AGENT_SUBPATHS:
                    if sub.startswith(prefix):
                        return True
        return False

    def _auth_gate(self, path: str, public_paths: set, admin_paths: set, method: str = "GET") -> dict | None:
        """Check auth for API paths. Returns user dict or None (response already sent).

        `admin_paths` is kept for backwards-compat with exact-match checks;
        prefix and subpath checks are delegated to _is_admin_{get,post,delete}.
        """
        if path in public_paths:
            return _auth_mod.SYNTHETIC_ADMIN if not _auth_mod.auth_enabled() else (self._get_auth_user() or _auth_mod.SYNTHETIC_ADMIN)
        user = self._require_auth()
        if not user:
            return None
        # Method-specific admin check
        is_admin_required = False
        if method == "GET":
            is_admin_required = self._is_admin_get(path)
        elif method == "POST":
            is_admin_required = self._is_admin_post(path)
        elif method == "DELETE":
            is_admin_required = self._is_admin_delete(path)
        # Legacy exact-match fallthrough
        if not is_admin_required and path in admin_paths:
            is_admin_required = True
        if is_admin_required and user["role"] != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Admin access required"}, 403)
            return None
        # Capability gate (projects, etc.) — admin bypasses via has_capability
        needed_cap = self._path_requires_capability(method, path)
        if needed_cap and not _auth_mod.has_capability(user, needed_cap):
            self._send_json({"error": f"Capability '{needed_cap}' not granted"}, 403)
            return None
        return user
