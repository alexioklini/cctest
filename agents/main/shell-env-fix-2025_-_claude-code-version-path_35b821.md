---
name: "\"shell-env-fix-2025 <-> claude-code-version-path\""
description: "Bug-resolution relationship: PATH discovery issue and its fix"
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
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
last_recalled: 2026-04-05
  - file: researcher_agent_role_-_researcher_tool_chain_2d9815.md
    type: same_topic
  - file: memory_generation_pipeline_ac1bd2.md
    type: co_recalled
  - file: openclaw-vs-claudecode-skills-comparison-extended-_82c838.md
    type: co_recalled
  - file: memory_system_e5dba6.md
    type: co_recalled
  - file: memory_health_report_2026-03-26_3f4592.md
    type: co_recalled
  - file: shell-env-fix-2025_8c1d05.md
    type: co_recalled
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: co_recalled
  - file: dev-workflow-feedback_cffe4d.md
    type: co_recalled
  - file: memory_health_reports_chain_0dfb71.md
    type: co_recalled
  - file: scheduled_tasks_87aabf.md
    type: co_recalled
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: memory_health_report_2026-03-25_a3253f.md
    type: same_topic
  - file: memory_summary_218799.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_scheduled_tasks_f0e6d1.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: co_recalled
  - file: reporter_tool_chain_92a110.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: mcp_servers_vs_native_tools_supplementary_tools_an_408255.md
    type: same_topic
  - file: system_architecture_brain-agent_on_mac_studio_m2_m_bb9752.md
    type: same_topic
---

---
related:
  - name: "tool-expansion-analysis-2026-03"
    relationship: "enables"
    detail: "Proper shell environment enables MCP CLI server expansion"
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary includes both the problem and solution"
---
**claude-code-version-path** documents the critical bug where the `claude` binary (v2.1.83) was not discoverable via PATH in non-interactive shells used by Brain Agent's execute_command.

**shell-env-fix-2025** resolves this by implementing login shell wrapper (`/bin/zsh -l -c "command"`) that sources ~/.zprofile and ~/.zshrc, restoring PATH entries from shell profiles that npm-installed tools depend on.

This is a textbook bug-resolution relationship: documentation of the problem (claude-code-version-path) and the solution that fixes it (shell-env-fix-2025).
