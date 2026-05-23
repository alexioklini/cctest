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
| 2 | Split `settings.js` (6,140 → ~6 files) | 🔄 in progress |
| 3 | Split `panels.js` (5,819 → ~5 files) | ⬜ planned |
| 4 | Split `chat.js` (4,013 → ~4 files) + REVIEW init.js / translation.js | ⬜ planned |
| 5 | Final source-validation (mandatory close-out) | ⬜ planned |

**RESUME POINT:** Phases 0–1 complete (gate green, baseline net-globals = **879**; de-dup verified
and rejected as net-negative). Next: **Phase 2 (split settings.js)**.

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
| `settings.js` | 6,140 | SPLIT (Phase 2) | ⬜ | → settings_agent / _teams / _general / _tools / _hooks / _schedule |
| `panels.js` | 5,819 | SPLIT (Phase 3) | ⬜ | → panels_chats / _projects / _right / _gdpr |
| `chat.js` | 4,013 | SPLIT (Phase 4) | ⬜ | → chat_send / _render / _nav / _tools |
| `init.js` | 2,899 | REVIEW (Phase 4) | ⬜ | Bootstrap + monitors. Over 2k — split out monitor classes if cohesive, else document why it stays whole (load-time init sequencing). Decision recorded at Phase 4. |
| `translation.js` | 2,389 | REVIEW | ⬜ | Over 2k but a single cohesive domain. Split only if it holds clear sub-domains; else stays. Decision to be recorded. |
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
| `dom_helpers.js` (NEW) | 0 | DEFERRED | ⬜ | Created only if a shared helper emerges during the splits; F-U1/F-U2 were rejected, so not created yet. |

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
