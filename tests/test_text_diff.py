"""text_diff — deterministic text/code/JSON-structure comparison.

WHY: the tool exists so 'vergleiche Datei A mit B' never means reading both
files into chat (token bloat, misread lines) or ad-hoc python_exec. These
tests pin the intent: unified diff + honest counts, structural JSON mode
that ignores object-key order but NOT array order, artifact export, and
clean refusals (binary, missing, oversized files).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402,F401  (loads TOOL_DISPATCH; warms lazy imports)
from engine.context import request_context  # noqa: E402
from engine.tools.diff_tools import tool_text_diff  # noqa: E402


class _FakeAgent:
    agent_id = "main"


class TextDiffFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._prev_cwd = os.getcwd()
        os.chdir(self._tmp)
        self._sid = "textdiff-test-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))

    def tearDown(self):
        os.chdir(self._prev_cwd)
        try:
            import shutil
            from engine.tools.file_tools import _resolve_artifact_dir
            with request_context(current_session_id=self._sid,
                                 current_agent=_FakeAgent()):
                art_dir, _ = _resolve_artifact_dir()
            if art_dir and self._sid in art_dir and os.path.isdir(art_dir):
                shutil.rmtree(art_dir)
        except Exception:
            pass

    def _write(self, name: str, text: str, binary: bool = False) -> str:
        p = os.path.join(self._tmp, name)
        with open(p, "wb" if binary else "w",
                  **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(text if not binary else text)
        return p


class TestTextDiff(TextDiffFixture):
    def test_unified_diff_with_counts(self):
        a = self._write("a.py", "def f():\n    return 1\n\nprint(f())\n")
        b = self._write("b.py", "def f():\n    return 2\n\nprint(f())\n")
        out = json.loads(tool_text_diff({"path_a": a, "path_b": b}))
        self.assertEqual(out["differences"], 2)          # 1 removed + 1 added
        self.assertIn("+1 / -1 in 1 hunk(s)", out["report"])
        self.assertIn("-    return 1", out["report"])
        self.assertIn("+    return 2", out["report"])

    def test_identical_files(self):
        a = self._write("x1.txt", "gleich\n")
        b = self._write("x2.txt", "gleich\n")
        out = json.loads(tool_text_diff({"path_a": a, "path_b": b}))
        self.assertEqual(out["differences"], 0)
        self.assertIn("identisch", out["report"])

    def test_json_mode_ignores_key_order_finds_changes(self):
        a = self._write("c1.json", json.dumps(
            {"server": {"port": 8420, "host": "a"}, "flags": ["x", "y"]}))
        b = self._write("c2.json", json.dumps(
            {"flags": ["x", "z"], "server": {"host": "a", "port": 9000,
                                             "tls": True}}))
        out = json.loads(tool_text_diff(
            {"path_a": a, "path_b": b, "mode": "json"}))
        rep = out["report"]
        self.assertEqual(out["differences"], 3)
        self.assertIn("~ `server.port`: 8420 → 9000", rep)
        self.assertIn("+ `server.tls` = true", rep)
        self.assertIn("~ `flags[1]`: y → z", rep)   # array ORDER is honored
        self.assertNotIn("server.host", rep)        # key reorder ≠ change

    def test_html_artifact_export(self):
        a = self._write("v1.sql", "SELECT a FROM t;\n")
        b = self._write("v2.sql", "SELECT a, b FROM t;\n")
        out = json.loads(tool_text_diff(
            {"path_a": a, "path_b": b, "out": "diff.html"}))
        path = out["saved"]["path"]
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("v1.sql", html)
        self.assertIn("v2.sql", html)

    def test_binary_is_refused_with_xlsx_hint(self):
        a = self._write("w.xlsx", b"PK\x03\x04\x00\x00bin", binary=True)
        b = self._write("t.txt", "text\n")
        out = json.loads(tool_text_diff({"path_a": a, "path_b": b}))
        self.assertIn("binary", out["error"])
        self.assertIn("xlsx_diff", out["error"])

    def test_missing_file_is_error(self):
        b = self._write("t.txt", "x\n")
        out = json.loads(tool_text_diff({"path_a": "nope.txt", "path_b": b}))
        self.assertIn("File not found", out["error"])

    def test_four_site_registration(self):
        # schema + group + dispatch identity (direct fn ref, no lambda)
        from engine.tool_schemas import TOOL_DEFINITIONS
        self.assertTrue(any(d["name"] == "text_diff" for d in TOOL_DEFINITIONS))
        self.assertIn("text_diff", brain.TOOL_GROUPS["documents"])
        self.assertIs(brain.TOOL_DISPATCH["text_diff"], tool_text_diff)


if __name__ == "__main__":
    unittest.main()
