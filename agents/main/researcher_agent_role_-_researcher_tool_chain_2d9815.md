---
name: "\"Researcher_Agent_Role <-> Researcher_Tool_Chain\""
description: Concept (agent role) and implementation (tool infrastructure) relationship
type: project
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
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
last_recalled: 2026-04-05
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: multi_agent_content_pipeline_94fcc2.md
    type: same_topic
  - file: sdk_gap_plan_implementation_5ab4e9.md
    type: same_topic
---

---
related:
  - name: "tool-expansion-analysis-2026-03"
    relationship: "necessitates"
    detail: "Tool chain must exist to enable Researcher agent operations"
  - name: "Reporter_Agent_Role"
    relationship: "counterpart"
    detail: "Researcher and Reporter are paired roles; Researcher and Researcher_Tool_Chain are paired concept-implementation"
---
**Researcher_Agent_Role** defines the Researcher agent's purpose, capabilities, and position within the Brain Agent ecosystem as an analytical agent for research tasks.

**Researcher_Tool_Chain** documents the specific tools and execution environment that implement the Researcher's analytical capabilities (Glob, Grep, Read, WebFetch/WebSearch, execute_command with login shell wrapper).

This relationship represents the classic agent/tool pattern: role definition (what the agent does) paired with infrastructure definition (how the agent accomplishes it). The tool chain exists to fulfill the agent's stated role.
