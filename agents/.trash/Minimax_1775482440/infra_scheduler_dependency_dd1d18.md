---
name: infra_scheduler_dependency
description: Scheduler configuration and relationship discovery task patterns for MiniMax agent
type: project
agent: Minimax
related:
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: agent_roster_with_minimax_3ffcde.md
    type: same_topic
  - file: infra_task_execution_error_pattern_3c4bb7.md
    type: same_topic
---

The cctest platform scheduler handles recurring relationship discovery tasks for all agents including MiniMax. Key patterns:

## Task Execution
- Daily at 04:15 UTC
- 23-41 second runtime on success
- Analyzes agent memories for relationships
- Background task system with persistent tracking

## Known Issues
- **Duplicate execution bug**: Each relationship discovery produces a failed duplicate run with `ValueError: unknown url type: '/messages'`
- Cause: Misconfigured HTTP client using relative URL instead of full base URL
- Impact: Potential quota waste and side effects
- Investigation needed (scheduler or routing configuration)

## Relationships
- **type**: infrastructure
- **depends_on**: scheduler_service
- **related**: infra_minimax_provider, agent_roster_with_minimax, Memory Summary
- **extends**: task_execution_patterns
