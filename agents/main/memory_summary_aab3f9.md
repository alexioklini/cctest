---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: main
related:
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: claude-code-version-path_-_shell-env-fix-2025_224b40.md
    type: same_topic
  - file: dev-workflow-feedback_af2401.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: reporter-agent-summary_a4d7cf.md
    type: same_topic
last_recalled: 2026-04-08
---

# Memory Summary
*Last updated: 2026-04-06*

## User Profile & Context
The user (Alexander) is a developer working on a self-hosted AI assistant platform called **Brain Agent** — a multi-agent system built on Claude SDK with persistent memory, scheduled tasks, and a custom UI. The project is deployed on a Mac Studio M2 Max (32GB unified memory). Alexander has strong interest in local LLM inference (MLX/oMLX), Mistral AI models, and infrastructure automation. He communicates in both German and English depending on context.

## Communication & Working Style
- Switches between German and English freely; responds well to bilingual replies when context warrants
- Prefers concise, actionable answers with clear structure (tables, headers)
- Asks follow-up questions to drill deeper into technical topics (e.g., from general Mistral overview → Devstral specifics → local hosting → hardware benchmarks)
- Casual conversation is welcome (greetings, jokes, off-topic questions handled lightly)

## Technical Preferences
- **Local inference**: MLX on Apple Silicon preferred; Mac Studio M2 Max (32GB) is primary inference hardware
- **Mistral/Devstral**: Active interest in Devstral Small 2 (24B) for local coding agent use cases via MLX
- **Infrastructure**: Cloudflare tunnel, custom daemon, CLIProxyAPI, oMLX server (port 8000, Crow-4B)
- **SDK**: Claude Agent SDK (Python); sidecar must NOT import claude_cli (breaks anyio streaming)
- **Providers**: Mistral (Pro subscription), oMLX (anthropic API type), MiniMax, CLIProxyAPI

## Active Projects & Ongoing Work
**Brain Agent** — self-hosted multi-agent AI assistant:
- All original roadmap milestones completed through 2026-03-24
- SDK migration gaps documented (7-phase plan): MCP server, summaries, file watcher, background tasks, hooks
- Artifact panel UI must match Claude.ai design (verify in Chrome)
- REST sidecar + server-side hooks required to avoid streaming buffering from SDK hooks
- Tool results not yet shown in chat UI (blocked by SDK hooks issue)
- Provider/model management UI is flaky (needs multiple attempts)
- Thinking param via SDK sidecar causes SSE stream hangs (server-side bug, backlogged)

## Task Execution Insights
- **_relationship_discovery_main** runs daily at 04:15 — consistently errors on one run with `ValueError: unknown url type: '/messages'`, then succeeds on the second attempt. This is a recurring infrastructure issue (likely a URL configuration problem in the scheduled task runner or MCP client). The error does not block operation but wastes one execution slot.
- Scheduled tasks complete quickly (4-5s) when successful, suggesting lightweight tool usage.

## Key Decisions & Context
- CLIProxyAPI shares Claude's 5-hour quota — runaway tool loops can exhaust it; avoid aggressive polling
- oMLX uses `anthropic` API type (not `openai`) — important for provider configuration
- User-triggered actions must execute directly, not via scheduler indirection
- Devstral Small 2 on MLX (M2 Max 32GB): expected ~8–15 tokens/sec generation at Q4 quantization — viable for local coding agent workflows
- Jalousie/blind motor repair: user asked about manually removing slats from a broken motorized outdoor blind (unrelated personal task, low priority context)
