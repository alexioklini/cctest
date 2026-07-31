---
name: Brain admin login for autonomous testing
description: Admin credentials for the local Brain server's auth gate, used when running test plans (e.g. docs/kg-test-plan.md) autonomously via Chrome or HTTP.
type: reference
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
Local Brain server (http://localhost:8420) admin login:
- username: `admin`
- password: `admin`

Used for autonomous test runs that need:
- Browser sessions through `mcp__claude-in-chrome__*` (login flow at /web/)
- Admin-only HTTP endpoints (POST /v1/mempalace/kg/reextract, POST /v1/services/server, etc.) — log in once, get a JWT, cache for the session

This is dev/local credentials only; do NOT treat them as production secrets. The user explicitly authorised using them for testing in this conversation. If anything ever changes, the user will say so explicitly.

For Chrome-driven test runs, the typical flow is:
1. `mcp__claude-in-chrome__tabs_context_mcp` to find or create a tab
2. `mcp__claude-in-chrome__navigate` to http://localhost:8420
3. Fill the login form via `form_input`, submit
4. Drive the test plan from the project panel + Settings → Knowledge Graph

Don't dump the token into chat output unless the user asks for it.
