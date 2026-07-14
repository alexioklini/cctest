"""Nebenbefund-Fixes aus dem L7-Live-E2E (v9.342.0) — 4 gezielte Härtungen.

  1. NER-Recall-Netz: de_core_news_md verpasst 'Bonnie M Stark' im getippten
     deutschen Satz (der Turn-1-Wire-Leak des E2E) — das kleine _sm-Modell
     wird als Union für PERSON-Spans mitgefahren, eng gegated (≥2
     kapitalisierte Tokens, kein Overlap, Stop-Token-Liste gegen
     Title-Case-Prosa).
  2. Session-Delete purgt jetzt auch pii_decisions (der Ledger trug 468
     verwaiste raw_value-Klarwert-Zeilen nach Löschung der Test-Sessions).
  3. render_variant: Genitiv-/Klitik-Suffix nach Apostroph bleibt verbatim
     ('Bonnie Stark's' → 'Cameron Taylor's', nie mehr 'Taylor'm').
  4. Padding-Kollision '5 FEB 1947' vs '05 FEB 1947' auf demselben Fake:
     der reverse behält deterministisch die GEPADDETE Form (statt
     last-write-wins), Werte bleiben identisch.

Run: python3 -m unittest tests.test_l7_cleanup_fixes -v
NER-Tests laden echte spaCy-Modelle (~2-4s) und skippen sauber, wenn die
Modelle im Env fehlen.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402
from engine import pii_ner  # noqa: E402
from engine.identity import render_variant  # noqa: E402


class TestNerRecallNet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pii_ner.load_models(("de",))
        if not pii_ner.is_available("de"):
            raise unittest.SkipTest("de spaCy model not installed")
        cls.recall_loaded = ("de#recall" in pii_ner._NLP_CACHE)

    def _names(self, txt):
        return [txt[f["start"]:f["end"]]
                for f in pii_ner.scan_text(txt, lang="de", name_precision=True)
                if f["rule_id"] == "name"]

    def test_e2e_miss_is_caught(self):
        """Der gemessene Turn-1-Miss: md taggt den Satz nicht, das
        Recall-Netz muss ihn fangen."""
        if not self.recall_loaded:
            self.skipTest("de_core_news_sm not installed")
        txt = ("Prüfe die KO-Kundin Bonnie M Stark aus Oregon City: "
               "recherchiere im Web nach aktuellen öffentlichen Einträgen.")
        self.assertIn("Bonnie M Stark", self._names(txt))

    def test_title_case_prose_never_fires(self):
        """Der _sm-Rest-FP-Modus: durchgängig kapitalisierte Prosa —
        Stop-Token-Liste (flektierte Verben/Adverbien) killt den Span."""
        txt = "Der Bericht Wurde Gestern Erstellt und Enthält Keine Fehler."
        self.assertEqual(self._names(txt), [])

    def test_plain_german_sentences_unchanged(self):
        for txt in (
            "Die Wiener Privatbank erstellt Risikoanalysen externer Partner.",
            "Bitte prüfe die Meldung an die FMA nach der DORA-Richtlinie.",
        ):
            self.assertEqual(self._names(txt), [], txt)

    def test_main_model_findings_still_present(self):
        txt = "Kontoinhaberin ist Frau Kimberlee Stark, wohnhaft in Wien."
        self.assertIn("Kimberlee Stark", self._names(txt))

    def test_recall_survives_missing_model(self):
        """Ohne Recall-Modell im Cache bleibt scan_text voll funktionsfähig."""
        saved = pii_ner._NLP_CACHE.pop("de#recall", None)
        try:
            txt = "Kontoinhaberin ist Frau Kimberlee Stark aus Wien."
            self.assertIn("Kimberlee Stark", self._names(txt))
        finally:
            if saved is not None:
                pii_ner._NLP_CACHE["de#recall"] = saved

    def test_unload_drops_recall_entry(self):
        if not self.recall_loaded:
            self.skipTest("de_core_news_sm not installed")
        pii_ner.unload_model("de")
        try:
            self.assertNotIn("de", pii_ner._NLP_CACHE)
            self.assertNotIn("de#recall", pii_ner._NLP_CACHE)
        finally:
            pii_ner.load_models(("de",))


class TestSessionDeletePurgesLedger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="brain-l7fix-test-")
        import server_lib.db as _dbmod
        self._dbmod = _dbmod
        self._orig_chat_db = _dbmod.CHAT_DB
        _dbmod.CHAT_DB = os.path.join(self.tmpdir, "chats.db")
        try:
            _dbmod._db_pool.conns = {}
        except AttributeError:
            pass
        _dbmod.ChatDB.init()

    def tearDown(self):
        _dbmod = self._dbmod
        try:
            for c in (_dbmod._db_pool.conns or {}).values():
                try:
                    c.close()
                except Exception:
                    pass
            _dbmod._db_pool.conns = {}
        except AttributeError:
            pass
        _dbmod.CHAT_DB = self._orig_chat_db
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_delete_session_drops_pii_decisions(self):
        ChatDB = self._dbmod.ChatDB
        ChatDB.record_pii_decisions("sess-a", "u1", "t1", "anonymise", [
            {"rule_id": "name", "value": "Bonnie Stark",
             "fake_value": "Cameron Taylor"},
        ])
        ChatDB.record_pii_decisions("sess-b", "u1", "t2", "anonymise", [
            {"rule_id": "name", "value": "Nicholas Lubeck",
             "fake_value": "Logan Scott"},
        ])
        with self._dbmod._db_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM pii_decisions").fetchone()[0]
        self.assertEqual(n, 2)
        ChatDB.delete_session("sess-a")
        with self._dbmod._db_conn() as conn:
            rows = conn.execute(
                "SELECT session_id FROM pii_decisions").fetchall()
        # sess-a's cleartext row is gone, the OTHER session's row survives.
        self.assertEqual([r[0] for r in rows], ["sess-b"])


class TestGenitiveRendering(unittest.TestCase):
    ARGS = ("stark", ["bonnie", "marie"], "Taylor", ["Cameron", "Quinn"])

    def test_genitive_suffix_verbatim(self):
        self.assertEqual(
            render_variant("Bonnie Stark's", *self.ARGS), "Cameron Taylor's")

    def test_typographic_apostrophe(self):
        self.assertEqual(
            render_variant("Bonnie Stark’s", *self.ARGS), "Cameron Taylor’s")

    def test_initials_still_map(self):
        """Regression: echte Initialen (kein Apostroph davor) mappen weiter."""
        self.assertEqual(
            render_variant("Bonnie M. Stark", *self.ARGS),
            "Cameron Q. Taylor")

    def test_mrz_form_unchanged(self):
        self.assertEqual(
            render_variant("STARK<<BONNIE<MARIE", *self.ARGS),
            "TAYLOR<<CAMERON<QUINN")


class TestDatePaddingCollision(unittest.TestCase):
    def _mk(self):
        m = ps.new_mapping()
        self.addCleanup(ps.close_mapping, m.mapping_id)
        return m

    def test_padded_form_wins_either_order(self):
        for order in (("5 FEB 1947", "05 FEB 1947"),
                      ("05 FEB 1947", "5 FEB 1947")):
            m = self._mk()
            for orig in order:
                m.record(orig, "10 FEB 1947", "dob")
            self.assertEqual(m.reverse["10 FEB 1947"], "05 FEB 1947",
                             f"order={order}")
            # BEIDE forward-Einträge bleiben (jede Oberflächenform wird
            # weiterhin ersetzt) — nur der reverse ist dedupliziert.
            self.assertEqual(m.forward["5 FEB 1947"], "10 FEB 1947")
            self.assertEqual(m.forward["05 FEB 1947"], "10 FEB 1947")

    def test_non_date_collision_keeps_last_write_wins(self):
        """Bestandsverhalten für Nicht-Datums-Kollisionen unangetastet."""
        m = self._mk()
        m.record("Bonnie Stark", "Cameron Taylor", "name")
        m.record("Bonnie Starke", "Cameron Taylor", "name")
        self.assertEqual(m.reverse["Cameron Taylor"], "Bonnie Starke")

    def test_distinct_dates_never_deduped(self):
        """Zwei ECHT verschiedene Daten auf demselben Fake wären ein Bug an
        anderer Stelle — record darf sie nicht stillschweigend mergen."""
        m = self._mk()
        m.record("05 FEB 1947", "10 FEB 1947", "dob")
        m.record("6 FEB 1947", "10 FEB 1947", "dob")
        self.assertEqual(m.reverse["10 FEB 1947"], "6 FEB 1947")


if __name__ == "__main__":
    unittest.main()
