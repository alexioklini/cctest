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

    def test_steuerid_uses_opaque_token(self):
        # 36574261809 — Finanzamt example value; passes Steuer-ID checksum but
        # not PESEL (which is the only other 11-digit rule that would steal
        # the span — see overlap-suppression in `_pii_scan_text`).
        original = "Steuer-ID: 36574261809"
        findings = _scan(original)
        steuer = [f for f in findings if f["rule_id"] in ("de_steuerid", "tax_id_ctx")]
        self.assertTrue(steuer, f"expected Steuer-ID finding, got {findings}")
        mapping = ps.new_mapping()
        try:
            anonymised = ps.pseudonymize_text(original, findings, mapping=mapping)
            # Some token of the form <KIND_N_SALT> should appear.
            self.assertRegex(anonymised, r"<[A-Z_]+_\d+_[a-z0-9]+>")
            # Original digits gone.
            self.assertNotIn("36574261809", anonymised)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_token_format_structure(self):
        text = "Steuer-ID: 36574261809"
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
        original = "Steuer-ID: 36574261809"
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


if __name__ == "__main__":
    unittest.main()
