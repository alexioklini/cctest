# Feature Proposal: Projects + Document Ingestion + Knowledge Graph

**Status**: Proposed
**Effort**: ~25 days
**Priority**: P1
**Author**: Brain Agent Team
**Date**: 2026-03-20

---

## Problem Statement

Brain Agent's memory system is built on manually-curated markdown files
indexed by QMD. This works well for agent-generated memories but creates
several gaps:

- No way to ingest external documents (PDFs, DOCX, URLs) into agent knowledge
- No concept of "projects" — all work with an agent shares the same flat context
- No watched folders that auto-sync external content into agent memory
- No relationships between memories (flat files, no graph)
- Users can't scope a conversation to a specific project's documents

Similar to Claude Desktop's Projects feature, users need a way to organize
work into projects with scoped documents, folders, and conversations.

---

## Projects Architecture

### Concept

A **Project** belongs to an agent and defines a scope for conversations:

```
Agent (e.g., Researcher)
  ├── Agent-level memories (*.md) — always available
  ├── Agent-level documents (ingested/) — always available
  └── Projects/
      ├── ml-research/
      │   ├── project.json (config, description, folders)
      │   ├── ingested/ (project-specific documents)
      │   └── *.md (project-specific memories)
      └── company-handbook/
          ├── project.json
          ├── ingested/
          └── *.md
```

### Chat Scoping

```
+------------------------------------------------------------------+
|  Chat WITHOUT project (current behavior):                         |
|    Context = agent memories + main shared memories                 |
+------------------------------------------------------------------+
|  Chat WITH project selected:                                      |
|    Context = project docs + project memories                      |
|             + agent memories + main shared memories               |
+------------------------------------------------------------------+
```

### project.json

```json
{
  "name": "ML Research",
  "description": "Machine learning papers and experiments",
  "created_at": "2026-03-20T10:00:00Z",
  "watch_folders": [
    {
      "path": "/Users/alexander/Papers/ml",
      "pattern": "*.pdf",
      "recursive": true
    }
  ],
  "documents": [
    "attention-is-all-you-need.pdf",
    "scaling-laws.pdf"
  ],
  "tags": ["ml", "research"],
  "model": null
}
```

### Directory Structure

```
agents/Researcher/
  soul.md
  agent.json
  commands.json
  *.md                          ← agent-level memories
  ingested/                     ← agent-level ingested docs
    ingest-a3f8c2-001.md
    ingest-a3f8c2-002.md
  projects/
    ml-research/
      project.json              ← project config
      *.md                      ← project-specific memories
      ingested/                 ← project-specific ingested docs
        ingest-b7e1d4-001.md
    company-handbook/
      project.json
      ingested/
        ingest-c9d2e1-001.md
        ...
```

### QMD Collections

Each project gets its own QMD collection for scoped search:

```
Collections:
  Researcher          → agents/Researcher/*.md + agents/Researcher/ingested/
  Researcher/ml-research → agents/Researcher/projects/ml-research/
  Researcher/company-handbook → agents/Researcher/projects/company-handbook/
```

When chatting with a project, `memory_recall` searches:
1. Project collection (highest priority)
2. Agent collection
3. Main collection (shared memory)

### Web UI: Project Management

```
+-----------------------------------------------------------------------+
| Researcher                                                            |
+-----------------------------------------------------------------------+
| [Chat] [Projects] [Config]                                            |
+-----------------------------------------------------------------------+
|                                                                       |
|  Projects                                              [+ New Project]|
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | ML Research                                          [Active]  |   |
|  | Machine learning papers and experiments                        |   |
|  | 3 documents · 2 watched folders · 45 chunks indexed            |   |
|  | [Open Chat] [Manage] [Delete]                                  |   |
|  +---------------------------------------------------------------+   |
|  | Company Handbook                                               |   |
|  | Internal policies and procedures                               |   |
|  | 1 document · 102 chunks indexed                                |   |
|  | [Open Chat] [Manage] [Delete]                                  |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Web UI: Project Detail / Manage

```
+-----------------------------------------------------------------------+
| Project: ML Research                                             [X]  |
+-----------------------------------------------------------------------+
| [Documents] [Folders] [Settings]                                      |
+-----------------------------------------------------------------------+
|                                                                       |
|  Documents                                      [Upload] [Ingest URL] |
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | attention-is-all-you-need.pdf    PDF  28 chunks  2026-03-18   |   |
|  | scaling-laws.pdf                 PDF  22 chunks  2026-03-17   |   |
|  | https://arxiv.org/abs/2301.00    URL   5 chunks  2026-03-16   |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|  Watched Folders                                            [+ Add]   |
|  +---------------------------------------------------------------+   |
|  | /Users/alexander/Papers/ml   *.pdf (recursive)  12 files      |   |
|  | Last scan: 30s ago                          [Remove] [Rescan]  |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Web UI: Chat with Project Selected

```
+-----------------------------------------------------------------------+
| Researcher · ML Research                              [Execute ▾]     |
+-----------------------------------------------------------------------+
| [No Project] [ML Research ✓] [Company Handbook]      ← project tabs   |
+-----------------------------------------------------------------------+
|                                                                       |
|  User: What does the attention paper say about multi-head attention?   |
|                                                                       |
|  Recalling from ML Research project...                                |
|  ✔ 3 memories (project: 2, agent: 1)                                 |
|                                                                       |
|  According to "Attention Is All You Need" (pages 4-5), multi-head     |
|  attention allows the model to jointly attend to information from      |
|  different representation subspaces...                                 |
|                                                                       |
+-----------------------------------------------------------------------+
```

### TUI: Project Commands

```
/projects                     List projects for current agent
/projects new <name>          Create a new project
/projects open <name>         Switch to project context (scoped chat)
/projects close               Return to agent-level chat (no project)
/projects delete <name>       Delete project and all its data
/projects ingest <file>       Ingest document into current project
/projects watch <path>        Add watched folder to current project
```

### API Endpoints

```
GET    /v1/agents/{id}/projects              List projects
POST   /v1/agents/{id}/projects              Create project
GET    /v1/agents/{id}/projects/{name}       Get project details
PUT    /v1/agents/{id}/projects/{name}       Update project config
DELETE /v1/agents/{id}/projects/{name}       Delete project
POST   /v1/agents/{id}/projects/{name}/ingest  Ingest document into project
GET    /v1/agents/{id}/projects/{name}/docs  List project documents
```

### Chat Integration

When `/v1/chat` receives `project: "ml-research"` in the request body:
1. System prompt includes: "You are working in project 'ML Research'. Prioritize project-specific documents."
2. `memory_recall` searches project collection first, then agent, then main
3. `memory_store` writes to project directory (not agent root)
4. Project context is shown in the UI header

---

## Document Ingestion Pipeline

(The ingestion pipeline serves both agent-level and project-level documents)

### Overview

A document ingestion pipeline that accepts files (PDF, DOCX, TXT, MD, HTML)
or URLs, extracts text, chunks it intelligently, stores chunks as memory
files, and triggers QMD indexing for hybrid search retrieval.

---

## Proposed Solution

### Overview

A document ingestion pipeline that accepts files (PDF, DOCX, TXT, MD, HTML)
or URLs, extracts text, chunks it intelligently, stores chunks as memory
files in the agent's directory, and triggers QMD indexing for hybrid search
retrieval.

### Architecture

```
                          +------------------+
  Upload (Web UI)  -----> |                  |
  /ingest (TUI)   -----> |  Ingestion API   |
  POST /ingest     -----> |  (server.py)     |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |     Parser       |
                          |  PDF -> text     |
                          |  DOCX -> text    |
                          |  HTML -> text    |
                          |  URL -> fetch    |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |     Chunker      |
                          |  Split into      |
                          |  ~1500 token     |
                          |  chunks with     |
                          |  200 token       |
                          |  overlap         |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |  Memory Store    |
                          |  Write .md files |
                          |  with frontmatter|
                          |  to agents/<id>/ |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |   QMD Index      |
                          |  Auto-detected   |
                          |  by file watcher |
                          |  (5s poll cycle) |
                          +------------------+
```

---

## Supported Formats

| Format | Extension   | Parser                  | Notes                        |
|--------|-------------|-------------------------|------------------------------|
| PDF    | .pdf        | `pymupdf` (fitz)        | Text + OCR fallback          |
| DOCX   | .docx       | `python-docx`           | Paragraphs, tables, headers  |
| TXT    | .txt        | Built-in                | Direct read, encoding detect |
| MD     | .md         | Built-in                | Preserve as-is               |
| HTML   | .html       | `html.parser` (stdlib)  | Strip tags, keep structure   |
| URL    | http(s)://  | `web_fetch` + HTML parse| Uses existing web_fetch tool |

### Parser Dependencies

```bash
# Required for PDF support
pip3 install pymupdf

# Required for DOCX support
pip3 install python-docx

# HTML and TXT: no additional dependencies (stdlib)
```

PDF and DOCX parsers are optional — if not installed, those formats return a
clear error: "Install pymupdf for PDF support: pip3 install pymupdf".

---

## Chunking Strategy

### Parameters

```
+-------------------------------------------+
| Chunking Configuration                    |
+-------------------------------------------+
| chunk_size:     1500 tokens (~6000 chars) |
| chunk_overlap:  200 tokens  (~800 chars)  |
| min_chunk_size: 100 tokens  (~400 chars)  |
| split_on:       paragraph > sentence > char|
+-------------------------------------------+
```

### Algorithm

1. Extract full text from document
2. Split into paragraphs (double newline boundaries)
3. Accumulate paragraphs into chunks until `chunk_size` is reached
4. When a chunk is full, include `chunk_overlap` tokens from the end
   of the current chunk as the start of the next chunk
5. If a single paragraph exceeds `chunk_size`, split on sentence boundaries
6. If a single sentence exceeds `chunk_size`, split on word boundaries
7. Discard chunks smaller than `min_chunk_size` (likely artifacts)
8. Preserve section headers: prepend the last-seen header to each chunk

### Example: How Overlap Works

```
Document text:
  [====== Paragraph 1 ======] [=== Paragraph 2 ===] [==== Paragraph 3 ====]
  [========= Paragraph 4 =========] [=== Paragraph 5 ===] [== Paragraph 6 ==]

Chunks (with overlap):
  Chunk 1: [P1] [P2] [P3]
  Chunk 2:          [P3*] [P4]           (* = overlap, last 200 tokens of P3)
  Chunk 3:                    [P4*] [P5] [P6]
```

Overlap ensures that information at chunk boundaries is not lost during
retrieval. QMD's semantic search can match on overlapping content from
either neighboring chunk.

---

## Generated Memory File Format

Each chunk becomes a markdown file in the agent's directory:

### Filename Convention

```
agents/<agent>/ingest-<source_hash>-<chunk_index>.md
```

Example: `agents/Researcher/ingest-a3f8c2-003.md`

The `source_hash` is a 6-character hash of the original filename/URL,
ensuring all chunks from the same document share a prefix. This matches
the existing memory filename pattern using hash suffixes for uniqueness.

### File Content

```markdown
---
title: "Company Handbook - Chapter 3: Engineering Practices"
source: "company-handbook.pdf"
source_type: pdf
ingested_at: "2026-03-20T14:30:00Z"
chunk_index: 3
total_chunks: 45
page_range: "23-27"
agent: Researcher
tags:
  - ingested
  - company-handbook
---

## Chapter 3: Engineering Practices

### 3.1 Code Review Process

All code changes must go through peer review before merging. The review
process follows these steps:

1. Author creates a pull request with a clear description
2. At least one reviewer from the team must approve
3. CI/CD pipeline must pass all checks
4. Author addresses all comments before merging

### 3.2 Testing Standards

Unit test coverage must exceed 80% for all new code. Integration tests
are required for any feature that touches external APIs or databases.

[Content continues with the actual extracted text from pages 23-27...]
```

### Frontmatter Fields

| Field          | Type    | Description                                |
|----------------|---------|-------------------------------------------|
| `title`        | string  | Section header or "Source - Chunk N"       |
| `source`       | string  | Original filename or URL                   |
| `source_type`  | string  | pdf, docx, txt, md, html, url              |
| `ingested_at`  | string  | ISO 8601 timestamp                         |
| `chunk_index`  | integer | 0-based chunk position                     |
| `total_chunks` | integer | Total chunks from this source              |
| `page_range`   | string  | PDF page numbers (PDF only)                |
| `agent`        | string  | Agent that owns this memory                |
| `tags`         | list    | Always includes "ingested" + source name   |

---

## Web UI: Ingestion Interface

### Ingest Button in Agent Memory Section

```
+-----------------------------------------------------------------------+
| Memory: Researcher                               [+ Store] [^ Ingest] |
+-----------------------------------------------------------------------+
|                                                                       |
|  Search memories: [________________________] [Search]                 |
|                                                                       |
|  +--- Memory Files -------------------------------------------+      |
|  | Name                          Source      Size    Updated   |      |
|  |-------------------------------------------------------------|     |
|  | competitor-analysis.md        agent       4.2K    2h ago    |      |
|  | market-research-notes.md      agent       2.8K    1d ago    |      |
|  | ingest-a3f8c2-001.md          PDF         5.1K    3d ago    |      |
|  | ingest-a3f8c2-002.md          PDF         4.8K    3d ago    |      |
|  | ...44 more from company-handbook.pdf                        |      |
|  | ingest-b7e1d4-001.md          URL         3.2K    5d ago    |      |
|  | ingest-b7e1d4-002.md          URL         2.9K    5d ago    |      |
|  +-------------------------------------------------------------+     |
+-----------------------------------------------------------------------+
```

### Drag-and-Drop Upload Dialog

```
+-----------------------------------------------------------------------+
| Ingest Document into Researcher's Memory                         [X]  |
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  |                                                               |   |
|  |     +------+                                                  |   |
|  |     | FILE |   Drop files here or click to browse             |   |
|  |     +------+                                                  |   |
|  |                                                               |   |
|  |     Supported: PDF, DOCX, TXT, MD, HTML                      |   |
|  |     Max size: 50MB                                            |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|  Or paste a URL:                                                      |
|  [https://_______________________________________________] [Fetch]    |
|                                                                       |
|  --- Options ---                                                      |
|  Chunk size:    [1500] tokens                                         |
|  Chunk overlap: [200 ] tokens                                         |
|  Tags:          [                    ] (comma-separated)              |
|                                                                       |
|  [Cancel]                                        [Ingest Document]    |
+-----------------------------------------------------------------------+
```

### Ingestion Progress

```
+-----------------------------------------------------------------------+
| Ingesting: company-handbook.pdf (4.2 MB)                              |
+-----------------------------------------------------------------------+
|                                                                       |
|  [1/4] Parsing PDF...                                     done        |
|        Extracted 52,340 words from 198 pages                          |
|                                                                       |
|  [2/4] Chunking text...                                   done        |
|        Created 45 chunks (avg 1,163 words each)                       |
|                                                                       |
|  [3/4] Storing memory files...                            done        |
|        Wrote 45 files to agents/Researcher/                           |
|                                                                       |
|  [4/4] QMD indexing...                               in progress      |
|        [=========================>              ] 28/45 embedded       |
|                                                                       |
|  Estimated time remaining: ~30 seconds                                |
+-----------------------------------------------------------------------+
```

### Ingested Documents List

```
+-----------------------------------------------------------------------+
| Ingested Documents: Researcher                                        |
+-----------------------------------------------------------------------+
|                                                                       |
|  +--- Documents -------------------------------------------------+   |
|  | Source                    Type  Chunks  Size    Ingested        |   |
|  |--------------------------------------------------------------- |  |
|  | company-handbook.pdf      PDF     45    4.2MB   2026-03-17     |   |
|  |   Status: 45/45 indexed, 45/45 embedded                       |   |
|  |   [Re-ingest]  [Delete All Chunks]                             |   |
|  |                                                                |   |
|  | https://blog.example.com  URL      3    28KB    2026-03-15     |   |
|  |   Status: 3/3 indexed, 3/3 embedded                           |   |
|  |   [Re-ingest]  [Delete All Chunks]                             |   |
|  |                                                                |   |
|  | meeting-notes.docx        DOCX    12    1.1MB   2026-03-10     |   |
|  |   Status: 12/12 indexed, 12/12 embedded                       |   |
|  |   [Re-ingest]  [Delete All Chunks]                             |   |
|  +----------------------------------------------------------------+  |
|                                                                       |
|  Total: 3 documents, 60 chunks, 5.3 MB source data                   |
+-----------------------------------------------------------------------+
```

---

## TUI: /ingest Command

```
$ /ingest ~/Documents/company-handbook.pdf

Ingesting: company-handbook.pdf (4.2 MB)
  Parsing PDF... 198 pages, 52,340 words
  Chunking... 45 chunks (avg 1,163 words, 200 token overlap)
  Storing... 45 memory files written to agents/Researcher/
  QMD indexing... waiting for background embed (auto, ~60s)

Done. 45 chunks ingested. Use /recall to search.

$ /ingest https://blog.example.com/ai-agents-2026

Fetching URL... 28KB downloaded
  Parsing HTML... 3,450 words extracted
  Chunking... 3 chunks
  Storing... 3 memory files written
  QMD indexing... waiting for background embed

Done. 3 chunks ingested.

$ /ingest --list

Ingested Documents (Researcher):
  company-handbook.pdf     PDF   45 chunks  2026-03-17
  https://blog.example.com URL    3 chunks  2026-03-15
  meeting-notes.docx       DOCX  12 chunks  2026-03-10

$ /ingest --delete company-handbook.pdf

Delete 45 chunks from company-handbook.pdf? [y/N] y
  Deleted 45 memory files
  QMD will remove from index on next cycle (~5s)
Done.
```

---

## API Endpoint

### POST /v1/agents/{agent}/ingest

Multipart file upload or URL ingestion.

**File Upload:**

```
POST /v1/agents/Researcher/ingest
Content-Type: multipart/form-data

file: <binary file data>
chunk_size: 1500          (optional, default 1500)
chunk_overlap: 200        (optional, default 200)
tags: handbook,reference  (optional, comma-separated)
```

**URL Ingestion:**

```
POST /v1/agents/Researcher/ingest
Content-Type: application/json

{
  "url": "https://blog.example.com/article",
  "chunk_size": 1500,
  "chunk_overlap": 200,
  "tags": ["blog", "reference"]
}
```

**Response (SSE stream for progress):**

```
event: progress
data: {"step": "parsing", "detail": "198 pages, 52340 words"}

event: progress
data: {"step": "chunking", "detail": "45 chunks created"}

event: progress
data: {"step": "storing", "detail": "45 files written"}

event: progress
data: {"step": "indexing", "detail": "waiting for QMD embed"}

event: done
data: {"source": "company-handbook.pdf", "chunks": 45, "files": [...]}
```

### GET /v1/agents/{agent}/ingested

List ingested documents.

```json
{
  "documents": [
    {
      "source": "company-handbook.pdf",
      "source_type": "pdf",
      "chunks": 45,
      "source_size": 4200000,
      "ingested_at": "2026-03-17T10:30:00Z",
      "index_status": {
        "indexed": 45,
        "embedded": 45,
        "stale": 0
      }
    }
  ]
}
```

### DELETE /v1/agents/{agent}/ingested/{source_hash}

Delete all chunks from an ingested document.

```json
{
  "deleted": 45,
  "source": "company-handbook.pdf"
}
```

---

## Workflows

### 1. User Uploads PDF via Web UI

1. User opens Researcher agent's Memory tab
2. Clicks "Ingest" button
3. Drags `company-handbook.pdf` (4.2 MB) into the drop zone
4. Clicks "Ingest Document"
5. Progress bar shows: Parsing (3s) -> Chunking (1s) -> Storing (2s) -> Indexing (~60s)
6. 45 memory files created in `agents/Researcher/`
7. QMD's 5-second file watcher detects new files, indexes them
8. Embedding runs automatically (debounced per collection)
9. User can now ask Researcher questions about the handbook

### 2. User Pastes URL in TUI

1. User types `/ingest https://blog.example.com/ai-agents-2026`
2. Engine calls `web_fetch` to download the page
3. HTML parser strips navigation, ads, scripts — extracts article text
4. Text chunked into 3 chunks (article is 3,450 words)
5. 3 memory files written with URL as source
6. QMD auto-indexes within 5 seconds

### 3. Agent Uses Ingested Knowledge in Conversation

```
User: What does the company handbook say about code review?

Researcher: [internally calls memory_recall("company handbook code review")]
  -> QMD returns chunk 3 (pages 23-27, Chapter 3: Engineering Practices)
  -> Chunk contains code review process and standards

Researcher: According to the company handbook (Chapter 3, pages 23-27),
the code review process requires:
1. Author creates a PR with a clear description
2. At least one peer reviewer must approve
3. CI/CD pipeline must pass
4. Author addresses all comments before merging

The handbook also specifies 80% minimum unit test coverage for new code.
```

The agent can cite the source because the frontmatter contains `source`,
`page_range`, and `chunk_index`. The system prompt or tool description
instructs agents to reference source metadata when answering from
ingested content.

### 4. User Deletes Ingested Document

1. User opens Ingested Documents list
2. Clicks "Delete All Chunks" next to `company-handbook.pdf`
3. Confirmation dialog: "Delete 45 chunks? This cannot be undone."
4. Server deletes all 45 `ingest-a3f8c2-*.md` files from agent directory
5. QMD detects deleted files on next poll cycle and removes from index
6. Document no longer appears in ingested list or search results

---

## Integration with Existing QMD

The ingestion pipeline integrates naturally with QMD because it outputs
standard markdown files into agent directories — exactly what QMD already
indexes.

| Component              | Integration Point                           |
|-----------------------|---------------------------------------------|
| File storage          | `agents/<name>/ingest-*.md` (standard path) |
| Index detection       | `_qmd_index_keeper` 5s mtime poll (existing)|
| Embedding             | Per-collection debounced embed (existing)    |
| Search                | `memory_recall` tool (existing)              |
| Health monitoring     | `/v1/services/qmd/docs` (existing)          |
| Collection management | One collection per agent (existing)          |

No changes to QMD are required. The ingestion pipeline is purely an
input mechanism that produces files in the format QMD already understands.

---

## Deduplication

### Same Document Re-ingested

When a document with the same filename/URL is ingested again:

1. Compute `source_hash` from filename/URL (same as before)
2. Check for existing `ingest-<source_hash>-*.md` files
3. If found, prompt user: "This document was previously ingested (45 chunks). Replace?"
4. On confirm: delete old chunks, ingest new version
5. QMD automatically handles the file changes

### Identical Content Detection

Before writing chunks, compute SHA-256 of each chunk's content body.
If an identical chunk already exists (same hash), skip writing it.
This prevents duplicate content when re-ingesting an unchanged document.

### Cross-Document Dedup

Not implemented in v1. If the same paragraph appears in two different
documents, it will exist as two separate chunks. QMD's ranking will
naturally surface the most relevant match.

---

## Implementation Plan

| Day  | Task                                                         |
|------|--------------------------------------------------------------|
| 1    | Ingestion API endpoint, file upload handling in server.py    |
| 2    | PDF parser (pymupdf), DOCX parser (python-docx)             |
| 3    | HTML/TXT parsers, URL ingestion via web_fetch                |
| 4    | Chunking algorithm with overlap, section header preservation |
| 5    | Memory file writer with frontmatter, source tracking         |
| 6    | Web UI: Ingest dialog, drag-and-drop, progress display       |
| 7    | Web UI: Ingested documents list, delete, re-ingest           |
| 8    | TUI /ingest command, deduplication, testing                  |
| 9    | Watch folder: IngestWatcher thread, ingest_registry.json     |
| 10   | Watch folder: mtime/size change detection, auto re-ingest    |
| 11   | Watch folder: Web UI config tab, TUI /ingest watch commands  |
| 12   | Knowledge graph integration: auto-relationships, testing     |

---

## Benefits

- **Knowledge onboarding**: Instantly make external documents searchable by agents
- **No manual curation**: Upload a PDF, agent can answer questions from it
- **Source tracking**: Every chunk knows where it came from (file, page, URL)
- **Natural integration**: Uses existing QMD pipeline — no new search infra needed
- **Overlap chunking**: Minimizes information loss at chunk boundaries
- **Managed lifecycle**: Track, re-ingest, and delete ingested documents cleanly
- **Multi-format**: PDF, DOCX, TXT, MD, HTML, URLs — covers common knowledge formats

---

## Watched Folders (Auto-Ingestion)

Agents can be configured with one or more **watched folders**. All supported
files in these folders are automatically ingested and kept up to date — new
files are ingested, modified files are re-ingested, deleted files have their
chunks removed.

### Configuration in agent.json

```json
{
  "display_name": "Researcher",
  "model": "claude-sonnet-4-6",
  "ingest_watch": [
    {
      "path": "/Users/alexander/Documents/research-papers",
      "pattern": "*.pdf",
      "recursive": true,
      "chunk_size": 1500,
      "tags": ["research", "papers"]
    },
    {
      "path": "/Users/alexander/Projects/docs",
      "pattern": "*.md",
      "recursive": false,
      "tags": ["project-docs"]
    },
    {
      "path": "/Volumes/NAS/company/handbooks",
      "pattern": "*.{pdf,docx}",
      "recursive": true,
      "tags": ["company", "handbook"]
    }
  ]
}
```

### Watch Mechanism

```
+------------------------------------------------------------------+
|  IngestWatcher (background thread per agent, 30s poll cycle)      |
+------------------------------------------------------------------+
|                                                                    |
|  For each watched folder:                                          |
|    1. Scan for matching files (glob pattern, optional recursive)   |
|    2. Compare file mtime + size against ingest_registry.json       |
|    3. New file → ingest (parse → chunk → store → QMD embed)       |
|    4. Modified file → re-ingest (delete old chunks → ingest new)   |
|    5. Deleted file → remove chunks from agent dir + QMD            |
|    6. Update ingest_registry.json with current state               |
|                                                                    |
+------------------------------------------------------------------+
```

### Ingest Registry (per agent)

```json
// agents/Researcher/ingest_registry.json
{
  "watches": {
    "/Users/alexander/Documents/research-papers": {
      "files": {
        "attention-is-all-you-need.pdf": {
          "mtime": 1773900000.0,
          "size": 2340000,
          "hash": "a3f8c2",
          "chunks": 28,
          "ingested_at": "2026-03-18T10:30:00Z"
        },
        "scaling-laws.pdf": {
          "mtime": 1773850000.0,
          "size": 1890000,
          "hash": "b7e1d4",
          "chunks": 22,
          "ingested_at": "2026-03-17T14:15:00Z"
        }
      },
      "last_scan": "2026-03-20T11:00:30Z"
    }
  }
}
```

### Web UI: Watched Folders in Agent Config

```
+-----------------------------------------------------------------------+
| Agent Config: Researcher                                         [X]  |
+-----------------------------------------------------------------------+
| [Soul] [Settings] [Skills] [MCP] [Schedule] [Memory] [Watch Folders] |
+-----------------------------------------------------------------------+
|                                                                       |
|  Watched Folders                                            [+ Add]   |
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | /Users/alexander/Documents/research-papers                     |   |
|  | Pattern: *.pdf  |  Recursive: Yes  |  Tags: research, papers  |   |
|  | Status: 12 files, 340 chunks, last scan 30s ago                |   |
|  | [Edit] [Remove] [Rescan Now]                                   |   |
|  +---------------------------------------------------------------+   |
|  | /Users/alexander/Projects/docs                                 |   |
|  | Pattern: *.md   |  Recursive: No   |  Tags: project-docs      |   |
|  | Status: 5 files, 18 chunks, last scan 30s ago                  |   |
|  | [Edit] [Remove] [Rescan Now]                                   |   |
|  +---------------------------------------------------------------+   |
|  | /Volumes/NAS/company/handbooks                                 |   |
|  | Pattern: *.{pdf,docx}  |  Recursive: Yes  |  Tags: company     |   |
|  | Status: 3 files, 102 chunks, last scan 30s ago                 |   |
|  | [Edit] [Remove] [Rescan Now]                                   |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|  Total: 20 files watched, 460 chunks indexed                         |
|  Scan interval: every 30s  |  Auto-embed: Yes                       |
+-----------------------------------------------------------------------+
```

### TUI: Watch Folder Commands

```
$ /ingest watch
Watched Folders (Researcher):
  /Users/alexander/Documents/research-papers  *.pdf (recursive)  12 files, 340 chunks
  /Users/alexander/Projects/docs              *.md               5 files, 18 chunks
  /Volumes/NAS/company/handbooks              *.{pdf,docx}       3 files, 102 chunks

$ /ingest watch add /path/to/folder --pattern "*.pdf" --recursive --tags research
  Added watch: /path/to/folder (*.pdf, recursive)
  Initial scan: found 8 files, ingesting...
  Done. 8 files → 195 chunks

$ /ingest watch remove /path/to/folder
  Remove watch and delete 195 ingested chunks? [y/N] y
  Done.

$ /ingest watch rescan
  Scanning 3 watched folders...
  /Users/alexander/Documents/research-papers: 1 new, 0 modified, 0 deleted
  /Users/alexander/Projects/docs: 0 new, 1 modified, 0 deleted
  /Volumes/NAS/company/handbooks: 0 new, 0 modified, 0 deleted
  Ingesting 1 new + re-ingesting 1 modified...
  Done.
```

### Watch Workflow

1. Admin adds a watched folder to Researcher agent (Web UI or TUI)
2. Initial scan runs immediately — ingests all matching files
3. Background thread polls every 30 seconds:
   - New PDF appears in folder → auto-ingested → chunks stored → QMD embedded
   - Existing PDF modified (new version) → old chunks deleted → re-ingested
   - PDF deleted from folder → chunks removed from agent memory
4. Agent always has up-to-date knowledge from watched folders
5. Admin can trigger "Rescan Now" for immediate sync

### Edge Cases

- **Large initial scan**: If a watched folder has 500 PDFs, initial ingestion
  is queued and processed in batches of 10 to avoid overwhelming the system.
  Progress is reported via the activity viewer.
- **Network folders offline**: If a NAS path is unreachable, the scanner logs
  a warning and retries on the next cycle. Existing chunks are preserved.
- **Permission errors**: Files that can't be read are skipped with a warning
  in the log. The agent's other files continue normally.
- **Overlapping watches**: If two agents watch the same folder, each gets
  its own copy of chunks in its own memory. This is by design — agents
  have isolated memory.

---

## Knowledge Graph Integration

When the [Knowledge Graph Memory](knowledge-graph-memory.md) feature is
implemented, ingested document chunks gain automatic relationship tracking:

### Automatic Relationships

```yaml
---
title: "Company Handbook - Chapter 3: Engineering Practices"
source: company-handbook.pdf
chunk_index: 3
total_chunks: 45
related:
  - file: ingest-a3f8c2-002.md
    type: prev_chunk
    label: "Chapter 2: Team Structure"
  - file: ingest-a3f8c2-004.md
    type: next_chunk
    label: "Chapter 4: Deployment Process"
  - file: ingest-a3f8c2-000.md
    type: same_source
    label: "Table of Contents"
---
```

### Relationship Types for Ingested Content

| Relationship    | Description                                          |
|-----------------|------------------------------------------------------|
| `prev_chunk`    | Previous chunk in same document (sequential reading) |
| `next_chunk`    | Next chunk in same document                          |
| `same_source`   | Other chunks from the same document                  |
| `same_topic`    | Chunks from different docs on the same topic (LLM)   |
| `references`    | Manual cross-reference to other memories             |

### Graph-Aware Retrieval

When an agent recalls from ingested content, the knowledge graph enables:

1. **Context expansion**: Found chunk 3? Automatically include chunks 2 and 4
   for better context around the match.
2. **Cross-document linking**: "What do our handbook AND the research paper
   say about testing?" → graph traversal finds related chunks across documents.
3. **Source navigation**: "Show me the rest of this document" → follow
   `same_source` links to retrieve all chunks from that document.

```
memory_recall("code review process", mode="graph", hops=1)

Results:
  1. ingest-a3f8c2-003.md (score: 0.95) — Chapter 3: Engineering Practices
     └── related: ingest-a3f8c2-002.md (prev_chunk) — Chapter 2: Team Structure
     └── related: ingest-a3f8c2-004.md (next_chunk) — Chapter 4: Deployment
  2. team-practices.md (score: 0.72) — agent memory about team practices
     └── related: ingest-a3f8c2-003.md (same_topic) — linked by ingestion
```

---

## Limitations and Future Work

- **No OCR for image-heavy PDFs**: v1 extracts text layers only. Image-based PDFs
  (scans) would need OCR integration (Tesseract or cloud OCR). Add in v2.

- **No table extraction**: Complex tables in PDFs/DOCX are flattened to text.
  Structured table extraction could be added with dedicated parsers.

- **No cross-agent sharing**: Ingested documents belong to one agent. To share,
  ingest into the main agent (accessible via `memory_shared`).

- **Chunk size tuning**: The default 1500-token chunks work well for general
  documents. Highly structured documents (API docs, legal text) may benefit
  from different strategies. Configurable per-ingestion in v1, auto-detection
  in v2.

- **Watch folder scan interval**: Fixed at 30s in v1. Configurable per-watch
  and filesystem event-based (fsnotify) in v2.

---

## Open Questions

1. Should ingested files have a separate subdirectory (`agents/<name>/ingested/`)
   or live alongside agent memories?
   Recommendation: same directory, `ingest-` prefix provides clear distinction
   and QMD indexes the whole directory regardless.

2. Should chunking happen client-side (Web UI) or server-side?
   Recommendation: server-side always. Client just uploads the raw file.

3. Should there be a global max on ingested content per agent?
   Recommendation: yes, configurable. Default 500 chunks per agent
   (~750K tokens of searchable content). Prevents unbounded growth.

4. Should ingested content be included in context window compaction summaries?
   Recommendation: no. Ingested content is retrieved on demand via
   `memory_recall`, not loaded into context. This keeps context clean.

5. What is the maximum file size to accept?
   Recommendation: 50MB default, configurable. Larger files should be
   split by the user or handled via a background job queue.
