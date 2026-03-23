# Brain Agent Roadmap

## Planned

Each item has a detailed feature proposal with mockups, user workflows, and effort estimates.

### Security & Control
- [Hooks system](features/hooks-system.md) — pre/post tool execution hooks (deterministic shell scripts) · ~6 days · **P1** ✅ done (v3.7.0)

### Code Intelligence
- [Code structure graph](features/code-graph.md) — AST-based code knowledge graph via Tree-sitter (14 languages), blast-radius analysis, callers/callees/imports/tests queries, integrated KG visualization · ~9 days · **P1**
- [Permissions model](features/permissions-model.md) — per-tool approval with allow/deny patterns, sandboxing · ~9 days · **P5**
- [Plan mode](features/plan-mode.md) — read-only analysis mode that disables write tools · ~5 days · **P1** ✅ done (v1.7.0)

### Infrastructure & Deployment
- [Provider fallback chains](features/provider-fallback.md) — ordered fallback with exponential backoff retry · ~8 days · **P2**
- [Docker deployment](features/docker-deployment.md) — Dockerfile + docker-compose for cross-platform usage · ~5 days · **P5**
- [Multi-messaging frontends](features/multi-messaging-frontends.md) — generic adapter framework for multiple simultaneous messaging channels (Telegram, Discord, Slack, etc.) with per-channel config, agent routing, and lifecycle management · ~15 days · **P2**
- [Discord + Slack adapters](features/discord-slack-adapters.md) — expand beyond Web/TUI/Telegram · ~10 days · **P4**
- [Remote nodes](features/remote-nodes.md) — lightweight node agents on remote machines with centralized management, tool routing, live activity tracking · ~12 days · **P2**
- [Worktrees](features/worktrees.md) — isolated git worktrees for parallel agent work · ~8 days · **P3**

### User Features
- [Agent workflows](features/agent-workflows.md) — multi-step blueprints with approval gates and branching · ~15 days · **P2**
- [Custom slash commands](features/custom-slash-commands.md) — user-defined commands with prompt templates · ~7 days · **P1** ✅ done (v1.7.0)
- [LLM-assisted input refinement](features/llm-input-refinement.md) — AI-powered text improvement in all inputs · ~5 days · **P1** ✅ done (v1.7.0)

### Platform
- [Web UI optimization](features/webui-optimization.md) — collapsible left sidebar, remove redundant elements · ~6 days · **P2**
- ~~Self-awareness memory~~ — merged into Projects + Knowledge graph + RAG
- [MCP client support](features/mcp-client-support.md) — dynamic MCP server connections at runtime · ~8 days · **P2**
- [A2A protocol](features/a2a-protocol.md) — Google Agent-to-Agent for cross-system interop · ~12 days · **P3**
- [Embeddable SDK](features/embeddable-sdk.md) — standalone Python package for embedding Brain Agent · ~15 days · **P3**
- [Hierarchical instructions](features/hierarchical-instructions.md) — per-project .brain/instructions.md auto-loading · ~4 days · **P3**

### Memory & Search
- [Projects + Knowledge graph + RAG](features/document-ingestion.md) — per-agent projects with scoped docs/folders/chats, document ingestion pipeline, watched folders, memory graph traversal, self-awareness · ~25 days · **P1** (combines [knowledge-graph-memory.md](features/knowledge-graph-memory.md) + [document-ingestion.md](features/document-ingestion.md) + [self-awareness-memory.md](features/self-awareness-memory.md))
- [Universal file intelligence](features/universal-file-intelligence.md) — XLSX/PPTX/CSV parsers, image vision+OCR, audio/video transcription, read/write/edit document tools, rich KG metadata, QMD indexing for all formats · ~11 days · **P1**
- [Web result caching](features/web-result-caching.md) — LRU cache for web_fetch/exa_search with TTL · ~2 days · **P1** ✅ done (v1.7.0)

### Observability & Operations
- [Cost tracking + Rate limiting](features/cost-tracking.md) — per-agent API spend tracking, budgets, alerts, request/token/cost throttling · ~8 days · **P1** ✅ done (v1.7.0)
- [Observability & tracing](features/observability-tracing.md) — structured traces for LLM calls, tool execution, latency/error dashboards · ~8 days · **P2**
- [Audit trail](features/audit-trail.md) — append-only log of all agent actions, searchable, exportable · ~5 days · **P2**
- [Notifications](features/notifications.md) — email/webhook/in-app alerts for task completion, errors, budget, node status · ~7 days · **P2**

### Execution & Sandbox
- [Streaming tool output](features/streaming-tool-output.md) — real-time stdout/stderr streaming during command execution · ~5 days · **P1** ✅ done (v1.7.0)
- [Code sandbox](features/code-sandbox.md) — isolated run_code tool with restricted filesystem/network access · ~8 days · **P3**

### Multi-modal & Data
- [Multi-modal support](features/multimodal-support.md) — image upload/display in chat, vision model support · ~7 days · **P2**
- [Backup / export / import](features/backup-export-import.md) — portable archives for full instance or per-agent migration · ~5 days · **P2**

**Total: 28 proposals · ~232 days estimated**

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
