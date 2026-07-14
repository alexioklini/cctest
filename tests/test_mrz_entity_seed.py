"""L5 — OCR-Preamble scannen + MRZ-Entity-Seed (PII_ANALYSIS_PARITY_HANDOVER §L5).

L5a: der deterministische OCR-Block (`[Bild-Anhänge — …]`) ist CONTENT, kein
Boilerplate — er trägt MRZ/Name/DOB fotografierter Ausweise und muss durch
Scan + Ledger-Rewrite laufen; die Pfad-Notice bleibt exempt (read_document
braucht die Pfade verbatim). Neue Nachrichten tragen den Block VOR der
Notice; Legacy-History (Block INNERHALB der Notice) wird beim Split in die
scannbare Hälfte gezogen.

L5b: die MRZ ist die sauberste maschinenlesbare Identitätsquelle im Material
— ein checksummen-validierter Parse seedet die Entitäts-Map VOR dem
Text-Scan (Name+Varianten, Passnummer bare+10er-Form, DOB-Oberflächenformen),
womit auch Extremgarble (`BONNT DCMARTE`) per Fuzzy-Attach eingefangen wird.

Run: python3 -m pytest tests/test_mrz_entity_seed.py -v
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402
from engine.tools.doc_checks import mrz_check_digit, parse_mrz  # noqa: E402


def _td3_line2(num, nat, dob, sex, exp, pers="<" * 14):
    l = (num + str(mrz_check_digit(num)) + nat
         + dob + str(mrz_check_digit(dob)) + sex
         + exp + str(mrz_check_digit(exp)) + pers
         + (str(mrz_check_digit(pers)) if pers.strip("<") else "0"))
    comp = l[0:10] + l[13:20] + l[21:43]
    return l + str(mrz_check_digit(comp))


# Golden-MRZ des echten neuen Passes (Referenzmaterial, Chat 58e3c521438a).
GOLD_L1 = "P<USASTARK<<BONNIE<MARIE" + "<" * 20
GOLD_L2 = _td3_line2("560683707", "USA", "470205", "F", "270126")


class TestL5aNoticeSplit(unittest.TestCase):
    """L5a: _split_attachment_notice — OCR-Block scannbar, Pfade exempt."""

    OCR = ("\n\n[Bild-Anhänge — automatisch, ohne KI erkannt "
           "(Text via OCR + deterministische Merkmale):]\n\n"
           "STARK / BONNIE MARIE / 560683707 / 05 Feb 1947")
    NOTICE = ("\n\n[User attached files saved to disk:]\n"
              "  - /tmp/brain-attachments/x/STARK_Bonnie_107625.jpg")

    def test_new_order_ocr_block_lands_in_typed_half(self):
        from handlers.chat import _split_attachment_notice
        text = "Prüfe den Pass." + self.OCR + self.NOTICE
        typed, notice = _split_attachment_notice(text)
        self.assertIn("560683707", typed)          # OCR-Inhalt wird gescannt
        self.assertNotIn("560683707", notice.replace("107625", ""))
        self.assertIn("STARK_Bonnie_107625.jpg", notice)  # Pfad bleibt exempt
        self.assertEqual(typed + notice, text)     # verlustfrei

    def test_legacy_order_ocr_block_moved_out_of_notice(self):
        # Pre-L5-History: der Block wurde ans ENDE der Notice gehängt und
        # entging damit jedem Scan (F5). Der Split zieht ihn in die
        # scannbare Hälfte; die Pfade bleiben in der exempten.
        from handlers.chat import _split_attachment_notice
        text = "Prüfe den Pass." + self.NOTICE + self.OCR
        typed, notice = _split_attachment_notice(text)
        self.assertIn("560683707", typed)
        self.assertIn("BONNIE MARIE", typed)
        self.assertIn("STARK_Bonnie_107625.jpg", notice)
        self.assertNotIn("Bild-Anhänge", notice)
        # Reorder ist wire-only und verlustfrei (Inhalt vollständig).
        self.assertEqual(sorted(typed + notice), sorted(text))

    def test_no_notice_passthrough(self):
        from handlers.chat import _split_attachment_notice
        typed, notice = _split_attachment_notice("nur Text")
        self.assertEqual((typed, notice), ("nur Text", ""))

    def test_ledger_rewrite_covers_ocr_block_both_orders(self):
        # L3c + L5a: der Ledger-Replace erwischt Werte im OCR-Block (beide
        # Reihenfolgen), NIE in den Pfaden.
        from handlers.chat import _apply_pii_decisions_to_wire
        decisions = {"h1": {"value": "560683707", "fake_value": "888417222",
                            "rule_id": "passport",
                            "turn_action": "anonymise",
                            "false_positive": False}}
        for text in ("Prüfe." + self.OCR + self.NOTICE,
                     "Prüfe." + self.NOTICE + self.OCR):
            wire, replaced, _ = _apply_pii_decisions_to_wire(
                [{"role": "user", "content": text}], decisions)
            out = wire[0]["content"]
            self.assertIn("888417222", out)
            self.assertNotIn("560683707", out)
            self.assertIn("STARK_Bonnie_107625.jpg", out)  # Pfad intakt
            self.assertEqual(replaced, 1)


class TestL5bSeed(unittest.TestCase):
    """L5b: seed_identity_from_mrz — Entity + Passnummer + DOB-Formen."""

    def _seed(self):
        m = ps.new_mapping()
        parsed = parse_mrz(GOLD_L1 + "\n" + GOLD_L2)
        self.assertIsNotNone(parsed)
        res = ps.seed_identity_from_mrz(m, parsed)
        return m, res

    def test_seed_registers_name_passport_dob(self):
        m, res = self._seed()
        self.assertEqual(res, {"name": True, "passport": True, "dob": True})
        self.assertEqual(len(m.entities), 1)
        # Standard-Namensvarianten sind echte forward-Einträge (§7.9) —
        # inkl. der Form, die die NER bei 'Stark Bonnie …' verfehlt.
        for form in ("Bonnie Stark", "Bonnie Marie Stark", "Bonnie M. Stark",
                     "STARK<<BONNIE<MARIE"):
            self.assertIn(form, m.forward, form)
        # Passnummer: bare + 10er-Form konsistent (F2).
        self.assertIn("560683707", m.forward)
        fake9 = m.forward["560683707"]
        self.assertEqual(len(fake9), 9)
        self.assertEqual(m.forward.get("5606837078"),
                         fake9 + str(mrz_check_digit(fake9)))
        # DOB-Oberflächenformen, konsistent mit dem Scanner-Generator.
        # (Unpaddede Varianten wie '5 Feb 1947' dedupen gegen die gepaddete,
        # wenn der geshiftete Tag zweistellig ist — der Scanner-Generator
        # liefert für sie trotzdem dasselbe Datum, letzte Assertion.)
        for form in ("1947-02-05", "05.02.1947", "02/05/1947",
                     "05 FEB 1947"):
            self.assertIn(form, m.forward, form)
            self.assertEqual(m.forward[form], ps._fake_date(form, m.salt))
        shifted = dt.date(1947, 2, 5) + dt.timedelta(
            days=ps.date_offset_days(m.salt))
        unpadded_fake = ps._fake_date("5 Feb 1947", m.salt)
        self.assertEqual(
            dt.datetime.strptime(unpadded_fake, "%d %b %Y").date(), shifted)
        # Konstanter Offset: Delta zwischen Real und Fake ist der
        # Session-Offset, nicht Jitter.
        fake_iso = dt.date.fromisoformat(m.forward["1947-02-05"])
        self.assertEqual((fake_iso - dt.date(1947, 2, 5)).days,
                         ps.date_offset_days(m.salt))

    def test_extreme_garble_attaches_after_seed(self):
        # 'BONNT DCMARTE' (Doppel-Garble aus dem echten Material) attacht
        # string-seitig NICHT (Handover Session-3-Nebenbefund) — mit
        # geseedeter Entität fängt der Fuzzy-Attach sie ein.
        m, _ = self._seed()
        fake = ps._entity_fake_name("BONNT DCMARTE", m)
        self.assertEqual(len(m.entities), 1)       # KEINE zweite Entität
        self.assertNotIn("BONNT", fake)
        self.assertNotIn("DCMARTE", fake)

    def test_seed_honesty_gates(self):
        # Unlesbare Felder seeden NICHTS (dieselbe Ehrlichkeits-Invariante
        # wie doc_checks: nie aus Garble raten).
        m = ps.new_mapping()
        parsed = {"surname": "STARK", "givens": "BONNIE MARIE",
                  "document_number": "560683707",
                  "dob": dt.date(1947, 2, 5),
                  "checks": {"document_number": False, "dob": None,
                             "expiry": None, "composite": None}}
        res = ps.seed_identity_from_mrz(m, parsed)
        self.assertEqual(res, {"name": False, "passport": False, "dob": False})
        self.assertEqual(m.forward, {})
        self.assertEqual(m.entities, {})

    def test_lone_surname_never_seeds(self):
        m = ps.new_mapping()
        parsed = parse_mrz(GOLD_L1 + "\n" + GOLD_L2)
        parsed["givens"] = ""
        res = ps.seed_identity_from_mrz(m, parsed)
        self.assertFalse(res["name"])
        self.assertEqual(m.entities, {})

    def test_mrz_scan_after_seed_uses_same_fakes(self):
        # Die spätere Scanner-Findung der MRZ-Zeile (rule 'mrz') muss
        # dieselbe Fake-Nummer tragen wie der Seed (F2: ein Dokument, ein
        # Fake).
        m, _ = self._seed()
        findings = [{"rule_id": "mrz", "label": "mrz",
                     "start": 0, "end": len(GOLD_L2)}]
        faked = ps.pseudonymize_text(GOLD_L2, findings, mapping=m)
        self.assertTrue(faked.startswith(m.forward["560683707"]))
        reparsed = parse_mrz(GOLD_L1 + "\n" + faked)
        self.assertIsNotNone(reparsed)
        self.assertTrue(reparsed["checks"]["document_number"])


class TestEntitySweep(unittest.TestCase):
    """L5: apply_entity_variants — Fuzzy-Fenster-Sweep hinter dem Scan.
    Fängt die drei am echten Material gemessenen Leak-Klassen: reordered
    ('Stark Bonnie M', NER-Wortstellungs-Lücke), Mixed-Case-Dateinamen-
    Heading, MRZ-Garble ('PSUSASTARK<<BONNT DCMARTE')."""

    def _mapping(self):
        m = ps.new_mapping()
        ps.seed_identity_from_mrz(m, parse_mrz(GOLD_L1 + "\n" + GOLD_L2))
        return m

    def test_sweeps_reordered_garble_and_heading_forms(self):
        m = self._mapping()
        cases = ["von Stark Bonnie M auf Echtheit",
                 "j PSUSASTARK<<BONNT DCMARTE << em CR RR",
                 "CF -  - STARK, Bonnie M Mrs. (107625) - Pass.jpg"]
        for t in cases:
            out, n = ps.apply_entity_variants(t, mapping=m)
            self.assertGreaterEqual(n, 1, t)
            low = out.lower()
            for tok in ("stark", "bonnie", "marie", "bonnt", "dcmarte"):
                self.assertNotIn(tok, low, f"{t!r} -> {out!r}")
        # Ersetzte Spans sind ECHTE forward/reverse-Einträge — L3a kann
        # einen gefakten Pfad-Anteil zurückübersetzen, der Ledger-Rewrite
        # kennt die Form ab jetzt exakt.
        self.assertIn("Stark Bonnie M", m.forward)

    def test_sweep_conservative_no_false_positives(self):
        m = self._mapping()
        for t in ("Anna Weber prüft den Fall",
                  "Der Starkstrom-Anschluss im Keller",
                  "Sehr Geehrter Herr Doktor",
                  "STARK_Bonnie_M_Mrs._107625.pdf",   # _ nie Separator (Pfade)
                  "WebID Solutions GmbH"):
            out, n = ps.apply_entity_variants(t, mapping=m)
            self.assertEqual((out, n), (t, 0), t)

    def test_sweep_noop_without_entities(self):
        m = ps.new_mapping()
        out, n = ps.apply_entity_variants("Stark Bonnie M", mapping=m)
        self.assertEqual((out, n), ("Stark Bonnie M", 0))

    def test_mrz_garble_line_substring_stage(self):
        # Lowercase-Bleed-Garble ('peUEASTARK<<800"1', echtes Material):
        # ALLCAPS-Substring-Ersetzung NUR in Zeilen mit '<<'.
        m = self._mapping()
        out, n = ps.apply_entity_variants('peUEASTARK<<800"1', mapping=m)
        self.assertNotIn("STARK", out)
        self.assertIn("<<", out)
        # Ohne MRZ-Kontext bleibt derselbe Token unangetastet.
        out2, _ = ps.apply_entity_variants("peUEASTARK 800", mapping=m)
        self.assertIn("STARK", out2)

    def test_allcaps_lone_surname_is_registered_variant(self):
        # VIZ-Nachnamenszeile ('STARK' allein) — Einzeltokens sind für den
        # Fenster-Sweep unsichtbar, das exakte Paar muss existieren.
        m = self._mapping()
        self.assertIn("STARK", m.forward)
        out, n = ps.apply_known_values("STARK\nGiven names", mapping=m)
        self.assertNotIn("STARK", out)
        # Wortgrenze: Komposita bleiben heil.
        out2, _ = ps.apply_known_values("STARKSTROM", mapping=m)
        self.assertEqual(out2, "STARKSTROM")


class TestOrchestrator(unittest.TestCase):
    """brain._gdpr_seed_entities_from_attachments — Pfad-Gating + Seed."""

    def test_seeds_from_image_path_and_skips_others(self):
        import brain
        from engine.tools import doc_checks as dc
        calls = []
        orig = dc._ocr_mrz_strip
        dc._ocr_mrz_strip = lambda p: (calls.append(p) or
                                       GOLD_L1 + "\n" + GOLD_L2)
        try:
            m = ps.new_mapping()
            n = brain._gdpr_seed_entities_from_attachments(
                ["/tmp/x/pass.jpg", "/tmp/x/akte.txt"], m)
        finally:
            dc._ocr_mrz_strip = orig
        self.assertEqual(n, 1)
        self.assertEqual(calls, ["/tmp/x/pass.jpg"])   # .txt nie geOCRt
        self.assertIn("560683707", m.forward)
        self.assertEqual(len(m.entities), 1)

    def test_best_read_first_garbled_photo_never_poisons_entity(self):
        # Am echten Material gemessen: Dateinamen-Sortierung ließ das
        # 1-Prüfziffern-Foto ('BONNTIMARTI', verklebtes BONNIE MARIE) die
        # Entität VOR dem 5-Prüfziffern-CF-Scan anlegen. Beste Lesung MUSS
        # zuerst seeden; die Zweitlesung derselben Dokumentnummer darf
        # keine neue Entität anlegen.
        import brain
        from engine.tools import doc_checks as dc
        num = "560683707"
        garbled = ("P<USASTARK<<BONNTIMARTI" + "<" * 21 + "\n"
                   + num + str(mrz_check_digit(num))
                   + "USAABCDEF0FGHIJKL0")   # Nummer ✓, Daten unlesbar
        by_path = {"/tmp/x/1_foto.jpg": garbled,
                   "/tmp/x/2_cf_scan.jpg": GOLD_L1 + "\n" + GOLD_L2}
        orig = dc._ocr_mrz_strip
        dc._ocr_mrz_strip = lambda p: by_path[p]
        try:
            m = ps.new_mapping()
            brain._gdpr_seed_entities_from_attachments(
                sorted(by_path), m)   # Garble-Foto zuerst im Dateinamen-Sort
        finally:
            dc._ocr_mrz_strip = orig
        self.assertEqual(len(m.entities), 1)
        ent = list(m.entities.values())[0]
        self.assertEqual(ent["givens"], ["bonnie", "marie"])
        self.assertNotIn("bonntimarti", ent["givens"])

    def test_unparseable_strip_seeds_nothing(self):
        import brain
        from engine.tools import doc_checks as dc
        orig = dc._ocr_mrz_strip
        dc._ocr_mrz_strip = lambda p: "kein mrz hier"
        try:
            m = ps.new_mapping()
            n = brain._gdpr_seed_entities_from_attachments(
                ["/tmp/x/foto.jpg"], m)
        finally:
            dc._ocr_mrz_strip = orig
        self.assertEqual(n, 0)
        self.assertEqual(m.forward, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
