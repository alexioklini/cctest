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
               "ing", "mmag", "ddr", "dipl.-ing", "frau dr", "herr dr"}
_NAME_NOUN_SUFFIX = _re.compile(
    r"(ung|heit|keit|schaft|tion|rechten|recht|kontakte|vorfall|vorfalls|"
    r"prinzip|vorschläge|wörter|person|personen|verarbeitern|verarbeitung|"
    r"button|binding|transformer)$", _re.IGNORECASE)
_NON_NAME_TOKENS = {"pre-trained", "transformer", "delete", "button", "admin",
                    "rechten", "binding", "data", "owner", "ticket", "review"}
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
_ADDR_HOUSE_NO = _re.compile(r"\b\d{1,4}\s*[-–]?\s*\d{0,4}[a-zA-Z]?\b")
_ADDR_PLZ = _re.compile(r"\b\d{4,5}\b")


def _passes_address_precision_gate(value: str, text: str, start: int) -> bool:
    end = start + len(value)
    after = text[end:end + 30]
    return bool(_ADDR_HOUSE_NO.search(after) or _ADDR_PLZ.search(after))


def _passes_name_precision_gate(value: str, text: str, start: int) -> bool:
    v = value.strip()
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
              max_findings: int = 100,
              name_precision: bool = False) -> list[dict]:
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
            "category": "contact",
            "source": "ner",
        })
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

        # ── Context-gated heuristics ──
        {"id": "passport", "label": "Passport number",
         "re": _re.compile(r"passport[^\w\n]{0,20}([A-Z][0-9]{6,9}|[A-Z]{1,2}[0-9]{6,8})", _re.IGNORECASE)},
        {"id": "dob", "label": "Date of birth",
         "re": _re.compile(r"(?:\b(?:DOB|born|date\s+of\s+birth|geboren|geburtsdatum|né|née|nacido)\b[^\n]{0,20}?(?:\d{1,2}[\/.\- ]\d{1,2}[\/.\- ]\d{2,4}|\d{4}-\d{2}-\d{2}))", _re.IGNORECASE)},

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
    r"\b(?:geboren|geburtsdatum|geb\.|born|date\s+of\s+birth|\bDOB\b|"
    r"n[ée]|né|née|nacido|nata|nato|"
    r"gestorben|verstorben|died|deceased|d[ée]c[ée]d[ée]|"
    r"heirat|verheiratet|married|mariage|"
    r"eingestellt|eintritt|einstellungsdatum|date\s+of\s+hire|hired|"
    r"austritt|ausgeschieden|date\s+of\s+leaving)\b",
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
    # Track distinct matched values per rule_id for the min_occurrences gate.
    # `value` (normalised, lowercased) is stashed on each finding so the post-
    # pass can both count distinct values AND drop a whole rule below threshold.
    for rule in _pii_rules():
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
    try:
        if is_available("de"):
            ner_findings = scan_text(
                text, lang="de", max_findings=max_findings,
                name_precision=bool((cfg or {}).get("name_precision_gate")))
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
        print(f"[pii_ner] scan skipped: {e}", flush=True)

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
            # A bare date is not PII; keep only person- or birth-linked dates.
            dn = _name_distance(f["start"], f["end"], name_spans)
            db = _birth_context_distance(text, f["start"], f["end"])
            near_name = dn is not None and dn <= _prox
            if not (near_name or db is not None):
                continue
            # Confidence distance = nearest of the two anchors that fired.
            cands = [x for x in (dn if near_name else None, db) if x is not None]
            if cands:
                f["_ctx_dist"] = min(cands)
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
