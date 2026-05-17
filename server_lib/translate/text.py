"""Text translation — single sidecar background_call with glossary-aware system prompt."""
from __future__ import annotations

from .detect import LANG_NAMES, detect_language
from .glossary import load_glossary, glossary_to_system_block


def _lang_label(code: str) -> str:
    code = (code or "").strip().lower()
    return LANG_NAMES.get(code, code or "the source language")


def build_translate_system_prompt(source_lang: str, target_lang: str,
                                  glossary: dict | None = None) -> str:
    """Self-contained — used by tool, HTTP wrapper, and document chunker."""
    src = _lang_label(source_lang) if source_lang else "the source language (auto-detect)"
    tgt = _lang_label(target_lang)
    parts = [
        f"You are a professional translator. Translate the user's text from {src} into {tgt}.",
        "",
        "RULES:",
        "- Output ONLY the translation. No preamble, no explanation, no quotes around the result.",
        "- Preserve all formatting: line breaks, lists, headings, code blocks, markdown, whitespace.",
        "- Preserve numbers, dates, units, URLs, email addresses, and proper nouns verbatim.",
        "- Keep the same register, tone, and level of formality as the source.",
        "- If the source contains text already in the target language, keep it as-is.",
        "- Do not summarise, expand, or improve — translate faithfully.",
    ]
    block = glossary_to_system_block(glossary)
    if block:
        parts.append(block)
    return "\n".join(parts)


def build_rewrite_system_prompt(lang: str, tone: str) -> str:
    """System prompt for the tone-rewrite pass applied after translation (or instead of noop)."""
    lang_label = _lang_label(lang) if lang else "the text's language"
    tone_descriptions = {
        "formal": "formal and professional — use precise vocabulary, full sentences, and avoid contractions or colloquialisms",
        "informal": "informal and conversational — use everyday language, contractions, and a friendly register",
        "plain": "plain and simple — use short sentences, common words, and remove jargon",
        "marketing": "engaging and persuasive — use active voice, vivid language, and a positive tone",
        "technical": "technical and precise — use domain-specific terminology, avoid ambiguity, and prefer noun phrases",
    }
    tone_desc = tone_descriptions.get(tone, tone)
    return "\n".join([
        f"You are a professional editor rewriting text in {lang_label}.",
        f"Rewrite the text so that it sounds {tone_desc}.",
        "",
        "RULES:",
        "- Output ONLY the rewritten text. No preamble, no explanation, no quotes around the result.",
        "- Preserve all formatting: line breaks, lists, headings, code blocks, markdown, whitespace.",
        "- Preserve numbers, dates, units, URLs, email addresses, and proper nouns verbatim.",
        "- Keep the same meaning — do not add, remove, or distort information.",
        "- Do not translate — the output must stay in the same language as the input.",
    ])


def translate_text(text: str, target_lang: str, *,
                   source_lang: str = "",
                   glossary_slug: str = "",
                   model: str = "",
                   tone: str = "",
                   auto_detect_fallback_model: str = "") -> dict:
    """Translate `text` into `target_lang`.

    - source_lang: ISO 639-1; if empty, attempts auto-detection (lingua + optional LLM fallback).
    - glossary_slug: optional glossary file id.
    - model: model id used for the translation call. Falls back to ask_llm's resolution chain.

    Returns: {translation, source_lang, target_lang, detected, model, glossary}.
    Raises ValueError on bad input.
    """
    text = text or ""
    target_lang = (target_lang or "").strip().lower()
    if not text.strip():
        raise ValueError("text is empty")
    if not target_lang:
        raise ValueError("target_lang is required")
    if target_lang not in LANG_NAMES:
        # Tolerate non-canonical entries — only emit a warning shape, don't reject.
        pass

    detected = None
    src = (source_lang or "").strip().lower()
    if not src:
        detected = detect_language(text, fallback_model=auto_detect_fallback_model or None)
        src = detected.get("lang", "") if detected else ""

    same_lang = bool(src and src == target_lang)

    if same_lang and not tone:
        return {
            "translation": text,
            "source_lang": src,
            "target_lang": target_lang,
            "detected": detected,
            "model": "",
            "glossary": glossary_slug or "",
            "noop": True,
        }

    glossary = load_glossary(glossary_slug) if glossary_slug else None

    import brain  # late import
    chosen_model = (model or "").strip()
    if not chosen_model:
        try:
            tcfg = brain.get_tool_config() or {}
            chosen_model = ((tcfg.get("translation") or {}).get("default_model") or "").strip()
        except Exception:
            chosen_model = ""
    if not chosen_model:
        try:
            tcfg = brain.get_tool_config() or {}
            chosen_model = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        except Exception:
            chosen_model = ""
    if not chosen_model:
        chosen_model = getattr(brain, "_delegate_fallback_model", "") or ""
    if not chosen_model:
        raise RuntimeError("no model available for translation — set tools_config.translation.default_model")

    if same_lang:
        # Same language + tone → rewrite only, no translation step.
        translated = text
    else:
        system_prompt = build_translate_system_prompt(src, target_lang, glossary=glossary)
        # GDPR policy gate: pseudonymise the source text, possibly swap to
        # local model, then deanonymise the translation. Tokens like
        # <EMAIL_1_HEX> are preserved verbatim by the translation prompt's
        # "preserve URLs, emails verbatim" rule.
        _xlate_deanon = brain._identity_deanon
        _wire_text = text
        try:
            chosen_model, (_pii_text,), _xlate_deanon = brain.gdpr_pick_model_for_background(
                chosen_model, [text], purpose="translate_text")
            _wire_text = _pii_text
        except brain.GDPRBlockedError as e:
            raise RuntimeError(f"translation blocked by GDPR policy: {e}")
        from handlers import sidecar_proxy as _sidecar_proxy
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": _wire_text}],
            model=chosen_model,
            system_prompt=system_prompt,
        )
        if _res.get("error"):
            raise RuntimeError(f"translation failed: {_res['error']}")
        result = _xlate_deanon(_res.get("reply") or "")
        if not result:
            raise RuntimeError("translation returned empty result")
        translated = result.strip()

    if tone:
        try:
            tcfg = brain.get_tool_config() or {}
            rewrite_model = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        except Exception:
            rewrite_model = ""
        rewrite_model = rewrite_model or chosen_model
        rewrite_lang = target_lang or src
        rewrite_prompt = build_rewrite_system_prompt(rewrite_lang, tone)
        # GDPR policy gate for the rewrite pass — `translated` already
        # contains real PII (whether it came from the source or was
        # restored by the deanon callback above). Anonymise again before
        # the rewrite call and deanon the rewritten reply.
        _rewrite_deanon = brain._identity_deanon
        _wire_translated = translated
        try:
            rewrite_model, (_pii_translated,), _rewrite_deanon = brain.gdpr_pick_model_for_background(
                rewrite_model, [translated], purpose="translate_text_rewrite")
            _wire_translated = _pii_translated
        except brain.GDPRBlockedError:
            # Treat as soft-skip: keep the translation, drop the tone pass.
            _wire_translated = None
        if _wire_translated is not None:
            from handlers import sidecar_proxy as _sidecar_proxy
            _res = _sidecar_proxy.background_call(
                messages=[{"role": "user", "content": _wire_translated}],
                model=rewrite_model,
                system_prompt=rewrite_prompt,
            )
            rewritten = _rewrite_deanon(_res.get("reply") or "")
            if rewritten and not _res.get("error"):
                translated = rewritten.strip()

    return {
        "translation": translated,
        "source_lang": src,
        "target_lang": target_lang,
        "detected": detected,
        "model": chosen_model,
        "glossary": glossary_slug or "",
        "noop": False,
    }
