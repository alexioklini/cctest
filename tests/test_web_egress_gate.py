"""Web-Egress-Gate (L4 Phase 1) — Sicherheits- und FP-Tests.

Das Gate (`brain._gdpr_guard_web_args`, aufgerufen in
`engine/llm_loop.py:dispatch_tool`) verweigert in anonymisierenden Sessions
Web-Tool-Calls, deren Args geschützte Werte (Originale), Pseudonyme/Tokens
(Fakes) oder frische Personen-PII enthalten.

Die wichtigsten Invarianten (PII_ANALYSIS_PARITY_HANDOVER.md §4):
  - SICHERHEIT: mit aktivem Mapping erreicht kein Klarwert das Netzwerk —
    auch nicht im URL-Slug (bizapedia.com/people/bonnie-stark.html).
  - Fakes werden in JEDEM Modus verweigert (eine Fake-Suche ist semantisch
    leer oder trifft echte Fremdpersonen → Gift-Evidenz).
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

    def test_fake_refused_even_in_allow_mode(self):
        self._set_mode("allow")
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._guard("searxng_search",
                          {"query": "Erika Muster obituary"}, mapping=m)
        self.assertIsNotNone(out)


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

    def test_block_group_refuses_at_dispatch_too(self):
        """Defense in depth: auch wenn exclude_tools den Call eigentlich
        verhindert, verweigert der Dispatch-Gate zusätzlich."""
        self._set_mode("block_group")
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard("searxng_search",
                          {"query": "Bonnie M Stark"}, mapping=m)
        self.assertIsNotNone(out)

    def test_ask_mode_behaves_like_refuse_phase1(self):
        self._set_mode("ask")
        m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
        out = self._guard("searxng_search",
                          {"query": "Bonnie M Stark"}, mapping=m)
        self.assertIsNotNone(out)


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


class _AskModeBase(_GateTestBase):
    """L4 Phase 2 (ask mode): per-value consent, release/deny ledger,
    fake→original translation for the outgoing request only."""

    SID = "web-egress-ask-test"

    def setUp(self):
        super().setUp()
        from server_lib.db import ChatDB
        self._ChatDB = ChatDB
        self._orig_decisions = ChatDB.get_session_pii_decisions
        self._orig_releases = ChatDB.get_session_web_releases
        self._orig_record = ChatDB.record_pii_decisions
        self.recorded = []   # (turn_action, decisions)
        ChatDB.get_session_pii_decisions = staticmethod(lambda sid: {})
        ChatDB.get_session_web_releases = staticmethod(lambda sid: {})
        ChatDB.record_pii_decisions = staticmethod(
            lambda sid, uid, tid, ta, ds: self.recorded.append((ta, ds)) or len(ds))
        self._set_mode("ask")

    def tearDown(self):
        self._ChatDB.get_session_pii_decisions = self._orig_decisions
        self._ChatDB.get_session_web_releases = self._orig_releases
        self._ChatDB.record_pii_decisions = self._orig_record
        super().tearDown()

    def _set_releases(self, *entries):
        """entries: (value, rule_id, status)."""
        rel = {brain._web_release_hash(v): {"value": v, "rule_id": rid,
                                            "status": st, "created_at": 0,
                                            "user_id": "u1"}
               for v, rid, st in entries}
        self._ChatDB.get_session_web_releases = staticmethod(lambda sid: rel)

    def _guard_ask(self, tool, args, mapping, cb=None):
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = mapping.mapping_id if mapping else ""
            ctx.current_session_id = self.SID
            ctx.current_user_id = "u1"
            if cb is not None:
                ctx.event_callback = cb
            return brain._gdpr_guard_web_args(tool, args)

    def _answering_cb(self, option, seen):
        """Event-callback stub: answers the consent dialog synchronously with
        `option` for every question (the pending slot is registered BEFORE the
        emit, so a synchronous deliver is race-free)."""
        def cb(event_type, payload):
            seen.append((event_type, payload))
            if event_type != "user_input_needed":
                return
            answers = {q["question"]: option
                       for q in (payload.get("questions") or [])}
            self.assertTrue(
                brain.deliver_ask_user_answer(self.SID, answers=answers))
        return cb


class TestAskModeReleases(_AskModeBase):
    def test_released_original_passes(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        self._set_releases(("Bonnie M Stark", "name", "released"))
        out, args = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}, m)
        self.assertIsNone(out)
        self.assertEqual(args["query"], "Bonnie M Stark Oregon City")

    def test_released_covers_registered_variant(self):
        """Die Freigabe von 'Bonnie M Stark' deckt die L2-registrierte
        Variante 'Bonnie Stark' (Variant-Intersection über den
        first+last-Slug 'bonnie-stark')."""
        m = self._mapping(("Bonnie M Stark", "Erika M Muster", "name"),
                          ("Bonnie Stark", "Erika Muster", "name"))
        self._set_releases(("Bonnie M Stark", "name", "released"))
        out, _ = self._guard_ask(
            "searxng_search", {"query": "Bonnie Stark obituary"}, m)
        self.assertIsNone(out)

    def test_denied_value_refused_without_dialog(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        self._set_releases(("Bonnie M Stark", "name", "denied"))
        seen = []
        out, args = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}, m,
            cb=self._answering_cb("Freigeben (für diese Sitzung)", seen))
        self.assertIsNotNone(out)
        data = json.loads(out)
        self.assertIn("verweigert", data["hint"])
        self.assertEqual(seen, [])  # kein Dialog für verweigerte Werte

    def test_released_fake_translated_for_dispatch_only(self):
        """Step (c): Modell sucht mit dem Fake → ausgehende Args tragen das
        Original (auch als URL-Slug); die Eingabe-Args bleiben unverändert."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        self._set_releases(("Bonnie M Stark", "name", "released"))
        query_args = {"query": "Erika Muster Oregon City obituary"}
        out, args = self._guard_ask("searxng_search", query_args, m)
        self.assertIsNone(out)
        self.assertIn("Bonnie M Stark", args["query"])
        self.assertNotIn("Erika Muster", args["query"])
        # Eingabestruktur (Wire) unangetastet
        self.assertIn("Erika Muster", query_args["query"])
        # URL-Slug-Form des Fakes
        out, args = self._guard_ask(
            "web_fetch",
            {"url": "https://www.bizapedia.com/people/erika-muster.html"}, m)
        self.assertIsNone(out)
        self.assertNotIn("erika-muster", args["url"])
        self.assertIn("bonnie", args["url"])

    def test_unreleased_fake_never_translated(self):
        """Sicherheits-Negativtest: ohne Freigabe wird NIE übersetzt — der
        Refusal trägt die unveränderten Args (Fake bleibt Fake)."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out, args = self._guard_ask(
            "searxng_search", {"query": "Erika Muster Oregon City"}, m)
        self.assertIsNotNone(out)
        self.assertNotIn("Bonnie", args["query"])

    def test_non_interactive_refuses_no_consent(self):
        """Background/Scheduler (kein event_callback) kann nicht fragen →
        refuse, nichts wird persistiert."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out, _ = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark"}, m)
        self.assertIsNotNone(out)
        self.assertIn("nicht möglich", json.loads(out)["hint"])
        self.assertEqual(self.recorded, [])


class TestAskModeConsentDialog(_AskModeBase):
    def test_consent_granted_records_and_passes(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        seen = []
        out, args = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}, m,
            cb=self._answering_cb("Freigeben (für diese Sitzung)", seen))
        self.assertIsNone(out)
        # Dialog wurde emittiert und beantwortet
        kinds = [e for e, _ in seen]
        self.assertIn("user_input_needed", kinds)
        self.assertIn("user_input_received", kinds)
        # Wert im Dialog sichtbar (der User ist Dateneigner), Ledger-Zeile da
        payload = seen[0][1]
        self.assertIn("Bonnie M Stark", payload["questions"][0]["question"])
        self.assertEqual(len(self.recorded), 1)
        action, ds = self.recorded[0]
        self.assertEqual(action, "release_web")
        self.assertEqual(ds[0]["value"], "Bonnie M Stark")
        self.assertEqual(ds[0]["value_hash"],
                         brain._web_release_hash("Bonnie M Stark"))

    def test_consent_granted_translates_matched_fake(self):
        """Konsent auf Basis einer FAKE-Query: Grant übersetzt den Fake im
        ausgehenden Request auf das Original."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        seen = []
        out, args = self._guard_ask(
            "searxng_search", {"query": "Erika Muster Oregon City"}, m,
            cb=self._answering_cb("Freigeben (für diese Sitzung)", seen))
        self.assertIsNone(out)
        self.assertIn("Bonnie M Stark", args["query"])
        self.assertNotIn("Erika Muster", args["query"])

    def test_consent_denied_records_and_refuses(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        seen = []
        out, args = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}, m,
            cb=self._answering_cb("Nicht freigeben", seen))
        self.assertIsNotNone(out)
        self.assertIn("verweigert", json.loads(out)["hint"])
        self.assertEqual(self.recorded[0][0], "deny_web")
        # Args unangetastet (keine Übersetzung bei Verweigerung)
        self.assertEqual(args["query"], "Bonnie M Stark Oregon City")

    def test_consent_timeout_refuses_and_no_second_dialog(self):
        """Timeout: refuse ohne Ledger-Zeile; im SELBEN Turn kein zweites
        Modal (asked-Set auf dem RequestContext)."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        orig_timeout = brain._WEB_CONSENT_TIMEOUT_S
        brain._WEB_CONSENT_TIMEOUT_S = 1
        seen = []

        def silent_cb(event_type, payload):
            seen.append((event_type, payload))

        try:
            with request_context():
                ctx = get_request_context()
                ctx._gdpr_mapping_id = m.mapping_id
                ctx.current_session_id = self.SID
                ctx.event_callback = silent_cb
                out1, _ = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "Bonnie M Stark"})
                out2, _ = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "Bonnie M Stark again"})
        finally:
            brain._WEB_CONSENT_TIMEOUT_S = orig_timeout
        self.assertIsNotNone(out1)
        self.assertIsNotNone(out2)
        self.assertEqual(self.recorded, [])
        dialogs = [e for e, _ in seen if e == "user_input_needed"]
        self.assertEqual(len(dialogs), 1)  # nur EIN Modal pro Turn

    def test_partial_release_denied_dob_refuses(self):
        """Teilfreigabe (§4.3e): Name released, DOB denied → die kombinierte
        Query wird refused (deny gewinnt), der Hinweis leitet zum
        Umformulieren an."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"),
                          ("05.02.1947", "17.02.1947", "dob"))
        self._set_releases(("Bonnie M Stark", "name", "released"),
                           ("05.02.1947", "dob", "denied"))
        out, _ = self._guard_ask(
            "searxng_search",
            {"query": "Bonnie M Stark born 05.02.1947"}, m)
        self.assertIsNotNone(out)
        self.assertIn("verweigert", json.loads(out)["hint"])
        # Nur der Name released → Query ohne DOB läuft durch
        out2, _ = self._guard_ask(
            "searxng_search", {"query": "Bonnie M Stark Oregon City"}, m)
        self.assertIsNone(out2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
