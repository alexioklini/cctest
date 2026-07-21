"""L6 — Report fidelity (handover F6: 'der Report lügt leise').

Covers the three L6 layers:

  * L6b linter — `pseudonymizer.lint_residual_fakes`: residual fake
    substance in FINAL (post-deanonymise) text: mangled tokens, exact
    reverse keys (docx run-splits on files), reformatted fake dates,
    declined/initialled fake names. Warning layer — recall over precision,
    but the negative cases (Starkstrom-style mid-word, foreign salt,
    generic placeholders) must never fire.
  * L6a/L6b file seam — `make_gdpr_after_file_write_cb`: reversible files
    get linted after the reverse walk (result carries `unrestored`);
    a .pdf (non-reversible) with fake substance emits a LOUD error row and
    queues a model-directed warning on the RequestContext, drained by
    `engine/llm_loop.dispatch_tool` into the tool result.
  * L6c clamp — the new report-fidelity + no-PDF instructions in
    `_GDPR_ANON_CLAMP`.

Run: python3 -m unittest tests.test_report_fidelity -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402


def _mk_mapping():
    m = ps.new_mapping()
    return m


class TestLintResidualFakes(unittest.TestCase):
    def setUp(self):
        self.m = _mk_mapping()

    def tearDown(self):
        ps.close_mapping(self.m.mapping_id)

    def test_clean_text_returns_empty(self):
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")
        out = ps.lint_residual_fakes(
            "Bonnie Stark wurde geprüft, alles konsistent.", mapping=self.m)
        self.assertEqual(out, [])

    def test_empty_mapping_or_text_noop(self):
        self.assertEqual(ps.lint_residual_fakes("", mapping=self.m), [])
        self.assertEqual(ps.lint_residual_fakes("text", mapping=self.m), [])

    def test_token_remnant_same_salt_flagged(self):
        tok = self.m.next_token("passport")
        self.m.record("560683707", tok, "passport")
        out = ps.lint_residual_fakes(f"Nummer: {tok}", mapping=self.m)
        self.assertEqual([f["reason"] for f in out], ["token_remnant"])
        self.assertEqual(out[0]["value"], tok)

    def test_token_remnant_foreign_salt_ignored(self):
        self.m.record("x@y.de", self.m.next_token("email"), "email")
        out = ps.lint_residual_fakes(
            "Fremd: <EMAIL_1_ffff9999>", mapping=self.m)
        self.assertEqual(out, [])

    def test_saltless_token_flagged_only_for_minted_kinds(self):
        self.m.record("x@y.de", self.m.next_token("email"), "email")
        out = ps.lint_residual_fakes(
            "Rest <EMAIL_1> und Platzhalter <ITEM_1>.", mapping=self.m)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["value"], "<EMAIL_1>")
        self.assertEqual(out[0]["reason"], "token_remnant")

    def test_exact_fake_word_bounded(self):
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")
        out = ps.lint_residual_fakes(
            "Kunde Sam Mitchell erschien persönlich.", mapping=self.m)
        self.assertEqual([f["reason"] for f in out], ["exact_fake"])
        # Mid-word never fires (the 'Starkstrom' guard, fake side).
        out2 = ps.lint_residual_fakes(
            "Die Sam Mitchellson GmbH liefert.", mapping=self.m)
        self.assertEqual(out2, [])

    def test_reformatted_date_german_long_form(self):
        fake = ps._fake_date("05.02.1947", self.m.salt)
        self.assertNotEqual(fake, "05.02.1947")
        self.m.record("05.02.1947", fake, "dob")
        d = ps._parse_date_surface(fake)
        long_de = f"{d.day}. {ps._MONTHS_DE[d.month - 1].title()} {d.year}"
        out = ps.lint_residual_fakes(
            f"Die Person wurde am {long_de} geboren.", mapping=self.m)
        self.assertEqual([f["reason"] for f in out], ["reformatted_date"])

    def test_reformatted_date_iso_variant(self):
        fake = ps._fake_date("05.02.1947", self.m.salt)
        self.m.record("05.02.1947", fake, "dob")
        d = ps._parse_date_surface(fake)
        iso = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
        out = ps.lint_residual_fakes(f"DOB: {iso}", mapping=self.m)
        self.assertEqual([f["reason"] for f in out], ["reformatted_date"])

    def test_restored_original_date_not_flagged(self):
        fake = ps._fake_date("05.02.1947", self.m.salt)
        self.m.record("05.02.1947", fake, "dob")
        # Post-deanonymise text carries the ORIGINAL — clean.
        out = ps.lint_residual_fakes(
            "geboren am 05.02.1947 (5. Februar 1947)", mapping=self.m)
        self.assertEqual(out, [])

    def test_non_date_categories_skip_date_check(self):
        # A name entry whose fake happens to contain digits must not go
        # through date parsing.
        self.m.record("K-107625", "K-204817", "bare_identifier")
        out = ps.lint_residual_fakes("Ref K-999999", mapping=self.m)
        self.assertEqual(out, [])

    def test_name_genitive_and_initials(self):
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")
        self.m.entities["e1"] = {
            "sur": "stark", "givens": ["bonnie"],
            "fake_sur": "Mitchell", "fake_givens": ["Sam"],
        }
        out = ps.lint_residual_fakes(
            "Mitchells Unterlagen; Rückfragen an S. M. bitte.",
            mapping=self.m)
        reasons = sorted(f["reason"] for f in out)
        self.assertEqual(reasons, ["name_genitive", "name_initials"])

    def test_genitive_skipped_when_registered_variant(self):
        # If surname+s is itself a registered reverse key, deanonymize
        # already handles it — no warning.
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")
        self.m.record("Starks", "Mitchells", "name")
        self.m.entities["e1"] = {
            "sur": "stark", "givens": ["bonnie"],
            "fake_sur": "Mitchell", "fake_givens": ["Sam"],
        }
        out = ps.lint_residual_fakes("kein Genitiv hier", mapping=self.m)
        self.assertEqual(out, [])

    def test_finding_cap(self):
        tok = self.m.next_token("email")
        self.m.record("a@b.de", tok, "email")
        # 60 distinct saltless remnants → capped at 50.
        text = " ".join(f"<EMAIL_{i}>" for i in range(2, 62))
        out = ps.lint_residual_fakes(text, mapping=self.m)
        self.assertEqual(len(out), 50)


# ---------------------------------------------------------------------------
# File seam — reuses the fake-session/DB scaffolding from
# tests/test_chat_worker_helpers.py.
# ---------------------------------------------------------------------------

from tests.test_chat_worker_helpers import (  # noqa: E402
    FakeChatDB, _FakeSession, _FakeSessions)


class TestFileSeamLint(unittest.TestCase):
    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        self.fake_db = FakeChatDB()
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = self.fake_db
        self._orig_sessions = getattr(chat_mod, "sessions", None)
        self.fake_sessions = _FakeSessions()
        chat_mod.sessions = self.fake_sessions

        import brain
        self._orig_iap = brain._is_artifact_path
        brain._is_artifact_path = lambda _p: True

        self.m = ps.new_mapping()
        self.fake_date = ps._fake_date("05.02.1947", self.m.salt)
        self.m.record("05.02.1947", self.fake_date, "dob")
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")

    def tearDown(self):
        if self._orig_chatdb is not None:
            self.chat_mod.ChatDB = self._orig_chatdb
        if self._orig_sessions is not None:
            self.chat_mod.sessions = self._orig_sessions
        import brain
        brain._is_artifact_path = self._orig_iap
        ps.close_mapping(self.m.mapping_id)

    def _cb(self, sid):
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)
        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.m.mapping_id, session_id=sid, agent_id="main")
        return cb, sess

    def test_md_with_reformatted_date_reports_unrestored(self):
        cb, sess = self._cb("sid-l6-md")
        d = ps._parse_date_surface(self.fake_date)
        long_de = f"{d.day}. {ps._MONTHS_DE[d.month - 1].title()} {d.year}"
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.md")
            with open(path, "w") as f:
                f.write(f"KYC-Bericht\n\nGeboren am {long_de}.\n")
            cb(path, "created", "main")
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertEqual(result.get("unrestored"), 1)
        self.assertIn(long_de, result.get("residues", []))

    def test_md_clean_has_no_unrestored_field(self):
        cb, sess = self._cb("sid-l6-clean")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.md")
            with open(path, "w") as f:
                f.write("Bericht: Bonnie Stark, geboren 05.02.1947.\n")
            cb(path, "created", "main")
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertNotIn("unrestored", result)

    def _write_pdf(self, path, text):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)
        doc.close()

    def test_pdf_with_fake_substance_fails_loud(self):
        import engine
        cb, sess = self._cb("sid-l6-pdf")
        from engine.context import request_context, get_request_context
        with request_context():
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "report.pdf")
                self._write_pdf(
                    path, "Bericht: Sam Mitchell, geprueft und echt.")
                cb(path, "created", "main")
            warns = get_request_context()._gdpr_file_warnings
        # Loud synthetic pair with error status + warning text.
        kinds = [t for (t, _) in sess.live_stream.events]
        self.assertEqual(kinds,
                         ["synthetic_tool_use", "synthetic_tool_result"])
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertEqual(result.get("restored"), 0)
        self.assertGreaterEqual(result.get("unrestored", 0), 1)
        self.assertIn("NICHT", result.get("warning", ""))
        self.assertEqual(self.fake_db.rows[1].get("status")
                         or json.loads(self.fake_db.rows[1]["content"]).get(
                             "status"), "error")
        # Model-directed warning queued on the RequestContext.
        self.assertTrue(warns and "report.pdf" in warns[0])

    def test_pdf_without_fake_substance_is_silent(self):
        cb, sess = self._cb("sid-l6-pdf-clean")
        from engine.context import request_context, get_request_context
        with request_context():
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "clean.pdf")
                self._write_pdf(path, "Technischer Anhang ohne Namen.")
                cb(path, "created", "main")
            warns = get_request_context()._gdpr_file_warnings
        self.assertEqual(sess.live_stream.events, [])
        self.assertEqual(self.fake_db.rows, [])
        self.assertFalse(warns)


class TestSvgReverse(unittest.TestCase):
    """M7/G6: .svg is text XML → reversible like .html. A locally-rendered
    render_diagram .svg carries real values (args-deanon); a model-written .svg
    carries fake tokens that the reverse walker must restore."""

    def setUp(self):
        self.m = ps.new_mapping()
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")

    def tearDown(self):
        ps.close_mapping(self.m.mapping_id)

    def test_svg_in_supported_exts(self):
        from engine.file_pseudonymize import SUPPORTED_EXTS, _PLAIN_EXTS
        self.assertIn(".svg", SUPPORTED_EXTS)
        self.assertIn(".svg", _PLAIN_EXTS)

    def test_svg_fake_token_restored(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "diagram.svg")
            with open(path, "w") as f:
                f.write('<svg><text>Sam Mitchell</text></svg>')
            restored = ps.deanonymize_file(path, path, mapping=self.m)
            with open(path) as f:
                out = f.read()
        self.assertEqual(restored, 1)
        self.assertIn("Bonnie Stark", out)
        self.assertNotIn("Sam Mitchell", out)


class TestFileSeamNonTree(unittest.TestCase):
    """M7/G5: the after-file-write callback must reverse+lint files the model
    wrote OUTSIDE the artifact tree (a .docx for a real meeting written to an
    absolute path used to sail through with fake names). The callback no longer
    gates on `_is_artifact_path` — model-written files are handled everywhere.
    """

    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        self.fake_db = FakeChatDB()
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = self.fake_db
        self._orig_sessions = getattr(chat_mod, "sessions", None)
        self.fake_sessions = _FakeSessions()
        chat_mod.sessions = self.fake_sessions
        self.m = ps.new_mapping()
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")

    def tearDown(self):
        if self._orig_chatdb is not None:
            self.chat_mod.ChatDB = self._orig_chatdb
        if self._orig_sessions is not None:
            self.chat_mod.sessions = self._orig_sessions
        ps.close_mapping(self.m.mapping_id)

    def _cb(self, sid):
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)
        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.m.mapping_id, session_id=sid, agent_id="main")
        return cb, sess

    def test_non_tree_md_still_reversed(self):
        """A .md written to an arbitrary (non-artifact) absolute path is still
        de-anonymised in place. Mutation guard: re-adding the `_is_artifact_path`
        bail (with the stub forced False) would leave the fake token in place."""
        import brain
        _orig = brain._is_artifact_path
        brain._is_artifact_path = lambda _p: False  # NOT in the artifact tree
        try:
            cb, sess = self._cb("sid-m7-nontree")
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "hv_sprechvorlage.docx.md")
                with open(path, "w") as f:
                    f.write("Aufsichtsrat: Sam Mitchell.\n")
                cb(path, "created", "main")
                with open(path) as f:
                    out = f.read()
        finally:
            brain._is_artifact_path = _orig
        self.assertIn("Bonnie Stark", out)
        self.assertNotIn("Sam Mitchell", out)
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertEqual(result.get("restored"), 1)


class TestFileSeamImage(unittest.TestCase):
    """M7/G6: a raster image written under active anonymisation can bake fake
    values into pixels — not reversible, not lintable without OCR. The callback
    fails LOUD (synthetic error row + degradation tally + model-directed
    warning) so the model re-renders as .svg."""

    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        self.fake_db = FakeChatDB()
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = self.fake_db
        self._orig_sessions = getattr(chat_mod, "sessions", None)
        self.fake_sessions = _FakeSessions()
        chat_mod.sessions = self.fake_sessions
        self.m = ps.new_mapping()
        self.m.record("Bonnie Stark", "Sam Mitchell", "name")

    def tearDown(self):
        if self._orig_chatdb is not None:
            self.chat_mod.ChatDB = self._orig_chatdb
        if self._orig_sessions is not None:
            self.chat_mod.sessions = self._orig_sessions
        ps.close_mapping(self.m.mapping_id)

    def _cb(self, sid):
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)
        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.m.mapping_id, session_id=sid, agent_id="main")
        return cb, sess

    def test_png_fails_loud_and_tallies(self):
        from engine.context import request_context, get_request_context
        cb, sess = self._cb("sid-m7-png")
        with request_context():
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "konzernstruktur.png")
                with open(path, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # bogus pixels
                cb(path, "created", "main")
            warns = get_request_context()._gdpr_file_warnings
            degr = get_request_context()._gdpr_degradation
        # Loud synthetic pair with error status + warning text.
        kinds = [t for (t, _) in sess.live_stream.events]
        self.assertEqual(kinds,
                         ["synthetic_tool_use", "synthetic_tool_result"])
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertEqual(result.get("restored"), 0)
        self.assertIn(".svg", result.get("warning", ""))
        # Model-directed warning + per-turn degradation tally.
        self.assertTrue(warns and "konzernstruktur.png" in warns[0])
        self.assertEqual((degr or {}).get("image_unreversible"), 1)


class TestDispatchDrainsFileWarnings(unittest.TestCase):
    """dispatch_tool appends queued report-fidelity warnings to the tool
    result string (the model-visible half of L6a) and clears the queue."""

    def test_drain_appends_and_clears(self):
        import brain
        from engine import llm_loop
        from engine.context import request_context, get_request_context

        def _fake_tool(args):
            ctx = get_request_context()
            ctx._gdpr_file_warnings = ["report.pdf: PDF enthält 2 Werte …"]
            return json.dumps({"ok": True})

        brain.TOOL_DISPATCH["_l6_test_tool"] = _fake_tool
        try:
            with request_context():
                out, is_err = llm_loop.dispatch_tool("_l6_test_tool", {})
                self.assertIn("⚠️ GDPR: report.pdf", out)
                self.assertFalse(is_err)
                self.assertIsNone(
                    get_request_context()._gdpr_file_warnings)
        finally:
            brain.TOOL_DISPATCH.pop("_l6_test_tool", None)

    def test_no_warnings_leaves_result_untouched(self):
        import brain
        from engine import llm_loop
        from engine.context import request_context

        brain.TOOL_DISPATCH["_l6_test_tool2"] = lambda args: '{"ok": true}'
        try:
            with request_context():
                out, _ = llm_loop.dispatch_tool("_l6_test_tool2", {})
                self.assertEqual(out, '{"ok": true}')
        finally:
            brain.TOOL_DISPATCH.pop("_l6_test_tool2", None)


class TestClampReportFidelity(unittest.TestCase):
    def test_clamp_carries_l6c_instructions(self):
        import brain
        out = brain._apply_system_prompt_postprocess(
            "BASE", caveman_system=0, caveman_chat=0, plan_mode=False,
            gdpr_anon=True)
        self.assertIn("Report fidelity", out)
        # rechnen ja — reformatieren nein
        self.assertIn("Computing WITH the values", out)
        self.assertIn("EXACTLY in the surface form", out)
        # PDF steering
        self.assertIn("Do NOT generate PDF files", out)
        self.assertIn(".html or .md", out)


class TestFabricationStripKeepsQuote(unittest.TestCase):
    """Chat db9867f5: mistral-small answered a birth-date question purely as
    '[Quelle: user_message — "…geboren am 16.02.1947"]'. The fabrication strip
    removed the WHOLE bracket → empty reply. It must keep the quoted text."""

    def setUp(self):
        import brain
        self.brain = brain

    def test_pure_citation_keeps_quote_text(self):
        brain = self.brain
        out, n = brain.strip_fabricated_citations(
            '[Quelle: user_message — "Bonnie Marie Stark, geboren am 05.02.1947"]')
        self.assertEqual(n, 1)
        self.assertIn("geboren am 05.02.1947", out)
        self.assertNotIn("[Quelle:", out)

    def test_decorative_bracket_without_quote_removed(self):
        out, n = self.brain.strip_fabricated_citations(
            "Die Analyse ist fertig. [Quelle: report.pdf]")
        self.assertEqual(out.strip(), "Die Analyse ist fertig.")

    def test_claim_plus_citation_keeps_claim(self):
        out, _ = self.brain.strip_fabricated_citations(
            'Sie wurde am 05.02.1947 geboren. '
            '[Quelle: user_message — "geboren am 05.02.1947"]')
        self.assertIn("Sie wurde am 05.02.1947 geboren", out)


class TestUserMessageCountsAsSource(unittest.TestCase):
    """A quote of what the user typed is grounded — not fabricated
    (chat db9867f5)."""

    def setUp(self):
        import brain
        self.brain = brain

    def test_user_message_quote_verified(self):
        reply = ('Sie wurde am 05.02.1947 geboren. '
                 '[Quelle: user_message — "geboren am 05.02.1947"]')
        user = "Bonnie Marie Stark, geboren am 05.02.1947. Wann geboren?"
        val = self.brain.validate_citations_in_response(
            reply, session_id=None, user_text=user)
        self.assertEqual(val["verified"], 1)
        self.assertEqual(len(val["unverified"]), 0)

    def test_without_user_text_unverified(self):
        reply = ('Sie wurde am 05.02.1947 geboren. '
                 '[Quelle: user_message — "geboren am 05.02.1947"]')
        val = self.brain.validate_citations_in_response(reply, session_id=None)
        self.assertEqual(val["verified"], 0)


class TestFilenameDeanon(unittest.TestCase):
    """Generated artifacts named from the model's fake identity get renamed to
    the real name (content is already reversed); a same-dir ALIAS keeps the
    model's remembered (fake) path readable so the round-trip never breaks."""

    def setUp(self):
        import handlers.chat as _hc
        self.hc = _hc
        self.m = ps.new_mapping()
        # Name PARTS as they land in the reverse map (fake → real).
        for real, fake in [
            ("Bonnie Marie Stark", "Logan Kerry Edwards"),
            ("Stark", "Edwards"), ("Bonnie", "Logan"), ("Marie", "Kerry"),
        ]:
            self.m.record(real, fake, "name")

    def test_helper_substitutes_name_tokens(self):
        got = self.hc._gdpr_deanonymise_filename(
            "kundendaten_pruefung_logan_edwards.html", self.m)
        self.assertEqual(got, "kundendaten_pruefung_bonnie_stark.html")

    def test_helper_preserves_case_and_separators(self):
        self.assertEqual(
            self.hc._gdpr_deanonymise_filename("Report-Logan-Edwards.pdf", self.m),
            "Report-Bonnie-Stark.pdf")

    def test_helper_noop_without_name(self):
        # A name-less filename is never touched (safe no-op).
        self.assertEqual(
            self.hc._gdpr_deanonymise_filename("kundendaten_pruefung.html", self.m),
            "kundendaten_pruefung.html")

    def test_helper_noop_empty_mapping(self):
        self.assertEqual(
            self.hc._gdpr_deanonymise_filename("logan_edwards.html",
                                               ps.new_mapping()),
            "logan_edwards.html")

    def test_rename_registers_filename_pair_for_reply_deanon(self):
        # Chat 8de1eeb8: after the rename, the model's reply still says
        # '…Logan_Kerry_Edwards.html' — underscore-joined name parts that the
        # word-bounded alpha reverse keys can NEVER restore ('_' is \w, so
        # 'Logan' has no boundary inside 'Logan_Kerry'). The rename block
        # records the whole filename as a derived fake→real pair, which makes
        # deanonymize_text (streamer + final reply pass) restore it — label
        # AND markdown link target.
        self.m.record("Bonnie Marie Stark", "Logan Kerry Edwards", "name")
        fake_fn = "Pruefung_Logan_Kerry_Edwards.html"
        real_fn = self.hc._gdpr_deanonymise_filename(fake_fn, self.m)
        self.assertEqual(real_fn, "Pruefung_Bonnie_Marie_Stark.html")
        # What the rename block does after a committed rename:
        self.m.record(real_fn, fake_fn, "name", count=False)
        reply = f"Der Report liegt bereit: 📁 [{fake_fn}]({fake_fn})"
        out, n = ps.deanonymize_text(reply, mapping=self.m)
        self.assertEqual(out, f"Der Report liegt bereit: 📁 [{real_fn}]({real_fn})")
        self.assertGreaterEqual(n, 2)
        # Derived bookkeeping: no new FINDING was counted, entry is derived.
        self.assertIn(real_fn, self.m.derived)
        # The reply-side highlight (find_restored_spans) marks the filename.
        spans = ps.find_restored_spans(out, mapping=self.m)
        self.assertTrue(any(s["original"] == real_fn for s in spans))

    def test_after_write_cb_records_filename_pair(self):
        # Drive the REAL callback end-to-end on a written .html: content is
        # reversed, the file renamed real (alias at fake path), and the
        # mapping now carries the fake→real FILENAME pair for the reply pass.
        import shutil
        self.m.record("Bonnie Marie Stark", "Logan Kerry Edwards", "name")
        d = tempfile.mkdtemp()
        try:
            fake = os.path.join(d, "report_logan_edwards.html")
            with open(fake, "w") as f:
                f.write("<html>Logan Kerry Edwards</html>")
            cb = self.hc.make_gdpr_after_file_write_cb(
                mapping_id=self.m.mapping_id, session_id="test-fn-pair",
                agent_id="main")
            cb(fake, "write", "main")
            real = os.path.join(d, "report_bonnie_stark.html")
            self.assertTrue(os.path.isfile(real))
            with open(real) as f:
                self.assertIn("Bonnie Marie Stark", f.read())
            self.assertEqual(
                self.m.reverse.get("report_logan_edwards.html"),
                "report_bonnie_stark.html")
            self.assertEqual(
                self.m.forward.get("report_bonnie_stark.html"),
                "report_logan_edwards.html")
            self.assertIn("report_bonnie_stark.html", self.m.derived)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rename_alias_roundtrip_hardlink(self):
        # The alias fallback chain (hardlink first — the Windows-safe path)
        # leaves the fake path pointing at the renamed real file.
        import shutil
        d = tempfile.mkdtemp()
        try:
            fake = os.path.join(d, "report_logan_edwards.html")
            with open(fake, "w") as f:
                f.write("<html>Bonnie Marie Stark</html>")
            new_name = self.hc._gdpr_deanonymise_filename(
                os.path.basename(fake), self.m)
            real = os.path.join(d, new_name)
            os.rename(fake, real)
            alias_ok = False
            for mk in (lambda: os.link(real, fake),
                       lambda: os.symlink(real, fake),
                       lambda: shutil.copy2(real, fake)):
                try:
                    mk(); alias_ok = True; break
                except Exception:
                    continue
            self.assertTrue(alias_ok)
            self.assertEqual(new_name, "report_bonnie_stark.html")
            self.assertTrue(os.path.isfile(real))
            # The model's remembered fake path still resolves to the same bytes.
            self.assertTrue(os.path.isfile(fake))
            with open(fake) as a, open(real) as b:
                self.assertEqual(a.read(), b.read())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
