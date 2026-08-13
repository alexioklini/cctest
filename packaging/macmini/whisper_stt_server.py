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

Streaming lane (ROUTERSTREAMINGSPEC): a WebSocket server on --ws-port
(default 8003) speaks the partial/final protocol for Voxtral Realtime —
one connection per utterance, binary PCM16-LE 16kHz mono in, cumulative
'partial' JSON frames out, '{"type":"end"}' → 'final' + close. The llm-router
proxies wss://…/v1/audio/transcriptions/stream to this port. Requires the
`websockets` package in the venv (lane disables itself if missing).
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


def _voxtral_get(repo: str):
    """Load-or-get the cached voxmlx model bundle (model, sp, prompt, delay)."""
    import voxmlx
    ent = _VOX_CACHE.get(repo)
    if ent is None:
        model, sp, _config = voxmlx.load_model(repo)
        prompt_tokens, n_delay = voxmlx._build_prompt_tokens(sp)
        ent = _VOX_CACHE[repo] = (model, sp, prompt_tokens, n_delay)
    return ent


def _voxtral_transcribe(wav_path: str, repo: str) -> str:
    """Transcribe via a CACHED voxmlx model. voxmlx.transcribe() reloads the
    model per call (0.0.2), so we cache load_model() and drive generate()
    directly — pinned to the voxmlx 0.0.2 internals we ship in the venv."""
    import voxmlx
    from mistral_common.tokens.tokenizers.base import SpecialTokenPolicy
    model, sp, prompt_tokens, n_delay = _voxtral_get(repo)
    tokens = voxmlx.generate(
        model, wav_path, prompt_tokens,
        n_delay_tokens=n_delay, temperature=0.0, eos_token_id=sp.eos_id,
    )
    return sp.decode(tokens, special_token_policy=SpecialTokenPolicy.IGNORE).strip()


# ── Voxtral streaming lane (WebSocket, own port) ───────────────────────────
# Port of voxmlx 0.0.2's stream.py mic loop into a feed()/finish() session:
# incremental mel/encoder/decoder state, left-pad on the first chunk, prefill
# once 38 positions exist, then token-by-token decode gated by how much real
# audio arrived. Differences from the CLI: tokens are accumulated as IDS and
# the cumulative text is re-decoded per emit (per-token decode would split
# UTF-8 umlauts); on model EOS the sentence is committed and the incremental
# state resets (the CLI does the same) — the session text keeps growing.
_VOX_STREAM_N_LEFT = 32
_VOX_STREAM_N_RIGHT = 17


class _VoxStreamSession:
    def __init__(self, repo: str):
        import mlx.core as mx
        model, sp, prompt_tokens, n_delay = _voxtral_get(repo)
        self.m, self.sp = model, sp
        self.prompt_tokens = prompt_tokens
        self.prefix_len = len(prompt_tokens)
        self.eos = sp.eos_id
        self.t_cond = model.time_embedding(mx.array([n_delay], dtype=mx.float32))
        mx.eval(self.t_cond)
        self.text_embeds = model.language_model.embed(mx.array([prompt_tokens]))[0]
        mx.eval(self.text_embeds)
        self.n_layers = len(model.language_model.layers)
        self.committed = ""   # text of EOS-closed sentences
        self.ids: list[int] = []  # token ids of the open sentence
        self.audio_s = 0.0
        self._reset_incremental()

    def _reset_incremental(self) -> None:
        import numpy as np
        self.cache = None
        self.y = None
        self.audio_tail = None
        self.conv1_tail = None
        self.conv2_tail = None
        self.encoder_cache = None
        self.ds_buf = None
        self.pending = np.zeros(0, dtype=np.float32)
        self.audio_embeds = None
        self.n_fed = 0
        self.n_decoded = 0
        self.first_cycle = True
        self.prefilled = False

    def text(self) -> str:
        from mistral_common.tokens.tokenizers.base import SpecialTokenPolicy
        open_seg = (self.sp.decode(self.ids, special_token_policy=SpecialTokenPolicy.IGNORE)
                    .strip() if self.ids else "")
        return (self.committed + " " + open_seg).strip()

    def _commit_segment(self) -> None:
        from mistral_common.tokens.tokenizers.base import SpecialTokenPolicy
        if self.ids:
            seg = self.sp.decode(self.ids, special_token_policy=SpecialTokenPolicy.IGNORE).strip()
            if seg:
                self.committed = (self.committed + " " + seg).strip()
            self.ids = []

    def _decode_steps(self, n: int):
        """Decode up to n positions. Returns (consumed, hit_eos)."""
        import mlx.core as mx
        for i in range(n):
            tok_embed = self.m.language_model.embed(self.y.reshape(1, 1))[0, 0]
            step = (self.audio_embeds[i] + tok_embed)[None, None, :]
            logits = self.m.decode(step, self.t_cond, mask=None, cache=self.cache)
            next_y = mx.argmax(logits[0, -1:], axis=-1).squeeze()
            mx.async_eval(next_y)
            tid = self.y.item()
            if tid == self.eos:
                self.cache = None
                self.y = None
                return i, True
            self.ids.append(tid)
            if i > 0 and i % 256 == 0:
                mx.clear_cache()
            self.y = next_y
        return n, False

    def _encode(self, chunk) -> None:
        import mlx.core as mx
        from voxmlx.audio import log_mel_spectrogram_step
        mel, self.audio_tail = log_mel_spectrogram_step(chunk, self.audio_tail)
        new_e, self.conv1_tail, self.conv2_tail, self.encoder_cache, self.ds_buf = (
            self.m.encode_step(mel, self.conv1_tail, self.conv2_tail,
                               self.encoder_cache, self.ds_buf))
        if new_e is not None:
            mx.eval(new_e)
            self.audio_embeds = (new_e if self.audio_embeds is None
                                 else mx.concatenate([self.audio_embeds, new_e]))

    def feed(self, pcm) -> str | None:
        """Feed float32 PCM (16kHz mono). Returns cumulative text when new
        tokens were produced, else None."""
        import numpy as np
        import mlx.core as mx
        from voxmlx.audio import SAMPLES_PER_TOKEN
        from voxmlx.cache import RotatingKVCache
        self.audio_s += len(pcm) / 16000.0
        self.pending = np.append(self.pending, pcm.astype(np.float32))
        if len(self.pending) >= SAMPLES_PER_TOKEN:
            n_feed = (len(self.pending) // SAMPLES_PER_TOKEN) * SAMPLES_PER_TOKEN
            chunk = self.pending[:n_feed]
            self.pending = self.pending[n_feed:]
            if self.first_cycle:
                left = np.zeros(_VOX_STREAM_N_LEFT * SAMPLES_PER_TOKEN, dtype=np.float32)
                chunk = np.concatenate([left, chunk])
                self.first_cycle = False
            self.n_fed += n_feed
            self._encode(chunk)

        produced = False
        while self.audio_embeds is not None:
            safe_total = _VOX_STREAM_N_LEFT + self.n_fed // SAMPLES_PER_TOKEN
            n_dec = min(self.audio_embeds.shape[0], safe_total - self.n_decoded)
            if n_dec <= 0:
                break
            if not self.prefilled:
                if self.n_decoded + self.audio_embeds.shape[0] < self.prefix_len:
                    break
                self.cache = [RotatingKVCache(8192) for _ in range(self.n_layers)]
                pre = (self.text_embeds + self.audio_embeds[:self.prefix_len])[None, :, :]
                logits = self.m.decode(pre, self.t_cond, "causal", self.cache)
                mx.eval(logits, *[x for c in self.cache for x in (c.keys, c.values)])
                self.y = mx.argmax(logits[0, -1:], axis=-1).squeeze()
                mx.async_eval(self.y)
                rest = self.audio_embeds[self.prefix_len:]
                self.audio_embeds = rest if rest.shape[0] > 0 else None
                self.n_decoded = self.prefix_len
                self.prefilled = True
                continue
            consumed, hit_eos = self._decode_steps(n_dec)
            if consumed > 0:
                produced = True
            self.n_decoded += consumed
            if self.audio_embeds is not None and self.audio_embeds.shape[0] > consumed:
                self.audio_embeds = self.audio_embeds[consumed:]
            else:
                self.audio_embeds = None
            if hit_eos:
                # Sentence done — commit and start fresh (mirrors the CLI's
                # reset_all_state; un-fed remainder audio restarts left-padded).
                self._commit_segment()
                self._reset_incremental()
                break
        return self.text() if produced else None

    def finish(self) -> str:
        """Right-pad flush + decode the remainder. Returns the final text."""
        import numpy as np
        from voxmlx.audio import SAMPLES_PER_TOKEN
        if self.cache is not None and self.y is not None:
            right = np.zeros(_VOX_STREAM_N_RIGHT * SAMPLES_PER_TOKEN, dtype=np.float32)
            self._encode(np.concatenate([self.pending, right]))
            self.pending = np.zeros(0, dtype=np.float32)
            if self.audio_embeds is not None:
                self._decode_steps(self.audio_embeds.shape[0])
                self.audio_embeds = None
            if self.y is not None:
                tid = self.y.item()
                if tid != self.eos:
                    self.ids.append(tid)
        self._commit_segment()
        return self.committed


_STREAM_SLOT = threading.Lock()   # one streaming session at a time (M4 GPU)


def _ws_stream_handler(ws) -> None:
    """One WS connection = one utterance (see ROUTERSTREAMINGSPEC): first frame
    JSON config, then binary PCM16-LE 16kHz mono frames; server sends
    cumulative 'partial' texts, '{"type":"end"}' triggers 'final' + close."""
    import numpy as np
    key = os.environ.get("STT_API_KEY", "")
    if key:
        auth = ws.request.headers.get("Authorization", "")
        if auth != f"Bearer {key}":
            ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
            return
    try:
        cfg = json.loads(ws.recv(timeout=10))
    except Exception:
        ws.send(json.dumps({"type": "error", "message": "expected JSON config frame"}))
        return
    requested = (cfg.get("model") or "").strip() or "voxtral-mini-realtime-mlx"
    repo = _VOXTRAL_ALIASES.get(requested)
    if repo is None:
        ws.send(json.dumps({"type": "error",
                            "message": f"model '{requested}' does not support streaming"}))
        return
    if not _STREAM_SLOT.acquire(blocking=False):
        ws.send(json.dumps({"type": "error",
                            "message": "busy: a streaming session is already active"}))
        return
    t0 = time.time()
    sess = None
    try:
        # Hold the GPU lock for the WHOLE session: batch requests queue behind
        # the stream (the stream is the latency-critical display) and the
        # single-resident eviction can't pull voxtral out from under us.
        with _lock:
            _release_unused(repo, is_voxtral=True)
            sess = _VoxStreamSession(repo)
            last_emit = None
            while True:
                try:
                    msg = ws.recv(timeout=30)
                except TimeoutError:
                    # 30s without frames → flush what we have and close.
                    ws.send(json.dumps({"type": "final", "text": sess.finish()}))
                    break
                if isinstance(msg, (bytes, bytearray)):
                    if not msg:
                        continue
                    pcm = np.frombuffer(msg, dtype=np.int16).astype(np.float32) / 32768.0
                    text = sess.feed(pcm)
                    if text is not None and text != last_emit:
                        last_emit = text
                        ws.send(json.dumps({"type": "partial", "text": text}))
                else:
                    try:
                        mtype = json.loads(msg).get("type")
                    except (ValueError, AttributeError):
                        continue
                    if mtype == "end":
                        ws.send(json.dumps({"type": "final", "text": sess.finish()}))
                        break
        # WBP-Bugfix: NOT closing right after the final send — a server-side
        # close racing the buffered final through relay+Cloudflare reached
        # clients as close 1006 WITHOUT the final. The client closes once it
        # has the final; we only reap after a short grace. Deliberately
        # OUTSIDE the GPU lock so the grace never delays the next utterance.
        try:
            ws.recv(timeout=2)
        except Exception:
            pass  # client closed (expected) or stayed silent — reap either way
    except Exception as e:
        # Client-close mid-stream lands here too — nothing is persisted.
        try:
            ws.send(json.dumps({"type": "error", "message": str(e)[:300]}))
        except Exception:
            pass
    finally:
        _STREAM_SLOT.release()
        with _lock:
            _METRICS["requests_total"] += 1
            _METRICS["model_loaded"] = True
            _METRICS["last_model"] = repo
            _METRICS["resident"] = f"voxtral:{repo}"
            _METRICS["last_duration_s"] = round(time.time() - t0, 2)
            if sess is not None and sess.audio_s > 0:
                _METRICS["last_audio_s"] = round(sess.audio_s, 1)
                _METRICS["total_audio_s"] = round(
                    _METRICS["total_audio_s"] + sess.audio_s, 1)
        try:
            ws.close()
        except Exception:
            pass


def _run_ws_server(host: str, port: int) -> None:
    try:
        from websockets.sync.server import serve
    except ImportError:
        sys.stderr.write("[whisper-stt] websockets not installed — streaming lane disabled\n")
        return
    sys.stderr.write(f"[whisper-stt] streaming lane on ws://{host}:{port}\n")
    with serve(_ws_stream_handler, host, port, ping_interval=15,
               max_size=8 * 1024 * 1024) as server:
        server.serve_forever()


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
    ap.add_argument("--ws-port", type=int, default=8003,
                    help="Voxtral streaming WebSocket port (0 = disabled)")
    ap.add_argument("--default-model", default=_DEFAULT_MODEL)
    args = ap.parse_args()
    Handler.default_model = args.default_model
    if args.ws_port:
        threading.Thread(target=_run_ws_server, args=(args.host, args.ws_port),
                         name="voxtral-stream-ws", daemon=True).start()
    print(f"[whisper-stt] ready on {args.host}:{args.port} "
          f"(default {args.default_model})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
