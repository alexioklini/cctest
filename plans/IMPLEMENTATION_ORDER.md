# Implementation Order — the four spec'd features

**Decided:** 2026-06-03. Covers the four features with detailed specs: **Output
Presets · Studio · Research (★ marquee) · Inline Citations**. brain-agent VERSION
at decision: 9.62.0.

This is the build sequence. It respects the hard dependencies between the specs
and front-loads value where a piece is independent.

---

## The dependency facts that drive the order

1. **Output Presets owns the shared infra** — the `project_outputs` table + `POST
   /v1/projects/<id>/generate` endpoint (`OUTPUT_PRESETS_DETAILED_SPEC.md §2`).
   Studio browses that store; Deep Research saves its report into it; Audio
   Overview (later) writes to it too. → Presets' shared infra is FIRST.
2. **Studio depends on the store** — pure presentation; needs outputs to exist.
3. **Research splits in two:**
   - **Fast Research** — search → pick → append to `web_urls`. **No store
     dependency** (imports sources, doesn't generate outputs). Can ship early/
     standalone. The import seam already exists.
   - **Deep Research** — bounded agentic loop → cited `research_report` saved to
     the store. **Depends on the store + Studio** to browse the report.
4. **Inline Citations is independent infra-wise** (extends the validator + chat
   render, no store dependency) but its VALUE peaks once cited documents exist to
   click through → best placed AFTER the generators (presets / Deep reports).

---

## THE ORDER

### Phase 0 — Fast Research (early standalone win) — OPTIONAL FRONT-LOAD
- `RESEARCH_IMPORT_DETAILED_SPEC.md` Phase 1 only.
- Topic → `/v1/web/search` (+exa) → SERP pick (reuse `panels_websuche.js`) →
  append selected to project `web_urls` (existing whitelist path) → existing sync
  daemon mines them.
- **Why first:** zero store dependency, the entire import half already exists,
  delivers "I can finally add sources by searching" value immediately.
- *Skip/merge into Phase 3 if you'd rather not context-switch — it's the one piece
  that can float.*

### Phase 1 — Output Presets (the foundation) ⭐ START HERE if not doing Phase 0
- `OUTPUT_PRESETS_DETAILED_SPEC.md`. Build in its own §7 sub-order:
  1. **Shared infra**: `project_outputs` table + `POST …/generate` endpoint. ← the
     single highest-leverage deliverable; unblocks Studio + Deep Research + Audio.
  2. Preset generators (start with ONE — Study Guide — end-to-end to prove the
     pipeline, then add Briefing/FAQ/Timeline).
  3. Generate UI (cards + options + generating/done states).
- **Exit when:** clicking a preset produces a cited `.md` saved to the store.

### Phase 2 — Studio (view what Phase 1 produces)
- `STUDIO_DETAILED_SPEC.md`. `GET /outputs` + grouped browse + open (.md viewer; add
  the MP3 case for later Audio Overview) + lifecycle (regenerate/rename/delete).
- **Why here:** the moment presets generate outputs, you need somewhere to see +
  manage them. Pairs immediately after Phase 1.
- **Exit when:** a project's outputs are browseable + manageable end-to-end.

### Phase 3 — Deep Research (the marquee agentic loop)
- `RESEARCH_IMPORT_DETAILED_SPEC.md` Phases 2–3. Bounded background task (decompose
  → multi-search → fetch/read → rank) → structured+cited `research_report` saved as
  a `project_outputs` row → propose-approve sources UI + live progress.
- **Why here:** Deep's report lands in the store (Phase 1) and is browsed in Studio
  (Phase 2), so both exist before Deep needs them. Budget = generous (~60 fetches /
  ~4 min / ~80k tok), enforced + visible; in-app completion notification.
- **Exit when:** topic → bounded run → proposed sources + a cited report in Studio.

### Phase 4 — Inline Citations (polish the cited docs everything now produces)
- `INLINE_CITATIONS_DETAILED_SPEC.md`. Extend the validator to resolve+store the
  drawer anchor → numbered inline chips → click→open-source→highlight-span viewer.
- **Why last:** independent of the store, and its value is highest once presets +
  Deep reports are generating cited documents to click through. Improves every
  cited surface at once (chat answers, preset outputs, research reports).
- **Exit when:** chips render inline + jump to the drawer-anchored passage.

---

## Summary line

**Fast Research (front-loaded) → Output Presets (store+endpoint first) → Studio →
Deep Research → Inline Citations.**

Strict-dependency core = **Presets → Studio → Deep Research → Citations**; Fast
Research floats to the front because it has no store dependency.

---

## Cross-cutting reminders (apply every phase)

- The `project_outputs` store + `/generate` endpoint are **built once in Phase 1**
  and reused — do NOT fork a second generation/storage path in Studio, Research, or
  Audio Overview.
- Per-phase: update the `brain-agent-guide` skill in the SAME change (endpoints →
  `01-api.md`, UI → `06-user-manual.md` in German, store → `03-storage.md`,
  internals → `05-internals.md`); VERSION bump ×2; python-compile brain.py; graceful
  restart (SIGTERM, never SIGKILL); commit to main; `./web/js/js_gate.sh` green.
- Verify each phase end-to-end (its spec's success criteria) before starting the
  next — don't continue from a state you can't describe.

## Not in this order (separate efforts)
Audio Overview + Join mode + Mind Map + Video/Audio ingest + Live mic are scoped
plans, not in this four-feature order. Audio Overview, when built, reuses the Phase
1 store/endpoint + the Phase 2 Studio MP3 case.
