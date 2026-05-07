"""Live microphone translation pipeline.

Architecture:
- Browser MediaRecorder emits self-contained webm/mp4 chunks every 4s.
- POST /v1/translate/live/<id>/chunk uploads each chunk; the LiveSession
  worker queues it for transcription.
- A background thread per session pulls chunks → Voxtral → translate →
  broadcasts `segment` and `translation` SSE events.
- POST /v1/translate/live/<id>/stop signals end-of-stream and flushes.

Why per-chunk transcription (not rolling buffer):
- MediaRecorder chunks are independently decodable (each carries its own
  header). They can go straight to Voxtral.
- Aggregating audio server-side requires ffmpeg or a webm/mp4 demuxer to
  splice fragments — heavy dep that adds nothing once chunks are 4s+.
- Voxtral happily transcribes 4s clips in ~0.4s, so latency stays under 5s
  even on the slow path.

Trade-off: a sentence spanning a chunk boundary may produce two segments
that should be one. We accept that — UI displays them sequentially with
correct timestamps. Batching segments into "rolling sentences" would be
nicer but is out of scope for this iteration.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


def _norm_lang(s) -> str:
    """Normalize a language tag to a lowercase short ISO code.
    'en-US' / 'EN_us' → 'en'. None/empty → ''."""
    if not s:
        return ""
    s = str(s).strip().lower()
    for sep in ("-", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s


class SpeakerTracker:
    """Assigns stable speaker labels per-segment using resemblyzer embeddings.

    For each Voxtral segment we slice the exact audio window by timestamp,
    embed that slice, and compare against known speaker centroids.  This gives
    per-segment speaker labels rather than one label for the whole chunk, so
    two speakers in the same chunk are handled correctly as long as Voxtral's
    segment boundaries fall at the speaker change.

    Centroids are updated with EWMA so they drift-correct over the session.
    Thread-safe — all state is guarded by a single lock.

    Falls back gracefully: if the slice is too short (<0.5s) or the audio
    can't be read, returns the previous speaker label (or "Speaker 1").
    """

    MATCH_THRESHOLD = 0.82   # cosine sim above this → same speaker
    EWMA_ALPHA = 0.3         # weight of new embedding vs running centroid
    MIN_SAMPLES = 8000       # 0.5s at 16kHz — minimum slice for a clean embed
    SAMPLE_RATE = 16000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._centroids: list = []   # list of numpy arrays, one per speaker
        self._encoder = None         # lazy-loaded VoiceEncoder
        self._last_speaker: str = "Speaker 1"

    def _load_encoder(self):
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder()
        return self._encoder

    def _read_wav_samples(self, wav_path: str):
        """Read WAV as float32 mono array at SAMPLE_RATE, or None on error."""
        try:
            import numpy as np
            import wave
            with wave.open(wav_path, 'rb') as wf:
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
            if sampwidth == 2:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                return None, framerate
            if n_channels > 1:
                samples = samples[::n_channels]  # take left channel
            return samples, framerate
        except Exception:
            return None, self.SAMPLE_RATE

    def _embed_samples(self, samples):
        """Embed a float32 numpy array; returns unit vector or None."""
        try:
            import numpy as np
            from resemblyzer import preprocess_wav
            enc = self._load_encoder()
            # preprocess_wav accepts a numpy array directly
            wav = preprocess_wav(samples, source_sr=self.SAMPLE_RATE)
            if len(wav) < self.MIN_SAMPLES:
                return None
            emb = enc.embed_utterance(wav)
            norm = np.linalg.norm(emb) + 1e-9
            return emb / norm
        except Exception:
            return None

    def _match_or_create(self, emb) -> str:
        """Given a unit embedding, return the matched/new speaker label.
        Must be called with self._lock held."""
        import numpy as np
        best_idx, best_sim = -1, -1.0
        for i, centroid in enumerate(self._centroids):
            sim = float(np.dot(emb, centroid))
            if sim > best_sim:
                best_sim, best_idx = sim, i
        if best_sim >= self.MATCH_THRESHOLD:
            c = self._centroids[best_idx]
            c = (1 - self.EWMA_ALPHA) * c + self.EWMA_ALPHA * emb
            c /= (np.linalg.norm(c) + 1e-9)
            self._centroids[best_idx] = c
            return f"Speaker {best_idx + 1}"
        else:
            self._centroids.append(emb.copy())
            return f"Speaker {len(self._centroids)}"

    def assign_segment(self, wav_path: str, seg_start: float, seg_end: float) -> str:
        """Return 'Speaker N' for the audio window [seg_start, seg_end] seconds."""
        import numpy as np
        samples, framerate = self._read_wav_samples(wav_path)
        if samples is None:
            return self._last_speaker

        # Resample index to the file's actual framerate
        sr = framerate or self.SAMPLE_RATE
        i0 = int(seg_start * sr)
        i1 = int(seg_end * sr)
        slice_ = samples[i0:i1]

        # Resample to 16kHz if needed (resemblyzer expects 16kHz)
        if sr != self.SAMPLE_RATE and len(slice_) > 0:
            try:
                import librosa
                slice_ = librosa.resample(slice_, orig_sr=sr, target_sr=self.SAMPLE_RATE)
            except Exception:
                pass  # fall through — embed at wrong sr, still better than chunk

        emb = self._embed_samples(slice_)
        if emb is None:
            # Slice too short — keep previous speaker (avoids phantom new speakers)
            return self._last_speaker

        with self._lock:
            label = self._match_or_create(emb)
        self._last_speaker = label
        return label


SESSION_TTL_SECONDS = 7200       # 2h after last activity
SESSION_SWEEP_INTERVAL = 600


@dataclass
class _Chunk:
    seq: int
    path: str
    mime: str
    received_at: float


@dataclass
class _Segment:
    """A finalized transcript segment, optionally with translation attached."""
    index: int                  # 0-based index in the session's segment list
    chunk_seq: int              # source chunk
    start: float                # absolute time relative to recording start
    end: float
    text: str
    translation: str = ""
    speaker: str = ""           # "Speaker N" label from resemblyzer, empty = unknown
    detected_lang: str = ""     # ISO code from Voxtral/Whisper (chunk-level today)


class LiveSession:
    """Per-recording state: audio queue, output segments, subscriber list."""

    def __init__(self, sess_id: str, *, target_lang: str, source_lang: str,
                 glossary: str, model: str, agent_id: str, user_id: str = "") -> None:
        self.id = sess_id
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.glossary = glossary
        self.model = model
        self.agent_id = agent_id
        self.user_id = user_id
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.closed = False
        self.error: str = ""

        # Time offset within the recording — chunks come in order but Voxtral
        # reports per-chunk timestamps starting at 0. We accumulate the
        # *measured* duration of each chunk's last segment to keep the
        # timeline monotonic even if chunks are slightly trimmed.
        self._time_offset_s: float = 0.0

        self._chunks: "queue.Queue[Optional[_Chunk]]" = queue.Queue()
        self._segments: list[_Segment] = []
        self._segments_lock = threading.Lock()
        self._speaker_tracker = SpeakerTracker()

        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

        self._worker: Optional[threading.Thread] = None
        self._tmpdir = ""

    # ─── Subscriber API ────────────────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._sub_lock:
            self._subscribers.append(q)
            # Replay segments so a late subscriber sees the full state.
            with self._segments_lock:
                snap = list(self._segments)
            for s in snap:
                q.put(("segment", self._segment_to_dict(s)))
                if s.translation:
                    q.put(("translation", {"index": s.index,
                                            "translation": s.translation}))
            if self.closed:
                q.put(("closed", {"reason": "session_closed"}))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event_type: str, data: dict) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait((event_type, data))
            except queue.Full:
                pass

    def _segment_to_dict(self, s: _Segment) -> dict:
        return {
            "index": s.index,
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "text": s.text,
            "translation": s.translation,
            "speaker": s.speaker,
            "detected_lang": s.detected_lang,
            "target_lang": self.target_lang,
        }

    # ─── Producer API (HTTP handler) ───────────────────────────────────

    def add_chunk(self, seq: int, audio_bytes: bytes, mime: str) -> None:
        if self.closed:
            return
        if not self._tmpdir:
            import tempfile
            self._tmpdir = tempfile.mkdtemp(prefix=f"brain-live-{self.id}-")
        ext = self._ext_for_mime(mime)
        path = os.path.join(self._tmpdir, f"chunk-{seq:06d}{ext}")
        try:
            with open(path, "wb") as f:
                f.write(audio_bytes)
        except OSError as e:
            self.error = f"chunk write failed: {e}"
            return
        self._chunks.put(_Chunk(seq=seq, path=path, mime=mime,
                                received_at=time.time()))
        self.updated_at = time.time()
        self._ensure_worker()

    @staticmethod
    def _ext_for_mime(mime: str) -> str:
        mime = (mime or "").lower()
        if "webm" in mime:
            return ".webm"
        if "mp4" in mime:
            return ".mp4"
        if "ogg" in mime:
            return ".ogg"
        if "wav" in mime:
            return ".wav"
        return ".webm"

    def stop(self) -> None:
        # Sentinel signals the worker loop to drain + exit.
        self._chunks.put(None)
        self.updated_at = time.time()

    # ─── Worker thread ─────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name=f"live-translate-{self.id}",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        try:
            while True:
                chunk = self._chunks.get()
                if chunk is None:
                    break
                self._process_chunk(chunk)
        except Exception as e:
            self.error = f"worker crashed: {e}"
            self._broadcast("error", {"error": self.error})
        finally:
            self.closed = True
            self._broadcast("closed", {"reason": "stopped"})
            # Wake any blocked subscriber queues with a sentinel-like event.
            with self._sub_lock:
                for q in self._subscribers:
                    try:
                        q.put_nowait(("closed", {"reason": "stopped"}))
                    except queue.Full:
                        pass

    def _process_chunk(self, chunk: _Chunk) -> None:
        """Transcribe one chunk + (optionally) translate each segment.

        Translations fire individually so the partial-then-final UX works:
        the segment shows up as soon as the transcript lands, and the
        translation arrives a moment later.
        """
        import brain  # late, avoids circular at module load

        # Resolve transcription model — default Voxtral. Don't go through
        # _transcription_resolve here because it needs an arg; we hard-code
        # the default-model lookup, which honors tools_config.json.
        cfg = brain._transcription_config()
        model_arg = cfg.get("default_model") or "voxtral-mini-latest"
        try:
            model_id, route = brain._transcription_resolve(model_arg)
        except Exception as e:
            self.error = f"resolve transcribe model failed: {e}"
            self._broadcast("error", {"error": self.error})
            return
        wire = (route.get("wire") or "").lower()

        try:
            if wire == "openai_audio":
                provider = route.get("provider") or ""
                if not provider:
                    raise RuntimeError(f"model '{model_id}' has no provider")
                api_id = brain.get_api_model_id(model_id)
                result = brain._transcribe_with_voxtral(
                    chunk.path, api_id, provider,
                    self.source_lang or None, with_segments=True,
                )
            elif wire == "mlx_whisper":
                result = brain._transcribe_with_whisper(
                    chunk.path, model_id, self.source_lang or None,
                    with_segments=True,
                )
            else:
                raise RuntimeError(f"unknown wire '{wire}'")
        except Exception as e:
            # Don't kill the session on a single chunk failure — just skip.
            self._broadcast("error", {"error": f"chunk {chunk.seq}: {e}"[:240]})
            return

        segments = result.get("segments") or []
        if not segments:
            return

        # Chunk-level detected language. Voxtral returns one language per
        # response; we attach it to every segment from this chunk. With short
        # VAD-gated chunks (4–8s) this is one-speaker-per-chunk in practice,
        # so per-chunk == per-segment for the auto-TTS skip-when-same logic.
        # Fallback chain: per-segment field (future-proof) → chunk-level →
        # manual source hint. Normalized to lowercase ISO short code.
        chunk_lang = _norm_lang(result.get("language"))
        if not chunk_lang:
            chunk_lang = _norm_lang(self.source_lang)

        # Apply absolute timeline offset and assign per-segment speaker labels.
        # Each segment is embedded individually using its Voxtral timestamps,
        # so two speakers within one chunk get different labels.
        offset = self._time_offset_s
        for s in segments:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            seg_start = float(s.get("start") or 0.0)
            seg_end = float(s.get("end") or seg_start)
            speaker = self._speaker_tracker.assign_segment(
                chunk.path, seg_start, seg_end
            )
            seg_lang = _norm_lang(s.get("language")) or chunk_lang
            with self._segments_lock:
                idx = len(self._segments)
                seg = _Segment(
                    index=idx,
                    chunk_seq=chunk.seq,
                    start=seg_start + offset,
                    end=seg_end + offset,
                    text=text,
                    speaker=speaker,
                    detected_lang=seg_lang,
                )
                self._segments.append(seg)
            self._broadcast("segment", self._segment_to_dict(seg))

            if self.target_lang and self.target_lang != (
                self.source_lang or ""
            ).lower():
                self._translate_segment_async(idx, text)

        # Advance the offset by the chunk's measured duration. Voxtral
        # returns segment ends relative to chunk start, so the last segment's
        # end is a good proxy for chunk length.
        try:
            chunk_dur = float(segments[-1].get("end") or 0.0)
        except (TypeError, ValueError):
            chunk_dur = 0.0
        if chunk_dur <= 0:
            chunk_dur = 4.0  # MediaRecorder timeslice default
        self._time_offset_s = offset + chunk_dur

    def _translate_segment_async(self, idx: int, text: str) -> None:
        """Translate a single segment in a daemon thread so we don't block
        the next chunk."""
        def _go() -> None:
            try:
                from .text import translate_text
                r = translate_text(
                    text, self.target_lang,
                    source_lang=self.source_lang or "",
                    glossary_slug=self.glossary,
                    model=self.model,
                )
                tr = (r.get("translation") or "").strip()
            except Exception as e:
                tr = ""
                self._broadcast("error", {"error": f"translate seg {idx}: {e}"[:240]})
            with self._segments_lock:
                if 0 <= idx < len(self._segments):
                    self._segments[idx].translation = tr
            self._broadcast("translation", {"index": idx, "translation": tr})
        t = threading.Thread(target=_go, name=f"live-tr-{self.id}-{idx}",
                             daemon=True)
        t.start()

    # ─── Cleanup ────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        # Worker has exited — wipe the tmpdir.
        if self._tmpdir and os.path.isdir(self._tmpdir):
            try:
                import shutil
                shutil.rmtree(self._tmpdir, ignore_errors=True)
            except Exception:
                pass


class LiveSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()
        self._sweeper_started = False

    def create(self, **fields) -> LiveSession:
        self._ensure_sweeper()
        sess_id = "ls" + uuid.uuid4().hex[:14]
        sess = LiveSession(sess_id, **fields)
        with self._lock:
            self._sessions[sess_id] = sess
        return sess

    def get(self, sess_id: str) -> Optional[LiveSession]:
        with self._lock:
            return self._sessions.get(sess_id)

    def _ensure_sweeper(self) -> None:
        if self._sweeper_started:
            return
        with self._lock:
            if self._sweeper_started:
                return
            self._sweeper_started = True
        threading.Thread(target=self._sweep_loop,
                         name="live-translate-sweeper",
                         daemon=True).start()

    def _sweep_loop(self) -> None:
        while True:
            time.sleep(SESSION_SWEEP_INTERVAL)
            cutoff = time.time() - SESSION_TTL_SECONDS
            with self._lock:
                stale = [sid for sid, s in self._sessions.items()
                         if s.closed and s.updated_at < cutoff]
                for sid in stale:
                    s = self._sessions.pop(sid, None)
                    if s:
                        s.cleanup()


REGISTRY = LiveSessionRegistry()
