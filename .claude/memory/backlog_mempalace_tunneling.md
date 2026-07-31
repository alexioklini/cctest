---
name: MemPalace tunneling + knowledge graph
description: Future work — automatic tunneling for cross-user/team/project memory sharing, and KG auto-extraction
type: project
originSessionId: 79ad597c-34ad-41f0-891e-84de26479e03
---
MemPalace now uses `user_id/agent_id` wings for per-user memory isolation (implemented 2026-04-16). Shared wings (no `/`, e.g. `brain_code`) remain globally visible.

## Tunneling

Next step: automatic tunneling so memories can be shared across boundaries when appropriate. This is complex because multiple overlapping scopes need to interop:

- **Main agent** — shared by all users
- **Agent teams** — team head + members, may share context
- **Individual users** — each has their own `user_id/agent_id` wings
- **User teams** (future) — groups of users who should share memories
- **Projects** — project-scoped conversations that may span users/agents

**Why:** A single tunneling strategy won't fit all cases. Need a concept that handles which memories flow where without creating a confusing permission model or flooding users with irrelevant cross-user content.

**How to apply:** When revisiting this, start with a concept design before implementing. Consider: who decides tunnel creation (automatic vs. manual), what granularity (wing-to-wing, room-level), and how to handle the read/write asymmetry (should tunneled memories be read-only from the receiving side?).

## Knowledge Graph Auto-Extraction

MemPalace has a full temporal knowledge graph (`knowledge_graph.sqlite3` — entities, triples with valid_from/valid_to, relationship types) but Brain doesn't populate it. The MCP tools (`kg_add`, `kg_query`, `kg_invalidate`, `kg_timeline`) exist but aren't exposed since v7.4.0 collapsed the tool surface to `mempalace_query` only.

Options:
- **Chat sync extraction**: run an LLM pass per session to extract entity-relationship triples from conversations (expensive but automatic)
- **Miner extraction**: extract from source code during mining (simpler — function names, imports, class hierarchies)
- **Agent-driven**: re-expose `kg_add` as a Brain tool so agents can build the graph during conversations

**Why:** KG section hidden from admin dashboard (2026-04-16) because it shows all zeros — no point displaying empty stats.

**How to apply:** Start with the cheapest option (miner extraction for code entities), then add chat-based extraction if the KG proves useful.
