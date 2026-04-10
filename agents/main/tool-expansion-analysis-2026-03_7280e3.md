---
name: "tool-expansion-analysis-2026-03"
description: Brain Agent tool expansion infrastructure analysis relationship
type: project
agent: main
related:
  - file: researcher_tool_chain_6c2e11.md
    type: same_topic
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
last_recalled: 2026-04-08
---

---
related:
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary references MCP and tool expansion decisions; this document is the authoritative analysis"
  - name: "memory-architecture"
    relationship: "same_topic"
    detail: "Both describe core Brain Agent platform internals — tool infrastructure and memory system architecture"
  - name: "openclaw-vs-claudecode-skills-comparison"
    relationship: "same_topic"
    detail: "Both analyze skill/tool expansion approaches internally and comparatively"
  - name: "Reporter Agent Summary"
    relationship: "depends_on"
    detail: "Tool infrastructure enables Reporter's diverse data access and formatting capabilities"
  - name: "Researcher_Tool_Chain"
    relationship: "enables"
    detail: "Researcher agent depends heavily on tool infrastructure for analytical tasks"
  - name: "MiniMax-Provider-Integration"
    relationship: "validates"
    detail: "MiniMax provider integration demonstrates the need for MCP infrastructure and MPI servers for external API access"

---
## Tool Expansion Analysis (2026-03-25) — Updated Relationships

This analysis defines how Brain Agent's tool infrastructure supports the entire agent ecosystem:

- **35 built-in tools in TOOL_DISPATCH** (claude_cli.py: 14,262 lines)
- **3 skills installed**: github, gmail, word-docx
- **0 MCP servers currently connected** but infrastructure fully built (mcp.json ready)
- **7 slash commands**: /help, /new, /agent, /model, /models, /tools, /schedule

### Architecture Enables
- **Coder agent**: Uses core tools for file operations, testing, shell commands
- **Researcher agent**: Requires Glob/Grep/Read/WebFetch for analytical tasks; validated by Researcher_Tool_Chain
- **Reporter agent**: Uses tool chain for content discovery and formatting
- **Crow 9B agent**: Inherits the entire tool infrastructure for relationship discovery task execution

### Critical Findings
- **Token overhead**: All 35 tool definitions sent per call (~5K-10K tokens) requires future tool relevance scoring
- **Monolith risk**: claude_cli.py growing too large; MCP servers offer modular alternative
- **MCP infrastructure built but unused**: Low-hanging fruit for expansion (sqlite introspection, puppeteer browser automation)
- **Execution reliability depends on shell-env-fix-2025** for PATH resolution enabling CLI tools and MCP stdio servers
