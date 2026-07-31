---
name: MemPalace upgraded to 3.3.4 (2026-05-01)
description: 2026-05-01 — bumped requirements pin + active venv from 3.3.3 → 3.3.4; key risks evaluated, cross-wing topic tunnels do not fire on Brain-managed wings
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Upgraded mempalace 3.3.3 → 3.3.4 on 2026-05-01.

- `packaging/common/requirements.txt`: pin moved 3.3.0 → 3.3.4 (was lagging two minor; venv was already at 3.3.3).
- Active venv `/Users/alexander/.mempalace/venv` upgraded via its own pip; smoke-tested every import Brain uses (palace, searcher, miner, knowledge_graph, closet_llm, mcp_server) — all green at 3.3.4. Server restarted via `launchctl kickstart -k`, warm-pool primed all 3 gemma-4-26b slots, miner cycles clean.

**Why:** The 3.3.4 release fixes (a) ChromaDB HNSW segment bloat (#1191 — ours wasn't bloated yet but the ALLOC pinning helps any future re-mine), (b) `max_seq_id` poisoning that silently dropped writes (#1135), (c) Stop-hook reopen SIGSEGV from metadata mismatch on `get_or_create_collection` (#1089/#1262/#1289), (d) HNSW divergence floor scaling (#1287). All four are passive-safe — they only kick in on the relevant failure modes.

**Cross-wing topic tunnels (new feature in 3.3.4) — does NOT affect Brain.** The new `_compute_topic_tunnels_for_wing` only emits tunnels when wings have **confirmed `TOPIC` labels** in their entity registry, and TOPIC labels only come from running `mempalace init --llm` on a corpus. Brain's `mempalace-project-sync` daemon does not run `mempalace init` — it just ensures `mempalace.yaml` exists and calls `mp_miner.mine()`. So `topics_by_wing` is empty for every Brain-managed wing, and `_compute_topic_tunnels_for_wing` returns 0 tunnels. The "project wings are strictly isolated" invariant in CLAUDE.md is preserved by construction, not by any new guard.

If we ever start running `mempalace init` per project, set `topic_tunnel_min_count` in `~/.mempalace/config.json` to a high number (or `MEMPALACE_TOPIC_TUNNEL_MIN_COUNT` env var) to opt out before the first project-mine cycle.

**Closet patch status:** the 80K `closet_llm.MAX_CONTENT_CHARS` Brain-side patch was already reverted on 2026-04-29 ("Brain doesn't use LLM closets anymore — chroma-direct path"), so the upgrade does not clobber any Brain modification. `closet_llm.py:57` is back to upstream `30000` and stays that way.

**Schema:** 3.3.4 keeps the 3.3.3 KG schema (`source_drawer_id`, `adapter_name` columns present), so the `TypeError`-fallback in `kg_extract.py` for 3.3.0 schemas remains a no-op safety net.

**How to apply:** future upgrades — `/Users/alexander/.mempalace/venv/bin/python -m pip install --upgrade mempalace==<v>`, then `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`, wait >6s for HTTP bind, tail `~/.brain-agent/server.error.log` (NOT server.log — launchd FD quirk).
