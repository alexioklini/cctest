#!/usr/bin/env python3
"""mlx-whisper STT wrapper for the Mac mini M4 — the remote speech-to-text lane
for the Windows-11 Brain-Agent deployment.

On the Mac Studio, Brain drives mlx_whisper in-process (engine/tools/translate_tools.py,
provider `local-mlx-whisper`). The Windows client has no MLX, so this wrapper serves
the same mlx_whisper models behind an OpenAI-compatible /v1/audio/transcriptions
endpoint. On Windows, create a transcription model with a provider whose base_url
points here; Brain then transcribes over HTTP with no functional change.

Use this only if the installed oMLX build does NOT already serve
/v1/audio/transcriptions (try oMLX first — one less service). Same rationale and
shape as glm_ocr_server.py: stdlib http.server + mlx_whisper, single GPU lane.

Run (foreground):
    python3 whisper_stt_server.py --host 0.0.0.0 --port 8001 \
            --default-model mlx-community/whisper-large-v3-turbo
launchd KeepAlive: see MACMINI_SETUP.md section 4.

Optional bearer auth via STT_API_KEY env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

# Live metrics so the dashboard can render this service as a peer of oMLX with
# real numbers (not just an up/down dot). Updated under _lock during a request.
_STARTED_AT = time.time()
_METRICS = {
    "requests_total": 0,        # transcription requests served
    "requests_failed": 0,
    "model_loaded": False,      # True once the model has served ≥1 request
    "last_model": None,         # model id of the most recent request
    "last_duration_s": None,    # wall-clock of the last transcription
    "last_audio_s": None,       # decoded audio length of the last request
    "total_audio_s": 0.0,       # cumulative transcribed audio seconds
    "resident": None,           # engine:repo currently held in RAM (single-resident policy)
}

# Map bare model ids Brain might send to full HF repos (mlx_whisper accepts a
# repo directly, so a full repo passes through untouched).
_REPO_ALIASES = {
    "whisper-large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "whisper-large-v3": "mlx-community/whisper-large-v3-mlx",
    "whisper-medium": "mlx-community/whisper-medium-mlx",
    "whisper-small": "mlx-community/whisper-small-mlx",
    "whisper-base": "mlx-community/whisper-base-mlx",
    "whisper-tiny": "mlx-community/whisper-tiny-mlx",
}


def _repo_for(model: str, default: str) -> str:
    if not model:
        return default
    return _REPO_ALIASES.get(model, model)


# ── Voxtral Realtime lane (voxmlx) ─────────────────────────────────────────
# Same endpoint, same GPU lock — a second local STT engine next to whisper.
# Voxtral Realtime yields no timestamps, so the response synthesizes ONE
# segment spanning the whole clip (fine for the 4–8s live-translation chunks
# and dictation; for long media files whisper stays the better choice).
_VOXTRAL_ALIASES = {
    "voxtral-mini-realtime-mlx": "mlx-community/Voxtral-Mini-4B-Realtime-6bit",
}
_VOX_CACHE: dict = {}  # repo -> (model, sp, prompt_tokens, n_delay)


def _release_unused(repo: str, is_voxtral: bool) -> None:
    """Single-resident-model policy (call under _lock, before transcribing):
    drop whatever OTHER model is still cached — mlx_whisper's ModelHolder
    keeps one whisper resident forever, _VOX_CACHE keeps voxtral — and hand
    the freed Metal buffers back. Steady state on one engine = only that
    engine in RAM (~1.6–4GB saved on the 24GB box); an alternating
    voxtral↔whisper workload pays a reload per switch instead of holding
    both. Whisper→whisper switches are handled by ModelHolder itself."""
    changed = False
    if is_voxtral:
        mod = sys.modules.get("mlx_whisper.transcribe")
        if mod is not None and getattr(mod.ModelHolder, "model", None) is not None:
            mod.ModelHolder.model = None
            mod.ModelHolder.model_path = None
            changed = True
        for k in [k for k in _VOX_CACHE if k != repo]:
            _VOX_CACHE.pop(k, None)
            changed = True
    else:
        if _VOX_CACHE:
            _VOX_CACHE.clear()
            changed = True
    if changed:
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass


def _voxtral_transcribe(wav_path: str, repo: str) -> str:
    """Transcribe via a CACHED voxmlx model. voxmlx.transcribe() reloads the
    model per call (0.0.2), so we cache load_model() and drive generate()
    directly — pinned to the voxmlx 0.0.2 internals we ship in the venv."""
    import voxmlx
    from mistral_common.tokens.tokenizers.base import SpecialTokenPolicy
    ent = _VOX_CACHE.get(repo)
    if ent is None:
        model, sp, _config = voxmlx.load_model(repo)
        prompt_tokens, n_delay = voxmlx._build_prompt_tokens(sp)
        ent = _VOX_CACHE[repo] = (model, sp, prompt_tokens, n_delay)
    model, sp, prompt_tokens, n_delay = ent
    tokens = voxmlx.generate(
        model, wav_path, prompt_tokens,
        n_delay_tokens=n_delay, temperature=0.0, eos_token_id=sp.eos_id,
    )
    return sp.decode(tokens, special_token_policy=SpecialTokenPolicy.IGNORE).strip()


def _wav_duration_s(path: str) -> float | None:
    """Clip length from the container (voxtral reports no duration itself).
    WAV via stdlib; anything else via soundfile (a voxmlx dependency)."""
    try:
        import wave
        with wave.open(path, "rb") as w:
            fr = w.getframerate()
            return round(w.getnframes() / fr, 2) if fr else None
    except Exception:
        pass
    try:
        import soundfile as sf
        info = sf.info(path)
        return round(info.frames / info.samplerate, 2) if info.samplerate else None
    except Exception:
        return None


def _parse_multipart(body: bytes, boundary: bytes) -> dict:
    """Minimal multipart/form-data parser — enough for OpenAI's audio upload
    (a `file` part + a few text parts). Returns {name: bytes|str}."""
    result: dict = {}
    delim = b"--" + boundary
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        headers = head.decode("utf-8", "replace")
        name = None
        is_file = False
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token[5:].strip('"')
                    if token.startswith("filename="):
                        is_file = True
        if name is None:
            continue
        result[name] = data if is_file else data.decode("utf-8", "replace").strip()
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "whisper-stt-wrapper/1.0"
    default_model = _DEFAULT_MODEL

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        key = os.environ.get("STT_API_KEY", "")
        if not key:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {key}"

    def log_message(self, fmt, *args):
        sys.stderr.write("[whisper-stt] " + (fmt % args) + "\n")

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok"})
        elif self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {"object": "list", "data": [
                {"id": self.default_model, "object": "model"}]})
        elif self.path.rstrip("/") == "/status":
            with _lock:
                m = dict(_METRICS)
            m["uptime_seconds"] = round(time.time() - _STARTED_AT)
            m["default_model"] = self.default_model
            self._send_json(200, m)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/audio/transcriptions":
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            self._send_json(400, {"error": "expected multipart/form-data"})
            return
        boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            fields = _parse_multipart(self.rfile.read(length), boundary)
        except Exception as e:
            self._send_json(400, {"error": f"bad request: {e}"})
            return
        audio = fields.get("file")
        if not isinstance(audio, (bytes, bytearray)):
            self._send_json(400, {"error": "no audio file in request"})
            return
        requested_model = fields.get("model") or ""
        is_voxtral = requested_model in _VOXTRAL_ALIASES
        repo = (_VOXTRAL_ALIASES[requested_model] if is_voxtral
                else _repo_for(requested_model, self.default_model))
        language = fields.get("language") or None
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="stt-")
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        t0 = time.time()
        try:
            if is_voxtral:
                # Multilingual, no language hint supported; no timestamps →
                # one synthesized segment over the whole clip below.
                with _lock:
                    _release_unused(repo, is_voxtral=True)
                    text = _voxtral_transcribe(path, repo)
                audio_s = _wav_duration_s(path)
                # Text survival beats timestamps: emit the segment even when
                # the duration probe failed (end 0.0), else downstream drops it.
                segs = ([{"start": 0.0, "end": audio_s or 0.0, "text": text}]
                        if text else [])
                detected_lang = ""
            else:
                import mlx_whisper
                kwargs = {"path_or_hf_repo": repo}
                if language:
                    kwargs["language"] = language
                with _lock:
                    _release_unused(repo, is_voxtral=False)
                    result = mlx_whisper.transcribe(path, **kwargs)
                text = (result.get("text") or "").strip()
                segs = result.get("segments") or []
                audio_s = segs[-1].get("end") if segs else None
                detected_lang = result.get("language") or language or ""
        except Exception as e:
            with _lock:
                _METRICS["requests_total"] += 1
                _METRICS["requests_failed"] += 1
                _METRICS["last_model"] = repo
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
        with _lock:
            _METRICS["requests_total"] += 1
            _METRICS["model_loaded"] = True
            _METRICS["last_model"] = repo
            _METRICS["resident"] = f"{'voxtral' if is_voxtral else 'whisper'}:{repo}"
            _METRICS["last_duration_s"] = round(time.time() - t0, 2)
            if audio_s is not None:
                _METRICS["last_audio_s"] = round(audio_s, 1)
                _METRICS["total_audio_s"] = round(
                    _METRICS["total_audio_s"] + audio_s, 1)
        # OpenAI verbose_json shape: Brain's live translation and the media tab
        # build ALL their output from `segments` — a text-only response makes
        # them silently produce nothing. language/duration/usage ride along so
        # detected-language handling and per-minute billing see real values.
        out_segments = []
        for s in segs:
            if not isinstance(s, dict):
                continue
            try:
                out_segments.append({
                    "id": len(out_segments),
                    "start": float(s.get("start") or 0.0),
                    "end": float(s.get("end") or 0.0),
                    "text": (s.get("text") or "").strip(),
                })
            except (TypeError, ValueError):
                pass
        resp = {"text": text, "language": detected_lang, "segments": out_segments}
        if audio_s is not None:
            resp["duration"] = round(float(audio_s), 2)
            resp["usage"] = {"seconds": round(float(audio_s), 2)}
        self._send_json(200, resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--default-model", default=_DEFAULT_MODEL)
    args = ap.parse_args()
    Handler.default_model = args.default_model
    print(f"[whisper-stt] ready on {args.host}:{args.port} "
          f"(default {args.default_model})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
