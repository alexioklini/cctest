"""Language detection — lingua primary (offline, ~accurate), Mistral fallback
when confidence is low or lingua isn't installed."""
from __future__ import annotations

# ISO 639-1 → human-readable, used in system prompts + UI labels.
LANG_NAMES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ru": "Russian", "uk": "Ukrainian", "cs": "Czech", "sk": "Slovak",
    "hu": "Hungarian", "ro": "Romanian", "tr": "Turkish", "el": "Greek",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
    "ar": "Arabic", "he": "Hebrew", "hi": "Hindi",
    "id": "Indonesian", "vi": "Vietnamese", "th": "Thai",
}

_DETECTOR = None
_DETECTOR_READY = False


def _build_detector():
    global _DETECTOR, _DETECTOR_READY
    if _DETECTOR_READY:
        return _DETECTOR
    _DETECTOR_READY = True
    try:
        from lingua import LanguageDetectorBuilder
        # Low-accuracy mode is dramatically faster (~5ms vs ~50ms) and only
        # loses meaningful accuracy on very short snippets (<25 chars), where
        # the Mistral fallback takes over anyway.
        _DETECTOR = (
            LanguageDetectorBuilder
            .from_all_languages()
            .with_low_accuracy_mode()
            .with_preloaded_language_models()
            .build()
        )
    except Exception:
        _DETECTOR = None
    return _DETECTOR


def detect_language(text: str, *, min_confidence: float = 0.6,
                    fallback_model: str | None = None) -> dict:
    """Detect the language of `text`.

    Returns: {lang: ISO 639-1 or '', confidence: float, source: 'lingua'|'llm'|'empty'}.

    `fallback_model` enables the LLM fallback for short or ambiguous snippets;
    when omitted (or no provider configured) the lingua result is returned
    even at low confidence.
    """
    text = (text or "").strip()
    if not text:
        return {"lang": "", "confidence": 0.0, "source": "empty"}

    sample = text[:2000]
    det = _build_detector()
    lang = ""
    conf = 0.0
    if det is not None:
        try:
            cvs = det.compute_language_confidence_values(sample)
            if cvs:
                top = cvs[0]
                lang = (top.language.iso_code_639_1.name or "").lower()
                conf = float(top.value)
        except Exception:
            pass

    if lang and conf >= min_confidence:
        return {"lang": lang, "confidence": round(conf, 3), "source": "lingua"}

    if fallback_model:
        llm_lang = _llm_detect(sample, fallback_model)
        if llm_lang:
            return {"lang": llm_lang, "confidence": 0.95, "source": "llm"}

    return {"lang": lang, "confidence": round(conf, 3), "source": "lingua"}


def _llm_detect(text: str, model: str) -> str:
    """Single tiny LLM call. Returns ISO 639-1 or ''."""
    try:
        import brain as _brain
        # GDPR policy gate: detect / pseudonymise / swap per admin policy.
        # Reply is a 2-letter code so deanon is a no-op, but apply it
        # uniformly so any future change to the policy keeps working.
        try:
            model, (_pii_text,), _deanon = _brain.gdpr_pick_model_for_background(
                model, [text], purpose="lang_detect")
        except _brain.GDPRBlockedError:
            return ""
        from handlers import sidecar_proxy as _sidecar_proxy
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": f"Detect the language of this text. Reply with only the ISO 639-1 two-letter code (e.g. 'en', 'de'). No explanation.\n\n{_pii_text}"}],
            model=model,
            system_prompt="You are a language identifier. Output a single ISO 639-1 code, lowercase, nothing else.",
            cost_purpose="lang_detect",
            max_tokens=8,
        )
        out = _deanon(_res.get("reply") or "")
        code = out.strip().lower()[:2]
        if code in LANG_NAMES:
            return code
    except Exception:
        pass
    return ""
