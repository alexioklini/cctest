# Feature Proposal: Custom Slash Commands

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** Medium (1 week)
**Priority:** Medium

---

## Problem

Users frequently perform the same tasks by typing similar prompts over and over. Common
examples include:

- "Summarize my unread emails and list action items"
- "Check server status on all services and report any issues"
- "Review this PR and focus on security and performance"
- "Translate this text to Spanish"

Currently there is no mechanism to save these as reusable shortcuts. Users must either
remember their exact phrasing, scroll through chat history to copy-paste, or rely on the
agent's memory of past interactions. This creates friction for power users who have
optimized their prompts through iteration.

The existing slash command system (`/schedule`, `/workflow`, `/agents`, etc.) demonstrates
that users understand and use the `/command` pattern. Extending this to user-defined
commands is a natural evolution.

## Proposed Solution

Allow users to define **custom slash commands** per-agent via a `commands.json` file or
through the agent configuration UI. Each command has:

- **name** -- the slash command trigger (e.g., `daily-report`)
- **description** -- shown in the command picker popup
- **prompt** -- the template text sent as a message, with optional variable interpolation
- **agent** -- optional, override which agent handles the command
- **model** -- optional, override which model to use
- **variables** -- declared parameters with optional defaults and descriptions

### Configuration Format

```text
# agents/main/commands.json

{
  "commands": [
    {
      "name": "daily-report",
      "description": "Generate morning briefing with emails, calendar, and tasks",
      "prompt": "Generate my daily morning report:\n1. Summarize unread emails (last 12 hours)\n2. List today's calendar events\n3. Show pending scheduled tasks\n4. Any memory items flagged as urgent\nFormat as a clean briefing with sections.",
      "icon": "newspaper"
    },
    {
      "name": "pr-review",
      "description": "Review a GitHub pull request",
      "prompt": "Review this pull request: {{url}}\n\nFocus on:\n- Security vulnerabilities\n- Performance implications\n- Code style and readability\n- Test coverage\n\nProvide specific line-level suggestions.",
      "variables": {
        "url": {
          "description": "GitHub PR URL",
          "required": true
        }
      },
      "icon": "git-branch"
    },
    {
      "name": "translate",
      "description": "Translate text to a target language",
      "prompt": "Translate the following text to {{language}}. Preserve formatting and tone.\n\nText:\n{{text}}",
      "variables": {
        "text": {
          "description": "Text to translate",
          "required": true
        },
        "language": {
          "description": "Target language",
          "default": "Spanish"
        }
      }
    },
    {
      "name": "server-check",
      "description": "Check health of all services",
      "prompt": "Check the status of all services:\n1. Brain Agent server (port 8420)\n2. QMD memory search (port 8181)\n3. oMLX inference (port 8000)\n4. CLIProxyAPI (port 8317)\n5. Cloudflare tunnel\nReport any issues and suggest fixes.",
      "agent": "main"
    },
    {
      "name": "standup",
      "description": "Generate standup update from recent activity",
      "prompt": "Generate my standup update based on:\n1. What I worked on yesterday (check recent chat history and memory)\n2. What I'm planning today (check scheduled tasks)\n3. Any blockers\nFormat as bullet points, keep it concise.",
      "model": "claude-sonnet-4-6"
    },
    {
      "name": "explain",
      "description": "Explain code in a file",
      "prompt": "Read and explain the code in {{file_path}}.\nFocus on:\n- What the code does at a high level\n- Key functions and their purposes\n- Any notable patterns or design decisions\n- Potential issues or improvements",
      "variables": {
        "file_path": {
          "description": "Path to the file to explain",
          "required": true
        }
      }
    }
  ]
}
```

### Variable Interpolation Syntax

Variables use double curly braces: `{{variable_name}}`

- **Required variables** -- user must provide a value; the command will not execute without
  it.
- **Optional variables with defaults** -- if the user omits them, the default value is
  substituted.
- **Inline syntax** -- `/pr-review https://github.com/org/repo/pull/42` fills `{{url}}`
  positionally.
- **Named syntax** -- `/translate --language=French --text="Hello world"` for explicit
  assignment.
- **Interactive prompt** -- if required variables are missing, show a popup/prompt asking
  for them.

### Resolution Order for Positional Arguments

When a user types `/pr-review https://github.com/...`, positional arguments are matched
to variables in the order they appear in the `variables` object. For named arguments,
`--variable=value` syntax takes precedence.

## Web UI Mockups

### Slash Command Popup with Custom Commands

```text
+-----------------------------------------------------------------------+
|  /                                                                    |
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  |  / Commands                                                   |   |
|  +---------------------------------------------------------------+   |
|  |                                                               |   |
|  |  BUILT-IN                                                     |   |
|  |  /agents         Switch or manage agents                      |   |
|  |  /schedule        Manage scheduled tasks                      |   |
|  |  /skills          Browse and install skills                   |   |
|  |  /memory          Search agent memory                         |   |
|  |  /teams           View team structure                         |   |
|  |                                                               |   |
|  |  CUSTOM                                              [Edit]   |   |
|  |  /daily-report    Generate morning briefing           Custom  |   |
|  |  /pr-review       Review a GitHub pull request        Custom  |   |
|  |  /translate       Translate text to a target lang     Custom  |   |
|  |  /server-check    Check health of all services        Custom  |   |
|  |  /standup         Generate standup update              Custom  |   |
|  |  /explain         Explain code in a file              Custom  |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Command with Variable Prompt

```text
+-----------------------------------------------------------------------+
|  /pr-review                                                           |
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  |  /pr-review                                                   |   |
|  +---------------------------------------------------------------+   |
|  |                                                               |   |
|  |  Review a GitHub pull request                                 |   |
|  |                                                               |   |
|  |  url (required):                                              |   |
|  |  +-------------------------------------------------------+   |   |
|  |  | https://github.com/org/repo/pull/42                    |   |   |
|  |  +-------------------------------------------------------+   |   |
|  |                                                               |   |
|  |                                        [ Cancel ] [ Run ]     |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Command Editor in Agent Config Modal

```text
+-----------------------------------------------------------------------+
|  Agent Config: main                                                   |
|  [Soul] [Settings] [Skills] [MCP] [Schedule] [Commands]              |
+-----------------------------------------------------------------------+
|                                                                       |
|  Custom Commands                                         [+ Add]      |
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | Name:        daily-report                                     |   |
|  | Description: Generate morning briefing with emails,           |   |
|  |              calendar, and tasks                               |   |
|  | Icon:        newspaper                                        |   |
|  |                                                               |   |
|  | Prompt:                                                       |   |
|  | +-----------------------------------------------------------+|   |
|  | |Generate my daily morning report:                          ||   |
|  | |1. Summarize unread emails (last 12 hours)                 ||   |
|  | |2. List today's calendar events                            ||   |
|  | |3. Show pending scheduled tasks                            ||   |
|  | |4. Any memory items flagged as urgent                      ||   |
|  | |Format as a clean briefing with sections.                  ||   |
|  | +-----------------------------------------------------------+|   |
|  |                                                               |   |
|  | Variables: (none)                                             |   |
|  | Agent override: (default)                                     |   |
|  | Model override: (default)                                     |   |
|  |                                                               |   |
|  |                              [Delete]  [Test]  [Save]         |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | Name:        pr-review                                        |   |
|  | Description: Review a GitHub pull request                     |   |
|  | Variables:   url (required)                [+ Add Variable]   |   |
|  |                              [Delete]  [Test]  [Save]         |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Variable Editor (Expanded)

```text
  +---------------------------------------------------------------+
  | Variable: url                                                  |
  +---------------------------------------------------------------+
  |  Name:         url                                             |
  |  Description:  GitHub PR URL                                   |
  |  Required:     [x]                                             |
  |  Default:      (empty)                                         |
  |  Placeholder:  https://github.com/org/repo/pull/123            |
  +---------------------------------------------------------------+
```

## TUI Mockups

### Custom Commands in Slash Completer

```text
> /

  Completions:
  ---------------------
  /agents          Manage agents
  /schedule        Manage scheduled tasks
  /skills          Browse skills
  /memory          Search memory
  /teams           Team structure
  ---------------------
  /daily-report    Generate morning briefing              [custom]
  /pr-review       Review a GitHub pull request           [custom]
  /translate       Translate text to a target language    [custom]
  /server-check    Check health of all services           [custom]
  /standup         Generate standup update                [custom]
  /explain         Explain code in a file                 [custom]
```

### Executing a Custom Command

```text
> /pr-review https://github.com/brain-agent/core/pull/87

  Expanding: /pr-review → Review this pull request:
  https://github.com/brain-agent/core/pull/87 ...

  [Coder] Reviewing PR #87...

  ## Security
  - Line 42 in auth.py: Raw SQL query without parameterization.
    Suggestion: Use parameterized query to prevent SQL injection.

  ## Performance
  - Line 118 in dashboard.py: Loading all users in a loop (N+1 query).
    Suggestion: Batch fetch with a single query.

  ## Style
  - Consistent use of f-strings, good.
  - Missing docstrings on 3 new functions.

  3 issues found (1 critical, 1 moderate, 1 minor).
```

### Missing Required Variable

```text
> /pr-review

  Command /pr-review requires:
    url: GitHub PR URL

  Usage: /pr-review <url>
  Example: /pr-review https://github.com/org/repo/pull/42
```

### Command with Default Variable

```text
> /translate "Hello, how are you?"

  Expanding: /translate → Translate to Spanish (default):
  "Hello, how are you?"

  [main] Hola, como estas?

> /translate --language=French "Hello, how are you?"

  Expanding: /translate → Translate to French:
  "Hello, how are you?"

  [main] Bonjour, comment allez-vous?
```

## Implementation Plan

### Phase 1: Core Engine (Days 1-2)

1. **Command loader** -- read `commands.json` from `agents/<name>/` directory at agent
   initialization. Merge with built-in commands.
2. **Variable interpolation engine** -- parse `{{var}}` placeholders, resolve positional
   and named arguments, prompt for missing required variables.
3. **Command execution** -- expand the prompt template with resolved variables and send
   as a regular chat message through the existing `/v1/chat` endpoint.
4. **Validation** -- check for duplicate command names (custom vs built-in), validate
   variable references in templates, enforce naming conventions.

### Phase 2: API Endpoints (Day 3)

1. `GET /v1/commands?agent=X` -- list custom commands for an agent.
2. `POST /v1/commands` -- create or update a custom command.
3. `DELETE /v1/commands` -- delete a custom command.
4. `POST /v1/commands/expand` -- preview a command expansion with given variables
   (for the "Test" button in the editor).

### Phase 3: Web UI (Days 3-5)

1. **Command popup integration** -- add "CUSTOM" section to the existing slash command
   picker popup, with a "Custom" badge on each entry.
2. **Variable prompt popup** -- when a command with required variables is selected, show
   a form popup collecting values before execution.
3. **Command editor** -- new "Commands" tab in the agent config modal with
   create/edit/delete/test functionality.
4. **Variable editor** -- inline variable definition with name, description, required
   flag, default value, and placeholder.

### Phase 4: TUI (Days 5-6)

1. **Completer integration** -- add custom commands to the existing slash command
   completer with `[custom]` labels.
2. **Argument parsing** -- parse positional and `--name=value` arguments from the
   command line.
3. **Missing variable prompt** -- show usage help when required variables are not
   provided.
4. **Expansion preview** -- briefly show the expanded prompt before sending.

### Phase 5: Polish (Day 7)

1. **Command sharing** -- import/export commands as JSON snippets.
2. **Global commands** -- commands defined in `agents/main/commands.json` are available
   to all agents (like global MCP servers).
3. **Telegram support** -- custom commands accessible via Telegram with `/cmd_name`
   syntax.
4. **Documentation and examples** -- ship default `commands.example.json` with useful
   templates.

## Sharing Commands Between Agents

Commands follow the same inheritance model as MCP servers and skills:

1. **Agent-specific commands** -- defined in `agents/<name>/commands.json`, only available
   to that agent.
2. **Global commands** -- defined in `agents/main/commands.json`, available to all agents
   unless overridden.
3. **Override behavior** -- if an agent defines a command with the same name as a global
   command, the agent-specific version takes precedence.
4. **Export/Import** -- commands can be exported as JSON and shared between agents or
   users. The Web UI command editor includes "Copy JSON" and "Import" buttons.

## Advanced Features (Future)

### Command Chaining

```text
{
  "name": "morning-routine",
  "description": "Full morning routine",
  "chain": ["daily-report", "server-check", "standup"],
  "prompt": "Run all three and combine into a single morning brief."
}
```

### Context-Aware Variables

```text
{
  "name": "review-current",
  "description": "Review code in the current directory",
  "prompt": "Review all modified files: {{git_diff}}",
  "variables": {
    "git_diff": {
      "source": "command",
      "command": "git diff --name-only"
    }
  }
}
```

### Conditional Prompts

```text
{
  "name": "deploy",
  "prompt": "{{#if environment == 'production'}}Run full test suite first, then {{/if}}Deploy to {{environment}}.",
  "variables": {
    "environment": {
      "default": "staging",
      "options": ["staging", "production"]
    }
  }
}
```

## Benefits

1. **Reduced friction** -- frequently used prompts become one-word commands instead of
   multi-line messages.
2. **Consistency** -- the same optimized prompt runs every time, avoiding the drift that
   happens when users retype from memory.
3. **Discoverability** -- custom commands appear in the slash popup, making agent
   capabilities visible to all users (especially useful for shared/team setups).
4. **Onboarding** -- new users can see what the agent can do by browsing the command list.
5. **Shareability** -- teams can share command libraries, ensuring everyone uses the same
   optimized prompts for common tasks.
6. **Low implementation cost** -- leverages the existing slash command infrastructure and
   chat endpoint; primarily a UI and template feature.

## Effort Estimate

| Component | Estimate |
|---|---|
| Command loader + parser | 1 day |
| Variable interpolation engine | 1 day |
| API endpoints | 0.5 day |
| Web UI (popup, editor, variable form) | 2 days |
| TUI (completer, arg parsing) | 1 day |
| Global commands + sharing | 0.5 day |
| Testing + polish | 1 day |
| **Total** | **~7 days** |

## Open Questions

1. Should custom commands support multi-turn conversations, or only single-message
   expansion? Proposal: start with single-message, add multi-turn later.
2. Maximum number of custom commands per agent? Proposal: no hard limit, but the popup
   should paginate or search-filter if there are many.
3. Should command names allow spaces or be restricted to kebab-case? Proposal: kebab-case
   only (`daily-report`, not `daily report`) to match the slash command convention.
4. Should there be a "recently used" section in the command popup? Proposal: yes, show
   last 3 used commands at the top.
5. How to handle command name conflicts between agents when using global commands?
   Proposal: agent-specific always wins, show a warning in the editor.
