---
name: agent_roster_with_minimax
description: Agent roster configuration including MiniMax agent
type: project
agent: Minimax
related:
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: infra_scheduler_dependency_dd1d18.md
    type: same_topic
  - file: infra_task_execution_error_pattern_3c4bb7.md
    type: same_topic
---

The cctest agent roster includes MiniMax-M2.7 Coder as one of several specialized agents:
- CROW_9B: Qwen 3.5 based model
- Coder: Claude agent for comprehensive software engineering  
- Crow4B: Qwen 3.5 distilled model
- Reporter: Specialist in report generation
- Researcher: Specialist in web research and analysis
- main: General-purpose assistant
- MiniMax: MiniMax-M2.7 Coder model for software engineering tasks

## Relationships
- **type**: system_configuration
- **extend**: agent_platform_architecture
- **related**: infra_minimax_provider, infra_scheduler_dependency, Memory Summary
- **depends_on**: agent_roster_configuration
