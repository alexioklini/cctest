# Studio — DETAILED DESIGN SPEC

**Status:** DETAILED SPEC (mockups + workflows + edge cases). PRE-IMPLEMENTATION.
**Supersedes:** `STUDIO_PLAN.md` (lean scope; locked decisions hold).
**Reads with:** `OUTPUT_PRESETS_DETAILED_SPEC.md` (defines the `project_outputs`
store + `generate` endpoint Studio browses) + `AUDIO_OVERVIEW_PLAN.md` (MP3
outputs). **Parent:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 3). VERSION: 9.62.0.

> Studio is a **presentation layer** over the shared store — no generation logic.
> Mockups are intent, not pixel-final.

---

## 0. Verified code anchors

| Capability | Where | Note |
|---|---|---|
| Output store | `project_outputs` table (defined in `OUTPUT_PRESETS_DETAILED_SPEC.md §2`) | `id, project_id, kind, title, path, artifact_id, opts, status, created_at`. |
| Artifact content/download | `admin_artifacts.py:1473` (`/v1/artifacts/<id>/content`), `:1522` (download) | Reuse to open an output. |
| Artifact viewer | `web/js/panels_artifacts.js:525` (`renderArtifactContent`) | Reuse for `.md`; ADD an audio case for MP3. |
| Versioning | `artifact_versions` (+ `artifact_updated` SSE) | Regenerate semantics build on this. |
| Existing browse-grid pattern | `panels_artifacts.js` (`_browseArtifactsFilter`, source/type filters) | Mirror its filter UI per-project. |

---

## 1. Feature summary & locked decisions

Per-project surface to **browse + manage** generated outputs (audio overviews,
study guides, briefings, FAQs, timelines, research reports — many per project,
several of the same type). It is a view over `project_outputs`.

**Locked:** thin standalone surface · **full lifecycle: open · regenerate ·
rename · delete** · gated on the store existing.

---

## 2. MOCKUPS

### 2.1 Studio tab — grouped by kind

```
┌─ 🎬 Studio · EU AI Act Compliance ───────────────────────────┐
│  9 outputs                       Filter: [ All ▾ ]  [+ New ▾] │  ← "+ New" = jump to Generate
│                                                              │
│  🎧 Audio Overviews (2)                                       │
│   ┌────────────────────────────┐ ┌────────────────────────┐ │
│   │ ▶ GPAI obligations — pod   │ │ ▶ Risk tiers — podcast │ │
│   │   4:12 · 2 days ago        │ │   3:48 · today         │ │
│   │   [Open][Regen][⋯]         │ │   [Open][Regen][⋯]     │ │
│   └────────────────────────────┘ └────────────────────────┘ │
│                                                              │
│  📖 Study Guides (1)   📋 Briefings (2)   ❓ FAQ (1)          │
│   ┌────────────────────────────┐ …                          │
│   │ 📄 Study Guide — EU AI Act │                            │
│   │   18 cites · today         │                            │
│   │   [Open][Regen][⋯]         │                            │
│   └────────────────────────────┘                            │
│                                                              │
│  🔬 Research Reports (1)                                      │
│   ┌────────────────────────────┐                            │
│   │ 📄 GPAI Transparency — synth │  ⟳ generating…           │ │  ← live status row
│   └────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```
- Grouped by `kind` headers with counts (the "many of same type" requirement).
- `[⋯]` menu = Rename · Delete · Download.
- `Filter: All ▾` = the existing browse-grid filter pattern, scoped to this
  project + filterable by kind.
- A `generating`/`error` row shows live status.

### 2.2 Open an output (.md → viewer; .mp3 → player)

```
┌─ 📄 Study Guide — EU AI Act Compliance ──────────────────────┐
│  [ Download ]  [ Regenerate ]  [ Rename ]  [ Delete ]   ✕    │
│  ─────────────────────────────────────────────────────────── │
│  ## Key Concepts                                            │
│  1. **GPAI**… [Quelle: art_53 — "…"]   ← inline chips (spec 3)│
│  …                                                          │
└──────────────────────────────────────────────────────────────┘

  (MP3 output)
┌─ 🎧 GPAI obligations — podcast ──────────────────────────────┐
│  ▶ ──●────────────────  1:46 / 4:12     [ Download ] [ ⋯ ]    │
│  Hosts: Oliver & Jane · generated from 14 sources            │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 Rename (inline)

```
   │ 📄 [ Study Guide — EU AI Act______ ]  [✓] [✕]  │   ← inline edit of title
```

### 2.4 Delete confirm

```
┌─ Delete output? ─────────────────────────────────────────────┐
│  "Study Guide — EU AI Act Compliance"                        │
│  Removes the output and its file. This can't be undone.      │
│                            [ Cancel ]   [ Delete ]            │
└──────────────────────────────────────────────────────────────┘
```

### 2.5 Empty Studio

```
┌─ 🎬 Studio ──────────────────────────────────────────────────┐
│   No outputs yet.                                            │
│   Generate a Study Guide, Briefing, Audio Overview, and more │
│   from this project's sources.        [ Generate → ]         │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. END-TO-END WORKFLOWS

### W1 — Browse
1. U opens project → Studio tab.
2. FE → `GET /v1/projects/<id>/outputs` → rows newest-first.
3. FE groups by `kind` (2.1), renders cards, shows live status for
   `generating`/`error` rows (poll/SSE). ✔

### W2 — Open `.md`
- Click Open → `GET /v1/artifacts/<artifact_id>/content` → `renderArtifactContent`
  (2.2). Inline citation chips render per `INLINE_CITATIONS_DETAILED_SPEC.md`. ✔

### W3 — Open `.mp3` (audio overview)
- Click ▶ → audio player (2.2 bottom). Needs an **audio case added** to
  `renderArtifactContent` (currently text/image). ✔

### W4 — Regenerate
1. `[Regen]` → `POST /v1/projects/<id>/generate` with the row's stored `kind` +
   `opts`.
2. **Locked semantics: NEW output row** (history visible) — the old one stays.
   (Alternative: version-in-place via `artifact_versions`; we choose new-row for
   clearer history. Confirm at build.)
3. New row appears `generating` → `ready`. ✔

### W5 — Rename
- `[⋯] → Rename` → inline edit (2.3) → `PATCH /v1/projects/<id>/outputs/<oid>
  {title}`. Updates the row only (file untouched). ✔

### W6 — Delete
- `[⋯] → Delete` → confirm (2.4) → `DELETE /v1/projects/<id>/outputs/<oid>` →
  removes the row AND its artifact file (reuse the artifact-delete path — **no
  orphaned files**). ✔

### W7 — Download
- `[⋯] → Download` → `GET /v1/artifacts/<artifact_id>/download`. ✔

### W8 — Watch a generation complete in Studio
- A generation started elsewhere (Generate panel / Audio Overview) shows as a
  `generating` row; Studio updates it to `ready` live (SSE/poll). ✔

---

## 4. EDGE CASES

- **E1 Output file missing on disk** (manual deletion / failed write) — row shows
  `⚠ file missing`; Open disabled; offer Delete-row to clean up.
- **E2 Regenerate while original still generating** — allowed; independent rows.
- **E3 Non-member user** — `/outputs` + manage endpoints enforce membership; 403.
- **E4 Many outputs (50+)** — paginate / lazy-load per kind group; the count
  headers stay accurate.
- **E5 Delete an output mid-generation** — block delete while `status=generating`
  (or cancel-then-delete); don't delete a file being written.
- **E6 MP3 too large to stream inline** — fall back to a download link.

---

## 5. API CONTRACT (Studio-owned endpoints)

- `GET /v1/projects/<id>/outputs` → `[{id, kind, title, path, artifact_id, status,
  created_at, created_by}]`, newest-first. (Generation/regeneration = the SHARED
  `POST …/generate` from the presets spec — NOT redefined here.)
- `PATCH /v1/projects/<id>/outputs/<oid> {title}` → rename.
- `DELETE /v1/projects/<id>/outputs/<oid>` → delete row + file.
- All require project membership.

---

## 6. BUILD PHASING
1. `GET /outputs` + Studio browse view (grouped, filtered, live status).
2. Open (`.md` viewer reuse + **MP3 audio case**).
3. Manage: rename (`PATCH`) · delete (`DELETE` + file) · download · regenerate
   (calls shared endpoint).

## 7. OPEN ITEMS (decide at build)
1. Regenerate = new row (chosen) vs version-in-place — confirm + UX.
2. Endpoint prefix `/v1/projects/<id>/outputs` vs `/v1/studio/*` — keep consistent
   with the generate endpoint.
3. Grouping vs single filtered grid — mock shows grouped; the existing grid is a
   filter pattern to mirror.
4. Audio inline-player vs download-only for large MP3.

## 8. Repo-convention obligations
brain-agent-guide: endpoints → `01-api.md`; UI → `06-user-manual.md` (German);
store already documented by the presets spec → `03-storage.md` cross-ref. VERSION
×2. compile brain.py. SIGTERM-only. commit→main. js_gate green (new JS globals
counted).

## 9. Success criteria
Studio lists a project's outputs grouped by kind (multiple-of-type clean), each
openable (md viewer / audio player), regenerable (new row), renamable, deletable
(no orphan files); generating/error rows live-update; reuses the SHARED store +
generate endpoint; W- and E-series behave as specified; js_gate + compile + version
check pass.
