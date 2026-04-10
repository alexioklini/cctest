---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: CROW_9B
---

# Memory Summary — CROW_9B
*Last updated: 2026-04-06*

## User Profile & Context
Alexander is a developer building a sophisticated local AI assistant platform (cctest project). He runs a multi-agent system using the Claude Agent SDK, with local inference via oMLX (Crow-4B on port 8000), Cloudflare tunnel for remote access, and multiple LLM providers (CLIProxyAPI, MiniMax, Mistral). He has deep knowledge of the stack and makes precise architectural decisions.

## Communication & Working Style
Prefers direct, concrete answers without fluff. Tracks bugs and blockers meticulously via structured memory files. Uses scheduled agents (CROW_9B and others) for autonomous maintenance tasks. Does not need explanations of basics — communicate at peer level.

## Technical Preferences
- Local-first inference with oMLX (anthropic API type, not openai)
- Claude Agent SDK for agent orchestration; never import claude_cli in SDK sidecars (breaks anyio streaming)
- REST sidecar + server-side hooks preferred over SDK hooks (SDK hooks cause streaming buffering)
- Mistral SDK provider type: "mistral"
- CLIProxyAPI shares Claude's 5-hour quota — avoid runaway tool loops
- Artifact panel UI must match Claude.ai design; verify in Chrome

## Active Projects & Ongoing Work
**cctest** — local AI assistant platform. All original roadmap items completed through 2026-03-24, plus major additions. Current open items:
- SDK gap closure: 7-phase plan (MCP server, summaries, file watcher, background tasks, hooks)
- Known bugs: SSE stream hangs with thinking param via SDK sidecar; tool results not shown in chat UI (blocked by SDK hooks/streaming issue)
- Backlog: provider/model management UI is flaky

## Task Execution Insights
- `_relationship_discovery_CROW_9B` fails consistently on first attempt with `ValueError: unknown url type: '/messages'` — this is a transient startup/initialization error. The task succeeds on retry (84s duration). Pattern: schedule two attempts or add retry logic.
- Relationship discovery (2026-04-05 success) identified 40+ memories, 4 relationship clusters: memory health report chain, shell environment fix, and others. System reports 0% failure rate and no data conflicts across agent roster.
- No conversation activity in the last 48h — agent is operating in autonomous maintenance mode only.

## Key Decisions & Context
- User-triggered actions must execute directly, never via scheduler indirection
- Daily memory health reports form a temporal validation chain confirming system stability
- oMLX uses anthropic API type — do not assume openai compatibility
- CLIProxyAPI quota exhaustion is a real risk; tool loops must be bounded

