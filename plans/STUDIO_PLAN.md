# Studio — Per-Project Outputs Surface — Implementation Plan

> ⬆️ **SUPERSEDED by `STUDIO_DETAILED_SPEC.md`** (mockups + workflows + edge
> cases). Locked decisions below still hold; use the detailed spec to build.

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Depends on:** the shared `project_outputs` store (defined in
`OUTPUT_PRESETS_PLAN.md` + `AUDIO_OVERVIEW_PLAN.md`) existing first.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3 — "Studio: multiple
outputs of same type"). brain-agent VERSION when scoped: 9.62.0.

---

## What this is

NotebookLM stores many generated outputs per notebook (several audio overviews,
several reports, in several languages). "Studio" = the per-project surface to
**browse + manage** those outputs. It is a **presentation layer**, not new
generation machinery — the generators live in the presets / Audio Overview plans.

---

## Why it's thin — the store is already decided

`OUTPUT_PRESETS_PLAN.md` and `AUDIO_OVERVIEW_PLAN.md` already commit to a shared
`project_outputs` store (`kind`, `title`, `path`, `created_at`, `opts`) holding
many generated outputs per project. Studio is the view over it.

**What exists today:** a generic **artifacts browse grid** (`web/js/panels_
artifacts.js`) — agent-scoped, filterable by source (chat/scheduled) + type, with
a content viewer (`renderArtifactContent`, `panels_artifacts.js:525`). **What's
missing:** a *per-project, curated outputs* view grouped by `kind`. Studio fills
exactly that gap, reusing the existing viewer for opening a `.md`/MP3 output.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Tracking** | Thin standalone plan | Greenlit; explicitly gated on the `project_outputs` store existing. |
| **Manage actions** | **Full: open · regenerate · rename · delete** | Complete lifecycle per output. |

---

## Build steps

### 1. List endpoint

- `GET /v1/projects/<id>/outputs` → the project's `project_outputs` rows
  (`id, kind, title, path, created_at, opts`), newest first, groupable by `kind`.
  Auth + project-membership check.

### 2. Studio view (frontend)

- A per-project "Studio" section/tab: list/grid of outputs **grouped or filterable
  by `kind`** (so "3 Audio Overviews · 2 Study Guides · 1 FAQ" reads cleanly — the
  "many of the same type" requirement).
- Reuse `renderArtifactContent` to open a `.md`/MP3 output (don't build a new
  viewer).
- Follow web/js conventions (global `<script>`, load order); gate with
  `./web/js/js_gate.sh`.

### 3. Manage actions (full lifecycle)

- **Open** — view via the existing artifact viewer.
- **Regenerate** — re-run the output's generator with its stored `opts` (calls the
  shared `POST /v1/projects/<id>/generate` with the same `kind` + options). Decide
  UX: new output row vs. version-in-place (`artifact_versions` already versions —
  lean new row so history is visible, or reuse versioning; DECIDE AT BUILD).
- **Rename** — update the row `title`. Small `PATCH`/manage endpoint.
- **Delete** — remove the row + its artifact file (use the existing artifact
  delete path; don't orphan files).

---

## Open items to resolve AT BUILD

1. **Regenerate semantics** — new output row vs. new version of the existing one.
   (New row = clearer history; versioning = tidier. Pick one.)
2. **Endpoint surface** — extend `/v1/projects/<id>/...` vs. a `/v1/studio/*`
   prefix shared with generation. Keep consistent with the generate endpoint.
3. **Grouping UX** — sections per `kind` vs. a filter dropdown (the artifacts grid
   already has a filter pattern to mirror).
4. **MP3 outputs** — audio overviews need an inline player in the viewer; confirm
   `renderArtifactContent` handles audio or add an audio case.

## Explicitly OUT of scope

- The generators themselves (presets / Audio Overview plans).
- The `project_outputs` store definition (shared — defined with presets/Audio).
- Multiple LANGUAGE variants per output (rides translation stack later; the store
  supports it via `opts`, the UI just lists them).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new endpoint(s) → `01-api.md`; new project UI →
  `06-user-manual.md` (German). VERSION bump in two places. python-compile
  brain.py. Graceful restart (SIGTERM, never SIGKILL). Commit to main.
  `./web/js/js_gate.sh` passes.

## Success criteria

- A project's Studio lists all its generated outputs, grouped/filterable by kind,
  showing multiple of the same type cleanly.
- Each output can be opened, regenerated, renamed, and deleted; delete removes the
  underlying file (no orphans).
- MP3 (audio overview) outputs play inline.
- Reuses the shared store + generate endpoint (no second generation path).
- js_gate passes; brain.py compiles; version check after restart.
