---
title: "### Tools (30+)"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:37:55.250034+00:00"
chunk_index: 4
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-003.md
    type: prev_chunk
  - file: ingest-6ebdb6-005.md
    type: next_chunk
  - file: ingest-6ebdb6-000.md
    type: same_source
---

### Tools (30+)


-

`HookRunner`

loads

hooks

from

`agent.json`

`hooks.scripts[]`,

runs

via

subprocess

with

env

vars

+

stdin

JSON

-

Hooks

timeout

(default

5s),

fail-open

on

crash,

exit

1

=

block

(pre)

or

error

(post),

exit

2

=

skip

chain

-

`_after_file_write()`

centralizes

QMD

reindex

+

entity

extraction

+

KG

update

+

file

events

+

external

hooks

-

`_execute_tool()`

orchestrates:

built-in

pre

→

external

pre

→

execute

→

built-in

post

→

external

post

-

Workflow

`allowed_tools`

restriction

now

enforced

(was

dead

code)

-

Hook

runners

cached

per

agent,

invalidated

on

config

save

-

GET/POST

`/v1/agents/{id}/hooks`

for

hook

management

-

Compaction

sends

SSE

events

(`compacting`,

`compacted`)

for

spinner

feedback

- Universal File Intelligence: DocumentParser extended with parse_xlsx, parse_pptx, parse_csv, parse_image, parse_svg
- read_document: format-aware reader dispatching by extension (PDF pages, XLSX sheets, PPTX slides, images, CSV)
- write_document: markdown → DOCX/XLSX/PPTX/PDF conversion
- edit_document: targeted edits (DOCX replace_text, XLSX update_cell/add_row, PPTX update_slide/add_slide)
- IngestManager auto-handles all new formats via DocumentParser.parse() dispatch
- Code Structure Graph: `CodeGraph` class with Tree-sitter AST parsing, SQLite storage (`code-graph.db`)
- 14 languages: Python, JS, TS, TSX, Go, Rust, Java, C, C++, C#, Ruby, Kotlin, Swift, PHP
- Qualified names: `{file_path}::{ClassName.method}`, edges: CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY
- `code_graph_build`: parse directory, `code_graph_query`: 8 query types, `code_graph_impact`: BFS blast-radius via NetworkX
- `_maybe_update_code_graph(path)` in `_after_file_write()` for incremental updates on source file changes
- Incremental builds: SHA-256 file hash skip, re-parse only changed + dependent files
- `code_graph_enhance` tool: generate LLM summaries, classify architecture layers, generate guided tour
- Node summaries: one-line LLM descriptions per function/class (batched by file, uses Haiku)
- Architecture layers: api/service/data/ui/util/test classification via path+name pattern matching
- Guided tour: dependency-ordered walkthrough with layer grouping, key classes, reading order
- Lossless compaction: `compacted` column on messages — originals preserved for search, compacted set used for conversation
- Context fill indicator in footer: token estimate / max context with color-coded progress bar
- Manual compact button + LCM badge in footer
- Session corruption fix: `_rollback_messages()` reverts all intermediate tool-loop messages on failure
- Partial response preservation: on cancel/error, save streamed text + tools to chat history
- Message metadata: model, tokens, cost, tools, thinking persisted per assistant message, restored on load
- `augmented_messages` strips metadata fields (only role+content sent to API) — prevents 400 errors
- Extended thinking: `thinking` param in inference_params, budget levels (low=2K, med=8K, high=32K)
- Thinking blocks: `content_block_start/delta/stop` with `thinking_delta` + `signature_delta` capture
- Thinking blocks preserved in conversation history (required by Anthropic API for tool loops)
- Thinking UI: collapsible purple block in chat, "Thinking deeply..." spinner, persisted in metadata
- Model display: shown in inline thinking indicator + spinner bar, updates on fallback
- Remote node badge: purple pill on tool blocks when `node` param present
- Resizable sidebars: drag handles on right edge of left sidebar, left edge of project panel, persisted to localStorage

### Agent Teams

Agents can be organized into teams with a hierarchical delegation model:

- **Team head**: An agent with a `team` field in `agent.json` containing `members` list
- **Team members**: Agents listed in a team head's `team.members` array
- **Standalone agents**: Agents not in any team (excluding main)
- **main**: Global orchestrator, never has a `team` field

```json
// Example team head agent.json
{
  "description": "Research team lead",
  "display_name": "Research Lead",
  "model": "claude-sonnet-4-6",
  "team": {
    "name": "Research Team",
    "description": "Handles research and analysis tasks",
    "avatar": "🔬",
    "members": ["Researcher", "crow"]
  }
}
```

**Delegation scoping:**
- `main` → team heads + standalone agents (not members directly)
- Team heads → their team members
- Team members → peers in same team + their team head

**Shared memory scoping:**
- `memory_shared(scope="global")` → main agent's memory (default)
- `memory_shared(scope="team")` → team head's memory

**API:**
- `GET /v1/teams` — team structure
- `POST /v1/teams` — create/update/dissolve/move teams

### Agent Directory Structure

```
agents/<name>/
  soul.md         # Personality, role, instructions
  agent.json      # {description, display_name, model, avatar, max_context, paused}
  tools.md        # Optional per-agent tool guide
  mcp.json        # MCP server connections
  gmail.json      # Gmail credentials (not in git)
  skills/         # Installed skills (SKILL.md per skill)
  *.md            # Memory files (indexed by QMD)
  chats.db        # Chat history (in main agent dir)
  scheduler.db    # Scheduled tasks (in main agent dir)
```

### Tools (30+)
