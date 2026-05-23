# JS Refactor Plan — web/js/ (Tier F)

Companion to the completed Python refactor (`REFACTOR_REPORT.md`). Same discipline:
analyze → goal → gate → extract-with-gate-between → living report. This file is the
**plan**; progress is tracked in `JS_REFACTOR_REPORT.md` (created when work starts).

**User decisions (2026-05-23):**
1. **Build a real gate FIRST** (before any refactor) — JS has no test/lint/build infra today.
2. **Split + dedup, stay on global `<script>` tags** — NO ES modules, NO bundler.
3. **Defer thread-local** (separate later tier) — this plan is web/js only.
4. **JS first**, thread-local after.

---

## The problem

`web/js/` = **29,193 LOC across 17 files**, loaded as **global-scope `<script>` tags** in a
fixed dependency order via `web/index.html` (api → state → utils → nav → sessions → chat →
panels → settings → search → files → workflows → favourites → share → translation →
classification → buddy → init). Every function/var is a browser global; cross-file calls rely
on load order. **No package.json, no ESLint, no tests, no bundler.** The Python refactor was
safe because of the import-gate + unittest + `refactor_gate.sh`; **none of that exists here** —
so Phase 0 is building the equivalent.

The 3 monsters: `settings.js` (6,140), `panels.js` (5,819), `chat.js` (4,013) = 16k of the 29k.

## The goal (success criteria — loop until met)

- The 3 monster files split into cohesive same-style global-script files (~4-8 each), each a
  clear single domain. No file over ~2,000 LOC when done.
- Cross-file duplication (modal scaffolding, DOM helpers, date formatters) extracted into
  shared files (extend existing `utils.js`/`api.js`, don't make competing copies).
- **Zero behavior change** — verified by the gate after every step.
- **Net global count unchanged** (a moved function is still exactly one global, defined once).
- Load order preserved/correct: index.html `<script>` tags updated so every function is a
  global before its first caller runs.
- Living `JS_REFACTOR_REPORT.md`, one commit per split, gate green between each.

## Non-goals (explicit scope fence)

- NO ES modules / import-export / bundler (user decision — stay global scripts).
- NO framework migration, NO CSS changes, NO behavior/feature changes.
- NO thread-local work (deferred to a later tier).
- NOT splitting files that aren't monsters unless they hold extractable duplication.

---

## Phase 0 — Build the GATE (DO THIS FIRST, before any refactor)

The JS analog of `refactor_gate.sh`. Lives in `web/js/`. Node 22 / npm 10 are available.

### Gate component 1 — ESLint (undefined-global detection)
- **ESLint 9 flat config** (`web/js/eslint.config.js`), `sourceType: "script"` (these are
  global scripts, not modules). ESLint 8 is EOL — use 9.
- Rules: `no-undef` (error), `no-unused-vars` (warn — too noisy as error initially),
  `no-redeclare` (error — catches a function accidentally defined in two split files).
- **Globals list is AUTO-GENERATED, never hand-maintained** (a hand list rots the instant the
  refactor moves a global). A `web/js/gen-globals.sh` greps every top-level
  `function`/`async function`/`const NAME`/`class NAME`/`var NAME` across `web/js/*.js` plus the
  `<script>` inline block + onclick handlers in index.html, and writes `web/js/.globals.json`.
  ESLint loads it. Regenerate as the FIRST step of the gate so it's always current.
- **The real failure mode this catches:** because the globals list is generated FROM the
  defining files, a function that's *called* but *defined nowhere* (e.g. a split dropped it, or
  a typo'd rename) is NOT in `.globals.json` → `no-undef` fires at the call site. That is the
  exact "moved function lost its global" signal. (A function defined but never called surfaces
  via `no-unused-vars`.) **Net-globals invariant:** the gate also diffs `.globals.json` count
  before/after a split — it must be unchanged (a split relocates globals, never adds/drops).

### Gate component 2 — Playwright smoke test (`web/js/smoke.spec.js`)
- Boots against the running server (`http://127.0.0.1:8420`), logs in `admin`/`admin`, exercises
  the core flows the 3 monsters own. Key flows (selectors to confirm against index.html during
  Phase 0 build): login → welcome view; open chat + type (send button activates); open General
  Settings modal (tabs render); open a settings sub-tab that triggers a fetch (models/providers);
  toggle the right panel; open Projects list. Assert no console errors during each flow
  (a thrown ReferenceError from a missing global shows here even if ESLint somehow missed it).
- **Console-error assertion is load-bearing** — it's the runtime backstop for anything static
  analysis can't see (dynamic dispatch, inline-handler typos).

### Gate component 3 — `web/js/js_gate.sh`
```
1. gen-globals.sh                  # regenerate .globals.json from current source
2. npx eslint (flat config)        # no-undef / no-redeclare must pass
3. globals-count diff vs baseline  # net globals unchanged (split invariant)
4. (if server up) npx playwright test smoke.spec.js  # core flows + zero console errors
```
Prints `JS GATE PASS ✓` / `JS GATE FAIL ✗`. Component 4 skips with a loud notice if the server
isn't running (so pure-split steps can gate on 1-3 fast; a server-up run is required before
declaring a step done).

**Phase 0 exit:** gate built, **runs green against UNCHANGED current code** (this proves the
gate works and establishes the baseline globals count + passing smoke), committed.

> devDeps only (`eslint`, `@eslint/js`, `@playwright/test`) in `web/js/package.json`. `node_modules/`
> gitignored. This adds a JS toolchain to a repo that had none — keep it minimal and isolated under `web/js/`.

---

## Phase 1 — Cross-cutting de-dup (the JS U1–U5), smallest blast radius first

Extract duplication into shared files BEFORE splitting monsters, so the split files inherit the
deduped helpers. Extend existing `utils.js`/`api.js` — do NOT create competing copies (the
Python U2 lesson). Candidates from analysis (verify counts at extraction time):
- **F-U1 modal factory** — ~35 hand-rolled overlay-create blocks → `createModal({title,content,buttons})` in a new `dom_helpers.js` (or utils.js). Keep the `.modal-overlay`/`.modal-content` class names (CSS depends on them).
- **F-U2 DOM helpers** — repeated `.closest('.modal-overlay').remove()`, `classList.toggle(x,cond)` → `dom_helpers.js`.
- **F-U3 date/duration formatters** — `humanAgo` (currently buried in panels.js) + inline date formatting → `utils.js`.
- **Already centralized (do NOT re-extract):** `esc`, `showToast/showDialog/showConfirm/showPrompt`, `modelOption/modelShortName/modelHasCapability`, `API._headers/_handleAuthError`. Audit for stray direct `fetch()` not going through `API.*` and repoint.

Each de-dup = one commit, gate between.

## Phase 2 — Split `settings.js` (6,140 → ~6 files)
`settings_agent.js` (agent settings modal), `settings_teams.js` (team CRUD),
`settings_general.js` (general modal + switchGeneralTab), `settings_tools.js` (tool settings +
research-mode + integrations), `settings_hooks.js` (hook UI/CRUD), `settings_schedule.js`
(scheduled tasks + forms). Add the new `<script>` tags in place of the old `settings.js` line in
index.html, original relative order. `switchGeneralTab` is itself ~2k LOC — if a single file is
still too big, split by tab via a dispatch table (analysis §5.2). Gate after.

## Phase 3 — Split `panels.js` (5,819 → ~5 files)
`panels_chats.js` (chats list view + scroll utils), `panels_projects.js` (all project CRUD/files/
input-folders/sync — large; may sub-split), `panels_right.js` (right panel + resize),
`panels_gdpr.js` (GDPR/PII/classification modals). Gate after.

## Phase 4 — Split `chat.js` (4,013 → ~4 files)
`chat_send.js` (sendMessage + SSE callbacks), `chat_render.js` (renderMessages + reconcile),
`chat_nav.js` (turn nav/collapse), `chat_tools.js` (tool render + worker flows). chat is the
streaming core — the smoke test's send-flow + console-error assertion is the key guard here.
Gate after.

---

## The safe-split rule (load-order contract — analysis §3)

Analysis confirmed: **no forward-reference risk** — every function is called lazily (click
handlers/callbacks), no file reads another's globals at top level, no circular deps, no
`window[name]()` dynamic dispatch, no `eval`. So a function can move to a new file as long as:
1. The new file contains ONLY definitions (no top-level executable code reading other-file globals).
2. New `<script>` tags go in the original file's index.html slot, BEFORE `init.js` (init.js is the
   only load-time caller, last in the order).
3. A function is never split apart from a same-load-time-called sibling.
The gate's `no-undef` (against the generated globals) + net-globals-count diff + smoke
console-error check together enforce this mechanically.

## Invariants any split MUST preserve
1. **Net global count unchanged** — relocate, never add/drop a global. (Gate checks this.)
2. **Load order correct** — every global defined before its first caller (init.js is the only
   load-time caller; everything else is lazy).
3. **No competing copies** — extend utils.js/api.js; one definition per name (`no-redeclare`).
4. **Zero behavior change** — smoke flows + console-error-free; CSS class names preserved.
5. **Fail loud** — gate red blocks the commit; a missing global must surface, not silently no-op.

## Execution protocol (same as the Python refactor)
- Subagent-per-split (bulky reads die with it); main thread holds only pass/fail + what moved.
- One split = one commit, directly to main. Gate green before commit. `JS_REFACTOR_REPORT.md`
  updated in the same commit (Master file-map flipped ⬜→✅, per-split record block).
- Stop + report on any gate failure or genuine ambiguity the plan doesn't answer.
- Restart not needed (static files; just reload the browser) — but run the server-up smoke
  before declaring a phase done.

## Open question to settle at Phase 0
- **Where does the smoke test get a clean logged-in state?** admin/admin against the live dev
  server is simplest but mutates real state if a flow creates data. Decision: smoke flows are
  READ-ONLY (open views/modals, type-without-send) so they don't create chats/projects. If a
  write-path needs covering later, use a throwaway session and clean up. (Confirm during build.)
