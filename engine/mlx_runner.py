"""The single lane for local MLX models — OCR and speech-to-text.

Everything that runs a model on the Apple GPU *inside this process* is handed to
one long-lived thread here, and callers block for the result. Two independent
reasons, and either one alone would be enough:

**1. MLX/Metal crashes when driven from many threads.** Reproduced, both fatal
(SIGSEGV in `libmlx.dylib`, taking the daemon with it):
  - two threads evaluating at once → crash in `mlx::core::eval`
  - a thread that ran MLX and then EXITS → crash in `_pthread_tsd_cleanup`, as
    Metal tears down its thread-local state. This one bites even when the
    evaluations were serialised by a lock — which is why a lock is not enough:
    our callers are pool threads (`ingest_queue_*`, HTTP workers) that die.

**2. Concurrent users would pile onto one GPU.** Five people uploading a scan
and a voice memo at the same time used to start five model runs at once — the
machine has one GPU, so that is five ways to make everyone slow and, at worst,
to exhaust memory. Now they queue.

Scope, deliberately: this lane is for the IN-PROCESS models (OCR + whisper).
The oMLX chat server has its OWN queue (`LocalProviderQueue`, `max_concurrent`
per provider) and is left alone. Sharing one lane with chat would risk a
deadlock — a chat turn holds its slot across its whole turn *including tool
calls*, so a turn that calls an OCR tool from inside its own slot would wait on
itself. Two lanes, no such edge.
"""

from __future__ import annotations

import queue
import threading
import time

_jobs: "queue.Queue" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()

# Observability: how deep is the queue, how long did the last caller wait.
_stats = {"queued": 0, "ran": 0, "wait_total": 0.0, "wait_max": 0.0}
_stats_lock = threading.Lock()


def _pump() -> None:
    while True:
        fn, box, done = _jobs.get()
        try:
            box["result"] = fn()
        except BaseException as e:      # noqa: BLE001 — must never kill the pump
            box["error"] = e
        finally:
            done.set()


def run(fn, *, label: str = ""):
    """Run `fn` on the MLX thread; block until it finishes. Re-raises its error.

    Every local-model call goes through here. Callers keep their own timeouts —
    a caller that gives up while queued would leave the job to run anyway, so we
    do not offer a cancel: the work is seconds, not minutes.
    """
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_pump, daemon=True,
                                       name="mlx-runner")
            _worker.start()

    depth = _jobs.qsize()
    if depth:
        print(f"[mlx-runner] {label or 'job'} queued behind {depth} — "
              f"one GPU, one at a time", flush=True)

    box: dict = {}
    done = threading.Event()
    t0 = time.monotonic()
    _jobs.put((fn, box, done))
    done.wait()
    waited = time.monotonic() - t0

    with _stats_lock:
        _stats["ran"] += 1
        _stats["wait_total"] += waited
        _stats["wait_max"] = max(_stats["wait_max"], waited)

    if "error" in box:
        raise box["error"]
    return box["result"]


def stats() -> dict:
    """Queue depth + wait times, for the admin status view."""
    with _stats_lock:
        ran = _stats["ran"]
        return {
            "pending": _jobs.qsize(),
            "ran": ran,
            "wait_avg_s": round(_stats["wait_total"] / ran, 2) if ran else 0.0,
            "wait_max_s": round(_stats["wait_max"], 2),
        }
