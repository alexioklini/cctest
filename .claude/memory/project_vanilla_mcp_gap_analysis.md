---
name: Vanilla MemPalace MCP vs Brain — gap analysis 2026-04-29
description: Brain's mining + chroma-direct search are equivalent or better than vanilla MCP. Real remaining gap is model composition tendency (Mistral Small vs Opus), not tool surface or search logic
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
After end-of-day session 6e70de0e (5.5/7 on canary) the user asked whether to fall back to vanilla MemPalace MCP's mechanisms for search/tools. Audit of `/Users/alexander/.mempalace/venv/lib/python3.14/site-packages/mempalace/mcp_server.py`:

**Vanilla MemPalace MCP exposes 29 tools** (vs Brain's 4: mempalace_query + 3 KG). Surface includes: search, list_wings/rooms/drawers, get_drawer, add_drawer, check_duplicate, taxonomy, kg_query/add/invalidate/timeline/stats, traverse_graph, find_tunnels, create_tunnel, list_tunnels, follow_tunnels, diary_read/write, hook_settings, memories_filed_away, reconnect, get_aaak_spec.

**Critical finding**: vanilla MCP's `tool_search` calls `search_memories()` — the SAME function Brain abandoned this morning because of the closet-boost hydration bug (returned 19 byte-identical frontmatter hits on multi-chunk title-repeating sources). So either:
1. The user's vanilla "Claude Code with MemPalace" test that produced 7/7 on the IT-Risk Score canary used the `mempalace search` CLI (which calls `search()` not `search_memories()`), not the MCP tool, OR
2. Opus 4.7 was robust enough to recover from the bad hits via additional drilldown (`get_drawer`, `list_drawers` tools), OR
3. Something in Claude Code's MCP transport differs from `search_memories()`'s direct invocation.

**Reproduced live**: `search_memories()` on the same wing+query returned 5 byte-identical frontmatter hits with sim 1.090/1.012/0.986/0.864/0.861 — all useless. `col.query()` directly on Chroma returned 5 distinct hits with section 2.13 body in hit [3] — the right answer.

**Brain is ALREADY ahead of vanilla MCP on these dimensions**:
- Search backend: direct `col.query()` instead of broken `search_memories()`
- Project-wing auto-scoping via `_thread_local.project`
- `read_path` / `read_path_original` resolution returned with each drawer (vanilla forces the model to guess paths from basenames)
- Tighter tool surface (4 vs 29 tools = less cognitive load on the model)
- System-prompt with citation discipline + 3-step retrieval flow
- markitdown PDF→md conversion (same as user's vanilla setup)

**What vanilla MCP has that Brain doesn't**:
1. `tool_get_drawer(drawer_id)` — fetch one specific chunk by id. Useful for "I want chunk N+1 from this source".
2. `tool_list_drawers(wing, room, limit, offset)` — paginate through every drawer of a wing/room. Useful for sequential browse.
3. `tool_check_duplicate(content, threshold)` — pre-write dedup. Brain doesn't expose a "save" path; not currently relevant.
4. Query sanitizer (`sanitize_query`) — strips system-prompt-injection attempts from query strings. Defensive; not a quality booster.
5. `as_of` parameter on KG query (temporal filtering). Brain has this on its kg_query.

**How to apply**:

DO NOT fall back to vanilla MCP. Brain's mempalace_query + read_document path produces equivalent or better retrieval than vanilla MCP, and is project-aware which vanilla isn't.

CONSIDER adding (low priority) these three primitives if the model ever needs them:
- `mempalace_get_drawer(drawer_id)` — for cases where mempalace_query returns drawer_ids and the model wants chunk N+2.
- `mempalace_list_drawers(wing=auto, room?, limit, offset)` — for sequential browse through a wing's content. Useful when "what's the full structure of document X" is asked.

The REAL remaining gap to vanilla-Claude-Code-with-Opus-4.7 is composition tendency: Mistral Small compresses bullet lists, Opus reproduces verbatim. This is a model-behavior issue, not a tool surface issue. Proven by the fact that `read_document` now returns the full 1903-line .md including section 2.13 with all schwellen, weights, table — Mistral Small simply chose to summarise rather than reproduce.

**To close the model-composition gap** (in priority order, cheapest to most expensive):
1. Strengthen system prompt with "reproduce ALL bullets and ALL numbered values from the source verbatim — do NOT summarise enumerations" rule. ~5min, no risk, helps Mistral.
2. Switch composer model to Magistral (Mistral's reasoning variant, on the same subscription). ~0min config, may help anti-summarisation.
3. Switch composer model to Opus 4.7 for policy sessions. Costs API quota; 100% known to deliver 7/7.
4. Per-question routing (auto-detect "policy/legal/document" questions and route to Opus). Probably overkill.

**Update 2026-04-30 — external comparison run**: user tested PI Coding Agent + MemPalace as MCP server against Brain's v8.22.0 stack on the same canary corpus. Result: **no improvement, slight regression** vs Brain. Combined with Brain's v8.22.0 disciplines (PRECISION + verbatim CITATION + REFUSAL) and validated sampling defaults (`temperature=0.2`, `top_p=0.85`), Brain's stack now beats the vanilla-MCP route on the same model class. Confirms the gap-analysis conclusion: search backend + project scoping + read_path resolution + citation discipline matter more than the 29-vs-4 tool surface. The "consider adding get_drawer/list_drawers" suggestion stays low-priority; external test gives no signal that the missing primitives are blocking quality.
