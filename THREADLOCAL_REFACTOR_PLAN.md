# Thread-Local Refactor Plan — request-context → typed RequestContext + contextvars (Tier G)

Companion to the completed Python module-extraction refactor (`REFACTOR_REPORT.md`) and the
web/js refactor (`JS_REFACTOR_PLAN.md`). Same discipline: **analyze → goal → gate → migrate-with-
gate-between → living report**. This file is the **plan**; progress is tracked in
`THREADLOCAL_REFACTOR_REPORT.md` (the living "what's done" record). HTML report + auto-verification
at the end, exactly like the prior tiers.

This is the "thread-local after JS" tier the JS plan deferred (`JS_REFACTOR_PLAN.md` §"User
decisions" 3–4: *"Defer thread-local (separate later tier)… JS first, thread-local after."*).

---

## ▶ HOW TO START / RESUME IN A FRESH SESSION (read this first)

**Disk is the memory; the conversation is disposable.** To pick this up in any new session:
1. Read this file (`THREADLOCAL_REFACTOR_PLAN.md`) top-to-bottom — full scope, gate, phases.
2. Read `THREADLOCAL_REFACTOR_REPORT.md` — the **source of truth for what's done**. Its attribute
   map shows every attribute's migration status (⬜ planned / 🔄 in progress / ✅ done). Resume at the
   first non-done phase.
3. Run `./refactor_gate.sh` to confirm the gate is green before changing anything.

**Trigger to type in a fresh session:**
- Cold start: `Start Tier G — read THREADLOCAL_REFACTOR_PLAN.md and begin.`
- Resume mid-way: `Continue Tier G — check THREADLOCAL_REFACTOR_REPORT.md for where we are and proceed.`

**RESUME POINT (update as phases complete):**
> Phase 0 (gate) status — see `THREADLOCAL_REFACTOR_REPORT.md` Status board. If that file doesn't
> exist yet, Phase 0 hasn't started — begin there.

**Recommended goal wording** (paste as the session goal so the run is checkable + bounded):
> Execute Tier G per THREADLOCAL_REFACTOR_PLAN.md. Success = every request-context attribute migrated
> off raw `_thread_local` onto the typed RequestContext (contextvars-backed) accessed through one
> module; all enter/exit sites use the single context-manager (no scattered `=None` teardown); the
> new concurrency/bleed test passes; `./refactor_gate.sh` green after every step (no new unittest
> failures beyond the 3 known spaCy ones); THREADLOCAL_REFACTOR_REPORT.md + HTML published each step;
> Phase 5 source-validation passes. Run Phase 0→4 unattended, committing per green step; HARD STOP
> before declaring the tier complete so I can review. Stop immediately on any gate failure or a
> decision the plan doesn't already answer.

---

**User decisions (2026-05-23):**
1. **End-state = typed `RequestContext` dataclass in a `contextvars.ContextVar`**, entered/exited via
   ONE context-manager (token reset = automatic teardown). Fixes bleed-risk structurally + async-safe.
2. **Build a real concurrency/bleed gate FIRST** (before any migration) — there is ZERO coverage of
   the no-request-state-bleed property today. Wire it into the existing `refactor_gate.sh`.
3. **Scope = request-context only.** Leave the 6 DB-connection `threading.local()` pools alone
   (`_db_pool`, `_cost_db_pool`, `_auth_db_pool`, `_sched_db_pool`, `_context_db_pool`,
   `_code_graph_db_pool`) — they're a correct, separate use of `threading.local()`.

---

## The problem

`engine/context.py:23` defines `_thread_local = threading.local()`. It carries **~33 ad-hoc
request-scoped attributes** (`current_agent`, `current_user_id`, `current_session_id`, `project`,
`mcp_manager`, `memory_store`, `_current_model`, `research_mode_override`, `caveman_*`,
`event_callback`, `_gdpr_*`, `trace_id`, `tool_*`, `note_context`, `plan_mode`, `delegate_*`,
`workflow_*`, …) accessed via **~140 references across 18 files**. CLAUDE.md §"Concurrency & Thread
Safety" makes the invariant explicit: *"Thread-locals required for every request/background thread …
Never fall back to globals — concurrent requests bleed."*

Three structural fragilities:
1. **Untyped, no source of truth.** Attributes are read with `getattr(_thread_local, 'x', default)`
   sprinkled across the codebase (≥20 distinct `getattr` call shapes). Nothing enumerates the legal
   attribute set; a typo'd attribute silently reads its default forever.
2. **Manual teardown = bleed-risk.** Cleanup is hand-written `_thread_local.x = None` in `finally`
   blocks (≥10 sites just in `handlers/chat.py`). Miss one attribute on one path → it bleeds into the
   next request reusing that worker thread. This is the exact failure mode CLAUDE.md warns about, with
   no test guarding it.
3. **Three import spellings** (`engine._thread_local` ×93, `brain._thread_local` ×6,
   `from engine.context import _thread_local` ×3) plus a real historical import-bug (the
   `engine/doc_convert.py` OCR cost-tracking case that silently defaulted `agent_id='main'`). Any
   migration MUST preserve every alias so all spellings keep resolving.

Non-chat-worker setters also exist (scheduler, `handlers/translate.py`, the classification daemon in
`server_daemons.py`) — they set request-context attrs too and must adopt the same context-manager.

## The goal (success criteria — loop until met)

- A single typed **`RequestContext`** (dataclass, all ~33 fields enumerated with types + defaults)
  stored in a **`contextvars.ContextVar`**, defined in one module (extend `engine/context.py` — the
  current home of `_thread_local` — or a new `engine/reqctx.py`; pick one, state which).
- **One context-manager** (`request_context(**overrides)` / `bind_request_context(...)`) is the ONLY
  way request state is entered + torn down. Entering pushes a context; exiting resets the
  ContextVar token — teardown is automatic and total (no per-attribute `=None`). Nested binds
  (delegate / sub-call) stack and pop correctly.
- **Every read/write goes through typed accessors** on that module (or the context object) — zero raw
  `getattr(_thread_local, …)` / `_thread_local.x = …` left in live code (grep-gate enforces).
- **Backward-compat shim:** `_thread_local` keeps resolving from `engine`, `brain`, and
  `engine.context` for the duration (so the migration is incremental + every alias works), backed by
  the new ContextVar so reads/writes through the old name and the new accessors see the SAME state.
  The shim is removed only when the last raw reference is gone (Phase 4) — state which sites still use
  it at each step.
- **Zero behavior change** — verified by `refactor_gate.sh` (existing unittests + imports) after every
  step, PLUS the new concurrency/bleed test.
- **No request-state bleed** — the new test runs overlapping requests on a shared worker pool and
  asserts each sees only its own context. This property has no coverage today; it is the headline
  acceptance criterion.
- Living `THREADLOCAL_REFACTOR_REPORT.md`, one commit per migration step, gate green between each,
  HTML republished each step.

## Non-goals (explicit scope fence)

- NO touching the 6 DB-connection `threading.local()` pools (separate, correct pattern).
- NO behavior/feature changes; NO new request-context attributes; NO renaming attributes for taste
  (a field keeps its name unless a rename is forced by the dataclass — if so, alias it).
- NO async/await rewrite of the request handlers — `contextvars` is chosen precisely because it works
  for BOTH the current threaded model and any future async without rewriting call sites.
- NOT migrating the DB pools, warmup KV state, or the session-scoped tool-dedup state
  (`brain.py:11204` — already moved off threading.local by a prior tier).

---

## Phase 0 — Build the GATE (DO THIS FIRST, before any migration)

The Python analog already exists: `refactor_gate.sh` (unittest baseline 80 pass / 3 known spaCy
fails + 16/16 import check + grep-gate). Phase 0 EXTENDS it with the missing piece — a concurrency
bleed test — and adds a Tier-G grep-gate.

### Gate component 1 — concurrency / no-bleed test (`tests/test_request_context_isolation.py`)
- The property with zero coverage today. Spin up N worker threads sharing a pool; each enters
  `request_context(current_user_id=Ui, current_session_id=Si, current_agent=Ai, project=Pi, …)`,
  does interleaved work (sleep/yield to force overlap), and asserts at every checkpoint that it reads
  back **exactly its own** values — never another thread's. Include a teardown assertion: after the
  context-manager exits, the ContextVar is back to its prior value (default at top level), proving no
  residue leaks to the next task on that thread.
- Add a **negative control** that would FAIL on the old raw-`_thread_local` design if teardown were
  skipped (documents what the test actually catches — mirrors the JS smoke's console-error backstop).
- Must run in the bare `/opt/homebrew/bin/python3` test process (no server, no spaCy) so it's part of
  the fast gate.

### Gate component 2 — Tier-G grep-gate (raw access must be gone, per migrated attribute)
- A `refactor_gate.sh tlgrep <attr>` mode (parallel to the existing `grep <SYMBOL>` mode): proves a
  migrated attribute has **no remaining raw `_thread_local.<attr>` read/write** in live code (the
  shim definition + the accessor module are the only allowed mentions). A surviving raw access =
  NOT DONE for that attribute → finish or revert. This is the Tier-G analog of the existing "original
  definition must be gone from brain.py" gate-2.

### Gate component 3 — extend `refactor_gate.sh`
```
1. unittest suite          # baseline 80 pass / 3 known spaCy fails — NO new failures
2. import check            # 16/16 core+handler modules import clean
3. test_request_context_isolation.py  # NEW — the no-bleed property (component 1)
4. tlgrep <attr> (per step)            # NEW — raw access for a migrated attr is gone (component 2)
```
Prints `GATE PASS ✓` / `GATE FAIL ✗` (reuse the existing script's vocabulary). Components 1–3 run on
every invocation; component 4 is the per-attribute proof run during migration steps.

**Phase 0 exit:** the concurrency test is written and **passes against the CURRENT raw-`_thread_local`
design** (this proves the test is correct + establishes the green baseline before any change), the
grep-gate mode works, `refactor_gate.sh` is green, committed.

> The test must pass on TODAY's code first. If it can't (because today's manual teardown actually does
> leak somewhere), that's a real pre-existing bug — surface it, document it as a known-finding baseline
> (like the JS `.eslint-baseline.txt`), and make the gate fail only on NEW leaks. Don't silently "fix"
> it as part of building the gate.

---

## Phase 1 — Introduce RequestContext + ContextVar + the shim (no call-site changes yet)

Smallest-blast-radius first: stand up the new machinery WITHOUT migrating any call site, so the gate
proves the shim is behavior-identical before anything moves.
- Define `RequestContext` dataclass (all ~33 fields, typed, defaulted) + `_request_ctx: ContextVar` +
  `request_context(**overrides)` context-manager + typed get/set accessors, in the chosen module.
- Make `_thread_local` a **compatibility shim object** whose `__getattr__`/`__setattr__` proxy to the
  ContextVar-backed RequestContext (so the 93 `engine._thread_local.x` reads + the `brain.` /
  `engine.context.` aliases all hit the new storage). Preserve all three import spellings + the
  re-exports.
- Gate: full `refactor_gate.sh` green INCLUDING the new concurrency test — the shim must pass the
  same no-bleed property. One commit.

## Phase 2 — Migrate the enter/exit sites to the context-manager (kills scattered teardown)
The bleed-risk fix. Replace the hand-written set-then-`finally: =None` blocks with `with
request_context(...)`. Order by blast radius (smallest first):
1. **Non-chat-worker setters** (fewest attrs): `handlers/translate.py`, the classification daemon in
   `server_daemons.py`, `engine/scheduler.py`. Each becomes a `with request_context(...)`.
2. **Background calls** routed through `sidecar_proxy.background_call` / `tool_mcp.handle_tools_call`
   (reconstitutes context from the sidecar payload — CLAUDE.md §"Dispatch path" step 3).
3. **The chat worker** (`handlers/chat.py` — the biggest, ~43 refs + ~10 teardown sites): wrap the
   turn in one `with request_context(...)`; delete the `finally: _thread_local.x = None` block once
   the context-manager owns teardown. This is the highest-value, highest-risk step — gate hard
   (unittests + concurrency test + a real server-up smoke turn).
Gate after each site. Each = one commit.

## Phase 3 — Migrate the reads to typed accessors (kill raw getattr)
Replace every `getattr(_thread_local, 'x', default)` / `_thread_local.x` READ with the typed accessor
(`reqctx.current_agent()` or `ctx.current_agent`). Do it file-by-file (18 files), gate between, each a
commit. After each file, run `refactor_gate.sh tlgrep <attr>` for the attrs that file owned to prove
the raw access is gone there.

## Phase 4 — Remove the shim
When the grep-gate shows ZERO raw `_thread_local.<attr>` reads/writes remain in live code, delete the
`_thread_local` compatibility shim (or reduce it to a thin deprecated alias if any external/Telegram
path still needs it — state which). Final `tlgrep` over ALL attrs must be clean. One commit.

## Phase 5 — Final source-validation (REQUIRED close-out, like the JS/Python audits)

Mirrors the post-Tier-C/E/F audits: **verify the report against actual source — don't trust the
report.** Do NOT skip; this is the acceptance gate for the whole tier.
1. **Every claimed-migrated attribute** is on the `RequestContext` dataclass with a type + default
   (grep the dataclass; cross-check against the Phase-0 attribute inventory — none dropped/renamed
   silently).
2. **Zero raw `_thread_local.<attr>` access** in live code for every attribute (`tlgrep` over the full
   attribute list) — the shim/accessor module is the only allowed mention (or shim is deleted).
3. **All teardown is context-manager-owned** — independently grep for surviving `_thread_local.* =
   None` / `del _thread_local` in live code; there should be none (the context-manager's token-reset
   replaced them). Any survivor = a missed enter/exit site → fix or revert.
4. **All three import spellings still resolve** (or are intentionally gone) — `import engine; engine._thread_local`,
   `import brain; brain._thread_local`, `from engine.context import _thread_local` (or RequestContext
   accessors). No dangling import. The historical `engine/doc_convert.py` OCR-cost path must still
   resolve (don't reintroduce that silent-default bug).
5. **The concurrency/bleed test passes** + a teardown-residue assertion is present and meaningful
   (would fail if a context leaked).
6. **Full `refactor_gate.sh` green** (no new unittest failures beyond the 3 known spaCy ones; 16/16
   imports) + a real server-up smoke turn (one interactive chat turn + one scheduled/background call)
   confirming the live request path still threads context correctly.
7. **Coverage-promise check:** every one of the ~33 attributes from the Phase-0 inventory appears in
   the report's attribute map with a final status (✅ migrated / 🚫 intentionally-left-with-reason) —
   none silently omitted.
8. Mark any inconsistency in `THREADLOCAL_REFACTOR_REPORT.md` and fix (or revert) before declaring done.

---

## The safe-migration rule (correctness contract)
1. **The shim and the new accessors back the SAME ContextVar** — there is never a moment where reads
   through the old name and the new accessors see different state. (Phase 1 establishes this; the gate
   proves it via the concurrency test.)
2. **Teardown is total + automatic** — a migrated enter/exit site uses the context-manager; the
   ContextVar token-reset clears EVERY field at once. No per-attribute teardown survives (Phase 5 §3).
3. **Every import spelling preserved** until the shim is removed; the shim removal is its own gated step.
4. **Background + delegate sub-calls bind a NESTED context** that pops correctly (the scheduler /
   sidecar-reconstituted / delegate paths must not bleed into or out of the parent).
5. **Fail loud** — gate red blocks the commit; a missed teardown site must surface via the concurrency
   test or the residue grep, not silently no-op.

## The attribute inventory — COMPLETE scope, fixed up front (NOT grown)

`THREADLOCAL_REFACTOR_REPORT.md` MUST open with a table covering **all request-context attributes from
day one** — exactly like the JS report's Master file-map and the Python report's domain map. Every
attribute is listed with its current ref count, owning subsystem, and migration status. The run only
**flips statuses** (⬜→🔄→✅); it never appends a "newly discovered" attribute. A genuinely new
attribute = a scope change to flag to the user, not a silent add. The Phase-0 inventory (from
`grep -rhoE '_thread_local\.[a-z_]+'`) is the fixed scope: ~33 attributes including `current_agent`,
`current_user_id`, `current_session_id`, `memory_store`, `_current_model`, `project`, `mcp_manager`,
`current_team_ids`, `session_id`, `research_mode_override`, `event_callback`, `_gdpr_mapping_id`,
`_gdpr_anonymising`, `_gdpr_after_file_write_cb`, `trace_id`, `tool_use_id`, `tool_round`, `plan_mode`,
`note_context`, `in_worker_subagent`, `execution_overrides`, `delegate_agent_id`, `current_worker_id`,
`caveman_system`, `caveman_chat`, `attachment_image_model`, `attachments`, `workflow_run_id`,
`workflow_execution_id`, `workflow_default_model`, `workflow_agent_id`, `_intent_action_recovery_count`,
`_guided_tasks_for_msg`, `_discovered_tools`. **The report's Phase-0 step records the exact final list
+ ref counts** (this plan's list is indicative; the inventory is authoritative once measured).

## Execution protocol (same as the JS + Python refactors)
- Subagent-per-migration-step (bulky reads die with it); main thread holds only pass/fail + what moved.
- One step = one commit, directly to main. Gate green before commit. `THREADLOCAL_REFACTOR_REPORT.md`
  updated in the same commit: **flip the status of the affected attribute/site rows** + add a per-step
  record block. The inventory is the COMPLETE scope written on the FIRST commit; the run only flips
  statuses — it NEVER appends a row. A genuinely new attribute = surface to the user as a scope change.
- **Publish the HTML report on every update**: `./refactor_publish.sh "<msg>" THREADLOCAL_REFACTOR_REPORT.md`
  (the publish script + converter were parameterized 2026-05-23 to take a report filename; they
  regenerate `THREADLOCAL_REFACTOR_REPORT.html` and push). **HTML is a generated VIEW — never
  hand-edited; only the `.md` is source of truth.**
- The server is launchd-managed; a static-reload isn't enough here (Python daemon) — restart the
  server (`launcher.py restart` or the daemon) before the Phase-2/Phase-5 server-up smoke.
- Stop + report on any gate failure or genuine ambiguity the plan doesn't answer.
- **Phase 5 (final source-validation) is mandatory before declaring the tier complete.**

## Open questions to settle at Phase 0 (during the build, not now)
- **Module home:** extend `engine/context.py` (current `_thread_local` home) vs a new `engine/reqctx.py`.
  Decide by import-cycle safety (the accessors are imported very widely — must not create a cycle with
  `brain`). Record the call.
- **Server-up smoke harness:** the existing tiers used a real chat turn + a scheduled run. Confirm the
  same two-path smoke (interactive + background) is the right runtime backstop here, since the bug class
  (context bleed) only manifests under concurrency — the unittest covers the property; the server smoke
  covers the wiring.
