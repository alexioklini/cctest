---
name: project-read-doc-cache-removed
description: "2026-05-19 — per-session read_document/read_file cache removed; cross-turn re-reads now always go to disk. Within-turn dedup catches accidental double-reads. Citation validator's path lookup replaced with thin `_session_read_paths` tracker."
metadata: 
  node_type: memory
  type: project
  originSessionId: bb94532c-fd1e-48ff-8846-7f939dda5b26
---

The `_read_doc_cache` (v8.23.0) returned stubs on repeat `read_document`/`read_file` calls within a session. Across turns that was wrong — turn-2's stub said *"use the previous tool_result"* but Brain doesn't carry tool_result blocks across turns (only user msgs + final assistant text survive in `session.messages`). On a content-thin turn-1 reply the model would hit the stub, have no in-context source, and either fabricate or refuse with "I don't know what you're talking about."

**Why removed, not patched**:
- Bumping TTL / persisting to SQLite (B / C options discussed) fixed eviction edge cases but didn't fix the cross-turn-stub-lies-to-model failure.
- Adding a same-turn-only guard preserved within-turn dedup but the existing `_tool_dedup` already does that — cache became redundant.
- The user's accepted invariant: re-reads across turns are fine. Mistral docx conversion is ~2s, OCR-bound PDFs cost real money but rare. Trade is acceptable.

**Why:** prevents the model from answering blind after a long gap / reload / restart when turn-1's reply didn't capture enough content. Predictability > savings.

**How to apply:**
- Don't re-introduce cross-turn caching for content tools.
- If a future "coding mode" lands ([[backlog-lean-ctx-coding-mode]]), retain tool_results across turns instead — different design pattern, not the cache.
- Citation validator path lookup now via `_record_session_read_path()` + `_session_read_paths[sid]: set[str]` in brain.py. Public helper kept as `_read_doc_cache_session_paths()` for backward compat — name is misleading, the underlying store is no longer a cache.
- `read_document` / `read_file` removed from `_DEDUP_EXEMPT`. Within-turn double-reads now hit dedup → returns "duplicate call" error to model. That's intentional — within the turn the prior tool_result IS still in context, model should use that.
- `_after_file_write` no longer needs to invalidate (no cache).
- CLAUDE.md "Token Optimisations (v8.23)" section rewritten to document the removal.
