---
name: Models invent room-filter values from tool schema's example list
description: 2026-04-29 — bbc8472c session: Mistral Small filtered mempalace_query with room='document' (invented), got 0 drawers, hallucinated answer; tool schema invited the guess by listing 'document' as a valid example
type: feedback
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
The mempalace_query tool schema's `room` param description listed `"chat, chat_summary, chat_attachment, reference, document, artifacts, or any other configured room"` as examples. The model (Mistral Small) read that as a permissions list and tried `room: "document"` — which doesn't exist in Brain's project mining (everything goes to `room: "general"`). All three retries that turn used invented room filters; all returned 0 drawers; the model fell back to training-data hallucination.

**Why:** Tool schemas serve as documentation AND as suggested vocabulary. Listing "document" in an examples list is read by some models as "valid value to try". The original schema came from a more permissive multi-source MemPalace setup; for Brain's project-mining path it's actively misleading.

**How to apply:**

When a tool param has enum-like semantics but the actual valid values depend on which corpus/wing/installation, do NOT list speculative examples in the schema description. Either:
- Hard-code the actual valid values (omit speculation), OR
- Tell the model NOT to guess and to omit the param

Brain's `mempalace_query.room` schema now does both: lists ONLY the rooms Brain's miner actually creates ('general', 'artifacts', 'chat*', 'reference') and tells the model `**DO NOT GUESS room names**`.

Same pattern applies to `wing` (already addressed in v8.21.0 with auto-scoping) and `include_chat_history` (now also called out in system prompt — model was using it for non-chat questions, which silently switched the search to the project_chat wing).

**Reproduction at the time of this note**: same wing has 1372 drawers in `room='general'` + 77 in `room='artifacts'`; `room='document'` returns zero. Without this fix the model tries the speculative value, gets nothing, and fabricates a generic answer instead of refusing.

**Bigger principle**: every "optional filter" param on a retrieval tool is a footgun if the model can interpret a wrong value as "filtering down" instead of "ruling out a successful search". Default-no-filter behavior should be the strongly-preferred path; filter params should be hard-coded enums or omitted when the surface area is uncertain.
