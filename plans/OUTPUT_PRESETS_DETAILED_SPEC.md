# Output Presets — DETAILED DESIGN SPEC

**Status:** DETAILED SPEC (mockups + end-to-end workflows + edge cases).
PRE-IMPLEMENTATION — nothing built.
**Supersedes:** `OUTPUT_PRESETS_PLAN.md` (lean scope; its locked decisions hold).
**Parent:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3). brain-agent VERSION: 9.62.0.

> Mockups are intent (layout/states/data-flow), not pixel-final. This spec is the
> first of a coupled trio — read with `STUDIO_DETAILED_SPEC.md` (browses the same
> store) and note the SHARED endpoint/store called out in §1.

---

## 0. Verified code anchors

| Capability | Where | Note |
|---|---|---|
| Generation call | `sidecar_proxy.background_call(*, messages, model, project, purpose="transform", max_rounds, ...)` (`handlers/sidecar_proxy.py:834`) | Synchronous one-shot LLM transform; takes `project=` for scoping. |
| Project-scoped retrieval | `tool_mempalace_query` (`engine/mempalace_glue.py:398`) | Force-scoped to `project__<id>`; refuses outside a project. |
| Artifact write | `_get_artifact_session_folder` (`engine/tool_exec.py:88`) + `engine/tools/file_tools.py:176` | Writes register as `artifact_versions` rows + emit `artifact_updated` SSE. |
| Artifact browse/content/download | `admin_artifacts.py:1397/1473/1522` | Existing surface Studio reuses. |
| Citation discipline | research_mode / Topic B (`engine/prompt_build.py`) + validator (`handlers/chat.py:2371`) | Presets reuse this so outputs are grounded + cited. |

---

## 1. Feature summary & SHARED infrastructure

Four one-click grounded outputs over a project's sources: **Study Guide ·
Briefing Doc · FAQ · Timeline**. Each = canned grounded prompt + output format →
cited `.md` saved as a project output.

**⚠️ SHARED — build once, used by 3+ features:**
- **Endpoint** `POST /v1/projects/<id>/generate {kind, options}` — dispatches to
  preset generators (this spec), Audio Overview, later Flashcards/Quizzes.
- **Store** `project_outputs` table — holds many outputs/project; browsed by Studio
  (`STUDIO_DETAILED_SPEC.md`).

This spec OWNS the definition of the endpoint + store (others reference it).

**Locked decisions** (from the lean plan): server endpoint · saved `.md` artifact ·
all four presets in v1.

---

## 2. The `project_outputs` store (defined here, shared)

Table `project_outputs`:
```
id            TEXT PRIMARY KEY     -- uuid
project_id    TEXT  (indexed)
kind          TEXT                 -- study_guide|briefing|faq|timeline|audio_overview|research_report|...
title         TEXT                 -- editable (Studio rename)
path          TEXT                 -- artifact path (.md / .mp3)
artifact_id   TEXT                 -- link to artifact_versions for versioning
opts          TEXT (JSON)          -- the generation options (for regenerate)
status        TEXT                 -- generating|ready|error
created_at    INTEGER
created_by    TEXT  (user_id)
```
- `status=generating` row inserted at request time; flipped to `ready`/`error` on
  completion. Studio + the preset UI poll/SSE on it.
- `opts` makes regenerate reproducible (Studio "regenerate" replays it).

---

## 3. MOCKUPS

### 3.1 Generate panel — on the project page (a "Generate" / Studio section)

```
┌─ Generate from sources ──────────────────────────────────────┐
│  Create a grounded output from this project's 14 sources.    │
│                                                              │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│   │ 📖        │  │ 📋        │  │ ❓        │  │ 🕒        │    │
│   │ Study     │  │ Briefing │  │ FAQ      │  │ Timeline │    │
│   │ Guide     │  │ Doc      │  │          │  │          │    │
│   │ [Generate]│  │[Generate]│  │[Generate]│  │[Generate]│    │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│   ▸ Options (optional)                                       │  ← collapsed by default
│     Focus: [____________]   Length: ( )Short (•)Std ( )Long  │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Generating state (after a card click)

```
┌─ 📖 Study Guide — generating… ───────────────────────────────┐
│   ⟳ Reading sources and writing…  ~20–40s     [ Cancel ]     │
│   (You can leave this — it'll appear in Studio when done.)    │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Done — inline result + saved to Studio

```
┌─ ✅ Study Guide ready ────────────────────────────────────────┐
│   📄 "Study Guide — EU AI Act Compliance"   18 citations      │
│      Saved to Studio · just now                               │
│                                                              │
│   [ Open ]   [ Regenerate ]   [ Go to Studio ]                │
│                                                              │
│   ── preview ───────────────────────────────────────────────│
│   ## Key Concepts                                            │
│   1. **GPAI** — general-purpose AI models… [Quelle: art_53…] │
│   …                                                          │
└──────────────────────────────────────────────────────────────┘
```

### 3.4 Empty-project / no-sources state

```
┌─ Generate from sources ──────────────────────────────────────┐
│   ⓘ This project has no sources yet.                          │
│     Add files, web URLs, or run Research first.               │
│     [ Go to Sources ]                                         │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. END-TO-END WORKFLOWS

Notation: **U**=user · **FE**=frontend · **BE**=backend.

### W1 — Generate a preset (happy path)
1. U opens project → Generate section → clicks **Study Guide** (optionally sets
   Focus/Length).
2. FE → `POST /v1/projects/<id>/generate {kind:"study_guide", options:{focus,length}}`.
3. BE inserts `project_outputs` row `status=generating`, returns `{output_id}`.
   FE shows 3.2 and polls/SSE on the output.
4. BE: `tool_mempalace_query` (project-scoped) gathers sources → one
   `background_call(purpose="transform", project=<id>, ...)` with the preset prompt
   + research-mode citation discipline → writes a cited `.md` artifact → updates the
   row `status=ready, path, artifact_id, title`.
5. FE shows 3.3 (preview + Open/Regenerate/Go-to-Studio). Output now in Studio. ✔

### W2 — Generate with no sources
- 3.4 empty state; cards disabled. No call. ✔

### W3 — Timeline with no dated content
- Preset prompt instructs **omit, don't invent**. BE produces a Timeline that says
  "no datable events found in the sources" rather than fabricating. Row still
  `ready` (a valid, honest output). ✔

### W4 — User cancels mid-generation
- FE cancel → BE marks the row `error`/removes it; partial artifact discarded. ✔

### W5 — Generation fails (LLM/quota/GDPR)
- BE sets `status=error` with a reason; FE shows an inline error + retry. Routes
  through normal cost/quota + GDPR seams (a quota block = a clean error, not a
  crash). ✔

### W6 — Regenerate (from 3.3 or Studio)
- Replays the stored `opts` via the same endpoint → a NEW `project_outputs` row
  (history preserved; Studio shows both). ✔ (Versioning UX detail → Studio spec.)

### W7 — Concurrent generations
- Multiple presets fired together → independent rows, each generating; FE tracks
  each by `output_id`. No shared mutable state. ✔

---

## 5. EDGE CASES (E-series)

- **E1 Project deleted mid-generation** — BE aborts cleanly; orphan row/artifact
  cleaned by the existing project-delete soft-trash path.
- **E2 Very large corpus** — retrieval is bounded (broad `mempalace_query` /
  top-N drawers); note coverage if truncated (no silent cut — repo rule).
- **E3 Non-member user** — endpoint enforces project-membership; 403 otherwise.
- **E4 Model emits no citations** — validator flags it (existing path);
  output still saved but UI can badge "low citation coverage."
- **E5 Duplicate rapid clicks** — debounce on the FE; BE idempotency optional
  (each click = a new output is acceptable, but guard accidental double-fire).

---

## 6. API CONTRACT

`POST /v1/projects/<id>/generate`
- body: `{kind: study_guide|briefing|faq|timeline, options?: {focus?:str,
  length?: short|std|long}}`
- 200: `{output_id, status:"generating"}`
- progress: SSE `output_updated` (or poll `GET /v1/projects/<id>/outputs` — defined
  in Studio spec) → `status: ready|error`, `path`, `title`.
- auth + project-membership required.

**Preset prompts** stored server-side (tunable without a frontend deploy — a
`tools_config.json` section or a presets file). Each grounded + cite-aware:
- **study_guide** — concepts · key terms/definitions · review questions, all cited.
- **briefing** — exec summary → key points → implications.
- **faq** — grounded Q/A pairs.
- **timeline** — chronological dated events + source refs; **omit if none dated**.

---

## 7. BUILD PHASING

1. **Store + endpoint** (`project_outputs` table + `POST …/generate`) — SHARED
   foundation; coordinate with Audio Overview + Studio.
2. **The four preset generators** (prompts + retrieval + write).
3. **Generate UI** (cards + options + generating/done states).

---

## 8. OPEN ITEMS (decide at build)

1. Preset prompt storage location (config section vs presets file) — prefer
   admin-tunable.
2. Length → concrete word/section targets.
3. Whole-project retrieval (broad query vs iterate top-N drawers) — SHARED with
   Audio Overview + Research.
4. SSE vs poll for the output status (reuse `artifact_updated` or a new
   `output_updated`).

## 9. Repo-convention obligations
brain-agent-guide: endpoint → `01-api.md`; UI → `06-user-manual.md` (German);
`project_outputs` → `03-storage.md`. VERSION ×2. compile brain.py. SIGTERM-only
restart. commit→main. js_gate green.

## 10. Success criteria
Four preset cards each produce a grounded, cited `.md` saved to the project store,
browseable in Studio; Timeline omits-not-invents; cancel/fail/regenerate/concurrent
all behave per W- and E-series; endpoint + store are SHARED (single path).
