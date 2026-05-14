#!/usr/bin/env python3
"""Brain Sidecar — Anthropic SDK agentic loop in an isolated process.

Single hard rule: Brain does NOT modify data flowing in or out of this loop.
What the caller posts to /turn is what the SDK sees. What the SDK emits is
what the caller gets back, verbatim.

This file is the reference implementation. Anything Brain-side that wants to
add middleware, compression, or guards must do it BEFORE calling /turn (e.g.
GDPR pre-scan, quota gate, system-prompt assembly).
"""

import argparse
import http.server
import json
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from typing import Any


# Turn id -> cancel Event. POST /cancel/<turn_id> sets the event; the loop
# checks it between rounds and on stream drain.
_TURN_CANCELS: dict[str, threading.Event] = {}
_TURN_CANCELS_LOCK = threading.Lock()


def _register_turn(turn_id: str) -> threading.Event:
    if not turn_id:
        return threading.Event()
    ev = threading.Event()
    with _TURN_CANCELS_LOCK:
        _TURN_CANCELS[turn_id] = ev
    return ev


def _unregister_turn(turn_id: str) -> None:
    if not turn_id:
        return
    with _TURN_CANCELS_LOCK:
        _TURN_CANCELS.pop(turn_id, None)


def _signal_cancel(turn_id: str) -> bool:
    with _TURN_CANCELS_LOCK:
        ev = _TURN_CANCELS.get(turn_id)
    if ev is None:
        return False
    ev.set()
    return True

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed in this venv.", file=sys.stderr)
    print("  python3 -m venv .venv_sidecar && .venv_sidecar/bin/pip install -e sidecar/",
          file=sys.stderr)
    sys.exit(2)


# ---------- Wire-protocol contract ----------
#
# POST /turn  body fields:
#   model              str    Anthropic model id (or anything the base_url accepts)
#   base_url           str    e.g. http://localhost:8000 (oMLX) or http://localhost:8317 (CLIProxyAPI)
#   api_key            str    Bearer/x-api-key value the SDK will send
#   system             str    System prompt, verbatim
#   messages           list   Anthropic-shape message list
#   tools              list   Anthropic tool schemas
#   max_tokens         int    (default 16000)
#   max_rounds         int    (default 25)
#   tool_endpoint      str    Where to POST {name, args, ...} for each tool_use
#   tool_endpoint_auth str    Authorization header value sent with tool calls
#   trace_id           str    Optional. Echoed in events.
#   turn_id            str    Optional. Also accepted as X-Turn-Id header.
#                             Used to address the running turn via POST /cancel/<turn_id>.
#
# Sampling params (all optional — sidecar omits the kwarg when unset; Brain decides
# defaults per resolved model):
#   temperature        float
#   top_p              float
#   top_k              int
#   stop_sequences     list[str]
#   thinking           dict    e.g. {"type": "enabled", "budget_tokens": 8000}
#
# POST /cancel/<turn_id>:
#   Flips the cancel flag for an in-flight turn. The loop checks between rounds
#   and after the SDK stream drains for the current round; on cancel it emits
#   a `cancelled` event then `done` and exits cleanly.
#
# Stream=true (default): response is text/event-stream, one event per line:
#   data: {"type": "<type>", "data": {...}}\n\n
#
# Event types emitted (we forward Anthropic's events 1:1 + a few Brain-overlay events):
#   anthropic.message_start
#   anthropic.content_block_start
#   anthropic.content_block_delta
#   anthropic.content_block_stop
#   anthropic.message_delta
#   anthropic.message_stop
#   tool_dispatch_start    {round, name, args, tool_use_id}
#   tool_dispatch_done     {round, name, elapsed_ms, result_chars, is_error}
#   round_start            {round}
#   round_end              {round, stop_reason, has_tool_use, content_chars, usage}
#   done                   {rounds, tool_calls_total, usage_total, final_text, stop_reason}
#   error                  {message, traceback}
#
# Stream=false:
#   200 OK, application/json:
#     {"final_text": "...", "stop_reason": "...", "rounds": N,
#      "tool_calls_total": N, "usage_total": {...}, "tool_events": [...]}
#   On error: 500 with {"error": "...", "traceback": "..."}


# ---------- Tool dispatch ----------

def dispatch_tool_via_http(endpoint: str, auth: str, name: str, args: dict,
                           trace_id: str | None,
                           tool_context: dict | None = None,
                           tool_use_id: str = "",
                           timeout_s: float = 120.0) -> tuple[str, bool, float]:
    """POST a tool call to Brain's /v1/tools/call (or a stub in Phase 1).

    Returns (result_string, is_error, elapsed_seconds). The result is whatever
    the caller's endpoint returned — we do not interpret, summarise, or cap it.
    """
    body: dict[str, Any] = {"name": name, "args": args}
    if trace_id:
        body["trace_id"] = trace_id
    if tool_context:
        body["context"] = tool_context
    if tool_use_id:
        body["tool_use_id"] = tool_use_id
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", auth)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - t0
        result = payload.get("result", "")
        is_error = bool(payload.get("is_error", False))
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        return result, is_error, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        return f"tool dispatch failed: {type(e).__name__}: {e}", True, elapsed


# ---------- Core loop (streaming) ----------

def run_turn_streaming(req: dict, emit, cancel_event: threading.Event | None = None) -> dict:
    """Execute one turn. `emit(type, data)` writes an SSE event to the client.

    Returns a dict suitable for the non-streaming response (also used to build
    the final `done` event in streaming mode).
    """
    client = anthropic.Anthropic(api_key=req["api_key"], base_url=req["base_url"])

    messages = list(req["messages"])  # shallow copy — never reach into the originals
    system = req["system"]
    tools = req.get("tools") or []
    max_tokens = int(req.get("max_tokens", 16000))
    max_rounds = int(req.get("max_rounds", 25))
    tool_endpoint = req.get("tool_endpoint") or ""
    tool_endpoint_auth = req.get("tool_endpoint_auth") or ""
    tool_context = req.get("tool_context") or None
    trace_id = req.get("trace_id") or ""

    # Sampling params: omit from the SDK call when the caller didn't set them.
    # The wire library is dumb — Brain decides defaults per resolved model.
    sampling_kwargs: dict[str, Any] = {}
    if "temperature" in req and req["temperature"] is not None:
        sampling_kwargs["temperature"] = float(req["temperature"])
    if "top_p" in req and req["top_p"] is not None:
        sampling_kwargs["top_p"] = float(req["top_p"])
    if "top_k" in req and req["top_k"] is not None:
        sampling_kwargs["top_k"] = int(req["top_k"])
    if req.get("stop_sequences"):
        sampling_kwargs["stop_sequences"] = list(req["stop_sequences"])
    if isinstance(req.get("thinking"), dict):
        sampling_kwargs["thinking"] = req["thinking"]
    # disable_parallel_tool_use: maps to Anthropic's tool_choice.
    # Set to True forces sequential tool use (one tool_use per round); the
    # OpenAI-shape `parallel_tool_calls: false` doesn't apply here — this is
    # the Anthropic SDK equivalent. Skipped when no tools are present (the
    # Anthropic API rejects tool_choice without tools).
    if req.get("disable_parallel_tool_use") and tools:
        sampling_kwargs["tool_choice"] = {
            "type": "auto",
            "disable_parallel_tool_use": True,
        }

    usage_total = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rounds: list[dict] = []
    tool_calls_total = 0
    tool_events: list[dict] = []
    final_text = ""
    final_stop_reason = ""

    # Per-turn `stream` knob (PROMPT_TOOLS_UNIFICATION_PLAN.md investigation):
    # `True` (default) uses `client.messages.stream()` and forwards every SSE
    # event for live UI updates. `False` uses `client.messages.create()`
    # synchronously per round — the harness's HTTP shape. Some upstreams
    # (Mistral via CLIProxyAPI on the canonical research task) showed
    # different stop_reason behavior between the two; the non-streaming
    # mode matches the harness's known-good flow byte-for-byte.
    use_streaming = req.get("stream", True) is not False

    for round_idx in range(max_rounds):
        round_no = round_idx + 1
        if cancel_event is not None and cancel_event.is_set():
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "round_start"})
            break
        emit("round_start", {"round": round_no})

        # Streaming SDK call — events forwarded verbatim
        round_text_parts: list[str] = []
        tool_uses: list[dict] = []
        serialised_blocks: list[dict] = []
        round_usage: dict[str, Any] = {}
        round_stop_reason = ""

        try:
            if use_streaming:
                with client.messages.stream(
                    model=req["model"],
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                    **sampling_kwargs,
                ) as stream:
                    # The SDK emits typed events; we forward each as
                    # `anthropic.<event_type>` with its raw shape in `data`.
                    for ev in stream:
                        etype = getattr(ev, "type", "") or ""
                        # Pull a serialisable payload. Anthropic events are pydantic
                        # models with `.model_dump()` in 0.101.x. Fall back to dict().
                        try:
                            payload = ev.model_dump(mode="json")
                        except Exception:
                            try:
                                payload = dict(ev)  # type: ignore[arg-type]
                            except Exception:
                                payload = {"_repr": repr(ev)[:500]}
                        emit(f"anthropic.{etype}", payload)
                    # After draining the stream, accumulate the final message
                    final_msg = stream.get_final_message()
            else:
                # Non-streaming path: harness-equivalent flow. Single HTTP call,
                # full Message returned synchronously. No per-token events
                # forwarded — live UI gets a single round_end event instead.
                final_msg = client.messages.create(
                    model=req["model"],
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                    **sampling_kwargs,
                )
        except anthropic.APIError as e:
            emit("error", {"message": f"APIError: {e}",
                            "round": round_no,
                            "traceback": traceback.format_exc()[-2000:]})
            final_stop_reason = "api_error"
            break

        # Usage from the assembled final message
        u = getattr(final_msg, "usage", None)
        if u is not None:
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                v = getattr(u, k, None) or 0
                usage_total[k] = usage_total.get(k, 0) + int(v)
                round_usage[k] = int(v)

        # Walk content blocks: text → final_text, tool_use → dispatch
        for blk in (final_msg.content or []):
            btype = getattr(blk, "type", "")
            if btype == "text":
                round_text_parts.append(blk.text)
                serialised_blocks.append({"type": "text", "text": blk.text})
            elif btype == "tool_use":
                tool_uses.append(blk)
                serialised_blocks.append({
                    "type": "tool_use",
                    "id": blk.id,
                    "name": blk.name,
                    "input": blk.input,
                })
            elif btype == "thinking":
                # Forward as-is — keep signature so subsequent rounds still validate
                serialised_blocks.append({
                    "type": "thinking",
                    "thinking": getattr(blk, "thinking", ""),
                    "signature": getattr(blk, "signature", ""),
                })

        round_text = "\n".join(p for p in round_text_parts if p)
        round_stop_reason = getattr(final_msg, "stop_reason", "") or ""
        if round_text:
            # Track the most recent non-empty round text. If we ever exit
            # without a clean "no tool_use" round (e.g. max_rounds with a
            # final tool_use round, or a peer disconnect mid-round), this
            # is what the caller should see as the reply rather than "".
            final_text = round_text

        # Append the assistant turn to messages (full content list, including tool_use)
        messages.append({"role": "assistant", "content": serialised_blocks})

        emit("round_end", {
            "round": round_no,
            "stop_reason": round_stop_reason,
            "has_tool_use": bool(tool_uses),
            "content_chars": len(round_text),
            "usage": round_usage,
        })

        rounds.append({
            "round": round_no,
            "stop_reason": round_stop_reason,
            "content_chars": len(round_text),
            "tool_uses": len(tool_uses),
            "usage": round_usage,
        })

        if not tool_uses:
            final_text = round_text
            final_stop_reason = round_stop_reason
            break

        if cancel_event is not None and cancel_event.is_set():
            final_text = round_text
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "post_round"})
            break

        # Dispatch tools in batch, then append a single user message containing all tool_result blocks
        result_blocks = []
        for tu in tool_uses:
            tool_calls_total += 1
            tu_args = tu.input or {}
            emit("tool_dispatch_start", {
                "round": round_no,
                "tool_use_id": tu.id,
                "name": tu.name,
                "args": tu_args,
            })
            result_str, is_error, elapsed = dispatch_tool_via_http(
                tool_endpoint, tool_endpoint_auth, tu.name, tu_args, trace_id,
                tool_context=tool_context, tool_use_id=tu.id)
            emit("tool_dispatch_done", {
                "round": round_no,
                "tool_use_id": tu.id,
                "name": tu.name,
                "elapsed_ms": int(elapsed * 1000),
                "result_chars": len(result_str),
                "is_error": is_error,
            })
            tool_events.append({
                "round": round_no,
                "name": tu.name,
                "args": tu_args,
                "elapsed_ms": int(elapsed * 1000),
                "result_chars": len(result_str),
                "is_error": is_error,
            })
            result_block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_str,
            }
            if is_error:
                result_block["is_error"] = True
            result_blocks.append(result_block)
        messages.append({"role": "user", "content": result_blocks})
    else:
        final_stop_reason = "max_rounds"

    summary = {
        "final_text": final_text,
        "stop_reason": final_stop_reason,
        "rounds": len(rounds),
        "tool_calls_total": tool_calls_total,
        "usage_total": usage_total,
        "tool_events": tool_events,
    }
    return summary


# ---------- HTTP server ----------

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _format_sse(event_type: str, data: dict) -> bytes:
    # Single-line SSE — type goes in the JSON, not the SSE `event:` field,
    # so the wire format is uniform regardless of event taxonomy.
    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    return ("data: " + payload + "\n\n").encode("utf-8")


class SidecarHandler(http.server.BaseHTTPRequestHandler):
    # Suppress default access log noise; we log selectively below
    def log_message(self, fmt, *args):
        if "/health" in (args[0] if args else ""):
            return
        sys.stderr.write(f"[sidecar] {self.address_string()} - {fmt % args}\n")

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "anthropic_version": anthropic.__version__}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        # POST /cancel/<turn_id> — flips the cancel flag for an in-flight turn.
        if parsed.path.startswith("/cancel/"):
            turn_id = parsed.path[len("/cancel/"):]
            found = _signal_cancel(turn_id)
            body = json.dumps({"cancelled": found, "turn_id": turn_id}).encode("utf-8")
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path != "/turn":
            self.send_error(404, "not found")
            return
        query = urllib.parse.parse_qs(parsed.query)
        stream = query.get("stream", ["true"])[0].lower() != "false"

        length = int(self.headers.get("Content-Length") or "0")
        try:
            raw = self.rfile.read(length) if length else b"{}"
            req_body = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self.send_error(400, f"invalid JSON: {e}")
            return

        # Minimal field validation; we want to surface caller bugs, not paper over them.
        required = ("model", "base_url", "api_key", "system", "messages")
        missing = [k for k in required if k not in req_body]
        if missing:
            self.send_error(400, f"missing fields: {missing}")
            return

        # Caller-minted turn id (X-Turn-Id header) so cancel can race the response.
        turn_id = self.headers.get("X-Turn-Id", "") or req_body.get("turn_id", "")

        if stream:
            self._serve_streaming(req_body, turn_id)
        else:
            self._serve_blocking(req_body, turn_id)

    # --- streaming path ---

    def _serve_streaming(self, req_body: dict, turn_id: str = ""):
        cancel_event = _register_turn(turn_id)
        # Force connection close on stream end so the caller's HTTPResponse
        # iterator sees EOF promptly. With HTTP/1.0 + 'keep-alive' header the
        # urllib-side reader can sit blocked on the socket for minutes after
        # the final `done` event.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        if turn_id:
            self.send_header("X-Turn-Id", turn_id)
        self.end_headers()
        try:
            self.wfile.write(b": stream-start\n\n")
            self.wfile.flush()
        except Exception:
            return  # client gone already

        # keepalive ticker so reverse proxies don't time us out on long prefills
        stop_keepalive = threading.Event()
        write_lock = threading.Lock()

        def _keepalive():
            while not stop_keepalive.wait(15.0):
                try:
                    with write_lock:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except Exception:
                    return

        kt = threading.Thread(target=_keepalive, daemon=True, name="sidecar-keepalive")
        kt.start()

        def emit(event_type: str, data: dict):
            try:
                with write_lock:
                    self.wfile.write(_format_sse(event_type, data))
                    self.wfile.flush()
            except Exception:
                # client disconnected; loop continues regardless (no-op write)
                pass

        try:
            summary = run_turn_streaming(req_body, emit, cancel_event=cancel_event)
            emit("done", summary)
        except Exception as e:
            emit("error", {"message": f"{type(e).__name__}: {e}",
                            "traceback": traceback.format_exc()[-3000:]})
        finally:
            stop_keepalive.set()
            _unregister_turn(turn_id)
            try:
                with write_lock:
                    self.wfile.flush()
            except Exception:
                pass

    # --- blocking JSON path (background tasks) ---

    def _serve_blocking(self, req_body: dict, turn_id: str = ""):
        cancel_event = _register_turn(turn_id)
        # Reuse the same loop, but discard events into a no-op emit.
        try:
            summary = run_turn_streaming(req_body, lambda et, d: None,
                                          cancel_event=cancel_event)
            body = json.dumps(summary, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-3000:],
            }).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            _unregister_turn(turn_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8421)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), SidecarHandler)
    print(f"[sidecar] anthropic={anthropic.__version__}  "
          f"listening on http://{args.host}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[sidecar] shutting down", flush=True)
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
