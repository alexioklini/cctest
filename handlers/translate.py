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
# Audio/video uploads can be larger — Voxtral accepts ~25min mp3 ≈ 25MB; bump
# to 200MB so users can drop a 1h podcast or short video without resizing.
MAX_MEDIA_BYTES = 200 * 1024 * 1024


class TranslateHandlerMixin:
    """Mixin with /v1/translate/* handlers."""

    # ─── Text-to-Speech ─────────────────────────────────────────────────────

    def _handle_translate_tts_voices(self):
        """GET /v1/translate/tts/voices — list available TTS voices from the provider.

        Calls /audio/voices on the configured TTS provider. Returns
        {voices: [{slug, name, gender, languages, tags}]} sorted by name.
        Falls back to a hardcoded set if the provider endpoint fails.
        """
        try:
            import brain
            import urllib.request as _urllib

            cfg = brain.get_tool_config().get("text_to_speech", {}) or {}
            model_id = (cfg.get("default_model") or "").strip()
            if not model_id:
                raise RuntimeError("no TTS model configured — set text_to_speech.default_model in the Tools tab")
            prov = brain.resolve_provider_for_model(model_id)
            base_url = (prov.get("base_url") or "").rstrip("/")
            api_key = prov.get("api_key") or ""

            if not base_url or not api_key:
                raise RuntimeError(f"TTS provider for '{model_id}' not configured")

            all_voices = []
            seen_slugs: set = set()
            for page in range(1, 10):
                req = _urllib.Request(
                    f"{base_url}/audio/voices?page={page}&page_size=50",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with _urllib.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                for v in data.get("items", []):
                    slug = v.get("slug") or ""
                    if slug and slug not in seen_slugs:
                        seen_slugs.add(slug)
                        all_voices.append({
                            "slug": slug,
                            "name": v.get("name") or slug,
                            "gender": v.get("gender") or "",
                            "languages": v.get("languages") or [],
                            "tags": v.get("tags") or [],
                        })
                if page >= data.get("total_pages", 1):
                    break

            all_voices.sort(key=lambda v: v["name"])
            self._send_json({"voices": all_voices})
        except Exception as e:
            # Return a minimal hardcoded fallback so the UI stays usable.
            self._send_json({"voices": [
                {"slug": "en_paul_neutral", "name": "Paul - Neutral", "gender": "male", "languages": ["en_us"], "tags": []},
                {"slug": "en_paul_cheerful", "name": "Paul - Cheerful", "gender": "male", "languages": ["en_us"], "tags": []},
                {"slug": "gb_oliver_neutral", "name": "Oliver - Neutral", "gender": "male", "languages": ["en_gb"], "tags": []},
                {"slug": "gb_jane_sarcasm", "name": "Jane - Sarcasm", "gender": "female", "languages": ["en_gb"], "tags": []},
            ], "error": str(e)})

    def _tts_provider(self):
        """Resolve (base_url, api_key) for the configured TTS model, or None."""
        import brain
        cfg = brain.get_tool_config().get("text_to_speech", {}) or {}
        model_id = (cfg.get("default_model") or "").strip()
        if not model_id:
            return None
        prov = brain.resolve_provider_for_model(model_id)
        base_url = (prov.get("base_url") or "").rstrip("/")
        api_key = prov.get("api_key") or ""
        if not base_url or not api_key:
            return None
        return base_url, api_key

    def _handle_tts_voice_create(self):
        """POST /v1/translate/tts/voices — clone a custom voice.
        Body: {name, sample_audio_b64, sample_filename?, languages?: [iso], gender?,
        age?, tags?}. Proxies to Mistral POST /v1/audio/voices. Lets the user
        register native-accent voices (e.g. German) so audio auto-uses them."""
        import urllib.request
        body = self._read_json() or {}
        name = (body.get("name") or "").strip()
        sample = (body.get("sample_audio_b64") or body.get("sample_audio") or "").strip()
        if not name or not sample:
            self._send_json({"error": "name and sample_audio_b64 are required"}, 400)
            return
        prov = self._tts_provider()
        if not prov:
            self._send_json({"error": "no TTS provider configured"}, 503)
            return
        base_url, api_key = prov
        payload = {"name": name, "sample_audio": sample,
                   "sample_filename": body.get("sample_filename") or "sample.mp3"}
        for k in ("languages", "gender", "age", "tags"):
            if body.get(k):
                payload[k] = body[k]
        try:
            req = urllib.request.Request(
                f"{base_url}/audio/voices", data=json.dumps(payload).encode(), method="POST",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            # Bust the audio_overview voice-roster cache so the new voice is
            # usable immediately (it auto-picks by language on the next render).
            try:
                from engine import audio_overview as _ao
                _ao._voices_cache["voices"] = None
            except Exception:
                pass
            self._send_json({"ok": True, "voice": data})
        except urllib.error.HTTPError as e:
            self._send_json({"error": f"voice create failed {e.code}: {e.read().decode()[:300]}"}, 502)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_tts_voice_delete(self, path):
        """DELETE /v1/translate/tts/voices/<voice_id> — remove a cloned voice."""
        import urllib.request
        voice_id = path.rstrip("/").split("/")[-1]
        if not voice_id:
            self._send_json({"error": "voice_id required"}, 400)
            return
        prov = self._tts_provider()
        if not prov:
            self._send_json({"error": "no TTS provider configured"}, 503)
            return
        base_url, api_key = prov
        try:
            req = urllib.request.Request(
                f"{base_url}/audio/voices/{voice_id}", method="DELETE",
                headers={"Authorization": f"Bearer {api_key}"})
            urllib.request.urlopen(req, timeout=20)
            try:
                from engine import audio_overview as _ao
                _ao._voices_cache["voices"] = None
            except Exception:
                pass
            self._send_json({"ok": True})
        except urllib.error.HTTPError as e:
            self._send_json({"error": f"voice delete failed {e.code}: {e.read().decode()[:200]}"}, 502)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_translate_tts(self):
        """POST /v1/translate/tts — {text, lang, [model, voice]} → audio/mpeg bytes.

        Calls Mistral's OpenAI-compatible /audio/speech endpoint using the
        voxtral-mini-tts model. Returns raw audio bytes with Content-Type
        audio/mpeg so the browser can play them directly via an Audio object.
        """
        body = self._read_json()
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json({"error": "text is required"}, 400)
            return

        try:
            import brain
            import urllib.request

            cfg = brain.get_tool_config().get("text_to_speech", {}) or {}
            # Honor the explicitly-requested model first, then the configured
            # default. No hardcoded fallback — if neither is set, raise.
            model_id = (body.get("model") or "").strip() or (cfg.get("default_model") or "").strip()
            if not model_id:
                self._send_json({"error": "no TTS model configured — set text_to_speech.default_model in the Tools tab"}, 503)
                return
            voice = (body.get("voice") or "").strip()
            # Language-matched voice selection when the caller doesn't pin a voice.
            # Prefer an EXPLICIT `lang` (the Translation tab knows the source/target
            # language exactly — a better signal than re-detection); otherwise, if
            # `auto_voice` is set, detect the language from the text. Either way we
            # pick a voice tagged for that language, falling back to English when
            # none exists (e.g. no German voice cloned yet).
            _req_lang = (body.get("lang") or "").strip().lower()[:2]
            if not voice and (_req_lang or body.get("auto_voice")):
                try:
                    from engine import audio_overview as _ao
                    _lang = _req_lang or _ao._detect_corpus_lang(text)
                    _va, _ = _ao._voices_for_lang(_lang)
                    voice = _va
                except Exception:
                    voice = ""
            voice = voice or cfg.get("voice") or "nova"

            # Resolve provider for the TTS model. resolve_provider_for_model
            # uses _models_config[model_id] exactly — no fuzzy match. If the
            # configured id isn't in the models registry, base_url/api_key
            # come back empty and we surface that below.
            prov = brain.resolve_provider_for_model(model_id)
            base_url = (prov.get("base_url") or "").rstrip("/")
            api_key = prov.get("api_key") or ""
            if not base_url or not api_key:
                self._send_json({"error": f"TTS provider for '{model_id}' not configured — verify the model exists in the Models tab and its provider has a base_url + api_key"}, 503)
                return

            # Wire model id: use the registry's base_model_id (set per-model
            # in the Models tab) rather than blind-splitting on '/'. This is
            # the same helper chat / transcribe use, so a saved bare id like
            # 'voxtral-mini-tts-latest' goes through unchanged.
            api_model_id = brain.get_api_model_id(model_id)

            payload = json.dumps({
                "model": api_model_id,
                "input": text,
                "voice": voice,
            }).encode("utf-8")

            url = f"{base_url}/audio/speech"
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    resp_ct = resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    err_body = ""
                self._send_json({"error": f"TTS API error {e.code}: {err_body}"}, 502)
                return

            # Mistral voxtral-tts returns JSON {"audio_data": "<base64 mp3>"}.
            # Decode to raw bytes before sending to the browser.
            import base64 as _b64
            if resp_ct.startswith("application/json") or raw[:1] == b"{":
                try:
                    jdata = json.loads(raw)
                    audio_bytes = _b64.b64decode(jdata["audio_data"])
                except Exception as dec_err:
                    self._send_json({"error": f"TTS decode error: {dec_err}"}, 502)
                    return
            else:
                audio_bytes = raw

            # Cost-account the read-aloud render (char-billed) under the
            # "Read aloud" use-case. Best-effort — reuses the audio_overview
            # helper so the rate lookup matches the podcast path exactly.
            try:
                from engine import audio_overview as _ao
                _rc = brain.get_request_context()
                _ao._log_tts_cost(
                    len(text), session_id=(_rc.current_session_id or ""),
                    user_id=(_rc.current_user_id or ""), agent_id="main",
                    purpose="read_aloud")
            except Exception:
                pass

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio_bytes)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(audio_bytes)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

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
            # Persist to translation history (fire-and-forget).
            if not result.get("noop") and result.get("translation"):
                try:
                    user_id = ((getattr(self, "_auth_user", None) or {}).get("id") or "")
                    _tr_history_save_text(
                        user_id=user_id,
                        text=text,
                        result=result,
                        source_lang=(body.get("source_lang") or "").strip().lower(),
                        target_lang=target_lang,
                    )
                except Exception:
                    pass
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ─── Translation History ─────────────────────────────────────────────────

    def _handle_translate_history_list(self):
        """GET /v1/translate/history — entries for current user (admins see all).

        Returns {entries: [...], current_user_id, is_admin}. Each entry carries
        user_id so the UI can label other users' rows when admin is viewing.
        """
        try:
            user = getattr(self, "_auth_user", None) or {}
            user_id = user.get("id") or ""
            is_admin = user.get("role") == "admin"
            from server_lib.db import TranslateHistoryDB
            entries = TranslateHistoryDB.list_all() if is_admin else TranslateHistoryDB.list_for_user(user_id)
            self._send_json({
                "entries": entries,
                "current_user_id": user_id,
                "is_admin": is_admin,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_translate_history_delete(self, entry_id: str):
        """DELETE /v1/translate/history/<id>.

        Admins can delete any entry; other users only their own.
        """
        try:
            user = getattr(self, "_auth_user", None) or {}
            user_id = user.get("id") or ""
            is_admin = user.get("role") == "admin"
            from server_lib.db import TranslateHistoryDB
            TranslateHistoryDB.delete(entry_id, user_id, admin=is_admin)
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_translate_history_file(self, entry_id: str):
        """GET /v1/translate/history/<id>/file?which=source|output|<format>

        Serves a file (source upload, primary output, or for media a specific
        format like srt/vtt/translation_txt) tied to a history entry. Admins
        can fetch any entry's file; users only their own.
        """
        from urllib.parse import urlparse, parse_qs
        try:
            user = getattr(self, "_auth_user", None) or {}
            user_id = user.get("id") or ""
            is_admin = user.get("role") == "admin"
            from server_lib.db import TranslateHistoryDB
            entry = TranslateHistoryDB.get(entry_id, user_id, admin=is_admin)
            if not entry:
                self._send_json({"error": "not found"}, 404)
                return
            qs = parse_qs(urlparse(self.path).query)
            which = (qs.get("which") or ["output"])[0]
            try:
                result = json.loads(entry.get("result_json") or "{}")
            except Exception:
                result = {}
            target_path = ""
            if which == "source":
                target_path = result.get("source_artifact_path") or ""
            elif which == "output":
                target_path = entry.get("artifact_path") or ""
            else:
                # media: format-specific output
                files_map = result.get("output_files_paths") or {}
                target_path = files_map.get(which) or ""
            if not target_path or not os.path.exists(target_path):
                self._send_json({"error": "file unavailable"}, 404)
                return
            self._serve_file(target_path)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _serve_file(self, path: str) -> None:
        """Stream a file with a sensible Content-Type and inline disposition."""
        import mimetypes
        ctype, _ = mimetypes.guess_type(path)
        if not ctype:
            ctype = "application/octet-stream"
        try:
            size = os.path.getsize(path)
        except OSError:
            self._send_json({"error": "file not readable"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        # Inline so browsers display PDFs/images instead of forcing download;
        # downloads still work via the download button.
        fname = os.path.basename(path).replace('"', '')
        self.send_header("Content-Disposition", f'inline; filename="{fname}"')
        self.end_headers()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

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
        tone = (fields.get("tone") or "").strip()
        agent_id = (fields.get("agent_id") or "main").strip() or "main"
        session_id = (fields.get("session_id") or "").strip()
        try:
            user_id = ((getattr(self, "_auth_user", None) or {}).get("id") or "")
        except Exception:
            user_id = ""

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
            tone=tone,
            model=model,
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
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
        # SSE close-after-terminal rule (9.277.0, same as chat SSE): without
        # an explicit close the client never sees end-of-response (SSE has no
        # content framing) and each stream leaks a server thread + socket.
        self.close_connection = True
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
        """GET /v1/translate/jobs/<id>/result[?format=...] — download output.

        For document jobs the path is fixed (job.output_path).
        For media jobs an optional `format` query param picks one of the
        emitted output files (transcript_txt / transcript_srt / transcript_vtt /
        translation_txt / translation_srt / translation_vtt / bilingual_txt).
        Defaults to `primary`.
        """
        from server_lib.translate import JOB_REGISTRY
        job = JOB_REGISTRY.get(job_id)
        if not job:
            self._send_json({"error": "job not found"}, 404)
            return
        if job.state != "done":
            self._send_json({"error": f"job not ready (state={job.state})"}, 409)
            return

        # Resolve the file to serve.
        target_path = job.output_path
        if job.kind == "media" and job.output_files:
            # Pull `format` out of the query string. self.path includes ?…
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                fmt = (qs.get("format") or [""])[0]
            except Exception:
                fmt = ""
            if fmt:
                target_path = job.output_files.get(fmt) or ""
                if not target_path:
                    self._send_json({"error": f"unknown format '{fmt}'"}, 400)
                    return

        if not target_path or not os.path.isfile(target_path):
            self._send_json({"error": "output file missing"}, 410)
            return
        try:
            with open(target_path, "rb") as f:
                data = f.read()
        except OSError as e:
            self._send_json({"error": f"read failed: {e}"}, 500)
            return
        fname = os.path.basename(target_path)
        self.send_response(200)
        # Octet-stream so the browser always offers a Save dialog regardless
        # of the actual MIME.
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

    # ─── Media translation (audio/video) ───────────────────────────────────

    def _handle_translate_media_upload(self):
        """POST /v1/translate/media — multipart upload of audio/video file.

        Form fields:
          - file (required, audio/video, ≤200MB)
          - target_lang (required ISO 639-1 — empty string allowed = transcribe-only)
          - source_lang (optional — passed to Voxtral as language hint)
          - glossary (optional slug)
          - model (optional translation model id)
          - transcribe_model (optional — voxtral-* / whisper-* id)
          - agent_id (optional, defaults to 'main')

        Returns: {job_id, ...job_dict}.
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
        if length <= 0 or length > MAX_MEDIA_BYTES + 32 * 1024:
            self._send_json({"error": "payload too large"}, 413)
            return

        raw = self.rfile.read(length)
        fields, file_name, file_bytes = _parse_multipart(raw, boundary)
        if not file_name or not file_bytes:
            self._send_json({"error": "missing file"}, 400)
            return
        ext = os.path.splitext(file_name)[1].lower()
        from server_lib.translate import MEDIA_EXTS
        if ext not in MEDIA_EXTS:
            self._send_json({
                "error": f"unsupported extension '{ext}' — supported: {sorted(MEDIA_EXTS)}",
            }, 400)
            return
        if len(file_bytes) > MAX_MEDIA_BYTES:
            self._send_json({"error": "file too large"}, 413)
            return

        target_lang = (fields.get("target_lang") or "").strip().lower()
        # Empty target_lang is allowed for transcribe-only mode — UI uses this.
        source_lang = (fields.get("source_lang") or "").strip().lower()
        glossary_slug = (fields.get("glossary") or "").strip()
        model = (fields.get("model") or "").strip()
        transcribe_model = (fields.get("transcribe_model") or "").strip()
        agent_id = (fields.get("agent_id") or "main").strip() or "main"
        try:
            media_user_id = ((getattr(self, "_auth_user", None) or {}).get("id") or "")
        except Exception:
            media_user_id = ""

        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="brain-translate-media-")
        src_path = os.path.join(tmpdir, file_name)
        try:
            with open(src_path, "wb") as f:
                f.write(file_bytes)
        except OSError as e:
            self._send_json({"error": f"save failed: {e}"}, 500)
            return

        # Same `tr<14 hex>` synthesis as document jobs — keeps artifact-folder
        # name unique across concurrent jobs.
        import uuid
        session_id = f"tr{uuid.uuid4().hex[:14]}"

        from server_lib.translate import JOB_REGISTRY
        job = JOB_REGISTRY.create(
            kind="media",
            filename=file_name,
            src_path=src_path,
            target_lang=target_lang,
            source_lang=source_lang,
            glossary=glossary_slug,
            model=model,
            transcribe_model=transcribe_model,
            agent_id=agent_id,
            session_id=session_id,
            user_id=media_user_id,
        )

        t = threading.Thread(
            target=_run_media_job,
            args=(job.id,),
            name=f"translate-media-{job.id}",
            daemon=True,
        )
        t.start()
        self._send_json({"job_id": job.id, **job.to_dict()})

    # ─── Live microphone translation ───────────────────────────────────

    def _handle_live_start(self):
        """POST /v1/translate/live/start — open a live transcription session.

        Body: {target_lang, source_lang?, glossary?, model?}
        Returns: {id, target_lang, source_lang, glossary, model}.
        """
        body = self._read_json()
        from server_lib.translate import LIVE_REGISTRY
        target_lang = (body.get("target_lang") or "").strip().lower()
        source_lang = (body.get("source_lang") or "").strip().lower()
        glossary = (body.get("glossary") or "").strip()
        model = (body.get("model") or "").strip()
        # Best-effort current user / agent — used for cleanup tracking.
        agent_id = (body.get("agent_id") or "main").strip() or "main"
        try:
            user_id = ((getattr(self, "_auth_user", None) or {}).get("id") or "")
        except Exception:
            user_id = ""
        sess = LIVE_REGISTRY.create(
            target_lang=target_lang,
            source_lang=source_lang,
            glossary=glossary,
            model=model,
            agent_id=agent_id,
            user_id=user_id,
        )
        self._send_json({
            "id": sess.id,
            "target_lang": sess.target_lang,
            "source_lang": sess.source_lang,
            "glossary": sess.glossary,
            "model": sess.model,
        })

    def _handle_live_chunk(self, sess_id: str):
        """POST /v1/translate/live/<id>/chunk — multipart audio fragment."""
        from server_lib.translate import LIVE_REGISTRY
        sess = LIVE_REGISTRY.get(sess_id)
        if not sess:
            self._send_json({"error": "session not found"}, 404)
            return
        if sess.closed:
            self._send_json({"error": "session closed"}, 409)
            return

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
        # Per-chunk cap: 25MB. 4s of 16kHz mono opus is ~30KB, so this is huge
        # headroom — guards against accidental large uploads or video tracks.
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 25 * 1024 * 1024:
            self._send_json({"error": "chunk too large"}, 413)
            return
        raw = self.rfile.read(length)
        fields, _file_name, file_bytes = _parse_multipart(raw, boundary)
        if not file_bytes:
            self._send_json({"error": "missing chunk"}, 400)
            return
        try:
            seq = int(fields.get("seq") or "0")
        except ValueError:
            seq = 0
        mime = (fields.get("mime") or "audio/webm").strip()
        sess.add_chunk(seq, file_bytes, mime)
        self._send_json({"ok": True, "seq": seq, "bytes": len(file_bytes)})

    def _handle_live_stop(self, sess_id: str):
        """POST /v1/translate/live/<id>/stop — flush + close."""
        from server_lib.translate import LIVE_REGISTRY
        sess = LIVE_REGISTRY.get(sess_id)
        if not sess:
            self._send_json({"error": "session not found"}, 404)
            return
        sess.stop()
        # Persist to translation history.
        try:
            user_id = ((getattr(self, "_auth_user", None) or {}).get("id") or "")
            _tr_history_save_live(user_id=user_id, sess=sess)
        except Exception:
            pass
        self._send_json({"ok": True})

    def _handle_live_stream(self, sess_id: str):
        """GET /v1/translate/live/<id> — SSE stream of segment + translation events."""
        from server_lib.translate import LIVE_REGISTRY
        sess = LIVE_REGISTRY.get(sess_id)
        if not sess:
            self._send_json({"error": "session not found"}, 404)
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
        # SSE close-after-terminal rule (9.277.0, same as chat SSE): without
        # an explicit close the client never sees end-of-response (SSE has no
        # content framing) and each stream leaks a server thread + socket.
        self.close_connection = True
        try:
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

        sub = sess.subscribe()
        try:
            while True:
                try:
                    ev_type, payload = sub.get(timeout=5.0)
                except _queue.Empty:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    continue
                try:
                    self.wfile.write(
                        f"event: {ev_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                if ev_type == "closed":
                    return
        finally:
            sess.unsubscribe(sub)


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


def _copy_source_to_artifacts(src_path: str, out_dir: str) -> str:
    """Copy an uploaded source file into the artifact folder under `source/`.

    Returns the destination path on success, '' on failure (history can still
    function without a source link). Filenames are kept verbatim — duplicates
    in the same folder get an `(N)` suffix to avoid clobber.
    """
    try:
        if not src_path or not os.path.exists(src_path):
            print(f"[translate] source-copy: src missing src={src_path!r}", flush=True)
            return ""
        import shutil
        dest_dir = os.path.join(out_dir, "source")
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(src_path)
        dest = os.path.join(dest_dir, base)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(base)
            i = 1
            while True:
                candidate = os.path.join(dest_dir, f"{stem} ({i}){ext}")
                if not os.path.exists(candidate):
                    dest = candidate
                    break
                i += 1
        shutil.copy2(src_path, dest)
        return dest
    except Exception as e:
        # Surface the failure so we don't silently produce history rows
        # whose source-link will 404. History still works without the copy.
        print(f"[translate] source-copy failed src={src_path!r} dst={out_dir!r}: {e}", flush=True)
        return ""


def _prime_artifact_threadlocals(brain_mod, session_id: str) -> None:
    """Make _after_file_write believe it's running inside a chat session.

    The translate worker runs in a daemon thread that never went through the
    chat dispatch, so the request context has neither current_session_id nor
    event_callback. Both are gates inside _after_file_write — without priming,
    the artifact would never be registered in the DB. The event callback is a
    no-op: nobody is listening on this synthetic session's SSE stream.
    """
    try:
        brain_mod.get_request_context().current_session_id = session_id
        if not brain_mod.get_request_context().event_callback:
            brain_mod.get_request_context().event_callback = lambda *_args, **_kw: None
    except Exception:
        pass


def _run_translate_job(job_id: str) -> None:
    """Background worker — runs translate_document_file and updates the job.

    Lives at module level (not a TranslateHandlerMixin method) so the daemon
    thread keeps no implicit reference to a handler instance.
    """
    from server_lib.translate import JOB_REGISTRY, translate_document_file
    job = JOB_REGISTRY.get(job_id)
    if not job:
        return
    import brain  # late — picks up AGENTS_DIR + helpers
    with brain.request_context():
        try:
            # Resolve output dir to the agent's artifact folder for this synthetic
            # session so the translated file shows up under Artifacts.
            import brain  # late — picks up AGENTS_DIR + helpers
            from datetime import datetime as _dt
            folder = f"{_dt.now().strftime('%Y-%m-%d')}_{job.session_id[:8]}"
            out_dir = os.path.join(brain.AGENTS_DIR, job.agent_id, "artifacts", folder)
            os.makedirs(out_dir, exist_ok=True)

            # Copy the source into the artifact folder so history can re-open it.
            # Original lives in /tmp/brain-translate-* and gets cleaned on reboot;
            # the artifact copy is the durable handle for the History UI.
            source_artifact_path = _copy_source_to_artifacts(job.src_path, out_dir)

            result = translate_document_file(
                job.src_path,
                target_lang=job.target_lang,
                source_lang=job.source_lang,
                glossary_slug=job.glossary,
                model=job.model,
                tone=job.tone,
                output_dir=out_dir,
                progress=job.update_progress,
            )

            # Promote as artifact so the artifact panel (and miner) sees it.
            # _after_file_write reads thread-locals: current_session_id is required
            # by _register_artifact_version, and event_callback gates the whole
            # artifact registration block. Worker thread starts with neither set —
            # without this priming the file lands on disk but never gets a DB row.
            _prime_artifact_threadlocals(brain, job.session_id)
            if source_artifact_path:
                try:
                    brain._after_file_write(source_artifact_path, "created", job.agent_id)
                except Exception:
                    pass
            try:
                brain._after_file_write(result["output_path"], "created", job.agent_id)
            except Exception:
                pass

            job.source_artifact_path = source_artifact_path
            job.finish(
                output_path=result["output_path"],
                runs=result.get("runs", 0),
                fallback=result.get("fallback", False),
                detected=result.get("detected"),
                noop=result.get("noop", False),
                model=result.get("model", ""),
            )
            # Persist to translation history.
            try:
                _tr_history_save_job(job)
            except Exception:
                pass
        except Exception as e:
            job.fail(str(e))



def _run_media_job(job_id: str) -> None:
    """Background worker for audio/video translation jobs."""
    from server_lib.translate import (
        JOB_REGISTRY, transcribe_and_translate, write_media_output_files,
    )
    job = JOB_REGISTRY.get(job_id)
    if not job:
        return
    import brain
    with brain.request_context():
        try:
            import brain
            from datetime import datetime as _dt
            folder = f"{_dt.now().strftime('%Y-%m-%d')}_{job.session_id[:8]}"
            out_dir = os.path.join(brain.AGENTS_DIR, job.agent_id, "artifacts", folder)
            os.makedirs(out_dir, exist_ok=True)

            # Persist the source media into the artifact folder so history can
            # re-play it later — same rationale as the document worker.
            source_artifact_path = _copy_source_to_artifacts(job.src_path, out_dir)
            job.source_artifact_path = source_artifact_path

            def _progress(stage: str, done: int, total: int) -> None:
                job.update_stage(stage, done, total)

            result = transcribe_and_translate(
                job.src_path,
                target_lang=job.target_lang,
                source_lang=job.source_lang,
                glossary_slug=job.glossary,
                model=job.model,
                transcribe_model=job.transcribe_model,
                progress=_progress,
            )

            base_name = os.path.splitext(os.path.basename(job.filename))[0] or "media"
            files = write_media_output_files(result, out_dir=out_dir, base_name=base_name)

            # Promote each emitted file as artifact (the bilingual TXT is the
            # "primary"; SRT/VTT live alongside).
            _prime_artifact_threadlocals(brain, job.session_id)
            if source_artifact_path:
                try:
                    brain._after_file_write(source_artifact_path, "created", job.agent_id)
                except Exception:
                    pass
            for path in {v for k, v in files.items() if k != "primary"}:
                try:
                    brain._after_file_write(path, "created", job.agent_id)
                except Exception:
                    pass

            job.finish_media(
                transcript=result.get("transcript", ""),
                segments=result.get("segments") or [],
                duration_s=result.get("duration_s", 0.0),
                output_files=files,
                primary_path=files.get("primary", ""),
                transcribe_model=result.get("transcribe_model", ""),
                translation_model=job.model,
            )
            # Persist to translation history.
            try:
                _tr_history_save_job(job)
            except Exception:
                pass
        except Exception as e:
            job.fail(str(e))


# ─── Translation history helpers ────────────────────────────────────────────

def _tr_history_save_text(*, user_id: str, text: str, result: dict,
                          source_lang: str, target_lang: str) -> None:
    import uuid as _uuid
    from server_lib.db import TranslateHistoryDB
    title = text[:80].replace("\n", " ")
    detected = (result.get("detected") or {})
    actual_src = detected.get("language") or source_lang or "auto"
    TranslateHistoryDB.add(
        entry_id=_uuid.uuid4().hex,
        user_id=user_id,
        type="text",
        title=title,
        source_lang=actual_src,
        target_lang=target_lang,
        result_json=json.dumps({
            "text": text,
            "translation": result.get("translation", ""),
            "detected": detected,
            "noop": result.get("noop", False),
        }, ensure_ascii=False),
    )


def _tr_history_save_job(job) -> None:
    import uuid as _uuid
    from server_lib.db import TranslateHistoryDB
    title = os.path.basename(job.filename) if job.filename else f"Job {job.id}"
    src_artifact = getattr(job, "source_artifact_path", "") or ""
    data: dict = {
        "job_id": job.id,
        "filename": job.filename,
        # `source_artifact_path` is the durable copy under agents/<id>/artifacts.
        # Empty when the source copy failed or wasn't kept (history still works,
        # source link is just hidden).
        "source_artifact_path": src_artifact,
        "source_filename": os.path.basename(src_artifact) if src_artifact else "",
    }
    artifact_path = ""
    if job.kind == "document":
        data["output_filename"] = os.path.basename(job.output_path) if job.output_path else ""
        artifact_path = job.output_path or ""
    elif job.kind == "media":
        data["segments"] = job.segments or []
        data["transcript"] = job.transcript or ""
        data["duration_s"] = job.duration_s
        data["output_files"] = {k: os.path.basename(v) for k, v in (job.output_files or {}).items()}
        # Full paths for the file-serving endpoint (basename map above is for
        # display; the resolver needs the absolute path).
        data["output_files_paths"] = dict(job.output_files or {})
        artifact_path = job.output_path or ""
    TranslateHistoryDB.add(
        entry_id=_uuid.uuid4().hex,
        user_id=getattr(job, "user_id", "") or "",
        type=job.kind,
        title=title,
        source_lang=job.source_lang or "",
        target_lang=job.target_lang or "",
        result_json=json.dumps(data, ensure_ascii=False),
        artifact_path=artifact_path,
    )


def _tr_history_save_live(*, user_id: str, sess) -> None:
    import uuid as _uuid
    from server_lib.db import TranslateHistoryDB
    segs_raw = list(sess.segments) if hasattr(sess, "segments") else []
    if not segs_raw:
        return
    import brain
    with brain.request_context():
        from datetime import datetime as _dt
        title = f"Live mic — {_dt.now().strftime('%H:%M')}"
        seg_dicts = [
            {
                "start": s.start if hasattr(s, "start") else s.get("start", 0),
                "end": s.end if hasattr(s, "end") else s.get("end", 0),
                "text": s.text if hasattr(s, "text") else s.get("text", ""),
                "translation": s.translation if hasattr(s, "translation") else s.get("translation", ""),
            }
            for s in segs_raw
        ]

        # Write SRT + TXT into the agent's artifact folder so the live recording
        # can be downloaded later from history (matches the doc/media behaviour).
        output_files: dict = {}
        artifact_path = ""
        try:
            import brain
            from server_lib.translate import to_srt, to_vtt
            agent_id = getattr(sess, "agent_id", "") or "main"
            synth_session = f"tr{_uuid.uuid4().hex[:14]}"
            folder = f"{_dt.now().strftime('%Y-%m-%d')}_{synth_session[:8]}"
            out_dir = os.path.join(brain.AGENTS_DIR, agent_id, "artifacts", folder)
            os.makedirs(out_dir, exist_ok=True)
            base = f"live-mic-{_dt.now().strftime('%Y%m%d-%H%M%S')}"
            text_key = "translation" if any(s.get("translation") for s in seg_dicts) else "text"
            srt_path = os.path.join(out_dir, f"{base}.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(to_srt(seg_dicts, text_key))
            output_files["srt"] = srt_path
            txt_path = os.path.join(out_dir, f"{base}.txt")
            lines = []
            for s in seg_dicts:
                line = s.get("translation") or s.get("text") or ""
                if line:
                    lines.append(line)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            output_files["txt"] = txt_path
            artifact_path = txt_path
            _prime_artifact_threadlocals(brain, synth_session)
            for p in output_files.values():
                try:
                    brain._after_file_write(p, "created", agent_id)
                except Exception:
                    pass
        except Exception:
            # Failing to write the artifact files shouldn't drop the history row.
            pass

        TranslateHistoryDB.add(
            entry_id=_uuid.uuid4().hex,
            user_id=user_id,
            type="live",
            title=title,
            source_lang=getattr(sess, "source_lang", "") or "",
            target_lang=getattr(sess, "target_lang", "") or "",
            result_json=json.dumps({
                "segments": seg_dicts,
                "output_files": {k: os.path.basename(v) for k, v in output_files.items()},
                "output_files_paths": output_files,
            }, ensure_ascii=False),
            artifact_path=artifact_path,
        )
