"""Unit tests for the pseudonymizer module.

Most tests run against *real* `_pii_scan_text` output from brain.py rather than
hand-crafted span dicts — the scanner contract is the load-bearing edge of
this feature, so we want a deviation in either side to fail loudly here.

Run with: python -m pytest tests/test_pseudonymizer.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

# Repo root on path so `import pseudonymizer` and `import brain` work whether
# pytest is invoked from `tests/` or the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402


def _scan(text: str) -> list[dict]:
    """Late-binding import — brain.py is heavy, only load when tests run."""
    import brain
    # Use a stable cfg so tests don't depend on config.json: enable scanning,
    # default category actions; this matches PII_DEFAULT_CATEGORY_ACTIONS.
    cfg = brain._get_gdpr_scanner_config()
    return brain._pii_scan_text(text, cfg=cfg)


class TestRoundtrip(unittest.TestCase):
    """Forward then reverse must restore the original verbatim."""

    def test_iban_roundtrip(self):
        original = "My account is DE89370400440532013000 — please credit it."
        findings = _scan(original)
        self.assertTrue(any(f["rule_id"] == "iban" for f in findings),
                        "scanner didn't find the IBAN we expected")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            self.assertNotIn("DE89370400440532013000", anonymised)
            restored, n = ps.deanonymize_text(anonymised, mapping=mapping)
            self.assertEqual(restored, original)
            self.assertEqual(n, 1)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_email_roundtrip(self):
        original = "Contact alice@example.com or bob@example.com for details."
        findings = _scan(original)
        emails = [f for f in findings if f["rule_id"] == "email"]
        # Note: contact category defaults to 'ignore' so emails may not appear.
        # Skip if the scanner config drops them — the roundtrip logic itself is
        # exercised by other rules. Email is the most likely to be 'ignore'.
        if not emails:
            self.skipTest("emails ignored under default cfg")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            self.assertNotIn("alice@example.com", anonymised)
            self.assertNotIn("bob@example.com", anonymised)
            restored, n = ps.deanonymize_text(anonymised, mapping=mapping)
            self.assertEqual(restored, original)
            self.assertGreaterEqual(n, 2)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_multiple_kinds_roundtrip(self):
        original = (
            "Hans Müller (Steuer-ID: 36574261809) lives at "
            "IBAN DE89370400440532013000, phone +49 30 12345678."
        )
        findings = _scan(original)
        self.assertGreater(len(findings), 1, "expected ≥2 findings")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            for f in findings:
                slice_ = original[f["start"]:f["end"]]
                self.assertNotIn(slice_, anonymised,
                                 f"original value for rule {f['rule_id']} "
                                 f"({slice_!r}) leaked into anonymised text")
            restored, _ = ps.deanonymize_text(anonymised, mapping=mapping)
            self.assertEqual(restored, original)
        finally:
            ps.close_mapping(mapping.mapping_id)


class TestShapeFakes(unittest.TestCase):
    """Shape-preserving fakes must be Luhn / mod-97 valid and have the right
    digit count + separator layout."""

    def test_iban_fake_is_mod97_valid(self):
        # Use a real-looking German test IBAN that passes the scanner.
        original = "DE89 3704 0044 0532 0130 00"
        findings = _scan(original)
        ibans = [f for f in findings if f["rule_id"] == "iban"]
        self.assertEqual(len(ibans), 1, "scanner should find exactly one IBAN")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            # The fake should preserve the separator layout from the original.
            fake = next(iter(mapping.reverse.keys()))
            self.assertEqual(len(fake), len(original),
                             "fake IBAN should match original character length")
            self.assertTrue(fake.startswith("DE"),
                            "fake should preserve country code")
            # Validate the fake passes mod-97 (canonicalise spaces first).
            cleaned = "".join(fake.split()).upper()
            self.assertTrue(_iban_mod97_valid(cleaned),
                            f"fake IBAN {fake!r} failed mod-97 check")
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_credit_card_fake_is_luhn_valid(self):
        original = "Card: 4111 1111 1111 1111"
        findings = _scan(original)
        ccs = [f for f in findings if f["rule_id"] == "credit_card"]
        self.assertEqual(len(ccs), 1, "scanner should find the test CC")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            fake = next(iter(mapping.reverse.keys()))
            self.assertEqual(len(fake), len(original.split("Card: ", 1)[1]))
            # Luhn check.
            digits = "".join(c for c in fake if c.isdigit())
            self.assertTrue(_luhn_valid(digits),
                            f"fake CC {fake!r} failed Luhn check")
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_shape_fake_is_deterministic(self):
        """Same original value → same fake within a mapping (so consistent
        across multiple appearances)."""
        text = (
            "IBAN: DE89370400440532013000 ... and again: DE89370400440532013000."
        )
        findings = _scan(text)
        mapping = ps.new_mapping()
        try:
            ps.pseudonymize_text(text, findings, mapping=mapping)
            fakes = list(mapping.reverse.keys())
            self.assertEqual(len(fakes), 1,
                             "two occurrences of same IBAN should map to one fake")
        finally:
            ps.close_mapping(mapping.mapping_id)


class TestOpaqueTokens(unittest.TestCase):
    """Opaque tokens for non-shape-preserving categories."""

    # 9.383.7: national-ID rules (Steuer-ID etc.) now get a SHAPE-TRUE,
    # checksum-valid fake instead of an opaque token — see
    # test_national_id_shape_valid. These opaque-token tests use a rule that
    # is STILL opaque (a JWT secret has no shape faker).
    _OPAQUE_TEXT = ('Authorization: Bearer '
                    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.'
                    'dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U')

    def test_jwt_uses_opaque_token(self):
        original = self._OPAQUE_TEXT
        findings = _scan(original)
        self.assertTrue([f for f in findings if f["rule_id"] == "jwt"],
                        f"expected jwt finding, got {findings}")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            self.assertRegex(anonymised, r"<[A-Z_]+_\d+_[a-z0-9]+>")
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_national_id_shape_valid(self):
        # The behaviour that replaced the opaque token: a Steuer-ID becomes a
        # shape-true, checksum-VALID fake (9.383.7).
        import engine.pii_ner as _pn
        _ok = {r["id"]: r.get("ok") for r in _pn._pii_rules()}["de_steuerid"]
        original = "Steuer-ID: 36574261809"
        findings = _scan(original)
        mapping = ps.new_mapping()
        try:
            anon = ps.pseudonymize_text(original, findings, mapping=mapping)
            self.assertNotIn("36574261809", anon)          # original gone
            self.assertNotIn("<", anon)                     # NOT an opaque token
            import re as _re
            fake = _re.search(r"\d{11}", anon).group(0)
            self.assertTrue(_ok(fake), f"fake {fake} fails Steuer-ID checksum")
            # Roundtrip restores the original.
            back, _ = ps.deanonymize_text(anon, mapping=mapping)
            self.assertEqual(back, original)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_token_format_structure(self):
        text = self._OPAQUE_TEXT
        findings = _scan(text)
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(text, findings, mapping=mapping)
            # Token must contain the mapping's salt.
            self.assertIn(mapping.salt, anonymised)
            # Strict format match.
            self.assertRegex(anonymised, ps._TOKEN_RE_STRICT)
        finally:
            ps.close_mapping(mapping.mapping_id)


class TestTolerantReverse(unittest.TestCase):
    """LLM may mangle the bracket spacing or case. Reverse must recover."""

    def _get_token_and_original(self):
        # A JWT still produces an opaque token (9.383.7: national IDs no
        # longer do — they get shape-true fakes).
        original = ('Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.'
                    'dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U')
        findings = _scan(original)
        mapping = ps.new_mapping()
        anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
        # Find the opaque token in the anonymised string.
        import re as _re
        m = _re.search(ps._TOKEN_RE_STRICT, anonymised)
        self.assertIsNotNone(m, f"no opaque token in {anonymised!r}")
        return mapping, m.group(0), mapping.reverse[m.group(0)]

    def test_recovers_whitespace_padding(self):
        mapping, token, original_value = self._get_token_and_original()
        try:
            mangled = token.replace("<", "< ").replace(">", " >")
            llm_reply = f"The user mentioned {mangled} earlier."
            restored, n = ps.deanonymize_text(llm_reply, mapping=mapping)
            self.assertIn(original_value, restored)
            self.assertEqual(n, 1)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_recovers_case_mangling(self):
        mapping, token, original_value = self._get_token_and_original()
        try:
            # LLM lowercased the whole token.
            mangled = token.lower()
            restored, n = ps.deanonymize_text(f"value {mangled}", mapping=mapping)
            self.assertIn(original_value, restored)
            self.assertEqual(n, 1)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_ignores_unknown_salt(self):
        """Token with a different salt (e.g. from a different mapping) must
        not be restored."""
        mapping, _, _ = self._get_token_and_original()
        try:
            foreign = "<PERSON_1_zzzz>"
            restored, n = ps.deanonymize_text(f"value {foreign}", mapping=mapping)
            self.assertEqual(restored, f"value {foreign}")
            self.assertEqual(n, 0)
        finally:
            ps.close_mapping(mapping.mapping_id)


class TestOverlapAndStability(unittest.TestCase):
    """Cross-cutting properties."""

    def test_no_self_pseudonymization(self):
        """If text already contains something that looks like a token (e.g.
        user pasted a previous turn's output back in), the scanner shouldn't
        flag the token, and we shouldn't re-pseudonymise it."""
        mapping = ps.new_mapping()
        # Synthetic prior-mapping token; scanner won't find it.
        fake_token = f"<PERSON_1_{mapping.salt}>"
        text = f"Earlier you said {fake_token} — but who is that really?"
        # No findings expected; pseudonymize_text is a no-op.
        out = ps.pseudonymize_text(text, [], mapping=mapping)
        self.assertEqual(out, text)
        ps.close_mapping(mapping.mapping_id)

    def test_stable_within_mapping(self):
        """Same original value across multiple `pseudonymize_text` calls on
        the same mapping → same replacement."""
        text1 = "Mein IBAN ist DE89370400440532013000."
        text2 = "Bitte überweise auf DE89370400440532013000."
        mapping = ps.new_mapping()
        try:
            a1 = ps.pseudonymize_text(text1, _scan(text1), mapping=mapping,
                                      source="chat_text")
            a2 = ps.pseudonymize_text(text2, _scan(text2), mapping=mapping,
                                      source="attachment:notes.txt")
            # The replacement value should be the same in both anonymised strings.
            # Find what each got replaced with.
            replacement = mapping.forward.get("DE89370400440532013000")
            self.assertIsNotNone(replacement)
            self.assertIn(replacement, a1)
            self.assertIn(replacement, a2)
            self.assertEqual(set(mapping.sources),
                             {"chat_text", "attachment:notes.txt"})
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_mapping_close_clears_registry(self):
        mapping = ps.new_mapping()
        mid = mapping.mapping_id
        self.assertIsNotNone(ps.get_mapping(mid))
        ps.close_mapping(mid)
        self.assertIsNone(ps.get_mapping(mid))

    def test_empty_findings_is_noop(self):
        text = "Just some prose with no PII."
        mapping = ps.new_mapping()
        out = ps.pseudonymize_text(text, [], mapping=mapping)
        self.assertEqual(out, text)
        self.assertEqual(mapping.forward, {})
        ps.close_mapping(mapping.mapping_id)

    def test_finding_counts_recorded(self):
        text = "IBAN DE89370400440532013000 and Steuer-ID: 36574261809."
        findings = _scan(text)
        mapping = ps.new_mapping()
        try:
            ps.pseudonymize_text(text, findings, mapping=mapping)
            # Total entries should equal unique values found.
            self.assertEqual(sum(mapping.finding_counts.values()), len(findings))
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_second_pass_does_not_re_anonymise_known_fake(self):
        """Regression: scanning an already-anonymised buffer (e.g. the history
        pass over `session.messages` whose user msg was anonymised seconds ago
        in the same turn) must NOT mint a fresh fake for the synthetic IBAN
        — that would build a chain real → fake1 → fake2 that deanonymise
        can't always collapse in one Phase-1 sweep (sort order dependent).
        Bug observed in session 48d97d70: a single-turn anonymise+history pass
        produced a 2-hop mapping and left the synthetic IBAN visible in the
        final reply."""
        text = "Mail mich an alice@example.com zur IBAN DE89370400440532013000"
        mapping = ps.new_mapping()
        try:
            # Pass 1: scan + anonymise the fresh user input.
            f1 = _scan(text)
            anon1 = ps.pseudonymize_text(text, f1, mapping=mapping,
                                         source="chat_text")
            self.assertEqual(len(mapping.forward), 2,
                             "first pass should mint exactly real-IBAN + email")
            # Pass 2: history walker scans the same buffer again. The synthetic
            # IBAN is mod-97-valid so the scanner re-matches it.
            f2 = _scan(anon1)
            anon2 = ps.pseudonymize_text(anon1, f2, mapping=mapping,
                                         source="history")
            self.assertEqual(anon1, anon2,
                             "history pass must NOT mutate already-anonymised text")
            self.assertEqual(len(mapping.forward), 2,
                             "no new entries should be minted on second pass")
            # Round-trip restores the real values.
            restored, n = ps.deanonymize_text(anon2, mapping=mapping)
            self.assertIn("DE89370400440532013000", restored)
            self.assertIn("alice@example.com", restored)
            self.assertNotIn(mapping.forward["DE89370400440532013000"], restored)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_find_restored_spans_locates_originals_with_categories(self):
        """The UI highlight pipeline needs (start, end, original, fake,
        category) for every restored value in the final reply. Categories
        come from per-entry `Mapping.categories` populated on record(); a
        legacy mapping deserialised without that field still produces spans
        (with category='unknown'), so the renderer never crashes on old data.
        """
        text = "Mail an alice@example.com mit IBAN DE89370400440532013000."
        mapping = ps.new_mapping()
        try:
            f = _scan(text)
            ps.pseudonymize_text(text, f, mapping=mapping)
            spans = ps.find_restored_spans(text, mapping=mapping)
            self.assertEqual(len(spans), 2)
            by_orig = {s["original"]: s for s in spans}
            self.assertIn("alice@example.com", by_orig)
            self.assertEqual(by_orig["alice@example.com"]["category"], "email")
            self.assertIn("DE89370400440532013000", by_orig)
            self.assertEqual(by_orig["DE89370400440532013000"]["category"], "iban")
            # Offsets are correct against the original text.
            for s in spans:
                self.assertEqual(text[s["start"]:s["end"]], s["original"])
            # Legacy fallback: a mapping without categories produces spans
            # tagged 'unknown' rather than raising.
            mapping.categories = {}
            spans2 = ps.find_restored_spans(text, mapping=mapping)
            self.assertTrue(all(s["category"] == "unknown" for s in spans2))
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_deanonymise_collapses_legacy_chain(self):
        """Safety net: if a chained mapping somehow exists (legacy data, or a
        future code path that bypasses the pseudonymize_text skip-known-fake
        guard), deanonymise iterates Phase 1 to fixed point and collapses
        real → fake1 → fake2 in one call."""
        mapping = ps.new_mapping()
        try:
            real = "DE89370400440532013000"
            fake1 = "DE06225596675938608799"   # synthetic mod-97-valid
            fake2 = "DE46251298961721045062"   # second synthetic mod-97-valid
            # Hand-craft the broken chain (would normally be prevented by the
            # skip-known-fake guard, but we test deanonymise's resilience).
            mapping.record(real, fake1, "iban")
            mapping.record(fake1, fake2, "iban")
            text = f"Bitte überweise auf {fake2}."
            restored, n = ps.deanonymize_text(text, mapping=mapping)
            self.assertIn(real, restored)
            self.assertNotIn(fake1, restored)
            self.assertNotIn(fake2, restored)
            # Each hop counts as one restore.
            self.assertGreaterEqual(n, 2)
        finally:
            ps.close_mapping(mapping.mapping_id)


# ---------------------------------------------------------------------------
# Helpers used by tests
# ---------------------------------------------------------------------------


def _luhn_valid(digits: str) -> bool:
    total, alt = 0, False
    for c in reversed(digits):
        n = int(c)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def _iban_mod97_valid(iban: str) -> bool:
    if not 15 <= len(iban) <= 34:
        return False
    rearr = iban[4:] + iban[:4]
    num = ""
    for c in rearr:
        if "A" <= c <= "Z":
            num += str(ord(c) - 55)
        elif c.isdigit():
            num += c
        else:
            return False
    rem = 0
    for d in num:
        rem = (rem * 10 + int(d)) % 97
    return rem == 1


class TestRegionPreserving(unittest.TestCase):
    """9.384.1 — a faked phone keeps its E.164 country code (+49→+49, not
    +99) and a known city is replaced by one from the SAME country (Köln→DE,
    Wien→AT). Analysetauglich statt region-blind."""

    _SALT = "abcd"

    def test_phone_keeps_country_code(self):
        for num, cc in [("+49 171 2345678", "+49"), ("+43 1 5350000", "+43"),
                        ("+1 415 555 0123", "+1"), ("+44 20 7946 0958", "+44")]:
            fake = ps._fake_phone(num, self._SALT)
            self.assertTrue(fake.lstrip().startswith(cc),
                            f"{num} → {fake} lost country code {cc}")
            self.assertNotIn("+99", fake)
            # national part actually changed
            self.assertNotEqual(ps._digits_only(fake), ps._digits_only(num))

    def test_phone_national_keeps_trunk(self):
        for num in ("030 12345678", "0171/2345678"):
            fake = ps._fake_phone(num, self._SALT)
            self.assertTrue(fake.lstrip().startswith("0"), fake)
            self.assertNotEqual(ps._digits_only(fake), ps._digits_only(num))

    def test_city_stays_in_country(self):
        from engine.geo_regions import country_of_city
        for city in ("Köln", "Wien", "Zürich", "Paris", "Roma", "London"):
            fake = ps._fake_city_regional(city, self._SALT)
            self.assertNotEqual(fake.lower(), city.lower())
            self.assertEqual(country_of_city(fake), country_of_city(city),
                             f"{city} → {fake} left its country")

    def test_unknown_city_falls_back(self):
        # Not in the geo map → neutral pool, still a non-empty different name.
        fake = ps._fake_city_regional("Kleinkleckersdorf", self._SALT)
        self.assertTrue(fake and fake != "Kleinkleckersdorf")

    def test_no_style_mix_in_pool(self):
        # The canonical IT pool has 'Rom' (not both Rom+Roma) so a substitution
        # can't flip spelling style.
        from engine.geo_regions import COUNTRY_TO_CITIES
        it = COUNTRY_TO_CITIES["IT"]
        self.assertIn("Rom", it)
        self.assertNotIn("Roma", it)


if __name__ == "__main__":
    unittest.main()
