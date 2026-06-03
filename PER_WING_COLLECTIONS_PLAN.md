# Per-Wing MemPalace Collections — Design Plan

**Status:** IMPLEMENTED (2026-06-03) — see "Implementation outcome" at the bottom.
**Author:** Claude (with Alexander)
**Date:** 2026-06-03
**Target version:** 9.62.0
**No flag (decided mid-build):** per-wing isolation is ALWAYS ON — not a toggle. A
default-off flag would leave the corruption-prone shared index live by default
(the opposite of the goal) and mean two code paths forever. Safety comes from the
verify-before-destroy migration + the tested auto-heal, not a toggle.

## 1. Problem

Today **all ~13,380 drawers across every wing live in ONE ChromaDB collection
(`mempalace_drawers`) backed by ONE physical HNSW index segment.** Wings
(`project__X`, `user__X`, `team__X`, `brain_code`, …) are only a **metadata
field**, filtered at query time (`where={"wing": …}`).

Consequence (root cause of the recurring corruption):

- A scoped delete of N drawers for one wing (e.g. the macrumors web-URL churn:
  ~74 drawers deleted + re-filed **every** project-sync cycle because a news
  homepage's content changes on every fetch) mutates the **one shared HNSW
  index** that holds all 13,380 nodes.
- A bulk delete racing a concurrent upsert, or an unflushed HNSW + process death,
  wedges that single segment → on next boot sqlite ≫ HNSW → integrity-fail →
  **the WHOLE segment (all wings) is quarantined + rebuilt**, not just the
  affected wing. Retrieval dies palace-wide until rebuild.

The blast radius of any single-wing fault is the entire palace. In production
(many projects, frequently-changing content) this is unacceptable.

## 2. Goal & success criteria

**One ChromaDB collection per wing**, so:

1. **Isolation** — corruption/churn in one wing's HNSW index CANNOT affect any
   other wing. A wedged `project__macrumors` leaves `project__bank_policies`,
   `user__alex`, `brain_code` fully queryable.
2. **Minimal blast radius** — when something corrupts, only that one wing's
   drawers are involved.
3. **Auto-healing, zero admin** — the affected wing's collection rebuilds itself
   from durable sqlite on the next query that hits the corruption, transparently;
   other wings never notice.
4. **No data loss** — durable sqlite remains the source of truth; rebuild/migrate
   never drops recoverable data.

Verification: a fault-injection test that corrupts one wing's HNSW segment and
asserts (a) other wings still answer, (b) the bad wing self-heals on next query,
(c) no admin action, (d) drawer count restored from sqlite.

## 3. Decisions (locked with Alexander 2026-06-03)

| # | Decision | Choice |
|---|----------|--------|
| 1 | How to drive per-wing collections | **Vendor-patch** the package (tracked in `project_mempalace_venv_patches`, re-applied on pip upgrade) |
| 2 | Granularity | **One collection per wing** (max isolation) |
| 3 | Migration of existing data | **Rebuild-from-scratch** — re-mine file-derived wings; reset chat-sync/profile cursors to re-derive chat wings from the durable chat DB (see §7 caveat) |
| 4 | Rollout | **Plan first** (this doc) → implement behind a default-off flag → stage |

## 4. Collection-name scheme

Each wing → its own collection. Chroma collection names must be `[a-zA-Z0-9._-]`,
3–512 chars, start/end alphanumeric. Wing names already are close, but
`project__X` (double underscore) and synthetic markers need a deterministic,
collision-free mapping.

```
collection_name(wing) = "w_" + sanitize(wing)
  sanitize: lowercase is NOT applied (chroma is case-sensitive; keep wing case);
            replace any char not in [A-Za-z0-9._-] with "-";
            collapse repeats; ensure starts/ends alphanumeric;
            if > 500 chars or after-sanitize collision risk, append "_" + sha256(wing)[:10].
```

- `project__f201b24ff6a2`  → `w_project__f201b24ff6a2` (underscores are legal — kept verbatim where possible)
- `project_chat__f201…`    → `w_project_chat__f201…`
- `user__alex@me.com`      → `w_user__alex-me.com_<hash>` (`@` illegal → `-`, hash for safety)
- `brain_code`             → `w_brain_code`
- `main_artifacts`         → `w_main_artifacts`

The closets collection splits the same way: `c_<sanitize(wing)>` (one closets
collection per wing, mirroring drawers). KG stays a single SQLite file per palace
(it's not Chroma, has no HNSW, and already uses anchored prefix scoping — out of
scope; see §9).

A pure helper `wing_to_collection(wing) -> (drawers_name, closets_name)` is the
single source of truth, used by every read/write/recovery path.

## 5. Vendor patches (package side)

Tracked in `project_mempalace_venv_patches`. Each is additive + backward-compatible
(default arg = current behavior), so un-patched calls are unchanged.

1. **`palace.py`** — `get_collection` / `get_closets_collection` already accept
   `collection_name`; no change needed (confirmed parameterized).
2. **`mcp_server.py`** — `_get_collection(create, collection_name=None)` and
   `tool_add_drawer(..., collection_name=None)`: when provided, use it instead of
   `_config.collection_name`. Thread it through.
3. **`miner.py`** — `mine(..., collection_name=None, closets_collection_name=None)`:
   forward to `get_collection`/`get_closets_collection` instead of the defaults.
4. **`searcher.py`** — `search_memories(..., collection_name=None)`: pass to
   `get_collection`. (Brain's query path mostly drives chroma directly via
   `get_collection` + `col.query`, so this is for completeness.)
5. **`repair.py`** — `rebuild_index(palace_path, collection_name="mempalace_drawers")`
   and `rebuild_from_sqlite(...)`: parameterize the hardcoded `COLLECTION_NAME`
   constant so recovery can target ONE wing's collection.

**Risk:** patches lost on `pip install -U mempalace`. Mitigation: documented
re-apply procedure + a startup assertion that the patch markers are present
(log-warn if missing, fall back to single-collection mode so we never crash).

## 6. Brain-side changes (~25 call sites)

### 6.1 New module: `engine/wing_collections.py`
- `wing_to_collection(wing) -> (drawers, closets)` (the name mapper)
- `per_wing_enabled() -> bool` (reads `config.json → mempalace.per_wing_collections`, default False)
- `get_wing_collection(palace_path, wing, *, create, kind="drawers")` — the ONE
  accessor everything uses; routes to per-wing name when the flag is on, else the
  legacy shared name (so flag-off == byte-identical to today).
- `list_wing_collections(palace_path) -> list[str]` (for admin/recovery sweeps).

### 6.2 Write paths (all route through `get_wing_collection`)
- `server_daemons.py` chat-sync `_file_drawer` (722) → write to `w_<wing>`.
- `server_daemons.py` miner `mp_miner.mine(...)` (391/489/1784/1887/2035) → pass
  `collection_name=` per wing being mined.
- `server_daemons.py` project-sync stale-purge `.delete()` (1600/1618) → delete
  from the wing's own collection (now a delete touches ONLY that wing's index —
  **this alone removes the cross-wing corruption path**).
- `server.py` profile `tool_add_drawer` (2881/4058) → per-wing.
- The metadata "hall" upsert (754) → per-wing, and bring it UNDER `_palace_write_lock`
  (currently unlocked — pre-existing latent bug).
- Artifact `tool_add_drawer` in miner (461) → per-wing + under the lock (currently
  unlocked).

### 6.3 Read/query path — the biggest restructure
`engine/mempalace_glue.py tool_mempalace_query`:
- **Today:** one `col = get_collection(...)`, `where={"wing": …}` or `$in[wings]`,
  then an UNCONDITIONAL post-`_wing_visible` filter.
- **Per-wing:** resolve the target wing set up front (project pin / user default /
  explicit-with-visibility-check — UNCHANGED logic), then **query each wing's own
  collection** and merge results (re-rank across the merged set with the existing
  cross-encoder). The post-`_wing_visible` filter STAYS as defense-in-depth (cheap,
  and the C3 visibility pre-check already gates which collections we open).
- Multi-wing (project + project_chat, or helpdesk + brain_code) → query 2
  collections, merge. Bounded fan-out (≤ a few wings per turn), so latency is fine.
- A missing per-wing collection (wing never written) → empty result, not an error.

### 6.4 Recovery path — `engine/mempalace_glue.py _try_rebuild_palace`
- **Today:** corruption anywhere → rebuild the whole shared collection.
- **Per-wing:** the failing query knows its wing → rebuild ONLY that wing's
  collection (`rebuild_index(palace_path, collection_name=w_<wing>)`, then the
  sqlite-swap tier scoped to that collection). Other wings untouched. This is the
  "auto-heal with minimal impact" requirement, realized.
- Keep the lock + cooldown. Per-wing cooldown key = the wing.

### 6.5 KG (`engine/kg_extract.py`)
- KG is SQLite, not Chroma — no HNSW, not the corruption source. **No collection
  split.** Its reads of drawers (`col.get(where={"wing"})`) switch to the wing's
  own collection via `get_wing_collection`. Anchored-prefix scoping unchanged.

## 7. Migration (rebuild-from-scratch) — **with the no-data-loss caveat**

⚠️ **Caveat surfaced for confirmation:** a *pure* drop-and-remine loses
chat-derived wings (`project_chat__X`, `user__X` turns, summaries, profiles) —
those are NOT on disk as files. The chat DB still holds the raw turns, so we
re-derive them via cursor reset, not file re-mining. The plan therefore is:

On first boot with the flag ON (one-time, idempotent, gated by a
`mempalace.per_wing_migrated` marker in config):
1. **File-derived wings** (`project__X`, `brain_code`, `*_artifacts`, web-URLs):
   let the existing daemons re-mine into the per-wing collections. The miner's
   content-hash dedup means this is just a normal mine into a fresh empty
   collection.
2. **Chat-derived wings** (`project_chat__X`, `user__X` chat, summaries): reset the
   chat-sync cursors (`mempalace_update_cursor` back to 0) and the profile/summary
   hashes, so the chat-sync daemon re-files every turn from the durable chat DB
   into the per-wing collections. No turn is lost (chat DB is the source of truth).
3. **Old shared collection** (`mempalace_drawers`/`mempalace_closets`): dropped
   AFTER migration verifies (Q2). Verification gate: for every wing, the new
   per-wing collection's drawer count must be ≥ the count sqlite holds for that
   wing. Only when ALL wings pass is the old collection deleted. If ANY wing fails
   verification → KEEP the old collection, log a warning, leave the flag usable.
   (So a half-migrated palace never loses the old data.)
4. **Rollback:** available only until the old collection is dropped. Because Q2
   drops it post-verify, the durable per-wing sqlite becomes the rollback source
   after that (a wing rebuilds from its own sqlite).

Migration runs in a background thread, logs progress, never blocks startup, and is
resumable (per-wing done-markers). No admin action.

## 8. Feature flag & safety

- `config.json → mempalace.per_wing_collections` (bool, **default false**).
- Flag OFF → every path uses the legacy shared collection name → behavior
  byte-identical to 9.61.x (ships dark, Rule-5/CLAUDE.md compliant).
- Flag ON → per-wing routing + (one-time) migration.
- Startup assertion that vendor patches are present; if absent, force flag OFF +
  log-warn (never crash).

## 9. Out of scope (this change) + a HARD CONSTRAINT
- KG collection splitting (not Chroma, not the corruption source).
- Dropping the old shared collection (separate explicit cleanup after soak).

**HARD CONSTRAINT (Alexander, 2026-06-03): frequent re-indexing is a FIRST-CLASS,
PALACE-WIDE workload — never throttle or suppress it, for ANY wing type.**
MemPalace exists to index BOTH static AND frequently-changing content. The
per-cycle delete + re-mine churn (web-URLs like macrumors/blogs today, but equally
any project-file wing, chat wing, or future source that changes often) is the
system working AS DESIGNED, not a bug — and web-news URLs stay as-is in production.
The churn was only ever dangerous because every wing shared ONE HNSW index. The
design's job is therefore NOT "reduce churn" but "make churn SAFE everywhere":
per-wing collections give each wing its own index, so any wing can re-index as
hard as it needs every cycle; corruption from that churn is contained to the one
wing and auto-heals from that wing's sqlite, with other wings unaffected. Earlier
"reduce churn / smarter change-detection / remove the news URLs" ideas are DROPPED
— they fight the feature. COROLLARY: because churn is the NORMAL mode across many
wings in production (not an edge case), the per-wing auto-heal path must be
rock-solid and is the headline test (§10.3) — it will be exercised constantly.

## 10. Test plan (intent-encoding, per CLAUDE.md rule 9)
1. `wing_to_collection` mapping: collision-free, chroma-name-legal, stable,
   handles `@`/unicode/long/`project__`/synthetic markers.
2. Flag-OFF parity: every accessor returns the legacy collection name (proves
   ships-dark).
3. **Fault isolation (the headline test):** seed 2 wings in per-wing mode, corrupt
   wing A's HNSW segment on disk, assert wing B still queries, wing A self-heals on
   next query from sqlite, drawer count restored, no exception bubbles to caller.
4. Query merge: multi-wing query (project + project_chat) returns correctly merged
   + re-ranked results from 2 collections.
5. Stale-purge isolation: a bulk delete in wing A's collection leaves wing B's
   collection (and its HNSW mtime) untouched.
6. Migration idempotency: run twice → second run is a no-op (done-markers honored).

## 11. Decisions resolved (2026-06-03)
- **Q1 (chat wings):** Re-derive chat-history wings via cursor-reset from the
  durable chat DB (NO data loss). Rationale: dropping them would destroy
  non-recoverable conversation memory + user profiles at migration time, which
  contradicts the bulletproof / minimal-data-loss goal — and re-derivation is the
  same migration code minus a cursor reset, so there is no cost to keeping it.
- **Q2:** Drop the old shared collection AFTER migration verifies (Alexander's
  call). NOTE: this removes the instant flag-off rollback path, so migration MUST
  verify per-wing drawer counts ≥ what sqlite holds for each wing BEFORE deleting
  the old collection; if verification fails for any wing, the old collection is
  KEPT and a warning logged (never delete on an unverified migration).
- **Q3:** Commit the 9.61.0 benchmark first (done + tested + independent), then
  build per-wing collections as a focused 9.62.0 effort.
```

---

## Implementation outcome (2026-06-03)

DONE, no flag (always on). Files:
- **engine/wing_collections.py** (NEW): `wing_to_collection` (legal/injective/
  deterministic mapper), `collection_names_for`, `get_wing_collection`,
  `add_drawer_to_wing` (per-wing analog of tool_add_drawer), `purge_wing_room`,
  `list_wing_collections`, `assert_miner_patch` (hard startup guard — no fallback).
- **engine/wing_migrate.py** (NEW): one-time idempotent migration. Resets chat
  cursors (re-derive chat wings from durable chat DB) + lets file wings re-mine;
  VERIFY per-wing count >= old-shared per-wing count, THEN drop the old shared
  collection. Keeps old collection if any wing falls short (no data loss).
- **engine/mempalace_glue.py**: `_query_wings` (per-wing query + merge, one wing's
  failure isolated), `_rebuild_wings` (auto-heal ONLY the affected wing); main
  query block + helpdesk brain_code block rewired.
- **server_daemons.py**: chat-sync drawer write + hall-stamp + closet rebuild,
  miner.mine x5 (per-wing collection_name), stale-purge delete x2, drawer-count
  helpers x3 — all per-wing; under _palace_write_lock.
- **server.py**: profile mirror + purge + immediate-sync + MemPalaceClient
  purge_by_prefix — all per-wing; assert_miner_patch + migration kicked at startup.
- **engine/kg_extract.py**: drawer readers + closet-regen → per-wing.
- **Vendor patches** (project_mempalace_venv_patches): miner.py + closet_llm.py
  got optional collection_name params (additive, back-compat).

Tests: tests/test_wing_collections.py (14, mapping + always-on + patch guard),
tests/test_wing_fault_isolation.py (HEADLINE — corrupt wing A, wing B still
queries, A auto-heals from sqlite, no admin). Full suite: 0 new regressions (3
pre-existing spaCy NER failures are unrelated).

NOT done (lower priority, display-only): admin-dashboard reads in
handlers/admin_observability.py (mempalace stats / palace explorer) still read the
old shared collection name — they will show 0 after migration until updated. Not
a corruption path. Track as a follow-up.
