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
        # task_id -> {"turn_id": str, "cancel": threading.Event,
        #             "stream": LiveStream|None}
        self._live: dict[str, dict] = {}

    @staticmethod
    def _make_stream():
        """Per-task live event buffer for the transcript SSE (replay + follow).
        Reuses server.py's LiveStream — resolved via sys.modules like the other
        engine→server seams (the entry module is `__main__` under launchd).
        Returns None when unavailable (unit tests) — the run then simply has no
        live transcript; everything else is unaffected."""
        import sys as _sys
        _srv = _sys.modules.get("__main__") or _sys.modules.get("server")
        cls = getattr(_srv, "LiveStream", None) if _srv else None
        try:
            return cls() if cls is not None else None
        except Exception:
            return None

    def get_stream(self, task_id: str):
        """The live transcript stream of a RUNNING task, or None once finished
        (the transcript endpoint then replays the stored row instead)."""
        with self._lock:
            live = self._live.get(task_id)
            return live.get("stream") if live else None

    # ---- public API -------------------------------------------------------

    def spawn(self, *, session, title: str, prompt: str,
              group_id=None, follow_up=None, parent_task_id=None,
              spawn_turn_id="", retry_of=None, model_override="") -> str:
        """Insert a running row + launch the worker thread. Returns task_id
        immediately. `session` is the live Session the tool was called from —
        we snapshot the fields we need under its lock, never hand the object to
        the thread.

        Fan-out: group_id links calls the model emitted together; follow_up is
        the recombine instruction carried out once the whole group is done.
        parent_task_id is set when spawned from inside a background run (the
        tool refuses this — nesting guard — so it's belt-and-suspenders).
        spawn_turn_id links the task to the spawning chat turn (Stopp-cascade).
        retry_of + model_override come from retry_background_task: the retry
        clone may run on an explicitly chosen model — an explicit override wins
        over the per-model background_task_model offload (the whole point is to
        escape the model that just failed)."""
        task_id = uuid.uuid4().hex
        with session.lock:
            snapshot = {
                "session_id": session.id,
                "agent_id": session.agent_id,
                "model": session.model,
                "user_id": getattr(session, "user_id", "") or "",
                "project": getattr(session, "project", "") or "",
                # Needed so the worker thread can rebuild the project's DOMAIN
                # context (apply_domain_context) — a Code-Mode project's
                # working_dir / code index / tool scoping live there, and a bare
                # `with request_context()` on a fresh thread has none of it.
                "project_id": getattr(session, "project_id", "") or "",
                "research_mode_override": getattr(session, "research_mode_override", None),
                "thinking_level": getattr(session, "thinking_level", "") or "",
                "group_id": group_id or None,
                # M1 (G1): inherit the spawning session's anonymisation mapping.
                # A fan-out leaf must see the SAME fake world as its parent —
                # otherwise it either runs unprotected (today: the leaf reads the
                # customer file in the clear and may google real names) or, with a
                # mapping of its own, invents a SECOND fake for every value and
                # returns identities the parent can never resolve.
                "gdpr_mapping_id": getattr(session, "_gdpr_mapping_id", "") or "",
            }
        title = (title or "Hintergrundaufgabe").strip()[:200]
        prompt = (prompt or "").strip()
        # Per-model fan-out offload: a chat model may declare a cheaper model to
        # run its fanned-out leaf tasks (e.g. mistral-medium chats offload to
        # mistral-small). The decompose/orchestrate reasoning stays on the chat
        # model; only the leaf runs swap. Empty/unset → leaf stays on chat model.
        # background_task_model == "auto" → classify THIS sub-task's prompt and
        # pick the best-fitting leaf model (same dispatcher as composer Auto).
        if model_override:
            import brain as _brain
            snapshot["model"] = model_override
            snapshot["thinking_level"] = self._match_thinking_level(
                snapshot.get("thinking_level"), model_override,
                (_brain._models_config or {}).get(model_override) or {})
        else:
            snapshot["model"] = self._resolve_fanout_model(snapshot["model"], snapshot, prompt)
        ChatDB.create_background_task(
            task_id, snapshot["session_id"], snapshot["agent_id"],
            snapshot["model"], title, prompt,
            group_id=group_id, follow_up=follow_up, parent_task_id=parent_task_id,
            spawn_turn_id=spawn_turn_id or "", retry_of=retry_of)
        cancel_ev = threading.Event()
        with self._lock:
            self._live[task_id] = {"turn_id": "", "cancel": cancel_ev,
                                   "stream": self._make_stream()}
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

    def cancel_session_tasks(self, session_id: str, spawn_turn_id: str = "") -> int:
        """Cancel every RUNNING background task of a session (optionally only
        those spawned by one chat turn — the Stopp-cascade case). Returns the
        number of cancel requests issued. DB rows are the source of truth for
        which tasks exist; self.cancel() no-ops for tasks whose thread already
        finished."""
        cancelled = 0
        try:
            rows = ChatDB.list_background_tasks(session_id) or []
        except Exception:
            rows = []
        for r in rows:
            if r.get("status") != "running":
                continue
            if spawn_turn_id and (r.get("spawn_turn_id") or "") != spawn_turn_id:
                continue
            if self.cancel(r.get("id")):
                cancelled += 1
        return cancelled

    def cancel_tool(self, task_id: str, tool_use_id: str) -> bool:
        """Cancel ONE in-flight tool call of a running task. Resolve the task's
        live turn_id, then BOTH: (1) if the tool is a subprocess-backed tool
        (python_exec / execute_command) registered for kill, SIGKILL its process
        group — a TRUE kill, the work actually stops; (2) trip the sidecar's
        per-tool cancel so the loop is unblocked immediately either way (covers
        non-killable tools + races where the kill lands first). The TASK keeps
        running. Returns True if the task was live and we acted on the tool."""
        import handlers.sidecar_proxy as sidecar_proxy
        from engine.tool_exec import kill_tool_process
        with self._lock:
            live = self._live.get(task_id)
        if not live:
            return False
        turn_id = live.get("turn_id") or ""
        if not turn_id:
            return False
        # (1) Real kill for subprocess-backed tools (no-op if not registered).
        killed = kill_tool_process(turn_id, tool_use_id)
        # (2) Always unblock the sidecar loop's wait for this tool.
        unblocked = sidecar_proxy.cancel_tool(turn_id, tool_use_id)
        return bool(killed or unblocked)

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
                members = ChatDB.claim_background_group(session_id, gid)
                if members:
                    from handlers.chat import deliver_background_group
                    deliver_background_group(session_id, gid, members)
                    delivered += 1
            except Exception as e:
                print(f"[bgtask-sweep] deliver failed grp {gid}: {e}", flush=True)
        return delivered

    def _resolve_fanout_model(self, chat_model: str, snapshot: dict, prompt: str = "") -> str:
        """Resolve the model a fanned-out leaf task should run on.

        The chat model's registry entry may carry `background_task_model` — a
        cheaper model its leaf tasks offload to. Empty/unset, or pointing at a
        model that isn't enabled, falls back to the chat model unchanged. When
        we swap, the chat's thinking_level is smart-matched to the leaf model's
        reasoning granularity (see `_match_thinking_level`).

        Special value `"auto"`: instead of a fixed offload model, classify this
        sub-task's `prompt` (keyword / LLM / hybrid per auto_route.classifier_mode)
        and pick the best-fitting enabled model — the same intent-routing the
        composer's Auto uses, applied per fanned-out leaf."""
        import brain as _brain
        cfg = (_brain._models_config or {}).get(chat_model) or {}
        target = (cfg.get("background_task_model") or "").strip()
        if not target or target == chat_model:
            return chat_model
        if target == "auto":
            # Intent-route the leaf: classify the sub-task prompt, pick by tier.
            # No attachments (leaf prompts are text) and no ACL scope (the parent
            # turn already passed the user's gate). Always returns a concrete id.
            # Use the full analysis so complexity + the use-case map apply here
            # too (resolve_task_analysis falls open to the keyword purpose).
            _an = _brain.resolve_task_analysis(prompt or "") or {}
            picked = _brain._resolve_auto_model_tiered(
                _an.get("purpose"),
                complexity=_an.get("complexity"),
                task_types=_an.get("task_types"))
            if not picked or picked == chat_model:
                return chat_model
            tcfg = (_brain._models_config or {}).get(picked) or {}
            snapshot["thinking_level"] = self._match_thinking_level(
                snapshot.get("thinking_level"), picked, tcfg)
            return picked
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
            live_stream = (self._live.get(task_id) or {}).get("stream")
        ChatDB.set_background_task_turn(task_id, turn_id)

        # Live transcript tap: forward the loop's Brain-vocabulary events
        # (text_delta / thinking_* / tool_call / tool_result / usage) into the
        # per-task LiveStream so GET /v1/background-tasks/<id>/transcript can
        # replay + follow while the task runs. Tool results are capped so a
        # 100KB read_document result doesn't balloon the replay buffer — the
        # full text still reaches the model; only the transcript VIEW is
        # trimmed (result_chars carries the true length).
        def _emit(et, data):
            if live_stream is None:
                return
            try:
                if et == "tool_result" and isinstance(data, dict):
                    r = data.get("result")
                    if isinstance(r, str):
                        data = dict(data)
                        data["result_chars"] = len(r)
                        if len(r) > 4000:
                            data["result"] = r[:4000] + " … [gekürzt]"
                live_stream.emit(et, data)
            except Exception:
                pass  # a transcript viewer must never break the run

        status = "done"
        output = ""
        error = ""
        usage_in = usage_out = tool_calls = 0
        tool_events = []
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
                current_bg_task_id=task_id,  # ask_user keys its pending slot on this
                cost_purpose="background_task",  # cost-ledger use-case bucket
            ):
                # Rebuild the session's DOMAIN context on this fresh thread —
                # the SAME seam the chat worker uses. Without it the sub-agent
                # ran with working_dir=None: in a Code-Mode project that means
                # relative paths fall back to the SERVER's process cwd instead of
                # the project (user-reported: "die Subagenten schreiben in das
                # Arbeitsverzeichnis"), the per-project code index is missing
                # (code_graph_db=None), the code_* tools stay deferred and the
                # MemPalace exclusions don't apply. Calling apply_domain_context
                # inherits ALL of it at once instead of re-deriving fields here.
                # MUST run BEFORE build_first_turn_prefix — the code-mode branch
                # of the system prompt and the un-deferred code tools depend on it.
                try:
                    _brain.apply_domain_context(
                        agent_id=snap["agent_id"],
                        project=snap.get("project") or "",
                        project_id=snap.get("project_id") or "",
                        user_id=snap["user_id"],
                        research_mode_override=snap.get("research_mode_override"),
                    )
                except Exception:
                    pass  # best-effort: a broken project must not kill the task
                system_prompt, _tools, _ = _brain.build_first_turn_prefix(
                    model, snap["agent_id"],
                    mcp_manager=getattr(_brain, "_mcp_manager", None),
                    discovered_tools=set(),
                    is_openai_shape=False,
                    purpose="interactive",
                )
                # Code mode: tell the sub-agent ITS output folder — the spawning
                # CHAT's folder plus a per-task subfolder (`…/subagents/<task_id>/`).
                # Without it the model invents a name, and a fan-out's concurrent
                # sub-agents would collide on the same `report.html`. Runs AFTER
                # apply_domain_context (working_dir is set) and inside the
                # request_context that carries current_bg_task_id.
                try:
                    _pre = _brain._artifact_folder_preamble_text(
                        snap["agent_id"], snap["session_id"])
                    if _pre:
                        prompt = f"{_pre}\n\n{prompt}"
                    else:
                        print(f"[bg-task] {task_id[:8]} no output-folder preamble "
                              f"(code_mode off or no working_dir)", flush=True)
                except Exception as _e:
                    print(f"[bg-task] {task_id[:8]} preamble failed: {_e}", flush=True)
                messages = [{"role": "user", "content": prompt}]
                # M1 (G1): join the parent session's fake world BEFORE the gate.
                #
                # The spawning chat model only ever saw fakes, so the `prompt` it
                # wrote is ALREADY pseudonymised. Binding the parent mapping means
                # (a) the leaf's tool loop is protected exactly like the chat's —
                # result seam, args-deanon and web-egress gate all read this field
                # — and (b) values the leaf discovers itself (a customer file it
                # reads) get the SAME fake as in the parent, instead of a second
                # identity nobody can reconcile.
                #
                # Rehydrates from chats.db: the spawning turn has long since ended
                # and called close_mapping(), so the in-memory registry entry is
                # usually gone by the time a leaf gets here.
                _inherited_mid = snap.get("gdpr_mapping_id") or ""
                _bound = _brain.gdpr_bind_mapping(_inherited_mid) if _inherited_mid else False

                # GDPR / quota seam — same gate every background caller passes
                # through (NEVER a bypass, not even when we already bound a
                # mapping: the gate also enforces ARL classification and the
                # quota/force-local model swap, which have nothing to do with
                # pseudonymisation).
                #
                # When a parent mapping is bound the payload is already fake-space,
                # so the scan finds nothing and the gate returns identity — it just
                # must not REPLACE our inherited id with the "" that comes back.
                gdpr_blobs = [system_prompt, prompt]
                model, new_blobs, _deanon = _brain.gdpr_pick_model_for_background(
                    model, gdpr_blobs, purpose="background_task")
                if new_blobs is not gdpr_blobs:
                    system_prompt = new_blobs[0]
                    messages = [{"role": "user", "content": new_blobs[1]}]
                if not _bound:
                    # No parent to inherit from (task spawned from a
                    # non-anonymising session whose prompt itself carried PII):
                    # a mapping minted HERE must still reach the tool loop, or the
                    # gate would again protect only the entry prompt.
                    _inherited_mid = getattr(_deanon, "mapping_id", "") or ""

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
                    bg_task_id=task_id,  # ask_user keys its pending slot on this
                    emit=_emit,    # live transcript tap (subagent pane / panel)
                    # M1: run the whole leaf turn under the (inherited or freshly
                    # minted) mapping — this is what makes its tool loop obey the
                    # seams and the egress gate.
                    gdpr_mapping_id=_inherited_mid,
                )
                # The leaf may have extended the SHARED mapping (it read a document
                # the parent never saw). Persist it so the parent's next turn can
                # reverse those tokens and reuses the same fakes — otherwise the
                # new tokens die with this thread's registry entry.
                if _inherited_mid:
                    _brain.gdpr_persist_mapping(
                        _inherited_mid, snap["session_id"], turn_id=f"bgtask:{task_id}")
            output = res.get("reply") or ""
            usage = res.get("usage_total") or {}
            usage_in = int(usage.get("input_tokens") or 0)
            usage_out = int(usage.get("output_tokens") or 0)
            tool_calls = int(res.get("tool_calls_total") or 0)
            # Map the sidecar's tool_events to the assistant.metadata.tools[]
            # shape the Activity panel already renders (so bg-task tool calls
            # become the SAME expandable cards as in-chat tools, and survive a
            # reload). Synthesise a tool_use_id per entry (the blocking path
            # doesn't surface the real ids) so the client can key cards stably.
            tool_events = []
            for _i, _ev in enumerate(res.get("tool_events") or []):
                tool_events.append({
                    "name": _ev.get("name", ""),
                    "args": _ev.get("args") or {},
                    "tool_use_id": _ev.get("tool_use_id") or f"bg-{task_id}-{_i}",
                    "tool_round": _ev.get("round"),
                    "result": _ev.get("result_text") or "",
                    "is_error": bool(_ev.get("is_error")),
                    "elapsed_ms": _ev.get("elapsed_ms"),
                })
            # Cost is logged CENTRALLY by background_call above (one row, keyed
            # by the ACTUAL executing model — fan-out offload + GDPR force-local
            # swaps are already applied to `model` — and tagged 'background_task'
            # via the request_context cost_purpose set in this worker). No
            # explicit _log_call_cost here, or it would double-count.
            if res.get("timed_out"):
                # Wall-clock limit (run_turn_blocking enforces _TIMEOUT_S now).
                # Distinct from user-cancel: the delivery preamble tells the
                # model a RETRY is legitimate here, unlike a deliberate Stopp.
                status = "timeout"
                error = (f"Zeitlimit überschritten ({int(_TIMEOUT_S // 60)} min) "
                         f"— Teilergebnis behalten")
            elif cancel_ev.is_set():
                status = "cancelled"
            elif res.get("error"):
                status = "error"
                error = str(res.get("error"))
            elif res.get("cancelled"):
                status = "cancelled"
            elif not output.strip():
                # Finished "cleanly" but produced nothing — an insufficient
                # response the join must surface as retryable, not as success.
                status = "empty"
                error = "Leere Antwort — der Lauf lieferte kein Ergebnis"
        except _Cancelled:
            status = "cancelled"
        except _brain.GDPRSkipError as se:
            # Policy 'skip' — deliberate no-op, NOT a failure. Complete the task
            # empty rather than marking it error (which would read as broken).
            status = "done"
            output = f"(Übersprungen durch DSGVO-Richtlinie: {se})"
        except _brain.GDPRBlockedError as ge:
            status = "error"
            error = f"Blocked before run: {ge}"
        except Exception as e:  # noqa: BLE001 — surface, never silently swallow
            status = "error"
            error = f"{type(e).__name__}: {e}"
        finally:
            # If cancel tripped after a partial reply, keep the partial text.
            # (timeout keeps its own status — the classes drive different
            # retry guidance in the delivery preamble.)
            if cancel_ev.is_set() and status not in ("error", "timeout"):
                status = "cancelled"
            ChatDB.finish_background_task(
                task_id, status, output=output, error=error,
                usage_in=usage_in, usage_out=usage_out, tool_calls=tool_calls,
                tool_events=tool_events)
            # Terminal event for attached transcript viewers — emitted AFTER the
            # DB write (a viewer reconciling on `done` finds the finished row)
            # and BEFORE the pop (late attaches fall back to the stored replay).
            _emit("done", {
                "status": status, "error": error,
                "usage": {"input": usage_in, "output": usage_out},
                "tool_calls": tool_calls,
            })
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
                    members = ChatDB.claim_background_group(snap["session_id"], gid)
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
