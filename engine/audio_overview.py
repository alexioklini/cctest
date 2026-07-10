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
# LANGUAGE: the episode language is detected from the material (or set via
# opts.lang) — Voxtral TTS speaks 9 languages (_TTS_LANGUAGES). Voices are
# matched to the language from the provider roster (incl. cloned voices);
# when no matching voice exists the English defaults speak the localized
# script. (The former "ENGLISH-ONLY" header claim died with v9.304.0 — the
# Studio worker now feeds lang/speakers like the chat path.)
#
# SPEAKERS (v9.304.0): 1–4 speakers with optional names/personas/per-speaker
# voices via opts.speakers=[{name?,voice?,persona?}]. Script tags are
# HOST_1:..HOST_4: (parse_dialogue still accepts the legacy HOST_A/HOST_B).

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

def make_basename(seed: str) -> str:
    """Build a human-readable, filesystem-safe basename for an audio overview
    from a content seed (the chat title/summary, the project name, or a topic) —
    so the files read 'Podcast — Wetter in Berlin (3f9a1b2c).mp3' instead of
    'audio_overview-3f9a1b2c.mp3'. A short uuid suffix keeps repeated podcasts
    from the same source from overwriting each other. No extension."""
    slug = re.sub(r"\s+", " ", (seed or "").strip())
    # Drop characters awkward in filenames across macOS/Linux/Windows.
    slug = re.sub(r'[\\/:*?"<>|]+', "", slug).strip(" .-")
    if len(slug) > 60:
        slug = slug[:60].rsplit(" ", 1)[0].strip()
    suffix = uuid.uuid4().hex[:8]
    return f"Podcast — {slug} ({suffix})" if slug else f"Podcast ({suffix})"


# length → rough exchange-count guidance baked into the script-gen prompt.
_LENGTH_GUIDANCE = {
    "short": "Keep it tight: ~6–10 exchanges, a focused 2–3 minute conversation.",
    "std": "A natural full episode: ~14–20 exchanges, roughly 5–7 minutes spoken.",
    "long": "A deep dive: ~26–36 exchanges covering every distinct point in the sources.",
}

# Cap the number of TTS calls per overview so a runaway script can't fan out into
# hundreds of provider hits. Lines beyond this are dropped (logged, not silent).
_MAX_LINES = 80

# ISO-639-1 → human language name, for the script-gen prompt when non-English.
# Voxtral TTS officially speaks these 9 (mistral.ai/news/voxtral-tts). Detection
# of any other language falls back to an English overview.
_TTS_LANGUAGES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "nl": "Dutch", "pt": "Portuguese", "it": "Italian", "hi": "Hindi", "ar": "Arabic",
}

# Short-TTL cache of the provider voice roster so we don't re-list per line.
_voices_cache = {"at": 0.0, "voices": None}
_VOICES_TTL = 300.0


def _list_voices() -> list:
    """Fetch the Voxtral voice roster ({slug,id,languages,gender,...}), cached.
    Returns [] on failure (callers fall back to the English defaults)."""
    now = time.time()
    if _voices_cache["voices"] is not None and (now - _voices_cache["at"]) < _VOICES_TTL:
        return _voices_cache["voices"]
    import urllib.request
    voices = []
    try:
        cfg = _brain.get_tool_config().get("text_to_speech", {}) or {}
        model_id = (cfg.get("default_model") or "").strip()
        prov = _brain.resolve_provider_for_model(model_id)
        base_url = (prov.get("base_url") or "").rstrip("/")
        api_key = prov.get("api_key") or ""
        if base_url and api_key:
            offset = 0
            for _ in range(10):
                req = urllib.request.Request(
                    f"{base_url}/audio/voices?limit=50&offset={offset}",
                    headers={"Authorization": f"Bearer {api_key}"})
                data = json.loads(urllib.request.urlopen(req, timeout=10).read())
                items = data.get("items") or data.get("voices") or []
                voices.extend(items)
                if len(items) < 50:
                    break
                offset += 50
    except Exception as e:
        print(f"[audio_overview] voice list failed: {e}", flush=True)
    _voices_cache["voices"] = voices
    _voices_cache["at"] = now
    return voices


def _lang_matches(voice_langs: list, lang: str) -> bool:
    """A voice supports `lang` if any of its language tags starts with the ISO
    code (e.g. 'de' matches 'de_de'; 'en' matches 'en_us'/'en_gb')."""
    return any(str(vl).lower().startswith(lang) for vl in (voice_langs or []))


def _voices_for_lang(lang: str) -> tuple[str, str]:
    """Pick (male_voice, female_voice) slugs for `lang`. Prefers a male+female
    pair tagged for the language; falls back to the English Oliver/Jane defaults
    when the roster has no match (today's case for non-English — until a voice is
    cloned). Returns slugs (or voice ids) the speech endpoint accepts."""
    if lang == "en":
        return DEFAULT_HOST_A_VOICE, DEFAULT_HOST_B_VOICE
    voices = _list_voices()
    male = female = None
    for v in voices:
        if not _lang_matches(v.get("languages"), lang):
            continue
        ident = v.get("slug") or v.get("id")
        g = (v.get("gender") or "").lower()
        if g == "male" and not male:
            male = ident
        elif g == "female" and not female:
            female = ident
        elif not male:
            male = ident
        elif not female:
            female = ident
    # Fall back to English defaults for any host we couldn't match.
    return (male or DEFAULT_HOST_A_VOICE), (female or DEFAULT_HOST_B_VOICE)


def _voice_pool_for_lang(lang: str, n: int) -> list[str]:
    """n voice ids for `lang`, gender-alternating and distinct where the roster
    allows; cycles the English defaults when the roster runs short (they speak a
    localized script acceptably until a native voice is cloned)."""
    males: list[str] = []
    females: list[str] = []
    for v in _list_voices():
        if not _lang_matches(v.get("languages"), lang):
            continue
        ident = v.get("slug") or v.get("id")
        if not ident:
            continue
        (males if (v.get("gender") or "").lower() == "male" else females).append(ident)
    if lang == "en":
        males.insert(0, DEFAULT_HOST_A_VOICE)
        females.insert(0, DEFAULT_HOST_B_VOICE)
    pool: list[str] = []
    i = 0
    while len(pool) < n and (males or females):
        src = males if ((i % 2 == 0 and males) or not females) else females
        ident = src.pop(0)
        if ident not in pool:
            pool.append(ident)
        i += 1
    while len(pool) < n:
        pool.append(DEFAULT_HOST_A_VOICE if len(pool) % 2 == 0 else DEFAULT_HOST_B_VOICE)
    return pool


_DEFAULT_SPEAKER_NAMES = ["Oliver", "Jane", "Alex", "Maya"]


def _resolve_speakers(opts: dict, lang: str) -> list[dict]:
    """Normalise opts into 1–4 speaker dicts [{name, voice, persona}].

    Accepts opts.speakers=[{name?,voice?,persona?}] (Studio UI) and the legacy
    opts.host_a_voice/host_b_voice (chat/tool path — mapped onto the first two
    speakers). Voices left empty are filled from the language-matched roster
    (gender-alternating, distinct where possible)."""
    raw = opts.get("speakers")
    if not isinstance(raw, list) or not raw:
        raw = [{}, {}]  # classic two-host default
    raw = [s if isinstance(s, dict) else {} for s in raw[:4]]
    legacy = [(opts.get("host_a_voice") or "").strip(),
              (opts.get("host_b_voice") or "").strip()]
    pool = _voice_pool_for_lang(lang, len(raw))
    speakers = []
    for i, s in enumerate(raw):
        voice = (s.get("voice") or "").strip() or (legacy[i] if i < len(legacy) else "")
        speakers.append({
            "name": (s.get("name") or "").strip()[:40] or _DEFAULT_SPEAKER_NAMES[i],
            "voice": voice or pool[i],
            "persona": (s.get("persona") or "").strip()[:240],
        })
    return speakers


def _detect_corpus_lang(text: str) -> str:
    """Detect the dominant language of the material (ISO-639-1). Returns 'en' on
    any failure or for a language Voxtral can't speak (→ English overview)."""
    try:
        from server_lib.translate import detect_language
        r = detect_language((text or "")[:4000]) or {}
        lang = (r.get("lang") or "en").lower()[:2]
        return lang if lang in _TTS_LANGUAGES else "en"
    except Exception:
        return "en"


def _build_script_prompt(sources_text: str, *, focus: str, length: str,
                         audience: str, source_label: str = "RETRIEVED PROJECT SOURCES",
                         lang: str = "en", speakers: list | None = None) -> str:
    """The user-turn prompt for the dialogue script-gen transform call.
    `source_label` names the material so the prompt reads naturally for both
    project sources and a chat transcript. `lang` (ISO-639-1) is the language the
    spoken dialogue should be written in (one of the 9 Voxtral languages).
    `speakers` = 1–4 dicts [{name, persona?}]; None → the classic two-host
    default (keeps wiki_gen and older callers unchanged)."""
    if not speakers:
        speakers = [{"name": HOST_A_NAME}, {"name": HOST_B_NAME}]
    n = len(speakers)
    tags = [f"HOST_{i + 1}" for i in range(n)]
    length_guidance = _LENGTH_GUIDANCE.get(length, _LENGTH_GUIDANCE["std"])
    aud = (audience or "").strip()
    audience_line = (
        f"Pitch it for this audience: {aud}." if aud
        else "Pitch it for a curious general listener with no prior knowledge.")
    focus_line = (
        f"\nFOCUS: centre the conversation on — {focus.strip()}" if (focus or "").strip()
        else "")
    today = time.strftime("%B %-d, %Y")
    lang_name = _TTS_LANGUAGES.get(lang, "English")
    if lang == "en":
        language_clause = (
            "LANGUAGE: write the spoken dialogue in ENGLISH even if the material is in "
            "another language — render names/quotes/terms naturally for an English speaker.")
    else:
        language_clause = (
            f"LANGUAGE: write the ENTIRE spoken dialogue in {lang_name} — the hosts "
            f"speak {lang_name} natively. The material may be in {lang_name} already; "
            f"keep the conversation in {lang_name} throughout (only quote foreign terms "
            f"verbatim where natural).")
    roster = "\n".join(
        f"{tags[i]} = {s.get('name') or _DEFAULT_SPEAKER_NAMES[i]}"
        + (f" — {s['persona']}" if (s.get("persona") or "").strip() else "")
        for i, s in enumerate(speakers))
    if n == 1:
        opening = (
            f"You are scripting a single-narrator audio overview about the material "
            f"in the {source_label} below. The narrator explains it engagingly and "
            f"conversationally — clear, vivid, spoken register; short spoken "
            f"paragraphs, each on its own line. NOT a dry lecture.\n\n"
            f"NARRATOR:\n{roster}\n")
        format_clause = (
            "OUTPUT FORMAT — strict, one spoken paragraph per line, nothing else:\n"
            f"{tags[0]}: <what the narrator says>\n"
            f"Every line MUST start with '{tags[0]}: '. No stage directions, no "
            "markdown, no section headers — only spoken lines. Do not write "
            "'[laughs]' or similar; write only words that should be spoken aloud.")
    else:
        opening = (
            f"You are scripting a {n}-speaker audio overview (a podcast) about the "
            f"material in the {source_label} below. The speakers discuss it in an "
            f"engaging, natural, conversational style — they explain, react, ask "
            f"each other questions, and build on each other. NOT a lecture; a real "
            f"conversation. Stay true to each speaker's persona.\n\n"
            f"SPEAKERS:\n{roster}\n")
        example = "\n".join(
            f"{tags[i]}: <what {speakers[i].get('name') or tags[i]} says>" for i in range(n))
        tag_list = ", ".join(f"'{t}: '" for t in tags)
        format_clause = (
            "OUTPUT FORMAT — strict, one line per spoken turn, nothing else:\n"
            f"{example}\n"
            f"Alternate naturally (not rigidly); every speaker gets meaningful turns. "
            f"Every line MUST start with one of: {tag_list}. No stage directions, no "
            "markdown, no section headers, no narrator — only spoken dialogue lines. "
            "Do not write '[laughs]' or similar; write only words that should be "
            "spoken aloud.")
    return (
        f"{opening}\n"
        f"{audience_line}\n"
        f"{length_guidance}{focus_line}\n\n"
        f"{format_clause}\n\n"
        f"{language_clause}\n\n"
        f"TODAY'S DATE is {today}. If the speakers refer to the current time, "
        f"recent events, or 'this year', anchor it to this date — do NOT assume "
        f"an earlier year.\n\n"
        "GROUNDING: base the conversation ONLY on the material below. Do not "
        "invent facts, numbers, names, or dates that aren't in it. It is "
        "fine to simplify and paraphrase for a spoken register, but stay faithful. "
        "If the material is thin, keep the episode short rather than padding with "
        "invented content.\n\n"
        f"=== {source_label} ===\n"
        f"{sources_text}"
    )


# Each dialogue line → (speaker_index, spoken_text). Accepts the numbered tags
# HOST_1..HOST_4 (current prompt) AND the legacy letters HOST_A/HOST_B.
_LINE_RE = re.compile(r"^\s*HOST_([A-D1-4])\s*:\s*(.+?)\s*$")


def parse_dialogue(script: str) -> list[tuple[int, str]]:
    """Parse 'HOST_n:'-tagged lines into [(speaker_index, text)] (0-based).
    Lines that don't match the tag are folded onto the previous speaker (model
    sometimes wraps a long turn). Returns [] if nothing parsed."""
    lines: list[tuple[int, str]] = []
    for raw in (script or "").splitlines():
        m = _LINE_RE.match(raw)
        if m:
            key = m.group(1)
            idx = "ABCD".index(key) if key.isalpha() else int(key) - 1
            lines.append((idx, m.group(2).strip()))
        elif lines and raw.strip():
            # continuation of the previous turn
            idx, prev = lines[-1]
            lines[-1] = (idx, (prev + " " + raw.strip()).strip())
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


def _stitch(lines: list[tuple[int, str]], voices: list[str],
            on_progress=None) -> tuple[bytes, int, int]:
    """Render every dialogue line and concatenate the MP3 segments (raw byte
    concat — validated playable; ffmpeg deliberately not required). `voices` is
    the per-speaker voice list (index-matched to parse_dialogue's speaker
    indices; out-of-range indices wrap). Returns (mp3_bytes,
    rendered_line_count, chars_synthesized). Skips empty/over-cap lines.
    `chars_synthesized` drives TTS cost accounting (char-billed)."""
    voices = voices or [DEFAULT_HOST_A_VOICE, DEFAULT_HOST_B_VOICE]
    segments: list[bytes] = []
    rendered = 0
    chars = 0
    capped = lines[:_MAX_LINES]
    for i, (idx, text) in enumerate(capped):
        if not text.strip():
            continue
        voice = voices[idx % len(voices)]
        segments.append(_tts_segment(text, voice))
        rendered += 1
        chars += len(text)
        if on_progress:
            on_progress(i + 1, len(capped))
    if len(lines) > _MAX_LINES:
        print(f"[audio_overview] script had {len(lines)} lines, capped to {_MAX_LINES}")
    return b"".join(segments), rendered, chars


def _log_tts_cost(chars: int, *, session_id: str, user_id: str, agent_id: str,
                  purpose: str = "read_aloud") -> float:
    """Log a synthetic cost row for the TTS render (char-billed, not token-billed)
    and return the computed USD cost. Rate is PER MODEL — models.<id>.cost_per_1k_chars_usd
    on the configured TTS model (0 = don't meter, e.g. a local voice). `purpose` is
    the cost-ledger use-case bucket (audio_overview for Studio podcasts, read_aloud
    for chat). Best-effort — never raises."""
    if chars <= 0:
        return 0.0
    try:
        from engine.quotas import _unit_rate
        cfg = _brain.get_tool_config().get("text_to_speech", {}) or {}
        model_id = (cfg.get("default_model") or "").strip()
        rate = _unit_rate(model_id, "cost_per_1k_chars_usd")
        cost = round((chars / 1000.0) * rate, 6)
        tracker = getattr(_brain, "_cost_tracker", None)
        if tracker is not None and (rate > 0 or model_id):
            provider = _brain._models_config.get(model_id, {}).get("provider", "")
            tracker.log_tts(agent=agent_id or "main", session_id=session_id or "",
                            model=model_id or "tts", provider=provider,
                            chars=chars, cost_usd=cost, user_id=user_id or "",
                            purpose=purpose)
        return cost
    except Exception as e:
        print(f"[audio_overview] TTS cost log failed: {e}", flush=True)
        return 0.0


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

        # Language + speakers (v9.304.0 — the Studio path previously hardcoded
        # English/two hosts; now it matches the chat path): explicit opts.lang
        # wins, else detect from the corpus. Speakers 1–4 with personas +
        # per-speaker voices via _resolve_speakers.
        lang = (opts.get("lang") or "").strip().lower()[:2]
        if lang not in _TTS_LANGUAGES:
            lang = _detect_corpus_lang(corpus)
        speakers = _resolve_speakers(opts, lang)
        print(f"[audio_overview] lang={lang} speakers="
              f"{[(s['name'], s['voice']) for s in speakers]}", flush=True)

        # Dedicated audio_overview_model knob (v9.168.0) for the dialogue-script
        # LLM; empty -> background default. (The TTS voice model is separate:
        # text_to_speech.default_model.)
        model = ""
        try:
            _am = (_brain._server_config().get("audio_overview_model") or "").strip()
            if _am and _brain._is_model_available(_am):
                model = _am
        except Exception:
            pass
        if not model:
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
                corpus, focus=focus, length=length, audience=audience,
                lang=lang, speakers=speakers)}],
            model=model, cost_purpose="audio_overview", agent_id=agent_id,
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
        # Content-based filenames (the project name) instead of a hex id.
        base = make_basename(display)

        # Save the script as a sibling .md artifact (debuggable / re-renderable).
        outdir = output_gen._outputs_dir(project_dir)
        script_name = f"{base}.md"
        script_path = os.path.join(outdir, script_name)
        roster = " · ".join(f"{s['name']} ({s['voice']})" for s in speakers)
        script_md = f"# {title} — Script\n\n*Sprecher: {roster} · Sprache: {lang}*\n\n"
        for idx, text in lines:
            who = speakers[idx % len(speakers)]["name"]
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
            mp3, rendered, tts_chars = _stitch(
                lines, [s["voice"] for s in speakers], on_progress=_progress)
        except Exception as e:
            ChatDB.update_project_output(
                output_id, status="error", error=f"TTS render failed: {e}"[:500])
            return
        tts_cost = _log_tts_cost(tts_chars, session_id=f"output-{output_id}",
                                 user_id=user_id, agent_id=agent_id,
                                 purpose="audio_overview")
        if not mp3:
            ChatDB.update_project_output(output_id, status="error",
                                         error="No audio was produced (all lines empty).")
            return

        if _cancelled():
            ChatDB.update_project_output(output_id, status="cancelled", phase="")
            return

        mp3_name = f"{base}.mp3"
        mp3_path = os.path.join(outdir, mp3_name)
        with open(mp3_path, "wb") as f:
            f.write(mp3)
        artifact_id = output_gen._register_output_artifact(
            f"output-{output_id}", agent_id, mp3_path, mp3_name) or ""

        # Cost-account the script-gen call, then ADD the TTS render cost (logged
        # separately above as its own cost_log row) into the row's total so the
        # Studio card + footer show the full price of the overview.
        # Compute-only: background_call already wrote the LLM cost row centrally;
        # we only fold the (separately-logged) TTS cost into the displayed total.
        meta = _brain.account_background_usage(
            result, model, session_id=f"output-{output_id}",
            user_id=user_id, agent_id=agent_id, purpose="audio_overview", log=False)
        meta["cost"] = round(meta.get("cost", 0) + tts_cost, 6)
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


def _corpus_to_audio(*, corpus: str, agent_id: str, out_dir: str, opts: dict,
                     user_id: str, basename: str, project_name: str = "",
                     cost_session_id: str = "audio-tool",
                     source_label: str = "RETRIEVED PROJECT SOURCES") -> dict:
    """SYNCHRONOUS core: pre-gathered corpus → script .md + stitched .mp3 in
    `out_dir`. Returns {ok, mp3_path, script_path, lines, cost, error}. Shared by
    the project-sources path (generate_to_folder) and the chat-transcript path
    (generate_from_chat) — only the corpus differs. Does NOT touch the
    project_outputs table; the file-write tracker registers the artifacts.
    Cost-accounts BOTH the script-gen LLM call and the TTS render (char-billed)
    against `cost_session_id`."""
    length = opts.get("length") or "std"
    audience = (opts.get("audience") or "").strip()
    focus = (opts.get("focus") or "").strip()

    if not corpus:
        return {"ok": False, "error": "No content to make an overview from."}

    # Detect the material's language → speak the podcast in it, with voices tagged
    # for that language (falling back to the English defaults when none exist).
    # An explicit language override (opts.lang) or explicit voices skip detection.
    lang = (opts.get("lang") or "").strip().lower()[:2] or _detect_corpus_lang(corpus)
    if lang not in _TTS_LANGUAGES:
        lang = "en"
    speakers = _resolve_speakers(opts, lang)
    print(f"[audio_overview] lang={lang} speakers="
          f"{[(s['name'], s['voice']) for s in speakers]}", flush=True)

    model = _brain._background_model_default()
    if not model:
        return {"ok": False, "error": "No background model configured."}

    from handlers import sidecar_proxy
    result = sidecar_proxy.background_call(
        messages=[{"role": "user", "content": _build_script_prompt(
            corpus, focus=focus, length=length, audience=audience,
            source_label=source_label, lang=lang, speakers=speakers)}],
        model=model, cost_purpose="audio_overview", agent_id=agent_id,
        session_id=cost_session_id, project=project_name, user_id=user_id, max_rounds=1)
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])[:300]}
    lines = parse_dialogue((result.get("reply") or "").strip())
    if not lines:
        return {"ok": False, "error": "Model did not produce a parseable script."}

    os.makedirs(out_dir, exist_ok=True)
    script_path = os.path.join(out_dir, basename + ".md")
    roster = " · ".join(f"{s['name']} ({s['voice']})" for s in speakers)
    script_md = f"# Audio Overview Script\n\n*Sprecher: {roster} · Sprache: {lang}*\n\n"
    for idx, text in lines:
        who = speakers[idx % len(speakers)]["name"]
        script_md += f"**{who}:** {text}\n\n"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_md)

    try:
        mp3, rendered, tts_chars = _stitch(lines, [s["voice"] for s in speakers])
    except Exception as e:
        return {"ok": False, "error": f"TTS render failed: {e}"[:300], "script_path": script_path}
    if not mp3:
        return {"ok": False, "error": "No audio produced.", "script_path": script_path}
    mp3_path = os.path.join(out_dir, basename + ".mp3")
    with open(mp3_path, "wb") as f:
        f.write(mp3)
    # Script-gen LLM cost was already logged centrally by background_call
    # (compute-only here); the TTS render is a separate char-billed cost row.
    script_meta = _brain.account_background_usage(
        result, model, session_id=cost_session_id, user_id=user_id,
        agent_id=agent_id, purpose="audio_overview", log=False)
    tts_cost = _log_tts_cost(tts_chars, session_id=cost_session_id,
                             user_id=user_id, agent_id=agent_id,
                             purpose="audio_overview")
    total_cost = round(script_meta.get("cost", 0) + tts_cost, 6)
    return {"ok": True, "mp3_path": mp3_path, "script_path": script_path,
            "lines": rendered, "cost": total_cost, "tts_chars": tts_chars,
            "lang": lang, "speakers": [s["name"] for s in speakers]}


def generate_to_folder(*, agent_id: str, project_name: str, out_dir: str,
                       opts: dict, user_id: str, basename: str,
                       cost_session_id: str = "audio-tool") -> dict:
    """Agent-tool / project path: gather the PROJECT's sources, then build audio."""
    focus = (opts.get("focus") or "").strip()
    corpus, _n = output_gen._gather_sources(agent_id, project_name, focus, user_id)
    if not corpus:
        return {"ok": False, "error": "No sources found for this project."}
    return _corpus_to_audio(corpus=corpus, agent_id=agent_id, out_dir=out_dir,
                            opts=opts, user_id=user_id, basename=basename,
                            project_name=project_name, cost_session_id=cost_session_id)


# A chat needs at least this many user/assistant turns to be worth an overview.
_MIN_CHAT_TURNS = 2


def _chat_corpus(session_id: str) -> tuple[str, int]:
    """Build an overview corpus from a chat's transcript. Returns (corpus, turns).
    Uses only real user/assistant turns (skips internal thinking/tool rows); the
    conversation text IS the source material the hosts discuss."""
    from server_lib.db import ChatDB
    msgs = ChatDB.load_messages(session_id) or []
    blocks, turns = [], 0
    for m in msgs:
        role = m.get("role")
        if role not in ("user", "human", "assistant"):
            continue  # skip thinking/tool/system rows
        content = m.get("content")
        if not isinstance(content, str):
            continue  # tool-call / structured rows
        text = content.strip()
        if not text:
            continue
        who = "User" if role in ("user", "human") else "Assistant"
        blocks.append(f"{who}: {text}")
        turns += 1
    return "\n\n".join(blocks), turns


def generate_from_chat(*, agent_id: str, session_id: str, out_dir: str,
                       opts: dict, user_id: str, basename: str) -> dict:
    """Chat path: build the corpus from the chat transcript (no project), then
    build audio. Used outside a project so any chat can become a podcast."""
    corpus, turns = _chat_corpus(session_id)
    if turns < _MIN_CHAT_TURNS:
        return {"ok": False, "error": "This chat is too short to make an audio "
                                      "overview — have a longer conversation first."}
    return _corpus_to_audio(corpus=corpus, agent_id=agent_id, out_dir=out_dir,
                            opts=opts, user_id=user_id, basename=basename,
                            cost_session_id=session_id,
                            source_label="CONVERSATION TRANSCRIPT")


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
