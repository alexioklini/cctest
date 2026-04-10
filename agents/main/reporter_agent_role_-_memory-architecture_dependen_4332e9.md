---
name: "Reporter_Agent_Role_-_memory-architecture_dependency"
description: "Reporter architecture dependency on hub-and-spoke memory system"
type: reference
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: reporter_tool_chain_92a110.md
    type: same_topic
  - file: agent_roster_with_minimax_3ffcde.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: reporter_agent_summary_-_tool-expansion-analysis_0214eb.md
    type: same_topic
  - file: reporter_agent_role_-_researcher_counterpart_95de32.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: reporter_agent_summary_2b0fba.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: researcher_agent_role_-_reporter_agent_summary_d7f789.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
---

---
related: memory-architecture ⇄ depends_on ⇄ Reporter_Agent_Role
---

Reporter agent's architecture specifically depends on the memory architecture defined in memory-architecture.md. This dependency enables Reporter to access the hub-and-spoke shared memory system via memory_shared() calls for platform context, user formatting preferences, and task state coordination with other Research Team members.
