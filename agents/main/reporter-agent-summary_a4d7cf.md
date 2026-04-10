---
name: "reporter-agent-summary"
description: Detailed technical specifications and implementation details for the Reporter agent
type: project
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
last_recalled: 2026-04-05
  - file: researcher_tool_chain_6c2e11.md
    type: co_recalled
  - file: memory_summary_aab3f9.md
    type: co_recalled
  - file: memory_health_report_2026-03-25_a3253f.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: co_recalled
  - file: memory-architecture.md
    type: co_recalled
  - file: memory-architecture_fa7efd.md
    type: co_recalled
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: references
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: reporter_reporting_tooling_46aba8.md
    type: same_topic
  - file: multi_agent_content_pipeline_94fcc2.md
    type: same_topic
  - file: reporter_agent_summary_8cef33.md
    type: same_topic
---

---
related:
  - name: Memory Summary
    relationship: extends
    detail: "Extends Memory Summary by providing detailed technical specifications and implementation details while the summary offers platform-level user context"
  - name: memory-architecture
    relationship: references
    detail: "Uses the hub-and-spoke shared memory architecture defined in memory-architecture document"
---
The Reporter agent represents the concrete implementation of the Reporter role from the Memory Summary context. It is a separate project memory that provides detailed technical specifications, configuration details, and implementation notes for the agent responsible for generating formatted reports and summaries (e.g., project comparison reports). The Reporter agent converts raw analyses into structured markdown outputs for user presentation. This memory is referenced by Memory Summary as an active agent in the roster and extends the summary with specific technical details about its operation.
