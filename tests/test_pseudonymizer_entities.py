"""L2 — Entitäts-konsistente Pseudonymisierung (PII_ANALYSIS_PARITY_HANDOVER).

Deckt die Verifikationsliste aus dem Handover §L2 ab:
  - F1: alle Oberflächenformen derselben Person mappen auf EINE Fake-Entität,
    jeweils in der FORMGLEICHEN Variante (Reihenfolge, Initialen, MRZ,
    E-Mail-Localpart, Case, OCR-Garble).
  - F2: die Fake-MRZ passiert `mrz_verify`-Mathematik (parse_mrz) mit
    durchgehend GÜLTIGEN ICAO-9303-Prüfziffern; Golden-MRZs beider echter
    Pässe aus dem Referenzmaterial.
  - L2c: konstanter Session-Offset — Deltas ("10 Jahre − 1 Tag", +9 Tage
    Renewal-Gap) bleiben EXAKT erhalten; Dokument-Lebenszyklus (Expiry in
    der MRZ) bleibt unverändert.
  - §7.9: Varianten sind echte forward/reverse-Einträge (L3a-Args-Deanon und
    Web-Egress-Gate arbeiten auf diesen Tabellen).

Run: python3 -m pytest tests/test_pseudonymizer_entities.py -v
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402
from engine.tools.doc_checks import mrz_check_digit, parse_mrz  # noqa: E402


def _anon(mapping, text, rule="name"):
    """Simulierter Scanner-Fund über den ganzen String (die Scanner-Kontrakte
    selbst testet test_pii_ner / test_pseudonymizer)."""
    findings = [{"rule_id": rule, "label": "t", "start": 0, "end": len(text)}]
    return ps.pseudonymize_text(text, findings, mapping=mapping)


def _td3_line2(num, nat, dob, sex, exp, pers="<" * 14):
    l = (num + str(mrz_check_digit(num)) + nat
         + dob + str(mrz_check_digit(dob)) + sex
         + exp + str(mrz_check_digit(exp)) + pers
         + (str(mrz_check_digit(pers)) if pers.strip("<") else "0"))
    comp = l[0:10] + l[13:20] + l[21:43]
    return l + str(mrz_check_digit(comp))


class TestF1EntityJoin(unittest.TestCase):
    """F1: die Oberflächenformen aus dem Original-Chat müssen auf EINE
    Fake-Entität mappen — sonst sieht das Modell 3-5 verschiedene Personen
    und der Kernbefund 'Personalien konsistent über 34 Jahre' kippt in
    erfundene Betrugssignale."""

    # Die Formen aus Failure-Katalog F1 (ohne 'BONNT DCMARTE' — extremes
    # Doppel-Garble, wird erst mit dem L5b-MRZ-Entity-Seed eingefangen).
    FORMS = [
        "Bonnie M Stark",
        "STARK, BONNIE MARIE",
        "Bonnie N. Stark",          # OCR-Fehler in der Akte
        "STARK<<BONNIE<MARIE",      # MRZ-Namensform
        "Stark, Bonnie",
        "B. Stark",
        "Bonnie MASE",              # OCR-Garble
    ]

    def test_all_forms_one_entity(self):
        m = ps.new_mapping()
        try:
            fakes = {f: _anon(m, f) for f in self.FORMS}
            self.assertEqual(len(m.entities), 1,
                             f"expected ONE entity, got {m.entities}")
            ent = next(iter(m.entities.values()))
            # Jede Form ist formgleich gefakt: Nachname-Fake überall drin
            # (Case-insensitiv), kein Original-Token übrig.
            for orig, fake in fakes.items():
                self.assertNotIn("stark", fake.lower(), (orig, fake))
                self.assertNotIn("bonnie", fake.lower(), (orig, fake))
                # Formen mit Nachnamen-Token tragen den Fake-Nachnamen; der
                # Garble 'MASE' mappt auf den nächstliegenden Token (marie →
                # Vornamens-Fake) — dort reicht Entitäts-Zugehörigkeit.
                if "stark" in orig.lower():
                    self.assertIn(ent["fake_sur"].lower(), fake.lower(),
                                  (orig, fake))
            # Formtreue Stichproben.
            self.assertIn(",", fakes["STARK, BONNIE MARIE"])
            self.assertTrue(fakes["STARK, BONNIE MARIE"].isupper())
            self.assertIn("<<", fakes["STARK<<BONNIE<MARIE"])
            self.assertRegex(fakes["B. Stark"], r"^[A-Z]\. ")
            self.assertRegex(fakes["Bonnie N. Stark"], r" [A-Z]\. ")
        finally:
            ps.close_mapping(m.mapping_id)

    def test_email_localpart_joins_entity(self):
        m = ps.new_mapping()
        try:
            _anon(m, "Bonnie M Stark")
            ent = next(iter(m.entities.values()))
            fake_mail = _anon(m, "kbstark@pacbell.net", rule="email")
            self.assertIn(ent["fake_sur"].lower(), fake_mail)
            self.assertNotIn("stark", fake_mail.lower())
            self.assertIn("@example.net", fake_mail)
            fake_dotted = _anon(m, "bonnie.stark@pacbell.net", rule="email")
            self.assertIn(".", fake_dotted.split("@")[0])
            self.assertIn(ent["fake_sur"].lower(), fake_dotted)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_email_without_entity_falls_back(self):
        m = ps.new_mapping()
        try:
            fake = _anon(m, "alice@example.com", rule="email")
            self.assertIn("@example.com", fake)
            self.assertNotIn("alice", fake)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_two_real_persons_stay_distinct(self):
        """Konservativität: 'Anna Weber' darf NICHT in die Stark-Entität
        gemerged werden (False-Merge zweier echter Personen wäre in einer
        Betrugsprüfung schlimmer als ein Miss)."""
        m = ps.new_mapping()
        try:
            f1 = _anon(m, "Bonnie M Stark")
            f2 = _anon(m, "Anna Weber")
            self.assertEqual(len(m.entities), 2)
            e1, e2 = m.entities["e1"], m.entities["e2"]
            self.assertNotEqual(e1["fake_sur"], e2["fake_sur"],
                                "two persons must never share a fake surname")
            self.assertNotEqual(f1, f2)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_variants_are_real_forward_reverse_entries(self):
        """§7.9-Invariante: die vorregistrierten Varianten stehen als echte
        Einträge in forward/reverse — davon leben L3a (Args-Deanon) und der
        Web-Egress-Gate, ohne dass dort Code angepasst wurde."""
        m = ps.new_mapping()
        try:
            _anon(m, "STARK, BONNIE MARIE")
            for expected in ("Bonnie Stark", "Stark, Bonnie",
                             "Bonnie Marie Stark", "STARK<<BONNIE<MARIE",
                             "Stark"):
                self.assertIn(expected, m.forward, expected)
                self.assertIn(m.forward[expected], m.reverse)
            # Args-Deanon-Pfad: Modell schreibt eine Variante, die nie im
            # Text stand — reverse übersetzt sie trotzdem zurück.
            fake_plain = m.forward["Bonnie Stark"]
            back, n = ps.deanonymize_text(
                f"suche nach {fake_plain} in den Akten", mapping=m)
            self.assertIn("Bonnie Stark", back)
            self.assertGreaterEqual(n, 1)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_known_values_sweep_word_bounded(self):
        """apply_known_values ersetzt registrierte Varianten, die der Scanner
        nicht fand (deutsche NER vs. englische Namen) — aber nie mitten im
        Wort ('Stark' ≠ 'Starkstrom')."""
        m = ps.new_mapping()
        try:
            _anon(m, "Bonnie M Stark")
            text = ("Drawer: Bonnie Stark, KO-Kunde seit 1992. "
                    "Die Starkstromleitung bleibt unberührt.")
            out, n = ps.apply_known_values(text, mapping=m)
            self.assertNotIn("Bonnie Stark", out)
            self.assertIn("Starkstromleitung", out)
            self.assertGreaterEqual(n, 1)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_entities_survive_persistence_roundtrip(self):
        m = ps.new_mapping()
        try:
            _anon(m, "Bonnie M Stark")
            nonce, ct = ps.encrypt_mapping(m)
            m2 = ps.decrypt_mapping(m.mapping_id, nonce, ct)
            self.assertEqual(m2.entities, m.entities)
            # Legacy-Zeile ohne entities-Feld lädt weiter (→ {}).
            d = ps._serialize_mapping(m)
            d.pop("entities")
            m3 = ps._deserialize_mapping(d)
            self.assertEqual(m3.entities, {})
        finally:
            ps.close_mapping(m.mapping_id)


class TestF2MrzAndDates(unittest.TestCase):
    """F2: Rechen-Checks dürfen auf Fakes keine falschen Fälschungsindizien
    liefern — Fake-MRZ mit gültigen Prüfziffern, Datums-Offset konstant."""

    # Golden-Werte beider echter Pässe (Referenzmaterial, Handover §L1):
    # alt (2007): 3099879889USA4702058F1701186 / neu (2026): 5606837078…

    def _fake_lines(self, m, num="560683707", dob="470205", exp="270126"):
        l1 = "P<USASTARK<<BONNIE<MARIE" + "<" * 20
        l2 = _td3_line2(num, "USA", dob, "F", exp)
        return _anon(m, l1, rule="mrz"), _anon(m, l2, rule="mrz"), l2

    def test_fake_mrz_all_checksums_valid(self):
        for num, dob, exp in (("560683707", "470205", "270126"),   # neuer Pass
                              ("309987988", "470205", "170118")):  # alter Pass
            m = ps.new_mapping()
            try:
                fl1, fl2, real_l2 = self._fake_lines(m, num, dob, exp)
                self.assertEqual(len(fl2), 44)
                self.assertNotIn(num, fl2)
                parsed = parse_mrz(fl1 + "\n" + fl2)
                self.assertIsNotNone(parsed, (fl1, fl2))
                checks = parsed["checks"]
                for k in ("document_number", "dob", "expiry",
                          "personal_number", "composite"):
                    self.assertIs(checks[k], True, (k, checks, fl2))
                self.assertNotIn("STARK", fl1)
            finally:
                ps.close_mapping(m.mapping_id)

    def test_mrz_expiry_unchanged_dob_shifted(self):
        m = ps.new_mapping()
        try:
            _, fl2, real_l2 = self._fake_lines(m)
            self.assertEqual(fl2[21:27], real_l2[21:27],
                             "expiry is a document-lifecycle date — unchanged")
            off = ps.date_offset_days(m.salt)
            real_dob = dt.date(1947, 2, 5)
            fake_dob = dt.datetime.strptime("19" + fl2[13:19], "%Y%m%d").date()
            self.assertEqual((fake_dob - real_dob).days, off)
            self.assertNotEqual(off, 0)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_mrz_number_consistent_with_viz(self):
        """'560683707' (VIZ, bare) und '5606837078' (MRZ, mit Prüfziffer)
        müssen auf DENSELBEN Fake mappen — nicht zwei unabhängige Tokens."""
        m = ps.new_mapping()
        try:
            _, fl2, _ = self._fake_lines(m)
            fake9 = fl2[0:9]
            self.assertEqual(m.forward.get("560683707"), fake9)
            self.assertEqual(m.forward.get("5606837078"), fl2[0:10])
        finally:
            ps.close_mapping(m.mapping_id)

    def test_mrz_number_never_reuses_opaque_token(self):
        """Wurde die bare Nummer vorher von einer Checksummen-Regel als
        OPAKER Token beansprucht (cz_rc matcht 9-Steller), darf der Token
        NICHT in die MRZ gespleißt werden."""
        m = ps.new_mapping()
        try:
            tok = _anon(m, "560683707", rule="cz_rc")
            self.assertTrue(tok.startswith("<"))
            _, fl2, _ = self._fake_lines(m)
            self.assertEqual(len(fl2), 44)
            self.assertNotIn("<CZ", fl2)
            self.assertTrue(fl2[0:9].isalnum(), fl2)
            parsed = parse_mrz("P<USASTARK<<BONNIE<MARIE" + "<" * 20 + "\n" + fl2)
            # Struktur bleibt parsebar, Nummern-Prüfziffer gültig.
            self.assertIs(parsed["checks"]["document_number"], True)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_passport_span_keeps_keyword(self):
        m = ps.new_mapping()
        try:
            fake = _anon(m, "Passport No. 560683707", rule="passport_ctx_loose")
            self.assertTrue(fake.startswith("Passport No. "), fake)
            self.assertNotIn("560683707", fake)
            self.assertRegex(fake, r"Passport No\. \d{9}$")
        finally:
            ps.close_mapping(m.mapping_id)

    def test_date_deltas_exact(self):
        """Der Verifikations-Testfall aus dem Handover: '10 Jahre − 1 Tag'
        (27.01.2017 → 26.01.2027) und '+9 Tage' (18.01.2017 → 27.01.2017)
        müssen auf den Fakes EXAKT erhalten bleiben."""
        m = ps.new_mapping()
        try:
            def _shift(s):
                fake = ps._fake_date(s, m.salt)
                self.assertNotEqual(fake, s)
                return dt.datetime.strptime(fake, "%d.%m.%Y").date()

            a, b = _shift("27.01.2017"), _shift("26.01.2027")
            self.assertEqual((b - a).days, 3651)          # 10y − 1d
            c, d = _shift("18.01.2017"), _shift("27.01.2017")
            self.assertEqual((d - c).days, 9)             # Renewal-Gap
        finally:
            ps.close_mapping(m.mapping_id)

    def test_textual_and_exif_formats(self):
        m = ps.new_mapping()
        try:
            off = ps.date_offset_days(m.salt)
            f1 = ps._fake_date("5 FEB 1947", m.salt)
            self.assertRegex(f1, r"^\d{1,2} [A-Z]{3} \d{4}$")
            got = dt.datetime.strptime(f1, "%d %b %Y").date()
            self.assertEqual((got - dt.date(1947, 2, 5)).days, off)
            f2 = ps._fake_date("26. Jan 2027", m.salt)
            self.assertRegex(f2, r"^\d{1,2}\. [A-ZÄÖÜ][a-zäöü]{2} \d{4}$")
            f3 = ps._fake_date("2026:07:02 14:24:48", m.salt)
            self.assertTrue(f3.endswith(" 14:24:48"))
            self.assertRegex(f3, r"^\d{4}:\d{2}:\d{2} ")
            # Dasselbe Datum in zwei Formaten → derselbe Tag (F2:
            # 'Formatblindheit' erzeugte Widersprüche im selben Kontext).
            g1 = dt.datetime.strptime(
                ps._fake_date("05.02.1947", m.salt), "%d.%m.%Y").date()
            self.assertEqual(g1, got)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_unparseable_date_falls_back_to_opaque(self):
        """Regression: früher gab _fake_date für Unbekanntes das nackte JAHR
        zurück → reverse['1947'] hätte jedes Vorkommen des Jahres
        zerschrieben. Jetzt: unverändert → _build_replacement mintet einen
        opaken Token."""
        m = ps.new_mapping()
        try:
            out = _anon(m, "Quartal Q3/im Lenz 1947", rule="date")
            self.assertRegex(out, r"^<DATE_\d+_[a-z0-9]+>$")
            self.assertNotIn("1947", m.reverse)
        finally:
            ps.close_mapping(m.mapping_id)


if __name__ == "__main__":
    unittest.main()
