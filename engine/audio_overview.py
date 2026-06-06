# Audio Overview (NotebookLM-style podcast) — script-gen + two-voice stitch.
#
# A project_outputs kind (kind="audio_overview") that runs over the SAME shared
# pipeline as the text presets (engine/output_gen) but produces an .mp3 instead
# of a cited .md:
#   1. gather project sources (output_gen._gather_sources — project-scoped) →
#   2. ONE background_call(purpose="transform") writing a two-host English
#      dialogue tagged HOST_A:/HOST_B: →
#   3. save the script as a <pdir>/outputs/audio_overview-<id>.md artifact
#      (debuggable + re-renderable) →
#   4. render each dialogue line via Voxtral TTS (HOST_A→Oliver, HOST_B→Jane),
#      concatenate the MP3 segments into one file →
#   5. register the .mp3 as the output artifact + flip the project_outputs row.
#
# WHY raw MP3 concat (not ffmpeg): Voxtral returns standard MPEG-3 frames; naive
# byte concatenation of the segments plays as one track in browsers + macOS
# (validated via afinfo at build time). ffmpeg is NOT a reliable dependency on
# this host (broken libx265 link, 2026-06-06), so we deliberately avoid it — the
# AUDIO_OVERVIEW_PLAN flagged this exact decision.
#
# AUDIO LANGUAGE IS ENGLISH-ONLY by hard TTS constraint (voxtral-mini-tts has 10
# voices, all English). A German project yields an English podcast ABOUT the
# German content — the script-gen prompt instructs exactly that.

import base64
import json
import os
import re
import time
import uuid

import brain as _brain
from engine import output_gen
from server_lib.db import ChatDB

# Default host voices: the only male+female pair sharing an accent (en_gb),
# for the clearest two-host contrast. Voice is a per-TTS-call param, so these
# are just two slugs — overridable via opts.host_a_voice / host_b_voice.
DEFAULT_HOST_A_VOICE = "gb_oliver_neutral"   # male
DEFAULT_HOST_B_VOICE = "gb_jane_sarcasm"     # female
HOST_A_NAME = "Oliver"
HOST_B_NAME = "Jane"

# length → rough exchange-count guidance baked into the script-gen prompt.
_LENGTH_GUIDANCE = {
    "short": "Keep it tight: ~6–10 exchanges, a focused 2–3 minute conversation.",
    "std": "A natural full episode: ~14–20 exchanges, roughly 5–7 minutes spoken.",
    "long": "A deep dive: ~26–36 exchanges covering every distinct point in the sources.",
}

# Cap the number of TTS calls per overview so a runaway script can't fan out into
# hundreds of provider hits. Lines beyond this are dropped (logged, not silent).
_MAX_LINES = 80


def _build_script_prompt(sources_text: str, *, focus: str, length: str,
                         audience: str) -> str:
    """The user-turn prompt for the dialogue script-gen transform call."""
    length_guidance = _LENGTH_GUIDANCE.get(length, _LENGTH_GUIDANCE["std"])
    aud = (audience or "").strip()
    audience_line = (
        f"Pitch it for this audience: {aud}." if aud
        else "Pitch it for a curious general listener with no prior knowledge.")
    focus_line = (
        f"\nFOCUS: centre the conversation on — {focus.strip()}" if (focus or "").strip()
        else "")
    return (
        f"You are scripting a two-host audio overview (a podcast) about the "
        f"material in the RETRIEVED PROJECT SOURCES below. Two hosts, "
        f"{HOST_A_NAME} and {HOST_B_NAME}, discuss it in an engaging, natural, "
        f"conversational style — they explain, react, ask each other questions, "
        f"and build on each other. NOT a lecture; a real conversation.\n\n"
        f"{audience_line}\n"
        f"{length_guidance}{focus_line}\n\n"
        "OUTPUT FORMAT — strict, one line per spoken turn, nothing else:\n"
        f"HOST_A: <what {HOST_A_NAME} says>\n"
        f"HOST_B: <what {HOST_B_NAME} says>\n"
        "Alternate naturally (not rigidly). Every line MUST start with 'HOST_A: ' "
        "or 'HOST_B: '. No stage directions, no markdown, no section headers, no "
        "narrator — only spoken dialogue lines. Do not write '[laughs]' or similar; "
        "write only words that should be spoken aloud.\n\n"
        "LANGUAGE: write the spoken dialogue in ENGLISH even if the sources are in "
        "another language — this is an English-language overview ABOUT the material. "
        "Render names/quotes/terms naturally for an English speaker.\n\n"
        "GROUNDING: base the conversation ONLY on the retrieved sources. Do not "
        "invent facts, numbers, names, or dates that aren't in the sources. It is "
        "fine to simplify and paraphrase for a spoken register, but stay faithful. "
        "If the sources are thin, keep the episode short rather than padding with "
        "invented content.\n\n"
        "=== RETRIEVED PROJECT SOURCES ===\n"
        f"{sources_text}"
    )


# Each dialogue line → (host_key, spoken_text). host_key ∈ {"A","B"}.
_LINE_RE = re.compile(r"^\s*HOST_([AB])\s*:\s*(.+?)\s*$")


def parse_dialogue(script: str) -> list[tuple[str, str]]:
    """Parse 'HOST_A:/HOST_B:' lines into [(host_key, text)]. Lines that don't
    match the tag are folded onto the previous speaker (model sometimes wraps a
    long turn). Returns [] if nothing parsed."""
    lines: list[tuple[str, str]] = []
    for raw in (script or "").splitlines():
        m = _LINE_RE.match(raw)
        if m:
            lines.append((m.group(1), m.group(2).strip()))
        elif lines and raw.strip():
            # continuation of the previous turn
            host, prev = lines[-1]
            lines[-1] = (host, (prev + " " + raw.strip()).strip())
    return lines


def _tts_segment(text: str, voice: str) -> bytes:
    """Synthesize one dialogue line to MP3 bytes via the configured Voxtral TTS
    model. Mirrors handlers/translate._handle_translate_tts's wire logic in-process
    (the worker can't HTTP its own server cleanly). Raises on failure."""
    import urllib.request
    cfg = _brain.get_tool_config().get("text_to_speech", {}) or {}
    model_id = (cfg.get("default_model") or "").strip()
    if not model_id:
        raise RuntimeError("no TTS model configured (text_to_speech.default_model)")
    prov = _brain.resolve_provider_for_model(model_id)
    base_url = (prov.get("base_url") or "").rstrip("/")
    api_key = prov.get("api_key") or ""
    if not base_url or not api_key:
        raise RuntimeError(f"TTS provider for '{model_id}' not configured")
    api_model_id = _brain.get_api_model_id(model_id)
    payload = json.dumps({"model": api_model_id, "input": text, "voice": voice}).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/audio/speech", data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        ct = resp.headers.get("Content-Type", "")
    # Voxtral returns JSON {"audio_data": "<base64 mp3>"}; some providers send raw.
    if ct.startswith("application/json") or raw[:1] == b"{":
        return base64.b64decode(json.loads(raw)["audio_data"])
    return raw


def _stitch(lines: list[tuple[str, str]], voice_a: str, voice_b: str,
            on_progress=None) -> tuple[bytes, int]:
    """Render every dialogue line and concatenate the MP3 segments (raw byte
    concat — validated playable; ffmpeg deliberately not required). Returns
    (mp3_bytes, rendered_line_count). Skips empty/over-cap lines."""
    segments: list[bytes] = []
    rendered = 0
    capped = lines[:_MAX_LINES]
    for i, (host, text) in enumerate(capped):
        if not text.strip():
            continue
        voice = voice_a if host == "A" else voice_b
        segments.append(_tts_segment(text, voice))
        rendered += 1
        if on_progress:
            on_progress(i + 1, len(capped))
    if len(lines) > _MAX_LINES:
        print(f"[audio_overview] script had {len(lines)} lines, capped to {_MAX_LINES}")
    return b"".join(segments), rendered


def run_audio_overview(*, output_id: str, agent_id: str, project_name: str,
                       project_dir: str, opts: dict, user_id: str):
    """Daemon-thread body for kind='audio_overview'. Parallels
    output_gen._run_generation but writes a script .md + a stitched .mp3.
    The project_outputs row already exists (status='generating')."""
    def _cancelled():
        return ChatDB.project_output_cancelled(output_id)

    t0 = time.time()
    try:
        focus = (opts.get("focus") or "").strip()
        length = opts.get("length") or "std"
        audience = (opts.get("audience") or "").strip()
        voice_a = (opts.get("host_a_voice") or "").strip() or DEFAULT_HOST_A_VOICE
        voice_b = (opts.get("host_b_voice") or "").strip() or DEFAULT_HOST_B_VOICE

        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return
        ChatDB.update_project_output(output_id, phase="gathering")
        corpus, _n = output_gen._gather_sources(agent_id, project_name, focus, user_id)
        if not corpus:
            ChatDB.update_project_output(
                output_id, status="error",
                error="No sources found for this project — add files, web URLs, or run Research first.")
            return

        model = _brain._background_model_default()
        if not model:
            ChatDB.update_project_output(output_id, status="error",
                                         error="No model available (set a server default model).")
            return

        # ── Phase 1: write the dialogue script ──────────────────────────────
        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return
        ChatDB.update_project_output(output_id, phase="scripting")
        from handlers import sidecar_proxy
        result = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": _build_script_prompt(
                corpus, focus=focus, length=length, audience=audience)}],
            model=model, purpose="transform", agent_id=agent_id,
            session_id=f"output-{output_id}", project=project_name,
            user_id=user_id, max_rounds=1)
        if result.get("error"):
            ChatDB.update_project_output(output_id, status="error", error=str(result["error"])[:500])
            return
        script = (result.get("reply") or "").strip()
        lines = parse_dialogue(script)
        if not lines:
            ChatDB.update_project_output(
                output_id, status="error",
                error="The model did not produce a parseable two-host script.")
            return

        proj_cfg = _brain.ProjectManager.get_project(agent_id, project_name) or {}
        display = proj_cfg.get("name") or project_name
        title = f"Audio Overview — {display}"

        # Save the script as a sibling .md artifact (debuggable / re-renderable).
        outdir = output_gen._outputs_dir(project_dir)
        script_name = f"audio_overview-{output_id}.md"
        script_path = os.path.join(outdir, script_name)
        script_md = (f"# {title} — Script\n\n*Hosts: {HOST_A_NAME} ({voice_a}) · "
                     f"{HOST_B_NAME} ({voice_b})*\n\n")
        for host, text in lines:
            who = HOST_A_NAME if host == "A" else HOST_B_NAME
            script_md += f"**{who}:** {text}\n\n"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_md)
        output_gen._register_output_artifact(f"output-{output_id}", agent_id, script_path, script_name)

        # ── Phase 2: render + stitch the audio ──────────────────────────────
        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return
        ChatDB.update_project_output(output_id, phase="voicing")

        def _progress(done, total):
            # Cheap heartbeat so the UI shows movement on long episodes.
            ChatDB.update_project_output(output_id, phase=f"voicing {done}/{total}")
        try:
            mp3, rendered = _stitch(lines, voice_a, voice_b, on_progress=_progress)
        except Exception as e:
            ChatDB.update_project_output(
                output_id, status="error", error=f"TTS render failed: {e}"[:500])
            return
        if not mp3:
            ChatDB.update_project_output(output_id, status="error",
                                         error="No audio was produced (all lines empty).")
            return

        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return

        mp3_name = f"audio_overview-{output_id}.mp3"
        mp3_path = os.path.join(outdir, mp3_name)
        with open(mp3_path, "wb") as f:
            f.write(mp3)
        artifact_id = output_gen._register_output_artifact(
            f"output-{output_id}", agent_id, mp3_path, mp3_name) or ""

        # Cost-account the script-gen call (TTS billed separately by provider;
        # not metered here). Reuse the shared footer-meta shape.
        meta = _brain.account_background_usage(
            result, model, session_id=f"output-{output_id}",
            user_id=user_id, agent_id=agent_id)
        meta["duration_s"] = round(time.time() - t0, 1)

        ChatDB.update_project_output(
            output_id, status="ready", phase="", title=title, path=mp3_path,
            artifact_id=artifact_id, citations=rendered,
            model=meta.get("model", ""), tokens_in=meta.get("tokens_in", 0),
            tokens_out=meta.get("tokens_out", 0), cost=meta.get("cost", 0),
            duration_s=meta.get("duration_s", 0))
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            ChatDB.update_project_output(output_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def generate_to_folder(*, agent_id: str, project_name: str, out_dir: str,
                        opts: dict, user_id: str, basename: str) -> dict:
    """SYNCHRONOUS generation for the agent tool path. Gathers project sources,
    writes the script .md + stitched .mp3 into `out_dir` (the caller's session
    artifact folder), and returns {ok, mp3_path, script_path, lines, error}.

    Unlike run_audio_overview this does NOT touch the project_outputs table —
    the agent-tool deliverable is just the artifact files in the session folder
    (the file-write tracker auto-registers them + emits artifact_updated)."""
    focus = (opts.get("focus") or "").strip()
    length = opts.get("length") or "std"
    audience = (opts.get("audience") or "").strip()
    voice_a = (opts.get("host_a_voice") or "").strip() or DEFAULT_HOST_A_VOICE
    voice_b = (opts.get("host_b_voice") or "").strip() or DEFAULT_HOST_B_VOICE

    corpus, _n = output_gen._gather_sources(agent_id, project_name, focus, user_id)
    if not corpus:
        return {"ok": False, "error": "No sources found for this project."}
    model = _brain._background_model_default()
    if not model:
        return {"ok": False, "error": "No background model configured."}

    from handlers import sidecar_proxy
    result = sidecar_proxy.background_call(
        messages=[{"role": "user", "content": _build_script_prompt(
            corpus, focus=focus, length=length, audience=audience)}],
        model=model, purpose="transform", agent_id=agent_id,
        session_id="audio-tool", project=project_name, user_id=user_id, max_rounds=1)
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])[:300]}
    lines = parse_dialogue((result.get("reply") or "").strip())
    if not lines:
        return {"ok": False, "error": "Model did not produce a parseable two-host script."}

    os.makedirs(out_dir, exist_ok=True)
    script_path = os.path.join(out_dir, basename + ".md")
    script_md = f"# Audio Overview Script\n\n*Hosts: {HOST_A_NAME} ({voice_a}) · {HOST_B_NAME} ({voice_b})*\n\n"
    for host, text in lines:
        who = HOST_A_NAME if host == "A" else HOST_B_NAME
        script_md += f"**{who}:** {text}\n\n"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_md)

    try:
        mp3, rendered = _stitch(lines, voice_a, voice_b)
    except Exception as e:
        return {"ok": False, "error": f"TTS render failed: {e}"[:300], "script_path": script_path}
    if not mp3:
        return {"ok": False, "error": "No audio produced.", "script_path": script_path}
    mp3_path = os.path.join(out_dir, basename + ".mp3")
    with open(mp3_path, "wb") as f:
        f.write(mp3)
    return {"ok": True, "mp3_path": mp3_path, "script_path": script_path, "lines": rendered}


def start(*, agent_id: str, project: dict, opts: dict, user_id: str) -> str:
    """Insert a generating row + spawn the audio worker. Returns output_id.
    Mirrors output_gen.start_generation but for kind='audio_overview'."""
    import threading
    output_id = uuid.uuid4().hex
    project_id = project.get("id") or ""
    project_name = project.get("folder_name") or project.get("name") or ""
    project_dir = project.get("dir") or ""
    pending_title = f"Audio Overview — {project.get('name') or project_name}"
    ChatDB.create_project_output(
        output_id, agent_id, project_id, "audio_overview", pending_title,
        json.dumps(opts or {}), user_id)
    threading.Thread(
        target=run_audio_overview,
        kwargs={"output_id": output_id, "agent_id": agent_id,
                "project_name": project_name, "project_dir": project_dir,
                "opts": opts or {}, "user_id": user_id},
        daemon=True, name=f"audio_ov_{output_id[:8]}").start()
    return output_id
