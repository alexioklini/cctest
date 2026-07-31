---
name: project_sync_changed_file_optimization
description: "2026-06-23 (v9.189.4–.7) project-sync changed-file path made fast — PosixPath prefilter bug, closet/mine scoping, fingerprint recompute; folder/binary project gotchas"
metadata: 
  node_type: memory
  type: project
  originSessionId: 61f9a9c1-07ff-4546-9696-34f75401aee2
---

Continued from [[project_kg_sync_idempotency]]. Made the project-sync CHANGED-file path fast (was ~270-285s for a 1-file change; now ~14s file-based). Shipped + pushed to main (9.189.4/.5/.6/.7).

**Three wins (commit 42d641f, v9.189.4/.5/.6):**
- **9.189.6 = THE real fix.** The mine bulk pre-filter in `_mine_batched` (server_daemons.py) NEVER filtered: `mp_miner.scan_project()` yields `pathlib.PosixPath`, but drawer `source_file` keys + `mine()`'s `files=` list are `str`. PosixPath ≠ str as a dict key → `_mined.get(f)` always missed → ALL ~195 files hit `mine()`'s per-file `file_already_mined()` Qdrant skip-check = the ~264s. The 9.189.2/e449e31 prefilter AND upstream `bulk_check_mined` both had this bug — never filtered. Fix: `files = [os.fspath(f) for f in files]`. Verified live: 0/195 → 195/195 path matches; indexing 264s→1.9s.
- **9.189.5**: wing-scoped the prefilter fetch — one `col.get(where={wing})` instead of `bulk_check_mined()` scanning the whole shared corpus (all projects). (Turned out NOT the bottleneck — the PosixPath bug was — but still correct at scale.)
- **9.189.4**: closet regen rebuilds ONLY changed sources via `_regen_closets_parallel(only_sources=set(stale_sources))` in engine/kg_extract.py (idempotent per-source purge+upsert, our OWN reimpl not upstream `regenerate_closets`) — 1 source ~6s vs full-wing ~195 LLM calls.

**Folder/binary projects (kg-real-policies, external recursive input_folders of PDF/DOCX) — extra findings:**
- **9.189.7 (commit 47bb084)**: the no-change fingerprint gate (9.189.3) sampled the fp at iteration START, but doc_convert regenerates `.brain-extracted/<name>.md` companions DURING the sync → mtimes move after sampling → stored fp mismatched settled tree → NEXT cycle did a full ~55s sync before converging. Every change cost one wasted full cycle. Fix: re-stat the fp at successful completion (`final_state=='idle'`) instead of reusing `_cur_fp`. Verified: PDF touch→full re-mine(192s), then immediate no-change sync skips 0.00s (was 55s catch-up). File-based unaffected.
- **BOTH open items now FIXED (v9.189.8, commit f66694e):**
  - **Item 1 (prefilter for folder/binary):** input_folders + web-urls were mined with a RAW `mp_miner.mine()` that bypassed `_mine_batched`'s prefilter — that was the real cause (NOT a path mismatch; scan_project actually returns the `.brain-extracted/*.md` companions which DO match stored source_file keys, verified 15/15). Fix: route both through `_mine_batched` (added `respect_gitignore` param: ingested=False, input_folders=True, web-urls=False). IT-folder 1-file change indexing ~21s→~1.8s.
  - **Item 2 (wing-wide entity-link rebuild ~100-165s per mine):** mp_miner.mine() runs hallways+topic+entity-tunnel recompute (full, ∝ wing size, no upstream skip flag) at the end of EVERY call. THREE fixes: (a) empty scan_project (ingested/ = only mempalace.yaml) no longer calls mine() at all (the old whole-folder fallback fired the rebuild on every empty mine = the 168s ghost step → 0.0s); (b) `_mine_batched` calls mine() ONCE over all changed files, not per-25-batch (was multiplying the fixed rebuild by batch count); (c) **venv patch** `mempalace/miner.py` gates the rebuild on `total_drawers > 0` so a touch-only/identical re-mine (0 drawers) skips it — see [[project_mempalace_venv_patches]] Patch (5). A REAL content change still pays ONE rebuild (upstream full recompute, not a delta).
  - Verified: kg-real-policies warm 1-file change 30.7s (was ~202s), 0 entity-link rebuilds when drawers=0; no-change 0.00s; risikoanalysen file-based unaffected. The `files_filed:3024` in an old summary was the entity-link count, NOT real files.

**Verification harness** (no eval file — live API): mint admin JWT (recipe in HANDOVER_SYNC_OPTIMIZATION.md), `POST /v1/agents/main/projects/<name>/sync-now` (single-threaded daemon, QUEUES — wait for the run row), read phase timing from `project_sync_runs.log` JSON (`folders[].steps.{doc_convert,indexing,kg}` + top-level `steps.closet_rerank`; elapsed_s per phase). Daemon log = ~/.brain-agent/server.error.log. ⚠️ DON'T `: > server.error.log` to clear it while the daemon holds the fd — writes stop landing until restart; truncate only when about to restart anyway.

**KG triple count question (user asked why 571 not "~50%"):** NOT a bug. The per-run `kg_extraction_log.triples_extracted` (~179-305, fluctuating) is ONE cycle's extraction, NOT the KG size. The buggy cursor re-extracted the whole source every cycle with varying LLM output → the number the user watched swung. Live KG holds the UNION across all chunk source_files (NRA = 26 chunk files → 502 triples, 0 duplicates, accumulated across extraction timestamps). Cursor fix froze it stable. 7/571 use off-profile predicates (LLM invented despite normative profile) = 1.2% noise.
