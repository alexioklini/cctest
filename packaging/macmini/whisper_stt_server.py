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
        repo = _repo_for(fields.get("model") or "", self.default_model)
        language = fields.get("language") or None
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="stt-")
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        t0 = time.time()
        try:
            import mlx_whisper
            kwargs = {"path_or_hf_repo": repo}
            if language:
                kwargs["language"] = language
            with _lock:
                result = mlx_whisper.transcribe(path, **kwargs)
            text = (result.get("text") or "").strip()
            segs = result.get("segments") or []
            audio_s = segs[-1].get("end") if segs else None
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
            _METRICS["last_duration_s"] = round(time.time() - t0, 2)
            if audio_s is not None:
                _METRICS["last_audio_s"] = round(audio_s, 1)
                _METRICS["total_audio_s"] = round(
                    _METRICS["total_audio_s"] + audio_s, 1)
        self._send_json(200, {"text": text})


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
