# Feature Proposal: Self-Awareness Memory

**Status:** Proposed
**Priority:** High
**Effort:** Medium (2-3 days)
**Affects:** `claude_cli.py`, `agents/main/`, scheduled tasks

---

## Problem

When users ask Brain Agent about itself -- its architecture, configuration, tools,
or troubleshooting -- the main agent does not know the answers. It has no memory of
its own codebase, configuration structure, or operational details.

### Current Behavior (Before)

```text
User: How do I add a new LLM provider?

main: To add a new provider, you would typically need to modify your
      configuration file. I believe you should add the provider's API
      endpoint and key to a settings file. The exact format would
      depend on your setup...

      [vague, hedging, partially wrong]
```

The agent guesses because:

1. `CLAUDE.md` and `README.md` are development docs for Claude Code, not loaded into
   the agent's runtime context
2. `tools.md` describes tool usage but not system architecture or configuration
3. `soul.md` defines personality but not operational knowledge
4. No memory files contain self-documentation

Users expect Brain Agent to know itself. When it cannot answer "what tools do you
have?" or "how do I configure Telegram?", it feels broken despite being fully
functional.

---

## Proposed Solution

Generate comprehensive self-documentation as markdown memory files in the main
agent's directory. These files are automatically indexed by QMD and searchable
via `memory_recall`, giving the agent accurate knowledge about itself.

### Memory Files to Create

```text
agents/main/
  _self/                          # Self-documentation directory
    architecture.md               # System architecture, components, data flow
    configuration-guide.md        # How to configure providers, Telegram, agents
    tools-reference.md            # All tools with parameters, examples, tips
    troubleshooting.md            # Common issues and solutions
    api-reference.md              # Server API endpoints with request/response
    agent-management.md           # Creating, configuring, teams, delegation
```

The `_self/` prefix groups these files together and signals they are auto-generated.
QMD indexes all `.md` files in the agent directory, so these are immediately
searchable.

### Expected Behavior (After)

```text
User: How do I add a new LLM provider?

main: [memory_recall: "add new LLM provider configuration"]
      -> Retrieves from _self/configuration-guide.md

main: To add a new provider, edit config.json and add an entry under
      "providers":

      {
        "providers": {
          "my-provider": {
            "base_url": "https://api.example.com/v1",
            "api_key": "your-key-here",
            "type": "openai",
            "default_model": "model-name"
          }
        }
      }

      Set "type" to "openai" for OpenAI-compatible APIs or "anthropic"
      for Anthropic-native APIs. The server auto-routes to the correct
      provider based on which model you select.

      You can also add providers through the Web UI: Settings ->
      Providers tab -> Add Provider button. Test the connection before
      saving.

      [accurate, specific, actionable]
```

### Example Conversations: Before vs After

```text
+------------------------------------------------------------------+
| BEFORE (no self-knowledge)                                        |
+------------------------------------------------------------------+
|                                                                    |
| User: What tools do you have?                                      |
|                                                                    |
| main: I have various tools available to help you with tasks.       |
|       I can search the web, manage files, and more. What would     |
|       you like help with?                                          |
|                                                                    |
|       [unhelpful, avoids specifics]                                |
|                                                                    |
+------------------------------------------------------------------+
| AFTER (self-awareness memory)                                      |
+------------------------------------------------------------------+
|                                                                    |
| User: What tools do you have?                                      |
|                                                                    |
| main: [memory_recall: "available tools list"]                      |
|                                                                    |
| main: I have 20+ built-in tools organized into categories:        |
|                                                                    |
|   File operations: read_file, write_file, edit_file,              |
|   list_directory, search_files                                     |
|                                                                    |
|   Shell: execute_command (non-interactive only)                    |
|                                                                    |
|   Web: web_fetch, exa_search                                      |
|                                                                    |
|   Gmail: gmail_inbox, gmail_read, gmail_search, gmail_send,       |
|   gmail_reply                                                      |
|                                                                    |
|   Memory: memory_store, memory_recall, memory_shared,             |
|   memory_delete                                                    |
|                                                                    |
|   Agents: delegate_task, task_status, task_cancel                  |
|                                                                    |
|   Skills: use_skill (loads from 7000+ available skills)            |
|                                                                    |
|   Schedule: schedule_list, schedule_history                        |
|                                                                    |
|   Plus any tools from connected MCP servers.                       |
|                                                                    |
|       [complete, accurate, organized]                              |
|                                                                    |
+------------------------------------------------------------------+
```

### Auto-Generation Workflow

```text
                    Source Files                    Generated Memory
                    ============                    ================

  CLAUDE.md ----+
                |
  README.md ----+---> /self-init command ---> agents/main/_self/
                |     (or scheduled task)       architecture.md
  config.json --+                               configuration-guide.md
                |                               tools-reference.md
  tools.md -----+                               troubleshooting.md
                |                               api-reference.md
  soul.md ------+                               agent-management.md
                                                      |
                                                      v
                                                 QMD indexes
                                                 automatically
                                                      |
                                                      v
                                                 memory_recall
                                                 finds answers
```

### Step-by-Step Workflow

1. **Initial setup**: User runs `/self-init` command (or it runs automatically on
   first server start)

```text
User: /self-init

main: Generating self-documentation from source files...

      Reading CLAUDE.md...          done
      Reading README.md...          done
      Reading config.json schema... done
      Reading tools.md...           done
      Reading agent configs...      done

      Writing architecture.md...           done (142 lines)
      Writing configuration-guide.md...    done (198 lines)
      Writing tools-reference.md...        done (267 lines)
      Writing troubleshooting.md...        done (156 lines)
      Writing api-reference.md...          done (189 lines)
      Writing agent-management.md...       done (134 lines)

      QMD indexing...               done (6 files, 1086 lines)

      Self-documentation complete. I now know about my own
      architecture, configuration, tools, and API.
```

2. **User asks a question**: Agent recalls from its own memory

```text
User: How do I set up the Telegram bot?

main: [memory_recall: "Telegram bot setup configuration"]
      -> Hits _self/configuration-guide.md, Telegram section

main: To set up the Telegram bot:

      1. Get a bot token from @BotFather on Telegram
      2. Add to config.json:
         "telegram": {
           "bot_token": "123456:ABC...",
           "allowed_users": [your_telegram_user_id]
         }
      3. Start the bot: python3 brain.py telegram
         Or enable in Web UI: Settings -> Telegram -> Start

      The bot runs as an in-process thread inside the server.
      Get your user ID by messaging @userinfobot on Telegram.
```

3. **Scheduled auto-update**: A daily task regenerates docs if source files changed

```text
Scheduled task: self-doc-refresh
Schedule:       daily 04:00
Agent:          main
Prompt:         Check if CLAUDE.md, README.md, or tools.md have
                changed since last self-doc generation. If yes,
                regenerate the affected _self/ files.
```

### Auto-Update Mechanism

```text
+-- Daily at 04:00 -------------------------------------------+
|                                                              |
|  1. Read mtime of source files:                              |
|     - CLAUDE.md, README.md, tools.md, config.json            |
|                                                              |
|  2. Compare with stored mtimes in _self/.meta.json:          |
|     {                                                        |
|       "last_generated": "2026-03-20T04:00:00",              |
|       "sources": {                                           |
|         "CLAUDE.md": "2026-03-19T15:30:00",                 |
|         "README.md": "2026-03-18T10:00:00",                 |
|         ...                                                  |
|       }                                                      |
|     }                                                        |
|                                                              |
|  3. If any source changed:                                   |
|     - Re-read changed sources                                |
|     - Regenerate affected _self/ files                       |
|     - Update .meta.json                                      |
|     - QMD auto-indexes on file change                        |
|                                                              |
|  4. If nothing changed: skip (no wasted LLM calls)           |
|                                                              |
+--------------------------------------------------------------+
```

---

## Implementation Plan

### Phase 1: Generator Script (Day 1)

1. Add `_generate_self_docs()` function to `claude_cli.py`
2. Reads CLAUDE.md, README.md, tools.md, config.json (schema only, no secrets)
3. Generates structured markdown files in `agents/main/_self/`
4. Each file has YAML frontmatter: `title`, `generated_from`, `generated_at`
5. Writes `.meta.json` with source file mtimes for change detection

### Phase 2: Integration (Day 2)

1. Add `/self-init` as a recognized slash command in Web UI and TUI
2. On first server start (no `_self/` directory exists), prompt user to run init
3. Add `self-doc-refresh` scheduled task template
4. Ensure QMD indexes `_self/` subdirectory (already does -- all `.md` files)

### Phase 3: Content Quality (Day 2-3)

1. Write high-quality templates for each doc file
2. Architecture doc: component diagram, data flow, key patterns
3. Configuration guide: every config.json field with defaults and examples
4. Tools reference: every tool with parameters, return values, gotchas
5. Troubleshooting: common error messages with solutions
6. API reference: every endpoint with curl examples
7. Agent management: creation, teams, delegation, memory, skills

### Phase 4: Refresh Mechanism (Day 3)

1. Implement mtime-based change detection
2. Create scheduled task that runs `_generate_self_docs(check_only=True)`
3. Only regenerate files whose sources changed
4. Log refresh activity to scheduler history

---

## What to Include vs Exclude

### Include (safe, useful)

- Architecture and component relationships
- Config.json field names, types, defaults, and examples
- Tool names, parameters, and usage patterns
- API endpoint paths, methods, and response formats
- Agent creation and management workflows
- Troubleshooting steps for common errors
- Service management (start/stop/restart)
- MCP configuration format
- Gmail setup steps (generic, no credentials)
- Scheduler syntax and examples

### Exclude (sensitive or volatile)

- Actual API keys, tokens, passwords
- Specific IP addresses and hostnames (use placeholders)
- User email addresses
- Telegram user IDs
- File contents of gmail.json
- Actual chat history or memory content
- Internal implementation details that change frequently

---

## Memory File Structure

Each generated file follows this format:

```text
---
title: Configuration Guide
generated_from: [CLAUDE.md, README.md, config.json]
generated_at: 2026-03-20T04:00:00
auto_generated: true
---

# Configuration Guide

This document describes how to configure Brain Agent. It is
auto-generated from the project's source documentation.

## Provider Configuration

To add a new LLM provider, edit config.json...

[... structured content ...]
```

The `auto_generated: true` frontmatter flag lets the system know these files can be
safely overwritten during refresh. Manual edits to auto-generated files will be
preserved until the next refresh cycle.

---

## Benefits

- **Accurate answers** -- agent responds with verified information from its own docs
  instead of hallucinating configuration syntax or tool names
- **Self-maintaining** -- scheduled task keeps docs current as the codebase evolves
- **No runtime cost** -- docs are pre-generated markdown, recalled via existing
  memory_recall mechanism. No extra LLM calls at query time.
- **Leverages existing infra** -- QMD hybrid search, memory_recall, scheduled tasks
  are all already built. This feature composes them.
- **User trust** -- when the agent knows itself, users trust it for other tasks too
- **Onboarding** -- new users can ask the agent how to use it instead of reading docs

## Trade-offs

- **Storage** -- ~6 markdown files, roughly 1000-1500 lines total. Negligible.
- **Generation cost** -- initial generation requires reading source files and
  formatting. One-time cost, minimal ongoing (only on source changes).
- **Staleness risk** -- if source files change and the scheduled task fails or is
  disabled, docs become stale. Mitigated by mtime checking and a manual
  `/self-init` command.
- **Scope creep** -- temptation to document everything. Keep it focused on what
  users actually ask: configuration, tools, troubleshooting. Not internals.

## Dependencies

- QMD must be running (for indexing and recall)
- Scheduled task system (for auto-refresh)
- No new libraries or external services needed

## Future Extensions

- **Per-agent self-docs** -- each agent generates docs about its own capabilities,
  skills, and MCP connections
- **Interactive help** -- `/help providers` command that directly surfaces the
  relevant section from self-docs
- **Version tracking** -- track which version of source docs generated each
  self-doc, show diff on refresh
- **User-contributed FAQ** -- users can flag questions the agent answered poorly,
  and those get added to troubleshooting.md
