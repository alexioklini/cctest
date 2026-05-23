# Refactor Handover — Module Extraction

**Goal:** Identify *all* opportunities to refactor the codebase into reusable modules, then execute the extraction completely (the previous attempt was left incomplete).

**Status:** Plan written. **Phase 0 (safety net) DONE.** Execution protocol set. Ready to start Phase 1.
**Last updated:** 2026-05-23 · **HEAD:** `d48b5de` on `main` · **Resume:** read this file top-to-bottom, then jump to *"How to resume"*.

> **When the user says "refactor the code" (or similar), execute autonomously per the *Execution protocol* section below — do NOT ask which phase to start or pause at each boundary. The protocol already encodes the answers.**

---

## TL;DR for the next session

1. The full extraction plan is in **`REFACTOR_PLAN.md`** (already written). Read it.
2. A reusable verification gate is in **`refactor_gate.sh`** (already written, validated). Run it after every extraction.
3. Two files are **uncommitted** (`REFACTOR_PLAN.md`, `refactor_gate.sh`). Decide whether to commit them first.
4. **Two open decisions block nothing but should be settled** (see *Open decisions*). Then start **Phase 1: audit last time's incompleteness** (sweep `engine/` for surviving duplicate copies in `brain.py`).
5. Execution style chosen by user: **phase-gated checkpoints** — do a full phase, stop, show grep-proofs + test results, then continue.

---

## ✅ RESUME POINT (2026-05-23): Phases 1–3 DONE — start here with Tier C

**Phases 1–3 complete** (17 extractions, 0 reverts, gate green). brain.py 25,182→18,814 (−25.3%), handlers/admin.py 5,416→79, server.py 5,827→3,895. Full per-domain status in `REFACTOR_REPORT.md`. HEAD `746ed54` + report commit on `main`.

**Tier C decision — USER-APPROVED 2026-05-23: "Full Tier C, gated per-step."** Do C1 + C2 + C3, but:
- **C2 prerequisite (hard):** write + commit a characterization test for the tool-exec path FIRST (mirror what `tests/test_scheduler_characterization.py` did for B2). The tool-exec layer has no existing tests.
- **The Tier C gate is heavier than `./refactor_gate.sh`** — each sub-step must additionally pass: **(C1)** eval harness within noise (Δ < 0.10 of baseline) + warmup **byte-identical** KV-prefix check; **(C2)** the new characterization test + eval; **(C3)** project-wing isolation test (`project__*` must not leak — security).
- **Stop on ANY eval regression** (Δ ≥ 0.10) — revert that sub-step, log BLOCKED, surface to user. Do NOT push through eval noise.
- Eval harness lives in `eval/` (`judge_mistral.py`, `harness/`, `questions.json`). It consumes provider quota — that's expected and approved for Tier C.
- Same per-extraction discipline as Phases 1–3: subagent-per-extraction, gate-2 (old `def`/`class` GONE), commit per green sub-step, update + publish `REFACTOR_REPORT.md` each time, flip the ⛔ rows in the Master domain map.
- D2 already done in Phase 1; **D1/D3 confirmed clean** (audit). The only Tier-D remnant for Phase 4 is finishing-as-needed alongside C2/C3 — verify, don't redo.

**First steps for the new session:** read `REFACTOR_REPORT.md` (source of truth), run `./refactor_gate.sh` (confirm still green), then start C2's characterization test (the prerequisite) OR C1 — your pick, but C1/C2 one-at-a-time with the full eval gate between.

---

## Execution protocol (how to run this autonomously — user-approved 2026-05-23)

**Driver:** continuous single session held open by a goal-condition until *Phases 1–3 complete + gate green + report pushed*. Run extraction-after-extraction without pausing for the user; clean context via subagent-per-extraction + disk-state. Hard-stop before Tier C (Phase 4).
**On obstacle (failed gate / genuine ambiguity):** revert that extraction (tree stays clean — principle #2), log it as BLOCKED in `REFACTOR_REPORT.md`, **skip to the next independent domain**, and surface all blockers together when pausing at Tier C. Do NOT halt the whole run for one snag. Only a systemic failure (gate broken, can't import at all, repo/push auth dead) halts everything.


The user wants: *"say refactor the code, walk away, come back hours later done"* — without manually starting each phase. These settings make that safe and uninterrupted up to Tier C.

**Autonomy boundary:** Run **Phase 1 → Phase 3 (audit + Tier A + Tier B + handler/db/server splits) start-to-finish, unattended.** Commit after each green gate. **HARD STOP before Tier C** (C1 model-select/system-prompt, C2 tool-exec, C3 MemPalace glue) and report — Tier C is eval-gated + KV-cache-sensitive and needs the user's review before proceeding.
Also stop immediately (don't push through) on: a **failed gate**, an **import break**, or a **decision the plan/handover doesn't already answer**.

**Context-window discipline (this is what keeps a multi-hour run from overfilling):**
- **One subagent per extraction.** Spawn an `Explore`/`general-purpose` agent to do the bulky work — read the brain.py block, grep all call sites, run `./refactor_gate.sh`. The agent's reads/greps/test logs stay in *its* context and die with it. It returns ONLY: pass/fail, the call sites it repointed, and one line of summary. The main thread must NEVER read brain.py wholesale or dump grep/test output into its own context.
- **Disk is the memory, not the conversation.** After each extraction, update `REFACTOR_REPORT.md` (the living progress report). State survives compaction AND a fresh session. The conversation is disposable; the report is truth. On resume, read `REFACTOR_REPORT.md` first to see what's done. Each report entry must answer the four questions: **what moved · where to · was the old code deleted (principle-#3 evidence) · did the gate/tests pass.** Use the template in that file; log REVERTED/BLOCKED attempts too, not just successes.
- **Commit after every green extraction** (one extraction = one commit, directly to main per project rule). Any point in the run is therefore recoverable; a later failure never strands earlier good work.

**Pre-decided (do NOT re-ask):**
- *NER tests:* the 3 `test_pii_ner.py` failures stay the fixed baseline ("no new failures"). Do NOT load spaCy per gate run.
- *Smoke test:* NOT automated. Rely on import-gate + unittest between phases; the user does a manual chat sanity-check after the run. (Tier C still requires the eval harness — that gate stands.)
- *Characterization tests (from external-analysis review, see `REFACTOR_PLAN.md` §1.5):* the core paths (scheduler, tool-exec, sessions, sidecar) have NO tests, so the gate can't catch regressions there. **Before B2 (scheduler) and before Tier C (C2 tool-exec), first write+commit behavior-pinning tests for ONLY that path**, so the gate is trustworthy for the risky moves. Tier A needs none (self-contained). This is a hard prerequisite, not optional.
- *Out of scope (do NOT start):* the ~29k-line JS frontend, full thread-local→DI conversion, encoding-all-invariants-as-tests. Tracked in `REFACTOR_PLAN.md` §8 as separate future initiatives. B1 relocates thread-locals only — do not attempt DI conversion.

**Per-extraction loop (the subagent runs this, returns the result):**
1. Move the block to its new module; re-export from brain.py so callers still resolve.
2. Gate 2: `./refactor_gate.sh grep <SYMBOL>` → confirm brain.py shows ONLY changelog/comment hits.
3. Gate 3: grep call sites repo-wide → all resolve to the new module.
4. Gates 4+5: `./refactor_gate.sh` → imports clean + no new test failures.
5. If all pass: commit, append a full entry to `REFACTOR_REPORT.md` (what moved · where · old code deleted? · tests) + flip the domain's row in the *Master domain map* + flip the Status board + update Running totals. If any fail: revert the move, log it as a REVERTED/BLOCKED entry in the report, STOP and report.

> `REFACTOR_REPORT.md` opens with a **Master domain map** = the COMPLETE scope (every domain done/planned-with-phase/gated/excluded-with-reason). It is fixed up front, not grown. The autonomous run flips statuses in it; it does not add rows. A genuinely new domain = a scope change to surface to the user, not a silent append.

> **Publish the report on every update (user wants remote viewing).** After updating `REFACTOR_REPORT.md`, run `./refactor_publish.sh "<commit msg>"` — it regenerates `REFACTOR_REPORT.html` (zero-dep converter `refactor_report_html.py`) and pushes to `origin/main`. **HTML is a generated VIEW, never hand-edited — only the `.md` is the source of truth I read/update.** Repo is PUBLIC (user-approved 2026-05-23); report viewable at https://github.com/alexioklini/cctest/blob/main/REFACTOR_REPORT.html. Never edit the .html directly; if it drifts from the .md, regenerate.

---

## Why last time was incomplete (the core lesson)

The changelog shows this codebase already ran **four** duplication-elimination sweeps (v8.28.0 → v8.32.0) chasing the same "drift trap," and the v9.10.0 doc-extraction unification needed **five commits** to actually finish. The failure mode is consistent:

> **"Extract" is two operations and only the first gets done:**
> 1. ✅ Create the new module / copy the logic out
> 2. ❌ **Delete the old copy and re-point every caller** ← this is what slips

An extraction that only does step 1 leaves a second copy that silently drifts. So the execution discipline is built to make step-2 incompleteness **fail loudly**.

---

## The completeness contract — "Definition of Done" per extraction

A single extraction is done **only when all five gates pass** (not when the new module merely works):

| Gate | Check | Catches | How |
|---|---|---|---|
| 1. Moved | new module owns the logic | — | manual |
| 2. **Old copy deleted** | `grep <symbol>` in brain.py returns **only changelog/comments**, zero live code | the drift trap | `./refactor_gate.sh grep <SYMBOL>` |
| 3. Callers re-pointed | every call site resolves to the new module | orphaned callers | grep call sites repo-wide |
| 4. Imports clean | all core+handler modules import | NameErrors from missed re-exports | `./refactor_gate.sh` (Gate 4) |
| 5. Behavior unchanged | no new test failures | silent regressions | `./refactor_gate.sh` (Gate 5) |

**Rules:** one extraction = one commit; never batch (batching hides which move broke something); commit only when the gate passes; state gate results in the commit message.

---

## Phase 0 — DONE (safety net established + validated)

### Baseline recorded at clean HEAD `4bad7e4`
- **Imports: 18/18 clean** — `brain`, `server`, 11 handlers, `server_lib.{db,auth}`, `engine.{doc_convert,classification,kg_extract}`. *This is the gate that catches missed re-exports.*
- **Tests: 80 pass / 3 fail** via stdlib `unittest`. The **3 failures are all in `test_pii_ner.py`** and are **environmental** (spaCy `de_core_news_md` is installed but not loaded in the bare test process — server loads it lazily at startup via `engine.pii_ner.load_models()`, the test process never calls that). **Not code defects; will not move during refactoring.** Gate rule = "no *new* failures beyond these 3 named tests."
- Server daemon healthy on :8420; sidecar healthy on :8421 (`anthropic 0.101.0`).

### Environment facts (these cost time to discover — don't re-learn them)
- **Daemon interpreter = `/opt/homebrew/bin/python3` (Python 3.14)**, per `~/Library/LaunchAgents/com.brain-agent.server.plist`. Use this exact path for all checks.
- It is **PEP 668 externally-managed** → `pip install` refuses without `--break-system-packages`. **Do NOT install pytest into it.** The tests are plain `unittest`, so `python3 -m unittest discover -s tests -p "test_*.py"` runs them with zero install.
- `brain.py` lines ~8–110 are a giant **changelog string** (version tuples). It matches almost any symbol grep — that's why Gate 2 says "only changelog/comments." Eyeball accordingly. *(Possible future refinement: make the grep helper exclude the version-tuple lines explicitly so the proof is readable.)*
- `/health` on :8420 returns `{"error":"Not found"}` — wrong path, the server uses a different health route. Sidecar `/health` works. Not a problem.

### Files created this session (UNCOMMITTED)
- `REFACTOR_PLAN.md` — the full extraction plan (all candidates, tiers, sequencing, invariants).
- `refactor_gate.sh` — the verification gate (validated: PASS at clean baseline; grep helper proven on a deleted symbol `tool_read_attachment` → only changelog hits, and a live symbol `_wf_parse` → real refs).

> **First action tomorrow:** decide whether to `git commit` these two files before starting. (Project rule per CLAUDE.md / memory `feedback_commit_directly_to_main`: commit directly to main, no branches, no PRs.)

---

## Open decisions (settle before/early in Phase 1)

1. **The 3 NER test failures.** Current approach: treat as fixed baseline ("no new failures"). Alternative: have the gate load the spaCy model first so all 83 pass. **Recommendation: leave as known-baseline** — loading NER per gate run adds ~120 MB + latency to every check. *(User has not yet answered.)*

2. **Smoke test (boot + 1 chat turn + 1 scheduled fire).** Not yet built — it consumes provider quota and touches real DBs, so it wasn't auto-created. Options: (a) script it against the running daemon, (b) rely on import-gate + unittest and do a manual chat sanity-check each phase, (c) defer until Phase 4 (eval-gated core work) where it matters most. *(User has not yet answered.)*

---

## The plan (summary — full detail in `REFACTOR_PLAN.md`)

**Ground truth (verified):** `brain.py` = 25,182 lines. **Dependency DAG is clean & one-way** — `brain.py` imports nothing from `handlers/`/`server_lib/` (verified: `grep "^from handlers\|^from server_lib" brain.py` is empty). No circular imports. → extraction is low-risk by construction *as long as new modules never import back from brain.py* (use `engine/context.py` for shared state instead).

**Entanglement seams** (the hard part): `_thread_local` (spans execution/scheduler/tasks/dispatch/prompt-build), `TOOL_DEFINITIONS/GROUPS/DISPATCH` registry, provider-routing locks, 8× `_*_db_pool`.

### brain.py extraction tiers
- **Tier A — high value, low risk (self-contained):** A1 workflow engine (~1,500 LOC → `engine/workflow.py`, the cleanest first win), A2 code-structure graph (~1,200 → `engine/code_graph.py`), A3 git/github tools (~390, at brain.py:18978/19176 → `engine/tools/git_tools.py`), A4 gmail tools (~400, at brain.py:4916+ → `engine/tools/gmail_tools.py`), A5 trace/audit (~400).
- **Tier B — high value, medium risk:** **B1 extract `_thread_local` → `engine/context.py` FIRST** (prerequisite for B2–B4), B2 scheduler (~1,640), B3 PII scanner (`_pii_rules` etc. ~900 → merge into `engine/pii_ner.py`; also fixes the web/index.html sync drift via codegen), B4 quotas/cost/rate-limit.
- **Tier C — high value, high risk (eval-gated):** C1 model-select + `_build_system_prompt` (~2,600; **must pass eval harness + warmup KV-prefix byte-stability check**), C2 tool-exec layer (~2,000), C3 MemPalace glue (~1,000; **must pass project-wing isolation test**).
- **Tier D — finish half-done extractions:** D1 doc_convert (**likely already complete per v9.10.0 — verify, don't assume**), D2 classification glue, D3 kg_extract entity-indexing, D4 pii_ner (folded into B3).

### Other oversized files
- `handlers/admin.py` (5,416) → `handlers/admin/` package (workflows ~900 *first*, artifacts ~3,200, costs ~490, skills ~300, config ~290, teams ~200, agents ~180, observability ~100).
- `server.py` (5,815) → pull out `server_daemons.py` (~1,500: 7 background loops) + optional `server_init.py` (~600). Keep HTTP dispatch + Session classes.
- `handlers/chat.py` (3,540) → `server_lib/sse_stream.py` (~150) + `handlers/gdpr_recovery.py` (~80).
- `server_lib/db.py` (1,962) → `node_registry.py` (~80) + `mempalace_sync.py` (~100); keep `ChatDB`.

### Cross-cutting reusable utilities (new shared modules)
- U1 path-traversal guard (5 divergent copies — **security risk**) → `server_lib/pathsafe.py:SafePathValidator`.
- U2 HTTP body read (16 sites) → `server_lib/http_util.py:read_request_body`.
- U3 SSE formatter → folded into `server_lib/sse_stream.py`.
- U4 repo-root path constant (~82 occurrences, cosmetic).
- U5 PII web/server sync → after B3, **codegen the JS rule table from the Python source** so they can't drift.
- **Do NOT re-extract** (already centralized): `_send_json`/`_read_json`, auth gates, `resolve_provider_for_model`, `@_db_safe`/`_db_conn`, TOOL_DEFINITIONS dedup. `handlers/favourites.py` vs `server_lib/favourites.py` is a legit HTTP/DB layer split.

---

## Recommended sequencing (phase-gated)

- **Phase 0 — safety net.** ✅ DONE.
- **Phase 1 — audit + pay down debt + pure wins.** First *audit last time's incompleteness*: run `./refactor_gate.sh grep <symbol>` for the Tier-D symbols to find surviving duplicate copies in brain.py and finish those. Then Tier A pure wins (A1 workflow first) + `admin/workflows.py` split + `db.py` node-registry/mempalace-sync split. *Gate after phase: full `./refactor_gate.sh` + grep proofs shown to user.*
- **Phase 2 — shared seam.** B1 (`engine/context.py`) + U1/U2/U4 utilities. *Gate: gate script + grep that no module reads `brain._thread_local` directly.*
- **Phase 3 — mid-risk domains.** B2 scheduler, B3 PII (+U5 codegen), B4 quotas; full `admin/` split; `server_daemons.py`; chat.py SSE/GDPR split. *Gate: gate script + scheduled-task run + PII-block test.*
- **Phase 4 — core path (eval-gated).** C1, C2, C3; complete D1–D3. *Gate: full eval harness within noise (Δ < 0.10 of baseline) + warmup KV-prefix byte-identical check for C1.*

---

## Invariants any extraction MUST preserve

1. **One-way DAG** — extracted modules must not import back from `brain.py` (cycle). Shared state → `engine/context.py`.
2. **Handler mixins resolve names from server.py globals** — re-export moved symbols so the mixin chain keeps resolving.
3. **System prompt stays user-agnostic & byte-stable** (warmup KV prefix). Gate C1 on byte-identical warmup payload.
4. **Project memory isolation** (`project__*` wings never leak) — gate C3 on wing-visibility test.
5. **Thread-locals set before every background call** — daemons moved to `server_daemons.py` must still set `engine._thread_local.*` before calling the sidecar.
6. **The 4-edit-site rule for tools** (TOOL_DEFINITIONS / TOOL_GROUPS / `tool_*` fn / TOOL_DISPATCH). When extracting git/gmail tools the *function* moves but *registration* stays in brain.py — Gate 3 must confirm all four sites still point correctly. (This is exactly the v8.27.0 image_gen registration bug.)
7. **Fail loud** (CLAUDE.md Rule 12) — no silent skips; a broken re-export must surface.

---

## How to resume tomorrow (concrete first steps)

```bash
cd /Users/alexander/Documents/dev/cctest

# 1. Confirm the baseline still holds (should print GATE PASS ✓)
./refactor_gate.sh

# 2. (decision) commit the planning artifacts
git add REFACTOR_PLAN.md refactor_gate.sh REFACTOR_HANDOVER.md
git commit  # per project rule: directly to main

# 3. Start Phase 1 audit — sweep Tier-D symbols for surviving duplicate copies.
#    Eyeball that any hits are ONLY the changelog string / comments:
./refactor_gate.sh grep "convert_one"
./refactor_gate.sh grep "_extract_pdf"
./refactor_gate.sh grep "_classification_gate_tool_text"
#    ...then move incomplete glue into the matching engine/ module, gate, commit.

# 4. Then Tier A first pure win — workflow engine extraction:
./refactor_gate.sh grep "_wf_parse"   # baseline: defn + 4 call sites in brain.py
#    move workflow lexer/parser/interpreter -> engine/workflow.py,
#    re-export from brain.py, then:
./refactor_gate.sh                     # gates 4+5 must pass
./refactor_gate.sh grep "_wf_parse"    # gate 2: brain.py now shows ONLY changelog
```

**Interpreter for any manual check:** `/opt/homebrew/bin/python3` (NOT bare `python3` if PATH differs).
**Test runner:** `python3 -m unittest discover -s tests -p "test_*.py"` (no pytest).
**Eval harness** (Phase 4 gate): `eval/` dir — `judge_mistral.py`, `harness/`, `questions.json`.

---

## Reference: key files & line markers (verified this session)
- `brain.py:8-110` — changelog string (ignore in greps).
- `brain.py:4916+` — gmail tools (`tool_gmail_inbox/read/search/send/reply`).
- `brain.py:13267` — `_wf_parse` (workflow parser); workflow engine cluster ~lines 12500–14000.
- `brain.py:18978` — `tool_git_command`; `brain.py:19176` — `tool_github_command`.
- Per-dir docs exist: `engine/CLAUDE.md`, `handlers/CLAUDE.md` (no `server_lib/CLAUDE.md`).
- Tests: 7 files in `tests/` (`test_pseudonymizer*.py`, `test_pii_ner.py`, `test_gdpr_*.py`, `test_chat_worker_helpers.py`, `test_mcp_server.py`).
