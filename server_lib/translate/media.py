"""Audio/Video translation pipeline.

Single-shot transcribe (Voxtral) + segment-aligned translate. Output formats:
TXT (transcript only), TXT-pair (transcript+translation), SRT, VTT.

Why segment-aligned: a paragraph translation drifts off the audio timeline.
For SRT/VTT we need translated text that maps 1:1 to the original segments
so timestamps stay valid. We translate segments in batched chunks (numbered-
list framing, mirrors document.py) so timing references survive.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Iterable

from .text import translate_text

# Audio + video container extensions Voxtral accepts. Voxtral reads audio
# tracks out of common video containers transparently — listed here so the
# UI and the multipart handler know what to advertise.
SUPPORTED_EXTS = {
    ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".webm",
    ".ogg", ".flac", ".aac", ".mkv", ".avi",
}

# Same chunk granularity as document.py — keeps numbered-list parser stable.
_SEGMENTS_PER_CHUNK = 50

# Fallback batch when the LLM returns the wrong number of items: translate
# segments individually so we never lose content (mirrors document._translate_chunks).
_PARSE_FAIL_FALLBACK = "per_segment"


# ─── Output format generators ──────────────────────────────────────────────

def _fmt_srt_time(t: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm (comma separator)."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_time(t: float) -> str:
    """WebVTT timestamp: HH:MM:SS.mmm (period separator)."""
    return _fmt_srt_time(t).replace(",", ".")


def to_srt(segments: list[dict], text_key: str = "text") -> str:
    """Render segments as SRT. Each segment becomes a numbered cue.

    Cue numbers are 1..N over the *non-empty* segments — players reject SRTs
    with gaps in the numbering (Quicktime, VLC tolerate it but it's spec-
    violating).
    """
    out: list[str] = []
    cue = 0
    for s in segments:
        text = (s.get(text_key) or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0.0)
        end = float(s.get("end") or start)
        if end < start:
            end = start
        cue += 1
        out.append(str(cue))
        out.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
        out.append(text)
        out.append("")  # blank line between cues
    return "\n".join(out).rstrip() + "\n"


def to_vtt(segments: list[dict], text_key: str = "text") -> str:
    """Render segments as WebVTT."""
    out: list[str] = ["WEBVTT", ""]
    for s in segments:
        text = (s.get(text_key) or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0.0)
        end = float(s.get("end") or start)
        if end < start:
            end = start
        out.append(f"{_fmt_vtt_time(start)} --> {_fmt_vtt_time(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_txt(segments: list[dict], text_key: str = "text") -> str:
    """Plain text: one segment per line. Joined into paragraphs at blank gaps
    (gap > 1.5s between end and next start)."""
    if not segments:
        return ""
    parts: list[str] = []
    prev_end = 0.0
    for s in segments:
        text = (s.get(text_key) or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0.0)
        if parts and (start - prev_end) > 1.5:
            parts.append("")  # paragraph break on long pause
        parts.append(text)
        prev_end = float(s.get("end") or start)
    return "\n".join(parts).rstrip() + "\n"


def to_bilingual_txt(segments: list[dict]) -> str:
    """Pretty-print transcript + translation side-by-side (timestamped)."""
    out: list[str] = []
    for s in segments:
        src = (s.get("text") or "").strip()
        tgt = (s.get("translation") or "").strip()
        if not src and not tgt:
            continue
        ts = f"[{_fmt_srt_time(float(s.get('start') or 0.0))}]"
        out.append(ts)
        if src:
            out.append(src)
        if tgt:
            out.append(f"→ {tgt}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ─── Segment-aligned translation ──────────────────────────────────────────

_NUMBERED_RE = re.compile(r"^\s*\[(\d+)\]\s*(.*?)\s*$", re.DOTALL)


def _parse_numbered_response(reply: str, expected: int) -> list[str] | None:
    """Pull `[1] ... [2] ...` items out of the LLM reply. Returns None on
    parse failure so the caller can per-item fallback."""
    if not reply.strip():
        return None
    # Split on `[N]` markers. Tolerate leading prose.
    parts = re.split(r"\[(\d+)\]", reply)
    # parts is like ['', '1', ' text...', '2', ' text...']
    if len(parts) < 3:
        return None
    items: dict[int, str] = {}
    for i in range(1, len(parts), 2):
        try:
            idx = int(parts[i])
        except (ValueError, IndexError):
            continue
        body = parts[i + 1] if i + 1 < len(parts) else ""
        items[idx] = body.strip().rstrip()
    if len(items) != expected:
        return None
    out: list[str] = []
    for n in range(1, expected + 1):
        if n not in items:
            return None
        out.append(items[n])
    return out


def translate_segments(
    segments: list[dict],
    target_lang: str,
    *,
    source_lang: str = "",
    glossary_slug: str = "",
    model: str = "",
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Translate each segment's `text` and store result in `translation`.

    Batched in chunks of _SEGMENTS_PER_CHUNK using the same numbered-list
    framing as document.py: the LLM gets one prompt with `[1] ... [N] ...`
    and must reply in the same shape. On parse-fail we fall back to per-
    segment calls so content is never silently dropped.
    """
    enriched: list[dict] = [dict(s) for s in segments]
    indexes = [i for i, s in enumerate(enriched)
               if (s.get("text") or "").strip()]
    total = len(indexes)
    if not total:
        if progress:
            progress(0, 0)
        return enriched
    if progress:
        progress(0, total)

    done = 0
    for chunk_start in range(0, total, _SEGMENTS_PER_CHUNK):
        chunk_idx = indexes[chunk_start:chunk_start + _SEGMENTS_PER_CHUNK]
        chunk_texts = [enriched[i]["text"] for i in chunk_idx]
        # Build numbered prompt. We feed the whole list into translate_text
        # and ask Mistral to keep numbering — same trick the document pipeline
        # validated. Glossary application happens inside translate_text.
        numbered = "\n".join(f"[{n + 1}] {t}" for n, t in enumerate(chunk_texts))
        try:
            res = translate_text(
                numbered,
                target_lang,
                source_lang=source_lang,
                glossary_slug=glossary_slug,
                model=model,
                tone="",
            )
            reply = res.get("translation") or ""
        except Exception as e:
            reply = ""
            err = str(e)
        else:
            err = ""
        parsed = _parse_numbered_response(reply, len(chunk_texts)) if reply else None

        if parsed is None:
            # Per-segment fallback — slow but never drops content.
            for i, src_idx in enumerate(chunk_idx):
                src = enriched[src_idx]["text"]
                try:
                    r = translate_text(
                        src, target_lang,
                        source_lang=source_lang,
                        glossary_slug=glossary_slug,
                        model=model,
                    )
                    enriched[src_idx]["translation"] = (r.get("translation") or "").strip()
                except Exception:
                    # Last resort: keep original text. UI surfaces partial state.
                    enriched[src_idx]["translation"] = src
                done += 1
                if progress:
                    progress(done, total)
        else:
            for src_idx, txt in zip(chunk_idx, parsed):
                enriched[src_idx]["translation"] = txt
            done += len(chunk_idx)
            if progress:
                progress(done, total)

    return enriched


# ─── End-to-end pipeline ──────────────────────────────────────────────────

def transcribe_and_translate(
    file_path: str,
    *,
    target_lang: str,
    source_lang: str = "",
    glossary_slug: str = "",
    model: str = "",
    transcribe_model: str = "",
    progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Full pipeline: transcribe via Voxtral → translate segments.

    progress(stage, done, total): stage in {'transcribe', 'translate'}.
    transcribe phase reports done=0/total=0 (single blocking call); translate
    reports per-batch.
    """
    # Late-import brain to avoid circulars (this module imports translate_text
    # which imports `_run_delegate` at call time).
    import brain

    if progress:
        progress("transcribe", 0, 0)

    # Resolve transcription model — same path the tool uses, so we get the
    # same GDPR + fallback behavior for free.
    model_id, route = brain._transcription_resolve(transcribe_model or "")
    wire = (route.get("wire") or "").lower()
    api_model_id = brain.get_api_model_id(model_id)

    if wire == "mlx_whisper":
        result = brain._transcribe_with_whisper(file_path, model_id, source_lang or None,
                                                with_segments=True)
    elif wire == "openai_audio":
        provider = route.get("provider") or ""
        if not provider:
            raise RuntimeError(f"transcribe model '{model_id}' has no provider configured")
        result = brain._transcribe_with_voxtral(
            file_path, api_model_id, provider, source_lang or None, with_segments=True,
        )
    else:
        raise RuntimeError(f"unknown transcription wire '{wire}' for '{model_id}'")

    transcript = result.get("transcript") or ""
    detected_lang = result.get("language") or source_lang or ""
    duration_s = result.get("duration_s") or 0.0
    segments = result.get("segments") or []

    translated: list[dict] = []
    if target_lang and segments:
        # Skip translation when source and target match — preserves transcript
        # only. Voxtral often returns language=None so accept "no detected lang"
        # as ambiguous (do translate anyway).
        if detected_lang and detected_lang.lower() == target_lang.lower():
            translated = [{**s, "translation": s.get("text", "")} for s in segments]
            if progress:
                progress("translate", len(segments), len(segments))
        else:
            translated = translate_segments(
                segments, target_lang,
                source_lang=detected_lang or source_lang,
                glossary_slug=glossary_slug,
                model=model,
                progress=(lambda d, t: progress("translate", d, t)) if progress else None,
            )
    else:
        translated = list(segments)

    return {
        "transcript": transcript,
        "language": detected_lang,
        "target_lang": target_lang,
        "duration_s": duration_s,
        "segments": translated,  # each: {text, start, end, [translation]}
        "transcribe_model": model_id,
    }


def write_output_files(
    result: dict,
    *,
    out_dir: str,
    base_name: str,
) -> dict[str, str]:
    """Write transcript+translation in TXT/SRT/VTT/bilingual formats.

    Returns a dict mapping format → absolute output path. The TXT-bilingual
    file is always considered the "primary" output (most human-readable);
    SRT+VTT live alongside for downstream tools.
    """
    os.makedirs(out_dir, exist_ok=True)
    segs = result.get("segments") or []
    has_translation = any(s.get("translation") for s in segs)

    files: dict[str, str] = {}

    # 1. Transcript-only TXT (always written)
    p = os.path.join(out_dir, f"{base_name}.transcript.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(to_txt(segs, "text"))
    files["transcript_txt"] = p

    # 2. Transcript SRT/VTT
    p = os.path.join(out_dir, f"{base_name}.transcript.srt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(to_srt(segs, "text"))
    files["transcript_srt"] = p
    p = os.path.join(out_dir, f"{base_name}.transcript.vtt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(to_vtt(segs, "text"))
    files["transcript_vtt"] = p

    if has_translation:
        # 3. Translation-only TXT/SRT/VTT
        tl = (result.get("target_lang") or "tr").lower()
        p = os.path.join(out_dir, f"{base_name}.{tl}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(to_txt(segs, "translation"))
        files["translation_txt"] = p
        p = os.path.join(out_dir, f"{base_name}.{tl}.srt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(to_srt(segs, "translation"))
        files["translation_srt"] = p
        p = os.path.join(out_dir, f"{base_name}.{tl}.vtt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(to_vtt(segs, "translation"))
        files["translation_vtt"] = p

        # 4. Bilingual side-by-side TXT (primary download)
        p = os.path.join(out_dir, f"{base_name}.bilingual.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(to_bilingual_txt(segs))
        files["bilingual_txt"] = p
        files["primary"] = files["bilingual_txt"]
    else:
        files["primary"] = files["transcript_txt"]

    return files
