# Audio Overview — Interactive "Join" Mode — Implementation Plan

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Depends on:** `AUDIO_OVERVIEW_PLAN.md` (basic two-host podcast) shipping first —
Join mode interrupts/resumes a podcast that must already exist.

**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 1 #2). brain-agent VERSION
when scoped: 9.62.0.

---

## What this is

NotebookLM's signature differentiator: while the two-host podcast plays, the user
taps **"Join"**, interrupts the hosts with a spoken OR typed question, it's
answered **from the same sources, in the hosts' voices**, then playback resumes.

---

## KEY FINDING — the hard part already exists

The handover guessed HIGH effort assuming we'd build a realtime voice stack from
scratch. **We won't.** The live-translation feature already implements every hard
primitive, fully tuned:

- **`web/js/translation_live.js`** (624 LOC) — browser mic capture (`getUserMedia`
  + `MediaRecorder`, 16kHz mono, echo cancel), **client-side VAD** (tuned
  silence/flush: `HARD_CAP`/`MIN_LEN`/noise threshold), chunked POST + SSE results.
- **`_trLiveSetMicMuted(muted)`** (`translation_live.js:68`) — toggles
  `MediaStreamTrack.enabled` so the recorder receives silence during playback (no
  self-transcription of TTS echo). **This is the "don't transcribe the hosts"
  mechanism, already solved.**
- **`trLiveTtsStop()`** (`translation_live.js:79`) — pause current playback + drop
  queue + restore mic. **This is the interrupt-the-hosts teardown, already solved.**
- A TTS **play queue** pattern (enqueue segments, play sequentially, `audio.onended`
  drains next).
- **Endpoints `/v1/translate/live/{start,<id>/chunk,<id>/stop,<id>}`**
  (`handlers/translate.py:731+`) — open session, push audio fragment, flush/close,
  SSE segment+result stream.
- **STT** — `tool_transcribe_audio` (`engine/tools/translate_tools.py:312`,
  Voxtral/Whisper).
- **TTS** — `POST /v1/translate/tts` (the basic Audio Overview path).
- **Grounded answer** — `tool_mempalace_query` over the project (same retrieval
  the podcast script used).
- **Streaming chat turn** — `API.streamChat` / `API.attachStream` (`web/js/api.js`).

So Join mode = **orchestration of existing pieces** into a state machine, NOT new
realtime infra. Effort: MEDIUM, not HIGH.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Scope** | Greenlit, plan now | Depends on basic Audio Overview shipping. |
| **Answer style** | **As the two hosts, in-character** | Re-render the answer in Oliver/Jane voices via the existing script-gen + stitch pipeline (a short 2–4 line exchange). Seamless / NotebookLM-like. |
| **Input mode** | **Both spoken AND typed** | Spoken reuses the live mic/VAD/transcribe stack. Typed is a near-free fallback for noisy rooms. |
| **Audio language** | English only (inherits the Voxtral constraint from Audio Overview). | |

---

## Build steps

### 1. Playback state machine (frontend, `web/js/`)

A small controller around the podcast `<audio>` element. States:
`PLAYING → (Join tap) → LISTENING → THINKING → ANSWERING → RESUMING → PLAYING`.

- **PLAYING** — podcast MP3 plays; "Join" button visible.
- **Join tap** → pause podcast, record resume position. If spoken: un-mute mic via
  the `_trLiveSetMicMuted` pattern + start the live capture loop. If typed: show an
  input box.
- **LISTENING** — VAD detects the question; on flush, stop capture, mute mic.
  (Reuse the `/v1/translate/live/*` chunk+SSE flow, OR a simpler one-shot record →
  `transcribe_audio` if a rolling session is overkill — DECIDE AT BUILD.)
- **THINKING** — run a grounded answer turn (see step 2).
- **ANSWERING** — stitch the answer in the two host voices, play it (reuse the
  Audio Overview stitch + the live-translate play-queue).
- **RESUMING** — seek the podcast back to the recorded position, resume, restore
  the Join button.

**Reuse, don't re-author:** lift the mic-mute + playback-stop + queue logic from
`translation_live.js` rather than writing fresh `getUserMedia`/VAD code. Consider
extracting the shared bits into a small helper module if duplication gets ugly
(respect the global-`<script>` load-order convention; run `./web/js/js_gate.sh`).

### 2. Grounded, in-character answer (engine)

- Take the transcribed/typed question + project id.
- `tool_mempalace_query` (project-scoped) for grounding — same as the podcast.
- `background_call(purpose="transform", ...)` with a prompt that writes a SHORT
  two-host exchange answering the question (`HOST_A:` / `HOST_B:` tagged, English,
  grounded, cite-aware). Reuse the Audio Overview script format so the stitch step
  is identical.
- Stitch → MP3 (or stream line-by-line into the play queue for lower latency —
  DECIDE AT BUILD; latency matters here in a way it didn't for the full podcast).

### 3. Endpoint

- `POST /v1/projects/<id>/audio-overview/ask` (or under the same studio prefix):
  `{question, host_a_voice, host_b_voice}` → returns the answer audio (or streams
  it). Localhost auth like the rest. Wire into `handlers/` + `server.py` dispatch.

### 4. UI

- "Join" button on the Audio Overview player + a state indicator (listening /
  thinking / answering). Typed-question input as the fallback path.
- Follow web/js conventions; gate with `./web/js/js_gate.sh`.

---

## Open items to resolve AT BUILD

1. **Live-session vs one-shot capture** — reuse the full `/v1/translate/live/*`
   rolling session, or a simpler single-utterance record → `transcribe_audio`?
   The rolling session is heavier but already battle-tested; one-shot is lighter
   but new code. Lean one-shot unless VAD quality demands the tuned pipeline.
2. **Answer latency** — stitch-then-play (simple) vs stream-line-by-line into the
   queue (lower perceived latency). Latency is user-facing here.
3. **Resume accuracy** — does pausing/seeking the concatenated MP3 land cleanly?
   (Ties to the Audio Overview concat-method choice — raw-frame concat may have
   imprecise seek.)
4. **Barge-in depth** — v1: hosts fully pause, then user talks (half-duplex, like
   live-translate). True barge-in (interrupt mid-sentence with overlap) is out of
   scope — note it as a possible v3.

## Explicitly OUT of scope

- True full-duplex barge-in / overlapping talk (v3 if ever).
- Non-English audio (Voxtral constraint).
- Typed/spoken answer in a single neutral voice — we chose in-character hosts.

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new endpoint → `01-api.md`; UI → `06-user-manual.md`
  (German). VERSION bump in two places. python-compile brain.py. Graceful restart
  (SIGTERM, never SIGKILL). Commit to main. (Same rules as Audio Overview plan.)

## Success criteria

- During podcast playback, tapping Join pauses the hosts, captures a spoken OR
  typed question, answers it grounded in project sources **in the two host
  voices**, then resumes the podcast from where it paused.
- Mic does not transcribe the hosts' own audio (mute-during-playback holds).
- `./web/js/js_gate.sh` passes; brain.py compiles; version check after restart.
