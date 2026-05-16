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


# Per-turn replay log: turn_id -> {"events": [(seq, type, data), ...],
#                                  "done": bool, "done_at": float | None,
#                                  "cond": threading.Condition, "started_at": float}
# Used by GET /turn/<id>/events?since=N — clients (typically a restarted Brain
# proxy) can reconnect to an in-flight turn and replay missed events. Entries
# are purged 5 min after the turn reaches a terminal state so the sidecar
# doesn't accumulate logs forever; in-flight turns are never purged.
_TURN_LOG_RETAIN_S = 300.0
_TURN_LOGS: dict[str, dict] = {}
_TURN_LOGS_LOCK = threading.Lock()


def _register_turn(turn_id: str) -> threading.Event:
    if not turn_id:
        return threading.Event()
    ev = threading.Event()
    with _TURN_CANCELS_LOCK:
        _TURN_CANCELS[turn_id] = ev
    with _TURN_LOGS_LOCK:
        _TURN_LOGS[turn_id] = {
            "events": [],
            "done": False,
            "done_at": None,
            "cond": threading.Condition(),
            "started_at": time.time(),
        }
    return ev


def _unregister_turn(turn_id: str) -> None:
    if not turn_id:
        return
    with _TURN_CANCELS_LOCK:
        _TURN_CANCELS.pop(turn_id, None)
    # Don't drop the log here — clients may still reconnect to replay. Mark
    # done; the janitor thread purges old logs.


def _signal_cancel(turn_id: str) -> bool:
    with _TURN_CANCELS_LOCK:
        ev = _TURN_CANCELS.get(turn_id)
    if ev is None:
        return False
    ev.set()
    return True


def _log_event(turn_id: str, event_type: str, data: dict) -> None:
    """Append one event to the per-turn replay log and wake any reconnectors."""
    if not turn_id:
        return
    with _TURN_LOGS_LOCK:
        log = _TURN_LOGS.get(turn_id)
    if log is None:
        return
    with log["cond"]:
        seq = len(log["events"]) + 1
        log["events"].append((seq, event_type, data))
        if event_type in ("done", "error", "cancelled"):
            log["done"] = True
            log["done_at"] = time.time()
        log["cond"].notify_all()


def _purge_old_logs() -> None:
    """Drop replay logs that finished more than _TURN_LOG_RETAIN_S seconds ago."""
    now = time.time()
    with _TURN_LOGS_LOCK:
        stale = [
            tid for tid, log in _TURN_LOGS.items()
            if log["done"] and log["done_at"] is not None
            and (now - log["done_at"]) > _TURN_LOG_RETAIN_S
        ]
        for tid in stale:
            _TURN_LOGS.pop(tid, None)


def _replay_log_snapshot(turn_id: str, since: int) -> tuple[list, bool] | None:
    """Snapshot of events with seq > since. Returns (events, done) or None if
    the turn id is unknown."""
    with _TURN_LOGS_LOCK:
        log = _TURN_LOGS.get(turn_id)
    if log is None:
        return None
    with log["cond"]:
        events = [e for e in log["events"] if e[0] > since]
        return events, log["done"]


def _wait_for_event(turn_id: str, after_seq: int, timeout_s: float) -> tuple[list, bool] | None:
    """Block until at least one new event arrives with seq > after_seq, or the
    turn reaches a terminal state, or timeout fires. Returns (events, done) or
    None if the turn id is unknown."""
    with _TURN_LOGS_LOCK:
        log = _TURN_LOGS.get(turn_id)
    if log is None:
        return None
    with log["cond"]:
        deadline = time.time() + timeout_s
        while True:
            events = [e for e in log["events"] if e[0] > after_seq]
            if events or log["done"]:
                return events, log["done"]
            remaining = deadline - time.time()
            if remaining <= 0:
                return [], log["done"]
            log["cond"].wait(timeout=remaining)


def _log_janitor() -> None:
    """Background thread: every 60s, purge replay logs older than retention."""
    while True:
        time.sleep(60.0)
        try:
            _purge_old_logs()
        except Exception:
            pass

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
# GET /turn/<turn_id>/events?since=N:
#   Replay buffered events for a turn whose POST /turn SSE stream was lost
#   (e.g. Brain process restart). Returns text/event-stream with each event
#   preceded by a `: seq=N\n` comment so the client can checkpoint. After
#   replaying buffered events, follows live events until the turn terminates.
#   Logs are retained for ~5 min after a terminal event; in-flight turns are
#   never purged.
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

# End-of-sequence tokens that some local models (gemma-4-e4b on oMLX, qwen3,
# etc.) sometimes emit verbatim as plain text instead of using them as a stop
# signal. `_visible_text` strips these and trailing whitespace so the
# surrounding empty-round logic treats "<eos>" as no answer.
_EOS_TOKENS = (
    "<eos>", "<end_of_turn>", "<|endoftext|>", "<|im_end|>", "<|eot_id|>",
    "<|end|>", "</s>",
)


def _visible_text(text: str) -> str:
    """Return `text` with trailing whitespace + known EOS tokens removed.
    Empty / EOS-only payloads collapse to '' so callers can use a simple
    truthiness check instead of duplicating the token list."""
    if not text:
        return ""
    s = text.strip()
    changed = True
    while changed and s:
        changed = False
        for tok in _EOS_TOKENS:
            if s.endswith(tok):
                s = s[: -len(tok)].rstrip()
                changed = True
            if s.startswith(tok):
                s = s[len(tok):].lstrip()
                changed = True
    return s


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
    # oMLX/vLLM extension: chat_template_kwargs is forwarded via `extra_body`
    # so the SDK passes it through as a top-level JSON field on the wire. We
    # use this to set `enable_thinking: false` on gemma-4/qwen3/etc. so the
    # model doesn't emit its final answer into the reasoning channel. Must
    # mirror brain._apply_inference_to_payload byte-for-byte or the warmup
    # KV prefix won't match the chat prefix.
    if isinstance(req.get("chat_template_kwargs"), dict):
        sampling_kwargs["extra_body"] = {"chat_template_kwargs": req["chat_template_kwargs"]}

    usage_total = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rounds: list[dict] = []
    tool_calls_total = 0
    tool_events: list[dict] = []
    final_text = ""
    final_stop_reason = ""
    # Empty-reply nudges: some models (notably gemma-4 on oMLX) sometimes end
    # a turn with no text AND no tool_use after consuming a tool_result. The
    # surrounding system would persist that as a silent empty assistant
    # message. Instead, append a synthetic user prompt asking for the answer
    # and continue the loop. Capped per turn so a stuck model can't loop
    # forever; empty rounds still count toward max_rounds.
    empty_nudges = 0
    EMPTY_NUDGE_MAX = 3
    EMPTY_GIVEUP_TEXT = ("No response was returned. Please modify your "
                         "request or change the model.")

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
        # Only update `final_text` when this round produced something the
        # caller can actually read. Whitespace-only payloads (gemma-4 emits
        # `\n` placeholders before a tool_use block when it's in tool-spam
        # mode) and bare EOS tokens (gemma-4-e4b on oMLX emits `<eos>`
        # verbatim as text instead of as stop signal) are worse than no
        # payload — they overwrite a real answer from an earlier round and
        # the chat worker renders them as empty assistant text. `_visible_text`
        # strips both classes; if it returns "", treat the round as empty.
        round_visible = _visible_text(round_text)
        if round_visible:
            final_text = round_visible

        # Append the assistant turn to messages (full content list, including tool_use).
        # Anthropic rejects empty content blocks on subsequent rounds, so a
        # truly empty assistant turn (no text, no tool_use, no thinking) is
        # padded with a single space — only happens for the empty-nudge path
        # below; the placeholder never reaches the user.
        if not serialised_blocks:
            serialised_blocks = [{"type": "text", "text": " "}]
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
            # Same whitespace + EOS-token guard as above — don't clobber an
            # earlier real answer with a final-round `\n` or `<eos>` placeholder.
            if round_visible:
                final_text = round_visible
                final_stop_reason = round_stop_reason
                break
            # Empty terminating round: model ended without text and without
            # tool_use. Nudge once and continue. If we've already nudged
            # EMPTY_NUDGE_MAX times, give up with the predefined message.
            if empty_nudges < EMPTY_NUDGE_MAX:
                empty_nudges += 1
                emit("empty_round_nudge", {
                    "round": round_no,
                    "attempt": empty_nudges,
                    "max": EMPTY_NUDGE_MAX,
                })
                messages.append({
                    "role": "user",
                    "content": "Please provide your answer now based on the "
                               "information gathered so far.",
                })
                # Don't break — loop continues with the nudge appended.
                continue
            # Out of nudges — surface the predefined give-up text so the
            # assistant message is persisted instead of swallowed.
            final_text = EMPTY_GIVEUP_TEXT
            final_stop_reason = "empty_after_nudges"
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
        # If max_rounds was reached without any real text, surface the
        # give-up text so the assistant message is persisted instead of
        # silently empty (same invariant as the empty-nudges path).
        if not final_text.strip():
            final_text = EMPTY_GIVEUP_TEXT

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
        # log_error() forwards (code, message) — args[0] is an int there.
        # Skip health-check chatter; keep everything else.
        first = args[0] if args else ""
        if isinstance(first, str) and "/health" in first:
            return
        try:
            sys.stderr.write(f"[sidecar] {self.address_string()} - {fmt % args}\n")
        except Exception:
            sys.stderr.write(f"[sidecar] {self.address_string()} - {fmt} {args}\n")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            body = json.dumps({"ok": True, "anthropic_version": anthropic.__version__}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # GET /turn/<id>/events?since=N — replay buffered events for a turn.
        # Used by a restarted Brain to re-attach to an in-flight turn whose SSE
        # stream the previous proxy process was reading.
        # ?since=N (default 0) means: stream all events with seq > N. The
        # response is text/event-stream identical to /turn — same event types,
        # same `data: {...}\n\n` framing. Each event also carries a synthetic
        # comment line `: seq=<N>` so the client can checkpoint.
        if parsed.path.startswith("/turn/") and parsed.path.endswith("/events"):
            turn_id = parsed.path[len("/turn/"):-len("/events")]
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0
            self._serve_replay(turn_id, since)
            return

        self.send_error(404, "not found")

    def _serve_replay(self, turn_id: str, since: int) -> None:
        snap = _replay_log_snapshot(turn_id, since)
        if snap is None:
            self.send_error(404, f"unknown turn_id: {turn_id}")
            return
        events, done = snap
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Turn-Id", turn_id)
        self.end_headers()
        try:
            self.wfile.write(b": replay-start\n\n")
            self.wfile.flush()
        except Exception:
            return

        last_seq = since
        # Replay snapshot first.
        try:
            for seq, etype, data in events:
                self.wfile.write(f": seq={seq}\n".encode("utf-8"))
                self.wfile.write(_format_sse(etype, data))
                self.wfile.flush()
                last_seq = seq
        except Exception:
            return

        if done:
            return

        # Follow live events until terminal.
        while True:
            res = _wait_for_event(turn_id, last_seq, timeout_s=20.0)
            if res is None:
                return
            new_events, done = res
            try:
                if not new_events:
                    # Keepalive so reverse proxies don't time us out.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                else:
                    for seq, etype, data in new_events:
                        self.wfile.write(f": seq={seq}\n".encode("utf-8"))
                        self.wfile.write(_format_sse(etype, data))
                        self.wfile.flush()
                        last_seq = seq
            except Exception:
                return
            if done:
                return

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
            # Log every event for replay BEFORE attempting the wire write.
            # A peer disconnect (e.g. Brain process restart) must not prevent
            # us from buffering the event for a reconnecting client.
            _log_event(turn_id, event_type, data)
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
        # Reuse the same loop. Events still get logged for replay (a reconnect
        # against a blocking turn lets the caller follow progress live), but
        # are not written to this response.
        def _bg_emit(et: str, d: dict) -> None:
            _log_event(turn_id, et, d)
        try:
            summary = run_turn_streaming(req_body, _bg_emit,
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
    threading.Thread(target=_log_janitor, daemon=True,
                     name="sidecar-log-janitor").start()
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
