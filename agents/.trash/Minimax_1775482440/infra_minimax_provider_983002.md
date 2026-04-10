---
name: infra_minimax_provider
description: MiniMax provider infrastructure configuration
type: project
agent: Minimax
related:
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: agent_roster_with_minimax_3ffcde.md
    type: same_topic
  - file: infra_scheduler_dependency_dd1d18.md
    type: same_topic
  - file: infra_task_execution_error_pattern_3c4bb7.md
    type: same_topic
---

The MiniMax-M2.7 Coder model is configured as a provider in the cctest agent roster. It operates as a local provider with its own API type and endpoint configuration (non-OpenAI-compatible). 

## Relationships
- **type**: infrastructure
- **extends**: cctest_platform
- **related**: agent_roster_with_minimax, infra_scheduler_dependency, Memory Summary
- **depends_on**: MiniMax API configuration
