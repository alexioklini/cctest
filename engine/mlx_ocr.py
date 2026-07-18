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

import base64
import mimetypes
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

# Only ever touched from the MLX runner thread → needs no lock of its own.
_holder: dict = {"repo": None, "model": None, "processor": None}


def _run_on_worker(fn, *, label: str = "ocr"):
    """Hand `fn` to the shared MLX lane (engine/mlx_runner) and block.

    Every local-model call — OCR here, speech-to-text in translate_tools — goes
    through that one lane: MLX/Metal crashes when driven from several threads,
    and there is only one GPU to share between concurrent users.
    """
    from engine import mlx_runner
    return mlx_runner.run(fn, label=label)


def is_available() -> bool:
    """True when the OCR model can be reached — locally via mlx_vlm (Apple
    Silicon) OR via a configured remote GLM-OCR endpoint (the Windows path, where
    there is no MLX so a Mac-mini HTTP wrapper serves the same GLM-OCR model)."""
    if _remote_endpoint()[0]:
        return True
    try:
        import mlx_vlm  # noqa: F401
        return True
    except Exception:
        return False


def _remote_endpoint() -> tuple[str, str, str]:
    """(base_url, api_key, model) for a remote GLM-OCR wrapper, or ("","","").

    Set `ocr.mlx_ocr_url` in config.json to an OpenAI-compatible vision endpoint
    (a thin FastAPI wrapper on the Mac mini serving GLM-OCR via mlx_vlm — see
    MACMINI_SETUP.md). This is what makes the fast dedicated-OCR lane work on a
    box without MLX (Windows): the model, prompts and return shape are identical,
    only the transport is HTTP instead of an in-process mlx_vlm call.

    Reads through doc_convert._ocr_config() (the single per-call OCR-config
    reader) rather than re-parsing config.json here.
    """
    try:
        from engine import doc_convert
        ocr = doc_convert._ocr_config()
    except Exception:
        return "", "", ""
    url = (ocr.get("mlx_ocr_url") or "").rstrip("/")
    if not url:
        return "", "", ""
    api_key = ocr.get("mlx_ocr_api_key") or ""
    model = (ocr.get("mlx_ocr_model") or DEFAULT_MODEL).strip()
    return url, api_key, model


def _extract_remote(base_url: str, api_key: str, model: str, path: str,
                    prompt: str, max_tokens: int) -> tuple[str, str | None]:
    """OCR one image via a remote OpenAI-compatible vision endpoint.

    Same (text, error) contract as extract(); never raises. The image is inlined
    as a base64 data URI so no shared filesystem with the Mac mini is needed.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
    mime = mimetypes.guess_type(path)[0] or "image/png"
    data_uri = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt or DEFAULT_PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        import httpx
        # OCR of a full page can take a few seconds on the mini; generous timeout.
        resp = httpx.post(f"{base_url}/v1/chat/completions", json=payload,
                          headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        return "", f"remote OCR failed ({type(e).__name__}): {e}"
    return (text, None) if text else ("", None)


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


# The image is DESCRIBED, not transcribed. Say so explicitly: with a vague
# "Describe this image" GLM-OCR falls back to what it was built for and reads
# the text off the pixels — on a webcam portrait it returned the browser tab
# bar and never mentioned the person. Told plainly not to transcribe, the same
# model answers "a woman in a room with a bookcase and an American flag".
# The instruction is model-agnostic on purpose: it does no harm to a general
# vision model (gemma, mistral), so the admin can swap the model without
# touching the prompt.
_DESCRIBE_PROMPT = (
    "Describe what is VISIBLE in this image: the scene, people, objects, "
    "setting, colours, and any diagrams or charts. Do NOT transcribe text — "
    "describe the picture itself."
)


def describe_image(path: str, *, repo: str = "", max_tokens: int = 320
                   ) -> tuple[str, str | None]:
    """Describe an image (what is SHOWN, not what is written). (text, error).

    The counterpart to extract(): that one reads characters, this one reads the
    picture. Used when a text-only model gets an image whose TEXT gave us
    nothing — a scene photo, a diagram — so a description is all that can be
    salvaged.
    """
    return extract(path, repo=repo, prompt=_DESCRIBE_PROMPT,
                   max_tokens=max_tokens)


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
    # Remote GLM-OCR wrapper takes precedence when configured (the no-MLX path):
    # same model + prompt over HTTP. On the Mac this key is unset → in-process MLX.
    r_url, r_key, r_model = _remote_endpoint()
    if r_url:
        return _extract_remote(r_url, r_key, r_model, path,
                               prompt or DEFAULT_PROMPT, max_tokens)
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
