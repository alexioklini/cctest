---
name: Researcher_Tool_Chain
description: "Addressing tool-chain execution requirements for the Researcher agent"
type: project
agent: main
related:
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_23bd9e.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: same_topic
  - file: memory-architecture_fa7efd.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: same_topic
  - file: memory_summary_218799.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
last_recalled: 2026-04-08
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: reporter_tool_chain_92a110.md
    type: same_topic
---

---
related:
  - name: "shell-env-fix-2025"
    relationship: "depends_on"
    detail: "Login shell PATH resolution is critical infrastructure for Researcher tool operations"
  - name: "Reporter Agent Summary"
    relationship: "counterpart"
    detail: "Reporter provides polished output from Researcher's raw analysis; tool reliability impacts quality"
  - name: "tool-expansion-analysis-2026-03"
    relationship: "extends"
    detail: "Documents the complete tool ecosystem and classifies suitable tools for research tasks"
  - name: "c98f47eb317a"
    relationship: "depends_on"
    detail: "Core Brain Agent memory architecture supports Researcher by providing platform context"
  - name: "MiniMax-Provider-Integration"
    relationship: "validates"
    detail: "Manual provider integration validates that external tool infrastructure works correctly"
---
The Researcher agent relies on a robust tool infrastructure to perform analytical tasks:

**Core Workflow Tools:**
- Glob: fast file pattern matching for discovery. Uses last-write-wins sorting.
- Grep: content search with regex across codebases. Provides matches, files_with_matches, count outputs.
- Read: file content retrieval up to 2000 lines with line numbering.
- WebFetch / WebSearch: web content retrieval and analysis.

**Execution Environment:**
- execute_command with login shell wrapper (shell-env-fix-2025) ensures proper PATH for external tools (npm packages like claude, gh, etc.)
- memory_shared and memory_recall provide access to platform context via hub-and-spoke model
- MCP infrastructure (when connected) provides additional data sources (sqlite introspection, etc.)

All tools use structured outputs (files_with_matches with count limits, content with line numbers enabled by default -n, offset handling) to enable programmatic analysis and reduce token overhead in responses. The tool chain directly enables Researcher's stated capabilities for web research, codebase exploration, and technical analysis.
