"""Transparent anonymisation — reversible pseudonymization for text + files.

Pairs with brain.py's GDPR scanner (`_pii_scan_text`). The scanner finds spans;
this module replaces them with stable tokens, records the mapping, and reverses
on LLM reply. Goal: GDPR-sensitive data never reaches cloud LLMs while users
still see real values in chat output.

Strategy (per CLAUDE.md decisions):
- Token style: **hybrid**. Free-text PII (names, IDs, emails, etc.) → opaque
  `<KIND_N_SALT>` tokens. Numerics where downstream consumers (xlsx formulas,
  Luhn-checking code) may parse the value → shape-preserving fakes (valid
  Luhn for credit cards, valid mod-97 for IBANs, same-digit-count for phones).
- Stable per-(session, value): the same original string gets the same token
  across multiple turns of the same session via a deterministic hash → index.
- Reverse path is tolerant of LLM mangling (`< PERSON_1_a8k2 >` → recovered).
- Mapping lives in an in-memory dict keyed by `mapping_id`; persistence to
  encrypted SQLite is step 2 of the rollout — this module is pure logic.

This file is pure: no SQLite, no SSE, no HTTP. Step 2 adds the encrypted
store; step 3 wires it into the chat worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Token format
# ---------------------------------------------------------------------------
#
# Angle brackets survive markdown / JSON rendering better than `[ ]` (markdown
# rewrites bracketed text as link syntax). The per-session salt prevents the
# token from colliding with anything the user typed and makes it unguessable.
# `KIND` is uppercase rule_id; `N` is the per-mapping running index for that
# kind; `SALT` is 4 base32 chars derived from `secrets.token_bytes`.

_TOKEN_RE_STRICT = re.compile(r"<([A-Z][A-Z0-9_]*)_(\d+)_([a-z0-9]{4,8})>")
# Tolerant: allows whitespace inside the brackets and case-insensitive — covers
# LLMs that rewrite `<PERSON_1_ab12>` as `< person_1_ab12 >` or similar.
_TOKEN_RE_TOLERANT = re.compile(
    r"<\s*([A-Za-z][A-Za-z0-9_]*)_(\d+)_([a-z0-9]{4,8})\s*>",
    re.IGNORECASE,
)


def _rule_id_to_kind(rule_id: str) -> str:
    """`de_steuerid` → `DE_STEUERID`. Uppercased so the token is visually
    distinct from prose and won't collide with normal words."""
    return rule_id.upper()


# ---------------------------------------------------------------------------
# Shape-preserving fakes
# ---------------------------------------------------------------------------
#
# Used only for rule_ids in SHAPE_PRESERVING. For everything else, the opaque
# token is used. Each generator is fully deterministic from a seed (the SHA-256
# of the original value + mapping salt) so the same original always produces
# the same fake within a mapping.

SHAPE_PRESERVING: frozenset[str] = frozenset({
    # Defaults; the chat worker will read config later to allow disabling.
    "iban",
    "credit_card",
    "phone",
    # Soft-PII: a plausible look-alike preserves prose readability for the
    # LLM (e.g. "Hans Müller wohnt in Wien" → "John Doe wohnt in Springfield"
    # still parses as a sentence about a person+place; an opaque
    # `<NAME_1_xxxx>` token can confuse smaller models on long contexts).
    "name",
    "address",
    "organisation",
    "email",
    "date",
})


def _seed_int(value: str, salt: str, n: int = 8) -> int:
    """Deterministic non-negative int from value+salt, `n` bytes wide."""
    h = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).digest()
    return int.from_bytes(h[:n], "big")


def _digits_only(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _luhn_check(digits: str) -> bool:
    total = 0
    alt = False
    for c in reversed(digits):
        n = int(c)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def _make_luhn(prefix: str, length: int) -> str:
    """Pad `prefix` to `length-1` digits and append a Luhn check digit."""
    body = prefix[: length - 1]
    if len(body) < length - 1:
        # Pad with seeded-but-deterministic digits via repeating prefix.
        while len(body) < length - 1:
            body += prefix[len(body) % max(1, len(prefix))]
    # Compute check digit.
    total = 0
    alt = True  # Position from right of full number; check digit is rightmost
    for c in reversed(body):
        n = int(c)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    check = (10 - (total % 10)) % 10
    return body + str(check)


def _fake_credit_card(original: str, salt: str) -> str:
    """Generate a Luhn-valid number with the same digit count as `original`.
    Preserves any spaces / dashes in their original positions so cell layout
    in xlsx stays intact."""
    digits = _digits_only(original)
    n = len(digits)
    if not 13 <= n <= 19:
        return _fake_opaque_digits(original, salt)
    # Use a fixed test-range BIN ('4' = Visa test) so the fake is recognisable
    # as synthetic by anyone who inspects it, but still Luhn-valid.
    seed = _seed_int(original, salt, n=8)
    body_prefix = "4" + str(seed)[-max(0, n - 2) :].rjust(max(0, n - 2), "0")
    fake_digits = _make_luhn(body_prefix, n)
    return _re_inject_separators(original, fake_digits)


_IBAN_LETTER_VAL = {chr(ord("A") + i): str(10 + i) for i in range(26)}


def _iban_check_digits(country: str, bban: str) -> str:
    """Compute mod-97 IBAN check digits (positions 3-4)."""
    rearranged = bban + country + "00"
    converted = ""
    for c in rearranged:
        if c.isdigit():
            converted += c
        elif c in _IBAN_LETTER_VAL:
            converted += _IBAN_LETTER_VAL[c]
        else:
            return "00"  # Shouldn't happen with valid country+bban.
    rem = 0
    for d in converted:
        rem = (rem * 10 + int(d)) % 97
    check = 98 - rem
    return f"{check:02d}"


def _fake_iban(original: str, salt: str) -> str:
    """Generate a mod-97-valid IBAN with the same country code and length
    as the original. Country is preserved (regulator-friendly); the body is
    seeded from the original."""
    cleaned = "".join(original.split()).upper()
    if len(cleaned) < 15:
        return _fake_opaque_digits(original, salt)
    country = cleaned[:2]
    if not (country.isalpha() and len(country) == 2):
        return _fake_opaque_digits(original, salt)
    bban_len = len(cleaned) - 4
    seed = _seed_int(original, salt, n=16)
    bban = str(seed).rjust(bban_len, "0")[:bban_len]
    check = _iban_check_digits(country, bban)
    # The original template's non-digit positions hold the country letters and
    # any separators; the injector emits one digit per digit-slot. So we feed
    # it only the digits (check + bban), not the whole IBAN — feeding letters
    # in would land them in digit positions and corrupt the country code.
    return _re_inject_separators(original, check + bban)


def _fake_phone(original: str, salt: str) -> str:
    """Same-digit-count phone. Uses E.164-style with a fake country code 999
    so the value is obviously synthetic (real ITU never assigns 999)."""
    digits = _digits_only(original)
    n = len(digits)
    if not 8 <= n <= 15:
        return _fake_opaque_digits(original, salt)
    seed = _seed_int(original, salt, n=8)
    body = str(seed).rjust(n - 3, "0")[: n - 3]
    fake_digits = "999" + body
    # Preserve leading '+' if present, plus internal separators.
    leading_plus = original.lstrip().startswith("+")
    out = _re_inject_separators(original, fake_digits)
    if leading_plus and not out.lstrip().startswith("+"):
        # _re_inject_separators preserves non-digit chars; '+' is non-digit so
        # this branch should be rare, but guard it anyway.
        out = "+" + out
    return out


def _fake_opaque_digits(original: str, salt: str) -> str:
    """Fallback for malformed numerics — opaque all-zero digit string."""
    digits = _digits_only(original)
    return _re_inject_separators(original, "0" * len(digits))


def _re_inject_separators(template: str, digits: str) -> str:
    """Walk `template`; emit a `digits` char for each digit position, copy
    every non-digit char through (spaces, dashes, dots, +). Stops when digits
    are exhausted."""
    out = []
    it = iter(digits)
    for c in template:
        if c.isdigit():
            try:
                out.append(next(it))
            except StopIteration:
                # template has more digit slots than we produced — abort to
                # opaque to avoid leaking original digits.
                return digits
        else:
            out.append(c)
    # Append any leftover digits (shouldn't happen, but defensive).
    out.extend(it)
    return "".join(out)


# ---------------------------------------------------------------------------
# Soft-PII shape-preserving fakes (names, addresses, orgs, emails, dates)
# ---------------------------------------------------------------------------
#
# The lists are deliberately small + bland. Goal is to produce a fake that
# REMAINS RECOGNISABLE AS PII to the LLM (so it still parses the sentence
# structure correctly) without leaking the original. Lists are
# English-flavoured but locale-neutral; the German-only NER scope of Phase 1
# means inputs are German prose, so reproducing exotic German names would
# add value but also add complexity. "John Doe wohnt in Springfield"
# parses fine.

_FIRST_NAMES = (
    "John", "Jane", "Alex", "Maria", "Chris", "Sam", "Pat", "Robin",
    "Taylor", "Jordan", "Casey", "Morgan", "Riley", "Quinn", "Avery",
    "Drew", "Emerson", "Hayden", "Kerry", "Logan", "Reese", "Sage",
    "Skyler", "Tristan", "Wren", "Blake", "Cameron", "Dakota", "Elliott",
    "Finley",
)
_LAST_NAMES = (
    "Doe", "Smith", "Brown", "Jones", "Miller", "Davis", "Wilson",
    "Taylor", "Clark", "Lewis", "Walker", "Hall", "Allen", "Young",
    "King", "Wright", "Scott", "Green", "Adams", "Baker", "Carter",
    "Mitchell", "Roberts", "Turner", "Phillips", "Campbell", "Parker",
    "Evans", "Edwards", "Collins",
)
_STREET_BASES = (
    "Main", "Park", "Oak", "Elm", "Cedar", "Maple", "River", "Hill",
    "Lake", "Forest", "Spring", "Garden", "Meadow", "Bridge", "Church",
    "Market", "Station",
)
_CITIES = (
    "Springfield", "Riverside", "Franklin", "Greenville", "Bristol",
    "Clinton", "Fairview", "Salem", "Madison", "Georgetown", "Arlington",
    "Ashland", "Burlington", "Manchester", "Oxford", "Newport", "Kingston",
)
_ORG_NAMES = (
    "Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne",
    "Hooli", "Pied Piper", "Soylent", "Cyberdyne", "Tyrell", "Wonka",
    "Vandelay", "Massive Dynamic", "Oscorp", "Wernham Hogg",
)
_ORG_LEGAL_FORMS_DE = ("GmbH", "AG", "KG", "OHG", "UG", "e.V.", "eG")
_ORG_LEGAL_FORMS_EN = (
    "Ltd", "Ltd.", "Inc", "Inc.", "Corp", "Corp.", "LLC", "LLP",
    "PLC", "Co", "Co.", "Company",
)
_ORG_LEGAL_FORMS_OTHER = (
    "SARL", "SAS", "SA", "S.A.", "S.A.S.", "S.r.l.", "S.p.A.", "B.V.",
    "N.V.", "AB", "AS", "Oy",
)
_ALL_LEGAL_FORMS = tuple(
    sorted(
        set(_ORG_LEGAL_FORMS_DE + _ORG_LEGAL_FORMS_EN + _ORG_LEGAL_FORMS_OTHER),
        key=len,
        reverse=True,
    )
)


def _pick(seq: tuple, original: str, salt: str, *, kind: str = "") -> str:
    """Deterministic pick from `seq` based on hash(salt:kind:original)."""
    h = hashlib.sha256(f"{salt}:{kind}:{original}".encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "big") % len(seq)
    return seq[idx]


def _fake_name(original: str, salt: str) -> str:
    """Generate a plausible first+last name. Preserves token count when the
    original is a single token (e.g. just a surname) — emits one name only."""
    parts = original.split()
    first = _pick(_FIRST_NAMES, original, salt, kind="first")
    last = _pick(_LAST_NAMES, original, salt, kind="last")
    if len(parts) <= 1:
        # Single-token original — surname only is the safer assumption (NER
        # often catches surnames in formal prose).
        return last
    return f"{first} {last}"


# Number-extracting regex used by address fake to keep house numbers shaped.
_HOUSE_NUM_RE = re.compile(r"\b(\d{1,5}[A-Za-z]?)\b")
# Postal code patterns we care about preserving the shape of.
_POSTAL_RE = re.compile(r"\b(\d{4,5})\b")


def _fake_address(original: str, salt: str) -> str:
    """Generate a plausible address. Preserves the rough structure of the
    original — if the NER caught a multi-word LOC, we emit a street+number
    (and optional postal+city) similar in length; if it caught just a city
    name, we emit just a city."""
    street_base = _pick(_STREET_BASES, original, salt, kind="street")
    city = _pick(_CITIES, original, salt, kind="city")
    # Single-token original is almost always a city/region name caught by NER.
    if len(original.split()) <= 1 and not any(c.isdigit() for c in original):
        return city
    # Multi-word with no digits → street name + city, no number.
    num_match = _HOUSE_NUM_RE.search(original)
    postal_match = _POSTAL_RE.search(original)
    # Preserve German "Straße" suffix if present (locale signal for the LLM).
    if "straße" in original.lower() or "strasse" in original.lower():
        street = f"{street_base}straße"
    elif "street" in original.lower():
        street = f"{street_base} Street"
    elif "road" in original.lower() or original.lower().endswith(" rd"):
        street = f"{street_base} Road"
    else:
        street = f"{street_base}straße"  # default to DE flavour (Phase 1)
    parts = [street]
    if num_match:
        # Use a deterministic but different number.
        seed = _seed_int(original, salt, n=4)
        parts[0] = f"{street} {1 + (seed % 199)}"
    if postal_match:
        seed = _seed_int(original, salt + ":postal", n=4)
        postal_len = len(postal_match.group(1))
        # Range that respects the original length (4 or 5 digits).
        low = 10 ** (postal_len - 1)
        high = (10 ** postal_len) - 1
        fake_postal = low + (seed % (high - low + 1))
        parts.append(f"{fake_postal} {city}")
    else:
        parts.append(city)
    return ", ".join(parts)


def _fake_organisation(original: str, salt: str) -> str:
    """Generate a plausible org name. Preserves legal-form suffix if present
    (GmbH, AG, Ltd, Inc, SARL, …)."""
    base = _pick(_ORG_NAMES, original, salt, kind="org")
    # Try to detect a trailing legal form — longest-suffix-wins so "GmbH"
    # doesn't shadow "Co." etc.
    suffix = ""
    stripped = original.strip().rstrip(".")
    for lf in _ALL_LEGAL_FORMS:
        lf_norm = lf.rstrip(".")
        if stripped.lower().endswith(" " + lf_norm.lower()):
            suffix = " " + lf  # keep original casing/punctuation
            break
    if suffix:
        return base + suffix
    # No legal form detected — default to "Corp" for plausibility.
    return base + " Corp"


# Email shape: local-part regex. We preserve dots and underscores in the
# local-part to keep the shape recognisable.
_EMAIL_RE = re.compile(r"^([^@]+)@([^@]+)$")


def _fake_email(original: str, salt: str) -> str:
    """Generate a plausible-looking email. Preserves:
      - presence of a dot in the local part (firstname.lastname vs nodot)
      - TLD (.de stays .de, .com stays .com — locale signal survives)
      - approximate length
    Always uses `example.<tld>` domain so the address is RFC-2606 safe and
    can never accidentally hit a real inbox."""
    m = _EMAIL_RE.match(original.strip())
    if not m:
        # Malformed — fall back to opaque digits scheme via the standard path.
        # Returning a deterministic but obviously-fake string keeps shape.
        first = _pick(_FIRST_NAMES, original, salt, kind="first").lower()
        return f"{first}@example.org"
    local, domain = m.group(1), m.group(2)
    # TLD: last dot-separated segment of the domain, capped at 6 chars.
    tld = domain.rsplit(".", 1)[-1] if "." in domain else "org"
    tld = tld.lower()[:6] or "org"
    first = _pick(_FIRST_NAMES, original, salt, kind="first").lower()
    last = _pick(_LAST_NAMES, original, salt, kind="last").lower()
    if "." in local:
        local_fake = f"{first}.{last}"
    elif "_" in local:
        local_fake = f"{first}_{last}"
    elif "-" in local:
        local_fake = f"{first}-{last}"
    elif any(c.isdigit() for c in local):
        # local part had digits → keep digits (random 2-digit suffix)
        seed = _seed_int(original, salt, n=2)
        local_fake = f"{first}{seed % 100:02d}"
    else:
        # Plain — just first name, lowercase.
        local_fake = first
    return f"{local_fake}@example.{tld}"


# Date formats we recognise. Order matters: longest/most-specific first.
# Each entry is (compiled_regex, "format_id"). Format id drives reconstruction.
_DATE_PATTERNS: tuple = (
    # ISO 8601: 2026-05-19
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "iso"),
    # European: 19.05.2026 / 19.5.2026 / 19-05-2026
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "eu_dot"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "eu_dash"),
    # US: 05/19/2026 / 5/19/2026
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "us_slash"),
    # 2-digit year variants
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), "eu_dot_yy"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"), "us_slash_yy"),
)


def _fake_date(original: str, salt: str) -> str:
    """Generate a fake date that preserves the original's format AND its
    year/month (so seasonality + age-bracket stay intact). Day jitters by
    a deterministic offset within the same month — avoids leaking exact DOB
    while preserving "born in 1987-May" signal."""
    raw = original.strip()
    for pat, fmt in _DATE_PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        # Parse fields based on the format. All formats give us (year, month, day)
        # in some order.
        if fmt == "iso":
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif fmt in ("eu_dot", "eu_dash"):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif fmt == "us_slash":
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif fmt == "eu_dot_yy":
            d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = 2000 + yy if yy < 50 else 1900 + yy
        elif fmt == "us_slash_yy":
            mo, d, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = 2000 + yy if yy < 50 else 1900 + yy
        else:  # pragma: no cover
            continue
        # Validate
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
            continue
        # Jitter day within the same month. Days-in-month is approximate
        # (28 covers every month); good enough — we never need to emit Feb 30.
        seed = _seed_int(original, salt, n=4)
        new_d = 1 + (seed % 28)
        # Reassemble in the original format.
        if fmt == "iso":
            return f"{y:04d}-{mo:02d}-{new_d:02d}"
        if fmt == "eu_dot":
            # Preserve zero-padding choice from the original.
            d_orig_padded = len(m.group(1)) == 2
            mo_orig_padded = len(m.group(2)) == 2
            d_s = f"{new_d:02d}" if d_orig_padded else str(new_d)
            mo_s = f"{mo:02d}" if mo_orig_padded else str(mo)
            return f"{d_s}.{mo_s}.{y:04d}"
        if fmt == "eu_dash":
            return f"{new_d:02d}-{mo:02d}-{y:04d}"
        if fmt == "us_slash":
            d_orig_padded = len(m.group(2)) == 2
            mo_orig_padded = len(m.group(1)) == 2
            d_s = f"{new_d:02d}" if d_orig_padded else str(new_d)
            mo_s = f"{mo:02d}" if mo_orig_padded else str(mo)
            return f"{mo_s}/{d_s}/{y:04d}"
        if fmt == "eu_dot_yy":
            return f"{new_d:02d}.{mo:02d}.{y % 100:02d}"
        if fmt == "us_slash_yy":
            return f"{mo:02d}/{new_d:02d}/{y % 100:02d}"
    # Unrecognised — return the original year if we can find one.
    yr = re.search(r"\b(19|20)\d{2}\b", raw)
    if yr:
        return yr.group(0)
    return raw  # Last-resort: pass through (caller falls back to opaque token)


_SHAPE_GENERATORS: dict[str, Callable[[str, str], str]] = {
    "iban": _fake_iban,
    "credit_card": _fake_credit_card,
    "phone": _fake_phone,
    "name": _fake_name,
    "address": _fake_address,
    "organisation": _fake_organisation,
    "email": _fake_email,
    "date": _fake_date,
}


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


@dataclass
class Mapping:
    """In-memory pseudonymization mapping for a single session/turn.

    `forward` is original→token; `reverse` is token→original. Both maintained
    so lookups are O(1) in either direction. `counters` tracks the next index
    per kind so repeated kinds get N=1, 2, 3 within one mapping. `salt` is
    drawn at construction and embedded in every token.
    """
    mapping_id: str
    salt: str
    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    # Sources contributing to this mapping (e.g. "chat_text", "attachment:foo.docx").
    sources: list[str] = field(default_factory=list)
    # Per-category counts for audit / UI display (no values, just totals).
    finding_counts: dict[str, int] = field(default_factory=dict)
    # Per-entry category: original → rule_id. Used to label restored spans in
    # the UI tooltip ("email — alice@… was anonymised as <EMAIL_1_…>").
    # Optional — legacy mappings deserialised from disk won't have it; callers
    # tolerate a missing entry as "unknown" category.
    categories: dict[str, str] = field(default_factory=dict)

    def next_token(self, rule_id: str) -> str:
        kind = _rule_id_to_kind(rule_id)
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return f"<{kind}_{n}_{self.salt}>"

    def record(self, original: str, replacement: str, rule_id: str) -> None:
        self.forward[original] = replacement
        self.reverse[replacement] = original
        self.finding_counts[rule_id] = self.finding_counts.get(rule_id, 0) + 1
        self.categories[original] = rule_id


# Process-wide registry. Step 2 will mirror these to encrypted SQLite for
# durability across server restarts; this module remains purely in-memory.
_REGISTRY: dict[str, Mapping] = {}
_REGISTRY_LOCK = threading.Lock()


def new_mapping() -> Mapping:
    """Create a fresh mapping with a unique id + random salt. Caller is
    responsible for `close_mapping(mapping_id)` at end of turn / on error."""
    mid = uuid.uuid4().hex
    # 4 chars of base36 = ~20 bits; collision-resistant within a mapping and
    # short enough to keep tokens readable.
    salt = secrets.token_hex(2)  # 4 hex chars
    m = Mapping(mapping_id=mid, salt=salt)
    with _REGISTRY_LOCK:
        _REGISTRY[mid] = m
    return m


def get_mapping(mapping_id: str) -> Mapping | None:
    with _REGISTRY_LOCK:
        return _REGISTRY.get(mapping_id)


def close_mapping(mapping_id: str) -> None:
    """Drop the mapping from the in-memory registry. After this, deanonymize
    is a no-op for tokens minted by this mapping (they remain in output text
    but no original is available). Step 2's encrypted store will optionally
    survive close; this in-memory pass is what enforces the per-turn lifetime
    when persistence is off."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(mapping_id, None)


# ---------------------------------------------------------------------------
# Forward pass — pseudonymize_text
# ---------------------------------------------------------------------------


def pseudonymize_text(
    text: str,
    findings: list[dict],
    *,
    mapping: Mapping,
    source: str = "chat_text",
) -> str:
    """Replace each finding span with a stable token (or shape-fake).

    Args:
      text: original input.
      findings: list of `{rule_id, label, start, end, ...}` from `_pii_scan_text`.
      mapping: target Mapping (mutated in place — new entries added).
      source: label recorded on `mapping.sources` for audit (e.g. "chat_text",
        "attachment:report.docx").

    Returns:
      Text with each finding span replaced. Spans are processed end-descending
      so earlier offsets stay valid through the rewrite.
    """
    if not text or not findings:
        return text

    if source and source not in mapping.sources:
        mapping.sources.append(source)

    # Sort by start desc so splicing from the end keeps earlier offsets stable.
    # Then by end desc as tiebreaker (longer match wins on identical start —
    # though the scanner's overlap suppression already prevents this case).
    ordered = sorted(findings, key=lambda f: (-f["start"], -f["end"]))

    out = text
    for f in ordered:
        s, e = f["start"], f["end"]
        if s < 0 or e > len(text) or s >= e:
            continue
        original = text[s:e]
        # If this span is already a known fake in this mapping (e.g. a
        # mod-97-valid synthetic IBAN re-matched by the scanner on a later
        # pass over the same buffer), leave it untouched. Otherwise we'd
        # build a chain real → fake1 → fake2 that deanonymise can only
        # collapse one hop at a time.
        if original in mapping.reverse:
            continue
        rule_id = f["rule_id"]
        # Stable within a mapping: same original → same token even if it
        # appears in multiple sources.
        replacement = mapping.forward.get(original)
        if replacement is None:
            replacement = _build_replacement(original, rule_id, mapping)
            mapping.record(original, replacement, rule_id)
        out = out[:s] + replacement + out[e:]

    return out


def _build_replacement(original: str, rule_id: str, mapping: Mapping) -> str:
    """Pick shape-fake vs opaque token based on rule_id."""
    gen = _SHAPE_GENERATORS.get(rule_id)
    if gen is not None and rule_id in SHAPE_PRESERVING:
        try:
            return gen(original, mapping.salt)
        except Exception:
            # Defensive: shape-fake bug must never leak the original. Fall
            # through to opaque token.
            pass
    return mapping.next_token(rule_id)


# ---------------------------------------------------------------------------
# Reverse pass — deanonymize_text
# ---------------------------------------------------------------------------


def deanonymize_text(text: str, *, mapping: Mapping) -> tuple[str, int]:
    """Restore original values in `text`.

    Two-phase lookup:
      1. Direct: replace every key in `mapping.reverse` that appears in `text`.
         This covers shape-fakes (which are not regex-matchable in general)
         and opaque tokens that survived intact.
      2. Tolerant regex: for any `<KIND_N_SALT>` token whose salt matches
         this mapping's salt but with whitespace / case mangling, normalise
         to the strict form and look up.

    Returns `(restored_text, restored_count)`.
    """
    if not text or not mapping.reverse:
        return text, 0

    out = text
    restored = 0

    # Phase 1: direct token substring replacement. Sort by length desc so
    # longer tokens replace first and we don't accidentally match a prefix
    # of a longer token.
    #
    # Iterate to a fixed point — defends against chained mappings
    # (real → fake1 → fake2) that could form if a buffer was pseudonymised
    # twice with the same Mapping. The pseudonymize_text guard prevents
    # chain creation at the source; this loop is a safety net so an
    # existing chain in a persisted mapping (or a future code path that
    # forms one) still resolves to the real value. Cap at len(reverse)+1
    # iterations so a self-referential mapping can't infinite-loop.
    sorted_keys = sorted(mapping.reverse.keys(), key=len, reverse=True)
    max_passes = len(sorted_keys) + 1
    for _ in range(max_passes):
        changed = False
        for token in sorted_keys:
            if token in out:
                count = out.count(token)
                if count:
                    out = out.replace(token, mapping.reverse[token])
                    restored += count
                    changed = True
        if not changed:
            break

    # Phase 2: tolerant — recover LLM-mangled opaque tokens.
    salt = mapping.salt

    def _tolerant_sub(m: re.Match) -> str:
        nonlocal restored
        kind, n, found_salt = m.group(1), m.group(2), m.group(3)
        if found_salt.lower() != salt.lower():
            return m.group(0)  # Different mapping or noise.
        canonical = f"<{kind.upper()}_{n}_{salt}>"
        if canonical in mapping.reverse:
            restored += 1
            return mapping.reverse[canonical]
        return m.group(0)

    out = _TOKEN_RE_TOLERANT.sub(_tolerant_sub, out)

    return out, restored


# ---------------------------------------------------------------------------
# Span locator — for UI highlighting of de-anonymised values
# ---------------------------------------------------------------------------


def find_restored_spans(text: str, *, mapping: Mapping) -> list[dict]:
    """Locate every occurrence of each mapping entry's *original* value in
    `text` (which should be the de-anonymised final text). Returns a list of
    non-overlapping spans:

        [{"start": int, "end": int, "original": str, "fake": str,
          "category": str}, ...]

    Longest originals are matched first so a longer value can't be eclipsed
    by a shorter one that happens to be a substring (e.g. an email address
    that contains a domain that's also tracked separately). Spans are sorted
    ascending by start. Returns `[]` for empty text or empty mapping.

    The fake field carries the mapping's forward replacement so the UI can
    show "original was anonymised as fake" without re-querying the mapping.
    The category is the rule_id (e.g. "email", "iban") — legacy mappings
    without per-entry categories fall back to "unknown".
    """
    if not text or not mapping.forward:
        return []
    # Sort originals by length desc — a longer match claims its span before
    # any shorter substring inside it gets a chance.
    originals = sorted(mapping.forward.keys(), key=len, reverse=True)
    claimed: list[tuple[int, int]] = []
    spans: list[dict] = []

    def _overlaps(s: int, e: int) -> bool:
        for cs, ce in claimed:
            if s < ce and e > cs:
                return True
        return False

    for orig in originals:
        if not orig:
            continue
        start = 0
        while True:
            i = text.find(orig, start)
            if i < 0:
                break
            j = i + len(orig)
            if not _overlaps(i, j):
                spans.append({
                    "start": i,
                    "end": j,
                    "original": orig,
                    "fake": mapping.forward.get(orig, ""),
                    "category": mapping.categories.get(orig, "unknown"),
                })
                claimed.append((i, j))
            start = j
    spans.sort(key=lambda s: s["start"])
    return spans


# ---------------------------------------------------------------------------
# Convenience: one-shot scan-and-pseudonymize
# ---------------------------------------------------------------------------


def pseudonymize_with_scanner(
    text: str,
    scanner: Callable[[str], list[dict]],
    *,
    mapping: Mapping,
    source: str = "chat_text",
) -> tuple[str, list[dict]]:
    """Helper: call `scanner(text)` and pseudonymize in one shot. Used in
    tests and in step 3 to keep the chat-worker call site small. `scanner`
    must return the same shape as `_pii_scan_text`."""
    findings = scanner(text)
    if not findings:
        return text, []
    return pseudonymize_text(text, findings, mapping=mapping, source=source), findings


# ---------------------------------------------------------------------------
# Persistence — AES-GCM encryption + SQLite mirror
# ---------------------------------------------------------------------------
#
# Why encrypt: the SQLite row sits on the same disk as the key, so encryption
# is NOT confidentiality against the server admin. The actual security
# boundary is "data never leaves this machine". The encryption is
# defense-in-depth: a backup tape, an exfiltrated DB file, or a forgotten
# `cp chats.db /tmp/share` does not also leak the cleartext PII map.
#
# Key lifecycle: bootstrapped lazily at first use into
# `agents/main/pseudonym.key` (32 random bytes, mode 0600). Rotating the key
# invalidates every persisted map — by design; maps are per-turn anyway.


_KEY_PATH_OVERRIDE: str | None = None  # set by tests
_KEY_CACHE: bytes | None = None
_KEY_LOCK = threading.Lock()


def _default_key_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "agents", "main", "pseudonym.key",
    )


def _key_path() -> str:
    return _KEY_PATH_OVERRIDE or _default_key_path()


def _load_or_create_key() -> bytes:
    """Read the 32-byte AES key from disk, generating it on first use.

    Atomic: the keyfile is written via tmp+rename and chmod 0600 so a partial
    write or shared-filesystem permission slip doesn't leave a readable secret.
    """
    global _KEY_CACHE
    with _KEY_LOCK:
        if _KEY_CACHE is not None:
            return _KEY_CACHE
        path = _key_path()
        if os.path.exists(path):
            with open(path, "rb") as f:
                key = f.read()
            if len(key) != 32:
                raise RuntimeError(
                    f"pseudonym.key at {path} has unexpected length {len(key)} "
                    "(expected 32). Refusing to use — manual intervention required."
                )
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            key = secrets.token_bytes(32)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(key)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                # Best-effort on platforms that don't honor chmod (Windows);
                # the file still has whatever default permissions the FS gave.
                pass
            os.replace(tmp, path)
        _KEY_CACHE = key
        return key


def _serialize_mapping(m: Mapping) -> dict:
    """Mapping → dict suitable for json.dumps. `counters` keys are already
    strings; everything else is str/int."""
    return {
        "mapping_id": m.mapping_id,
        "salt": m.salt,
        "forward": m.forward,
        "counters": m.counters,
        "sources": m.sources,
        "finding_counts": m.finding_counts,
        "categories": m.categories,
    }


def _deserialize_mapping(d: dict) -> Mapping:
    m = Mapping(mapping_id=d["mapping_id"], salt=d["salt"])
    m.forward = dict(d.get("forward") or {})
    m.reverse = {v: k for k, v in m.forward.items()}
    m.counters = dict(d.get("counters") or {})
    m.sources = list(d.get("sources") or [])
    m.finding_counts = dict(d.get("finding_counts") or {})
    m.categories = dict(d.get("categories") or {})
    return m


def encrypt_mapping(m: Mapping) -> tuple[bytes, bytes]:
    """Encrypt a Mapping for at-rest storage. Returns `(nonce, ciphertext)`.

    Caller stores both alongside `mapping_id` in SQLite; nonce is 12 random
    bytes (AES-GCM standard), ciphertext includes the GCM tag at the end.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _load_or_create_key()
    nonce = secrets.token_bytes(12)
    payload = json.dumps(_serialize_mapping(m), ensure_ascii=False).encode("utf-8")
    aead = AESGCM(key)
    # AAD binds the ciphertext to its mapping_id so swapping rows is detected.
    ct = aead.encrypt(nonce, payload, m.mapping_id.encode("ascii"))
    return nonce, ct


def decrypt_mapping(mapping_id: str, nonce: bytes, ciphertext: bytes) -> Mapping:
    """Reverse of `encrypt_mapping`. Raises if the key is wrong, the row was
    tampered with, or the mapping_id doesn't match the bound AAD."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _load_or_create_key()
    aead = AESGCM(key)
    payload = aead.decrypt(nonce, ciphertext, mapping_id.encode("ascii"))
    d = json.loads(payload.decode("utf-8"))
    return _deserialize_mapping(d)


def save_mapping(m: Mapping, *, session_id: str, turn_id: str | None = None) -> None:
    """Encrypt and persist the mapping to chats.db. Idempotent — re-saving
    the same mapping_id overwrites the row. Safe to call multiple times during
    a turn (e.g. after each new pseudonymize_text/file call extends the map).
    """
    from server_lib.db import ChatDB
    nonce, ct = encrypt_mapping(m)
    ChatDB.save_pseudonym_map(
        mapping_id=m.mapping_id,
        session_id=session_id,
        turn_id=turn_id or "",
        nonce=nonce,
        ciphertext=ct,
    )


def load_mapping(mapping_id: str) -> Mapping | None:
    """Read encrypted mapping from chats.db and decrypt. Returns None if not
    found. Caller is responsible for adding the result to the in-memory
    registry if they want subsequent `get_mapping(mapping_id)` to find it
    (use `restore_mapping_to_registry`)."""
    from server_lib.db import ChatDB
    row = ChatDB.load_pseudonym_map(mapping_id)
    if not row:
        return None
    nonce, ct = row
    return decrypt_mapping(mapping_id, nonce, ct)


def restore_mapping_to_registry(m: Mapping) -> None:
    """Insert a previously-saved (decrypted) mapping back into the in-memory
    registry. Used during chat reload to restore deanonymisation capability
    for historical messages, and during boot recovery."""
    with _REGISTRY_LOCK:
        _REGISTRY[m.mapping_id] = m


def delete_persisted_mapping(mapping_id: str) -> None:
    """Drop the encrypted row from chats.db. Does NOT remove from in-memory
    registry — call `close_mapping` for that, or both for full cleanup."""
    from server_lib.db import ChatDB
    ChatDB.delete_pseudonym_map(mapping_id)


# ---------------------------------------------------------------------------
# Reverse-pass file walker — thin re-export of engine.file_pseudonymize.
#
# Note (2026-05-16, v9.x): the forward `pseudonymize_file` walker was
# retired. Pseudonymisation of attachment content now happens text-side,
# inside `brain.tool_read_document` / `tool_read_file` via
# `brain._gdpr_anon_tool_text`. The reverse walker is still needed for
# files the LLM writes back: `brain._after_file_write` calls
# `deanonymize_file` to restore real PII into artifacts before the user
# sees them.
# ---------------------------------------------------------------------------


def deanonymize_file(src_path: str, dst_path: str, *, mapping: Mapping) -> int:
    """Reverse pass. Returns count of tokens restored. Unsupported types
    are copied through unchanged (return 0) — caller wraps the call in a
    "did the LLM write a file we never sent?" guard."""
    from engine.file_pseudonymize import deanonymize_file as _impl
    return _impl(src_path, dst_path, mapping=mapping)


# ---------------------------------------------------------------------------
# Public API summary
# ---------------------------------------------------------------------------

__all__ = [
    "Mapping",
    "SHAPE_PRESERVING",
    "new_mapping",
    "get_mapping",
    "close_mapping",
    "pseudonymize_text",
    "deanonymize_text",
    "pseudonymize_with_scanner",
    "find_restored_spans",
    # Persistence
    "encrypt_mapping",
    "decrypt_mapping",
    "save_mapping",
    "load_mapping",
    "restore_mapping_to_registry",
    "delete_persisted_mapping",
    # File walkers (reverse pass only — forward pseudonymisation lives in
    # brain.tool_read_document via _gdpr_anon_tool_text).
    "deanonymize_file",
]
