# Feature Proposal: Hooks System (Three-Layer Hook Architecture)

**Status:** In Progress (P1)
**Author:** Brain Agent Team
**Date:** 2026-03-20 (revised 2026-03-23)
**Effort Estimate:** 5-6 days

---

## Problem Statement

Brain Agent controls tool behavior exclusively through prompt injection: `tools.md` and
`soul.md` are loaded into the system prompt to guide the LLM. This approach has a
fundamental weakness — the LLM can ignore, misinterpret, or creatively reinterpret
these instructions. There is no deterministic enforcement layer.

Additionally, a codebase analysis revealed **18 hook-like patterns** already scattered
across `claude_cli.py` and `server.py` — plan mode blocking, banned commands, output
truncation, audit logging, QMD reindexing, entity extraction, file tracking, cost
tracking, and more. These are hardcoded, non-configurable, and duplicated across
multiple call sites.

## Proposed Solution: Three-Layer Hook Architecture

### Layer 1: Tool-Level Hooks
```
LLM requests tool_call → PRE-HOOKS → tool executes → POST-HOOKS → result to LLM
```
- **Pre-hooks**: block/allow tool execution, validate arguments
- **Post-hooks**: transform output, log results, trigger notifications

### Layer 2: File-Write Hooks
```
write_file/edit_file completes → AFTER-FILE-WRITE pipeline:
  → QMD reindex (if .md in agent dir)
  → Entity extraction → knowledge graph update
  → File event emission (UI attachments)
  → External after_file_write scripts
```
Currently scattered across ~15 call sites. Centralized into one pipeline.

### Layer 3: LLM-Call Hooks
```
Before LLM API call → BEFORE-LLM (rate limit, round limit, budget check)
After LLM API call  → AFTER-LLM (cost tracking, usage recording)
```

---

## Existing Patterns Being Consolidated

### Built-in Hooks (engine-internal, always run)

| # | Pattern | Type | Location |
|---|---------|------|----------|
| 1 | Plan mode blocking | tool pre | `_execute_tool()` |
| 2 | Tool call deduplication | tool pre | `_check_tool_dedup()` |
| 3 | Tracing spans | tool pre+post | `_execute_tool()` |
| 4 | Audit logging | tool post | `_execute_tool()` |
| 5 | ANSI stripping | tool post | `_strip_ansi()` |
| 6 | File write tracking (events) | file post | `tool_write_file()` |
| 7 | QMD reindex | file post | `_maybe_qmd_reindex()` |
| 8 | Entity extraction + KG | file post | `MemoryStore.store()` |
| 9 | Rate limiting | LLM pre | `send_message()` |
| 10 | Cost tracking | LLM post | `_log_call_cost()` |
| 11 | Tool round limiting | LLM pre | `send_message()` |
| 12 | Audit status inference | tool post | `_execute_tool()` |

### Becoming User-Configurable External Hooks

| # | Pattern | Type | Replaces |
|---|---------|------|----------|
| 13 | Banned command patterns | tool pre | Hardcoded `banned_commands` in `tool_execute_command()` |
| 14 | Output size limits | tool post | Hardcoded 50KB/10KB truncation in 4+ tools |
| 15 | Workflow tool restriction | tool pre | Dead code (set but never enforced) |
| 16 | Custom blocking rules | tool pre | New capability |
| 17 | Webhook/Slack notifications | tool post | New capability |
| 18 | Approval gates | tool pre | New capability |

---

## Hook Types

| Type | Fires | Can Block? | Receives | Returns |
|------|-------|-----------|----------|---------|
| `pre` | Before tool executes | Yes | Tool name, args (JSON) | Exit 0 = allow, exit 1 = block |
| `post` | After tool executes | No | Tool name, args, result (JSON) | Exit 0 = pass-through, stdout = modified result |
| `after_file_write` | After write_file/edit_file | No | File path, action, agent | stdout ignored |

## Hook Execution Flow

```text
LLM decides to call tool
    │
    ▼
┌──────────────────────────────┐
│ BUILT-IN PRE-HOOKS           │
│  plan_mode_check()           │
│  dedup_check()               │
│  workflow_tool_check()       │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ EXTERNAL PRE-HOOKS           │
│  hook_1.sh (exit 0 → next)  │
│  hook_2.sh (exit 1 → BLOCK) │
└──────────────────────────────┘
    │ (all passed)
    ▼
┌──────────────────────────────┐
│ TRACING: start span          │
│ TOOL EXECUTES                │
│ TRACING: end span            │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ BUILT-IN POST-HOOKS          │
│  audit_log()                 │
│  status_inference()          │
│  after_file_write_pipeline() │
│    → QMD reindex             │
│    → entity extraction       │
│    → file event emission     │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ EXTERNAL POST-HOOKS          │
│  hook_3.sh (modify result)   │
│  hook_4.sh (pass-through)    │
└──────────────────────────────┘
    │
    ▼
Result returned to LLM
```

---

## Configuration

### agent.json with hooks section

```json
{
  "description": "Code assistant",
  "display_name": "Coder",
  "model": "claude-sonnet-4-6",
  "hooks": {
    "enabled": true,
    "timeout": 5000,
    "scripts": [
      {
        "name": "block-dangerous-commands",
        "type": "pre",
        "tools": ["execute_command"],
        "script": "hooks/block-dangerous.sh",
        "enabled": true
      },
      {
        "name": "truncate-large-output",
        "type": "post",
        "tools": ["execute_command", "web_fetch"],
        "script": "hooks/truncate-output.sh",
        "enabled": true
      },
      {
        "name": "notify-on-file-write",
        "type": "after_file_write",
        "script": "hooks/notify-write.sh",
        "enabled": true
      }
    ]
  }
}
```

### Hook Script Interface

```text
Environment Variables:
  HOOK_TYPE         = "pre" | "post" | "after_file_write"
  HOOK_TOOL_NAME    = "execute_command"
  HOOK_TOOL_ARGS    = '{"command": "rm -rf /tmp/old"}'
  HOOK_AGENT        = "Coder"
  HOOK_SESSION_ID   = "abc123"
  HOOK_TIMESTAMP    = "2026-03-23T14:30:00Z"

For post-hooks only:
  HOOK_TOOL_RESULT  = '{"exit_code": 0, "output": "..."}'

For after_file_write hooks only:
  HOOK_FILE_PATH    = "/path/to/file.md"
  HOOK_FILE_ACTION  = "created" | "modified"

stdin:
  Full JSON payload

Exit codes:
  0 = allow / pass-through
  1 = block (pre-hook) / error logged (post-hook)
  2 = skip remaining hooks in chain
```

---

## Centralized File-Write Pipeline

Replaces scattered `_maybe_qmd_reindex()`, `_extract_entities()`, and `file_created`
event calls from ~15 locations with one function called from `tool_write_file()` and
`tool_edit_file()`:

```python
def _after_file_write(path: str, action: str, agent_id: str = ""):
    """Centralized post-file-write pipeline."""
    # 1. QMD reindex
    _maybe_qmd_reindex(path)

    # 2. Entity extraction + knowledge graph
    if path.endswith(".md") and agent_id:
        try:
            with open(path) as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
            entities = _extract_entities(body)
            if entities:
                _update_entity_index(agent_id, os.path.basename(path), entities)
        except Exception:
            pass

    # 3. File event emission
    ecb = getattr(_thread_local, 'event_callback', None)
    if ecb:
        ecb("file_created", {
            "path": path, "name": os.path.basename(path),
            "size": os.path.getsize(path), "action": action,
        })

    # 4. External after_file_write hooks
    _run_external_hooks("after_file_write", agent_id=agent_id,
                        file_path=path, file_action=action)
```

---

## Implementation Plan

### Phase 1 — Core Engine (~2 days)

1. `HookRunner` class in `claude_cli.py`:
   - Load hooks from `agent.json` `hooks.scripts[]`
   - Match hooks by type + tool pattern (with `*` wildcard)
   - Run scripts via subprocess with env vars + stdin JSON
   - Timeout enforcement (kill after `hooks.timeout` ms)
   - Fail-open by default (crashed hooks don't block)
2. Integrate into `_execute_tool()`:
   - External pre-hooks after built-in checks
   - External post-hooks after built-in logging
3. Centralize `_after_file_write()` pipeline
4. Fix dead workflow tool restriction (enforce `_thread_local.workflow_allowed_tools`)

### Phase 2 — Configurable Output Limits (~0.5 days)

- Move hardcoded 50KB/10KB truncation to `agent.json` or provide as built-in hook script

### Phase 3 — Server API + Web UI (~2 days)

- CRUD endpoints: `GET/POST /v1/agents/{id}/hooks`
- Hook log endpoint: `GET /v1/agents/{id}/hooks/log`
- Hooks tab in agent config modal (table + editor)
- Hook log viewer

### Phase 4 — Built-in Hook Library (~1 day)

- `block-dangerous.sh` — banned command patterns
- `audit-log.sh` — structured JSONL logging
- `truncate-output.sh` — configurable output size limits
- `notify-webhook.sh` — webhook notification template

**Total: ~5.5 days**

---

## Hook Script Examples

### 1. Block Dangerous Commands (pre-hook)

```bash
#!/bin/bash
# hooks/block-dangerous.sh
COMMAND=$(echo "$HOOK_TOOL_ARGS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('command',''))")

PATTERNS=("rm -rf /" "rm -rf ~" "mkfs" "dd if=/dev/zero" ":(){ :|:& };:")

for p in "${PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qF "$p"; then
    echo "BLOCKED: Command matches dangerous pattern: $p"
    exit 1
  fi
done
exit 0
```

### 2. Truncate Large Output (post-hook)

```bash
#!/bin/bash
# hooks/truncate-output.sh
MAX_CHARS=${HOOK_MAX_CHARS:-10000}
RESULT="$HOOK_TOOL_RESULT"
if [ ${#RESULT} -gt $MAX_CHARS ]; then
  echo "${RESULT:0:$MAX_CHARS}"
  echo "--- TRUNCATED (${#RESULT} → $MAX_CHARS chars) ---"
else
  exit 0  # empty stdout = pass-through
fi
```

### 3. Webhook Notification on File Write (after_file_write hook)

```bash
#!/bin/bash
# hooks/notify-write.sh
curl -s -X POST "$HOOK_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Agent $HOOK_AGENT wrote: $HOOK_FILE_PATH ($HOOK_FILE_ACTION)\"}" &
exit 0
```

---

## Risks and Mitigations

- **Performance**: Hooks spawn subprocesses. Mitigated by tight timeout (5s), tool pattern filtering
- **Hangs**: Killed after timeout. Fail-open default prevents lockout
- **Shell injection**: Args passed via env vars + stdin JSON, not shell expansion
- **Complexity**: Hooks are optional. Built-in hooks replace existing scattered code (net simpler)
