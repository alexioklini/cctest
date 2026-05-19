# Extracted from server.py — session CRUD and management handlers
import json
import os
import sqlite3
import threading
import time

import brain as engine


def _backfill_orphan_artifacts(sid: str) -> None:
    """Safety net: register any file in this session's artifact folder that
    has no matching `artifacts` row.

    Under normal conditions every file written via `write_file` / `edit_file` /
    `python_exec` is registered in real time by `brain._after_file_write`. If
    that path ever silently drops a registration (as happened in v9.0.0 when
    the sidecar tool-dispatch thread had no `event_callback`), the file lands
    on disk but never surfaces in the artifacts panel. This pass closes the
    gap on chat reopen: cheap diff (one DB query + one listdir), no-op on
    healthy sessions.

    Only ADDS missing rows — never touches existing artifact_versions, so
    healthy version history is safe.
    """
    try:
        existing = ChatDB.get_artifacts(sid) or []
        registered_paths = {a.get("path") for a in existing if a.get("path")}
        agent_id = ""
        if existing:
            agent_id = existing[0].get("agent_id") or ""
        if not agent_id:
            info = ChatDB.get_session_info(sid)
            agent_id = (info or {}).get("agent_id") or "main"
        artifacts_root = os.path.join(engine.AGENTS_DIR, agent_id, "artifacts")
        if not os.path.isdir(artifacts_root):
            return
        # Folder name is `<YYYY-MM-DD>_<sid>`; the date is whenever the first
        # write happened, which may not be today — scan for the `_<sid>`
        # suffix instead of guessing today's date.
        suffix = f"_{sid}"
        folders = [d for d in os.listdir(artifacts_root) if d.endswith(suffix)]
        if not folders:
            return
        # Run registration under the session's thread-local context so
        # `_register_artifact_version` can resolve session_id.
        prev_sid = getattr(engine._thread_local, "current_session_id", None)
        engine._thread_local.current_session_id = sid
        try:
            for folder in folders:
                folder_path = os.path.join(artifacts_root, folder)
                try:
                    entries = os.listdir(folder_path)
                except OSError:
                    continue
                for name in entries:
                    fpath = os.path.join(folder_path, name)
                    if not os.path.isfile(fpath):
                        continue
                    if fpath in registered_paths:
                        continue
                    try:
                        engine._register_artifact_version(
                            fpath, "created", agent_id)
                    except Exception:
                        pass
        finally:
            engine._thread_local.current_session_id = prev_sid
    except Exception:
        pass


class SessionsHandlerMixin:

    def _handle_list_sessions(self):
        # Support ?agent=X&status=active|archived&project=Y
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", ""))
        status = unquote(params.get("status", ""))
        project = unquote(params.get("project", ""))
        # Multi-user: scope to visible user IDs + team-visible sessions
        auth_user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        visible = _auth_mod.get_visible_user_ids(auth_user)
        vteam = None
        caller_uid = None
        if visible is not None:
            vteam = [t["id"] for t in _auth_mod.AuthDB.get_user_teams(auth_user["id"])]
            caller_uid = auth_user["id"]
        if agent or project:
            if project:
                # Resolve name → id once. New sessions filter by id; legacy
                # sessions (created before the project_id column existed) are
                # backfilled at startup, so id-only filtering is correct.
                pid = _project_id_for_name(agent or "main", project)
                all_sessions = ChatDB.list_sessions(agent_id=agent or None, status=status or None,
                                                   project=project, project_id=pid or None,
                                                   visible_user_ids=visible, visible_team_ids=vteam,
                                                   caller_user_id=caller_uid)
                self._send_json({"sessions": all_sessions})
            else:
                all_sessions = ChatDB.list_sessions(agent_id=agent, status=status or None,
                                                   visible_user_ids=visible, visible_team_ids=vteam,
                                                   caller_user_id=caller_uid)
                self._send_json({"sessions": all_sessions})
        else:
            self._send_json({"sessions": ChatDB.list_sessions(visible_user_ids=visible, visible_team_ids=vteam, caller_user_id=caller_uid)})

    def _handle_get_messages(self, path):
        """GET /v1/sessions/<id>/messages"""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        _backfill_orphan_artifacts(sid)
        msgs = ChatDB.load_messages(sid)
        resp = {"session_id": sid, "messages": msgs}
        session = sessions.get(sid)
        # In-flight turn? Expose the flag + any incrementally-persisted partial
        # reply so the client can attach to GET /v1/chat/stream and render the
        # streaming text immediately on (re)open.
        _streaming = bool(getattr(session, "_streaming", False)) if session else False
        if _streaming:
            resp["streaming"] = True
            _st, _ = ChatDB.get_streaming_text(sid)
            if _st:
                resp["streaming_text"] = _st
        if session:
            resp["max_context"] = session.max_context
            resp["total_tokens"] = engine._estimate_conversation_tokens(session.messages)
            resp["summary"] = session.summary or ""
            resp["title"] = session.title or ""
            resp["caveman_mode"] = session.caveman_mode
            resp["save_to_memory"] = int(getattr(session, "save_to_memory", 0) or 0)
            resp["project"] = session.project or ""
            resp["workflow_run_id"] = getattr(session, "workflow_run_id", "") or ""
            _rmo = getattr(session, "research_mode_override", None)
            resp["research_mode_override"] = (None if _rmo is None else bool(_rmo))
            resp["gdpr_action_pref"] = getattr(session, "gdpr_action_pref", "") or ""
            resp["has_gdpr_mapping"] = bool(
                getattr(session, "_gdpr_mapping_id", "") or "")
            if not resp["has_gdpr_mapping"]:
                try:
                    resp["has_gdpr_mapping"] = bool(
                        ChatDB.list_pseudonym_maps_for_session(sid) or [])
                except Exception:
                    pass
        else:
            info = ChatDB.get_session_info(sid)
            if info:
                resp["summary"] = info.get("summary", "")
                resp["title"] = info.get("title", "")
                resp["caveman_mode"] = int(info.get("caveman_mode", 0) or 0)
                resp["save_to_memory"] = int(info.get("save_to_memory", 0) or 0)
                resp["project"] = info.get("project", "") or ""
                resp["workflow_run_id"] = info.get("workflow_run_id", "") or ""
                _rmo_db = info.get("research_mode_override", None)
                resp["research_mode_override"] = (None if _rmo_db is None
                                                   else bool(_rmo_db))
                _pref_db = info.get("gdpr_action_pref", "") or ""
                resp["gdpr_action_pref"] = (_pref_db if _pref_db in
                    ("anonymise", "local_model", "continue") else "")
                try:
                    resp["has_gdpr_mapping"] = bool(
                        ChatDB.list_pseudonym_maps_for_session(sid) or [])
                except Exception:
                    resp["has_gdpr_mapping"] = False
        self._send_json(resp)

    def _handle_next_prompt_suggestion(self, path):
        """GET /v1/sessions/<id>/next-prompt — generate a "predicted next user message"
        suggestion for the composer ghost-text. Synchronous: calls the LLM using the
        session's current messages (or an override model) and returns the text.
        Returns {"suggestion": "..."} or {"suggestion": null} when disabled/empty.
        """
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"suggestion": None, "error": "session_not_found"}, 404)
            return
        try:
            cfg = engine._get_next_prompt_config(session.agent_id)
            if not cfg.get("enabled", True):
                self._send_json({"suggestion": None, "config": cfg})
                return
            # Set thread-local agent context so LLM call picks up the right config
            engine._thread_local.current_agent = engine.AgentConfig(session.agent_id)
            try:
                text = engine.generate_next_prompt_suggestion(session)
            finally:
                engine._thread_local.current_agent = None
            self._send_json({
                "suggestion": text,
                "model_used": (cfg.get("model") or session.model),
                "config": cfg,
            })
        except Exception as e:
            self._send_json({"suggestion": None, "error": str(e)}, 500)

    def _handle_session_inspect(self, path):
        """GET /v1/sessions/<id>/inspect — full session debug view."""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        msgs = ChatDB.load_messages(sid, include_compacted=True)

        # Show the verbatim system prompt that was sent to the model on the
        # session's most recent turn. Persisted into sessions.last_system_prompt
        # by handlers/chat.py — never rebuilt here. Rebuilding always lies
        # (different hour-rounded timestamp, different active tool set if
        # config changed since the turn) and would surface filtered/altered
        # text. If the session has no recorded turn yet (first send still
        # in flight, or pre-migration session), the field is empty and
        # we surface a placeholder.
        system_prompt = ""
        system_tokens = 0
        memory_summary = ""
        memory_tokens = 0
        if session:
            try:
                with _db_conn() as _ssp_conn:
                    row = _ssp_conn.execute(
                        "SELECT last_system_prompt FROM sessions WHERE id = ?",
                        (sid,)).fetchone()
                if row and row[0]:
                    system_prompt = row[0]
                    system_tokens = len(system_prompt) // 4  # rough estimate
                else:
                    system_prompt = (
                        "[no system prompt captured for this session yet — "
                        "send a turn and re-open the inspector to see the "
                        "verbatim prompt that was sent to the model]"
                    )
                    system_tokens = 0
                # Memory summary (injected on first turn, separate from system prompt)
                try:
                    agent_config = engine.AgentConfig(session.agent_id)
                    ms = engine.get_memory_summary(session.agent_id)
                    if ms:
                        tc = agent_config.config.get("token_config") or {}
                        cap = tc.get("memory_summary_cap", 3000)
                        memory_summary = ms[:cap] if len(ms) > cap else ms
                        memory_tokens = len(memory_summary) // 4
                except Exception:
                    pass
            except Exception:
                pass

        # Build interaction pairs: user message + assistant response.
        # metadata.cost stored on each assistant message is the *cumulative* session
        # cost as of that turn (snapshot of get_session_cost), so per-turn cost is
        # the delta against the previous turn.
        interactions = []
        prev_cum_cost = 0.0
        i = 0
        while i < len(msgs):
            m = msgs[i]
            if m["role"] == "user":
                user_msg = m
                # Find matching assistant response
                assistant_msg = None
                j = i + 1
                while j < len(msgs):
                    if msgs[j]["role"] == "assistant":
                        assistant_msg = msgs[j]
                        break
                    j += 1
                meta = (assistant_msg or {}).get("metadata", {})
                user_meta = user_msg.get("metadata") or {}
                content_in = user_msg.get("content", "")
                if isinstance(content_in, list):
                    content_in = " ".join(str(b.get("text", "")) for b in content_in if isinstance(b, dict))
                content_out = (assistant_msg or {}).get("content", "")
                if isinstance(content_out, list):
                    content_out = " ".join(str(b.get("text", "")) for b in content_out if isinstance(b, dict))
                # Wire-truth for transparent-anonymisation turns: when the
                # user message was pseudonymised before reaching the cloud
                # LLM (`metadata.wire_content` set by handlers/chat.py), or
                # when the assistant reply was de-anonymised before being
                # persisted (`metadata.wire_content` captured pre-restore),
                # surface the raw on-wire text so the inspector can render
                # "typed by user → sent to cloud" / "received from cloud →
                # shown to user" side-by-side. Empty/missing on every other
                # turn — chat UI semantics are unchanged.
                def _flatten(c):
                    if isinstance(c, list):
                        return " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                    return c or ""
                user_wire = user_meta.get("wire_content")
                user_wire_str = _flatten(user_wire) if user_wire is not None else ""
                asst_wire = meta.get("wire_content")
                asst_wire_str = _flatten(asst_wire) if asst_wire is not None else ""
                # Extract request payloads (what was actually sent to API)
                payloads = meta.get("request_payloads", [])
                cum_cost = float(meta.get("cost") or 0.0) if assistant_msg else prev_cum_cost
                turn_cost = max(0.0, cum_cost - prev_cum_cost)
                interactions.append({
                    "turn": len(interactions) + 1,
                    "user": {
                        "content": content_in,
                        "tokens_est": len(str(content_in)) // 4,
                        "wire_content": user_wire_str,
                        "gdpr_mapping_id": user_meta.get("gdpr_mapping_id") or "",
                    },
                    "assistant": {
                        "content": content_out,
                        "tokens_est": len(str(content_out)) // 4,
                        "tokens_in": meta.get("tokens_in", 0),
                        "tokens_out": meta.get("tokens_out", 0),
                        "tokens_total": meta.get("tokens", 0),
                        "duration": meta.get("duration", 0),
                        "model": meta.get("model", ""),
                        "cost": round(turn_cost, 4),
                        "tools": meta.get("tools", []),
                        "thinking": bool(meta.get("thinking")),
                        "thinking_level": meta.get("thinking_level") or ("none" if meta.get("thinking") is None else None),
                        "caveman_chat": int(meta.get("caveman_chat") or 0),
                        "caveman_system": int(meta.get("caveman_system") or 0),
                        "sdk": meta.get("sdk", False),
                        "request_payloads": payloads,
                        "wire_content": asst_wire_str,
                        "gdpr_mapping_id": meta.get("gdpr_mapping_id") or "",
                        "gdpr_restored": int(meta.get("gdpr_restored") or 0),
                    } if assistant_msg else None,
                    "compacted": bool(m.get("compacted")),
                })
                if assistant_msg:
                    prev_cum_cost = cum_cost
                i = (j + 1) if assistant_msg else (i + 1)
            else:
                i += 1

        # Totals — total session cost is the latest cumulative snapshot, not a sum
        # of (already-cumulative) per-message values.
        total_in = sum((ix["assistant"] or {}).get("tokens_in", 0) for ix in interactions if ix.get("assistant"))
        total_out = sum((ix["assistant"] or {}).get("tokens_out", 0) for ix in interactions if ix.get("assistant"))
        total_duration = sum((ix["assistant"] or {}).get("duration", 0) for ix in interactions if ix.get("assistant"))
        total_cost = prev_cum_cost

        self._send_json({
            "session_id": sid,
            "agent": session.agent_id if session else "",
            "model": session.model if session else "",
            "max_context": session.max_context if session else 0,
            "system_prompt": {"content": system_prompt, "tokens_est": system_tokens},
            "memory_summary": {"content": memory_summary, "tokens_est": memory_tokens},
            "interactions": interactions,
            "totals": {
                "turns": len(interactions),
                "tokens_in": total_in,
                "tokens_out": total_out,
                "duration": round(total_duration, 2),
                "cost": round(total_cost, 4),
            },
        })

    def _handle_session_gdpr_maps_list(self, path):
        """GET /v1/sessions/<id>/gdpr-maps — admin-only.

        Returns the list of pseudonym_maps rows persisted for this session
        (mapping_id, turn_id, created_at). Bodies stay encrypted at rest;
        the detail endpoint decrypts one mapping on demand. Step 6.4.
        """
        sid = path.split("/")[3]
        # Admin gate. Owners do NOT see plaintext PII even on their own
        # chats — pseudonymisation is a privacy boundary, not a UX feature.
        user = getattr(self, '_auth_user', None)
        if not user or (user.get("role") != "admin" and user.get("id") != "__system__"):
            self._send_json({"error": "admin only"}, 403)
            return
        if self._session_access_check(sid) is None:
            return
        try:
            rows = ChatDB.list_pseudonym_maps_for_session(sid)
        except Exception as e:
            self._send_json({"error": f"db error: {e}"}, 500)
            return
        # Each row: (mapping_id, turn_id, created_at)
        out = [
            {"mapping_id": r[0], "turn_id": r[1] or "",
             "created_at": r[2]}
            for r in (rows or [])
        ]
        self._send_json({"session_id": sid, "mappings": out})

    def _handle_session_gdpr_map_detail(self, path):
        """GET /v1/sessions/<id>/gdpr-maps/<mapping_id> — admin-only.

        Decrypts the stored mapping and returns the forward (real → token)
        pairs plus per-finding metadata so the auditor can see what was
        sent vs. what the user typed. Step 6.4.
        """
        parts = path.split("/")
        # /v1/sessions/<sid>/gdpr-maps/<mapping_id>  → parts: ['','v1','sessions',sid,'gdpr-maps',mid]
        if len(parts) < 6:
            self._send_json({"error": "malformed path"}, 400)
            return
        sid = parts[3]
        mapping_id = parts[5]
        user = getattr(self, '_auth_user', None)
        if not user or (user.get("role") != "admin" and user.get("id") != "__system__"):
            self._send_json({"error": "admin only"}, 403)
            return
        if self._session_access_check(sid) is None:
            return
        import pseudonymizer as _ps  # local import to avoid cycles at boot
        try:
            mapping = _ps.load_mapping(mapping_id)
        except Exception as e:
            # AAD mismatch, missing keyfile, or tampered ciphertext all
            # land here. Surface the class of failure (not the trace) so
            # the auditor knows whether to investigate.
            self._send_json({"error": f"decrypt failed: {type(e).__name__}: {e}"}, 500)
            return
        if mapping is None:
            self._send_json({"error": "mapping not found for this id"}, 404)
            return
        # Cross-check: the loaded mapping's id must match the URL. Defence
        # against a future bug where load_mapping silently returns the
        # wrong row.
        if getattr(mapping, "mapping_id", "") != mapping_id:
            self._send_json({"error": "mapping_id mismatch"}, 500)
            return
        # forward = {real_value: token}. categories = {rule_id: count}.
        # sources = set of input labels (chat_text, attachment:<name>, …).
        pairs = [
            {"real": real, "token": tok}
            for real, tok in (mapping.forward or {}).items()
        ]
        self._send_json({
            "session_id": sid,
            "mapping_id": mapping_id,
            "pairs": pairs,
            "categories": dict(mapping.finding_counts or {}),
            "sources": sorted(mapping.sources or []),
            "token_count": len(pairs),
        })

    def _handle_session_pii_history_summary(self, path):
        """GET /v1/sessions/<id>/pii-history-summary — server-side PII scan
        over the session's loaded user + assistant text.

        Mirrors the client-side `piiHistoryText` extraction (no tool_use /
        tool_result, no metadata) and runs the full server scanner — regex +
        bare-id + spaCy NER. Returns category counts the composer history
        badge can union with its local regex scan so soft-PII (name /
        address / organisation) that only NER detects still surfaces.

        Returns: {session_id, counts: {<label>: N}, finding_count, has,
                  worst_action: 'ignore'|'warn'|'block'}
        """
        sid = path.split("/")[3]
        if self._session_access_check(sid) is None:
            return
        cfg = engine._get_gdpr_scanner_config()
        # Honour the master toggle — if scanner is disabled, return an
        # empty result rather than a 4xx so the client doesn't spam errors.
        if not cfg.get("enabled", True):
            self._send_json({
                "session_id": sid, "counts": {}, "finding_count": 0,
                "has": False, "worst_action": "ignore", "disabled": True,
            })
            return
        try:
            msgs = ChatDB.load_messages(sid, include_compacted=True)
        except Exception as e:
            self._send_json({"error": f"db error: {e}"}, 500)
            return
        # Mirror web/js/nav.js:piiHistoryText — user + assistant text only.
        # Tool calls/results are downstream of user intent and would surface
        # URLs / search snippets that the client deliberately skips.
        parts: list[str] = []
        for m in msgs or []:
            role = m.get("role") or ""
            if role not in ("user", "human", "assistant"):
                continue
            c = m.get("content")
            if isinstance(c, str):
                if c:
                    parts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = b.get("text")
                        if isinstance(t, str) and t:
                            parts.append(t)
            # Attachment metadata (filenames + mime) — same as client.
            meta = m.get("metadata") or {}
            for f in (meta.get("files") or []):
                if isinstance(f, dict):
                    bits = [f.get(k) for k in ("name", "filename", "path",
                                                "mime", "type")
                            if f.get(k)]
                    if bits:
                        parts.append(" ".join(str(b) for b in bits))
        text = "\n".join(parts)
        if not text:
            self._send_json({
                "session_id": sid, "counts": {}, "finding_count": 0,
                "has": False, "worst_action": "ignore",
            })
            return
        try:
            findings = engine._pii_scan_text(text, cfg=cfg, max_findings=200)
        except Exception as e:
            print(f"[pii_history_summary] scan failed: {e}", flush=True)
            self._send_json({"error": "scan failed"}, 500)
            return
        # Aggregate by label (matches the client's `summarize`, which keys by
        # human-readable label so the popover can render the same chip names
        # for regex + NER findings interchangeably).
        counts: dict[str, int] = {}
        worst = "ignore"
        for f in findings:
            label = f.get("label") or f.get("rule_id") or "?"
            counts[label] = counts.get(label, 0) + 1
            a = f.get("action") or "warn"
            if a == "block":
                worst = "block"
            elif a == "warn" and worst != "block":
                worst = "warn"
        self._send_json({
            "session_id": sid,
            "counts": counts,
            "finding_count": sum(counts.values()),
            "has": bool(counts),
            "worst_action": worst,
        })

    def _handle_get_session_files(self, path):
        """GET /v1/sessions/<id>/files — returns all files from all messages (including compacted)"""
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        msgs = ChatDB.load_messages(sid, include_compacted=True)
        files = []
        seen = set()
        for m in msgs:
            meta = m.get("metadata") or {}
            for f in (meta.get("files") or []):
                key = f.get("path") or f.get("name") or str(f)
                if key not in seen:
                    seen.add(key)
                    files.append(f)
        self._send_json({"session_id": sid, "files": files})

    def _handle_session_search(self):
        """GET /v1/sessions/search?q=<query>&agent=<agent_id>&limit=20 — deep search across chat content."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        query = (qs.get("q") or [""])[0]
        agent_id = (qs.get("agent") or [""])[0]
        limit = int((qs.get("limit") or ["20"])[0])

        if not query:
            self._send_json({"results": [], "query": ""})
            return

        results = []
        seen_sessions = set()

        # 1. QMD semantic search on chat transcript chunks
        if agent_id:
            try:
                ms = engine.MemoryStore(agent_id)
                qmd_results = ms.recall(query, limit=limit * 2, mem_type="chat_transcript")
                for r in qmd_results:
                    sid = ""
                    # Extract session_id from frontmatter (already parsed into result)
                    fm_path = r.get("file_path", "")
                    # Try to read session_id from the file's frontmatter
                    if fm_path and os.path.exists(fm_path):
                        try:
                            with open(fm_path, "r") as f:
                                raw_head = f.read(500)
                            fm, _ = engine._parse_frontmatter(raw_head)
                            sid = fm.get("session_id", "")
                        except Exception:
                            pass
                    if not sid:
                        # Try to extract from filename: chat-{session_id}-{chunk}.md
                        fname = os.path.basename(fm_path or "")
                        if fname.startswith("chat-") and fname.endswith(".md"):
                            parts = fname[5:].rsplit("-", 1)
                            if len(parts) == 2:
                                sid = parts[0]
                    if sid and sid not in seen_sessions:
                        seen_sessions.add(sid)
                        info = ChatDB.get_session_info(sid)
                        if info:
                            info["match_type"] = "content"
                            info["match_preview"] = (r.get("content", ""))[:150]
                            info["score"] = r.get("score", 0)
                            results.append(info)
            except Exception:
                pass

        # 2. SQLite search on title + summary (for sessions not found by QMD)
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                q = ("SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as message_count "
                     "FROM sessions s WHERE (s.title LIKE ? OR s.summary LIKE ?)")
                params = [f"%{query}%", f"%{query}%"]
                if agent_id:
                    q += " AND s.agent_id = ?"
                    params.append(agent_id)
                q += " ORDER BY s.last_active DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    d = dict(r)
                    if d["id"] not in seen_sessions:
                        seen_sessions.add(d["id"])
                        d["match_type"] = "title" if query.lower() in (d.get("title") or "").lower() else "summary"
                        d["score"] = 0
                        results.append(d)
        except Exception:
            pass

        # 3. SQLite search on message content (catches chats not indexed in QMD)
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                q = ("SELECT DISTINCT m.session_id, m.content FROM messages m "
                     "JOIN sessions s ON s.id = m.session_id "
                     "WHERE m.content LIKE ?")
                params = [f"%{query}%"]
                if agent_id:
                    q += " AND s.agent_id = ?"
                    params.append(agent_id)
                q += " ORDER BY m.created_at DESC LIMIT ?"
                params.append(limit * 3)  # over-fetch since multiple messages per session
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    sid = r["session_id"]
                    if sid in seen_sessions:
                        continue
                    seen_sessions.add(sid)
                    info = ChatDB.get_session_info(sid)
                    if info:
                        # Extract a preview snippet around the match
                        content = r["content"] if isinstance(r["content"], str) else ""
                        idx = content.lower().find(query.lower())
                        if idx >= 0:
                            start = max(0, idx - 40)
                            end = min(len(content), idx + len(query) + 80)
                            preview = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                        else:
                            preview = content[:120]
                        info["match_type"] = "content"
                        info["match_preview"] = preview
                        info["score"] = 0
                        results.append(info)
                        if len(results) >= limit:
                            break
        except Exception:
            pass

        # Sort by score (QMD results) then recency
        results.sort(key=lambda x: (x.get("score", 0), x.get("last_active", 0)), reverse=True)
        # Multi-user: filter search results to sessions the caller can see
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            visible_uids = set(_auth_mod.get_visible_user_ids(user) or [])
            my_team_ids = {t["id"] for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
            def _accessible(r):
                owner = r.get("user_id") or ""
                if not owner:
                    return True  # legacy anonymous
                if owner in visible_uids:
                    return True
                if r.get("visibility") == "team" and r.get("team_id") in my_team_ids:
                    return True
                return False
            results = [r for r in results if _accessible(r)]
        self._send_json({"results": results[:limit], "query": query})

    def _handle_manage_session(self):
        """POST /v1/sessions/manage — archive, unarchive, clear, delete_message"""
        body = self._read_json()
        action = body.get("action", "")
        sid = body.get("session_id", "")
        if sid and self._session_access_check(sid, require_manage=True) is None:
            return

        if action == "set_visibility":
            vis = body.get("visibility", "user")
            team_id = body.get("team_id", "")
            if vis not in ("user", "team"):
                self._send_json({"error": "visibility must be 'user' or 'team'"}, 400); return
            if vis == "team" and not team_id:
                self._send_json({"error": "team_id required for team visibility"}, 400); return
            if vis == "team":
                # Caller must be a member of the target team (admin bypass handled above)
                user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
                if user["role"] != "admin" and user["id"] != "__system__":
                    my_teams = {t["id"] for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
                    if team_id not in my_teams:
                        self._send_json({"error": "You are not a member of that team"}, 403); return
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET visibility = ?, team_id = ? WHERE id = ?",
                             (vis, team_id if vis == "team" else "", sid))
                conn.commit()
            self._send_json({"status": "updated", "session_id": sid, "visibility": vis, "team_id": team_id if vis == "team" else ""})
            return

        if action == "archive":
            ChatDB.archive_session(sid)
            with sessions._lock:
                sessions._sessions.pop(sid, None)
            self._send_json({"status": "archived", "session_id": sid})
        elif action == "unarchive":
            ChatDB.unarchive_session(sid)
            self._send_json({"status": "unarchived", "session_id": sid})
        elif action == "clear":
            ChatDB.clear_messages(sid)
            s = sessions.get(sid)
            if s:
                s.messages = []
            self._send_json({"status": "cleared", "session_id": sid})
        elif action == "delete_message":
            msg_id = body.get("message_id")
            if msg_id:
                ChatDB.delete_message(msg_id)
                # Also remove from in-memory session
                s = sessions.get(sid)
                if s:
                    s.messages = [m for m in s.messages if m.get("id") != msg_id]
                self._send_json({"status": "deleted", "message_id": msg_id})
            else:
                self._send_json({"error": "message_id required"}, 400)
        elif action == "delete_messages":
            # Bulk delete: accepts message_ids (list)
            msg_ids = body.get("message_ids", [])
            if not msg_ids:
                self._send_json({"error": "message_ids required"}, 400)
                return
            s = sessions.get(sid)
            id_set = set(msg_ids)
            # Collect artifact IDs from messages being deleted
            artifact_ids_to_delete = set()
            with _db_conn() as conn:
                placeholders = ",".join("?" * len(msg_ids))
                rows = conn.execute(
                    f"SELECT metadata FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                    [sid] + list(msg_ids)).fetchall()
                for (meta_str,) in rows:
                    if not meta_str:
                        continue
                    try:
                        meta = json.loads(meta_str)
                        for f in meta.get("files", []):
                            aid = f.get("artifact_id")
                            if aid:
                                artifact_ids_to_delete.add(aid)
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Delete messages
                conn.execute(f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                             [sid] + list(msg_ids))
                # Delete orphaned artifacts and their versions + files
                for aid in artifact_ids_to_delete:
                    row = conn.execute("SELECT path FROM artifacts WHERE id = ?", (aid,)).fetchone()
                    conn.execute("DELETE FROM artifact_versions WHERE artifact_id = ?", (aid,))
                    conn.execute("DELETE FROM artifacts WHERE id = ?", (aid,))
                    if row and row[0]:
                        try:
                            os.remove(row[0])
                            # Remove parent dir if empty
                            parent = os.path.dirname(row[0])
                            if parent and os.path.isdir(parent) and not os.listdir(parent):
                                os.rmdir(parent)
                        except OSError:
                            pass
                conn.commit()
            if s:
                with s.lock:
                    s.messages = [m for m in s.messages if m.get("id") not in id_set]
            self._send_json({"status": "deleted", "count": len(msg_ids),
                             "artifacts_deleted": len(artifact_ids_to_delete)})
        elif action == "archive_all":
            agent = body.get("agent")
            project = body.get("project")
            pid = _project_id_for_name(agent or "main", project) if project else ""
            ChatDB.archive_all(agent, project=project if project is not None else None,
                              project_id=pid or None)
            self._send_json({"status": "archived_all"})
        elif action == "unarchive_all":
            agent = body.get("agent")
            project = body.get("project")
            pid = _project_id_for_name(agent or "main", project) if project else ""
            ChatDB.unarchive_all(agent, project=project if project is not None else None,
                                project_id=pid or None)
            self._send_json({"status": "unarchived_all"})
        elif action == "delete_all":
            agent = body.get("agent")
            archived_only = body.get("archived_only", False)
            project = body.get("project")
            pid = _project_id_for_name(agent or "main", project) if project else ""
            sids = ChatDB.delete_all(agent, archived_only,
                                    project=project if project is not None else None,
                                    project_id=pid or None)
            for sid in (sids or []):
                sessions.delete(sid)
                if agent:
                    try:
                        _cleanup_chat_index(sid, agent)
                    except Exception:
                        pass
            self._send_json({"status": "deleted_all", "count": len(sids or [])})
        elif action == "delete":
            # Get agent_id before deleting so we can trigger summary refresh
            info = ChatDB.get_session_info(sid)
            sessions.delete(sid)
            self._send_json({"status": "deleted", "session_id": sid})
            # Clean up indexed transcript files and trigger memory summary refresh
            if info:
                agent = info.get("agent_id", "main")
                try:
                    _cleanup_chat_index(sid, agent)
                except Exception:
                    pass
                try:
                    engine.trigger_memory_summary_refresh(agent)
                except Exception:
                    pass
        elif action == "incognito":
            # Mark session as incognito — excluded from memory summary
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET status = 'incognito' WHERE id = ?", (sid,))
                conn.commit()
            s = sessions.get(sid)
            if s:
                s.status = "incognito"
            self._send_json({"status": "incognito", "session_id": sid})
        elif action == "un_incognito":
            # Revert incognito session back to active
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET status = 'active' WHERE id = ?", (sid,))
                conn.commit()
            s = sessions.get(sid)
            if s:
                s.status = "active"
            self._send_json({"status": "active", "session_id": sid})
        elif action == "rename":
            title = body.get("title", "").strip()
            if not title:
                self._send_json({"error": "title required"}, 400)
                return
            # Rename targets the primary `title` column. The LLM-generated
            # `summary` is left untouched — it only surfaces as a hover
            # tooltip and the collapsible block in the chat view.
            with _db_conn() as conn:
                conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid))
                conn.commit()
            s = sessions.get(sid)
            if s:
                with s.lock:
                    s.title = title
            self._send_json({"status": "renamed", "session_id": sid, "title": title})
        elif action == "save_to_memory":
            # 0=off, 1=on, 2=auto
            mode = body.get("mode", None)
            if mode is None:
                mode = 1 if body.get("value", False) else 0
            mode = max(0, min(2, int(mode)))
            ChatDB.update_session_save_to_memory(sid, mode)
            s = sessions.get(sid)
            if s:
                s.save_to_memory = mode
            self._send_json({"status": "ok", "save_to_memory": mode, "session_id": sid})
        elif action == "research_mode_override":
            # Per-session override of the project's research_mode default.
            # Body: {value: null|true|false} — null clears the override
            # (falls back to project default); true/false force on/off for
            # this session, sticky across turns.
            raw = body.get("value", None) if "value" in body else body.get("mode", None)
            if raw is None or raw == "null":
                normalised = None
            else:
                normalised = bool(raw)
            ChatDB.update_session_research_mode_override(sid, normalised)
            s = sessions.get(sid)
            if s:
                s.research_mode_override = normalised
            self._send_json({"status": "ok",
                              "research_mode_override": normalised,
                              "session_id": sid})
        elif action == "gdpr_action_pref":
            # Transparent-anonymisation sticky preference (step 6.2).
            # Body: {value: ''|'anonymise'|'local_model'|'continue'} — empty
            # clears the preference (modal asks again on next send). 'cancel'
            # is rejected (would brick the chat). The web modal POSTs here
            # when the user ticks "Don't ask again for this chat".
            raw = (body.get("value") or "").strip().lower()
            if raw not in ("", "anonymise", "local_model", "continue"):
                self._send_json({"error": f"invalid value: {raw!r}"}, 400)
                return
            ChatDB.update_session_gdpr_action_pref(sid, raw)
            s = sessions.get(sid)
            if s:
                s.gdpr_action_pref = raw
                # Empty value with a prior mapping means the user explicitly
                # opted out of the session-sticky auto-anonymise rule.
                # Without this flag, the chat worker would silently re-enter
                # the anonymise branch because `pseudonym_maps` has rows.
                if not raw:
                    s._gdpr_skip_auto = True
                    s._gdpr_mapping_id = None
                    s._gdpr_streamer = None
                else:
                    s._gdpr_skip_auto = False
            self._send_json({"status": "ok",
                              "gdpr_action_pref": raw,
                              "session_id": sid})
        elif action == "purge_memory":
            # Remove every MemPalace drawer/closet filed from this session and
            # reset the sync cursor so re-enabling memory re-ingests from scratch.
            _purge_mempalace_session(sid)
            try:
                with _db_conn() as conn:
                    conn.execute("DELETE FROM chat_mempalace_sync WHERE session_id = ?", (sid,))
                    conn.commit()
            except Exception:
                pass
            self._send_json({"status": "ok", "purged": True, "session_id": sid})
        elif action in ("memorize_turns", "purge_turns"):
            # Body: {turn_ids: [mid, ...]} OR {scope, anchor_turn_id} where
            # scope ∈ {"all","this","above","below"}. turn_ids wins if provided.
            turn_ids = body.get("turn_ids")
            scope = (body.get("scope") or "").strip().lower()
            anchor = int(body.get("anchor_turn_id") or 0)
            resolved: list[int] = []
            if isinstance(turn_ids, list) and turn_ids:
                resolved = [int(t) for t in turn_ids if str(t).isdigit() or isinstance(t, int)]
            elif scope:
                try:
                    with _db_conn() as conn:
                        rows = conn.execute(
                            "SELECT id FROM messages WHERE session_id = ? AND role = 'user' "
                            "ORDER BY id", (sid,)
                        ).fetchall()
                    all_turns = [int(r[0]) for r in rows]
                except Exception:
                    all_turns = []
                if scope == "all":
                    resolved = all_turns
                elif scope == "this":
                    resolved = [anchor] if anchor else []
                elif scope == "above":
                    resolved = [t for t in all_turns if t < anchor]
                elif scope == "below":
                    resolved = [t for t in all_turns if t > anchor]
            if not resolved:
                self._send_json({"status": "ok", "count": 0, "session_id": sid})
                return
            if action == "purge_turns":
                _purge_mempalace_turns(sid, resolved)
                self._send_json({"status": "ok", "purged": len(resolved),
                                 "turn_ids": resolved, "session_id": sid})
            else:
                # memorize — run in background since add_drawer can take a moment
                def _do_mem():
                    try:
                        _memorize_mempalace_turns(sid, resolved)
                    except Exception as e:
                        print(f"[mempalace-memorize-turns] bg error: {e}")
                threading.Thread(target=_do_mem, daemon=True,
                                 name=f"mp-mem-turns-{sid[:8]}").start()
                self._send_json({"status": "ok", "memorizing": len(resolved),
                                 "turn_ids": resolved, "session_id": sid})
        elif action == "caveman_mode":
            mode = max(0, min(3, int(body.get("mode", 0))))
            ChatDB.update_session_caveman_mode(sid, mode)
            s = sessions.get(sid)
            if s:
                s.caveman_mode = mode
            # Cache invalidation no longer needed: caveman level lives outside
            # the cache key as post-processing on the cached base prose.
            self._send_json({"status": "ok", "caveman_mode": mode, "session_id": sid})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)
