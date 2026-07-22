"""PROJECT_RETRIEVAL_PII_DIALOG_PLAN — Mid-Turn-PII-Dialog bei Projekt-Retrieval.

Projekt-Retrieval (MemPalace/Wiki/KG) liefert ROHES Wissen; der Result-Seam
war apply-only und hatte in Projekt-Chats keinen Seed → PII ging roh ans
Cloud-LLM (Test-2-Befund 2026-07-22). Diese Suite pinnt den neuen Guard
`brain._gdpr_retrieval_guard` (frischer Scan NUR für Retrieval-Quellen +
EIN blockierender Batch-Dialog pro Turn):

  1. Neue, unentschiedene Werte → Dialog; Wahl »Anonymisieren« seedet das
     aktive Mapping (dieselben Fakes wie der Pre-Send-Pfad) + schreibt
     pii_decisions-Zeilen; die bestehenden Apply-Sweeps faken den Wert im
     selben Ergebnis.
  2. Bereits entschiedene Werte (Ledger) und bereits gemappte Werte fragen
     NIE erneut.
  3. FP-Gates (rule_overrides ignore etc.) laufen im Scan — ignorierte Regeln
     erreichen den Dialog nicht.
  4. Nicht-interaktive Turns (kein event_callback) → fail-closed: Inhalt wird
     zurückgehalten, nie still durchgereicht.
  5. Timeout → asked-Set: derselbe Turn refust ohne zweites Modal.
  6. »Falschtreffer« → Klartext + false_positive-Zeile; »Abbrechen«/»Lokal« →
     Refusal + (bei Lokal) Kontext-Flags für den Worker-Restart.
  7. Roundtrip: retrieval-geseedete Fakes werden vom args-deanon (L3a)
     zurückübersetzt (read_document auf read_path funktioniert).

Run: python3 -m unittest tests.test_gdpr_retrieval_dialog -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as ps  # noqa: E402
from engine.context import get_request_context, request_context  # noqa: E402
from server_lib.db import ChatDB  # noqa: E402

IBAN = "DE89370400440532013000"
# Minimal-Config: Scanner an, Default-Kategorie-Aktionen (financial=warn →
# IBAN triggert; contact=ignore bleibt wie in Produktion).
CFG_ON = {"enabled": True}


class _GuardTestBase(unittest.TestCase):
    def setUp(self):
        self.sid = f"test-retrieval-pii-{uuid.uuid4().hex[:12]}"
        self.mapping = ps.new_mapping()
        self._orig_cfg = brain._get_gdpr_scanner_config
        brain._get_gdpr_scanner_config = lambda: dict(CFG_ON)

    def tearDown(self):
        brain._get_gdpr_scanner_config = self._orig_cfg
        try:
            ps.close_mapping(self.mapping.mapping_id)
        except Exception:
            pass
        try:
            ps.delete_persisted_mapping(self.mapping.mapping_id)
        except Exception:
            pass
        try:
            ChatDB.delete_session_pii_decisions(self.sid)
        except Exception:
            pass

    def _ctx_run(self, fn, *, event_callback=None, bg_task=False):
        """Run fn() inside a fresh request context wired for the guard."""
        with request_context():
            ctx = get_request_context()
            ctx.current_session_id = self.sid
            ctx._gdpr_mapping_id = self.mapping.mapping_id
            ctx.event_callback = event_callback
            ctx.current_bg_task = bg_task
            return fn()

    def _answering_cb(self, per_value_option, turn_option, calls):
        """Event-callback that answers the dialog synchronously (the slot is
        registered BEFORE the emit, so delivering inside the emit is the
        race-free fast path)."""
        def cb(event_type, payload):
            if event_type != "user_input_needed":
                return
            calls.append(payload)
            answers = {}
            for q in payload.get("questions") or []:
                if q["question"] == brain._RETRIEVAL_PII_TURN_Q:
                    answers[q["question"]] = turn_option
                else:
                    answers[q["question"]] = per_value_option
            brain.deliver_ask_user_answer(self.sid, answers=answers)
        return cb


class TestDialogSeedsMapping(_GuardTestBase):
    """Neuer Wert → Dialog → »Anonymisieren« → Mapping-Seed + Ledger + Fake."""

    def test_anonymise_seeds_and_applies(self):
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        text = json.dumps({"drawers": [
            {"content": f"Überweisung an IBAN {IBAN} freigegeben"}]},
            ensure_ascii=False)
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=cb)
        self.assertEqual(len(calls), 1, "genau EIN Dialog")
        # Turn-weite Frage ist Teil des Batches.
        qs = [q["question"] for q in calls[0]["questions"]]
        self.assertIn(brain._RETRIEVAL_PII_TURN_Q, qs)
        # Mapping wurde geseedet, Ergebnis trägt den Fake statt des Originals.
        self.assertIn(IBAN, self.mapping.forward)
        fake = self.mapping.forward[IBAN]
        self.assertNotIn(IBAN, out)
        self.assertIn(fake, out)
        # Ledger-Zeile mit fake_value persistiert (session-sticky).
        rows = ChatDB.get_session_pii_decisions(self.sid)
        anon = [d for d in rows.values()
                if d.get("turn_action") == "anonymise"
                and d.get("value") == IBAN]
        self.assertTrue(anon and anon[0].get("fake_value") == fake)

    def test_roundtrip_args_deanon_translates_seeded_fake(self):
        # Schritt 4 des Plans: der L3a-args-deanon muss auch retrieval-
        # geseedete Fakes zurückübersetzen (read_document auf read_path).
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        text = f"Kontoauszug {IBAN} aus dem Projektordner"
        self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=cb)
        fake = self.mapping.forward[IBAN]

        def _roundtrip():
            args = brain._gdpr_deanon_tool_args(
                "read_document", {"path": f"/tmp/auszug_{fake}.pdf"})
            return args.get("path", "")
        real_path = self._ctx_run(_roundtrip)
        self.assertIn(IBAN, real_path)
        self.assertNotIn(fake, real_path)


class TestNoReAsk(_GuardTestBase):
    """Entschiedene/gemappte Werte fragen nie erneut."""

    def test_ledger_decided_value_skips_dialog(self):
        ChatDB.record_pii_decisions(
            self.sid, "", "", "send",
            [{"rule_id": "iban", "value": IBAN, "source": "message"}])
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        text = f"Zahlung an {IBAN}"
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=cb)
        self.assertEqual(calls, [], "entschiedener Wert → kein Dialog")
        self.assertEqual(out, text)  # Nutzer hatte Klartext akzeptiert

    def test_mapped_value_skips_dialog_and_applies(self):
        ps.seed_from_decision(self.mapping, "iban", IBAN)
        fake = self.mapping.forward[IBAN]
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"Neue Buchung {IBAN}", "mempalace_query"),
            event_callback=cb)
        self.assertEqual(calls, [], "gemappter Wert → kein Dialog")
        self.assertIn(fake, out)


class TestFpGates(_GuardTestBase):
    """Production-FP-Gates laufen im Scan — ignore erreicht den Dialog nie."""

    def test_rule_override_ignore_suppresses_dialog(self):
        brain._get_gdpr_scanner_config = lambda: {
            "enabled": True, "rule_overrides": {"iban": "ignore"}}
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        text = f"Konto {IBAN}"
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=cb)
        self.assertEqual(calls, [])
        self.assertEqual(out, text)

    def test_scanner_disabled_is_noop(self):
        brain._get_gdpr_scanner_config = lambda: {"enabled": False}
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"Konto {IBAN}", "mempalace_query"),
            event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertIn(IBAN, out)


class TestFailClosed(_GuardTestBase):
    """Background/Timeout: Inhalt zurückhalten, nie still durchreichen."""

    def test_background_turn_refuses(self):
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"Geheime IBAN {IBAN}", "mempalace_query"),
            event_callback=None)
        parsed = json.loads(out)
        self.assertEqual(parsed.get("error"), "retrieval_pii_withheld")
        self.assertNotIn(IBAN, out)

    def test_bg_task_refuses_even_with_callback(self):
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"IBAN {IBAN}", "mempalace_query"),
            event_callback=lambda *_: None, bg_task=True)
        self.assertEqual(json.loads(out).get("error"),
                         "retrieval_pii_withheld")

    def test_timeout_refuses_and_no_second_modal(self):
        orig_timeout = brain._RETRIEVAL_PII_TIMEOUT_S
        brain._RETRIEVAL_PII_TIMEOUT_S = 1.2
        try:
            calls = []

            def silent_cb(event_type, payload):
                if event_type == "user_input_needed":
                    calls.append(payload)  # nie beantworten → Timeout

            def _twice():
                t = f"IBAN {IBAN}"
                first = brain._gdpr_anon_tool_text(t, "mempalace_query")
                second = brain._gdpr_anon_tool_text(t, "mempalace_query")
                return first, second
            first, second = self._ctx_run(_twice, event_callback=silent_cb)
            self.assertEqual(json.loads(first).get("error"),
                             "retrieval_pii_withheld")
            self.assertEqual(json.loads(second).get("error"),
                             "retrieval_pii_withheld")
            self.assertEqual(len(calls), 1, "kein zweites Modal im selben Turn")
        finally:
            brain._RETRIEVAL_PII_TIMEOUT_S = orig_timeout


class TestUserChoices(_GuardTestBase):
    """Falschtreffer / Abbrechen / Lokal."""

    def test_fp_value_stays_clear_and_recorded(self):
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_FP,
                                brain._RETRIEVAL_PII_OPT_PROCEED, calls)
        text = f"Vereins-IBAN {IBAN} (öffentlich)"
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=cb)
        self.assertEqual(out, text, "FP bleibt Klartext")
        self.assertNotIn(IBAN, self.mapping.forward)
        rows = [d for d in ChatDB.get_session_pii_decisions(self.sid).values()
                if d.get("value") == IBAN]
        self.assertTrue(rows and rows[0].get("false_positive"))

    def test_cancel_refuses_and_persists_nothing(self):
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_CANCEL, calls)
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"IBAN {IBAN}", "mempalace_query"),
            event_callback=cb)
        self.assertEqual(json.loads(out).get("error"),
                         "retrieval_pii_withheld")
        self.assertNotIn(IBAN, self.mapping.forward)
        self.assertEqual(ChatDB.get_session_pii_decisions(self.sid), {})

    def test_local_sets_worker_flags_and_short_circuits(self):
        calls = []
        cb = self._answering_cb(brain._RETRIEVAL_PII_OPT_ANON,
                                brain._RETRIEVAL_PII_OPT_LOCAL, calls)

        def _run_then_again():
            first = brain._gdpr_anon_tool_text(
                f"IBAN {IBAN}", "mempalace_query")
            ctx = get_request_context()
            flags = (bool(ctx._dynamic.get("_retrieval_pii_local_switch")),
                     bool(ctx._dynamic.get("_retrieval_pii_all_local")))
            # Re-Run nach dem Lokal-Switch (gleicher Kontext): Guard ist
            # inaktiv, Rohwert geht durch (lokales Modell darf PII sehen).
            second = brain._gdpr_anon_tool_text(
                f"IBAN {IBAN}", "mempalace_query")
            return first, flags, second
        first, flags, second = self._ctx_run(_run_then_again,
                                             event_callback=cb)
        self.assertEqual(json.loads(first).get("error"),
                         "retrieval_pii_withheld")
        self.assertEqual(flags, (True, True))
        self.assertIn(IBAN, second)
        self.assertEqual(len(calls), 1)


class TestScopeAndLocality(_GuardTestBase):
    """Nur Retrieval-Quellen; lokale Modelle nie fragen."""

    def test_non_retrieval_source_untouched(self):
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"IBAN {IBAN}", "read_document:test"),
            event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertIn(IBAN, out)

    def test_local_model_never_asks(self):
        orig = brain.is_model_local
        brain.is_model_local = lambda m: True
        try:
            def _run():
                get_request_context()._current_model = "local-test-model"
                return brain._gdpr_anon_tool_text(
                    f"IBAN {IBAN}", "mempalace_query")
            out = self._ctx_run(
                _run,
                event_callback=lambda *_: self.fail("kein Dialog erwartet"))
            self.assertIn(IBAN, out)
        finally:
            brain.is_model_local = orig


if __name__ == "__main__":
    unittest.main()
