# Video / YouTube Source Ingestion — Implementation Plan

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 2). brain-agent VERSION
when scoped: 9.62.0.

---

## What this is

NotebookLM: paste a YouTube URL → its transcript becomes a source. We add the
same: a video URL → transcript → mined into the project's MemPalace wing + KG,
searchable like any other project knowledge. The handover's "CHEAP onramp" — and
verification confirms it.

---

## Why it's cheap — the pipeline already exists

- **`_sync_project_web_urls(pdir, web_urls)`** (`server_daemons.py:1012`) is a
  near-exact template. It already: reads a URL list from `project.json`, fetches
  each fresh per sync cycle, writes each to `pdir/web-urls/<slug>_<ts>.md`,
  **hash-gates** (re-mine only on content change), and the sync loop (branch 1b,
  ~line 1904) mines those `.md` files into the wing + KG with stale-file purging
  when a URL is removed.
- A video transcript is **just markdown**. Once written as a `.md`, it flows
  through the IDENTICAL downstream mining path — **zero new mining code.**
- **STT already exists** — `tool_transcribe_audio`
  (`engine/tools/translate_tools.py:312`, Voxtral/Whisper) for caption-less videos.
- **CONFIRMED ABSENT:** no `yt-dlp`/`youtube` anywhere in the repo. The fetch +
  transcript step is the only genuinely new code.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Ingest model** | **Persistent `video_urls` field in `project.json`** | Mirror `web_urls`: a `video_urls` list + a sync fn copied from `_sync_project_web_urls`. Re-checked each cycle, hash-gated, auto-mined. Most consistent with existing design. |
| **Transcript strategy** | **Captions first, STT fallback** | yt-dlp subtitle/auto-sub (fast, no audio download); if none, download audio → `transcribe_audio`. Robust across all videos. |
| **URL scope** | **Any yt-dlp-supported site** | yt-dlp handles YouTube, Vimeo, podcasts, etc. Accept any URL it can extract. Broader value — but validate per-site + fail loud (see caveat). |

---

## ⚠️ Operational caveat (must design for, not ignore)

yt-dlp against YouTube is **operationally fragile**: YouTube periodically breaks
extractors (needs yt-dlp version bumps), and server-IP requests can hit
bot-detection / throttling. With "any site," the failure surface is wider still.

**Design rule:** failures must degrade **loudly**. A fetch/transcribe failure for
a video URL must:
- NOT write an empty or partial `.md` (which would silently mine garbage),
- surface a clear per-URL error in the project sync status (like the web-url path
  surfaces fetch state),
- leave any previously-mined transcript for that URL intact (don't purge good data
  on a transient fetch failure).

This matches the repo "fail loud / no silent truncation" rule.

---

## Build steps

### 1. yt-dlp dependency

- Add `yt-dlp` to the project venv (NOT the sidecar venv — this runs in the Brain/
  daemon process). Confirm where daemon deps are declared and add it there.
- It's a moving target: note that it needs periodic updates; consider pinning +
  a documented bump procedure.

### 2. Transcript fetch function (new)

- `fetch_video_transcript(url) -> (markdown, meta) | error`:
  1. Try yt-dlp subtitle/auto-sub extraction (`--write-auto-sub`, no audio
     download). Convert the subtitle (vtt/srt) → clean markdown (strip timestamps,
     dedupe auto-caption repetition).
  2. If no captions: yt-dlp audio-only download → `transcribe_audio` → markdown.
  3. Prepend a small header (title, channel, url, duration) so the mined chunk is
     self-describing.
- Return a clear error on failure (see caveat) — never a half-transcript.

### 3. `video_urls` project field + sync

- Whitelist `video_urls` in `ProjectManager.update_project` (mirror `web_urls`).
- Editor: a "Video URLs" section in project-settings (mirror the "Web URLs"
  section).
- New `_sync_project_video_urls(pdir, video_urls)` copied from
  `_sync_project_web_urls`: write to `pdir/videos/<slug>_<ts>.md`, hash-gated,
  stale-purge on removal. Wire it into the sync loop next to the web-url branch
  (1b) so the `.md` files get mined into the wing + KG.
- `_is_stale_src` must recognise `videos/` files (file-existence check, like
  web-urls/ — both live under pdir).

### 4. (Decided design) — persistent only

No one-shot import tool for v1 (we chose persistent field). A one-shot import
could be added later but is out of scope here.

---

## Open items to resolve AT BUILD

1. **Subtitle → markdown cleanup** — auto-captions are noisy (no punctuation,
   repeated lines). Decide how much to clean vs. pass raw. A light dedup + a
   sentence-join pass may materially improve mining quality.
2. **Audio download size/length cap** — a 3-hour video is a big audio download +
   long STT. Cap duration or warn? STT cost/time is real.
3. **yt-dlp pinning + update cadence** — pin a version + document the bump, or
   float latest and accept breakage risk.
4. **Where daemon deps live** — confirm the requirements file / venv the project-
   sync daemon uses and add yt-dlp there (NOT the sidecar venv).
5. **Bot-detection mitigation** — if server-IP YouTube fetches get throttled,
   is a cookies/PO-token path needed? Defer unless it actually bites.

## Explicitly OUT of scope

- One-shot import tool/button (chose persistent field).
- Live/streaming video. Only static published videos.
- Per-video language handling beyond what `transcribe_audio` already does.

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: `project.json` schema change + new editor section →
  `03-storage.md` + `06-user-manual.md` (German). If any endpoint changes →
  `01-api.md`. VERSION bump in two places. python-compile brain.py. Graceful
  restart (SIGTERM, never SIGKILL). Commit to main.

## Success criteria

- Adding a video URL to a project results in its transcript being mined into the
  wing + KG and answerable via chat, hash-gated (no re-mine when unchanged).
- A caption-less video falls back to STT and still mines.
- A failed fetch surfaces a clear per-URL error and does NOT mine an empty file or
  destroy a previously-good transcript.
- Removing a URL purges its mined transcript (stale sweep).
- brain.py compiles; version check after restart.
