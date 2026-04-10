---
name: "infra_scheduler_dependency <-> infra_minimax_provider"
description: Scheduler execution depends on MiniMax provider configuration being available and correct
type: project
agent: main
related:
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: agent_roster_with_minimax_-_infra_minimax_provider_bfd61b.md
    type: same_topic
  - file: agent_roster_with_minimax_3ffcde.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: minimax_agent_roster_extensions_b5a832.md
    type: same_topic
last_recalled: 2026-04-08
  - file: reporter_agent_-_minimax_provider_integration_09b903.md
    type: same_topic
---

The infrastructure scheduler depends on the MiniMax provider configuration (models, API endpoints) to successfully execute scheduled tasks. This includes the MiniMax-M2.5/M2.7 provider used for various agent operations.
