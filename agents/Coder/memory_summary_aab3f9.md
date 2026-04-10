---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: Coder
related:
  - file: memory_summary_health_reports_input_dca3fb.md
    type: same_topic
  - file: benchmark_vs_memory_architecture_9e0504.md
    type: references
  - file: minimax-m27-vs-opus-46-coding.md
    type: references
---

# Memory Summary — Coder Agent
_Last updated: 2026-04-06_

## User Profile & Context
The user (Alexander) is a software engineer building a full-stack AI chat platform (cctest project). They work across infrastructure, backend, and frontend, with deep involvement in LLM provider integration, streaming protocols, and agent orchestration using the Claude Agent SDK / Brain Agent platform.

## Technical Preferences
- **Languages/Stack**: Python, TypeScript/JavaScript; REST APIs, SSE streaming
- **LLM Providers**: Anthropic (Claude), oMLX (local, port 8000, Crow-4B, anthropic API type), CLIProxyAPI (shares Claude's 5-hour quota), MiniMax, Mistral (Pro subscription via SDK)
- **Architecture**: SDK sidecar pattern for agent execution; server-side hooks for streaming; REST sidecar must never import `claude_cli` to avoid anyio streaming breakage
- **Conventions**: Avoid interactive tool loops that can exhaust CLIProxyAPI quota; user-triggered actions execute directly, not via scheduler indirection

## Active Projects & Ongoing Work
### cctest — AI Chat Platform
- **Status**: All original roadmap items complete; major additions through 2026-03-24
- **Key architecture**: Cloudflare tunnel, server daemon, multi-provider LLM routing
- **SDK migration**: 7-phase plan in progress to close gaps (MCP server, summaries, file watcher, background tasks, hooks)
- **Open backlog**:
  - Tool results not shown in chat UI (blocked by SDK hooks killing streaming)
  - Provider/model management UI flaky (needs multiple attempts)
  - SSE stream hangs when thinking param sent via SDK sidecar (server-side bug)

## Task Execution Insights
### Scheduled Tasks — Operational Pattern
- **_relationship_discovery_Coder** runs daily at 04:15; consistently completes in 11–41 seconds
- Each run discovers and stores 2–7 cross-reference relationships between memory documents
- **Recurring error**: `ValueError: unknown url type: '/messages'` — a duplicate task instance fires alongside the successful one (likely a scheduling artifact); the error instance runs 0s with no tools and can be ignored
- Relationship discovery focuses on: SDK streaming ↔ SDK gap plan, memory health reports (sequential chain), skills comparison ↔ memory architecture, coder agent task patterns

## Key Decisions & Context
- **Artifacts UI**: Must match Claude.ai design; always verify in Chrome
- **SDK streaming**: SDK hooks cause buffering; solution is REST sidecar + server-side hooks
- **oMLX provider type**: Uses `anthropic` API type (not `openai`) — important for provider routing logic
- **CLIProxyAPI quota**: Shared with Claude's 5-hour quota; runaway tool loops are a real risk
- **Sidecar constraint**: SDK sidecar must never import `claude_cli` — breaks anyio streaming
- **Mistral provider**: Type string is `"mistral"`, replicates Vibe CLI behavior for Pro key

## Communication & Working Style
- Prefers concise, actionable responses
- Values direct execution over indirection
- Expects architectural decisions to be documented in memory for persistence across sessions

