"""Design-Modus attachment embedding (v9.364.0).

`brain._inline_attachment_refs` replaces `attachment://<name>` references in a
just-written .html artifact with data-URIs of the matching files in the
session's /tmp/brain-attachments/<sid>/ dir — deterministically, so image
bytes never flow through the model. Unresolvable references stay in place and
queue a model-visible warning that `llm_loop.dispatch_tool` drains into the
tool result (the _gdpr_file_warnings pattern).

WHY these tests: the feature's contract is (a) the saved artifact is
self-contained (data-URI, no attachment:// left), (b) failures are LOUD in
the same round (warning reaches the model), never silent broken <img> tags.
"""
import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class _InlineBase(unittest.TestCase):
    def setUp(self):
        import brain
        self.brain = brain
        self.sid = "test-design-inline"
        self.attach_dir = os.path.join("/tmp", "brain-attachments", self.sid)
        os.makedirs(self.attach_dir, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        for f in os.listdir(self.attach_dir):
            os.unlink(os.path.join(self.attach_dir, f))
        os.rmdir(self.attach_dir)

    def _write_html(self, body, name="report.html"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def _run(self, path):
        from engine.context import request_context, get_request_context
        with request_context():
            get_request_context().session_id = self.sid
            self.brain._inline_attachment_refs(path)
            return get_request_context()._design_file_warnings


class TestInlineHappyPath(_InlineBase):
    def test_ref_becomes_data_uri(self):
        with open(os.path.join(self.attach_dir, "shot.png"), "wb") as f:
            f.write(_PNG)
        path = self._write_html(
            '<html><body><img src="attachment://shot.png"></body></html>')
        warns = self._run(path)
        text = open(path, encoding="utf-8").read()
        self.assertNotIn("attachment://", text)
        self.assertIn(
            "data:image/png;base64," + base64.b64encode(_PNG).decode("ascii"),
            text)
        self.assertIsNone(warns)

    def test_urlencoded_name_and_multiple_refs(self):
        with open(os.path.join(self.attach_dir, "mein bild.png"), "wb") as f:
            f.write(_PNG)
        with open(os.path.join(self.attach_dir, "logo.svg"), "wb") as f:
            f.write(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        path = self._write_html(
            '<img src="attachment://mein%20bild.png">'
            '<img src="attachment://logo.svg">')
        warns = self._run(path)
        text = open(path, encoding="utf-8").read()
        self.assertNotIn("attachment://", text)
        self.assertIn("data:image/png;base64,", text)
        self.assertIn("data:image/svg+xml;base64,", text)
        self.assertIsNone(warns)


class TestInlineFailLoud(_InlineBase):
    def test_missing_file_warns_and_keeps_ref(self):
        path = self._write_html('<img src="attachment://gibtsnicht.png">')
        warns = self._run(path)
        text = open(path, encoding="utf-8").read()
        self.assertIn("attachment://gibtsnicht.png", text)
        self.assertTrue(warns and "gibtsnicht.png" in warns[0])

    def test_non_image_ext_warns_and_keeps_ref(self):
        with open(os.path.join(self.attach_dir, "doc.pdf"), "wb") as f:
            f.write(b"%PDF-1.4")
        path = self._write_html('<img src="attachment://doc.pdf">')
        warns = self._run(path)
        text = open(path, encoding="utf-8").read()
        self.assertIn("attachment://doc.pdf", text)
        self.assertTrue(warns and "doc.pdf" in warns[0])

    def test_oversize_image_warns_and_keeps_ref(self):
        with open(os.path.join(self.attach_dir, "huge.png"), "wb") as f:
            f.write(b"\x00" * (self.brain._ATTACH_INLINE_MAX_IMG + 1))
        path = self._write_html('<img src="attachment://huge.png">')
        warns = self._run(path)
        text = open(path, encoding="utf-8").read()
        self.assertIn("attachment://huge.png", text)
        self.assertTrue(warns and "huge.png" in warns[0])

    def test_path_traversal_resolves_to_basename(self):
        # "attachment://../../etc/passwd.png" must never leave the session's
        # attachment dir — the name is reduced to its basename, which then
        # simply doesn't exist there.
        path = self._write_html(
            '<img src="attachment://../../etc/passwd.png">')
        warns = self._run(path)
        self.assertIn("attachment://", open(path, encoding="utf-8").read())
        self.assertTrue(warns and "passwd.png" in warns[0])


class TestInlineGates(_InlineBase):
    def test_non_html_untouched(self):
        with open(os.path.join(self.attach_dir, "shot.png"), "wb") as f:
            f.write(_PNG)
        body = 'x = "attachment://shot.png"'
        path = self._write_html(body, name="script.py")
        warns = self._run(path)
        self.assertEqual(open(path, encoding="utf-8").read(), body)
        self.assertIsNone(warns)

    def test_no_session_untouched(self):
        from engine.context import request_context
        with open(os.path.join(self.attach_dir, "shot.png"), "wb") as f:
            f.write(_PNG)
        body = '<img src="attachment://shot.png">'
        path = self._write_html(body)
        with request_context():
            self.brain._inline_attachment_refs(path)
        self.assertEqual(open(path, encoding="utf-8").read(), body)

    def test_html_without_refs_not_rewritten(self):
        path = self._write_html("<html><body>hi</body></html>")
        mtime = os.path.getmtime(path)
        self._run(path)
        self.assertEqual(os.path.getmtime(path), mtime)


class TestDispatchDrainsDesignWarnings(unittest.TestCase):
    """dispatch_tool appends queued design warnings to the tool result string
    and clears the queue — the model must learn about a broken attachment://
    reference in the SAME round (same contract as the GDPR drain)."""

    def test_drain_appends_and_clears(self):
        import brain
        from engine import llm_loop
        from engine.context import request_context, get_request_context

        def _fake_tool(args):
            get_request_context()._design_file_warnings = [
                "attachment://x.png: Datei nicht gefunden"]
            return json.dumps({"ok": True})

        brain.TOOL_DISPATCH["_design_test_tool"] = _fake_tool
        try:
            with request_context():
                out, is_err = llm_loop.dispatch_tool("_design_test_tool", {})
                self.assertIn("⚠️ Design: attachment://x.png", out)
                self.assertFalse(is_err)
                self.assertIsNone(
                    get_request_context()._design_file_warnings)
        finally:
            brain.TOOL_DISPATCH.pop("_design_test_tool", None)


if __name__ == "__main__":
    unittest.main()
