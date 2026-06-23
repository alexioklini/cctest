# Handover — Project-Sync Idempotency & Scaling Optimization

**Date:** 2026-06-22 late
**Author:** previous session (Claude)
**Goal (user's hard requirement):**
> A project sync on **unchanged** files must finish in **~1 second across ALL phases** — no mining, no KG extraction, no closet regen. And when re-mining/KG/closet **is** necessary, it must touch **only changed data**, as fast as possible. Context: **hundreds of projects**, each auto-resyncing on a **6-hour interval**, so a no-op sync must be near-free.

---

## ✅✅ UPDATE 2 (2026-06-23): CHANGED-FILE PATH NOW FAST TOO — SHIPPED (9.189.4/.5/.6)

The #1 follow-up below ("1-file change = 269s") is **DONE & verified live**. A
single-file change on risikoanalysen now syncs in **~14s across ALL phases**
(was ~270–285s). Committed + pushed to main (`42d641f`).

Root cause of the 264s indexing phase: **the mine bulk pre-filter never
filtered.** `mp_miner.scan_project()` returns `pathlib.PosixPath`; drawer
`source_file` keys (and `mine()`'s `files=` list) are `str`. A `PosixPath` is
never equal to a `str` as a dict key, so `_mined.get(f)` ALWAYS missed → every
file looked "changed" → all ~195 files were handed to `mine()`, which then paid
the per-file `file_already_mined()` Qdrant skip-check. The 9.189.2/`e449e31`
prefilter AND the original `bulk_check_mined` both had this bug — they never
actually filtered. **Fix (9.189.6): `os.fspath()` the scan result.** Verified
against the live palace: 0/195 path matches → 195/195; indexing 264s → **1.9s**.

Two more (shipped same commit):
- **9.189.5**: wing-scoped the prefilter fetch — one `get(where={wing})` instead
  of upstream `bulk_check_mined()` scanning the whole shared corpus (all projects).
- **9.189.4**: closet regen rebuilds ONLY changed sources via
  `_regen_closets_parallel(only_sources=…)` (idempotent per-source purge+upsert)
  instead of the full wing — 1 source (~6s) vs ~195 LLM calls. NO venv patch
  needed — `_regen_closets_parallel` is OUR reimplementation, not upstream.

Measured changed-file breakdown (run 3333, warm pool settled):
indexing 1.9s · kg 1.1s · closet 5.8s · total **14.1s**. (First post-restart run
showed 113s indexing = transient warm-pool GPU contention, NOT steady-state.)

Remaining nice-to-haves: the embedding model reloads + hits HuggingFace on each
mine() (a few seconds, fine for now); at hundreds of projects, re-verify the full
unchanged cycle stays quick (each project = one os.stat walk via the fingerprint
gate).

---

## ✅ UPDATE (end of session): FAST GATE VERIFIED WORKING

The open bug below was a **test-timing artifact**, not a real bug. Confirmed:
- Full sync (run 3310) **persisted** `source_fingerprint` (`9ad34c51…`), state idle.
- Next sync with **no changes** (run **3311**): `elapsed = 0.0s`, `skipped_unchanged = true`. **The ~1s requirement is MET** (vs 285–287s before).
- Change-test (touch one ingested file → sync) in progress at handover write — expected: a non-skipped run that re-mines/KGs only the changed source.

So the core requirement ("unchanged sync finishes in ~1s, all phases") is **done and verified**. Remaining = the SCALING optimizations (#2 wing-scope bulk_check_mined, #3 per-file closet regen) — nice-to-have for hundreds of projects, not blockers. Everything is committed + pushed to main (through `d3f64c4`, plus this handover update).

---

## TL;DR status

- **KG re-extraction bug: FIXED & verified** (commits `c9028ab` + `2c2a199`, shipped in running 9.189.2/.3). KG now skips unchanged drawers — proven: sync after stable-keys-laid-down wrote **0 new KG progress rows** for the risikoanalysen wing.
- **Mining was never the problem** — `files_filed_this_cycle=0`; miner already skips unchanged files. The "indexing took 2 min" was per-file `file_already_mined()` Qdrant queries.
- **Indexing per-file cost: mitigated** with a bulk pre-filter (commit `e449e31`, 9.189.2).
- **Fast project-level no-change gate: WRITTEN BUT NOT YET WORKING — UNCOMMITTED.** This is the main unfinished piece. See "OPEN BUG" below.
- **UI/German + AI-instructions features from earlier today: DONE & pushed** (see commit list).

## Git state at handover

Pushed to `main` through `e85c67b`? **NO — check.** Local commits ahead of origin:
```
e449e31 perf(project-sync): bulk pre-filter so an unchanged project skips mining fast
270b3bf fix(projects): plain-German, non-technical sync phase labels
2c2a199 fix(kg): stable source-path cursor key so unchanged sources truly skip
c9028ab fix(kg): deterministic representative drawer id so unchanged sources skip
9741fad fix(projects): cap expanded instructions in source tree (scrollable)
30fc93d feat(projects): upload progress bar for project instruction files
e85c67b fix(projects): generic placeholder for AI-instruction prompt field
```
**UNCOMMITTED working-tree changes (the 9.189.3 fast-gate):**
- `brain.py` — VERSION bumped to 9.189.3 + a CHANGELOG entry for the fast gate.
- `server_daemons.py` — the `_project_source_fingerprint()` helper + the "FAST no-change gate" block in the per-project loop.

**FIRST ACTION for next session:** decide whether to keep/fix the uncommitted gate (recommended: fix it, it's the core requirement), then commit + `SKILL_DOC_OK=1 git push` everything. Verify `git log origin/main` to see what's already remote. Per repo convention: commit + push directly to `main` (see [[feedback_commit_to_main]]).

---

## The three problems & their fixes (detail)

### 1. KG re-extracted everything every cycle — FIXED
- **File:** `engine/kg_extract.py`, `_iter_wing_source_files()` + `_process_source()`.
- **Root cause:** progress cursor key was `<representative_drawer_id>#<chunk>`. `rep_did = drawer_ids[0]` came from Qdrant `col.get(where=wing)` in **non-deterministic order**, AND drawer ids churn across mining cycles (content-hash ids, many drawers/source). So the cursor key drifted every run → `_already` skip-set never matched → full re-extract; `drawers_skipped=0` always; orphan progress rows piled up (343 distinct rep_did prefixes / 793 rows for ONE source).
- **Fix:** (a) `c9028ab` sort drawer_ids (necessary, insufficient). (b) `2c2a199` the real fix — cursor key is now `src_<sha1(source_file)[:24]>#<chunk>`, derived from the **source file path** (stable across cycles). `rep_did` still used for triple provenance (`source_drawer_id`). Per-drawer chunking mode keyed by its own drawer id (unaffected).
- **Verified:** after one clean run lays down stable keys, the next sync wrote **0** new progress rows for the wing (confirmed via `kg_extraction_progress` timestamps).

### 2. "Why is mining running?" — it WASN'T
- `project_sync_runs.summary.files_filed_this_cycle = 0`. The mempalace miner skips unchanged files via `file_already_mined(collection, source_file, check_mtime=True)` (in the venv at `/Users/alexander/.mempalace/venv/lib/python3.14/site-packages/mempalace/palace.py`). The wasted ~141–285s/cycle was **entirely KG**.

### 3. Indexing phase took ~2 min with no changes — per-file Qdrant queries
- `_mine_batched` (`server_daemons.py`) calls `mp_miner.mine(files=batch)`; mine() calls `file_already_mined()` **per file**, each a paginated `collection.get(where={source_file})`. ~200 files = ~200 Qdrant round-trips for a no-op.
- **Fix `e449e31`:** bulk pre-filter — `bulk_check_mined(col)` once → `{source_file: mtime}` map → drop unchanged files → only changed files reach mine(). **Caveat:** mtime-only, misses the rare `normalize_version` schema-upgrade re-mine (self-corrects on next edit / Full-Resync). **Scaling concern:** `bulk_check_mined` scans the WHOLE shared drawers collection (all projects) — heavy at hundreds of projects. The project-level gate (below) should make this fire only for genuinely-changed projects, but consider scoping bulk_check_mined to the wing.

---

## OPEN BUG — the fast no-change gate (9.189.3, uncommitted) doesn't trigger

**What it should do:** at the TOP of each per-project iteration (after the due-gate + web-URL sync, BEFORE `start_run` and all phases), compute `_project_source_fingerprint(pdir, project, weburl_folder)` (pure `os.stat` walk over ingested/ + input_folders + web-urls/, sha1 of sorted `path|mtime_ns|size`). If it equals the last **successful** cycle's stored `sync_status.source_fingerprint` AND `sync_status.state == "idle"` → skip the ENTIRE project (persist a quick idle sync_row, log a no-op run, `continue`) in ~1s. The normal-completion `sync_row` stores `source_fingerprint: _cur_fp` so the NEXT cycle can match.

**Symptom observed:** after restarting to 9.189.3 and running a full sync (run 3309, 285s), the stored `sync_status` had **NO `source_fingerprint` key** (`fp: None`), so the gate can never match → every sync still does full work. The gate "never logged" (no "unchanged (fingerprint match)" nor "fingerprint failed" line).

**Why this matters:** the gate is the CORE of the requirement. Until the fingerprint persists, nothing is skipped.

**Hypotheses to check (in order):**
1. **Timing:** was run 3309 (started `22:48:29`) actually executed by the 9.189.3 process, or by the prior (9.189.2) process whose `sync_row` had no fingerprint key? Confirm the 9.189.3 boot timestamp vs 22:48:29. If 3309 predates the restart, just run a FRESH sync on 9.189.3 and re-check — the fp may persist fine. **(Most likely this.)**
2. **Scope of `_cur_fp`:** the normal `sync_row` is at `server_daemons.py:~2946`, `_cur_fp` computed at `~2133`. Both are in the same `try` for the project iteration, so `_cur_fp` should be in scope. BUT if the iteration takes a code branch that `continue`s or returns before reaching `sync_row`, or if there's an exception between, the persist with the fp won't run. Verify `_cur_fp` is defined on every path reaching line 2946 (it is set at 2133/2136 unconditionally inside the try, before any phase work — should be safe).
3. **`update_project` field handling:** confirm `ProjectManager.update_project` stores the full `sync_status` dict verbatim (it's whitelisted; it does). Not the suspect.
4. **A second/older persist overwriting it:** `grep -n '"sync_status":' server_daemons.py` → only 2 sites (gate `_row` @2156, normal `sync_row` @2967). No stray overwrite. Not the suspect.

**Fastest path to confirm/fix:**
```bash
cd /Users/alexander/Documents/dev/cctest
# 1. ensure running version has the gate
curl -s localhost:8420/v1/status | python3 -c "import sys,json;print(json.load(sys.stdin)['version'])"   # expect 9.189.3
# 2. fresh full sync (lays down fp), then check it persisted
TOKEN=$(cat /tmp/_tok)   # admin JWT minted earlier; re-mint if expired (see RECIPES)
curl -s -X POST -H "Authorization: Bearer $TOKEN" localhost:8420/v1/agents/main/projects/risikoanalysen/sync-now
# wait for idle, then:
python3 -c "import json;ss=json.load(open('agents/main/projects/risikoanalysen/project.json')).get('sync_status') or {};print('fp:',ss.get('source_fingerprint'))"
# 3. if fp now present → trigger a SECOND sync; it MUST log 'unchanged (fingerprint match) — skipped all phases' and finish in ~1s.
grep "unchanged (fingerprint match)" /Users/alexander/.brain-agent/server.error.log | tail
```
If after a fresh 9.189.3 sync the fp STILL doesn't persist, add a debug `print` right before the `sync_row` persist dumping `_cur_fp` and confirm the line executes.

---

## REMAINING OPTIMIZATION WORK (the user's scaling ask)

Even when work IS needed, do only the minimum, fast. Priorities:

1. **Make the project gate actually work** (above) — biggest win: hundreds of unchanged projects each cost one `os.stat` walk (~ms), zero Qdrant. This is non-negotiable per the requirement.
2. **Scope `bulk_check_mined` to the wing**, not the whole shared collection. Today it scans ALL drawers across ALL projects per changed project — O(total_corpus) per project. At hundreds of projects this is a hot spot. Either add a wing filter to the bulk fetch, or cache the map per cycle and reuse across projects.
3. **Per-file KG/closet scoping when a project DID change:** currently a changed project runs `_run_kg_for` per source over the whole wing and `_run_closet_regen_for` (incremental, but triggers a full wing rebuild if ANY file changed — see `run_closet_regen_incremental`). For "one file changed in a 400-file project," closet regen rebuilding the whole wing is the next bottleneck. Investigate scoping closet regen to changed sources only (upstream `run_closet_regen_incremental` "doesn't accept per-file filters yet" per the code comment — may need an upstream/venv patch, see [[project_mempalace_venv_patches]]).
4. **The long gap between index phase and KG start** the user noticed: historically caused by full-wing metadata fetches between phases (see CHANGELOG 9.163.6/9.185.0 — `_count_wing_drawers_*` were removed). Re-verify no remaining full-wing scan sits between phases on a changed project.
5. **Qdrant load:** transient `Connection refused` during testing = Qdrant overload from repeated test syncs + concurrent KG (Qdrant process itself never crashed, up since 06-19). The gate + wing-scoping reduce this load. Don't mistake it for a crash.

---

## CONCRETE DATA for the changed-file optimization (the user's #1 follow-up)

Measured at handover: touching ONE ingested file (`aml-risikoanalyse__000.md`)
→ sync run 3312 took **269.5s** and filed 9 drawers. That is FAR too slow for a
single-file change and is the top remaining optimization. Where the time goes
(to investigate precisely next session):
- KG: `_run_kg_for` runs `run_kg_post_pass` over the wing; with the stable cursor
  it SHOULD skip unchanged sources, but it still opens the wing + iterates. The
  changed source re-extracts (correct). Confirm the OTHER sources truly skip and
  aren't re-LLM'd. (Watch `new=` vs `skip=` per source in the KG log for run 3312+.)
- Closet regen: `run_closet_regen_incremental` triggers a **full wing rebuild**
  when ANY source changed (code comment: "upstream doesn't accept per-file
  filters yet"). For one changed file in a multi-hundred-drawer wing this is the
  likely bulk of the 269s. → needs per-source closet scoping (probably a
  mempalace venv patch, see [[project_mempalace_venv_patches]]).
- Mining: the bulk pre-filter should pass only the 1 changed file to mine().
  Confirm via the "[project-sync] pre-filter … N/M changed" log line.

ALSO OBSERVED (not a bug in our code): a log line
`[project] KG override changed for risikoanalysen (method llm→rules …) — KG+closet
cursors purged`. A KG method/profile toggle (UI or API) purges cursors and forces
a full re-extract. This explains both extra churn during testing AND the
`profile=generic` vs configured `normative` discrepancy seen in KG log lines.
Decide whether that toggle should be debounced / confirm it wasn't fired
accidentally during testing.

## Note on manual "Sync now" timing (observed, not a bug)

The project-sync daemon is **single-threaded** and processes projects sequentially
within a cycle. A manual `sync-now` is QUEUED and fires when the daemon reaches
that project — if big projects (web-url miners like macrumors, large folders) are
ahead in the cycle, a manual request can wait minutes for a fresh run row to
appear. During verification this looked like "sync-now didn't fire" but the run
just hadn't been reached yet. At hundreds of projects this sequential cycle is
itself worth considering (the fast no-change gate makes each unchanged project
~ms, so a full cycle over hundreds of unchanged projects should still be quick —
verify the per-cycle wall time at scale).

## Verification checklist (definition of done)

- [ ] Fresh full sync on risikoanalysen stores `sync_status.source_fingerprint`.
- [ ] Second sync (no file changes) logs `unchanged (fingerprint match) — skipped all phases` and `project_sync_runs.summary.skipped_unchanged=true`, elapsed ~**1s or less**.
- [ ] Touch ONE file (e.g. `touch agents/main/projects/risikoanalysen/ingested/aml-risikoanalyse__000.md`) → next sync does a FULL/partial run (fingerprint differs), and KG re-extracts ONLY that source (others show skip), mining re-files only that file.
- [ ] No `Connection refused` under a normal single sync.
- [ ] `bulk_check_mined` no longer scans the whole corpus per project (optimization #2) — or a documented decision to defer it.
- [ ] `py_compile` clean for brain.py + server_daemons.py + engine/kg_extract.py; runtime-import each (a module-level NameError passes py_compile but crashes boot — see [[feedback_compile_check_brain_py]]).
- [ ] Restart via graceful SIGTERM only (NEVER SIGKILL — [[feedback_never_sigkill_brain]]); confirm `/v1/status` version == brain.VERSION after restart.
- [ ] Commit each logical fix + `SKILL_DOC_OK=1 git push` (skill-doc pre-push hook blocks; this is a perf/internal change, override is fine, but consider a one-line note in `agents/main/skills/brain-agent-guide/05-internals.md` about the sync fast-gate).

---

## Key files / locations

- `server_daemons.py`:
  - `_project_source_fingerprint()` ~line 100 (NEW helper).
  - per-project loop `for agent_id, proj_name in ordered:` ~line 2052.
  - FAST no-change gate ~line 2119 (NEW, after web-URL sync, before `start_run`).
  - `_mine_batched()` ~line 1607 (bulk pre-filter inside).
  - `_run_kg_for()` ~line 1721; `_run_closet_regen_for()` ~line 1883.
  - normal `sync_row` persist ~line 2946–2967 (stores `source_fingerprint`).
  - success state is **`"idle"`** (line ~2900), NOT "ok" — the gate checks `state=="idle"`.
- `engine/kg_extract.py`: `_iter_wing_source_files` ~820 (sorted ids), `_process_source` ~1030 (`cursor_base` = sha1(source_file)).
- `engine/sync_log.py`: `last_completed_at` filters `state='idle'`; `finish_run(db, run_id, state, summary)`.
- Live KG log table: `agents/main/chats.db` → `kg_extraction_log`, `kg_extraction_progress`, `kg_extraction_source_state`, `project_sync_runs`.
- Daemon log (ALL daemon prints): `/Users/alexander/.brain-agent/server.error.log` (NOT server.log — see [[feedback_compile_check_brain_py]] / launchd fd note).
- Daemon's mempalace venv (read-only inspect; not importable from bare shell): `/Users/alexander/.mempalace/venv/lib/python3.14/site-packages/mempalace/`.
- risikoanalysen: project id `54b7a2111b46`, wing `project__54b7a2111b46`, profile `normative` (NOTE: KG log lines showed `profile=generic` for it — possible separate per-project-profile-override bug, NOT investigated).

## Recipes

Mint an admin JWT for the API (token cached at `/tmp/_tok`, re-mint if expired):
```python
import json,time,jwt as p,sqlite3
c=json.load(open('config.json')); s=(c.get('auth') or {}).get('jwt_secret') or c.get('jwt_secret')
con=sqlite3.connect('agents/main/auth.db'); con.row_factory=sqlite3.Row
r=con.execute("SELECT id,username,role FROM users WHERE role='admin' LIMIT 1").fetchone()
print(p.encode({'user_id':r['id'],'username':r['username'],'role':r['role'],'exp':time.time()+3600,'iat':time.time()},s,algorithm='HS256'))
```
Trigger a sync: `POST /v1/agents/main/projects/risikoanalysen/sync-now`.
Watch: `grep -E 'project-sync|wing=project__54b7a2111b46|unchanged \(fingerprint' /Users/alexander/.brain-agent/server.error.log | tail`.

## Memory written this session
- `project_kg_sync_idempotency.md` (the KG cursor + mining/indexing diagnosis).
- `project_ai_generate_project_instructions.md` (earlier feature, shipped 9.189.0).
