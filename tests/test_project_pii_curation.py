"""Option 3 (v9.400.0) — Projekt-weite PII-Vorentscheidung.

Einmal pro Projekt kuratieren statt pro Sitzung fragen: der inkrementelle
Scan über den gemineten Text-Korpus aggregiert Kandidaten als 'open'-Zeilen
in `project_pii_decisions`; kuratierte Werte (anonymise/fp, gelten für ALLE
Projekt-Nutzer) werden vom Retrieval-Guard OHNE Dialog honoriert — nur
unentschiedene Werte fragen weiter. Diese Suite pinnt:

  1. DB: Upsert-Semantik (neu=open, entschiedene behalten Status + mergen
     Quellen), Bulk-Decide, decided-Map ohne open-Zeilen.
  2. Scan: Korpus-Walker + inkrementeller sha1-Cursor (unverändert = skip),
     Kandidaten landen als open.
  3. Guard: project-anonymise → stiller Seed (kein Dialog); project-fp →
     Klartext (kein Dialog); open → Dialog wie bisher; Session-Entscheidung
     schlägt Projekt-Entscheidung; Norm-Brücke über JSON-Escapes
     (Projekt-Scan sieht echte Zeilenumbrüche, Runtime literalen \\n).

Run: python3 -m unittest tests.test_project_pii_curation -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as ps  # noqa: E402
from engine.context import get_request_context, request_context  # noqa: E402
from server_lib.db import ChatDB  # noqa: E402

IBAN = "DE89370400440532013000"
IBAN2 = "DE02120300000000202051"
CFG_ON = {"enabled": True}


class _ProjBase(unittest.TestCase):
    def setUp(self):
        self.pid = f"testpid{uuid.uuid4().hex[:10]}"
        self.sid = f"test-projpii-{uuid.uuid4().hex[:12]}"
        self.mapping = ps.new_mapping()
        self._orig_cfg = brain._get_gdpr_scanner_config
        brain._get_gdpr_scanner_config = lambda: dict(CFG_ON)

    def tearDown(self):
        brain._get_gdpr_scanner_config = self._orig_cfg
        for fn in (lambda: ps.close_mapping(self.mapping.mapping_id),
                   lambda: ps.delete_persisted_mapping(self.mapping.mapping_id),
                   lambda: ChatDB.delete_project_pii(self.pid),
                   lambda: ChatDB.delete_session_pii_decisions(self.sid)):
            try:
                fn()
            except Exception:
                pass


class TestLedgerDb(_ProjBase):
    def test_upsert_new_open_and_decided_keeps_status(self):
        n = ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": "max muster", "raw_value": "Max Muster",
             "rule_id": "name", "occurrences": 3, "source_files": ["a.md"]}])
        self.assertEqual(n, 1)
        rows = ChatDB.get_project_pii_rows(self.pid)
        self.assertEqual(rows[0]["status"], "open")
        ChatDB.decide_project_pii(self.pid, [
            {"id": rows[0]["id"], "status": "anonymise"}], "u1")
        # Re-Scan: gleicher Wert aus neuer Datei → Status bleibt, Quellen mergen.
        n2 = ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": "max muster", "raw_value": "Max Muster",
             "rule_id": "name", "occurrences": 5, "source_files": ["b.md"]}])
        self.assertEqual(n2, 0)
        row = ChatDB.get_project_pii_rows(self.pid)[0]
        self.assertEqual(row["status"], "anonymise")
        self.assertEqual(sorted(row["source_files"]), ["a.md", "b.md"])
        self.assertEqual(row["occurrences"], 5)

    def test_decided_map_excludes_open(self):
        ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": "offen wert", "raw_value": "Offen Wert",
             "rule_id": "name", "occurrences": 1, "source_files": []},
            {"norm_value": "fp wert", "raw_value": "FP Wert",
             "rule_id": "name", "occurrences": 1, "source_files": []}])
        rows = {r["norm_value"]: r for r in ChatDB.get_project_pii_rows(self.pid)}
        ChatDB.decide_project_pii(self.pid, [
            {"id": rows["fp wert"]["id"], "status": "fp"}], "u1")
        m = ChatDB.get_project_pii_decided_map(self.pid)
        self.assertNotIn("offen wert", m)
        self.assertEqual(m["fp wert"]["status"], "fp")

    def test_reset_to_open(self):
        ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": "x wert", "raw_value": "X Wert",
             "rule_id": "name", "occurrences": 1, "source_files": []}])
        rid = ChatDB.get_project_pii_rows(self.pid)[0]["id"]
        ChatDB.decide_project_pii(self.pid, [{"id": rid, "status": "anonymise"}], "u1")
        ChatDB.decide_project_pii(self.pid, [{"id": rid, "status": "open"}], "u1")
        self.assertEqual(ChatDB.get_project_pii_decided_map(self.pid), {})


class TestScan(_ProjBase):
    def _patched_pm(self, pdir):
        proj = {"id": self.pid, "name": "TestProj", "input_folders": []}
        return (unittest.mock.patch.object(
                    brain.ProjectManager, "get_project",
                    staticmethod(lambda a, n: proj)),
                unittest.mock.patch.object(
                    brain.ProjectManager, "_project_dir",
                    staticmethod(lambda a, n: pdir)))

    def test_scan_creates_open_candidates_and_cursor_skips(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as pdir:
            with open(os.path.join(pdir, "doc.md"), "w") as f:
                f.write(f"Überweisung an {IBAN} ist freigegeben.\n")
            p1, p2 = self._patched_pm(pdir)
            with p1, p2:
                st = brain.project_pii_scan("main", "TestProj")
                self.assertFalse(st.get("error"))
                self.assertEqual(st.get("new_candidates"), 1)
                rows = ChatDB.get_project_pii_rows(self.pid)
                self.assertEqual(rows[0]["status"], "open")
                self.assertEqual(rows[0]["raw_value"], IBAN)
                # Unveränderter Re-Scan: Cursor greift, nichts Neues.
                st2 = brain.project_pii_scan("main", "TestProj")
                self.assertEqual(st2.get("new_candidates"), 0)
                # Neue Datei → neuer Kandidat.
                with open(os.path.join(pdir, "doc2.md"), "w") as f:
                    f.write(f"Zweites Konto {IBAN2}.\n")
                st3 = brain.project_pii_scan("main", "TestProj")
                self.assertEqual(st3.get("new_candidates"), 1)


class _GuardProjBase(_ProjBase):
    """Guard-Tests: Request-Kontext mit Projekt + gepatchtem ProjectManager."""

    def _ctx_run(self, fn, *, event_callback=None):
        import unittest.mock
        proj = {"id": self.pid, "name": "TestProj"}
        with unittest.mock.patch.object(
                brain.ProjectManager, "get_project",
                staticmethod(lambda a, n: proj)):
            with request_context():
                ctx = get_request_context()
                ctx.current_session_id = self.sid
                ctx._gdpr_mapping_id = self.mapping.mapping_id
                ctx.event_callback = event_callback
                ctx.project = "TestProj"
                return fn()

    def _decide(self, norm, raw, status, rule_id="iban"):
        ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": norm, "raw_value": raw, "rule_id": rule_id,
             "occurrences": 1, "source_files": ["doc.md"]}])
        rows = {r["norm_value"]: r for r in ChatDB.get_project_pii_rows(self.pid)}
        ChatDB.decide_project_pii(self.pid, [
            {"id": rows[norm]["id"], "status": status}], "u1")


class TestGuardHonoursProject(_GuardProjBase):
    def test_project_anonymise_seeds_without_dialog(self):
        self._decide(brain._retrieval_pii_norm(IBAN), IBAN, "anonymise")
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(
                f"Konto {IBAN} laut Akte", "mempalace_query"),
            event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertIn(IBAN, self.mapping.forward)
        self.assertIn(self.mapping.forward[IBAN], out)
        self.assertNotIn(IBAN, out)
        # Session-Ledger-Write-through mit projekt-Disposition.
        rows = [d for d in ChatDB.get_session_pii_decisions(self.sid).values()
                if d.get("value") == IBAN]
        self.assertTrue(rows)

    def test_project_fp_stays_clear_without_dialog(self):
        self._decide(brain._retrieval_pii_norm(IBAN), IBAN, "fp")
        text = f"Vereins-IBAN {IBAN} (öffentlich)"
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertEqual(out, text)
        self.assertNotIn(IBAN, self.mapping.forward)

    def test_open_value_still_asks(self):
        # 'open' = unentschieden → Dialog feuert wie ohne Projekt-Ledger.
        ChatDB.upsert_project_pii_candidates(self.pid, [
            {"norm_value": brain._retrieval_pii_norm(IBAN), "raw_value": IBAN,
             "rule_id": "iban", "occurrences": 1, "source_files": []}])
        calls = []

        def cb(event_type, payload):
            if event_type != "user_input_needed":
                return
            calls.append(payload)
            answers = {q["question"]: ("Anonymisiert fortfahren"
                                       if q["question"] == brain._RETRIEVAL_PII_TURN_Q
                                       else "Anonymisieren")
                       for q in payload.get("questions") or []}
            brain.deliver_ask_user_answer(self.sid, answers=answers)
        self._ctx_run(lambda: brain._gdpr_anon_tool_text(
            f"Konto {IBAN}", "mempalace_query"), event_callback=cb)
        self.assertEqual(len(calls), 1, "open → Dialog wie bisher")

    def test_session_decision_beats_project(self):
        # Session-FP (jüngere, spezifischere Nutzer-Absicht) schlägt
        # Projekt-anonymise: der Wert bleibt Klartext.
        self._decide(brain._retrieval_pii_norm(IBAN), IBAN, "anonymise")
        ChatDB.record_pii_decisions(
            self.sid, "", "", "anonymise",
            [{"rule_id": "iban", "value": IBAN, "false_positive": True,
              "source": "test"}])
        text = f"Konto {IBAN}"
        out = self._ctx_run(
            lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
            event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertEqual(out, text)
        self.assertNotIn(IBAN, self.mapping.forward)

    def test_norm_bridges_json_escapes(self):
        # Projekt-Scan sah "Max Muster" über echten Zeilenumbruch (→ Leerzeichen);
        # Runtime-Text trägt den literalen \n-Escape. Die Norm-Brücke matcht,
        # geseedet wird die RUNTIME-Oberflächenform (nur die steht im Text).
        import unittest.mock  # noqa: F401
        self._decide("max muster", "Max Muster", "anonymise", rule_id="name")
        runtime_val = "Max\\nMuster"
        self.assertEqual(brain._retrieval_pii_norm(runtime_val), "max muster")
        fake_finding = [{"rule_id": "name", "start": 12,
                         "end": 12 + len(runtime_val), "action": "warn"}]
        text = f'{{"drawer": "{runtime_val} ist zuständig"}}'
        self.assertEqual(text[12:12 + len(runtime_val)], runtime_val)
        with unittest.mock.patch.object(brain, "_pii_scan_text",
                                        lambda t, **kw: (fake_finding
                                                         if runtime_val in t
                                                         else [])):
            out = self._ctx_run(
                lambda: brain._gdpr_anon_tool_text(text, "mempalace_query"),
                event_callback=lambda *_: self.fail("kein Dialog erwartet"))
        self.assertIn(runtime_val, self.mapping.forward)
        self.assertNotIn(runtime_val, out)


if __name__ == "__main__":
    unittest.main()
