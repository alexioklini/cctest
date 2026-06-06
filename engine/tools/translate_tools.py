# Translation + audio-transcription tool bodies (extracted from brain.py, E4).
#
# Six agent-facing tools — translate_text / translate_document /
# detect_language / get_glossary / list_glossaries / transcribe_audio — plus
# the transcription helper cluster PRIVATE to transcribe_audio:
#   _whisper_repo_for, _transcription_config, _transcribe_with_whisper,
#   _transcribe_with_voxtral, _normalize_legacy_audio_id, _transcription_resolve.
#
# The translation tools themselves are thin wrappers over server_lib.translate.
# Pure relocation: JSON envelopes + error strings byte-identical to pre-E4.
#
# NOTE on the audio helpers: server_lib/translate/{media,live}.py call
# `brain._transcription_resolve` / `brain._transcribe_with_whisper` /
# `brain._transcribe_with_voxtral` / `brain._transcription_config` (late
# `import brain`). brain.py re-exports them, so those callers resolve unchanged.
#
# Seams:
#   - `_ok` / `_err` / `_get_artifact_session_folder` from engine.tool_exec.
#   - `_thread_local` from engine.context.
#   - server_lib.translate.* imported lazily inside each translation tool.
#   - brain runtime symbols (`_after_file_write`, `AGENTS_DIR`, `_current_agent`,
#     `_models_config`, `get_tool_config`, `get_api_model_id`, `_WHISPER_PROVIDER`,
#     `_audit_log`) reached lazily via `import brain as _brain`. NO top-level
#     `import brain` (cycle).
#
# brain.py re-exports all 6 tools + the 6 audio helpers via
# `from engine.tools.translate_tools import (...)`.

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

from engine.context import get_request_context
from engine.tool_exec import _ok, _err, _get_artifact_session_folder


# ─── Transcription helper cluster (private to transcribe_audio) ──────────────

def _whisper_repo_for(model_id: str) -> str:
    """Map a whisper model id (e.g. 'whisper-base', 'whisper-large-v3') to the
    HuggingFace mlx-community repo id. The id-to-repo derivation is mechanical:
    'whisper-<size>' → 'mlx-community/whisper-<size>-mlx'."""
    base = model_id.split("/")[-1]  # tolerate scoped ids
    if not base.startswith("whisper-"):
        raise ValueError(f"not a whisper model id: '{model_id}'")
    return f"mlx-community/{base}-mlx"


def _transcription_config() -> dict:
    """Read the transcribe_audio block from tools_config.json (merged with defaults)."""
    import brain as _brain
    try:
        return _brain.get_tool_config().get("transcribe_audio", {}) or {}
    except Exception:
        return {}


def _transcribe_with_whisper(file_path: str, model_id: str, language: str | None,
                             with_segments: bool = False) -> dict:
    """Run local mlx-whisper. Raises on import / runtime failure."""
    repo = _whisper_repo_for(model_id)
    import mlx_whisper  # type: ignore
    kwargs = {"path_or_hf_repo": repo}
    if language:
        kwargs["language"] = language
    result = mlx_whisper.transcribe(file_path, **kwargs) or {}
    transcript = (result.get("text") or "").strip()
    detected_language = result.get("language") or language or ""
    segments = result.get("segments") or []
    duration_s = 0.0
    if segments:
        try:
            duration_s = float(segments[-1].get("end", 0.0))
        except (TypeError, ValueError):
            duration_s = 0.0
    out = {
        "transcript": transcript,
        "language": detected_language,
        "duration_s": round(duration_s, 2),
    }
    if with_segments:
        norm: list[dict] = []
        for s in segments:
            if not isinstance(s, dict):
                continue
            try:
                norm.append({
                    "text": (s.get("text") or "").strip(),
                    "start": float(s.get("start") or 0.0),
                    "end": float(s.get("end") or 0.0),
                })
            except (TypeError, ValueError):
                pass
        out["segments"] = norm
    return out


def _transcribe_with_voxtral(file_path: str, model_id: str, provider_name: str,
                             language: str | None, with_segments: bool = False) -> dict:
    """POST audio to Mistral Voxtral /audio/transcriptions (OpenAI-compatible multipart).
    Raises on HTTP error so the caller can decide whether to fall back.

    When `with_segments=True`, the returned dict carries a `segments` list of
    `{text, start, end}` (Voxtral always returns these — the flag just controls
    whether we expose them upward). Used for SRT/VTT generation.
    """
    try:
        # config.json lives at the repo root next to brain.py (the original
        # used brain.py's own __file__). Resolve via brain's location.
        import brain as _brain
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(_brain.__file__)), "config.json")
        with open(cfg_path) as f:
            prov = json.load(f).get("providers", {}).get(provider_name, {}) or {}
    except Exception as e:
        raise RuntimeError(f"could not load provider '{provider_name}': {e}")
    base_url = (prov.get("base_url") or "").rstrip("/")
    api_key = prov.get("api_key") or ""
    if not base_url or not api_key:
        raise RuntimeError(f"provider '{provider_name}' missing base_url or api_key")

    # Build multipart body manually (stdlib only — keeps install footprint identical).
    boundary = f"----brainagentboundary{int(time.time()*1000)}"
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lstrip(".").lower() or "bin"
    mime_map = {
        "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
        "mp4": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac",
        "aac": "audio/aac", "webm": "audio/webm",
    }
    mime = mime_map.get(ext, "application/octet-stream")

    parts: list[bytes] = []
    def _field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")
    _field("model", model_id)
    if language:
        _field("language", language)
    if with_segments:
        # Mistral OpenAI-compat: array fields use repeated key with []. Voxtral
        # returns segments unconditionally, but we still pass this so the wire
        # is explicit about what we expect — future-proofs against a default
        # change to text-only.
        _field("timestamp_granularities[]", "segment")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = f"{base_url}/audio/transcriptions"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code} from {url}: {err_body}")

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"voxtral: bad JSON response: {e}")

    transcript = (data.get("text") or "").strip()
    detected_language = data.get("language") or language or ""
    # Voxtral returns usage.prompt_audio_seconds; older / OpenAI-shape APIs use usage.seconds.
    duration_s = 0.0
    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("prompt_audio_seconds", "seconds", "audio_seconds"):
            try:
                v = float(usage.get(key) or 0.0)
            except (TypeError, ValueError):
                v = 0.0
            if v:
                duration_s = v
                break
    raw_segments = data.get("segments") or []
    if not duration_s and raw_segments:
        try:
            duration_s = float(raw_segments[-1].get("end", 0.0))
        except (TypeError, ValueError):
            duration_s = 0.0
    out = {
        "transcript": transcript,
        "language": detected_language,
        "duration_s": round(duration_s, 2),
    }
    if with_segments:
        # Normalize to a stable shape — drop speaker_id/type/etc., enforce floats.
        norm: list[dict] = []
        for s in raw_segments:
            if not isinstance(s, dict):
                continue
            try:
                norm.append({
                    "text": (s.get("text") or "").strip(),
                    "start": float(s.get("start") or 0.0),
                    "end": float(s.get("end") or 0.0),
                })
            except (TypeError, ValueError):
                pass
        out["segments"] = norm
    return out


def _normalize_legacy_audio_id(requested: str) -> str:
    """Back-compat: pre-capability ids stored in tools_config.json or passed by
    older callers. Maps to the new canonical model_config ids.

      - 'whisper:base' (and other sizes) → 'whisper-base'
      - bare 'tiny'/'base'/'small'/'medium'/'large-v3' → 'whisper-base' etc.
      - bare 'voxtral-mini-latest' / 'voxtral-small-latest' → scoped 'mistral-experimental/voxtral-*-latest'
    """
    r = (requested or "").strip()
    if not r:
        return r
    low = r.lower()
    legacy_whisper_sizes = {"tiny", "base", "small", "medium", "large-v3"}
    if low.startswith("whisper:"):
        size = low.split(":", 1)[1]
        return f"whisper-{size}"
    if low in legacy_whisper_sizes:
        return f"whisper-{low}"
    if low in ("voxtral-mini-latest", "voxtral-small-latest"):
        return f"mistral-experimental/{low}"
    return r


def _transcription_resolve(model_arg: str | None) -> tuple[str, dict]:
    """Resolve a model arg to (canonical_id, route_dict). route_dict has {wire, provider}.

    The model registry is _models_config: any entry whose capabilities list
    includes 'audio' is selectable here. The provider field on the model
    determines the wire:
      - provider == 'local-mlx-whisper' → wire 'mlx_whisper' (in-process)
      - everything else → wire 'openai_audio' (multipart POST to <base_url>/audio/transcriptions)

    The configured id MUST match an entry in _models_config exactly. Pre-
    capability legacy ids ('whisper:base', bare 'voxtral-mini-latest', bare
    sizes) are normalised once via _normalize_legacy_audio_id before lookup
    — that's the only mapping layer. No fuzzy / case-insensitive / suffix
    fallback: if the configured id doesn't resolve, raise so the admin
    notices instead of silently dispatching to a near-match.
    """
    import brain as _brain
    _models_config = _brain._models_config
    _WHISPER_PROVIDER = _brain._WHISPER_PROVIDER
    cfg = _transcription_config()
    requested = (model_arg or "").strip()
    if not requested:
        requested = (cfg.get("default_model") or "").strip()
    if not requested:
        audio_ids = sorted([
            mid for mid, c in _models_config.items()
            if "audio" in (c.get("capabilities") or [])
        ])
        raise ValueError(
            "no transcription model configured. Set transcribe_audio.default_model "
            f"in the Tools tab. Configured (capability=audio): {', '.join(audio_ids) or '(none)'}"
        )
    # The configured id is used verbatim. No legacy normalisation, no
    # fuzzy match — what the admin sets in the Tools tab is what the tool
    # uses. If it doesn't match _models_config exactly, we raise below.
    entry = _models_config.get(requested)
    if not entry:
        audio_ids = sorted([
            mid for mid, c in _models_config.items()
            if "audio" in (c.get("capabilities") or [])
        ])
        raise ValueError(
            f"unknown transcription model '{requested}'. "
            f"Configured (capability=audio): {', '.join(audio_ids) or '(none)'}"
        )

    if "audio" not in (entry.get("capabilities") or []):
        raise ValueError(
            f"model '{requested}' is not flagged with the 'audio' capability. "
            "Add 'audio' to its capabilities in the Models tab."
        )

    provider = (entry.get("provider") or "").strip()
    if provider == _WHISPER_PROVIDER:
        return requested, {"wire": "mlx_whisper", "provider": provider}
    return requested, {"wire": "openai_audio", "provider": provider}


# ─── transcribe_audio tool ───────────────────────────────────────────────────

def tool_transcribe_audio(args: dict) -> str:
    import brain as _brain
    file_path = args.get("file", "")
    language = args.get("language") or None
    if not file_path:
        return _err("transcribe_audio: 'file' is required")
    file_path = os.path.expanduser(file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        return _err(f"transcribe_audio: file not found: {file_path}")

    try:
        model_id, route = _transcription_resolve(args.get("model"))
    except ValueError as e:
        return _err(f"transcribe_audio: {e}")
    wire = (route.get("wire") or "").lower()

    # GDPR gate: when server_block is master-on and the chosen backend is cloud
    # (i.e. not mlx_whisper), swap to the configured local fallback. We can't scan
    # audio content, so this is a conservative blanket policy — voice notes can
    # carry PII the scanner would otherwise catch in text.
    fallback_used_reason = ""
    if wire != "mlx_whisper":
        try:
            cfg_root = json.load(open(os.path.join(os.path.dirname(os.path.abspath(_brain.__file__)), "config.json")))
            gdpr = cfg_root.get("gdpr_scanner") or {}
            if gdpr.get("enabled") and gdpr.get("server_block"):
                fb_id = (_transcription_config().get("fallback_model") or "whisper-base")
                model_id, route = _transcription_resolve(fb_id)
                wire = (route.get("wire") or "").lower()
                fallback_used_reason = "gdpr_server_block"
                try:
                    if _brain._audit_log:
                        _agent = get_request_context().current_agent or _brain._current_agent
                        _brain._audit_log.log_action(
                            agent=(_agent.agent_id if _agent else "main"),
                            action_type="pii_auto_fallback",
                            tool_name="transcribe_audio",
                            args_summary=f"cloud -> {model_id}",
                            result_summary="audio file, content not scannable",
                            result_status="ok",
                            session_id=get_request_context().current_session_id or None,
                            source="background",
                        )
                except Exception:
                    pass
        except Exception:
            pass

    # Dispatch.
    try:
        if wire == "mlx_whisper":
            try:
                result = _transcribe_with_whisper(file_path, model_id, language)
            except ImportError:
                return _err("transcribe_audio: mlx-whisper is not installed. Run: pip3 install --user mlx-whisper")
        elif wire == "openai_audio":
            provider = route.get("provider") or ""
            if not provider:
                return _err(f"transcribe_audio: model '{model_id}' has no provider configured")
            api_model_id = _brain.get_api_model_id(model_id)
            try:
                result = _transcribe_with_voxtral(file_path, api_model_id, provider, language)
            except Exception as cloud_err:
                fb_id = (_transcription_config().get("fallback_model") or "whisper-base")
                try:
                    fb_model_id, _ = _transcription_resolve(fb_id)
                    result = _transcribe_with_whisper(file_path, fb_model_id, language)
                    fallback_used_reason = f"cloud_error: {cloud_err}"[:200]
                    model_id = fb_model_id
                except Exception:
                    return _err(f"transcribe_audio: {cloud_err}")
        else:
            return _err(f"transcribe_audio: unknown wire '{wire}' for model '{model_id}'")
    except Exception as e:
        return _err(f"transcribe_audio: {e}")

    out = {
        "transcript": result["transcript"],
        "language": result["language"],
        "duration_s": result["duration_s"],
        "model": model_id,
        "file": file_path,
    }
    if fallback_used_reason:
        out["fallback_used"] = fallback_used_reason

    # Optional chained translation step.
    translate_to = (args.get("translate_to") or "").strip().lower()
    if translate_to and result["transcript"].strip():
        try:
            from server_lib.translate import translate_text as _tt
            tr = _tt(
                result["transcript"],
                translate_to,
                source_lang=result["language"] or "",
                glossary_slug=args.get("glossary") or "",
            )
            out["translation"] = tr["translation"]
            out["target_lang"] = tr["target_lang"]
            out["translation_model"] = tr["model"]
        except Exception as e:
            out["translation_error"] = str(e)[:200]

    try:
        print(f"[transcribe_audio] model={model_id} duration_s={result['duration_s']} "
              f"chars={len(result['transcript'])} fallback={fallback_used_reason or '-'} "
              f"translate_to={translate_to or '-'}", flush=True)
    except Exception:
        pass
    return _ok(out)


def tool_generate_audio_overview(args: dict) -> str:
    """Generate a two-host audio overview (NotebookLM-style podcast .mp3). In a
    PROJECT it discusses the project's sources; OUTSIDE a project it discusses the
    CURRENT CHAT's conversation. English-only audio (Voxtral TTS constraint). Writes
    a script .md + a stitched .mp3 into the session artifact folder."""
    import brain as _brain
    from engine import audio_overview

    project = get_request_context().project or ""
    _ag = get_request_context().current_agent or _brain._current_agent
    agent_id = getattr(_ag, "agent_id", None) or (_ag if isinstance(_ag, str) else "main")
    user_id = get_request_context().current_user_id or ""
    session_id = get_request_context().current_session_id or "audio"

    length = (args.get("length") or "std").strip()
    if length not in ("short", "std", "long"):
        length = "std"
    opts = {
        "focus": (args.get("topic") or "").strip(),
        "length": length,
        "audience": (args.get("audience") or "").strip(),
        "host_a_voice": (args.get("host_a_voice") or "").strip(),
        "host_b_voice": (args.get("host_b_voice") or "").strip(),
    }
    # Write into the session's artifact folder (same convention as write_file).
    folder = _get_artifact_session_folder(session_id)
    out_dir = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder)
    # Content-based filename: prefer the topic, else the project name (chat path
    # has no obvious seed → the helper falls back to a plain 'Podcast' label).
    basename = audio_overview.make_basename(opts.get("focus") or project or "")
    print(f"[audio_overview] tool start project={project or '(chat)'} length={length}", flush=True)
    if project:
        res = audio_overview.generate_to_folder(
            agent_id=agent_id, project_name=project, out_dir=out_dir,
            opts=opts, user_id=user_id, basename=basename,
            cost_session_id=session_id)
    else:
        # No project → make the overview from this chat's transcript.
        res = audio_overview.generate_from_chat(
            agent_id=agent_id, session_id=session_id, out_dir=out_dir,
            opts=opts, user_id=user_id, basename=basename)
    if not res.get("ok"):
        return _err(f"generate_audio_overview: {res.get('error', 'generation failed')}")
    # Register both files so they appear in the Artifacts panel + emit SSE.
    for p in (res.get("script_path"), res.get("mp3_path")):
        if p:
            try:
                _brain._after_file_write(p, "created", agent_id)
            except Exception:
                pass
    return _ok({
        "status": "done",
        "audio_file": os.path.basename(res["mp3_path"]),
        "script_file": os.path.basename(res["script_path"]),
        "spoken_lines": res.get("lines", 0),
        "cost_usd": res.get("cost", 0),
        "hosts": f"{audio_overview.HOST_A_NAME} & {audio_overview.HOST_B_NAME} (English)",
        "note": "Audio overview generated and saved to the session artifact folder. "
                "The .mp3 is the podcast; the .md is the dialogue script.",
    })


# ─── Translation tools ──────────────────────────────────────────────────────

def tool_translate_text(args: dict) -> str:
    text = args.get("text") or ""
    target_lang = (args.get("target_lang") or "").strip().lower()
    if not text:
        return _err("translate_text: 'text' is required")
    if not target_lang:
        return _err("translate_text: 'target_lang' is required")
    try:
        from server_lib.translate import translate_text as _tt
        result = _tt(
            text,
            target_lang,
            source_lang=(args.get("source_lang") or "").strip().lower(),
            glossary_slug=(args.get("glossary") or "").strip(),
            model=(args.get("model") or "").strip(),
            tone=(args.get("tone") or "").strip(),
        )
        return _ok(result)
    except ValueError as e:
        return _err(f"translate_text: {e}")
    except Exception as e:
        return _err(f"translate_text: {e}")


def tool_detect_language(args: dict) -> str:
    text = args.get("text") or ""
    if not text:
        return _err("detect_language: 'text' is required")
    try:
        from server_lib.translate import detect_language as _dl
        return _ok(_dl(text))
    except Exception as e:
        return _err(f"detect_language: {e}")


def tool_list_glossaries(args: dict) -> str:
    try:
        from server_lib.translate import list_glossaries as _lg
        return _ok({"glossaries": _lg()})
    except Exception as e:
        return _err(f"list_glossaries: {e}")


def tool_get_glossary(args: dict) -> str:
    slug = (args.get("slug") or "").strip()
    if not slug:
        return _err("get_glossary: 'slug' is required")
    try:
        from server_lib.translate import load_glossary as _ld
        g = _ld(slug)
        if not g:
            return _err(f"get_glossary: not found '{slug}'")
        return _ok({"glossary": g})
    except Exception as e:
        return _err(f"get_glossary: {e}")


def tool_translate_document(args: dict) -> str:
    import brain as _brain
    path = (args.get("path") or "").strip()
    target_lang = (args.get("target_lang") or "").strip().lower()
    if not path:
        return _err("translate_document: 'path' is required")
    if not target_lang:
        return _err("translate_document: 'target_lang' is required")

    # Resolve relative paths against the current artifact folder so the agent
    # can pass a bare filename of something it just wrote — same convention
    # write_file uses.
    src_path = os.path.expanduser(path)
    if not os.path.isabs(src_path):
        session_id = get_request_context().current_session_id
        agent = get_request_context().current_agent or _brain._current_agent
        if session_id and agent:
            folder = _get_artifact_session_folder(session_id)
            artifact_dir = os.path.join(_brain.AGENTS_DIR, agent.agent_id, "artifacts", folder)
            candidate = os.path.join(artifact_dir, src_path)
            src_path = candidate if os.path.exists(candidate) else os.path.abspath(src_path)
        else:
            src_path = os.path.abspath(src_path)
    if not os.path.isfile(src_path):
        return _err(f"translate_document: file not found: {src_path}")

    # Output goes into the artifact folder so it auto-promotes.
    session_id = get_request_context().current_session_id
    agent = get_request_context().current_agent or _brain._current_agent
    if session_id and agent:
        folder = _get_artifact_session_folder(session_id)
        out_dir = os.path.join(_brain.AGENTS_DIR, agent.agent_id, "artifacts", folder)
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = os.path.dirname(src_path) or "."

    try:
        from server_lib.translate import translate_document_file
        result = translate_document_file(
            src_path,
            target_lang=target_lang,
            source_lang=(args.get("source_lang") or "").strip().lower(),
            glossary_slug=(args.get("glossary") or "").strip(),
            model=(args.get("model") or "").strip(),
            output_dir=out_dir,
        )
    except FileNotFoundError as e:
        return _err(f"translate_document: file not found: {e}")
    except ValueError as e:
        return _err(f"translate_document: {e}")
    except Exception as e:
        return _err(f"translate_document: {e}")

    # Register the output as an artifact write so the panel picks it up.
    try:
        _brain._after_file_write(result["output_path"], "created",
                                 agent.agent_id if agent else "main")
    except Exception:
        pass
    return _ok(result)
