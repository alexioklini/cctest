---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: Crow4B
related:
  - file: memory_relationship_discovery_crow4b_2026-04-06_eb5692.md
    type: same_topic
---

## User Profile & Context
Alexander is a developer building and maintaining a sophisticated AI agent platform ("Brain Agent" / cctest project). He operates as both the architect and primary user, managing LLM infrastructure, agent orchestration, and frontend UI. He has deep knowledge of the Anthropic SDK, Claude CLI, and local inference setups.

## Communication & Working Style
Prefers direct, structured responses with clear decisions over exploratory commentary. Expects concise answers and actionable outcomes. Dislikes indirection — user-triggered actions must execute directly, not via scheduler. Values precision in tool usage and architectural consistency.

## Technical Preferences
- **Languages/Stack:** Python (backend), likely TypeScript/JS (frontend); uses Claude Agent SDK and Anthropic API
- **LLM Providers:** oMLX (local, port 8000, Crow-4B model, anthropic API type), CLIProxyAPI (shares Claude's 5-hour quota — avoid runaway loops), MiniMax, Mistral (Pro subscription via Vibe CLI replication)
- **Infrastructure:** Server daemon + Cloudflare tunnel; scheduled tasks via Brain Agent platform
- **Critical constraint:** SDK sidecar must NEVER import `claude_cli` (breaks anyio streaming)
- **Artifact UI:** Must match Claude.ai design; verify in Chrome

## Active Projects & Ongoing Work
**Brain Agent Platform (cctest):**
- All original roadmap items complete + major additions through 2026-03-24
- SDK migration gaps being addressed via 7-phase plan (MCP server, summaries, file watcher, background tasks, hooks)
- Open backlog: tool results not shown in chat UI (blocked by SDK hooks killing streaming); provider/model management UI is flaky

**Known Bugs:**
- SSE stream hangs when thinking param sent via SDK sidecar (server-side)
- `_relationship_discovery_Crow4B` task intermittently fails with `ValueError: unknown url type: '/messages'` — likely a misconfigured base URL in urllib when the scheduler fires; succeeds on retry

## Task Execution Insights
- `_relationship_discovery_Crow4B` has a recurring startup error: `ValueError: unknown url type: '/messages'` on first attempt each day, succeeds on second attempt (~83s). This suggests a race condition or initialization issue with the URL configuration at task start.
- Relationship discovery (when successful) correctly identifies Crow4B's dependency on memory system tools and the scheduler infrastructure.
- No user conversations in the current period — all activity is automated scheduled tasks.

## Key Decisions & Context
- oMLX uses `anthropic` API type (not `openai`) — critical for provider config
- REST sidecar + server-side hooks required to avoid streaming buffering from SDK hooks
- CLIProxyAPI quota is shared with Claude's 5-hour window; tool loops can exhaust it — keep automated tasks lean
- Crow4B is the local inference model (4B params, Qwen 3.5 base, distilled from Opus 4.6), used as the agent executing these scheduled tasks

