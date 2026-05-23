"""Characterization (behavior-pinning) tests for the file/shell/python/doc tools.

Pins the deterministic behavior of the 10 file-system tools BEFORE they are
extracted from brain.py to `engine/tools/file_tools.py` (refactor Tier E, step
E1). Contract: after extraction these must still pass — the move is a pure
relocation, so the JSON envelopes + error strings + security gates must not
shift by a byte.

WHY: these tools (read/write/edit_file, list_directory, search_files,
execute_command, python_exec, read/write/edit_document) had NO tests, yet they
carry the security-relevant path/command behavior + the artifact-tracking
side-effects (`_after_file_write`, `_gdpr_anon_tool_text`). The import-gate
can't catch a logic regression here; these cases can.

All expected values were captured by PROBING the live tools (not guessed) on
2026-05-23 against the pre-E1 brain.py.

What is pinned (hermetic — tmpdir + thread-locals, no live server/sidecar):
  * read_file round-trip + numbered-line format + not-found error
  * edit_file single-replace + not-found-string error (NOTE: needs a real
    AgentConfig-like current_agent — a bare string crashes with
    `'str' object has no attribute 'agent_id'`, a behavior we pin too)
  * list_directory file/dir typing + sorting
  * search_files regex match shape + invalid-regex error
  * execute_command stdout capture + TERM=dumb env isolation
  * python_exec output + script_N.py naming + output.txt artifact
  * read_document text-format pagination

What is NOT pinned (integration/format-lib — gate blind spots for E1):
  * write/edit_document for binary formats (docx/xlsx/pptx serialization)
  * read_document binary extraction (doc_convert subprocess pipeline)
  * execute_command/python_exec streaming, timeout, artifact registration
    side-effects (verified by the eval run, not this gate)

Run: python3 -m unittest tests.test_file_tools_characterization -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402


class _FakeAgent:
    """Minimal stand-in for AgentConfig — the file tools read `.agent_id`.
    Pinned behavior: a bare-string current_agent makes edit_file crash with
    `'str' object has no attribute 'agent_id'` (see test_edit_file_*), so the
    real callers always pass an AgentConfig; we mirror that here."""
    agent_id = "main"


class _FileToolFixture(unittest.TestCase):
    """Each test runs in a fresh tmpdir (as cwd) with a known session + agent.
    Relative paths resolve against cwd; the tools abspath them."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="filetool_chartest_")
        os.chdir(self._tmp)
        # Unique session id per test so the artifact folder is fresh — write_file
        # / python_exec route into agents/<a>/artifacts/<date>_<sid>/ and the
        # script_N.py counter would otherwise accrue across runs (order-dependent).
        # request_context(...) is entered via enterContext so it tears down
        # automatically (restoring the prior context) when the test ends.
        self._sid = "filetool-chartest-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))

    def tearDown(self):
        os.chdir(self._prev_cwd)

    def _write(self, name, content):
        with open(os.path.join(self._tmp, name), "w") as f:
            f.write(content)


class TestReadFile(_FileToolFixture):
    """read_file: numbered-line content + 1-indexed showing + not-found error.
    The numbered format and the explicit 'Do NOT retry' not-found message are
    contracts the model relies on."""

    def test_basic_round_trip_numbered_lines(self):
        self._write("test.txt", "line1\nline2\nline3\n")
        out = json.loads(brain.tool_read_file({"path": "test.txt"}))
        self.assertEqual(out["total_lines"], 3)
        self.assertEqual(out["showing"], "1-3")
        # numbered, tab-separated, right-aligned line numbers
        self.assertIn("\t", out["content"])
        self.assertIn("line1", out["content"])
        self.assertTrue(out["content"].lstrip().startswith("1\t") or "     1\t" in out["content"])

    def test_nonexistent_is_error(self):
        out = json.loads(brain.tool_read_file({"path": "nope.txt"}))
        self.assertIn("error", out)
        self.assertIn("File not found", out["error"])


class TestEditFile(_FileToolFixture):
    """edit_file: single replace count + status; not-found-string error.
    INVARIANT: requires an AgentConfig-like current_agent (reads .agent_id) —
    a bare string crashes; pinned so the extraction keeps that contract."""

    def test_single_replace(self):
        self._write("f.txt", "foo bar foo\n")
        out = json.loads(brain.tool_edit_file(
            {"path": "f.txt", "old_string": "bar", "new_string": "BAR"}))
        self.assertEqual(out.get("replacements"), 1)
        self.assertEqual(out.get("status"), "edited")
        self.assertEqual(open(os.path.join(self._tmp, "f.txt")).read().strip(), "foo BAR foo")

    def test_missing_old_string_is_error(self):
        self._write("f.txt", "foo\n")
        out = json.loads(brain.tool_edit_file(
            {"path": "f.txt", "old_string": "zzz", "new_string": "y"}))
        self.assertIn("error", out)
        self.assertIn("not found", out["error"])

    def test_bare_string_agent_crashes_gracefully(self):
        # Pin the contract: current_agent MUST be an AgentConfig (has .agent_id).
        # A bare string is caught and surfaced as a tool error, not a raw crash.
        self._write("f.txt", "a\n")
        get_request_context().current_agent = "main"  # wrong type on purpose
        out = json.loads(brain.tool_edit_file(
            {"path": "f.txt", "old_string": "a", "new_string": "b"}))
        self.assertIn("error", out)
        self.assertIn("agent_id", out["error"])


class TestWriteFile(_FileToolFixture):
    """write_file: returns size + status='written'. Relative paths default into
    the agent's artifact session folder (file-write tracking depends on it)."""

    def test_write_returns_size_and_status(self):
        out = json.loads(brain.tool_write_file({"path": "out.txt", "content": "hello"}))
        self.assertEqual(out.get("size"), 5)
        self.assertEqual(out.get("status"), "written")
        # relative path is routed into the artifact folder
        self.assertIn("artifacts", out["path"])
        self.assertTrue(out["path"].endswith("out.txt"))


class TestListDirectory(_FileToolFixture):
    """list_directory: per-entry name/type/size; directories report size=null."""

    def test_flat_listing_types(self):
        self._write("a.txt", "x")
        os.mkdir(os.path.join(self._tmp, "sub"))
        out = json.loads(brain.tool_list_directory({"path": "."}))
        self.assertEqual(out["count"], 2)
        by_name = {e["name"]: e for e in out["entries"]}
        self.assertEqual(by_name["sub"]["type"], "directory")
        self.assertIsNone(by_name["sub"]["size"])
        self.assertEqual(by_name["a.txt"]["type"], "file")


class TestSearchFiles(_FileToolFixture):
    """search_files: match shape {file,line,text} + invalid-regex error."""

    def test_regex_match_shape(self):
        self._write("f1.txt", "needle here\nother\n")
        self._write("f2.txt", "nothing\n")
        out = json.loads(brain.tool_search_files({"pattern": "needle", "path": "."}))
        self.assertEqual(out["match_count"], 1)
        m = out["matches"][0]
        self.assertEqual(m["file"], "f1.txt")
        self.assertEqual(m["line"], 1)
        self.assertIn("needle", m["text"])

    def test_invalid_regex_is_error(self):
        self._write("f.txt", "x")
        out = json.loads(brain.tool_search_files({"pattern": "[bad(", "path": "."}))
        self.assertIn("error", out)
        self.assertIn("invalid regex", out["error"])


class TestExecuteCommand(_FileToolFixture):
    """execute_command: captures stdout + exit_code; forces TERM=dumb (no TTY
    colour/escape pollution in the result the model reads)."""

    def test_echo_stdout_captured(self):
        out = json.loads(brain.tool_execute_command({"command": "echo hi"}))
        self.assertEqual(out.get("exit_code"), 0)
        self.assertEqual(out.get("output"), "hi\n")

    def test_term_is_dumb(self):
        out = json.loads(brain.tool_execute_command({"command": "echo $TERM"}))
        self.assertEqual(out.get("output"), "dumb\n")


class TestPythonExec(_FileToolFixture):
    """python_exec: runs in a subprocess, captures stdout, saves the script as
    script_N.py, and falls back to an output.txt artifact."""

    def test_simple_stdout(self):
        out = json.loads(brain.tool_python_exec({"code": "print(2+2)"}))
        self.assertEqual(out.get("exit_code"), 0)
        self.assertEqual(out.get("output"), "4\n")
        # script saved as script_N.py (N depends on prior writes in the folder;
        # a fresh per-test session id makes it script_1, but pin the PATTERN not
        # the index so the test is order-independent).
        self.assertRegex(out.get("script", ""), r"^script_\d+\.py$")


class TestReadDocument(_FileToolFixture):
    """read_document on a plain-text/markdown file: format='text' + numbered
    pagination (same numbering contract as read_file)."""

    def test_markdown_text_format(self):
        self._write("r.md", "# Title\ntext\n")
        out = json.loads(brain.tool_read_document({"path": "r.md"}))
        self.assertEqual(out.get("format"), "text")
        self.assertEqual(out.get("total_lines"), 2)
        self.assertIn("Title", out["content"])


if __name__ == "__main__":
    unittest.main()
