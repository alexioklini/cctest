---
name: 2026-04-29 EOD — rolled back to 6e70de0e (5.5/7) state after later experiments regressed quality
description: Removed drilldown tools + REPRODUCTION DISCIPLINE prompt block + memory_kg group split. Kept all infrastructure fixes. Stable known-good config.
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
End of 2026-04-29: After session 6e70de0e scored 5.5/7 (best of the day on Mistral Small 4), three subsequent additions regressed quality on the same canary down to 1-3.5/7:
- Drilldown tools (`mempalace_get_drawer`, `mempalace_list_drawers`) added to schema + advertised in prompt
- REPRODUCTION DISCIPLINE block (~30 lines with worked examples) added to prompt
- KG tools split out into `memory_kg` group

User asked to roll back to 6e70de0e state. Done as follows.

**Removed** (these were the additions that regressed quality):
1. Tool schemas for `mempalace_get_drawer` and `mempalace_list_drawers` removed from TOOL_DEFINITIONS
2. Handler functions `tool_mempalace_get_drawer`, `tool_mempalace_list_drawers`, helper `_resolve_project_wing` removed
3. Dispatch entries for both tools removed
4. Removed from `_CONCURRENT_SAFE_TOOLS` and `execution.DEFAULT_PROFILES`
5. `memory_kg` tool group split rolled back — KG tools (`kg_query`, `kg_search`, `kg_neighbors`) back in `memory` group as before
6. REPRODUCTION DISCIPLINE block (the trimmed one-sentence form AND the original 30-line bloated form) removed from system prompt
7. DRILLDOWN TOOLS section removed from system prompt

Project memory block size: 14.3K (peak bloat) → 11.3K (current). 6e70de0e baseline was ~9-10K; we're 1-2K above, that's the room/include_chat_history defensive warnings (kept — see below).

**Kept** (these were not the cause of regression and removing them would re-introduce older bugs):
- `room=DO-NOT-GUESS` warning + `include_chat_history=false` warning in system prompt — added after bbc8472c invented `room='document'` and got 0/7. Removing this risks re-introducing that failure mode.
- `read_document` plain-text branch — 500-line cap removed, full file returned (added after c4027c7e)
- `read_document` in `execution.DEFAULT_PROFILES` as `heavy: False` — prevents auto-isolation summarising large reads
- markitdown PDF→md preferred backend in `doc_convert.py`
- `tool_mempalace_query` uses direct `col.query()` instead of `search_memories()` — fixes the closet-boost-broken-on-multi-chunk-title-repeating-sources bug. THE root-cause fix.
- `read_path` / `read_path_original` resolution attached to each drawer
- `_summarise_tool_result` 2-vs-3-tuple unpack fix in `execution.py:840`
- KG disabled in config (`mempalace.kg.enabled=false`, `regenerate_closets=false`)
- closet_llm.py MAX_CONTENT_CHARS reverted 80000→30000 (upstream default; we don't use LLM closets anymore)
- All stale state purged: 1,348 KG triples, 2,059 orphan entities, 234 unused closets, 458 cursor rows for the kg-real-policies wing

**Verification post-rollback**:
- `mempalace_get_drawer` and `mempalace_list_drawers` removed from `TOOL_DEFINITIONS` and `TOOL_DISPATCH` ✓
- `memory` group has the original 5 tools: mempalace_query, save_chat_to_memory, mempalace_kg_query, mempalace_kg_search, mempalace_kg_neighbors ✓
- `memory_kg` group no longer exists ✓
- "REPRODUCTION DISCIPLINE" string not in source ✓
- "reproduce every entry" string not in source ✓
- `mempalace_query("IT-Risk Score Berechnung", n_results=5)` smoke test returns 5 distinct drawers including section 2.13 body ✓

**Known limitation** (accepted): Mistral Small 4 quality on policy-reproduction is stochastic — across the day the same model + same prompt ranged 0/7 to 5.5/7. The infrastructure is correct (verified through 6 fixes during the day); the variance is model-inherent. Average expectation 2-5/7; users should run the canary 3+ times to get a stable measurement.

**How to apply**:

This is the known-good state for DSGVO-compliant policy Q&A. Don't add prompt rules in pursuit of accuracy without testing 3+ runs first against the canary — the bloat-experiment showed even single-sentence rules can regress quality on this model size class. Future work should target the model selection question (e.g. wait for a Mistral release with better verbatim-reproduction tendencies) rather than prompt engineering.

**Files modified by this rollback**:
- `claude_cli.py`: removed 2 tool schemas, 2 handler functions, 1 helper function, 2 dispatch entries, 1 group entry, 1 prompt block. Reverted concurrent-safe set and tool-group split.
- `execution.py`: removed 2 entries from DEFAULT_PROFILES.

**Files NOT touched by this rollback** (kept all today's improvements):
- `doc_convert.py`: markitdown integration stays.
- `server.py`: conversion knob plumbing stays.
- `agents/main/chats.db`: stale KG cursors stay purged.
- `~/.mempalace/brain/`: triples + closets stay purged for the wing.

**Don't re-add the drilldown tools or REPRODUCTION DISCIPLINE** without testing against the canary 3+ runs and verifying quality holds at ≥5/7 average.
