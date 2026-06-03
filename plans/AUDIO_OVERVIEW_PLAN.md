# Audio Overview (Podcast) — Implementation Plan

**Status:** SCOPED, not built. Decisions locked in the 2026-06-03 NotebookLM-gap
session. This file is the durable spec so a fresh session / subtask can build it
cold without re-deriving anything.

**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 1 #1 — the top pick).
**brain-agent VERSION when scoped:** 9.62.0.

---

## What this is

NotebookLM's signature output: feed it your sources, get back a ~podcast where
**two AI hosts discuss the material**. We're building the generation half (script
+ two-voice render). Interactive "Join" mode (tap-to-interrupt) is explicitly a
**v2**, tracked separately — NOT in this plan.

---

## Locked decisions (do not re-litigate without reason)

| Decision | Choice | Why / constraint |
|---|---|---|
| **Surface** | BOTH, phased: (1) agent tool `generate_audio_overview` → MP3 in session artifact folder; (2) project-page "Studio" button calling the same engine | Tool first = cheap, reuses artifact plumbing, works in Telegram/desktop. Project button = NotebookLM parity, needs new UI + project-output store. |
| **Controls** | NotebookLM-style: topic focus, target audience, length, language up front | More prompt + UI surface, but matches the product we're chasing. |
| **Audio language** | **English only**, for ANY source language | HARD CONSTRAINT: Voxtral `voxtral-mini-tts-latest` has **10 voices, all English** (en_us: 8M/0F, en_gb: 1M/1F). A German project yields an English podcast *about* German content. "Match source language" is a fast-follow gated on a multilingual TTS provider — unblocks with NO architecture change, only voice-roster selection. |
| **Default host voices** | **Oliver + Jane (`en_gb`, M/F)** | Only male+female pair sharing an accent. Clearest two-host contrast. Voice is a per-request param, so this is just two slugs. |

---

## Why both hard parts already exist (build on, don't rebuild)

- **Retrieval/grounding** — `tool_mempalace_query` (`engine/mempalace_glue.py:398`),
  per-wing collections (v9.62.0), KG, citation validator. Project sources are
  already mineable + queryable.
- **TTS** — `POST /v1/translate/tts` (`handlers/translate.py:84`), Voxtral
  `voxtral-mini-tts-latest`, configured + enabled in `tools_config.json →
  text_to_speech`. **Voice is a per-call `body.voice` param** — alternating two
  hosts = calling the existing endpoint twice with different slugs. No new TTS
  infra.
- **Script-gen LLM pass** — route through `sidecar_proxy.background_call(...)`
  (`handlers/sidecar_proxy.py:834`), a synchronous one-shot transform that returns
  a string. Exactly the right primitive.
- **Artifact write** — relative path → session artifact folder convention
  (`engine/tools/file_tools.py:174`); writes auto-register as `artifact_versions`
  rows + emit `artifact_updated` SSE. MP3 drops here for free.

The **"missing middle"** is only: (1) the script-gen prompt + pass, (2) the
multi-line two-voice stitch. Everything else is plumbing that exists.

---

## Build steps

### Phase 1 — Engine + agent tool (the cheap, high-value half)

1. **Script-gen pass.** New function (suggest `engine/audio_overview.py`):
   - Input: project id / session sources + controls (topic, audience, length_hint,
     host names).
   - Gather source material via `tool_mempalace_query` (project-scoped) — same
     retrieval the chat uses. For a whole-project overview, pull a broad query or
     iterate top drawers.
   - One `background_call(purpose="transform", ...)` writing a **turn-tagged
     two-host dialogue** in English. Output format: lines like
     `HOST_A: ...` / `HOST_B: ...` (pick a delimiter that survives TTS — strip the
     tag before sending text to the voice). Honor length via target word/line
     count in the prompt.
   - Keep the script as a `.md`/`.txt` artifact too (debuggable, re-renderable).

2. **Stitch.** For each script line, call the existing TTS path with that host's
   voice (`HOST_A → en_gb Oliver`, `HOST_B → en_gb Jane`). Concatenate the
   returned MP3 bytes into one file.
   - **DECISION NEEDED AT BUILD:** raw MP3-frame concatenation (works for many
     players but technically sloppy) vs. a proper concat (ffmpeg / pydub). Check
     whether ffmpeg is already a dependency before adding one. Note in the build
     which was chosen and why.
   - Write final MP3 via the artifact-folder convention → auto-registers +
     SSE `artifact_updated`.

3. **Agent tool `generate_audio_overview`.** 4-site add per CLAUDE.md "Adding a
   tool": schema in `TOOL_DEFINITIONS` (`engine/tool_schemas.py`), `TOOL_GROUPS`
   (`brain.py`), `tool_*` impl (`engine/tools/<group>.py` — likely a new file or
   `misc`), `TOOL_DISPATCH` entry (`brain.py`, **direct fn ref, not a lambda
   forwarder** — dispatch-identity rule). Params: `topic`, `audience`, `length`,
   `host_a_voice`, `host_b_voice` (defaults Oliver/Jane).

### Phase 2 — Project "Studio" surface (UI)

4. **Endpoint** — `POST /v1/projects/<id>/audio-overview` (or a `/v1/studio/*`
   prefix) calling the same Phase-1 engine over the full project corpus.
5. **Project-output store** — where do project-level generated outputs live? NLM
   stores many per notebook. Minimal: reuse artifacts tagged to the project;
   richer: a small `project_outputs` table. **DECIDE AT BUILD.** ⚠️ **SHARED with
   `OUTPUT_PRESETS_PLAN.md`** (Study Guide/Briefing/FAQ/Timeline) and later
   Flashcards/Quizzes — the endpoint (`POST /v1/projects/<id>/generate`) and the
   output store should be built ONCE and shared, not forked. Resolve this store
   shape together with the presets plan; lean toward a `project_outputs` table.
6. **Web UI** — button on the project page + controls modal (topic/audience/
   length/voice). Follow the global-scope JS conventions (web/js/, fixed load
   order, `./js_gate.sh` before committing JS).

---

## Repo-convention obligations (same change, not later)

- **brain-agent-guide skill** (standing rule, CLAUDE.md): new tool → `02-tools.md`;
  new endpoint → `01-api.md`; user-facing UI → `06-user-manual.md` (**German**).
- **VERSION bump in two places** (brain.py + skill version) per
  `[[feedback_version_two_places]]`.
- **python-compile brain.py** after editing it (esp. any CHANGELOG prose) per
  `[[feedback_compile_check_brain_py]]`.
- **Graceful restart only** — `launchctl kill SIGTERM`, NEVER SIGKILL per
  `[[feedback_never_sigkill_brain]]`.
- **Commit to main** directly per `[[feedback_commit_to_main]]`.

---

## Open items to resolve AT BUILD (flagged, not blocking the start)

1. **MP3 concat method** — ffmpeg available? pydub? raw-frame concat? (see Phase 1.2)
2. **Project-output storage** — reuse artifacts vs. new `project_outputs` table (Phase 2.5)
3. **Length controls → concrete numbers** — map "short/medium/long" to word/line targets.
4. **Whole-project retrieval strategy** — single broad `mempalace_query` vs.
   iterate top-N drawers for fuller coverage.
5. **Tool group** — which group does `generate_audio_overview` belong to
   (new `audio`? `documents`? `misc`?).

## Explicitly OUT of scope (do not build here)

- Interactive "Join" mode (Tier 1 #2 — separate plan, v2).
- Non-English audio (gated on multilingual TTS provider; fast-follow).
- Video Overview (Tier 1 #3).

---

## How to verify it works (success criteria)

- Tool call on a real project returns an MP3 artifact that plays, with two
  audibly distinct voices alternating, content grounded in project sources.
- Script artifact is inspectable and re-renderable.
- Project button produces the same over the full corpus.
- No regressions: `./web/js/js_gate.sh` passes; brain.py compiles; `/v1/status`
  version == `brain.VERSION` after graceful restart.
