# Extracted from server.py — chat/inference handlers
import base64
import json
import mimetypes
import os
import queue
import socket
import threading
import time

import brain as engine


class ChatHandlerMixin:

    # Cache: model -> provider config (refreshed when providers change)
    # Provider cache now in engine (resolve_provider_for_model)

    # Tools whose results contain clickable file-path references (MemPalace drawers/triples).
    _PROJECT_REF_TOOLS = frozenset({
        "mempalace_query", "mempalace_get_drawer", "mempalace_list_drawers",
        "mempalace_kg_query", "mempalace_kg_search", "mempalace_kg_neighbors",
    })
    # Tools whose results contain URL references (web searches/fetches).
    _WEB_REF_TOOLS = frozenset({"exa_search", "web_fetch"})

    @staticmethod
    def _resolve_original_path(sf: str) -> str:
        """Resolve a MemPalace source_file to the original binary path.
        .brain-extracted/foo.pdf.md  →  <parent>/foo.pdf
        foo.pdf.md (bare companion)  →  foo.pdf
        Anything else               →  unchanged
        """
        import re
        m = re.match(r'^(.+)/\.brain-extracted/(.+)\.md$', sf)
        if m:
            return f"{m[1]}/{m[2]}"
        m2 = re.match(r'^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$', sf, re.IGNORECASE)
        if m2:
            return m2[1]
        return sf

    @classmethod
    def _extract_references(cls, tool_name: str, result_str: str) -> list:
        """Extract normalized reference dicts from a tool result string.
        Returns [] for tools that don't produce references.
        Each ref: {title, link, snippet, domain, favicon, source_file?}
        This is the single source of truth — client reads persisted refs
        from metadata.tools[i].references instead of re-parsing results.
        """
        if not result_str:
            return []
        refs = []

        if tool_name in cls._PROJECT_REF_TOOLS:
            # MemPalace tools return JSON with drawers/triples/edges each
            # carrying a source_file. Resolve to original binary path.
            seen = set()
            try:
                data = json.loads(result_str)
                items = (list(data.get("drawers") or []) +
                         list(data.get("results") or []) +
                         list(data.get("triples") or []) +
                         list(data.get("edges") or []))
            except Exception:
                items = []

            for it in items:
                if not isinstance(it, dict):
                    continue
                sf = it.get("source_file") or ""
                if not sf or sf in seen:
                    continue
                room = it.get("room") or ""
                if room in ("chat", "chat_summary", "chat_attachment"):
                    continue
                seen.add(sf)
                original = cls._resolve_original_path(sf)
                basename = original.rsplit("/", 1)[-1] or original
                snippet = ""
                if it.get("snippet"):
                    snippet = str(it["snippet"])[:280]
                elif it.get("text"):
                    snippet = str(it["text"])[:280]
                elif it.get("subject") and it.get("predicate") and it.get("object"):
                    snippet = f"({it['subject']}) — [{it['predicate']}] → ({it['object']})"[:280]
                refs.append({
                    "title": basename,
                    "link": original,
                    "snippet": snippet,
                    "domain": "project",
                    "favicon": "",
                    "source_file": sf,
                })

            # Regex sweep for any source_file tokens the JSON parse may have missed
            # (truncated result strings, nested structures, etc.)
            import re as _re
            for m in _re.finditer(r'"source_file"\s*:\s*"([^"]+)"', result_str):
                sf = m.group(1)
                if not sf or sf in seen:
                    continue
                if _re.match(r'^\d+$', sf):
                    continue  # bare turn-id, not a document
                if _re.match(r'^[a-f0-9]+#summary$', sf, _re.IGNORECASE):
                    continue
                seen.add(sf)
                original = cls._resolve_original_path(sf)
                basename = original.rsplit("/", 1)[-1] or original
                refs.append({
                    "title": basename,
                    "link": original,
                    "snippet": "",
                    "domain": "project",
                    "favicon": "",
                    "source_file": sf,
                })
            return refs

        if tool_name in cls._WEB_REF_TOOLS:
            # Worker envelope: pre-extracted references array takes priority
            try:
                data = json.loads(result_str)
                if data.get("worker") and isinstance(data.get("references"), list):
                    for r in data["references"]:
                        if r and r.get("link"):
                            dom = r.get("domain") or ""
                            refs.append({
                                "title": r.get("title") or dom or r["link"],
                                "link": r["link"],
                                "snippet": r.get("snippet") or "",
                                "domain": dom,
                                "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else "",
                            })
                    if refs:
                        return refs
            except Exception:
                pass

            # Direct JSON result
            try:
                data = json.loads(result_str)
                results = data.get("results") if isinstance(data, dict) else None
                if isinstance(results, list):
                    for r in results:
                        url = r.get("link") or r.get("url") or ""
                        if not url:
                            continue
                        dom = ""
                        try:
                            from urllib.parse import urlparse
                            dom = urlparse(url).hostname or ""
                            dom = dom.removeprefix("www.")
                        except Exception:
                            pass
                        refs.append({
                            "title": r.get("title") or dom or url,
                            "link": url,
                            "snippet": (r.get("snippet") or "")[:200],
                            "domain": dom,
                            "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else "",
                        })
                    return refs
                if isinstance(data, dict) and data.get("url"):
                    url = data["url"]
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(url).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    import re as _re
                    title = dom
                    tm = _re.search(r'<title[^>]*>([^<]+)</title>', data.get("content") or "", _re.IGNORECASE)
                    if tm:
                        title = tm.group(1).strip()
                    refs.append({"title": title, "link": url, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
                    return refs
            except Exception:
                pass

            # Regex fallback for truncated JSON
            import re as _re
            if tool_name == "exa_search":
                for m in _re.finditer(r'"title"\s*:\s*"([^"]*)"[^}]*?"link"\s*:\s*"([^"]*)"', result_str):
                    raw_title, link = m.group(1), m.group(2)
                    try:
                        title = json.loads(f'"{raw_title}"')
                    except Exception:
                        title = raw_title
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(link).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    refs.append({"title": title, "link": link, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
            elif tool_name == "web_fetch":
                m = _re.search(r'"url"\s*:\s*"([^"]*)"', result_str)
                if m:
                    url = m.group(1)
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(url).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    title = dom
                    tm = _re.search(r'<title[^>]*>([^<]+)</title>', result_str, _re.IGNORECASE)
                    if tm:
                        title = tm.group(1).strip()
                    refs.append({"title": title, "link": url, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
            return refs

        return []

    @staticmethod
    def _resolve_provider_static(model: str) -> dict:
        """Find the provider that has the given model. Returns {api_key, base_url, provider_name}.
        Thread-safe. Delegates to engine.resolve_provider_for_model()."""
        if engine._models_config:
            model = engine.resolve_model(model)
        return engine.resolve_provider_for_model(model)

    def _resolve_provider(self, model: str) -> dict:
        """Instance method wrapper for _resolve_provider_static."""
        return BrainAgentHandler._resolve_provider_static(model)

    def _handle_create_session(self):
        body = self._read_json()
        model = body.get("model", server_config["default_model"])
        agent_req = body.get("agent", "main")
        # ACL gate: caller must have access to both the agent and the model
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_req):
            self._send_json({"error": f"Access to agent '{agent_req}' not permitted"}, 403)
            return
        if not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        provider = self._resolve_provider(model)
        project_req = body.get("project", "")
        custom_status_req = body.get("status", "")
        note_req = body.get("note_context", "")

        # Warm session pool claim — only when the incoming request matches
        # the pooled shape exactly: agent=main, no project, no custom status,
        # no note context. Any of those change the system prompt / behavior
        # and would make a pre-primed KV prefix invalid.
        model_cfg_claim = engine._models_config.get(model, {})
        pooled = None
        if (model_cfg_claim.get("warmup")
                and agent_req == WarmSessionPool.POOL_AGENT
                and not project_req and not custom_status_req and not note_req):
            pooled = warm_pool.claim(model)
        if pooled is not None:
            session = pooled
            # Promote from warm_pool status to active (visible in sidebar)
            session.status = "active"
            ChatDB.save_session(
                session.id, session.agent_id, session.model,
                session.title, session.status,
                session.created_at, session.last_active,
                session.project or "",
            )
            # Immediately kick off a replacement build
            threading.Thread(
                target=lambda m=model: warm_pool.try_build(m),
                daemon=True, name=f"warm-pool-refill-{model[:16]}",
            ).start()
            print(f"[warm-pool] claimed {model} ({session.id[:8]})")
        else:
            session = sessions.create(
                agent_id=agent_req,
                model=model,
                api_key=provider["api_key"],
                base_url=provider["base_url"],
                max_context=body.get("max_context") or engine.get_model_max_context(model),
            )
        # Stamp user ownership (for MemPalace wing scoping)
        auth_user = getattr(self, '_auth_user', None)
        uid = ""
        if auth_user and auth_user.get("id"):
            if auth_user["id"] != "__system__":
                uid = auth_user["id"]
            else:
                # Auth disabled — resolve to the first real user (typically the sole admin)
                try:
                    users = _auth_mod.AuthDB.list_users()
                    if users:
                        uid = users[0]["id"]
                except Exception:
                    pass
        if uid:
            session.user_id = uid
            ChatDB.update_session_user(session.id, uid)
        # Default memory mode: per-user preference wins over the global
        # classifier config. Pref `memory_chats_default` is 0|1|2|null;
        # null means "fall through to classifier.default_mode" so an unset
        # pref doesn't accidentally disable a server-wide opt-in.
        mcfg = engine._load_mempalace_config()
        clf_cfg = (mcfg.get("chat_sync", {}) or {}).get("classifier", {}) or {}
        default_mem = int(clf_cfg.get("default_mode", 0))
        try:
            actor = getattr(self, "_auth_user", None) or {}
            user_prefs = actor.get("preferences") or {}
            pref_chat = user_prefs.get("memory_chats_default")
            if pref_chat is not None:
                default_mem = int(pref_chat)
        except Exception:
            pass
        if default_mem:
            session.save_to_memory = default_mem
            ChatDB.update_session_save_to_memory(session.id, default_mem)
        project = body.get("project", "")
        if project:
            session.project = project
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, project)
        note_context = body.get("note_context", "")
        if note_context:
            session.note_context = note_context
        # Bind to a workflow_history row so the chat loop's round-0 preamble
        # can pull the run summary. Combined with status='workflow_run'
        # below, this hides the session from the sidebar until the user hits
        # "Save to chats" in the inline detail view.
        wf_run_id = body.get("workflow_run_id", "")
        if wf_run_id:
            session.workflow_run_id = wf_run_id
            ChatDB.update_session_workflow_run_id(session.id, wf_run_id)
        # Allow setting custom status (e.g., 'note_chat' to hide from chat lists)
        custom_status = body.get("status", "")
        if custom_status:
            session.status = custom_status
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "",
                               workflow_run_id=session.workflow_run_id)
        # Per-model warmup flag
        mcfg = engine.resolve_model_settings(model)
        warmup_enabled = bool(mcfg.get("warmup", False))

        # Claimed pool sessions are already warm — skip the "warmup" status
        # marker (that's for fresh sessions still prefilling) and skip the
        # redundant _trigger_warmup call.
        claimed = pooled is not None

        # Mark warmup sessions so they don't appear in sidebar until first message
        if warmup_enabled and not custom_status and not claimed:
            session.status = "warmup"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "max_context": session.max_context,
            "project": session.project or "",
            "warmup": warmup_enabled,
            "pre_warmed": claimed,
        })

        # Trigger warmup in background (skip if session was claimed from pool)
        if warmup_enabled and not claimed:
            _trigger_warmup(session)

    def _handle_switch_agent(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        agent_id = body.get("agent", "main")
        model = body.get("model")
        # ACL gate for agent + (optional) model change
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_id):
            self._send_json({"error": f"Access to agent '{agent_id}' not permitted"}, 403)
            return
        if model and not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        session.switch_agent(agent_id, model)
        warmup_enabled = False
        if model:
            provider = self._resolve_provider(model)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            mcfg = engine.resolve_model_settings(model)
            warmup_enabled = bool(mcfg.get("warmup", False))
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "warmup": warmup_enabled,
        })
        if warmup_enabled:
            _trigger_warmup(session)

    def _handle_cancel(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        session.cancel_token.cancel()
        self._send_json({"status": "cancelled"})

    def _handle_chat(self):
        """Handle chat request with SSE streaming."""
        body = self._read_json()
        sid = body.get("session_id", "")
        message = body.get("message", "")
        model_override = body.get("model")
        chat_mode = body.get("mode", "")
        project_name = body.get("project")  # Optional project scope
        thinking_level = body.get("thinking")  # none, low, medium, high
        # ACL: only owner/team-member/admin can post to the session
        if sid and self._session_access_check(sid) is None:
            return
        # ACL: model override must be permitted
        if model_override:
            user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
            if not _auth_mod.can_access_model(user, model_override):
                self._send_json({"error": f"Access to model '{model_override}' not permitted"}, 403)
                return
        session = sessions.get(sid)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not message:
            self._send_json({"error": "No message"}, 400)
            return

        # Custom command expansion
        if message.startswith("/"):
            agent = engine.AgentConfig(session.agent_id)
            custom_cmds = agent.load_commands()
            cmd_word = message.split()[0][1:]  # strip / and get first word
            for cmd in custom_cmds:
                if cmd.get("name", "").lower() == cmd_word.lower():
                    template = cmd.get("template", "")
                    # Replace {{input}} with rest of message
                    rest = message[len(cmd_word) + 1:].strip()
                    message = template.replace("{{input}}", rest)
                    break

        # If model changed, re-resolve provider
        if model_override and model_override != session.model:
            provider = self._resolve_provider(model_override)
            with session.lock:
                session.model = model_override
                session.api_key = provider["api_key"]
                session.base_url = provider["base_url"]

        # Auto model selection: if agent uses model="auto", re-resolve per message
        agent_cfg = session.agent.config
        if not model_override and agent_cfg.get("model") == "auto":
            auto_model, auto_purpose = engine.resolve_auto_model_for_task(agent_cfg, message)
            if auto_model and auto_model != session.model:
                provider = self._resolve_provider(auto_model)
                with session.lock:
                    session.model = auto_model
                    session.api_key = provider["api_key"]
                    session.base_url = provider["base_url"]
                    session.max_context = engine.get_model_max_context(auto_model)

        # Reset cancel token
        with session.lock:
            session.cancel_token = engine.CancelToken()
            session._streaming = True

        # --- Unified attachment routing: multimodal vs disk based on model capabilities ---
        import base64 as _b64
        import mimetypes as _mt

        def _guess_mime(filename: str) -> str:
            mt, _ = _mt.guess_type(filename)
            return mt or "application/octet-stream"

        # Collect all attachments from both legacy body.images and body.files
        all_attachments = []
        for img in body.get("images", []):
            all_attachments.append({
                "name": "image",
                "content": img.get("data", ""),
                "encoding": "base64",
                "media_type": img.get("media_type", "image/png"),
            })
        for f in body.get("files", []):
            all_attachments.append({
                "name": f.get("name", "file"),
                "content": f.get("content", "") or f.get("data", ""),
                "encoding": f.get("encoding", "base64"),
                "media_type": f.get("media_type") or f.get("type") or _guess_mime(f.get("name", "file")),
            })

        content_blocks = []
        disk_files = []
        MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20MB

        if all_attachments:
            raw_formats = engine.get_model_raw_formats(session.model)
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)

            for f in all_attachments:
                mime = f["media_type"]
                is_base64 = f["encoding"] == "base64"
                # Check file size (base64 is ~4/3 of raw)
                too_large = is_base64 and len(f["content"]) * 3 // 4 > MAX_INLINE_BYTES
                # OpenAI wire format only supports image/* as multimodal content blocks
                api_blocked = not mime.startswith("image/")

                if (engine._mime_matches(mime, raw_formats)
                        and is_base64 and not too_large and not api_blocked):
                    # Route as multimodal content block — LLM sees raw data as image_url data URI
                    data_uri = f"data:{mime};base64,{f['content']}"
                    content_blocks.append({"type": "image_url", "image_url": {"url": data_uri}})
                else:
                    # Route to disk — agent uses read_document/read_file
                    disk_files.append(f)

        # Build user_content with any multimodal blocks
        if content_blocks:
            content_blocks.append({"type": "text", "text": message})
            user_content = content_blocks
        else:
            user_content = message

        # Save disk-routed files and append notice
        if disk_files:
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)
            os.makedirs(attach_dir, exist_ok=True)
            saved_paths = []
            for f in disk_files:
                fname = f.get("name", "file")
                safe_name = fname.replace("/", "_").replace("\\", "_")
                fpath = os.path.join(attach_dir, safe_name)
                content = f.get("content", "")
                if f.get("encoding") == "base64":
                    with open(fpath, "wb") as fp:
                        fp.write(_b64.b64decode(content))
                else:
                    with open(fpath, "w", errors="replace") as fp:
                        fp.write(content)
                saved_paths.append(fpath)
            paths_list = "\n".join(f"  - {p}" for p in saved_paths)
            has_docs = any(os.path.splitext(p)[1].lower() in (".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv")
                           for p in saved_paths)
            if has_docs:
                notice = (f"\n\n[User attached files saved to disk. "
                          f"IMPORTANT: Use the read_document tool (NOT read_file) to read these — "
                          f"read_document handles PDF, DOCX, XLSX, PPTX and other document formats:]\n{paths_list}")
            else:
                notice = f"\n\n[User attached files saved to disk:]\n{paths_list}"
            message = message + notice
            if isinstance(user_content, str):
                user_content = user_content + notice
            else:
                for block in user_content:
                    if block.get("type") == "text":
                        block["text"] = block["text"] + notice
                        break

        # Promote warmup session to active on first message
        if session.status == "warmup":
            session.status = "active"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        # Add user message (persisted to DB)
        session.add_message("user", user_content)

        # SSE streaming setup (start early so we can send compaction events)
        # Disable Nagle's algorithm for real-time SSE delivery
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
        self.wfile.flush()  # Ensure headers are pushed before streaming

        # Wait for warmup if in progress (after SSE headers so client stays connected)
        if session._warmup_active:
            try:
                self.wfile.write(b"event: warmup\ndata: {\"status\":\"waiting\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            completed = session._warmup_done.wait(timeout=30)
            try:
                if completed and not session._warmup_cancel.is_set():
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                else:
                    # Warmup cancelled or timed out — proceed anyway but log it
                    reason = "cancelled" if session._warmup_cancel.is_set() else "timed out"
                    print(f"  [warmup] {session.model} {reason}, proceeding without cache ({session.id[:8]})")
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # Pre-processing: tool result budget + microcompact
        engine._thread_local.current_session_id = session.id
        if len(session.messages) > 4:
            engine._apply_tool_result_budget(session.messages, session_id=session.id,
                                              agent_id=session.agent_id)
            session.messages, _mc_freed = engine._microcompact(session.messages, keep_recent=5)

        # Check context and compact (with SSE progress)
        estimated = engine._estimate_conversation_tokens(session.messages)
        ctx_cfg = engine._context_manager.get_config() if engine._context_manager else {}
        threshold_pct = ctx_cfg.get("compact_threshold", 0.75) if ctx_cfg.get("enabled") else engine.COMPACT_THRESHOLD
        pre_compact_pct = 0
        if estimated >= int(session.max_context * threshold_pct):
            pre_compact_pct = int(estimated / session.max_context * 100)
            sse_line = f"event: compacting\ndata: {json.dumps({'pct': pre_compact_pct, 'tokens': estimated, 'max_tokens': session.max_context})}\n\n"
            try:
                self.wfile.write(sse_line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        session.messages, was_compacted = engine._check_and_compact(
            session.messages, session.model, session.api_key,
            session.base_url,
            max_tokens=session.max_context,
            session_id=session.id,
        )
        if was_compacted:
            new_est = engine._estimate_conversation_tokens(session.messages)
            new_pct = int(new_est / session.max_context * 100)
            sse_line = f"event: compacted\ndata: {json.dumps({'pct': new_pct, 'tokens': new_est, 'old_pct': pre_compact_pct})}\n\n"
            try:
                self.wfile.write(sse_line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        event_queue = queue.Queue()
        created_files = []
        _partial_reply = []  # accumulate text deltas for partial response recovery
        _partial_tools = []  # accumulate tool calls
        _partial_thinking = []  # accumulate thinking blocks
        _thinking_summary = {}  # opaque-reasoning summary (format + reasoning_tokens)
        _usage_totals = {"tokens_in": 0, "tokens_out": 0, "last_tokens_in": 0}  # cumulative across tool rounds; last_tokens_in = most recent round only
        _request_payloads = []  # capture request snapshots per tool round
        def event_callback(event_type, data):
            if event_type == "text_delta":
                _partial_reply.append(data.get("text", ""))
            elif event_type in ("file_created", "artifact_updated"):
                created_files.append(data)
            elif event_type == "thinking_delta":
                _partial_thinking.append(data.get("text", ""))
            elif event_type == "thinking_done":
                # Persist this round's thinking as its own message row so the
                # transcript preserves chronological order: thinking → tool calls →
                # next round's thinking → final assistant text. The engine fires this
                # per tool-round, so multi-round reasoning ends up as multiple rows
                # interleaved with tool_call/tool_result. Skip if no text (opaque path).
                _round_text = data.get("text") or "".join(_partial_thinking)
                _round_text = _round_text.strip()
                if _round_text:
                    _tr = data.get("tool_round")
                    _meta = {"tool_round": _tr} if _tr is not None else None
                    try:
                        session.add_message("thinking", _round_text, metadata=_meta)
                    except Exception as _e:
                        print(f"[thinking-persist] failed: {_e}", flush=True)
                # Reset the accumulator so the next round starts fresh.
                _partial_thinking.clear()
            elif event_type == "thinking_summary":
                _thinking_summary.update(data)
            elif event_type == "tool_call":
                name = data.get("name", "")
                args = data.get("args", {})
                tr = data.get("tool_round")
                # Update existing entry if re-emitted with full args, else append
                if args and _partial_tools and _partial_tools[-1].get("name") == name and not _partial_tools[-1].get("args"):
                    _partial_tools[-1]["args"] = args
                    if tr is not None:
                        _partial_tools[-1]["tool_round"] = tr
                else:
                    entry = {"name": name, "args": args}
                    if tr is not None:
                        entry["tool_round"] = tr
                    _partial_tools.append(entry)
            elif event_type == "tool_result":
                # Attach result to the last matching tool entry and extract
                # normalized references server-side. The cap controls how much
                # of the raw result string we persist — references are stored
                # separately in t["references"] so the client never needs to
                # re-parse the raw result JSON to render the references panel.
                tool_name = data.get("name", "")
                result_str = str(data.get("result", ""))
                if tool_name in ("read_document", "read_file",
                                 "read_path", "read_path_original"):
                    cap = 50000
                else:
                    cap = 5000
                refs = ChatHandlerMixin._extract_references(tool_name, result_str)
                for t in reversed(_partial_tools):
                    if t["name"] == tool_name and "result" not in t:
                        t["result"] = result_str[:cap]
                        if refs:
                            t["references"] = refs
                        break
                if refs:
                    event_queue.put(("references", {
                        "tool_name": tool_name,
                        "references": refs,
                        "tool_round": data.get("tool_round", 0),
                    }))
            elif event_type == "usage":
                _usage_totals["tokens_in"] += data.get("tokens_in", 0)
                _usage_totals["tokens_out"] += data.get("tokens_out", 0)
                _usage_totals["last_tokens_in"] = data.get("tokens_in", 0)
                # Attach per-round actual tokens to the matching request_payload
                _ur = data.get("tool_round")
                if _ur is not None:
                    for _p in _request_payloads:
                        if _p.get("tool_round") == _ur:
                            _p["tokens_in"] = data.get("tokens_in", 0)
                            _p["tokens_out"] = data.get("tokens_out", 0)
                            break
                return  # internal only, don't send to client
            elif event_type == "worker_usage":
                # Worker-side LLM call (e.g. summariser) tokens. Add to turn totals
                # so the status bar reflects the real cost. Forward to client for
                # the worker-flow panel.
                _usage_totals["tokens_in"] += data.get("tokens_in", 0)
                _usage_totals["tokens_out"] += data.get("tokens_out", 0)
                # (fall through so the event reaches the SSE queue)
            elif event_type == "request_payload":
                _request_payloads.append(data)
                return  # internal only, don't send to client
            event_queue.put((event_type, data))

        handler_self = self  # capture for closure

        def _rollback_messages(session, sid, target_count):
            """Rollback session.messages to target_count and remove extras from DB.
            Handles intermediate tool_use/tool_result messages from the agentic loop."""
            with session.lock:
                extras = len(session.messages) - target_count
                if extras <= 0:
                    return
                session.messages = session.messages[:target_count]
            # Delete the extra messages from DB (they were appended by send_message's tool loop)
            try:
                with _db_conn() as conn:
                    # Get all message IDs for this session, ordered by id
                    rows = conn.execute(
                        "SELECT id FROM messages WHERE session_id = ? ORDER BY id",
                        (sid,)
                    ).fetchall()
                    # Keep only the first target_count messages
                    if len(rows) > target_count:
                        ids_to_delete = [r[0] for r in rows[target_count:]]
                        conn.executemany("DELETE FROM messages WHERE id = ?", [(mid,) for mid in ids_to_delete])
                        conn.commit()
            except Exception as e:
                print(f"  [WARN] Message rollback DB cleanup: {e}", flush=True)

        def worker():
            # Set thread-local agent context (thread-safe, no global mutation)
            engine._thread_local.memory_store = session.memory
            agent_config = engine.AgentConfig(session.agent_id)
            engine._thread_local.current_agent = agent_config
            engine._thread_local.current_session_id = sid
            engine._thread_local.current_user_id = session.user_id or ""
            # Team IDs the user belongs to — used for team-scoped MemPalace wing filtering
            try:
                engine._thread_local.current_team_ids = [
                    t["id"] for t in _auth_mod.AuthDB.get_user_teams(session.user_id)
                ] if session.user_id else []
            except Exception:
                engine._thread_local.current_team_ids = []

            # Reset per-request state (prevents cross-session leaks in pooled threads)
            engine.reset_tool_dedup()

            # Use shared MCP manager (singleton from main())
            engine._thread_local.mcp_manager = engine._mcp_manager

            # Set plan mode if requested
            engine._thread_local.plan_mode = (chat_mode == "plan")

            # Set project scope if provided
            if project_name:
                session.project = project_name
                engine._thread_local.project = project_name
            else:
                engine._thread_local.project = session.project  # Use session's existing project

            # Set note context for AI-assisted note editing
            if session.note_context:
                engine._thread_local.note_context = session.note_context
            else:
                engine._thread_local.note_context = None

            # Workflow-run binding: when this session was created from the
            # inline workflow detail view, expose the execution_id so the
            # round-0 preamble can pull a compact summary of the run.
            engine._thread_local.workflow_run_id = getattr(session, 'workflow_run_id', '') or ''

            # Set caveman modes: chat-level (session toggle) + system-level (model config)
            engine._thread_local.caveman_chat = session.caveman_mode
            model_cfg = engine.resolve_model_settings(session.model) if engine._models_config else {}
            engine._thread_local.caveman_system = int(model_cfg.get("caveman_system", 0) or 0)

            # Set worker subagent execution overrides from agent config
            engine._thread_local.execution_overrides = agent_config.config.get("execution_overrides") or {}

            # Set attachment image model for read_attachment vision support
            engine._thread_local.attachment_image_model = server_config.get("attachment_image_model", "")

            # Set current model for worker summariser (cache reuse)
            engine._thread_local._current_model = session.model

            # Snapshot message count for rollback on failure
            _msg_count_before = len(session.messages)
            _req_start = time.time()

            try:
                # --- Standard backend ---
                # Use detected purpose from auto-resolve, or fall back to agent's fixed purpose
                purpose = session.agent.config.get("model_purpose")
                if not purpose and session.agent.config.get("model") == "auto":
                    purpose = engine.classify_task_purpose(message)
                inf_params = engine.get_inference_params(session.model, purpose)
                # Apply thinking level from request — only when the model supports thinking.
                _model_cfg = engine._models_config.get(session.model, {}) or {}
                _tfmt = _model_cfg.get("thinking_format", "none")
                if thinking_level and thinking_level != "none" and _tfmt != "none":
                    _THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 32768}
                    inf_params["thinking"] = True
                    inf_params["thinking_budget"] = _THINKING_BUDGETS.get(thinking_level, 8192)
                    # Provider-facing reasoning toggle. Engine's _apply_inference_to_payload maps this
                    # per thinking_format: reasoning_effort for mistral_blocks/reasoning_field/openai_opaque,
                    # chat_template_kwargs.enable_thinking for oMLX inline_tags variants, etc.
                    inf_params["thinking_level"] = thinking_level
                else:
                    inf_params.pop("thinking", None)
                    inf_params.pop("thinking_budget", None)
                    inf_params.pop("thinking_level", None)
                # If thinking-mode flipped vs what the warmup keeper primed,
                # kick off a background re-prime so the *next* turn's KV
                # prefix matches. Current turn still pays the cold cost.
                # No-op when model isn't warmup-flagged or has thinking_format=none.
                _wants_thinking = bool(inf_params.get("thinking"))
                engine.maybe_reprime_for_thinking(session.model, _wants_thinking,
                                                  agent_id=session.agent_id)
                reply = engine.send_message_with_fallback(
                    session.messages, session.model, session.api_key,
                    session.base_url,
                    silent=True, escape_watcher=session.cancel_token,
                    event_callback=event_callback,
                    provider_resolver=handler_self._resolve_provider,
                    inference_params=inf_params,
                    purpose=purpose,
                    session_id=sid,
                )
                if reply:
                    # Compute cost before saving
                    session_cost = None
                    if engine._cost_tracker:
                        try:
                            sc = engine._cost_tracker.get_session_cost(sid)
                            session_cost = round(sc.get("cost", 0.0), 4)
                        except Exception:
                            pass
                    # Build metadata: model, tokens, cost, files, tools, duration, usage
                    _req_duration = round(time.time() - _req_start, 2)
                    msg_metadata = {}
                    msg_metadata["model"] = session.model
                    msg_metadata["duration"] = _req_duration
                    msg_metadata["tokens_in"] = _usage_totals["tokens_in"]
                    msg_metadata["tokens_out"] = _usage_totals["tokens_out"]
                    msg_metadata["last_tokens_in"] = _usage_totals["last_tokens_in"]
                    if _request_payloads:
                        msg_metadata["request_payloads"] = _request_payloads
                    fb_model = getattr(engine._thread_local, '_fallback_model_used', None)
                    if fb_model:
                        msg_metadata["model"] = fb_model
                        msg_metadata["original_model"] = session.model
                    msg_metadata["tokens"] = engine._estimate_conversation_tokens(session.messages)
                    if session_cost is not None:
                        msg_metadata["cost"] = session_cost
                    if created_files:
                        msg_metadata["files"] = created_files
                    if _partial_tools:
                        msg_metadata["tools"] = _partial_tools
                    # Leftover thinking deltas that never got a thinking_done (truncated
                    # stream / error before flush). Persist as a fallback thinking row
                    # rather than losing the content.
                    thinking_leftover = "".join(_partial_thinking).strip()
                    if thinking_leftover:
                        try:
                            session.add_message("thinking", thinking_leftover,
                                                 metadata={"tool_round": None, "fallback": True})
                        except Exception:
                            msg_metadata["thinking"] = thinking_leftover  # legacy fallback
                        _partial_thinking.clear()
                    if _thinking_summary:
                        msg_metadata["thinking_summary"] = _thinking_summary
                    # Per-turn state snapshot: thinking level requested + caveman modes applied
                    if thinking_level:
                        msg_metadata["thinking_level"] = thinking_level
                    _cav_chat = int(getattr(engine._thread_local, "caveman_chat", 0) or 0)
                    _cav_sys = int(getattr(engine._thread_local, "caveman_system", 0) or 0)
                    if _cav_chat:
                        msg_metadata["caveman_chat"] = _cav_chat
                    if _cav_sys:
                        msg_metadata["caveman_system"] = _cav_sys
                    # --- Citation validator (Phase 1+2: validate + optional re-round) ---
                    # Phase 1: scans reply for [Quelle: X — "Y"] brackets, verifies each
                    # quote against the actual source files, counts uncited claims.
                    # Phase 2: when a project chat's reply violates the citation
                    # threshold (>30% uncited bullets OR ≥2 unverified quotes), fire ONE
                    # synchronous re-round with feedback — the corrected text replaces
                    # `reply` before persistence and the `done` SSE event. Max 1 re-round
                    # per turn. Gated by mempalace.citation_reround.enabled in config.
                    if getattr(engine._thread_local, 'project', None) and reply:
                        try:
                            _val = engine.validate_citations_in_response(reply, session_id=sid)
                            _cv_meta = {
                                "verified": _val.get("verified", 0),
                                "unverified_count": len(_val.get("unverified", []) or []),
                                "unverified_samples": [
                                    {"basename": bn, "quote_excerpt": q[:120], "reason": r}
                                    for (bn, q, r) in (_val.get("unverified") or [])[:5]
                                ],
                                "uncited_claims": _val.get("uncited_claims", 0),
                                "claim_total": _val.get("claim_total", 0),
                                "total_brackets": _val.get("total_brackets", 0),
                            }

                            # Phase 2 — synchronous re-round on threshold violation
                            _mp_cfg = engine._load_mempalace_config()
                            _rr_cfg = (_mp_cfg.get("citation_reround") or {}) if isinstance(_mp_cfg, dict) else {}
                            _rr_enabled = bool(_rr_cfg.get("enabled", False))
                            if _rr_enabled and engine.citation_reround_needed(_val):
                                try:
                                    _clean_msgs = engine.clean_messages_for_api(session.messages)
                                    _new_reply, _retry_val = engine.run_citation_reround(
                                        _clean_msgs, reply, _val,
                                        model=session.model,
                                        api_key=session.api_key,
                                        base_url=session.base_url,
                                        temperature=float(_rr_cfg.get("temperature", 0.2)),
                                        top_p=float(_rr_cfg.get("top_p", 0.85)),
                                        timeout=float(_rr_cfg.get("timeout_seconds", 180)),
                                    )
                                    if _new_reply and _new_reply != reply:
                                        _cv_meta["reround_fired"] = True
                                        _cv_meta["reround_original_reply"] = reply
                                        _cv_meta["reround_retry_validation"] = {
                                            "verified": _retry_val.get("verified", 0),
                                            "unverified_count": len(_retry_val.get("unverified", []) or []),
                                            "uncited_claims": _retry_val.get("uncited_claims", 0),
                                            "claim_total": _retry_val.get("claim_total", 0),
                                            "total_brackets": _retry_val.get("total_brackets", 0),
                                        }
                                        try: print(f"[citation-reround] fired: original={len(reply)}c -> corrected={len(_new_reply)}c")
                                        except Exception: pass
                                        reply = _new_reply
                                    else:
                                        _cv_meta["reround_fired"] = False
                                        _cv_meta["reround_skipped_reason"] = "no_change_or_empty"
                                except Exception as _e_rr:
                                    _cv_meta["reround_fired"] = False
                                    _cv_meta["reround_error"] = f"{type(_e_rr).__name__}: {_e_rr}"
                                    try: print(f"[citation-reround] error: {_e_rr}")
                                    except Exception: pass

                            msg_metadata["citation_validation"] = _cv_meta
                        except Exception as _e:
                            # Validation must never crash the response; log and continue.
                            try: print(f"[citation-validator] error: {_e}")
                            except Exception: pass
                    session.add_message("assistant", reply, metadata=msg_metadata or None)
                    done_data = {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                        "max_context": session.max_context,
                        "model": session.model,
                        "duration": _req_duration,
                        "tokens_in": _usage_totals["tokens_in"],
                        "tokens_out": _usage_totals["tokens_out"],
                        "last_tokens_in": _usage_totals["last_tokens_in"],
                    }
                    if session_cost is not None:
                        done_data["cost"] = session_cost
                    # Include fallback model info if a fallback was used
                    fb_model = getattr(engine._thread_local, '_fallback_model_used', None)
                    if fb_model:
                        done_data["fallback_model"] = fb_model
                        done_data["original_model"] = session.model
                    # Include file attachments
                    if created_files:
                        done_data["files"] = created_files
                    event_queue.put(("done", done_data))

                    # Continuous session summarization: refresh memory summary at token thresholds
                    try:
                        token_count = engine._estimate_conversation_tokens(session.messages)
                        last_summary_tokens = getattr(session, '_last_summary_at', 0)
                        threshold = 10000 if last_summary_tokens == 0 else last_summary_tokens + 5000
                        if token_count >= threshold:
                            session._last_summary_at = token_count
                            engine.trigger_memory_summary_refresh(session.agent_id)
                    except Exception:
                        pass

                    # Auto-memory extraction: check if response contains memorable info
                    try:
                        am_cfg = engine._get_auto_memory_config(session.agent_id)
                        min_msg_len = am_cfg.get("min_message_length", 20)
                        if am_cfg.get("enabled", True) and reply and message and len(message) > min_msg_len:
                            threading.Thread(
                                target=engine._auto_memory_extract,
                                args=(session.agent_id, message, reply[:1000]),
                                daemon=True,
                                name=f"auto_memory_{session.agent_id}"
                            ).start()
                    except Exception:
                        pass

                    # Generate chat summary (background, for sidebar display)
                    try:
                        if len(session.messages) >= 2 and not session.summary:
                            threading.Thread(
                                target=_generate_chat_summary,
                                args=(session,),
                                daemon=True,
                                name=f"chat_summary_{sid}"
                            ).start()
                    except Exception:
                        pass

                    # Index chat transcript for content search (4+ messages, every 4th message or first time)
                    try:
                        msg_count = len(session.messages)
                        if msg_count >= 4 and (msg_count % 4 == 0 or not os.path.isdir(
                                os.path.join(engine.AGENTS_DIR, session.agent_id, "chats-indexed"))):
                            threading.Thread(
                                target=_index_chat_transcript,
                                args=(session,),
                                daemon=True,
                                name=f"chat_index_{sid}"
                            ).start()
                    except Exception:
                        pass
                else:
                    # Empty reply — rollback all intermediate messages from tool loop
                    _rollback_messages(session, sid, _msg_count_before)
                    event_queue.put(("done", {"text": "", "tokens": 0, "model": session.model}))
            except engine.TaskCancelled:
                # Save partial response if any text was streamed
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += "\n\n*(Cancelled)*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": "Cancelled"}))
            except SystemExit as e:
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += f"\n\n*(Engine error: exit code {e.code})*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": f"Engine fatal error (exit code {e.code})"}))
            except Exception as e:
                import traceback
                traceback.print_exc()
                partial = "".join(_partial_reply).strip()
                if partial:
                    _rollback_messages(session, sid, _msg_count_before)
                    partial += f"\n\n*(Error: {str(e)[:200]})*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _partial_tools
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                event_queue.put(("error", {"message": str(e)}))
            finally:
                with session.lock:
                    session._streaming = False
                # Clean up thread-local state
                engine._thread_local.current_agent = None
                engine._thread_local.mcp_manager = None
                engine._thread_local.memory_store = None
                engine._thread_local.plan_mode = False
                engine._thread_local.caveman_chat = 0
                engine._thread_local.caveman_system = 0
                engine._thread_local.execution_overrides = {}
                engine._thread_local._current_model = None
                event_queue.put(None)  # sentinel


        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Stream events to client with keepalive (chunked encoding for HTTP/1.1)
        try:
            while True:
                try:
                    event = event_queue.get(timeout=5)
                except queue.Empty:
                    # If worker thread died, stop waiting
                    if not t.is_alive() and event_queue.empty():
                        try:
                            sse_err = f'event: error\ndata: {json.dumps({"message": "Server worker terminated unexpectedly"})}\n\n'
                            self.wfile.write(sse_err.encode("utf-8")); self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    # Send keepalive comment to prevent browser timeout
                    try:
                        self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                    continue
                if event is None:
                    break
                event_type, data = event
                sse_line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(sse_line.encode("utf-8")); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_cancel_scheduled(self):
        """POST /v1/schedule/cancel — cancel a running scheduled task."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Task name required"}, 400)
            return
        if engine._scheduler and engine._scheduler.cancel_running_task(name):
            self._send_json({"status": "cancelling", "name": name})
        else:
            self._send_json({"error": f"Task '{name}' not running"}, 404)

    def _handle_chat_answer(self):
        """POST /v1/chat/answer — deliver a user answer to a pending ask_user tool call.

        Body shapes:
          {session_id, answer: "..."}                             # single question
          {session_id, answers: {"<question>": "<answer>", ...}}  # batch
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        answer = body.get("answer")
        answers = body.get("answers")
        if not session_id or (answer is None and not isinstance(answers, dict)):
            self._send_json({"error": "session_id and answer/answers are required"}, 400)
            return
        if self._session_access_check(session_id) is None:
            return
        # Normalize answers dict values to strings
        if isinstance(answers, dict):
            answers = {str(k): str(v) for k, v in answers.items() if v is not None}
        from brain import deliver_ask_user_answer
        ok = deliver_ask_user_answer(
            session_id,
            answer=str(answer) if answer is not None else None,
            answers=answers if isinstance(answers, dict) and answers else None,
        )
        if not ok:
            self._send_json({"error": "no pending question for this session"}, 404)
            return
        self._send_json({"delivered": True, "session_id": session_id})
