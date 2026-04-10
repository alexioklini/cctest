---
name: "\"tool-expansion-analysis-2026-03\""
description: "\"Comprehensive analysis of Brain Agent tool expansion pathways: skills, MCP servers, slash commands, and native tools\""
type: project
agent: main
related:
  - file: claude-code-version-path_2b8576.md
    type: same_topic
  - file: shell-env-fix-2025_8c1d05.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_92bcff.md
    type: same_topic
  - file: mbaim1-vpn-connection_248938.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
last_recalled: 2026-04-08
  - file: dev-workflow-feedback_cffe4d.md
    type: co_recalled
  - file: memory_summary_aab3f9.md
    type: co_recalled
  - file: dev-workflow-feedback_af2401.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: co_recalled
  - file: memory-architecture.md
    type: co_recalled
  - file: memory-architecture_fa7efd.md
    type: co_recalled
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: memory_summary_references_memory_architecture_855b1f.md
    type: same_topic
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: same_topic
  - file: memory_health_report_2026-03-25_a3253f.md
    type: same_topic
  - file: researcher_tool_chain_6c2e11.md
    type: same_topic
  - file: reporter-agent-summary_a4d7cf.md
    type: co_recalled
---

---
related:
  - name: "openclaw-vs-claudecode-skills-comparison"
    relationship: "same_topic"
    detail: "Both analyze skill/tool expansion approaches — internal expansion strategy vs external skill system comparison"
  - name: "memory-architecture"
    relationship: "same_topic"
    detail: "Both describe core Brain Agent platform internals — tool system architecture and memory system architecture"
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary references MCP and tool expansion decisions; this memory is the authoritative analysis"
  - name: "shell-env-fix-2025"
    relationship: "references"
    detail: "Tool expansion (especially MCP stdio servers and CLI tools) depends on the login shell PATH fix"
  - name: "Reporter Agent Summary"
    relationship: "references"
    detail: "Tool expansion analysis describes the agent/tool architecture; Reporter is a concrete agent within that system"
---
## Tool Expansion Analysis (2026-03-25)

### Current State
- 35 built-in tools in TOOL_DISPATCH (claude_cli.py: 14,262 lines)
- 3 skills installed: github, gmail, word-docx
- 0 MCP servers connected (infrastructure fully built, mcp.json is empty)
- Slash commands: /help, /new, /agent, /model, /models, /tools, /schedule

### Recommended Classification
- Domain knowledge → Skill (SKILL.md)
- External API/service → MCP Server
- User workflow shortcut → Slash Command
- Core platform capability → Native Tool

### Key Architectural Concerns
1. Token overhead: all 35 tool definitions sent in every API call (~5K-10K tokens)
2. Monolith risk: claude_cli.py growing; favor MCP over native for non-core tools
3. MCP infrastructure is ready but unused — low-hanging fruit for expansion

### Priority Opportunities
- MCP: sqlite (introspect own DBs), puppeteer (browser automation)
- Skills: docker, python-project, macos-admin, brain-agent-dev
- Future: Tool relevance scoring to reduce per-call token overhead
