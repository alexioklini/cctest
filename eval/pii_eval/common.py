"""Shared types, taxonomy normalization, and value-level scoring for the
PII-detector shootout.

Design decisions (locked with the user 2026-06-23):
  * VALUE-MATCH scoring, not span-match. Every detector is reduced to a set of
    (normalized_value, canonical_type) findings; LLMs can't reliably emit char
    offsets, so offsets would penalize arithmetic, not detection. Regex/NER
    findings are mapped to their substring value too, so all four detectors are
    judged on the same axis.
  * Type-aware but type-forgiving matching: a finding counts as a true positive
    if its value matches a gold value AND the canonical type is in the gold
    type's accept-set (e.g. a detector calling an IBAN "financial" still
    matches gold type "iban"). Value-only match (wrong type) is tracked
    separately as `value_only` so we can see detection-vs-classification.
  * Multi-rep aware: the runner repeats non-deterministic detectors and reports
    mean +/- spread, per feedback_eval_single_run_noise (single-run deltas
    <0.05 are noise).

Nothing here imports brain or the server; it's pure stdlib so it runs in any
venv (the Presidio/GLiNER venv and the server venv both).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field


# --- Canonical taxonomy -------------------------------------------------------
# Every detector's native labels are mapped onto these buckets so cross-detector
# scoring is apples-to-apples. Keep this list small and semantic.
CANON_TYPES = [
    "name",          # person name
    "address",       # postal address / location tied to a person or org
    "organisation",  # company / product / system / vendor
    "email",
    "phone",
    "iban",
    "credit_card",
    "national_id",   # any government ID w/ or w/o checksum (Steuer-ID, BSN, AHV, PESEL, ...)
    "secret",        # API keys, tokens, private keys, passwords
    "network",       # IP addresses
    "date",          # birth/hire/event dates (personal-data dates only)
]

# Accept-sets: a predicted canonical type "satisfies" these gold types. Most are
# identity, but we let the broad financial/id buckets cross-match so a detector
# isn't punished for a defensible coarser label.
TYPE_ACCEPTS: dict[str, set[str]] = {
    "iban": {"iban", "national_id"},          # some libs lump IBAN under financial/id
    "credit_card": {"credit_card"},
    "national_id": {"national_id", "iban"},
    "secret": {"secret"},
    "network": {"network"},
    "email": {"email"},
    "phone": {"phone"},
    "name": {"name"},
    "address": {"address", "organisation"},   # address<->org boundary is fuzzy in NER
    "organisation": {"organisation", "address"},
    "date": {"date"},
}


def _accepts(pred_type: str, gold_type: str) -> bool:
    return gold_type in TYPE_ACCEPTS.get(pred_type, {pred_type})


@dataclass(frozen=True)
class Finding:
    value: str
    type: str  # canonical type (one of CANON_TYPES) or "other"


# --- Value normalization ------------------------------------------------------
# Goal: "DE89 3704 0044 0532 0130 00" == "DE8937040044053201300 0" == "de89...".
# For structured PII (ids/iban/cc/phone) we strip separators; for names/orgs we
# casefold + collapse whitespace; for addresses we do a looser containment match
# at scoring time (handled in score_doc).
_STRUCTURED = {"iban", "credit_card", "national_id", "phone", "network"}


def normalize_value(value: str, vtype: str) -> str:
    v = unicodedata.normalize("NFKC", value or "").strip()
    if vtype in _STRUCTURED:
        return re.sub(r"[\s./\-]", "", v).lower()
    if vtype in {"email", "secret"}:
        return v.strip().lower() if vtype == "email" else v.strip()
    # name / address / organisation / date: casefold + collapse ws
    return re.sub(r"\s+", " ", v).casefold().strip()


# --- Type mapping helpers for adapters ---------------------------------------
# Each adapter maps its native labels here. Unknown labels -> "other" (ignored
# in scoring, since gold never uses "other"). This keeps a stray detector label
# from inflating false positives on entity types we don't grade.

# Our detector's rule_id/category -> canon. Driven by category primarily.
OUR_CATEGORY_MAP = {
    "secrets": "secret",
    "national_id": "national_id",
    "national_id_ctx": "national_id",
    "financial": "iban",          # refined per-rule below
    "contact": "name",            # refined per-rule below (email/phone/name/addr/org)
    "network": "network",
    "personal": "date",           # refined per-rule below (date/passport/dob)
    "business_id": "organisation",
    "bare_identifier": "national_id",
}
OUR_RULE_MAP = {
    "email": "email", "phone": "phone",
    "name": "name", "address": "address", "organisation": "organisation",
    "iban": "iban", "credit_card": "credit_card",
    "ipv4": "network", "ipv6": "network",
    "date": "date", "dob": "date", "passport": "national_id",
}


def map_our_finding(rule_id: str, category: str) -> str:
    if rule_id in OUR_RULE_MAP:
        return OUR_RULE_MAP[rule_id]
    return OUR_CATEGORY_MAP.get(category, "other")


# Presidio entity -> canon
PRESIDIO_MAP = {
    "PERSON": "name", "LOCATION": "address", "NRP": "name",
    "ORGANIZATION": "organisation", "ORG": "organisation",
    "EMAIL_ADDRESS": "email", "PHONE_NUMBER": "phone",
    "IBAN_CODE": "iban", "CREDIT_CARD": "credit_card",
    "IP_ADDRESS": "network", "CRYPTO": "secret", "URL": "other",
    "DATE_TIME": "date",
    # national ids presidio ships
    "DE_TAX_ID": "national_id", "DE_VAT_ID": "national_id",
    "ES_NIF": "national_id", "ES_NIE": "national_id", "IT_FISCAL_CODE": "national_id",
    "PL_PESEL": "national_id", "FI_PERSONAL_IDENTITY_CODE": "national_id",
    "UK_NHS": "national_id", "UK_NINO": "national_id",
    "AU_TFN": "national_id", "AU_MEDICARE": "national_id", "AU_ABN": "organisation",
    "IN_AADHAAR": "national_id", "IN_PAN": "national_id",
    "SG_NRIC_FIN": "national_id", "US_SSN": "national_id", "US_ITIN": "national_id",
    "US_PASSPORT": "national_id", "US_DRIVER_LICENSE": "national_id",
    "AT_SVNR": "national_id", "CH_AHV": "national_id", "BE_NRN": "national_id",
    "NL_BSN": "national_id",
}

# GLiNER label -> canon (GLiNER labels are free-text we pass in; we keep them
# aligned to our schema so the map is near-identity).
GLINER_MAP = {
    "person": "name", "person name": "name", "name": "name",
    "address": "address", "location": "address",
    "organization": "organisation", "organisation": "organisation", "company": "organisation",
    "email": "email", "email address": "email",
    "phone": "phone", "phone number": "phone",
    "iban": "iban", "bank account": "iban",
    "credit card": "credit_card", "credit card number": "credit_card",
    "national id": "national_id", "national id number": "national_id",
    "tax id": "national_id", "social security number": "national_id",
    "passport": "national_id", "passport number": "national_id", "id number": "national_id",
    "api key": "secret", "secret": "secret", "password": "secret", "token": "secret",
    "ip address": "network",
    "date": "date", "date of birth": "date",
}

# M4 LLM emits our canon types directly (we instruct it to), so map is identity
# with a few synonyms.
LLM_MAP = {t: t for t in CANON_TYPES}
LLM_MAP.update({
    "person": "name", "location": "address", "org": "organisation",
    "organization": "organisation", "ip": "network", "api_key": "secret",
    "tax_id": "national_id", "ssn": "national_id", "passport": "national_id",
})


# --- Scoring ------------------------------------------------------------------
@dataclass
class DocScore:
    doc_id: str
    tp: int = 0          # gold value found, type accepted
    value_only: int = 0  # gold value found, but type wrong
    fn: int = 0          # gold value missed
    fp: int = 0          # predicted value not in gold (and not a near-dup)
    matched_gold: list = field(default_factory=list)
    missed_gold: list = field(default_factory=list)
    false_pos: list = field(default_factory=list)


def _value_match(pred_norm: str, gold_norm: str, gold_type: str) -> bool:
    """Match predicted vs gold normalized values.

    * structured (iban/cc/national_id/phone/network) + email/secret: exact, OR
      one fully contains the other with the SHORTER >= 8 chars — handles a
      detector that over-captures a context word ("Server 192.168.1.214" for IP
      "192.168.1.214") or splits a separator, without letting a short fragment
      collide with a different full ID.
    * free-text (name/address/org/date): containment either way, shorter >= 4
      ("Klinsky" <-> "Alexander Klinsky")."""
    if not pred_norm or not gold_norm:
        return False
    if pred_norm == gold_norm:
        return True
    short, long = sorted((pred_norm, gold_norm), key=len)
    if gold_type in _STRUCTURED or gold_type in {"email", "secret"}:
        return len(short) >= 8 and short in long
    if len(short) >= 4 and short in long:
        return True
    return False


def score_doc(doc_id: str, gold: list[dict], preds: list[Finding]) -> DocScore:
    """Greedy one-to-one value matching. Each gold item can be satisfied once."""
    s = DocScore(doc_id=doc_id)
    gold_norm = [(normalize_value(g["value"], g["type"]), g["type"], g["value"]) for g in gold]
    used_gold = [False] * len(gold_norm)
    used_pred = [False] * len(preds)

    # Pass 1: type-accepted matches (true positives)
    for pi, p in enumerate(preds):
        if p.type == "other":
            used_pred[pi] = True  # not graded, neither TP nor FP
            continue
        pnorm = normalize_value(p.value, p.type)
        for gi, (gnorm, gtype, graw) in enumerate(gold_norm):
            if used_gold[gi]:
                continue
            if _accepts(p.type, gtype) and _value_match(pnorm, gnorm, gtype):
                s.tp += 1
                used_gold[gi] = True
                used_pred[pi] = True
                s.matched_gold.append(graw)
                break

    # Pass 2: value matches with wrong type (detection ok, classification off)
    for pi, p in enumerate(preds):
        if used_pred[pi]:
            continue
        pnorm = normalize_value(p.value, p.type)
        for gi, (gnorm, gtype, graw) in enumerate(gold_norm):
            if used_gold[gi]:
                continue
            if _value_match(pnorm, gnorm, gtype):
                s.value_only += 1
                used_gold[gi] = True
                used_pred[pi] = True
                break

    # Leftover gold = false negatives; leftover graded preds = false positives
    for gi, (gnorm, gtype, graw) in enumerate(gold_norm):
        if not used_gold[gi]:
            s.fn += 1
            s.missed_gold.append(graw)
    for pi, p in enumerate(preds):
        if not used_pred[pi] and p.type != "other":
            s.fp += 1
            s.false_pos.append(f"{p.value} [{p.type}]")
    return s


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


def load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def mean_spread(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    if len(xs) == 1:
        return m, 0.0
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m, var ** 0.5
