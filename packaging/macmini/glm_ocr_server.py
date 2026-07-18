#!/usr/bin/env python3
"""GLM-OCR HTTP wrapper for the Mac mini M4 — the remote OCR lane for the
Windows-11 Brain-Agent deployment.

On the Mac Studio, Brain runs GLM-OCR in-process via mlx_vlm (engine/mlx_ocr.py).
The Windows client has no MLX, so this thin wrapper serves the SAME GLM-OCR model
behind an OpenAI-compatible /v1/chat/completions endpoint on the Mac mini. Brain's
engine/mlx_ocr.extract() posts a vision message (text prompt + base64 image) here
when config.json -> ocr.mlx_ocr_url is set; the model, prompts and return shape
are identical to the in-process path, so OCR quality is unchanged.

Deliberately dependency-light: stdlib http.server + mlx_vlm (already needed for
the model). No FastAPI/uvicorn. Single-threaded on purpose — one GPU, and mlx_vlm
must be driven from ONE thread (Metal SIGSEGVs otherwise), exactly like Brain's
own mlx_runner lane.

Run (foreground):
    python3 glm_ocr_server.py --host 0.0.0.0 --port 8003 \
            --model mlx-community/GLM-OCR-8bit
As a launchd KeepAlive service: see MACMINI_SETUP.md section 4a.

Optional bearer auth: set OCR_API_KEY in the environment; requests must then send
Authorization: Bearer <key>. Brain sends config.json -> ocr.mlx_ocr_api_key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# One model slot, loaded lazily on the first request and reused (a reload costs
# ~0.7s and re-reads >1GB). Guarded by _lock because ThreadingHTTPServer hands
# each request its own thread — but generate() itself is serialized so mlx_vlm
# only ever runs on one thread at a time (Metal requirement).
_holder: dict = {"repo": None, "model": None, "processor": None}
_lock = threading.Lock()
_DEFAULT_MODEL = "mlx-community/GLM-OCR-8bit"


def _load(repo: str):
    if _holder["repo"] != repo:
        from mlx_vlm import load
        model, processor = load(repo)
        _holder.update(repo=repo, model=model, processor=processor)
    return _holder["model"], _holder["processor"]


_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.DOTALL)


def _extract_image_and_prompt(messages: list) -> tuple[str | None, str]:
    """Pull the first image (data URI) and the text prompt out of an OpenAI
    vision `messages` array. Returns (image_bytes_path, prompt)."""
    prompt_parts: list[str] = []
    img_bytes: bytes | None = None
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            prompt_parts.append(content)
            continue
        for part in content or []:
            ptype = part.get("type")
            if ptype == "text":
                prompt_parts.append(part.get("text") or "")
            elif ptype == "image_url":
                url = (part.get("image_url") or {}).get("url") or ""
                m = _DATA_URI_RE.match(url)
                if m and img_bytes is None:
                    img_bytes = base64.b64decode(m.group("b64"))
    if img_bytes is None:
        return None, " ".join(p for p in prompt_parts if p).strip()
    # mlx_vlm.generate wants a file path — spill to a temp file.
    fd, path = tempfile.mkstemp(suffix=".png", prefix="glmocr-")
    with os.fdopen(fd, "wb") as f:
        f.write(img_bytes)
    return path, " ".join(p for p in prompt_parts if p).strip()


def _run_ocr(model_repo: str, image_path: str, prompt: str,
             max_tokens: int) -> str:
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    with _lock:
        model, processor = _load(model_repo)
        formatted = apply_chat_template(
            processor, model.config, prompt, num_images=1)
        out = generate(model, processor, formatted, [image_path],
                       max_tokens=max_tokens, verbose=False)
    return (out.text if hasattr(out, "text") else str(out)).strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "glm-ocr-wrapper/1.0"
    default_model = _DEFAULT_MODEL

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        key = os.environ.get("OCR_API_KEY", "")
        if not key:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {key}"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("[glm-ocr] " + (fmt % args) + "\n")

    def do_GET(self):
        # Health + a minimal /v1/models so Brain's reachability checks pass.
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok", "model": _holder["repo"]})
        elif self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {"object": "list", "data": [
                {"id": self.default_model, "object": "model"}]})
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
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._send_json(400, {"error": f"bad request: {e}"})
            return
        model_repo = payload.get("model") or self.default_model
        max_tokens = int(payload.get("max_tokens") or 4096)
        img_path, prompt = _extract_image_and_prompt(payload.get("messages") or [])
        if not img_path:
            self._send_json(400, {"error": "no image in request"})
            return
        try:
            text = _run_ocr(model_repo, img_path, prompt, max_tokens)
        except Exception as e:
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        finally:
            try:
                os.unlink(img_path)
            except Exception:
                pass
        # Minimal OpenAI chat-completion envelope — Brain reads
        # choices[0].message.content and nothing else.
        self._send_json(200, {
            "object": "chat.completion",
            "model": model_repo,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8003)
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    args = ap.parse_args()
    Handler.default_model = args.model
    # Preload so the first real request isn't slow (and fail fast if mlx_vlm or
    # the model is missing).
    print(f"[glm-ocr] loading {args.model} ...", flush=True)
    _load(args.model)
    print(f"[glm-ocr] ready on {args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
