"""GDPR_ALL_CHECKS_PRE_DIALOG_PLAN — decision-driven anonymisation.

Alle PII-Erkennung läuft EINMAL, vor dem Pre-Send-Dialog (Scan-Endpoint inkl.
MRZ-Pass); der Worker WENDET das bestätigte Decision-Set AN — er re-detektiert
nie. Diese Suite pinnt die drei neuen Mechaniken:

  1. `pseudonymizer.seed_from_decision` mintet DIESELBEN Fakes wie der alte
     Scan-/Seed-Pfad (Token-Stabilität — Plan §Open risk: mrz_passport muss
     bare + 10er-Prüfziffern-Form registrieren, mrz_dob alle Oberflächenformen).
  2. `pseudonymizer.purge_value` setzt False-Positive-Marks retroaktiv durch
     (der 912d9199-Fehler: FP markiert, trotzdem anonymisiert — weil das
     wiederverwendete Mapping den Wert noch trug).
  3. `brain._gdpr_anon_tool_text` ist APPLY-ONLY: bekannte Werte werden
     ersetzt, unbekannte PII bleibt roh und das Mapping wächst nicht durch
     Frisch-Erkennung (Plan-Verifikation #4).

Run: python3 -m unittest tests.test_gdpr_decision_driven -v
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as ps  # noqa: E402
from engine.context import get_request_context, request_context  # noqa: E402
from engine.tools.doc_checks import mrz_check_digit, parse_mrz  # noqa: E402

from tests.test_mrz_entity_seed import GOLD_L1, GOLD_L2  # noqa: E402


def _gold_parsed():
    parsed = parse_mrz(GOLD_L1 + "\n" + GOLD_L2)
    assert parsed is not None
    return parsed


class _MappingTestBase(unittest.TestCase):
    def setUp(self):
        self._mappings = []

    def tearDown(self):
        for m in self._mappings:
            ps.close_mapping(m.mapping_id)

    def _new(self):
        m = ps.new_mapping()
        self._mappings.append(m)
        return m


class TestSeedFromDecisionTokenStability(_MappingTestBase):
    """Fakes aus dem Decision-Pfad == Fakes aus dem alten Seed-Pfad."""

    def test_mrz_passport_matches_seed_registrations(self):
        parsed = _gold_parsed()
        num = parsed["document_number"]
        # Alter Pfad: seed_identity_from_mrz auf einem Mapping.
        m_old = self._new()
        ps.seed_identity_from_mrz(m_old, parsed)
        # Neuer Pfad: seed_from_decision auf einem Mapping mit DEMSELBEN Salt.
        m_new = ps.Mapping(mapping_id="t-new", salt=m_old.salt)
        self.assertTrue(ps.seed_from_decision(m_new, "mrz_passport", num))
        # Bare Form: identischer Fake (salt-deterministisch).
        self.assertEqual(m_old.forward[num], m_new.forward[num])
        # 10er-Prüfziffern-Form ist mitregistriert und identisch — sonst
        # fragmentieren VIZ- und MRZ-Vorkommen in zwei Fakes (Plan §Open risk).
        ten = num + str(mrz_check_digit((num + "<" * 9)[:9]))
        self.assertIn(ten, m_old.forward)
        self.assertIn(ten, m_new.forward)
        self.assertEqual(m_old.forward[ten], m_new.forward[ten])

    def test_mrz_dob_seeds_every_surface_form(self):
        parsed = _gold_parsed()
        dob = parsed["dob"]
        self.assertIsInstance(dob, dt.date)
        m_old = self._new()
        ps.seed_identity_from_mrz(m_old, parsed)
        m_new = ps.Mapping(mapping_id="t-new", salt=m_old.salt)
        self.assertTrue(
            ps.seed_from_decision(m_new, "mrz_dob", dob.isoformat()))
        old_dob_keys = {k for k, r in m_old.categories.items() if r == "dob"}
        new_dob_keys = {k for k, r in m_new.categories.items() if r == "dob"}
        self.assertEqual(old_dob_keys, new_dob_keys)
        for k in old_dob_keys:
            self.assertEqual(m_old.forward[k], m_new.forward[k])

    def test_mrz_name_creates_entity_with_variants(self):
        m = self._new()
        self.assertTrue(
            ps.seed_from_decision(m, "mrz_name", "Bonnie Marie Stark"))
        self.assertEqual(len(m.entities), 1)
        # Standard-Varianten sind echte forward-Paare (§7.9-Invariante).
        self.assertTrue(any("stark" in k.lower() for k in m.forward))

    def test_generic_rule_uses_build_replacement(self):
        # Ein Nicht-MRZ-Wert (z. B. IBAN) bekommt denselben Shape-Fake, den
        # der Scan-Splice über _build_replacement gemintet hätte.
        m = self._new()
        iban = "DE89370400440532013000"
        self.assertTrue(ps.seed_from_decision(m, "iban", iban))
        fake = m.forward[iban]
        self.assertNotEqual(fake, iban)
        expected = ps._build_replacement(
            iban, "iban", ps.Mapping(mapping_id="x", salt=m.salt))
        self.assertEqual(fake, expected)

    def test_organisation_without_stem_never_mints(self):
        # M4-Scanner-FP-Guard gespiegelt: die Org-Schicht sagt "kein
        # Firmenname" → seed_from_decision fasst den Wert nicht an.
        m = self._new()
        self.assertFalse(ps.seed_from_decision(m, "organisation", "Trust"))
        self.assertNotIn("Trust", m.forward)

    def test_fake_value_is_never_reminted(self):
        m = self._new()
        ps.seed_from_decision(m, "iban", "DE89370400440532013000")
        fake = m.forward["DE89370400440532013000"]
        # Der Fake selbst (steht in reverse) darf nie erneut gemintet werden
        # — sonst entsteht die real→fake1→fake2-Kette.
        self.assertFalse(ps.seed_from_decision(m, "iban", fake))


class TestPurgeValue(_MappingTestBase):
    """FP-Enforcement: purge_value entfernt JEDE Mapping-Spur des Werts."""

    def test_purges_exact_pair(self):
        m = self._new()
        ps.seed_from_decision(m, "uk_nhs", "943 476 5919")
        self.assertIn("943 476 5919", m.forward)
        self.assertGreater(ps.purge_value(m, "943 476 5919"), 0)
        self.assertNotIn("943 476 5919", m.forward)
        self.assertEqual(m.reverse, {})

    def test_purges_entity_and_all_variants(self):
        m = self._new()
        ps.seed_from_decision(m, "mrz_name", "Bonnie Marie Stark")
        self.assertEqual(len(m.entities), 1)
        self.assertTrue(m.forward)
        ps.purge_value(m, "Bonnie Marie Stark")
        self.assertEqual(len(m.entities), 0)
        # Keine Variante des Namens überlebt im forward-Table.
        self.assertFalse(
            [k for k in m.forward if "stark" in k.lower()],
            f"Entity-Varianten überlebten den Purge: {list(m.forward)}")

    def test_purges_all_date_surface_forms(self):
        m = self._new()
        ps.seed_from_decision(m, "mrz_dob", "1947-02-05")
        self.assertTrue(m.forward)
        ps.purge_value(m, "1947-02-05")
        self.assertEqual(
            m.forward, {},
            "Datums-Oberflächenformen überlebten den FP-Purge")

    def test_purges_ten_char_passport_twin(self):
        m = self._new()
        ps.seed_from_decision(m, "mrz_passport", "560683707")
        ten = "560683707" + str(mrz_check_digit(("560683707" + "<" * 9)[:9]))
        self.assertIn(ten, m.forward)
        ps.purge_value(m, "560683707")
        self.assertNotIn("560683707", m.forward)
        self.assertNotIn(ten, m.forward)

    def test_other_entity_survives(self):
        m = self._new()
        ps.seed_from_decision(m, "mrz_name", "Bonnie Marie Stark")
        ps.seed_from_decision(m, "name", "Anna Weber")
        ps.purge_value(m, "Bonnie Marie Stark")
        self.assertEqual(len(m.entities), 1)
        self.assertIn("Anna Weber", m.forward)


class TestApplyKnownValuesAllCategories(_MappingTestBase):
    def test_categories_none_covers_non_entity_rules(self):
        m = self._new()
        ps.seed_from_decision(m, "uk_nhs", "9434765919")
        fake = m.forward["9434765919"]
        out, n = ps.apply_known_values(
            "NHS: 9434765919 bitte prüfen", mapping=m, categories=None)
        self.assertEqual(n, 1)
        self.assertIn(fake, out)
        self.assertNotIn("9434765919", out)
        # Default-Kategorien decken uk_nhs weiterhin NICHT (unverändert).
        out2, n2 = ps.apply_known_values(
            "NHS: 9434765919 bitte prüfen", mapping=m)
        self.assertEqual(n2, 0)

    def test_multiword_key_matches_across_linebreak(self):
        m = self._new()
        m.record("Bonnie Marie Stark", "Erika Muster", "name")
        out, n = ps.apply_known_values(
            "Inhaberin: Bonnie Marie\nStark (lt. Akte)",
            mapping=m, categories=None)
        self.assertEqual(n, 1)
        self.assertIn("Erika Muster", out)

    def test_word_boundary_still_holds(self):
        m = self._new()
        m.record("Stark", "Muster", "name")
        out, n = ps.apply_known_values(
            "Starkstrom bleibt heil", mapping=m, categories=None)
        self.assertEqual(n, 0)
        self.assertIn("Starkstrom", out)


class TestAnonToolTextApplyOnly(_MappingTestBase):
    """brain._gdpr_anon_tool_text: apply-only — kein Frisch-Scan."""

    def _run(self, text, mapping):
        with request_context():
            get_request_context()._gdpr_mapping_id = mapping.mapping_id
            return brain._gdpr_anon_tool_text(text, "read_document:test")

    def test_applies_known_values(self):
        m = self._new()
        ps.seed_from_decision(m, "uk_nhs", "9434765919")
        fake = m.forward["9434765919"]
        out = self._run("Patient NHS 9434765919 im Dokument", m)
        self.assertIn(fake, out)
        self.assertNotIn("9434765919", out)

    def test_unknown_pii_stays_raw_and_mapping_stable(self):
        # Plan-Verifikation #4: ein Re-Read ohne unbekannte Garble-Varianten
        # fügt dem Mapping NICHTS hinzu — und nie durch Frisch-Erkennung.
        m = self._new()
        ps.seed_from_decision(m, "uk_nhs", "9434765919")
        before = len(m.forward)
        text = ("Undecided: Maria Schmidt, IBAN DE89370400440532013000, "
                "kein bekannter Wert enthalten")
        out = self._run(text, m)
        self.assertEqual(out, text)  # unbekannte PII bleibt ROH (by design)
        self.assertEqual(len(m.forward), before)

    def test_fp_purged_value_stays_clear(self):
        # Der 912d9199-Kernfall am Tool-Seam: nach dem Purge wird der Wert
        # nicht mehr ersetzt.
        m = self._new()
        ps.seed_from_decision(m, "uk_nhs", "9434765919")
        ps.purge_value(m, "9434765919")
        out = self._run("NHS 9434765919", m)
        self.assertEqual(out, "NHS 9434765919")


class TestValuesSameSubject(unittest.TestCase):
    """FP-Propagation auf abgeleitete Werte — live gefundene Regression: die
    Turn-End-Zeilen der ENTITÄTS-VARIANTEN ('Bonnie Stark', 'STARK', DOB-
    Formen, Prüfziffern-Zwilling) sind eigene Ledger-Werte mit anonymise-
    Status und minteten nach dem Purge eine FRISCHE Entität, deren Fuzzy-
    Sweep den FP-Wert wieder fakte. values_same_subject erkennt die
    Zugehörigkeit; der Worker filtert damit den Mint-Satz."""

    def test_name_variants_relate(self):
        fp = "Bonnie Marie Stark"
        for cand in ("Bonnie Stark", "STARK, BONNIE MARIE", "Stark",
                     "Bonnie M Stark", "bonnie marie stark"):
            self.assertTrue(ps.values_same_subject(fp, cand), cand)

    def test_unrelated_name_does_not_relate(self):
        fp = "Bonnie Marie Stark"
        for cand in ("Anna Weber", "Thomas Weber", "Starkstrom GmbH"):
            self.assertFalse(ps.values_same_subject(fp, cand), cand)

    def test_date_surface_forms_relate(self):
        self.assertTrue(ps.values_same_subject("1947-02-05", "05.02.1947"))
        self.assertTrue(ps.values_same_subject("1947-02-05", "05 FEB 1947"))
        self.assertFalse(ps.values_same_subject("1947-02-05", "06.02.1947"))

    def test_check_digit_twin_relates(self):
        ten = "560683707" + str(
            mrz_check_digit(("560683707" + "<" * 9)[:9]))
        self.assertTrue(ps.values_same_subject("560683707", ten))
        self.assertFalse(ps.values_same_subject("560683707", "123456789"))

    def test_fp_survives_variant_remint_attempt(self):
        # Ende-zu-Ende auf Mapping-Ebene: Purge + Variante-nicht-minten ⇒
        # der Fuzzy-Sweep lässt den FP-Namen in Ruhe.
        m = ps.new_mapping()
        try:
            ps.seed_from_decision(m, "mrz_name", "Bonnie Marie Stark")
            ps.purge_value(m, "Bonnie Marie Stark")
            # Der Worker filtert 'Bonnie Stark' (Varianten-Ledger-Zeile) via
            # values_same_subject — würde er minten, fakte der Sweep wieder:
            self.assertTrue(
                ps.values_same_subject("Bonnie Marie Stark", "Bonnie Stark"))
            out, n = ps.apply_entity_variants(
                "Brief an Bonnie Marie Stark", mapping=m)
            self.assertEqual(n, 0)
            self.assertIn("Bonnie Marie Stark", out)
        finally:
            ps.close_mapping(m.mapping_id)


class TestMrzFindingsFromParse(unittest.TestCase):
    """brain._mrz_findings_from_parse — Ehrlichkeits-Gates wie der Seed."""

    def test_gold_parse_yields_three_findings(self):
        f = brain._mrz_findings_from_parse(_gold_parsed())
        by_rule = {x["rule_id"]: x for x in f}
        self.assertEqual(
            set(by_rule), {"mrz_name", "mrz_passport", "mrz_dob"})
        self.assertEqual(by_rule["mrz_name"]["value"], "Bonnie Marie Stark")
        self.assertEqual(by_rule["mrz_passport"]["value"], "560683707")
        self.assertEqual(by_rule["mrz_dob"]["value"], "1947-02-05")
        for x in f:
            self.assertGreaterEqual(x["confidence"], 0.9)
            self.assertTrue(x["label"])

    def test_no_verified_checksum_yields_nothing(self):
        parsed = dict(_gold_parsed())
        parsed["checks"] = {"document_number": False, "dob": None,
                            "expiry": False}
        self.assertEqual(brain._mrz_findings_from_parse(parsed), [])

    def test_name_needs_two_checksums(self):
        # Die Namenszeile hat keine eigene Prüfziffer — 1 verifizierende
        # Checksumme reicht für Nummer/DOB, aber NICHT für den Namen.
        parsed = dict(_gold_parsed())
        parsed["checks"] = {"document_number": True, "dob": False,
                            "expiry": False, "composite": False}
        rules = {x["rule_id"] for x in brain._mrz_findings_from_parse(parsed)}
        self.assertIn("mrz_passport", rules)
        self.assertNotIn("mrz_name", rules)
        self.assertNotIn("mrz_dob", rules)

    def test_empty_parse(self):
        self.assertEqual(brain._mrz_findings_from_parse(None), [])
        self.assertEqual(brain._mrz_findings_from_parse({}), [])

    def test_rules_have_labels_and_personal_category(self):
        from engine.pii_ner import PII_RULE_CATEGORIES, PII_RULE_LABELS
        for rid in ("mrz_name", "mrz_passport", "mrz_dob"):
            self.assertIn(rid, PII_RULE_LABELS)
            # personal (warn) — contact/ignore würde die Findings aus dem
            # Dialog fernhalten.
            self.assertEqual(PII_RULE_CATEGORIES[rid], "personal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
