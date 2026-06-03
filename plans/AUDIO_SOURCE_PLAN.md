# Audio File as a Project Source — Implementation Plan

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 2). brain-agent VERSION
when scoped: 9.62.0.

---

## What this is

Drop an audio file (recorded meeting, voice note) into a project → it's
transcribed and mined into the project's MemPalace wing + KG, becoming permanent,
searchable project knowledge. Today `transcribe_audio` is a **per-turn chat tool**
only — a recorded meeting yields a one-off answer, never lasting project knowledge.

**This is the smallest gap in the whole analysis** — it's literally "audio is
missing from the conversion pre-pass," exactly analogous to how PDF support works.

---

## Why it's tiny — the exact mechanism already exists

The project miner **only reads `.md`/`.txt`/code extensions**. A **`doc_convert`
pre-pass** converts `.pdf`/`.docx` → companion `.md` files under
`<folder>/.brain-extracted/` BEFORE the miner runs. From `server_daemons.py:1180`:

> "The miner itself only reads .md/.txt/code extensions; without this pass, PDFs
> dropped into an input folder are silently ignored."

Audio is just another file type the miner can't read directly. So audio-as-source
= **add an audio branch to that same pre-pass**. The converter already exists:
`tool_transcribe_audio` (`engine/tools/translate_tools.py:312`, Voxtral/Whisper).

Everything downstream — mining into wing + KG, hash-gating, stale-file purge,
incremental mtime-gating — is the existing input-folder pipeline, reused unchanged.
**No new field, no new endpoint, no daemon-loop change.** Smaller than the video
plan (videos needed a `video_urls` field because they're URLs; audio files just
sit in an input folder).

## Bonus: privacy posture comes for free

`transcribe_audio` already has a **GDPR gate** (`translate_tools.py`, ~line 333):
when `gdpr_scanner.server_block` is on and the chosen backend is cloud, it swaps to
local Whisper ("we can't scan audio content, so this is a conservative blanket
policy"). Mining audio inherits this correct posture automatically.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Hook point** | **Extend the `doc_convert` pre-pass** | Add an audio branch to the same pass that does PDF/DOCX → companion `.md` under `.brain-extracted/`. Mirrors PDF support exactly; rides all existing mining/stale/hash logic. |
| **Formats** | **Common set: mp3, wav, m4a, ogg, flac** | Match what the STT backend (Voxtral/Whisper) accepts. Transcode-if-needed deferred. |

---

## Build steps

### 1. Add an audio branch to the conversion pre-pass

- In the `doc_convert` pre-pass (the code around `server_daemons.py:1180` that
  produces `.brain-extracted/*.md` companions), add: when an input-folder file has
  an audio extension (mp3/wav/m4a/ogg/flac), convert it via the same transcription
  path `tool_transcribe_audio` uses → write a companion `.md` under
  `.brain-extracted/`, hash-gated identically to the PDF companion.
  - Reuse the transcription internals (`_transcription_resolve` / the route used
    by `tool_transcribe_audio`) rather than calling the agent tool wrapper.
  - Prepend a tiny header (filename, duration, transcribed-on) so the mined chunk
    is self-describing.
- This is likely an edit in `engine/doc_convert.py` (the module the pre-pass
  imports) + ensuring the audio extensions are recognised by the pre-pass walk.

### 2. That's the feature

The existing miner picks up the new `.md` companions and mines them into the wing
+ KG with no further changes. Verify the stale-purge + hash-gate paths treat audio
companions the same as PDF companions.

---

## Open items to resolve AT BUILD

1. **Exact STT route reuse** — call `_transcription_resolve` + the underlying
   transcribe function directly (not the `tool_*` wrapper, which formats a chat
   result). Find the cleanest internal entry point.
2. **Duration/size cap** — a 2-hour meeting is a long STT run inside a sync cycle.
   Cap, or run async / chunked? Don't block the whole sync loop on one huge file.
3. **Companion-hash basis** — hash the audio bytes (re-transcribe only if the file
   changed), matching the PDF companion's gating.
4. **Format probing** — confirm Voxtral/Whisper accept the chosen extensions
   natively; if one needs transcoding (e.g. some m4a), decide transcode vs skip.
5. **Where `transcribe_audio` lives vs. the daemon** — confirm the daemon process
   can reach the transcription route (same process / config as the chat tool).

## Explicitly OUT of scope

- Live mic input to chat (separate Tier 2 item).
- Speaker diarization / per-speaker labels (transcript is flat text v1).
- Audio as a per-chat attachment that becomes permanent (that's the existing
  attachment flow + this; not changing attachment behavior here).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: audio now a mineable project source → note in
  `03-storage.md` (disk layout / mineable types) + `06-user-manual.md` (German).
  No new endpoint/tool, so `01-api.md`/`02-tools.md` likely untouched — verify.
  VERSION bump in two places. python-compile brain.py. Graceful restart (SIGTERM,
  never SIGKILL). Commit to main.

## Success criteria

- Dropping an audio file into a project input folder results in its transcript
  being mined into the wing + KG and answerable via chat.
- Re-running the sync does NOT re-transcribe an unchanged file (hash-gated).
- Removing the file purges its mined transcript (stale sweep), like PDFs.
- With `server_block` on, transcription uses the local backend (inherited GDPR
  gate).
- brain.py compiles; version check after restart.
