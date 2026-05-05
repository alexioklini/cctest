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

        # Apply absolute timeline offset.
        offset = self._time_offset_s
        for s in segments:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            start = float(s.get("start") or 0.0) + offset
            end = float(s.get("end") or s.get("start") or 0.0) + offset
            with self._segments_lock:
                idx = len(self._segments)
                seg = _Segment(
                    index=idx,
                    chunk_seq=chunk.seq,
                    start=start,
                    end=end,
                    text=text,
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
