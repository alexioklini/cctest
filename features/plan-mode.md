# Feature Proposal: Plan / Analysis Mode

**Status:** Proposal
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort Estimate:** 4-5 days

---

## Problem Statement

Brain Agent has no way to ask an agent to analyze a task without risk of it making
changes. When a user asks "refactor the auth module," the agent immediately starts
reading files, writing edits, and running commands. There is no separation between
planning and execution.

This causes several problems:

1. **No preview** — Users cannot see what the agent intends to do before it does it.
   A refactoring task might touch 15 files; the user would like to review the plan
   before committing to it.

2. **Wasted context** — If the agent starts executing and the user realizes the
   approach is wrong, the context window has already been consumed by tool calls
   and results. Compaction loses detail.

3. **Risk of unintended changes** — The agent might modify files the user did not
   expect, send emails, or execute commands with side effects.

4. **No structured output** — When users ask "how would you approach X?", the agent
   gives a prose answer. A structured plan with files, changes, and risk assessment
   is more useful.

5. **No plan-then-execute workflow** — Users cannot approve a plan and then say
   "execute exactly this." The plan is lost in chat history.

## Proposed Solution

A **Plan Mode** toggle that restricts the agent to read-only tools and produces a
structured analysis plan. The plan can then be executed with a single click/command.

### Mode Definitions

| Mode | Available Tools | Behavior |
|------|----------------|----------|
| **Execute** (default) | All tools | Normal operation, tools run freely |
| **Plan** | Read-only tools only | Analyze and produce structured plan |

### Read-Only Tools (available in Plan Mode)

```text
ALLOWED in Plan Mode:
  read_file           — Read file contents
  list_directory      — List files and directories
  search_files        — Regex search across files
  memory_recall       — Search agent memory
  memory_shared       — Read shared memory (read-only)
  web_fetch           — Fetch URL content (GET only)
  exa_search          — Web search
  use_skill           — Load skill instructions
  delegate_task       — Delegate to other agents (plan mode propagates)
  mcp_*               — MCP tools marked as read-only in their schema

BLOCKED in Plan Mode:
  write_file          — Create/overwrite files
  edit_file           — Modify files
  execute_command     — Run shell commands
  gmail_send          — Send emails
  gmail_reply         — Reply to emails
  memory_store        — Write to memory
  memory_delete       — Delete memory
  schedule_*          — Modify schedules
  task_cancel         — Cancel tasks
```

### Plan Output Format

When in Plan Mode, the system prompt is augmented with instructions to produce
a structured plan:

```text
You are in PLAN MODE. You may only read and analyze — do not make any changes.
Produce a structured plan with the following sections:

## Summary
One paragraph describing the task and approach.

## Files to Examine
List of files you need to read to understand the codebase.

## Proposed Changes
For each file that needs modification:
- File path
- What changes are needed and why
- Risk level (low/medium/high)

## Execution Steps
Numbered list of steps to implement the plan.

## Risks and Considerations
What could go wrong. Dependencies. Testing needed.

## Estimated Impact
Files modified, lines changed (approximate), new files created.
```

---

## UI Mockups

### Web UI: Mode Toggle in Status Bar

```text
Execute Mode (default):
+------------------------------------------------------------------+
|  [main v]  Session: Refactoring Auth  |  claude-sonnet-4-6  |    |
|  [======= Execute Mode =======]      |  Tokens: 12.4k/128k |    |
+------------------------------------------------------------------+

Plan Mode (toggled):
+------------------------------------------------------------------+
|  [main v]  Session: Refactoring Auth  |  claude-sonnet-4-6  |    |
|  [======== Plan Mode =========]      |  Tokens: 12.4k/128k |    |
+------------------------------------------------------------------+
|  +--------------------------------------------------------------+|
|  |  PLAN MODE ACTIVE — Read-only analysis, no changes will be   ||
|  |  made. Agent will produce a structured execution plan.       ||
|  +--------------------------------------------------------------+|

The mode toggle is a clickable button in the status bar area. In Plan Mode,
it displays with a distinct blue background. A thin blue border appears
around the entire chat area as a persistent visual reminder.
```

### Web UI: Mode Toggle Button Detail

```text
Execute mode button:
  +---------------------+
  |  >> Execute Mode    |  <- green background, bold
  +---------------------+

Plan mode button:
  +---------------------+
  |  :: Plan Mode ::    |  <- blue background, italic
  +---------------------+

Click toggles between modes. Keyboard shortcut: Ctrl+Shift+P.
```

### Web UI: Chat Showing Plan Mode Output

```text
+------------------------------------------------------------------+
|  PLAN MODE                                            [blue bar] |
+------------------------------------------------------------------+
|                                                                  |
|  You: Refactor the auth module to use JWT tokens instead of      |
|       session cookies                                            |
|                                                                  |
|  Agent: Analyzing the codebase for the auth refactoring...       |
|                                                                  |
|  [tool] read_file                                                |
|  path: server.py (lines 1-50)                                    |
|  > (file contents shown)                                         |
|                                                                  |
|  [tool] search_files                                             |
|  pattern: "session|cookie|auth" glob: "*.py"                     |
|  > 23 matches across 5 files                                     |
|                                                                  |
|  [tool] read_file                                                |
|  path: claude_cli.py (lines 200-280)                             |
|  > (file contents shown)                                         |
|                                                                  |
|  +------------------------------------------------------------+ |
|  |  EXECUTION PLAN                                             | |
|  |                                                             | |
|  |  ## Summary                                                 | |
|  |  Replace cookie-based session auth with JWT tokens.         | |
|  |  Affects 4 files. Estimated 120 lines changed.              | |
|  |                                                             | |
|  |  ## Proposed Changes                                        | |
|  |                                                             | |
|  |  1. server.py (lines 45-80)                    Risk: HIGH   | |
|  |     - Replace session middleware with JWT verify             | |
|  |     - Add JWT secret to config.json                         | |
|  |     - Change /v1/sessions to issue JWT tokens               | |
|  |                                                             | |
|  |  2. claude_cli.py (lines 200-230)              Risk: MEDIUM | |
|  |     - Update auth check in tool execution                   | |
|  |     - Remove session cookie references                      | |
|  |                                                             | |
|  |  3. web/index.html (lines 150-180)             Risk: LOW    | |
|  |     - Store JWT in localStorage instead of cookie           | |
|  |     - Add Authorization header to fetch calls               | |
|  |                                                             | |
|  |  4. config.json                                Risk: LOW    | |
|  |     - Add jwt_secret field                                  | |
|  |                                                             | |
|  |  ## Execution Steps                                         | |
|  |  1. Add PyJWT dependency                                    | |
|  |  2. Add jwt_secret to config.json                           | |
|  |  3. Implement JWT issue/verify in server.py                 | |
|  |  4. Update auth middleware in server.py                     | |
|  |  5. Update claude_cli.py auth checks                        | |
|  |  6. Update web UI fetch calls                               | |
|  |  7. Test login flow end-to-end                              | |
|  |                                                             | |
|  |  ## Risks                                                   | |
|  |  - Breaking change: all existing sessions invalidated       | |
|  |  - JWT secret must be kept secure in config.json            | |
|  |  - Telegram bot auth flow needs separate handling           | |
|  |                                                             | |
|  |  ## Estimated Impact                                        | |
|  |  Files: 4 modified, 0 new                                   | |
|  |  Lines: ~120 changed, ~30 added, ~40 removed               | |
|  +------------------------------------------------------------+ |
|                                                                  |
|  +------------------------------------------------------------+ |
|  |       [Execute This Plan]        [Refine Plan]             | |
|  +------------------------------------------------------------+ |
|                                                                  |
+------------------------------------------------------------------+
```

### Web UI: "Execute This Plan" Button Behavior

```text
User clicks [Execute This Plan]:

1. Mode toggles from Plan -> Execute
2. The plan text is sent as a new message with prefix:
   "Execute the following plan exactly as specified:\n\n{plan}"
3. Agent begins implementing in Execute mode with full tool access
4. Blue border disappears, green "Execute Mode" indicator returns

User clicks [Refine Plan]:

1. Stays in Plan Mode
2. Input box pre-fills: "Revise the plan: "
3. User can add constraints: "...but don't touch telegram.py"
4. Agent re-analyzes and produces updated plan
```

### TUI: /plan Toggle Command

```text
$ /plan

  Plan Mode: ON
  Read-only analysis mode active. Write tools are disabled.
  Agent will produce structured execution plans.
  Use /plan again to toggle off, or /plan exec to execute last plan.

brain [plan] > Refactor the auth module to use JWT

  Agent: Analyzing the codebase...

  [read_file] server.py (lines 1-50)
  [search_files] "session|cookie|auth" in *.py -> 23 matches
  [read_file] claude_cli.py (lines 200-280)

  ============================================================
  EXECUTION PLAN
  ============================================================

  ## Summary
  Replace cookie-based session auth with JWT tokens...

  ## Proposed Changes
  1. server.py (HIGH) — Replace session middleware with JWT...
  2. claude_cli.py (MEDIUM) — Update auth checks...
  3. web/index.html (LOW) — JWT in localStorage...
  4. config.json (LOW) — Add jwt_secret...

  ## Execution Steps
  1. Add PyJWT dependency
  ...

  ============================================================

$ /plan exec

  Plan Mode: OFF (Execute Mode)
  Executing the last plan...

  Agent: I'll now implement the JWT refactoring plan. Starting
  with step 1...

  [execute_command] pip3 install PyJWT
  > Successfully installed PyJWT-2.8.0

  [edit_file] config.json
  > Added jwt_secret field
  ...
```

### TUI: /plan Subcommands

```text
$ /plan          Toggle plan mode on/off
$ /plan on       Enable plan mode
$ /plan off      Disable plan mode (back to execute)
$ /plan exec     Execute the last generated plan
$ /plan show     Show the last generated plan
$ /plan save     Save the last plan to a file
```

### Web UI: Blocked Tool Indicator

When the agent tries to use a write tool in Plan Mode, the UI shows
a clear indicator:

```text
  [tool] write_file                                    [BLOCKED]
  path: server.py
  > Tool blocked: write_file is not available in Plan Mode.
  > Switch to Execute Mode to make changes.
```

---

## User Workflow

### Step 1: User Toggles Plan Mode

The user clicks the mode toggle in the Web UI status bar (or types `/plan` in
TUI). The interface changes to show Plan Mode is active:

- Blue border around chat area
- "Plan Mode" indicator in status bar
- System prompt updated to include plan mode instructions

### Step 2: User Asks for Analysis

User types: "Refactor the auth module to use JWT tokens instead of session cookies"

The agent receives the message along with plan mode instructions in the system
prompt. It knows to analyze and plan, not execute.

### Step 3: Agent Analyzes and Plans

The agent uses read-only tools to understand the codebase:

1. Reads relevant files (`read_file`)
2. Searches for patterns (`search_files`)
3. Lists directory structure (`list_directory`)
4. Recalls relevant memory (`memory_recall`)

If the agent tries to use `write_file` or `execute_command`, the engine blocks
the call and returns an error message. The agent adapts and continues analysis.

The agent produces a structured plan with:
- Summary of the approach
- Files to modify with specific changes
- Risk assessment per file
- Numbered execution steps
- Estimated impact

### Step 4: User Reviews and Executes

The user reads the plan. Two options:

**Option A: Execute immediately**
Click "Execute This Plan" button. Mode switches to Execute, plan is sent as
a message, agent implements it step by step.

**Option B: Refine**
Click "Refine Plan" and add constraints: "Don't touch telegram.py, and use
jose instead of PyJWT." Agent produces an updated plan.

---

## Implementation Plan

### Engine Changes (claude_cli.py)

1. **Mode state** — Add `plan_mode` boolean to session state (persisted in
   `chats.db` session metadata)
2. **Tool filter** — In the tool dispatch function, check `plan_mode` before
   executing write tools. Return blocked message for disallowed tools.
3. **System prompt injection** — When `plan_mode` is active, append plan mode
   instructions to the system prompt (after `tools.md` and `soul.md`)
4. **Plan extraction** — Parse the agent's plan response and store it as
   structured data for the "Execute This Plan" flow
5. **Execute plan flow** — When user clicks execute, send the stored plan
   text as a new user message with execute prefix

### Tool Classification

Add a `readonly` property to each tool definition:

```python
TOOLS = {
    "read_file":        {"readonly": True,  ...},
    "write_file":       {"readonly": False, ...},
    "edit_file":        {"readonly": False, ...},
    "list_directory":   {"readonly": True,  ...},
    "search_files":     {"readonly": True,  ...},
    "execute_command":  {"readonly": False, ...},
    "web_fetch":        {"readonly": True,  ...},
    "exa_search":       {"readonly": True,  ...},
    "gmail_inbox":      {"readonly": True,  ...},
    "gmail_read":       {"readonly": True,  ...},
    "gmail_search":     {"readonly": True,  ...},
    "gmail_send":       {"readonly": False, ...},
    "gmail_reply":      {"readonly": False, ...},
    "memory_store":     {"readonly": False, ...},
    "memory_recall":    {"readonly": True,  ...},
    "memory_shared":    {"readonly": True,  ...},  # read-only in plan mode
    "memory_delete":    {"readonly": False, ...},
    "delegate_task":    {"readonly": True,  ...},  # propagates plan mode
    "task_status":      {"readonly": True,  ...},
    "task_cancel":      {"readonly": False, ...},
    "use_skill":        {"readonly": True,  ...},
    "schedule_list":    {"readonly": True,  ...},
    "schedule_history": {"readonly": True,  ...},
}
```

### Server Changes (server.py)

1. **Plan mode in session** — Store `plan_mode` in session metadata
2. **Toggle endpoint** — `POST /v1/sessions/<id>/plan-mode` with `{"enabled": true/false}`
3. **Plan store** — Store last plan in session for "Execute This Plan" flow
4. **Execute plan endpoint** — `POST /v1/sessions/<id>/execute-plan`

### Web UI Changes (web/index.html)

1. **Mode toggle button** — Clickable button in status bar area
2. **Visual indicators** — Blue border, mode label, blocked tool badges
3. **"Execute This Plan" button** — Rendered after plan output in chat
4. **"Refine Plan" button** — Pre-fills input with refinement prompt
5. **Keyboard shortcut** — `Ctrl+Shift+P` toggles plan mode

### TUI Changes (tui.py)

1. **`/plan` command** — Toggle with subcommands (on, off, exec, show, save)
2. **Prompt indicator** — `brain [plan] >` when plan mode is active
3. **Blocked tool display** — Clear `[BLOCKED]` label for write tools

### Delegate Propagation

When plan mode is active and the agent delegates a task to another agent,
plan mode propagates:

```python
# In delegate_task handler
if _thread_local.plan_mode:
    # Delegated agent also runs in plan mode
    delegate_options["plan_mode"] = True
```

The delegated agent can only read and analyze, producing a sub-plan that
the parent agent incorporates into the overall plan.

---

## Context Window Integration

Plan Mode interacts well with Brain Agent's existing context management:

### Reduced Context Consumption

In Plan Mode, the agent only runs read tools. These consume less context than
write tools because there are no "here is what I changed" outputs. A plan
session typically uses 30-50% less context than an equivalent execute session.

### Plan as Compaction Anchor

When context compaction fires (at 75% usage), the structured plan is preserved
as a high-priority summary. The compaction algorithm recognizes plan blocks and
keeps them intact while summarizing earlier read-tool outputs.

### Plan Across Sessions

The stored plan persists in session metadata. If the user closes the browser and
returns, the plan is still available. The "Execute This Plan" button re-appears
when the session is loaded with a stored plan.

---

## Benefits

- **Safety** — Users can preview what the agent will do before any changes
- **Better plans** — Structured output format produces more thorough analysis
  than ad-hoc prose responses
- **Context efficiency** — Plan mode consumes less context, leaving more room
  for execution
- **Iterative refinement** — Users can refine plans before committing to them
- **Audit trail** — Plans are stored and can be reviewed later
- **Teaching tool** — Users learn how the agent approaches problems by seeing
  the plan before execution
- **Reduced errors** — Agent mistakes caught at plan stage cost nothing to fix;
  mistakes during execution require rollback

## Effort Estimate

| Component | Days |
|-----------|------|
| Engine: mode state + tool filter | 1 |
| Engine: system prompt injection | 0.5 |
| Engine: plan storage + execute flow | 0.5 |
| Server: API endpoints | 0.5 |
| Web UI: mode toggle + visual indicators | 1 |
| Web UI: execute/refine buttons | 0.5 |
| TUI: /plan command + prompt indicator | 0.5 |
| Delegate propagation | 0.5 |
| Testing | 0.5 |
| **Total** | **~5 days** |

## Risks and Trade-offs

### LLM May Ignore Plan Mode

Even with plan mode instructions in the system prompt, the LLM might try to
call write tools. The engine-level tool filter handles this deterministically
— the call is blocked regardless of what the LLM requests. The LLM receives
the blocked message and adapts.

### Plan Quality Varies by Model

Smaller models (e.g., Crow-4B) may produce less detailed plans than larger
models (e.g., Claude Opus). Mitigation: the plan format template in the system
prompt provides strong structure. Users can also select a more capable model
for plan mode sessions.

### Execute Fidelity

When the user clicks "Execute This Plan," the agent receives the plan as a
message and implements it. There is no guarantee the agent follows the plan
exactly — it might deviate based on what it discovers during execution. This
is actually a feature: the plan is guidance, not a rigid script. The agent
can adapt if it encounters unexpected situations.

### Mode Confusion

Users might forget they are in Plan Mode and wonder why nothing is changing.
Mitigation: persistent visual indicators (blue border, mode label in status
bar, `[plan]` in TUI prompt) make the current mode always visible.

### Delegated Plan Mode

When plan mode propagates to delegated agents, the delegated agent cannot
make changes either. This is correct for most cases but might be limiting
for tasks where a sub-agent needs to run a command to gather information
(e.g., `execute_command("git log")`). Mitigation: `execute_command` could
be allowed in plan mode for read-only commands (those matching patterns
like `^ls`, `^cat`, `^git log`), but this adds complexity. Initial
implementation blocks all `execute_command` calls; a follow-up could add
a "safe commands" allowlist for plan mode.

---

## Future Extensions

- **Diff preview** — In the plan, show actual diffs (unified diff format) for
  proposed file changes
- **Plan versioning** — Store multiple plan iterations, compare changes between
  versions
- **Plan templates** — Pre-built plan formats for common tasks (refactoring,
  debugging, feature implementation, code review)
- **Collaborative planning** — Multiple agents contribute to a single plan
  (team head collects sub-plans from members)
- **Plan cost estimation** — Estimate token usage and API cost for executing
  the plan
- **Partial execution** — Execute individual steps from the plan, not all-or-nothing
- **Plan export** — Export plan as a GitHub issue, Jira ticket, or markdown file
