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
# Group-level straggler deadline. MUST be >= the per-task timeout: a member is
# allowed its full _TIMEOUT_S on its own, so the group can't force-fail it
# sooner. This is a BACKSTOP that fires shortly AFTER a straggler's own timeout
# would have terminated it, in case that didn't take — then the group delivers
# as a partial. (Was 600s < _TIMEOUT_S, which wrongly killed members early.)
_GROUP_TIMEOUT_S = _TIMEOUT_S + 120.0  # per-task timeout + 2 min grace


class _Cancelled(Exception):
    pass


class BackgroundTaskRunner:
    """Process-wide singleton. Threads are daemonic; durable state is in the DB."""

    def __init__(self):
        self._lock = threading.Lock()
        # task_id -> {"turn_id": str, "cancel": threading.Event}
        self._live: dict[str, dict] = {}

    # ---- public API -------------------------------------------------------

    def spawn(self, *, session, title: str, prompt: str,
              group_id=None, follow_up=None, parent_task_id=None) -> str:
        """Insert a running row + launch the worker thread. Returns task_id
        immediately. `session` is the live Session the tool was called from —
        we snapshot the fields we need under its lock, never hand the object to
        the thread.

        Fan-out: group_id links calls the model emitted together; follow_up is
        the recombine instruction carried out once the whole group is done.
        parent_task_id is set when spawned from inside a background run (the
        tool refuses this — nesting guard — so it's belt-and-suspenders)."""
        task_id = uuid.uuid4().hex
        with session.lock:
            snapshot = {
                "session_id": session.id,
                "agent_id": session.agent_id,
                "model": session.model,
                "user_id": getattr(session, "user_id", "") or "",
                "project": getattr(session, "project", "") or "",
                "thinking_level": getattr(session, "thinking_level", "") or "",
                "group_id": group_id or None,
            }
        # Per-model fan-out offload: a chat model may declare a cheaper model to
        # run its fanned-out leaf tasks (e.g. mistral-medium chats offload to
        # mistral-small). The decompose/orchestrate reasoning stays on the chat
        # model; only the leaf runs swap. Empty/unset → leaf stays on chat model.
        snapshot["model"] = self._resolve_fanout_model(snapshot["model"], snapshot)
        title = (title or "Hintergrundaufgabe").strip()[:200]
        prompt = (prompt or "").strip()
        ChatDB.create_background_task(
            task_id, snapshot["session_id"], snapshot["agent_id"],
            snapshot["model"], title, prompt,
            group_id=group_id, follow_up=follow_up, parent_task_id=parent_task_id)
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

    def sweep_group_timeouts(self) -> int:
        """Force-deliver fan-out groups stalled on a straggler past
        _GROUP_TIMEOUT_S. Marks the running members error (DB), trips their live
        cancel flags so the sidecar turns stop, then claims + delivers each group
        as a partial. Driven by the bgtask-group-timeout daemon loop. Returns the
        number of groups delivered."""
        from server_lib.db import ChatDB
        delivered = 0
        affected = ChatDB.sweep_stalled_groups(_GROUP_TIMEOUT_S) or []
        for session_id, gid in affected:
            # Stop any still-live straggler threads for this group (best effort —
            # the DB row is already error, so a late finish_background_task is a
            # harmless overwrite).
            try:
                rows = ChatDB.list_background_tasks(session_id) or []
                for r in rows:
                    if r.get("status") == "running":  # shouldn't remain, but be safe
                        self.cancel(r.get("id"))
            except Exception:
                pass
            try:
                members = ChatDB.claim_background_group(gid)
                if members:
                    from handlers.chat import deliver_background_group
                    deliver_background_group(session_id, gid, members)
                    delivered += 1
            except Exception as e:
                print(f"[bgtask-sweep] deliver failed grp {gid}: {e}", flush=True)
        return delivered

    def _resolve_fanout_model(self, chat_model: str, snapshot: dict) -> str:
        """Resolve the model a fanned-out leaf task should run on.

        The chat model's registry entry may carry `background_task_model` — a
        cheaper model its leaf tasks offload to. Empty/unset, or pointing at a
        model that isn't enabled, falls back to the chat model unchanged. When
        we swap, the chat's thinking_level is smart-matched to the leaf model's
        reasoning granularity (see `_match_thinking_level`)."""
        import brain as _brain
        cfg = (_brain._models_config or {}).get(chat_model) or {}
        target = (cfg.get("background_task_model") or "").strip()
        if not target or target == chat_model:
            return chat_model
        tcfg = (_brain._models_config or {}).get(target)
        if not tcfg or not tcfg.get("enabled", True):
            # Configured but missing/disabled — don't silently route to a dead
            # model; keep the chat model and leave a breadcrumb.
            print(f"[bgtask] background_task_model '{target}' for '{chat_model}' "
                  f"missing/disabled — leaf stays on chat model", flush=True)
            return chat_model
        snapshot["thinking_level"] = self._match_thinking_level(
            snapshot.get("thinking_level"), target, tcfg)
        return target

    @staticmethod
    def _match_thinking_level(level: str | None, target: str, tcfg: dict) -> str:
        """Smart-match a requested thinking_level to the leaf model's reasoning
        granularity, preserving intent rather than dropping it:

        - '' / 'none'                  → kept verbatim (no thinking requested).
        - target can't reason (none)   → '' (model default; thinking impossible).
        - target is on/off-only        → low/medium/high collapse to 'high'
          (inline_tags / mistral_blocks)   ('thinking on' stays on, not dropped).
        - target has full granularity  → kept verbatim (reasoning_field /
          (reasoning_field/openai_opaque)  openai_opaque accept low/medium/high).
        """
        lvl = (level or "").strip().lower()
        if lvl in ("", "none"):
            return lvl
        fmt = (tcfg or {}).get("thinking_format", "none")
        if fmt == "none":
            return ""
        if fmt in ("inline_tags", "mistral_blocks"):
            return "high"  # on/off models: any positive level → on
        return lvl  # reasoning_field / openai_opaque: full low/medium/high

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
                current_bg_task=True,  # nesting guard: run_background_task refuses here
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
                    bg_task=True,  # nesting guard: this run can't spawn bg-tasks
                )
            output = res.get("reply") or ""
            usage = res.get("usage_total") or {}
            usage_in = int(usage.get("input_tokens") or 0)
            usage_out = int(usage.get("output_tokens") or 0)
            tool_calls = int(res.get("tool_calls_total") or 0)
            # Cost logging — keyed by `model`, which by here is the ACTUAL
            # executing model (fan-out offload swap + any GDPR force-local swap
            # both already applied). Still inside the request_context above, so
            # agent/user resolve like every other _log_call_cost caller. Skips
            # itself when usage is 0 (cancel/error before any tokens).
            _brain._log_call_cost(model, usage_in, usage_out,
                                  session_id=snap["session_id"])
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
            # Group-aware delivery. If this task belongs to a group, attempt the
            # ATOMIC claim — only the LAST finisher (rowcount==1) wins and fires
            # the join delivery; the others no-op. A standalone task (no group_id)
            # falls back to the legacy per-task delivery. Idle-only + single-flight
            # are enforced inside the delivery fns; runs on THIS (daemon) thread.
            try:
                gid = snap.get("group_id")
                if gid:
                    members = ChatDB.claim_background_group(gid)
                    if members:  # we are the last finisher — deliver the group
                        from handlers.chat import deliver_background_group
                        deliver_background_group(snap["session_id"], gid, members)
                    # members is None → not last, or already claimed → no-op
                else:
                    from handlers.chat import deliver_background_results
                    deliver_background_results(snap["session_id"])
            except Exception as e:  # never let delivery kill the runner thread
                print(f"[bgtask] auto-deliver failed: {e}", flush=True)


# Process-wide singleton.
background_task_runner = BackgroundTaskRunner()
