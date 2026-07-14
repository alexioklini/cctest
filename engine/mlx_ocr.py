"""In-process MLX OCR — a dedicated document-OCR model, run on our own GPU.

The third OCR lane, next to `mistral_ocr` (cloud) and `local_vision` (a general
vision LLM behind oMLX's OpenAI endpoint). This one loads a PURPOSE-BUILT OCR
model directly via `mlx_vlm`, in this process — same shape as STT, which drives
`mlx_whisper` in-process rather than through a server.

Why not just point `local_vision` at oMLX:
  - oMLX stays reserved for the chat models (gemma-4-12B + the cloud fallback),
    where its SSD KV-cache is the whole point. OCR has no conversation and no KV
    prefix to keep warm, so it gains nothing there — and a 1.2s OCR call would
    contend with chat for the same server.
  - Measured on an M4 (same passport scan, same prompt):
        gemma-4-12B via oMLX   36.6 s   ~7 GB
        GLM-OCR-4bit in-proc    1.2 s   2.3 GB   ← 30x faster, 3x smaller
    A 0.9B document specialist beats a 12B generalist on documents outright;
    this is not a speed/quality trade.

Model cache: `mlx_vlm.load()` has NO cache of its own (unlike mlx_whisper, whose
`ModelHolder` already holds one model and only reloads on a model change). A
reload costs ~0.7s and re-reads 1.25GB, so we keep the same one-slot holder here
— OCR runs back-to-back over a folder import, and reloading per page would
dominate the runtime.
"""

from __future__ import annotations

import os
import queue
import re
import threading

# GLM-OCR's task-prefixed prompt, plus an explicit do-not-guess clause. The bare
# "Text Recognition:" prompt makes the model INVENT text where the image is
# unreadable rather than leave a gap — on real webcam passport scans it produced
# "Sarah M. Stark" and "Gina M. Stark" (the person is Bonnie), a passport holder
# "Pham Van Pham", and a "Type of Airport: New York City" that appears nowhere in
# the image. For a compliance file an invented name is far worse than a missing
# one, so we tell it to omit what it cannot read. Measured: this alone removed
# the fabricated given names.
DEFAULT_PROMPT = (
    "Text Recognition: Transcribe ONLY text that is clearly and legibly visible "
    "in the image. Do NOT guess, do NOT complete partial words, do NOT invent "
    "names, dates or numbers. If a field is blurred, cut off or unreadable, "
    "omit it."
)

# Default model: GLM-OCR 0.9B, #1 on OmniDocBench v1.5.
# 8-bit (1.58GB) over 4-bit (1.25GB) — measured over the 10 REAL passport scans
# from the ko-kunden chat (browser screenshots of a webcam-held passport, i.e.
# the actual worst case, not a clean render):
#     4-bit   4.6s/image   passport-no read 5/10   HALLUCINATED on 2/10
#     8-bit   8.9s/image   passport-no read 5/10   HALLUCINATED on 1/10
# Same recognition, half the fabrications, for a few seconds per page. On ID
# documents a made-up date outweighs the speed. (On a clean synthetic scan the
# two are indistinguishable — which is exactly why that test was not enough.)
DEFAULT_MODEL = "mlx-community/GLM-OCR-8bit"

# Only ever touched from the MLX worker thread → needs no lock of its own.
_holder: dict = {"repo": None, "model": None, "processor": None}

# ── The MLX worker thread ────────────────────────────────────────────────────
# MLX/Metal must be driven from ONE long-lived thread. Two failure modes, both
# reproduced and both fatal (SIGSEGV inside libmlx.dylib, taking the daemon
# with it):
#   1. Two threads evaluating concurrently → crash in `mlx::core::eval`.
#   2. A thread that runs MLX and then EXITS → crash in `_pthread_tsd_cleanup`
#      as Metal tears down its thread-local state. This one bites even when the
#      evaluations were serialised by a lock, which is why a lock alone is not
#      enough: our callers are POOL threads (`ingest_queue_*`) that die.
# So we own a single daemon thread that never exits, and callers hand it work
# and block for the result. Serialising costs nothing real — there is one GPU.
_jobs: "queue.Queue" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _pump():
    while True:
        fn, box, done = _jobs.get()
        try:
            box["result"] = fn()
        except BaseException as e:      # noqa: BLE001 — must never kill the pump
            box["error"] = e
        finally:
            done.set()


def _run_on_worker(fn):
    """Run `fn` on the MLX thread and return its result (raises its error)."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_pump, daemon=True,
                                       name="mlx-ocr")
            _worker.start()
    box: dict = {}
    done = threading.Event()
    _jobs.put((fn, box, done))
    done.wait()
    if "error" in box:
        raise box["error"]
    return box["result"]


def is_available() -> bool:
    """True when mlx_vlm can be imported (Apple Silicon + package installed)."""
    try:
        import mlx_vlm  # noqa: F401
        return True
    except Exception:
        return False


def _get(repo: str):
    """Load (and cache) the OCR model. One slot — a repo change evicts.

    Runs ONLY on the MLX worker thread (via _run_on_worker), which is also the
    only thread that reads the holder — hence no lock.
    """
    if _holder["repo"] != repo:
        from mlx_vlm import load
        model, processor = load(repo)
        _holder.update(repo=repo, model=model, processor=processor)
    return _holder["model"], _holder["processor"]


def unload() -> None:
    """Drop the cached model (frees its GPU memory). Used when the admin picks
    a different OCR model, so the old weights stop answering."""
    # Evict ON the MLX thread — freeing MLX arrays from another thread is the
    # same cross-thread Metal access that crashes.
    def _drop():
        _holder.update(repo=None, model=None, processor=None)
    try:
        _run_on_worker(_drop)
    except Exception:
        pass


# ── Document-type classification ─────────────────────────────────────────────
# Reading the CHARACTERS off a photographed ID is hard (see the measurements in
# doc_convert); recognising THAT IT IS AN ID is easy — and it is the question
# that actually decides how the file must be handled. Measured on the 10 real
# webcam passport photos: 8/8 passports classified `passport`, both portrait
# shots `photo`, ~1s each. Including the one image whose text OCR could not read
# at all — which is exactly the case where a text-based PII scan is blind and a
# passport would otherwise slip through unflagged.
_TYPE_PROMPT = (
    "Classify this document image. Answer with ONE word from this list only: "
    "passport, id_card, drivers_license, bank_statement, invoice, receipt, "
    "contract, payslip, medical, certificate, correspondence, screenshot, "
    "photo, other. Answer with the single word, nothing else."
)

DOC_TYPES = (
    "passport", "id_card", "drivers_license", "bank_statement", "invoice",
    "receipt", "contract", "payslip", "medical", "certificate",
    "correspondence", "screenshot", "photo", "other",
)


def classify_document(path: str, *, repo: str = "") -> str:
    """Best-effort document-type of an image. "" when undecidable.

    Cheap (one short generation, ~1s) and — unlike the OCR text — reliable on
    exactly the bad photographs where it matters most.
    """
    text, err = extract(path, repo=repo, prompt=_TYPE_PROMPT, max_tokens=24)
    if err or not text:
        return ""
    # The model occasionally answers with a short list ("passport, id_card,
    # drivers_license") when the document could be several things — take the
    # first, it is the most likely one.
    word = re.split(r"[,\s]+", text.strip().lower())[0].strip(".:;\"'")
    return word if word in DOC_TYPES else ""


def extract(path: str, *, repo: str = "", prompt: str = "",
            max_tokens: int = 4096) -> tuple[str, str | None]:
    """OCR one image file. Returns (text, error); error is None on success.

    Never raises — a broken OCR must not fail the ingest of the document it was
    only meant to enrich, so every failure comes back as an error string the
    caller can log and move past.
    """
    repo = repo or DEFAULT_MODEL
    if not os.path.isfile(path):
        return "", f"file not found: {path}"
    try:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
    except Exception as e:
        return "", (f"mlx_vlm not installed ({type(e).__name__}) — "
                    f"pip3 install --break-system-packages mlx-vlm")
    def _work():
        model, processor = _get(repo)
        formatted = apply_chat_template(
            processor, model.config, prompt or DEFAULT_PROMPT, num_images=1)
        return generate(model, processor, formatted, [path],
                        max_tokens=max_tokens, verbose=False)

    try:
        # ALL MLX work goes to the one long-lived thread — see _run_on_worker.
        # Doing it on the caller's thread crashes: the caller is a pool worker
        # (ingest_queue_*), and Metal SIGSEGVs when a thread that touched MLX
        # later exits.
        out = _run_on_worker(_work)
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
    text = (out.text if hasattr(out, "text") else str(out)).strip()
    return (text, None) if text else ("", None)
