# Feature Proposal: Code Structure Graph (AST-Based Knowledge Graph for Code)

**Status:** Proposal (P1)
**Author:** Brain Agent Team
**Date:** 2026-03-23
**Effort Estimate:** 8-10 days
**Inspired by:** [code-review-graph](https://github.com/tirth8205/code-review-graph)

---

## Problem Statement

The Coder agent currently has no structural understanding of codebases it works with.
When asked to modify a function, it must read files, grep for references, and hope it
finds all callers. It has no way to answer:

- "What functions call `process_order()`?"
- "If I change this class, what else breaks?"
- "Which functions in this module have no tests?"
- "Show me the import dependency tree for this file"

The existing Knowledge Graph handles *semantic* relationships between memories and notes
(via LLM extraction). But code relationships are *structural* — they can be extracted
deterministically from AST parsing, with zero LLM cost and perfect accuracy.

## Proposed Solution

Add a **Code Structure Graph** layer alongside the existing Memory KG. Built via
Tree-sitter AST parsing, stored in SQLite, traversed with NetworkX. Integrated into
the existing knowledge graph visualization and hook system.

### Architecture

```
Source files
    │
    ▼ (Tree-sitter AST parsing)
┌─────────────────────────┐
│ CodeGraph (code-graph.db)│
│                          │
│ Nodes: File, Class,      │
│   Function, Test, Type   │
│                          │
│ Edges: CALLS, IMPORTS,   │
│   INHERITS, IMPLEMENTS,  │
│   CONTAINS, TESTED_BY    │
│                          │
│ NetworkX for traversal   │
└─────────────────────────┘
    │
    ├── Agent tools (code_graph_query, code_graph_impact, code_graph_build)
    ├── Knowledge Map visualization (merged with memory KG)
    └── after_file_write hook (incremental updates)
```

### What Exists vs What's New

| Capability | Existing (Memory KG) | New (Code Graph) |
|---|---|---|
| Storage | QMD (markdown + BM25 + vector) | SQLite + NetworkX |
| Parsing | LLM-based entity extraction | Deterministic Tree-sitter AST |
| Node types | Memories, notes, chat transcripts | Files, classes, functions, tests, types |
| Edge types | same_topic, references, extends | CALLS, IMPORTS, INHERITS, CONTAINS, TESTED_BY |
| Update trigger | after_file_write hook | Same hook (for .py/.js/.ts/etc files) |
| Visualization | Canvas 2D force-directed | Same canvas (merged view) |
| Query | QMD semantic search | Structural graph queries (callers, callees, impact) |

Both graphs coexist. The Memory KG captures *what the agent knows*. The Code Graph
captures *how the code is structured*.

---

## Graph Schema

### Node Types

| Kind | What | Key Fields |
|---|---|---|
| `file` | Source file | `language`, `line_count`, `file_hash` (SHA-256) |
| `class` | Class, struct, interface, enum, module | `modifiers`, `parent_class` |
| `function` | Function, method, constructor | `params`, `return_type`, `parent_class` |
| `test` | Test function | Same as function, `is_test=true` |
| `type` | Type alias, interface, enum | `line_start`, `line_end` |

### Edge Types

| Kind | Meaning | Example |
|---|---|---|
| `CALLS` | Function calls another | `app.py::handle_request` → `db.py::query` |
| `IMPORTS_FROM` | File imports module | `app.py` → `flask` |
| `INHERITS` | Class extends another | `Admin` → `User` |
| `IMPLEMENTS` | Class implements interface | `SqlStore` → `Store` |
| `CONTAINS` | Structural containment | `app.py` → `handle_request` |
| `TESTED_BY` | Function has test coverage | `query` → `test_query` |

### Qualified Names

Every node has a unique qualified name: `{file_path}::{ClassName.method_name}`

Examples:
- `server.py::BrainAgentHandler.do_GET`
- `claude_cli.py::MemoryStore.recall`
- `web/index.html` (file node)

---

## Supported Languages

14 languages via `tree-sitter-language-pack`:

Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, C#, Ruby, Kotlin,
Swift, PHP

Each language has specific AST node type mappings for classes, functions, imports,
and calls.

---

## Agent Tools (3)

### 1. `code_graph_build`

Build or rebuild the code graph for a directory.

```json
{
  "name": "code_graph_build",
  "input_schema": {
    "properties": {
      "path": { "type": "string", "description": "Directory to parse" },
      "incremental": { "type": "boolean", "description": "Only re-parse changed files (default: true)" }
    },
    "required": ["path"]
  }
}
```

Returns: node count, edge count, files parsed, languages detected.

### 2. `code_graph_query`

Query the code graph for structural relationships.

```json
{
  "name": "code_graph_query",
  "input_schema": {
    "properties": {
      "query_type": {
        "type": "string",
        "enum": ["callers_of", "callees_of", "imports_of", "importers_of",
                 "tests_for", "inheritors_of", "children_of", "file_summary"],
        "description": "Type of structural query"
      },
      "target": { "type": "string", "description": "Qualified name or function/class name to query" },
      "limit": { "type": "integer", "description": "Max results (default: 20)" }
    },
    "required": ["query_type", "target"]
  }
}
```

### 3. `code_graph_impact`

Blast-radius analysis: what's affected if files/functions change.

```json
{
  "name": "code_graph_impact",
  "input_schema": {
    "properties": {
      "files": {
        "type": "array", "items": { "type": "string" },
        "description": "List of changed file paths"
      },
      "depth": { "type": "integer", "description": "Max traversal depth (default: 2)" }
    },
    "required": ["files"]
  }
}
```

Returns: changed nodes, impacted nodes, impacted files, untested changes, warnings.

---

## Incremental Updates

### Via after_file_write Hook

The existing `_after_file_write()` pipeline already fires after every `write_file` and
`edit_file`. The code graph hooks into this:

```python
def _after_file_write(path, action, agent_id):
    _maybe_qmd_reindex(path)         # existing
    _extract_entities(...)            # existing
    _maybe_update_code_graph(path)    # NEW
    _emit_file_event(...)             # existing
    _run_external_hooks(...)          # existing
```

### Change Detection

- File SHA-256 hash stored in `file_hash` column
- Skip re-parsing if hash unchanged
- When a file changes, also re-parse its dependents (files that import it)
- Git-aware: `git diff --name-only` for bulk change detection

---

## Visualization Integration

Merge code graph nodes into the existing Knowledge Map canvas:

### New Node Colors

| Type | Color | Existing KG Equivalent |
|---|---|---|
| File | `#3b82f6` (blue) | — |
| Class | `#f59e0b` (amber) | Similar to `project` |
| Function | `#22c55e` (green) | — |
| Test | `#a855f7` (purple) | — |
| Type | `#6b7280` (gray) | — |

### Filter Chips

Add `file`, `class`, `function`, `test`, `type` filter chips alongside existing
`general`, `feedback`, `project`, `reference`, `note`, `chat_transcript` chips.

### Toggle

"Code Graph" toggle in the Knowledge Map header to show/hide code nodes
(separate from memory nodes). Default: shown when Coder agent is selected.

---

## Implementation Plan

### Phase 1 — Core Parser + Graph (~4 days)

1. **Dependencies**: Add `tree-sitter`, `tree-sitter-language-pack`, `networkx` to requirements
2. **`CodeParser` class**: Tree-sitter AST walking, language-specific node type mappings,
   name extraction, call resolution, import resolution
3. **`CodeGraph` class**: SQLite schema (nodes, edges, metadata tables), insert/query methods,
   NetworkX BFS for blast-radius analysis, incremental update with hash-based skip
4. **14 language mappings**: class types, function types, import types, call types per language

### Phase 2 — Agent Tools (~1.5 days)

1. `code_graph_build` — parse directory, build graph
2. `code_graph_query` — 8 query patterns (callers, callees, imports, tests, etc.)
3. `code_graph_impact` — blast-radius BFS with warnings
4. Tool definitions, dispatch, icons, verbs, READONLY classification

### Phase 3 — Hook Integration (~0.5 days)

1. Add `_maybe_update_code_graph(path)` to `_after_file_write()` pipeline
2. Detect source file extensions, skip non-code files
3. Re-parse changed file + dependents incrementally

### Phase 4 — Visualization (~1.5 days)

1. Merge code graph nodes into `/v1/agents/{id}/graph` API response
2. Add code-specific colors and filter chips to Knowledge Map
3. Code Graph toggle in map header
4. Node detail panel shows function signature, callers, callees

### Phase 5 — Server API + Settings (~1 day)

1. `GET/POST /v1/code-graph/config` — settings (enabled, languages, excluded dirs)
2. `GET /v1/code-graph/stats` — node/edge counts, language distribution
3. `POST /v1/code-graph/build` — trigger build from web UI
4. Settings tab or section in Coder agent config

**Total: ~8.5 days**

---

## Dependencies

```
tree-sitter>=0.23,<1
tree-sitter-language-pack>=0.3,<1
networkx>=3.2,<4
```

All pip-installable, no external services needed. Tree-sitter runs locally, no API costs.

---

## Configuration

Per-agent in `agent.json`:

```json
{
  "code_graph": {
    "enabled": true,
    "root_dirs": ["/path/to/project"],
    "exclude_dirs": ["node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"],
    "exclude_patterns": ["*.min.js", "*.generated.*"],
    "languages": ["python", "javascript", "typescript"],
    "max_file_size": 500000,
    "incremental": true
  }
}
```

---

## Benefits

- **Zero LLM cost** — AST parsing is deterministic, no API calls for graph construction
- **Perfect accuracy** — Tree-sitter parses actual syntax, not LLM guesswork
- **Blast-radius analysis** — "change X, Y breaks" before making changes
- **Test coverage visibility** — instantly see which functions lack tests
- **Token savings** — agent reads only impacted files, not the whole codebase
- **14 languages** — covers most production codebases
- **Incremental** — sub-second updates via file hash diffing
- **Integrated** — same visualization, same hook system, same tool pattern as existing KG

## Risks

- **tree-sitter dependency** — ~300MB for language pack binaries. Mitigated: optional install
- **Large codebases** — 100K+ files could slow initial build. Mitigated: incremental updates, exclude patterns
- **Cross-file resolution** — import resolution is heuristic-based (filesystem paths). Won't resolve all dynamic imports
- **Language coverage** — 14 languages covers most but not all (missing: Elixir, Haskell, Scala, etc.)
