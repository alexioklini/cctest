---
name: RBAC design decisions for Brain multi-user production
description: Locked-in design choices for Brain's multi-user/RBAC rollout — agent model, roles, default ACLs, registration policy
type: project
originSessionId: 58566bb4-dc63-420f-b993-003d7a0654ee
---
Gap analysis on 2026-04-22 produced a 10-step plan to take Brain from mostly-unguarded to production-ready multi-user. Key design decisions the user locked in:

**Agent model:** Agents are GLOBAL singletons, not user-owned. One `main`, one `research`, etc., shared across all users. No `owner_user_id` field on agents. Access controlled via grant-based ACL table (`user_agent_permissions`, `team_agent_permissions`) — no row = no access; admin sees all.

**Default ACL policy:** Default-allow on `main` agent for new users (configurable). All other agents + all models require explicit admin grant. New user without extra grants = chat-only with main.

**Roles:**
- `admin` — only role that can edit ANY config (server-level config, providers, models, agent.json, hooks, skills, mempalace, etc.)
- `poweruser` — team lead. Can create/manage their own teams, add/remove members. Cannot edit config.
- `user` — chat-only within their ACL grants + capability flags.

**No self-registration.** `/v1/auth/register` must be disabled/admin-gated, `auth.registration_enabled` defaults to `false`, Register tab removed from login overlay. Users only appear via admin action in the Users & Teams management GUI.

**Admin Management GUI is a first-class MVP requirement**, not a nice-to-have — Settings → "Users & Teams" tab with user CRUD, disable/enable, reset password, role change, per-user agent/model grants, per-user capability flags (`allow_projects`, `allow_artifacts`, `allow_workflows`, `allow_skills_install`), team CRUD, team membership.

**Why:** First production test environment needs defensible auth without overengineering. The auth scaffolding (users, roles, teams, JWT, bcrypt, `can_access_*` helpers in auth.py + server.py) was already ~70% built — the gap was enforcement on config-mutation + resource-read endpoints, not foundation.

**How to apply:** When implementing any RBAC-related change, follow the 10-step order (see conversation on 2026-04-22). Don't add per-agent ownership fields; extend grant tables instead. Don't re-enable self-registration. Don't give poweruser any config-mutation capability beyond team management.
