"""Wiki page generation + media — Phase 5.

Reuses the Studio/audio_overview generators against WIKI page content (a page,
or a page + its subtree) instead of a project's MemPalace wing:
  - generate_summary  → an LLM summary saved as a CHILD wiki page.
  - generate_podcast  → a two-host dialogue (LLM) rendered to MP3 (reusing
                        audio_overview's TTS stitch), saved as an artifact and
                        linked from a CHILD wiki page.
  - save_media        → store an uploaded image/audio/video as an artifact under
                        the wiki page's synthetic session; returns a markdown
                        snippet the editor inserts.

Read-aloud is pure client-side (reuses /v1/translate/tts), so it lives in the UI.
"""
import os
import uuid

from engine.context import get_request_context


def _gather_page_text(page_id, include_children=False):
    """The page's markdown, optionally concatenated with its descendants
    (depth-first), each under its title. Access-checked per page."""
    from engine import wiki_store
    from server_lib.db import ChatDB
    root = wiki_store.get_page(page_id)   # raises/None on no access
    if not root:
        return None, None
    title = root.get("title") or "Wiki"
    blocks = [f"# {title}\n\n{root.get('body_md', '')}"]
    if include_children:
        # Walk the subtree using the owner's full page set.
        rc = get_request_context()
        all_rows = ChatDB.list_wiki_pages_for_user(
            rc.current_user_id or "", list(rc.current_team_ids or []))
        by_parent = {}
        for r in all_rows:
            by_parent.setdefault(r.get("parent_id") or "", []).append(r)
        seen = set()

        def walk(pid):
            for child in by_parent.get(pid, []):
                if child["id"] in seen:
                    continue
                seen.add(child["id"])
                blocks.append(f"## {child.get('title', '')}\n\n{child.get('body_md', '')}")
                walk(child["id"])
        walk(page_id)
    return title, "\n\n---\n\n".join(blocks)[:24000]


def _bg_model():
    import brain as _brain
    try:
        sc = _brain._server_config() or {}
        m = (sc.get("chat_summary_model") or "").strip()
        if m and _brain._is_model_available(m):
            return m
    except Exception:
        pass
    return _brain._background_model_default()


def generate_summary(page_id, include_children=False):
    """Summarize a page (or subtree) into a new CHILD wiki page. Returns the new
    page, or {'error': ...}."""
    import brain as _brain
    from handlers import sidecar_proxy
    from engine import wiki_store

    title, text = _gather_page_text(page_id, include_children)
    if not text:
        return {"error": "page not found or empty"}
    model = _bg_model()
    if not model:
        return {"error": "no background model available"}
    rc = get_request_context()
    sys_p = (
        "Summarize the following wiki content into a concise, well-structured "
        "markdown briefing: a short overview paragraph, then the key points as "
        "bullets, then any decisions/open questions. Keep the source language. "
        "Return ONLY the markdown body (no title line, no code fences).")
    out = sidecar_proxy.background_call(
        messages=[{"role": "user", "content": text}],
        model=model, system_prompt=sys_p, purpose="transform",
        cost_purpose="wiki", max_rounds=1,
        user_id=rc.current_user_id or None, session_id=f"wiki-sum-{page_id[:8]}")
    if not isinstance(out, dict) or out.get("error"):
        return {"error": "summary generation failed: " + str(out.get("error") if isinstance(out, dict) else out)}
    body = (out.get("reply") or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3].rstrip()
    if not body:
        return {"error": "empty summary"}
    parent = wiki_store.get_page(page_id)
    page = wiki_store.create_page(
        scope=parent.get("scope", "user"),
        title=f"Zusammenfassung: {title}",
        body_md=body, parent_id=page_id,
        project_id=parent.get("project_id", ""),
        team_id=parent.get("team_id", ""),
        source="generated")
    return page or {"error": "could not save summary page"}


def generate_podcast(page_id, include_children=False):
    """Generate a two-host audio overview of a page (or subtree): LLM dialogue
    script → MP3 (reusing audio_overview's TTS stitch) → artifact, linked from a
    new CHILD wiki page. Returns the new page, or {'error': ...}."""
    import brain as _brain
    from handlers import sidecar_proxy
    from engine import wiki_store, audio_overview, output_gen

    title, text = _gather_page_text(page_id, include_children)
    if not text:
        return {"error": "page not found or empty"}
    model = _bg_model()
    if not model:
        return {"error": "no background model available"}
    rc = get_request_context()
    user_id = rc.current_user_id or ""

    # 1. Dialogue script (reuse audio_overview's prompt builder + parser).
    lang = "de"
    try:
        lang = audio_overview._detect_corpus_lang(text) or "de"
    except Exception:
        pass
    script_prompt = audio_overview._build_script_prompt(
        text, focus="", length="std", audience="",
        source_label="WIKI CONTENT", lang=lang)
    out = sidecar_proxy.background_call(
        messages=[{"role": "user", "content": script_prompt}],
        model=model, system_prompt="", purpose="transform",
        cost_purpose="wiki", max_rounds=1,
        user_id=user_id or None, session_id=f"wiki-pod-{page_id[:8]}")
    if not isinstance(out, dict) or out.get("error"):
        return {"error": "script generation failed"}
    lines = audio_overview.parse_dialogue(out.get("reply") or "")
    if not lines:
        return {"error": "empty dialogue script"}

    # 2. Render to MP3 (reuse the TTS stitch + voice picker).
    voice_a, voice_b = audio_overview._voices_for_lang(lang)
    mp3, rendered, chars = audio_overview._stitch(lines, voice_a, voice_b)
    if not mp3:
        return {"error": "TTS produced no audio"}

    # 3. Save the MP3 as an artifact under the wiki page's synthetic session.
    syn_session = f"wiki-{page_id}"
    agent_id = "main"
    parent = wiki_store.get_page(page_id)
    artifact_id = ""
    try:
        from engine.tool_exec import _get_artifact_session_folder
        folder = _get_artifact_session_folder(syn_session)
        outdir = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
        os.makedirs(outdir, exist_ok=True)
        fname = f"podcast-{uuid.uuid4().hex[:8]}.mp3"
        fpath = os.path.join(outdir, fname)
        with open(fpath, "wb") as f:
            f.write(mp3)
        artifact_id = output_gen._register_output_artifact(syn_session, agent_id, fpath, fname) or ""
    except Exception as e:
        return {"error": f"could not save audio: {e}"}

    try:
        audio_overview._log_tts_cost(chars, session_id=syn_session,
                                     user_id=user_id, agent_id=agent_id,
                                     purpose="wiki")
    except Exception:
        pass

    # 4. Child wiki page linking the audio artifact (rendered as <audio> by the UI).
    body = (f"🎧 **Podcast** ({rendered} Zeilen)\n\n"
            f"[[audio:{artifact_id}]]\n\n_Automatisch erzeugte Audio-Übersicht._")
    page = wiki_store.create_page(
        scope=parent.get("scope", "user"),
        title=f"Podcast: {title}", body_md=body, parent_id=page_id,
        project_id=parent.get("project_id", ""),
        team_id=parent.get("team_id", ""), source="generated")
    if not page:
        return {"error": "could not save podcast page"}
    page["artifact_id"] = artifact_id
    return page


# Media kinds we accept for embeds, by extension → markdown form.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}


def save_media(page_id, filename, data_bytes):
    """Store uploaded media as an artifact under the page's synthetic session.
    Returns {artifact_id, kind, snippet} — `snippet` is the markdown to insert
    (an [[image|audio|video:<artifact_id>]] token the UI resolves to a real
    <img>/<audio>/<video> via authed blob-fetch). Access-checked via the page."""
    import brain as _brain
    from engine import wiki_store, output_gen
    page = wiki_store.get_page(page_id)   # access check
    if not page:
        return {"error": "page not found"}
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _IMAGE_EXTS:
        kind = "image"
    elif ext in _AUDIO_EXTS:
        kind = "audio"
    elif ext in _VIDEO_EXTS:
        kind = "video"
    else:
        return {"error": f"unsupported media type '{ext}'"}
    syn_session = f"wiki-{page_id}"
    agent_id = "main"
    rc = get_request_context()
    try:
        from engine.tool_exec import _get_artifact_session_folder
        folder = _get_artifact_session_folder(syn_session)
        outdir = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
        os.makedirs(outdir, exist_ok=True)
        safe = os.path.basename(filename) or f"media{ext}"
        safe = f"{uuid.uuid4().hex[:8]}-{safe}"
        fpath = os.path.join(outdir, safe)
        with open(fpath, "wb") as f:
            f.write(data_bytes)
        artifact_id = output_gen._register_output_artifact(syn_session, agent_id, fpath, safe) or ""
    except Exception as e:
        return {"error": f"could not save media: {e}"}
    return {"artifact_id": artifact_id, "kind": kind,
            "snippet": f"[[{kind}:{artifact_id}]]"}
