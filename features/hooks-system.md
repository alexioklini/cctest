# Feature Proposal: Hooks System (Pre/Post Tool Execution)

**Status:** Proposal
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort Estimate:** 5-7 days

---

## Problem Statement

Brain Agent controls tool behavior exclusively through prompt injection: `tools.md` and
`soul.md` are loaded into the system prompt to guide the LLM. This approach has a
fundamental weakness — the LLM can ignore, misinterpret, or creatively reinterpret
these instructions. There is no deterministic enforcement layer.

Concrete gaps:

1. **No policy enforcement** — Cannot block dangerous commands like `rm -rf /` at the
   engine level. The LLM might comply with a "never run rm -rf" instruction 99% of
   the time, but the 1% failure is catastrophic.
2. **No audit logging** — Tool calls are visible in chat history but not in a
   structured, queryable log. No way to answer "what files did the agent write today?"
3. **No output transformation** — Cannot auto-format, sanitize, or truncate tool
   outputs before they re-enter the LLM context.
4. **No integration points** — Cannot trigger external systems (Slack notifications,
   webhooks, metrics) when specific tools fire.
5. **No per-agent customization** — All agents share the same tool behavior. A
   research agent and a code agent have the same permissions.

## Proposed Solution

A shell-script hooks system that fires deterministically before and/or after tool
execution. Hooks are configured per-agent in `agent.json` and executed by the engine
(`claude_cli.py`) outside the LLM's control.

### Hook Types

| Type | Fires | Can Block? | Receives | Returns |
|------|-------|-----------|----------|---------|
| `pre` | Before tool executes | Yes | Tool name, args (JSON) | Exit 0 = allow, exit 1 = block |
| `post` | After tool executes | No | Tool name, args, result (JSON) | Exit 0 = pass-through, stdout = modified result |

### Hook Execution Flow

```text
User message
    |
    v
LLM decides to call tool (e.g., execute_command)
    |
    v
+----------------------------+
| PRE-HOOKS (sequential)     |
|                            |
|  hook_1.sh (pre)           |
|    exit 0 -> continue      |
|    exit 1 -> BLOCK + msg   |
|                            |
|  hook_2.sh (pre)           |
|    exit 0 -> continue      |
+----------------------------+
    |
    v (all pre-hooks passed)
+----------------------------+
| TOOL EXECUTES              |
|  execute_command("ls -la") |
|  -> result                 |
+----------------------------+
    |
    v
+----------------------------+
| POST-HOOKS (sequential)    |
|                            |
|  hook_3.sh (post)          |
|    stdout -> modified result|
|    (or pass-through)       |
+----------------------------+
    |
    v
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
  "avatar": "robot",
  "max_context": 65536,
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
        "name": "audit-log",
        "type": "post",
        "tools": ["*"],
        "script": "hooks/audit-log.sh",
        "enabled": true
      },
      {
        "name": "require-approval-for-writes",
        "type": "pre",
        "tools": ["write_file", "edit_file"],
        "script": "hooks/require-approval.sh",
        "enabled": false
      },
      {
        "name": "truncate-large-output",
        "type": "post",
        "tools": ["execute_command", "web_fetch"],
        "script": "hooks/truncate-output.sh",
        "enabled": true
      }
    ]
  }
}
```

### Hook Script Interface

Hooks receive data via environment variables and stdin:

```text
Environment Variables:
  HOOK_TOOL_NAME    = "execute_command"
  HOOK_TOOL_ARGS    = '{"command": "rm -rf /tmp/old", "timeout": 15}'
  HOOK_AGENT        = "Coder"
  HOOK_SESSION_ID   = "abc123"
  HOOK_TYPE         = "pre" | "post"
  HOOK_TIMESTAMP    = "2026-03-20T14:30:00Z"

For post-hooks only:
  HOOK_TOOL_RESULT  = '{"exit_code": 0, "stdout": "...", "stderr": ""}'

stdin:
  Full JSON payload (same data, for scripts that prefer parsing stdin)

stdout (pre-hook):
  Block message (shown to LLM if exit 1)

stdout (post-hook):
  Modified result (replaces original if non-empty)

Exit codes:
  0 = allow / pass-through
  1 = block (pre-hook) / error logged (post-hook)
  2 = skip remaining hooks in chain
```

---

## Hook Script Examples

### 1. Block Dangerous Commands

```bash
#!/bin/bash
# hooks/block-dangerous.sh — Pre-hook for execute_command
# Blocks commands matching dangerous patterns

COMMAND=$(echo "$HOOK_TOOL_ARGS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('command',''))")

DANGEROUS_PATTERNS=(
  "rm -rf /"
  "rm -rf ~"
  "rm -rf \$HOME"
  "mkfs"
  "dd if=/dev/zero"
  "> /dev/sda"
  "chmod -R 777 /"
  ":(){ :|:& };:"
)

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qF "$pattern"; then
    echo "BLOCKED: Command matches dangerous pattern: $pattern"
    echo "The command '$COMMAND' was blocked by the safety hook."
    exit 1
  fi
done

# Allow all other commands
exit 0
```

### 2. Audit Log (All Tools)

```bash
#!/bin/bash
# hooks/audit-log.sh — Post-hook for all tools
# Appends every tool call to a structured log file

LOG_DIR="agents/${HOOK_AGENT}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audit-$(date +%Y-%m-%d).jsonl"

python3 -c "
import json, sys, os
entry = {
    'timestamp': os.environ['HOOK_TIMESTAMP'],
    'agent': os.environ['HOOK_AGENT'],
    'session': os.environ['HOOK_SESSION_ID'],
    'tool': os.environ['HOOK_TOOL_NAME'],
    'args': json.loads(os.environ.get('HOOK_TOOL_ARGS', '{}')),
    'result_length': len(os.environ.get('HOOK_TOOL_RESULT', ''))
}
print(json.dumps(entry))
" >> "$LOG_FILE"

exit 0
```

### 3. Require Approval for File Writes

```bash
#!/bin/bash
# hooks/require-approval.sh — Pre-hook for write_file, edit_file
# Writes a pending-approval marker; external system must approve

APPROVAL_DIR="/tmp/brain-agent-approvals"
mkdir -p "$APPROVAL_DIR"

REQUEST_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")
APPROVAL_FILE="$APPROVAL_DIR/$REQUEST_ID.json"

# Write approval request
echo "$HOOK_TOOL_ARGS" > "$APPROVAL_FILE"

echo "PENDING APPROVAL: File write operation requires approval."
echo "Request ID: $REQUEST_ID"
echo "Waiting for approval (timeout: 60s)..."

# Poll for approval (simplified — real implementation would use the API)
for i in $(seq 1 12); do
  if [ -f "$APPROVAL_FILE.approved" ]; then
    rm -f "$APPROVAL_FILE" "$APPROVAL_FILE.approved"
    exit 0
  fi
  if [ -f "$APPROVAL_FILE.denied" ]; then
    MSG=$(cat "$APPROVAL_FILE.denied")
    rm -f "$APPROVAL_FILE" "$APPROVAL_FILE.denied"
    echo "DENIED: $MSG"
    exit 1
  fi
  sleep 5
done

# Timeout = deny
rm -f "$APPROVAL_FILE"
echo "DENIED: Approval timed out after 60 seconds."
exit 1
```

### 4. Truncate Large Output

```bash
#!/bin/bash
# hooks/truncate-output.sh — Post-hook for execute_command, web_fetch
# Truncates output longer than 10000 chars to save context window

MAX_CHARS=10000
RESULT="$HOOK_TOOL_RESULT"
LENGTH=${#RESULT}

if [ "$LENGTH" -gt "$MAX_CHARS" ]; then
  echo "${RESULT:0:$MAX_CHARS}"
  echo ""
  echo "--- OUTPUT TRUNCATED ($LENGTH chars -> $MAX_CHARS chars) ---"
else
  # Empty stdout = pass-through original result
  exit 0
fi
```

---

## UI Mockups

### Web UI: Hooks Tab in Agent Config Modal

```text
+------------------------------------------------------------------+
|  Agent Config: Coder                                    [X]      |
+------------------------------------------------------------------+
| [Soul] [Settings] [Skills] [MCP] [Schedule] [Hooks]             |
+------------------------------------------------------------------+
|                                                                  |
|  Hooks   [Enabled]                         Timeout: [5000] ms   |
|  ----------------------------------------------------------------|
|                                                                  |
|  +------------------------------------------------------------+ |
|  | Name                      | Type | Tools          | Active | |
|  |------------------------------------------------------------|  |
|  | block-dangerous-commands  | pre  | execute_command | [ON]  |  |
|  | audit-log                 | post | *               | [ON]  |  |
|  | require-approval-writes   | pre  | write_file, ... | [OFF] |  |
|  | truncate-large-output     | post | execute_command | [ON]  |  |
|  +------------------------------------------------------------+ |
|                                                                  |
|  [+ Add Hook]                                                    |
|                                                                  |
|  --- Hook Editor ---                                             |
|  Name:   [block-dangerous-commands          ]                    |
|  Type:   (o) Pre-hook  ( ) Post-hook                             |
|  Tools:  [execute_command                   ] [+ Add Tool]       |
|  Script: [hooks/block-dangerous.sh          ] [Browse...]        |
|                                                                  |
|  Preview:                                                        |
|  +----------------------------------------------------------+   |
|  | #!/bin/bash                                               |   |
|  | # hooks/block-dangerous.sh                                |   |
|  | COMMAND=$(echo "$HOOK_TOOL_ARGS" | python3 -c ...)        |   |
|  | ...                                                       |   |
|  +----------------------------------------------------------+   |
|                                                                  |
|                                        [Cancel]  [Save Hook]    |
+------------------------------------------------------------------+
```

### Web UI: Add Hook Dialog

```text
+--------------------------------------------------+
|  Add New Hook                             [X]    |
+--------------------------------------------------+
|                                                  |
|  Name:   [                              ]        |
|                                                  |
|  Type:   [Pre-hook           v]                  |
|                                                  |
|  Tools:  [Select tools...    v]                  |
|          [ ] execute_command                     |
|          [ ] write_file                          |
|          [ ] edit_file                           |
|          [ ] web_fetch                           |
|          [ ] gmail_send                          |
|          [x] * (all tools)                       |
|                                                  |
|  Script path: [hooks/my-hook.sh  ] [Browse]      |
|                                                  |
|  Or paste script inline:                         |
|  +----------------------------------------------+|
|  | #!/bin/bash                                   ||
|  |                                               ||
|  +----------------------------------------------+|
|                                                  |
|                         [Cancel]  [Create Hook]  |
+--------------------------------------------------+
```

### TUI: /hooks Command

```text
$ /hooks

  Hooks for agent: Coder
  +---------------------------------------------------------+
  | #  Name                      Type  Tools           On?  |
  |---------------------------------------------------------|
  | 1  block-dangerous-commands  pre   execute_command  YES  |
  | 2  audit-log                 post  *                YES  |
  | 3  require-approval-writes   pre   write_file,...   NO   |
  | 4  truncate-large-output     post  execute_command  YES  |
  +---------------------------------------------------------+

  Commands:
    /hooks enable <#>    Enable a hook
    /hooks disable <#>   Disable a hook
    /hooks add           Add a new hook (interactive)
    /hooks remove <#>    Remove a hook
    /hooks test <#>      Run hook with sample input
    /hooks log           Show recent hook activity

$ /hooks log

  Recent hook activity (last 10):
  14:30:01  block-dangerous-commands  BLOCKED  execute_command("rm -rf /")
  14:29:45  audit-log                 OK       read_file("server.py")
  14:29:30  truncate-large-output     TRUNCATED execute_command("find / ...")
  14:28:12  audit-log                 OK       memory_store(...)
```

### Terminal Output: Hook Blocking a Dangerous Command

```text
You: Delete all temporary files from the root directory

Agent: I'll clean up the temporary files for you.

  [tool] execute_command
  command: rm -rf /tmp/* /var/tmp/*

  HOOK BLOCKED: block-dangerous-commands
  +---------------------------------------------------------+
  | Command matches dangerous pattern: rm -rf /             |
  | The command 'rm -rf /tmp/* /var/tmp/*' was blocked by   |
  | the safety hook.                                        |
  +---------------------------------------------------------+

Agent: The command was blocked by a safety hook because it matched
a dangerous pattern. Let me use a safer approach instead — I'll
list the files first and remove them individually.

  [tool] execute_command
  command: find /tmp -maxdepth 1 -type f -mtime +7 -print

  [result] /tmp/old-log.txt
           /tmp/cache-abc123.dat

  [tool] execute_command
  command: rm /tmp/old-log.txt /tmp/cache-abc123.dat

  [result] (success)

Agent: Done. I removed 2 temporary files older than 7 days:
- /tmp/old-log.txt
- /tmp/cache-abc123.dat
```

---

## User Workflow

### Step 1: Create a Hook Script

User writes a shell script and saves it in the agent's hooks directory:

```text
agents/Coder/hooks/block-dangerous.sh
```

The script follows the hook interface: reads `HOOK_TOOL_ARGS`, exits 0 (allow)
or 1 (block with message on stdout).

### Step 2: Configure in agent.json or Web UI

**Option A — Edit agent.json directly:**

Add the hook to the `hooks.scripts` array (see configuration section above).

**Option B — Use Web UI:**

1. Click on the agent card to open config modal
2. Navigate to the "Hooks" tab
3. Click "+ Add Hook"
4. Fill in name, type, tools, and script path
5. Click "Create Hook"
6. Toggle the enable switch

### Step 3: Agent Tries a Dangerous Command

During a chat, the agent decides to run `rm -rf /`:

1. Engine receives the tool call from the LLM
2. Engine checks for pre-hooks matching `execute_command`
3. Runs `block-dangerous.sh` with the command in `HOOK_TOOL_ARGS`
4. Script detects the pattern, prints block message, exits 1
5. Engine does NOT execute the command
6. Block message is returned to the LLM as the tool result

### Step 4: User Sees the Block in Chat

The chat shows the hook block message inline, clearly attributed to the hook.
The LLM receives the block message and adapts its approach (e.g., uses a safer
command). The audit log records the blocked attempt.

---

## Implementation Plan

### Engine Changes (claude_cli.py)

1. **Hook loader** — Parse `hooks` section from `agent.json`, resolve script
   paths relative to agent directory
2. **Pre-hook runner** — Before each tool call, find matching pre-hooks, run
   sequentially, abort on exit 1
3. **Post-hook runner** — After each tool call, find matching post-hooks, run
   sequentially, allow result modification
4. **Timeout enforcement** — Kill hook process after `hooks.timeout` ms
5. **Error handling** — Hook crashes treated as exit 0 (fail-open) with warning
   logged; configurable fail-closed mode

### Server Changes (server.py)

1. **Hook management API** — CRUD endpoints for hooks
2. **Hook log endpoint** — `GET /v1/agents/<name>/hooks/log`

### Web UI Changes (web/index.html)

1. **Hooks tab** in agent config modal
2. **Hook editor** with syntax-highlighted script preview
3. **Hook log viewer** showing recent hook activity

### TUI Changes (tui.py)

1. **`/hooks`** slash command with subcommands

---

## Benefits

- **Deterministic safety** — Dangerous commands are blocked regardless of LLM behavior
- **Audit trail** — Every tool call logged with structured data for compliance
- **Extensibility** — Any shell script can be a hook; no code changes needed for new policies
- **Per-agent policies** — Research agents can have permissive hooks; code agents can have strict ones
- **Output control** — Truncate, sanitize, or transform tool output before it consumes context
- **Integration** — Hooks can call webhooks, send Slack messages, update dashboards
- **Composable** — Multiple hooks chain together; each can independently allow or block

## Effort Estimate

| Component | Days |
|-----------|------|
| Engine: hook loader + runner | 2 |
| Engine: timeout, error handling | 0.5 |
| Server: API endpoints | 0.5 |
| Web UI: hooks tab + editor | 1.5 |
| TUI: /hooks command | 0.5 |
| Testing + example hooks | 1 |
| Documentation | 0.5 |
| **Total** | **~6 days** |

## Risks and Trade-offs

### Performance Impact

Each tool call now spawns 0-N subprocess for hooks. Mitigation: hooks run with a
tight timeout (default 5s), and the `tools` filter ensures only relevant hooks fire.
Most agents will have 1-3 hooks total.

### Hook Script Hangs

If a hook script hangs (e.g., waiting for network, infinite loop):

1. The hook timeout (default 5000ms) kills the process after the deadline
2. A killed hook is treated as exit code 1 (block) for pre-hooks, providing
   fail-safe behavior
3. The hang is logged with a warning
4. The LLM receives a message: "Hook timed out: <hook-name> (killed after 5s)"
5. Configurable: `"on_timeout": "block"` (default) or `"on_timeout": "allow"`

### Fail-Open vs Fail-Closed

Default is **fail-open**: if a hook crashes (not timeout, but actual error), the
tool call proceeds. This prevents broken hooks from locking out all tool usage.

For high-security environments, configure `"on_error": "block"` per hook to
enable fail-closed behavior.

### Shell Injection

Hook scripts receive tool arguments via environment variables. Arguments
containing shell metacharacters could be dangerous if the hook script uses
`eval` or unquoted variable expansion. Mitigation:

- Documentation warns against `eval` and unquoted expansions
- Engine passes args as JSON via stdin as well (safer to parse)
- Example scripts use `python3 -c` for JSON parsing, not shell expansion

### Complexity

Adding hooks increases cognitive overhead for agent configuration. Mitigation:

- Hooks are entirely optional; default behavior is unchanged
- Provide a library of ready-made hook scripts for common patterns
- Web UI makes hooks discoverable and manageable without editing JSON

---

## Future Extensions

- **Hook marketplace** — Share hooks via ClawHub alongside skills
- **Conditional hooks** — Fire only when pattern matches (regex on args)
- **Async post-hooks** — Fire-and-forget for logging, notifications
- **Hook metrics** — Dashboard showing hook fire count, block rate, latency
- **Built-in hooks** — Common patterns (rate limiting, path allowlists) as
  first-class engine features without shell scripts
