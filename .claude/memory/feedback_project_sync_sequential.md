---
name: project-sync daemon processes projects strictly sequentially
description: One thread, one project at a time. A long-running KG extraction in project A blocks all other projects' cycles until A finishes. Manual Sync now requests jump to the front of the *next* cycle but never preempt the in-flight one.
type: project
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
The `mempalace-project-sync` daemon iterates `for agent_id, proj_name in ordered:` linearly in a single thread (server.py around line 13202). Within one cycle:

- Project A's full pipeline runs to completion: doc_convert → mp_miner.mine → kg_extract.run_kg_post_pass → optional closet_regen.
- Then Project B starts.
- And so on.

**Order**: requested-first (manual Sync now) then filesystem order for the rest. New requests during a running cycle get added to the next cycle's requested set, deduped (set semantics, not queue with multiplicity). They do NOT preempt.

**Wait gate**: the 6h `interval_seconds` only ticks down *after* the cycle finishes. So a 9h cycle = 9h work + 6h sleep = 15h between starts.

**Why single-threaded** (intentional, not lazy):
1. One writer to MemPalace + KG SQLite avoids WAL coordination + retry complexity
2. Provider rate limits (cliproxyapi quota etc.) would force serialisation at the gateway anyway

**Cursor durability**: kg_extraction_progress is per-chunk; killing the daemon mid-cycle and restarting picks up where it left off (cursor-skip on already-done chunks).

**When this becomes a problem**:
- Multi-tenant deployment with one slow project blocking the rest for hours
- Heavily-edited corpora where churn rate exceeds processing rate

**Fix for those**: per-project daemon threads + shared `LocalProviderQueue` semaphore (already exists in claude_cli.py; just needs to be wired into the per-project worker pattern). Not built today; estimate ~200 LOC. Not needed for static-corpus single-tenant cases (the current usage).

**For the 150-PDF bank-policy use case**: not a problem. One project, one cycle, ~6h, done.
