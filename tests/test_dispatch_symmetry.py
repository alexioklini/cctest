"""Dispatch-Symmetrie (L3) — Args-Deanonymisierung + Results-Seams + Notice-Split.

L3 macht die Tool-Grenze symmetrisch: das Modell denkt in Fakes, die Tools
arbeiten auf Rohdaten — ohne dass eines vom anderen weiß.

Die wichtigsten Invarianten (PII_ANALYSIS_PARITY_HANDOVER.md L3):
  - SICHERHEIT (Negativtest): Web-Tools stehen NIEMALS in der Deanon-Whitelist
    — Args-Deanon für web_fetch/searxng/exa wäre stiller Egress.
  - Reihenfolge am Dispatch: erst Web-Gate, dann Deanon (der Gate prüft die
    Args des Modells, nie rückübersetzte).
  - execute_command/python_exec: Strings mit Netzwerk-Markern (curl, https://,
    urllib …) behalten ihre Fakes — sonst Egress durch die Seitentür.
  - L3c: der Ledger-Rewrite zerschreibt die Attachment-Notice (Pfade!) nicht.

Run: python3 -m unittest tests.test_dispatch_symmetry -v
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


class _SymmetryTestBase(unittest.TestCase):
    def setUp(self):
        self._mappings = []

    def tearDown(self):
        for m in self._mappings:
            pseudonymizer.close_mapping(m.mapping_id)

    def _mapping(self, *entries):
        m = _mk_mapping(*entries)
        self._mappings.append(m)
        return m

    def _deanon(self, tool, args, mapping=None):
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = mapping.mapping_id if mapping else ""
            return brain._gdpr_deanon_tool_args(tool, args)


class TestWhitelistInvariant(unittest.TestCase):
    def test_no_web_tool_in_deanon_whitelist(self):
        """DIE L3-Kerninvariante: Deanon-Whitelist ∩ Web-Tools = ∅."""
        overlap = set(brain.WEB_SEARCH_TOOLS) & set(brain.GDPR_ARGS_DEANON_TOOLS)
        self.assertEqual(overlap, set(),
                         f"Web-Tools in der Args-Deanon-Whitelist: {overlap} "
                         "— das wäre stiller PII-Egress!")

    def test_whitelisted_tools_exist_in_dispatch(self):
        """Whitelist-Namen müssen echte Tools sein (Tippfehler-Schutz)."""
        for name in brain.GDPR_ARGS_DEANON_TOOLS:
            self.assertIn(name, brain.TOOL_DISPATCH,
                          f"{name} steht in GDPR_ARGS_DEANON_TOOLS, aber "
                          "nicht in TOOL_DISPATCH")


class TestArgsDeanon(_SymmetryTestBase):
    def test_deanonymises_whitelisted_tool_args(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._deanon("mempalace_query",
                           {"query": "Erika Muster KO Kunde"}, mapping=m)
        self.assertEqual(out["query"], "Bonnie M Stark KO Kunde")

    def test_deanonymises_nested_args(self):
        m = self._mapping(("107625", "888417", "bare_identifier"))
        out = self._deanon("identity_consistency",
                           {"sources": [{"path": "/x/CF_888417.pdf"},
                                        {"path": "/x/other.pdf"}]},
                           mapping=m)
        self.assertEqual(out["sources"][0]["path"], "/x/CF_107625.pdf")
        self.assertEqual(out["sources"][1]["path"], "/x/other.pdf")

    def test_original_args_not_mutated(self):
        """Wire/History behalten die Fakes: Deanon liefert eine NEUE Struktur."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        args = {"query": "Erika Muster"}
        out = self._deanon("mempalace_query", args, mapping=m)
        self.assertEqual(args["query"], "Erika Muster")
        self.assertEqual(out["query"], "Bonnie M Stark")

    def test_web_tools_never_deanonymised(self):
        """Sicherheits-Negativtest: Fake im Web-Arg bleibt Fake."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        for tool in brain.WEB_SEARCH_TOOLS:
            args = {"query": "Erika Muster obituary",
                    "url": "https://x.test/Erika Muster"}
            out = self._deanon(tool, args, mapping=m)
            self.assertEqual(out, args, f"{tool}: Args wurden verändert!")
            self.assertNotIn("Bonnie", json.dumps(out),
                             f"{tool}: Klarwert im Web-Arg — Egress!")

    def test_no_mapping_no_change(self):
        out = self._deanon("read_document", {"path": "/x/Erika Muster.pdf"})
        self.assertEqual(out, {"path": "/x/Erika Muster.pdf"})

    def test_execute_command_network_marker_keeps_fakes(self):
        """curl/https im Command → Fakes bleiben (kein Seitentür-Egress)."""
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._deanon(
            "execute_command",
            {"command": 'curl "https://google.com/search?q=Erika Muster"'},
            mapping=m)
        self.assertIn("Erika Muster", out["command"])
        self.assertNotIn("Bonnie", out["command"])

    def test_execute_command_local_deanonymised(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._deanon(
            "execute_command",
            {"command": 'grep -r "Erika Muster" /tmp/brain-attachments/'},
            mapping=m)
        self.assertIn("Bonnie M Stark", out["command"])

    def test_python_exec_network_marker_keeps_fakes(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._deanon(
            "python_exec",
            {"code": 'import urllib.request\nq = "Erika Muster"'},
            mapping=m)
        self.assertIn("Erika Muster", out["code"])

    def test_python_exec_local_deanonymised(self):
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        out = self._deanon(
            "python_exec",
            {"code": 'name = "Erika Muster"\nprint(name in open("/tmp/x").read())'},
            mapping=m)
        self.assertIn("Bonnie M Stark", out["code"])


class TestDispatchOrder(_SymmetryTestBase):
    def test_web_gate_fires_before_deanon(self):
        """Ein Web-Call mit Fake wird am Gate refused — die Args erreichen
        weder Deanon noch das Tool. Modus 'refuse' explizit gesetzt: seit dem
        allow-Fake→Original-Release (Chat faa124e1) würde 'allow' den Fake
        stattdessen übersetzen; dieser Test prüft die DISPATCH-REIHENFOLGE
        (Gate vor Deanon), nicht die allow-Policy, also den refusenden Modus
        fixieren statt vom Live-web_egress abhängen."""
        from engine import llm_loop
        _orig_cfg = brain._get_gdpr_scanner_config
        _cfg = dict(_orig_cfg())
        _cfg["web_egress"] = "refuse"
        brain._get_gdpr_scanner_config = lambda: _cfg
        try:
            m = self._mapping(("Bonnie M Stark", "<PERSON_1_ab12>", "name"))
            with request_context():
                ctx = get_request_context()
                ctx._gdpr_mapping_id = m.mapping_id
                ctx.current_session_id = None
                result, is_error = llm_loop.dispatch_tool(
                    "searxng_search", {"query": "<PERSON_1_ab12> obituary"})
        finally:
            brain._get_gdpr_scanner_config = _orig_cfg
        self.assertTrue(is_error)
        parsed = json.loads(result)
        self.assertEqual(parsed.get("error"), "web_query_blocked_pii")
        self.assertNotIn("Bonnie", result)


class TestWebResultAnon(unittest.TestCase):
    """Wrapper-Mechanik von _web_result_anon (Scanner via Monkeypatch —
    deterministisch, unabhängig von der Maschinen-Config)."""

    def setUp(self):
        self._orig = brain._gdpr_anon_tool_text

    def tearDown(self):
        brain._gdpr_anon_tool_text = self._orig

    def test_content_field_anonymised_json_preserved(self):
        from engine.tools import misc_tools
        brain._gdpr_anon_tool_text = lambda text, source: text.replace(
            "Bonnie M Stark", "Erika Muster")
        payload = json.dumps({"url": "https://x.test/p", "status": 200,
                              "content": "Bonnie M Stark, age 79, Oregon City"})
        out = misc_tools._web_result_anon(payload, "web_fetch:test")
        parsed = json.loads(out)
        self.assertNotIn("Bonnie", parsed["content"])
        self.assertIn("Erika Muster", parsed["content"])
        self.assertEqual(parsed["url"], "https://x.test/p")
        self.assertEqual(parsed["status"], 200)

    def test_non_json_and_error_payloads_pass_through(self):
        from engine.tools import misc_tools
        called = []
        brain._gdpr_anon_tool_text = lambda text, source: called.append(1) or text
        self.assertEqual(misc_tools._web_result_anon("not json", "s"), "not json")
        err = json.dumps({"error": "web_fetch: HTTP 404"})
        self.assertEqual(misc_tools._web_result_anon(err, "s"), err)
        self.assertEqual(called, [])  # kein Scan ohne content-Feld


class TestLedgerNoticeSplit(unittest.TestCase):
    """L3c: _apply_pii_decisions_to_wire darf die Attachment-Notice
    (Dateipfade!) nicht zerschreiben — nur den getippten Teil."""

    def _decisions(self):
        return {"h1": {"value": "107625", "fake_value": "888417",
                       "rule_id": "bare_identifier",
                       "turn_action": "anonymise", "false_positive": False}}

    def test_notice_paths_survive_ledger_rewrite(self):
        from handlers.chat import _apply_pii_decisions_to_wire
        text = ("Prüfe Kunde 107625 bitte."
                "\n\n[User attached files saved to disk: "
                "/tmp/brain-attachments/x/CF_STARK_107625.pdf]")
        wire, replaced, counts = _apply_pii_decisions_to_wire(
            [{"role": "user", "content": text}], self._decisions())
        out = wire[0]["content"]
        self.assertIn("Kunde 888417", out)                    # getippter Teil ersetzt
        self.assertIn("CF_STARK_107625.pdf", out)             # Pfad unangetastet
        self.assertEqual(replaced, 1)
        self.assertEqual(counts, {"bare_identifier": 1})

    def test_block_content_notice_also_exempt(self):
        from handlers.chat import _apply_pii_decisions_to_wire
        blocks = [{"type": "text", "text":
                   "Kunde 107625\n\n[User attached image(s): "
                   "/tmp/brain-attachments/x/107625.jpg]"}]
        wire, replaced, _ = _apply_pii_decisions_to_wire(
            [{"role": "user", "content": blocks}], self._decisions())
        out = wire[0]["content"][0]["text"]
        self.assertIn("Kunde 888417", out)
        self.assertIn("107625.jpg", out)


class TestDisplayResultDeanon(_SymmetryTestBase):
    """The DISPLAY-only result de-anon (activity-panel / chat result box).

    The function itself just reverses under an active mapping; the CALLER
    (engine/llm_loop) decides WHEN by gating on whether the tool actually ran on
    real values (`_deanon_args is not None`). So the check is: a mapping + a
    changed result → de-anon; no mapping / no change / oversize → None.
    """

    def _display(self, result_str, mapping):
        from engine.llm_loop import _gdpr_deanon_result_for_display
        with request_context():
            get_request_context()._gdpr_mapping_id = mapping.mapping_id
            return _gdpr_deanon_result_for_display(result_str)

    def test_result_is_deanonymised_under_mapping(self):
        m = self._mapping(("Bonnie Stark", "Logan Carter", "name"))
        out = self._display('{"query":"Logan Carter","text":"Logan Carter"}', m)
        self.assertEqual(out, '{"query":"Bonnie Stark","text":"Bonnie Stark"}')

    def test_no_mapping_returns_none(self):
        from engine.llm_loop import _gdpr_deanon_result_for_display
        with request_context():
            get_request_context()._gdpr_mapping_id = ""
            self.assertIsNone(_gdpr_deanon_result_for_display(
                '{"x":"Logan Carter"}'))

    def test_unchanged_result_returns_none(self):
        m = self._mapping(("Bonnie Stark", "Logan Carter", "name"))
        # Nothing to reverse → None (no redundant field shipped).
        self.assertIsNone(self._display('{"x":"nothing to reverse here"}', m))


class TestDispatchedArgsStash(_SymmetryTestBase):
    """dispatch_tool records the REAL args a tool ran on (`_gdpr_dispatched_args`)
    for uniform display — local tools always, web tools when allow-mode
    translated their args to originals; in refuse mode a protected web query is
    blocked (no dispatched args)."""

    def test_local_tool_stashes_real_args(self):
        import engine
        from engine.llm_loop import dispatch_tool
        m = self._mapping(("Bonnie M Stark", "Erika Muster", "name"))
        with request_context():
            get_request_context()._gdpr_mapping_id = m.mapping_id
            # mempalace_query is local + in the deanon whitelist.
            try:
                dispatch_tool("mempalace_query", {"query": "Erika Muster KYC"})
            except Exception:
                pass  # the tool impl may error in a bare test ctx — we only
                      # care that the stash was set before the call ran.
            disp = get_request_context()._gdpr_dispatched_args
        self.assertIsNotNone(disp)
        self.assertEqual(disp.get("query"), "Bonnie M Stark KYC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
