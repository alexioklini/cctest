---
name: Code graph as MemPalace extraction kind
description: Backlog — fold code_graph into MemPalace as just another triple/drawer extraction profile, not a separate system. Captured 2026-04-26 during KG-from-prose step-1 design.
type: project
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
User wants the code graph (`code_graph.db`, Tree-sitter, 14 languages) integrated into MemPalace as deeply as possible — treated as just another *kind* of extraction alongside the prose-triple extractor, not a parallel system the agent has to remember separately.

**Why:** Captured 2026-04-26 while scoping the step-1 prose KG (`kg_extract.py`) for project input folders. User asked whether code graph is transparent for project queries; answer was no — it's a separate substrate, not project-scoped, not auto-built from the project sync loop. Rather than fixing those gaps in place, the user wants the deeper consolidation.

**How to apply:** When implementing this, the unifying principle is: code AST extraction is just one more `Profile` in the `kg_extract` framework. Tree-sitter parses produce the same `(subject, predicate, object, source_file, source_drawer_id, adapter_name)` shape that the prose LLM extractor produces. The KG already has the columns; the unification is conceptual, not schema.

## Vision

- One graph, one query tool from the agent's perspective: `mempalace_kg_query` returns hits whether they came from prose triples, code-AST triples, or future profiles (config files, schemas, OpenAPI specs).
- Code becomes a built-in `Profile` with `extractor=tree_sitter` instead of `extractor=llm`. Same scoping discipline (project wing via `source_file` prefix + `adapter_name`).
- `code_graph.db` either deprecated or kept as a derived index (built from MemPalace triples) for the BFS/impact-analysis paths that benefit from a graph DB shape. Decision deferred until implementation.
- The existing `code_graph_*` tools either fold into `mempalace_kg_*` or stay as thin convenience wrappers. The agent should not need to know which substrate the answer came from.
- Project sync loop fires the code-AST extractor for code files in input folders, the prose LLM extractor for prose. One pass, one set of progress counters, one UI surface for monitoring.

## Predicate vocabulary alignment

Tree-sitter edges (`CALLS`, `IMPORTS_FROM`, `INHERITS`, `IMPLEMENTS`, `CONTAINS`, `TESTED_BY`) become predicates in the same KG triples table as the prose ones (`requires`, `cites`, `defines`, ...). Choose snake_case lowercase to match the prose convention: `calls`, `imports_from`, `inherits`, `implements`, `contains`, `tested_by`. Documented in the KG predicate registry alongside prose predicates.

## Things to figure out at implementation time

1. **Sharing semantics for code.** Prose triples in a project wing are strictly private. Code might deliberately be shared across projects (vendored libs, shared utils). The "always project-private" rule may not be right for code — needs a separate decision. Possibly a per-input-folder `share=true|false` flag.
2. **Symbol qualified-name format.** Code graph today uses `{file_path}::{ClassName.method}`. As a triple subject this works; just ensure the format survives across the wing scoping (relative vs absolute paths).
3. **Incremental build invariant.** `_after_file_write` already triggers `_maybe_update_code_graph(path)` — that hook needs to also write to the unified KG, not just `code_graph.db`. If we keep `code_graph.db` as a derived index, the hook becomes "extract triples → upsert KG → rebuild derived index." If we drop it, the hook just upserts KG.
4. **Migration.** Existing `code_graph.db` content (already populated for the brain repo + artifacts) needs a one-shot import into the KG, or a re-build pass. Re-build is simpler and idempotent.
5. **Skip rule for prose mining.** Code files should NOT be mined as text drawers (vector embeddings of code are low-signal). Extension-based skip in MemPalace's miner, code-AST extractor handles them.
6. **Performance.** Tree-sitter is fast (incremental, hash-skip), but a 50K-LOC codebase produces tens of thousands of edges. Triples table needs to handle the volume — already does for prose, but worth measuring.
7. **`code_graph_enhance` (LLM summaries + architecture layers + guided tour).** That's a hybrid — Tree-sitter for structure plus LLM for semantics. Folds naturally into the framework as an optional second pass after AST extraction. Same predicate space, just `summary_of` / `layer` / `tour_position` predicates added.

## Sequencing

Comes after step 1 (prose KG) is shipped and validated. Step 1.5 was originally "scope code graph to projects" as a smaller fix; this backlog item supersedes that — do the full consolidation instead. Likely sequence when picked up:

1. Add `code` profile to `kg_extract` with Tree-sitter extractor.
2. Wire it into the project sync loop (skip prose extractor for code extensions, run code extractor instead).
3. Wire `_after_file_write` to write to unified KG.
4. One-shot rebuild of brain repo + artifacts into KG.
5. Decide: keep `code_graph.db` as derived index, or drop it. Re-test all `code_graph_*` tool callers.
6. Possibly unify the agent-facing tools — `code_graph_query` becomes a thin wrapper over `mempalace_kg_query` with a `predicate_in=[calls, imports_from, ...]` filter.

## Why not now

Step 1 (prose KG over PDFs) has clear immediate value for the bank-policies use case. The code-graph consolidation is bigger, touches more existing code, and has open design questions (sharing semantics, derived-index decision). Bundling them blows up risk. Keep them sequential.
