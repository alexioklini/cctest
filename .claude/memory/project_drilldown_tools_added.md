---
name: mempalace_get_drawer + mempalace_list_drawers added; KG tools split into memory_kg group; cleanup of stale corpus state
description: 2026-04-29 — added vanilla-MCP-equivalent drilldown tools, hid the 3 KG tools from default loadout, hardened system prompt against bullet-list compression, cleaned up stale triples/closets after KG-off decision
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
End-of-day session 6e70de0e scored 5.5/7 on the IT-Risk Score canary. The remaining 1.5 points were Mistral Small compressing bullet lists (5 Score-Arten merged into 4, three blockade thresholds reduced to two, CIA Faktor 1-4 weighting omitted). User asked to (a) clean up unused KG/closet state, (b) add vanilla MemPalace MCP's drilldown primitives, (c) sharpen the system prompt against compression — then test with Magistral.

**(a) Cleanup actions** (one-shot, idempotent):
1. Deleted 1,348 stale KG triples (gemini + mistral-vibe + mistral-small lineage) for project__f201b24ff6a2 prefix matches.
2. Orphan-entity sweep deleted 2,059 entities no longer referenced by any triple. KG-DB went from 2217 → 869 triples (other projects' rows preserved).
3. Dropped chats.db cursor rows for the wing: kg_extraction_progress (290), kg_extraction_log (52), kg_extraction_source_state (58), closet_regen_progress (58).
4. Dropped 234 unused LLM-closets from `project__f201b24ff6a2` Chroma collection (Brain doesn't read closets anymore since chroma-direct switch).
5. Reverted `closet_llm.py` MAX_CONTENT_CHARS patch 80000 → 30000. Brain doesn't run regenerate_closets so the patch was dead weight; reverting reduces the surface area that pip upgrade can clobber.

**(b) Two new tools** mirroring vanilla MemPalace MCP's drilldown surface:

- `mempalace_get_drawer(drawer_id)` — fetch one chunk by id. Project-wing-scoped (refuses cross-project ids to prevent guessed-id leakage). Returns full content + chunk_index + source_file.
- `mempalace_list_drawers(source_file?, limit, offset)` — paginate every chunk of the project wing in storage order. Optional source_file filter narrows to one document. Returns drawer_id + 200-char preview + chunk_index + content_length per drawer; also reports `total_in_wing` so the model can paginate sensibly.

Both auto-scope via `_resolve_project_wing()` (new shared helper). Both registered in the `memory` tool group, dispatch table, `_CONCURRENT_SAFE_TOOLS`, and `execution.DEFAULT_PROFILES` as `heavy: False`. Verified live: list returned 1,449 total drawers in the kg-real-policies wing; ISMS Handbuch filter returned 63 chunks (chunk_index 0–62).

**(c) Tool-group split**: the 3 KG tools (kg_query, kg_search, kg_neighbors) moved out of `memory` into a new `memory_kg` group. Default agents only get the simple `memory` group — KG tools are no longer in the default loadout. To opt back in, an agent's `token_config.tool_groups` needs to include `memory_kg`. Reasoning: yesterday's regression hunt showed KG triples bias the model toward fragmentary answers (low-density compositional output); without explicit need they're cognitive-load on the prompt.

**(d) System prompt hardened with REPRODUCTION DISCIPLINE block**:
- "When the source contains a numbered list, bullet list, value table, threshold sequence, or any structured enumeration that is part of the answer — reproduce ALL items VERBATIM."
- Explicit examples calling out the canary failure modes: "If the source lists three thresholds (50%/80%/90%), your answer must list all three"; "if the source defines five distinct score-types, your answer must list all five"; "if the source provides an 11-row percent-to-score table, your answer must include all 11 rows"; "if the source assigns a numeric weighting (CIA-Rating Faktor 1-4), include the specific number range".
- "Compression of enumerations into prose is the most common failure mode for policy questions. The user is asking BECAUSE they need the exact details — fewer details means less correct answer, not more concise. If the result is long, that is fine; correctness wins."
- New DRILLDOWN TOOLS section advertises get_drawer + list_drawers with usage hints.

**Why drilldown is useful even with the new sharper prompt**: when read_document on a 1903-line .md returns 53K of content but the model decides to skip the table region for compression-budget reasons, list_drawers + get_drawer let it pull just the relevant chunks (chunk_index 38–42 covers section 2.13 in the ISMS Handbuch). Cheaper than re-issuing read_document with offset+limit, which the model has been shaky on (default offset=1, didn't always get the math right).

**How to apply**:

When testing canary regressions, the order of suspicion is now:
1. Tool-call sequence in metadata.tools[] — what did the model actually call
2. Drawer payload — what content reached the model
3. Composition — did the model preserve the structure of the source (this is what the new REPRODUCTION DISCIPLINE addresses)

For composition issues, prefer prompt sharpening (cheap, no risk) over model swap. Model swap stays as the fallback when prompt rules can't get the model past its compression bias. User's preference: stay on Mistral subscription (Magistral / Mistral Small 4) for DSGVO reasons; Opus 4.7 not an option. Magistral (reasoning variant) likely best next test — its anti-summarisation tendency may match the corpus better.

**Idempotency notes**:
- The cleanup queries are safe to re-run: prefix-matched DELETEs become no-ops if rows are gone.
- The tool registrations are additive; running the install script twice doesn't duplicate.
- The tool-group split is non-breaking: agents whose `token_config.tool_groups` already lists `memory` keep working unchanged; KG tools just won't be exposed unless they explicitly add `memory_kg`. Existing agents with chat-history relying on kg_query calls won't break — the tool functions still exist and dispatch, they just need to be re-enabled at the group level.
