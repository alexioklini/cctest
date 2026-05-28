"""Detached background tasks — same-agent/same-config agentic runs spawned
mid-turn via the `run_background_task` tool.

Flow:
  - The chat model calls `run_background_task(title, prompt)`. The tool impl
    (engine/tools/delegation.py) calls `runner.spawn(...)`, which inserts a
    `running` row and starts a daemon thread, then returns IMMEDIATELY with the
    task id. The spawning chat turn ends normally — nothing blocks.
  - The thread runs a fresh `sidecar_proxy.background_call(...)` replicating the
    session's agent/model/system-prompt/tools, with a pre-minted `turn_id` so a
    `Stopp` can cancel it via the sidecar's `POST /cancel/<turn_id>`.
  - On completion (or cancel/error) the full output is written to the
    `background_tasks` row. It is fed back into the SPAWNING session's NEXT turn
    wire-only (see handlers/chat.py), so it never enters chat history.

The runner owns no state beyond a `{task_id: cancel_flag}` map for live cancel;
everything durable lives in the `background_tasks` table (ChatDB).
"""

from __future__ import annotations

import threading
import uuid

from server_lib.db import ChatDB

# Background research can fan out across many tool rounds — give it real room.
# (The chat default is the per-agent limit; a detached research task is exactly
# the case where a high round budget is wanted, since it isn't blocking anyone.)
_MAX_ROUNDS = 40
_TIMEOUT_S = 3600.0


class _Cancelled(Exception):
    pass


class BackgroundTaskRunner:
    """Process-wide singleton. Threads are daemonic; durable state is in the DB."""

    def __init__(self):
        self._lock = threading.Lock()
        # task_id -> {"turn_id": str, "cancel": threading.Event}
        self._live: dict[str, dict] = {}

    # ---- public API -------------------------------------------------------

    def spawn(self, *, session, title: str, prompt: str) -> str:
        """Insert a running row + launch the worker thread. Returns task_id
        immediately. `session` is the live Session the tool was called from —
        we snapshot the fields we need under its lock, never hand the object to
        the thread."""
        task_id = uuid.uuid4().hex
        with session.lock:
            snapshot = {
                "session_id": session.id,
                "agent_id": session.agent_id,
                "model": session.model,
                "user_id": getattr(session, "user_id", "") or "",
                "project": getattr(session, "project", "") or "",
                "thinking_level": getattr(session, "thinking_level", "") or "",
            }
        title = (title or "Hintergrundaufgabe").strip()[:200]
        prompt = (prompt or "").strip()
        ChatDB.create_background_task(
            task_id, snapshot["session_id"], snapshot["agent_id"],
            snapshot["model"], title, prompt)
        cancel_ev = threading.Event()
        with self._lock:
            self._live[task_id] = {"turn_id": "", "cancel": cancel_ev}
        t = threading.Thread(
            target=self._run, args=(task_id, snapshot, prompt, cancel_ev),
            daemon=True, name=f"bgtask-{task_id[:8]}")
        t.start()
        return task_id

    def cancel(self, task_id: str) -> bool:
        """Request cancel: trip the flag + POST /cancel to the sidecar. The
        worker captures whatever partial output exists and marks the row
        `cancelled`. Returns True if the task was live."""
        import handlers.sidecar_proxy as sidecar_proxy
        with self._lock:
            live = self._live.get(task_id)
        if not live:
            return False
        live["cancel"].set()
        turn_id = live.get("turn_id") or ""
        if turn_id:
            sidecar_proxy.cancel_turn(turn_id)
        return True

    # ---- worker -----------------------------------------------------------

    def _run(self, task_id: str, snap: dict, prompt: str, cancel_ev: threading.Event):
        import brain as _brain
        import handlers.sidecar_proxy as sidecar_proxy
        from engine.context import request_context

        turn_id = uuid.uuid4().hex
        with self._lock:
            if task_id in self._live:
                self._live[task_id]["turn_id"] = turn_id
        ChatDB.set_background_task_turn(task_id, turn_id)

        status = "done"
        output = ""
        error = ""
        usage_in = usage_out = tool_calls = 0
        try:
            if cancel_ev.is_set():
                raise _Cancelled()
            model = snap["model"]
            # Replicate the session's exact config (system prompt + tools) so the
            # task runs as the SAME agent — the whole point of the feature. Done
            # inside a request_context so build_first_turn_prefix's
            # _current_model bind + teardown are clean on this fresh thread.
            # current_agent must be an AgentConfig object (prompt builder reads
            # it off the context), matching the chat worker (chat.py:2101).
            with request_context(
                current_agent=_brain.AgentConfig(snap["agent_id"]),
                current_user_id=snap["user_id"],
            ):
                system_prompt, _tools, _ = _brain.build_first_turn_prefix(
                    model, snap["agent_id"],
                    mcp_manager=getattr(_brain, "_mcp_manager", None),
                    discovered_tools=set(),
                    is_openai_shape=False,
                    purpose="interactive",
                )
                messages = [{"role": "user", "content": prompt}]
                # GDPR / quota seam — same gate every background caller passes
                # through (never a bypass). May swap to a local model or raise.
                gdpr_blobs = [system_prompt, prompt]
                model, new_blobs, _deanon = _brain.gdpr_pick_model_for_background(
                    model, gdpr_blobs, purpose="background_task")
                if new_blobs is not gdpr_blobs:
                    system_prompt = new_blobs[0]
                    messages = [{"role": "user", "content": new_blobs[1]}]

                if cancel_ev.is_set():
                    raise _Cancelled()

                res = sidecar_proxy.background_call(
                    messages=messages,
                    model=model,
                    system_prompt=system_prompt,
                    purpose="interactive",
                    agent_id=snap["agent_id"],
                    session_id=snap["session_id"],
                    project=snap["project"],
                    user_id=snap["user_id"],
                    max_rounds=_MAX_ROUNDS,
                    thinking_level=snap["thinking_level"] or None,
                    timeout_s=_TIMEOUT_S,
                    turn_id=turn_id,
                )
            output = res.get("reply") or ""
            usage = res.get("usage_total") or {}
            usage_in = int(usage.get("input_tokens") or 0)
            usage_out = int(usage.get("output_tokens") or 0)
            tool_calls = int(res.get("tool_calls_total") or 0)
            if cancel_ev.is_set():
                status = "cancelled"
            elif res.get("error"):
                status = "error"
                error = str(res.get("error"))
            elif res.get("cancelled"):
                status = "cancelled"
        except _Cancelled:
            status = "cancelled"
        except _brain.GDPRBlockedError as ge:
            status = "error"
            error = f"Blocked before run: {ge}"
        except Exception as e:  # noqa: BLE001 — surface, never silently swallow
            status = "error"
            error = f"{type(e).__name__}: {e}"
        finally:
            # If cancel tripped after a partial reply, keep the partial text.
            if cancel_ev.is_set() and status != "error":
                status = "cancelled"
            ChatDB.finish_background_task(
                task_id, status, output=output, error=error,
                usage_in=usage_in, usage_out=usage_out, tool_calls=tool_calls)
            with self._lock:
                self._live.pop(task_id, None)


# Process-wide singleton.
background_task_runner = BackgroundTaskRunner()
