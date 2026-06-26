# Output generation pipeline — the SHARED background worker behind
# POST /v1/agents/<agent>/projects/<name>/generate (Output Presets) and, later,
# Audio Overview + Deep Research (which save their report as a project_outputs row).
#
# Flow (OUTPUT_PRESETS_DETAILED_SPEC §4 W1):
#   1. handler inserts a project_outputs row status='generating', returns output_id
#   2. start_generation() spawns a daemon thread running _run_generation()
#   3. thread: tool_mempalace_query (project-scoped) gathers sources →
#      ONE background_call(purpose="transform", project=<name>) with the preset
#      prompt + grounding discipline → writes a cited .md under <pdir>/outputs/ →
#      registers it as an artifact → flips the row to status='ready' (or 'error').
#
# No live chat session is involved — the .md is registered under a synthetic
# session id `output-<output_id>` so Studio's existing artifact-content endpoint
# (resolves by artifact_id, disk-fallback) can open it.

import json
import os
import re
import threading
import uuid

import brain as _brain
from engine import output_presets
from engine.context import request_context
from server_lib.db import ChatDB  # ChatDB lives in server_lib.db (NOT re-exported on brain)

# How many drawers to pull for the grounding corpus. Bounded so a huge project
# can't blow the transform call's context (E2). If the project has more, this is
# a top-N slice — _gather_sources notes the truncation in the corpus header so
# the model (and reader) know coverage was capped (no silent cut — repo rule).
_RETRIEVAL_N = 25
# Broad seed query — preset generation wants a wide sweep of the corpus, not a
# focused lookup. The project force-scopes the wing, so this just ranks drawers.
_SEED_QUERY = "overview key points main topics summary"


def _gather_sources(agent_id: str, project_name: str, focus: str, user_id: str) -> tuple[str, int]:
    """Retrieve project sources as a single source-tagged corpus string.

    Returns (corpus_text, drawer_count). Runs inside a project-scoped request
    context so tool_mempalace_query pins to the project's knowledge wing
    (project-pinned queries force-scope the wing + skip the C3 gate, so the
    empty/own user_id can't widen the search beyond this project)."""
    query = (focus or "").strip() or _SEED_QUERY
    with request_context(project=project_name, current_agent=agent_id, current_user_id=user_id):
        raw = _brain.tool_mempalace_query({"query": query, "n_results": _RETRIEVAL_N})
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return "", 0
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    # tool_mempalace_query returns {drawers:[{source_file,text,similarity,…}],
    # count, total_before_filter}.
    drawers = (data or {}).get("drawers", []) if isinstance(data, dict) else []
    if not drawers:
        return "", 0
    blocks = []
    for d in drawers:
        src = d.get("source_file") or d.get("wing") or "source"
        src = os.path.basename(src.rstrip("/")) or src  # full paths → readable basename for citations
        text = (d.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"--- Source: {src} ---\n{text}")
    corpus = "\n\n".join(blocks)
    total_before = (data or {}).get("total_before_filter", len(drawers))
    if isinstance(total_before, int) and total_before > len(drawers):
        corpus = (f"[Coverage note: showing the top {len(drawers)} of "
                  f"{total_before} matching source passages.]\n\n") + corpus
    return corpus, len(drawers)


def _outputs_dir(pdir: str) -> str:
    d = os.path.join(pdir, "outputs")
    os.makedirs(d, exist_ok=True)
    return d


def _count_citations(text: str) -> int:
    """Count [Quelle: ...] citation brackets in the generated output."""
    return len(re.findall(r"\[Quelle:", text or ""))


def _register_output_artifact(session_id: str, agent_id: str, path: str, name: str) -> str | None:
    """Register the generated .md as an artifact so Studio can open/version it.

    Uses a synthetic session id (output-<id>) — generation isn't tied to a chat
    session. Returns the artifact_id, or None on failure."""
    artifact_id = uuid.uuid4().hex[:12]
    try:
        with open(path, "rb") as f:
            content = f.read(5 * 1024 * 1024)
        size = os.path.getsize(path)
        if size > 5 * 1024 * 1024:
            content = None  # too large for the DB snapshot — disk-only
    except OSError:
        return None
    # Type from the extension (markdown → served as text by the artifact-content
    # endpoint; .mp3 audio overviews later → document). Match the repo's map.
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    artifact_type = _brain._ARTIFACT_TYPE_MAP.get(ext, "text")
    ChatDB.create_artifact(artifact_id, session_id, agent_id, name, path, artifact_type, "output")
    ChatDB.add_artifact_version(artifact_id, 1, content, size, None, "created")
    return artifact_id


def render_metadata_footer(meta: dict) -> str:
    """A markdown '## Metadaten' footer (model · date/time · duration · in/out
    tokens · cost) appended to a generated report. Shared by Studio outputs +
    Deep Research so the footer reads identically everywhere. Empty string when
    there's no usable metadata."""
    if not meta:
        return ""
    import datetime as _dt
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    dur = meta.get("duration_s") or 0
    dur_str = f"{int(dur // 60)} min {int(dur % 60)} s" if dur >= 60 else f"{dur:.1f} s"
    ti, to = meta.get("tokens_in", 0), meta.get("tokens_out", 0)
    cost = meta.get("cost", 0)
    return (
        "\n\n---\n\n## Metadaten\n"
        f"- **Modell:** {meta.get('model', '—')}\n"
        f"- **Erstellt:** {when}\n"
        f"- **Dauer:** {dur_str}\n"
        f"- **Tokens:** {ti:,} Eingabe · {to:,} Ausgabe\n"
        f"- **Kosten:** ${cost:.4f}\n")


def save_report_output(output_id, agent_id, project_dir, kind, title, body_md, meta=None,
                       category=None, sources=None, stats=None):
    """SHARED: write a generated report as <pdir>/outputs/<kind>-<id>.md (canonical)
    AND a styled <kind>-<id>.html (the editorial visual report), register BOTH as
    artifacts, and flip the project_outputs row to ready. Used by the preset
    generators AND Deep Research so every output saves + browses identically in
    Studio. The .md stays the source of truth (wiki mining, search, audio overview,
    the markdown editor all read it); the .html is the primary, downloadable
    deliverable. The project_outputs row must already exist (status=generating).
    `meta` (model/tokens/cost/duration) is appended as a footer + stored on the row.
    `category`/`sources`/`stats` only style the HTML (Deep Research passes them;
    Studio leaves them None → a clean default-styled report)."""
    outdir = _outputs_dir(project_dir)
    fname = f"{kind}-{output_id}.md"
    path = os.path.join(outdir, fname)
    body = f"# {title}\n\n{body_md}\n" + render_metadata_footer(meta)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    artifact_id = _register_output_artifact(f"output-{output_id}", agent_id, path, fname) or ""

    # Render the styled HTML twin from the SAME markdown body. Best-effort: a
    # render failure must never block the (canonical) .md save — the row still
    # goes ready with the markdown artifact. html_artifact_id is the primary the
    # UI offers for viewing/download; falls back to the .md if rendering failed.
    html_artifact_id = ""
    try:
        from engine import report_html
        html_doc = report_html.render_report_html(
            body_md, title, meta=meta, sources=sources, category=category, stats=stats)
        html_name = f"{kind}-{output_id}.html"
        html_path = os.path.join(outdir, html_name)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        html_artifact_id = _register_output_artifact(
            f"output-{output_id}", agent_id, html_path, html_name) or ""
    except Exception as e:
        print(f"[report_html] HTML render failed for {output_id}: {e}", flush=True)

    fields = dict(status="ready", title=title, path=path,
                  artifact_id=artifact_id, html_artifact_id=html_artifact_id,
                  citations=_count_citations(body_md))
    if meta:
        fields.update(model=meta.get("model", ""), tokens_in=meta.get("tokens_in", 0),
                      tokens_out=meta.get("tokens_out", 0), cost=meta.get("cost", 0),
                      duration_s=meta.get("duration_s", 0))
    ChatDB.update_project_output(output_id, **fields)
    # File the finished report into the wiki (best-effort, background) so it's
    # browsable + searchable alongside everything else. Scoped to the project
    # (project_id from the row → project_chat wing), owned by its creator.
    try:
        row = ChatDB.get_project_output(output_id) or {}
        import threading as _th
        from engine import wiki_store as _wiki

        def _file():
            try:
                _wiki.wiki_from_artifact(
                    title=title, body_md=body_md, source="studio",
                    source_ref=f"output/{output_id}",
                    user_id=row.get("created_by", "") or "",
                    project_id=row.get("project_id", "") or "",
                    scope="user", agent_id=agent_id)
            except Exception as _e:
                print(f"[wiki] output→wiki failed for {output_id}: {_e}", flush=True)
        _th.Thread(target=_file, daemon=True, name=f"wiki-out-{output_id[:8]}").start()
    except Exception:
        pass
    return output_id, path, artifact_id


def _run_generation(*, output_id: str, agent_id: str, project_name: str, project_id: str,
                    project_dir: str, kind: str, opts: dict, user_id: str):
    """Daemon-thread body: gather → transform → write → register → flip row.
    Cooperative cancel: checks the row's `cancel` flag before each phase (an
    in-flight LLM call still completes — no sidecar cancel-token — then aborts)."""
    def _cancelled():
        return ChatDB.project_output_cancelled(output_id)

    import time as _time
    _t0 = _time.time()
    try:
        focus = (opts.get("focus") or "").strip()
        length = opts.get("length") or "std"
        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return
        ChatDB.update_project_output(output_id, phase="gathering")
        corpus, n = _gather_sources(agent_id, project_name, focus, user_id)
        if not corpus:
            ChatDB.update_project_output(
                output_id, status="error",
                error="No sources found for this project — add files, web URLs, or run Research first.")
            return

        prompt = output_presets.build_prompt(kind, corpus, focus=focus, length=length)
        # Dedicated studio_model knob (v9.167.0); empty -> background default.
        model = ""
        try:
            _sm = (_brain._server_config().get("studio_model") or "").strip()
            if _sm and _brain._is_model_available(_sm):
                model = _sm
        except Exception:
            pass
        if not model:
            model = _brain._background_model_default()
        if not model:
            ChatDB.update_project_output(
                output_id, status="error",
                error="No model available (set a server default model).")
            return

        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return
        ChatDB.update_project_output(output_id, phase="writing")
        from handlers import sidecar_proxy
        result = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            cost_purpose="studio",
            agent_id=agent_id,
            session_id=f"output-{output_id}",
            project=project_name,
            user_id=user_id,
            max_rounds=1,
        )
        if result.get("error"):
            ChatDB.update_project_output(output_id, status="error", error=str(result["error"])[:500])
            return
        reply = (result.get("reply") or "").strip()
        if not reply:
            ChatDB.update_project_output(output_id, status="error", error="Model returned an empty output.")
            return

        # Cancelled while the (uninterruptible) LLM call ran → abort the save so
        # we don't materialise a file the user asked to stop.
        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return

        # Cost-count this LLM call (attributed to user_id, like chats) + capture
        # the execution metadata for the card + report footer.
        # Compute-only: background_call already wrote the cost row centrally.
        meta = _brain.account_background_usage(
            result, model, session_id=f"output-{output_id}",
            user_id=user_id, agent_id=agent_id, purpose="studio", log=False)
        meta["duration_s"] = round(_time.time() - _t0, 1)

        # Title: "<preset prefix> — <project display name>".
        proj_cfg = _brain.ProjectManager.get_project(agent_id, project_name) or {}
        display = proj_cfg.get("name") or project_name
        title = f"{output_presets.PRESETS[kind]['title_prefix']} — {display}"
        save_report_output(output_id, agent_id, project_dir, kind, title, reply, meta=meta)
    except Exception as e:  # never let the thread die silently — record it
        import traceback
        traceback.print_exc()
        try:
            ChatDB.update_project_output(output_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def start_generation(*, agent_id: str, project: dict, kind: str, opts: dict, user_id: str) -> str:
    """Insert a generating row + spawn the worker. Returns the output_id.

    Caller has already validated kind + project membership."""
    output_id = uuid.uuid4().hex
    project_id = project.get("id") or ""
    project_name = project.get("folder_name") or project.get("name") or ""
    project_dir = project.get("dir") or ""
    pending_title = f"{output_presets.PRESETS[kind]['title_prefix']} — {project.get('name') or project_name}"
    ChatDB.create_project_output(
        output_id, agent_id, project_id, kind, pending_title, json.dumps(opts or {}), user_id)
    threading.Thread(
        target=_run_generation,
        kwargs={
            "output_id": output_id, "agent_id": agent_id, "project_name": project_name,
            "project_id": project_id, "project_dir": project_dir, "kind": kind,
            "opts": opts or {}, "user_id": user_id,
        },
        daemon=True, name=f"output_gen_{output_id[:8]}").start()
    return output_id
