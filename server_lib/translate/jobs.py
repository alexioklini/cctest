"""In-memory job registry for document translation.

Translating a document blocks for tens of seconds; the HTTP upload returns a
job_id immediately and the UI subscribes to a per-job SSE stream for progress.
Result file lives on disk inside the requesting agent's artifact folder so it
auto-promotes as a normal artifact (versioned, viewable, downloadable from
the artifact panel) — same place the `translate_document` tool writes when
called from chat.

Why not persist jobs in SQLite? They're transient (1h TTL), the result file
is the durable artifact, and a missed-progress-tick on server restart isn't
worth the schema cost.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

JOB_TTL_SECONDS = 3600
JOB_SWEEP_INTERVAL = 300


@dataclass
class TranslateJob:
    id: str
    state: str = "queued"          # queued | running | done | error
    filename: str = ""             # original upload name
    src_path: str = ""             # tmp file the worker reads
    output_path: str = ""          # final translated file (artifact folder)
    target_lang: str = ""
    source_lang: str = ""
    glossary: str = ""
    model: str = ""
    runs_done: int = 0
    runs_total: int = 0
    progress_pct: float = 0.0
    error: str = ""
    detected: dict | None = None
    fallback: bool = False         # PDF→DOCX flag, surface in UI
    noop: bool = False
    runs: int = 0                  # final run count (post-translate)
    agent_id: str = "main"         # for artifact path resolution
    session_id: str = ""           # synthetic session for artifact folder
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Subscriber queues — one per active SSE listener. Each receives every
    # progress event after they subscribe; they're free to disconnect at
    # any time. Lock guards the list, not the queues.
    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _terminal: bool = False        # latched once when state becomes done/error

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "filename": self.filename,
            "target_lang": self.target_lang,
            "source_lang": self.source_lang,
            "glossary": self.glossary,
            "model": self.model,
            "runs_done": self.runs_done,
            "runs_total": self.runs_total,
            "progress_pct": round(self.progress_pct, 2),
            "error": self.error,
            "detected": self.detected,
            "fallback": self.fallback,
            "noop": self.noop,
            "runs": self.runs,
            "output_filename": os.path.basename(self.output_path) if self.output_path else "",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            # Replay current state immediately so a late subscriber doesn't
            # have to wait for the next tick to see what's going on.
            q.put({"type": "status", "job": self.to_dict()})
            if self._terminal:
                q.put({"type": "done", "job": self.to_dict()})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, ev: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def update_progress(self, done: int, total: int) -> None:
        self.runs_done = done
        self.runs_total = total
        self.progress_pct = (done / total * 100.0) if total else 0.0
        self.updated_at = time.time()
        if self.state == "queued":
            self.state = "running"
        self._broadcast({"type": "progress", "job": self.to_dict()})

    def finish(self, *, output_path: str, runs: int, fallback: bool,
               detected: dict | None, noop: bool, model: str) -> None:
        self.state = "done"
        self.output_path = output_path
        self.runs = runs
        self.fallback = fallback
        self.detected = detected
        self.noop = noop
        self.model = model or self.model
        self.progress_pct = 100.0
        self.updated_at = time.time()
        self._terminal = True
        self._broadcast({"type": "done", "job": self.to_dict()})

    def fail(self, message: str) -> None:
        self.state = "error"
        self.error = message[:500]
        self.updated_at = time.time()
        self._terminal = True
        self._broadcast({"type": "error", "job": self.to_dict()})


class JobRegistry:
    """Process-wide job table. Thread-safe."""

    def __init__(self) -> None:
        self._jobs: dict[str, TranslateJob] = {}
        self._lock = threading.Lock()
        self._sweeper_started = False

    def create(self, **fields) -> TranslateJob:
        self._ensure_sweeper()
        job_id = uuid.uuid4().hex[:16]
        job = TranslateJob(id=job_id, **fields)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[TranslateJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_active(self) -> list[TranslateJob]:
        with self._lock:
            return [j for j in self._jobs.values() if not j._terminal]

    def _ensure_sweeper(self) -> None:
        # Lazy: a registry that's never used should never spawn a thread.
        if self._sweeper_started:
            return
        with self._lock:
            if self._sweeper_started:
                return
            self._sweeper_started = True
        t = threading.Thread(target=self._sweep_loop, name="translate-job-sweeper", daemon=True)
        t.start()

    def _sweep_loop(self) -> None:
        while True:
            time.sleep(JOB_SWEEP_INTERVAL)
            cutoff = time.time() - JOB_TTL_SECONDS
            with self._lock:
                stale = [jid for jid, j in self._jobs.items()
                         if j._terminal and j.updated_at < cutoff]
                for jid in stale:
                    j = self._jobs.pop(jid, None)
                    # Don't delete the output file — it lives in the artifact
                    # folder and is owned by the artifact system from there.
                    # Only the tmp upload (src_path) is ours to clean.
                    if j and j.src_path and os.path.exists(j.src_path):
                        try:
                            os.unlink(j.src_path)
                        except OSError:
                            pass


# Module-level singleton. Importable as `from server_lib.translate.jobs import REGISTRY`.
REGISTRY = JobRegistry()
