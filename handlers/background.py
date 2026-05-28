"""HTTP surface for detached background tasks (the 'Hintergrundaufgaben' panel).

Routes (registered in server.py):
  GET    /v1/background-tasks?session_id=X      → list tasks for a session
  POST   /v1/background-tasks/cancel {task_id}   → request cancel (partial kept)
  DELETE /v1/background-tasks {task_id}          → remove a finished/aborted row
  GET    /v1/background-tasks/<id>/transcript    → SSE: live sidecar events while
                                                   running, else a single replay
                                                   of the stored final output

Durable state lives in the `background_tasks` table (ChatDB); the runner
(`engine.background_tasks.background_task_runner`) owns the live threads.
"""

from __future__ import annotations

import urllib.request
import urllib.error
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

        Running task → proxy the sidecar's live event stream. Finished task (or
        sidecar log already purged) → replay the stored output as one terminal
        event, so the client uses a single SSE code path either way.
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

        def emit(event_type, data):
            try:
                self.wfile.write(encode_sse(event_type, data))
                self.wfile.flush()
                return True
            except (OSError, BrokenPipeError):
                return False

        # Always lead with the request that started this task, so the transcript
        # shows the ANFRAGE (prompt + title), not just the run's result — for
        # both the live and the stored-replay path below.
        emit("request", {"title": row.get("title") or "", "prompt": row.get("prompt") or ""})

        turn_id = row.get("turn_id") or ""
        if row.get("status") == "running" and turn_id:
            import handlers.sidecar_proxy as _sp
            sc_url = _sp.sidecar_url() + f"/turn/{turn_id}/events?since=0"
            req = urllib.request.Request(sc_url, method="GET")
            req.add_header("Accept", "text/event-stream")
            try:
                resp = urllib.request.urlopen(req, timeout=3600.0)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                resp = None
            if resp is not None:
                try:
                    # Raw passthrough of the sidecar SSE frames — same wire shape
                    # the chat recovery path consumes, no re-translation needed.
                    for raw in resp:
                        try:
                            self.wfile.write(raw)
                            self.wfile.flush()
                        except (OSError, BrokenPipeError):
                            break
                finally:
                    resp.close()
                return
            # Sidecar unreachable / log purged → fall through to stored replay.

        # Finished (or no live stream available): replay the stored output.
        full = ChatDB.get_background_task(task_id) or row
        text = full.get("output") or ""
        if text:
            emit("text_delta", {"text": text})
        emit("done", {
            "status": full.get("status"),
            "error": full.get("error") or "",
            "usage": {"input": full.get("usage_in"), "output": full.get("usage_out")},
            "tool_calls": full.get("tool_calls"),
        })
