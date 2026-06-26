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


def tool_endpoint_internal() -> str:
    cfg = _sidecar_config()
    return cfg.get("tool_endpoint_internal") or "http://127.0.0.1:8420/v1/tools/call"


def cancel_turn(turn_id: str) -> bool:
    """POST /cancel/<turn_id> to the sidecar — the same endpoint the chat
    cancel-watch thread hits. Used by detached background tasks (which have no
    Session.cancel_token to poll). Best-effort: returns True on a 2xx."""
    if not turn_id:
        return False
    try:
        req = urllib.request.Request(
            sidecar_url() + f"/cancel/{turn_id}", data=b"", method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def cancel_tool(turn_id: str, tool_use_id: str) -> bool:
    """POST /cancel-tool/<turn_id>/<tool_use_id> — cancel ONE in-flight tool
    dispatch within a turn (the sidecar loop returns a synthetic error result
    for it and proceeds). Best-effort: True on a 2xx (a matching in-flight tool
    was found); 404 → already finished / unknown → False."""
    if not turn_id or not tool_use_id:
        return False
    try:
        req = urllib.request.Request(
            sidecar_url() + f"/cancel-tool/{turn_id}/{tool_use_id}",
            data=b"", method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


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


def _build_tool_list(*, purpose: str, agent_id: str | None,
                     mcp_manager=None, breakdown: dict | None = None) -> list[dict]:
    """Return Anthropic-shape tool schemas for the given purpose.

    Thin wrapper over engine.resolve_active_tools — the single source of
    truth (PROMPT_TOOLS_UNIFICATION_PLAN.md). Deferred-group filtering and
    MCP merging happen inside the resolver; we just hand it the discovered-
    tools set from thread-local. If `breakdown` is passed it's filled in place
    with {in_prompt, deferred, excluded} (ground truth of the resolution).
    """
    discovered = engine.get_request_context()._discovered_tools or set()
    return engine.resolve_active_tools(
        purpose=purpose,
        agent_id=agent_id,
        discovered_tools=discovered,
        mcp_manager=mcp_manager,
        is_openai_shape=False,
        breakdown=breakdown,
    )


def _dispatchable_allowed_tools(tools: list[dict], breakdown: dict) -> list[dict]:
    """The Brain-side dispatch enforcement whitelist. A DEFERRED tool is
    hidden from the prompt but still DISPATCHABLE — the model may reach it via
    tool_search (or call it directly if it knows the name), and that must work
    (the documented 'deferral is recoverable' contract). So the whitelist is
    `in_prompt ∪ deferred` — everything EXCEPT hard-`excluded` tools (Websuche
    web-lockout, helpdesk read-only, etc.). Returns the list of allowed NAMES.

    Bug it fixes (chat f2168652): enforcement used only the in-prompt names, so
    a deferred tool the model legitimately called (read_document on an
    attachment turn, gated out by the classifier) was hard-rejected with
    'tool X is not available in this context' — making deferred == deactivated,
    contradicting the gating design + breaking tool_search recovery."""
    names = set(b for b in (breakdown.get("in_prompt") or []))
    names |= set(b for b in (breakdown.get("deferred") or []))
    # MCP tools in the wire list aren't in the breakdown's built-in lists; allow
    # whatever was actually offered too (belt-and-suspenders).
    names |= set(t.get("name", "") for t in tools if t.get("name"))
    return sorted(n for n in names if n)


def _log_wire_tools(tools: list[dict], *, turn_id: str, purpose: str,
                    agent_id: str | None, model: str) -> None:
    """Diagnostic: dump the resolved tool-name list per turn to stdout.

    Gated on `sidecar.tool_list_log` in config.json (default off). Used to
    debug "did the model actually see tool X?" questions — e.g. when a chat
    expected to call `use_skill` instead reached for `exa_search`, this lets
    us verify whether the tool was in the wire payload at all.
    """
    try:
        if not _sidecar_config().get("tool_list_log"):
            return
        names = sorted(t.get("name", "?") for t in tools)
        print(
            f"[wire-tools] turn={turn_id[:8]} agent={agent_id or '-'} "
            f"model={model} purpose={purpose} n={len(names)} :: "
            f"{', '.join(names)}",
            flush=True,
        )
    except Exception:
        pass  # Diagnostic must never break the turn.


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


def _chat_template_kwargs(model: str, thinking_level: str | None) -> dict | None:
    """Decide whether to send `chat_template_kwargs.enable_thinking`.

    oMLX/vLLM-style providers expose `chat_template_kwargs` so callers can
    flip on/off chat-template features the model itself supports. For gemma-4 /
    qwen3 / etc. the chat template emits a reasoning channel unless we pass
    `enable_thinking: false` explicitly. This must mirror the warmup payload
    byte-for-byte (see brain._apply_inference_to_payload), or the KV prefix
    misses and first-turn behavior diverges from primed behavior.

    Returns the dict to forward as `extra_body` on the SDK call, or None
    when the model/provider doesn't need it.
    """
    model_cfg = (engine._models_config or {}).get(model, {}) or {}
    if model_cfg.get("thinking_format") != "reasoning_field":
        return None
    prov_name = model_cfg.get("provider", "") or ""
    if not engine._provider_supports_chat_template_kwargs(prov_name):
        return None
    want_thinking = bool(thinking_level and thinking_level not in ("off", "none"))
    return {"enable_thinking": want_thinking}


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
        # Streaming usage updates ride here, but they're unreliable on
        # OpenAI-translated providers (CLIProxyAPI → Mistral was observed
        # reporting input_tokens that excluded tool_result content in the
        # final round). We now source usage from sidecar `round_end` events
        # instead — those carry `stream.get_final_message().usage`, which is
        # the SDK's authoritative final count for the round.
        return

    if ev_type == "anthropic.message_stop":
        return  # round_end carries the authoritative usage; nothing here.

    # --- Brain-overlay events ---

    if ev_type == "round_start":
        state["round_index"] = data.get("round", state.get("round_index", 0) + 1)
        return

    if ev_type == "round_end":
        # Authoritative per-round usage from the sidecar's assembled
        # final_message. `tokens_in` aggregates the three Anthropic input
        # counters (input + cache_creation + cache_read) so providers that
        # report the full prompt via cache_creation (oMLX /v1/messages
        # always sets input_tokens=0 and puts the prompt size in
        # cache_creation_input_tokens) aren't undercounted.
        usage = data.get("usage") or {}
        if usage:
            tokens_in = (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
            )
            callback("usage", {
                "tokens_in": tokens_in,
                "tokens_out": int(usage.get("output_tokens", 0) or 0),
                "tool_round": data.get("round", state.get("round_index", 0)),
            })
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
            # Forward the tool_use_id so the client can pair this result with its
            # tool_call row by stable id. The `tool_call` event carries the id; if
            # `tool_result` omits it, the client's id-match fails AND its
            # name-fallback is disabled (it only fires when the call row has no
            # id), so the result never attaches to the call — spinner stays,
            # output hidden until reload (where rows are rebuilt id-less from
            # metadata and the name-fallback works). See web/js/chat.js
            # renderToolCall lookahead.
            "tool_use_id": data.get("tool_use_id", ""),
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
    purpose: str = "interactive",
    tool_context: dict,
    sampling: dict,
    thinking_level: str | None,
    max_tokens: int,
    max_rounds: int,
    event_callback: Callable,
    cancel_token: Any,
    timeout_s: float = 1800.0,
    disable_parallel_tool_use: bool = False,
) -> dict:
    """Drive one chat turn through the sidecar.

    `purpose` drives tool resolution via engine.resolve_active_tools
    (PROMPT_TOOLS_UNIFICATION_PLAN.md). The agent_id is read from
    tool_context['agent_id']; MCP tools come from engine._mcp_manager.

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
    system prompt, persisting the assistant message, running the citation
    validator. We do not touch those.
    """
    sid = tool_context.get("session_id") or ""
    nonce = tool_mcp.mint_nonce(sid)
    turn_id = uuid.uuid4().hex
    tool_context = dict(tool_context)  # don't mutate caller's dict
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    # Record the active turn so a Brain restart can re-attach to it. Only
    # interactive (session-bound) turns are tracked — background/blocking
    # callers go through run_turn_blocking and aren't worth resuming.
    if sid:
        try:
            from server_lib.db import ChatDB as _ChatDB
            _ChatDB.set_active_turn(sid, turn_id, model)
        except Exception:
            pass

    _tb: dict = {}
    _tools = _build_tool_list(
        purpose=purpose,
        agent_id=tool_context.get("agent_id") or None,
        mcp_manager=getattr(engine, "_mcp_manager", None),
        breakdown=_tb,
    )
    # Carry the DISPATCHABLE tool names so the Brain-side dispatcher can ENFORCE
    # scope — the model can otherwise call a hard-EXCLUDED tool (Websuche
    # web-lockout, helpdesk read-only). This is in_prompt ∪ deferred: a deferred
    # tool is hidden from the prompt but still dispatchable (tool_search-
    # recoverable), so it must NOT be rejected. tool_mcp.handle_tools_call
    # checks this list; empty/absent = no enforcement (legacy callers).
    tool_context["allowed_tools"] = _dispatchable_allowed_tools(_tools, _tb)
    _log_wire_tools(_tools, turn_id=turn_id, purpose=purpose,
                    agent_id=tool_context.get("agent_id") or None, model=model)
    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "base_url": _normalise_anthropic_base_url(base_url),
        "api_key": api_key,
        "system": system_prompt,
        "messages": _to_anthropic_messages(messages),
        "tools": _tools,
        "max_tokens": int(max_tokens),
        "max_rounds": int(max_rounds),
        "tool_endpoint": tool_endpoint_internal(),
        "tool_endpoint_auth": f"Bearer {nonce}",
        "tool_context": tool_context,
        "turn_id": turn_id,
        "trace_id": tool_context.get("trace_id") or "",
        # `disable_parallel_tool_use`: Anthropic SDK equivalent of OpenAI's
        # `parallel_tool_calls: false`. When True, forces sequential tool
        # use (one tool_use block per round). Some providers' tool_choice
        # path is more reliable than parallel batching.
        "disable_parallel_tool_use": bool(disable_parallel_tool_use),
    }
    # Sampling — only forward what's set, the sidecar omits unset kwargs.
    for key in ("temperature", "top_p", "top_k", "stop_sequences"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    th = _thinking_param(model, thinking_level)
    if th is not None:
        payload["thinking"] = th
    ctk = _chat_template_kwargs(model, thinking_level)
    if ctk is not None:
        payload["chat_template_kwargs"] = ctk

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

    # Local-provider concurrency gate. Pre-sidecar, the native loop wrapped
    # its /chat/completions call in _provider_queue.acquire_if so two local
    # chats (or a chat + the warmup keeper) couldn't hit oMLX/CLIProxyAPI's
    # serialised wire at once. The SDK migration (fdcb655) moved the loop into
    # the sidecar and dropped that gate — only run_model_warmup still acquired,
    # so live chat went ungated and contended with warmup. Re-acquire here.
    # No-op for cloud (max_concurrent<=0). Whole-turn scope: held across all
    # the sidecar's rounds incl. tool execution — coarser than the old
    # per-wire release, but the sidecar gives Brain no per-round wire markers,
    # and on max_concurrent=1 serialising whole turns is the safe default.
    try:
        _prov = engine.resolve_provider_for_model(model) or {}
        _provider_name = _prov.get("provider_name", "") or "default"
    except Exception:
        _provider_name = "default"

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
      with engine.get_provider_queue().acquire_if(
              _provider_name, label=purpose or "interactive",
              session_id=sid or None,
              agent_id=tool_context.get("agent_id") or None,
              user_id=tool_context.get("user_id") or None,
              model=model, event_callback=event_callback,
              cancel_token=cancel_token, timeout=timeout_s):
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
                            # The sidecar sends a traceback alongside the
                            # message; log it so a sidecar-side crash is
                            # diagnosable from prod logs (the message alone —
                            # e.g. "IndexError: list index out of range" — is
                            # not enough to locate the failing line).
                            _tb = data.get("traceback")
                            if _tb:
                                print(f"[sidecar-proxy] turn={turn_id[:8]} "
                                      f"sidecar error traceback:\n{_tb}",
                                      flush=True)
                        elif ev_type == "cancelled":
                            cancelled = True
                continue
            if line.startswith("data: "):
                buf += line[6:]
            elif line.startswith("data:"):
                buf += line[5:]

    except engine.TaskCancelled:
        # Cancelled while waiting in the provider queue (Stop button or admin
        # queue-cancel) — never reached the sidecar. Treat as a cancel, not an
        # error, so the worker's rollback path runs instead of surfacing a
        # "(Sidecar error)" reply.
        cancelled = True
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
        if sid:
            try:
                from server_lib.db import ChatDB as _ChatDB
                _ChatDB.clear_active_turn(sid, turn_id)
            except Exception:
                pass
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
        # Per-round answer-text split for chronological display interleaving.
        "text_segments": final_summary.get("text_segments", []) or [],
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
    purpose: str = "interactive",
    tool_context: dict,
    sampling: dict,
    thinking_level: str | None,
    max_tokens: int,
    max_rounds: int,
    timeout_s: float = 1800.0,
    turn_id: str | None = None,
    forced_tool: dict | None = None,
) -> dict:
    """Non-streaming variant for background callers (scheduler, summariser,
    classifier, refine, ...). Returns the same shape as run_turn() minus the
    live event_callback hook. Phase 3 + 4 use this.

    `turn_id`: callers that need to cancel the run mid-flight (background tasks)
    or attach a live transcript pass a pre-minted id; the sidecar's
    `POST /cancel/<turn_id>` then targets it. Default None → mint as before.

    `forced_tool`: structured-output mode. An Anthropic tool def
    {name, description, input_schema}. When set, the model is offered ONLY this
    tool and forced to call it (tool_choice {type:tool,name}); the sidecar
    captures the tool_use `input` (never dispatches it) and returns it as
    `forced_tool_input`. This is the Anthropic-idiomatic way to get reliable
    schema-valid JSON from local models that drop braces on free-text JSON
    (verified on vllm-metal/Qwen). Overrides the per-purpose tool list.
    """
    sid = tool_context.get("session_id") or ""
    nonce = tool_mcp.mint_nonce(sid)
    turn_id = turn_id or uuid.uuid4().hex
    tool_context = dict(tool_context)
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    if forced_tool:
        # Structured-output mode: offer ONLY the forced tool and constrain the
        # model to it. No dispatch happens (the sidecar captures the input), so
        # `allowed_tools` stays empty — nothing is executable this turn.
        _tools = [forced_tool]
        tool_context["allowed_tools"] = []
    else:
        _tb: dict = {}
        _tools = _build_tool_list(
            purpose=purpose,
            agent_id=tool_context.get("agent_id") or None,
            mcp_manager=getattr(engine, "_mcp_manager", None),
            breakdown=_tb,
        )
        # DISPATCHABLE set = in_prompt ∪ deferred (a deferred tool is hidden but
        # still callable / tool_search-recoverable — must not be hard-rejected).
        # Only hard-EXCLUDED tools are out of scope. See _dispatchable_allowed_tools.
        tool_context["allowed_tools"] = _dispatchable_allowed_tools(_tools, _tb)
    _log_wire_tools(_tools, turn_id=turn_id, purpose=purpose,
                    agent_id=tool_context.get("agent_id") or None, model=model)
    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "base_url": _normalise_anthropic_base_url(base_url),
        "api_key": api_key,
        "system": system_prompt,
        "messages": _to_anthropic_messages(messages),
        "tools": _tools,
        "max_tokens": int(max_tokens),
        "max_rounds": int(max_rounds),
        "tool_endpoint": tool_endpoint_internal(),
        "tool_endpoint_auth": f"Bearer {nonce}",
        "tool_context": tool_context,
        "turn_id": turn_id,
        "trace_id": tool_context.get("trace_id") or "",
    }
    if forced_tool:
        payload["tool_choice"] = {"type": "tool", "name": forced_tool["name"]}
        payload["capture_forced_tool"] = forced_tool["name"]
    for key in ("temperature", "top_p", "top_k", "stop_sequences"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    th = _thinking_param(model, thinking_level)
    if th is not None:
        payload["thinking"] = th
    ctk = _chat_template_kwargs(model, thinking_level)
    if ctk is not None:
        payload["chat_template_kwargs"] = ctk

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
        "forced_tool_input": summary.get("forced_tool_input"),
        "cancelled": False,
        "error": error_msg or summary.get("error"),
        "turn_id": turn_id,
    }


def background_call(
    *,
    messages: list[dict],
    model: str,
    system_prompt: str = "",
    purpose: str = "transform",
    agent_id: str = "main",
    session_id: str = "",
    project: str = "",
    user_id: str | None = None,
    max_tokens: int | None = None,
    max_rounds: int = 1,
    thinking_level: str | None = None,
    timeout_s: float = 1800.0,
    provider_resolver=None,
    turn_id: str | None = None,
    bg_task: bool = False,
    account_cost: bool = True,
    cost_purpose: str | None = None,
    forced_tool: dict | None = None,
    temperature: float | None = None,
) -> dict:
    """Thin convenience wrapper around `run_turn_blocking` for background /
    non-interactive LLM calls (Phase 4).

    `bg_task=True` marks this as a detached background-task run so the tool
    dispatch context carries `current_bg_task` — the run_background_task nesting
    guard reads it to refuse spawning further background tasks (no runaway
    fan-out). Default False (scheduler/summariser/etc. are not bg-tasks).

    Resolves provider + inference params + sampling from the model id, builds
    a minimal `tool_context`, and calls the sidecar. Caller picks the model;
    if the picked model isn't on an Anthropic-shape provider, the sidecar
    returns an empty reply — that's the admin's job to fix in their config.

    Returns the same dict shape as `run_turn_blocking`. Caller decides how to
    handle `error` / `reply`.

    `account_cost` (default True): log this call to the cost ledger CENTRALLY —
    one `cost_log` row per background_call, attributed to `user_id`/`agent_id`,
    even when the cost is $0 (local/free model) or the sidecar reported no usage.
    This is the single seam that makes EVERY background LLM call appear in the
    cost breakdown (so a $0 row that should cost money flags a rate-config gap,
    and a missing row flags a logging gap). Set False ONLY for non-billable
    measurement (e.g. model benchmarking).

    `cost_purpose`: the cost-ledger USE-CASE tag (chat_summary, translate_text,
    kg_extract, …) — SEPARATE from `purpose`, which must stay one of the 5
    tool-resolution purposes in `_VALID_PURPOSES` (interactive / transform /
    memory_summary / research_minimal / helpdesk). Effective tag is: this arg →
    else the request context's `cost_purpose` → else `purpose`. Callers that need
    the usage numbers for display call `brain.account_background_usage(result,
    model, …, log=False)` (compute-only — no second row)."""
    if provider_resolver is None:
        provider_resolver = engine.resolve_provider_for_model
    prov = provider_resolver(model)
    inf = engine.get_inference_params(model)
    _max_tokens = int(max_tokens or inf.get("max_tokens") or engine.get_model_max_output(model))
    _user_id = user_id if user_id is not None else (
        engine.get_request_context().current_user_id or "")
    tool_context = {
        "session_id": session_id,
        "agent_id": agent_id,
        "user_id": _user_id,
        "team_ids": [],
        "project": project,
        # Code-mode working dir (if the caller set it on the request context via
        # apply_domain_context) so background file tools write into working_dir.
        "working_dir": engine.get_request_context().working_dir or "",
        "code_graph_db": engine.get_request_context().code_graph_db or "",
        "note_context": None,
        "workflow_run_id": "",
        "plan_mode": False,
        "research_mode_override": None,
        "execution_overrides": {},
        "attachment_image_model": "",
        "caveman_chat": 0,
        "caveman_system": 0,
        "trace_id": "",
        "bg_task": bool(bg_task),
    }
    sampling = {
        # Explicit `temperature` arg overrides the model's configured value —
        # used by deterministic callers (KG triple extraction needs temp 0.0 for
        # stable, reproducible output; without this a model configured at 0.7
        # made the extracted triple set vary run-to-run, e.g. 229 vs 330 on the
        # same docs).
        "temperature": (temperature if temperature is not None
                        else inf.get("temperature")),
        "top_p": inf.get("top_p"),
        "top_k": inf.get("top_k"),
        "stop_sequences": inf.get("stop") or inf.get("stop_sequences"),
    }
    result = run_turn_blocking(
        messages=messages,
        model=model,
        api_key=prov["api_key"],
        base_url=prov["base_url"],
        system_prompt=system_prompt,
        purpose=purpose,
        tool_context=tool_context,
        sampling=sampling,
        thinking_level=thinking_level,
        max_tokens=_max_tokens,
        max_rounds=max_rounds,
        timeout_s=timeout_s,
        turn_id=turn_id,
        forced_tool=forced_tool,
    )
    # Central cost ledger seam — one row per background_call, even at $0 (local/
    # free) or zero reported usage, so the breakdown is a complete audit. Tagged
    # with `purpose`; attributed to the resolved user/agent. Best-effort: a cost
    # logging hiccup must never fail the actual call.
    if account_cost:
        try:
            usage = (result or {}).get("usage_total") or {}
            ti = (int(usage.get("input_tokens", 0) or 0)
                  + int(usage.get("cache_creation_input_tokens", 0) or 0)
                  + int(usage.get("cache_read_input_tokens", 0) or 0))
            to = int(usage.get("output_tokens", 0) or 0)
            # Cost-ledger bucket, in priority order: explicit cost_purpose arg →
            # request context's cost_purpose (e.g. bg-tasks set 'background_task'
            # while tool-purpose stays 'interactive') → the tool-resolution
            # `purpose`. Kept SEPARATE from `purpose` so the cost tag can be
            # fine-grained without breaking resolve_active_tools' _VALID_PURPOSES.
            _cost_purpose = (cost_purpose
                             or engine.get_request_context().cost_purpose
                             or purpose)
            engine._log_call_cost(model, ti, to, session_id=session_id,
                                  user_id=_user_id, agent_id=agent_id,
                                  purpose=_cost_purpose, api_key=prov.get("api_key", ""))
        except Exception as _ce:
            print(f"[background_call] cost log failed: {_ce}", flush=True)
    return result


def helpdesk_call(
    *,
    messages: list[dict],
    model: str,
    system_prompt: str,
    session_id: str,
    user_id: str = "",
    project: str = "",
    event_callback: Callable,
    cancel_token: Any = None,
    max_rounds: int = 6,
    max_tokens: int | None = None,
    timeout_s: float = 600.0,
) -> dict:
    """Drive one Brainy (helpdesk) turn through the sidecar, STREAMING.

    Like background_call (resolves provider + sampling from the model id) but
    uses the streaming run_turn path so the helpdesk endpoint can relay SSE to
    the browser. Pins purpose='helpdesk' (the fixed read-only tool set) and sets
    helpdesk_mode=True in the tool_context so the dispatch thread can load the
    backend-exclusive brain-agent-guide skill.

    `session_id` scopes the read-only tools (session info / activity) — it is
    NOT used as the sidecar turn's persisted session, so the main chat's
    history, live_stream, and active-turn tracking are untouched.

    `project` (the project NAME, as the main chat carries it) force-scopes
    mempalace_query / KG to that project's `project__<id>` wing — so when the
    user asks Brainy from inside a project, Brainy reads the SAME isolated
    project knowledge the main agent does. Empty when not in a project, leaving
    the wing scope as before (no project knowledge).
    """
    prov = engine.resolve_provider_for_model(model)
    inf = engine.get_inference_params(model)
    _max_tokens = int(max_tokens or inf.get("max_tokens") or engine.get_model_max_output(model))
    tool_context = {
        # Empty session_id => run_turn skips active-turn tracking (no collision
        # with the main chat's resumable-stream bookkeeping). The helpdesk tools
        # read the chat session from `helpdesk_session_id` instead.
        "session_id": "",
        "helpdesk_session_id": session_id,
        "agent_id": "main",
        "user_id": user_id or "",
        "team_ids": [],
        # The project NAME (mempalace_glue resolves name → id and force-scopes
        # to project__<id>); empty => no project knowledge, as before.
        "project": project or "",
        "note_context": None,
        "workflow_run_id": "",
        "plan_mode": False,
        "helpdesk_mode": True,
        "research_mode_override": None,
        "execution_overrides": {},
        "attachment_image_model": "",
        "caveman_chat": 0,
        "caveman_system": 0,
        "trace_id": "",
    }
    sampling = {
        "temperature": inf.get("temperature"),
        "top_p": inf.get("top_p"),
        "top_k": inf.get("top_k"),
        "stop_sequences": inf.get("stop") or inf.get("stop_sequences"),
    }
    result = run_turn(
        messages=messages,
        model=model,
        api_key=prov["api_key"],
        base_url=prov["base_url"],
        system_prompt=system_prompt,
        purpose="helpdesk",
        tool_context=tool_context,
        sampling=sampling,
        thinking_level=None,
        max_tokens=_max_tokens,
        max_rounds=max_rounds,
        event_callback=event_callback,
        cancel_token=cancel_token,
        timeout_s=timeout_s,
    )
    # Central cost ledger — Brainy turns go through run_turn (not background_call),
    # so log here. Attributed to the helpdesk session + user; tagged 'helpdesk'.
    try:
        usage = (result or {}).get("usage_total") or {}
        ti = (int(usage.get("input_tokens", 0) or 0)
              + int(usage.get("cache_creation_input_tokens", 0) or 0)
              + int(usage.get("cache_read_input_tokens", 0) or 0))
        to = int(usage.get("output_tokens", 0) or 0)
        engine._log_call_cost(model, ti, to, session_id=session_id,
                              user_id=user_id or "", agent_id="main",
                              purpose="helpdesk", api_key=prov.get("api_key", ""))
    except Exception as _ce:
        print(f"[helpdesk_call] cost log failed: {_ce}", flush=True)
    return result
