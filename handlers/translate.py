"""HTTP wrappers around server_lib/translate.

These endpoints are thin shims over the same functions used by the Tools in
brain.py. Don't reimplement logic here — call into server_lib.translate.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import queue as _queue


# Multipart upload size cap for document translation. 50MB matches read_document.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class TranslateHandlerMixin:
    """Mixin with /v1/translate/* handlers."""

    # ─── Detection ──────────────────────────────────────────────────────────

    def _handle_translate_detect(self):
        """POST /v1/translate/detect — {text} → {lang, confidence, source}."""
        body = self._read_json()
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json({"error": "text is required"}, 400)
            return
        try:
            from server_lib.translate import detect_language
            # Optional LLM fallback for short / ambiguous snippets — pulled from
            # tools_config so the user can wire any cheap model.
            try:
                tcfg = engine.get_tool_config().get("translation", {}) or {}
                fb = (tcfg.get("detection_fallback_model") or "").strip()
            except Exception:
                fb = ""
            result = detect_language(text, fallback_model=fb or None)
            self._send_json(result)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ─── Text translation ───────────────────────────────────────────────────

    def _handle_translate_text(self):
        """POST /v1/translate/text — {text, target_lang, [source_lang, glossary, model, tone]}.

        Returns the full translation as JSON (not streamed). The Mistral call
        is a single request — streaming chunks would arrive in fragments mid-
        sentence, which is worse UX than waiting 1-2s for the complete result.
        """
        body = self._read_json()
        text = body.get("text") or ""
        target_lang = (body.get("target_lang") or "").strip().lower()
        if not text.strip():
            self._send_json({"error": "text is required"}, 400)
            return
        if not target_lang:
            self._send_json({"error": "target_lang is required"}, 400)
            return
        try:
            from server_lib.translate import translate_text
            result = translate_text(
                text,
                target_lang,
                source_lang=(body.get("source_lang") or "").strip().lower(),
                glossary_slug=(body.get("glossary") or "").strip(),
                model=(body.get("model") or "").strip(),
                tone=(body.get("tone") or "").strip(),
            )
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ─── Glossary CRUD ──────────────────────────────────────────────────────

    def _handle_glossaries_list(self):
        """GET /v1/translate/glossaries → {glossaries: [...]}"""
        try:
            from server_lib.translate import list_glossaries
            self._send_json({"glossaries": list_glossaries()})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_glossary_get(self, slug: str):
        """GET /v1/translate/glossaries/<slug> → glossary dict"""
        try:
            from server_lib.translate import load_glossary
            g = load_glossary(slug)
            if not g:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(g)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_glossary_save(self):
        """POST /v1/translate/glossaries — create or overwrite (by slug/name)."""
        body = self._read_json()
        try:
            from server_lib.translate import save_glossary
            saved = save_glossary(body)
            self._send_json(saved)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_glossary_delete(self, slug: str):
        """DELETE /v1/translate/glossaries/<slug>"""
        try:
            from server_lib.translate import delete_glossary
            ok = delete_glossary(slug)
            if not ok:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json({"deleted": slug})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ─── Document translation (async + SSE) ────────────────────────────────

    def _handle_translate_document_upload(self):
        """POST /v1/translate/document — multipart upload.

        Form fields:
          - file (required, .docx/.pptx/.pdf, ≤50MB)
          - target_lang (required, ISO 639-1)
          - source_lang (optional)
          - glossary (optional slug)
          - model (optional id)
          - agent_id (optional, defaults to 'main')
          - session_id (optional — if omitted a synthetic translate-* id is
            minted so the artifact lands in a translation-specific folder)

        Returns: {job_id, filename, target_lang, source_lang, glossary, model}.
        """
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self._send_json({"error": "missing boundary"}, 400)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_DOCUMENT_BYTES + 32 * 1024:
            self._send_json({"error": "payload too large"}, 413)
            return

        raw = self.rfile.read(length)
        fields, file_name, file_bytes = _parse_multipart(raw, boundary)
        if not file_name or not file_bytes:
            self._send_json({"error": "missing file"}, 400)
            return
        ext = os.path.splitext(file_name)[1].lower()
        from server_lib.translate import DOCUMENT_EXTS
        if ext not in DOCUMENT_EXTS:
            self._send_json({
                "error": f"unsupported extension '{ext}' — supported: {sorted(DOCUMENT_EXTS)}",
            }, 400)
            return
        if len(file_bytes) > MAX_DOCUMENT_BYTES:
            self._send_json({"error": "file too large"}, 413)
            return

        target_lang = (fields.get("target_lang") or "").strip().lower()
        if not target_lang:
            self._send_json({"error": "target_lang is required"}, 400)
            return
        source_lang = (fields.get("source_lang") or "").strip().lower()
        glossary_slug = (fields.get("glossary") or "").strip()
        model = (fields.get("model") or "").strip()
        agent_id = (fields.get("agent_id") or "main").strip() or "main"
        session_id = (fields.get("session_id") or "").strip()

        # Persist the upload to a tmp file the worker can read.
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="brain-translate-")
        src_path = os.path.join(tmpdir, file_name)
        try:
            with open(src_path, "wb") as f:
                f.write(file_bytes)
        except OSError as e:
            self._send_json({"error": f"save failed: {e}"}, 500)
            return

        # Synthetic session id so the artifact promotion code doesn't collide
        # with real chat sessions. Format mirrors `sched-<id>` so the artifact
        # miner / browse view can recognise it later if we want filtering.
        # Hex prefix has to be unique in the first 8 chars — that's what the
        # artifact folder name uses. `translate-<hex>` would collide on the
        # constant prefix, so put the entropy first: `tr<14 hex>`.
        if not session_id:
            import uuid
            session_id = f"tr{uuid.uuid4().hex[:14]}"

        from server_lib.translate import JOB_REGISTRY
        job = JOB_REGISTRY.create(
            filename=file_name,
            src_path=src_path,
            target_lang=target_lang,
            source_lang=source_lang,
            glossary=glossary_slug,
            model=model,
            agent_id=agent_id,
            session_id=session_id,
        )

        # Worker thread runs the actual translation. Daemon so it doesn't
        # block server shutdown — the file lands on disk anyway, and the
        # job entry has 1h TTL for late SSE reconnects.
        t = threading.Thread(
            target=_run_translate_job,
            args=(job.id,),
            name=f"translate-doc-{job.id}",
            daemon=True,
        )
        t.start()
        self._send_json({"job_id": job.id, **job.to_dict()})

    def _handle_translate_job_status(self, job_id: str):
        """GET /v1/translate/jobs/<id> — SSE stream of progress events.

        Events:
          - status (initial replay of current job state)
          - progress (per chunk)
          - done (terminal, with output_filename)
          - error (terminal)
          - keepalive (5s ping)
        """
        from server_lib.translate import JOB_REGISTRY
        job = JOB_REGISTRY.get(job_id)
        if not job:
            self._send_json({"error": "job not found"}, 404)
            return

        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

        sub = job.subscribe()
        last_keepalive = time.time()
        try:
            while True:
                try:
                    ev = sub.get(timeout=5.0)
                except _queue.Empty:
                    # Keepalive comment — same trick chat SSE uses.
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    last_keepalive = time.time()
                    continue
                kind = ev.get("type", "status")
                payload = json.dumps(ev.get("job", {}), ensure_ascii=False)
                try:
                    self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                if kind in ("done", "error"):
                    return
        finally:
            job.unsubscribe(sub)

    def _handle_translate_job_result(self, job_id: str):
        """GET /v1/translate/jobs/<id>/result — download translated file."""
        from server_lib.translate import JOB_REGISTRY
        job = JOB_REGISTRY.get(job_id)
        if not job:
            self._send_json({"error": "job not found"}, 404)
            return
        if job.state != "done":
            self._send_json({"error": f"job not ready (state={job.state})"}, 409)
            return
        if not job.output_path or not os.path.isfile(job.output_path):
            self._send_json({"error": "output file missing"}, 410)
            return
        try:
            with open(job.output_path, "rb") as f:
                data = f.read()
        except OSError as e:
            self._send_json({"error": f"read failed: {e}"}, 500)
            return
        fname = os.path.basename(job.output_path)
        self.send_response(200)
        # Octet-stream so the browser always offers a Save dialog regardless
        # of the actual MIME (.docx/.pptx/.pdf).
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


# ─── Helpers (module-level so the worker can import without circulars) ─────

def _parse_multipart(raw: bytes, boundary: str) -> tuple[dict[str, str], str, bytes]:
    """Tiny multipart parser — pulls one file part + simple text fields.

    Mirrors handlers/projects.py:_handle_project_image_upload which works
    against the same browser-generated form data. We only handle one file
    field per request (the document upload); other parts become string fields.
    """
    fields: dict[str, str] = {}
    file_name = ""
    file_bytes = b""
    parts = raw.split(b"--" + boundary.encode())
    for p in parts:
        if b"Content-Disposition" not in p:
            continue
        head_end = p.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        head = p[:head_end].decode("latin-1", errors="replace")
        body = p[head_end + 4:]
        # Strip the trailing CRLF that comes before the next boundary marker.
        if body.endswith(b"\r\n"):
            body = body[:-2]
        # Pull the field name.
        name = ""
        fname = ""
        for line in head.split("\r\n"):
            for tok in line.split(";"):
                tok = tok.strip()
                if tok.startswith("name="):
                    name = tok.split("=", 1)[1].strip().strip('"')
                elif tok.startswith("filename="):
                    fname = tok.split("=", 1)[1].strip().strip('";')
        if fname:
            file_name = fname
            file_bytes = body
        elif name:
            try:
                fields[name] = body.decode("utf-8", errors="replace").strip("\r\n")
            except Exception:
                fields[name] = ""
    return fields, file_name, file_bytes


def _run_translate_job(job_id: str) -> None:
    """Background worker — runs translate_document_file and updates the job.

    Lives at module level (not a TranslateHandlerMixin method) so the daemon
    thread keeps no implicit reference to a handler instance.
    """
    from server_lib.translate import JOB_REGISTRY, translate_document_file
    job = JOB_REGISTRY.get(job_id)
    if not job:
        return
    try:
        # Resolve output dir to the agent's artifact folder for this synthetic
        # session so the translated file shows up under Artifacts.
        import brain  # late — picks up AGENTS_DIR + helpers
        from datetime import datetime as _dt
        folder = f"{_dt.now().strftime('%Y-%m-%d')}_{job.session_id[:8]}"
        out_dir = os.path.join(brain.AGENTS_DIR, job.agent_id, "artifacts", folder)
        os.makedirs(out_dir, exist_ok=True)

        result = translate_document_file(
            job.src_path,
            target_lang=job.target_lang,
            source_lang=job.source_lang,
            glossary_slug=job.glossary,
            model=job.model,
            output_dir=out_dir,
            progress=job.update_progress,
        )

        # Promote as artifact so the artifact panel (and miner) sees it.
        try:
            brain._after_file_write(result["output_path"], "created", job.agent_id)
        except Exception:
            pass

        job.finish(
            output_path=result["output_path"],
            runs=result.get("runs", 0),
            fallback=result.get("fallback", False),
            detected=result.get("detected"),
            noop=result.get("noop", False),
            model=result.get("model", ""),
        )
    except Exception as e:
        job.fail(str(e))
