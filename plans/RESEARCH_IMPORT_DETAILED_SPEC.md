# Research → Import — DETAILED DESIGN SPEC

**Status:** DETAILED SPEC (mockups + end-to-end workflows for all use cases).
PRE-IMPLEMENTATION — nothing built. This is the product's **most important
feature** (user designation, 2026-06-03), so it gets a full spec before any code.

**Supersedes:** `RESEARCH_IMPORT_PLAN.md` (the lean scope). That file's locked
decisions still hold; this doc is the implementation-grade elaboration.

**Parent:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3). brain-agent VERSION: 9.62.0.

> ⚠️ This spec contains UI mockups + workflow diagrams. The mockups are *intent*,
> not pixel-final — they fix layout, states, and data flow so implementation and
> review have a shared reference. Adjust visuals to match the existing UI's look.

---

## 0. Verified code anchors (build on these, don't reinvent)

| Capability | Where | Note |
|---|---|---|
| Search-only endpoint | `POST /v1/web/search` (`handlers/chat.py:3194`) | SearXNG passthrough, no fetch/LLM. Returns `{query, results:[{title,link}], result_count}`. |
| Exa search | `exa_search` (`engine/tools/misc_tools.py:833`) | `{title, link}` per result. Paid, higher quality. |
| SERP "search → pick" UI pattern | `web/js/panels_websuche.js` | The Websuche basket already renders a Google-style SERP with per-result checkboxes. **Reuse this interaction pattern.** |
| Import seam (persistent source) | project `web_urls` field, whitelisted in `handlers/projects.py:196` | Append a `{url,title}` → sync daemon fetches (crawl4ai), mines into wing+KG, hash-gated, stale-purges on removal. **This is the entire import-half.** |
| Long-run progress | `run_background_task` + `task_status` (`engine/tool_schemas.py:820`) | Deep Research is a long agentic run — surface via this. |
| Mining progress UI | `GET …/projects/{name}/sync-status` (`handlers/projects.py:561`) + `panels_project_sync.js` | Shows "mining…" — Fast import reuses this to show imported sources being mined. |
| Report-as-output | shared `project_outputs` store + `POST /v1/projects/<id>/generate` (`OUTPUT_PRESETS_PLAN.md`) | A research report is an output `kind: research_report`. |

---

## 1. Feature summary & locked decisions

Two modes, one feature, sharing the import seam:

- **Fast Research (Discover):** topic → search → user picks results → selected URLs
  appended to the project as persistent, mined sources. Synchronous, seconds.
- **Deep Research:** topic → a **bounded agentic loop** (decompose → multi-search →
  fetch/read candidates → dedup/rank) → proposes a curated source set for approval
  AND produces a cited `.md` report saved as a project output. Asynchronous,
  minutes.

**Locked (from `RESEARCH_IMPORT_PLAN.md`):**
- Build BOTH Fast + Deep.
- Import target = BOTH: source URLs → `web_urls` AND a synthesized report → output.
- Deep Research = built **inside brain-agent** (the `deep-research` skill is the
  Claude Code harness's, NOT callable as a product feature).
- Sources are **proposed, never auto-imported** — the user always approves.

---

## 2. Entry points & surface

Research lives **inside a project** (it imports *into* a project's sources).

```
┌─ Project: "EU AI Act Compliance" ───────────────────────────┐
│  [ Sources ]  [ Chat ]  [ Studio ]  [ 🔍 Research ]   ⚙︎     │   ← new "Research" tab
└──────────────────────────────────────────────────────────────┘
```

- New **"Research" tab** in the project view (peer of Sources/Chat/Studio).
- Disabled with a tooltip when no search backend is configured (exa key AND no
  SearXNG) — see Workflow E1.

---

## 3. MOCKUPS

### 3.1 Research tab — initial state

```
┌─ 🔍 Research ────────────────────────────────────────────────┐
│                                                              │
│  Find new sources for this project.                          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Topic or question…                                     │ │
│  │ e.g. "GPAI model transparency obligations under the    │ │
│  │       EU AI Act"                                        │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│   Mode:  ( • ) Fast      ( ) Deep                            │
│          quick search    plans + reads + writes a report     │
│                                                              │
│   Search:  [✓] SearXNG (free)   [✓] Exa (better)            │  ← only show configured ones
│                                                              │
│                                    [ Start Research → ]      │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Fast Research — results (the "pick" screen)

```
┌─ 🔍 Research · Fast · "GPAI transparency obligations" ───────┐
│  12 results · 3 selected            [ Select all ] [ Clear ] │
│                                                              │
│  [✓] EU AI Act — Article 53 GPAI obligations               │
│      eur-lex.europa.eu/…/art_53          ⓘ already in proj? │
│  [✓] Commission GPAI Code of Practice (2025)               │
│      digital-strategy.ec.europa.eu/…                        │
│  [ ] Transparency requirements explainer — IAPP            │
│      iapp.org/news/…                                        │
│  [✓] Model cards & documentation duties (analysis)         │
│      lawfaremedia.org/…                                     │
│  [ ] (Reddit thread — low quality)                  ⚠ blog  │
│      reddit.com/r/…                                          │
│  …                                                          │
│                                                              │
│                    [ Import 3 selected sources → project ]   │
└──────────────────────────────────────────────────────────────┘
```
- Each row: checkbox · title · URL · badges. Badge `already in project` (dedup vs
  existing `web_urls`, pre-checked OFF + disabled). Badge `⚠ blog/forum` (low-trust
  domain hint, advisory only).
- Reuses the `panels_websuche.js` SERP rendering.

### 3.3 Fast Research — post-import confirmation

```
┌─ ✅ Imported 3 sources ──────────────────────────────────────┐
│  Added to project sources. Mining into memory now…          │
│                                                              │
│   • art_53 (eur-lex)            ⟳ mining…                    │  ← live, polls /sync-status
│   • GPAI Code of Practice       ⟳ mining…                    │
│   • Model cards analysis        ✓ mined (42 drawers)         │
│                                                              │
│   These are now searchable in chat.   [ Go to Sources ]      │
└──────────────────────────────────────────────────────────────┘
```

### 3.4 Deep Research — live progress (async)

```
┌─ 🔬 Deep Research · "GPAI transparency obligations" ─────────┐
│  Running…  ~2–4 min          [ Cancel ]                      │
│                                                              │
│  ▸ Planning sub-questions………………………………… ✓ (6 sub-queries)    │
│  ▸ Searching………………………………………………………… ✓ (48 candidates)      │
│  ▸ Reading & ranking sources…………………… ⟳ 19 / 48            │
│  ▸ Writing report……………………………………………… ◦ pending            │
│                                                              │
│  Budget: 31 / 60 fetches · 14k / 80k tokens                 │  ← bounded, visible
│                                                              │
│  (You can leave this tab — it keeps running. We'll notify    │
│   you when it's done.)                                       │
└──────────────────────────────────────────────────────────────┘
```

### 3.5 Deep Research — results (propose-then-approve)

```
┌─ 🔬 Deep Research · done ────────────────────────────────────┐
│                                                              │
│  📄 Report: "GPAI Transparency Obligations — Synthesis"      │
│      18 citations · 9 sources       [ Open report ]          │
│                                                              │
│  Proposed sources to import (9 found, dedup'd vs project):   │
│  [✓] EU AI Act Art. 53 (eur-lex)                            │
│  [✓] GPAI Code of Practice                                  │
│  [✓] EDPB opinion 2025/03                                   │
│  [ ] IAPP explainer            (already in project)  ⛔      │
│  …                                                          │
│                                                              │
│  [ Import 7 selected → project ]   [ Save report only ]      │
└──────────────────────────────────────────────────────────────┘
```
- Report is **saved to Studio regardless** of import choices.
- Sources: same pick UI as Fast; defaults to all-new-checked, in-project ones
  disabled.

---

## 4. END-TO-END WORKFLOWS (all use cases)

Notation: **U**=user, **FE**=frontend, **BE**=backend/server, **D**=sync daemon.

### W1 — Fast Research, happy path
1. U opens project → Research tab → types topic → Mode=Fast → Start.
2. FE → `POST /v1/web/search {query}` (+ Exa if enabled, merged/deduped by URL).
3. BE returns `{results:[{title,link}]}`. FE renders SERP (3.2), pre-marks
   in-project URLs as disabled (dedup vs `web_urls`).
4. U checks N results → "Import N selected".
5. FE → `update_project` appending the N `{url,title}` to `web_urls` (existing
   whitelist path).
6. D (next sync cycle) fetches each (crawl4ai), mines into wing+KG, hash-gated.
7. FE shows 3.3, polling `/sync-status` until each shows mined. Sources now
   answerable in chat. ✔

### W2 — Deep Research, happy path
1. U: Research tab → topic → Mode=Deep → Start.
2. FE → `POST /v1/projects/<id>/research/deep {topic, search_backends, budget}`.
   BE spawns a **bounded background task** (run_background_task semantics),
   returns a `task_id`. FE switches to 3.4 and polls `task_status`/SSE.
3. BE loop (bounded by budget): decompose topic → sub-queries; run
   exa/searxng per sub-query; `web_fetch` (crawl4ai) top candidates; read +
   dedup + rank → curated source set.
4. BE: one grounded synthesis `background_call` over fetched material → cited
   `.md` → saved as `project_outputs {kind:research_report}`.
5. Task completes → FE shows 3.5: report link + proposed sources.
6. U approves a subset → same import path as W1 step 5–7. Report already in
   Studio. ✔

### W3 — Fast, user imports nothing
- U unchecks all / closes. No `web_urls` change. No-op. ✔ (Never auto-import.)

### W4 — Deep, "Save report only"
- U clicks "Save report only" → no sources imported; the `research_report`
  output stays in Studio. ✔

### W5 — Re-research same topic later
- New run. Dedup vs current `web_urls` hides/disables already-imported sources
  (3.2/3.5 badges). A new report output is created (Studio keeps both — versioning
  per `STUDIO_PLAN.md`). ✔

### W6 — Source already in project
- Dedup pre-pass (compare normalized URL vs `web_urls`): row shown with
  `already in project`, checkbox OFF + disabled. Prevents duplicate mining. ✔

### W7 — Imported source fails to fetch/mine
- The `web_urls` mining path already handles fetch failure (see
  `VIDEO_INGEST_PLAN.md` fail-loud rule + existing web-url behavior). 3.3 shows
  that source as `⚠ couldn't fetch` (from `/sync-status`), others proceed. The bad
  URL stays in `web_urls` (user can remove it in Sources). ✔

### W8 — Deep Research hits budget ceiling
- Loop stops at the cap (fetches/tokens/rounds), synthesizes from what it has,
  and the report notes coverage was bounded ("searched 48, read 31 within
  budget"). **No silent truncation** — 3.4 shows the budget bars; the report says
  so. ✔

### W9 — Deep Research finds nothing usable
- All candidates low-quality/unfetchable → BE returns "no strong sources found,"
  no report saved (or a stub explaining why), proposes nothing. FE shows an empty
  state with a "broaden the topic / try Fast" hint. ✔

---

## 5. ERROR & EDGE CASES (E-series)

- **E1 No search backend configured** — Research tab disabled, tooltip "Configure
  Exa or SearXNG in Settings → Tools." (Check at tab render.)
- **E2 Search backend down mid-run** — Fast: show the error, keep any partial
  results. Deep: degrade (skip that backend), continue if the other works; if both
  fail, fail the task loudly with the reason.
- **E3 User cancels Deep mid-run** — `task_status` cancel; partial work discarded;
  no report saved unless a synthesis already completed (then offer to keep it).
- **E4 Deep task survives a server restart** — reuse the background-task recovery
  posture; if unrecoverable, mark the task failed with a clear message (don't hang
  the UI).
- **E5 GDPR/quota** — Deep fans out many LLM+fetch calls; ALL route through the
  normal cost/quota + GDPR seams (`gdpr_pick_model_for_background`). A quota block
  surfaces as a task error, not a crash.
- **E6 Malformed/duplicate URLs from search** — the `web_urls` whitelist already
  normalizes + dedups (`projects.py:196`); rely on it.
- **E7 Non-project context** — Research is project-only; not shown outside a
  project (it has nowhere to import to).
- **E8 Huge result set** — cap the SERP (e.g. top 30) + show "showing 30 of N";
  Deep caps candidates by budget. No unbounded lists.

---

## 6. DATA & API CONTRACTS (to finalize at build)

**New endpoints:**
- `POST /v1/projects/<id>/research/search {topic, backends[]}` → `{results:
  [{title, url, snippet, in_project:bool, trust_hint?}]}` (Fast; may just reuse
  `/v1/web/search` + a dedup pass — DECIDE).
- `POST /v1/projects/<id>/research/deep {topic, backends[], budget{fetches,
  tokens, rounds}}` → `{task_id}`. Progress via `task_status`/SSE.
- Import = existing `update_project` (`web_urls` append) — **no new endpoint.**
- Report = existing `project_outputs` (`kind: research_report`).

**Deep Research result object** (returned on task completion):
`{report_output_id, proposed_sources:[{title,url,snippet,in_project}],
budget_used:{fetches,tokens,rounds}, coverage_note}`.

**Budget defaults (tunable, server-side):** e.g. `fetches:60, tokens:80k,
rounds:8`. Surfaced in the UI (3.4) — never silent.

---

## 7. BUILD PHASING

1. **Phase 1 — Fast Research** (small, high value): search endpoint/reuse + SERP
   pick UI (reuse Websuche) + dedup + append to `web_urls` + mining-status view.
   Shippable alone.
2. **Phase 2 — Deep Research engine** (the agentic loop): bounded background task,
   decompose/search/fetch/rank, synthesis → `research_report` output.
3. **Phase 3 — Deep Research UI**: live progress (3.4) + propose-approve (3.5),
   wired to Studio for the report.

Each phase verified before the next (CLAUDE.md goal-driven discipline).

---

## 8. USER DECISIONS (resolved 2026-06-03) + remaining open items

**DECIDED:**
1. **Deep Research budget = GENEROUS** — default ceiling ≈ **60 fetches / ~4 min**
   wall-clock (plus a token cap, suggest ~80k). Deeper coverage; surfaced live in
   the 3.4 progress bars; enforced (W8 — stop + note bounded coverage, never
   silent).
3. **Report style = STRUCTURED + CITED** — long-form structured report under the
   project's **research_mode discipline** (REFUSAL/PRECISION/CITATION + the
   citation validator). This is the product's differentiator — grounded, cited,
   refuses rather than fabricates. The synthesis `background_call` runs with that
   discipline on.
5. **Completion notification = IN-APP ONLY** — a badge/toast when the user returns
   to the app; no push/Telegram in v1. (Run keeps going if they leave the tab; they
   see it on return.)

**STILL OPEN (decide at build, sensible defaults noted):**
2. **Search backend default** — prefer Exa when its key is configured, else
   SearXNG; offer both as toggles per run (3.1). Default both-on where available.
4. **Trust hints** — show advisory low-quality-domain badges (blog/forum/reddit) in
   the SERP (3.2); Deep MAY down-rank them but does not exclude. Advisory only.
6. **Auto-refresh** — imported sources (`web_urls`) already re-fetch on the normal
   project cycle (keep). Deep *reports* do NOT auto-regenerate in v1 (could ride the
   scheduler later).

---

## 9. Repo-convention obligations (same change as implementation)

- brain-agent-guide skill: new endpoints → `01-api.md`; new project UI/tab →
  `06-user-manual.md` (German); the agentic loop/tools → `02-tools.md`;
  `research_report` output kind → `03-storage.md`; architecture of the Deep loop →
  `05-internals.md`. VERSION bump in two places. python-compile brain.py. Graceful
  restart (SIGTERM, never SIGKILL). Commit to main. `./web/js/js_gate.sh` passes
  (net-globals-count updated for new JS).

## 10. Success criteria (end-to-end)

- **Fast:** topic → SERP → pick → selected URLs become mined project sources,
  answerable in chat, with live mining status and dedup vs existing sources.
- **Deep:** topic → bounded agentic run with visible progress + budget → proposes a
  deduped source set for approval AND saves a cited report to Studio; budgets
  enforced + surfaced; cancel/restart/quota/empty-result all handled per the
  E-series.
- No auto-import; no silent truncation; all the W- and E-cases behave as specified.
- js_gate passes; brain.py compiles; version check after restart.
```
