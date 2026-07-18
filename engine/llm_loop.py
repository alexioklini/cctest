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

# ── Stream-stability knobs (9.277.0) ─────────────────────────────────────────
# The Anthropic SDK (deleted with the sidecar, v9.247.0) silently provided
# incomplete-stream detection and connection retries; the bare urllib loop
# replaced it with nothing — a mid-generation upstream drop was accepted as a
# FINISHED answer (the user had to type "continue" by hand), and any transient
# connect flake hard-errored the turn. These reinstate both behaviors.
STREAM_RESUME_MAX = 2   # auto-continue attempts after a truncated stream
CONNECT_RETRIES = 2     # extra connect attempts (only before any byte arrived)
_RESUME_NUDGE = (
    "Your previous reply was cut off by a connection error mid-stream. "
    "Continue your answer EXACTLY where it stopped — do not repeat anything "
    "you already wrote, do not apologize, do not summarize; continue "
    "seamlessly from the last character.")


def _is_retryable_connect_error(e: Exception) -> bool:
    """Connect-phase errors worth retrying: transient network failures and
    retry-safe HTTP statuses. Other 4xx (auth, bad request) fail immediately."""
    import http.client
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (408, 425, 429, 500, 502, 503, 504)
    return isinstance(e, (urllib.error.URLError, http.client.HTTPException,
                          ConnectionError, TimeoutError, OSError))


def _http_error_body(e: Exception) -> str:
    """Best-effort body snippet of an HTTPError for diagnosable turn errors
    (the provider's actual complaint instead of a bare status line)."""
    try:
        import urllib.error
        if isinstance(e, urllib.error.HTTPError):
            return (e.read() or b"")[:500].decode("utf-8", "replace").strip()
    except Exception:
        pass
    return ""


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


def _block_text(v) -> str:
    """Flatten a mistral_blocks `text`/`thinking` value to a plain string.
    The value may be a str, or a list of {type:'text', text:'..'} sub-blocks."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        out = []
        for b in v:
            if isinstance(b, dict):
                out.append(b.get("text") or b.get("thinking") or "")
            elif isinstance(b, str):
                out.append(b)
        return "".join(out)
    return ""


def _split_content(content) -> tuple[str, str]:
    """Normalise an OpenAI `delta.content` into (visible_text, reasoning_text).

    Plain string → (content, ''). Mistral `mistral_blocks` list shape →
    text blocks joined into visible_text, thinking blocks into reasoning_text.
    Anything unexpected coerces to str so the caller never does str += non-str."""
    if isinstance(content, str):
        return content, ""
    if isinstance(content, list):
        txt, think = [], []
        for blk in content:
            if not isinstance(blk, dict):
                if blk:
                    txt.append(str(blk))
                continue
            btype = blk.get("type", "")
            if btype == "thinking":
                think.append(_block_text(blk.get("thinking")))
            elif btype == "text":
                txt.append(_block_text(blk.get("text")))
            else:
                # Unknown block — prefer any text-ish field, don't crash.
                txt.append(_block_text(blk.get("text") or blk.get("content")))
        return "".join(txt), "".join(think)
    return (str(content) if content else ""), ""


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
                 "raw_bytes", "got_done", "error_payload", "_anth_cache_read")

    def __init__(self):
        self.text = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}  # index -> {id,name,args}
        self.usage: dict = {}
        self.finish_reason = ""
        self.raw_bytes = 0
        self._anth_cache_read = 0  # Anthropic-wire: cache_read_input_tokens
        # True iff the stream terminated properly ([DONE] marker seen). False
        # means the upstream socket ended mid-generation — the round result is
        # a TRUNCATED partial, not a finished answer (the "user must type
        # continue" failure mode; run_loop auto-resumes it).
        self.got_done = False
        # A provider error delivered INSIDE the 200-SSE stream
        # (`data: {"error": ...}`). Previously silently skipped (no `choices`)
        # → surfaced as the misleading empty-after-nudges give-up text.
        self.error_payload = None


def _drain_openai_stream(resp, emit_delta: Callable[[str, str], None],
                         round_no: int) -> _RoundResult:
    """Drain one OpenAI SSE response. `emit_delta(kind, text)` fires for live
    content ('text') and reasoning ('thinking') deltas as they arrive.

    Accumulates tool_calls by their `index` (OpenAI streams function.name once,
    then function.arguments in fragments). Returns the assembled _RoundResult.

    A socket death mid-stream returns the PARTIAL result (got_done False)
    instead of raising — the caller distinguishes truncation from a finished
    round via `rr.got_done` and auto-resumes. Only the byte-bound guard raises.
    """
    rr = _RoundResult()
    try:
        _drain_openai_stream_inner(resp, emit_delta, round_no, rr)
    except RuntimeError:
        raise  # byte-bound guard — a runaway stream must still kill the round
    except Exception:
        # Upstream socket died mid-generation (reset, tunnel drop, provider
        # kill, cancel-watcher close). rr holds everything received so far;
        # got_done stays False → run_loop treats the round as TRUNCATED.
        pass
    return rr


def _drain_openai_stream_inner(resp, emit_delta, round_no: int,
                               rr: _RoundResult) -> None:
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
                rr.got_done = True
                return
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            if isinstance(ev, dict) and ev.get("error") and not ev.get("choices"):
                # Provider error inside the 200-SSE stream — surface it instead
                # of draining to an empty round (which would read as "the model
                # said nothing" and trigger the nudge loop).
                rr.error_payload = ev.get("error")
                return
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
                    # `content` is usually a plain string, but Mistral's
                    # `mistral_blocks` reasoning path (magistral / mistral-small
                    # 2603+ / mistral-medium 3.x+ with reasoning_effort set)
                    # streams it as a LIST of blocks:
                    #   [{"type":"thinking","thinking":[{"type":"text","text":..}]}]
                    #   [{"type":"text","text":".."}]
                    # Splitting it: thinking blocks → reasoning stream, text
                    # blocks → visible text. (Without this we'd do `str += list`
                    # → TypeError: can only concatenate str (not "list") to str.)
                    _txt, _think = _split_content(content)
                    if _txt:
                        rr.text += _txt
                        emit_delta("text", _txt)
                    if _think:
                        rr.reasoning += _think
                        emit_delta("thinking", _think)
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
    # Stream ended (EOF) without a [DONE] marker: rr.got_done stays False —
    # the caller treats the round as truncated.


# ---------- Anthropic wire adapter (wire_api="anthropic") ----------
#
# Some providers expose a Messages-API endpoint (`/v1/messages`) that behaves
# DIFFERENTLY from their OpenAI-compat `/chat/completions` for the same upstream
# model. Concretely: the Kimi coding plan (kimi-for-coding = K2.7 Code, a
# thinking-ONLY model) can be told to skip thinking via `thinking:{type:"disabled"}`
# on the Anthropic endpoint — combined with tools — whereas its OpenAI endpoint
# rejects `reasoning_effort:"none"` (400) and is unstable with tools. This
# adapter speaks the Anthropic wire for those providers, filling the SAME
# `_RoundResult` so everything downstream in run_loop is unchanged. Selected per
# provider via the `wire_api` config flag (default "openai").


def build_anthropic_payload(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    sampling: dict,
    thinking_level: str | None,
    forced_tool: dict | None,
) -> dict:
    """Assemble the Anthropic /v1/messages payload for one round, from the same
    inputs build_openai_payload takes. `messages` is the OpenAI-shape loop list
    (role/content + tool exchanges); we translate to Anthropic message blocks.
    """
    anth_messages = _openai_messages_to_anthropic(messages)

    payload: dict[str, Any] = {
        "model": engine.get_api_model_id(model),
        "max_tokens": int(max_tokens),
        "messages": anth_messages,
        "stream": True,
    }
    if system_prompt:
        payload["system"] = system_prompt

    # Thinking OFF unless the UI asked for a level. Anthropic-shape switch is
    # thinking:{type} — "disabled" is what the Kimi coding endpoint accepts to
    # actually suppress reasoning (reasoning_effort:"none" is rejected there).
    if thinking_level and thinking_level not in ("off", "none"):
        # budget_tokens must be < max_tokens; give reasoning a generous share.
        _budget = max(1024, int(max_tokens) // 2)
        payload["thinking"] = {"type": "enabled", "budget_tokens": _budget}
    else:
        payload["thinking"] = {"type": "disabled"}

    if tools:
        payload["tools"] = _openai_tools_to_anthropic(tools)
    if forced_tool:
        payload["tools"] = _openai_tools_to_anthropic([{
            "type": "function", "function": forced_tool}])
        payload["tool_choice"] = {"type": "tool", "name": forced_tool["name"]}

    # Sampling: Anthropic accepts temperature/top_p/top_k at top level.
    for key in ("temperature", "top_p", "top_k"):
        if key in sampling and sampling[key] is not None:
            payload[key] = sampling[key]
    if sampling.get("stop_sequences"):
        payload["stop_sequences"] = sampling["stop_sequences"]

    return payload


def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """OpenAI {type:function, function:{name,description,parameters}} →
    Anthropic {name, description, input_schema}."""
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) and "function" in t else t
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description", "") or "",
            "input_schema": fn.get("parameters")
            or {"type": "object", "properties": {}},
        })
    return out


def _openai_messages_to_anthropic(messages: list[dict]) -> list[dict]:
    """Translate the OpenAI-shape loop messages (which include assistant
    tool_calls and role:"tool" results) into Anthropic message blocks.

    - assistant text + tool_calls → one assistant message with text +
      tool_use blocks
    - role:"tool" → user message with a tool_result block (Anthropic carries
      tool results in the USER turn)
    - system messages are handled by the top-level `system` field, not here
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            continue  # top-level system field
        if role == "tool":
            out.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or m.get("tool_use_id") or "",
                "content": content if isinstance(content, str) else _block_text(content),
            }]})
            continue
        if role == "assistant":
            blocks: list[dict] = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                _t = _block_text(content)
                if _t:
                    blocks.append({"type": "text", "text": _t})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    parsed = _parse_tool_input_json(args)
                    args = parsed if parsed is not None else {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "input": args or {},
                })
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            out.append({"role": "assistant", "content": blocks})
            continue
        # user (or anything else) → plain content
        if isinstance(content, str):
            out.append({"role": "user", "content": content})
        elif isinstance(content, list):
            out.append({"role": "user", "content": content})
        else:
            out.append({"role": "user", "content": str(content or "")})
    return out


def _drain_anthropic_stream(resp, emit_delta: Callable[[str, str], None],
                            round_no: int) -> _RoundResult:
    """Drain one Anthropic /v1/messages SSE response into the SAME _RoundResult
    shape the OpenAI drain produces, so run_loop is wire-agnostic downstream.

    Anthropic event vocabulary: message_start / content_block_start (text |
    thinking | tool_use) / content_block_delta (text_delta | thinking_delta |
    input_json_delta) / content_block_stop / message_delta (stop_reason +
    output usage) / message_stop. NB: this endpoint emits `event:<type>` with NO
    space after the colon; we key off the `type` field in the data line anyway.
    """
    rr = _RoundResult()
    try:
        _drain_anthropic_stream_inner(resp, emit_delta, round_no, rr)
    except RuntimeError:
        raise
    except Exception:
        pass
    return rr


def _drain_anthropic_stream_inner(resp, emit_delta, round_no: int,
                                  rr: _RoundResult) -> None:
    buf = b""
    # index → tool slot, mirroring the OpenAI drain's rr.tool_calls (by index).
    block_kind: dict[int, str] = {}
    in_tokens = 0
    for raw in resp:
        rr.raw_bytes += len(raw)
        if rr.raw_bytes > _MAX_STREAM_BYTES:
            raise RuntimeError(
                f"stream exceeded {_MAX_STREAM_BYTES} bytes in round {round_no}")
        buf += raw
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            s = line.decode("utf-8", "replace").strip()
            if not s or s.startswith(":") or s.startswith("event:"):
                continue
            if not s.startswith("data:"):
                continue
            payload = s[5:].strip()
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            etype = ev.get("type")
            if etype == "error" or (ev.get("error") and not etype):
                rr.error_payload = ev.get("error") or ev
                return
            if etype == "message_start":
                u = (ev.get("message") or {}).get("usage") or {}
                in_tokens = int(u.get("input_tokens", 0) or 0)
                # cache read reported on the input side.
                rr._anth_cache_read = int(  # type: ignore[attr-defined]
                    u.get("cache_read_input_tokens", 0) or 0)
            elif etype == "content_block_start":
                idx = ev.get("index", 0)
                cb = ev.get("content_block") or {}
                kind = cb.get("type")
                block_kind[idx] = kind
                if kind == "tool_use":
                    slot = rr.tool_calls.setdefault(
                        idx, {"id": "", "name": "", "args": ""})
                    slot["id"] = cb.get("id") or slot["id"]
                    slot["name"] = cb.get("name") or slot["name"]
            elif etype == "content_block_delta":
                idx = ev.get("index", 0)
                delta = ev.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    t = delta.get("text") or ""
                    if t:
                        rr.text += t
                        emit_delta("text", t)
                elif dtype == "thinking_delta":
                    t = delta.get("thinking") or ""
                    if t:
                        rr.reasoning += t
                        emit_delta("thinking", t)
                elif dtype == "input_json_delta":
                    slot = rr.tool_calls.setdefault(
                        idx, {"id": "", "name": "", "args": ""})
                    slot["args"] += delta.get("partial_json") or ""
            elif etype == "message_delta":
                d = ev.get("delta") or {}
                if d.get("stop_reason"):
                    rr.finish_reason = d["stop_reason"]
                u = ev.get("usage") or {}
                out_tokens = int(u.get("output_tokens", 0) or 0)
                # cache_read_input_tokens is reported here in message_delta, NOT
                # in message_start (verified against api.kimi.com: message_start
                # always carries cache_read=0, the real value lands in the final
                # message_delta). Prefer the delta value when present, so cache
                # hits aren't silently reported as 0 (the k3 0%-caching bug).
                if "cache_read_input_tokens" in u:
                    rr._anth_cache_read = int(  # type: ignore[attr-defined]
                        u.get("cache_read_input_tokens", 0) or 0)
                # message_delta also carries a corrected NON-cached input_tokens
                # (message_start over-reports it as the full prompt). Prefer it
                # when present so prompt_tokens isn't double-counted once cached
                # is added back below.
                if "input_tokens" in u:
                    in_tokens = int(u.get("input_tokens", 0) or 0)
                # Assemble an OpenAI-shape usage dict so run_loop's existing
                # split (prompt/cached/completion) works unchanged.
                cached = getattr(rr, "_anth_cache_read", 0) or 0
                rr.usage = {
                    "prompt_tokens": in_tokens + cached,
                    "prompt_tokens_details": {"cached_tokens": cached},
                    "completion_tokens": out_tokens,
                }
            elif etype == "message_stop":
                rr.got_done = True
                return


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
    # Web-egress gate (L4): in anonymising sessions, refuse web-tool calls
    # whose args contain protected values / pseudonyms BEFORE anything leaves
    # the machine. Never raises; no-op for non-web tools and non-anonymising
    # sessions (the overwhelming common case). In ask mode (Phase 2) the gate
    # may block on a per-value consent dialog and returns args in which
    # RELEASED fakes are translated back to originals — dispatch-only copy,
    # the wire history keeps the model's fakes.
    guard, args = engine._gdpr_guard_web_args(name, args)
    if guard is not None:
        return guard, True

    # Args-deanonymisation (L3a, dispatch symmetry): for whitelisted LOCALLY-
    # executing tools, translate pseudonyms back to real values so retrieval/
    # paths/scripts work on the raw data. MUST run AFTER the web gate (the
    # gate judges the model's own args) and NEVER touches web tools (that
    # would be silent egress — see brain.GDPR_ARGS_DEANON_TOOLS). Returns a
    # new structure; `args` (and thus the wire history) keeps the fakes.
    args = engine._gdpr_deanon_tool_args(name, args)

    fn = engine.TOOL_DISPATCH.get(name)
    if fn is not None:
        try:
            raw = fn(args)
        except Exception as e:
            return (json.dumps({
                "error": f"tool crashed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            }, ensure_ascii=False), True)
        # Report-fidelity warnings (L6a): the GDPR after-file-write callback
        # flags files it CANNOT de-anonymise (a .pdf written in an anonymise
        # session). Drain them here — the one choke point every file-writing
        # tool (write_document, python_exec, execute_command) passes through —
        # and append to the result so the MODEL learns in the same round and
        # can regenerate as .html/.md.
        try:
            _ctx = engine.get_request_context()
            _fw = _ctx._gdpr_file_warnings
            if _fw:
                _ctx._gdpr_file_warnings = None
                if isinstance(raw, str):
                    raw = raw + "\n\n" + "\n".join(
                        f"⚠️ GDPR: {w}" for w in _fw)
                elif isinstance(raw, dict):
                    raw = dict(raw)
                    raw["gdpr_warning"] = " | ".join(_fw)
            # Design-Modus attachment inlining: unresolvable attachment://
            # references in a written HTML artifact (file missing, non-image,
            # too large) — same drain pattern, so the model can correct the
            # reference in the same round.
            _dw = _ctx._design_file_warnings
            if _dw:
                _ctx._design_file_warnings = None
                if isinstance(raw, str):
                    raw = raw + "\n\n" + "\n".join(
                        f"⚠️ Design: {w}" for w in _dw)
                elif isinstance(raw, dict):
                    raw = dict(raw)
                    raw["design_warning"] = " | ".join(_dw)
            # L7b: tally deterministic doc_checks verdicts for the per-turn
            # degradation strip ("Dokument-Prüfung serverseitig") — only
            # meaningful in anonymising sessions (mapping active).
            if (_ctx._gdpr_mapping_id
                    and name in ("mrz_verify", "doc_dates_check",
                                 "identity_consistency")):
                _dg = _ctx._gdpr_degradation
                if _dg is None:
                    _dg = {}
                    _ctx._gdpr_degradation = _dg
                _dg["doc_checks"] = int(_dg.get("doc_checks", 0)) + 1
        except Exception:
            pass
        if isinstance(raw, str):
            return raw, _looks_like_error(raw)
        return json.dumps(raw, ensure_ascii=False), _looks_like_error_dict(raw)

    mcp_mgr = engine.get_request_context().mcp_manager or engine._mcp_manager
    if mcp_mgr is not None:
        try:
            for td in mcp_mgr.get_tool_definitions():
                if td.get("name") == name:
                    raw = mcp_mgr.call_tool(name, args)
                    # M2/M3 (G7): MCP was the ONLY completely seam-free tool path
                    # in the dispatcher — no args-deanon (correct: the server may
                    # be REMOTE), no gate (fixed: MCP tools are EGRESS_TOOLS now)
                    # and no result seam (fixed here). Without this an MCP server's
                    # answer — a CRM record, a calendar entry, a mail body — went
                    # RAW to the cloud model in an anonymising session. No-op when
                    # no mapping is active.
                    #
                    # Inside this try ON PURPOSE, mirroring the built-in path: the
                    # seam runs the classification gate, whose
                    # ClassificationBlockedError propagates out of `fn(args)` into
                    # the same `except Exception` there. Same behaviour, one rule.
                    is_str = isinstance(raw, str)
                    _txt = raw if is_str else json.dumps(raw, ensure_ascii=False)
                    _txt = engine._gdpr_anon_tool_text(_txt, f"mcp:{name}")
                    return _txt, (_looks_like_error(_txt) if is_str
                                  else _looks_like_error_dict(raw))
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
    wire_api: str | None = None,
    tool_use_id_setter: Callable[[str], None] | None = None,
    pause_gate: Callable[[], None] | None = None,
    drain_injections: Callable[[], list[str]] | None = None,
    progress_cb: Callable[[dict], None] | None = None,
    tools_refresh: Callable[[], tuple[list[dict], list[str]] | None] | None = None,
    budget_gate: Callable[[], str | None] | None = None,
) -> dict:
    """Drive one turn's rounds. `emit(type, data)` fires Brain-vocabulary events;
    `is_cancelled()` is polled between rounds and after each stream drain.

    Interactive-turn extras (all optional, all no-ops for background turns):
    - `pause_gate()` is called at each round boundary (before the next payload
      build) and BLOCKS while the user has paused the turn; it returns once the
      turn is resumed (or immediately if not paused). Soft pause: the current
      round + any in-flight tool always finishes first, so no tokens are wasted
      and output is never torn.
    - `drain_injections()` returns a list of user-supplied strings to splice into
      the running turn as synthetic `role:"user"` messages (consumed by the model
      on its next round) — the same mechanism the empty-round nudge uses. Called
      at each round boundary, right after `pause_gate()`.
    - `progress_cb(state)` receives a live snapshot of turn progress (round, the
      tool executing now + when it started, tools already completed, partial-text
      length) so a concurrent "btw" side-call can report what the agent is doing
      right now. Pure sink for data the loop already computes — cheap.
    - `budget_gate()` is called at each round boundary and returns a REASON string
      to stop, or None to continue. It is how cost enforcement reaches the loop
      WITHOUT the loop knowing anything about quotas: the caller closes over its
      own budget state and decides. A stop is graceful — the text produced so far
      is kept and returned with `stop_reason="budget"` plus `stop_detail=<reason>`;
      nothing is discarded. (An exception from the gate is swallowed: a broken
      budget check must never kill a turn that is otherwise fine.)
    - `tools_refresh()` is called at each round boundary (after round 1). When it
      returns `(new_tools, new_allowed)` the wire tool array + dispatch whitelist
      are REPLACED for the remaining rounds — the undefer-after-discovery hook:
      a tool_search hit mid-turn gets DECLARED to the model from the next round,
      so strict function-callers (glm-5.2) can actually use what they found
      (chat 2cb5a9dd). Returning None means "no change" (the common case — the
      caller must only return a value when a NEW tool was discovered, so the
      prompt prefix stays byte-stable otherwise).

    Returns a summary dict shaped like the old sidecar `done` payload:
      {final_text, text_segments, stop_reason, rounds, tool_calls_total,
       usage_total, tool_events, forced_tool_input?}
    `usage_total` uses the Anthropic-shape keys the callers already read
    (input_tokens / output_tokens / cache_read_input_tokens) so the cost-ledger
    extraction in sidecar_proxy.background_call/helpdesk_call keeps working.
    """
    loop_messages = _to_openai_messages(messages)
    # Wire protocol: honour an explicit override, else resolve from the model's
    # provider (single source of truth). Default "openai" for every provider
    # except those flagged wire_api:"anthropic" (e.g. Kimi coding plan, whose
    # /v1/messages endpoint is the only one that supports thinking-off + tools).
    if wire_api is None:
        try:
            wire_api = (engine.resolve_provider_for_model(model)
                        or {}).get("wire_api", "openai")
        except Exception:
            wire_api = "openai"
    _anthropic_wire = (wire_api or "openai").lower() == "anthropic"
    if _anthropic_wire:
        endpoint = base_url.rstrip("/") + "/messages"
        headers = dict(engine.make_headers(api_key))
        headers["anthropic-version"] = "2023-06-01"
    else:
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
    # Human-readable detail for a NON-VOLUNTARY stop (budget). `stop_reason` says
    # WHICH brake fired; this says why, in words the caller can show the user.
    final_stop_detail = ""
    # Terminal provider-error message. Surfaced in the summary as "error" so
    # BLOCKING callers (run_turn/run_turn_blocking → their "error" field) fail
    # loud too — without this, an HTTP 402/401 on the FIRST round ended the
    # turn as a clean empty `done` (reply=0c rounds=0 error=None; the silent
    # Kilo credit-exhaustion mode, 2026-07-04). The SSE `error` event alone
    # only reaches live watchers, not result-dict consumers.
    final_error_msg = None
    forced_tool_input: dict | None = None
    empty_nudges = 0
    resume_attempts = 0
    # True when the PREVIOUS round was a truncated stream whose partial text
    # is the tail of text_segments — the resumed round's text is then joined
    # into the SAME segment (the model continues mid-sentence; a "\n\n"
    # separator would tear the paragraph). Matches the client's live view,
    # which concatenates text_delta events without separators anyway.
    resume_join_next = False

    allowed_set = set(allowed_tools or [])

    # --- live progress snapshot (for the concurrent "btw" side-call) ---
    _turn_started_at = time.time()
    _completed_tools: list[dict] = []  # [{name, elapsed_ms}] this turn

    def _report_progress(**extra):
        if progress_cb is None:
            return
        try:
            progress_cb({
                "round": extra.get("round", 0),
                "started_at": _turn_started_at,
                "elapsed_ms": int((time.time() - _turn_started_at) * 1000),
                "completed_tools": list(_completed_tools),
                "partial_text_len": len("\n\n".join(text_segments)),
                **extra,
            })
        except Exception:
            pass

    for round_idx in range(max_rounds):
        round_no = round_idx + 1
        if is_cancelled():
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "round_start"})
            break

        # Cost brake. Checked BEFORE the payload build so a turn that has blown its
        # budget doesn't pay for one more round to find out. Graceful by design:
        # we break with the text produced so far intact (the caller surfaces the
        # reason to the user) rather than raising — a half-finished answer the user
        # can see beats an exception that discards the work already paid for.
        if budget_gate is not None and round_idx > 0:
            try:
                _stop = budget_gate()
            except Exception:
                _stop = None  # a broken budget check must not kill a healthy turn
            if _stop:
                final_stop_reason = "budget"
                final_stop_detail = str(_stop)
                emit("budget_stop", {"round": round_no, "reason": final_stop_detail})
                break

        # --- round boundary: honour pause, then splice in any injected messages ---
        if pause_gate is not None:
            try:
                pause_gate()  # blocks while paused; returns on resume
            except Exception:
                pass
        if is_cancelled():
            final_stop_reason = "cancelled"
            emit("cancelled", {"round": round_no, "phase": "round_start"})
            break
        if drain_injections is not None:
            try:
                _injected = drain_injections() or []
            except Exception:
                _injected = []
            for _inj in _injected:
                _inj = (_inj or "").strip()
                if not _inj:
                    continue
                # M3 (G10): a mid-turn injection (POST /v1/chat/inject — the user's
                # "btw …") is TYPED TEXT going onto the wire, exactly like a composer
                # message, which IS scanned. This path was not: a name typed here
                # reached the cloud model raw and left no ledger row, so it never
                # self-healed on later turns either. Seam + ledger it like any other
                # read; no-op when no mapping is active.
                _inj = engine._gdpr_anon_tool_text(_inj, "chat_inject")
                loop_messages.append({"role": "user", "content": _inj})
                emit("injected_message", {"round": round_no, "text": _inj})

        # Undefer-after-discovery: re-declare the tool array when a mid-turn
        # tool_search found something new (see docstring). Only ever REPLACES
        # when the caller signals a change, so the prompt prefix stays stable
        # on turns that discover nothing.
        if tools_refresh is not None and round_idx > 0 and not forced_tool:
            try:
                _refreshed = tools_refresh()
            except Exception:
                _refreshed = None
            if _refreshed:
                tools, _new_allowed = _refreshed
                allowed_set = set(_new_allowed or [])
                emit("tools_redeclared", {
                    "round": round_no, "n_tools": len(tools or [])})

        _report_progress(round=round_no, phase="round_start", active_tool=None)

        if _anthropic_wire:
            payload = build_anthropic_payload(
                model=model, system_prompt=system_prompt, messages=loop_messages,
                tools=tools, max_tokens=max_tokens, sampling=sampling,
                thinking_level=thinking_level, forced_tool=forced_tool)
        else:
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
            # Connect phase with bounded retry (F3, 9.277.0): a transient
            # network flake or retry-safe HTTP status gets CONNECT_RETRIES
            # fresh attempts with linear backoff. Safe because NOTHING has
            # streamed yet — no duplicate deltas possible. Errors after the
            # first byte are handled as truncation by the drain instead.
            _payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            _attempt = 0
            while True:
                try:
                    req = urllib.request.Request(
                        endpoint, data=_payload_bytes, headers=headers, method="POST")
                    resp = urllib.request.urlopen(req, timeout=1800)
                    break
                except Exception as ce:
                    if (is_cancelled() or _attempt >= CONNECT_RETRIES
                            or not _is_retryable_connect_error(ce)):
                        raise
                    _attempt += 1
                    emit("stream_retry", {
                        "round": round_no, "attempt": _attempt,
                        "max": CONNECT_RETRIES,
                        "error": f"{type(ce).__name__}: {ce}"[:300]})
                    time.sleep(float(_attempt))  # 1s, then 2s
            _wt = threading.Thread(target=_watch, daemon=True,
                                   name=f"llm-loop-cancel-{round_no}")
            _wt.start()
            if _anthropic_wire:
                rr = _drain_anthropic_stream(resp, _emit_delta, round_no)
            else:
                rr = _drain_openai_stream(resp, _emit_delta, round_no)
        except Exception as e:
            _stop_watch.set()
            if is_cancelled():
                final_stop_reason = "cancelled"
                emit("cancelled", {"round": round_no, "phase": "stream"})
                break
            _msg = f"{type(e).__name__}: {e}"
            _body = _http_error_body(e)
            if _body:
                _msg += f" — {_body}"
            emit("error", {"message": _msg,
                           "round": round_no,
                           "traceback": traceback.format_exc()[-2000:]})
            final_stop_reason = "api_error"
            final_error_msg = _msg
            break
        finally:
            _stop_watch.set()

        if _thinking_started["v"]:
            emit("thinking_done", {"tool_round": round_no})

        # --- provider error inside the 200-SSE stream (F5) ---
        if rr.error_payload is not None:
            try:
                _perr = json.dumps(rr.error_payload, ensure_ascii=False)[:500]
            except Exception:
                _perr = str(rr.error_payload)[:500]
            emit("error", {"message": f"provider error: {_perr}",
                           "round": round_no})
            final_stop_reason = "api_error"
            final_error_msg = f"provider error: {_perr}"
            break

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
        elif rr.text or rr.tool_calls:
            # F6: a round produced content but no usage object (typically a
            # truncated stream that died before the final usage chunk). The
            # cost ledger will under-count this round — loud audit signal per
            # the v9.90.0 complete-coverage rule, no fabricated numbers.
            print(f"[inprocess-loop] round {round_no}: no usage on stream "
                  f"({'truncated' if not rr.got_done else 'complete'}) — "
                  f"cost row under-counts this round", flush=True)

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
            if resume_join_next and text_segments:
                # Continuation of a truncated round: extend its segment.
                text_segments[-1] = text_segments[-1] + round_visible
                if text_round_segments:
                    text_round_segments[-1]["text"] += round_visible
            else:
                text_segments.append(round_visible)
                text_round_segments.append({"round": round_no, "text": round_visible})
            final_text = "\n\n".join(text_segments)
        resume_join_next = False

        # --- truncated stream (F2, 9.277.0): ended without [DONE] ---
        # The upstream socket died mid-generation. Pre-9.277.0 this was
        # silently accepted as a FINISHED answer (stop_reason "") and the user
        # had to type "continue" by hand. Auto-resume instead: append the
        # partial as the assistant turn plus a continue nudge, capped.
        if not rr.got_done and not forced_tool:
            if is_cancelled():
                final_stop_reason = "cancelled"
                emit("cancelled", {"round": round_no, "phase": "stream"})
                break
            rounds.append({
                "round": round_no, "stop_reason": "truncated",
                "content_chars": len(rr.text), "tool_uses": 0,
            })
            if resume_attempts < STREAM_RESUME_MAX:
                resume_attempts += 1
                emit("stream_resumed", {
                    "round": round_no, "attempt": resume_attempts,
                    "max": STREAM_RESUME_MAX,
                    "partial_chars": len(rr.text or "")})
                # Torn tool calls are unusable (half-streamed args) — the
                # assistant turn carries only the partial TEXT; the model
                # re-issues any tool calls it still wants on the resume round.
                loop_messages.append({"role": "assistant",
                                      "content": rr.text or " "})
                loop_messages.append({"role": "user", "content": _RESUME_NUDGE})
                resume_join_next = bool(round_visible)
                continue
            # Resumes exhausted: keep the partial as the final answer, but
            # flag it loudly instead of pretending the turn finished cleanly.
            emit("stream_truncated", {
                "round": round_no, "attempts": resume_attempts})
            final_stop_reason = "stream_truncated"
            break

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
            # Weak models sometimes bleed the sequential_thinking fields
            # (nextThoughtNeeded/thoughtNumber/totalThoughts) into a `think` call —
            # `think` has only `thought`. Drop the strays before the tool_call
            # event so the chat view / metadata don't show phantom fields. Only the
            # simple `think` tool (kept minimal on purpose); sequential_thinking's
            # own numbered fields are legitimate and left untouched.
            if tu["name"] == "think" and isinstance(tu_args, dict) and len(tu_args) > 1:
                tu_args = {"thought": tu_args.get("thought", "")}
                tu["input"] = tu_args
            emit("tool_call", {
                "name": tu["name"], "args": tu_args,
                "tool_round": round_no, "tool_use_id": tu["id"],
            })
            _report_progress(round=round_no, phase="tool_call",
                             active_tool=tu["name"],
                             active_tool_started_at=time.time())
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
            _completed_tools.append({"name": tu["name"],
                                     "elapsed_ms": int(elapsed * 1000),
                                     "is_error": is_error})
            _report_progress(round=round_no, phase="tool_result",
                             active_tool=None)
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
        # for-else: reached ONLY when the loop ran out of rounds without breaking
        # (a cancel/budget break skips this). The model was still working — it did
        # not choose to stop — so this is a TRUNCATION, and the caller must say so.
        final_stop_reason = "max_rounds"
        final_stop_detail = (
            f"Runden-Limit erreicht ({max_rounds} Tool-Runden) — das Modell war noch "
            f"nicht fertig, die Arbeit ist unvollständig.")
        if not final_text.strip():
            final_text = EMPTY_GIVEUP_TEXT

    summary = {
        "final_text": final_text,
        "text_segments": text_round_segments,
        "stop_reason": final_stop_reason,
        "stop_detail": final_stop_detail,
        "rounds": len(rounds),
        "tool_calls_total": tool_calls_total,
        "usage_total": usage_total,
        "tool_events": tool_events,
        "error": final_error_msg,
    }
    if forced_tool_input is not None:
        summary["forced_tool_input"] = forced_tool_input
    return summary
