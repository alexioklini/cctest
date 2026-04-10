---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: Minimax
---

## User Profile & Context
The user (Alexander) is a developer building a sophisticated multi-agent AI platform with a custom Claude Code harness. The project involves local LLM inference, multiple AI providers, and a rich chat UI. Alexander works at an advanced level, managing infrastructure, SDK integrations, and agent orchestration simultaneously.

## Communication & Working Style
No direct conversations available in this period. Based on project context, Alexander prefers direct, technical responses with clear actionable outcomes. Feedback is captured in dedicated memory files indicating a structured, iterative working style.

## Technical Preferences
- **Languages/Stack:** Python (Claude Agent SDK, Anthropic SDK), JavaScript/TypeScript (UI)
- **Architecture:** Multi-agent system with scheduled background tasks, REST sidecar pattern, Cloudflare tunnel for remote access
- **LLM Providers:** oMLX (local, port 8000, Crow-4B), CLIProxyAPI (Claude quota-sharing), MiniMax (MiniMax-M2.7 Coder), Mistral (Pro subscription)
- **Critical constraint:** SDK sidecar must NEVER import `claude_cli` — breaks anyio streaming
- **Streaming:** REST sidecar + server-side hooks required to avoid SDK hook buffering issues

## Active Projects & Ongoing Work
**cctest — Claude Code Harness Platform**
- All original roadmap items complete; major additions through 2026-03-24
- SDK gap closure in progress: 7-phase plan covering MCP server, summaries, file watcher, background tasks, hooks
- Known bugs: SSE stream hangs with thinking param via SDK sidecar; tool results not shown in chat UI (blocked by SDK hooks)
- Provider/model management UI is flaky

**Minimax Agent Role**
- Runs as the MiniMax-M2.7 Coder agent within the roster
- Executes scheduled tasks: `_memory_summary_Minimax` and `_relationship_discovery_Minimax` (daily at 04:15)

## Task Execution Insights
- **_relationship_discovery_Minimax:** Intermittent failures with `ValueError: unknown url type: '/messages'` — occurs on roughly every other run (2026-04-06 failed, 2026-04-05 succeeded, 2026-04-05 earlier run failed). The error originates in Python's urllib at the agent harness level, suggesting a misconfigured base URL or endpoint construction issue in the scheduler/agent runtime. Successful runs complete in ~41 seconds.
- **Pattern:** The duplicate task entries on 2026-04-05 (one error, one success) suggest the scheduler may retry on failure or run duplicate instances.
- **Recommendation:** Investigate URL construction for the `/messages` endpoint in the agent runner — likely a missing base URL or malformed concatenation when the task fires.

## Key Decisions & Context
- CLIProxyAPI shares Claude's 5-hour quota — runaway tool loops must be avoided
- Artifact panel UI must match Claude.ai design; always verify in Chrome
- User-triggered actions must execute directly, not via scheduler indirection
- oMLX uses Anthropic API type (not OpenAI)
- Mistral provider replicates Vibe CLI behavior for Pro subscription key

*Last updated: 2026-04-06 04:20*

