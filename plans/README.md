# NotebookLM-Gap Implementation Plans — Index

Plan files for the brain-agent vs. NotebookLM gap-closing effort. Source map:
`../NOTEBOOKLM_GAP_HANDOVER.md` (full 5-tier gap inventory). We walk the tiers
top-down; a plan file is written **only when a feature is greenlit**. Deferred /
rejected features get a one-line note here, not a file.

**▶ BUILD SEQUENCE:** [IMPLEMENTATION_ORDER.md](IMPLEMENTATION_ORDER.md) — the
decided order for the four spec'd features: **Fast Research → Output Presets
(store+endpoint first) → Studio → Deep Research → Inline Citations.**

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
| **Output presets (×4)** | 3 | `spec'd` | [OUTPUT_PRESETS_DETAILED_SPEC.md](OUTPUT_PRESETS_DETAILED_SPEC.md) | Full spec: mockups + W1–W7 + E1–E5. Study Guide/Briefing/FAQ/Timeline → `POST /v1/projects/<id>/generate` → cited `.md`. **Owns the SHARED `project_outputs` store + generate endpoint** (used by Studio + Audio Overview). Lean [plan](OUTPUT_PRESETS_PLAN.md) superseded. |
| **Studio (per-project outputs)** | 3 | `spec'd` | [STUDIO_DETAILED_SPEC.md](STUDIO_DETAILED_SPEC.md) | Full spec: mockups + W1–W8 + E1–E6. Browse/manage over the shared store, grouped by kind, full lifecycle (open/regen/rename/delete), MP3 player case. Lean [plan](STUDIO_PLAN.md) superseded. |
| **★ Research → import (Fast + Deep)** | 3 | `spec'd` | [RESEARCH_IMPORT_DETAILED_SPEC.md](RESEARCH_IMPORT_DETAILED_SPEC.md) | **Most important feature.** Full detailed spec: mockups + end-to-end workflows (W1–W9) + error cases (E1–E8). Fast=search→pick→`web_urls`; Deep=bounded agentic loop→propose sources + cited report→Studio. Lean plan [RESEARCH_IMPORT_PLAN.md](RESEARCH_IMPORT_PLAN.md) superseded. §8 user decisions resolved. |
| **Inline span citations** | 4 | `spec'd` | [INLINE_CITATIONS_DETAILED_SPEC.md](INLINE_CITATIONS_DETAILED_SPEC.md) | Full spec: mockups + W1–W6 + E1–E7. Numbered inline chips + click→open source→highlight cited span, **drawer-anchored**. New work = resolve+store drawer ref at validation + span-highlight viewer. Lean [plan](INLINE_CITATIONS_PLAN.md) superseded. |

## Discussed but not greenlit

- **Video Overview** (Tier 1 #3) — `deferred`. No video-generation model (nor a
  slide-render→video path) is wired into the stack, so it's blocked at the model
  layer regardless of effort. Revisit only if/when such a model is configured.
- **Flashcards & Quizzes** (Tier 3) — `deferred`. User decision: not needed. Would
  ride the shared `generate` endpoint cheaply as static `.md` if revisited; an
  interactive player (the real NotebookLM experience) is the larger build. No plan
  file until wanted.
- **Sidebar summaries** (Tier 4) — `deferred` (already solved). Per-chat LLM
  summaries already exist (generated via `chat_summary_model`, stored on
  `session.summary`, returned by `list_sessions`, searchable). They surface as a
  **title hover-tooltip by deliberate design** (`panels_chats.js:25` — "never
  replaces the title; keeps the list dense"). No real gap. If inline NotebookLM-
  style subtitles are ever wanted, it's a small reversible JS tweak / user toggle —
  but it overrides an intentional UX decision. Not building.
- **Multilingual audio** (Tier 4) — `deferred` (dependency note). Splits in two:
  **chat** multilingual is largely ALREADY shipped (lang follows model + full
  translation stack) — possible minor UX polish only. **Audio** is BLOCKED at the
  TTS layer — the only TTS-capable models are Voxtral (English-only, 10 en_* voices)
  and no multilingual TTS provider is configured. **Unblock condition:** wire a TTS
  provider with non-English voices (e.g. ElevenLabs/Azure/Coqui). When that lands,
  the `AUDIO_OVERVIEW_PLAN.md` voice-roster-per-language fast-follow unblocks with
  NO architecture change. No build plan until the provider exists.

## Tier 5 — not built now (platform/reach; lowest priority for self-hosted)

Skipped 2026-06-03 — none greenlit. Two flagged as **possibly useful later** (the
user's call), the rest deferred as questionable-fit for a self-hosted tool:

- **Real-time collaborative co-editing** — ⭐ *possibly useful later* (user-flagged).
  Sessions/projects are already shareable (user/team/global per
  `[[project_sharing_model_v835]]`); live co-edit is the large add. Not now.
- **Featured / curated notebooks** — ⭐ *possibly useful later* (user-flagged).
  Content/curation surface, not core infra. Not now.
- **Native mobile apps** (iOS/Android) — `deferred`. Large effort; web + Electron +
  Telegram cover reach today. (Note: this is the unblock for Tier 2 live-mic's real
  payoff + Tier 4 audio-on-mobile.)
- **Public "anyone with link" share** — `deferred`. Needs an anonymous-access model;
  current sharing is within-instance (user/team/global) only.
- **Integrations marketplace / one-click connectors** — `deferred`. MCP-via-config
  exists; an in-app marketplace is not core.

## All five NotebookLM gap tiers now walked

Tiers 1–4 worked feature-by-feature (10 scoped/spec'd, 4 deferred). Tier 5 logged
above. Nothing built yet — these are decision/planning artifacts.
