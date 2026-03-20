# Feature Proposal: Knowledge Graph Memory

**Status:** Proposed
**Priority:** High
**Effort:** High (~10 days)
**Affects:** `claude_cli.py`, `server.py`, `web/index.html`, `tui.py`, `tools.md`

---

## Problem

Brain Agent's memory system stores facts as flat markdown files indexed by QMD
(BM25 + vector + LLM reranking). Each memory is an isolated document. There is
no way to express relationships between memories -- "this decision relates to
that project", "this person works on these projects", "this bug was caused by
that architecture choice".

### What This Means in Practice

When an agent stores 50+ memories, critical connections are invisible:

```text
User: Who is working on Project Alpha?

main: [searches memory for "Project Alpha"]
      [finds project-alpha.md -- describes the project goals]
      [does NOT find john-smith.md -- mentions Project Alpha but
       QMD ranks it low because the query is about the project,
       not the person]

      Project Alpha is a data pipeline migration project started
      in January. I don't have specific team member information
      stored for this project.
```

The agent has the information -- it just cannot connect the dots.

### Competitive Landscape

- **OpenFang** has knowledge graphs with entity extraction and relationship edges
- **OpenClaw** community builds 12-layer memory architectures with activation
  scores, decay functions, and associative recall
- **MemGPT/Letta** uses tiered memory with explicit cross-references

Brain Agent's memory is powerful for search but blind to structure.

---

## Proposed Solution

Add a **relationship layer** on top of existing markdown memory. Memories link
to each other via YAML frontmatter. A new `mode: "graph"` parameter on
`memory_recall` traverses these relationships. The Web UI gets a Knowledge Map
visualization.

### Design Principles

1. **Additive, not replacing** -- existing memory_store/recall work unchanged
2. **Markdown is still source of truth** -- relationships live in frontmatter
3. **QMD still indexes everything** -- graph traversal is a post-search step
4. **Links are bidirectional in display, unidirectional in storage** -- reverse
   links are computed at query time, not duplicated in files
5. **Graceful degradation** -- if a linked file is deleted, the dangling link
   is ignored (not an error)

---

## Memory File Format

### Current Format

```markdown
---
title: Project Alpha
date: 2026-03-15
tags: [projects, data-pipeline]
---

Project Alpha is a data pipeline migration from Postgres to ClickHouse.
Timeline: Q1-Q2 2026. Budget approved for 3 engineers.
```

### Proposed Format (with relationships)

```markdown
---
title: Project Alpha
date: 2026-03-15
tags: [projects, data-pipeline]
related:
  - file: team-structure-2026-a1b2.md
    rel: team
    label: "Team members"
  - file: clickhouse-evaluation-c3d4.md
    rel: reference
    label: "Tech evaluation"
  - file: john-smith-e5f6.md
    rel: person
    label: "Tech lead"
---

Project Alpha is a data pipeline migration from Postgres to ClickHouse.
Timeline: Q1-Q2 2026. Budget approved for 3 engineers.
```

### Relationship Types

| Type        | Meaning                          | Example                          |
|-------------|----------------------------------|----------------------------------|
| `reference` | General "see also" link          | Decision links to research       |
| `person`    | Links to a person memory         | Project links to team member     |
| `project`   | Links to a project memory        | Person links to their project    |
| `team`      | Links to team/org structure      | Project links to team roster     |
| `caused_by` | Causal relationship              | Bug links to root cause          |
| `follows`   | Temporal sequence                | Phase 2 follows Phase 1          |
| `child`     | Hierarchical parent-child        | Epic links to sub-tasks          |

Custom types are allowed -- the schema is open.

---

## Tool Changes

### memory_store (updated)

New optional `related` parameter:

```json
{
  "name": "memory_store",
  "parameters": {
    "title": "John Smith",
    "content": "Senior engineer. Tech lead on Project Alpha. Joined March 2025.",
    "tags": ["people", "engineering"],
    "related": [
      {"file": "project-alpha-a1b2.md", "rel": "project", "label": "Tech lead"}
    ]
  }
}
```

The agent can also add relationships to existing memories by calling
memory_store with `mode: "link"`:

```json
{
  "name": "memory_store",
  "parameters": {
    "mode": "link",
    "file": "project-alpha-a1b2.md",
    "related": [
      {"file": "john-smith-e5f6.md", "rel": "person", "label": "Tech lead"}
    ]
  }
}
```

### memory_recall (updated)

New optional `mode` parameter:

```
mode: "search"  (default, current behavior -- QMD hybrid search)
mode: "graph"   (search + follow relationships, 1-2 hops)
mode: "links"   (return only direct links of a specific memory)
```

#### Graph Mode Traversal

```
  memory_recall(query="Project Alpha", mode="graph")

  Step 1: QMD search for "Project Alpha"
          => project-alpha-a1b2.md (score: 0.95)

  Step 2: Read frontmatter of top result
          => related: [team-structure-2026-a1b2.md,
                       clickhouse-evaluation-c3d4.md,
                       john-smith-e5f6.md]

  Step 3: Fetch linked memories (1 hop)
          => john-smith-e5f6.md: "Senior engineer. Tech lead..."
          => team-structure-2026-a1b2.md: "Alpha team: John, Sarah, Mike"

  Step 4: Return combined results with relationship context
```

Response format for graph mode:

```json
{
  "results": [
    {
      "file": "project-alpha-a1b2.md",
      "title": "Project Alpha",
      "content": "...",
      "score": 0.95,
      "depth": 0,
      "links": [
        {
          "file": "john-smith-e5f6.md",
          "rel": "person",
          "label": "Tech lead",
          "title": "John Smith",
          "content": "Senior engineer...",
          "depth": 1
        },
        {
          "file": "team-structure-2026-a1b2.md",
          "rel": "team",
          "label": "Team members",
          "title": "Team Structure 2026",
          "content": "Alpha team: John, Sarah, Mike...",
          "depth": 1
        }
      ]
    }
  ]
}
```

### New Tool: memory_graph (optional, stretch goal)

Dedicated tool for graph operations:

```json
{
  "name": "memory_graph",
  "parameters": {
    "action": "neighbors",
    "file": "project-alpha-a1b2.md",
    "depth": 2,
    "rel_types": ["person", "team"]
  }
}
```

---

## Activation and Decay

Memories that are accessed frequently or recently should rank higher in graph
traversal. This borrows from cognitive science (spreading activation networks)
and OpenClaw's decay model.

### Activation Score

Each memory gets an activation score based on:

```
activation = base_score + recency_bonus + access_frequency

where:
  base_score      = QMD search relevance (0.0 - 1.0)
  recency_bonus   = 0.3 * exp(-days_since_last_access / 30)
  access_frequency = 0.2 * min(access_count / 10, 1.0)
```

### Decay

Memories that have not been accessed in 90+ days get a decay penalty:

```
if days_since_last_access > 90:
    decay_penalty = 0.1 * (days_since_last_access - 90) / 365
    activation = max(activation - decay_penalty, 0.1)
```

### Storage

Access metadata stored in a lightweight SQLite table (in chats.db):

```sql
CREATE TABLE memory_access (
    file TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed TEXT,  -- ISO 8601
    created TEXT         -- ISO 8601
);
```

This table is updated every time memory_recall returns a result. No changes
to the markdown files themselves.

---

## QMD Integration

### How QMD Indexes Relationships

QMD indexes the full markdown file including frontmatter. The `related` field
is part of the indexed content, so searching for a filename will surface files
that link to it.

However, for efficient graph traversal, the server maintains an in-memory
adjacency list built at startup by scanning frontmatter:

```python
# Built at startup, refreshed when _qmd_index_keeper detects file changes
_memory_graph: dict[str, list[dict]] = {}
# Example: {"project-alpha-a1b2.md": [{"file": "john-smith-e5f6.md", "rel": "person"}]}

def _rebuild_memory_graph(agent: str):
    """Scan agent memory dir, parse YAML frontmatter, build adjacency list."""
    graph = {}
    for md_file in Path(f"agents/{agent}").glob("*.md"):
        if md_file.name in ("soul.md", "tools.md"):
            continue
        frontmatter = _parse_frontmatter(md_file)
        if "related" in frontmatter:
            graph[md_file.name] = frontmatter["related"]
    return graph

def _reverse_links(graph: dict, target: str) -> list[dict]:
    """Find all memories that link TO the target (reverse edges)."""
    reverse = []
    for source, links in graph.items():
        for link in links:
            if link["file"] == target:
                reverse.append({"file": source, "rel": link["rel"]})
    return reverse
```

### Refresh Strategy

The existing `_qmd_index_keeper` thread (5s mtime poll) triggers graph rebuild
when it detects file changes. This means the graph stays current with minimal
overhead -- no new background threads needed.

---

## Web UI: Knowledge Map

### Main View

```
+------------------------------------------------------------------+
|  Brain Agent                              [main v] [Settings]     |
+------------------------------------------------------------------+
|  Chat  |  Memory  |  Knowledge Map  |  Schedule  |  Skills       |
+------------------------------------------------------------------+
|                                                                    |
|   +-------------------+                                            |
|   | Project Alpha     |----+                                       |
|   | [project]         |    |     +-------------------+             |
|   +-------------------+    +---->| John Smith        |             |
|          |                       | [person]          |             |
|          |                       +-------------------+             |
|          |                              |                          |
|          |     +-------------------+    |                          |
|          +---->| Team Structure    |<---+                          |
|          |     | [team]           |                                |
|          |     +-------------------+                               |
|          |                                                         |
|          |     +-------------------+                               |
|          +---->| ClickHouse Eval  |                                |
|                | [reference]      |                                |
|                +-------------------+                               |
|                                                                    |
|  [Zoom +] [Zoom -] [Fit] [Filter: all v]     12 memories, 8 links|
+------------------------------------------------------------------+
```

### Implementation

- **Library:** D3.js force-directed graph (loaded from CDN, ~80KB)
- **Nodes:** Rounded rectangles with title and type badge
- **Edges:** Labeled arrows showing relationship type
- **Interaction:** Click node to view memory content in sidebar, drag to
  rearrange, scroll to zoom, filter by relationship type
- **Colors:** Type-based coloring (person=blue, project=green, reference=gray)
- **Data source:** New endpoint `GET /v1/memory/graph?agent=main`

### Memory Detail Sidebar

When a memory is selected (clicked in Knowledge Map or viewed in Memory tab):

```
+------------------------------------------------------------------+
|  Memory: Project Alpha                           [Edit] [Delete]  |
+------------------------------------------------------------------+
|                                                                    |
|  Project Alpha is a data pipeline migration from Postgres to      |
|  ClickHouse. Timeline: Q1-Q2 2026. Budget approved.              |
|                                                                    |
|  Tags: projects, data-pipeline                                    |
|  Created: 2026-03-15                                              |
|  Last accessed: 2026-03-20 (accessed 14 times)                   |
|                                                                    |
|  --- Related Memories ---                                         |
|                                                                    |
|  -> [person] John Smith          "Tech lead"                      |
|  -> [team]   Team Structure 2026 "Team members"                   |
|  -> [ref]    ClickHouse Eval     "Tech evaluation"                |
|                                                                    |
|  <- [project] John Smith         (links back to this)             |
|  <- [project] Sarah Chen         (links back to this)             |
|                                                                    |
+------------------------------------------------------------------+
```

The `->` arrows are outgoing links (stored in this file's frontmatter).
The `<-` arrows are incoming links (computed via reverse lookup).

---

## TUI: Memory Graph Command

### /memory graph

```
$ /memory graph project-alpha

  Project Alpha
  ├── [person] John Smith — "Tech lead"
  │   ├── [project] Project Beta — "Secondary assignment"
  │   └── [team] Engineering Team — "Member"
  ├── [team] Team Structure 2026 — "Team members"
  │   ├── [person] Sarah Chen
  │   └── [person] Mike Johnson
  └── [reference] ClickHouse Evaluation — "Tech evaluation"

  6 memories, 7 relationships (depth: 2)
```

### /memory links <file>

```
$ /memory links project-alpha-a1b2.md

  Outgoing (3):
    -> [person]    john-smith-e5f6.md        "Tech lead"
    -> [team]      team-structure-2026-a1b2.md "Team members"
    -> [reference] clickhouse-evaluation-c3d4.md "Tech evaluation"

  Incoming (2):
    <- [project]   john-smith-e5f6.md        (reverse)
    <- [project]   sarah-chen-g7h8.md        (reverse)
```

---

## End-to-End Workflow

### Step 1: Agent stores Project Alpha

```
User: Remember that Project Alpha is our data pipeline migration
      from Postgres to ClickHouse, starting Q1 2026.

main: [calls memory_store with title="Project Alpha", content="...",
       tags=["projects", "data-pipeline"]]
      => Stored as agents/main/project-alpha-a1b2.md
```

### Step 2: Agent stores John with relationship

```
User: John Smith is the tech lead on Project Alpha.

main: [calls memory_store with title="John Smith",
       content="Senior engineer. Tech lead on Project Alpha.",
       tags=["people"],
       related=[{file: "project-alpha-a1b2.md", rel: "project",
                 label: "Tech lead"}]]
      => Stored as agents/main/john-smith-e5f6.md
         with frontmatter linking to project-alpha.md

      [also calls memory_store mode="link" to add reverse link
       on project-alpha-a1b2.md pointing to john-smith-e5f6.md]
```

### Step 3: User asks a connected question

```
User: Who is working on Project Alpha?

main: [calls memory_recall(query="Project Alpha", mode="graph")]

      QMD search: project-alpha-a1b2.md (score: 0.95)
      Graph hop 1: john-smith-e5f6.md (rel: person, "Tech lead")
                   team-structure-2026-a1b2.md (rel: team)
      Graph hop 1 from team-structure:
                   sarah-chen-g7h8.md, mike-johnson-i9j0.md

main: The Project Alpha team consists of:
      - John Smith (Tech lead) — Senior engineer
      - Sarah Chen — Frontend engineer
      - Mike Johnson — Data engineer

      The project is a Postgres to ClickHouse migration,
      timeline Q1-Q2 2026.
```

### Step 4: User explores in Web UI

The user opens Knowledge Map, sees the cluster of related memories around
Project Alpha, clicks on John Smith's node, and sees all his project
associations in the sidebar.

---

## Server API Changes

### New Endpoints

```
GET /v1/memory/graph?agent=main
    Returns full graph structure for visualization.
    Response: {nodes: [{id, title, tags, type, activation}],
               edges: [{source, target, rel, label}]}

GET /v1/memory/graph?agent=main&root=project-alpha-a1b2.md&depth=2
    Returns subgraph rooted at a specific memory.
```

### Modified Endpoints

```
POST /v1/chat
    memory_recall tool now accepts mode parameter.
    memory_store tool now accepts related parameter.
```

---

## Benefits

1. **Richer context** -- agents connect dots across memories automatically
2. **Better recall accuracy** -- graph traversal finds related info that keyword
   search misses
3. **Visual exploration** -- Knowledge Map gives users insight into what the
   agent knows and how it is connected
4. **Competitive parity** -- matches OpenFang/OpenClaw graph memory capabilities
5. **Cognitive model** -- activation/decay mirrors how human memory works,
   surfacing frequently-used information naturally
6. **Backward compatible** -- existing memories without `related` fields work
   exactly as before

## Trade-offs

1. **Complexity** -- relationship management adds cognitive load for agents;
   they must decide when and how to link memories
2. **Link maintenance** -- when a memory is deleted, dangling links must be
   handled (solved: ignore missing targets)
3. **Frontmatter bloat** -- heavily-linked memories get long frontmatter
   (mitigated: max 20 links per memory)
4. **Graph traversal cost** -- following 2 hops on a large graph could be slow
   (mitigated: in-memory adjacency list, max depth limit)
5. **Agent prompt complexity** -- tools.md grows with graph mode instructions
6. **D3.js dependency** -- adds ~80KB to web UI (loaded from CDN, acceptable)

## Effort Breakdown

| Task                                  | Days |
|---------------------------------------|------|
| Frontmatter schema + parser           | 1    |
| memory_store related parameter        | 1    |
| memory_recall graph mode              | 2    |
| In-memory graph + rebuild logic       | 1    |
| Activation/decay tracking             | 1    |
| Web UI Knowledge Map (D3.js)          | 2    |
| Web UI memory sidebar links           | 0.5  |
| TUI /memory graph command             | 0.5  |
| API endpoints                         | 0.5  |
| tools.md updates + testing            | 0.5  |
| **Total**                             | **10** |

## Open Questions

1. Should agents auto-detect relationships (entity extraction) or only create
   them when explicitly told? Auto-detection is powerful but error-prone.
2. Maximum graph depth for traversal -- 2 hops seems right for now, but some
   use cases might benefit from 3.
3. Should the graph be per-agent or global? Per-agent matches current memory
   scoping, but cross-agent links could be valuable for teams.
4. Should activation scores be visible to the agent in recall results, or
   only used internally for ranking?
