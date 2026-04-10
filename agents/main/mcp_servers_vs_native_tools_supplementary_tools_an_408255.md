---
name: "MCP Servers vs Native Tools: Supplementary Tools Analysis"
description: "Comprehensive analysis of supplementary tools for Brain Agent: MCP servers vs skills vs native tools. Includes marketplace options and architectural decision framework."
type: chat_transcript
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
last_recalled: 2026-04-08
  - file: memory_summary_aab3f9.md
    type: same_topic
---

## Analysis Request
Conduct a comprehensive analysis of implementing supplementary tools for brain-agent functionality. Evaluate integration options including skills, custom slash commands, and MCP servers as alternatives to direct tool infrastructure expansion, ensuring architectural cleanliness and system scalability.

## Key Analysis & Findings

MCP servers are the best immediate expansion vector — the infrastructure is 100% built and battle-ready, with zero servers actually connected. Skills complement this for knowledge-heavy domains. Native tools should be reserved strictly for capabilities that need Brain Agent internals.

## Implementation Guidance

**When to use MCP servers:** When you actually need capabilities our native tools *can't* do (database queries, browser automation, a Kubernetes API).

**When NOT to use:** MCP server does NOT replace native tools for file reading and basic operations. Tests confirmed everything works without persistence.

**Skills:** Complement MCP servers well for knowledge-heavy domains.

**Native tools:** Reserve for Brain Agent internal capabilities only.

## MCP Marketplace

**Official MCP Registry:** [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io) — the canonical registry, backed by Anthropic. More mature than skills marketplace.

**Architectural principle:** Only implement supplementary tools when native infrastructure genuinely cannot support the requirement. Zero residual servers — clean infrastructure on demand.
