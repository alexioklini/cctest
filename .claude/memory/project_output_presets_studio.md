---
name: project_output_presets_studio
description: v9.63-9.64 (2026-06-03) Output Presets + Studio built — shared project_outputs store + generate endpoint + Generate UI + Studio browse; Phases 1+2 of the four-feature order
metadata: 
  node_type: memory
  type: project
  originSessionId: 8de38dd2-48cf-4103-bc5a-5bf45893b489
---

Output Presets + Studio shipped (Phases 1+2 of `plans/IMPLEMENTATION_ORDER.md`'s four-feature order: Presets→Studio→Deep Research→Inline Citations).

**v9.63.0** — SHARED foundation (reused by Studio/Audio Overview/Deep Research, build-once):
- `project_outputs` table (`server_lib/db.py`, modeled on `background_tasks`): generating→ready/error, `opts` JSON, `path`/`artifact_id`, boot crash-reconcile. CRUD on ChatDB (@_db_safe).
- `engine/output_presets.py` (4 canned grounded prompts + Topic-B citation/omit-don't-invent discipline) + `engine/output_gen.py` (daemon worker: project-scoped `mempalace_query` → ONE `background_call(purpose="transform")` → cited `.md` under `<pdir>/outputs/` → registered artifact under synthetic session `output-<id>` → row flipped).
- `POST /v1/agents/<a>/projects/<name>/generate {kind,options}` + `GET .../outputs[/<id>]`.

**v9.64.0** — Studio UI + mutation endpoints:
- `POST .../outputs/<id>/rename` + `DELETE .../outputs/<id>` (row+artifact rows+file; 409 while generating). `ChatDB.delete_artifact_rows`.
- `web/js/panels_studio.js` (NEW, +19 globals, baseline 1070→1089): "Studio" tab on project page = Generate panel (4 cards + Fokus/Länge) + browse (grouped by kind, 2.5s poll, open .md modal via getArtifactContent+renderMarkdown, ⋯ menu).

**GOTCHAS learned (verify-as-you-go):**
- `ChatDB` is NOT on `brain` — import `from server_lib.db import ChatDB` (the Explore report was wrong; same class as v9.21.5 helpdesk fix).
- `tool_mempalace_query` returns `{drawers:[...], count, total_before_filter}` — NOT `{results:[...]}`.
- has-sources check must include `input_folders` (canary `kg-real-policies` has chunks=0, sources are mined input folders).
- **MUST restart before testing route changes** — a DELETE `.../outputs/<id>` on an UNrestarted server fell through to `_handle_project_delete` and soft-trashed the whole `kg-real-policies` project (restored from `agents/.trash/`). New DELETE route is ordered before + gated stricter than the catch-all.
- Endpoint convention is `/v1/agents/<a>/projects/<name>/…` (NOT the specs' loose `/v1/projects/<id>` shorthand).

**NOT YET (next):** Phase 3 Deep Research (report→project_outputs row), Phase 4 Inline Citations (chips). Studio viewer still needs an MP3 audio case when Audio Overview is built. Preset prompts are in the code module (admin-tunable config = deferred open-item). See [[project_summary]].
