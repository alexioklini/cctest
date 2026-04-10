---
id: memory_ordering
parent: Tools
created: 2026-03-20T04:15:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: related_to
    target: memory:common/prompts/memory_store.md
  - type: related_to
    target: memory:common/prompts/memory_recall.md
  - type: related_to
    target: memory:common/architect/planning_agent_storage.md
  - type: depends_on
    target: memory:common/architect/planning_agent_storage.md
update_site: Reporter Relationship Discovery
---

# Memory Ordering System

The scheduling and task execution system that manages when agents and tools should run.

## Core Components

### Memory System Integration
- **related_to**: memory_store, memory_recall
  - Task scheduling depends on memory tools being available
  - Relationship discovery scheduled tasks use memory mechanisms

### Planning Agent Dependencies
- **related_to**: planning_agent_storage
  - Planning agents store their task information in the planning system
  - Scheduling order depends on planning system availability
  - Ensures scheduled tasks like `_relationship_discovery_Reporter` can run

### Task Scheduling Pattern
- Manages scheduled executions of system tasks
- Coordinates between memory recall/store operations
- Handles dependencies between different agent operations

## Role in Reporter Task Execution

### Scheduled Task: `_relationship_discovery_Reporter`
- Scheduled to run twice daily
- Requires:
  - memory_ordering system to schedule it
  - memory_store to write relationships
  - memory_recall to read memories
  - Planning agent storage for task metadata

### Execution Flow
1. memory_ordering schedules the task at defined intervals
2. Task begins executing `_relationship_discovery_Reporter`
3. Agent calls memory_recall to get all memories
4. Relationship discovery analyzes memories
5. Uses memory_store to write updated relationships

## Dependencies
Depends specifically on planning_agent_storage for:
- Task definition storage
- Scheduling metadata
- Execution context

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_