---
name: infra_minimax_provider_relationships
description: MiniMax provider configuration and integration details
type: general
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary.md
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
  - file: researcher_agent_role_-_researcher_tool_chain_2d9815.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
    type: same_topic
  - file: memory_generation_pipeline_ac1bd2.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_memory_system_f15e25.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_scheduled_tasks_f0e6d1.md
    type: same_topic
  - file: crow4b_-_memory_summary_6f4362.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: mcp_servers_vs_native_tools_supplementary_tools_an_408255.md
    type: same_topic
  - file: system_architecture_brain-agent_on_mac_studio_m2_m_bb9752.md
    type: same_topic
---

MiniMax provider is integrated into the Brain Agent platform as a registered API provider (https://api.minimax.io/anthropic) using the anthropic API type instead of openai. Models include MiniMax-M2.5 and MiniMax-M2.7. Configured manually due to /v1/models endpoint returning 404 — resolved by setting explicit default_model and model entries in provider configuration.
