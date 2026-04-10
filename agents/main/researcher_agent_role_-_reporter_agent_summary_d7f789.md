---
name: "\"Researcher_Agent_Role <-> Reporter Agent Summary\""
description: Complementary agents that form the Research Team workflow
type: project
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: memory_summary.md
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
last_recalled: 2026-04-05
  - file: reporter_agent_role_-_researcher_counterpart_95de32.md
    type: same_topic
  - file: reporter_agent_role_-_memory-architecture_dependen_4332e9.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
---

---
related:
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary references the Research Team structure"
  - name: "tool-expansion-analysis-2026-03"
    relationship: "depends_on"
    detail: "Researcher tool chain relies on proper tool infrastructure"
---
The Research Team follows a producer-consumer pattern where:

**Researcher Agent:** Analytical agent that performs web research, codebase exploration, technical analysis, and provides raw structured data

**Reporter Agent:** Presentation agent that transforms raw analysis into polished reports, formats outputs, and handles delivery

This division of labor enables efficient processing: Researcher focuses on data gathering and factual analysis, Reporter focuses on presentation and user-facing output formatting. Both agents access the same shared memory hub for platform context and task state.
