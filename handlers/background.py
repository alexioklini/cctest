"""HTTP surface for detached background tasks (the 'Hintergrundaufgaben' panel).

Routes (registered in server.py):
  GET    /v1/background-tasks?session_id=X      → list tasks for a session
  POST   /v1/background-tasks/cancel {task_id}   → request cancel (partial kept)
  DELETE /v1/background-tasks {task_id}          → remove a finished/aborted row
  GET    /v1/background-tasks/<id>/transcript    → SSE: live Brain-vocabulary
                                                   events while running (LiveStream
                                                   replay+follow), else a single
                                                   replay of the stored final output

Durable state lives in the `background_tasks` table (ChatDB); the runner
(`engine.background_tasks.background_task_runner`) owns the live threads.
"""

from __future__ import annotations

from urllib.parse import urlparse, parse_qs

from server_lib.db import ChatDB
from server_lib.sse_stream import encode_sse


class BackgroundTasksHandlerMixin:

    def _handle_background_tasks_list(self):
        """GET /v1/background-tasks?session_id=X"""
        qs = parse_qs(urlparse(self.path).query)
        session_id = qs.get("session_id", [""])[0]
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        self._send_json({"tasks": ChatDB.list_background_tasks(session_id)})

    def _handle_background_tasks_running(self):
        """GET /v1/background-tasks/running — all RUNNING tasks across sessions
        (left-sidebar subagent tree). Non-admins see only tasks of their own
        sessions (legacy empty-owner sessions included — the list_sessions
        posture)."""
        user = getattr(self, "_auth_user", None) or {}
        is_admin = user.get("role") == "admin" or user.get("id") == "__system__"
        uid = None if is_admin else (user.get("id") or "")
        self._send_json({"tasks": ChatDB.list_running_background_tasks(user_id=uid)})

    def _handle_background_task_cancel(self):
        """POST /v1/background-tasks/cancel {task_id}"""
        from engine.background_tasks import background_task_runner
        body = self._read_json()
        task_id = (body.get("task_id") or "").strip()
        if not task_id:
            self._send_json({"error": "task_id required"}, 400)
            return
        ok = background_task_runner.cancel(task_id)
        # Not live (already finished, or unknown) → report current row state so
        # the UI can reconcile rather than treating it as an error.
        if not ok:
            row = ChatDB.get_background_task(task_id)
            if row is None:
                self._send_json({"error": "task not found"}, 404)
                return
            self._send_json({"task_id": task_id, "cancelled": False,
                             "status": row.get("status")})
            return
        self._send_json({"task_id": task_id, "cancelled": True})

    def _handle_background_task_cancel_tool(self):
        """POST /v1/background-tasks/cancel-tool {task_id, tool_use_id} — cancel
        ONE in-flight tool call of a running task. The task itself keeps going."""
        from engine.background_tasks import background_task_runner
        body = self._read_json()
        task_id = (body.get("task_id") or "").strip()
        tool_use_id = (body.get("tool_use_id") or "").strip()
        if not task_id or not tool_use_id:
            self._send_json({"error": "task_id and tool_use_id required"}, 400)
            return
        ok = background_task_runner.cancel_tool(task_id, tool_use_id)
        # Not live / already returned → report so the UI reconciles (the tool may
        # have finished between render and click). Not an error.
        self._send_json({"task_id": task_id, "tool_use_id": tool_use_id,
                         "cancelled": bool(ok)}, 200 if ok else 409)

    def _handle_background_task_delete(self):
        """DELETE /v1/background-tasks?task_id=X — Löschen. Refuses a running
        task (cancel it first) so we never orphan a live thread's final write."""
        qs = parse_qs(urlparse(self.path).query)
        task_id = (qs.get("task_id", [""])[0] or "").strip()
        if not task_id:
            self._send_json({"error": "task_id required"}, 400)
            return
        row = ChatDB.get_background_task(task_id)
        if row is None:
            self._send_json({"error": "task not found"}, 404)
            return
        if row.get("status") == "running":
            self._send_json({"error": "task still running — cancel it first"}, 409)
            return
        ChatDB.delete_background_task(task_id)
        self._send_json({"deleted": True, "task_id": task_id})

    def _handle_background_task_transcript(self, path: str):
        """GET /v1/background-tasks/<id>/transcript — 'Transkript anzeigen'.

        Running task → attach to the runner's per-task LiveStream (replay the
        buffered events, then follow live until the terminal `done`). Finished
        task (or no live stream, e.g. after a restart) → replay the stored
        output as one terminal event, so the client uses a single SSE code path
        either way. (Until v9.308.0 the live branch proxied the sidecar's
        per-turn event log — dead since the sidecar was deleted in v9.247.0, so
        'Transkript anzeigen' silently degraded to the stored replay.)
        """
        # path = /v1/background-tasks/<id>/transcript
        parts = path.strip("/").split("/")
        task_id = parts[2] if len(parts) >= 3 else ""
        row = ChatDB.get_background_task(task_id)
        if row is None:
            self._send_json({"error": "task not found"}, 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # SSE close-after-terminal rule (9.277.0, same as chat SSE): without
        # an explicit close the client never sees end-of-response (SSE has no
        # content framing) and each stream leaks a server thread + socket.
        self.close_connection = True

        def emit(event_type, data):
            try:
                self.wfile.write(encode_sse(event_type, data))
                self.wfile.flush()
                return True
            except (OSError, BrokenPipeError):
                return False

        # Always lead with the request that started this task, so the transcript
        # shows the ANFRAGE (prompt + title), not just the run's result — for
        # both the live and the stored-replay path below. `model` = the ACTUAL
        # executing model (fan-out offload / GDPR swap already applied at spawn)
        # so the Subagenten-Hub can label each card.
        emit("request", {"title": row.get("title") or "", "prompt": row.get("prompt") or "",
                         "model": row.get("model") or ""})

        from engine.background_tasks import background_task_runner
        stream = background_task_runner.get_stream(task_id)
        if row.get("status") == "running" and stream is not None:
            import queue as _q
            sub, replay, already_done = stream.attach()
            try:
                for ev_type, ev_data in replay:
                    if not emit(ev_type, ev_data):
                        return
                if already_done:
                    return  # replay already ended with the terminal event
                while True:
                    try:
                        ev_type, ev_data = sub.get(timeout=5.0)
                    except _q.Empty:
                        # SSE keepalive comment (5s rule) so proxies don't cut us.
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        except (OSError, BrokenPipeError):
                            return
                        continue
                    if not emit(ev_type, ev_data):
                        return
                    if ev_type in ("done", "error"):
                        return
            finally:
                stream.detach(sub)
            # unreachable — every path above returns; kept for clarity
        # No live stream (finished, or Brain restarted mid-run) → stored replay.

        # Finished (or no live stream available): replay the stored run —
        # tool events first (9.312.0: so a reloaded Subagenten-Karte shows the
        # same tool rows as the live view; the DB row carries them since
        # v9.51.6), then the output text, then the terminal `done`.
        full = ChatDB.get_background_task(task_id) or row
        for tev in (full.get("tool_events") or []):
            if not isinstance(tev, dict):
                continue
            tuid = tev.get("tool_use_id") or ""
            if not emit("tool_call", {"name": tev.get("name", ""),
                                      "args": tev.get("args") or {},
                                      "tool_use_id": tuid,
                                      "tool_round": tev.get("tool_round")}):
                return
            res = tev.get("result") or ""
            if not emit("tool_result", {"name": tev.get("name", ""),
                                        "tool_use_id": tuid,
                                        "result": res[:4000] + (" … [gekürzt]" if len(res) > 4000 else ""),
                                        "result_chars": len(res),
                                        "elapsed_ms": tev.get("elapsed_ms"),
                                        "is_error": bool(tev.get("is_error"))}):
                return
        text = full.get("output") or ""
        if text:
            emit("text_delta", {"text": text})
        emit("done", {
            "status": full.get("status"),
            "error": full.get("error") or "",
            "usage": {"input": full.get("usage_in"), "output": full.get("usage_out")},
            "tool_calls": full.get("tool_calls"),
        })
