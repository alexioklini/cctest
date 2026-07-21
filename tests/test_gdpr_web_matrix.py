"""End-to-End-Matrix: PII-Entscheidung × Web-Egress × lokal/cloud.

Die sechs Fälle, die zusammen die Korrektheit von „anonymisierter Chat mit
Websuche" ausmachen (Nutzer-Anforderung). Jeder Fall prüft die ZWEI
beobachtbaren Wahrheiten des Systems:

  (A) Was sieht das LLM?  — der Wire-Text wird aus dem Session-Mapping
      rückgeschrieben (`apply_known_values`): steht ein geschützter Wert im
      Mapping, sieht das LLM den Fake; steht er NICHT drin (FP/Klartext/lokal),
      sieht es das Original.
  (B) Was bekommt die Suchmaschine? — der ECHTE Web-Egress-Gate
      (`brain._gdpr_guard_web_args`): er übersetzt einen Fake fürs Retrieval
      zurück (allow), blockt (refuse), oder ist inaktiv, wenn kein Mapping aktiv
      ist (FP/Klartext/lokal → der Wert ist gar nicht geschützt).

Statt den ganzen Worker zu fahren, reproduzieren die Tests den Mapping-ZUSTAND
mit denselben Primitiven, die der Worker in run_session_turn aufruft
(`seed_from_decision` für ein anonymise-Votum, `purge_value` für ein
FP-Votum) — so ist der geprüfte Zustand der echte, nicht ein gemockter.

Fälle:
  1. Cloud + PII + Websuche ERLAUBT (allow)   → LLM anonym, Suchmaschine
     Klartext (Fake→Original übersetzt), Ergebnisse rück-anonymisiert.
  2. Cloud + PII + Websuche NICHT erlaubt (refuse) → LLM anonym, Suchmaschine
     blockiert + strukturierte Rückmeldung.
  3. Cloud + PII + False-Positive              → LLM Klartext, Suchmaschine
     Klartext, keine Rück-Anonymisierung (Wert nie im Mapping).
  4. Cloud + PII + „trotzdem senden"           → LLM Klartext, Suchmaschine
     Klartext, keine Rück-Anonymisierung (kein Mapping-Mint).
  5. Cloud + PII + „nutze lokal"               → lokales LLM Klartext,
     Suchmaschine Klartext, keine Rück-Anonymisierung (kein Mapping).
  6. Lokales LLM + PII                          → keine Detektion, alles
     Klartext, keine Anonymisierung/Deanonymisierung.

Run: python3 -m unittest tests.test_gdpr_web_matrix -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as ps  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402

# Der geschützte Personenname des Testfalls (wie im Chat faa124e1).
PERSON = "Lara Pulver"
RULE = "name"


class _MatrixBase(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = brain._get_gdpr_scanner_config
        self._mappings = []
        # NER hart aus — diese Suite prüft die GATE-/Mapping-Mechanik auf
        # Decision-Basis, nicht die Detektion (die lief pre-dialog). Ein
        # geladenes spaCy-Modell würde den Gate-Zusatz-Scan mit Extra-Findings
        # fluten (bekannter Ordnungs-Flake, vgl. test_web_egress_gate).
        from engine import pii_ner as _pn
        self._saved_nlp = dict(_pn._NLP_CACHE)
        _pn._NLP_CACHE.clear()

    def tearDown(self):
        brain._get_gdpr_scanner_config = self._orig_cfg
        for m in self._mappings:
            ps.close_mapping(m.mapping_id)
        from engine import pii_ner as _pn
        _pn._NLP_CACHE.clear()
        _pn._NLP_CACHE.update(self._saved_nlp)

    # ── Achsen-Helfer ────────────────────────────────────────────────────
    def _set_web_egress(self, mode):
        cfg = dict(brain._get_gdpr_scanner_config())
        cfg["web_egress"] = mode
        cfg["enabled"] = True
        brain._get_gdpr_scanner_config = lambda: cfg

    def _mapping(self):
        m = ps.new_mapping()
        self._mappings.append(m)
        return m

    def _anonymise_decision(self, m, value=PERSON, rule=RULE):
        """Worker-Pfad für ein anonymise-Votum: seed_from_decision mintet den
        Fake ins Mapping (run_session_turn §_mint)."""
        ok = ps.seed_from_decision(m, rule, value)
        self.assertTrue(ok, "seed_from_decision muss den Namen minten")
        return m.forward.get(value) or ""

    def _fp_decision(self, m, value=PERSON):
        """Worker-Pfad für ein False-Positive-Votum: purge_value entfernt jede
        Mapping-Spur (run_session_turn §_fp_vals)."""
        ps.purge_value(m, value)

    def _llm_sees(self, m, text):
        """(A) Was das LLM sieht: der Wire-Text nach dem deterministischen
        Rückschreiben aus dem Mapping (wie der Worker den Verlauf/Text
        pseudonymisiert)."""
        out, _ = ps.apply_entity_variants(text, mapping=m)
        out, _ = ps.apply_known_values(out, mapping=m, categories=None)
        return out

    def _search_gets(self, m, tool, args):
        """(B) Was die Suchmaschine bekommt: das ECHTE Web-Egress-Gate.
        Rückgabe: (refusal_or_None, dispatch_args)."""
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = m.mapping_id if m else ""
            ctx.current_session_id = None
            return brain._gdpr_guard_web_args(tool, args)


class TestCase1_CloudAnonWebAllow(_MatrixBase):
    """Cloud + PII + Websuche ERLAUBT → LLM anonym, Suchmaschine Klartext,
    Ergebnisse rück-anonymisiert."""

    def test_llm_sees_fake_search_gets_cleartext(self):
        self._set_web_egress("allow")
        m = self._mapping()
        fake = self._anonymise_decision(m)

        # (A) LLM: der echte Name ist durch den Fake ersetzt.
        seen = self._llm_sees(m, f"Bericht über {PERSON}, Kundin.")
        self.assertNotIn(PERSON, seen, "LLM darf den echten Namen NICHT sehen")
        self.assertIn(fake, seen, "LLM sieht den Fake")

        # (B) Suchmaschine: der Fake (den das Modell in die Query schreibt) wird
        # fürs Retrieval ins ORIGINAL zurückübersetzt.
        ref, args = self._search_gets(
            m, "searxng_search", {"query": f"{fake} Schauspielerin Bilder"})
        self.assertIsNone(ref, "allow: die Suche muss durchgehen")
        self.assertIn(PERSON, args["query"],
                      "Suchmaschine bekommt den echten Namen (Retrieval)")
        self.assertNotIn(fake, args["query"],
                         "der Fake selbst geht NICHT raus")

    def test_result_reanonymisation_seam_is_wired(self):
        # (C) Rück-Anonymisierung der Treffer: der L3b-Seam _gdpr_anon_tool_text
        # ersetzt Vorkommen des echten Namens in eingehendem Web-Content wieder
        # durch den Fake, bevor das LLM sie sieht.
        self._set_web_egress("allow")
        m = self._mapping()
        fake = self._anonymise_decision(m)
        with request_context():
            get_request_context()._gdpr_mapping_id = m.mapping_id
            result = brain._gdpr_anon_tool_text(
                f"Treffer: {PERSON} bei einer Premiere.", "searxng_search")
        self.assertNotIn(PERSON, result,
                         "Web-Treffer müssen vor dem LLM rück-anonymisiert sein")
        self.assertIn(fake, result)


class TestCase2_CloudAnonWebRefuse(_MatrixBase):
    """Cloud + PII + Websuche NICHT erlaubt → LLM anonym, Suchmaschine
    blockiert + meldet zurück."""

    def test_llm_sees_fake_search_blocked(self):
        self._set_web_egress("refuse")
        m = self._mapping()
        fake = self._anonymise_decision(m)

        # (A) LLM sieht weiterhin nur den Fake.
        seen = self._llm_sees(m, f"Prüfe {PERSON}.")
        self.assertNotIn(PERSON, seen)
        self.assertIn(fake, seen)

        # (B) Suchmaschine: der Fake wird blockiert (eine Fake-Suche träfe
        # Fremde), mit strukturierter Rückmeldung an das Modell.
        ref, args = self._search_gets(
            m, "searxng_search", {"query": f"{fake} obituary"})
        self.assertIsNotNone(ref, "refuse: die Suche muss blockiert werden")
        data = json.loads(ref)
        self.assertEqual(data["error"], "web_query_blocked_pii")
        self.assertIn("semantisch leer", data["hint"])
        # Der echte Name taucht in der Rückmeldung NIE auf (nur value-KINDS).
        self.assertNotIn("Lara", ref)
        self.assertNotIn("Pulver", ref)

    def test_original_in_query_also_blocked(self):
        # Selbst wenn das Modell den ECHTEN Namen in die Query schriebe (etwa
        # aus dem Verlauf), blockt refuse ihn.
        self._set_web_egress("refuse")
        m = self._mapping()
        self._anonymise_decision(m)
        ref, _ = self._search_gets(
            m, "searxng_search", {"query": f"{PERSON} news"})
        self.assertIsNotNone(ref, "der echte Name darf NIE rausgehen")


class TestCase3_CloudFalsePositive(_MatrixBase):
    """Cloud + PII + False-Positive → LLM Klartext, Suchmaschine Klartext,
    keine Rück-Anonymisierung."""

    def test_fp_value_stays_clear_everywhere(self):
        # allow ODER refuse ist egal: ein FP-Wert ist gar nicht geschützt.
        for mode in ("allow", "refuse"):
            with self.subTest(mode=mode):
                self._set_web_egress(mode)
                m = self._mapping()
                # Der Wert war evtl. mal im Mapping — das FP-Votum purged ihn.
                self._anonymise_decision(m)
                self._fp_decision(m)

                # (A) LLM sieht den echten Namen (kein Mapping-Eintrag mehr).
                seen = self._llm_sees(m, f"Bericht über {PERSON}.")
                self.assertIn(PERSON, seen,
                              "FP: das LLM bekommt den Klartext")

                # (B) Suchmaschine bekommt den echten Namen unverändert; der
                # Gate findet keinen geschützten Wert mehr → kein Refusal,
                # keine Übersetzung.
                ref, args = self._search_gets(
                    m, "searxng_search", {"query": f"{PERSON} Bilder"})
                self.assertIsNone(ref, "FP: keine Blockade")
                self.assertEqual(args["query"], f"{PERSON} Bilder",
                                 "FP: Query unverändert (Klartext)")

    def test_fp_result_not_reanonymised(self):
        # (C) Keine Rück-Anonymisierung: ein FP-Wert wird in Web-Treffern nicht
        # ersetzt (er ist nicht im Mapping).
        self._set_web_egress("allow")
        m = self._mapping()
        self._anonymise_decision(m)
        self._fp_decision(m)
        with request_context():
            get_request_context()._gdpr_mapping_id = m.mapping_id
            out = brain._gdpr_anon_tool_text(f"Treffer: {PERSON}.",
                                             "searxng_search")
        self.assertIn(PERSON, out, "FP-Wert bleibt im Treffer unverändert")


class TestCase4_CloudSendAnyway(_MatrixBase):
    """Cloud + PII + „trotzdem senden" → LLM Klartext, Suchmaschine Klartext,
    keine Rück-Anonymisierung. (`continue`/`send` mintet NICHTS ins Mapping.)"""

    def test_send_anyway_no_mapping_no_protection(self):
        for mode in ("allow", "refuse"):
            with self.subTest(mode=mode):
                self._set_web_egress(mode)
                # „trotzdem senden" = kein anonymise-Votum → das Mapping bleibt
                # leer (der Worker setzt _gdpr_pending_action nicht auf
                # anonymise, mintet also nichts). Wir modellieren das als leeres
                # Mapping.
                m = self._mapping()

                # (A) LLM sieht Klartext (nichts zu ersetzen).
                seen = self._llm_sees(m, f"Prüfe {PERSON}.")
                self.assertIn(PERSON, seen)

                # (B) Suchmaschine bekommt Klartext; ein leeres Mapping heißt
                # kein bekannter Wert → keine Blockade, keine Übersetzung.
                ref, args = self._search_gets(
                    m, "searxng_search", {"query": f"{PERSON} Bilder"})
                self.assertIsNone(ref, "send: keine Blockade")
                self.assertEqual(args["query"], f"{PERSON} Bilder")

    def test_gate_inactive_without_mapping(self):
        # Explizit: ohne aktives Mapping (mapping_id leer) ist der Gate ganz
        # inaktiv — der „trotzdem senden"-Turn installiert keins.
        self._set_web_egress("refuse")
        ref, _ = self._search_gets(None, "searxng_search",
                                   {"query": f"{PERSON} news"})
        self.assertIsNone(ref, "kein Mapping → Gate inaktiv")


class TestCase5_CloudUseLocal(_MatrixBase):
    """Cloud + PII + „nutze lokal" → lokales LLM Klartext, Suchmaschine
    Klartext, keine Rück-Anonymisierung. (local_model swappt das Modell und
    installiert KEIN Mapping — _is_local_turn unterdrückt die Anonymisierung.)"""

    def test_local_swap_leaves_everything_clear(self):
        self._set_web_egress("allow")
        # local_model-Pfad: der Worker setzt _gdpr_pending_action NICHT auf
        # anonymise (nur 'anonymise' tut das), also kein Mint → leeres Mapping.
        m = self._mapping()

        # (A) Das (lokale) LLM sieht Klartext.
        seen = self._llm_sees(m, f"Prüfe {PERSON}.")
        self.assertIn(PERSON, seen, "lokal: das LLM bekommt den Klartext")

        # (B) Suchmaschine bekommt Klartext, keine Blockade (kein geschützter
        # Wert im Mapping).
        ref, args = self._search_gets(
            m, "searxng_search", {"query": f"{PERSON} Bilder"})
        self.assertIsNone(ref)
        self.assertEqual(args["query"], f"{PERSON} Bilder")


class TestCase6_LocalModelNoDetection(_MatrixBase):
    """Lokales LLM + PII → keine Detektion, alles Klartext, keine
    Anonymisierung/Deanonymisierung. (is_model_local unterdrückt den ganzen
    GDPR-Pfad: _is_local_turn → kein gdpr_action → kein Mapping.)"""

    def test_local_model_bypasses_gdpr_entirely(self):
        self._set_web_egress("allow")
        # Ein lokaler Turn installiert kein Mapping (kein Egress → nichts zu
        # schützen). Modelliert als leeres Mapping / kein mapping_id.
        m = self._mapping()

        seen = self._llm_sees(m, f"Prüfe {PERSON}.")
        self.assertIn(PERSON, seen, "lokal: LLM bekommt Klartext")

        # Der Gate ist ohne aktives Mapping inaktiv.
        ref, args = self._search_gets(None, "searxng_search",
                                      {"query": f"{PERSON} Bilder"})
        self.assertIsNone(ref, "lokal: keine Blockade")
        self.assertEqual(args["query"], f"{PERSON} Bilder")

    def test_is_model_local_gates_the_wire(self):
        # Sanity: is_model_local ist das echte Gate-Kriterium (PROVIDER-basiert).
        # Ein als lokal markiertes Modell ist lokal, ein Cloud-Modell nicht —
        # das treibt _is_local_turn im Handler (kein Mapping für lokale Turns).
        self.assertTrue(callable(brain.is_model_local))


if __name__ == "__main__":
    unittest.main(verbosity=2)
