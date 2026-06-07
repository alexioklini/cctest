# AskUser / ask_llm tool bodies (extracted from brain.py, E4).
#
# Four tools that pause the agentic loop to ask the user (or a one-shot LLM):
#   - ask_user          — main-loop question(s); blocks on the session's
#                         pending-answer slot, unblocked by POST /v1/chat/answer.
#   - ask_user_for_file — pause for a file upload; blocks on the workflow/
#                         session pending-file slot, delivered by the upload
#                         endpoint.
#   - worker_ask_user   — ask from within a worker subagent (routes through the
#                         worker registry).
#   - ask_llm           — one-shot LLM text-in/text-out via the sidecar (no
#                         loop, no tools).
#
# ⚠️ BLOCKING STATE STAYS IN BRAIN. The pending-answer / pending-file registries
# and their lifecycle helpers MUST remain single-instance in brain.py because
# the HTTP delivery side mutates them under brain-held locks:
#   - `_ask_user_pending` / `_ask_user_lock` + `_ask_user_register` /
#     `_ask_user_clear` / `deliver_ask_user_answer` (handlers/chat.py,
#     handlers/admin_artifacts.py call `brain.deliver_ask_user_answer`).
#   - `_workflow_file_pending` / `_workflow_file_lock` + `_workflow_file_register`
#     / `_workflow_file_clear` / `deliver_workflow_file_answer`
#     (handlers/admin_workflows.py).
# The moved tool bodies reach those via `import brain as _brain` — only the
# `tool_*` functions and the pure `_normalize_ask_questions` helper move here.
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `get_request_context` from engine.context (event_callback, session/worker ids).
#   - brain runtime symbols (the blocking-state helpers above, `AgentConfig`,
#     `_load_tools_config`, `_current_agent`, `_delegate_fallback_model`)
#     reached lazily via `import brain as _brain`. NO top-level `import brain`.
#
# brain.py re-exports all 4 tools + `_normalize_ask_questions` via
# `from engine.tools.ask_tools import (...)`.

from __future__ import annotations

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


def _normalize_ask_questions(args: dict):
    """Return (questions_list, is_batch) from args.

    - `questions: [{question, options?}, ...]` → batch
    - `question: "..."` (+ optional `options`) → single, wrapped
    Returns ([], False) on invalid input.
    """
    qs = args.get("questions")
    if isinstance(qs, list) and qs:
        out = []
        for q in qs:
            if isinstance(q, dict) and q.get("question"):
                out.append({
                    "question": str(q["question"]),
                    "options": q.get("options") if isinstance(q.get("options"), list) else None,
                })
        return out, True
    q = args.get("question")
    if isinstance(q, str) and q:
        return [{"question": q, "options": args.get("options") if isinstance(args.get("options"), list) else None}], False
    return [], False


def tool_ask_llm(args: dict) -> str:
    """One-shot LLM call without an agentic loop. For workflows / chats that need
    a quick text-in / text-out transformation. No tools, no memory, no streaming.

    Args:
        prompt: user message
        system: optional system prompt (defaults to a generic helpful assistant)
        model:  model id (defaults to refinement.model from tools_config.json,
                falling back to the agent's preferred model)
    """
    import brain as _brain
    prompt = args.get("prompt") or ""
    if not prompt:
        return _err("ask_llm: 'prompt' is required")
    system_prompt = args.get("system") or (
        "You are a helpful assistant. Answer concisely and directly."
    )
    model = args.get("model") or ""
    # Resolution order: explicit kwarg → workflow MODEL header → workflow AGENT.preferred_model
    # → refinement model → current agent → server fallback.
    if not model:
        model = (get_request_context().workflow_default_model or "").strip()
    if not model:
        wf_agent = get_request_context().workflow_agent_id or ""
        if wf_agent:
            try:
                _ag = _brain.AgentConfig(wf_agent)
                model = (_ag.preferred_model or "").strip()
            except Exception:
                pass
    if not model:
        try:
            tcfg = _brain._load_tools_config() or {}
            model = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        except Exception:
            model = ""
    if not model:
        agent = get_request_context().current_agent or _brain._current_agent
        if agent:
            model = agent.preferred_model or ""
    if not model:
        model = _brain._delegate_fallback_model or ""
    if not model:
        return _err("ask_llm: no model configured")
    # Inherit the workflow's synthetic session_id ("wf-<execution_id>") so the
    # cost log can attribute this LLM call back to the workflow run.
    sid = get_request_context().current_session_id or ""
    try:
        from handlers import sidecar_proxy as _sidecar_proxy
        _agent_id = ""
        _ag = get_request_context().current_agent or _brain._current_agent
        if _ag is not None:
            _agent_id = getattr(_ag, "agent_id", "") or ""
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt=system_prompt,
            agent_id=(_agent_id or "main"),
            session_id=(sid or ""),
            cost_purpose="ask_llm",
        )
        text = _res.get("reply") or ""
        return _ok({"text": text.strip(), "model": model})
    except Exception as e:
        return _err(f"ask_llm: {e}")


def tool_ask_user_for_file(args: dict) -> str:
    """Pause and request a file upload from the user.

    Routes the request via the active event_callback as a 'file_upload_needed' SSE event,
    then blocks on a pending-file slot keyed by execution_id (workflow) or session_id (chat).
    Frontend uploads the file to /v1/workflows/upload-pending which delivers the answer.
    """
    import brain as _brain
    prompt = args.get("prompt") or "Please upload a file"
    accept = args.get("accept") or ""
    timeout = int(args.get("timeout_seconds", 600))

    # Prefer workflow execution_id; fall back to session_id for chat use.
    execution_id = get_request_context().workflow_execution_id
    session_id = get_request_context().current_session_id
    key = execution_id or session_id
    if not key:
        return _err("ask_user_for_file requires an active workflow execution or chat session")

    cb = get_request_context().event_callback
    if cb:
        try:
            cb("file_upload_needed", {
                "execution_id": execution_id or "",
                "session_id": session_id or "",
                "prompt": prompt,
                "accept": accept,
                "timeout_seconds": timeout,
            })
        except Exception:
            pass

    event = _brain._workflow_file_register(key)
    try:
        got = event.wait(timeout=timeout)
        if not got:
            return _err("ask_user_for_file: timed out waiting for upload")
        with _brain._workflow_file_lock:
            slot = _brain._workflow_file_pending.get(key) or {}
            payload = slot.get("result")
        if not payload:
            return _err("ask_user_for_file: cancelled by user")
        return _ok({
            "path": payload.get("path", ""),
            "filename": payload.get("filename", ""),
            "size_bytes": int(payload.get("size_bytes") or 0),
        })
    finally:
        _brain._workflow_file_clear(key)


def tool_worker_ask_user(args: dict) -> str:
    """Ask the user a question from within a worker subagent.

    Accepts single-question (`question` str) or a 1-item `questions` array.
    Multi-question batches are not supported inside workers — call once per question.
    """
    from execution import get_worker_registry
    worker_id = get_request_context().current_worker_id
    if not worker_id:
        return _err("worker_ask_user can only be called from within a worker subagent")
    questions, _ = _normalize_ask_questions(args)
    if not questions:
        return _err("question or questions is required")
    if len(questions) > 1:
        return _err("worker_ask_user does not support question batches yet — call once per question")
    q0 = questions[0]
    context_summary = args.get("context_summary", "")
    timeout = args.get("timeout_seconds", 300)
    answer = get_worker_registry().ask_user(
        worker_id, q0["question"], q0.get("options"), context_summary, timeout
    )
    if answer is None:
        return _err("No answer received (timed out or worker was aborted)")
    return _ok({"answer": answer})


def tool_ask_user(args: dict) -> str:
    """Ask the user one or more questions from the main chat loop (not inside a worker)."""
    import brain as _brain
    session_id = get_request_context().current_session_id
    if not session_id:
        return _err("ask_user requires an active session")
    questions, is_batch = _normalize_ask_questions(args)
    if not questions:
        return _err("question or questions is required")
    context_summary = args.get("context_summary", "")
    timeout = int(args.get("timeout_seconds", 300))

    cb = get_request_context().event_callback
    if cb:
        try:
            payload = {
                "session_id": session_id,
                "questions": questions,
                "context_summary": context_summary,
                "timeout_seconds": timeout,
            }
            if not is_batch:
                # Keep legacy fields for single-question consumers
                payload["question"] = questions[0]["question"]
                payload["options"] = questions[0]["options"]
            cb("user_input_needed", payload)
        except Exception:
            pass

    event = _brain._ask_user_register(session_id)
    try:
        got = event.wait(timeout=timeout)
        if not got:
            return _err("No answer received (timed out)")
        with _brain._ask_user_lock:
            slot = _brain._ask_user_pending.get(session_id) or {}
            answers_map = slot.get("answers")
            answer_str = slot.get("answer")
        if is_batch:
            if not isinstance(answers_map, dict) or not answers_map:
                return _err("No answers received")
            if cb:
                try:
                    cb("user_input_received", {"session_id": session_id, "answers": answers_map})
                except Exception:
                    pass
            return _ok({"answers": answers_map})
        # Single-question path. Prefer explicit answer, fall back to first value in answers map.
        if answer_str is None and isinstance(answers_map, dict) and answers_map:
            answer_str = next(iter(answers_map.values()))
        if answer_str is None:
            return _err("No answer received")
        if cb:
            try:
                cb("user_input_received", {"session_id": session_id, "answer": answer_str})
            except Exception:
                pass
        return _ok({"answer": answer_str})
    finally:
        _brain._ask_user_clear(session_id)
