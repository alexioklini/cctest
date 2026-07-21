"""Web-Egress-Gate (L4 Phase 1) — Sicherheits- und FP-Tests.

Das Gate (`brain._gdpr_guard_web_args`, aufgerufen in
`engine/llm_loop.py:dispatch_tool`) verweigert in anonymisierenden Sessions
Web-Tool-Calls, deren Args geschützte Werte (Originale), Pseudonyme/Tokens
(Fakes) oder frische Personen-PII enthalten.

Die wichtigsten Invarianten (PII_ANALYSIS_PARITY_HANDOVER.md §4):
  - SICHERHEIT: mit aktivem Mapping erreicht kein Klarwert das Netzwerk —
    auch nicht im URL-Slug (bizapedia.com/people/bonnie-stark.html).
  - Zwei Modi (v9.386.0): `refuse` blockt geschützte Werte, `allow` übersetzt
    bekannte Fakes ins ORIGINAL für Retrieval-Tools (Chat faa124e1) — damit eine
    gewollte Personen-Recherche funktioniert. Im `refuse`-Modus wird ein Fake
    verweigert (eine Fake-Suche ist semantisch leer oder trifft echte
    Fremdpersonen → Gift-Evidenz); opake Tokens ohne auflösbares Original
    refusen in beiden Modi. ('ask'/'block_group' entfernt.)
  - FP-Kosten: technische Queries ("Samsung … EXIF", "ICAO 9303 check digit")
    laufen IMMER ungehindert durch.
  - Ohne aktives Mapping ist das Gate vollständig inaktiv.

Run: python3 -m unittest tests.test_web_egress_gate -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402


def _mk_mapping(*entries):
    """entries: (original, fake, rule_id). Returns a registered live Mapping."""
    m = pseudonymizer.new_mapping()
    for orig, fake, rid in entries:
        m.record(orig, fake, rid)
    return m


def _cfg_with_mode(mode):
    cfg = dict(brain._get_gdpr_scanner_config())
    cfg["web_egress"] = mode
    return cfg


class _GateTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_cfg_fn = brain._get_gdpr_scanner_config
        self._mappings = []
        # DEFAULT-Modus 'refuse' hart fixieren: die Refusal-Tests dieser Suite
        # setzen keinen Modus explizit und erbten bisher STILL den web_egress
        # der Live-config.json (dort 'allow'). Solange 'allow' auch Fakes
        # refuste, fiel das nicht auf — seit dem allow-Fake→Original-Release
        # (Chat faa124e1) hängt das Ergebnis sonst an der zufälligen Live-
        # Config. Tests, die 'allow' prüfen, setzen den Modus selbst via
        # _set_mode.
        self._set_mode("refuse")
        # NER-Isolation (v9.342.0): diese Suite testet die GATE-Mechanik auf
        # Regex-/Mapping-Basis. Läuft sie NACH einer Suite, die die spaCy-
        # Modelle geladen hat (test_pii_ner), sieht der Gate-Zusatz-Scan
        # ZUSÄTZLICHE NER-Findings und die Consent-Slot-Assertions kippen —
        # der vorbestehende Ordnungs-Flake der Ask-Tests. NER hier hart aus.
        from engine import pii_ner as _pn
        self._saved_nlp = dict(_pn._NLP_CACHE)
        _pn._NLP_CACHE.clear()

    def tearDown(self):
        brain._get_gdpr_scanner_config = self._orig_cfg_fn
        for m in self._mappings:
            pseudonymizer.close_mapping(m.mapping_id)
        from engine import pii_ner as _pn
        _pn._NLP_CACHE.clear()
        _pn._NLP_CACHE.update(self._saved_nlp)

    def _mapping(self, *entries):
        m = _mk_mapping(*entries)
        self._mappings.append(m)
        return m

    def _set_mode(self, mode):
        cfg = _cfg_with_mode(mode)
        brain._get_gdpr_scanner_config = lambda: cfg

    def _guard(self, tool, args, mapping=None):
        """Refusal-only view of the gate (Phase-1 assertions). Phase 2 returns
        (refusal, args) — tests that care about the translated args use
        _guard_full."""
        return self._guard_full(tool, args, mapping=mapping)[0]

    def _guard_full(self, tool, args, mapping=None):
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = mapping.mapping_id if mapping else ""
            ctx.current_session_id = None  # ledger path off in unit tests
            return brain._gdpr_guard_web_args(tool, args)


class TestGateInactive(_GateTestBase):
    def test_non_web_tool_never_gated(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        self.assertIsNone(self._guard(
            "read_document", {"path": "/x/Bonnie M Stark.pdf"}, mapping=m))

    def test_no_mapping_gate_inactive(self):
        """Ohne aktives Mapping ist das Gate aus — Nicht-Anonymise-Sessions
        verhalten sich exakt wie vorher."""
        self.assertIsNone(self._guard(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}))

    def test_empty_args_pass(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        self.assertIsNone(self._guard("searxng_search", {"query": ""},
                                      mapping=m))


class TestKnownOriginals(_GateTestBase):
    def test_known_name_refused(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard("searxng_search",
                          {"query": "Bonnie M Stark Oregon City OR age 79"},
                          mapping=m)
        self.assertIsNotNone(out)
        data = json.loads(out)
        self.assertEqual(data["error"], "web_query_blocked_pii")
        kinds = [b["value_kind"] for b in data["blocked"]]
        self.assertIn("name", kinds)
        self.assertFalse(any(b["released"] for b in data["blocked"]))
        # Der Error enthält KINDS, nie den Wert selbst.
        self.assertNotIn("Bonnie", out)
        self.assertNotIn("Stark", out)

    def test_url_slug_detected(self):
        """Der Name steckt im URL-Slug (Space→'-', Mittelinitial fehlt) —
        genau der bizapedia-Fall aus dem Original-Chat."""
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard(
            "web_fetch",
            {"url": "https://www.bizapedia.com/people/bonnie-stark.html"},
            mapping=m)
        self.assertIsNotNone(out)
        self.assertEqual(json.loads(out)["error"], "web_query_blocked_pii")

    def test_case_insensitive(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard("searxng_search",
                          {"query": "BONNIE M STARK obituary"}, mapping=m)
        self.assertIsNotNone(out)

    def test_known_passport_number_refused(self):
        m = self._mapping(("5606837078", "<PASSPORT_1_ab12>", "passport"))
        out = self._guard("searxng_search",
                          {"query": "passport 5606837078 valid"}, mapping=m)
        self.assertIsNotNone(out)
        self.assertIn("passport",
                      [b["value_kind"] for b in json.loads(out)["blocked"]])


class TestFakes(_GateTestBase):
    def test_opaque_token_always_refused(self):
        m = self._mapping(("5606837078", "<PASSPORT_1_ab12>", "passport"))
        out = self._guard("searxng_search",
                          {"query": "passport <PASSPORT_1_ab12> check"},
                          mapping=m)
        self.assertIsNotNone(out)
        data = json.loads(out)
        self.assertEqual(data["error"], "web_query_blocked_pii")
        # Fake-Hinweis: Suche wäre semantisch leer / träfe Fremdpersonen.
        self.assertIn("semantisch leer", data["hint"])

    def test_shape_fake_name_refused(self):
        """Shape-Fakes sind reale Namen — eine Suche damit trifft echte
        FREMDE Personen (Gift-Evidenz). Immer refuse."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._guard("searxng_search",
                          {"query": "Erika Muster Oregon City age 79"},
                          mapping=m)
        self.assertIsNotNone(out)
        self.assertIn("semantisch leer", json.loads(out)["hint"])

    def test_fake_translated_to_original_in_allow_mode(self):
        """allow-Modus (Chat faa124e1): ein bekannter Personen-Fake wird ins
        ORIGINAL hin-übersetzt statt refused — `allow` gibt den Klarwert-Egress
        dieser Session generell frei, also darf ein VORHER pseudonymisierter
        Name nicht schlechter stehen als ein ungefakter. NICHT der Fake geht
        raus (er träfe Fremde), sondern das echte Original."""
        self._set_mode("allow")
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out, args = self._guard_full(
            "searxng_search", {"query": "Erika Muster obituary"}, mapping=m)
        self.assertIsNone(out, "allow: die Suche muss durchgehen")
        self.assertIn("Bonnie M Stark", args.get("query", ""),
                      "allow: das Original wird für den Request eingesetzt")
        self.assertNotIn("Erika Muster", args.get("query", ""),
                         "allow: der Fake selbst darf NICHT hinausgehen")

    def test_fake_still_refused_in_refuse_mode(self):
        """Gegenprobe: derselbe Personen-Fake refust im (Default-)refuse-Modus
        weiter — dort ist der Klarwert-Egress NICHT freigegeben."""
        self._set_mode("refuse")
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._guard("searxng_search",
                          {"query": "Erika Muster obituary"}, mapping=m)
        self.assertIsNotNone(out)
        self.assertIn("semantisch leer", json.loads(out)["hint"])


class TestTechnicalQueriesPass(_GateTestBase):
    """FP-Test: technische Queries aus dem Original-Chat dürfen NIE blocken."""

    def test_technical_queries_pass(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"),
                          ("05.02.1947", "<DOB_1_ab12>", "dob"))
        for q in ("Samsung Galaxy S23 Ultra EXIF GPS null null",
                  "ICAO 9303 check digit algorithm",
                  "Samsung SEFT trailer ShadowRemoval",
                  "reportlab table of contents python"):
            with self.subTest(q=q):
                self.assertIsNone(
                    self._guard("searxng_search", {"query": q}, mapping=m))


class TestFreshPII(_GateTestBase):
    def test_fresh_email_refused(self):
        """Ein nie gemappter Wert (dritte Person) wird vom Zusatz-Scan
        erwischt — auch wenn die Session-Config contact=ignore hat (der
        Gate fragt 'was IST PII', nicht 'was ist actionable')."""
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard("searxng_search",
                          {"query": "kontakt hmuellerx@web-privat-xyz.de"},
                          mapping=m)
        self.assertIsNotNone(out)
        self.assertIn("email",
                      [b["value_kind"] for b in json.loads(out)["blocked"]])


class TestModes(_GateTestBase):
    def test_allow_mode_originals_pass(self):
        self._set_mode("allow")
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        self.assertIsNone(self._guard(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"},
            mapping=m))

    def test_legacy_modes_fall_back_to_refuse(self):
        """v9.386.0: 'ask'/'block_group' wurden entfernt. Ein unbekannter/alter
        Modus-String im Gate fällt auf 'refuse' zurück (mode not in
        _WEB_EGRESS_MODES → refuse), also blockt eine Query mit geschütztem
        Wert. (Die persistierte Config normalisiert bereits beim Laden; dies
        deckt den Gate-internen Fallback ab.)"""
        for legacy in ("ask", "block_group", "garbage"):
            with self.subTest(mode=legacy):
                self._set_mode(legacy)
                m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
                out = self._guard("searxng_search",
                                  {"query": "Bonnie M Stark"}, mapping=m)
                self.assertIsNotNone(out, f"{legacy} muss wie refuse blocken")


class TestLedgerValues(_GateTestBase):
    def test_ledger_value_refused_and_fp_passes(self):
        """Ledger-Werte (pii_decisions) schützen auch ohne Mapping-Eintrag;
        False-Positive-Werte sind NICHT geschützt."""
        from server_lib.db import ChatDB
        orig = ChatDB.get_session_pii_decisions
        ChatDB.get_session_pii_decisions = staticmethod(lambda sid: {
            "h1": {"rule_id": "dob", "value": "05.02.1947",
                   "false_positive": False, "disposition": "",
                   "turn_action": "anonymise", "fake_value": "17.02.1947"},
            "h2": {"rule_id": "name", "value": "Max Beispielmann",
                   "false_positive": True, "disposition": "",
                   "turn_action": "send", "fake_value": ""},
        })
        try:
            m = self._mapping(("irrelevant", "<X_1_ab12>", "bare_identifier"))
            with request_context():
                ctx = get_request_context()
                ctx._gdpr_mapping_id = m.mapping_id
                ctx.current_session_id = "test-session"
                # Ledger-Original → refuse
                out, _ = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "born 05.02.1947 person"})
                self.assertIsNotNone(out)
                # Ledger-Fake → refuse (Fake-Pfad)
                out, _ = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "born 17.02.1947 person"})
                self.assertIsNotNone(out)
                self.assertIn("semantisch leer", json.loads(out)["hint"])
                # FP-Wert → pass
                out, _ = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "Max Beispielmann"})
                self.assertIsNone(out)
        finally:
            ChatDB.get_session_pii_decisions = orig


class TestNestedArgs(_GateTestBase):
    def test_value_in_nested_list_refused(self):
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard(
            "web_fetch",
            {"url": "https://example.com",
             "extra": {"queries": ["harmless", "Bonnie M Stark file"]}},
            mapping=m)
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
