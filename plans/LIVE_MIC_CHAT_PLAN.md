# Live Mic Input to Chat — Implementation Plan

**Status:** SCOPED, not built. Greenlit (full live/streaming variant) in the
2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 2, flagged low priority /
mobile-tied). brain-agent VERSION when scoped: 9.62.0.

---

## What this is

A mic button in the chat composer: speak your question, see it transcribed
**live as you talk** (partial-as-you-speak), then send it like any typed message.
Chosen variant = the **full live/streaming** path (not one-shot record-then-upload).

**Priority note (kept honest):** the handover marks this low priority — its real
payoff is mobile, and there's no native mobile app yet. We greenlit the richest
variant anyway. Sequence it AFTER the higher-value Tier 1 / earlier Tier 2 items.

---

## What already exists (the streaming stack is built)

- **`web/js/translation_live.js`** (624 LOC) — browser mic capture (`getUserMedia`,
  16kHz mono WAV encode), **tuned client-side VAD**, chunked POST + SSE results.
  The exact "speak → live partial transcript" loop.
- **`/v1/translate/live/{start,<id>/chunk,<id>/stop,<id>}`**
  (`handlers/translate.py:731+`) — open a live session, push audio fragments, SSE
  stream of segment results. **Transcribe-only mode** = pass empty `target_lang`
  (the UI already uses this — `handlers/translate.py:634,678`).
- So the streaming transcription backend a composer mic needs **already exists**;
  this feature is a NEW front-end surface (composer button + wiring) over it, plus
  driving the live session in transcribe-only mode.
- **`POST /v1/translate/media`** with `target_lang=""` exists too (one-shot
  multipart → transcript) — kept as a fallback path, not the primary.

## The gap

- **No mic button in the chat composer** (confirmed — none in `chat_send.js` /
  `index.html`).
- `translation_live.js` is wired to the *translation* UI, not the chat composer —
  its capture/VAD/SSE logic needs to be reused from the composer context (extract
  shared helpers or invoke the same live-session endpoints).

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Variant** | **Full live/streaming** | Reuse the rolling live-translate VAD session for partial-as-you-speak transcription in the composer (not one-shot record-then-send). |
| **Backend** | Existing `/v1/translate/live/*` in **transcribe-only mode** (`target_lang=""`) | No new transcription endpoint needed. |

---

## Build steps

### 1. Reuse the live-capture machinery from the composer

- Lift / share the mic capture + VAD + WAV-encode + chunk-POST + SSE-drain logic
  from `translation_live.js`. Two options (DECIDE AT BUILD):
  - (a) Extract the reusable core into a shared global-scope helper both the
    translation UI and the composer call, OR
  - (b) Have the composer open its own `/v1/translate/live/start` session
    (transcribe-only) and reuse the lower-level helpers.
- Respect web/js conventions (global `<script>`, fixed load order, no modules);
  gate with `./web/js/js_gate.sh` (update net-globals-count if helpers move).

### 2. Composer mic button + live transcript UX

- Add a mic button to the chat composer (`chat_send.js` / `index.html`).
- States: idle → recording (live partial transcript streams into the input box) →
  stopped (final transcript settled in the box, user edits/sends normally).
- Mic permission handling + a clear recording indicator. Stop on click again or on
  long silence (VAD already detects this).
- The transcript lands in the existing composer input — send path is unchanged.

### 3. Edge handling

- Browser without `getUserMedia`/`MediaRecorder` → hide the button (graceful, like
  `translation_live.js:164` already guards).
- GDPR: the transcribe path inherits the same local-fallback posture as
  `transcribe_audio` when `server_block` is on — confirm the live endpoint honors
  it (it routes through the same Voxtral/Whisper backend).

---

## Open items to resolve AT BUILD

1. **Share vs. re-open** — extract shared helpers (cleaner, touches the working
   translation UI) vs. composer opens its own live session (more isolated, some
   duplication). Pick by how cleanly the helpers factor out.
2. **Partial-transcript UX** — overwrite the input box live, or show partials in a
   ghost overlay until finalized? Live-overwrite is simpler; ghost avoids clobbering
   text the user already typed.
3. **Coexistence with typed text** — if the box already has typed text, append or
   replace? (Probably append at cursor.)
4. **Language hint** — let the user pick a source-language hint, or auto-detect
   (Voxtral does both)? Default auto.
5. **Mobile** — this feature's real payoff. If/when a mobile surface exists,
   re-check the capture path works there (MediaRecorder support varies).

## Explicitly OUT of scope

- One-shot record-then-upload variant (we chose full streaming; `/v1/translate/
  media` stays only as a fallback if streaming proves flaky).
- Voice OUTPUT / spoken replies (that's TTS / Audio Overview, separate).
- Wake-word / always-listening. Push-to-talk only.

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new composer control → `06-user-manual.md` (German).
  No new backend endpoint (reuses live-translate), so `01-api.md` likely
  untouched — verify. VERSION bump in two places. python-compile brain.py (if
  touched). Graceful restart (SIGTERM, never SIGKILL). Commit to main.
- `./web/js/js_gate.sh` must pass (globals-count updated if helpers relocate).

## Success criteria

- A mic button in the composer captures speech, shows a live partial transcript,
  and settles a final transcript in the input box that sends like a typed message.
- Works in transcribe-only mode (no translation side effects).
- Unsupported browsers hide the button cleanly.
- `server_block` on → transcription uses the local backend.
- `./web/js/js_gate.sh` passes; version check after restart.
