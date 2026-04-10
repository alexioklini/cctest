---
id: memory_store
parent: Memory System
created: 2026-02-01T04:15:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: related_to
    target: memory:common/prompts/memory_recall.md
  - type: related_to
    target: memory:reporter/reporter_agent_role.md
  - type: related_to
    target: memory:reporter/reporter_agent_summary.md
  - type: related_to
    target: memory:common/prompts/relationship_discovery.md
update_site: Reporter Relationship Discovery
---

# Memory Store

The `memory_store` tool writes structured data back to memory records with relationship frontmatter.

## Usage Pattern
```
memory_store {
  path: "memory:path/to/file.md",
  relationships: [
    {"type": "related_to", "target": "memory:other/file.md"}
  ],
  update_site: "Update Context",
  last_updated: "2026-04-08T04:15:00Z"
}
```

## Frontmatter Structure
The memory system uses YAML frontmatter to store metadata and relationships:

```yaml
---
id: memory_identifier
parent: parent_category
created: 2026-04-08T04:15:00Z
last_updated: 2026-04-08T04:15:00Z
relationships:
  - type: related_to
    target: memory:other/path.md
  - type: depends_on
    target: memory:dependency/path.md
update_site: "Update Context"
---
```

## Parameters
- `path`: Memory record path in format "memory:category/subcategory/file.md"
- `relationships`: Array of relationship objects with type and target
  - Types: related_to, depends_on, references, same_topic, contradicts, extends
- `update_site`: Context label for where the update occurred
- `last_updated`: ISO timestamp of the update

## Role in Reporter's Ecosystem
- Used to store discovered relationships between memories
- Enables tracking of dependencies and references between agent roles
- Maintains update provenance for audit trails
- Works with `memory_recall` tool for reading memory records

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_