# Universal Agent Knowledge Search

**Status**: Proposed
**Effort**: ~8-10 days (4 phases)
**Priority**: P1

## Problem

Brain Agent has 5+ knowledge sources per agent, all independently searchable through different interfaces. No single search crosses all types. A user looking for "authentication" must check memory recall, chat search, and knowledge map separately.

## Solution: Cmd+K Universal Search Modal

### UI: Cmd+K Command Palette Style

A centered modal (600x500px) triggered by **Cmd+K / Ctrl+K**:
- Search input with auto-focus
- Filter chips: agent selector, type toggles (memory/note/ingested/chat/shared), date range
- Results grouped by type with icons, highlighted snippets, scores, timestamps
- Keyboard navigation (arrows, Enter, Escape)
- "Explore in Graph" button per result → opens Knowledge Map centered on that node

### API: GET /v1/search

Parameters: q, agent, type, project, from, to, limit, mode (lex/vec/hybrid), graph (bool)

Response: grouped results with facets (agent counts, project counts, type counts)

### Search Pipeline

1. **QMD multi-collection query** — single query across agent memories, project notes, ingested docs, chat transcripts
2. **SQLite session metadata** — title + summary search for chat results
3. **Graph enrichment** (optional) — read related frontmatter for each result
4. **Merge + rank** — QMD score primary, recency secondary, graph connectivity bonus

### Implementation Phases

| Phase | Description | Effort |
|---|---|---|
| 1 | Backend /v1/search endpoint with multi-collection QMD query + SQLite + caching | 2-3 days |
| 2 | Cmd+K modal UI with grouped results, filters, keyboard nav | 2-3 days |
| 3 | Graph integration — connections in results, Explore in Graph, focusNode in Knowledge Map | 1-2 days |
| 4 | Advanced — LLM query reformulation, temporal parsing, cross-agent, path finding | 2-3 days |

### Knowledge Sources Searched

| Source | Storage | Index |
|---|---|---|
| Agent memories | agents/{id}/*.md | QMD (agent collection) |
| Project notes | projects/{name}/notes/**/*.md | QMD (project collection) |
| Ingested docs | projects/{name}/ingested/*.md | QMD (project collection) |
| Chat transcripts | chats-indexed/*.md | QMD (agent collection) |
| Session metadata | SQLite sessions table | SQLite LIKE |
| Shared memory | agents/main/*.md | QMD (main collection) |
