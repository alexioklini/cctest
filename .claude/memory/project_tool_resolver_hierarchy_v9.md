---
name: tool-resolver-hierarchy-global-agent-override-purpose-v9-0-x
description: Resolver layers for tool enabled/deferred + purpose filter. Replaces legacy per-agent tool_groups/extra_tools/deferred_tool_groups. Scheduled tasks bypass agent overrides.
metadata: 
  node_type: memory
  type: project
  originSessionId: 35d3fa9f-2086-4b4d-ab4a-69a77de4c073
---

**Resolution per tool, per LLM call**:

```
1. Global       (config.json → tool_settings.<name>)
   - enabled  : bool, default True
   - deferred : bool, default False
   - purposes : list[str], default [] = all purposes

2. Agent override (agent.json → token_config.tool_overrides.<name>)
   - {enabled?: bool, deferred?: bool}
   - Field present = override; absent = inherit
   - NO purposes override — purpose is call-level, not agent-level

3. Purpose filter (global.purposes intersected with call.purpose)
   - Empty/missing global purposes = no filter
```

**Scheduled tasks follow the same hierarchy** — no bypass. The task's
owning agent supplies layer 2. The call's purpose is decided by the
task's `tool_profile` field or `_memory_summary_*` name prefix.

**Helpers in brain.py**:
- `resolve_tool_enabled(name, agent_id)` — global → agent override
- `resolve_tool_deferred(name, agent_id)` — same
- `tool_passes_purpose(name, purpose)` — global purpose filter only
- `_agent_tool_override(agent_id, name)` — fast/disk fallback (thread-local current_agent OR agent.json read)

**Resolver call sites**:
- `_get_agent_tool_names(agent_id)` — applies enabled (layers 1+2)
- `resolve_active_tools(purpose, agent_id, ...)` — applies all 3 layers + MCP merge

**Endpoints**:
- `GET /v1/tools/settings` returns full registry + canonical purposes list
- `POST /v1/tools/settings` validates name + applies_with + bools + purposes
- `GET /v1/tools/breakdown?agent=<id>` per-tool token decomposition

**UI**:
- General Settings → Tools: full registry, all 63 tools grouped, per-tool
  enabled/deferred/purposes/prose/applies_with editor + cost header with
  per-tool Nt badges
- Per-agent → Tokens: tristate override matrix (inherit / force on / force off)
  per tool, no group-level controls anymore. Compact threshold + scheduled-tasks
  toggle survive.

**Migration** (one-shot at startup, idempotent):
- `seed_tool_settings_purposes()` — fills empty purposes lists from current behavior
- `migrate_agent_tool_overrides(agent_id)` — translates legacy tool_groups +
  extra_tools + deferred_tool_groups into per-tool overrides. Persists agent.json.
  Skips agents that already have tool_overrides set.

**Don't**:
- Re-add tool_groups / extra_tools / deferred_tool_groups / include_tools_guide / scheduled_task_tools to TOKEN_CONFIG_DEFAULTS — all deprecated, stripped from agent.json on save
- Add bypass paths for scheduled tasks — they go through the same hierarchy as every other LLM call
- Add a `purposes` field to agent overrides — purpose is a property of the call
