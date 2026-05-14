"""Brain → sidecar proxy.

The chat worker calls `run_turn(...)`. We:
  1. Build the Anthropic-shape POST /turn payload (model, base_url, api_key,
     system, messages, tools, sampling, tool_endpoint + per-turn nonce,
     tool_context bundle for tools that need session/user/project context).
  2. POST it streaming, drain the sidecar's SSE.
  3. Translate every sidecar event into Brain's event_callback vocabulary
     (text_delta, thinking_delta, tool_call, tool_result, usage, …) so the
     existing chat-worker plumbing (LiveStream, ChatDB.set_streaming_text,
     citation validator, persistence) keeps working unchanged.
  4. On cancel, POST /cancel/<turn_id> to the sidecar and stop reading.
  5. Return the assembled final reply string + a metadata summary.

No middleware here. We translate; we don't modify.

A blocking helper `run_turn_blocking(...)` for non-streaming callers (used by
the scheduler in Phase 3 and background tasks in Phase 4) lives at the bottom
of this file.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

import brain as engine
from server_lib import tool_mcp


# ---------- Config access ----------

def _sidecar_config() -> dict:
    """Read the top-level `sidecar` block from config.json.

    Falls back to {} so callers can probe `enabled`/`url` and decide.
    """
    cfg_path = engine.CONFIG_PATH if hasattr(engine, "CONFIG_PATH") else None
    if cfg_path is None:
        # Brain exposes the loaded config dict directly in some places
        return getattr(engine, "_server_config_dict", {}).get("sidecar", {}) or {}
    try:
        with open(cfg_path, "r") as f:
            return (json.load(f) or {}).get("sidecar", {}) or {}
    except Exception:
        return {}


def sidecar_url() -> str:
    cfg = _sidecar_config()
    return (cfg.get("url") or "http://127.0.0.1:8421").rstrip("/")


def sidecar_enabled() -> bool:
    cfg = _sidecar_config()
    return bool(cfg.get("enabled", False))


def tool_endpoint_internal() -> str:
    cfg = _sidecar_config()
    return cfg.get("tool_endpoint_internal") or "http://127.0.0.1:8420/v1/tools/call"


# ---------- Message + tool conversion ----------

def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert Brain's stored OpenAI-shape session.messages to Anthropic shape.

    Brain persists each turn as one row with role=user|assistant|thinking and
    a string `content`. We drop `thinking` rows (UI-only — never sent on the
    wire) and convert image-bearing user content from OpenAI's `image_url`
    blocks into Anthropic's `image` source blocks.
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            blocks: list[dict] = []
            for blk in content:
                btype = blk.get("type", "") if isinstance(blk, dict) else ""
                if btype == "text":
                    blocks.append({"type": "text", "text": blk.get("text", "")})
                elif btype == "image_url":
                    url = (blk.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        # data:image/png;base64,XXXX
                        try:
                            head, b64 = url.split(",", 1)
                            mime = head.split(":", 1)[1].split(";", 1)[0]
                        except Exception:
                            mime, b64 = "image/png", ""
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": b64},
                        })
                    # URL-only images are dropped — Anthropic SDK doesn't accept
                    # remote URLs through the wire endpoints we target (oMLX,
                    # CLIProxyAPI). Brain already coerces these into data URIs.
                elif btype == "image":
                    blocks.append(blk)
                # Other block types (tool_use/tool_result on assistant) aren't
                # persisted in session.messages — they live only inside the
                # per-turn ephemeral message list managed by the loop, which
                # the sidecar owns end-to-end now.
            if blocks:
                out.append({"role": role, "content": blocks})
            else:
                # Empty after filtering — preserve the turn as empty text so the
                # role alternation invariant holds.
                out.append({"role": role, "content": ""})
    return out


def _build_tool_list(allowed: set[str] | None) -> list[dict]:
    """Return Anthropic-shape tool schemas — the same shape the SDK harness
    feeds `tools=` with. Uses engine.TOOL_DEFINITIONS (already Anthropic shape).

    Applies the active agent's `deferred_tool_groups` — deferred tools never
    ship in the wire payload unless previously discovered via tool_search this
    session (mirrors the legacy send_message path at brain.py:25920).
    """
    tools = engine._filter_tools(engine.TOOL_DEFINITIONS, allowed, is_openai=False)
    tcfg = engine._get_token_config()
    deferred_groups = set(tcfg.get("deferred_tool_groups") or [])
    if not deferred_groups:
        return tools
    deferred_names: set[str] = set()
    for dg in deferred_groups:
        deferred_names.update(engine.TOOL_GROUPS.get(dg, set()))
    discovered = getattr(engine._thread_local, "_discovered_tools", set()) or set()
    return [t for t in tools
            if t["name"] not in deferred_names or t["name"] in discovered]


# ---------- Sampling param mapping ----------

_THINKING_BUDGETS = {"off": 0, "low": 2000, "medium": 8000, "high": 16000}


def _normalise_anthropic_base_url(base_url: str) -> str:
    """The Anthropic SDK appends `/v1/messages` itself. Brain stores OpenAI-style
    base URLs with a `/v1` suffix (e.g. `http://localhost:8000/v1`). Strip it so
    we don't end up posting to `/v1/v1/messages`.

    Idempotent: strips at most one trailing `/v1` segment.
    """
    s = (base_url or "").rstrip("/")
    if s.endswith("/v1"):
        s = s[:-3]
    return s


def _thinking_param(model: str, thinking_level: str | None) -> dict | None:
    """Map Brain's `thinking_level` knob to the SDK's `thinking` config.

    Returns the dict the SDK expects, or None when thinking is off / model
    doesn't support reasoning blocks.
    """
    if not thinking_level or thinking_level == "off" or thinking_level == "none":
        return None
    model_cfg = (engine._models_config or {}).get(model, {}) or {}
    if model_cfg.get("thinking_format") != "reasoning_field":
        # mistral_blocks / openai_opaque are mapped via reasoning_effort in
        # OpenAI wire format. With the Anthropic SDK we use `thinking={"type":
        # "enabled", "budget_tokens": N}` only for true reasoning_field models.
        return None
    budget = _THINKING_BUDGETS.get(thinking_level, 0)
    if budget == 0:
        return None
    return {"type": "enabled", "budget_tokens": budget}


# ---------- Event translation ----------
#
# Sidecar events are tagged `anthropic.<type>` for SDK-raw events and bare
# words for Brain-overlay (tool_dispatch_start / round_end / done / …).
# We translate each into the existing Brain event_callback vocabulary so the
# chat worker's wiring keeps working unchanged.

def _translate_anthropic_event(ev_type: str, data: dict,
                                state: dict, callback: Callable) -> None:
    """Map one sidecar event onto zero-or-more event_callback calls.

    `state` is per-turn scratch — used to buffer content-block-id → type
    so we can dispatch text vs thinking deltas correctly.
    """
    if ev_type == "anthropic.message_start":
        msg = data.get("message") or {}
        usage = msg.get("usage") or {}
        # Defer usage to round_end / message_delta where the SDK emits final counts.
        state["round_index"] = state.get("round_index", 0)
        return

    if ev_type == "anthropic.content_block_start":
        idx = data.get("index", 0)
        blk = data.get("content_block") or {}
        btype = blk.get("type", "")
        state.setdefault("block_types", {})[idx] = btype
        if btype == "tool_use":
            # Buffer name+input — we emit `tool_call` on tool_dispatch_start
            # so the args are guaranteed-complete.
            state.setdefault("tool_uses", {})[idx] = {
                "id": blk.get("id", ""),
                "name": blk.get("name", ""),
                "input_json": "",
            }
        elif btype == "text":
            callback("text_block_start", {})
        elif btype == "thinking":
            callback("thinking_start", {"tool_round": state.get("round_index", 0)})
        return

    if ev_type == "anthropic.content_block_delta":
        idx = data.get("index", 0)
        delta = data.get("delta") or {}
        dtype = delta.get("type", "")
        if dtype == "text_delta":
            callback("text_delta", {"text": delta.get("text", "")})
        elif dtype == "thinking_delta":
            callback("thinking_delta", {"text": delta.get("thinking", ""),
                                        "tool_round": state.get("round_index", 0)})
        elif dtype == "input_json_delta":
            tu = state.get("tool_uses", {}).get(idx)
            if tu is not None:
                tu["input_json"] += delta.get("partial_json", "")
        return

    if ev_type == "anthropic.content_block_stop":
        idx = data.get("index", 0)
        btype = (state.get("block_types") or {}).get(idx, "")
        if btype == "thinking":
            # The thinking_delta accumulator on the chat-worker side keeps the
            # text — flushing here triggers persistence as its own DB row.
            callback("thinking_done", {"tool_round": state.get("round_index", 0)})
        return

    if ev_type == "anthropic.message_delta":
        # Usage updates ride here; emit a synthetic `usage` event so the
        # chat worker sees the same shape it gets from send_message today.
        usage = data.get("usage") or {}
        if usage:
            callback("usage", {
                "tokens_in": int(usage.get("input_tokens", 0) or 0),
                "tokens_out": int(usage.get("output_tokens", 0) or 0),
                "tool_round": state.get("round_index", 0),
            })
        return

    if ev_type == "anthropic.message_stop":
        return  # round_end carries the same info; nothing to forward here.

    # --- Brain-overlay events ---

    if ev_type == "round_start":
        state["round_index"] = data.get("round", state.get("round_index", 0) + 1)
        return

    if ev_type == "round_end":
        # Per-round usage already emitted by anthropic.message_delta.
        return

    if ev_type == "tool_dispatch_start":
        callback("tool_call", {
            "name": data.get("name", ""),
            "args": data.get("args", {}) or {},
            "tool_round": data.get("round", state.get("round_index", 0)),
            "tool_use_id": data.get("tool_use_id", ""),
        })
        return

    if ev_type == "tool_dispatch_done":
        # The sidecar doesn't ship the result text in this event (kept thin to
        # avoid double-shipping a possibly large blob). The chat worker reads
        # the result via the persisted tool dispatch — but for the SSE flow
        # we need a `tool_result` event so the citation pipeline and references
        # see it. The sidecar embedded the raw result inside the next round's
        # user message, which Brain can't peek at without breaking the rule.
        # Workaround: the proxy intercepted the result on the dispatch leg
        # (`state["last_dispatch_result"]`), set there from the parallel
        # /v1/tools/call our sidecar made into Brain.
        result_key = (data.get("tool_use_id") or "") + "::" + data.get("name", "")
        result_payload = state.get("tool_results", {}).pop(result_key, None)
        callback("tool_result", {
            "name": data.get("name", ""),
            "result": result_payload or "",
            "tool_round": data.get("round", state.get("round_index", 0)),
            "elapsed_ms": data.get("elapsed_ms", 0),
            "is_error": bool(data.get("is_error", False)),
        })
        return

    if ev_type == "cancelled":
        callback("cancelled", data)
        return

    if ev_type == "error":
        callback("error", data)
        return

    if ev_type == "done":
        # Final summary; caller handles separately.
        return


# ---------- Tool-result interception ----------
#
# The sidecar fires /v1/tools/call against Brain, which dispatches via
# tool_mcp.handle_tools_call. The sidecar then includes the raw result in the
# NEXT user message it builds for the SDK. Brain never re-reads it.
#
# But the chat worker needs the result text for `event_callback("tool_result",
# {..., "result": <str>})` so its existing reference-extraction + citation
# validator + persistence wire stays whole.
#
# Solution: tool_mcp.handle_tools_call writes a copy into a per-turn
# `_tool_result_capture` dict keyed by (turn_id, tool_use_id) before
# returning to the sidecar. The proxy reads it back when emitting the
# translated `tool_result` event. The capture is cleared when the turn ends.

_TOOL_RESULT_CAPTURE: dict[tuple[str, str], dict] = {}
_TRC_LOCK = threading.Lock()


def capture_tool_result(turn_id: str, tool_use_id: str, name: str,
                        result: str, is_error: bool) -> None:
    if not turn_id:
        return
    with _TRC_LOCK:
        _TOOL_RESULT_CAPTURE[(turn_id, tool_use_id or name)] = {
            "name": name,
            "result": result,
            "is_error": is_error,
        }


def _drain_tool_result(turn_id: str, tool_use_id: str, name: str) -> dict | None:
    if not turn_id:
        return None
    with _TRC_LOCK:
        return _TOOL_RESULT_CAPTURE.pop((turn_id, tool_use_id or name), None)


def _purge_tool_results(turn_id: str) -> None:
    if not turn_id:
        return
    with _TRC_LOCK:
        for k in [k for k in _TOOL_RESULT_CAPTURE if k[0] == turn_id]:
            _TOOL_RESULT_CAPTURE.pop(k, None)


# ---------- The main entry point ----------

def run_turn(
    *,
    messages: list[dict],
    model: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    allowed_tools: set[str] | None,
    tool_context: dict,
    sampling: dict,
    thinking_level: str | None,
    max_tokens: int,
    max_rounds: int,
    event_callback: Callable,
    cancel_token: Any,
    timeout_s: float = 1800.0,
) -> dict:
    """Drive one chat turn through the sidecar.

    Returns:
      {
        "reply": <final assistant text>,
        "stop_reason": ...,
        "rounds": int,
        "tool_calls_total": int,
        "usage_total": {"input_tokens": ..., "output_tokens": ..., ...},
        "tool_events": [...],
        "cancelled": bool,
        "error": <str or None>,
      }

    The caller is responsible for everything that wraps the loop: building the
    system prompt, deciding the tool list, persisting the assistant message,
    running the citation validator. We do not touch those.
    """
    sid = tool_context.get("session_id") or ""
    nonce = tool_mcp.mint_nonce(sid)
    turn_id = uuid.uuid4().hex
    tool_context = dict(tool_context)  # don't mutate caller's dict
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "base_url": _normalise_anthropic_base_url(base_url),
        "api_key": api_key,
        "system": system_prompt,
        "messages": _to_anthropic_messages(messages),
        "tools": _build_tool_list(allowed_tools),
        "max_tokens": int(max_tokens),
        "max_rounds": int(max_rounds),
        "tool_endpoint": tool_endpoint_internal(),
        "tool_endpoint_auth": f"Bearer {nonce}",
        "tool_context": tool_context,
        "turn_id": turn_id,
        "trace_id": tool_context.get("trace_id") or "",
    }
    # Sampling — only forward what's set, the sidecar omits unset kwargs.
    for key in ("temperature", "top_p", "top_k", "stop_sequences"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    th = _thinking_param(model, thinking_level)
    if th is not None:
        payload["thinking"] = th

    url = sidecar_url() + "/turn"
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Turn-Id", turn_id)
    req.add_header("Accept", "text/event-stream")

    state: dict[str, Any] = {
        "round_index": 0,
        "block_types": {},
        "tool_uses": {},
        "tool_results": {},
        "turn_id": turn_id,
    }
    final_text = ""
    final_summary: dict[str, Any] = {}
    cancelled = False
    error_msg: str | None = None
    cancel_thread: threading.Thread | None = None

    def _watch_cancel(resp_obj):
        # Polls the cancel_token; on cancel, POST /cancel/<turn_id> and close
        # the response so the read loop exits promptly.
        if cancel_token is None:
            return
        while True:
            try:
                if getattr(cancel_token, "cancelled", False) or (
                        callable(getattr(cancel_token, "is_set", None)) and cancel_token.is_set()):
                    try:
                        cancel_url = sidecar_url() + f"/cancel/{turn_id}"
                        creq = urllib.request.Request(cancel_url, data=b"", method="POST")
                        urllib.request.urlopen(creq, timeout=5)
                    except Exception:
                        pass
                    try:
                        resp_obj.close()
                    except Exception:
                        pass
                    return
            except Exception:
                return
            time.sleep(0.5)

    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        cancel_thread = threading.Thread(
            target=_watch_cancel, args=(resp,), daemon=True,
            name=f"sidecar-cancel-watch-{turn_id[:8]}")
        cancel_thread.start()

        # Drain SSE lines
        buf = ""
        _event_count = 0
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace")
            if line.startswith(":"):  # SSE comment / keepalive
                continue
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                # Event terminator — process any buffered data line
                if buf:
                    try:
                        evt = json.loads(buf)
                    except Exception:
                        evt = None
                    buf = ""
                    if evt:
                        _event_count += 1
                        ev_type = evt.get("type", "")
                        data = evt.get("data") or {}
                        if ev_type == "tool_dispatch_done":
                            # Pull the captured raw result before translating
                            cap = _drain_tool_result(
                                turn_id,
                                data.get("tool_use_id", ""),
                                data.get("name", ""))
                            if cap:
                                state.setdefault("tool_results", {})[
                                    (data.get("tool_use_id") or "") + "::" + data.get("name", "")
                                ] = cap["result"]
                        _translate_anthropic_event(ev_type, data, state, event_callback)
                        if ev_type == "done":
                            final_summary = data
                            final_text = data.get("final_text", "") or ""
                        elif ev_type == "error":
                            error_msg = data.get("message", "sidecar error")
                        elif ev_type == "cancelled":
                            cancelled = True
                continue
            if line.startswith("data: "):
                buf += line[6:]
            elif line.startswith("data:"):
                buf += line[5:]

    except urllib.error.HTTPError as e:
        error_msg = f"sidecar HTTP {e.code}: {e.reason}"
        try:
            event_callback("error", {"message": error_msg})
        except Exception:
            pass
    except Exception as e:
        error_msg = f"sidecar transport {type(e).__name__}: {e}"
        try:
            event_callback("error", {"message": error_msg})
        except Exception:
            pass
    finally:
        tool_mcp.clear_nonce(nonce)
        _purge_tool_results(turn_id)
        # One-line summary on every turn so prod logs trace each sidecar call.
        print(f"[sidecar-proxy] turn={turn_id[:8]} model={model[:24]} "
              f"reply={len(final_text)}c rounds={final_summary.get('rounds', 0)} "
              f"tools={final_summary.get('tool_calls_total', 0)} "
              f"error={error_msg} cancelled={cancelled}", flush=True)

    return {
        "reply": final_text,
        "stop_reason": final_summary.get("stop_reason", ""),
        "rounds": final_summary.get("rounds", 0),
        "tool_calls_total": final_summary.get("tool_calls_total", 0),
        "usage_total": final_summary.get("usage_total", {}) or {},
        "tool_events": final_summary.get("tool_events", []) or [],
        "cancelled": cancelled,
        "error": error_msg,
        "turn_id": turn_id,
    }


def run_turn_blocking(
    *,
    messages: list[dict],
    model: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    allowed_tools: set[str] | None,
    tool_context: dict,
    sampling: dict,
    thinking_level: str | None,
    max_tokens: int,
    max_rounds: int,
    timeout_s: float = 1800.0,
) -> dict:
    """Non-streaming variant for background callers (scheduler, summariser,
    classifier, refine, ...). Returns the same shape as run_turn() minus the
    live event_callback hook. Phase 3 + 4 use this.
    """
    sid = tool_context.get("session_id") or ""
    nonce = tool_mcp.mint_nonce(sid)
    turn_id = uuid.uuid4().hex
    tool_context = dict(tool_context)
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "base_url": _normalise_anthropic_base_url(base_url),
        "api_key": api_key,
        "system": system_prompt,
        "messages": _to_anthropic_messages(messages),
        "tools": _build_tool_list(allowed_tools),
        "max_tokens": int(max_tokens),
        "max_rounds": int(max_rounds),
        "tool_endpoint": tool_endpoint_internal(),
        "tool_endpoint_auth": f"Bearer {nonce}",
        "tool_context": tool_context,
        "turn_id": turn_id,
        "trace_id": tool_context.get("trace_id") or "",
    }
    for key in ("temperature", "top_p", "top_k", "stop_sequences"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    th = _thinking_param(model, thinking_level)
    if th is not None:
        payload["thinking"] = th

    url = sidecar_url() + "/turn?stream=false"
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Turn-Id", turn_id)

    error_msg: str | None = None
    summary: dict = {}
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        summary = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        error_msg = f"sidecar HTTP {e.code}: {e.reason}"
    except Exception as e:
        error_msg = f"sidecar transport {type(e).__name__}: {e}"
    finally:
        tool_mcp.clear_nonce(nonce)
        _purge_tool_results(turn_id)

    return {
        "reply": summary.get("final_text", "") or "",
        "stop_reason": summary.get("stop_reason", ""),
        "rounds": summary.get("rounds", 0),
        "tool_calls_total": summary.get("tool_calls_total", 0),
        "usage_total": summary.get("usage_total", {}) or {},
        "tool_events": summary.get("tool_events", []) or [],
        "cancelled": False,
        "error": error_msg or summary.get("error"),
        "turn_id": turn_id,
    }
