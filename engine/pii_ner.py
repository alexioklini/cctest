"""spaCy NER PII detector.

Loads German NER model at startup, scans text for PER / LOC / ORG entities,
emits findings in the same shape as brain._pii_scan_text so the existing
pseudonymise pipeline picks them up unchanged.

Phase 1: German only. Phase 2 will add EN + RU and language routing.
"""

from __future__ import annotations

import logging
import re as _re
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
    # German (de_core_news_*) uses the WikiNER scheme (PER/LOC/ORG); English
    # (en_core_web_*) uses OntoNotes (PERSON/GPE/LOC/ORG). Map both so the
    # M9.3 English recall net can surface PERSON spans.
    "PER": "name",
    "PERSON": "name",
    "LOC": "address",
    "GPE": "address",
    "ORG": "organisation",
}

# M9.3-hardening (v9.349.0): the ENGLISH model runs ONLY as a narrow PERSON
# recall net, exactly like the `#recall` net — never as a full address/org
# detector. On mixed/German content en_core_web_md is grossly imprecise: it
# tags whole connective spans as PERSON ("Lebenslauf von Hristo …"), a bare
# German word as ORG ("Bitte"), and every country/nationality as GPE/NORP
# (Bulgaria, Balkan). Those false positives flooded the web-consent dialog in
# the live DD run and destroy usability. So English contributes PERSON only,
# and each PERSON span additionally passes the STRICT recall-net gate (all
# tokens capitalised, ≥2 substantial tokens, no stop/connective words) — not
# the loose main-model path that trusts the model's span. German is unchanged.
_MAIN_MODEL_LANGS = frozenset({"de"})  # default-trusted when no detection ran

# ── Language detection at the seam (v9.350.0) ────────────────────────────────
# The residual FP mode after v9.349 was the GERMAN model parsing ENGLISH prose
# (it tags English title-case as PER: "Risk-Based Approach", "May Have Saved
# Around", "CEO John Smith of Acme Corp" as ONE span) — and symmetrically the
# English model on German prose (handled by the strict gate). Fix: detect the
# language ONCE per scan (document level) and per finding (local window), both
# via cheap deterministic stopword counting — no new dependency, no model call.
#   * Document level: the dominant language's model becomes the TRUSTED main
#     model (loose path); the other runs as the strict PERSON-only recall net.
#     An English KYC file (>50% of the real corpus) is parsed by the English
#     model natively instead of producing German-model garbage.
#   * Span level: a TRUSTED-model finding whose local window is confidently the
#     OTHER language is dropped entirely — the other model is responsible
#     there (an English quote inside a German report). UNTRUSTED findings keep
#     the strict gate instead (never window-dropped: the whole point of the
#     recall net is English names inside German prose).
# Deliberately EXCLUSIVE stopword lists — a word that exists (even rarely) in
# the other language is omitted, so cross-lingual tokens ("in"/"an"/"am" are
# German AND English) never skew the count. Verified: no member appears as a
# common word of the other language.
_DE_STOPWORDS = frozenset({
    "der", "die", "das", "und", "ist", "nicht", "mit", "für", "von", "zu",
    "den", "dem", "ein", "eine", "auf", "als", "auch", "sich", "werden",
    "wurde", "bei", "nach", "aus", "über", "dass", "wird", "sind", "einer",
    "eines", "zum", "zur", "durch", "oder", "wie", "des", "um", "nur", "noch",
    "einem", "einen", "seiner", "ihrer", "wurden", "haben", "hatte",
})
_EN_STOPWORDS = frozenset({
    "the", "and", "of", "to", "that", "for", "with", "was", "on",
    "by", "from", "this", "are", "be", "has", "have", "not",
    "which", "its", "his", "her", "their", "were", "been", "or", "but",
    "they", "these", "those", "would", "should", "could", "about", "into",
})
_LANG_TOKEN_RE = _re.compile(r"[a-zA-ZäöüÄÖÜß]+")


def _stopword_counts(text: str) -> tuple[int, int]:
    de = en = 0
    for tok in _LANG_TOKEN_RE.findall(text.lower()):
        if tok in _DE_STOPWORDS:
            de += 1
        elif tok in _EN_STOPWORDS:
            en += 1
    return de, en


def _dominant_lang(text: str) -> str:
    """Document-level language: 'en' | 'de'. Defaults to 'de' (the UI/corpus
    default) unless English clearly dominates — a mixed report stays German."""
    de, en = _stopword_counts(text[:6000])
    return "en" if en > de * 1.2 and en >= 3 else "de"


def _window_lang(text: str, start: int, end: int) -> str:
    """Local language of a finding's surroundings: 'de' | 'en' | '' (ambiguous).
    Requires a CLEAR majority — ambiguity never drops anything."""
    lo, hi = max(0, start - 90), min(len(text), end + 90)
    de, en = _stopword_counts(text[lo:hi])
    if de >= 2 and de >= en * 2:
        return "de"
    if en >= 2 and en >= de * 2:
        return "en"
    return ""


def _lang_allows_label(trusted: bool, rule_id: str) -> bool:
    """An UNTRUSTED model contributes ONLY person NAMES — but from BOTH its
    PERSON and ORG labels, because en_core_web_md unreliably tags a real
    person's proper name as ORG on adverse-media prose ("Hristo Atanasov
    Kovachki" → ORG). Both are re-tested as `name` and must clear the strict
    span gate, so genuine orgs ("the European Prosecutor's Office") and bare
    words ("Bitte") don't sneak through. Untrusted never contributes address."""
    if trusted:
        return True
    return rule_id in ("name", "organisation")


def _strict_name_span_ok(value: str, toks: list[str]) -> bool:
    """The narrow recall-net name gate, factored out: EVERY token capitalised
    (kills lowercase connectives like 'von'/'of'/'the'), ≥2 substantial tokens,
    and no token is a stop/connective word. Used for the English recall net and
    the sm recall net so a swallowed multi-word span ('Lebenslauf von …',
    'the European Prosecutor') can never pass."""
    if len(toks) < 2 or not all(t[:1].isupper() for t in toks):
        return False
    if sum(1 for t in toks if len(t.rstrip(".")) >= 2) < 2:
        return False  # need two substantial tokens ("M M" never)
    if any(t.lower().strip(".,") in _RECALL_STOP_TOKENS for t in toks):
        return False
    return True

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


# ── Name-precision gate (opt-in) ─────────────────────────────────────────────
# The de_core_news_md model's dominant FP mode is tagging German common/compound
# nouns and product/tech terms as PER ("Datenschutzvorfall", "Benutzerkennwörter",
# "Cryptshare", "Delete Button"). The base shape gate only requires ONE
# capitalised token — and in German EVERY noun is capitalised, so it leaks. This
# tighter gate requires positive person-evidence before accepting a `name`:
#   1. a person honorific/title near the span (Herr/Frau/Dr./Mag./Prof. …), OR
#   2. >= 2 capitalised tokens, NONE of which looks like a German common noun
#      (noun-suffix) or a known tech/generic word.
# A lone capitalised token is never enough (precision-first). Verified on the
# kg-real-policies corpus: 12/12 real names kept, 21/21 spaCy FPs dropped.
# Gated by gdpr_scanner.name_precision_gate (default off until A/B-validated).
_HONORIFICS = {"herr", "frau", "hr", "fr", "dr", "mag", "prof", "dipl",
               "ing", "mmag", "ddr", "dipl.-ing", "frau dr", "herr dr",
               # v9.349.0: English/French honorifics — a KYC doc's name carries
               # "Mrs"/"Mr"/"Ms"/"Mme" as an appendage ("Bonnie M Mrs"), not a
               # name token. Stripped before the core-token count, and a
               # positive person-context signal in _name_has_person_context.
               "mr", "mrs", "ms", "miss", "sir", "mme", "mlle", "m.",
               "esq", "phd", "md"}
_NAME_NOUN_SUFFIX = _re.compile(
    r"(ung|ungen|heit|heiten|keit|keiten|schaft|schaften|tion|tionen|"
    r"rechten|recht|kontakte|vorfall|vorfalls|prinzip|vorschläge|wörter|"
    r"person|personen|verarbeitern|verarbeitung|button|binding|transformer|"
    # v9.349.0: German title-case-prose FP suffixes measured on the real DD
    # report (participles/adjectives/plurals/common report nouns that the
    # German model tags as PER: "Bestätigte Beteiligungen", "Dieser Bericht",
    # "Merkmal Daten", "Nationalität Bulgarisch", "Kopiert Drucken"). Verified
    # against 48 real German surnames — zero collateral. Adjectives that double
    # as surnames (Stark, Groß, Reich, Ernst…) are structurally safe: none end
    # in these syllables.
    r"igt|igte|iert|ierte|isch|bericht|daten|merkmal|kategorie|tät|icht|"
    r"bindung|beteiligung|unternehmen|kraftwerke|druck|drucken|kopiert)$",
    _re.IGNORECASE)
_NON_NAME_TOKENS = {"pre-trained", "transformer", "delete", "button", "admin",
                    "rechten", "binding", "data", "owner", "ticket", "review"}
# Recall-net stop tokens (v9.342.0): German function words / adverbs /
# participles that show up capitalized in title-cased prose ("Der Bericht
# Wurde Gestern Erstellt") — the _sm model's residual PER-FP mode after the
# shape checks. A real name never CONTAINS an inflected verb or adverb, so
# ONE hit kills the recall span. Kept small and unambiguous — adjectives
# that double as surnames (Stark, Groß, Klein…) must NOT be listed.
_RECALL_STOP_TOKENS = frozenset({
    "wurde", "wird", "werden", "wurden", "gestern", "heute", "morgen",
    "erstellt", "geprüft", "gemacht", "gelöscht", "geändert", "gesendet",
    "bitte", "danke", "keine", "kein", "alle", "viele", "immer", "wieder",
    "schon", "noch", "dann", "wenn", "aber", "oder", "nicht", "sehr",
    "auch", "sowie", "bereits", "zuerst", "zuletzt", "damit", "dabei",
    "dazu", "jedoch", "außerdem", "enthält", "haben", "hatte", "sind",
    # German connectives/prepositions that the English model swallows into a
    # PERSON span ("Lebenslauf VON Hristo …", "Prüfe DEN Lebenslauf").
    "von", "vom", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "prüfe", "lebenslauf", "vermögen",
    # English connectives/prepositions + generic nouns the OntoNotes model
    # over-includes ("THE European Prosecutor's OFFICE", "money LAUNDERING").
    "the", "of", "and", "for", "about", "with", "from", "to", "in", "on",
    "at", "by", "a", "an", "office", "prosecutor", "laundering", "money",
    "news", "latest", "report", "sector", "energy", "coal", "plant",
    "european", "public", "bank",
})
_HONORIFIC_NEAR = _re.compile(
    r"\b(Herr|Frau|Hr|Fr|Dr|Mag|Prof|Dipl|Ing|MMag|DDr)\.?\s*$")


def _name_has_person_context(text: str, start: int) -> bool:
    """True if a person honorific/title immediately precedes the span."""
    prefix = text[max(0, start - 12):start]
    return bool(_HONORIFIC_NEAR.search(prefix))


# Organisation FP mode: spaCy tags internal/legal abbreviations (ARL, DSG, UWG,
# VStG…) and concept compounds (KI-Gremium, KI-Systeme) as ORG. Real product/
# system names (SWIFT, ELBA, ZAK) are shape-identical to legal acronyms, so a
# blanket "short all-caps = drop" would kill them — instead we drop only (a) a
# curated legal/internal abbreviation stoplist and (b) KI-/IT-/EU- concept
# prefixes. Verified on kg-real-policies: 17/17 real orgs kept, 21/21 FPs dropped.
_ORG_CONCEPT_PREFIX = _re.compile(r"^(KI|IT|HR|EU|DSG|VVT)[- ]", _re.IGNORECASE)
_ORG_LEGAL_ABBR = {
    "arl", "anw", "dsg", "dsgvo", "bwg", "uwg", "vstg", "fm-gwg", "dpf", "dsb",
    "vvt", "mwi", "wpb", "gdpr", "bgb", "agb", "tom", "toms", "dsfa", "euc",
    "dor", "isms", "achtung", "kyc", "aml", "pb", "ikt",
}


def _passes_org_precision_gate(value: str) -> bool:
    t = value.strip()
    if _ORG_CONCEPT_PREFIX.match(t):
        return False
    if t.lower() in _ORG_LEGAL_ABBR:
        return False
    return True


# Address FP mode: spaCy's LOC tags bare toponyms (Wien, Österreich, Zürich) as
# `address`, and the existing person-proximity gate passes them because a person
# is usually nearby in policy text — but a bare city/country is not a person's
# identifiable address. spaCy also fragments real addresses to the street name
# only ("Seestraße"), dropping the number. So we require IDENTIFYING specificity:
# a house number or postal code in the span's immediate trailing context (the
# number sits right after the street: "Seestraße 27, 8002 Zürich"). Verified on
# handcrafted + kg-real-policies: 6/6 real street addresses kept, bare toponyms
# (Wien/Österreich/Zürich/Hamburg/Frankfurt) dropped.
# House number must sit IMMEDIATELY after the street name ("Seestraße 27"),
# allowing only a comma/space in between — anchored at the start of the trailing
# context. The old gate matched ANY 1-4 digit number within 30 chars, which
# falsely fired on "§ 25", "Abs. 5", "Nr. 3" further downstream (9.205.1 FP:
# "Auslagerungsvorhabens … § 25" passed because "25" was in-window).
_ADDR_HOUSE_NO = _re.compile(r"^[\s,]{0,3}\d{1,4}\s*[-–]?\s*\d{0,4}[a-zA-Z]?\b")
_ADDR_PLZ = _re.compile(r"\b\d{4,5}\b")
# A reference number, NOT a house number: "§ 25", "Abs. 5", "Nr. 3", "Art. 6".
_ADDR_REF_NUM = _re.compile(
    r"(?:§|\bAbs\.?|\bNr\.?|\bArt\.?|\bZ\.?|\blit\.?)\s*\d", _re.IGNORECASE)
# Single-token abstract German nouns spaCy mislabels as LOC — never an address.
# Suffix-based (covers -vorhaben(s), -ung(en), -heit, -keit, -schaft, -prozess…).
_ADDR_NOUN_SUFFIX = _re.compile(
    r"(vorhaben|vorhabens|ung|ungen|heit|keit|schaft|prozess|prozesses|"
    r"verfahren|verfahrens|wesen|tätigkeit|massnahme|maßnahme|massnahmen|"
    r"maßnahmen|konzept|konzepts|projekt|projekts|projektes)$", _re.IGNORECASE)


def _passes_address_precision_gate(value: str, text: str, start: int) -> bool:
    v = value.strip()
    # Reject a lone abstract noun mislabelled as a place (single token, no space).
    if " " not in v and _ADDR_NOUN_SUFFIX.search(v):
        return False
    end = start + len(value)
    after = text[end:end + 30]
    # A house number must be ADJACENT (anchored) and not a §/Abs./Nr. reference.
    if _ADDR_HOUSE_NO.search(after):
        return True
    # A PLZ anywhere in the window still qualifies (postal codes are 4-5 digits,
    # distinctive), but only if it isn't a reference number like "§ 25".
    m = _ADDR_PLZ.search(after)
    if m and not _ADDR_REF_NUM.search(after[max(0, m.start() - 6):m.end()]):
        return True
    return False


# v9.349.0 calibration (35 real analysis chats, 99 typed user messages):
# form/document field labels + legal-doc terms the model tags as PER
# ("Given Names", "Mentions Spéciales", "Bahamian Dormant Accounts
# Regulations"). A real person name never CONTAINS one of these as a whole
# token. Verified against the full real-name set from the calibration + a
# surname corpus — zero collateral.
_NAME_FORM_LABEL_TOKENS = frozenset({
    "names", "surname", "surnames", "mentions", "spéciales", "spéciale",
    "accounts", "account", "regulations", "regulation", "dormant", "given",
    "holder", "bearer", "signature", "nationality", "residence", "issuing",
})
# OCR / garbled-span noise tokens: short function words / fragments that show
# up inside mangled OCR spans ("Ex Verwaltete Les Otc", "De TOT", "Basia Us")
# but are never a substantial token of a real name. Kept SHORT and unambiguous.
# NB: several of these double as NOBILIARY PARTICLES in real names (van/le/la/
# du — "Vincent van Gogh", "Le Pen", "Du Pont"): a noise token therefore kills
# the span ONLY when it is NOT immediately followed by a substantial non-noise
# token (particle position). "Les Otc" kills (next is noise); "Le Pen" keeps.
# Particle-sensitive noise: these double as nobiliary particles in real names
# (van Gogh, Le Pen, Du Pont) → they only mark garble when NOT in particle
# position (not followed by a substantial name token).
_NAME_OCR_NOISE_TOKENS = frozenset({
    "us", "tot", "les", "otc", "ex", "het", "du", "le", "la", "van",
})
# Unconditional-drop tokens: country / bloc / currency codes a garbled OCR span
# appends as a "name token" ("EEALASKA USA"). NEVER a name token, any position.
_NAME_HARD_NOISE_TOKENS = frozenset({
    "usa", "uk", "eu", "uae", "usd", "eur", "gbp", "chf",
})


def _looks_like_name_noise(toks: list) -> bool:
    """True if a token list is a form-label / legal-doc / OCR-noise span rather
    than a real person name. `toks` = the span's tokens in original order."""
    low = [t.lower().strip(".,") for t in toks]
    if any(t in _NAME_FORM_LABEL_TOKENS for t in low):
        return True
    if len(low) >= 2 and any(t in _NAME_HARD_NOISE_TOKENS for t in low):
        return True
    if len(low) >= 2:
        for i, t in enumerate(low):
            if t not in _NAME_OCR_NOISE_TOKENS:
                continue
            nxt = low[i + 1] if i + 1 < len(low) else ""
            # Nobiliary-particle position: followed by a substantial (≥3 chars)
            # non-noise token → part of a real name, not garble.
            if nxt and len(nxt) >= 3 and nxt not in _NAME_OCR_NOISE_TOKENS:
                continue
            return True
    return False


def _passes_name_precision_gate(value: str, text: str, start: int) -> bool:
    v = value.strip()
    # Form-label / legal-doc / OCR-noise spans are rejected UNCONDITIONALLY —
    # even with a person nearby (a KYC doc's "Given Names:" label sits right
    # next to the real name, so the person-context early-return would wrongly
    # keep it). Runs before _name_has_person_context. Full token order is
    # passed so the nobiliary-particle position rule works ("Van der Berg").
    if _looks_like_name_noise(
            [t for t in _re.split(r"\s+", v.replace(",", " ")) if t]):
        return False
    if _name_has_person_context(text, start):
        return True
    toks = [t for t in _re.split(r"\s+", v.replace(",", " ")) if t]
    core = [t for t in toks if t.lower().strip(".") not in _HONORIFICS]
    cap = [t for t in core if t[:1].isupper() and t[:1].isalpha() and len(t) >= 2]
    if "." in v and len(cap) < 2:        # "Behoerde.dotx", "II."
        return False
    if len(cap) >= 2:
        if any(_NAME_NOUN_SUFFIX.search(t) or t.lower() in _NON_NAME_TOKENS
               for t in cap):
            return False
        return True
    return False                          # lone token is never a name


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
    # recall_model: a SECOND, small model whose PERSON spans are unioned in
    # as a recall net (v9.342.0). Measured miss: de_core_news_md does NOT tag
    # "Bonnie M Stark" in "Prüfe die KO-Kundin Bonnie M Stark aus Oregon
    # City: …" — de_core_news_sm does. The union is deliberately narrow
    # (PER only, ≥2 capitalized tokens, non-overlapping, same shape/precision
    # gates) because _sm's known FP mode is lowercase function-words — which
    # the token-shape check excludes structurally. FP cost is a modal
    # question; a miss is cleartext egress (handover §4.2 asymmetry).
    "de": {"display": "Deutsch", "model": "de_core_news_md",
           "recall_model": "de_core_news_sm"},
    # M9.3 (G12): English recall net. The KYC/DD corpus is majority non-German;
    # German NER on English content is inconsistent, so we run en_core_web_md in
    # addition to de and UNION the findings (see the NER pass in _pii_scan_text).
    # No recall_model — the union is already the recall lever here.
    "en": {"display": "English", "model": "en_core_web_md"},
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
            continue
        # Recall net (best-effort): the small second model whose PERSON
        # spans are unioned in by scan_text. Absence is fine — the main
        # model keeps working alone.
        recall_id = KNOWN_LANGUAGES.get(lang, {}).get("recall_model") or ""
        if recall_id:
            try:
                import spacy
                nlp_r = spacy.load(
                    recall_id,
                    disable=["parser", "tagger", "lemmatizer",
                             "attribute_ruler"],
                )
                with _NLP_LOCK:
                    _NLP_CACHE[lang + "#recall"] = nlp_r
                _log.info("[pii_ner] loaded recall net %s (lang=%s)",
                          recall_id, lang)
            except Exception as e:
                _log.warning(
                    "[pii_ner] recall net %s unavailable (%s) — main "
                    "model only for lang=%s", recall_id, e, lang,
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
        _NLP_CACHE.pop(lang + "#recall", None)
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
              max_findings: int = 100,
              name_precision: bool = False,
              trusted: bool | None = None) -> list[dict]:
    """Run NER over `text`, return findings shaped like brain._pii_scan_text.

    Findings carry:
      rule_id   - 'name' | 'address' | 'organisation'
      label     - display label
      category  - from PII_RULE_CATEGORIES ('contact' for name/address,
                  'business_id' for organisation)
      start     - char offset in `text`
      end       - char offset in `text`
      len       - end - start
      source    - 'ner' (for audit / debugging)

    `trusted` (v9.350.0): whether this model is the TRUSTED main model for the
    text's dominant language (loose path, all labels) or the strict PERSON-only
    recall contributor. None = legacy default (`lang in _MAIN_MODEL_LANGS`);
    `_pii_scan_text` passes it explicitly from `_dominant_lang(text)`.

    `action` is NOT set here — the caller resolves it via
    _pii_effective_action so per-rule overrides apply uniformly.
    """
    if not text or not isinstance(text, str):
        return []
    if max_findings <= 0:
        return []
    if trusted is None:
        trusted = lang in _MAIN_MODEL_LANGS
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
        # M9.3-hardening: an untrusted model contributes only person NAMES
        # (from PERSON and ORG labels — en mislabels real names as ORG on
        # adverse-media prose); its GPE/LOC are noise (Bulgaria, Balkan).
        if not _lang_allows_label(trusted, rule_id):
            continue
        value = ent.text.strip()
        if len(value) < _MIN_ENTITY_CHARS:
            continue
        # Untrusted: coerce ORG→name and require the STRICT span gate (all
        # tokens capitalised, ≥2 substantial, no stop/connective words). Keeps
        # a mislabelled real name ("Hristo Atanasov Kovachki"→ORG) while
        # dropping genuine orgs and bare words. The trusted model keeps its
        # loose path.
        if not trusted:
            if not _strict_name_span_ok(value, value.split()):
                continue
            rule_id = "name"
        else:
            # v9.350.0 span-level language check: a TRUSTED-model finding whose
            # local window is confidently the OTHER language is garbage-prone
            # (the German model tags English title-case prose as PER: "May Have
            # Saved Around") — drop it; the other language's model is
            # responsible for that window. Never applied to untrusted findings
            # (the recall net exists precisely for foreign names in this
            # language's prose).
            _wl = _window_lang(text, ent.start_char, ent.end_char)
            if _wl and _wl != lang:
                continue
        if not _passes_shape_gate(value, rule_id):
            continue
        # Opt-in NER-precision gate: tighten `name` (German-common-noun FP mode)
        # and `organisation` (legal-abbrev / concept-prefix FP mode). Caller
        # passes name_precision from cfg (gdpr_scanner.name_precision_gate).
        if name_precision and rule_id == "name" and \
                not _passes_name_precision_gate(value, text, ent.start_char):
            continue
        if name_precision and rule_id == "organisation" and \
                not _passes_org_precision_gate(value):
            continue
        if name_precision and rule_id == "address" and \
                not _passes_address_precision_gate(value, text, ent.start_char):
            continue
        findings.append({
            "rule_id": rule_id,
            "label": _LABEL_DISPLAY[rule_id],
            "start": ent.start_char,
            "end": ent.end_char,
            "len": ent.end_char - ent.start_char,
            # Category from the rule map — `organisation` lives under
            # `business_id` (a legal entity is not a natural person), the
            # rest under `contact`. Was hardcoded 'contact', which mislabelled
            # ORG findings in the audit view (action resolution was never
            # affected — _pii_effective_action resolves by rule_id).
            "category": PII_RULE_CATEGORIES.get(rule_id, "contact"),
            "source": "ner",
        })
        if len(findings) >= max_findings:
            break

    # Recall net (v9.342.0): union in PERSON spans the small second model
    # sees but the main model missed (measured: de_core_news_md drops
    # "Bonnie M Stark" in a German sentence; _sm tags it). Deliberately
    # narrow — names only, ≥2 capitalized tokens (structurally excludes
    # _sm's lowercase-function-word FP mode), no overlap with any main
    # finding, and the same shape/precision gates as the main pass.
    nlp_r = _NLP_CACHE.get(lang + "#recall")
    if nlp_r is not None and len(findings) < max_findings:
        try:
            doc_r = nlp_r(text[:_MAX_SCAN_CHARS])
        except Exception:
            doc_r = None
        if doc_r is not None:
            taken = [(f["start"], f["end"]) for f in findings]
            for ent in doc_r.ents:
                if _LABEL_MAP.get(ent.label_) != "name":
                    continue
                value = ent.text.strip()
                if len(value) < _MIN_ENTITY_CHARS:
                    continue
                if any(ent.start_char < e and ent.end_char > s
                       for s, e in taken):
                    continue  # overlaps a main-model finding
                if not _strict_name_span_ok(value, value.split()):
                    continue  # <2 caps / connective / stop-word → prose, not a name
                if not _passes_shape_gate(value, "name"):
                    continue
                if name_precision and not _passes_name_precision_gate(
                        value, text, ent.start_char):
                    continue
                findings.append({
                    "rule_id": "name",
                    "label": _LABEL_DISPLAY["name"],
                    "start": ent.start_char,
                    "end": ent.end_char,
                    "len": ent.end_char - ent.start_char,
                    "category": PII_RULE_CATEGORIES.get("name", "contact"),
                    "source": "ner",
                })
                taken.append((ent.start_char, ent.end_char))
                if len(findings) >= max_findings:
                    break
    return findings


# === GDPR / PII regex scanner (relocated from brain.py, B3) ============
# Pure regex + stdlib detection of personal data. Mirrors the browser-side
# PIIScanner in web/index.html. Moved here from brain.py so the regex half
# and the NER half (above) live together. The regex scanner has ZERO heavy
# dependencies (re + stdlib only) and runs without spaCy — spaCy stays
# lazy-imported in load_models(). DELICATE rule-ordering invariants: do NOT
# reorder _PII_RULES (first-match-wins + overlap suppression). brain.py
# re-exports _pii_rules / _pii_scan_text / _pii_scan_bare_identifiers /
# PII_RULE_CATEGORIES / PII_DEFAULT_CATEGORY_ACTIONS for back-compat.

PII_RULE_CATEGORIES: dict[str, str] = {
    # Tier 2 — cloud secrets / API keys / credentials. Always highest severity.
    "pem_private_key": "secrets", "aws_access_key": "secrets",
    "aws_secret_key": "secrets", "github_app_token": "secrets",
    "github_pat": "secrets", "slack_token": "secrets",
    "slack_webhook": "secrets", "google_api_key": "secrets",
    "google_oauth_client": "secrets", "stripe_live": "secrets",
    "stripe_test": "secrets", "openai_key": "secrets",
    "anthropic_key": "secrets", "twilio_sid": "secrets",
    "sendgrid_key": "secrets", "mailgun_key": "secrets",
    "jwt": "secrets", "azure_storage_conn": "secrets",
    "azure_account_key": "secrets", "basic_auth_url": "secrets",
    "generic_secret_assignment": "secrets",

    # Tier 1 — national IDs with checksum validation.
    "de_steuerid": "national_id", "uk_nino": "national_id",
    "uk_nhs": "national_id", "nl_bsn": "national_id",
    "be_national": "national_id", "pl_pesel": "national_id",
    "pt_nif": "national_id", "se_personnummer": "national_id",
    "dk_cpr": "national_id", "no_fnr": "national_id",
    "ch_ahv": "national_id", "cz_rc": "national_id",
    "ro_cnp": "national_id", "hu_taj": "national_id",
    "gr_amka": "national_id", "bg_egn": "national_id",
    "ie_pps": "national_id", "br_cpf": "national_id",
    "ca_sin": "national_id",
    "mx_curp": "national_id", "ar_dni": "national_id",
    "in_aadhaar": "national_id", "jp_mynumber": "national_id",
    "kr_rrn": "national_id", "sg_nric": "national_id",
    "tw_nid": "national_id", "at_svnr": "national_id",
    "fr_insee": "national_id", "es_dni_nie": "national_id",
    "it_codicefiscale": "national_id", "us_ssn": "national_id",
    "us_ssn_ctx": "national_id",

    # Context-fallback — keyword + shape, no checksum. Softer category.
    "svnr_ctx": "national_id_ctx", "ssn_ctx_loose": "national_id_ctx",
    "insurance_number_ctx": "national_id_ctx",
    "id_card_ctx": "national_id_ctx", "drivers_license_ctx": "national_id_ctx",
    "passport_ctx_loose": "national_id_ctx", "health_insurance_ctx": "national_id_ctx",

    # Financial
    "iban": "financial", "credit_card": "financial",
    "bank_account_ctx": "financial",

    # Business / legal-entity identifiers — NOT a natural person, so not
    # personal data under GDPR. Detected for the audit view but default-ignore.
    "br_cnpj": "business_id", "tax_id_ctx": "business_id",
    "organisation": "business_id",

    # Contact info (emails + phone) — often intentional, allowlist-aware
    "email": "contact", "phone": "contact",

    # Network identifiers (often infrastructure, not personal data)
    "ipv4": "network", "ipv6": "network",

    # Biographical / personal-document identifiers
    "passport": "personal", "dob": "personal", "date": "personal",
    "mrz": "personal",
    # Synthetic rules from the checksum-verified MRZ read of an attached ID
    # document (upload-time attachment scan; GDPR_ALL_CHECKS_PRE_DIALOG_PLAN).
    # Deliberately "personal", NOT "contact" like the NER name rule — the
    # contact default action is ignore, which would hide a checksum-anchored
    # passport-holder name from the pre-send dialog.
    "mrz_name": "personal", "mrz_passport": "personal", "mrz_dob": "personal",

    # spaCy NER findings (Phase 1: German). Sit in the `contact` category
    # alongside email/phone — soft PII the user often includes deliberately
    # in chat, so the default `ignore` action matches that ergonomics; admins
    # who want stricter handling flip the category to warn/block in Settings.
    # rule_ids minted in engine/pii_ner.py — keep in sync.
    "name": "contact",
    # address moves to 'personal' (warn) but only fires when person-name-gated
    # — see the address context gate in _pii_scan_text. organisation moves to
    # business_id (above). name stays contact/ignore (noisy sm-model PER tags).
    "address": "personal",

    # Heuristic fallback
    "bare_identifier": "bare_id",
}

# Default category actions. "block" means refuse (or swap to local) when
# server_block is true; downgraded to "warn" when server_block is false.
PII_DEFAULT_CATEGORY_ACTIONS: dict[str, str] = {
    "secrets":         "block",
    "national_id":     "warn",
    "national_id_ctx": "warn",
    "financial":       "warn",
    "contact":         "ignore",
    "network":         "ignore",
    "personal":        "warn",
    "bare_id":         "warn",
    "business_id":     "ignore",   # company/legal-entity IDs — not personal data
}

# German UI labels per category. Single source of truth for the Settings → GDPR
# panel (moved here from web/js/utils.js when the browser-side scanner was
# removed in 9.200.0 — the client now renders the catalog from the server config
# instead of a duplicated JS object).
PII_CATEGORY_LABELS: dict[str, str] = {
    "secrets":         "Secrets & API-Keys",
    "national_id":     "Nationale IDs (prüfsummengeprüft)",
    "national_id_ctx": "ID-ähnliche Werte (kontextbasiert)",
    "financial":       "Finanzen (IBAN, Karten, Konten)",
    "business_id":     "Unternehmens-IDs (keine personenbezogenen Daten)",
    "contact":         "Kontaktdaten (E-Mails, Telefon, Namen)",
    "network":         "Netzwerkadressen (IP)",
    "personal":        "Biografisch (Reisepass, Geburtsdatum, Adresse)",
    "bare_id":         "Reine numerische Bezeichner",
}

# Per-rule human labels (German) for the Settings → GDPR per-rule expander.
# Was PIIScanner.rules[*].label in the browser; moved server-side with the
# scanner removal. Server-only rules (spaCy NER) included.
PII_RULE_LABELS: dict[str, str] = {
    "pem_private_key": "Privater Schlüssel",
    "aws_access_key": "AWS-Access-Key-ID",
    "aws_secret_key": "AWS Secret Access Key",
    "github_app_token": "GitHub-App-Token",
    "github_pat": "Persönliches GitHub-Zugriffstoken",
    "slack_token": "Slack token", "slack_webhook": "Slack webhook URL",
    "google_api_key": "Google-API-Key",
    "google_oauth_client": "Google-OAuth-Client-ID",
    "stripe_live": "Stripe-Live-Key", "stripe_test": "Stripe-Test-Key",
    "openai_key": "OpenAI-API-Key", "anthropic_key": "Anthropic-API-Key",
    "twilio_sid": "Twilio-Account-SID", "sendgrid_key": "SendGrid-API-Key",
    "mailgun_key": "Mailgun-API-Key", "jwt": "JWT",
    "azure_storage_conn": "Azure-Storage-Verbindungszeichenfolge",
    "azure_account_key": "Azure-Account-Key",
    "basic_auth_url": "Zugangsdaten in URL",
    "generic_secret_assignment": "Fest codiertes Secret",
    "email": "E-Mail-Adresse", "iban": "IBAN",
    "ipv4": "IPv4-Adresse", "ipv6": "IPv6-Adresse",
    "us_ssn": "US-Sozialversicherungsnummer",
    "us_ssn_ctx": "US-Sozialversicherungsnummer",
    "at_svnr": "Österreichische Sozialversicherungsnummer",
    "fr_insee": "Französische INSEE / NIR",
    "de_steuerid": "Deutsche Steuer-ID",
    "uk_nino": "UK National Insurance Number", "uk_nhs": "UK-NHS-Nummer",
    "nl_bsn": "Niederländische BSN", "be_national": "Belgische Nationalnummer",
    "pl_pesel": "Polnische PESEL", "pt_nif": "Portugiesische NIF",
    "se_personnummer": "Schwedische Personnummer", "dk_cpr": "Dänische CPR",
    "no_fnr": "Norwegische Fødselsnummer", "ch_ahv": "Schweizer AHV",
    "cz_rc": "Tschechische rodné číslo", "ro_cnp": "Rumänische CNP",
    "hu_taj": "Ungarische TAJ", "gr_amka": "Griechische AMKA",
    "bg_egn": "Bulgarische EGN", "ie_pps": "Irische PPS",
    "br_cpf": "Brasilianische CPF", "br_cnpj": "Brasilianische CNPJ",
    "ca_sin": "Kanadische SIN", "mx_curp": "Mexikanische CURP",
    "ar_dni": "Argentinische DNI", "in_aadhaar": "Indische Aadhaar",
    "jp_mynumber": "Japanische My Number", "kr_rrn": "Koreanische RRN",
    "sg_nric": "Singapurische NRIC/FIN", "tw_nid": "Taiwanesische National-ID",
    "credit_card": "Kreditkartennummer", "phone": "Telefonnummer",
    "es_dni_nie": "Spanische DNI/NIE",
    "it_codicefiscale": "Italienische Codice Fiscale",
    "passport": "Reisepassnummer (heuristisch)", "dob": "Geburtsdatum",
    "date": "Datum (personenbezogen-gegated)",
    "mrz": "MRZ (maschinenlesbare Ausweiszone)",
    "mrz_name": "Name (MRZ, Ausweisdokument)",
    "mrz_passport": "Passnummer (MRZ, prüfziffern-validiert)",
    "mrz_dob": "Geburtsdatum (MRZ, Ausweisdokument)",
    "svnr_ctx": "Sozialversicherungsnummer (wahrscheinlich)",
    "ssn_ctx_loose": "Sozialversicherungsnummer (wahrscheinlich)",
    "tax_id_ctx": "Steueridentifikationsnummer (wahrscheinlich)",
    "insurance_number_ctx": "Versicherungsnummer (wahrscheinlich)",
    "id_card_ctx": "Ausweisnummer (wahrscheinlich)",
    "drivers_license_ctx": "Führerscheinnummer (wahrscheinlich)",
    "passport_ctx_loose": "Reisepassnummer (wahrscheinlich)",
    "bank_account_ctx": "Bankkontonummer (wahrscheinlich)",
    "health_insurance_ctx": "Krankenversicherungsnummer (wahrscheinlich)",
    # Server-only (spaCy NER, German)
    "name": "Name (spaCy NER, Deutsch)",
    "address": "Adresse / Ort (spaCy NER, Deutsch)",
    "organisation": "Organisation (spaCy NER, Deutsch)",
    "bare_identifier": "Reiner numerischer Bezeichner",
}

# Per-rule minimum DISTINCT occurrences required before a rule fires for a
# document. A rule contributes ZERO findings unless ≥ N distinct matched values
# appear (counted per whole document; chat = per message). Default 1 (fire on
# first match) for any rule not listed. Tuned to suppress false positives on
# number-dense / prose documents without losing genuine repeated PII. Admin-
# overridable per rule via gdpr_scanner.min_occurrences in config.json.
# Rationale per rule captured in the 2026-06-07 rule review.
PII_DEFAULT_MIN_OCCURRENCES: dict[str, int] = {
    # Context-gated date — only person-linked dates, AND ≥10 distinct.
    "date": 10,
    # Weak checksum-only bare-digit national IDs.
    "jp_mynumber": 10,
    "pl_pesel": 5, "no_fnr": 5, "gr_amka": 5, "bg_egn": 5, "ro_cnp": 5,
    "be_national": 5, "se_personnummer": 5, "ca_sin": 5, "uk_nhs": 5,
    "cz_rc": 5, "es_dni_nie": 5, "dk_cpr": 5,
    "at_svnr": 3, "us_ssn": 3,
    # Financial bare-shape.
    "credit_card": 3, "phone": 3,
    # Hard-coded secret (after the entropy/length bar is raised).
    "generic_secret_assignment": 3,
    # Keyword-anchored loose-shape context fallbacks (no checksum).
    "svnr_ctx": 3, "ssn_ctx_loose": 3, "insurance_number_ctx": 3,
    "id_card_ctx": 3, "drivers_license_ctx": 3, "passport_ctx_loose": 3,
    "bank_account_ctx": 3, "health_insurance_ctx": 3,
}


# Per-CATEGORY plain-language rationale (German) — why a finding in this
# category is a GDPR concern. Surfaced in the Data-view document reviewer as
# the tooltip text on each highlighted violation. Keyed by the category names
# in PII_RULE_CATEGORIES. A per-rule override (below) wins when present.
PII_CATEGORY_WHY: dict[str, str] = {
    "secrets":
        "Zugangsdaten / Geheimnis. Schlüssel, Token oder Passwörter dürfen "
        "niemals an externe Dienste übermittelt werden — Offenlegung ermöglicht "
        "unmittelbaren Missbrauch.",
    "national_id":
        "Staatliche Personenkennziffer (z. B. Steuer-ID, Sozialversicherungs-"
        "nummer). Eindeutig einer natürlichen Person zuordenbar — besonders "
        "schützenswertes personenbezogenes Datum nach Art. 9 / 87 DSGVO.",
    "national_id_ctx":
        "Mögliche staatliche Kennziffer (über Schlüsselwort + Form erkannt, "
        "ohne Prüfziffer). Wenn echt, eindeutig personenbeziehbar.",
    "financial":
        "Finanzdatum (IBAN, Kreditkarte, Kontonummer). Erlaubt Zahlungs- und "
        "Identitätsmissbrauch und ist personenbeziehbar.",
    "contact":
        "Kontaktdatum (E-Mail, Telefon, Name). Personenbezogenes Datum nach "
        "Art. 4 DSGVO — auch wenn häufig bewusst geteilt.",
    "personal":
        "Personenbezogenes Datum (Adresse, Geburts-/Lebensdatum, Ausweis). "
        "Einer natürlichen Person zuordenbar.",
    "network":
        "Netzwerk-Identifikator (IP-Adresse). Kann nach DSGVO als "
        "personenbeziehbar gelten, häufig jedoch Infrastruktur.",
    "business_id":
        "Identifikator einer juristischen Person (Firma/Behörde). KEIN "
        "personenbezogenes Datum nach DSGVO — nur informativ erfasst.",
    "bare_id":
        "Unstrukturierte ID-förmige Zahlenfolge. Heuristisch erkannt; "
        "möglicherweise eine Kennziffer.",
}

# Per-RULE rationale override (German) — for rules whose category text is too
# generic. Keyed by rule_id. Falls through to PII_CATEGORY_WHY when absent.
PII_RULE_WHY: dict[str, str] = {
    "email":   "E-Mail-Adresse — personenbezogenes Kontaktdatum (Art. 4 DSGVO).",
    "phone":   "Telefonnummer — personenbezogenes Kontaktdatum.",
    "iban":    "IBAN — Bankverbindung, erlaubt Zahlungs-/Identitätsmissbrauch.",
    "credit_card": "Kreditkartennummer (Luhn-geprüft) — Zahlungsdatum.",
    "de_steuerid": "Deutsche Steuer-Identifikationsnummer — eindeutige "
                   "staatliche Personenkennziffer.",
    "name":    "Personenname (NER erkannt) — personenbezogenes Datum.",
    "address": "Anschrift, einer Person zugeordnet — personenbezogenes Datum.",
    "dob":     "Geburtsdatum — besonders schützenswertes personenbezogenes Datum.",
    "mrz":     "Maschinenlesbare Ausweiszone (ICAO 9303) — bündelt Name, "
               "Geburtsdatum und Dokumentennummer in einer Zeile.",
    "ipv4":    "IPv4-Adresse — möglicher Netzwerk-/Personenbezug.",
    "ipv6":    "IPv6-Adresse — möglicher Netzwerk-/Personenbezug.",
}


def pii_finding_why(rule_id: str, category: str = "") -> str:
    """Plain-language (German) explanation of why a finding is a GDPR concern.

    Per-rule override wins; otherwise category text; otherwise a generic line.
    Used by engine.doc_review to annotate each violation for the UI tooltip.
    """
    if rule_id in PII_RULE_WHY:
        return PII_RULE_WHY[rule_id]
    cat = category or PII_RULE_CATEGORIES.get(rule_id, "personal")
    return PII_CATEGORY_WHY.get(
        cat, "Mögliches personenbezogenes Datum nach DSGVO.")


_PII_RULES: list[dict] = []


def _pii_rules() -> list[dict]:
    """Return compiled PII rules. Built lazily. Mirrors PIIScanner.rules in web/index.html."""
    global _PII_RULES
    if _PII_RULES:
        return _PII_RULES

    import re as _re

    def _digits(s): return "".join(c for c in s if c.isdigit())

    def _luhn_str(s: str) -> bool:
        d = _digits(s)
        if not d: return False
        total, alt = 0, False
        for c in reversed(d):
            n = int(c)
            if alt:
                n *= 2
                if n > 9: n -= 9
            total += n
            alt = not alt
        return total % 10 == 0

    def _cc_ok(m: str) -> bool:
        d = _digits(m)
        return 13 <= len(d) <= 19 and _luhn_str(d)

    def _iban_ok(s: str) -> bool:
        iban = "".join(s.split()).upper()
        if not 15 <= len(iban) <= 34: return False
        rearr = iban[4:] + iban[:4]
        num = ""
        for c in rearr:
            if "A" <= c <= "Z": num += str(ord(c) - 55)
            elif c.isdigit(): num += c
            else: return False
        rem = 0
        for d in num: rem = (rem * 10 + int(d)) % 97
        return rem == 1

    def _phone_ok(m: str) -> bool:
        d = _digits(m)
        return 8 <= len(d) <= 15

    def _mrz_line_ok(m: str) -> bool:
        # Structural validator (L2b): data line = number(9) chk nat(3)
        # dob(6) chk sex expiry(6) chk …; name line = doc-type/issuer prefix
        # then SURNAME<<GIVENS (letters + fillers only). A random ALL-CAPS
        # heading matches neither shape.
        s = m.strip()
        if _re.match(r"^[A-Z0-9<]{9}\d[A-Z<]{3}\d{6}\d[MFX<]\d{6}\d", s):
            return True
        return bool(_re.match(r"^[A-Z][A-Z<][A-Z<]{3}[A-Z<]*<<[A-Z<]+$", s))

    def _ipv4_ok(m: str) -> bool:
        # The match string may carry a leading context keyword (e.g.
        # "Gateway 192.168.1.1"), so extract the dotted-quad before validating.
        g = _re.search(r"(?:\d{1,3}\.){3}\d{1,3}", m)
        if not g:
            return False
        quad = g.group(0)
        return not any(quad.startswith(p) for p in ("0.", "127.", "255.", "169.254."))

    def _us_ssn_dashed_ok(m: str) -> bool:
        a, b, c = m.split("-")
        if a in ("000", "666") or a.startswith("9"): return False
        return b != "00" and c != "0000"

    def _us_ssn_ctx_ok(m: str) -> bool:
        g = _re.search(r"\d{9}", m)
        if not g: return False
        s = g.group(0)
        a, b, c = s[:3], s[3:5], s[5:]
        if a in ("000", "666") or a.startswith("9"): return False
        return b != "00" and c != "0000"

    def _at_svnr_ok(m: str) -> bool:
        if len(m) != 10 or not m.isdigit(): return False
        w = [3, 7, 9, 5, 8, 4, 2, 1, 6]
        d = [int(c) for c in m]
        vals = [d[0], d[1]] + d[3:]
        if sum(x * y for x, y in zip(vals, w)) % 11 != d[2]: return False
        dd, mm = int(m[4:6]), int(m[6:8])
        return 1 <= dd <= 31 and 1 <= mm <= 12

    def _fr_insee_ok(m: str) -> bool:
        clean = "".join(c if c.isdigit() else ("0" if c.upper() in "AB " else "") for c in m)
        if len(clean) != 15: return False
        body, key = clean[:13], int(clean[13:])
        return (97 - (int(body) % 97)) == key

    def _de_steuerid_ok(m: str) -> bool:
        g = _re.search(r"\d{11}", m)
        if not g: return False
        d = g.group(0)
        if d[0] == "0": return False
        counts: dict[str, int] = {}
        for c in d: counts[c] = counts.get(c, 0) + 1
        repeats = [n for n in counts.values() if n > 1]
        return len(repeats) == 1 and repeats[0] in (2, 3)

    def _dni_nie_ok(m: str) -> bool:
        s = m.upper()
        letters = "TRWAGMYFPDXBNJZSQVHLCKE"
        try:
            if s[0] in "XYZ":
                num = int(str("XYZ".index(s[0])) + s[1:-1])
            else:
                num = int(s[:-1])
        except ValueError:
            return False
        return letters[num % 23] == s[-1]

    # ── EU national IDs ──

    def _uk_nhs_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 10: return False
        s = sum(int(d[i]) * (10 - i) for i in range(9))
        chk = 11 - (s % 11)
        if chk == 11: chk = 0
        return chk != 10 and chk == int(d[9])

    def _nl_bsn_ok(m: str) -> bool:
        g = _re.search(r"\d{8,9}", m)
        if not g: return False
        d = g.group(0).rjust(9, "0")
        if int(d) == 0: return False
        w = [9,8,7,6,5,4,3,2,-1]
        return sum(int(d[i]) * w[i] for i in range(9)) % 11 == 0

    def _be_national_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 11: return False
        body9 = d[:9]; chk = int(d[9:])
        a = 97 - (int(body9) % 97)
        b = 97 - (int("2" + body9) % 97)
        return chk in (a, b)

    def _pl_pesel_ok(m: str) -> bool:
        w = [1,3,7,9,1,3,7,9,1,3]
        s = sum(int(m[i]) * w[i] for i in range(10))
        chk = (10 - (s % 10)) % 10
        if chk != int(m[10]): return False
        mm = int(m[2:4])
        return (1 <= mm <= 12) or (21 <= mm <= 32) or (41 <= mm <= 52) or (61 <= mm <= 72) or (81 <= mm <= 92)

    def _pt_nif_ok(m: str) -> bool:
        g = _re.search(r"\d{9}", m)
        if not g: return False
        d = g.group(0)
        if d[0] not in "123568 9": return False
        s = sum(int(d[i]) * (9 - i) for i in range(8))
        chk = 11 - (s % 11)
        if chk >= 10: chk = 0
        return chk == int(d[8])

    def _se_personnummer_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) not in (10, 12): return False
        short = d[2:] if len(d) == 12 else d
        s = 0
        for i in range(9):
            n = int(short[i]) * (2 if i % 2 == 0 else 1)
            if n > 9: n -= 9
            s += n
        chk = (10 - (s % 10)) % 10
        return chk == int(short[9])

    def _dk_cpr_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 10: return False
        dd, mm = int(d[:2]), int(d[2:4])
        return 1 <= dd <= 31 and 1 <= mm <= 12

    def _no_fnr_ok(m: str) -> bool:
        if len(m) != 11: return False
        d = [int(c) for c in m]
        w1 = [3,7,6,1,8,9,4,5,2]
        w2 = [5,4,3,2,7,6,5,4,3,2]
        s1 = sum(d[i] * w1[i] for i in range(9))
        k1 = 11 - (s1 % 11)
        if k1 == 11: k1 = 0
        if k1 == 10 or k1 != d[9]: return False
        s2 = sum(d[i] * w2[i] for i in range(10))
        k2 = 11 - (s2 % 11)
        if k2 == 11: k2 = 0
        return k2 != 10 and k2 == d[10]

    def _ch_ahv_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 13 or not d.startswith("756"): return False
        s = sum(int(d[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
        return (10 - (s % 10)) % 10 == int(d[12])

    def _cz_rc_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) not in (9, 10): return False
        if len(d) == 10:
            n = int(d)
            if n % 11 != 0 and not (n % 11 == 10 and d[9] == "0"): return False
        mm = int(d[2:4])
        real = mm - 50 if mm > 50 else mm
        return 1 <= real <= 12

    def _ro_cnp_ok(m: str) -> bool:
        if len(m) != 13: return False
        w = [2,7,9,1,4,6,3,5,8,2,7,9]
        s = sum(int(m[i]) * w[i] for i in range(12))
        chk = s % 11
        if chk == 10: chk = 1
        if chk != int(m[12]): return False
        mm, dd = int(m[3:5]), int(m[5:7])
        return 1 <= mm <= 12 and 1 <= dd <= 31

    def _hu_taj_ok(m: str) -> bool:
        g = _re.search(r"\d{3}[- ]?\d{3}[- ]?\d{3}", m)
        if not g: return False
        d = _digits(g.group(0))
        if len(d) != 9: return False
        s = sum(int(d[i]) * (3 if i % 2 == 0 else 7) for i in range(8))
        return (s % 10) == int(d[8])

    def _gr_amka_ok(m: str) -> bool:
        dd, mm = int(m[:2]), int(m[2:4])
        if dd < 1 or dd > 31 or mm < 1 or mm > 12: return False
        return _luhn_str(m)

    def _bg_egn_ok(m: str) -> bool:
        w = [2,4,8,5,10,9,7,3,6]
        s = sum(int(m[i]) * w[i] for i in range(9))
        chk = (s % 11) % 10
        if chk != int(m[9]): return False
        mm = int(m[2:4])
        real = mm - 40 if mm > 40 else (mm - 20 if mm > 20 else mm)
        return 1 <= real <= 12

    def _ie_pps_ok(m: str) -> bool:
        s = m.upper()
        if len(s) not in (8, 9): return False
        digits = s[:7]; check = s[7]
        letters = "WABCDEFGHIJKLMNOPQRSTUV"
        w = [8,7,6,5,4,3,2]
        total = sum(int(digits[i]) * w[i] for i in range(7))
        if len(s) == 9:
            extra = 0 if s[8] == "W" else (ord(s[8]) - 64)
            total += extra * 9
        return letters[total % 23] == check

    # ── Americas + APAC ──

    def _br_cpf_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 11 or d == d[0] * 11: return False
        def calc(end):
            s = sum(int(d[i]) * (end + 1 - i) for i in range(end))
            r = (s * 10) % 11
            return 0 if r == 10 else r
        return calc(9) == int(d[9]) and calc(10) == int(d[10])

    def _br_cnpj_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 14 or d == d[0] * 14: return False
        w1 = [5,4,3,2,9,8,7,6,5,4,3,2]
        w2 = [6,5,4,3,2,9,8,7,6,5,4,3,2]
        def calc(end, ws):
            s = sum(int(d[i]) * ws[i] for i in range(end))
            r = s % 11
            return 0 if r < 2 else 11 - r
        return calc(12, w1) == int(d[12]) and calc(13, w2) == int(d[13])

    def _ca_sin_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 9 or d[0] in ("0", "8"): return False
        return _luhn_str(d)

    def _in_aadhaar_ok(m: str) -> bool:
        # Verhoeff — m may include keyword prefix, extract 12 digits
        g = _re.search(r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}", m)
        if not g: return False
        d = _digits(g.group(0))
        if len(d) != 12: return False
        d2 = [
            [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
            [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
            [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
            [9,8,7,6,5,4,3,2,1,0]]
        p = [
            [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
            [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
            [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
        c = 0
        rev = [int(x) for x in reversed(d)]
        for i, x in enumerate(rev):
            c = d2[c][p[i % 8][x]]
        return c == 0

    def _jp_mynumber_ok(m: str) -> bool:
        w = [6,5,4,3,2,7,6,5,4,3,2]
        s = sum(int(m[i]) * w[i] for i in range(11))
        r = s % 11
        chk = 0 if r <= 1 else 11 - r
        return chk == int(m[11])

    def _kr_rrn_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 13: return False
        w = [2,3,4,5,6,7,8,9,2,3,4,5]
        s = sum(int(d[i]) * w[i] for i in range(12))
        chk = (11 - (s % 11)) % 10
        if chk != int(d[12]): return False
        mm, dd = int(d[2:4]), int(d[4:6])
        return 1 <= mm <= 12 and 1 <= dd <= 31

    def _sg_nric_ok(m: str) -> bool:
        if len(m) != 9: return False
        first, digits, check = m[0], m[1:8], m[8]
        w = [2,7,6,5,4,3,2]
        s = sum(int(digits[i]) * w[i] for i in range(7))
        if first in ("T", "G"): s += 4
        if first == "M": s += 3
        r = s % 11
        tables = {
            "S": "JZIHGFEDCBA", "T": "JZIHGFEDCBA",
            "F": "XWUTRQPNMLK", "G": "XWUTRQPNMLK",
            "M": "KLJNPQRTUWX",
        }
        t = tables.get(first)
        return bool(t) and t[r] == check

    def _tw_nid_ok(m: str) -> bool:
        mp = {"A":10,"B":11,"C":12,"D":13,"E":14,"F":15,"G":16,"H":17,"I":34,"J":18,"K":19,"L":20,"M":21,"N":22,"O":35,"P":23,"Q":24,"R":25,"S":26,"T":27,"U":28,"V":29,"W":32,"X":30,"Y":31,"Z":33}
        pref = mp.get(m[0])
        if pref is None: return False
        first, second = pref // 10, pref % 10
        digits = [first, second] + [int(c) for c in m[1:]]
        w = [1,9,8,7,6,5,4,3,2,1,1]
        return sum(digits[i] * w[i] for i in range(len(digits))) % 10 == 0

    # ── Tier 2 validators ──

    def _basic_auth_ok(m: str) -> bool:
        return not _re.search(r"://[^:]*:(password|changeme|example|xxx+|\*+)@", m, _re.IGNORECASE)

    def _generic_secret_ok(m: str) -> bool:
        # Bar raised 2026-06-07: length >=24 AND >=10 distinct chars (was 20/6)
        # to cut false positives on config IDs / hashes-as-labels.
        g = _re.search(r"[\"']([A-Za-z0-9+/=_\-]{24,})[\"']", m)
        if not g: return False
        v = g.group(1)
        if _re.fullmatch(r"(?:xxx+|\*+|changeme|example|placeholder|your[_-]?(?:key|token|secret))", v, _re.IGNORECASE):
            return False
        return len(set(v)) >= 10

    _PII_RULES = [
        # ── Tier 2: cloud secrets (distinct prefixes → high priority) ──
        {"id": "pem_private_key", "label": "Private key",
         "re": _re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]{1,10000}?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")},
        {"id": "aws_access_key", "label": "AWS access key ID",
         "re": _re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])")},
        {"id": "aws_secret_key", "label": "AWS secret access key",
         "re": _re.compile(r"(?:aws_secret_access_key|aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)[\s:=\"']*([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+=])", _re.IGNORECASE)},
        {"id": "github_app_token", "label": "GitHub app token",
         "re": _re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")},
        {"id": "github_pat", "label": "GitHub personal access token",
         "re": _re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")},
        {"id": "slack_token", "label": "Slack token",
         "re": _re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,200}\b")},
        {"id": "slack_webhook", "label": "Slack webhook URL",
         "re": _re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+")},
        {"id": "google_api_key", "label": "Google API key",
         "re": _re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")},
        {"id": "google_oauth_client", "label": "Google OAuth client ID",
         "re": _re.compile(r"\b\d{12}-[a-z0-9]{32}\.apps\.googleusercontent\.com\b")},
        {"id": "stripe_live", "label": "Stripe live key",
         "re": _re.compile(r"\b(?:sk|rk|pk)_live_[0-9a-zA-Z]{24,99}\b")},
        {"id": "stripe_test", "label": "Stripe test key",
         "re": _re.compile(r"\b(?:sk|rk|pk)_test_[0-9a-zA-Z]{24,99}\b")},
        {"id": "openai_key", "label": "OpenAI API key",
         "re": _re.compile(r"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20,}\b")},
        {"id": "anthropic_key", "label": "Anthropic API key",
         "re": _re.compile(r"\bsk-ant-[a-z0-9]{2,6}-[A-Za-z0-9_\-]{85,120}\b")},
        {"id": "twilio_sid", "label": "Twilio account SID",
         "re": _re.compile(r"\bAC[a-f0-9]{32}\b")},
        {"id": "sendgrid_key", "label": "SendGrid API key",
         "re": _re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")},
        {"id": "mailgun_key", "label": "Mailgun API key",
         "re": _re.compile(r"\bkey-[a-f0-9]{32}\b")},
        {"id": "jwt", "label": "JWT",
         "re": _re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")},
        {"id": "azure_storage_conn", "label": "Azure Storage connection string",
         "re": _re.compile(r"DefaultEndpointsProtocol=https;AccountName=[A-Za-z0-9]+;AccountKey=[A-Za-z0-9+/=]{80,};?(?:EndpointSuffix=[^;\s]+)?")},
        {"id": "azure_account_key", "label": "Azure account key",
         "re": _re.compile(r"(?:AccountKey|SharedAccessKey)=([A-Za-z0-9+/=]{40,100})(?=[;\"'\s]|$)")},
        {"id": "basic_auth_url", "label": "Credentials in URL",
         "re": _re.compile(r"\b(?:https?|ftp|ssh|git|postgres|postgresql|mysql|mongodb|redis)://[^\s:@/]+:[^\s@/]+@[A-Za-z0-9.\-]+"),
         "ok": _basic_auth_ok},
        {"id": "generic_secret_assignment", "label": "Hard-coded secret",
         "re": _re.compile(r"\b(?:api[_-]?key|secret|token|password|passwd|pwd|auth|bearer)[\s:=]{1,4}[\"']([A-Za-z0-9+/=_\-]{24,})[\"']", _re.IGNORECASE),
         "ok": _generic_secret_ok},

        # ── Context-gated first (keyword+digits beats bare-digits rules below) ──
        {"id": "de_steuerid", "label": "German Steuer-ID",
         "re": _re.compile(r"(?:\bSteuer[- ]?ID\b|Steueridentifikationsnummer|\bTIN\b)[^\d\n]{0,20}(\d{11})(?!\d)", _re.IGNORECASE),
         "ok": _de_steuerid_ok},

        # ── Standard identifiers ──
        {"id": "email", "label": "Email address",
         "re": _re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")},
        {"id": "iban", "label": "IBAN",
         # Country code + check digits + body. Optional spaces only BETWEEN
         # body characters, never at the tail — `[A-Z0-9](?:[ ]?[A-Z0-9])*`
         # forces the match to end on an alphanumeric, so the regex can't
         # greedily swallow a trailing space (which then surfaces in the
         # mapping dict as e.g. `'DE89…000 '` and confuses the audit view).
         "re": _re.compile(r"\b[A-Z]{2}\d{2}[ ]?[A-Z0-9](?:[ ]?[A-Z0-9]){10,29}\b"),
         "ok": _iban_ok},
        # IPv4 — CONTEXT-REQUIRED. A bare dotted quad like 20.2.4.3 is
        # byte-identical to a document clause/section number (e.g. "Formular
        # 20.2.4.3S", numbered criteria lists "20.2.2.0 20.2.2.1 …"), so the
        # octet-validated shape alone is NOT sufficient — it produced pure
        # false positives across the policy corpus. Fire only when an IP-ish
        # keyword sits immediately before the address. The capture group holds
        # the address; _ipv4_ok validates the captured quad (group 1).
        {"id": "ipv4", "label": "IPv4 address",
         "re": _re.compile(
             r"(?:\bIP(?:v4)?(?:[- ]?Adresse|[- ]?address)?\b|\bAdresse\b|"
             r"\bGateway\b|\bSubnet(?:z)?\b|\bNetmask\b|\bNetzmaske\b|"
             r"\bDNS\b|\bHost\b|\bServer\b|\bRouter\b|\bFirewall\b)"
             r"[^\d\n]{0,20}"
             r"((?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
             r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d))(?!\d)",
             _re.IGNORECASE),
         "ok": _ipv4_ok},
        {"id": "ipv6", "label": "IPv6 address",
         "re": _re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b")},
        {"id": "us_ssn", "label": "US Social Security Number",
         "re": _re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
         "ok": _us_ssn_dashed_ok},
        {"id": "us_ssn_ctx", "label": "US Social Security Number",
         "re": _re.compile(r"(?:\bSSN\b|\bsocial\s+security\b)[^\w\n]{0,15}\d{9}(?!\d)", _re.IGNORECASE),
         "ok": _us_ssn_ctx_ok},

        # ── Tier 1 EU national IDs ──
        {"id": "uk_nino", "label": "UK National Insurance Number",
         "re": _re.compile(r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z][0-9]{6}[A-D]?\b")},
        {"id": "uk_nhs", "label": "UK NHS number",
         "re": _re.compile(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{4}(?!\d)"),
         "ok": _uk_nhs_ok},
        {"id": "nl_bsn", "label": "Dutch BSN",
         "re": _re.compile(r"(?:\bBSN\b|burgerservicenummer|sofinummer)[^\d\n]{0,15}(\d{8,9})(?!\d)", _re.IGNORECASE),
         "ok": _nl_bsn_ok},
        {"id": "be_national", "label": "Belgian national number",
         "re": _re.compile(r"(?<!\d)\d{2}[. ]?\d{2}[. ]?\d{2}[- ]?\d{3}[. ]?\d{2}(?!\d)"),
         "ok": _be_national_ok},
        {"id": "pl_pesel", "label": "Polish PESEL",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _pl_pesel_ok},
        {"id": "pt_nif", "label": "Portuguese NIF",
         "re": _re.compile(r"(?:\bNIF\b|número\s+fiscal|contribuinte)[^\d\n]{0,15}(\d{9})(?!\d)", _re.IGNORECASE),
         "ok": _pt_nif_ok},
        {"id": "se_personnummer", "label": "Swedish personnummer",
         "re": _re.compile(r"(?<!\d)(?:\d{2})?\d{6}[-+]?\d{4}(?!\d)"),
         "ok": _se_personnummer_ok},
        {"id": "dk_cpr", "label": "Danish CPR",
         # Keyword-anchored (2026-06-07): the bare DDMMYY-#### shape with only a
         # date-validity gate (no checksum — DK abolished it) over-fired on any
         # date+4-digit pair. Now require a CPR/personnummer label within ~20
         # chars before. `_dk_cpr_ok` runs on the full match but extracts digits
         # via _digits(), and the label carries no digits, so the 10-digit CPR
         # validity check is unaffected.
         "re": _re.compile(r"(?:\bCPR\b|CPR[- ]?nr\.?|CPR[- ]?nummer|personnummer)[^\d\n]{0,20}((?<!\d)\d{6}[- ]?\d{4}(?!\d))", _re.IGNORECASE),
         "ok": _dk_cpr_ok},
        {"id": "no_fnr", "label": "Norwegian fødselsnummer",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _no_fnr_ok},
        {"id": "ch_ahv", "label": "Swiss AHV (OASI)",
         "re": _re.compile(r"\b756[.\- ]?\d{4}[.\- ]?\d{4}[.\- ]?\d{2}\b"),
         "ok": _ch_ahv_ok},
        {"id": "cz_rc", "label": "Czech rodné číslo",
         "re": _re.compile(r"(?<!\d)\d{6}/?\d{3,4}(?!\d)"),
         "ok": _cz_rc_ok},
        {"id": "ro_cnp", "label": "Romanian CNP",
         "re": _re.compile(r"(?<!\d)\d{13}(?!\d)"),
         "ok": _ro_cnp_ok},
        {"id": "hu_taj", "label": "Hungarian TAJ",
         "re": _re.compile(r"(?:\bTAJ\b|társadalom|társadalombiztos)[^\d\n]{0,15}(\d{3}[- ]?\d{3}[- ]?\d{3})(?!\d)", _re.IGNORECASE),
         "ok": _hu_taj_ok},
        {"id": "gr_amka", "label": "Greek AMKA",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _gr_amka_ok},
        {"id": "bg_egn", "label": "Bulgarian EGN",
         "re": _re.compile(r"(?<!\d)\d{10}(?!\d)"),
         "ok": _bg_egn_ok},
        {"id": "ie_pps", "label": "Irish PPS",
         "re": _re.compile(r"\b\d{7}[A-W][A-IW]?\b"),
         "ok": _ie_pps_ok},

        # ── Tier 1 Americas + APAC ──
        {"id": "br_cpf", "label": "Brazilian CPF",
         "re": _re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
         "ok": _br_cpf_ok},
        {"id": "br_cnpj", "label": "Brazilian CNPJ",
         "re": _re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
         "ok": _br_cnpj_ok},
        {"id": "ca_sin", "label": "Canadian SIN",
         "re": _re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}(?!\d)"),
         "ok": _ca_sin_ok},
        {"id": "mx_curp", "label": "Mexican CURP",
         "re": _re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", _re.IGNORECASE)},
        {"id": "ar_dni", "label": "Argentine DNI",
         "re": _re.compile(r"\bDNI[\s:]*\d{1,2}\.?\d{3}\.?\d{3}\b", _re.IGNORECASE)},
        {"id": "in_aadhaar", "label": "Indian Aadhaar",
         "re": _re.compile(r"(?:\baadhaar\b|\bUID\b|\bUIDAI\b)[^\d\n]{0,20}([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)", _re.IGNORECASE),
         "ok": _in_aadhaar_ok},
        {"id": "jp_mynumber", "label": "Japanese My Number",
         "re": _re.compile(r"(?<!\d)\d{12}(?!\d)"),
         "ok": _jp_mynumber_ok},
        {"id": "kr_rrn", "label": "Korean RRN",
         "re": _re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)"),
         "ok": _kr_rrn_ok},
        {"id": "sg_nric", "label": "Singapore NRIC/FIN",
         "re": _re.compile(r"\b[STFGM]\d{7}[A-Z]\b"),
         "ok": _sg_nric_ok},
        {"id": "tw_nid", "label": "Taiwan national ID",
         "re": _re.compile(r"\b[A-Z][12]\d{8}\b"),
         "ok": _tw_nid_ok},

        # ── Other checksum IDs ──
        {"id": "at_svnr", "label": "Austrian Sozialversicherungsnummer",
         "re": _re.compile(r"(?<!\d)\d{10}(?!\d)"),
         "ok": _at_svnr_ok},
        {"id": "fr_insee", "label": "French INSEE / NIR",
         "re": _re.compile(r"(?<!\d)[12]\d{2}(?:0[1-9]|1[0-2]|[2-9]\d)(?:\d{2}|\dA|\dB)\d{3}\d{3}[\s ]?\d{2}(?!\d)", _re.IGNORECASE),
         "ok": _fr_insee_ok},
        {"id": "es_dni_nie", "label": "Spanish DNI/NIE",
         "re": _re.compile(r"(?<![A-Z0-9])(?:[XYZ]?\d{7,8}[A-HJ-NP-TV-Z])(?![A-Z0-9])", _re.IGNORECASE),
         "ok": _dni_nie_ok},
        {"id": "it_codicefiscale", "label": "Italian Codice Fiscale",
         "re": _re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", _re.IGNORECASE)},

        # ── Credit card (after national IDs) ──
        {"id": "credit_card", "label": "Credit card number",
         "re": _re.compile(r"(?<![+\d])(?:\d[ -]?){13,19}(?!\d)"),
         "ok": _cc_ok},

        # ── Phone (after national IDs) ──
        {"id": "phone", "label": "Phone number",
         "re": _re.compile(r"(?:(?<![\w.])\+\d{1,3}[\s().-]?(?:\d[\s().-]?){7,14}\d|(?<!\d)\d{3}[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\d))"),
         "ok": _phone_ok},

        # ── Machine-readable zone (ICAO 9303) — L2b. Whole OCR line of MRZ
        # alphabet; the structural validator (_mrz_line_ok) keeps only real
        # name/data lines. Runs BEFORE the keyword-gated passport rules so
        # the full line is claimed as ONE span and rebuilt consistently by
        # pseudonymizer._fake_mrz (valid check digits) instead of digit
        # groups being nibbled by other rules. ──
        # Line-START anchored only: real OCR lines carry trailing garble
        # ('… P', >44-char confusion tails) that a $-anchor would let escape
        # entirely; the span stops at the MRZ alphabet edge and the trailing
        # junk stays raw (harmless), while the structural validator keeps
        # mid-prose ALLCAPS runs out.
        {"id": "mrz", "label": "Machine-readable zone (ICAO 9303)",
         "re": _re.compile(r"(?m)^[A-Z0-9<]{25,44}"),
         "ok": _mrz_line_ok},

        # ── Context-gated heuristics ──
        {"id": "passport", "label": "Passport number",
         "re": _re.compile(r"passport[^\w\n]{0,20}([A-Z][0-9]{6,9}|[A-Z]{1,2}[0-9]{6,8})", _re.IGNORECASE)},
        {"id": "dob", "label": "Date of birth",
         "re": _re.compile(r"(?:\b(?:DOB|born|date\s+of\s+birth|geboren|geburtsdatum|né|née|nacido)\b[^\n]{0,20}?(?:\d{1,2}[\/.\- ]\d{1,2}[\/.\- ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.?\s(?i:jan|feb|mar|mär|apr|may|mai|jun|jul|aug|sep|okt|oct|nov|dez|dec)[a-zäöüß]*\.?\s\d{2,4}))", _re.IGNORECASE)},

        # ── Context-fallback: fire on keyword + number-shape even if checksum
        # fails. Runs LAST — strict checksum rules above still win first. ──
        {"id": "svnr_ctx", "label": "Social-insurance number (likely)",
         "re": _re.compile(r"(?:\bSVNR\b|\bSV[- ]?Nr\.?\b|\bSV[- ]?Nummer\b|Sozialversicherungsnummer|social[- ]?insurance|national[- ]?insurance|\bNIN\b)[^\d\n]{0,20}(\d[\d \-\/.]{7,19}\d)", _re.IGNORECASE)},
        {"id": "ssn_ctx_loose", "label": "Social Security Number (likely)",
         "re": _re.compile(r"(?:\bSSN\b|social[- ]?security[- ]?(?:number|no\.?|\#)?)[^\d\n]{0,15}(\d{3}[- ]?\d{2}[- ]?\d{4}|\d{9})", _re.IGNORECASE)},
        {"id": "tax_id_ctx", "label": "Tax identification number (likely)",
         "re": _re.compile(r"(?:\bTIN\b|tax[- ]?id(?:entification)?[- ]?(?:number|no\.?)?|Steuer[- ]?ID|Steuernummer|USt[- ]?ID|VAT[- ]?(?:number|no\.?))[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{6,18}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "insurance_number_ctx", "label": "Insurance number (likely)",
         "re": _re.compile(r"(?:insurance[- ]?number|insurance[- ]?no\.?|Versicherungsnummer|numéro[- ]?(?:de[- ]?)?sécurité[- ]?sociale|numero[- ]?(?:di[- ]?)?previdenza)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{6,19}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "id_card_ctx", "label": "ID / identity card number (likely)",
         "re": _re.compile(r"(?:\bID[- ]?(?:number|no\.?|card)\b|Personalausweis|carte[- ]?d['\s-]identit|documento[- ]?(?:de[- ]?)?identi[dt]ad|cédula)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,16}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "drivers_license_ctx", "label": "Driver's license number (likely)",
         "re": _re.compile(r"(?:driver'?s?[- ]?licen[sc]e|Führerschein|permis[- ]?de[- ]?conduire|carnet[- ]?de[- ]?conducir|patente)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,16}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "passport_ctx_loose", "label": "Passport number (likely)",
         "re": _re.compile(r"(?:passport|Reisepass|passeport|pasaporte|passaporto)[^\w\n]{0,20}([A-Z0-9][A-Z0-9\- ]{5,14}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "bank_account_ctx", "label": "Bank account number (likely)",
         "re": _re.compile(r"(?:\baccount[- ]?(?:number|no\.?|\#)\b|\bacct\.?[- ]?(?:no\.?|\#)?\b|\bIBAN\b|Kontonummer|numéro[- ]?de[- ]?compte|número[- ]?de[- ]?cuenta)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-/.]{7,30}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "health_insurance_ctx", "label": "Health insurance number (likely)",
         "re": _re.compile(r"(?:health[- ]?insurance|Krankenversicherungsnummer|Krankenkasse|assurance[- ]?maladie|seguridad[- ]?social|Medicare|Medicaid|\bNHS[- ]?(?:number|no\.?)?|\bAMKA\b|\bTAJ\b)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,19}[A-Z0-9])", _re.IGNORECASE)},

        # ── Dates ─────────────────────────────────────────────────────
        # Runs last in the rules list so every national-ID + IBAN +
        # credit-card rule above gets first claim on their digit groups
        # via overlap suppression (otherwise YYYY-MM-DD would steal digits
        # from a 16-digit credit card start, dd.mm.yyyy from a Steuer-ID
        # context, etc.). Word-bounded so dates inside larger digit blocks
        # don't fire. Years constrained to 19xx/20xx to suppress noise
        # from arbitrary number triplets like "10/100/2030 ratio".
        {"id": "date", "label": "Date",
         "re": _re.compile(
             r"\b("
             r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}"          # ISO 2026-05-19
             # EXIF 2026:07:02 [14:24:48] — month/day validated in-pattern
             r"|(?:19|20)\d{2}:(?:0[1-9]|1[0-2]):(?:0[1-9]|[12]\d|3[01])"
             r"(?:\s\d{2}:\d{2}:\d{2})?"
             # Textual month (EN/DE, abbrev or full): 5 FEB 1947 / 26. Jan 2027
             r"|\d{1,2}\.?\s(?i:jan|feb|mar|mär|apr|may|mai|jun|jul|aug|sep"
             r"|okt|oct|nov|dez|dec)[a-zäöüß]*\.?\s(?:19|20)\d{2}"
             r"|\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}"        # 19.05.2026
             r"|\d{1,2}-\d{1,2}-(?:19|20)\d{2}"          # 19-05-2026
             r"|\d{1,2}/\d{1,2}/(?:19|20)\d{2}"          # 05/19/2026
             r"|\d{1,2}\.\d{1,2}\.\d{2}"                 # 19.05.26 (2-digit yr)
             r"|\d{1,2}/\d{1,2}/\d{2}"                   # 05/19/26
             r")\b"
         )},
    ]
    return _PII_RULES


def _pii_scan_bare_identifiers(text: str) -> list[dict]:
    """Heuristic: flag pasted lists of bare numeric identifiers. Fires when the
    message is dominated (>=60%) by 9-14-digit ID-shaped lines with little prose.
    Catches the 'what is this number?' paste case where the value fails all
    strict checksums but is clearly identifier-shaped."""
    import re as _re
    if not text or not isinstance(text, str) or len(text) > 2000:
        return []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    id_like = [l for l in lines if _re.fullmatch(r"\d[\d .\-/]{7,18}\d", l)]
    threshold = max(1, (len(lines) * 6 + 9) // 10)  # ceil(len*0.6)
    if len(id_like) < threshold:
        return []
    findings: list[dict] = []
    for line in id_like:
        digits = _re.sub(r"\D", "", line)
        if not 9 <= len(digits) <= 14:
            continue
        idx = text.find(line)
        if idx < 0:
            continue
        findings.append({
            "rule_id": "bare_identifier",
            "label": "Numeric identifier (unverified)",
            "start": idx,
            "end": idx + len(line),
            "len": len(line),
        })
        if len(findings) >= 20:
            break
    return findings


# Max char distance for a date/address to count as "near" a person name
# (same or adjacent sentence). Tuned in the 2026-06-07 rule review.
_DATE_ADDRESS_NAME_PROXIMITY = 120

# Birth / life-event keywords that make a nearby date personal even without an
# NER name (the merged `dob` logic — geboren/born/died/married/hire/leave).
_BIRTH_CONTEXT_RE = _re.compile(
    r"\b(?:geboren|geburts(?:datum|tag)|geb\.|born|birthday|birth\s*date|date\s+of\s+birth|\bDOB\b|"
    r"n[ée]|né|née|nacido|nata|nato|"
    r"gestorben|verstorben|died|deceased|d[ée]c[ée]d[ée]|"
    r"heirat|verheiratet|married|mariage|"
    r"eingestellt|eintritt|einstellungsdatum|date\s+of\s+hire|hired|"
    r"austritt|ausgeschieden|date\s+of\s+leaving)(?!\w)",
    _re.IGNORECASE)


def _name_distance(start: int, end: int, name_spans: list) -> int | None:
    """Smallest char-gap from [start,end) to any person-name span, or None if
    there are no name spans. 0 = adjacent/overlapping. Used both as a gate
    (gap <= max_dist) and as a CONFIDENCE signal (closer name → higher trust)."""
    best = None
    for ns, ne in name_spans:
        if ne <= start:
            gap = start - ne
        elif ns >= end:
            gap = ns - end
        else:
            gap = 0
        if best is None or gap < best:
            best = gap
    return best


def _name_within(start: int, end: int, name_spans: list, max_dist: int) -> bool:
    """True if any person-name span is within `max_dist` chars of [start,end)."""
    d = _name_distance(start, end, name_spans)
    return d is not None and d <= max_dist


def _birth_context_distance(text: str, start: int, end: int,
                            window: int = 30) -> int | None:
    """Char-gap from the span to the nearest birth/life-event keyword within
    `window`, or None if none. 0 = keyword touches the span."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    seg = text[lo:hi]
    nearest = None
    for m in _BIRTH_CONTEXT_RE.finditer(seg):
        ks, ke = lo + m.start(), lo + m.end()
        if ke <= start:
            gap = start - ke
        elif ks >= end:
            gap = ks - end
        else:
            gap = 0
        if nearest is None or gap < nearest:
            nearest = gap
    return nearest


def _date_has_birth_context(text: str, start: int, end: int,
                            window: int = 30) -> bool:
    """True if a birth/life-event keyword sits within `window` chars before/after
    the date span — the merged dob check (date + born/geboren/hire/…)."""
    return _birth_context_distance(text, start, end, window) is not None


# ── Per-finding confidence (evidence-based, NOT a calibrated probability) ────
# A transparent 0..1 score derived from the evidence each finding already
# carries. Intended to drive a future threshold ladder (ignore < ask <
# anonymise/fallback) — exposed now, thresholds added later once score
# distributions are observed. Honest about its nature: this is rule/evidence
# strength, not a statistical P(correct).
#
# Tiers (base by detection strength):
#   checksum-validated structured PII (IBAN/CC/national-IDs w/ a validator) 0.98
#   secrets w/ structural validator (key prefix / JWT shape)                0.95
#   context-keyword-anchored (Steuer-ID, ipv4, *_ctx rules)                 0.85
#   NER passing the precision gate (name+honorific, address+number, org)    0.78
#   NER passing only the base shape gate                                    0.55
#   loose/bare patterns (no checksum, no context anchor)                    0.50
# Modifiers: +0.02 per extra distinct occurrence over the min (capped +0.06);
#            secrets floored at 0.90 (a leaked key is high-stakes even if the
#            pattern is loose — bias the score toward catching it).

# Rules whose match is gated by a checksum / structural validator (the `ok` fn
# in _pii_rules). Kept as an explicit set so the confidence model is auditable
# and independent of regex-parsing the rules table.
_PII_CHECKSUM_RULES = {
    "iban", "credit_card", "br_cpf", "br_cnpj", "ca_sin", "in_aadhaar",
    "us_ssn", "fr_insee", "es_dni_nie", "it_codicefiscale", "nl_bsn",
    "pl_pesel", "be_national", "pt_nif", "se_personnummer", "dk_cpr",
    "no_fnr", "ch_ahv", "cz_rc", "ro_cnp", "hu_taj", "gr_amka", "bg_egn",
    "ie_pps", "at_svnr", "de_steuerid", "kr_rrn", "sg_nric", "tw_nid",
    "jp_mynumber", "mx_curp", "ar_dni", "uk_nino", "uk_nhs",
    # Structural validator (ICAO-9303 line shape) — same trust tier.
    "mrz",
}
# Context-keyword-anchored rules (the *_ctx family + keyword-gated ipv4/passport).
_PII_CONTEXT_RULES = {
    "svnr_ctx", "ssn_ctx_loose", "insurance_number_ctx", "id_card_ctx",
    "drivers_license_ctx", "passport_ctx_loose", "health_insurance_ctx",
    "bank_account_ctx", "tax_id_ctx", "ipv4", "ipv6", "passport", "dob",
}


# Score anchors the per-rule COUNT calibration maps onto. The two per-rule
# count-points (c_lo, c_hi) say: "this rule reaches SCORE_LO at c_lo distinct
# occurrences and SCORE_HI at c_hi". The default global band thresholds
# (gdpr_scanner.confidence_lower / _upper) are aligned to these anchors so the
# count bands the user described land in the intended band:
#   email count-points (3, 7): 1-2 occ → <lower (ignore), 3-6 → mid (ask),
#   >=7 → high (act). Per-rule overridable; SCORE anchors are global constants.
_PII_SCORE_LO = 0.50    # == default confidence_lower
_PII_SCORE_HI = 0.85    # == default confidence_upper
_PII_SCORE_BELOW = 0.30  # count below c_lo floor (still surfaced for audit)


def _count_calibrated_score(occurrences: int, c_lo: int, c_hi: int) -> float:
    """Map a distinct-occurrence count onto a 0..1 score via the rule's two
    count-points. Piecewise-linear: 1→BELOW, c_lo→SCORE_LO, c_hi→SCORE_HI,
    saturating above c_hi. This is the user's calibration: counts are how each
    rule's score crosses the GLOBAL lower/upper thresholds."""
    n = max(1, occurrences)
    c_lo = max(1, c_lo)
    c_hi = max(c_lo + 1, c_hi)
    if n <= 1:
        return _PII_SCORE_BELOW
    if n < c_lo:
        # ramp from BELOW(at 1) to SCORE_LO(at c_lo)
        frac = (n - 1) / max(1, c_lo - 1)
        return _PII_SCORE_BELOW + frac * (_PII_SCORE_LO - _PII_SCORE_BELOW)
    if n < c_hi:
        frac = (n - c_lo) / (c_hi - c_lo)
        return _PII_SCORE_LO + frac * (_PII_SCORE_HI - _PII_SCORE_LO)
    # at/above c_hi → high, small extra credit toward 0.99
    over = min(0.10, 0.02 * (n - c_hi))
    return min(0.99, _PII_SCORE_HI + over)


def _pii_confidence(finding: dict, *, gated: bool, occurrences: int,
                    count_points: tuple[int, int], ctx_dist: int | None = None
                    ) -> float:
    """Evidence-based 0..1 confidence. TWO independent evidence tracks, combined
    by max() (the agreed hybrid):

      A) Rule-class EVIDENCE — a checksum/secret/email is high-trust at a single
         occurrence (math / high-stakes / very specific regex); NER/context
         findings start lower and are moved by context distance. This track
         answers "is THIS hit real on its own merits".

      B) COUNT CALIBRATION — the per-rule (c_lo, c_hi) count-points map the
         per-file distinct-occurrence count onto the score (user's design: more
         hits ⇒ higher band). This track answers "does recurrence corroborate".

    final = max(A, B): a checksummed IBAN is high via A even at count 1; a bare
    name climbs via B as it recurs. Deterministic + explainable."""
    rid = finding.get("rule_id", "")
    cat = finding.get("category", "")
    source = finding.get("source", "")

    # ── Track A: rule-class evidence ──
    if cat == "secrets":
        base = 0.95
    elif rid in _PII_CHECKSUM_RULES:
        base = 0.98
    elif rid == "email":
        base = 0.92
    elif rid in _PII_CONTEXT_RULES:
        base = 0.82
    elif source == "ner":
        base = 0.72 if gated else 0.55
    elif rid in ("phone", "credit_card"):
        base = 0.68
    elif rid == "date":
        base = 0.65
    elif rid == "bare_identifier":
        base = 0.45
    else:
        base = 0.55

    rigid = (cat == "secrets") or (rid in _PII_CHECKSUM_RULES) or (rid == "email")

    # context distance moves only the non-rigid evidence track (NER date/address)
    dist_adj = 0.0
    if ctx_dist is not None and not rigid:
        prox = float(_DATE_ADDRESS_NAME_PROXIMITY)
        closeness = max(0.0, 1.0 - min(ctx_dist, prox) / prox)
        dist_adj = (closeness - 0.5) * 0.30                     # ±0.15
    evidence = base + (0.0 if rigid else dist_adj)

    # ── Track B: count calibration ──
    c_lo, c_hi = count_points
    count_score = _count_calibrated_score(occurrences, c_lo, c_hi)

    score = max(evidence, count_score)
    if cat == "secrets":
        score = max(score, 0.90)   # high-stakes floor — bias toward catching
    return round(max(0.05, min(score, 0.99)), 2)


# ── M6 (G4): table / mass-data column heuristic ──────────────────────────────
# Prose NER + regex miss the CELL. The real PII carrier in a KYC/DD workload is
# the tabular cell: "KO TULLNERSAntonius" (Excel truncation, no space),
# "KO STARK Bonnie M." (KO-prefix + ALLCAPS), "19470205" (DOB as YYYYMMDD),
# "300622-800-1" (account no.). Measured against the real scanner: 0 findings
# on these cells, while the ca_sin rule mis-matched "300622-800" as a Canadian
# SIN substring. Both are fixed here: the column heuristic reserves the WHOLE
# cell span for every cell in a name/ID/DOB column BEFORE the regex/NER passes
# run, so (a) truncated/prefixed names get caught and (b) the full-cell span
# blocks a substring false-match downstream.
#
# We only understand the ONE table shape our extractor emits: GitHub-flavoured
# markdown (`| a | b |` rows with a `|---|` separator under the header) — that's
# what engine/doc_convert renders every xlsx/csv into. A header cell maps to a
# rule by keyword; every non-empty, non-NULL data cell of that column becomes a
# finding of that rule. The header row itself is NEVER tokenised (tokenising a
# column head like "Kd.Nr." destroys table semantics — measured).
# Header → rule keywords. Matched on WORD BOUNDARIES, not raw substrings — a
# free-text notes column "Information Kundenkontakt" must NOT match "kunde", and
# a money column "Depotvolumen" must NOT match "depot". Each keyword is a token
# that must appear as a whole word (or `.`/`-`/`/`-separated fragment) in the
# header. Measured: without this, 205 money/notes cells were mis-tagged.
_TABLE_HEADER_RULES: tuple[tuple, ...] = (
    ("name", ("name", "kunde", "auftraggeber", "inhaber", "vorname",
              "nachname", "kontoinhaber", "begünstigter", "empfänger")),
    ("organisation", ("firma", "unternehmen", "gesellschaft", "emittent")),
    ("dob", ("geburtsdatum", "geburtstag", "geb", "dob")),
    # ID columns: the cell IS an identifier even when formless (107625). We
    # reserve its full-cell span so a downstream substring rule (ca_sin) can't
    # false-match a fragment (`300622-800` inside `300622-800-1`). Emitted as
    # `organisation` (business_id category).
    ("business_id", ("kd.nr", "kdnr", "kundennummer", "kunden-nr", "kd-nr",
                     "kto", "depot", "konto", "iban", "account", "kontonr",
                     "kontonummer", "depotnummer")),
)
# Header tokens that VETO a match even if a keyword hit — money/free-text
# columns whose name shares a stem with an ID/name keyword ("Depotvolumen",
# "Information Kundenkontakt", "Cash", "Volumen", "Kommentar").
_TABLE_HEADER_VETO = ("volumen", "volume", "cash", "betrag", "kontakt",
                      "information", "kommentar", "recherche", "notiz",
                      "summe", "saldo", "wert", "kurs")
# Cell values that carry no PII — never emit a finding for these.
_TABLE_EMPTY_CELLS = frozenset({"", "null", "none", "n/a", "na", "-", "0",
                                "nan", "false", "true"})
# Split a header into lowercased word tokens (word-boundary matching).
_HEADER_TOKEN_RE = _re.compile(r"[a-zäöüß0-9.\-/]+")


def _classify_table_header(header: str) -> str | None:
    """Map a markdown table header cell to a rule_id, or None. Word-boundary
    matching + a veto list so money/notes columns that merely share a stem
    ("Depotvolumen", "Information Kundenkontakt") don't select a column.
    `business_id` is returned for ID columns (caller emits it as
    'organisation')."""
    h = header.strip().lower()
    if not h:
        return None
    if any(v in h for v in _TABLE_HEADER_VETO):
        return None
    tokens = _HEADER_TOKEN_RE.findall(h)
    # For each token collect its matchable forms: the raw token, its
    # `.`/`-`/`/`-split fragments, and a fully de-punctuated form
    # ("kd.nr." → "kdnr"). Keyword hits any of these.
    forms: set[str] = set()
    for tok in tokens:
        forms.add(tok)
        forms.update(f for f in _re.split(r"[.\-/]", tok) if f)
        forms.add(_re.sub(r"[.\-/]", "", tok))
    for rule_id, needles in _TABLE_HEADER_RULES:
        for n in needles:
            n_norm = _re.sub(r"[.\-/]", "", n)
            if n in forms or n_norm in forms:
                return rule_id
    return None


def _scan_markdown_table_columns(text: str) -> list[dict]:
    """Find every data cell in a name/ID/DOB table column and return findings
    (rule_id + absolute char span in `text`). Robust against Excel truncation
    and inverted "Lastname Firstname" forms because it keys off the COLUMN, not
    the cell content. Never emits for the header row or empty/NULL cells."""
    findings: list[dict] = []
    lines = text.split("\n")
    # Char offset of the start of each line (for absolute spans).
    offs = []
    _o = 0
    for ln in lines:
        offs.append(_o)
        _o += len(ln) + 1  # +1 for the '\n' we split on
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        if line.count("|") < 2:
            i += 1
            continue
        # A header row is followed by a separator row of only |, -, :, space.
        sep = lines[i + 1].strip()
        if not sep or set(sep) - set("|-: "):
            i += 1
            continue
        headers = [c.strip() for c in line.strip().strip("|").split("|")]
        col_rule = [(_classify_table_header(h)) for h in headers]
        if not any(col_rule):
            i += 2
            continue
        # Data rows follow the separator until a blank line or a non-table line.
        j = i + 2
        while j < len(lines):
            row = lines[j]
            if row.count("|") < 2:
                break
            # Split preserving positions: walk the raw row so spans are exact.
            base = offs[j]
            # cell index ↔ raw segment between pipes
            seg_start = 0
            cell_idx = -1
            raw = row
            # Leading pipe: the first split segment before the first '|' is
            # outside the table (usually empty) — treat segments between pipes.
            parts = raw.split("|")
            pos = 0
            for k, seg in enumerate(parts):
                seg_off = pos
                pos += len(seg) + 1  # +1 for the '|'
                # Table cells are the segments strictly between the outer pipes.
                if k == 0 or k == len(parts) - 1:
                    continue
                cell_idx += 1
                if cell_idx >= len(col_rule):
                    continue
                rid = col_rule[cell_idx]
                if not rid:
                    continue
                val = seg.strip()
                if val.lower() in _TABLE_EMPTY_CELLS or len(val) < 2:
                    continue
                # Absolute span of the trimmed value inside the row.
                lead = len(seg) - len(seg.lstrip())
                s = base + seg_off + lead
                e = s + len(val)
                emit_rid = "organisation" if rid == "business_id" else rid
                findings.append({
                    "rule_id": emit_rid,
                    "start": s, "end": e, "len": e - s,
                    "_table_col": cell_idx,
                    "_raw_rule": rid,
                })
            j += 1
        i = j
    return findings


# ── M9.1 (G12): Sperrschrift (letter-spaced) name normalisation ──────────────
# Notarial / formal documents render names in letter-spacing:
# "Dr. Gottwald K R A N E B I T T E R", "Herr Günter K E R B L E R". NER sees
# individual capital letters, not a name → 0 findings, while the normal-cased
# form of the same name elsewhere in the doc IS faked. The cloud provider can
# then trivially invert the mapping (the spaced line names function + first
# name). Measured 0 findings (session 6c8dc5937f2c, HV protocols).
#
# We detect a run of ≥4 single capital letters each followed by whitespace and
# emit a `name` finding over the ORIGINAL spaced span — so the ledger
# anonymises the actual on-page text. The collapsed form ("KRANEBITTER") is
# stashed as `_collapsed` so the caller can register it as a variant of the
# entity (letting the entity layer tie it to the normal-cased occurrence).
# A leading first name / title token before the run is included when present so
# the fake reads naturally.
_SPERRSCHRIFT_RE = _re.compile(
    r"(?:\b[A-ZÄÖÜ][a-zäöüß]+\s+)?"        # optional preceding first-name/title
    r"(?:[A-ZÄÖÜ]\s+){3,}[A-ZÄÖÜ]\b"        # ≥4 letter-spaced capitals
)


def _scan_sperrschrift_names(text: str) -> list[dict]:
    """Find letter-spaced name runs and return `name` findings over the raw
    spaced span, each carrying `_collapsed` (the de-spaced surname). Empty when
    the pattern doesn't occur — cheap regex, never raises."""
    out: list[dict] = []
    for m in _SPERRSCHRIFT_RE.finditer(text):
        span = m.group(0)
        # Collapse the letter-spaced tail into a single word; keep any leading
        # first-name/title token separated by a single space.
        parts = span.split()
        # Trailing run = the sequence of single-letter tokens at the end.
        letters = []
        head = []
        for tok in parts:
            if len(tok) == 1 and tok.isalpha() and tok.isupper():
                letters.append(tok)
            else:
                head.append(tok)
        if len(letters) < 4:
            continue
        collapsed = ("".join(head[:1]) + " " if head else "") + "".join(letters)
        out.append({
            "rule_id": "name",
            "start": m.start(), "end": m.end(), "len": m.end() - m.start(),
            "_collapsed": collapsed.strip(),
        })
    return out


def _pii_scan_text(text: str, max_findings: int = 100,
                   cfg: dict | None = None) -> list[dict]:
    """Scan text for PII. Returns list of {rule_id, label, start, end, category,
    action} with overlap-suppression across rules (first match wins).

    Applies per-category actions: rules with action='ignore' are skipped entirely;
    email findings matching `email_allowlist` are suppressed regardless of action.
    A per-rule min_occurrences gate (distinct values, whole document) and the
    date/address person-name proximity gates run as a post-pass.
    """
    if not text or not isinstance(text, str):
        return []
    # Config-resolution helpers live in brain.py (config.json reader + the
    # rule_overrides/category action resolver). Lazy import breaks the
    # brain<->engine cycle; the regex scan itself is pure (no brain dep).
    import brain as _brain
    _get_gdpr_scanner_config = _brain._get_gdpr_scanner_config
    _pii_effective_action = _brain._pii_effective_action
    _pii_email_allowed = _brain._pii_email_allowed
    _pii_min_occurrences = _brain._pii_min_occurrences
    if cfg is None:
        cfg = _get_gdpr_scanner_config()
    allowlist = cfg.get("email_allowlist") or []
    findings: list[dict] = []
    spans: list[tuple[int, int]] = []

    # ── M6 (G4): table column pre-pass — runs FIRST so a full-cell span blocks
    # a downstream substring false-match (the ca_sin `300622-800` bug) AND so
    # NER/regex-missed cells (truncated / prefixed names, YYYYMMDD DOBs) still
    # get caught. Emits `name`/`organisation`/`dob` findings; each respects the
    # per-rule action + reserves its span. Never touches the header row.
    try:
        for tf in _scan_markdown_table_columns(text):
            rid = tf["rule_id"]
            action = _pii_effective_action(rid, cfg)
            s, e = tf["start"], tf["end"]
            if any(s < se and e > ss for ss, se in spans):
                continue
            # RESERVE the full-cell span even for ignore-action ID columns:
            # an ID cell (`300622-800-1`) is not personal data (business_id →
            # ignore by default), but reserving its span is exactly what stops
            # a downstream substring rule (ca_sin) from false-matching a
            # fragment of it. The cell just doesn't become a FINDING when
            # ignored. (This is M6.4's cell-boundary anchor.)
            spans.append((s, e))
            if action == "ignore":
                continue
            findings.append({
                "rule_id": rid,
                "label": PII_RULE_LABELS.get(rid, _LABEL_DISPLAY.get(rid, rid)),
                "start": s, "end": e, "len": e - s,
                "category": PII_RULE_CATEGORIES.get(rid, "contact"),
                "action": action,
                "source": "table",
                "_value": _re.sub(r"\s+", " ", text[s:e]).strip().lower(),
            })
            if len(findings) >= max_findings:
                break
    except Exception as _e:
        # The table heuristic must never break the regex pipeline.
        print(f"[pii_ner] table pre-pass skipped: {_e}", flush=True)

    # ── M9.1 (G12): Sperrschrift name pre-pass — catch letter-spaced names
    # ("K R A N E B I T T E R") that NER can't see. Emits a `name` finding over
    # the raw spaced span so the ledger anonymises the on-page text; the
    # collapsed surname rides along in `_value` so it counts as the same
    # distinct value as the normal-cased occurrence.
    try:
        _sp_action = _pii_effective_action("name", cfg)
        if _sp_action != "ignore":
            for sf in _scan_sperrschrift_names(text):
                s, e = sf["start"], sf["end"]
                if any(s < se and e > ss for ss, se in spans):
                    continue
                spans.append((s, e))
                findings.append({
                    "rule_id": "name",
                    "label": PII_RULE_LABELS.get("name", "Name"),
                    "start": s, "end": e, "len": e - s,
                    "category": PII_RULE_CATEGORIES.get("name", "contact"),
                    "action": _sp_action,
                    "source": "sperrschrift",
                    "_value": sf.get("_collapsed", text[s:e]).strip().lower(),
                })
                if len(findings) >= max_findings:
                    break
    except Exception as _e:
        print(f"[pii_ner] sperrschrift pre-pass skipped: {_e}", flush=True)

    # Track distinct matched values per rule_id for the min_occurrences gate.
    # `value` (normalised, lowercased) is stashed on each finding so the post-
    # pass can both count distinct values AND drop a whole rule below threshold.
    #
    # CONTEXT-RULE PRIORITY: rules whose match is anchored by an explicit keyword
    # ("Sozialversicherungsnummer", "SSN", …) — category `national_id_ctx` —
    # reserve their span FIRST, before the blind country-pattern national-ID
    # rules (cz_rc, no_fnr, …). Otherwise a number that merely FITS a foreign
    # pattern (e.g. 9 digits matching the Czech rodné číslo shape) gets that
    # label even when the user literally wrote "Sozialversicherungsnummer". This
    # does NOT reorder _PII_RULES (the array + its first-match-wins invariant are
    # untouched) — it only splits the iteration into a context-first pass + the
    # rest in original order. Within each pass the array order still decides ties.
    _all_rules = _pii_rules()
    _ctx_rules = [r for r in _all_rules
                  if PII_RULE_CATEGORIES.get(r["id"]) == "national_id_ctx"]
    _rest_rules = [r for r in _all_rules
                   if PII_RULE_CATEGORIES.get(r["id"]) != "national_id_ctx"]
    for rule in (_ctx_rules + _rest_rules):
        rid = rule["id"]
        action = _pii_effective_action(rid, cfg)
        if action == "ignore":
            continue
        for m in rule["re"].finditer(text):
            match = m.group(0)
            ok = rule.get("ok")
            if ok and not ok(match):
                continue
            s, e = m.start(), m.end()
            if any(s < se and e > ss for ss, se in spans):
                continue
            # Email allowlist: if this email matches a trusted address/domain,
            # skip it silently (don't reserve the span so weaker rules could
            # theoretically reclaim it — but no other rule matches bare emails
            # anyway, and consuming the span would incorrectly mask findings).
            if rid == "email" and _pii_email_allowed(match, allowlist):
                continue
            spans.append((s, e))
            findings.append({
                "rule_id": rid, "label": rule["label"],
                "start": s, "end": e, "len": e - s,
                "category": PII_RULE_CATEGORIES.get(rid, "personal"),
                "action": action,
                "_value": match.strip().lower(),
            })
            if len(findings) >= max_findings:
                break
        if len(findings) >= max_findings:
            break
    # Heuristic: bare-identifier fallback when the rule catalog didn't cover a
    # paste of ID-shaped numbers. Checksum-strict rules above still win first.
    bare_action = _pii_effective_action("bare_identifier", cfg)
    if bare_action != "ignore":
        for f in _pii_scan_bare_identifiers(text):
            if any(f["start"] < se and f["end"] > ss for ss, se in spans):
                continue
            spans.append((f["start"], f["end"]))
            f["category"] = "bare_id"
            f["action"] = bare_action
            f["_value"] = text[f["start"]:f["end"]].strip().lower()
            findings.append(f)
            if len(findings) >= max_findings:
                break

    # spaCy NER pass (Phase 1: German PER/LOC/ORG → name/address/organisation).
    # Runs after regex + bare-id so checksum-validated findings win on overlap.
    # Cap inherits remaining budget. Never raises into the regex pipeline.
    # Action policy is governed by the rule's category — admins who don't
    # want NER findings set the category to ignore in Settings → GDPR.
    # `name_spans` (PER entity char-ranges) feed the date/address context gates
    # below — a date or address only counts as personal when a person name is
    # adjacent (~120 chars). Collected even when `name` itself resolves to
    # ignore, because the gate needs the spans regardless of the name action.
    name_spans: list[tuple[int, int]] = []
    # M9.3 (G12): run the German model, then UNION in the English model's
    # findings (same pattern as the sm∪md recall net). The real KYC/DD corpus
    # is majority non-German (>50% English in ko-kunden), and German NER on
    # English content is inconsistent ("Craig Federighi" yes, "Tim Cook" no).
    # de runs first so it populates name_spans for the date/address proximity
    # gates; en only adds non-overlapping spans. Absence of the en model is
    # fine — `is_available` gates it, and it stays a de-only pipeline.
    #
    # v9.350.0 — language detection at the seam: the DOMINANT language's model
    # is the TRUSTED main model (loose path); the other runs as the strict
    # PERSON-only recall net. An English document is parsed natively by the
    # English model instead of producing German-model garbage spans ("CEO John
    # Smith of Acme Corp" as ONE name). Deterministic stopword counting — no
    # dependency, no model call.
    _doc_lang = _dominant_lang(text)
    for _ner_lang in ("de", "en"):
        try:
            if not is_available(_ner_lang):
                continue
            ner_findings = scan_text(
                text, lang=_ner_lang, max_findings=max_findings,
                name_precision=bool((cfg or {}).get("name_precision_gate")),
                trusted=(_ner_lang == _doc_lang))
            for f in ner_findings:
                if f.get("rule_id") == "name":
                    name_spans.append((f["start"], f["end"]))
            for f in ner_findings:
                if len(findings) >= max_findings:
                    break
                s, e = f["start"], f["end"]
                if any(s < se and e > ss for ss, se in spans):
                    continue
                action = _pii_effective_action(f["rule_id"], cfg)
                if action == "ignore":
                    continue
                spans.append((s, e))
                f["action"] = action
                # Collapse internal whitespace: PDF line-breaks inside a span
                # ("Alexander\n\nKlinsky") otherwise produce a value that never
                # matches the same name written inline, inflating distinct-value
                # counts and breaking de-anonymisation token stability.
                f["_value"] = _re.sub(r"\s+", " ", text[s:e]).strip().lower()
                findings.append(f)
        except Exception as e:
            # NER must never break the regex pipeline.
            print(f"[pii_ner] scan skipped (lang=%s): %s" % (_ner_lang, e),
                  flush=True)

    # ── Context gates (person-name proximity) ────────────────────────────────
    # `date` and `address` only count as personal data when tied to a person.
    # Drop their findings that have no person NAME within ~120 chars (same/
    # adjacent sentence). `date` ALSO keeps its birth/life-event keyword path
    # (a date next to 'geboren'/'born'/etc. counts even without an NER name) —
    # that half is handled by _date_has_birth_context().
    _prox = _DATE_ADDRESS_NAME_PROXIMITY
    kept: list[dict] = []
    for f in findings:
        rid = f.get("rule_id")
        if rid == "address":
            # Only a person-linked address is personal data. Record the gap to
            # the nearest person name as a confidence signal (closer = stronger).
            d = _name_distance(f["start"], f["end"], name_spans)
            if d is None or d > _prox:
                continue
            f["_ctx_dist"] = d
        elif rid == "date":
            # A bare date is not PII; keep ONLY dates with a real birth/life-
            # event keyword nearby (geboren/Geburtstag/born/heirat/…). Person-
            # NAME proximity alone is NOT enough: in formal documents a date
            # sits next to a signature ("Anzeige … 02.04.2025 … Gertraud Wisiak")
            # which is a DOCUMENT date, not a birthday — keeping those was a
            # systematic false positive (9.205.1). The birth-context keyword is
            # the only reliable signal that a date is personal.
            db = _birth_context_distance(text, f["start"], f["end"])
            if db is None:
                continue
            f["_ctx_dist"] = db
        kept.append(f)
    findings = kept

    # ── Distinct-occurrence count per rule (whole document) ──────────────────
    # Was the min_occurrences GATE (drop-whole-rule-below-N); since 9.195.0 the
    # count NO LONGER gates — it feeds the confidence score via the per-rule
    # count-points. We compute DISTINCT values per rule (the agreed counting
    # scope) and pass it to _pii_confidence; the three-band threshold resolver
    # downstream decides ignore/ask/act from the resulting score.
    distinct_by_rule: dict[str, set] = {}
    for f in findings:
        distinct_by_rule.setdefault(f["rule_id"], set()).add(f.get("_value", ""))
    occ_by_rule = {rid: len(vals) for rid, vals in distinct_by_rule.items()}

    # ── Per-finding confidence (evidence + count calibration) ────────────────
    ner_gated = bool((cfg or {}).get("name_precision_gate"))
    import brain as _brain2
    for f in findings:
        rid = f["rule_id"]
        gated = ner_gated and f.get("source") == "ner"
        f["confidence"] = _pii_confidence(
            f, gated=gated, occurrences=occ_by_rule.get(rid, 1),
            count_points=_brain2._pii_count_points(rid, cfg),
            ctx_dist=f.get("_ctx_dist"))

    # Strip internal keys before returning (audit/UI never see them).
    for f in findings:
        f.pop("_value", None)
        f.pop("_ctx_dist", None)
    return findings
