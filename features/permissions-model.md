# Feature Proposal: Permissions and Approval System

**Status:** Proposal
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort Estimate:** 7-10 days

---

## Problem Statement

Brain Agent runs all tools without any user approval. When the LLM decides to call
`execute_command`, `write_file`, `gmail_send`, or any other tool, it executes
immediately. There is no mechanism to:

1. **Require confirmation** for destructive operations before they run
2. **Deny specific tools** to specific agents entirely
3. **Restrict tool arguments** (e.g., only allow writes to a specific directory)
4. **Audit approvals** — no record of what was approved or denied
5. **Differentiate risk levels** — a `read_file` and a `gmail_send` have the
   same level of implicit trust

This is especially problematic for:

- **Scheduled tasks** — run unattended, no user watching
- **Delegated tasks** — agent A delegates to agent B, which runs tools autonomously
- **Telegram frontend** — used from mobile, harder to monitor
- **Public access** — Cloudflare tunnel exposes the server to the internet

## Proposed Solution

A per-tool permission system with three modes: **auto** (execute without asking),
**ask** (require user approval), and **deny** (always block). Permissions are
configured per-agent with pattern matching on tool arguments.

### Permission Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `auto` | Execute immediately, no prompt | Safe read-only tools, trusted paths |
| `ask` | Pause and request user approval | Destructive ops, external comms |
| `deny` | Block unconditionally | Forbidden operations for this agent |

### Permission Resolution Order

```text
1. Check agent-specific permissions (agent.json -> permissions)
2. Check tool-specific rules with pattern matching
3. Check argument patterns (path globs, command regex)
4. Fall back to default mode for that tool category
5. Global default: "auto" (backward compatible)
```

---

## Configuration

### agent.json permissions config

```json
{
  "description": "Research assistant",
  "display_name": "Researcher",
  "model": "claude-sonnet-4-6",
  "permissions": {
    "default": "auto",
    "rules": [
      {
        "tools": ["execute_command"],
        "mode": "ask",
        "description": "Require approval for all shell commands"
      },
      {
        "tools": ["execute_command"],
        "mode": "auto",
        "patterns": {
          "command": ["^ls ", "^cat ", "^head ", "^wc ", "^grep ", "^find "]
        },
        "description": "Auto-allow read-only shell commands"
      },
      {
        "tools": ["execute_command"],
        "mode": "deny",
        "patterns": {
          "command": ["^rm -rf", "^sudo", "^chmod 777", "^mkfs"]
        },
        "description": "Block dangerous commands outright"
      },
      {
        "tools": ["write_file", "edit_file"],
        "mode": "auto",
        "patterns": {
          "path": ["^agents/Researcher/", "^/tmp/"]
        },
        "description": "Auto-allow writes to own directory and tmp"
      },
      {
        "tools": ["write_file", "edit_file"],
        "mode": "ask",
        "description": "Ask for writes outside allowed paths"
      },
      {
        "tools": ["gmail_send", "gmail_reply"],
        "mode": "ask",
        "description": "Always ask before sending emails"
      },
      {
        "tools": ["delegate_task"],
        "mode": "auto",
        "description": "Allow delegation without asking"
      },
      {
        "tools": ["read_file", "search_files", "list_directory",
                  "memory_store", "memory_recall", "memory_shared"],
        "mode": "auto",
        "description": "Read and memory tools are always safe"
      }
    ],
    "scheduled_task_mode": "auto-with-log",
    "delegated_task_mode": "inherit"
  }
}
```

### Permission Rule Matching

Rules are evaluated top-to-bottom. The first matching rule wins. Rules with
`patterns` are more specific and should come before general rules for the
same tool.

```text
Tool call: execute_command(command="rm -rf /tmp/old")

Rule evaluation:
  1. execute_command + pattern "^rm -rf" -> DENY (match!)
  -> Result: BLOCKED

Tool call: execute_command(command="ls -la /home")

Rule evaluation:
  1. execute_command + pattern "^rm -rf" -> no match
  2. execute_command + pattern "^ls " -> AUTO (match!)
  -> Result: EXECUTE IMMEDIATELY

Tool call: execute_command(command="python3 script.py")

Rule evaluation:
  1. execute_command + pattern "^rm -rf" -> no match
  2. execute_command + pattern "^ls " -> no match
  3. execute_command (no pattern, mode=ask) -> ASK (match!)
  -> Result: PROMPT USER FOR APPROVAL
```

---

## UI Mockups

### Web UI: Approval Dialog During Chat

```text
+------------------------------------------------------------------+
|  Chat with Researcher                                            |
|------------------------------------------------------------------|
|                                                                  |
|  You: Find all Python files with unused imports and clean them   |
|                                                                  |
|  Researcher: I'll search for unused imports. Let me start by     |
|  scanning the Python files.                                      |
|                                                                  |
|  [tool] search_files                              [auto-allowed] |
|  pattern: "^import.*" glob: "*.py"                               |
|  > Found 47 matches across 12 files                              |
|                                                                  |
|  Researcher: I found several unused imports. Let me fix them.    |
|                                                                  |
|  +------------------------------------------------------------+ |
|  |  APPROVAL REQUIRED                                          | |
|  |                                                             | |
|  |  Researcher wants to run:                                   | |
|  |                                                             | |
|  |  edit_file                                                  | |
|  |  path: server.py                                            | |
|  |  old_string: "import unused_module\n"                       | |
|  |  new_string: ""                                             | |
|  |                                                             | |
|  |  Rule: "Ask for writes outside allowed paths"               | |
|  |                                                             | |
|  |  [Allow]  [Deny]  [Always Allow This Path]  [Deny All]     | |
|  +------------------------------------------------------------+ |
|                                                                  |
+------------------------------------------------------------------+
```

### Web UI: Permissions Editor in Agent Config Modal

```text
+------------------------------------------------------------------+
|  Agent Config: Researcher                               [X]      |
+------------------------------------------------------------------+
| [Soul] [Settings] [Skills] [MCP] [Schedule] [Permissions]        |
+------------------------------------------------------------------+
|                                                                  |
|  Default Mode: [Auto           v]                                |
|                                                                  |
|  Permission Rules (evaluated top-to-bottom, first match wins)    |
|  +------------------------------------------------------------+ |
|  | #  Tools              Mode  Pattern          Description    | |
|  |------------------------------------------------------------|  |
|  | 1  execute_command     DENY  ^rm -rf, ^sudo  Block danger  |  |
|  | 2  execute_command     AUTO  ^ls, ^cat, ...  Read-only OK  |  |
|  | 3  execute_command     ASK   (any)           Ask for rest  |  |
|  | 4  write_file,edit_f   AUTO  ^agents/Res...  Own dir OK    |  |
|  | 5  write_file,edit_f   ASK   (any)           Ask others    |  |
|  | 6  gmail_send,reply    ASK   (any)           Ask email     |  |
|  | 7  read_file,search_   AUTO  (any)           Reads safe    |  |
|  +------------------------------------------------------------+ |
|                                                                  |
|  [drag handles on left to reorder]                               |
|                                                                  |
|  [+ Add Rule]                                                    |
|                                                                  |
|  --- Scheduled Tasks ---                                         |
|  When no user is present: [Auto with Audit Log  v]              |
|    ( ) Block all "ask" tools                                     |
|    (o) Auto-allow and log (review later)                        |
|    ( ) Use pre-approved allowlist only                           |
|                                                                  |
|  --- Delegated Tasks ---                                         |
|  Inherit permissions from: [Delegating agent  v]                |
|                                                                  |
|                                        [Cancel]  [Save]          |
+------------------------------------------------------------------+
```

### Web UI: Add/Edit Permission Rule

```text
+--------------------------------------------------+
|  Edit Permission Rule                     [X]    |
+--------------------------------------------------+
|                                                  |
|  Description: [Block dangerous commands    ]     |
|                                                  |
|  Mode:  (o) Auto   ( ) Ask   ( ) Deny           |
|                                                  |
|  Tools:                                          |
|  [x] execute_command                             |
|  [ ] write_file                                  |
|  [ ] edit_file                                   |
|  [ ] read_file                                   |
|  [ ] gmail_send                                  |
|  [ ] gmail_reply                                 |
|  [ ] delegate_task                               |
|  [ ] web_fetch                                   |
|  [ ] * (all tools)                               |
|                                                  |
|  Argument Patterns (regex, optional):            |
|  +----------------------------------------------+|
|  | command:                                      ||
|  |   ^rm -rf                                     ||
|  |   ^sudo                                       ||
|  |   ^chmod 777                                  ||
|  | [+ add pattern]                               ||
|  +----------------------------------------------+|
|                                                  |
|                         [Cancel]  [Save Rule]    |
+--------------------------------------------------+
```

### TUI: Inline Approval Prompt

```text
You: Send an email to john@example.com about the project status

Agent: I'll compose and send that email for you.

  [tool] gmail_send
  to: john@example.com
  subject: Project Status Update
  body: Hi John, here's the latest on our project...

  +---------------------------------------------------------+
  | APPROVAL REQUIRED                                        |
  | Agent wants to send an email:                            |
  |   To: john@example.com                                   |
  |   Subject: Project Status Update                         |
  |   Body: Hi John, here's the latest on our project...     |
  |         (truncated, 245 chars)                            |
  |                                                          |
  |   [a]llow  [d]eny  [A]lways allow  [v]iew full body     |
  +---------------------------------------------------------+

> a

  [result] Email sent successfully.

Agent: Done! I sent the project status update to john@example.com.
```

### Web UI: Audit Log

```text
+------------------------------------------------------------------+
|  Permission Audit Log — Researcher              [Export CSV]      |
+------------------------------------------------------------------+
|  Filter: [All modes  v]  [All tools  v]  [Today  v]  [Search]   |
+------------------------------------------------------------------+
|                                                                  |
|  Time       Tool              Mode     Decision   Details        |
|  ----------------------------------------------------------------|
|  14:32:01   execute_command   ask      APPROVED   python3 scr..  |
|  14:31:45   edit_file         ask      DENIED     /etc/hosts     |
|  14:31:30   search_files      auto     allowed    *.py unused..  |
|  14:31:12   execute_command   deny     BLOCKED    rm -rf /tmp..  |
|  14:30:58   read_file         auto     allowed    server.py      |
|  14:30:45   gmail_send        ask      APPROVED   john@exampl..  |
|  14:30:01   execute_command   auto     allowed    ls -la /home   |
|                                                                  |
|  Showing 7 of 156 entries                    [< Prev] [Next >]   |
+------------------------------------------------------------------+
|                                                                  |
|  Summary (today):                                                |
|  +------+------+------+-------+---------+                        |
|  | Auto | Ask  | Deny | Total | Blocked |                        |
|  | 89   | 42   | 25   | 156   | 31      |                        |
|  +------+------+------+-------+---------+                        |
+------------------------------------------------------------------+
```

---

## User Workflow

### Step 1: Admin Configures Permissions

The admin (user) opens the Web UI, clicks on the Researcher agent card, opens
the config modal, and navigates to the "Permissions" tab. They set up rules:

- Read tools: auto (always safe)
- Shell commands matching read-only patterns: auto
- Shell commands matching dangerous patterns: deny
- All other shell commands: ask
- File writes to agent's own directory: auto
- File writes elsewhere: ask
- Email: ask

### Step 2: Agent Tries a Safe Write (Auto-Allowed)

```text
Agent calls: write_file(path="agents/Researcher/notes.md", content="...")

Permission check:
  Rule 4: write_file + pattern "^agents/Researcher/" -> AUTO
  Result: Execute immediately, log entry created

User sees in chat:
  [tool] write_file                                [auto-allowed]
  path: agents/Researcher/notes.md
  > Written 245 bytes
```

### Step 3: Agent Tries a Shell Command (Ask Mode)

```text
Agent calls: execute_command(command="python3 analyze.py --output results.csv")

Permission check:
  Rule 1: pattern "^rm -rf" -> no match
  Rule 2: pattern "^ls " -> no match
  Rule 3: execute_command (any) -> ASK
  Result: Pause and prompt user

User sees approval dialog in Web UI (or inline prompt in TUI).
User clicks "Allow".
Command executes. Result shown.
```

### Step 4: User Reviews Audit Log

After the session, the admin checks the audit log to see what was approved
and denied. They notice the agent frequently asks to run `python3` scripts,
so they add a new auto-allow pattern for `^python3 ` to reduce friction.

---

## Implementation Plan

### Engine Changes (claude_cli.py)

1. **Permission evaluator** — `_check_permission(agent, tool_name, tool_args)`
   returns `auto`, `ask`, or `deny`
2. **Rule matcher** — Pattern matching with regex on tool arguments
3. **Approval request** — For `ask` mode, emit an SSE event and wait for response
4. **Approval timeout** — Configurable timeout (default 5 minutes), then deny
5. **Audit logger** — SQLite table `permission_log` in `chats.db`
6. **Scheduled task handling** — Separate mode for unattended execution

### Server Changes (server.py)

1. **Approval SSE event** — New event type `permission_request` in chat stream
2. **Approval response endpoint** — `POST /v1/permissions/respond`
3. **Permissions CRUD API** — `GET/POST /v1/agents/<name>/permissions`
4. **Audit log endpoint** — `GET /v1/agents/<name>/permissions/log`

### Web UI Changes (web/index.html)

1. **Approval dialog** — Modal overlay during chat when `permission_request` received
2. **Permissions tab** — In agent config modal with rule editor
3. **Audit log page** — Filterable, sortable log view
4. **Visual indicators** — `[auto-allowed]` and `[user-approved]` badges on tool calls

### TUI Changes (tui.py)

1. **Inline approval prompt** — Intercept `permission_request` SSE events
2. **`/permissions`** slash command for viewing/editing rules
3. **Keyboard shortcuts** — `a` allow, `d` deny, `A` always allow

### SSE Protocol Addition

```text
Current SSE events:
  event: content_block_delta   (text streaming)
  event: tool_use              (tool call)
  event: tool_result           (tool output)
  :keepalive                   (every 5s)

New SSE events:
  event: permission_request
  data: {
    "request_id": "abc123",
    "tool": "execute_command",
    "args": {"command": "python3 script.py"},
    "rule": "Require approval for all shell commands",
    "agent": "Researcher",
    "timeout": 300
  }

New endpoint:
  POST /v1/permissions/respond
  body: {
    "request_id": "abc123",
    "decision": "allow" | "deny" | "always_allow",
    "always_allow_pattern": "^python3 "   (optional, for "always allow")
  }
```

---

## Scheduled Tasks: No User Present

When a scheduled task runs, there is no user to approve `ask`-mode tool calls.
Three configurable strategies:

### 1. Block All "Ask" Tools (`scheduled_task_mode: "block"`)

Any tool call that would trigger an approval prompt is blocked. The agent must
work with only `auto` and `deny` tools. Safest option.

### 2. Auto-Allow with Audit Log (`scheduled_task_mode: "auto-with-log"`)

All `ask`-mode tools are auto-allowed but logged with a flag `unattended: true`.
The admin reviews these entries later. Good balance of safety and functionality.

### 3. Pre-Approved Allowlist (`scheduled_task_mode: "allowlist"`)

Only specific tool+pattern combinations are pre-approved for unattended use.
Everything else is blocked.

```json
{
  "scheduled_task_mode": "allowlist",
  "scheduled_allowlist": [
    {"tool": "execute_command", "patterns": {"command": ["^python3 daily_report.py"]}},
    {"tool": "gmail_send", "patterns": {"to": ["^team@company.com$"]}},
    {"tool": "write_file", "patterns": {"path": ["^agents/Researcher/reports/"]}}
  ]
}
```

---

## Delegated Tasks

When agent A delegates to agent B, whose permissions apply?

### Option 1: Inherit from Delegator (`delegated_task_mode: "inherit"`)

Agent B uses agent A's permissions. If A is a restricted agent and B is
permissive, B still operates under A's restrictions for this task.

### Option 2: Use Own Permissions (`delegated_task_mode: "own"`)

Agent B uses its own permissions. Standard behavior — each agent is
independently configured.

### Option 3: Intersection (`delegated_task_mode: "intersection"`)

Agent B can only do what BOTH A's and B's permissions allow. Most restrictive
option, useful for high-security environments.

---

## Benefits

- **User control** — Destructive operations require explicit approval
- **Per-agent sandboxing** — Research agents can be restricted to read-only
- **Audit compliance** — Full log of what was allowed, denied, and by whom
- **Backward compatible** — Default mode is `auto`, existing behavior unchanged
- **Pattern-based** — Fine-grained control without listing every possible command
- **Multi-frontend** — Works in Web UI, TUI, and Telegram
- **Unattended safety** — Configurable behavior for scheduled tasks

## Effort Estimate

| Component | Days |
|-----------|------|
| Engine: permission evaluator + rule matcher | 2 |
| Engine: approval flow (SSE event + wait) | 1.5 |
| Engine: audit logger (SQLite) | 0.5 |
| Server: API endpoints | 1 |
| Web UI: approval dialog | 1 |
| Web UI: permissions editor tab | 1.5 |
| Web UI: audit log page | 1 |
| TUI: inline approval + /permissions | 1 |
| Telegram: approval inline keyboard | 0.5 |
| Testing + edge cases | 1 |
| Documentation | 0.5 |
| **Total** | **~9 days** |

## Risks and Trade-offs

### Approval Latency

When a tool requires approval, the LLM conversation pauses. The SSE stream
stays open (keepalive comments prevent timeout), but the user experience is
interrupted. Mitigation: clear UI showing what is waiting, with timeout.

### Approval Timeout

If the user does not respond within the timeout (default 5 minutes):

- The tool call is denied
- The LLM receives: "Permission denied: approval timed out after 5 minutes"
- The agent can adapt or ask the user to retry

### Over-Restriction

If permissions are too strict, the agent becomes useless — every tool call
requires approval, and the user spends more time approving than they would
doing the work themselves. Mitigation:

- Default is `auto` (everything allowed, like today)
- Pattern matching allows fine-grained auto-allow for safe operations
- "Always Allow" button in approval dialog adds patterns dynamically

### SSE Connection Stability

The approval flow requires the SSE connection to stay alive while waiting for
user input. Brain Agent already sends keepalive comments every 5 seconds, which
handles browser/proxy timeouts. The `AbortController` pattern in the Web UI
ensures cleanup if the user navigates away.

### Telegram Approval UX

Telegram has limited UI. Approval requests use inline keyboards:

```text
[Researcher] wants to run:
  execute_command("python3 script.py")

[Allow] [Deny]
```

The inline keyboard button sends the response. Works but less rich than Web UI.

### Complexity Budget

Permissions add cognitive overhead. Users must understand rule evaluation order,
pattern syntax, and the interaction between scheduled/delegated task modes.
Mitigation: ship with sensible presets ("Permissive", "Standard", "Strict")
that users can start from.

---

## Future Extensions

- **Permission presets** — "Permissive" (all auto), "Standard" (ask for shell +
  email), "Strict" (ask for everything except reads)
- **Time-based rules** — "Allow shell commands only during business hours"
- **Rate limiting** — "Max 10 file writes per session"
- **Approval delegation** — Specific users can approve specific agents
- **Permission inheritance** — Team-level permissions inherited by team members
- **Mobile notifications** — Push notification when approval is needed
