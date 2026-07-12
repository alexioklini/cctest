"""python_exec in a code-mode project: generated files must never touch the source tree.

The tool's own promise — "the working directory is the session's artifact folder,
files you write there become artifacts" — was FALSE in code mode, where cwd was the
PROJECT ROOT. Three separate leaks followed from that one lie:

  • the script itself → `script_1..8.py` dropped into the user's source tree
  • the stdout fallback → `output_1..5.txt` loose in `chats/`
  • the script's own writes → `open('report.html','w')` landed in the source tree

The first two were wrong destinations in our code. The third could only be fixed at
the CHOKE POINT — cwd — because the alternative (tell the model to use $BRAIN_OUT)
is a prompt dependency, and prompt dependencies fail here (v9.312.7: sub-agents were
told their folder and wrote elsewhere anyway).

cwd now IS the output folder, and the project's top-level entries are symlinked in
for the duration of the run so relative READS still reach the source. Symlinks, not
a monkeypatch prologue: patching open/glob/os.walk/Path means tracking stdlib call
graphs across versions (3.14's glob.glob delegates to iglob → infinite recursion;
Path.write_text calls self.open(mode=…) by keyword → double-bind). Symlinks make the
paths REAL, so every idiom — and every library we've never heard of — just works.

Run: python3 -m unittest tests.test_pyexec_code_mode
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402
from engine.tools import file_tools  # noqa: E402


class PyExecCodeModeTests(unittest.TestCase):
    SID = "ut-pyexec-cm"

    def setUp(self):
        self.wd = tempfile.mkdtemp(prefix="ut-cm-")
        os.makedirs(os.path.join(self.wd, "q1", "Queries"))
        for n, body in (("a.sql", "CREATE PROCEDURE sp_x AS SELECT 1;"),
                        ("b.sql", "CREATE PROCEDURE sp_y AS SELECT 2;")):
            with open(os.path.join(self.wd, "q1", "Queries", n), "w") as f:
                f.write(body)

    def tearDown(self):
        shutil.rmtree(self.wd, ignore_errors=True)

    def _run(self, code, timeout=None, bg_task=None):
        with request_context():
            c = get_request_context()
            c.current_agent = brain.AgentConfig("main")
            c.current_session_id = self.SID
            c.working_dir = self.wd
            c._dynamic["_codemode_chat_title"] = "UT"
            if bg_task:
                c.current_bg_task = True
                c.current_bg_task_id = bg_task
            args = {"code": code}
            if timeout:
                args["timeout"] = timeout
            raw = file_tools.tool_python_exec(args)
            out_dir = os.path.join(self.wd, brain.get_code_mode_chat_folder(self.SID))
        try:
            return json.loads(raw), out_dir
        except (ValueError, TypeError):
            return {"_raw": raw}, out_dir

    def _root_entries(self):
        return sorted(os.listdir(self.wd))

    def test_source_tree_is_never_polluted(self):
        """The reported bug: script_N.py landing next to the user's source."""
        _, out = self._run("print('hi')")
        self.assertEqual(self._root_entries(), ["chats", "q1"],
                         "python_exec wrote into the project root")
        self.assertIn("script_1.py", os.listdir(out))

    def test_stdout_fallback_lands_in_the_output_folder(self):
        """output_N.txt used to go to `watch_dir` — the whole `chats/` TREE, not
        this chat's folder — so they piled up loose in `chats/`. watch_dir is what
        we OBSERVE; it is not a place to write."""
        _, out = self._run("print('some output')")
        self.assertIn("output.txt", os.listdir(out))
        self.assertNotIn("output.txt", os.listdir(os.path.join(self.wd, "chats")))

    def test_relative_write_lands_in_output_folder_without_brain_out(self):
        """THE choke point: the model writes the way it naturally would — a bare
        relative path, no $BRAIN_OUT — and it must still be correct."""
        r, out = self._run("open('report.html','w').write('<h1>ok</h1>')")
        self.assertEqual(r["exit_code"], 0, r.get("output", ""))
        self.assertIn("report.html", os.listdir(out))
        self.assertEqual(self._root_entries(), ["chats", "q1"])

    def test_every_read_idiom_still_reaches_the_source(self):
        """Each of these broke, one after another, in the monkeypatch draft."""
        r, _ = self._run(
            "import os, glob\n"
            "from pathlib import Path\n"
            "print('open', open('q1/Queries/a.sql').read()[:6])\n"
            "print('glob', len(glob.glob('q1/Queries/*.sql')))\n"
            "print('rglob', len(list(Path('q1').rglob('*.sql'))))\n"
            "print('walk', sum(len(f) for _, _, f in os.walk('q1')))\n"
            "print('listdir', len(os.listdir('q1/Queries')))\n")
        self.assertEqual(r["exit_code"], 0, r.get("output", ""))
        o = r["output"]
        self.assertIn("open CREATE", o)
        self.assertIn("glob 2", o)
        self.assertIn("rglob 2", o)
        self.assertIn("walk 2", o)
        self.assertIn("listdir 2", o)

    def test_pathlib_write_text_works(self):
        """Regression: Path.write_text calls self.open(mode='w') BY KEYWORD, which
        double-bound the patched lambda's positional `mode`. Symlinks have no such
        failure mode — but assert the idiom, since it is what models reach for."""
        r, out = self._run("from pathlib import Path\nPath('d.csv').write_text('a,b\\n')")
        self.assertEqual(r["exit_code"], 0, r.get("output", ""))
        self.assertIn("d.csv", os.listdir(out))

    def test_nested_write_and_append(self):
        r, out = self._run(
            "import os\nfrom pathlib import Path\n"
            "os.makedirs('reports', exist_ok=True)\n"
            "Path('reports/deep.md').write_text('# x')\n"
            "open('log.txt','a').write('one\\n')\n")
        self.assertEqual(r["exit_code"], 0, r.get("output", ""))
        self.assertIn("log.txt", os.listdir(out))
        self.assertIn("deep.md", os.listdir(os.path.join(out, "reports")))

    def test_symlinks_are_removed_after_the_run(self):
        """Left behind, they would shadow the project inside the output folder AND
        make the artifact snapshot walk the entire source tree."""
        _, out = self._run("print('x')")
        links = [f for f in os.listdir(out)
                 if os.path.islink(os.path.join(out, f))]
        self.assertEqual(links, [], "source symlinks leaked into the output folder")
        self.assertNotIn("q1", os.listdir(out))

    def test_symlinks_are_removed_even_on_timeout(self):
        """Timeout and cancel `return` early — cleanup must sit in a finally."""
        _, out = self._run("import time; time.sleep(30)", timeout=2)
        links = [f for f in os.listdir(out)
                 if os.path.islink(os.path.join(out, f))]
        self.assertEqual(links, [], "a timed-out run leaked its source symlinks")

    def test_source_is_never_registered_as_an_artifact(self):
        r, _ = self._run("open('out.txt','w').write('x')")
        arts = r.get("artifacts") or []
        self.assertTrue(any("out.txt" in a for a in arts))
        self.assertFalse(any("q1" in a for a in arts),
                         f"the user's source was registered as an artifact: {arts}")

    def test_source_files_are_not_modified(self):
        self._run("open('report.html','w').write('x')")
        with open(os.path.join(self.wd, "q1", "Queries", "a.sql")) as f:
            self.assertTrue(f.read().startswith("CREATE"))

    def test_subagent_writes_into_its_own_folder_and_still_reads_source(self):
        """Concurrent fan-out tasks share the chat's session id, so without the
        per-task subfolder they resolve `report.html` to the same path."""
        r, out = self._run(
            "print(open('q1/Queries/a.sql').read()[:6])\n"
            "open('sub.md','w').write('# s')\n", bg_task="task-xyz")
        self.assertEqual(r["exit_code"], 0, r.get("output", ""))
        self.assertIn("CREATE", r["output"])
        self.assertTrue(out.endswith(os.path.join("subagents", "task-xyz")), out)
        self.assertIn("sub.md", os.listdir(out))
        self.assertEqual(self._root_entries(), ["chats", "q1"])


class PyExecPlainChatUnchangedTests(unittest.TestCase):
    """Outside code mode nothing may change: no symlinks, artifact folder as before."""
    SID = "ut-pyexec-plain"

    def test_plain_chat_is_untouched(self):
        with request_context():
            c = get_request_context()
            c.current_agent = brain.AgentConfig("main")
            c.current_session_id = self.SID
            r = json.loads(file_tools.tool_python_exec(
                {"code": "open('x.txt','w').write('hi')\nprint('ok')\n"}))
        af = os.path.join(brain.AGENTS_DIR, "main", "artifacts",
                          brain._get_artifact_session_folder(self.SID))
        try:
            self.assertEqual(r["exit_code"], 0)
            names = os.listdir(af)
            self.assertIn("x.txt", names)
            self.assertIn("script_1.py", names)
            self.assertEqual(
                [f for f in names if os.path.islink(os.path.join(af, f))], [],
                "plain chats must never get source symlinks")
        finally:
            shutil.rmtree(af, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
