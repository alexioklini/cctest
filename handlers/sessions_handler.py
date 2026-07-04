# Extracted from server.py — session CRUD and management handlers
import json
import os
import sqlite3
import threading
import time
import uuid

import brain as engine
from handlers import sidecar_proxy
from server_lib.sse_stream import encode_sse
from server_lib import auth as _auth_mod


def _pii_decision_history_with_names(session_id: str) -> dict:
    """value_hash → chronological decision events with resolved display names,
    in the shape both GDPR modals render: [{turn_action, false_positive,
    fake_value, by, by_id, at}] (oldest-first). Best-effort — any failure
    yields {} so the scan still returns. `by` falls back to 'System' for the
    empty user_id of non-interactive / legacy rows."""
    try:
        from server_lib.db import ChatDB as _ChatDB
        raw = _ChatDB.get_session_pii_decision_history(session_id)
    except Exception:
        return {}
    # Resolve user_id → display name once.
    names = {}
    try:
        for u in (_auth_mod.AuthDB.list_users() or []):
            names[u.get("id") or ""] = (u.get("display_name")
                                        or u.get("username") or "")
    except Exception:
        pass
    out = {}
    for vh, events in (raw or {}).items():
        rows = []
        for ev in events:
            uid = ev.get("user_id") or ""
            rows.append({
                "turn_action": ev.get("turn_action") or "",
                "false_positive": bool(ev.get("false_positive")),
                "fake_value": ev.get("fake_value") or "",
                "by_id": uid,
                "by": names.get(uid) or (uid and "Unbekannt") or "System",
                "at": ev.get("created_at"),
            })
        out[vh] = rows
    return out

# One-time download tokens for chat-bundle zips. The SSE build endpoint writes
# the zip to a temp file and registers a token here; the download endpoint
# serves it once and deletes both. Entries auto-expire after _BUNDLE_TTL.
_bundle_downloads: dict[str, dict] = {}
_bundle_lock = threading.Lock()
_BUNDLE_TTL = 600  # seconds


def _bundle_register(path: str, filename: str) -> str:
    tok = uuid.uuid4().hex
    with _bundle_lock:
        # Opportunistic GC of expired entries.
        now = time.time()
        for k, v in list(_bundle_downloads.items()):
            if now - v.get("ts", 0) > _BUNDLE_TTL:
                try:
                    os.unlink(v.get("path", ""))
                except OSError:
                    pass
                _bundle_downloads.pop(k, None)
        _bundle_downloads[tok] = {"path": path, "filename": filename, "ts": now}
    return tok


def _next_prompt_cache_sig(session) -> str:
    """Cheap signature of the conversation state for the next-prompt cache. Changes
    whenever a turn is added (count) or the tail message changes, plus the caveman
    mode (it shapes the suggestion's style). A matching sig ⇒ the cached suggestion
    is still valid; a new turn ⇒ regenerate."""
    try:
        msgs = session.messages or []
        n = len(msgs)
        last = msgs[-1] if msgs else {}
        last_role = last.get("role", "") if isinstance(last, dict) else ""
        last_c = last.get("content", "") if isinstance(last, dict) else ""
        last_len = len(last_c) if isinstance(last_c, str) else 0
        cm = int(getattr(session, "caveman_mode", 0) or 0)
        return f"{n}:{last_role}:{last_len}:{cm}"
    except Exception:
        return ""


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
        with engine.request_context(current_session_id=sid):
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
    except Exception:
        pass


def _flatten_content(content) -> str:
    """Render a message's content (string or content-block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append(b.get("text", ""))
            elif t in ("image_url", "image"):
                parts.append("_[Bild]_")
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _build_conversation_markdown(sid: str, info: dict, msgs: list) -> str:
    """Pure, deterministic markdown dump of the full conversation — no LLM.

    Includes user/assistant turns and thinking blocks (collapsed). Tool
    exchanges live only in-memory and never reach the DB, so they are absent by
    design — this dumps the persisted history.
    """
    from datetime import datetime as _dt
    title = (info.get("title") or "").strip() or "Chat"
    lines = [
        f"# {title}",
        "",
        f"- **Sitzung:** `{sid}`",
        f"- **Agent:** {info.get('agent_id') or 'main'}",
    ]
    if info.get("project"):
        lines.append(f"- **Projekt:** {info.get('project')}")
    if info.get("model"):
        lines.append(f"- **Modell:** {info.get('model')}")
    lines.append(f"- **Exportiert:** {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    turn = 0
    for m in msgs:
        role = m.get("role", "")
        text = _flatten_content(m.get("content", "")).rstrip()
        if not text:
            continue
        if role in ("user", "human"):
            turn += 1
            # Strip the round-0 artifact-folder preamble riding in the first
            # user message (plumbing, not what the user typed).
            _pre = (m.get("metadata") or {}).get("preamble")
            if _pre and text.startswith(_pre):
                text = text[len(_pre):].lstrip("\n")
            lines.append(f"## Anfrage {turn} — Nutzer")
            lines.append("")
            lines.append(text)
        elif role == "assistant":
            lines.append("### Antwort")
            lines.append("")
            lines.append(text)
        elif role == "thinking":
            lines.append("<details><summary>Denken</summary>")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("</details>")
        else:
            continue
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _generate_chat_summary_markdown(sid, info, agent_id, msgs, dump_md):
    """Run one background LLM call to produce a markdown summary of the chat.

    Returns the markdown string, or None on failure. Uses the configured
    `chat_summary_model` (same resolution as the sidebar synopsis), falling back
    to the background default.
    """
    from datetime import datetime as _dt
    # Build the source text from the deterministic dump (cap to keep the
    # prompt bounded; the summary should reflect the whole conversation).
    convo = dump_md[:60000]
    prompt = (
        "Erstelle eine strukturierte Zusammenfassung des folgenden Chat-Verlaufs "
        "als Markdown. Beginne mit einem kurzen Absatz zum Gesamtthema, dann eine "
        "Stichpunktliste der wichtigsten Fragen/Aufgaben und der jeweiligen "
        "Ergebnisse/Antworten. Gib NUR das Markdown aus, ohne Code-Fences.\n\n"
        "--- CHAT-VERLAUF ---\n" + convo
    )
    try:
        configured = (server_config.get("chat_summary_model") or "").strip()
        model = ""
        if configured:
            mcfg = (engine._models_config or {}).get(configured) or {}
            if mcfg.get("enabled", True):
                model = configured
        if not model:
            model = engine._background_model_default()
        if not model:
            return None

        with engine.request_context(current_session_id=sid):
            engine.get_request_context().current_agent = info.get("agent_id") or agent_id
            engine.get_request_context().current_user_id = (info.get("user_id") or "")
            _res = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                system_prompt="Du fasst Chat-Verläufe präzise auf Deutsch zusammen. Gib nur Markdown aus.",
                agent_id=agent_id,
                session_id=sid,
                user_id=(info.get("user_id") or ""),
                project=(info.get("project") or ""),
                purpose="transform",
                cost_purpose="chat_export_summary",
                max_tokens=2000,
            )
        if _res.get("error"):
            return None
        reply = (_res.get("reply") or "").strip()
        if not reply:
            return None
    except Exception:
        return None

    title = (info.get("title") or "").strip() or "Chat"
    header = (
        f"# Zusammenfassung — {title}\n\n"
        f"- **Sitzung:** `{sid}`\n"
        f"- **Modell (Zusammenfassung):** {model}\n"
        f"- **Erstellt:** {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "---\n\n"
    )
    return header + reply + "\n"


def _safe_name(name: str) -> str:
    """Sanitise a filename for inclusion in the zip (no path traversal)."""
    base = os.path.basename(name or "").strip() or "unnamed"
    return "".join(c for c in base if c.isalnum() or c in "._- ()[]") or "unnamed"


def _build_session_inspect_data(sid, info):
    """Assemble the same per-turn audit data the right-panel inspector shows:
    per-turn tool calls (input/output), web sources, payloads, GDPR ids, totals.
    Returns the inspect dict (or a minimal stub on failure)."""
    msgs = ChatDB.load_messages(sid, include_compacted=True)
    interactions = []
    i = 0
    n = len(msgs)
    while i < n:
        m = msgs[i]
        role = m.get("role", "")
        if role in ("user", "human"):
            user_meta = m.get("metadata") or {}
            content_in = _flatten_content(m.get("content", ""))
            # Find the next assistant message for this turn.
            assistant_msg = None
            j = i + 1
            while j < n:
                if msgs[j].get("role") == "assistant":
                    assistant_msg = msgs[j]
                    break
                if msgs[j].get("role") in ("user", "human"):
                    break
                j += 1
            meta = (assistant_msg.get("metadata") or {}) if assistant_msg else {}
            interactions.append({
                "turn": len(interactions) + 1,
                "user": {"content": content_in,
                         "gdpr_mapping_id": user_meta.get("gdpr_mapping_id") or ""},
                "assistant": ({
                    "content": _flatten_content(assistant_msg.get("content", "")),
                    "tokens_in": meta.get("tokens_in", 0),
                    "tokens_out": meta.get("tokens_out", 0),
                    "duration": meta.get("duration", 0),
                    "model": meta.get("model", ""),
                    "cost": meta.get("cost", 0),
                    "tools": meta.get("tools", []),
                    "thinking": meta.get("thinking"),
                    "thinking_level": meta.get("thinking_level"),
                    "request_payloads": meta.get("request_payloads", []),
                    "web_sources": meta.get("web_sources") or [],
                    "citation_validation": meta.get("citation_validation") or {},
                    "auto_route": meta.get("auto_route") or {},
                    "gdpr_mapping_id": meta.get("gdpr_mapping_id") or "",
                } if assistant_msg else None),
            })
            i = (j + 1) if assistant_msg else (i + 1)
        else:
            i += 1
    return {"session_id": sid, "agent": info.get("agent_id") or "main",
            "model": info.get("model") or "", "title": info.get("title") or "",
            "interactions": interactions}


def _render_tool_calls_md(inspect):
    """Readable per-turn tool-call input→output dump (the right-panel tool view)."""
    lines = ["# Tool-Aufrufe (Eingabe / Ausgabe)", ""]
    any_tool = False
    for ix in inspect.get("interactions", []):
        a = ix.get("assistant") or {}
        tools = a.get("tools") or []
        if not tools:
            continue
        any_tool = True
        lines.append(f"## Anfrage {ix.get('turn')}")
        lines.append("")
        for t in tools:
            name = t.get("name", "?")
            lines.append(f"### `{name}`")
            lines.append("")
            lines.append("**Eingabe:**")
            lines.append("```json")
            try:
                lines.append(json.dumps(t.get("args", {}), ensure_ascii=False, indent=2))
            except Exception:
                lines.append(str(t.get("args", "")))
            lines.append("```")
            res = t.get("result", "")
            lines.append("")
            lines.append("**Ausgabe:**")
            lines.append("```")
            lines.append((str(res) if res is not None else "")[:20000])
            lines.append("```")
            lines.append("")
    if not any_tool:
        lines.append("_Keine Tool-Aufrufe in dieser Sitzung._")
    return "\n".join(lines) + "\n"


def _render_references(inspect):
    """References pane = per-turn web sources (Webquellen) + citation validation."""
    refs = []
    for ix in inspect.get("interactions", []):
        a = ix.get("assistant") or {}
        for s in (a.get("web_sources") or []):
            refs.append({"turn": ix.get("turn"), "title": s.get("title", ""),
                         "url": s.get("url", ""), "content": s.get("content", ""),
                         "error": s.get("error")})
    md = ["# Referenzen / Webquellen", ""]
    if not refs:
        md.append("_Keine Webquellen in dieser Sitzung._")
    for r in refs:
        md.append(f"## [{r['turn']}] {r['title'] or r['url']}")
        md.append("")
        md.append(f"- **URL:** {r['url']}")
        if r.get("error"):
            md.append(f"- **Fehler:** {r['error']}")
        md.append("")
        if r.get("content"):
            md.append(r["content"][:20000])
        md.append("")
    return "\n".join(md) + "\n", refs


def _render_statistics(sid, info, inspect):
    """Session statistics: turns, tokens, cost, duration, models, per-tool counts."""
    from datetime import datetime as _dt
    inter = inspect.get("interactions", [])
    cost = engine._cost_tracker.get_session_cost(sid) if getattr(engine, "_cost_tracker", None) else {}
    tokens_in = sum((ix.get("assistant") or {}).get("tokens_in", 0) for ix in inter)
    tokens_out = sum((ix.get("assistant") or {}).get("tokens_out", 0) for ix in inter)
    duration = sum((ix.get("assistant") or {}).get("duration", 0) for ix in inter)
    models = {}
    tool_counts = {}
    for ix in inter:
        a = ix.get("assistant") or {}
        if a.get("model"):
            models[a["model"]] = models.get(a["model"], 0) + 1
        for t in (a.get("tools") or []):
            nm = t.get("name", "?")
            tool_counts[nm] = tool_counts.get(nm, 0) + 1
    stats = {
        "session_id": sid,
        "title": info.get("title") or "",
        "agent": info.get("agent_id") or "main",
        "project": info.get("project") or "",
        "turns": len(inter),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_seconds": round(duration, 1),
        "cost_usd": round(cost.get("cost", 0.0), 4),
        "llm_calls": cost.get("calls", 0),
        "models_used": models,
        "tool_call_counts": tool_counts,
        "exported_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    md = [
        "# Statistik", "",
        f"- **Sitzung:** `{sid}`",
        f"- **Titel:** {stats['title']}",
        f"- **Agent:** {stats['agent']}",
    ]
    if stats["project"]:
        md.append(f"- **Projekt:** {stats['project']}")
    md += [
        f"- **Anfragen (Turns):** {stats['turns']}",
        f"- **Tokens (Eingabe):** {tokens_in:,}",
        f"- **Tokens (Ausgabe):** {tokens_out:,}",
        f"- **Dauer gesamt:** {stats['duration_seconds']} s",
        f"- **Kosten gesamt:** ${stats['cost_usd']}",
        f"- **LLM-Aufrufe:** {stats['llm_calls']}",
        "",
        "## Verwendete Modelle", "",
    ]
    for mdl, c in (models.items() or []):
        md.append(f"- {mdl}: {c}×")
    if not models:
        md.append("_keine_")
    md += ["", "## Tool-Aufrufe nach Typ", ""]
    for nm, c in sorted(tool_counts.items(), key=lambda kv: -kv[1]):
        md.append(f"- `{nm}`: {c}×")
    if not tool_counts:
        md.append("_keine_")
    return "\n".join(md) + "\n", stats


def _enumerate_attachments(sid, msgs):
    """Resolve on-disk attachment files for a session (dedup by path)."""
    seen = set()
    out = []
    for m in msgs:
        for f in ((m.get("metadata") or {}).get("files") or []):
            p = f.get("path") or ""
            if p and p not in seen and os.path.isfile(p):
                seen.add(p)
                out.append((p, f.get("filename") or f.get("name") or os.path.basename(p)))
    # Also sweep the on-disk attachment dir (covers files whose metadata path
    # drifted but the bytes are still present).
    adir = os.path.join("/tmp", "brain-attachments", sid)
    if os.path.isdir(adir):
        for name in os.listdir(adir):
            p = os.path.join(adir, name)
            if os.path.isfile(p) and p not in seen:
                seen.add(p)
                out.append((p, name))
    return out


def _build_chat_bundle(sid, info, progress):
    """Build a complete-chat zip bundle to a temp file. `progress(pct, label)`
    is called as work advances. Returns (temp_zip_path, filename)."""
    import zipfile
    import tempfile
    from datetime import datetime as _dt

    agent_id = info.get("agent_id") or "main"
    root = f"chat-bundle_{sid[:8]}_{_dt.now().strftime('%Y-%m-%d_%H%M%S')}"
    fd, zpath = tempfile.mkstemp(prefix="chat-bundle_", suffix=".zip")
    os.close(fd)

    progress(5, "Nachrichten werden gelesen…")
    msgs = ChatDB.load_messages(sid, include_compacted=True)
    conversation_md = _build_conversation_markdown(sid, info, msgs)

    progress(20, "Audit-Daten werden zusammengestellt…")
    inspect = _build_session_inspect_data(sid, info)
    tool_calls_md = _render_tool_calls_md(inspect)
    references_md, refs = _render_references(inspect)
    statistics_md, stats = _render_statistics(sid, info, inspect)

    progress(40, "Anhänge werden gesammelt…")
    attachments = _enumerate_attachments(sid, msgs)

    progress(55, "Artefakte werden gesammelt…")
    artifacts = ChatDB.get_artifacts(sid) or []

    progress(70, "Hintergrundaufgaben…")
    bg_tasks = []
    try:
        bg_tasks = ChatDB.list_background_tasks(sid) if hasattr(ChatDB, "list_background_tasks") else []
    except Exception:
        bg_tasks = []

    progress(80, "Bundle wird gepackt…")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        def w(rel, data):
            z.writestr(f"{root}/{rel}", data)

        w("conversation.md", conversation_md)
        w("tool-calls.md", tool_calls_md)
        w("references.md", references_md)
        w("references.json", json.dumps(refs, ensure_ascii=False, indent=2))
        w("statistics.md", statistics_md)
        w("statistics.json", json.dumps(stats, ensure_ascii=False, indent=2))
        w("inspect.json", json.dumps(inspect, ensure_ascii=False, indent=2, default=str))
        w("messages.json", json.dumps(msgs, ensure_ascii=False, indent=2, default=str))
        if bg_tasks:
            w("background-tasks.json", json.dumps(bg_tasks, ensure_ascii=False, indent=2, default=str))

        # Attachments (user-uploaded files).
        for p, name in attachments:
            try:
                with open(p, "rb") as fh:
                    z.writestr(f"{root}/attachments/{_safe_name(name)}", fh.read())
            except OSError:
                pass

        # Artifacts (generated files — latest version bytes; disk first, DB fallback).
        for a in artifacts:
            name = _safe_name(a.get("name") or a.get("id"))
            data = None
            dpath = a.get("path") or ""
            if dpath and os.path.isfile(dpath):
                try:
                    with open(dpath, "rb") as fh:
                        data = fh.read()
                except OSError:
                    data = None
            if data is None:
                ver = ChatDB.get_artifact_content(a.get("id"))
                if ver and ver.get("content") is not None:
                    c = ver["content"]
                    data = c if isinstance(c, bytes) else str(c).encode("utf-8")
            if data is not None:
                z.writestr(f"{root}/artifacts/{name}", data)

        # README index.
        readme = _render_bundle_readme(sid, info, stats, len(attachments),
                                       len(artifacts), len(refs), bool(bg_tasks))
        w("README.md", readme)

    progress(95, "Abschluss…")
    return zpath, root + ".zip"


def _render_bundle_readme(sid, info, stats, n_attach, n_artifacts, n_refs, has_bg):
    from datetime import datetime as _dt
    lines = [
        f"# Chat-Bundle — {info.get('title') or 'Chat'}", "",
        f"Vollständiger Export der Sitzung `{sid}`, erstellt am "
        f"{_dt.now().strftime('%Y-%m-%d %H:%M:%S')}.", "",
        "## Inhalt", "",
        "- `conversation.md` — vollständiger Chat-Verlauf (verbatim)",
        "- `tool-calls.md` — Tool-Aufrufe pro Anfrage (Eingabe/Ausgabe)",
        "- `references.md` / `references.json` — Webquellen/Referenzen",
        "- `statistics.md` / `statistics.json` — Statistik (Tokens, Kosten, Modelle, Tools)",
        "- `inspect.json` — vollständige Audit-Daten pro Turn (Payloads, GDPR-IDs)",
        "- `messages.json` — Rohnachrichten inkl. Metadaten",
        f"- `attachments/` — {n_attach} hochgeladene Datei(en)",
        f"- `artifacts/` — {n_artifacts} generierte Datei(en)",
    ]
    if has_bg:
        lines.append("- `background-tasks.json` — Hintergrundaufgaben dieser Sitzung")
    lines += [
        "", "## Kennzahlen", "",
        f"- Anfragen: {stats.get('turns')}",
        f"- Tokens: {stats.get('tokens_in'):,} ein / {stats.get('tokens_out'):,} aus",
        f"- Kosten: ${stats.get('cost_usd')}",
        f"- Webquellen: {n_refs}",
    ]
    return "\n".join(lines) + "\n"


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

    def _handle_active_sessions(self):
        """GET /v1/sessions/active — IDs of sessions with a live chat turn running
        (in-memory `_streaming`). Lightweight live signal for the sidebar/project
        list 'läuft gerade' pills; the client only paints pills on sessions already
        in its access-filtered list, so returning bare IDs leaks nothing."""
        try:
            active = sorted(sessions.streaming_session_ids())
        except Exception:
            active = []
        self._send_json({"active": active})

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
            resp["model"] = session.model or ""
            resp["max_context"] = session.max_context
            resp["total_tokens"] = engine._estimate_conversation_tokens(session.messages)
            resp["summary"] = session.summary or ""
            resp["title"] = session.title or ""
            resp["caveman_mode"] = session.caveman_mode
            resp["save_to_memory"] = int(getattr(session, "save_to_memory", 0) or 0)
            resp["thinking_level"] = getattr(session, "thinking_level", "") or ""
            resp["project"] = session.project or ""
            resp["workflow_run_id"] = getattr(session, "workflow_run_id", "") or ""
            _rmo = getattr(session, "research_mode_override", None)
            resp["research_mode_override"] = (None if _rmo is None else bool(_rmo))
            resp["allow_further_web"] = bool(getattr(session, "allow_further_web", False))
            resp["gdpr_feedback_ask"] = bool(getattr(session, "gdpr_feedback_ask", False))
            resp["gdpr_details_visible"] = bool(getattr(session, "gdpr_details_visible", False))
            resp["web_basket"] = getattr(session, "web_basket", "") or ""
            resp["message_queue"] = getattr(session, "message_queue", "") or ""
            resp["goal_text"] = getattr(session, "goal_text", "") or ""
            resp["goal_status"] = getattr(session, "goal_status", "") or ""
            resp["goal_iteration"] = int(getattr(session, "goal_iteration", 0) or 0)
            resp["goal_max_iterations"] = int(
                getattr(session, "goal_max_iterations", 0) or 0)
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
                resp["model"] = info.get("model", "") or ""
                resp["summary"] = info.get("summary", "")
                resp["title"] = info.get("title", "")
                resp["caveman_mode"] = int(info.get("caveman_mode", 0) or 0)
                resp["save_to_memory"] = int(info.get("save_to_memory", 0) or 0)
                resp["thinking_level"] = info.get("thinking_level", "") or ""
                resp["project"] = info.get("project", "") or ""
                resp["workflow_run_id"] = info.get("workflow_run_id", "") or ""
                _rmo_db = info.get("research_mode_override", None)
                resp["research_mode_override"] = (None if _rmo_db is None
                                                   else bool(_rmo_db))
                resp["allow_further_web"] = bool(info.get("allow_further_web", 0))
                resp["gdpr_feedback_ask"] = bool(info.get("gdpr_feedback_ask", 0))
                resp["gdpr_details_visible"] = bool(info.get("gdpr_details_visible", 0))
                resp["web_basket"] = info.get("web_basket", "") or ""
                resp["message_queue"] = info.get("message_queue", "") or ""
                resp["goal_text"] = info.get("goal_text", "") or ""
                resp["goal_status"] = info.get("goal_status", "") or ""
                resp["goal_iteration"] = int(info.get("goal_iteration", 0) or 0)
                resp["goal_max_iterations"] = int(
                    info.get("goal_max_iterations", 0) or 0)
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

        Cached per Session (in-memory, survives page reloads while the server is
        up): the result is keyed by a cheap conversation signature (message count
        + last message). A repeat call for the same conversation state returns the
        cached text WITHOUT a new LLM call; a new turn changes the signature and
        forces regeneration. `?force=1` bypasses the cache.
        """
        from urllib.parse import urlparse, parse_qs
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
            force = parse_qs(urlparse(self.path).query).get("force", ["0"])[0] in ("1", "true")
            sig = _next_prompt_cache_sig(session)
            cached = getattr(session, "_next_prompt_cache", None)
            if (not force and cached and cached.get("sig") == sig
                    and cached.get("text")):
                self._send_json({
                    "suggestion": cached["text"],
                    "model_used": (cfg.get("model") or session.model),
                    "config": cfg, "cached": True,
                })
                return
            # Set thread-local agent context so LLM call picks up the right config
            with engine.request_context():
                engine.get_request_context().current_agent = engine.AgentConfig(session.agent_id)
                text = engine.generate_next_prompt_suggestion(session)
            # Cache the result against the signature captured BEFORE the call (the
            # conversation didn't change during a read-only suggestion call).
            if text:
                session._next_prompt_cache = {"sig": sig, "text": text}
            self._send_json({
                "suggestion": text,
                "model_used": (cfg.get("model") or session.model),
                "config": cfg, "cached": False,
            })
        except Exception as e:
            self._send_json({"suggestion": None, "error": str(e)}, 500)

    def _handle_session_audio_overview(self, path):
        """POST /v1/sessions/<id>/audio-overview — generate a two-host podcast (.mp3)
        from THIS CHAT's conversation (the chat-podcast button). Synchronous (~50s):
        builds the overview from the transcript, writes .mp3 + .md into the session
        artifact folder, returns {ok, artifact_id, audio_file, script_file,
        spoken_lines}. Body: {length?: short|std|long, focus?: str}."""
        import os as _os
        parts = path.split("/")
        sid = parts[3]
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"ok": False, "error": "session_not_found"}, 404)
            return
        body = self._read_json() or {}
        length = (body.get("length") or "std").strip()
        if length not in ("short", "std", "long"):
            length = "std"
        focus = (body.get("focus") or "").strip()
        force = bool(body.get("force"))
        agent_id = session.agent_id
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        try:
            from engine import audio_overview
            import hashlib

            # Cache key = the exact material the podcast is built from (transcript
            # corpus) + the knobs that change the output. If nothing changed since
            # the last podcast AND its artifact still exists, reuse it instead of
            # paying for a fresh script-gen + per-line TTS render. `force` bypasses.
            corpus, _turns = audio_overview._chat_corpus(sid)
            content_hash = hashlib.sha256(
                f"{length}|{focus}|{corpus}".encode("utf-8")).hexdigest()
            if not force:
                cached = ChatDB.get_chat_audio_overview(sid)
                if cached and cached.get("content_hash") == content_hash:
                    art = ChatDB.get_artifact(cached.get("artifact_id") or "")
                    if art:
                        self._send_json({
                            "ok": True, "cached": True,
                            "artifact_id": cached.get("artifact_id", ""),
                            "audio_file": cached.get("audio_file", ""),
                            "script_file": cached.get("script_file", ""),
                            "spoken_lines": cached.get("spoken_lines", 0),
                            "cost": cached.get("cost", 0),
                        })
                        return

            folder = engine._get_artifact_session_folder(sid)
            out_dir = _os.path.join(engine.AGENTS_DIR, agent_id, "artifacts", folder)
            # Name the files after the chat's content (its title), not a hex id —
            # "Podcast — Wetter in Berlin.mp3" reads far better than
            # "audio_overview-3f9a1b2c.mp3". Keep a short uuid suffix so repeated
            # podcasts from the same chat don't overwrite each other.
            seed = (getattr(session, "title", "") or getattr(session, "summary", "") or "").strip()
            basename = audio_overview.make_basename(seed)
            # Run inside a request context so background_call resolves agent config.
            with engine.request_context():
                engine.get_request_context().current_agent = engine.AgentConfig(agent_id)
                engine.get_request_context().current_session_id = sid
                res = audio_overview.generate_from_chat(
                    agent_id=agent_id, session_id=sid, out_dir=out_dir,
                    opts={"length": length, "focus": focus},
                    user_id=user["id"], basename=basename)
            if not res.get("ok"):
                self._send_json({"ok": False, "error": res.get("error", "generation failed")}, 400)
                return
            # Register both files directly (returns the artifact_id — the same path
            # the project worker uses; the generic file-write hook is unreliable
            # outside the chat-worker thread). The mp3's id is what the button opens.
            from engine import output_gen
            artifact_id = ""
            for p in (res.get("script_path"), res.get("mp3_path")):
                if not p:
                    continue
                aid = output_gen._register_output_artifact(
                    sid, agent_id, p, _os.path.basename(p)) or ""
                if p == res.get("mp3_path"):
                    artifact_id = aid
            audio_file = _os.path.basename(res["mp3_path"])
            script_file = _os.path.basename(res["script_path"])
            cost = res.get("cost", 0)
            # Cache so a re-click on the unchanged chat replays instead of rebuilding.
            ChatDB.set_chat_audio_overview(sid, {
                "content_hash": content_hash, "artifact_id": artifact_id,
                "audio_file": audio_file, "script_file": script_file,
                "spoken_lines": res.get("lines", 0), "cost": cost,
            })
            self._send_json({
                "ok": True,
                "artifact_id": artifact_id,
                "audio_file": audio_file,
                "script_file": script_file,
                "spoken_lines": res.get("lines", 0),
                "cost": cost,
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

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
                # (Memory-summary injection retired with MemoryStore — the wiki
                # is the agent's memory now. Field kept empty for inspector shape.)
            except Exception:
                pass

        # Round-0 preamble (artifact-folder note): prepended into the first
        # user message's content for the wire, stashed verbatim in that row's
        # metadata.preamble. Surface it as its own inspector card (like the
        # system prompt) rather than in the chat view — it's plumbing, not
        # conversation. Empty when the session never got one.
        preamble = ""
        for _m in msgs:
            if _m.get("role") == "user":
                _pre = (_m.get("metadata") or {}).get("preamble")
                if isinstance(_pre, str) and _pre:
                    preamble = _pre
                break  # only the first user message can carry it
        preamble_tokens = len(preamble) // 4 if preamble else 0

        # Build interaction pairs: user message + assistant response.
        # metadata.cost stored on each assistant message is the *cumulative* session
        # cost as of that turn (snapshot of get_session_cost), so per-turn cost is
        # the delta against the previous turn.
        interactions = []
        prev_cum_cost = 0.0
        prev_cum_cost_list = 0.0
        from engine.quotas import _get_cost_rate as _rate
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
                # Peel the round-0 preamble off the displayed turn text — it has
                # its own inspector card. (It stays in the wire content; this is
                # display-only.)
                _u_pre = user_meta.get("preamble")
                if (isinstance(_u_pre, str) and _u_pre and isinstance(content_in, str)
                        and content_in.startswith(_u_pre)):
                    content_in = content_in[len(_u_pre):].lstrip("\n")
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
                # API-Listenpreis pro Turn: meta.cost_list ist wie meta.cost der
                # KUMULATIVE Sitzungsstand am Turn-Ende → Delta. Alt-Turns ohne
                # das Feld: Liste == real (kein Flat-Modell-Traffic erfasst).
                _cum_list_raw = meta.get("cost_list") if assistant_msg else None
                cum_cost_list = float(_cum_list_raw) if _cum_list_raw is not None else (
                    prev_cum_cost_list + turn_cost)
                turn_cost_list = max(0.0, cum_cost_list - prev_cum_cost_list)
                # Cache-Ersparnis pro Turn: gecachte Tokens × (Eingabe- minus
                # Cache-Tarif) zum Tarif des Turn-Modells.
                _cr_t = int(meta.get("cache_read_tokens", 0) or 0) if assistant_msg else 0
                if _cr_t:
                    _r_t = _rate(meta.get("model") or (session.model if session else ""))
                    turn_cache_savings = _cr_t / 1_000_000 * (
                        _r_t.get("input", 0.0) - _r_t.get("cache_read", 0.0))
                else:
                    turn_cache_savings = 0.0
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
                        "cache_read_tokens": meta.get("cache_read_tokens", 0),
                        "tokens_total": meta.get("tokens", 0),
                        "duration": meta.get("duration", 0),
                        "model": meta.get("model", ""),
                        "cost": round(turn_cost, 4),
                        "cost_list": round(turn_cost_list, 4),
                        "cache_savings": round(max(0.0, turn_cache_savings), 4),
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
                        # Manual web-search: structured per-source records
                        # [{title,url,content,error}] this turn fetched
                        # (ephemeral on the wire, recorded here for audit). Lets
                        # the inspector show each source's FULL content per turn
                        # — distinct fetches across re-sends.
                        "web_sources": meta.get("web_sources") or [],
                    } if assistant_msg else None,
                    "compacted": bool(m.get("compacted")),
                })
                if assistant_msg:
                    prev_cum_cost = cum_cost
                    prev_cum_cost_list = cum_cost_list
                i = (j + 1) if assistant_msg else (i + 1)
            else:
                i += 1

        # Totals — total session cost is the latest cumulative snapshot, not a sum
        # of (already-cumulative) per-message values.
        total_in = sum((ix["assistant"] or {}).get("tokens_in", 0) for ix in interactions if ix.get("assistant"))
        total_out = sum((ix["assistant"] or {}).get("tokens_out", 0) for ix in interactions if ix.get("assistant"))
        total_cached = sum((ix["assistant"] or {}).get("cache_read_tokens", 0) for ix in interactions if ix.get("assistant"))
        total_duration = sum((ix["assistant"] or {}).get("duration", 0) for ix in interactions if ix.get("assistant"))
        total_cost = prev_cum_cost
        # Cache-hit ratio = cached / (full-price in + cached) — share of prompt
        # tokens billed at the discounted ~0.1x rate across the whole session.
        _prompt_total = total_in + total_cached
        cache_hit_pct = round(100.0 * total_cached / _prompt_total, 1) if _prompt_total else 0.0
        # Cached-token COST + savings, summed per turn at each turn's model rate
        # (cache_read rate = per-model cost_cache_read, default 0.1x input). Savings
        # = what those cached tokens WOULD have cost at the full input rate minus
        # what they actually cost at the cache_read rate.
        cached_cost = 0.0
        cached_savings = 0.0
        for _ix in interactions:
            _a = _ix.get("assistant") or {}
            _cr = _a.get("cache_read_tokens", 0) or 0
            if not _cr:
                continue
            _r = _rate(_a.get("model") or (session.model if session else ""))
            cached_cost += _cr / 1_000_000 * _r.get("cache_read", 0.0)
            cached_savings += _cr / 1_000_000 * (_r.get("input", 0.0) - _r.get("cache_read", 0.0))

        self._send_json({
            "session_id": sid,
            "agent": session.agent_id if session else "",
            "model": session.model if session else "",
            "max_context": session.max_context if session else 0,
            "system_prompt": {"content": system_prompt, "tokens_est": system_tokens},
            "preamble": {"content": preamble, "tokens_est": preamble_tokens},
            "memory_summary": {"content": memory_summary, "tokens_est": memory_tokens},
            "interactions": interactions,
            "totals": {
                "turns": len(interactions),
                "tokens_in": total_in,
                "tokens_out": total_out,
                "cache_read_tokens": total_cached,
                "cache_hit_pct": cache_hit_pct,
                "cached_cost": round(cached_cost, 4),
                "cached_savings": round(cached_savings, 4),
                "duration": round(total_duration, 2),
                "cost": round(total_cost, 4),
                "cost_list": round(prev_cum_cost_list, 4),
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

    def _handle_session_pii_history_detail(self, path):
        """GET /v1/sessions/<id>/pii-history-detail — per-finding PII scan over
        the session WITH source attribution.

        Unlike pii-history-summary (which joins all text and returns only label
        counts), this scans each message and each attachment SEPARATELY so every
        finding carries where it came from (chat history vs which file). Values
        are MASKED server-side — cleartext never crosses the wire here (the
        history modal reads cleartext only from the user's own prior decisions
        via /v1/gdpr/decisions). Feeds web/js/panels_gdpr.js openPiiHistoryModal.

        Each finding (and the top-level `decision_history` map, keyed by
        value_hash) carries the full chronological 'who decided what when'
        history with resolved display names — the SAME shape the pre-send modal
        reuses for its 'already seen' findings, so both modals look identical.

        Returns: {session_id, findings: [{rule_id, label, category, action,
        confidence, masked, value_hash, source, source_label, history:[...]}],
        decision_history: {value_hash: [{turn_action, false_positive, fake_value,
        by, by_id, at}]}, counts, finding_count, worst_action, truncated}
        """
        sid = path.split("/")[3]
        if self._session_access_check(sid) is None:
            return
        # Decision history is INDEPENDENT of the scanner toggle — resolve it
        # first so even a disabled-scanner response carries the prior decisions
        # (the history modal merges them in; the badge shows the button).
        decision_history = _pii_decision_history_with_names(sid)
        cfg = engine._get_gdpr_scanner_config()
        if not cfg.get("enabled", True):
            self._send_json({
                "session_id": sid, "findings": [], "counts": {},
                "finding_count": 0, "worst_action": "ignore",
                "disabled": True, "truncated": False,
                "decision_history": decision_history,
            })
            return
        try:
            msgs = ChatDB.load_messages(sid, include_compacted=True)
        except Exception as e:
            self._send_json({"error": f"db error: {e}"}, 500)
            return

        import hashlib

        def _mask(v):
            # Mirror web/js/panels_gdpr.js `mask` so client + server look alike.
            if not v:
                return ""
            if len(v) <= 6:
                return v[0] + ("•" * max(0, len(v) - 1))
            return v[:2] + ("•" * (len(v) - 4)) + v[-2:]

        MAX = 1000  # generous cap; the modal collapses groups so this is plenty
        findings = []
        counts = {}
        worst = "ignore"
        seen = set()  # (rule_id, value, source) dedupe
        truncated = False

        def _scan_one(text, source, source_label):
            nonlocal worst, truncated
            if not text or not isinstance(text, str):
                return
            try:
                raw = engine._pii_scan_text(text, cfg=cfg, max_findings=200)
            except Exception as e:
                print(f"[pii_history_detail] scan failed: {e}", flush=True)
                return
            for f in raw:
                if len(findings) >= MAX:
                    truncated = True
                    return
                start, end = f.get("start"), f.get("end")
                value = (text[start:end] if isinstance(start, int)
                         and isinstance(end, int) else (f.get("value") or ""))
                if not value:
                    continue
                rid = f.get("rule_id") or ""
                key = (rid, value, source)
                if key in seen:
                    continue
                seen.add(key)
                label = f.get("label") or rid or "?"
                action = f.get("action") or "warn"
                if action == "block":
                    worst = "block"
                elif action == "warn" and worst != "block":
                    worst = "warn"
                counts[label] = counts.get(label, 0) + 1
                # value_hash matches ChatDB.record_pii_decisions (sha256(rule|value))
                vh = hashlib.sha256(f"{rid}|{value}".encode("utf-8")).hexdigest()
                findings.append({
                    "rule_id": rid,
                    "label": label,
                    "category": f.get("category") or "",
                    "action": action,
                    "confidence": f.get("confidence"),
                    "masked": _mask(value),
                    "value_hash": vh,
                    "source": source,
                    "source_label": source_label,
                    "history": decision_history.get(vh, []),
                })

        for idx, m in enumerate(msgs or []):
            role = m.get("role") or ""
            if role not in ("user", "human", "assistant"):
                continue
            # Message text → source 'history' (with a human label per role).
            parts = []
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
            who = "Sie" if role in ("user", "human") else "Assistent"
            _scan_one("\n".join(parts), "history",
                      f"Chat-Verlauf ({who})")
            # Each attachment scanned separately → source 'file:<name>'.
            meta = m.get("metadata") or {}
            for fobj in (meta.get("files") or []):
                if not isinstance(fobj, dict):
                    continue
                fname = (fobj.get("name") or fobj.get("filename")
                         or fobj.get("path") or "Anhang")
                bits = [fobj.get(k) for k in ("name", "filename", "path",
                                              "mime", "type") if fobj.get(k)]
                _scan_one(" ".join(str(b) for b in bits),
                          f"file:{fname}", f"Anhang: {fname}")

        self._send_json({
            "session_id": sid,
            "findings": findings,
            "counts": counts,
            "finding_count": len(findings),
            "worst_action": worst,
            "truncated": truncated,
            "decision_history": decision_history,
        })

    def _handle_session_pii_decisions_view(self, path):
        """GET /v1/sessions/<id>/pii-decisions-view — DB-ONLY modal data.

        One row per DECIDED value (the latest decision = current status), built
        purely from the persisted pii_decisions ledger — NO live re-scan. This
        is what the GDPR history modal renders: a live scan produced phantom
        "open" duplicates because its string form for a value (e.g. a phone
        number formatted with spaces in the assistant reply) differed from the
        stored decision's string, so the value_hash join missed and the value
        showed twice (once decided, once "open"). Reading only the ledger means
        every row has a decision by definition — no phantom-open, no dupes.

        Returns: {session_id, items: [{rule_id, label, category, masked,
        value_hash, source, source_label, status, false_positive, turn_action,
        fake_value, history:[{turn_action,false_positive,fake_value,by,by_id,at}]}],
        counts: {status: N}}
        """
        sid = path.split("/")[3]
        if self._session_access_check(sid) is None:
            return
        history = _pii_decision_history_with_names(sid)  # value_hash → trail (named)
        try:
            from server_lib.db import ChatDB as _ChatDB
            raw = _ChatDB.get_session_pii_decision_history(sid)  # value_hash → events
        except Exception:
            raw = {}

        def _mask(v):
            if not v:
                return ""
            if len(v) <= 6:
                return v[0] + ("•" * max(0, len(v) - 1))
            return v[:2] + ("•" * (len(v) - 4)) + v[-2:]

        def _status_of(ev):
            if ev.get("false_positive"):
                return "fp"
            a = ev.get("turn_action") or ""
            if a == "anonymise":
                return "anon"
            if a in ("local", "local_model"):
                return "local"
            if a in ("send", "continue"):
                return "accepted"
            return "open"

        items = []
        counts = {}
        for vh, events in (raw or {}).items():
            if not events:
                continue
            latest = events[-1]            # newest decision = current status
            rid = latest.get("rule_id") or ""
            value = latest.get("value") or ""
            raw_src = latest.get("source") or ""
            status = _status_of(latest)
            counts[status] = counts.get(status, 0) + 1
            # NORMALISE the source key so the client groups by it correctly.
            # Decisions are persisted with inconsistent source values for the
            # SAME origin: the pre-send dialog writes 'message', the auto-
            # anonymise path writes '' — both mean "from the chat text". Collapse
            # every non-file source to the canonical 'history' so they form ONE
            # "Chat-Verlauf" group instead of two (the bug in the screenshot).
            if raw_src.startswith("file:"):
                src = raw_src
                src_label = "Anhang: " + raw_src[5:]
            else:
                src = "history"
                src_label = "Chat-Verlauf"
            # Resolve a human label server-side from the rule catalog (client
            # also has gdprRuleLabel, but sending it keeps the row self-contained).
            label = (engine.PII_RULE_LABELS.get(rid) if hasattr(engine, "PII_RULE_LABELS") else None) or rid
            items.append({
                "rule_id": rid,
                "label": label,
                "category": (engine.PII_RULE_CATEGORIES.get(rid, "")
                             if hasattr(engine, "PII_RULE_CATEGORIES") else ""),
                # It's the user's OWN chat — show the real value (the cleartext
                # already lives in the visible chat history). `masked` kept for
                # any caller that prefers it.
                "value": value,
                "masked": _mask(value),
                "value_hash": vh,
                "source": src,
                "source_label": src_label,
                "status": status,
                "false_positive": bool(latest.get("false_positive")),
                "turn_action": latest.get("turn_action") or "",
                "fake_value": latest.get("fake_value") or "",
                "history": history.get(vh, []),
            })
        self._send_json({
            "session_id": sid, "items": items, "counts": counts,
            "item_count": len(items),
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

        # (Removed v9.109.0: the MemoryStore-backed chat-transcript semantic
        # search — MemoryStore is retired. Session search now uses the SQL
        # title/summary match below; full-text recall lives in the wiki/MemPalace.)

        # SQLite search on title + summary
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

    def _handle_export_session(self):
        """POST /v1/sessions/export {session_id, kind: 'summary'|'dump'}

        Writes a markdown file into the session's artifact folder and registers
        it as an artifact (shows in the right-hand panel). `dump` is a pure,
        deterministic transform of the persisted conversation — no LLM. `summary`
        runs one background call through the configured `chat_summary_model`.
        """
        body = self._read_json()
        sid = body.get("session_id", "")
        kind = body.get("kind", "")
        if kind not in ("summary", "dump"):
            self._send_json({"error": "kind must be 'summary' or 'dump'"}, 400); return
        info = self._session_access_check(sid)
        if info is None:
            return

        agent_id = info.get("agent_id") or "main"
        msgs = ChatDB.load_messages(sid)
        if not msgs:
            self._send_json({"error": "session has no messages"}, 400); return

        dump_md = _build_conversation_markdown(sid, info, msgs)

        if kind == "dump":
            content = dump_md
            base = "chat-dump"
        else:
            content = _generate_chat_summary_markdown(sid, info, agent_id, msgs, dump_md)
            if content is None:
                self._send_json({"error": "summary generation failed"}, 502); return
            base = "chat-summary"

        from datetime import datetime as _dt
        fname = f"{base}_{sid[:8]}_{_dt.now().strftime('%Y-%m-%d_%H%M%S')}.md"

        # Resolve the session's artifact folder + write the file, then register
        # it as an artifact under the session's request context so
        # `_register_artifact_version` can resolve the session id (same pattern
        # as _backfill_orphan_artifacts).
        artifact_id = None
        try:
            with engine.request_context(current_session_id=sid):
                folder = engine._get_artifact_session_folder(sid)
                artifact_dir = os.path.join(
                    engine.AGENTS_DIR, agent_id, "artifacts", folder)
                os.makedirs(artifact_dir, exist_ok=True)
                fpath = os.path.join(artifact_dir, fname)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                res = engine._register_artifact_version(fpath, "created", agent_id)
                if res:
                    artifact_id = res[0]
        except Exception as e:
            self._send_json({"error": f"could not save artifact: {e}"}, 500); return

        # Nudge any attached client to refresh its artifacts panel live.
        try:
            s = sessions.get(sid)
            if s and getattr(s, "live_stream", None) and not s.live_stream.done:
                s.live_stream.emit("artifact_updated", {
                    "path": fpath, "name": fname,
                    "size": len(content.encode("utf-8")),
                    "action": "created", "artifact_id": artifact_id,
                    "artifact_role": "output", "artifact_type": "markdown",
                })
        except Exception:
            pass

        self._send_json({"status": "saved", "session_id": sid,
                         "name": fname, "artifact_id": artifact_id})

    def _handle_export_bundle(self):
        """POST /v1/sessions/export-bundle {session_id} — SSE.

        Builds a complete-chat zip (history, statistics, attachments, artifacts,
        tool-call I/O, references — everything the right panel shows) and streams
        real progress events while doing so. On completion emits a `done` event
        carrying a one-time download token; the zip itself is fetched via
        GET /v1/sessions/export-bundle/download?token=… and is NOT stored as an
        artifact.
        """
        body = self._read_json()
        sid = body.get("session_id", "")
        info = self._session_access_check(sid)
        if info is None:
            return

        # Open the SSE stream.
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
        except OSError:
            return

        client_gone = threading.Event()

        def emit(ev, data):
            try:
                self.wfile.write(encode_sse(ev, data))
                self.wfile.flush()
            except (OSError, BrokenPipeError):
                client_gone.set()

        def progress(pct, label):
            emit("progress", {"percent": pct, "stage": label})

        try:
            zpath, filename = _build_chat_bundle(sid, info, progress)
        except Exception as e:
            emit("error", {"message": f"Bundle fehlgeschlagen: {e}"})
            return

        if client_gone.is_set():
            try:
                os.unlink(zpath)
            except OSError:
                pass
            return

        try:
            size = os.path.getsize(zpath)
        except OSError:
            size = 0
        token = _bundle_register(zpath, filename)
        emit("progress", {"percent": 100, "stage": "Fertig"})
        emit("done", {"token": token, "filename": filename, "size": size})

    def _handle_export_bundle_download(self):
        """GET /v1/sessions/export-bundle/download?token=… — serve the built zip
        once, then delete it. Token is single-use."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        token = qs.get("token", [""])[0]
        with _bundle_lock:
            entry = _bundle_downloads.pop(token, None)
        if not entry:
            self._send_json({"error": "invalid or expired token"}, 404)
            return
        zpath = entry["path"]
        filename = entry["filename"]
        try:
            with open(zpath, "rb") as f:
                data = f.read()
        except OSError:
            self._send_json({"error": "bundle file gone"}, 404)
            return
        finally:
            try:
                os.unlink(zpath)
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (OSError, BrokenPipeError):
            pass

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
                # memorize — build (or re-version) a WIKI PAGE from the selected
                # turns in the background. The page mirrors to MemPalace, so the
                # chat becomes searchable via the wiki (the wiki-model replacement
                # for the old direct-to-wing _memorize_mempalace_turns write).
                def _do_mem():
                    try:
                        from engine import wiki_store as _wiki
                        page = _wiki.wiki_from_chat(sid, resolved)
                        if page:
                            print(f"[wiki-from-chat] {sid[:8]} → page {page['id']} "
                                  f"v{page.get('current_version')}")
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        print(f"[wiki-from-chat] bg error for {sid[:8]}: {e}")
                threading.Thread(target=_do_mem, daemon=True,
                                 name=f"wiki-mem-{sid[:8]}").start()
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
        elif action == "thinking_level":
            lvl = str(body.get("level", "") or "").lower().strip()
            if lvl not in ("", "none", "low", "medium", "high"):
                lvl = ""
            ChatDB.update_session_thinking_level(sid, lvl)
            s = sessions.get(sid)
            if s:
                s.thinking_level = lvl
            self._send_json({"status": "ok", "thinking_level": lvl, "session_id": sid})
        elif action == "allow_further_web":
            allow = bool(body.get("value"))
            ChatDB.update_session_allow_further_web(sid, allow)
            s = sessions.get(sid)
            if s:
                s.allow_further_web = allow
            self._send_json({"status": "ok", "allow_further_web": allow, "session_id": sid})
        elif action == "gdpr_feedback_ask":
            # Sticky opt-in for the post-turn GDPR feedback modal. Set on by the
            # pre-send modal checkbox; off when the user unticks "Frag mich
            # weiter" in the feedback modal.
            ask = bool(body.get("value"))
            ChatDB.update_session_gdpr_feedback_ask(sid, ask)
            s = sessions.get(sid)
            if s:
                s.gdpr_feedback_ask = ask
            self._send_json({"status": "ok", "gdpr_feedback_ask": ask, "session_id": sid})
        elif action == "gdpr_details_visible":
            # Per-session "Datenschutz-Details sichtbar" toggle (shield detail
            # switch in the composer). Persisted per chat so reopening the chat
            # restores the GDPR mark overlays + detail block visibility.
            vis = bool(body.get("value"))
            ChatDB.update_session_gdpr_details_visible(sid, vis)
            s = sessions.peek(sid)
            if s:
                s.gdpr_details_visible = vis
            self._send_json({"status": "ok", "gdpr_details_visible": vis, "session_id": sid})
        elif action == "web_basket":
            # Persist the per-session Websuche basket. value is the basket list
            # (array of {url,title,snippet,query,enabled}); we store it as JSON.
            val = body.get("value")
            try:
                basket_json = json.dumps(val if isinstance(val, list) else [])
            except (TypeError, ValueError):
                basket_json = "[]"
            ChatDB.update_session_web_basket(sid, basket_json)
            s = sessions.get(sid)
            if s:
                s.web_basket = basket_json
            self._send_json({"status": "ok", "session_id": sid})
        elif action == "message_queue":
            # Persist the per-session message queue. value is the queue list
            # (array of {id,text}); stored as JSON. The client owns ordering /
            # editing / removal — this just persists the current state so a
            # reload or reconnect restores it.
            val = body.get("value")
            try:
                queue_json = json.dumps(val if isinstance(val, list) else [])
            except (TypeError, ValueError):
                queue_json = "[]"
            ChatDB.update_session_message_queue(sid, queue_json)
            s = sessions.get(sid)
            if s:
                s.message_queue = queue_json
            self._send_json({"status": "ok", "session_id": sid})
        elif action == "goal":
            # Goal-Modus: set/clear the per-session goal. Non-empty goal arms
            # (or re-arms a fulfilled/capped goal to) 'active' and resets the
            # iteration counter; empty goal clears everything.
            from engine.goal_judge import GOAL_ITER_HARD_CAP
            goal = str(body.get("goal", "") or "").strip()
            gmax = 0
            try:
                gmax = max(0, min(GOAL_ITER_HARD_CAP,
                                  int(body.get("goal_max_iterations", 0) or 0)))
            except (TypeError, ValueError):
                gmax = 0
            status = "active" if goal else ""
            ChatDB.update_session_goal(sid, text=goal, status=status,
                                       iteration=0, max_iterations=gmax)
            s = sessions.get(sid)
            if s:
                with s.lock:
                    s.goal_text = goal
                    s.goal_status = status
                    s.goal_iteration = 0
                    s.goal_max_iterations = gmax
            self._send_json({"status": "ok", "session_id": sid,
                             "goal_text": goal, "goal_status": status,
                             "goal_max_iterations": gmax})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)
