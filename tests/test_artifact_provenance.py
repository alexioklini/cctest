"""Tests for artifact provenance (Quant-Workbench Phase B).

Contract: every artifact version a python_exec/r_exec SCRIPT produced carries
produced_by (the script name) + env_snapshot (the execution environment);
every OTHER writer (write_file here, execute_command by design) stays
honestly empty — None, never guessed. Pre-migration rows have NULL and the UI
must render them without the chips (covered client-side).

The exec tests register into the real chats.db (same side effect the
characterization suite already exercises) under a unique throwaway session id
that is fully cleaned up in tearDown.

Run: python3 -m unittest tests.test_artifact_provenance -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context  # noqa: E402
from engine.tools import file_tools  # noqa: E402


class _FakeAgent:
    agent_id = "main"


class _ProvFixture(unittest.TestCase):
    def setUp(self):
        self._prev_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="prov_test_")
        os.chdir(self._tmp)
        self._sid = "prov-test-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))

    def tearDown(self):
        os.chdir(self._prev_cwd)
        try:
            import shutil
            from server_lib.db import ChatDB
            from engine.tools.file_tools import _resolve_artifact_dir
            ChatDB.delete_artifacts_for_session(self._sid)
            with request_context(current_session_id=self._sid,
                                 current_agent=_FakeAgent()):
                art_dir, _ = _resolve_artifact_dir()
            if art_dir and self._sid in art_dir and os.path.isdir(art_dir):
                shutil.rmtree(art_dir)
        except Exception:
            pass

    def _artifact_versions(self, name):
        from server_lib.db import ChatDB
        arts = ChatDB.get_artifacts(self._sid)
        art = next((a for a in arts if a["name"] == name), None)
        self.assertIsNotNone(art, f"artifact {name} not registered "
                                   f"(have: {[a['name'] for a in arts]})")
        return art["versions"]


class TestProvenanceChain(_ProvFixture):
    def test_python_exec_file_carries_produced_by_and_env(self):
        out = json.loads(brain.tool_python_exec({
            "code": "open('report.csv','w').write('a;b\\n1;2\\n')"}))
        self.assertEqual(out.get("exit_code"), 0)
        vers = self._artifact_versions("report.csv")
        self.assertRegex(vers[-1]["produced_by"] or "", r"^script_\d+\.py$")
        self.assertTrue((vers[-1]["env_snapshot"] or "").startswith("py3."))

    def test_python_exec_stdout_fallback_carries_provenance(self):
        out = json.loads(brain.tool_python_exec({"code": "print('x'*10)"}))
        self.assertEqual(out.get("exit_code"), 0)
        vers = self._artifact_versions("output.txt")
        self.assertRegex(vers[-1]["produced_by"] or "", r"^script_\d+\.py$")

    def test_script_itself_has_no_produced_by(self):
        json.loads(brain.tool_python_exec({"code": "print(1)"}))
        from server_lib.db import ChatDB
        arts = ChatDB.get_artifacts(self._sid)
        script = next(a for a in arts if a["name"].startswith("script_"))
        self.assertIsNone(script["versions"][-1]["produced_by"])

    def test_write_file_stays_empty(self):
        """Characterization (plan criterion): write_file versions keep
        produced_by/env_snapshot NULL — no regression, no guessing."""
        out = json.loads(brain.tool_write_file({
            "path": "notes.md", "content": "# hi\n"}))
        self.assertNotIn("error", out)
        vers = self._artifact_versions("notes.md")
        self.assertIsNone(vers[-1]["produced_by"])
        self.assertIsNone(vers[-1]["env_snapshot"])

    @unittest.skipUnless(__import__("shutil").which("Rscript"),
                         "Rscript not installed")
    def test_r_exec_file_carries_r_env(self):
        out = json.loads(brain.tool_r_exec({
            "code": "write.csv(data.frame(a=1:3), 'r_out.csv')"}))
        self.assertEqual(out.get("exit_code"), 0)
        vers = self._artifact_versions("r_out.csv")
        self.assertRegex(vers[-1]["produced_by"] or "", r"^script_\d+\.R$")
        self.assertRegex(vers[-1]["env_snapshot"] or "", r"^R [\d.]+$")


class TestEnvSnapshot(unittest.TestCase):
    def test_py_snapshot_shape_and_cache(self):
        s1 = file_tools._env_snapshot_py("")
        s2 = file_tools._env_snapshot_py("")
        self.assertIs(s1, s2)  # cached per venv key
        self.assertRegex(s1, r"^py3\.\d+")


if __name__ == "__main__":
    unittest.main()
