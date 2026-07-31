---
name: Generic sharing/visibility model (v8.35.0)
description: One mechanism (private·users·team·global) for chats/projects/schedules/workflows/artifacts — what shipped, what's deferred
type: project
originSessionId: ee03e77a-3772-4069-bfc5-723a2af9e421
---
Shipped 2026-05-12 (commit b1511a4, v8.35.0) — implements the sharing-model-plan.html, phases P0–P5 (skipped P6 team-ingestion).

**Core idea**: every shareable item carries a five-field block `{owner_user_id, visibility, owner_team_id, extra_member_user_ids, excluded_user_ids}`. Creator = owner; only owner/admin changes visibility or transfers; transferable. `visibility ∈ {private, users, team, global}` — legacy `"user"` aliases to `"private"` on read.

**Where the logic lives**: `server_lib/auth.py` — `VISIBILITY_VALUES`, `normalize_visibility`, `can_access(user, block, legacy_open=)`, `can_manage(user, block)` (strictly owner-or-admin, NO team-head shortcut for items), `normalize_share_block` (drops grants/excludes the visibility makes moot; owner never excludable). `can_access_project`/`can_manage_project` are now thin and behaviour-preserving.

**Endpoints** (`handlers/share.py`, `ShareHandlerMixin` in server.py): `GET /v1/share?item_type=&item_id=&agent_id=` → block + `caller_can_manage`; `POST /v1/share` → update ACL; `POST /v1/share/transfer`. Per-item-type loaders/savers dispatch on `item_type` (chat/project_chat/project/schedule/workflow/artifact). Legacy owner-less chats/workflows: first non-admin `POST /v1/share` claims ownership (`claimed_ownership: true` in response). Schedules: only admin can adopt. Favourites cleanup on narrowing via new `FavouritesDB.remove_by_item_scope`.

**DB/storage changes**:
- `sessions`: + `extra_member_user_ids`/`excluded_user_ids` TEXT(JSON `[]`). `session_share_block(info)` maps `user_id`→owner. `ChatDB.update_session_share(**)`. `list_sessions` gained a `caller_user_id` param for the `users`/global post-filter + a `global` SQL arm.
- `schedules`: + `visibility`/`owner_team_id`/`extra_member_user_ids`/`excluded_user_ids`. `_schedule_get_row`/`_schedule_share_block`/`_schedule_update_share` module helpers in brain.py.
- `schedule_history`: + `visibility`/`owner_user_id`/`owner_team_id` snapshot cols, written at fire time by `Scheduler.begin_execution` (reads the parent row).
- `artifacts`: + `visibility_override` TEXT (empty = inherit parent). `ChatDB.get_artifact_with_parent_block(id)` → (parent_block, override, label); resolves `sched-<run>` synthetic sessions → schedule_history snapshot → live schedule. `ChatDB.set_artifact_visibility_override`. Narrow-only: `private` always OK, else must equal parent.
- Workflows: `<name>.flow.meta.json` sidecar next to the `.flow` source. `WorkflowEngine.get_workflow_meta`/`update_workflow_meta`/`workflow_block`. `list_workflows` filtered in `handlers/admin.py` via `can_access(workflow_block(meta), legacy_open=True)`.

**Web** (`web/js/share.js`): `shareDialog(itemType, itemId, agentId, opts)`, `shareTransferDialog` (type-to-confirm + memory-wing/quota warnings), `shareButton(...)`, `shareVisibilityPillHtml(...)`. `nav.js updatePageHeader` mounts the Share button + visibility pill beside the favourite star. CSS for `.share-*` + previously-missing `.fav-modal-foot`/`.fav-btn-primary`/`.fav-btn-sm`/`.fav-btn-danger` in `web/css/main.css`.

**Wing routing**: `_resolve_session_wing` unchanged — `private`/`users`/`global` all fall to `user__<owner>`, `team`→`team__<tid>`, project-scoped takes precedence. Transfer deliberately does NOT re-key existing MemPalace drawers (decided §10 — old owner already saw them; future drawers go to new owner). Billing follows the schedule's current owner automatically (the scheduled-run `ExecutionContext` reads `task_row.user_id`).

**Deferred (NOT done)** — future session:
- schedule-editor "Sharing" row UI; workflow-editor toolbar Share button (dialog reachable via header on chat/project, and via API for all types).
- `workflow_history` visibility snapshot cols (schedule_history has them; workflow doesn't yet).
- artifact list/download/version endpoints don't yet gate on `visibility_override` — they still inherit via parent-session access checks, which is the safe direction (override only narrows).
- Phase 6: team document-ingestion pipeline (`team_input_folders`, extend mempalace-project-sync to teams).
- Project right-pane keeps its old admin-only visibility selector; the new share dialog is additive (header button).
