"""
Worker Subagent Phase 2 — Summariser, WorkerRegistry, control tools.
"""

import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import execution
from engine.execution import WorkerState, WorkerRegistry, Worker, get_worker_registry
from claude_cli import _ok, _err, _thread_local


def _mock_tool(name: str, args: dict) -> str:
    return _ok({"mock": True, "tool": name, "echo": args})


# ---------- Summariser Tests ----------

class TestSummariserParsing(unittest.TestCase):

    def test_parse_with_sections(self):
        text = (
            "Found 3 results about DORA compliance.\n"
            'SECTIONS: [{"label": "Article 30", "line_start": 5, "line_end": 15}]'
        )
        summary, sections = execution._parse_summariser_output(text)
        self.assertIn("DORA", summary)
        self.assertNotIn("SECTIONS", summary)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["label"], "Article 30")

    def test_parse_no_sections(self):
        text = "Simple result with no sections marker."
        summary, sections = execution._parse_summariser_output(text)
        self.assertEqual(summary, text)
        self.assertEqual(sections, [])

    def test_parse_empty_sections(self):
        text = "Some summary.\nSECTIONS: []"
        summary, sections = execution._parse_summariser_output(text)
        self.assertEqual(summary, "Some summary.")
        self.assertEqual(sections, [])

    def test_parse_malformed_sections(self):
        text = "Summary text.\nSECTIONS: not valid json"
        summary, sections = execution._parse_summariser_output(text)
        self.assertEqual(summary, "Summary text.")
        self.assertEqual(sections, [])


class TestSummariserIntegration(unittest.TestCase):

    _counter = 0

    def setUp(self):
        TestSummariserIntegration._counter += 1
        _thread_local.current_session_id = f"test_sum_{TestSummariserIntegration._counter}"
        _thread_local.tool_use_id = f"tu_sum_{TestSummariserIntegration._counter}"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.current_agent.config = {}
        _thread_local._current_model = "test-model"
        _thread_local.event_callback = None
        execution._config_cache = None

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.tool_use_id = None
        _thread_local.current_agent = None
        _thread_local._current_model = None
        _thread_local.in_worker_subagent = False
        _thread_local.current_worker_id = None

    @patch('claude_cli._register_artifact_version', return_value=None)
    @patch('claude_cli.send_message_with_fallback')
    @patch('claude_cli.resolve_provider_for_model')
    def test_summariser_called(self, mock_resolve, mock_send, mock_reg):
        mock_resolve.return_value = {"api_key": "k", "base_url": "http://x"}
        mock_send.return_value = "Found 3 web results about testing.\nSECTIONS: []"

        result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertTrue(data.get("worker"))
        self.assertEqual(data["worker_phase"], 2)
        self.assertIn("Found 3 web results", data["summary"])
        self.assertEqual(data["sections"], [])
        mock_send.assert_called_once()
        # Cleanup artifact
        for a in data.get("artifacts", []):
            if os.path.exists(a["path"]):
                os.unlink(a["path"])

    @patch('claude_cli._register_artifact_version', return_value=None)
    @patch('claude_cli.send_message_with_fallback')
    @patch('claude_cli.resolve_provider_for_model')
    def test_summariser_fallback_on_error(self, mock_resolve, mock_send, mock_reg):
        mock_resolve.side_effect = Exception("provider not found")

        result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertTrue(data.get("worker"))
        # Should fall back to static summary
        self.assertIn("stored as artifact", data["summary"])
        for a in data.get("artifacts", []):
            if os.path.exists(a["path"]):
                os.unlink(a["path"])

    @patch('claude_cli._register_artifact_version', return_value=None)
    @patch('claude_cli.send_message_with_fallback')
    @patch('claude_cli.resolve_provider_for_model')
    def test_summariser_with_sections(self, mock_resolve, mock_send, mock_reg):
        mock_resolve.return_value = {"api_key": "k", "base_url": "http://x"}
        mock_send.return_value = (
            'Results summary.\n'
            'SECTIONS: [{"label": "Section A", "line_start": 1, "line_end": 10}]'
        )

        result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertEqual(len(data["sections"]), 1)
        self.assertEqual(data["sections"][0]["label"], "Section A")
        for a in data.get("artifacts", []):
            if os.path.exists(a["path"]):
                os.unlink(a["path"])


# ---------- WorkerRegistry Tests ----------

class TestWorkerRegistry(unittest.TestCase):

    def setUp(self):
        self.registry = WorkerRegistry()

    def _make_worker(self, wid="wkr_test_1", sid="sess_1") -> Worker:
        w = Worker(
            worker_id=wid,
            session_id=sid,
            parent_call_id="tc_1",
            agent_id="main",
            tool_name="exa_search",
        )
        return w

    def test_register_and_get(self):
        w = self._make_worker()
        self.registry.register(w)
        self.assertIs(self.registry.get("wkr_test_1"), w)
        self.assertIsNone(self.registry.get("nonexistent"))

    def test_list_session(self):
        w1 = self._make_worker("w1", "s1")
        w2 = self._make_worker("w2", "s1")
        w3 = self._make_worker("w3", "s2")
        self.registry.register(w1)
        self.registry.register(w2)
        self.registry.register(w3)
        self.assertEqual(len(self.registry.list_session("s1")), 2)
        self.assertEqual(len(self.registry.list_session("s2")), 1)

    def test_state_transitions(self):
        w = self._make_worker()
        self.registry.register(w)
        self.assertTrue(self.registry.update_state("wkr_test_1", WorkerState.RUNNING))
        self.assertEqual(w.state, WorkerState.RUNNING)
        self.assertTrue(self.registry.update_state("wkr_test_1", WorkerState.PAUSED))
        self.assertEqual(w.state, WorkerState.PAUSED)
        self.assertTrue(self.registry.update_state("wkr_test_1", WorkerState.RUNNING))
        self.assertTrue(self.registry.update_state("wkr_test_1", WorkerState.COMPLETED))
        self.assertEqual(w.state, WorkerState.COMPLETED)

    def test_invalid_transition(self):
        w = self._make_worker()
        self.registry.register(w)
        self.registry.update_state("wkr_test_1", WorkerState.RUNNING)
        # Can't go from RUNNING directly to QUEUED
        self.assertFalse(self.registry.update_state("wkr_test_1", WorkerState.QUEUED))

    def test_terminal_state_immutable(self):
        w = self._make_worker()
        self.registry.register(w)
        self.registry.update_state("wkr_test_1", WorkerState.RUNNING)
        self.registry.update_state("wkr_test_1", WorkerState.COMPLETED)
        self.assertFalse(self.registry.update_state("wkr_test_1", WorkerState.RUNNING))

    def test_cancel_idempotent(self):
        w = self._make_worker()
        self.registry.register(w)
        self.registry.update_state("wkr_test_1", WorkerState.RUNNING)
        _thread_local.event_callback = None
        self.assertTrue(self.registry.cancel("wkr_test_1", "test"))
        self.assertEqual(w.state, WorkerState.ABORTED)
        self.assertTrue(w.cancel_event.is_set())
        # Second cancel is idempotent
        self.assertTrue(self.registry.cancel("wkr_test_1", "again"))

    def test_pause_resume(self):
        w = self._make_worker()
        self.registry.register(w)
        self.registry.update_state("wkr_test_1", WorkerState.RUNNING)
        _thread_local.event_callback = None
        self.assertTrue(self.registry.pause("wkr_test_1", "thinking"))
        self.assertEqual(w.state, WorkerState.PAUSED)
        self.assertTrue(w.pause_event.is_set())
        self.assertTrue(self.registry.resume("wkr_test_1"))
        self.assertEqual(w.state, WorkerState.RUNNING)
        self.assertFalse(w.pause_event.is_set())

    def test_send_resumes_paused(self):
        w = self._make_worker()
        self.registry.register(w)
        self.registry.update_state("wkr_test_1", WorkerState.RUNNING)
        _thread_local.event_callback = None
        self.registry.pause("wkr_test_1")
        self.assertTrue(self.registry.send("wkr_test_1", "new info", "user"))
        self.assertEqual(w.state, WorkerState.RUNNING)
        msg = w.input_queue.get_nowait()
        self.assertEqual(msg["content"], "new info")

    def test_abort_session(self):
        w1 = self._make_worker("w1", "s1")
        w2 = self._make_worker("w2", "s1")
        self.registry.register(w1)
        self.registry.register(w2)
        self.registry.update_state("w1", WorkerState.RUNNING)
        self.registry.update_state("w2", WorkerState.RUNNING)
        _thread_local.event_callback = None
        count = self.registry.abort_session("s1", "session deleted")
        self.assertEqual(count, 2)
        self.assertEqual(w1.state, WorkerState.ABORTED)
        self.assertEqual(w2.state, WorkerState.ABORTED)

    def test_to_status_dict(self):
        w = self._make_worker()
        w.started_at = time.time() - 5
        w.state = WorkerState.RUNNING
        w.phase = "fetching"
        d = self.registry.to_status_dict(w)
        self.assertEqual(d["worker_id"], "wkr_test_1")
        self.assertEqual(d["state"], "RUNNING")
        self.assertGreater(d["elapsed_seconds"], 4)
        self.assertEqual(d["phase"], "fetching")


class TestAskUserFlow(unittest.TestCase):

    def setUp(self):
        self.registry = WorkerRegistry()
        _thread_local.event_callback = None

    def test_ask_and_answer(self):
        w = Worker(
            worker_id="wkr_q1", session_id="s1",
            parent_call_id="tc1", agent_id="main", tool_name="test",
        )
        self.registry.register(w)
        self.registry.update_state("wkr_q1", WorkerState.RUNNING)

        result = [None]
        def ask_thread():
            result[0] = self.registry.ask_user("wkr_q1", "Which version?",
                                                ["v1", "v2"], timeout_seconds=5)

        t = threading.Thread(target=ask_thread)
        t.start()
        time.sleep(0.1)
        self.assertEqual(w.state, WorkerState.WAITING_FOR_USER)
        self.assertTrue(self.registry.answer("wkr_q1", "v2"))
        t.join(timeout=3)
        self.assertEqual(result[0], "v2")

    def test_ask_timeout(self):
        w = Worker(
            worker_id="wkr_q2", session_id="s1",
            parent_call_id="tc1", agent_id="main", tool_name="test",
        )
        self.registry.register(w)
        self.registry.update_state("wkr_q2", WorkerState.RUNNING)

        result = self.registry.ask_user("wkr_q2", "Quick?", timeout_seconds=0.2)
        self.assertIsNone(result)
        self.assertEqual(w.state, WorkerState.ABORTED)


# ---------- Control Tool Handler Tests ----------

class TestControlTools(unittest.TestCase):

    def setUp(self):
        _thread_local.current_session_id = "test_ctrl_001"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.event_callback = None
        # Register a test worker
        w = Worker(
            worker_id="wkr_ctrl_1", session_id="test_ctrl_001",
            parent_call_id="tc1", agent_id="main", tool_name="exa_search",
            state=WorkerState.RUNNING, started_at=time.time(),
        )
        execution._worker_registry.register(w)

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.current_agent = None

    def test_worker_status(self):
        from claude_cli import tool_worker_status
        result = json.loads(tool_worker_status({}))
        self.assertIn("workers", result)
        self.assertGreaterEqual(len(result["workers"]), 1)

    def test_worker_status_by_id(self):
        from claude_cli import tool_worker_status
        result = json.loads(tool_worker_status({"worker_id": "wkr_ctrl_1"}))
        self.assertEqual(len(result["workers"]), 1)
        self.assertEqual(result["workers"][0]["tool"], "exa_search")

    def test_worker_abort(self):
        from claude_cli import tool_worker_abort
        result = json.loads(tool_worker_abort({"worker_id": "wkr_ctrl_1", "reason": "test"}))
        self.assertTrue(result.get("aborted"))

    def test_worker_pause_resume(self):
        from claude_cli import tool_worker_pause, tool_worker_resume
        # Re-register as running (may have been aborted by previous test)
        w = Worker(
            worker_id="wkr_pr_1", session_id="test_ctrl_001",
            parent_call_id="tc2", agent_id="main", tool_name="web_fetch",
            state=WorkerState.RUNNING, started_at=time.time(),
        )
        execution._worker_registry.register(w)
        result = json.loads(tool_worker_pause({"worker_id": "wkr_pr_1"}))
        self.assertTrue(result.get("paused"))
        result = json.loads(tool_worker_resume({"worker_id": "wkr_pr_1"}))
        self.assertTrue(result.get("resumed"))


if __name__ == "__main__":
    unittest.main()
