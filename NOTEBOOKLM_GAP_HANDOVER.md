# NotebookLM Gap Analysis — Handover for a Fresh Session

**Purpose:** Pick up the brain-agent vs. Google NotebookLM gap analysis in a new
session and decide what (if anything) to build. This doc carries the conclusions
so you don't re-derive them.

**Status:** ANALYSIS DONE, nothing built. No code written for any gap. This is a
decision/planning artifact. The "gap tiers" section is the COMPLETE inventory of
every gap the analysis found (5 tiers); "Recommended order" is a separate
opinionated subset — decide from the full list, not just the recommendation.

**Date of analysis:** 2026-06-03 · brain-agent VERSION at handover: **9.62.0**

---

## TL;DR

brain-agent already matches or beats NotebookLM on the *core* loop (chat-with-docs,
source-grounded answers, citations + a server-side citation validator, project
workspaces, web sources, team sharing) and is **ahead** on: knowledge-graph
triples (NotebookLM is embedding-only), cross-encoder reranking, code execution +
artifacts, image generation, GDPR/PII scanning, ARL document classification, and
local-model support.

The gaps are a handful of NotebookLM **signature output features** that genuinely
don't exist here. They're ranked in tiers below. The single highest
impact-to-effort item is **Audio Overview (podcast) generation** — and the two
hard pieces (source retrieval + TTS) already exist in the codebase.

---

## What already exists in the repo (so we build on, not rebuild)

Verified at handover (cite these when scoping):
- **TTS endpoint**: `handlers/translate.py` — `POST /v1/translate/tts` (Voxtral
  `voxtral-mini-tts`, returns MP3 bytes) + `GET /v1/translate/tts/voices`.
  NOT yet wired as an agent tool or to documents.
- **Audio transcription**: `transcribe_audio` tool (`engine/tool_schemas.py`) —
  Voxtral/Whisper; file uploads only, NOT a project-mining source path.
- **KG triples**: `mempalace_kg_query` / `mempalace_kg_search` over a per-project
  `knowledge_graph.sqlite3` — the natural backing for a mind-map.
- **Retrieval/grounding**: MemPalace per-wing collections (v9.62.0), cross-encoder
  rerank, citation validator + re-round (research_mode / Topic A/B).
- **Projects**: per-project input folders + web URLs + instructions + sharing.
- **NOT present**: YouTube/video ingest (no yt-dlp anywhere), audio-as-a-source,
  podcast/audio-overview generation, mind-map, study-guide/briefing/FAQ/timeline
  presets, video overview, native mobile apps, public share links.

---

## The gap tiers (the thing to discuss)

COMPLETE inventory of every gap the 2026-06-03 analysis surfaced (NotebookLM
feature research × brain-agent capability inventory). The tiering is by
impact-to-effort; it is NOT a filtered "what I'd build" list — see the separate
"Recommended order" section below for the opinionated subset. "Effort" is a rough
first guess to be validated when scoped.

### Tier 1 — Signature outputs, genuinely absent (highest value)

| Gap | NotebookLM | brain-agent today | Effort / notes |
|---|---|---|---|
| **Audio Overview (podcast)** | Two AI hosts discuss your sources | TTS endpoint exists, but NO dialogue-script generation, NO two-voice render, NOT wired to documents | **MEDIUM. Top pick.** Missing middle = (1) LLM pass writes a 2-host script from project sources, (2) stitch step alternates two Voxtral voices. Both endpoints exist. The feature users will *name*. |
| **Audio Overview — interactive "Join" mode** | Tap to interrupt the hosts, ask a question answered from sources, playback resumes | None | **HIGH effort.** NotebookLM's signature differentiator. Realistically a v2 on top of basic Audio Overview. |
| **Video Overview** | Narrated AI slides w/ extracted images/quotes/numbers; "cinematic" tier | None | **HIGHER effort** (slide-gen + narration + render). Lower priority than audio. |
| **Mind Map** | Interactive map; click a branch → grounded chat on that subtopic; multiple maps/notebook | None | **MEDIUM, strong fit.** We already extract KG triples — a mind-map is a natural visualization of the graph we build; could beat NotebookLM's embedding-only version. Mostly frontend over `mempalace_kg_query`. |

### Tier 2 — Source types we can't ingest

| Gap | NotebookLM | brain-agent today | Notes |
|---|---|---|---|
| **YouTube / video sources** | YouTube URL → transcript import | No path (no yt-dlp) | **CHEAP onramp:** yt-dlp/subtitle fetch → existing project mining pipeline. High "I can finally add my X" value. |
| **Audio file as a *source*** | Audio ingested into the corpus | `transcribe_audio` is a per-turn tool, not a mining source | Wire transcription output into project mining so a recorded meeting becomes permanent project knowledge. |
| **Live mic / voice input to chat** | (Mobile) voice; NotebookLM app | None (transcribe_audio is file-upload only; no live streaming) | Lower priority; ties to mobile. |

### Tier 3 — Generated-output presets & discovery

| Gap | NotebookLM | brain-agent today | Notes |
|---|---|---|---|
| **Study Guide** | One-click grounded study guide | Prompt + code-exec only, no preset | Thin templated layer. |
| **Briefing Doc / Reports** | One-click briefing / custom report (also "create from chat") | No templated surface | Thin templated layer. |
| **FAQ** | One-click grounded FAQ | No preset | Thin templated layer. |
| **Timeline** | One-click timeline | No preset | Thin templated layer. |
| **Flashcards** | Grounded flashcards | None | New output type. |
| **Quizzes** | Grounded quizzes, customizable count/difficulty/topic | None | New output type. |
| **Studio: multiple outputs of same type** | Store many audios/reports/maps per notebook (e.g. several languages) | Artifacts exist but no per-project "studio" surface | Organizational/UI. |
| **Deep Research (agentic source-finding)** | Plans, browses hundreds of sites, returns a grounded report to import | `deep-research` skill + exa/searxng + Websuche basket exist but NOT fused into "find → import into project" | Wire existing pieces together. |
| **Discover / Fast Research (lighter source-finder)** | Enter a topic → surfaces web+Drive sources to selectively import | Websuche basket is manual; no topic-driven suggestion → import | Smaller than Deep Research. |

### Tier 4 — Languages & grounding polish

| Gap | NotebookLM | brain-agent today | Notes |
|---|---|---|---|
| **80+ languages for chat AND audio overviews** | Full-length parity across 80+ langs | Strong translation stack, but no audio-output-per-language story; chat lang depends on model | Reuse translation stack for non-English overviews. |
| **Inline span-level clickable citations in UI** | Numbered chips jump to exact source passage | Citations + validator exist; UI shows compact pins | Mostly UI polish on an existing capability. |
| **Auto-generated sidebar summaries** | — (NotebookLM-adjacent polish) | Sidebar shows turn-count only | Minor. |

### Tier 5 — Platform / reach / collaboration (lowest priority for self-hosted)

| Gap | NotebookLM | brain-agent today | Notes |
|---|---|---|---|
| **Native mobile apps** (iOS/Android, offline audio, share-sheet) | Yes | Web + Electron desktop + Telegram | Large effort; questionable fit for self-hosted. |
| **Public "anyone with link" share** | Yes (view-only public notebooks) | user/team/global within the instance only | Needs anonymous-access model. |
| **Real-time collaborative co-editing** | Shared editing of notebooks | Sessions/projects shareable, no live co-edit | Large. |
| **Featured / curated notebooks** | Google-curated expert notebooks | None | Content/curation, not core. |
| **Integrations marketplace / one-click connectors** | (Drive etc.) | MCP via config, no in-app marketplace | Lower priority. |

### Where brain-agent already WINS (for balance — not gaps, document so we don't "fix" them)

KG triples (vs embedding-only) · cross-encoder rerank · code execution + artifacts
· image generation · GDPR/PII pre-submit scanning · ARL document classification ·
local-model support · **enterprise privacy posture** (self-hosted, no third-party
training/human-review — NotebookLM markets this; we have it inherently).

---

## Recommended order (MY opinionated subset of the full inventory above)

This is a *suggestion*, not the gap list — decide freely from all 5 tiers.

1. **Audio Overview generation** (Tier 1) — marquee feature, both halves
   (retrieval + TTS) already in the repo. Slots next to Translation Phase A/B/C.
   (Interactive "Join" mode = a later v2.)
2. **Mind Map from the KG** (Tier 1) — uniquely positioned (real triples vs
   NotebookLM's embeddings); mostly frontend.
3. **YouTube + audio-file source ingestion** (Tier 2) — cheap, high onramp value.
4. **Output presets** (Tier 3 — Study Guide / Briefing / FAQ / Timeline /
   Flashcards / Quizzes) — a thin templated layer over what the model already
   does ad hoc.

Deliberately deprioritized for a self-hosted tool: Video Overview, interactive
Join mode, native mobile, public links, real-time co-edit, featured notebooks.
The 80+-language audio story (Tier 4) rides along once Audio Overview exists.

---

## Open questions to decide in the fresh session

1. **Scope:** Build anything, or keep this as analysis? If building, just Tier 1
   #1 (Audio Overview), or a broader push?
2. **Audio Overview specifics (if chosen):**
   - Two distinct Voxtral voices available? (check `GET /v1/translate/tts/voices`)
   - Output format: a chat artifact (MP3 in the session artifact folder) vs a
     project-level "studio" surface in the web UI?
   - Script length/format controls (NotebookLM has topic focus / target audience)?
   - Languages — reuse the translation stack for non-English overviews?
   - Interactive "join the conversation" mode (NotebookLM's signature) — in scope
     or v2?
3. **Mind Map (if chosen):** render in the existing right-panel, or a new view?
   Click-a-branch → seed a `mempalace_kg_query`-scoped chat?
4. **YouTube ingest:** add `yt-dlp` to the project-sync pipeline as a new
   web-url-like source type, or a one-shot import tool?

---

## How to resume

- Re-read this doc + the original gap analysis lives only in the chat history of
  the 2026-06-03 session (this doc is the durable distillation).
- Confirm anchors still hold: `grep transcribe_audio engine/tool_schemas.py`,
  `grep -rn "translate/tts" handlers/translate.py`, `grep mempalace_kg_query
  engine/tool_schemas.py`.
- If building Audio Overview, start by listing available TTS voices and deciding
  the script-generation prompt + the two-voice stitch approach. Treat it as a new
  "Translation Phase D / Studio" effort.
- Per repo convention: update the `brain-agent-guide` skill in the SAME change if
  any user-facing feature/endpoint/tool is added (CLAUDE.md standing rule), and
  bump VERSION + skill version in both places.
