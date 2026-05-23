# Delegation + worker-subagent tool bodies (extracted from brain.py, E4).
#
# Two related clusters in one module:
#   - delegate_task / task_status / task_cancel — TaskRunner-backed agent
#     delegation (the `_task_runner` singleton stays in brain).
#   - worker_status / worker_send / worker_pause / worker_resume /
#     worker_abort — thin wrappers around `execution.get_worker_registry()`.
#
# Pure relocation: JSON envelopes + error strings byte-identical to pre-E4.
# (worker_ask_user is grouped with the other AskUser tools in
#  engine/tools/ask_tools.py — it shares the AskUser blocking machinery.)
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `_thread_local` from engine.context.
#   - `execution.get_worker_registry` imported lazily inside each worker tool
#     (matches the pre-E4 in-function import — `execution` lives at repo root).
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
        # Synchronous: wait for result
        result = _brain._task_runner.get_result(task_id)
        if result and result.get("status") == "completed":
            return _ok({
                "task_id": task_id,
                "agent": agent_id,
                "task": task,
                "response": result.get("result", ""),
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


def tool_worker_status(args: dict) -> str:
    """Get current state of worker subagents."""
    from execution import get_worker_registry
    registry = get_worker_registry()
    worker_id = args.get("worker_id")
    if worker_id:
        w = registry.get(worker_id)
        if not w:
            return _err(f"Worker '{worker_id}' not found")
        return _ok({"workers": [registry.to_status_dict(w)]})
    session_id = get_request_context().current_session_id or ""
    workers = registry.list_session(session_id)
    return _ok({"workers": [registry.to_status_dict(w) for w in workers]})


def tool_worker_abort(args: dict) -> str:
    """Abort a running worker."""
    from execution import get_worker_registry
    worker_id = args.get("worker_id", "")
    reason = args.get("reason", "user requested abort")
    if not worker_id:
        return _err("worker_id is required")
    ok = get_worker_registry().cancel(worker_id, reason)
    return _ok({"aborted": ok, "worker_id": worker_id})


def tool_worker_pause(args: dict) -> str:
    """Pause a running worker."""
    from execution import get_worker_registry
    worker_id = args.get("worker_id", "")
    reason = args.get("reason", "")
    if not worker_id:
        return _err("worker_id is required")
    ok = get_worker_registry().pause(worker_id, reason)
    if not ok:
        return _err(f"Cannot pause worker '{worker_id}' (not running or not found)")
    return _ok({"paused": True, "worker_id": worker_id})


def tool_worker_resume(args: dict) -> str:
    """Resume a paused worker."""
    from execution import get_worker_registry
    worker_id = args.get("worker_id", "")
    if not worker_id:
        return _err("worker_id is required")
    ok = get_worker_registry().resume(worker_id)
    if not ok:
        return _err(f"Cannot resume worker '{worker_id}' (not paused or not found)")
    return _ok({"resumed": True, "worker_id": worker_id})


def tool_worker_send(args: dict) -> str:
    """Send input to a running or paused worker."""
    from execution import get_worker_registry
    worker_id = args.get("worker_id", "")
    message = args.get("message", "")
    role = args.get("role", "user")
    if not worker_id or not message:
        return _err("worker_id and message are required")
    ok = get_worker_registry().send(worker_id, message, role)
    if not ok:
        return _err(f"Cannot send to worker '{worker_id}' (terminal state or not found)")
    return _ok({"sent": True, "worker_id": worker_id})
