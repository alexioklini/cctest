# NotebookLM-Gap Implementation Plans — Index

Plan files for the brain-agent vs. NotebookLM gap-closing effort. Source map:
`../NOTEBOOKLM_GAP_HANDOVER.md` (full 5-tier gap inventory). We walk the tiers
top-down; a plan file is written **only when a feature is greenlit**. Deferred /
rejected features get a one-line note here, not a file.

**Convention:** one plan file per feature, self-contained enough to build in a
dedicated session. Status values: `scoped` (decided, not started) · `spec'd`
(detailed spec w/ mockups + workflows) · `building` · `done` · `deferred` ·
`rejected`.

| Feature | Tier | Status | Plan | Decision in one line |
|---|---|---|---|---|
| **Audio Overview (podcast)** | 1 #1 | `scoped` | [AUDIO_OVERVIEW_PLAN.md](AUDIO_OVERVIEW_PLAN.md) | Build both surfaces (tool + project button); NotebookLM-style controls; **English-only audio** (Voxtral has no non-EN voices); default hosts Oliver+Jane (en_gb). |
| **Interactive "Join" mode** | 1 #2 | `scoped` | [JOIN_MODE_PLAN.md](JOIN_MODE_PLAN.md) | Greenlit — **not** HIGH effort: live mic/VAD/playback-mute stack already exists (`translation_live.js`). Answer in-character (two host voices), spoken + typed interrupt. Depends on Audio Overview shipping. |
| **Mind Map (KG viz)** | 1 #4 | `scoped` | [MIND_MAP_PLAN.md](MIND_MAP_PLAN.md) | Greenlit — beats NLM (real typed KG triples vs embeddings). New full-view page, lightweight graph lib (⚠️ vendor UMD build — no bundler), click-node→grounded chat. Needs one new whole-graph read endpoint. |
| **Video / YouTube ingest** | 2 | `scoped` | [VIDEO_INGEST_PLAN.md](VIDEO_INGEST_PLAN.md) | Greenlit — cheap: `_sync_project_web_urls` is a near-exact template, transcript=just markdown→existing mining. New `video_urls` field, captions→STT fallback, any yt-dlp site. ⚠️ yt-dlp operationally fragile — fail loud. |
| **Audio file as a source** | 2 | `scoped` | [AUDIO_SOURCE_PLAN.md](AUDIO_SOURCE_PLAN.md) | Greenlit — **smallest gap**: just add an audio branch to the `doc_convert` pre-pass (same mechanism as PDF→.md companion). transcribe_audio already exists; mining/hash/stale all reused. No new field/endpoint. GDPR gate inherited free. |
| **Live mic input to chat** | 2 | `scoped` | [LIVE_MIC_CHAT_PLAN.md](LIVE_MIC_CHAT_PLAN.md) | Greenlit (full live/streaming) — composer mic over the existing `/v1/translate/live/*` stack in transcribe-only mode. Low priority (real payoff is mobile, which doesn't exist yet) — sequence after higher-value items. |
| **Output presets (×4)** | 3 | `scoped` | [OUTPUT_PRESETS_PLAN.md](OUTPUT_PRESETS_PLAN.md) | Greenlit — Study Guide/Briefing/FAQ/Timeline. Server endpoint `POST /v1/projects/<id>/generate` → grounded turn → saved `.md` artifact. ⚠️ **SHARED endpoint + project-output store with Audio Overview** (+ later Flashcards/Quizzes) — build once. |
| **Studio (per-project outputs)** | 3 | `scoped` | [STUDIO_PLAN.md](STUDIO_PLAN.md) | Greenlit — thin browse/manage UI over the shared `project_outputs` store. List endpoint + outputs view grouped by kind + full lifecycle (open/regenerate/rename/delete). Depends on the store existing. |
| **★ Research → import (Fast + Deep)** | 3 | `spec'd` | [RESEARCH_IMPORT_DETAILED_SPEC.md](RESEARCH_IMPORT_DETAILED_SPEC.md) | **Most important feature.** Full detailed spec: mockups + end-to-end workflows (W1–W9) + error cases (E1–E8). Fast=search→pick→`web_urls`; Deep=bounded agentic loop→propose sources + cited report→Studio. Lean plan [RESEARCH_IMPORT_PLAN.md](RESEARCH_IMPORT_PLAN.md) superseded. **§8 has open questions for the user.** |

## Discussed but not greenlit

- **Video Overview** (Tier 1 #3) — `deferred`. No video-generation model (nor a
  slide-render→video path) is wired into the stack, so it's blocked at the model
  layer regardless of effort. Revisit only if/when such a model is configured.
- **Flashcards & Quizzes** (Tier 3) — `deferred`. User decision: not needed. Would
  ride the shared `generate` endpoint cheaply as static `.md` if revisited; an
  interactive player (the real NotebookLM experience) is the larger build. No plan
  file until wanted.

## Not yet discussed

Tier 4 (multilingual audio, span citations, sidebar summaries) · Tier 5 (mobile,
public links, co-edit, featured, marketplace).
