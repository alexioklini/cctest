---
id: reporter_relationships
parent: Reporter Agent
created: 2026-02-06T04:15:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: related_to
    target: memory:common/prompts/memory_store.md
  - type: related_to
    target: memory:common/prompts/memory_recall.md
  - type: related_to
    target: memory:common/prompts/relationship_discovery.md
update_site: Reporter Relationship Discovery
---

# Reporter Relationships

Central storage for discovered relationships between Reporter-related memory records.

## Management Approach
- Bucket pattern: Collects relationships for easy discovery and maintenance
- Maps relationships between:
  - Core Reporter agent memories (roles, summaries)
  - Memory system tools (memory_store, memory_recall)
  - Relationship discovery process memories

## Relationship Collection
The following memories contribute to the relationship graph:

### Agent Memories
- `memory:reporter/reporter_agent_role.md` - Reporter agent role definition
- `memory:reporter/reporter_agent_summary.md` - Reporter agent capabilities and scope

### Memory System
- `memory:common/prompts/memory_store.md` - Memory write operations
- `memory:common/prompts/memory_recall.md` - Memory query operations

### Process Memories
- `memory:common/prompts/relationship_discovery.md` - Relationship discovery process
- `memory:common/prompts/_relationship_discovery_reporter.md` - Scheduled task for Reporter

## Usage Pattern
This memory serves as a central index for Reporter-related relationships, enabling:
- Quick discovery of the Reporter agent's memory connections
- Maintenance of relationship graphs
- Documentation of dependencies between Reporter and other system components

## Discovery Pattern
Updated during scheduled task execution:
- Task: `_relationship_discovery_Reporter`
- Frequency: Scheduled runs
- Updates: relationship frontmatter on related memories

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_