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

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

# Pure stdlib module (difflib) — no brain import, safe at module level.
from engine import identity as _identity


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
    # L2 (Entitäts-Schicht, PII_ANALYSIS_PARITY_HANDOVER.md): Geburtsdaten
    # behalten ihren Keyword-Prefix und bekommen den Session-Datums-Offset;
    # Passnummern werden formgleiche Fakes (wie IBAN mod-97 / CC Luhn schon
    # immer); MRZ-Zeilen werden komplett konsistent neu gebaut, mit GÜLTIGEN
    # ICAO-9303-Prüfziffern — sonst produziert die LLM-eigene MRZ-Mathematik
    # falsche Fälschungsindizien (Failure F2).
    "dob",
    "passport",
    "passport_ctx_loose",
    "mrz",
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


# Month-name catalogs for textual date formats ('5 FEB 1947', '26. Jan 2027').
# EN + DE, full + abbreviated; rendering preserves language, abbreviation
# style and case of the original token.
_MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december")
_MONTHS_DE = ("januar", "februar", "märz", "april", "mai", "juni", "juli",
              "august", "september", "oktober", "november", "dezember")
_MONTHS_EN_AB = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                 "sep", "oct", "nov", "dec")
_MONTHS_DE_AB = ("jan", "feb", "mär", "apr", "mai", "jun", "jul", "aug",
                 "sep", "okt", "nov", "dez")


def _parse_month_token(tok: str):
    """Month-name token → (month_1_12, catalog) or (None, None)."""
    t = tok.lower().rstrip(".")
    for cat in (_MONTHS_EN, _MONTHS_DE):
        if t in cat:
            return cat.index(t) + 1, cat
    for cat in (_MONTHS_EN_AB, _MONTHS_DE_AB):
        if t[:3] in cat and len(t) <= 4:
            return cat.index(t[:3]) + 1, cat
    return None, None


def _render_month_like(tok: str, month: int, catalog: tuple) -> str:
    """Render `month` in the same style as the original token `tok`
    (language via catalog, abbreviation length, case)."""
    name = catalog[month - 1]
    if catalog in (_MONTHS_EN_AB, _MONTHS_DE_AB):
        out = name
    elif len(tok.rstrip(".")) <= 4:
        out = name[:len(tok.rstrip("."))]
    else:
        out = name
    if tok.isupper():
        out = out.upper()
    elif tok[0].isupper():
        out = out.title()
    if tok.endswith("."):
        out += "."
    return out


# Date formats we recognise. Order matters: longest/most-specific first.
# Each entry is (compiled_regex, "format_id"). Format id drives reconstruction.
_DATE_PATTERNS: tuple = (
    # ISO 8601: 2026-05-19
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "iso"),
    # EXIF: 2026:07:02 [14:24:48] — time part (if any) passes through verbatim.
    (re.compile(r"^(\d{4}):(\d{2}):(\d{2})(\s+\d{2}:\d{2}:\d{2})?$"), "exif"),
    # Textual month: 5 FEB 1947 / 05 Feb 1947 / 26. Jan 2027 / 19 JAN 2007
    (re.compile(r"^(\d{1,2})(\.?)[ ]([A-Za-zÄÖÜäöüß]{3,9}\.?)[ ](\d{4})$"),
     "dd_mon_yyyy"),
    # European: 19.05.2026 / 19.5.2026 / 19-05-2026
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "eu_dot"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "eu_dash"),
    # US: 05/19/2026 / 5/19/2026
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "us_slash"),
    # 2-digit year variants
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), "eu_dot_yy"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"), "us_slash_yy"),
)


def date_offset_days(salt: str) -> int:
    """Constant per-mapping day offset (L2c). Derived from the salt →
    deterministic, survives persistence with zero schema change. Range
    ±5..25, never 0 — a CONSTANT offset keeps ordering, deltas ('10y − 1d'),
    renewal gaps and EXIF distances EXACT, which the old per-value day
    jitter destroyed (Failure F2: inverted issue-before-expiry, broken
    validity spans → false forgery indications)."""
    seed = _seed_int("__date_offset__", salt, n=4)
    days = 5 + (seed % 21)
    return days if (seed >> 24) & 1 else -days


def _fake_date(original: str, salt: str) -> str:
    """Shift a date by the mapping's constant day offset, preserving the
    original's exact format (separator style, zero-padding, month-name
    language/abbreviation/case, EXIF time suffix). Year/month may drift at
    month boundaries — accepted trade-off (handover decision L2c) in
    exchange for exact relational arithmetic. Document-lifecycle dates
    (issue/expiry) never reach this generator: the scanner only emits
    `date` findings with birth-/life-event context."""
    raw = original.strip()
    for pat, fmt in _DATE_PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        mon_cat = None
        suffix = ""
        if fmt in ("iso", "exif"):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if fmt == "exif":
                suffix = m.group(4) or ""
        elif fmt == "dd_mon_yyyy":
            d, y = int(m.group(1)), int(m.group(4))
            mo, mon_cat = _parse_month_token(m.group(3))
            if mo is None:
                continue  # not a month name — let other patterns try
        elif fmt in ("eu_dot", "eu_dash", "eu_dot_yy"):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if fmt == "eu_dot_yy":
                y = 2000 + y if y < 50 else 1900 + y
        elif fmt in ("us_slash", "us_slash_yy"):
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if fmt == "us_slash_yy":
                y = 2000 + y if y < 50 else 1900 + y
        else:  # pragma: no cover
            continue
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
            continue
        try:
            shifted = (_dt.date(y, mo, d)
                       + _dt.timedelta(days=date_offset_days(salt)))
        except ValueError:
            continue
        ny, nmo, nd = shifted.year, shifted.month, shifted.day
        # Reassemble in the original format.
        if fmt == "iso":
            return f"{ny:04d}-{nmo:02d}-{nd:02d}"
        if fmt == "exif":
            return f"{ny:04d}:{nmo:02d}:{nd:02d}{suffix}"
        if fmt == "dd_mon_yyyy":
            d_pad = len(m.group(1)) == 2
            d_s = f"{nd:02d}" if d_pad else str(nd)
            mon_s = _render_month_like(m.group(3), nmo, mon_cat)
            return f"{d_s}{m.group(2)} {mon_s} {ny:04d}"
        if fmt == "eu_dot":
            d_pad = len(m.group(1)) == 2
            mo_pad = len(m.group(2)) == 2
            d_s = f"{nd:02d}" if d_pad else str(nd)
            mo_s = f"{nmo:02d}" if mo_pad else str(nmo)
            return f"{d_s}.{mo_s}.{ny:04d}"
        if fmt == "eu_dash":
            return f"{nd:02d}-{nmo:02d}-{ny:04d}"
        if fmt == "us_slash":
            d_pad = len(m.group(2)) == 2
            mo_pad = len(m.group(1)) == 2
            d_s = f"{nd:02d}" if d_pad else str(nd)
            mo_s = f"{nmo:02d}" if mo_pad else str(nmo)
            return f"{mo_s}/{d_s}/{ny:04d}"
        if fmt == "eu_dot_yy":
            return f"{nd:02d}.{nmo:02d}.{ny % 100:02d}"
        if fmt == "us_slash_yy":
            return f"{nmo:02d}/{nd:02d}/{ny % 100:02d}"
    # Unrecognised → pass through unchanged; _build_replacement detects the
    # unchanged value and falls back to an opaque token. (The old behavior —
    # returning just the year — put a bare '1947'→full-date entry into the
    # reverse map, which would rewrite every occurrence of that year.)
    return raw


# Unanchored search version of the date shapes — used to locate the date
# INSIDE a keyword-carrying span ('born 05.02.1947', 'DOB: 5 FEB 1947').
_DATE_SEARCH_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{4}:\d{2}:\d{2}(?:\s+\d{2}:\d{2}:\d{2})?"
    r"|\d{1,2}\.?[ ][A-Za-zÄÖÜäöüß]{3,9}\.?[ ]\d{4}"
    r"|\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}"
)


def _fake_dob(original: str, salt: str) -> str:
    """`dob` spans include the trigger keyword ('born …', 'Geburtsdatum: …').
    Keep the keyword verbatim, shift only the date part — so the LLM still
    understands WHAT the value is while the value itself is offset."""
    m = _DATE_SEARCH_RE.search(original)
    if not m:
        return original  # → opaque-token fallback in _build_replacement
    fake = _fake_date(m.group(0), salt)
    if fake == m.group(0):
        return original
    return original[:m.start()] + fake + original[m.end():]


_SHAPE_GENERATORS: dict[str, Callable[[str, str], str]] = {
    "iban": _fake_iban,
    "credit_card": _fake_credit_card,
    "phone": _fake_phone,
    "name": _fake_name,
    "address": _fake_address,
    "organisation": _fake_organisation,
    "email": _fake_email,
    "date": _fake_date,
    "dob": _fake_dob,
}


# ---------------------------------------------------------------------------
# L2 — Entity layer (PII_ANALYSIS_PARITY_HANDOVER.md §L2)
# ---------------------------------------------------------------------------
#
# ONE fake identity per person: every surface form of the same person
# ("STARK, BONNIE MARIE" · "Bonnie M Stark" · "STARK<<BONNIE<MARIE" ·
# "kbstark@…" · OCR garble) maps onto the FORM-MATCHING variant of the same
# fake identity. Without this, each variant gets an independent fake and the
# LLM sees 3-5 different persons — the identity join breaks and partial
# anonymisation manufactures FALSE fraud signals (Failure F1).
#
# Matching/rendering logic lives in engine/identity.py (shared with the L1
# doc_checks tools); this section owns the Mapping wiring.


def _is_person_ent(ent: dict) -> bool:
    """Entitäten ohne `kind` sind Personen (Legacy-Mappings von Platte, die vor
    M4 serialisiert wurden — die Org-Schicht kam erst v9.344.0 dazu)."""
    return ent.get("kind", "person") == "person"


def _entity_find(mapping: Mapping, form: str) -> dict | None:
    for ent in mapping.entities.values():
        if not _is_person_ent(ent):
            continue
        if _identity.entity_attach(form, ent["sur"], ent["givens"]):
            return ent
    return None


def _pick_avoiding(seq: tuple, key: str, salt: str, kind: str,
                   taken: set[str]) -> str:
    """Deterministic pool pick that walks past collisions — two REAL persons
    must never share a fake surname (that would merge them in the wire), and
    a fake must never equal a real token of the mapping's entities."""
    h = hashlib.sha256(f"{salt}:{kind}:{key}".encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "big") % len(seq)
    for i in range(len(seq)):
        cand = seq[(idx + i) % len(seq)]
        if cand.lower() not in taken:
            return cand
    return seq[idx]  # pool exhausted — accept the collision


def _entity_taken_tokens(mapping: Mapping) -> set[str]:
    """Alle real+fake belegten Tokens ÜBER BEIDE Entitäts-Arten — ein Org-Fake
    darf nicht auf einem Personen-Fake landen (und umgekehrt), sonst
    verschmelzen im Wire eine Firma und eine Person auf denselben String."""
    taken: set[str] = set()
    for ent in mapping.entities.values():
        if _is_person_ent(ent):
            taken.add(ent["sur"])
            taken.update(g for g in ent["givens"] if g)
            taken.add(ent["fake_sur"].lower())
            taken.update(f.lower() for f in ent["fake_givens"])
        else:
            taken.update(t for t in ent.get("stem", []) if t)
            taken.update(f.lower() for f in ent.get("fake_stem", []) if f)
    return taken


# Plausibler Namens-Token (lowercase, wie name_tokens liefert): Buchstaben
# inkl. Umlaute/Akzente, innen Apostroph/Bindestrich. Weist NER-Müll-Spans ab
# ('n>', '[projekt-gruppe'), die sonst Garbage-Entitäten mit sinnlosen
# Varianten-Registrierungen erzeugen (am Live-Material beobachtet).
_NAME_TOKEN_OK_RE = re.compile(r"^[a-zà-öø-ÿß][a-zà-öø-ÿß'\-]{0,24}$")


def _entity_create(mapping: Mapping, form: str) -> dict:
    sur, givens = _identity.guess_structure(form)
    if not sur or not _NAME_TOKEN_OK_RE.match(sur):
        raise ValueError(f"no name structure in {form!r}")
    givens = [g for g in givens if _NAME_TOKEN_OK_RE.match(g)]
    if len(givens) > 3:
        # >3 Vornamen = fast sicher ein Müll-Span (Markdown-Zeile o. Ä.) —
        # kein Entitäts-Anker; der Aufrufer fällt auf den einfachen
        # _fake_name-Shape-Fake zurück.
        raise ValueError(f"implausible name form {form!r}")
    taken = _entity_taken_tokens(mapping) | {sur} | set(givens)
    key = " ".join(sorted([sur] + [g for g in givens if g]))
    fake_sur = _pick_avoiding(_LAST_NAMES, key, mapping.salt, "last", taken)
    taken.add(fake_sur.lower())
    fake_givens: list[str] = []
    for i, g in enumerate(givens):
        f = _pick_avoiding(_FIRST_NAMES, key, mapping.salt, f"given{i}", taken)
        taken.add(f.lower())
        fake_givens.append(f)
    ent = {"kind": "person",
           "sur": sur, "givens": list(givens),
           "fake_sur": fake_sur, "fake_givens": fake_givens}
    mapping.entities[f"e{len(mapping.entities) + 1}"] = ent
    return ent


def _entity_learn(mapping: Mapping, ent: dict, form: str) -> bool:
    """Upgrade initials to full given names when a richer form arrives
    (entity knew 'm', form brings 'marie') and adopt genuinely new given
    names. Returns True when the entity changed (→ re-register variants)."""
    changed = False
    toks = _identity.name_tokens(form)
    if len(toks) > 4:
        # Müll-Span (Markdown-Zeile, Tabellenzeile): attachen ja (der Fake
        # bleibt konsistent), aber daraus keine 'Vornamen' adoptieren —
        # sonst lernt die Entität 'free'/'publicreco' (Live-Befund).
        return False
    for t in toks:
        if len(t) <= 1 or t == ent["sur"]:
            continue
        matched = False
        for i, g in enumerate(ent["givens"]):
            if t == g:
                matched = True
                break
            if len(g) == 1 and t.startswith(g):
                ent["givens"][i] = t   # initial → full name; fake stays stable
                matched, changed = True, True
                break
            if len(t) >= 4 and len(g) >= 4 \
                    and _identity._ratio(t, g) >= _identity.GARBLE_FLOOR:
                matched = True         # OCR garble of a known given — no learn
                break
        if not matched and len(t) >= 4 \
                and _identity._ratio(t, ent["sur"]) >= _identity.GARBLE_FLOOR:
            matched = True             # garble of the surname
        if (not matched and len(ent["givens"]) < 4
                and _NAME_TOKEN_OK_RE.match(t)
                and _identity.entity_attach(form, ent["sur"], ent["givens"])):
            # New middle name ('Bonnie MARIE Stark' when entity has [bonnie]).
            taken = _entity_taken_tokens(mapping) | {t}
            key = " ".join(sorted([ent["sur"]] + ent["givens"] + [t]))
            fake = _pick_avoiding(_FIRST_NAMES, key, mapping.salt,
                                  f"given{len(ent['givens'])}", taken)
            ent["givens"].append(t)
            ent["fake_givens"].append(fake)
            changed = True
    return changed


def _register_entity_variants(mapping: Mapping, ent: dict) -> None:
    """Predictable surface-form PAIRS become REAL forward/reverse entries —
    the args-deanon (L3a) and the web-egress gate read those tables, so
    registered variants make both entity-aware without touching their code."""
    pairs = _identity.standard_variant_pairs(
        ent["sur"], ent["givens"], ent["fake_sur"], ent["fake_givens"])
    for real, fake in pairs:
        if real in mapping.forward or real in mapping.reverse:
            continue
        if fake in mapping.reverse:
            continue
        mapping.record(real, fake, "name", count=False)


def _entity_fake_name(original: str, mapping: Mapping) -> str:
    ent = _entity_find(mapping, original)
    if ent is None:
        ent = _entity_create(mapping, original)
    else:
        _entity_learn(mapping, ent, original)
    fake = _identity.render_variant(
        original, ent["sur"], ent["givens"],
        ent["fake_sur"], ent["fake_givens"])
    _register_entity_variants(mapping, ent)
    return fake


# ---------------------------------------------------------------------------
# M4 — Organisations-Entitäten (PII_PARITY_WAVE2_HANDOVER.md §M4 / G2)
#
# Spiegelbild der Personen-Schicht: EIN Fake pro Firma, jede Oberflächenform
# (Langform/Kurzform/ALLCAPS-Registryform/Slug/Rechtsform-Varianten) rendert die
# formgleiche Variante DESSELBEN Fakes. Ohne das bekommt jede Oberflächenform
# einen eigenen Fake — dann bricht der Sanktions-/Registry-Abgleich (die Listen
# führen ALLCAPS-/Aliasformen: anderer String → anderer Fake → stiller False
# Negative in einem REGULATORISCHEN Bericht) und die Konzernstruktur, die im
# Namens-Enthaltensein steckt, wird gelöscht.
#
# Normalisierung/Rendering: engine/identity.py (org_*). Hier lebt nur das
# Mapping-Wiring — exakt die Arbeitsteilung der Personen-Schicht.
# ---------------------------------------------------------------------------

# Fake-Stamm-Pool: neutrale, klar erfundene Wortstämme. Bewusst NICHT die
# Cartoon-Namen aus _ORG_NAMES (Acme/Globex/Hooli) — ein Fake muss wie eine
# echte Firma AUSSEHEN, damit das Modell ihn als Firma behandelt und die
# Analysequalität nicht kippt, aber nicht wie eine ECHTE existierende.
_ORG_STEM_POOL = (
    "Nordstern", "Weststadt", "Blauwald", "Hochfeld", "Silberbach", "Rotbuche",
    "Grünthal", "Steinbrück", "Altmark", "Feldkirch", "Lindenau", "Sonnborn",
    "Marbach", "Eichgraben", "Kaltenberg", "Ravensbrunn", "Talheim",
    "Norwood", "Eastgate", "Fairbridge", "Kingsford", "Ashcroft", "Brightmoor",
    "Cedarholm", "Lakemont", "Ridgeway", "Stonefield", "Westbrook",
)
# Zweit-/Folge-Tokens (Tochter-/Sparten-Bezeichner) — dieselbe Rolle wie
# 'Immobilien'/'Invest' im echten Material.
#
# KEINE Wörter aus `_ORG_GENERIC_SOLO` hier hinein (Trust/Holding/Group/
# Partner/Capital/Management/Services): ein Fake-Token, das SELBST ein
# generisches Konzernwort ist, wird beim nächsten Scan als frische Org-PII
# klassifiziert und ein zweites Mal gefakt — FAKES-VON-FAKES, das bricht den
# Reply-Deanonymisierer und die NUTZERIN sieht den Fake (gemessen: der
# Fake-Stamm 'Nordstern Trust' wurde zu 'NORDSTERN Stark Corp'). Der Handover
# nennt genau diese Falle; sie schnappt auch im Fake-POOL zu, nicht nur bei
# einem zweiten Seam.
_ORG_QUALIFIER_POOL = (
    "Immobilien", "Anlagen", "Technik", "Handel", "Logistik", "Bau",
    "Energie", "Metall", "Chemie", "Papier", "Textil", "Werke",
    "Trading", "Supply", "Industries", "Systems", "Logistics", "Overseas",
    "Maritime", "Pacific", "Atlantic", "Continental",
)


def _org_find(mapping: Mapping, stem: list[str]) -> dict | None:
    for ent in mapping.entities.values():
        if _is_person_ent(ent):
            continue
        if ent.get("stem") == list(stem):
            return ent
    return None


def _org_fake_stem(mapping: Mapping, stem: list[str]) -> list[str]:
    """Fake-Stamm für `stem` — und dabei die KONZERNSTRUKTUR spiegeln.

    Teilt der neue Stamm ein Präfix mit einer bekannten Org-Entität
    ('wiener privatbank' ⊂ 'wiener privatbank immobilien'), erbt er deren
    Fake-Präfix und mintet nur für die ZUSÄTZLICHEN Tokens neue Fakes:

        Wiener Privatbank      → Nordstern Weststadt
        Wiener Privatbank Immobilien → Nordstern Weststadt Immobilien
                                       ^^^^^^^^^^^^^^^^^^^ geerbt

    Damit bleibt die Mutter-Tochter-Beziehung im Fake-Raum SICHTBAR — sie
    steckt real im Namens-Enthaltensein, und getrennte Fakes würden sie
    löschen (der Kern von G2). Umgekehrt gilt es auch: taucht die Mutter NACH
    der Tochter auf, erbt sie das gemeinsame Präfix von dieser."""
    taken = _entity_taken_tokens(mapping)
    fake: list[str] = []
    # Längstes gemeinsames Präfix mit einer bekannten Org-Entität finden.
    best: list[str] = []
    best_fake: list[str] = []
    for ent in mapping.entities.values():
        if _is_person_ent(ent):
            continue
        other, other_fake = ent.get("stem") or [], ent.get("fake_stem") or []
        n = 0
        for a, b in zip(stem, other):
            if a != b:
                break
            n += 1
        if n and n > len(best) and n <= len(other_fake):
            best, best_fake = list(other[:n]), list(other_fake[:n])
    fake.extend(best_fake)
    for i in range(len(fake), len(stem)):
        pool = _ORG_STEM_POOL if i == 0 else _ORG_QUALIFIER_POOL
        key = " ".join(stem[:i + 1])
        f = _pick_avoiding(pool, key, mapping.salt, f"orgstem{i}", taken)
        taken.add(f.lower())
        fake.append(f)
    return fake


def _org_create(mapping: Mapping, form: str) -> dict:
    stem, lf = _identity.org_structure(form)
    if not stem:
        raise ValueError(f"no org structure in {form!r}")
    if len(stem) > 6:
        # Müll-Span (halber Satz, Tabellenzeile) — kein Entitäts-Anker; der
        # Aufrufer fällt auf den einfachen _fake_organisation-Shape-Fake zurück.
        raise ValueError(f"implausible org form {form!r}")
    ent = {"kind": "org", "stem": list(stem),
           "fake_stem": _org_fake_stem(mapping, stem),
           "legal_forms": [lf] if lf else []}
    mapping.entities[f"e{len(mapping.entities) + 1}"] = ent
    return ent


def _register_org_variants(mapping: Mapping, ent: dict) -> None:
    """Wie `_register_entity_variants` bei Personen: die erwartbaren
    Oberflächenformen werden ECHTE forward/reverse-Paare, wodurch L3a-Deanon
    und das Web-Egress-Gate (und damit M5s Auto-Release) org-fähig werden,
    ohne dass dort Code angefasst wird."""
    pairs = _identity.org_variant_pairs(
        ent["stem"], ent["fake_stem"], ent.get("legal_forms") or [])
    for real, fake in pairs:
        if real in mapping.forward or real in mapping.reverse:
            continue
        if fake in mapping.reverse:
            continue
        mapping.record(real, fake, "organisation", count=False)


def _entity_fake_organisation(original: str, mapping: Mapping) -> str | None:
    """Entitäts-Generator für `organisation` (Gegenstück zu _entity_fake_name).
    Gibt None zurück, wenn keine Org-Struktur erkennbar ist → der Aufrufer
    fällt auf den alten String-Fake `_fake_organisation` zurück."""
    stem, lf = _identity.org_structure(original)
    if not stem:
        return None
    ent = _org_find(mapping, stem)
    if ent is None:
        try:
            ent = _org_create(mapping, original)
        except ValueError:
            return None
    elif lf and lf not in (ent.get("legal_forms") or []):
        # Neue Rechtsform-Oberfläche derselben Firma ('… SE' nach '… AG') —
        # als Variante mitregistrieren.
        ent.setdefault("legal_forms", []).append(lf)
    fake = _identity.org_render_variant(original, ent["stem"], ent["fake_stem"])
    _register_org_variants(mapping, ent)
    if fake == original:
        return None
    return fake


def _mrz_name_form(line: str) -> str | None:
    """MRZ-Namenszeile → 'Vornamen Nachname'-Form (garble-bereinigt) für die
    Entitäts-Maschinerie, sonst None."""
    m = _MRZ_NAME_RE.match(line.strip())
    if not m or "<<" not in m.group(2):
        return None
    sur, giv = _identity.parse_mrz_name(m.group(2))
    if not sur:
        return None
    try:
        from engine.tools.doc_checks import _strip_mrz_filler_garble
        giv = _strip_mrz_filler_garble(giv)
    except Exception:
        pass
    return f"{giv} {sur}".strip()


def _seed_entities_in_text_order(text: str, findings: list[dict],
                                 mapping: Mapping) -> None:
    """Entitäten in TEXT-Reihenfolge anlegen, BEVOR der Splice-Pass (end-
    absteigend) rendert. Echte Dokumente tragen den sauberen Scan vorn und
    OCR-Garble-Duplikate hinten — würde der Splice-Pass seeden, entstünde
    die Entität aus der schlechtesten Lesung und die saubere Form könnte
    nur noch attachen (gemessen am Referenz-JPG-Satz: Entität 'bonniecmartes'
    statt 'bonnie marie' → Glued-Varianten fehlen → Leak)."""
    for f in sorted(findings, key=lambda x: x.get("start", 0)):
        rid = f.get("rule_id")
        if rid not in ("name", "mrz", "organisation"):
            continue
        s, e = f.get("start", -1), f.get("end", -1)
        if not (0 <= s < e <= len(text)):
            continue
        val = text[s:e]
        if val in mapping.reverse or val in mapping.forward:
            continue
        try:
            if rid == "organisation":
                # Org-Entitäten (M4): LÄNGSTE Form zuerst wäre falsch — die
                # Text-Reihenfolge gilt auch hier, damit die Konzern-Spiegelung
                # (_org_fake_stem) die Mutter sieht, bevor die Tochter kommt.
                stem, _lf = _identity.org_structure(val)
                if not stem:
                    continue
                if _org_find(mapping, stem) is None:
                    _org_create(mapping, val)
                _register_org_variants(mapping, _org_find(mapping, stem))
                continue
            form = _mrz_name_form(val) if rid == "mrz" else val
            if not form:
                continue
            ent = _entity_find(mapping, form)
            if ent is None:
                ent = _entity_create(mapping, form)
            else:
                _entity_learn(mapping, ent, form)
            _register_entity_variants(mapping, ent)
        except Exception:
            continue


def _entity_fake_email(original: str, mapping: Mapping) -> str | None:
    """Email belonging to a known entity → fake identity's email, same
    localpart shape ('kbstark@pacbell.net' → 'muster@example.net',
    'bonnie.stark@…' → 'erika.muster@…'). Returns None when no entity
    matches — caller falls back to the generic _fake_email."""
    m = _EMAIL_RE.match(original.strip())
    if not m:
        return None
    local, domain = m.group(1), m.group(2)
    tld = domain.rsplit(".", 1)[-1].lower()[:6] if "." in domain else "org"
    lp = local.lower()
    for ent in mapping.entities.values():
        if not _is_person_ent(ent):
            continue
        sur = ent["sur"]
        if len(sur) < 4 or sur not in lp:
            continue
        fake_local = _identity.render_variant(
            local, sur, ent["givens"], ent["fake_sur"], ent["fake_givens"])
        if fake_local.lower() == lp:
            # Glued token ('kbstark') the token renderer couldn't split —
            # surgical: initials prefix from the fake givens + fake surname.
            idx = lp.find(sur)
            prefix = local[:idx]
            if prefix and prefix.isalpha():
                inits = "".join(f[0].lower() for f in ent["fake_givens"])
                prefix = (inits + "x" * len(prefix))[:len(prefix)]
            fake_local = f"{prefix}{ent['fake_sur'].lower()}{local[idx + len(sur):]}"
        return f"{fake_local.lower()}@example.{tld or 'org'}"
    return None


# ---------------------------------------------------------------------------
# L2b — passport numbers + MRZ lines (shape fakes with VALID ICAO checksums)
# ---------------------------------------------------------------------------


def _fake_id_like(bare: str, salt: str) -> str:
    """Same length, same per-position character class (letters stay letters,
    digits stay digits, separators verbatim). Deterministic from salt+value."""
    seed = hashlib.sha256(f"{salt}:idlike:{bare}".encode("utf-8")).digest()
    out = []
    for i, c in enumerate(bare):
        b = seed[i % len(seed)]
        if c.isdigit():
            out.append(str(b % 10))
        elif c.isalpha():
            ch = chr(ord("A") + b % 26)
            out.append(ch if c.isupper() else ch.lower())
        else:
            out.append(c)
    return "".join(out)


def _registered_id_fake(bare: str, mapping: Mapping, rule_id: str) -> str:
    """Stable fake for a bare document number, registered as its own
    forward/reverse pair so the SAME number found bare elsewhere (VIZ line,
    table cell, MRZ) maps to the SAME fake (Failure F2: '560683707' vs
    '5606837078' must not become two unrelated tokens)."""
    existing = mapping.forward.get(bare)
    if existing is not None:
        if len(existing) == len(bare) and existing.isalnum():
            return existing
        # The bare number was already claimed by another rule as an OPAQUE
        # token (e.g. a national-ID checksum rule coincidentally matching a
        # 9-digit passport number). That token must not be spliced into an
        # MRZ line — emit a shape fake WITHOUT re-registering (the existing
        # reverse entry stays authoritative for the bare form).
        return _fake_id_like(bare, mapping.salt)
    fake = _fake_id_like(bare, mapping.salt)
    mapping.record(bare, fake, rule_id, count=False)
    return fake


_PASSPORT_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{4,13}[A-Za-z0-9]")


def _fake_passport(original: str, mapping: Mapping) -> str:
    """`passport` spans include the trigger keyword ('Passport No. C03005988').
    Keep the keyword, fake only the number — per-char shape-preserving."""
    cands = [m for m in _PASSPORT_TOKEN_RE.finditer(original)
             if sum(ch.isdigit() for ch in m.group(0)) >= 2]
    if not cands:
        return original
    m = cands[-1]
    fake = _registered_id_fake(m.group(0), mapping, "passport")
    return original[:m.start()] + fake + original[m.end():]


# TD3/TD2 data line: number(9) chk nat(3) dob(6) chk sex expiry(6) chk [rest]
_MRZ_DATA_RE = re.compile(
    r"^([A-Z0-9<]{9})(\d)([A-Z<]{3})(\d{6})(\d)([MFX<])(\d{6})(\d)([A-Z0-9<]*)$")
_MRZ_NAME_RE = re.compile(r"^([A-Z][A-Z<][A-Z<]{3})([A-Z<]+)$")


def _mrz_yymmdd(s: str, *, dob: bool):
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    century = 1900 if (dob and yy > _dt.date.today().year % 100) else 2000
    try:
        return _dt.date(century + yy, mm, dd)
    except ValueError:
        return None


def _fake_mrz(original: str, mapping: Mapping) -> str:
    """Rebuild an MRZ line as a CONSISTENT fake: fake document number (same
    one the VIZ passport-number fake uses), DOB shifted by the mapping's
    constant date offset, expiry UNCHANGED (document-lifecycle date, handover
    decision L2c), nationality/sex verbatim — and ALL ICAO-9303 check digits
    recomputed so they VERIFY. The LLM's own MRZ math then works again
    instead of producing false forgery indications (F2). Name lines map the
    surname/givens onto the same fake entity as the VIZ text."""
    from engine.tools.doc_checks import mrz_check_digit  # pure helper

    lead = original[:len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    line = original.strip()

    m = _MRZ_DATA_RE.match(line)
    if m:
        number, _nchk, nat, dob_s, _dchk, sex, exp_s, exp_chk, rest = m.groups()
        bare = number.strip("<")
        fake_bare = _registered_id_fake(bare, mapping, "passport") if bare else ""
        fake_number = (fake_bare + "<" * 9)[:9]
        fake_nchk = str(mrz_check_digit(fake_number))
        dob = _mrz_yymmdd(dob_s, dob=True)
        if dob is None:
            fake_dob = _fake_id_like(dob_s, mapping.salt)  # unparseable → digits
        else:
            shifted = dob + _dt.timedelta(days=date_offset_days(mapping.salt))
            fake_dob = shifted.strftime("%y%m%d")
        fake_dchk = str(mrz_check_digit(fake_dob))
        # Also register the 10-char number+check form ('5606837078') so the
        # VIZ occurrence with check digit maps consistently.
        if bare and len(number.strip("<")) == len(fake_bare):
            ten_real = bare + _nchk
            ten_fake = fake_bare + fake_nchk
            if ten_real not in mapping.forward:
                mapping.record(ten_real, ten_fake, "passport", count=False)
        prefix = fake_number + fake_nchk + nat + fake_dob + fake_dchk \
            + sex + exp_s + exp_chk
        if len(line) == 44 and len(rest) == 16:
            personal, pchk, comp = rest[:14], rest[14], rest[15]
            fake_personal = _fake_id_like(personal, mapping.salt) \
                if personal.strip("<") else personal
            fake_pchk = str(mrz_check_digit(fake_personal)) \
                if pchk.isdigit() else pchk
            wo_comp = prefix + fake_personal + fake_pchk
            comp_src = wo_comp[0:10] + wo_comp[13:20] + wo_comp[21:43]
            fake_comp = str(mrz_check_digit(comp_src)) if comp.isdigit() else comp
            return lead + wo_comp + fake_comp + trail
        fake_rest = _fake_id_like(rest, mapping.salt) if rest.strip("<") else rest
        return lead + prefix + fake_rest + trail

    m = _MRZ_NAME_RE.match(line)
    if m and "<<" in m.group(2):
        doc_prefix, field_ = m.group(1), m.group(2)
        form = _mrz_name_form(line)
        if not form:
            return original
        ent = _entity_find(mapping, form)
        if ent is None:
            ent = _entity_create(mapping, form)
        else:
            _entity_learn(mapping, ent, form)
        _register_entity_variants(mapping, ent)
        giv_fakes = [f.upper() for g, f in zip(ent["givens"], ent["fake_givens"])
                     if g and len(g) > 1]
        fake_field = ent["fake_sur"].upper() + "<<" + "<".join(giv_fakes)
        fake_field = (fake_field + "<" * len(field_))[:len(field_)]
        return lead + doc_prefix + fake_field + trail

    return original  # unrecognised → opaque-token fallback


def _entity_fake_dob(original: str, mapping: Mapping) -> str | None:
    """dob/date spans keep their keyword prefix (same construction as
    `_fake_dob`), but the BARE date additionally gets its own forward/reverse
    pairs in every expected surface form. Without them the reply-side reverse
    only knows the FULL span string — and the model routinely reformats it
    ('geboren am **15.02.1947**': markdown bold splits the exact match; live
    chat db0ef544) so the fake date stayed visible to the user. With the bare
    pairs registered, the reverse pass restores the date itself regardless of
    the surrounding prose/markup — the same registration
    `seed_identity_from_mrz`/`seed_from_decision(mrz_dob)` always did."""
    m = _DATE_SEARCH_RE.search(original)
    if not m:
        return None  # no date found → shape/opaque fallback
    bare = m.group(0)
    fake_bare = _fake_date(bare, mapping.salt)
    if fake_bare == bare:
        return None
    d = _parse_date_surface(bare)
    if d is not None:
        for real in _dob_surface_forms(d):
            fk = _fake_date(real, mapping.salt)
            if fk == real or real in mapping.forward \
                    or fk in mapping.reverse:
                continue
            mapping.record(real, fk, "dob", count=False)
    return original[:m.start()] + fake_bare + original[m.end():]


_ENTITY_GENERATORS: dict[str, Callable[[str, "Mapping"], str | None]] = {
    "name": _entity_fake_name,
    "email": _entity_fake_email,
    "organisation": _entity_fake_organisation,   # M4/G2
    "passport": _fake_passport,
    "passport_ctx_loose": _fake_passport,
    "mrz": _fake_mrz,
    "dob": _entity_fake_dob,
    "date": _entity_fake_dob,
}


def _dob_surface_forms(d: _dt.date) -> list[str]:
    """Expected surface forms of a DOB in a KYC dossier (measured on the
    reference material): ISO, EU dot, US slash, textual EN month ('05 FEB
    1947' passport VIZ / '5 Feb 1947' OCR read). All parseable by
    _DATE_PATTERNS, so the fake side comes from _fake_date and stays
    consistent with scanner-minted fakes of the same date."""
    ab = _MONTHS_EN_AB[d.month - 1]
    forms = [
        f"{d.year:04d}-{d.month:02d}-{d.day:02d}",
        f"{d.day:02d}.{d.month:02d}.{d.year:04d}",
        f"{d.month:02d}/{d.day:02d}/{d.year:04d}",
    ]
    for mon in (ab.upper(), ab.title()):
        forms.append(f"{d.day:02d} {mon} {d.year:04d}")
        forms.append(f"{d.day} {mon} {d.year:04d}")
    seen: list[str] = []
    for f in forms:
        if f not in seen:
            seen.append(f)
    return seen


def seed_identity_from_mrz(mapping: Mapping, parsed: dict,
                           *, allow_new_entity: bool = True) -> dict:
    """L5b — seed the entity map from a STRUCTURED, checksum-plausible MRZ
    parse (engine.tools.doc_checks.parse_mrz over the _ocr_mrz_strip read of
    an attached document photo). The MRZ is the cleanest machine-readable
    identity source in a KYC dossier: seeding the name (entity + standard
    variants), the document number (bare + 10-char check-digit form, same
    registration _fake_mrz uses) and the DOB's expected surface forms BEFORE
    the turn's text scan makes every occurrence — incl. OCR garble caught by
    the fuzzy attach and forms the German NER misses ('Stark Bonnie',
    file-name forms) — map onto ONE fake identity from turn 1 instead of
    leaking raw or fragmenting into per-string fakes (handover L5b: the gap
    turns from leak into anchor). Per-field honesty gates below; the NAME
    additionally needs ≥2 verifying checksums (the name line has no checksum
    of its own — a photo whose data line is half-garbled tends to carry a
    garbled name too; measured on the reference set, where the 1-checksum
    photo read 'BONNTIMARTI' and would have poisoned the entity).
    `allow_new_entity=False` permits attaching/enriching an EXISTING entity
    but never creates one — the orchestrator passes it for a second read of
    an already-seeded document number (same doc ⇒ same person; a divergent
    name form there is OCR garble, not a new person).
    Returns {"name": bool, "passport": bool, "dob": bool}.
    """
    out = {"name": False, "passport": False, "dob": False}
    checks = parsed.get("checks") or {}
    n_verified = sum(1 for v in checks.values() if v)

    sur = (parsed.get("surname") or "").strip()
    giv = (parsed.get("givens") or "").strip()
    if sur and giv and n_verified >= 2:
        # Both name halves required: a lone surname seeds an ambiguous
        # entity (first-come fake for a whole family, handover Session-3
        # Nebenbefund) with near-zero variant value.
        form = " ".join(t.title() for t in f"{giv} {sur}".split())
        try:
            ent = _entity_find(mapping, form)
            if ent is None:
                if not allow_new_entity:
                    raise ValueError("re-read of a seeded document")
                ent = _entity_create(mapping, form)
            else:
                _entity_learn(mapping, ent, form)
            _register_entity_variants(mapping, ent)
            out["name"] = True
        except Exception:
            pass

    number = (parsed.get("document_number") or "").strip()
    if number and checks.get("document_number"):
        try:
            from engine.tools.doc_checks import mrz_check_digit
            fake = _registered_id_fake(number, mapping, "passport")
            if len(fake) == len(number):
                ten_real = number + str(mrz_check_digit((number + "<" * 9)[:9]))
                ten_fake = fake + str(mrz_check_digit((fake + "<" * 9)[:9]))
                if ten_real not in mapping.forward \
                        and ten_fake not in mapping.reverse:
                    mapping.record(ten_real, ten_fake, "passport", count=False)
            out["passport"] = True
        except Exception:
            pass

    dob = parsed.get("dob")
    if isinstance(dob, _dt.date) and checks.get("dob"):
        # Unreadable date field ⇒ checks["dob"] is None ⇒ no seed — the same
        # honesty invariant doc_checks applies (never guess from garble).
        for real in _dob_surface_forms(dob):
            fake = _fake_date(real, mapping.salt)
            if fake == real or real in mapping.forward \
                    or fake in mapping.reverse:
                continue
            mapping.record(real, fake, "dob", count=False)
            out["dob"] = True
    return out


def seed_from_decision(mapping: Mapping, rule_id: str, value: str) -> bool:
    """Decision-driven mint (GDPR_ALL_CHECKS_PRE_DIALOG_PLAN): the chat worker
    no longer re-detects — it mints exactly the values the pre-send dialog
    confirmed. This helper produces the SAME fakes the scanner/seed path would
    have produced for a finding of `rule_id` over `value` (same generators,
    same entity layer, same derived registrations), so tokens stay stable
    across the flow change and across turns of a reused mapping.

    `mrz_name`/`mrz_passport`/`mrz_dob` are the synthetic rule_ids the
    attachment-scan endpoint emits from a checksum-verified MRZ read — they
    reproduce `seed_identity_from_mrz`'s per-field registrations (passport:
    bare + 10-char check-digit form; dob: every expected surface form).
    Returns True when anything was minted/seeded."""
    value = (value or "").strip()
    if not value or value in mapping.reverse:
        return False  # empty, or already a fake in this mapping
    if rule_id == "mrz_name":
        try:
            ent = _entity_find(mapping, value)
            if ent is None:
                ent = _entity_create(mapping, value)
            else:
                _entity_learn(mapping, ent, value)
            _register_entity_variants(mapping, ent)
            return True
        except Exception:
            return False
    if rule_id == "mrz_passport":
        try:
            from engine.tools.doc_checks import mrz_check_digit
            fake = _registered_id_fake(value, mapping, "passport")
            if len(fake) == len(value):
                # Same double registration as seed_identity_from_mrz — VIZ
                # (bare) and MRZ (10-char check-digit form) must collapse
                # onto ONE fake (plan §Open risk: token stability).
                ten_real = value + str(mrz_check_digit((value + "<" * 9)[:9]))
                ten_fake = fake + str(mrz_check_digit((fake + "<" * 9)[:9]))
                if ten_real not in mapping.forward \
                        and ten_fake not in mapping.reverse:
                    mapping.record(ten_real, ten_fake, "passport", count=False)
            return True
        except Exception:
            return False
    if rule_id == "mrz_dob":
        d = _parse_date_surface(value)
        if d is None:
            return False
        seeded = False
        for real in _dob_surface_forms(d):
            fake = _fake_date(real, mapping.salt)
            if fake == real or real in mapping.forward \
                    or fake in mapping.reverse:
                continue
            mapping.record(real, fake, "dob", count=False)
            seeded = True
        return seeded
    # Scanner rules — entity seeding first (mirrors
    # _seed_entities_in_text_order), then the same generator dispatch
    # pseudonymize_text uses for a span of this rule.
    if rule_id in ("name", "mrz", "organisation"):
        try:
            if rule_id == "organisation":
                stem, _lf = _identity.org_structure(value)
                if not stem:
                    # M4 scanner-FP guard (mirror of pseudonymize_text): the
                    # org layer says "not a company name" → never fake it.
                    return False
                if _org_find(mapping, stem) is None:
                    _org_create(mapping, value)
                _register_org_variants(mapping, _org_find(mapping, stem))
            else:
                form = _mrz_name_form(value) if rule_id == "mrz" else value
                if form:
                    ent = _entity_find(mapping, form)
                    if ent is None:
                        ent = _entity_create(mapping, form)
                    else:
                        _entity_learn(mapping, ent, form)
                    _register_entity_variants(mapping, ent)
        except Exception:
            pass
    if value in mapping.forward:
        # The value was registered as a variant during entity seeding
        # (standard_variant_pairs, count=False) BEFORE reaching here. But this
        # IS the user-confirmed finding — promote it to a real find so the
        # report/turn-detail don't filter the actual decided value as a
        # derived variant (chat 80494e34). Idempotent.
        mapping.derived.discard(value)
        rid = mapping.categories.get(value, rule_id)
        mapping.finding_counts[rid] = mapping.finding_counts.get(rid, 0) + 1
        return True
    replacement = _build_replacement(value, rule_id, mapping)
    mapping.record(value, replacement, rule_id)
    return True


def values_same_subject(fp_value: str, candidate: str) -> bool:
    """Does `candidate` plausibly denote the SAME SUBJECT as the FP-marked
    `fp_value`? Used to suppress DERIVED mints once their parent is FP-marked:
    the turn-end recorder writes every registered variant ('Bonnie Stark',
    'STARK, BONNIE MARIE', the bare surname, dob surface forms, the passport
    check-digit twin) as its own ledger value — without this relation check
    those rows re-mint a fresh entity right after the purge and the fuzzy
    sweep re-fakes the FP'd value (found live). Conservative direction:
    over-matching keeps a variant in the CLEAR (the user judged the subject
    not-PII), never leaks an unrelated value."""
    f = (fp_value or "").strip()
    c = (candidate or "").strip()
    if not f or not c:
        return False
    _n = lambda s: " ".join(s.split()).lower()  # noqa: E731
    if _n(f) == _n(c):
        return True
    # Person: candidate attaches to the FP name's structure, or is a lone
    # token of it (the registered bare-surname variant).
    try:
        sur, givens = _identity.guess_structure(f)
        if sur:
            if _identity.entity_attach(c, sur, givens):
                return True
            if " " not in c.strip() and len(c) >= 4 \
                    and _n(c) in {sur} | set(givens):
                return True
    except Exception:
        pass
    # Organisation: same stem.
    try:
        stem_f, _ = _identity.org_structure(f)
        stem_c, _ = _identity.org_structure(c)
        if stem_f and stem_c and (stem_f == stem_c
                                  or set(stem_f) <= set(stem_c)
                                  or set(stem_c) <= set(stem_f)):
            return True
    except Exception:
        pass
    # Date: same calendar date in any surface form. dob spans carry a
    # keyword prefix ('geboren am 05.02.1947') — extract the date part on
    # BOTH sides so the bare surface-form ledger rows relate to the span.
    try:
        def _as_date(s):
            d = _parse_date_surface(s)
            if d is None:
                m = _DATE_SEARCH_RE.search(s)
                if m:
                    d = _parse_date_surface(m.group(0))
            return d
        d = _as_date(f)
        if d is not None and _as_date(c) == d:
            return True
    except Exception:
        pass
    # ID check-digit twin (bare vs 10-char form).
    if (c.startswith(f) or f.startswith(c)) and abs(len(c) - len(f)) == 1 \
            and min(len(c), len(f)) >= 5 and c[:5].isalnum():
        return True
    return False


def purge_value(mapping: Mapping, value: str) -> int:
    """False-positive enforcement: remove every mapping trace of `value` so a
    value the user marked "falsch erkannt" stays in the clear from now on —
    including on REUSED mappings that minted it on an earlier turn (the chat
    912d9199 failure: FP marks had no effect because the persisted mapping
    still carried the value and every sweep re-applied it).

    Drops: the exact forward/reverse pair (whitespace-collapsed compare), any
    person/org ENTITY the value attaches to plus all forward pairs rendering
    onto that entity's fake tokens (variants, swept garble forms, entity
    emails), every date surface form of the same calendar date, and the
    10-char check-digit twin of a bare document number. Returns entries
    removed."""
    norm = re.sub(r"\s+", " ", value or "").strip().lower()
    if not norm:
        return 0
    removed = 0
    # 1) Entity layer — collect the fake tokens of any entity this value
    # attaches to, then drop the entity itself.
    fake_markers: list[str] = []
    for eid in list(mapping.entities.keys()):
        ent = mapping.entities[eid]
        try:
            if _is_person_ent(ent):
                if _identity.entity_attach(value, ent["sur"], ent["givens"]):
                    fake_markers.append(ent["fake_sur"])
                    fake_markers.extend(f for f in ent["fake_givens"] if f)
                    del mapping.entities[eid]
            else:
                stem, _lf = _identity.org_structure(value)
                if stem and ent.get("stem") == list(stem):
                    fake_markers.extend(
                        f for f in ent.get("fake_stem", []) if f)
                    del mapping.entities[eid]
        except Exception:
            continue
    marker_res = [re.compile(r"(?<!\w)" + re.escape(m) + r"(?!\w)",
                             re.IGNORECASE) for m in fake_markers]
    # 2) Same calendar date in any surface form (an FP'd DOB was seeded in
    # 5+ formats); 3) 10-char check-digit twin of a bare document number.
    # dob spans carry a keyword prefix ('geboren am 05.02.1947') — extract
    # the date part like _fake_dob does, so the FP purge clears the bare
    # surface-form pairs too.
    _d_val = _parse_date_surface(value)
    if _d_val is None:
        _m_d = _DATE_SEARCH_RE.search(value or "")
        if _m_d:
            _d_val = _parse_date_surface(_m_d.group(0))
    twin = ""
    try:
        bare = value.strip()
        if bare and re.fullmatch(r"[A-Z0-9]{5,10}", bare):
            from engine.tools.doc_checks import mrz_check_digit
            twin = bare + str(mrz_check_digit((bare + "<" * 9)[:9]))
    except Exception:
        twin = ""
    for orig in list(mapping.forward.keys()):
        fake = mapping.forward[orig]
        onorm = re.sub(r"\s+", " ", orig).strip().lower()
        drop = (onorm == norm) or (twin and orig == twin)
        if not drop and _d_val is not None:
            try:
                drop = _parse_date_surface(orig) == _d_val
            except Exception:
                drop = False
        if not drop and marker_res:
            drop = any(r.search(fake or "") for r in marker_res)
        if drop:
            mapping.forward.pop(orig, None)
            if mapping.reverse.get(fake) == orig:
                mapping.reverse.pop(fake, None)
            mapping.categories.pop(orig, None)
            removed += 1
    return removed


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
    # L2 entity layer: entity_id → {sur, givens (real, lowercase),
    # fake_sur, fake_givens (display case)}. The BOOKKEEPING of which surface
    # variant belongs to which person; the forward/reverse string tables stay
    # the working memory (handover §7.9 — registered variants make the L3a
    # args-deanon and the web-egress gate entity-aware with zero code there).
    # Legacy mappings without the field deserialise to {}.
    entities: dict[str, dict] = field(default_factory=dict)
    # Originals registered as DERIVED entries (count=False): pre-registered
    # entity/org surface variants, the bare/10-char passport twin, extra DOB
    # surface forms. These are internal bookkeeping so the SAME subject stays
    # token-stable — they were never in the user's text and carry no meaning
    # for a human reading the privacy report. Tracked so the report + the
    # per-turn privacy detail can show only REAL findings (chat 80494e34: 34
    # of 49 rows were derived variants). Legacy mappings deserialise to set().
    derived: set = field(default_factory=set)

    def next_token(self, rule_id: str) -> str:
        kind = _rule_id_to_kind(rule_id)
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return f"<{kind}_{n}_{self.salt}>"

    def record(self, original: str, replacement: str, rule_id: str,
               count: bool = True) -> None:
        """count=False for derived entries (pre-registered entity variants,
        bare passport numbers) so audit counts keep reflecting what was
        actually FOUND, not what the entity layer prophylactically mapped."""
        self.forward[original] = replacement
        # Padding-collision determinism (v9.342.0, the L2 edge from the
        # v9.340 golden run): '5 FEB 1947' and '05 FEB 1947' render the SAME
        # fake once the constant offset makes the day two-digit — the reverse
        # can only restore ONE surface. Instead of last-write-wins, keep the
        # LONGER (padded, canonical) original whenever both colliding
        # originals parse to the same calendar date. Value identical either
        # way; this only makes the roundtrip padding deterministic. Non-date
        # collisions keep the existing last-write-wins behavior untouched.
        _prev = self.reverse.get(replacement)
        _keep_prev = False
        if (_prev is not None and _prev != original
                and len(_prev) > len(original)):
            try:
                _d_prev = _parse_date_surface(_prev)
                _keep_prev = (_d_prev is not None
                              and _d_prev == _parse_date_surface(original))
            except Exception:
                _keep_prev = False
        if not _keep_prev:
            self.reverse[replacement] = original
        if count:
            self.finding_counts[rule_id] = self.finding_counts.get(rule_id, 0) + 1
            self.derived.discard(original)  # a real find promotes a prior variant
        elif original not in self.categories:
            # Only mark as derived when this is the FIRST registration of the
            # value — a real find recorded earlier (count=True) must not be
            # demoted by a later prophylactic variant pass over the same value.
            self.derived.add(original)
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

    # L2: Entitäten in Text-Reihenfolge seeden (saubere Lesung vor Garble),
    # bevor der end-absteigende Splice-Pass rendert.
    _seed_entities_in_text_order(text, findings, mapping)

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
        # M4: der spaCy-ORG-Tagger wirft auch GEWÖHNLICHE Substantive als Firmen
        # aus (am echten Material gemessen: 'Trust' aus 'verwaltet den Trust',
        # 'Schwestern' aus 'sind Schwestern'). Sagt die Org-Entitäts-Schicht
        # "das ist kein Firmenname" (leerer Stamm), ist das ein Scanner-FALSCH-
        # TREFFER — dann darf der Wert GAR NICHT gefakt werden. Ohne diesen
        # Skip fiele er auf den alten String-Faker durch und würde als
        # 'Vandelay Corp' mitten in den Fließtext geschrieben (gemessen).
        if rule_id == "organisation" and not _identity.org_structure(original)[0]:
            continue
        # Stable within a mapping: same original → same token even if it
        # appears in multiple sources.
        replacement = mapping.forward.get(original)
        if replacement is None:
            replacement = _build_replacement(original, rule_id, mapping)
            mapping.record(original, replacement, rule_id)
        out = out[:s] + replacement + out[e:]

    return out


def _build_replacement(original: str, rule_id: str, mapping: Mapping) -> str:
    """Pick shape-fake vs opaque token based on rule_id. Entity-aware
    generators (L2: name/email/passport/mrz — they need the whole Mapping)
    run first; the plain salt-based generators are the fallback. A generator
    returning the input unchanged means 'could not fake this' → opaque token
    (never silently pass the original through)."""
    if rule_id in SHAPE_PRESERVING:
        egen = _ENTITY_GENERATORS.get(rule_id)
        if egen is not None:
            try:
                out = egen(original, mapping)
                if out and out != original:
                    return out
            except Exception:
                # Defensive: a shape-fake bug must never leak the original.
                pass
        gen = _SHAPE_GENERATORS.get(rule_id)
        if gen is not None:
            try:
                out = gen(original, mapping.salt)
                if out and out != original:
                    return out
            except Exception:
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
    # A purely-alphabetic key (a fake name token like 'Stark'/'Cameron') must
    # replace WORD-BOUNDED — a bare substring replace rewrites 'Cameronstrasse'
    # → 'Bonniestrasse' (9.383.6, the standalone-given regression). Keys with
    # any non-alnum char (opaque tokens '<NAME_1_xx>', multi-word/shape fakes
    # 'Maria Taylor', IBAN/date fakes with spaces/dots) keep the substring
    # replace — they can't collide mid-word and the tolerant token forms need
    # the loose match. Compiled once per key.
    _alpha_re = {
        k: re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)")
        for k in sorted_keys if k.isalpha()
    }
    for _ in range(max_passes):
        changed = False
        for token in sorted_keys:
            if token not in out:
                continue
            _pat = _alpha_re.get(token)
            if _pat is not None:
                out, count = _pat.subn(
                    lambda _m, _r=mapping.reverse[token]: _r, out)
            else:
                count = out.count(token)
                if count:
                    out = out.replace(token, mapping.reverse[token])
            if count:
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
# Known-values sweep — scanner-independent consistency pass
# ---------------------------------------------------------------------------


def apply_known_values(text: str, *, mapping: Mapping,
                       categories: tuple | None = ("name", "email",
                                                   "organisation",
                                                   "passport", "dob")
                       ) -> tuple[str, int]:
    """Replace REMAINING occurrences of already-mapped originals (default:
    entity-derived names/emails/ORGS plus the MRZ-seeded document numbers and
    DOB surface forms, L5b) with their registered fakes — word-bounded,
    longest-first.

    `organisation` gehört seit M4 in die Default-Kategorien und ist dort NICHT
    kosmetisch: die NER-Spanne ist bei Firmen fast nie der ganze Name (am
    echten Material gemessen — 'Wiener Privatbank SE' → Span 'Wiener
    Privatbank'; 'ABACO OVERSEAS HOLDINGS INC.' → zwei Spans; die Kurzform
    'WPB' erkennt der Scanner GAR NICHT, sie steht in `_ORG_LEGAL_ABBR`).
    Die registrierten Varianten der Org-Entität sind damit der einzige Weg,
    auf dem ALLCAPS-Registryformen, Slugs und Akronyme überhaupt gefasst
    werden. Complements the scanner pass in `_gdpr_anon_tool_text`
    and the chat worker's typed-text scan: the German spaCy NER often misses
    English names in mempalace drawers or web results, and a bare passport
    number / date has no context keyword for the regex rules — a registered
    value would otherwise reach the cloud raw (F5). Only values ≥4 chars;
    boundary check prevents mid-word hits ('Stark' never rewrites
    'Starkstrom'; `\\w` boundaries keep underscore-joined filename forms in
    paths intact). `categories=None` = ALL categories — the decision-driven
    apply path (GDPR_ALL_CHECKS_PRE_DIALOG_PLAN) rewrites every confirmed
    value, not just the entity-derived ones. Multi-word keys match
    whitespace-tolerantly (`\\s+` between tokens) so a dialog value that was
    whitespace-collapsed still hits its line-broken occurrence in a PDF
    extract. Returns (text, n_replaced)."""
    if not text or not mapping.forward:
        return text, 0
    keys = [k for k in mapping.forward
            if len(k) >= 4 and (categories is None
                                or mapping.categories.get(k) in categories)]
    if not keys:
        return text, 0
    n = 0
    for k in sorted(keys, key=len, reverse=True):
        fake = mapping.forward[k]
        if not fake:
            continue
        if " " in k:
            body = r"\s+".join(re.escape(p) for p in k.split())
        else:
            if k not in text:
                continue
            body = re.escape(k)
        pat = re.compile(r"(?<!\w)" + body + r"(?!\w)")
        text, c = pat.subn(lambda _m, _f=fake: _f, text)
        n += c
    return text, n


# Candidate windows for the fuzzy entity sweep: runs of 2-5 uppercase-initial
# tokens joined by space/comma/hyphen/'<' (MRZ). Underscore and slash are
# deliberately NOT separators — filename/path forms must stay verbatim (path
# integrity is L3a's job; a rewritten path in a tool result would break the
# read_document roundtrip). Lone tokens are excluded (single-surname
# ambiguity); trailing dots cover initials ('M.') and titles ('Mrs.').
_ENTITY_SWEEP_RUN_RE = re.compile(
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿß'\-]{0,24}\.?"
    r"(?:[ ,<\-]{1,4}[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿß'\-]{0,24}\.?){1,4}")

_ENTITY_SWEEP_TOKEN_RE = re.compile(
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿß'\-]{0,24}\.?")


def apply_entity_variants(text: str, *, mapping: Mapping) -> tuple[str, int]:
    """Fuzzy entity sweep (L5): find name-like token windows and replace the
    ones that plausibly belong to a KNOWN entity with the form-matching fake
    variant. This is the mop-up behind `apply_known_values` — exact
    registration can never enumerate OCR garble ('PSUSASTARK<<BONNT
    DCMARTE'), reordered forms ('Stark Bonnie M' — the NER word-order gap)
    or mixed-case file-name headings ('STARK, Bonnie M Mrs.'); the tested
    conservative `entity_attach` (≥2 tokens, distinct partners, anchored)
    decides, `render_variant` renders form-true, and nothing is ever LEARNED
    from a swept span (garble must not enrich the entity). Every replacement
    is registered as a real forward/reverse pair so the args-deanon (L3a)
    can translate a faked span back (path roundtrip) and the ledger rewrite
    covers the form on later turns. Returns (text, n_replaced)."""
    if not text or not mapping.entities:
        return text, 0
    replacements: list[tuple[int, int, str]] = []
    for run in _ENTITY_SWEEP_RUN_RE.finditer(text):
        toks = [(m.start() + run.start(), m.end() + run.start(), m.group(0))
                for m in _ENTITY_SWEEP_TOKEN_RE.finditer(run.group(0))]
        if len(toks) < 2:
            continue
        # Longest window first, leftmost first; accepted spans don't overlap.
        claimed: list[tuple[int, int]] = []
        for width in range(len(toks), 1, -1):
            for i in range(0, len(toks) - width + 1):
                s, e = toks[i][0], toks[i + width - 1][1]
                if any(s < ce and e > cs for cs, ce in claimed):
                    continue
                span = text[s:e]
                if span in mapping.forward or span in mapping.reverse:
                    continue  # exact sweep / prior mint owns this form
                ent = _entity_find(mapping, span)
                if ent is None:
                    continue
                fake = _identity.render_variant(
                    span, ent["sur"], ent["givens"],
                    ent["fake_sur"], ent["fake_givens"])
                if fake == span:
                    continue
                claimed.append((s, e))
                replacements.append((s, e, fake))
                if span not in mapping.forward and fake not in mapping.reverse:
                    mapping.record(span, fake, "name", count=False)
    n = len(replacements)
    for s, e, fake in sorted(replacements, key=lambda r: -r[0]):
        text = text[:s] + fake + text[e:]
    # Stage 3 — MRZ-garble lines: tesseract lowercase-bleed glues the
    # surname into non-window tokens ('peUEASTARK<<800"1' on the reference
    # set). Scoped HARD to lines containing '<<' (MRZ context — no German
    # compounds live there), ALLCAPS substring of entity tokens only.
    if "<<" in text:
        subs: list[tuple[str, str]] = []
        for ent in mapping.entities.values():
            if not _is_person_ent(ent):
                continue  # MRZ-Zeilen tragen Personen, keine Firmen
            for real, fake in zip([ent["sur"]] + ent["givens"],
                                  [ent["fake_sur"]] + ent["fake_givens"]):
                if real and len(real) >= 4:
                    subs.append((real.upper(), fake.upper()))
        out_lines = []
        for line in text.split("\n"):
            if "<<" in line:
                for real_u, fake_u in subs:
                    if real_u in line:
                        line = line.replace(real_u, fake_u)
                        n += 1
            out_lines.append(line)
        text = "\n".join(out_lines)
    return text, n


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
# Residual-fake linter — fail-loud reverse check (L6b)
# ---------------------------------------------------------------------------


def _parse_date_surface(raw: str) -> "_dt.date | None":
    """Parse one date surface form (any _DATE_PATTERNS shape) to a date.
    Mirrors the parse half of `_fake_date` without the reassembly."""
    raw = (raw or "").strip()
    for pat, fmt in _DATE_PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        if fmt in ("iso", "exif"):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif fmt == "dd_mon_yyyy":
            d, y = int(m.group(1)), int(m.group(4))
            mo, _cat = _parse_month_token(m.group(3))
            if mo is None:
                continue
        elif fmt in ("eu_dot", "eu_dash", "eu_dot_yy"):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if fmt == "eu_dot_yy":
                y = 2000 + y if y < 50 else 1900 + y
        elif fmt in ("us_slash", "us_slash_yy"):
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if fmt == "us_slash_yy":
                y = 2000 + y if y < 50 else 1900 + y
        else:  # pragma: no cover
            continue
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
            continue
        try:
            return _dt.date(y, mo, d)
        except ValueError:
            continue
    return None


def _date_alt_surface_forms(d: _dt.date) -> list[str]:
    """Surface forms a model plausibly REWRITES a fake date into (F6:
    'Reformatierung schlägt Reverse'): numeric styles plus textual months
    EN/DE, long and abbreviated, with and without day padding / ordinal
    dot. Case-insensitive matching happens at the caller."""
    forms = [
        f"{d.year:04d}-{d.month:02d}-{d.day:02d}",
        f"{d.day:02d}.{d.month:02d}.{d.year:04d}",
        f"{d.day}.{d.month}.{d.year:04d}",
        f"{d.day:02d}-{d.month:02d}-{d.year:04d}",
        f"{d.month:02d}/{d.day:02d}/{d.year:04d}",
        f"{d.month}/{d.day}/{d.year:04d}",
    ]
    months = {_MONTHS_EN[d.month - 1], _MONTHS_EN_AB[d.month - 1],
              _MONTHS_DE[d.month - 1], _MONTHS_DE_AB[d.month - 1]}
    for mon in months:
        for mon_s in (mon.title(), mon.upper()):
            forms += [
                f"{d.day} {mon_s} {d.year:04d}",
                f"{d.day:02d} {mon_s} {d.year:04d}",
                f"{d.day}. {mon_s} {d.year:04d}",
                f"{d.day:02d}. {mon_s} {d.year:04d}",
            ]
    forms.append(f"{_MONTHS_EN[d.month - 1].title()} {d.day}, {d.year:04d}")
    out: list[str] = []
    for f in forms:
        if f not in out:
            out.append(f)
    return out


# Bare `<KIND_N>` remnant — a token whose salt the model DROPPED entirely.
# Only flagged when KIND is a kind this mapping actually minted (counters),
# so generic placeholders in technical text ("<ITEM_1>") never fire.
_TOKEN_RE_SALTLESS = re.compile(r"<\s*([A-Za-z][A-Za-z0-9_]*)_(\d+)\s*>")

_LINT_MAX_FINDINGS = 50


def lint_residual_fakes(text: str, *, mapping: Mapping) -> list[dict]:
    """Fail-loud reverse linter (L6b, handover F6: 'der Report lügt leise').

    Scans FINAL user-facing text — the assistant reply AFTER
    `deanonymize_text`, or a written file's EXTRACTED text after
    `deanonymize_file` — for fake substance that survived the reverse pass:

      * ``token_remnant``     — `<KIND_N_SALT>`-shaped strings carrying this
        mapping's salt (mangled beyond the tolerant pass) or a minted KIND
        with the salt dropped. The user would see a placeholder.
      * ``exact_fake``        — a reverse-map key still present verbatim.
        Cannot happen right after `deanonymize_text` (fixed-point replace),
        but DOES happen on files: the per-run OOXML walkers miss a fake
        split across `<w:t>` runs, and xlsx formulas are skipped — linting
        the concatenated EXTRACTED text catches both.
      * ``reformatted_date``  — a fake date rewritten into another surface
        form ('17.02.1947' → '17. Februar 1947'): semantically the fake,
        no longer an exact reverse key.
      * ``name_genitive`` / ``name_initials`` — declined fake surname
        ('Mitchells' where surname-alone is not a registered variant) or
        the fake identity's initials pair ('S. M.').

    Returns up to 50 findings ``[{kind, value, reason}]`` — `value` is the
    FAKE substance found (safe to show: fakes only, never originals). An
    empty list means the reverse pass is clean. Purely read-only; the
    caller decides how loudly to warn (chat badge, synthetic row, audit).
    """
    if not text or not mapping.reverse:
        return []
    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, value: str, reason: str) -> bool:
        key = (value, reason)
        if key in seen or len(findings) >= _LINT_MAX_FINDINGS:
            return False
        seen.add(key)
        findings.append({"kind": kind, "value": value, "reason": reason})
        return True

    # 1) Token remnants. The tolerant deanonymize pass already restored
    #    everything restorable — whatever still matches here is unrecoverable
    #    (wrong N, foreign form) and would render as a placeholder.
    salt = mapping.salt.lower()
    for m in _TOKEN_RE_TOLERANT.finditer(text):
        if m.group(3).lower() == salt:
            _add(m.group(1).lower(), m.group(0), "token_remnant")
    minted_kinds = {k.upper() for k in mapping.counters}
    if minted_kinds:
        for m in _TOKEN_RE_SALTLESS.finditer(text):
            if m.group(1).upper() in minted_kinds:
                _add(m.group(1).lower(), m.group(0), "token_remnant")

    # 2) Exact fakes (word-bounded, ≥4 chars — same guards as
    #    apply_known_values so 'Starkstrom'-style mid-word hits never fire).
    #    Opaque-token keys are check 1's job — skipping them here keeps one
    #    surviving token from being reported twice.
    for fake in sorted(mapping.reverse.keys(), key=len, reverse=True):
        if len(fake) < 4 or fake not in text:
            continue
        if _TOKEN_RE_TOLERANT.fullmatch(fake):
            continue
        if re.search(r"(?<!\w)" + re.escape(fake) + r"(?!\w)", text):
            rule = mapping.categories.get(mapping.reverse[fake], "unknown")
            _add(rule, fake, "exact_fake")

    # 3) Reformatted fake dates. Only date-kinded entries; the fake's date
    #    part is parsed and its ALTERNATE surface forms searched — the exact
    #    form is check 2's job (and normally already restored).
    lower_text = text.lower()
    for fake, orig in mapping.reverse.items():
        if mapping.categories.get(orig) not in ("date", "dob"):
            continue
        dm = _DATE_SEARCH_RE.search(fake)
        if not dm:
            continue
        fake_date = _parse_date_surface(dm.group(0))
        if fake_date is None:
            continue
        exact = dm.group(0).lower()
        for form in _date_alt_surface_forms(fake_date):
            fl = form.lower()
            if fl == exact or fl not in lower_text:
                continue
            if re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)",
                         text, re.IGNORECASE):
                _add("date", form, "reformatted_date")
                break

    # 4) Fuzzy name residues from the entity layer (L2): genitive of the
    #    fake surname and the fake identity's initials pair. Both survive
    #    deanonymize_text because they are not exact reverse keys.
    for ent in mapping.entities.values():
        fsur = (ent.get("fake_sur") or "").strip()
        fgivens = [g for g in (ent.get("fake_givens") or []) if g]
        if fsur and len(fsur) >= 4 and f"{fsur}s" not in mapping.reverse:
            if re.search(r"(?<!\w)" + re.escape(fsur) + r"s(?!\w)", text):
                _add("name", f"{fsur}s", "name_genitive")
        if fsur and fgivens:
            pat = (re.escape(fgivens[0][0].upper()) + r"\.\s?"
                   + re.escape(fsur[0].upper()) + r"\.")
            m = re.search(r"(?<![\w.])" + pat + r"(?![\w.])", text)
            if m:
                _add("name", m.group(0), "name_initials")
        # Lone fake GIVEN name that survived (9.383.6): a table splits the
        # name into cells and the standalone given cell wasn't reversed —
        # e.g. a SHORT fake given ('Sam'/'Pat') deliberately not registered
        # as a variant to avoid word collisions. Flag it loud so the user
        # sees the miss. Only when the given is NOT already a reverse key
        # (a ≥4-char given IS registered now → reversed → not here) and is
        # word-bounded (never mid-word). Skips givens <3 chars (initials).
        for fg in fgivens:
            if len(fg) < 3 or fg in mapping.reverse:
                continue
            if re.search(r"(?<!\w)" + re.escape(fg) + r"(?!\w)", text):
                _add("name", fg, "name_given_lone")

    return findings


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
        "entities": m.entities,
        "derived": sorted(m.derived),  # set → list for JSON
    }


def _deserialize_mapping(d: dict) -> Mapping:
    m = Mapping(mapping_id=d["mapping_id"], salt=d["salt"])
    m.forward = dict(d.get("forward") or {})
    m.reverse = {v: k for k, v in m.forward.items()}
    m.counters = dict(d.get("counters") or {})
    m.sources = list(d.get("sources") or [])
    m.finding_counts = dict(d.get("finding_counts") or {})
    m.categories = dict(d.get("categories") or {})
    # L2 entity layer — legacy rows (pre-9.337.0) have no field → {}.
    m.entities = dict(d.get("entities") or {})
    # Derived-variant set (9.383.5) — legacy rows have no field → set().
    m.derived = set(d.get("derived") or ())
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
    "lint_residual_fakes",
    "apply_known_values",
    "date_offset_days",
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
