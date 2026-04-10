---
name: multi_agent_content_pipeline
description: "Main agent's multi-agent content pipeline definition"
type: general
agent: main
related:
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: claude-code-version-path_-_shell-env-fix-2025_224b40.md
    type: same_topic
  - file: reporter_agent_summary_2b0fba.md
    type: same_topic
  - file: researcher_agent_role_-_researcher_tool_chain_2d9815.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_memory_system_f15e25.md
    type: same_topic
last_recalled: 2026-04-08
---

---
related:
  - name: "Reporter_Agent_Summary"
    relationship: "extends"
    detail: "Multi-agent content pipeline depends on Reporter's presentation capabilities to transform Researcher outputs into professional reports for end users"
  - name: "memory-architecture"
    relationship: "depends_on"
    detail: "Operates within the Brain Agent's hub-and-spoke memory architecture with main agent memory = shared memory"
---
Multi-agent system architecture where specialized agents collaborate: Researcher provides data, Reporter renders professional outputs. This relationship forms the core editorial pipeline. The Reporter's outputs are consumed by end users or downstream systems, completing the content lifecycle started by research agents.
