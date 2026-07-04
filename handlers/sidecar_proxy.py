"""Brain LLM-turn entry points (in-process OpenAI agentic loop).

Historically this module proxied turns to a separate `sidecar/` subprocess that
owned an Anthropic-SDK loop. As of v9.246.0 the loop runs IN-PROCESS on the
caller's thread via `engine/llm_loop.py`, speaking the OpenAI
`/v1/chat/completions` wire shape (the path where Mistral prompt caching via
CLIProxyAPI reliably hits). The sidecar subprocess + its cross-process plumbing
(nonce auth, context rebuild, tool-result capture, SSE re-translation) were
deleted in v9.247.0. The public API here is unchanged:

  - `run_turn(...)`         — interactive, streaming (chat worker)
  - `run_turn_blocking(...)`— non-streaming (background callers)
  - `background_call(...)`  — convenience wrapper + central cost-ledger seam
  - `helpdesk_call(...)`    — Brainy turn (streaming, read-only tools)

These build the tool list + provider params and drive `engine.llm_loop.run_loop`,
which emits the Brain event vocabulary the chat worker already consumes
(text_delta / thinking_* / tool_call / tool_result / usage / …) — so LiveStream,
persistence, the citation validator, and the cost ledger are untouched.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

import brain as engine


# ---------- Cancellation (turn / per-tool) ----------
#
# The interactive loop polls the session's `cancel_token` and closes its stream
# socket mid-generation (engine/llm_loop.py). Background (blocking) turns have no
# session token, so they register a per-turn cancel Event here that
# run_turn_blocking polls via is_cancelled(). `cancel_turn(turn_id)` trips it —
# the in-process replacement for the old sidecar POST /cancel/<turn_id>.

_TURN_CANCELS: dict[str, threading.Event] = {}
_TURN_CANCELS_LOCK = threading.Lock()


def _register_turn_cancel(turn_id: str) -> threading.Event:
    ev = threading.Event()
    if turn_id:
        with _TURN_CANCELS_LOCK:
            _TURN_CANCELS[turn_id] = ev
    return ev


def _unregister_turn_cancel(turn_id: str) -> None:
    if turn_id:
        with _TURN_CANCELS_LOCK:
            _TURN_CANCELS.pop(turn_id, None)


def cancel_turn(turn_id: str) -> bool:
    """Trip the cancel flag for an in-flight (blocking) turn. Returns True if a
    matching in-flight turn was found. The in-process replacement for the old
    sidecar POST /cancel/<turn_id>."""
    if not turn_id:
        return False
    with _TURN_CANCELS_LOCK:
        ev = _TURN_CANCELS.get(turn_id)
    if ev is not None:
        ev.set()
        return True
    return False


def cancel_tool(turn_id: str, tool_use_id: str) -> bool:
    """Cancel ONE in-flight tool. In-process, per-tool kill is the tool's own
    subprocess-registration (kill_tool_process keyed by (turn_id, tool_use_id));
    the caller (background_tasks) already invokes that directly, so this is a
    best-effort second attempt. Returns True if a process was killed."""
    if not turn_id or not tool_use_id:
        return False
    try:
        from engine.tool_exec import kill_tool_process
        return bool(kill_tool_process(turn_id, tool_use_id))
    except Exception:
        return False


# ---------- Tool resolution ----------

def _build_tool_list_openai(*, purpose: str, agent_id: str | None,
                            mcp_manager=None, breakdown: dict | None = None) -> list[dict]:
    """OpenAI-shape tool schemas (returns {type:function,function:{...}} objects).
    Thin wrapper over engine.resolve_active_tools — the single source of truth."""
    discovered = engine.get_request_context()._discovered_tools or set()
    return engine.resolve_active_tools(
        purpose=purpose, agent_id=agent_id, discovered_tools=discovered,
        mcp_manager=mcp_manager, is_openai_shape=True, breakdown=breakdown)


def _dispatchable_allowed_tools(tools: list[dict], breakdown: dict) -> list[dict]:
    """The dispatch enforcement whitelist. A DEFERRED tool is hidden from the
    prompt but still DISPATCHABLE (tool_search-recoverable), so the whitelist is
    `in_prompt ∪ deferred` — everything EXCEPT hard-`excluded` tools (Websuche
    web-lockout, helpdesk read-only). Returns allowed NAMES."""
    names = set(b for b in (breakdown.get("in_prompt") or []))
    names |= set(b for b in (breakdown.get("deferred") or []))
    # OpenAI-shape tool objects nest the name under function.name.
    for t in tools:
        n = (t.get("function", {}) or {}).get("name", "") or t.get("name", "")
        if n:
            names.add(n)
    return sorted(n for n in names if n)


def _log_wire_tools(tools: list[dict], *, turn_id: str, purpose: str,
                    agent_id: str | None, model: str) -> None:
    """Diagnostic: dump the resolved tool-name list per turn. Gated on
    `tool_list_log` in config.json (default off)."""
    try:
        cfg = engine._server_config() if hasattr(engine, "_server_config") else {}
        if not (cfg or {}).get("tool_list_log"):
            return
        names = sorted(
            (t.get("function", {}) or {}).get("name", "") or t.get("name", "?")
            for t in tools)
        print(
            f"[wire-tools] turn={turn_id[:8]} agent={agent_id or '-'} "
            f"model={model} purpose={purpose} n={len(names)} :: "
            f"{', '.join(names)}",
            flush=True,
        )
    except Exception:
        pass  # Diagnostic must never break the turn.


def _apply_bg_context(ctx: dict) -> None:
    """Reinstate the per-turn request context for a background in-process loop.

    Background callers (scheduler, summariser, classifier, …) don't hold a live
    chat-worker context, so the loop's tool dispatch needs the agent/session/
    project set here (a thin mirror of the old tool_mcp._apply_context, minus the
    nonce/HTTP bits)."""
    tl = engine.get_request_context()
    tl.current_session_id = ctx.get("session_id") or ctx.get("helpdesk_session_id") or ""
    tl.session_id = tl.current_session_id
    tl.current_turn_id = ctx.get("turn_id") or ""
    tl.current_bg_task = bool(ctx.get("bg_task", False))
    # Dispatch whitelist for tool_search (same list the loop enforces).
    tl.allowed_tools = ctx.get("allowed_tools") or None
    tl.current_user_id = ctx.get("user_id") or ""
    tl.current_team_ids = list(ctx.get("team_ids") or [])
    tl.project = ctx.get("project") or ""
    tl.working_dir = ctx.get("working_dir") or None
    tl.code_graph_db = ctx.get("code_graph_db") or None
    tl.note_context = ctx.get("note_context") or None
    tl.workflow_run_id = ctx.get("workflow_run_id") or ""
    tl.plan_mode = bool(ctx.get("plan_mode", False))
    tl.helpdesk_mode = bool(ctx.get("helpdesk_mode", False))
    tl.research_mode_override = ctx.get("research_mode_override", None)
    tl.execution_overrides = ctx.get("execution_overrides") or {}
    tl.attachment_image_model = ctx.get("attachment_image_model") or ""
    tl._current_model = ctx.get("model") or None
    tl.current_agent = engine.AgentConfig(ctx.get("agent_id") or "main")
    tl.mcp_manager = engine._mcp_manager
    tl.caveman_chat = int(ctx.get("caveman_chat", 0) or 0)
    tl.caveman_system = int(ctx.get("caveman_system", 0) or 0)


# ---------- Interactive (streaming) ----------

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
    """Drive one chat turn through the in-process OpenAI loop.

    `purpose` drives tool resolution via engine.resolve_active_tools. The
    agent_id is read from tool_context['agent_id']; MCP tools come from
    engine._mcp_manager. Emits the Brain event vocabulary via `event_callback`;
    LiveStream / persistence / cost logging (all downstream) are unchanged.

    Returns: {reply, stop_reason, rounds, tool_calls_total, usage_total,
              tool_events, text_segments, cancelled, error, turn_id}.
    """
    from engine import llm_loop

    sid = tool_context.get("session_id") or ""
    turn_id = uuid.uuid4().hex
    tool_context = dict(tool_context)
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    # Record the active turn so a Brain restart can note it (resumable-stream
    # bookkeeping is Brain-side; the sidecar-event-log recovery branch is gone).
    if sid:
        try:
            from server_lib.db import ChatDB as _ChatDB
            _ChatDB.set_active_turn(sid, turn_id, model)
        except Exception:
            pass

    _tb: dict = {}
    _tools = _build_tool_list_openai(
        purpose=purpose, agent_id=tool_context.get("agent_id") or None,
        mcp_manager=getattr(engine, "_mcp_manager", None), breakdown=_tb)
    allowed_tools = _dispatchable_allowed_tools(_tools, _tb)
    tool_context["allowed_tools"] = allowed_tools
    # Mirror the whitelist onto the worker's request context so tool_search
    # only surfaces tools that are actually dispatchable this turn (a disabled
    # tool must not be discoverable — chat 2cb5a9dd).
    engine.get_request_context().allowed_tools = allowed_tools
    _log_wire_tools(_tools, turn_id=turn_id, purpose=purpose,
                    agent_id=tool_context.get("agent_id") or None, model=model)

    try:
        _prov = engine.resolve_provider_for_model(model) or {}
        _provider_name = _prov.get("provider_name", "") or "default"
    except Exception:
        _provider_name = "default"

    def _is_cancelled() -> bool:
        if cancel_token is None:
            return False
        try:
            if getattr(cancel_token, "cancelled", False):
                return True
            is_set = getattr(cancel_token, "is_set", None)
            return bool(callable(is_set) and is_set())
        except Exception:
            return False

    def _set_tool_use_id(tuid: str) -> None:
        # Expose tool_use_id so a subprocess-spawning tool can register under
        # (turn_id, tool_use_id) for per-tool kill.
        try:
            engine.get_request_context().tool_use_id = tuid
        except Exception:
            pass

    # In-process tools dispatch on THIS (worker) thread, whose request context
    # does NOT carry an event_callback by default. Without one,
    # brain._after_file_write skips artifact registration AND the blocking tools
    # (ask_user / ask_user_for_file) emit user_input_needed into a None callback →
    # block till timeout (the v9.101.12 bug). Install the artifact/passthrough
    # callback + the GDPR after_file_write hook (when a pseudonym mapping is
    # active), and restore prior values after the turn.
    _ctx = engine.get_request_context()
    _prev_ecb = _ctx.event_callback
    _prev_gdpr_cb = getattr(_ctx, "_gdpr_after_file_write_cb", None)
    _prev_gdpr_mid = getattr(_ctx, "_gdpr_mapping_id", "")
    try:
        from handlers.chat import make_artifact_event_callback
        _ctx.event_callback = make_artifact_event_callback(sid) if sid else None
    except Exception:
        _ctx.event_callback = None
    _gdpr_mid = tool_context.get("gdpr_mapping_id") or ""
    _ctx._gdpr_mapping_id = _gdpr_mid
    if _gdpr_mid:
        try:
            from handlers.chat import make_gdpr_after_file_write_cb
            _ctx._gdpr_after_file_write_cb = make_gdpr_after_file_write_cb(
                mapping_id=_gdpr_mid, session_id=sid or "",
                agent_id=tool_context.get("agent_id") or "main")
        except Exception:
            _ctx._gdpr_after_file_write_cb = None
    else:
        _ctx._gdpr_after_file_write_cb = None

    final_text = ""
    summary: dict[str, Any] = {}
    cancelled = False
    error_msg: str | None = None

    # prompt_cache_key = session id (interactive): the growing byte-stable prefix
    # (system + tools + history) bills the repeated span at the discounted rate.
    # Helpdesk (Brainy) turns run with an EMPTY tool_context session_id (so they
    # don't collide with the main chat's active-turn tracking) but still want a
    # stable per-conversation cache key — fall back to helpdesk_session_id so
    # multi-round Brainy turns reuse their prefix instead of keying to "".
    _pck = sid or tool_context.get("helpdesk_session_id") or ""

    try:
        # Local-provider concurrency gate — serialises local chats (+ warmup)
        # against oMLX/CLIProxyAPI's batched-decode capacity. No-op for cloud.
        with engine.get_provider_queue().acquire_if(
                _provider_name, label=purpose or "interactive",
                session_id=sid or None,
                agent_id=tool_context.get("agent_id") or None,
                user_id=tool_context.get("user_id") or None,
                model=model, event_callback=event_callback,
                cancel_token=cancel_token, timeout=timeout_s):
            # Interactive turn-control closures (pause/resume, mid-stream
            # injection, live-progress for the btw side-call). Only meaningful
            # with a real session id; background turns pass None (loop no-ops).
            _pause_gate = engine._turn_pause_gate(sid) if sid else None
            _drain_inj = engine._turn_drain_injections(sid) if sid else None
            _progress_cb = engine._turn_progress_cb(sid) if sid else None

            # Undefer-after-discovery (9.277.0): when a mid-turn tool_search
            # discovered a tool that is NOT yet declared on the wire, rebuild
            # the tool array (resolve_active_tools un-hides discovered deferred
            # names) so the model can call it from the next round. Returns None
            # on the hot path (nothing new discovered) so the prompt prefix
            # stays byte-stable for turns that never search.
            _declared_names = {(t.get("function", {}) or {}).get("name", "")
                               for t in (_tools or [])}

            def _tools_refresh():
                discovered = engine.get_request_context()._discovered_tools or set()
                if not (set(discovered) - _declared_names):
                    return None
                _tb2: dict = {}
                new_tools = _build_tool_list_openai(
                    purpose=purpose, agent_id=tool_context.get("agent_id") or None,
                    mcp_manager=getattr(engine, "_mcp_manager", None), breakdown=_tb2)
                new_allowed = _dispatchable_allowed_tools(new_tools, _tb2)
                for t in new_tools:
                    _declared_names.add((t.get("function", {}) or {}).get("name", ""))
                # Also absorb the discovered names themselves: if one of them
                # can't be declared even after the rebuild (edge: state changed
                # mid-turn), it must not re-trigger a rebuild every round.
                _declared_names.update(discovered)
                tool_context["allowed_tools"] = new_allowed
                engine.get_request_context().allowed_tools = new_allowed
                return new_tools, new_allowed

            summary = llm_loop.run_loop(
                model=model, system_prompt=system_prompt, messages=messages,
                tools=_tools, allowed_tools=allowed_tools,
                max_tokens=int(max_tokens), max_rounds=int(max_rounds),
                sampling=sampling, thinking_level=thinking_level,
                disable_parallel_tool_use=bool(disable_parallel_tool_use),
                prompt_cache_key=_pck, forced_tool=None,
                api_key=api_key, base_url=base_url,
                emit=event_callback, is_cancelled=_is_cancelled,
                tool_use_id_setter=_set_tool_use_id,
                pause_gate=_pause_gate, drain_injections=_drain_inj,
                progress_cb=_progress_cb, tools_refresh=_tools_refresh)
            final_text = summary.get("final_text", "") or ""
            if summary.get("stop_reason") == "cancelled":
                cancelled = True
    except engine.TaskCancelled:
        # Cancelled while waiting in the provider queue (Stop button / admin
        # queue-cancel) — treat as a cancel, not an error.
        cancelled = True
    except Exception as e:
        error_msg = f"inprocess loop {type(e).__name__}: {e}"
        try:
            event_callback("error", {"message": error_msg})
        except Exception:
            pass
    finally:
        # Restore the worker context's callback state (reused for downstream
        # post-turn work — summariser, next-prompt, etc.).
        try:
            _ctx.event_callback = _prev_ecb
            _ctx._gdpr_after_file_write_cb = _prev_gdpr_cb
            _ctx._gdpr_mapping_id = _prev_gdpr_mid
        except Exception:
            pass
        if sid:
            try:
                from server_lib.db import ChatDB as _ChatDB
                _ChatDB.clear_active_turn(sid, turn_id)
            except Exception:
                pass
        print(f"[inprocess-loop] turn={turn_id[:8]} model={model[:24]} "
              f"reply={len(final_text)}c rounds={summary.get('rounds', 0)} "
              f"tools={summary.get('tool_calls_total', 0)} "
              f"error={error_msg or summary.get('error')} cancelled={cancelled}", flush=True)

    return {
        "reply": final_text,
        "stop_reason": summary.get("stop_reason", ""),
        "rounds": summary.get("rounds", 0),
        "tool_calls_total": summary.get("tool_calls_total", 0),
        "usage_total": summary.get("usage_total", {}) or {},
        "tool_events": summary.get("tool_events", []) or [],
        "text_segments": summary.get("text_segments", []) or [],
        "cancelled": cancelled,
        # error_msg = an exception ESCAPED the loop; summary["error"] = the loop
        # caught a terminal provider error itself (api_error stop). Either way
        # the caller must see it — chat.py surfaces it in the reply.
        "error": error_msg or summary.get("error"),
        "turn_id": turn_id,
    }


# ---------- Non-streaming (background) ----------

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
    prompt_cache_key: str = "",
) -> dict:
    """Non-streaming variant for background callers (scheduler, summariser,
    classifier, refine, …). Same return shape as run_turn() minus the live
    event_callback hook.

    `forced_tool`: structured-output mode. An Anthropic-shape tool def
    {name, description, input_schema}. When set, the model is offered ONLY this
    tool and forced to call it; the loop captures the tool-call arguments (never
    dispatches) and returns them as `forced_tool_input`.
    """
    from engine import llm_loop

    turn_id = turn_id or uuid.uuid4().hex
    tool_context = dict(tool_context)
    tool_context.setdefault("model", model)
    tool_context["turn_id"] = turn_id

    if forced_tool:
        _tools = [{
            "type": "function",
            "function": {
                "name": forced_tool["name"],
                "description": forced_tool.get("description", ""),
                "parameters": forced_tool.get("input_schema")
                              or forced_tool.get("parameters") or {},
            },
        }]
        allowed_tools: list[str] = []
    else:
        _tb: dict = {}
        _tools = _build_tool_list_openai(
            purpose=purpose, agent_id=tool_context.get("agent_id") or None,
            mcp_manager=getattr(engine, "_mcp_manager", None), breakdown=_tb)
        allowed_tools = _dispatchable_allowed_tools(_tools, _tb)
    tool_context["allowed_tools"] = allowed_tools
    _log_wire_tools(_tools, turn_id=turn_id, purpose=purpose,
                    agent_id=tool_context.get("agent_id") or None, model=model)

    def _noop_emit(_t, _d):
        pass

    # Register a cancel flag so detached background tasks can stop this turn via
    # cancel_turn(turn_id) (the in-process replacement for the sidecar cancel).
    _cancel_ev = _register_turn_cancel(turn_id)

    summary: dict[str, Any] = {}
    error_msg: str | None = None
    try:
        with engine.request_context():
            _apply_bg_context(tool_context)
            summary = llm_loop.run_loop(
                model=model, system_prompt=system_prompt, messages=messages,
                tools=_tools, allowed_tools=allowed_tools,
                max_tokens=int(max_tokens), max_rounds=int(max_rounds),
                sampling=sampling, thinking_level=thinking_level,
                disable_parallel_tool_use=False,
                prompt_cache_key=(prompt_cache_key or ""),
                forced_tool=forced_tool, api_key=api_key, base_url=base_url,
                emit=_noop_emit, is_cancelled=_cancel_ev.is_set)
    except Exception as e:
        error_msg = f"inprocess loop {type(e).__name__}: {e}"
    finally:
        _unregister_turn_cancel(turn_id)

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
    prompt_cache_key: str = "",
) -> dict:
    """Thin convenience wrapper around `run_turn_blocking` for background /
    non-interactive LLM calls.

    `bg_task=True` marks this as a detached background-task run so the tool
    dispatch context carries `current_bg_task` — the run_background_task nesting
    guard reads it to refuse spawning further background tasks (no runaway
    fan-out). Default False (scheduler/summariser/etc. are not bg-tasks).

    Resolves provider + inference params + sampling from the model id, builds
    a minimal `tool_context`, and drives the in-process loop. Caller picks the
    model; if the picked model isn't on an OpenAI-compatible provider the loop
    returns an error — that's the admin's job to fix in their config.

    Returns the same dict shape as `run_turn_blocking`. Caller decides how to
    handle `error` / `reply`.

    `account_cost` (default True): log this call to the cost ledger CENTRALLY —
    one `cost_log` row per background_call, attributed to `user_id`/`agent_id`,
    even when the cost is $0 (local/free model) or no usage was reported. This is
    the single seam that makes EVERY background LLM call appear in the cost
    breakdown. Set False ONLY for non-billable measurement (e.g. benchmarking).

    `cost_purpose`: the cost-ledger USE-CASE tag (chat_summary, translate_text,
    kg_extract, …) — SEPARATE from `purpose`, which must stay one of the 5
    tool-resolution purposes in `_VALID_PURPOSES`. Effective tag is: this arg →
    else the request context's `cost_purpose` → else `purpose`."""
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
        # stable, reproducible output).
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
        # Prompt-cache key for background calls: default to the cost-purpose tag
        # (or `purpose`), so all same-purpose calls share one cache key and their
        # byte-stable instruction/schema prefix bills the repeated span at the
        # discounted cache_read rate.
        prompt_cache_key=(prompt_cache_key or cost_purpose or purpose or ""),
    )
    # Central cost ledger seam — one row per background_call, even at $0.
    if account_cost:
        try:
            usage = (result or {}).get("usage_total") or {}
            cr = int(usage.get("cache_read_input_tokens", 0) or 0)
            ti = (int(usage.get("input_tokens", 0) or 0)
                  + int(usage.get("cache_creation_input_tokens", 0) or 0))
            to = int(usage.get("output_tokens", 0) or 0)
            _cost_purpose = (cost_purpose
                             or engine.get_request_context().cost_purpose
                             or purpose)
            engine._log_call_cost(model, ti, to, session_id=session_id,
                                  user_id=_user_id, agent_id=agent_id,
                                  purpose=_cost_purpose, api_key=prov.get("api_key", ""),
                                  cache_read_tokens=cr)
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
    """Drive one Brainy (helpdesk) turn through the in-process loop, STREAMING.

    Like background_call (resolves provider + sampling from the model id) but
    uses the streaming run_turn path so the helpdesk endpoint can relay SSE to
    the browser. Pins purpose='helpdesk' (the fixed read-only tool set) and sets
    helpdesk_mode=True in the tool_context so the dispatch can load the
    backend-exclusive brain-agent-guide skill.

    `session_id` scopes the read-only tools (session info / activity) — it is
    NOT used as the turn's persisted session, so the main chat's history,
    live_stream, and active-turn tracking are untouched.

    `project` (the project NAME) force-scopes mempalace_query / KG to that
    project's `project__<id>` wing.
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
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
        ti = (int(usage.get("input_tokens", 0) or 0)
              + int(usage.get("cache_creation_input_tokens", 0) or 0))
        to = int(usage.get("output_tokens", 0) or 0)
        engine._log_call_cost(model, ti, to, session_id=session_id,
                              user_id=user_id or "", agent_id="main",
                              purpose="helpdesk", api_key=prov.get("api_key", ""),
                              cache_read_tokens=cr)
    except Exception as _ce:
        print(f"[helpdesk_call] cost log failed: {_ce}", flush=True)
    return result
