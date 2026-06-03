# Research → Import (Fast + Deep Research) — Implementation Plan

> ⬆️ **SUPERSEDED by `RESEARCH_IMPORT_DETAILED_SPEC.md`** — Research was designated
> the product's most important feature (2026-06-03), so it now has a full spec with
> mockups + end-to-end workflows. The locked decisions below still hold; use the
> detailed spec as the implementation reference.

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3 — Deep Research +
Discover/Fast Research). brain-agent VERSION when scoped: 9.62.0.

---

## What this is

NotebookLM source-discovery: enter a topic → it finds web sources (and, for Deep
Research, plans + browses many sites + returns a grounded report) → you import the
result into the notebook. We build both:
- **Fast Research** (Discover) — topic → search → pick from results → add to
  project. The cheap onramp.
- **Deep Research** — an agentic source-finder: plan → multi-search → fetch/read
  candidates → dedup → propose a curated source set + a synthesized report.

**Import target (locked):** BOTH — add the selected source URLs as persistent
project sources AND save a synthesized `.md` report as a project output.

---

## What exists (and an important distinction)

- **Search tools** — `exa_search` (`engine/tools/misc_tools.py:833`, returns
  `{title, link}`) + `searxng_search` (self-hosted, same shape). Clean URL lists,
  exactly what import needs.
- **`web_fetch`** — with crawl4ai headless rendering for JS pages.
- **THE IMPORT SEAM ALREADY EXISTS** — a project's `web_urls` field
  (`handlers/projects.py:196` whitelists it). A URL added there is fetched fresh,
  mined into the wing + KG, hash-gated, stale-purged on removal
  (`_sync_project_web_urls`). **"Import a source into a project" = append to
  `web_urls`.** Nothing new needed for that half.
- **Report-as-output** — the shared `project_outputs` store + `generate` endpoint
  (`OUTPUT_PRESETS_PLAN.md`). A synthesized report is just another output `kind`.
- **Websuche basket** — manual curate→prefetch, but **ephemeral per-turn** (NOT
  persisted). Useful pattern reference; not the persistence mechanism.

### ⚠️ Distinction the handover glossed

The **`deep-research` skill is a Claude Code HARNESS skill (the agent's), NOT a
brain-agent feature.** brain-agent has the raw search/fetch tools but no
orchestrated planning loop of its own. So "Deep Research" here = **building that
agentic loop inside brain-agent** (multi-search + read + dedup + synthesize),
using `background_call` + the search tools. Don't assume the harness skill is
callable from the product.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Scope** | **Both Fast + Deep Research** | Fast = the onramp (Phase 1); Deep = agentic loop (Phase 2). Shared import seam. |
| **Import target** | **Both**: source URLs → `web_urls` AND a synthesized `.md` report → project output | Sources stay live/refetched; report is a saved writeup. |

---

## Build steps

### Phase 1 — Fast Research (the cheap onramp)

1. **Search endpoint** — reuse the existing `POST /v1/web/search` (the Websuche
   search-only passthrough to `searxng_search`), or a thin new one that also runs
   `exa_search`. Topic in → `{title, link, snippet}[]` out. (No LLM, no fetch.)
2. **Selection UI** — present results in the project context; user checks the ones
   to import.
3. **Append to `web_urls`** — selected URLs added to the project's `web_urls` via
   the existing `update_project` path (`projects.py:196`). The sync daemon mines
   them automatically. **This is the whole import half — already built.**

Phase 1 alone delivers real value and is small.

### Phase 2 — Deep Research (the agentic loop)

4. **Planning + search agent** — a server-side loop (via `background_call`, likely
   `max_rounds > 1` with the search/fetch tools enabled):
   - decompose the topic into sub-queries,
   - run `exa_search`/`searxng_search` across them,
   - `web_fetch` (crawl4ai) the strongest candidates + read,
   - dedup + rank into a curated source set.
   - This is a genuine agentic loop — budget it (round cap, source cap) so it
     can't run away. Reuse the bg-task fan-out patterns if helpful (see
     `[[project_bgtask_fanout_join_spec]]`), but start simple/sequential.
5. **Synthesize report** — one grounded `background_call` over the fetched
   material → a cited `.md` saved as a `project_outputs` row (`kind:
   research_report`).
6. **Propose-then-import** — present the curated source set for user approval
   (don't auto-add — let the user pick), then append approved URLs to `web_urls`.
   The report output is saved regardless.

### Phase 3 — UI

7. A "Research" action in the project: topic input → (Fast) results to pick, or
   (Deep) a progress view → curated sources to approve + the generated report.
   Follow web/js conventions; gate with `./web/js/js_gate.sh`.

---

## Open items to resolve AT BUILD

1. **Search backend mix** — exa (paid, better) vs searxng (free) vs both; let the
   admin/user choose? Default to what's configured.
2. **Deep Research budget** — max sub-queries, max fetches, round cap, wall-clock.
   Must be bounded + the limits surfaced (no silent truncation — repo rule).
3. **Auto-import vs propose** — locked to PROPOSE for Deep (user approves sources).
   For Fast, the user already picks. Don't silently bloat a project's sources.
4. **Report grounding** — synthesize only from fetched material (cite sources);
   apply the project's citation discipline. Avoid the model adding un-fetched
   claims.
5. **Dedup vs existing project sources** — skip URLs already in `web_urls`.
6. **GDPR/quota** — Deep Research fans out many LLM + fetch calls; ensure it
   routes through the normal cost/quota + GDPR seams.

## Explicitly OUT of scope

- Using the Claude Code `deep-research` harness skill as the engine (it's the
  agent's, not the product's — build the loop in brain-agent).
- Drive/other-connector source discovery (NotebookLM has Drive; we don't — MCP
  only). Web sources only.
- Auto-refreshing Deep Research reports on a schedule (could ride the scheduler
  later).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new endpoints → `01-api.md`; new project UI →
  `06-user-manual.md` (German); if a new agent tool/loop is added → `02-tools.md`.
  New `research_report` output `kind` → `03-storage.md`. VERSION bump in two
  places. python-compile brain.py. Graceful restart (SIGTERM, never SIGKILL).
  Commit to main. `./web/js/js_gate.sh` passes.

## Success criteria

- **Fast:** topic → search results → selected URLs become persistent project
  sources that get mined (answerable via chat).
- **Deep:** topic → a bounded agentic search that proposes a curated, deduped
  source set for approval AND produces a cited `.md` research report saved as a
  project output.
- Approved sources land in `web_urls` and mine; the report appears in Studio.
- Budgets are enforced + surfaced; no runaway fan-out.
- brain.py compiles; version check after restart; js_gate passes.
