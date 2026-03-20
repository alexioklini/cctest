# Brain Agent Roadmap

## Planned

Each item has a detailed feature proposal with mockups, user workflows, and effort estimates.

### Security & Control
- [Hooks system](features/hooks-system.md) — pre/post tool execution hooks (deterministic shell scripts) · ~6 days
- [Permissions model](features/permissions-model.md) — per-tool approval with allow/deny patterns, sandboxing · ~9 days
- [Plan mode](features/plan-mode.md) — read-only analysis mode that disables write tools · ~5 days

### Infrastructure & Deployment
- [Provider fallback chains](features/provider-fallback.md) — ordered fallback with exponential backoff retry · ~8 days
- [Docker deployment](features/docker-deployment.md) — Dockerfile + docker-compose for cross-platform usage · ~5 days
- [Discord + Slack adapters](features/discord-slack-adapters.md) — expand beyond Web/TUI/Telegram · ~10 days

### User Features
- [Agent workflows](features/agent-workflows.md) — multi-step blueprints with approval gates and branching · ~15 days
- [Custom slash commands](features/custom-slash-commands.md) — user-defined commands with prompt templates · ~7 days
- [LLM-assisted input refinement](features/llm-input-refinement.md) — AI-powered text improvement in all inputs · ~5 days

### Platform
- [Web UI optimization](features/webui-optimization.md) — collapsible left sidebar, remove redundant elements · ~6 days
- [Self-awareness memory](features/self-awareness-memory.md) — teach agents about their own architecture · ~3 days
- [MCP client support](features/mcp-client-support.md) — dynamic MCP server connections at runtime · ~8 days
- [A2A protocol](features/a2a-protocol.md) — Google Agent-to-Agent for cross-system interop · ~12 days

## Done (Research)

- Gap analysis vs Claude Code / Cowork → [gap-analysis-claude-code.md](gap-analysis-claude-code.md)
- Gap analysis vs OpenClaw / OpenFang → [gap-analysis-openclaw-openfang.md](gap-analysis-openclaw-openfang.md)

## In Progress

## Completed

- v1.6.0 — TUI feature parity (30+ slash commands), slash command popup menus in TUI and Web UI
- v1.5.3 — Thread-safe agent context, provider resolution fix, memory robustness, concurrent scheduler
- v1.5.2 — Memory summary direct execution, QMD index path normalization, collection health stats
- v1.5.1 — MiniMax provider, Add Model UI, QMD session leak fix, in-process Telegram
- v1.5.0 — Settings dashboard, agent activity indicators, QMD document browser, smart model routing
- v1.4.0 — QMD hybrid memory search, SSE error handling, server resilience
- v1.2.0 — Multi-provider routing, Gmail, scheduler dashboard, SQLite resilience, Cloudflare deployment
- v1.1.0 — MCP support, Web UI, chat history, skill browser, avatars
- v1.0.0 — Client-server architecture, Telegram bot, background tasks, scheduler
