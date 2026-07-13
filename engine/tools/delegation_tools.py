# Delegation tool bodies (extracted from brain.py, E4).
#
#   - delegate_task / task_status / task_cancel — TaskRunner-backed agent
#     delegation (the `_task_runner` singleton stays in brain).
#   - run_background_task / retry_background_task — detached same-agent runs
#     (BackgroundTaskRunner in engine/background_tasks.py).
#
# (The worker_* control tools that used to live here were removed 2026-07-13 —
#  their registry lost its last writer when the native loop died, so
#  worker_abort & co. could never affect anything.)
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - request state via `get_request_context()` (engine.context).
#   - brain runtime symbols (`list_agents`, `_get_delegation_scope`,
#     `_task_runner`, `_current_agent`) reached lazily via `import brain as
#     _brain`. NO top-level `import brain` (cycle).
#
# brain.py re-exports all via `from engine.tools.delegation_tools import (...)`.

from __future__ import annotations

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


def tool_delegate_task(args: dict) -> str:
    """Delegate a task to another agent — runs in a background thread."""
    import brain as _brain
    agent_id = args.get("agent", "")
    task = args.get("task", "")
    wait = args.get("wait", True)
    if not agent_id or not task:
        return _err("delegate_task: agent and task are required")

    available = _brain.list_agents()
    if agent_id not in available:
        return _err(f"delegate_task: agent '{agent_id}' not found. Available: {', '.join(available)}")

    # Team-aware delegation scoping (prefer thread-local for concurrent requests)
    caller_id = get_request_context().delegate_agent_id
    if not caller_id:
        agent = get_request_context().current_agent or _brain._current_agent
        caller_id = agent.agent_id if agent else None
    if caller_id:
        scope = _brain._get_delegation_scope(caller_id)
        if agent_id not in scope:
            return _err(f"delegate_task: '{caller_id}' cannot delegate to '{agent_id}'. Allowed: {', '.join(scope)}")

    if not _brain._task_runner:
        return _err("Task runner not initialized")

    task_id = _brain._task_runner.submit(agent_id, task, args.get("model"))

    if wait:
        # Synchronous: wait for result (get_result joins with a 300s cap).
        result = _brain._task_runner.get_result(task_id)
        if result and result.get("status") == "completed":
            return _ok({
                "task_id": task_id,
                "agent": agent_id,
                "task": task,
                "response": result.get("result", ""),
            })
        elif result and result.get("status") == "running":
            # Join cap hit but the delegate is STILL WORKING — not a failure.
            # (Pre-hardening this surfaced as an error and the task's later
            # result was silently lost to the caller.)
            return _ok({
                "task_id": task_id,
                "agent": agent_id,
                "status": "running",
                "message": ("Delegate still running after 300s — NOT failed. "
                            f"Poll task_status(task_id='{task_id}') to fetch "
                            "the result later in this turn, or tell the user "
                            "it is still working."),
            })
        elif result:
            return _err(f"delegate_task: {result.get('status')} — {result.get('error', '')}")
        return _err("delegate_task: no result")
    else:
        # Async: return task_id immediately
        return _ok({
            "task_id": task_id,
            "agent": agent_id,
            "task": task,
            "status": "running",
            "message": f"Task submitted. Use task_status(task_id='{task_id}') to check progress.",
        })


def tool_task_status(args: dict) -> str:
    """Check status of a background task."""
    import brain as _brain
    if not _brain._task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if task_id:
        status = _brain._task_runner.get_status(task_id)
        if not status:
            return _err(f"Task '{task_id}' not found")
        # Truncate long results
        if status.get("result") and len(status["result"]) > 2000:
            status["result"] = status["result"][:2000] + "..."
        return _ok(status)
    else:
        # List all tasks
        tasks = _brain._task_runner.list_tasks()
        for t in tasks:
            if t.get("result") and len(t["result"]) > 200:
                t["result"] = t["result"][:200] + "..."
        return _ok({"tasks": tasks, "count": len(tasks)})


def tool_task_cancel(args: dict) -> str:
    """Cancel a running background task."""
    import brain as _brain
    if not _brain._task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if not task_id:
        return _err("task_cancel: task_id is required")
    if _brain._task_runner.cancel(task_id):
        return _ok({"task_id": task_id, "status": "cancelled"})
    return _err(f"Cannot cancel task '{task_id}' — not found or not running")


def tool_run_background_task(args: dict) -> str:
    """Spawn a DETACHED, same-agent background run. Returns immediately with a
    task id; the result is delivered to this session on its NEXT turn (see
    handlers/chat.py next-turn injection). Distinct from delegate_task (which
    targets ANOTHER agent and can block for the result)."""
    import sys as _sys
    from engine.background_tasks import background_task_runner

    title = (args.get("title") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return _err("run_background_task: prompt is required")

    ctx = get_request_context()
    # Nesting guard: a background run must NOT spawn further background tasks
    # (unbounded fan-out / infinite regress). Background runs set current_bg_task.
    if getattr(ctx, "current_bg_task", False):
        return _err("run_background_task: cannot start a background task from "
                    "inside a background task — do the work directly here.")

    session_id = ctx.current_session_id or ""
    if not session_id:
        return _err("run_background_task: no active session")
    # The live SessionManager singleton lives in the entry-point module, which
    # is `__main__` under launchd (NOT `server` — `import server` would create a
    # SECOND module with an empty session cache). Resolve the same way
    # server.py's `_inject_server_globals` does.
    _srv = _sys.modules.get("__main__") or _sys.modules.get("server")
    _sessions = getattr(_srv, "sessions", None) if _srv else None
    if _sessions is None:
        return _err("run_background_task: session manager unavailable")
    session = _sessions.peek(session_id) or _sessions.get(session_id)
    if session is None:
        return _err("run_background_task: session not loaded")

    # Same-turn grouping: every fan-out call the model emits in ONE turn belongs
    # to one group. Prefer the model's explicit group_id; otherwise synthesize a
    # stable one from the turn id so sibling calls collapse together WITHOUT
    # relying on the model (it drops group_id ~half the time, esp. local models —
    # measured in eval/fanout_probe.py). A lone call in a turn becomes a
    # group-of-one, which the join handles transparently.
    group_id = (args.get("group_id") or "").strip() or None
    follow_up = (args.get("follow_up") or "").strip() or None
    if not group_id:
        turn_id = getattr(ctx, "current_turn_id", "") or ""
        group_id = f"auto-{turn_id}" if turn_id else f"auto-{session_id}-solo-{__import__('uuid').uuid4().hex[:8]}"

    task_id = background_task_runner.spawn(
        session=session, title=title, prompt=prompt,
        group_id=group_id, follow_up=follow_up,
        spawn_turn_id=getattr(ctx, "current_turn_id", "") or "")
    return _ok({
        "task_id": task_id,
        "status": "running",
        "group_id": group_id,
        "note": ("Background task started. Tell the user it's running in the "
                 "Hintergrundaufgaben panel; its result will arrive automatically "
                 "once finished. Do NOT wait for it — finish this turn now. If you "
                 "started several tasks for one request, give them all the SAME "
                 "group_id and put the combine step in follow_up."),
    })


def tool_retry_background_task(args: dict) -> str:
    """Retry ONE failed background task (status error/timeout/empty) exactly
    once, optionally on a different model. Server-enforced cap: a task that is
    itself a retry, or that already has a retry pointing at it, is refused —
    the model cannot loop. User-cancelled tasks are refused too (a deliberate
    Stopp is the user's decision; ask them instead of re-running)."""
    import sys as _sys
    import brain as _brain
    from server_lib.db import ChatDB
    from engine.background_tasks import background_task_runner

    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return _err("retry_background_task: task_id is required")

    ctx = get_request_context()
    if getattr(ctx, "current_bg_task", False):
        return _err("retry_background_task: cannot retry from inside a "
                    "background task.")
    session_id = ctx.current_session_id or ""
    if not session_id:
        return _err("retry_background_task: no active session")

    row = ChatDB.get_background_task(task_id)
    if not row or row.get("session_id") != session_id:
        return _err(f"retry_background_task: task '{task_id}' not found in this chat")
    status = row.get("status") or ""
    if status == "running":
        return _err("retry_background_task: task is still running")
    if status == "cancelled":
        return _err("retry_background_task: this task was cancelled BY THE USER "
                    "— do not restart it. Use the partial result; if the result "
                    "is essential, ask the user how to proceed.")
    if status not in ("error", "timeout", "empty"):
        return _err(f"retry_background_task: task finished with status "
                    f"'{status}' — only error/timeout/empty tasks can be retried")
    # Server-side 1-retry cap: never retry a retry, never retry twice.
    if row.get("retry_of"):
        return _err("retry_background_task: this task is already a retry — no "
                    "second retry allowed. Do the work directly here or report "
                    "the failure.")
    if ChatDB.background_task_retry_exists(session_id, task_id):
        return _err("retry_background_task: this task was already retried once "
                    "— no second retry allowed. Do the work directly here or "
                    "report the failure.")

    model_override = (args.get("model") or "").strip()
    if model_override:
        mcfg = (_brain._models_config or {}).get(model_override)
        if not mcfg or not mcfg.get("enabled", True):
            enabled = sorted(m for m, c in (_brain._models_config or {}).items()
                             if (c or {}).get("enabled", True))
            return _err(f"retry_background_task: model '{model_override}' not "
                        f"available. Enabled models: {', '.join(enabled[:30])}")

    # Same session-resolution seam as tool_run_background_task.
    _srv = _sys.modules.get("__main__") or _sys.modules.get("server")
    _sessions = getattr(_srv, "sessions", None) if _srv else None
    if _sessions is None:
        return _err("retry_background_task: session manager unavailable")
    session = _sessions.peek(session_id) or _sessions.get(session_id)
    if session is None:
        return _err("retry_background_task: session not loaded")

    # The retry runs in a NEW group keyed to THIS turn (multiple retries issued
    # in one delivery turn join together). The original group's successful
    # sibling outputs are re-attached at join time via retry_of (see
    # _build_group_preamble) — their one-time delivery is already spent.
    turn_id = getattr(ctx, "current_turn_id", "") or ""
    group_id = f"auto-{turn_id}" if turn_id else (
        f"auto-{session_id}-retry-{__import__('uuid').uuid4().hex[:8]}")

    new_id = background_task_runner.spawn(
        session=session,
        title=(row.get("title") or "Hintergrundaufgabe"),
        prompt=(row.get("prompt") or ""),
        group_id=group_id,
        follow_up=(row.get("follow_up") or None),
        spawn_turn_id=turn_id,
        retry_of=task_id,
        model_override=model_override,
    )
    return _ok({
        "task_id": new_id,
        "retry_of": task_id,
        "status": "running",
        "model": model_override or row.get("model") or "",
        "note": ("Retry started (the one allowed retry for this task). The "
                 "result arrives automatically once finished — do NOT wait for "
                 "it; finish this turn now."),
    })
