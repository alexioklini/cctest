"""In-process OpenAI agentic loop (Option C — replaces the sidecar subprocess).

The chat worker calls `run_turn(...)` / background callers call `run_turn_blocking(...)`
DIRECTLY on their own thread. There is no subprocess, no HTTP hop for tool
dispatch, and no Anthropic SDK. We speak the OpenAI `/v1/chat/completions` wire
shape (the only path where Mistral prompt caching via CLIProxyAPI reliably hits
~95%, keyed by `prompt_cache_key`).

Design mirrors `sidecar/sidecar.py:run_turn_streaming` but:
  - OpenAI request shape: system → messages[0], tools → {type:function,...},
    thinking → reasoning_effort / chat_template_kwargs, prompt_cache_key top-level.
  - Single stream drain: parse `choices[].delta`, accumulate tool_calls by index.
  - Tool dispatch is a direct `engine.TOOL_DISPATCH[name](args)` call on THIS
    thread (which already holds the RequestContext) — no nonce, no context rebuild.
  - Emits the SAME Brain event vocabulary the chat worker's event_callback
    consumes (text_delta / thinking_* / tool_call / tool_result / usage /
    empty_round_nudge / cancelled / error) — i.e. the shape the old
    sidecar_proxy._translate_anthropic_event produced, so all downstream
    plumbing (LiveStream, persistence, citation validator, cost ledger) is
    unchanged.

Crash isolation: each round's stream + each tool dispatch is wrapped so one bad
turn can't wedge the worker; the caller (run_turn) wraps the whole loop in
try/except and always emits a terminal state. Stream bytes are bounded and the
round count is capped (max_rounds + the caller's 1.5× hard-stop lives upstream).
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import urllib.request
from typing import Any, Callable

import brain as engine


# End-of-sequence tokens some local models emit verbatim as plain text instead
# of using them as a stop signal. Stripped so "<eos>"-only rounds collapse to ''
# and the empty-round nudge logic treats them as no answer. (Ported verbatim
# from sidecar._visible_text.)
_EOS_TOKENS = (
    "<eos>", "<end_of_turn>", "<|endoftext|>", "<|im_end|>", "<|eot_id|>",
    "<|end|>", "</s>",
)

# Bound the per-round stream so a runaway/malformed response can't OOM the
# worker (the crash-isolation trade-off called out in the handover §3).
_MAX_STREAM_BYTES = 64 * 1024 * 1024  # 64 MB per round

EMPTY_NUDGE_MAX = 3
EMPTY_GIVEUP_TEXT = ("No response was returned. Please modify your "
                     "request or change the model.")


def _visible_text(text: str) -> str:
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


def _parse_tool_input_json(buf: str):
    """Parse a streamed tool-call arguments buffer. Returns the dict on success,
    None on unrecoverable failure. Tolerant repairs for the failure modes large
    LLM-emitted arguments hit (trailing comma, unterminated final string from a
    truncated stream). Ported from sidecar._parse_tool_input_json."""
    if not buf:
        return None
    try:
        v = json.loads(buf)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    try:
        repaired = re.sub(r",\s*}\s*$", "}", buf.strip())
        v = json.loads(repaired)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    try:
        s = buf.strip()
        if s.count('"') % 2 == 1:
            s = s + '"'
        if not s.endswith("}"):
            s = s + "}"
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return None


# ---------- Request build ----------

def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert Brain's stored session.messages to the OpenAI wire shape.

    Brain persists each turn as one row: role=user|assistant|thinking with a
    string `content` (or a list of content blocks for image-bearing user turns).
    We DROP `thinking` rows (UI-only, never on the wire — same as the old
    _to_anthropic_messages) and keep OpenAI `image_url` blocks as-is (they're
    already OpenAI shape; no conversion needed, unlike the Anthropic path).
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
                if not isinstance(blk, dict):
                    continue
                btype = blk.get("type", "")
                if btype == "text":
                    blocks.append({"type": "text", "text": blk.get("text", "")})
                elif btype == "image_url":
                    # Already OpenAI shape — pass through verbatim.
                    blocks.append({"type": "image_url",
                                   "image_url": blk.get("image_url") or {}})
                elif btype == "image":
                    # Anthropic-shape image (legacy stored form) → OpenAI data URI.
                    src = blk.get("source") or {}
                    if src.get("type") == "base64":
                        mime = src.get("media_type", "image/png")
                        b64 = src.get("data", "")
                        blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        })
            if blocks:
                out.append({"role": role, "content": blocks})
            else:
                out.append({"role": role, "content": ""})
    return out


def build_openai_payload(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    sampling: dict,
    thinking_level: str | None,
    disable_parallel_tool_use: bool,
    prompt_cache_key: str,
    forced_tool: dict | None,
) -> dict:
    """Assemble the OpenAI /v1/chat/completions payload for one round.

    The per-round `messages` list (system + history + intermediate tool
    exchanges) is passed in by the loop; everything else is stable across rounds.
    """
    wire_messages: list[dict] = []
    if system_prompt:
        wire_messages.append({"role": "system", "content": system_prompt})
    wire_messages.extend(messages)

    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "max_tokens": int(max_tokens),
        "messages": wire_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        # Parallel tool use: disable_parallel_tool_use → parallel_tool_calls:false
        # (OpenAI-idiomatic; the sidecar's Anthropic tool_choice.disable_parallel
        # maps here). Otherwise honour the per-model default (warmup mirrors this).
        if disable_parallel_tool_use:
            payload["parallel_tool_calls"] = False
        else:
            model_cfg = engine.resolve_model_settings(model)
            if model_cfg.get("parallel_tool_calls", True):
                payload["parallel_tool_calls"] = True

    # Forced-tool (structured-output) mode: offer ONLY the forced tool and
    # constrain the model to it via tool_choice. The model's single tool_call
    # `arguments` IS the structured result — captured, never dispatched.
    if forced_tool:
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": forced_tool["name"]},
        }

    # Sampling — only forward what's set. Mirror the sidecar's "omit unset" rule.
    for key in ("temperature", "top_p"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    # top_k / stop are provider-specific; route through _apply_inference_to_payload
    # below so oMLX gets top_k and everyone gets stop.
    if sampling.get("stop_sequences"):
        payload["stop"] = sampling["stop_sequences"]

    # Thinking + oMLX chat_template_kwargs + top_k, byte-for-byte matching the
    # warmup path (brain._apply_inference_to_payload) so the KV prefix hits.
    prov = engine.resolve_provider_for_model(model) or {}
    provider_name = prov.get("provider_name", "") or ""
    _inf = dict(engine.get_inference_params(model))
    # Overlay the resolved thinking level so _apply_inference_to_payload renders
    # reasoning_effort (cloud) / chat_template_kwargs.enable_thinking (oMLX).
    if thinking_level and thinking_level not in ("off", "none"):
        _inf["thinking_level"] = thinking_level
    elif thinking_level in ("off", "none"):
        _inf["thinking_level"] = "none"
    engine._apply_inference_to_payload(payload, _inf, provider_name, scoped_model=model)

    # prompt_cache_key: top-level OpenAI field. CLIProxyAPI → Mistral binds the
    # request to a shared prompt-cache prefix (cache reads bill ~0.1×). This is
    # the whole payoff of the OpenAI path.
    if prompt_cache_key:
        payload["prompt_cache_key"] = str(prompt_cache_key)

    return payload


# ---------- Stream drain ----------

class _RoundResult:
    __slots__ = ("text", "reasoning", "tool_calls", "usage", "finish_reason",
                 "raw_bytes")

    def __init__(self):
        self.text = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}  # index -> {id,name,args}
        self.usage: dict = {}
        self.finish_reason = ""
        self.raw_bytes = 0


def _drain_openai_stream(resp, emit_delta: Callable[[str, str], None],
                         round_no: int) -> _RoundResult:
    """Drain one OpenAI SSE response. `emit_delta(kind, text)` fires for live
    content ('text') and reasoning ('thinking') deltas as they arrive.

    Accumulates tool_calls by their `index` (OpenAI streams function.name once,
    then function.arguments in fragments). Returns the assembled _RoundResult.
    """
    rr = _RoundResult()
    buf = b""
    for raw in resp:
        rr.raw_bytes += len(raw)
        if rr.raw_bytes > _MAX_STREAM_BYTES:
            raise RuntimeError(
                f"stream exceeded {_MAX_STREAM_BYTES} bytes in round {round_no}")
        buf += raw
        # Process complete lines (SSE line-buffering across TCP chunks).
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            s = line.decode("utf-8", "replace").strip()
            if not s or s.startswith(":"):
                continue
            if not s.startswith("data:"):
                continue
            payload = s[5:].strip()
            if payload == "[DONE]":
                return rr
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            u = ev.get("usage")
            if u:
                rr.usage = u
            for ch in ev.get("choices", []) or []:
                delta = ch.get("delta") or {}
                fr = ch.get("finish_reason")
                if fr:
                    rr.finish_reason = fr
                content = delta.get("content")
                if content:
                    rr.text += content
                    emit_delta("text", content)
                # reasoning_content (oMLX/DeepSeek/Gemini-via-cliproxy). Some
                # providers use `reasoning` instead — accept both.
                reasoning = delta.get("reasoning_content")
                if reasoning is None:
                    reasoning = delta.get("reasoning")
                if reasoning:
                    rr.reasoning += reasoning
                    emit_delta("thinking", reasoning)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = rr.tool_calls.setdefault(
                        idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
    return rr


# ---------- Tool dispatch (direct, in-process) ----------

def _looks_like_error(s: str) -> bool:
    if not s:
        return False
    return s.lstrip()[:32].startswith('{"error"')


def _looks_like_error_dict(obj) -> bool:
    return isinstance(obj, dict) and "error" in obj and len(obj) <= 4


def dispatch_tool(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool by name on THIS thread (which holds the RequestContext).

    Returns (result_string, is_error). Built-in TOOL_DISPATCH first, then MCP.
    No truncation / summarisation — the raw string goes back to the model as a
    tool_result. (Ported from tool_mcp._dispatch; the context rebuild is gone —
    the worker thread already set the context via `with request_context()`.)
    """
    fn = engine.TOOL_DISPATCH.get(name)
    if fn is not None:
        try:
            raw = fn(args)
        except Exception as e:
            return (json.dumps({
                "error": f"tool crashed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            }, ensure_ascii=False), True)
        if isinstance(raw, str):
            return raw, _looks_like_error(raw)
        return json.dumps(raw, ensure_ascii=False), _looks_like_error_dict(raw)

    mcp_mgr = engine.get_request_context().mcp_manager or engine._mcp_manager
    if mcp_mgr is not None:
        try:
            for td in mcp_mgr.get_tool_definitions():
                if td.get("name") == name:
                    raw = mcp_mgr.call_tool(name, args)
                    if isinstance(raw, str):
                        return raw, _looks_like_error(raw)
                    return json.dumps(raw, ensure_ascii=False), _looks_like_error_dict(raw)
        except Exception as e:
            return (json.dumps({
                "error": f"mcp tool crashed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            }, ensure_ascii=False), True)
    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False), True


# ---------- The core loop ----------

def run_loop(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    allowed_tools: list[str],
    max_tokens: int,
    max_rounds: int,
    sampling: dict,
    thinking_level: str | None,
    disable_parallel_tool_use: bool,
    prompt_cache_key: str,
    forced_tool: dict | None,
    api_key: str,
    base_url: str,
    emit: Callable[[str, dict], None],
    is_cancelled: Callable[[], bool],
    tool_use_id_setter: Callable[[str], None] | None = None,
) -> dict:
    """Drive one turn's rounds. `emit(type, data)` fires Brain-vocabulary events;
    `is_cancelled()` is polled between rounds and after each stream drain.

    Returns a summary dict shaped like the old sidecar `done` payload:
      {final_text, text_segments, stop_reason, rounds, tool_calls_total,
       usage_total, tool_events, forced_tool_input?}
    `usage_total` uses the Anthropic-shape keys the callers already read
    (input_tokens / output_tokens / cache_read_input_tokens) so the cost-ledger
    extraction in sidecar_proxy.background_call/helpdesk_call keeps working.
    """
    loop_messages = _to_openai_messages(messages)
    endpoint = base_url.rstrip("/") + "/chat/completions"
    headers = engine.make_headers(api_key)

    usage_total = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rounds: list[dict] = []
    tool_calls_total = 0
    tool_events: list[dict] = []
    text_segments: list[str] = []
    text_round_segments: list[dict] = []
    final_text = ""
    final_stop_reason = ""
    forced_tool_input: dict | None = None
    empty_nudges = 0

    allowed_set = set(allowed_tools or [])

    for round_idx in range(max_rounds):
        round_no = round_idx + 1
        if is_cancelled():
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "round_start"})
            break

        payload = build_openai_payload(
            model=model, system_prompt=system_prompt, messages=loop_messages,
            tools=tools, max_tokens=max_tokens, sampling=sampling,
            thinking_level=thinking_level,
            disable_parallel_tool_use=disable_parallel_tool_use,
            prompt_cache_key=prompt_cache_key, forced_tool=forced_tool)

        # --- live-delta emit closures (mirror the translated sidecar events) ---
        _thinking_started = {"v": False}

        def _emit_delta(kind: str, text: str):
            if kind == "text":
                emit("text_delta", {"text": text})
            elif kind == "thinking":
                if not _thinking_started["v"]:
                    _thinking_started["v"] = True
                    emit("thinking_start", {"tool_round": round_no})
                emit("thinking_delta", {"text": text, "tool_round": round_no})

        # --- stream one round ---
        # A watcher thread closes the response socket the moment is_cancelled()
        # flips, so a mid-generation cancel breaks the blocking read (parity with
        # the sidecar's _watch_cancel — an in-process loop otherwise couldn't
        # interrupt a long stream between round boundaries).
        resp = None
        _stop_watch = threading.Event()

        def _watch():
            while not _stop_watch.wait(0.4):
                if is_cancelled():
                    try:
                        if resp is not None:
                            resp.close()
                    except Exception:
                        pass
                    return

        try:
            req = urllib.request.Request(
                endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=1800)
            _wt = threading.Thread(target=_watch, daemon=True,
                                   name=f"llm-loop-cancel-{round_no}")
            _wt.start()
            rr = _drain_openai_stream(resp, _emit_delta, round_no)
        except Exception as e:
            _stop_watch.set()
            if is_cancelled():
                final_stop_reason = "cancelled"
                emit("cancelled", {"round": round_no, "phase": "stream"})
                break
            emit("error", {"message": f"{type(e).__name__}: {e}",
                           "round": round_no,
                           "traceback": traceback.format_exc()[-2000:]})
            final_stop_reason = "api_error"
            break
        finally:
            _stop_watch.set()

        if _thinking_started["v"]:
            emit("thinking_done", {"tool_round": round_no})

        # --- usage: split cache_read from full-price input (the v9.245.0 split) ---
        u = rr.usage or {}
        if u:
            prompt_tokens = int(u.get("prompt_tokens", 0) or 0)
            details = u.get("prompt_tokens_details") or {}
            cached = int(details.get("cached_tokens", 0) or 0)
            completion = int(u.get("completion_tokens", 0) or 0)
            # tokens_in = full-price remainder (prompt minus cache hit).
            fresh_in = max(0, prompt_tokens - cached)
            usage_total["input_tokens"] += fresh_in
            usage_total["output_tokens"] += completion
            usage_total["cache_read_input_tokens"] += cached
            emit("usage", {
                "tokens_in": fresh_in,
                "tokens_out": completion,
                "cache_read_tokens": cached,
                "tool_round": round_no,
            })

        # --- assemble content + tool calls ---
        tool_uses = []
        for idx in sorted(rr.tool_calls.keys()):
            slot = rr.tool_calls[idx]
            if not slot.get("name"):
                continue
            parsed = _parse_tool_input_json(slot["args"])
            parse_err = ""
            if parsed is None:
                if slot["args"].strip():
                    parse_err = (f"the streamed tool arguments were not valid "
                                 f"JSON ({len(slot['args'])} chars received)")
                parsed = {}
            tool_uses.append({
                "id": slot.get("id") or f"call_{round_no}_{idx}",
                "name": slot["name"],
                "input": parsed,
                "parse_error": parse_err,
            })

        round_visible = _visible_text(rr.text)
        if round_visible:
            text_segments.append(round_visible)
            text_round_segments.append({"round": round_no, "text": round_visible})
            final_text = "\n\n".join(text_segments)

        # --- append the assistant turn to the wire history ---
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        assistant_msg["content"] = rr.text or None
        if tool_uses:
            assistant_msg["tool_calls"] = [{
                "id": tu["id"],
                "type": "function",
                "function": {"name": tu["name"],
                             "arguments": json.dumps(tu["input"], ensure_ascii=False)},
            } for tu in tool_uses]
        if not rr.text and not tool_uses:
            # Empty assistant turn — pad so role alternation stays valid (only
            # happens on the empty-nudge path; the placeholder never reaches DB).
            assistant_msg["content"] = " "
        loop_messages.append(assistant_msg)

        rounds.append({
            "round": round_no, "stop_reason": rr.finish_reason,
            "content_chars": len(rr.text), "tool_uses": len(tool_uses),
        })

        # --- forced-tool capture: harvest input, finish (do NOT dispatch) ---
        if forced_tool and tool_uses:
            for tu in tool_uses:
                if tu["name"] == forced_tool["name"]:
                    forced_tool_input = tu["input"] or {}
                    final_stop_reason = "forced_tool"
                    break
            if forced_tool_input is not None:
                break

        if not tool_uses:
            if round_visible:
                final_stop_reason = rr.finish_reason
                break
            # Empty terminating round: nudge and continue, capped.
            if empty_nudges < EMPTY_NUDGE_MAX:
                empty_nudges += 1
                emit("empty_round_nudge", {
                    "round": round_no, "attempt": empty_nudges,
                    "max": EMPTY_NUDGE_MAX,
                })
                loop_messages.append({
                    "role": "user",
                    "content": "Please provide your answer now based on the "
                               "information gathered so far.",
                })
                continue
            final_text = EMPTY_GIVEUP_TEXT
            final_stop_reason = "empty_after_nudges"
            break

        if is_cancelled():
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "post_round"})
            break

        # --- dispatch tools, append one message per tool_result (OpenAI: role=tool) ---
        for tu in tool_uses:
            tool_calls_total += 1
            tu_args = tu["input"] or {}
            emit("tool_call", {
                "name": tu["name"], "args": tu_args,
                "tool_round": round_no, "tool_use_id": tu["id"],
            })
            if tool_use_id_setter is not None:
                try:
                    tool_use_id_setter(tu["id"])
                except Exception:
                    pass

            if tu["parse_error"]:
                result_str = json.dumps({
                    "error": f"tool '{tu['name']}': {tu['parse_error']}. Re-send "
                             f"this tool call with valid JSON arguments (the "
                             f"previous arguments did not arrive intact).",
                }, ensure_ascii=False)
                is_error, elapsed = True, 0.0
            elif allowed_set and tu["name"] not in allowed_set:
                # Enforce the per-turn tool scope (Websuche lockout, helpdesk
                # read-only). A deferred tool is in allowed_set (in_prompt ∪
                # deferred), so only hard-excluded tools are rejected here.
                result_str = json.dumps({
                    "error": f"tool '{tu['name']}' is not available in this "
                             f"context. Available: "
                             f"{', '.join(sorted(allowed_set))}.",
                }, ensure_ascii=False)
                is_error, elapsed = True, 0.0
            else:
                # Instruction-gen transparency hook (parity with tool_mcp).
                try:
                    from engine import instruction_gen as _ig
                    _ig.note_tool_call(
                        engine.get_request_context().current_session_id or "",
                        tu["name"], tu_args)
                except Exception:
                    pass
                t0 = time.time()
                result_str, is_error = dispatch_tool(tu["name"], tu_args)
                elapsed = time.time() - t0

            emit("tool_result", {
                "name": tu["name"], "tool_use_id": tu["id"],
                "result": result_str, "tool_round": round_no,
                "elapsed_ms": int(elapsed * 1000), "is_error": is_error,
            })
            tool_events.append({
                "round": round_no, "name": tu["name"], "args": tu_args,
                "elapsed_ms": int(elapsed * 1000),
                "result_chars": len(result_str), "is_error": is_error,
                "result_text": (result_str or "")[:100000],
            })
            loop_messages.append({
                "role": "tool", "tool_call_id": tu["id"], "content": result_str,
            })
    else:
        final_stop_reason = "max_rounds"
        if not final_text.strip():
            final_text = EMPTY_GIVEUP_TEXT

    summary = {
        "final_text": final_text,
        "text_segments": text_round_segments,
        "stop_reason": final_stop_reason,
        "rounds": len(rounds),
        "tool_calls_total": tool_calls_total,
        "usage_total": usage_total,
        "tool_events": tool_events,
    }
    if forced_tool_input is not None:
        summary["forced_tool_input"] = forced_tool_input
    return summary
