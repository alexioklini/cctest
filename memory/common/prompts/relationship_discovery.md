---
id: relationship_discovery
parent: Memory System
created: 2026-02-01T04:17:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: related_to
    target: memory:common/prompts/memory_store.md
  - type: related_to
    target: memory:common/prompts/memory_recall.md
  - type: related_to
    target: memory:common/prompts/_relationship_discovery_reporter.md
  - type: depends_on
    target: memory:common/prompts/memory_store.md
  - type: depends_on
    target: memory:common/prompts/memory_recall.md
update_site: Reporter Relationship Discovery
---

# Relationship Discovery Process

The process for discovering and storing relationships between memory records.

## Core Workflow

### Step 1: Discovery
1. Use `memory_recall` with empty query to retrieve all memories
2. Analyze content to identify related memories
3. Determine meaningful relationships based on content analysis

### Step 2: Relationship Classification
Classify relationships using these types:
- **same_topic** - Memories covering the same subject
- **related_to** - General connection between memories
- **depends_on** - One memory structurally depends on another
- **references** - Memory explicitly references another
- **contradicts** - Memories contain contradictory information
- **extends** - One memory extends the information in another

### Step 3: Write Relationships
Use `memory_store` to write discovered relationships back to memory records:
- Store frontmatter with relationships field
- Include update context and timestamps
- Maintain provenance for audit trails

## Scheduled Task Integration
The `_relationship_discovery_Reporter` scheduled task performs this process:
- Runs daily at scheduled intervals
- Focuses on Reporter-related memories
- Updates relationship frontmatter for identified connections

## Technical Dependencies

Depends on both foundational memory tools:
- **memory_store**: For writing relationships after discovery
- **memory_recall**: For reading memories to analyze relationships

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_