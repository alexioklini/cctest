"""spaCy NER PII detector.

Loads German NER model at startup, scans text for PER / LOC / ORG entities,
emits findings in the same shape as brain._pii_scan_text so the existing
pseudonymise pipeline picks them up unchanged.

Phase 1: German only. Phase 2 will add EN + RU and language routing.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

_log = logging.getLogger(__name__)

# Module-level model cache. Keyed by language code so Phase 2 can drop
# in additional models without restructuring.
_NLP_CACHE: dict[str, Any] = {}
_NLP_LOCK = threading.Lock()
# Languages whose load failed — never retried, scan returns [] for them.
_LOAD_FAILED: set[str] = set()

# spaCy entity labels we care about, mapped to Brain rule_ids.
# 'address' is intentionally Brain's term for spaCy's LOC (users recognise
# it; LOC also covers cities/countries which we treat as addressy enough
# in the personal category).
_LABEL_MAP = {
    "PER": "name",
    "LOC": "address",
    "ORG": "organisation",
}

# Display labels surfaced in PII findings (mirrors the regex `label` field).
_LABEL_DISPLAY = {
    "name":         "Name",
    "address":      "Adresse / Ort",
    "organisation": "Organisation",
}

# Minimum entity length to reduce noise — single-letter / short tokens are
# almost always false positives in NER.
_MIN_ENTITY_CHARS = 3

# Hard cap on text length scanned per call — protects against pathological
# pastes (spaCy's sm model parses ~5K chars/ms, but we want a fixed ceiling).
_MAX_SCAN_CHARS = 50_000


def _passes_shape_gate(value: str, rule_id: str) -> bool:
    """Heuristic post-filter for NER findings on `de_core_news_sm`.

    The sm model has known false-positive modes on German text: it tags
    lowercase function-words and verbs as PER ("ich wohne", "mein name"),
    and lowercase place-names as LOC ("wien" in casual writing). German
    proper nouns are case-sensitive — Personen-, Orts- und Organisations-
    namen are always written with a capital letter — so requiring at least
    one alpha character that starts with uppercase eliminates the bulk of
    the noise without dropping legitimate findings.

    Returns True if the value should be kept, False to drop.

    Gate rules:
      * value MUST contain at least one alpha character (digits-only spans
        from numeric mislabelling get dropped).
      * Among alpha tokens, at least ONE must start with an uppercase
        letter. This rejects all-lowercase entities ("ich wohne", "wien")
        but keeps multi-word names where every word is capitalised
        ("Maria Schmidt", "Siemens", "München") and partial matches where
        a single capitalised token is enough to anchor the entity
        ("die DSGVO" → token DSGVO passes).
      * All-uppercase short entities (≤3 chars: "DSGVO" is 5, kept;
        "DSG" stays) are dropped only when no lowercase letters appear
        anywhere AND the token is in the common-acronym blocklist below.
        This catches "IBAN", "EU", "BGB" that the model occasionally
        labels as ORG.

    The gate is intentionally conservative — when in doubt it keeps the
    finding. Admins can still suppress whole categories or specific
    rule_ids via the GDPR settings.
    """
    if not value:
        return False
    # Must contain at least one alphabetic char somewhere.
    if not any(c.isalpha() for c in value):
        return False
    # Tokenise on whitespace + common punctuation that NER spans may include.
    tokens = [t for t in value.replace(",", " ").replace(".", " ").split() if t]
    if not tokens:
        return False
    # All-uppercase short acronym blocklist for ORG. spaCy's sm model
    # sometimes flags these as organisations when used in prose
    # ("im Sinne der DSGVO", "laut BGB"). They're legal references, not
    # the kind of org name a user wants pseudonymised.
    _ORG_ACRONYM_BLOCKLIST = {
        "IBAN", "EU", "BGB", "DSGVO", "GDPR", "AGB", "USA", "UK",
        "PIN", "TAN", "BIC", "ID", "URL", "API", "HTML", "PDF",
    }
    if rule_id == "organisation":
        if len(tokens) == 1 and tokens[0].upper() == tokens[0] and tokens[0] in _ORG_ACRONYM_BLOCKLIST:
            return False
    # Capitalisation gate: at least one alpha token must start with an
    # uppercase letter. German proper nouns are case-sensitive.
    has_capitalised_token = any(
        t[0].isupper() for t in tokens if t and t[0].isalpha()
    )
    if not has_capitalised_token:
        return False
    return True


def _model_id_for(lang: str) -> Optional[str]:
    """spaCy package name for a Brain language code, or None if Phase 1
    doesn't ship that language."""
    return KNOWN_LANGUAGES.get(lang, {}).get("model")


# Catalogue of languages Brain knows about. Single source of truth for the
# admin pill, the spaCy package name, and the human-readable display name.
# Phase 1 ships German only; Phase 2 will populate EN/RU with their actual
# spaCy model names. Adding a row here is enough to make the language
# selectable from the admin UI.
#
# Model choice — `de_core_news_md` (~45 MB on disk, ~120 MB resident):
# meaningful quality lift over the `_sm` variant (~+2 F1 on German NER)
# at modest cost. Pre-trained word vectors help on casual/mixed-case
# text where `_sm` over-fires PER on lowercase function-words.
KNOWN_LANGUAGES: dict[str, dict[str, str]] = {
    "de": {"display": "Deutsch", "model": "de_core_news_md"},
}


def load_models(languages: tuple[str, ...] = ("de",)) -> None:
    """Called once at server startup. Loads each language's model into the
    cache. Failures are logged but never fatal — NER becomes a no-op for
    that language and `scan_text` returns []. Idempotent.
    """
    for lang in languages:
        if lang in _NLP_CACHE or lang in _LOAD_FAILED:
            continue
        model_id = _model_id_for(lang)
        if not model_id:
            _LOAD_FAILED.add(lang)
            _log.warning("[pii_ner] no model registered for lang=%s", lang)
            continue
        try:
            import spacy  # local import — keep brain.py importable without spaCy
            # Disable parser/tagger/lemmatizer/attribute_ruler — only need
            # tok2vec + ner. Cuts load time + resident memory ~3x.
            nlp = spacy.load(
                model_id,
                disable=["parser", "tagger", "lemmatizer", "attribute_ruler"],
            )
            with _NLP_LOCK:
                _NLP_CACHE[lang] = nlp
            _log.info("[pii_ner] loaded %s (lang=%s)", model_id, lang)
        except Exception as e:
            _LOAD_FAILED.add(lang)
            _log.warning(
                "[pii_ner] failed to load %s: %s — NER disabled for lang=%s",
                model_id, e, lang,
            )


def is_available(lang: str = "de") -> bool:
    """True if the model is loaded and ready. False after a load failure
    or if `load_models` was never called for this language."""
    return lang in _NLP_CACHE


def unload_model(lang: str) -> bool:
    """Drop a language's loaded model from the cache, freeing its memory.
    Idempotent: returns True if the cache entry was present (now removed),
    False if it wasn't loaded. Clears the `_LOAD_FAILED` flag too so a
    subsequent `load_models` call retries fresh."""
    with _NLP_LOCK:
        nlp = _NLP_CACHE.pop(lang, None)
    _LOAD_FAILED.discard(lang)
    if nlp is None:
        return False
    _log.info("[pii_ner] unloaded lang=%s", lang)
    # No explicit del needed — dropping the reference is enough; spaCy's
    # Language object holds no OS-level handles requiring cleanup.
    return True


def list_loaded() -> list[dict]:
    """Snapshot of every known language's current state. One row per entry
    in KNOWN_LANGUAGES so the admin UI can render the catalogue even for
    languages that haven't been loaded yet. Stable order by `lang` for a
    deterministic UI."""
    out: list[dict] = []
    for lang in sorted(KNOWN_LANGUAGES.keys()):
        meta = KNOWN_LANGUAGES[lang]
        out.append({
            "lang": lang,
            "display": meta.get("display", lang),
            "model": meta.get("model", ""),
            "loaded": lang in _NLP_CACHE,
            "failed": lang in _LOAD_FAILED,
        })
    return out


def scan_text(text: str, *, lang: str = "de",
              max_findings: int = 100) -> list[dict]:
    """Run NER over `text`, return findings shaped like brain._pii_scan_text.

    Findings carry:
      rule_id   - 'name' | 'address' | 'organisation'
      label     - display label
      category  - 'contact' (matches PII_RULE_CATEGORIES)
      start     - char offset in `text`
      end       - char offset in `text`
      len       - end - start
      source    - 'ner' (for audit / debugging)

    `action` is NOT set here — the caller resolves it via
    _pii_effective_action so per-rule overrides apply uniformly.
    """
    if not text or not isinstance(text, str):
        return []
    if max_findings <= 0:
        return []
    nlp = _NLP_CACHE.get(lang)
    if nlp is None:
        return []  # model unavailable — graceful no-op
    try:
        # spaCy is thread-safe for inference if we don't mutate the pipeline.
        doc = nlp(text[:_MAX_SCAN_CHARS])
    except Exception as e:
        _log.warning("[pii_ner] scan failed (lang=%s, len=%d): %s",
                     lang, len(text), e)
        return []
    findings: list[dict] = []
    for ent in doc.ents:
        rule_id = _LABEL_MAP.get(ent.label_)
        if not rule_id:
            continue
        value = ent.text.strip()
        if len(value) < _MIN_ENTITY_CHARS:
            continue
        if not _passes_shape_gate(value, rule_id):
            continue
        findings.append({
            "rule_id": rule_id,
            "label": _LABEL_DISPLAY[rule_id],
            "start": ent.start_char,
            "end": ent.end_char,
            "len": ent.end_char - ent.start_char,
            "category": "contact",
            "source": "ner",
        })
        if len(findings) >= max_findings:
            break
    return findings
