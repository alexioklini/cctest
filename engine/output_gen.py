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


def save_report_output(output_id, agent_id, project_dir, kind, title, body_md):
    """SHARED: write a generated report as <pdir>/outputs/<kind>-<id>.md, register
    it as an artifact, and flip the project_outputs row to ready. Used by the
    preset generators AND Deep Research so every output saves + browses identically
    in Studio. The project_outputs row must already exist (status=generating)."""
    outdir = _outputs_dir(project_dir)
    fname = f"{kind}-{output_id}.md"
    path = os.path.join(outdir, fname)
    body = f"# {title}\n\n{body_md}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    artifact_id = _register_output_artifact(f"output-{output_id}", agent_id, path, fname) or ""
    ChatDB.update_project_output(
        output_id, status="ready", title=title, path=path,
        artifact_id=artifact_id, citations=_count_citations(body_md))
    return output_id, path, artifact_id


def _run_generation(*, output_id: str, agent_id: str, project_name: str, project_id: str,
                    project_dir: str, kind: str, opts: dict, user_id: str):
    """Daemon-thread body: gather → transform → write → register → flip row."""
    try:
        focus = (opts.get("focus") or "").strip()
        length = opts.get("length") or "std"
        corpus, n = _gather_sources(agent_id, project_name, focus, user_id)
        if not corpus:
            ChatDB.update_project_output(
                output_id, status="error",
                error="No sources found for this project — add files, web URLs, or run Research first.")
            return

        prompt = output_presets.build_prompt(kind, corpus, focus=focus, length=length)
        model = _brain._background_model_default()
        if not model:
            ChatDB.update_project_output(
                output_id, status="error",
                error="No model available (set a server default model).")
            return

        from handlers import sidecar_proxy
        result = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            purpose="transform",
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

        # Title: "<preset prefix> — <project display name>".
        proj_cfg = _brain.ProjectManager.get_project(agent_id, project_name) or {}
        display = proj_cfg.get("name") or project_name
        title = f"{output_presets.PRESETS[kind]['title_prefix']} — {display}"
        save_report_output(output_id, agent_id, project_dir, kind, title, reply)
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
