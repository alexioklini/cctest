---
name: "claude-code-version-path <-> shell-env-fix-2025"
description: Critical execution bug and its resolution within the execute_command subsystem
type: project
agent: main
related:
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: memory-architecture.md
    type: same_topic
  - file: memory_summary_-_openclaw-vs-claudecode-skills-com_6b0901.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: memory_summary_-_tool-expansion-analysis_b248dd.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
last_recalled: 2026-04-05
  - file: multi_agent_content_pipeline_94fcc2.md
    type: same_topic
  - file: reporter_agent_summary_8cef33.md
    type: same_topic
  - file: sdk_gap_plan_implementation_5ab4e9.md
    type: same_topic
---

claude-code-version-path documents the CLI binary PATH discovery problem; shell-env-fix-2025 resolves this PATH inheritance issue for non-interactive shells in Brain Agent's execute_command. Bidirectional dependency: the version path issue requires the fix, and the fix directly addresses the documented problem.
