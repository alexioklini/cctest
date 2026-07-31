---
name: project_deep_research
description: "v9.65.0 (2026-06-03) Deep Research (marquee agentic loop) + Fast Research built — Phase 3 of the four-feature order; one project \"Research\" tab, two modes, report→Studio"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8de38dd2-48cf-4103-bc5a-5bf45893b489
---

Deep + Fast Research shipped (Phase 3 of `plans/IMPLEMENTATION_ORDER.md`; the user-designated "most important feature"). Follows Output Presets + Studio ([[project_output_presets_studio]]).

**Engine** (`engine/deep_research.py`): DETERMINISTIC orchestration (CLAUDE.md rule 5), LLM at exactly 3 judgment points — decompose topic→≤8 sub-questions, rank/select fetched candidates, grounded cited synthesis. Plain code: multi-search (searxng/exa merged+deduped vs web_urls), web_fetch top candidates within FETCH budget, dedup, budget accounting. Synthesis prepends `render_research_mode_disciplines()` (REFUSAL/PRECISION/CITATION) → grounded, verbatim `[Quelle:…]`, omit-don't-invent. Saved via the SHARED `output_gen.save_report_output(kind=research_report)` → Studio browses it with zero new code. Budget default 60 fetches/80k tok/8 rounds, enforced+visible (W8), GDPR-gated (E5), cooperative cancel via `research_runs.cancel` (E3), boot reconcile.

**Store**: `research_runs` table (the RUN record: status/phase/progress JSON/budget JSON/report_output_id/proposed JSON/cancel) + CRUD. The REPORT itself is a `project_outputs` row (kind=research_report).

**Endpoints** (`handlers/projects.py` + server.py): GET `research/backends` (E1 gate), POST `research/search` (Fast: search+dedup, SERP cap 30), POST `research/deep` (spawn→{run_id,budget}), GET `research/runs/<id>` (poll), POST `research/runs/<id>/cancel`. Import = existing `update_project` web_urls append (NO new endpoint).

**Frontend**: `web/js/panels_research.js` (+19 globals, baseline 1089→1108). "🔍 Research" tab on project page — topic + Fast/Deep toggle + backend checkboxes; Fast SERP-pick→import; Deep live phase+budget progress (2.5s poll, cancel) → propose-approve (report→Studio + deduped checkable sources). No auto-import.

**KEY FACTS:**
- `background_call`/`gdpr_pick_model_for_background` `purpose=` MUST be in `brain._VALID_PURPOSES` = (interactive, transform, memory_summary, research_minimal, helpdesk). Custom purpose strings crash `resolve_active_tools`. Used `transform` (returns [] tools) for all synthesis calls. (Hit this bug first try.)
- `tool_searxng_search`/`exa_search` return `{results:[{title,link,score,snippet}]}`; `tool_web_fetch` returns `_ok` envelope `{content,fetch_method,status,url,error?}` (just json.loads).
- This install: SearXNG only, NO Exa key (`research/backends`→["searxng"]). Exa merge path code-exercised, needs a key to live-verify.
- Verified e2e: full Deep run (plan 8 sub-qs→16 candidates→6 fetched within budget→15-citation German report saved as research_report→3 proposed sources, honest coverage note), Fast search, cancel.

**NOT YET:** Phase 4 — Inline Citations (clickable chips). The four-feature order is then COMPLETE. See [[project_summary]].
