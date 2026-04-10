---
name: CROW_9B_relationships_part_1
description: "Primary relationships for CROW_9B agent: infrastructure, provider stack, SDK constraints"
type: project
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: multi_agent_research_team_relationships_53ba83.md
    type: same_topic
  - file: infrastructure_provider_relationships_25e122.md
    type: same_topic
last_recalled: 2026-04-08
---

# Agent CROW_9B — Core Relationships

## Agent Role & Capabilities
- **isa**: AI_agent (base class)
- **isa**: Qwen3_5_distilled (model lineage)
- **isa**: local_inference_agent (uses oMLX/Crow-4B)
- **ability**: reasoning, long-form dialogue, code analysis
- **ability**: memory_indexed_agent (uses structured memory system)

## Infrastructure Dependencies
- **depends_on**: [infra_omlx_local](infra_inference.md) — port 8000, Crow-4B model
- **depends_on**: [infra_cloudflare_tunnel](infra_deployment.md) — remote access routing
- **depends_on**: [infra_mcp_client_pattern](features/mcp-client-support.md) — MCP support for tools
- **references**: [feedback_omlx_anthropic](feedback_omlx_anthropic.md) — oMLX uses anthropic API type

## Provider Stack
- **uses**: [provider_omlx_local](infra_infeed_inferencer.md) via anthropic API type
  - **contradicts**: standard_openai_API_pattern (oMLX uses anthropic type, not openai, differing from typical SDK provider expectations)
- **uses**: [provider_mistral_pro](project_mistral_provider.md)
- **uses**: [provider_minimax](agents/Minimax/soul.md)
- **uses**: [provider_cliproxyapi](agents/main/soul.md) (shares Claude 5h quota)

## SDK & Streaming Constraints
- **has_constraint**: REST_sidecar_required (SDK hooks cause SSE buffering; REST sidecar decouples streaming)
- **references**: [feedback_sdk_streaming](feedback_sdk_streaming.md)
  - **same_topic**: [feedback_direct_execution](feedback_direct_execution.md)
  - **type**: connection_both_streaming_requirements
- **has_constraint**: no_thinking_param_via_sidecar (SSE stream hangs when thinking sent via SDK sidecar)
- **references**: [bug_thinking_sidecar](bug_thinking_sidecar.md)
- **has_constraint**: no_claude_cli_import (sdk sidecar must NEVER import claude_cli; breaks anyio streaming)
- **references**: [feedback_sidecar_no_claude_cli](feedback_sidecar_no_claude_cli.md)

## Execution & Runaway Protection
- **has_constraint**: direct_execution_required (user-triggered actions must execute directly, not via scheduler indirection — prevents runaway loop exhaustion of CLIProxyAPI quota)
- **references**: [feedback_direct_execution](feedback_direct_execution.md)
  - **same_topic**: [feedback_sdk_streaming](feedback_sdk_streaming.md)
- **has_constraint**: cliproxy_quota_guard (CLIProxyAPI shares Claude quota; runaway tool loops can exhaust 5-hour quota)
- **references**: [feedback_cliproxy_quota](feedback_cliproxy_quota.md)
- **contradicts**: naive_scheduler_delegation_for_tool_heavy_tasks (use direct execution instead)

## Memory & Reporting Architecture
- **references**: [features_knowledge_graph_memory](features/knowledge-graph-memory.md) (memory index structure)
- **references**: [memory_summary_structure](MEMORY.md) (auto-generated synthesis, updated periodically)
- **same_topic_as**: [Reporter_Agent_Role](agents/Reporter/soul.md) (both are agents in research team)
- **same_topic_as**: [Researcher_Agent_Role](agents/Researcher/soul.md) (both participate in multi-agent research workflows)

## Backlog Issues & Extensions
- **extends**: [backlog_tool_results_display](backlog_tool_results_display.md) (tool results not shown in chat UI; blocked by SDK hooks killing streaming)
- **depends_on**: [project_mistral_provider](project_mistral_provider.md) (provider sync issues create technical debt)
- **references**: [backlog_provider_model_sync](backlog_provider_model_sync.md) (flaky UI for provider/model sync)
- **references**: [project_sdk_gap_plan](project_sdk_gap_plan.md) (7-phase plan to close SDK migration gaps)

