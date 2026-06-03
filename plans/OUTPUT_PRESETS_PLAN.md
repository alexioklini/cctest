# Output Presets (Study Guide / Briefing / FAQ / Timeline) — Implementation Plan

> ⬆️ **SUPERSEDED by `OUTPUT_PRESETS_DETAILED_SPEC.md`** (mockups + workflows +
> edge cases). Locked decisions below still hold; use the detailed spec to build.

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3, the four "thin
templated" presets). brain-agent VERSION when scoped: 9.62.0.

---

## What this is

One-click grounded outputs over a project's sources, each a NotebookLM-named
feature: **Study Guide · Briefing Doc · FAQ · Timeline**. Each = a canned grounded
prompt + an expected output format, run over the project corpus and saved as a
`.md` artifact.

**Four NotebookLM features for essentially one mechanism** — the lowest-risk,
highest-leverage cluster in Tier 3.

---

## ⚠️ Shared infrastructure — coordinate with Audio Overview & later presets

The decisions here (**server endpoint that runs a grounded turn + saves a
project-level output**) are the SAME infrastructure the Audio Overview "project
Studio" surface needs (`AUDIO_OVERVIEW_PLAN.md` Phase 2), and the same the later
Flashcards/Quizzes (Tier 3) will need. **Build the endpoint + project-output store
ONCE, shared.** Don't fork a second generation path. A single
`POST /v1/projects/<id>/generate {kind, ...opts}` (or a `/v1/studio/*` surface)
should dispatch to: preset generators (this plan), audio overview, flashcards,
quizzes. The "project-output store" open item in the Audio Overview plan and this
plan is the SAME question — resolve it together.

---

## What already exists (so this is thin)

- The **entire grounded-answer path**: `tool_mempalace_query` + KG + citation
  validator / research-mode discipline. A preset is just a canned prompt run
  through this.
- **`sidecar_proxy.background_call(...)`** (`handlers/sidecar_proxy.py:834`) — the
  synchronous one-shot LLM call to run the generation server-side.
- **Artifact write** — relative path → session/project artifact folder
  (`engine/tools/file_tools.py:174`), auto-registers as `artifact_versions` +
  emits `artifact_updated` SSE. The `.md` output rides this.
- A **`renderPromptCards()` / favourites** UI pattern (`web/js/init.js:1159`) —
  reference for one-click card UI (NOT the same as project presets; favourites are
  user-curated. Use as a visual/interaction pattern, not the backing store).

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Mechanism** | **Server endpoint per generation** (`POST /v1/projects/<id>/generate`) | Runs the grounded turn server-side via `background_call`. Presets editable server-side; output is a first-class project output. (Shared with Audio Overview / Flashcards / Quizzes — see warning above.) |
| **Output** | **Saved `.md` artifact** in the project | Browseable + regenerable, closer to NotebookLM's stored outputs. Needs the (shared) project-output store. |
| **v1 set** | **All four: Study Guide · Briefing · FAQ · Timeline** | One mechanism, four preset definitions. |

---

## Build steps

### 1. Project-output store (SHARED — coordinate)

- Decide once (with Audio Overview): reuse artifacts tagged to the project vs. a
  small `project_outputs` table. NotebookLM stores many outputs per notebook, so a
  light `project_outputs(id, project_id, kind, title, path, created_at, opts)`
  table is probably the right call (also serves Studio / multiple-outputs). DECIDE
  with the Audio Overview plan, don't duplicate.

### 2. Generation endpoint

- `POST /v1/projects/<id>/generate {kind, options}` where `kind` ∈
  `study_guide | briefing | faq | timeline` (later: `audio_overview | flashcards |
  quizzes`). Auth + project-membership check.
- Gather sources via `tool_mempalace_query` (project-scoped) — broad query or
  iterate top drawers for coverage (same open question as Audio Overview's
  whole-project retrieval).
- `background_call(purpose="transform", project=<id>, ...)` with the preset's
  prompt. Write the result as a `.md` artifact, register a `project_outputs` row.
- Return the output id/path; emit an SSE so the UI updates.

### 3. The four preset prompts

Each preset = a prompt + an output structure. Keep them grounded + cite-aware
(reuse the project's citation discipline). Store server-side (so they're tunable
without a frontend deploy — e.g. a `config.json` / `tools_config.json` section or
a presets file).
- **Study Guide** — sections, key terms/definitions, review questions, all cited.
- **Briefing Doc** — executive summary → key points → implications, condensation.
- **FAQ** — grounded Q/A pairs derived from the corpus.
- **Timeline** — chronological events with dates + source refs. **Caveat: only as
  good as dated content in the corpus** — prompt must say "omit if no dated
  events," not invent a timeline.

### 4. UI

- One-click buttons (a "Generate" / Studio section) on the project page, one per
  preset, → call the endpoint, show progress, link the saved output.
- Browse generated outputs per project (ties to the project-output store / future
  Studio surface).
- Follow web/js conventions; gate with `./web/js/js_gate.sh`.

---

## Open items to resolve AT BUILD

1. **Project-output store shape** — SHARED with Audio Overview. Reuse artifacts vs.
   `project_outputs` table. (Lean table.)
2. **Whole-project retrieval** — single broad `mempalace_query` vs. iterate top-N
   drawers for coverage. SHARED question with Audio Overview.
3. **Preset prompt storage** — config section vs. a presets file vs. hardcoded.
   Prefer admin-tunable.
4. **Customization** — NotebookLM lets you steer (focus/length). v1: fixed presets;
   add an optional free-text "focus" param later.
5. **Regeneration / versioning** — re-running a preset: new output vs. overwrite?
   (`artifact_versions` already versions; decide UX.)

## Explicitly OUT of scope (separate Tier 3 items / plans)

- Flashcards / Quizzes (new output TYPES — separate discussion; same endpoint).
- Studio multiple-outputs-per-type management UI (separate; same store).
- Deep / Fast Research source-finding → import (separate).
- Per-preset language selection beyond what the model does (rides translation
  stack later).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new endpoint → `01-api.md`; new project UI →
  `06-user-manual.md` (German); new `project_outputs` storage → `03-storage.md`.
  VERSION bump in two places. python-compile brain.py. Graceful restart (SIGTERM,
  never SIGKILL). Commit to main. `./web/js/js_gate.sh` passes.

## Success criteria

- On a project, clicking any of the four presets generates a grounded, cited `.md`
  output saved to the project, browseable + regenerable.
- Timeline omits (not invents) when the corpus has no dated events.
- The endpoint + output store are SHARED with Audio Overview (not a second path).
- brain.py compiles; version check after restart; js_gate passes.
