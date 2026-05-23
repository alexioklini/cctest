# JS Refactor Report — web/js/ (Tier F) — living record

**Source of truth for what's done.** The plan is `JS_REFACTOR_PLAN.md`; this file tracks
progress. Mirrors the Python `REFACTOR_REPORT.md` discipline: a fixed Master file-map written
on day one (all 17 rows present, statuses planned), and the run only *flips statuses* — it never
appends a "newly discovered" row. A genuinely new domain = a scope change to surface, not a silent add.

**HTML (`JS_REFACTOR_REPORT.html`) is a generated VIEW** — never hand-edited. Regenerated +
pushed on every update via `./refactor_publish.sh "<msg>" JS_REFACTOR_REPORT.md`.

---

## Status board

| Phase | What | Status |
|---|---|---|
| 0 | Build the GATE (ESLint + net-globals + Playwright smoke), green on unchanged code | ✅ DONE |
| 1 | Cross-cutting de-dup — **verified and REJECTED** (all candidates net-negative; see below) | ✅ DONE (no-op + recorded) |
| 2 | Split `settings.js` (6,140 → **7 files**, all <2k) | ✅ DONE |
| 3 | Split `panels.js` (5,819 → **6 files**, all <2k) | ✅ DONE |
| 4 | Split `chat.js` (4,013 → 4 files) + REVIEW init.js (→3) / translation.js (→2) | ✅ DONE |
| 5 | Final source-validation (mandatory close-out) | ✅ DONE — all 8 checks pass |

**RESUME POINT:** Phases 0–5 complete. Gate green, all files <2k, source-validation passed.
**Tier F is ready for user review (HARD STOP).** Awaiting acceptance before declaring complete.

### Phase 1 decision — de-dup verified and REJECTED (user-approved 2026-05-23)

The plan said "verify counts at extraction time." Verification (subagent analysis) falsified the
premise that the de-dup candidates are wins. All four were rejected as net-negative churn or
behavior-change risk — which would violate the tier's zero-behavior-change invariant and the
plan's own "don't make competing copies" warning (the Python U2 lesson). **User approved skipping.**

| Candidate | Finding | Decision |
|---|---|---|
| **F-U1 modal factory** | 22 raw `modal-overlay` blocks: only **6 SIMPLE** (search/file-preview/provider-keys/key-edit/stats/password), **16 RICH** (tabs, vertical sidebars, dynamic innerHTML, sized classes). `utils.js` already has `showDialog({title,message,buttons,input,danger})`. A new `createModal()` would be a competing 2nd modal system; only 6/22 adoptable. | **SKIP** — would add a competing copy. |
| **F-U2 DOM helpers** | `.closest('.modal-overlay').remove()` ×18 (+7 sched variant), `classList.toggle(x,cond)` ×42. Each is a self-documenting one-liner; wrapping adds indirection, zero size gain. | **SKIP** — churn, zero gain. |
| **F-U3 date formatters** | `humanAgo`(ISO→"3h"), `timeAgo`(unix→"3h ago"), `_fmtTs`(ISO→abs ts), `_fmtDuration`(ms→elapsed) are 4 genuinely-distinct semantics, not duplicates. | **SKIP** — not dupes. |
| **fetch → API.*** | translation.js's 17 fetches are SSE-streaming job polls / FormData uploads / blob downloads-with-Bearer / live-mic chunk POSTs — special cases, not clean `API.post()` swaps; rerouting carries real behavior-change risk on the streaming/upload paths. | **SKIP** — not safe swaps. |

**`dom_helpers.js`** (Master-map row): deferred — created only if a genuinely-shared helper emerges
*during* the splits (Phases 2–4). If none emerges, its row is marked "not created — no shared
helper materialised." Phase 1 thus produces **no code change** (a verified no-op); the gate stays
green trivially.

---

## The gate (Phase 0) — what was built

Lives in `web/js/`. Run `cd web/js && ./js_gate.sh`. Node 22 / npm 10.

| Component | File | What it catches |
|---|---|---|
| 1. Globals generator | `gen-globals.sh` | Harvests every column-0 `function`/`async function`/`const`/`let`/`var`/`class NAME` **and** `window.NAME =` across the browser source `*.js` → `.globals.json`. Auto-generated, never hand-maintained. |
| 2. ESLint 9 flat config | `eslint.config.js` | `no-undef` (a global called but defined nowhere — the "moved fn lost its global" signal), `no-redeclare` (`builtinGlobals:false`, so two *source* defs of one name fire but a name's own definition doesn't), `no-unused-vars` (warn). `sourceType: "script"`. |
| 3. Net-globals invariant | `.globals-count.baseline` | A split must RELOCATE globals, never add/drop. Gate diffs the count vs baseline; mismatch fails unless the baseline is updated in the same commit. |
| 4. Playwright smoke | `smoke.spec.js` + `playwright.config.js` | Boots vs the live server, logs in admin/admin, exercises the core flows the 3 monsters own (login→welcome, composer type-without-send, General Settings modal + fetch-triggering tab, right-panel toggle, projects/chats nav). **Zero-console-error assertion is the runtime backstop** for anything static analysis misses. All flows READ-ONLY (no data created). Skips with a loud notice if the server is down. |

**Driver:** `js_gate.sh` → prints `JS GATE PASS ✓` / `JS GATE FAIL ✗`. Components 1–3 run fast
without the server; component 4 requires the dev server at `127.0.0.1:8420`. A server-up run is
REQUIRED before declaring any step done.

**devDeps only** (`package.json`): `eslint`, `@eslint/js`, `@playwright/test`. `node_modules/`
gitignored. No runtime dependency — `web/js` still ships as global `<script>` tags.

### Documented pre-existing baseline (`.eslint-baseline.txt`)

Three `no-undef` findings exist in **unchanged** code at Tier-F start. Out of refactor scope
(Rule 3 — don't fix adjacent bugs); the gate fails on any finding **not** in this list, so they
can't mask a regression. The list may shrink, never grow.

| File | Name | Why it's pre-existing |
|---|---|---|
| `chat.js` | `spinnerBar` | `const` declared in the `compacting` SSE callback, referenced in the separate `empty_round_nudge` callback (latent block-scope bug). |
| `sessions.js` | `autoGrow` | Optional, `try/catch`-guarded call to a function defined nowhere in web/js. |
| `workflows.js` | `loadSessions` | Optional, `typeof X === 'function'`-guarded. |

**Phase-0 baseline established:** net-globals = **879**.

---

## Master file-map — COMPLETE 17-file scope (fixed up front, statuses only flip)

⬜ planned · 🔄 in progress · ✅ done · 🚫 stays (with reason) · ➕ dedup-target/created

| File | LOC (start) | Disposition | Status | Target / reason |
|---|---|---|---|---|
| `settings.js` | 6,140 | SPLIT (Phase 2) | ✅ | DELETED → settings_teams (55) / settings_general (589) / settings_general_tabs (1,988, NEW — switchGeneralTab tab-bodies) / settings_tools (771) / settings_agent (1,539) / settings_hooks (110) / settings_schedule (1,109). All <2k. |
| `panels.js` | 5,819 | SPLIT (Phase 3) | ✅ | DELETED → panels_gdpr (818) / panels_chats (376) / panels_projects (1,324) / panels_project_sync (1,010, sub-split from projects) / panels_right (846) / panels_artifacts (1,452). All <2k. Trailing top-level `click` listener (deferred) kept with sync group. |
| `chat.js` | 4,013 | SPLIT (Phase 4) | ✅ | DELETED → chat_render (1,383) / chat_nav (266) / chat_tools (680) / chat_send (1,615). All <2k. |
| `init.js` | 2,899 | REVIEW → **SPLIT** | ✅ | **Decision: SPLIT.** Monitor-only split left it at ~2,187 (>2k), so also lifted the cohesive admin-user/settings domain. Reduced to **1,209** (bootstrap/auth/composer/toggles stay — load-order-sensitive). → user_admin (949) + monitors (745). |
| `translation.js` | 2,389 | REVIEW → **SPLIT** | ✅ | **Decision: SPLIT.** The live-mic domain (recording/VAD/WAV/live-SSE) is operationally independent (record-on-demand, only shares trState/trAuthHeaders). Lifted to translation_live (624); translation.js → **1,766** (text/glossary/document/media + history stay). |
| `workflows.js` | 1,897 | stays | 🚫 | Under 2k, single cohesive domain. De-dup only (Phase 1 helpers). |
| `nav.js` | 1,351 | stays | 🚫 | Cohesive navigation domain, under 2k. |
| `utils.js` | 1,154 | DEDUP TARGET | 🚫 | Phase 1 de-dup verified+rejected — no helpers extracted (existing `showDialog`/`esc`/etc. already centralised). Stays as-is. |
| `favourites.js` | 848 | stays | 🚫 | Cohesive, under 2k. |
| `files.js` | 604 | stays | 🚫 | Cohesive, under 2k. |
| `sessions.js` | 529 | stays | 🚫 | Cohesive, under 2k. |
| `classification.js` | 374 | stays | 🚫 | Cohesive, under 2k. |
| `api.js` | 371 | DEDUP TARGET | 🚫 | Stray-`fetch` audit done: the non-`API.*` fetches (translation.js SSE/upload/blob, auth/warmup) are special cases, not safe swaps. No change. |
| `buddy.js` | 351 | stays | 🚫 | Cohesive, under 2k. |
| `share.js` | 305 | stays | 🚫 | Cohesive, under 2k. |
| `state.js` | 94 | stays | 🚫 | Pure data, loads 2nd. Never split. |
| `search.js` | 55 | stays | 🚫 | Tiny. |
| `dom_helpers.js` (NEW) | 0 | DEFERRED → NOT CREATED | 🚫 | No genuinely-shared helper emerged during the splits (F-U1/F-U2 rejected). Not created. |

**New files created by the splits (all <2k):** `settings_general_tabs.js` (1,988), `panels_project_sync.js`
(1,010), `panels_artifacts.js` (1,452), `user_admin.js` (949), `monitors.js` (745), `translation_live.js`
(624) — plus the per-domain settings_*/panels_*/chat_* files listed in their parent rows above.

**Total Tier F scope = 29,193 LOC across 17 files.** Coverage promise: every file above appears
with a disposition + size — nothing silently out of scope.

---

## Per-step record

### Phase 0 — Gate built (commit pending)
- Created `gen-globals.sh`, `eslint.config.js`, `package.json`, `playwright.config.js`,
  `smoke.spec.js`, `js_gate.sh`, `.gitignore` under `web/js/`.
- Resolved the ESLint redeclare-vs-undef tension with `no-redeclare: {builtinGlobals:false}`:
  a name's own source definition is fine, but two definitions of one name still fire — the exact
  duplicate-definition signal a copy-instead-of-move split would trip.
- Excluded the gate's own Node tooling from globals-harvest + lint (it polluted the count by 4).
- Established baselines: net-globals **879**, eslint baseline = 3 documented pre-existing findings.
- **Gate green on unchanged code, all 4 components** (ESLint clean, net-globals 879, 5/5 smoke
  flows pass, zero console errors). Phase-0 exit criterion met.
- Smoke decision confirmed: all flows READ-ONLY (type-without-send, open-and-close modals) so the
  live admin/admin run creates no data.

### Phase 2 — `settings.js` split (6,140 → 7 files, all <2k)
- Cut-paste relocation of all 130 top-level defs into 6 cohesive files (teams / general / tools /
  agent / hooks / schedule), then `settings.js` DELETED. Zero reassignments vs the partition plan.
- **`switchGeneralTab` decomposition**: the ~2,000-LOC tab dispatcher kept `settings_general.js` at
  2,565 LOC (over the 2k limit). Per the plan ("split by tab via a dispatch table"), decomposed it:
  the 7 shared string-builder helpers (`P/G/ROW/DOT/MONO/BADGE/SEC`) hoisted to module scope; each
  `if (tab==='X')` body extracted to `async _genTab_<X>(C)` in a NEW `settings_general_tabs.js`
  (1,988 LOC); `switchGeneralTab` is now a 12-line `RENDERERS[tab]` dispatch. Nested wings/tunnels
  sub-tab logic stays inside `_genTab_mempalace`. Dead `tools-legacy-NEVER-MATCHES` block kept as a
  defined-but-unreferenced `_genTab_tools_legacy` (preserves its never-matched behavior, loses no global).
- **Net-globals 879 → 901** (+22 = 7 hoisted helpers + 15 `_genTab_*`). Sanctioned single bump:
  real new globals from decomposing one mega-function, none dropped. Baseline updated same commit.
- Verified: no duplicate fn name across the 7 files; load order in index.html places
  settings_general.js before settings_general_tabs.js (helpers defined first). **Gate green, smoke 5/5.**

### Phase 3 — `panels.js` split (5,819 → 6 files, all <2k)
- Cut-paste relocation of all 177 top-level defs into 6 cohesive files; panels.js DELETED.
  Plan named 4 targets; `projects` and `right` clusters each exceeded 2k, so sub-split per the
  plan's "may sub-split" allowance: projects → `panels_projects` (1,324) + `panels_project_sync`
  (1,010); right → `panels_right` (846) + `panels_artifacts` (1,452).
- Net-globals **901 unchanged** (pure relocation, no baseline bump).
- The one piece of top-level executable code (a `document.addEventListener('click', …)` sync-history
  expand handler — not a named def) went to `panels_project_sync.js`. Verified safe under the
  load-order contract: it only *registers* a listener (deferred), reads no other-file globals at load.
- Subagent hit + recovered from a block-comment-boundary corruption (smoke fail surfaced it →
  git restore → redo with block-comment tracking; final files pass `node --check` + full gate).
- Verified independently: no duplicate fn name across the 6 files. **Gate green, smoke 5/5.**

### Phase 4 — `chat.js` split + init.js / translation.js REVIEW (all files now <2k)
- **chat.js** (4,013) DELETED → chat_render (1,383) / chat_nav (266) / chat_tools (680) /
  chat_send (1,615). 88 defs relocated, no dupes, each `node --check` clean. The pre-existing
  `spinnerBar` latent block-scope finding relocated with its code (chat.js→chat_send.js); baseline
  entry re-pointed (not fixed — Rule 3). Net-globals 901 unchanged.
- **init.js REVIEW → SPLIT.** Plan said "split out monitors if cohesive, else document staying
  whole." Monitor-only extraction left init.js at ~2,187 (>2k → fails the no-file-over-2k criterion),
  so the split was extended to also lift the cohesive **admin-user/settings** domain (not bootstrap-
  coupled). init.js 2,899 → **1,209** (theme/thinking/composer/auth/research/caveman/slash/
  `init`/`renderPromptCards`/`applyRoleVisibility` stay — these run during the load-time bootstrap).
  → `user_admin.js` (949), `monitors.js` (745). Both load BEFORE init.js so their globals exist when
  `init()` runs. Net-globals unchanged.
- **translation.js REVIEW → SPLIT.** Single cohesive UI, but the live-mic domain (recording, VAD,
  WAV encode, live SSE, live-segment render — 20 defs) is operationally independent (record-on-demand,
  only reads shared `trState`/`trAuthHeaders`). Lifted to `translation_live.js` (624); translation.js
  2,389 → **1,766** (text/glossary/document/media + shared history stay). Loads after translation.js.
- **Pre-existing cross-file duplicate noted (NOT introduced, NOT fixed):** `escapeHtml` is defined in
  BOTH `favourites.js` and `workflows.js` — confirmed present before Tier F (git HEAD~5), both are
  "stays" files I never touched. ESLint `no-redeclare` is per-file so it doesn't flag cross-file dups;
  the net-globals count (`sort -u`) counts it once, so the invariant is unaffected. Out of scope
  (Rule 3). Flagged here for the Phase 5 cross-file-dup grep.
- **All files now <2,000 LOC.** Gate green, smoke 5/5.

### Phase 5 — Final source-validation (all 8 checks PASS, verified against source not the report)
1. **Every claimed split file exists at its claimed size** — all 20 new files confirmed via `wc -l`. ✓
2. **No function defined twice** — cross-file grep of all top-level fn/const/var/class names: the
   ONLY duplicate is the pre-existing `escapeHtml` (favourites.js + workflows.js, present before
   Tier F, untouched — documented in Phase 4). `const`/`let` grep "dupes" were destructuring
   artifacts, not real collisions. No split copied-instead-of-moved. ✓
3. **Net globals unchanged** — `.globals.json` writable count = 901 = baseline. (Phase-0 was 879;
   the +22 is the one sanctioned switchGeneralTab-decomposition bump, recorded at Phase 2.) ✓
4. **No monster file remains** — no web/js file >2,000 LOC; largest is settings_general_tabs.js
   (1,988). settings.js / panels.js / chat.js all DELETED (not shims). ✓
5. **index.html load order** — all 34 `js/` scripts present, none dangling; split files occupy
   their parents' original slots; ordering constraints hold (settings_general before
   settings_general_tabs; translation before translation_live; user_admin + monitors before
   init.js, which is last). ✓
6. **Full gate green + real browser pass** — `./js_gate.sh` PASS (eslint clean modulo 3 baseline,
   net-globals 901, smoke 5/5); headed (non-headless) browser pass 5/5; PLUS an extra console-error
   sweep over split-heavy flows not in the base smoke (Agent Settings, GDPR + Tools tabs, Translation
   view loading translation_live.js) — zero console errors. ✓
7. **Coverage promise** — all 17 original web/js files appear in the Master map with a final status
   + size; the 2 REVIEW files (init.js, translation.js) each carry a recorded SPLIT decision with
   rationale. Nothing silently omitted. ✓
8. **No inconsistency between report and source** — none found; nothing to revert. ✓

**Known gate limitation (documented, not a defect):** ESLint `no-redeclare` is per-file, so it does
NOT catch cross-file same-name globals — that's why Phase 5 check 2 greps cross-file independently.
The net-globals count (`sort -u`) is dup-insensitive, so the invariant stays valid regardless.

### Tier F summary
- **29,193 LOC across 17 files → all <2,000 LOC.** 3 monsters split (settings/panels/chat), 2 REVIEW
  files split (init/translation), de-dup verified-and-rejected, 20 new cohesive files created.
- Gate green after every step; one commit per green step, directly to main; HTML republished each step.
- Net behavior change: **zero** (gate + smoke + headed + extra sweep all green throughout).
- Net globals: 879 → 901 (single sanctioned decomposition bump; every split was a pure relocation).
