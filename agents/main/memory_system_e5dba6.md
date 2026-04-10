---
name: memory_system
description: Memory system architecture and generation pipeline
type: reference
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: claude-code-version-path_2b8576.md
    type: same_topic
last_recalled: 2026-04-05
  - file: memory_generation_pipeline_ac1bd2.md
    type: co_recalled
  - file: openclaw-vs-claudecode-skills-comparison-extended-_82c838.md
    type: co_recalled
  - file: memory_health_report_2026-03-26_3f4592.md
    type: co_recalled
  - file: shell-env-fix-2025_8c1d05.md
    type: co_recalled
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: co_recalled
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: co_recalled
  - file: dev-workflow-feedback_cffe4d.md
    type: co_recalled
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: co_recalled
  - file: memory_health_reports_chain_0dfb71.md
    type: co_recalled
  - file: scheduled_tasks_87aabf.md
    type: co_recalled
  - file: memory_summary_218799.md
    type: co_recalled
  - file: reporter_agent_role_747c13.md
    type: co_recalled
---

Brain Agent's hierarchical memory system: 1) Automatic background memory creation via scheduled task _memory_summary_Crow4B, 2) User-defined memories with names/types, 3) Relationship annotations between memories. Memory flows upward: automatic background summaries feed into periodic memory syntheses. Related to: background_memory_generation, memory_relationships
