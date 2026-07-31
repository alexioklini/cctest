---
name: RBAC rollout complete (2026-04-22)
description: All 10 steps of the Brain multi-user/RBAC plan shipped across Apr 22 — auth + config lockdown + ACLs + team wings + audit log all live
type: project
originSessionId: 58566bb4-dc63-420f-b993-003d7a0654ee
---
Completed the entire 10-step RBAC rollout on 2026-04-22. Shipping state (all enforced, all smoke-tested):

**1. Config-mutation lockdown** — ~30 endpoints admin-only via `_auth_gate` + `_is_admin_get/post/delete` predicates: providers, models/config, agents CRUD + hooks/file/commands/workflows, skills install/remove, services/server, mempalace classifier, warmup trigger, execution mode, backup/restore, tools/config. DELETE on agent workflows/ingested too.

**2. Self-registration disabled** — `auth.registration_enabled` default `false`, `/v1/auth/register` returns 403, Register tab + `authRegister`/`showAuth*` JS removed from web UI.

**3. Admin lifecycle actions** — `AuthDB.admin_reset_password()` + new `POST /v1/auth/users` actions `reset_password`/`disable`/`enable`. Self-delete/self-disable blocked.

**4. Admin GUI** — `openUserManagement()` modal (status dots, reset-pw, enable/disable, delete, role select, add form with display_name) + `openUserTeams()` modal (per-team add-member UI, admin head-picker). Two modals cross-link.

**5. Agent/model ACLs** — Tables `{user,team}_agent_permissions`, `{user,team}_model_permissions` in `auth.db`. Default-allow `main` on user create + one-time backfill for pre-ACL users. `GET|POST /v1/auth/permissions`. `can_access_agent`/`can_access_model` helpers. Filters applied to `list_agents`/`list_models`, gates applied to `POST /v1/sessions`, `POST /v1/agents/switch`, `POST /v1/chat` (model override). Admins bypass. Per-user "Permissions" gear in user modal opens grants panel with checkbox toggles + via-team chips.

**6. Capability flags** — `capabilities` JSON column on users (with ALTER migration). Role defaults: `user=chat+artifacts`, `poweruser=+projects`, `admin=all`. Enforced via `_path_requires_capability`: `allow_projects` currently gates `/v1/agents/*/projects*`, `/ingest*`, `/ingested*`. Admin bypasses. Merged caps exposed via `/v1/auth/me`. UI toggle row in Permissions modal.

**7. Project visibility** — `ProjectManager.create_project` already persisted `visibility`/`owner_user_id`/`owner_team_id`; added them to `update_project` allowed keys too. New helper `_project_access_check()` enforces `can_access_project` on GET/PUT/DELETE + notes/docs/ingest. Non-admins silently stripped from visibility/ownership fields in updates. Create-project modal gained visibility picker (private/team/global) with team selector populated from `/v1/auth/me` teams.

**8. Session team-scoping** — ALTER sessions to add `team_id` + `visibility` cols. `list_sessions` accepts `visible_team_ids` and ORs in team-visibility. New `_session_access_check()` helper enforces on `/messages`, `/inspect`, `/files`, `/next-prompt`, `POST /v1/chat`, `/v1/chat/answer`, `/v1/chat/cancel`, `/v1/sessions/manage`, `/v1/context/compact`, DELETE. New action `set_visibility` on `/v1/sessions/manage` (team members only, admin bypass). Session search results post-filtered.

**9. Team MemPalace wings** — `_resolve_session_wing()` returns `{team_id}--{agent_id}` for team sessions, `{user_id}--{agent_id}` for user-owned, bare `{agent_id}` for legacy. Used in all 3 chat-sync write sites (background daemon, immediate-sync on toggle, manual memorize_turns). `mempalace_query` post-filter now includes user's team wings in addition to user's own wings + shared wings. `current_team_ids` thread-local propagates to delegate threads.

**10. Audit log** — `audit_log` table (append-only) + `AuthDB.audit_write()` (fail-silently) + `AuthDB.audit_read()`. Instrumented: login success/failure, user.create/update/delete/reset_password/disable/enable, permission.grant/revoke.{agent,model}, team.create/update/add_member/remove_member/dissolve. `GET /v1/auth/audit?limit&actor&action&since` admin-only.

**Files touched:** `server.py` (main routing, handlers, wing logic), `auth.py` (AuthDB tables + helpers + audit), `claude_cli.py` (MemPalace query team-wing filter, thread-local propagation, `ProjectManager.update_project` visibility keys), `web/index.html` (admin modals, permissions panel with caps toggle, create-project visibility picker, Register removal), `config.json` (`registration_enabled: false`).

**Key hidden invariants (easy to break later):**
- Default-allow of `main` for new users is seeded in `create_user` AND backfilled on `AuthDB.init` for pre-ACL users. Admins don't get rows because they bypass.
- `_resolve_session_wing` is the single source of truth for wing naming — all three chat-sync sites use it. Breaking it silently splits session memory between wings.
- `_ADMIN_POST_EXACT` + `_is_admin_post` is the config-mutation lock. New config endpoints MUST be added to one of these, not just guarded ad-hoc inside the handler.
- `capabilities` column is JSON text; `_user_dict` merges overrides with role defaults. New capability keys need to be added to `CAPABILITY_KEYS` AND given a default in `CAPABILITY_DEFAULTS` for each role.
- Self-delete/self-disable guards in `_handle_auth_users_manage` are the only thing stopping the sole admin from locking themselves out.

**Not done (explicit out-of-scope for MVP):**
- Account lockout after N failed logins, 2FA, email-based password reset links, session (token) revocation server-side.
- Team-level cost quotas.
- Per-user preferences table (theme, default model within grants, default memory mode).
- Audit log UI surface (data is there but no admin GUI yet beyond curl).
- Retroactive wing migration when a session's visibility flips — only new turns go to the new wing; old drawers stay in the original wing.
