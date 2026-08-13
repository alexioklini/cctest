#!/usr/bin/env python3
"""Apple-Vision-OCR hinter einem OpenAI-kompatiblen /v1/chat/completions —
gleiche Wire wie glm_ocr_server.py, damit Router/Brain es als Drop-in-Modell
ansprechen können (`apple-vision-ocr`). Extrahiert das erste image_url-Data-URI
aus der Vision-Message, ruft das Swift-CLI (RecognizeDocumentsRequest →
Markdown) und antwortet mit dem Markdown als assistant-content.

Läuft auf ANE/CPU (keine GPU-Last). Ein Request zur Zeit (Vision ist schnell;
der Lock hält die Antwortzeiten vorhersagbar). Port 8006, launchd
com.brain-agent.apple-ocr. Optional Bearer-Auth via OCR_API_KEY env.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apple_ocr_cli")

_STARTED_AT = time.time()
_METRICS = {"requests_total": 0, "requests_failed": 0,
            "last_duration_s": None, "total_pages": 0}

_DATA_URI = re.compile(r"^data:(image/[a-z.+-]+);base64,(.*)$", re.DOTALL)


def _extract_images(messages: list) -> list[bytes]:
    """Alle image_url-Data-URIs aus OpenAI-Vision-Messages (Reihenfolge erhalten)."""
    images: list[bytes] = []
    for msg in messages or []:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            url = ((part.get("image_url") or {}).get("url") or "")
            m = _DATA_URI.match(url)
            if m:
                try:
                    images.append(base64.b64decode(m.group(2)))
                except Exception:
                    pass
    return images


class Handler(BaseHTTPRequestHandler):
    server_version = "apple-ocr/1.0"

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        key = os.environ.get("OCR_API_KEY", "")
        return not key or self.headers.get("Authorization", "") == f"Bearer {key}"

    def log_message(self, fmt, *args):
        sys.stderr.write("[apple-ocr] " + (fmt % args) + "\n")

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok"})
        elif self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {"object": "list", "data": [
                {"id": "apple-vision-ocr", "object": "model"}]})
        elif self.path.rstrip("/") == "/status":
            m = dict(_METRICS)
            m["uptime_seconds"] = round(time.time() - _STARTED_AT)
            self._send_json(200, m)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send_json(400, {"error": f"bad request: {e}"})
            return
        images = _extract_images(body.get("messages"))
        if not images:
            self._send_json(400, {"error": "no image_url data URI in messages"})
            return
        t0 = time.time()
        pages: list[str] = []
        try:
            for img in images:
                fd, path = tempfile.mkstemp(suffix=".png", prefix="ocr-")
                with os.fdopen(fd, "wb") as f:
                    f.write(img)
                try:
                    with _lock:
                        proc = subprocess.run(
                            [_CLI, path, "--lang", "de-DE,en-US"],
                            capture_output=True, timeout=120)
                    if proc.returncode != 0:
                        raise RuntimeError(
                            proc.stderr.decode("utf-8", "replace")[:300] or "ocr failed")
                    pages.append(proc.stdout.decode("utf-8", "replace").strip())
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        except Exception as e:
            _METRICS["requests_total"] += 1
            _METRICS["requests_failed"] += 1
            self._send_json(500, {"error": str(e)[:300]})
            return
        _METRICS["requests_total"] += 1
        _METRICS["total_pages"] += len(pages)
        _METRICS["last_duration_s"] = round(time.time() - t0, 2)
        text = "\n\n---\n\n".join(pages)
        self._send_json(200, {
            "id": "ocr-" + os.urandom(4).hex(),
            "object": "chat.completion",
            "model": "apple-vision-ocr",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0, "pages": len(pages)},
        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8006)
    args = ap.parse_args()
    print(f"[apple-ocr] ready on {args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
