---
name: project_memdash_integration_plan
description: "BUILT (v9.44.0, 2026-05-28): the third-party MemPalace Dashboard is folded INTO brain-agent — served by Brain at /memdash/, no separate port. Phase 1 done."
metadata: 
  node_type: memory
  type: project
  originSessionId: df4a3267-b48f-4c45-866c-8b0ac73cdafd
---

2026-05-28: User wants the MemPalace Dashboard (github.com/epinethrone/mempalace-frontend — a memory browser/curator UI) usable from brain-agent. **Decided AGAINST** running it as its own server (port 8765 + supervisor + reverse-proxy) — the dashboard hardcodes absolute URLs and would need a proxy + its own login + a separate Cloudflare tunnel. **Chosen approach: integrate INTO Brain** — Brain serves the dashboard's static frontend under an admin-gated `/memdash/` route and reimplements its `/api/*` calls as Brain handlers backed by Brain's IN-PROCESS MemPalace. No second port; remote access rides Brain's existing Cloudflare tunnel; gated by Brain admin RBAC; dashboard's own auth dropped.

**Phase 1 (approved, to build): reuse their frontend as-is.** Phase 2 (deferred, "if it makes sense"): rebuild as native Brain UI.

Full step-by-step plan: **`~/.claude/plans/typed-scribbling-scone.md`** (read it first — has the endpoint→tool map, file list, verification).

Key verified facts baked into the plan:
- 24/25 dashboard `mempalace.mcp_server.tool_*` functions are importable in Brain's venv (`~/.mempalace/venv/bin/python3`) → Brain calls them in-process, no subprocess. Only `tool_traverse` name differs.
- Brain serves its SPA via `_serve_static` (handlers/admin.py:40, routed from server.py:1698) — extend for `/memdash/`.
- Admin gating via the `_is_admin_get`/`_ADMIN_GET_PREFIXES` whitelist (server.py ~:1170), same mechanism used for /v1 admin routes.
- **#1 correctness risk**: the tool_* fns default to palace `~/.mempalace/palace/`, but Brain's palace is **`~/.mempalace/brain/`** (KG with span column = `~/.mempalace/brain/knowledge_graph.sqlite3`, NOT the stray `~/.mempalace/knowledge_graph.sqlite3`). Handlers must bind tools to the brain palace — reuse `engine/mempalace_glue._load_mempalace_config()` palace_path resolution. Verify via: `/memdash/api/palace` counts MUST match `/v1/mempalace/stats`.
- ~40 endpoints; drafts/versions are file-backed dashboard-only → STUB in Phase 1. Auth endpoints dropped (`/api/session` returns synthetic authed).
- TODO at impl: confirm the dashboard LICENSE permits vendoring its frontend.

Concurrent-write risk to the live palace ACCEPTED by user ([[project_chroma_bulk_delete_corruption]]). Frontend prefix-patch is re-apply-on-update discipline ([[project_mempalace_venv_patches]]). Related: [[project_summary]].

## SHIPPED 2026-05-28 (v9.44.0) — Phase 1 complete
- **`handlers/memdash.py`** (new mixin): reimplements `/memdash/api/*` over IN-PROCESS `mempalace.mcp_server.tool_*`. Read paths (`/palace`,`/search`,`/system`,`/export`) read the palace+KG sqlite DBs directly (mirror the dashboard's SQL); Lab+write paths call the tools in-process. Palace binding: `setdefault MEMPALACE_PALACE_PATH` to the brain palace (same as the chat-sync daemon) — the #1 risk, VERIFIED resolved (`/memdash/api/palace` counts == `/v1/mempalace/stats`: 13449 drawers/5 wings, palace=~/.mempalace/brain).
- **`web/memdash/*`** (vendored frontend, MIT, committed): patched in `app.js` (marked `BRAIN-PATCH`, re-apply on dashboard upgrade) — `/api/*`→`/memdash/api/*` prefix, auth via Brain's `localStorage['auth-token']`→`Authorization: Bearer` (X-Auth-Token dropped), 401/403+logout→redirect to Brain `/`. index.html asset paths rewritten to `/memdash/` prefix.
- **`server.py`**: MemDashHandlerMixin added to BrainAgentHandler bases + injection list; do_GET/do_POST dispatch `/memdash/` (static, no auth — like Brain's SPA at `/`) and `/memdash/api/*` (admin-gated via new `_memdash_admin_gate`). `handlers/admin.py:_serve_static` maps `/memdash[/]`→`web/memdash/index.html`.
- **Settings → MemPalace → "🗄️ MemPalace Dashboard öffnen"** (inline `window.open`, no new JS global → js_gate net-globals unchanged 1013).
- **Phase 1 stubs**: drafts disabled; version-undo log file-backed at `agents/main/memdash/versions.jsonl`; login/logout/credentials/session replaced by Brain auth (`/api/session`→synthetic-authed).
- Verified: static 200 no-auth, API 401 no-token, Lab reads live, write roundtrip (add→visible→delete→gone + version logged). LICENSE (MIT) permits vendoring; LICENSE kept at web/memdash/LICENSE.
- **Phase 2 (deferred, "if it makes sense")**: native Brain UI tab; port drafts/versions properly.
