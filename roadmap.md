# Brain Agent Roadmap

## Planned

- Optimize Web UI screen space — smaller agent cards or move agents to a collapsible left sidebar menu
- Custom slash commands — user-defined commands (like Claude Code skills) that can be configured per-agent and trigger predefined prompts or tool sequences
- Clean up redundant Web UI elements — remove duplicate current agent display (shown in both middle area and status bar), consolidate status information
- Agent workflows — define custom multi-step task blueprints for agents with sequential/parallel stages, approval gates (from other agents or the user), conditional branching, and reusable templates
- LLM-assisted input refinement — option to refine/improve user text via LLM before sending, available in chat input, all text boxes, and MD editors (soul.md, memory files, etc.)
- Self-awareness memory — store Brain Agent's own architecture, inner workings, config structure, and capabilities in main agent's memory so it can help users with setup, troubleshooting, and configuration questions
- MCP client support for agents — allow agents to initiate MCP client connections to external MCP servers, enabling dynamic tool discovery and cross-system integration
- Hooks system — pre/post tool execution hooks (deterministic shell scripts), critical gap vs Claude Code
- Permissions model — per-tool approval with allow/deny patterns at engine level, sandboxing for execute_command
- Plan mode — read-only analysis mode that disables write tools
- Provider fallback chains — ordered fallback with exponential backoff retry (gap vs OpenClaw)
- Docker deployment — Dockerfile for cross-platform usage
- Discord + Slack adapters — expand beyond Web/TUI/Telegram (gap vs OpenClaw 12+ / OpenFang 40 channels)
- A2A protocol — Google Agent-to-Agent for cross-system agent interop (gap vs OpenFang)

## Done (Research)

- Gap analysis vs Claude Code / Cowork → [gap-analysis-claude-code.md](gap-analysis-claude-code.md)
- Gap analysis vs OpenClaw / OpenFang → [gap-analysis-openclaw-openfang.md](gap-analysis-openclaw-openfang.md)

## In Progress

## Completed

- v1.5.3 — Thread-safe agent context, provider resolution fix, memory robustness, concurrent scheduler
- v1.5.2 — Memory summary direct execution, QMD index path normalization, collection health stats
- v1.5.1 — MiniMax provider, Add Model UI, QMD session leak fix, in-process Telegram
- v1.5.0 — Settings dashboard, agent activity indicators, QMD document browser, smart model routing
- v1.4.0 — QMD hybrid memory search, SSE error handling, server resilience
- v1.2.0 — Multi-provider routing, Gmail, scheduler dashboard, SQLite resilience, Cloudflare deployment
- v1.1.0 — MCP support, Web UI, chat history, skill browser, avatars
- v1.0.0 — Client-server architecture, Telegram bot, background tasks, scheduler
