---
name: v8.22.0 externally validated against PI + vanilla MemPalace MCP
description: 2026-04-30 — PI Coding Agent + MemPalace MCP showed no improvement vs Brain v8.22.0; Brain's discipline + sampling stack is the current best path
type: project
originSessionId: 14f9509b-52ac-46d7-b685-5a694fa330a7
---
External A/B test on the same German bank-policy canary corpus: PI Coding Agent wired to vanilla MemPalace as MCP server vs Brain v8.22.0. Result: no improvement, slight regression on the PI side.

**Why:** validates that Brain's stack — direct `col.query()` retrieval, project-wing auto-scoping, `read_path`/`read_path_original` on each drawer, the three-discipline system prompt (REFUSAL + PRECISION + verbatim CITATION), and the validated sampling defaults (`temperature=0.2`, `top_p=0.85`) — does the heavy lifting that the 29-tool vanilla MCP surface alone does not. Confirms `project_vanilla_mcp_gap_analysis.md`'s conclusion that the 4-vs-29 tool gap is not what's blocking quality on the Mistral Small class.

**How to apply:**
- Don't propose falling back to vanilla MemPalace MCP for retrieval-quality reasons.
- Don't deprioritise the v8.22.0 disciplines or the temperature/top_p caps when looking for further gains — they are pulling the weight, not the tool surface.
- The remaining gap to Opus 7/7 is still composition tendency (Mistral Small compresses enumerations); model swap remains the cheapest path to close it, not more retrieval engineering.
- The "consider adding `mempalace_get_drawer` / `mempalace_list_drawers`" backlog item stays low-priority — external comparison gave no signal those primitives are blocking quality.
