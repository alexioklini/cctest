---
id: memory_recall
parent: Memory System
created: 2026-02-01T04:16:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: related_to
    target: memory:common/prompts/memory_store.md
  - type: related_to
    target: memory:common/prompts/relationship_discovery.md
  - type: depends_on
    target: memory:common/prompts/memory_store.md
update_site: Reporter Relationship Discovery
---

# Memory Recall

The `memory_recall` tool retrieves memory records from the system's memory store.

## Query Patterns

### Empty Query Pattern
To retrieve all memories:
```
memory_recall {
  query: ""
}
```

### Keyword Search
```
memory_recall {
  query: "Reporter"
}
```

## Results Structure
The tool returns all memory records that match the query criteria, allowing agents to:
- Discover related memories
- Build relationship graphs
- Identify dependencies
- Query memory by category, type, or content

## Role in Reporter's Relationship Discovery
- First step: Recall all memories to analyze content
- Discovers Reporter-related memories by their content
- Enables subsequent filtering and relationship analysis
- Works with `memory_store` to write back discovered relationships

## Technical Implementation
- Retrieves records with YAML frontmatter
- Returns structured data for programmatic processing
- Enables relationship discovery across the agent ecosystem
- Supports empty query to get all memories

## Dependencies

Depends on `memory_store` for:
- Persisting discovered relationships after analysis
- Maintaining relationship graph
- Tracking updates and provenance

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_