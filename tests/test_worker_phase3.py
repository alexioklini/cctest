"""
Worker Subagent Phase 3 — Concurrent cap, session cascade, admin endpoint.
"""

import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution
from execution import WorkerState, WorkerRegistry, Worker, get_worker_registry, _TERMINAL_STATES
from claude_cli import _ok, _err, _thread_local


def _mock_tool(name, args):
    return _ok({"mock": True, "tool": name})


def _slow_tool(name, args):
    time.sleep(0.3)
    return _ok({"mock": True, "tool": name})


class TestConcurrentWorkerCap(unittest.TestCase):

    _counter = 0

    def setUp(self):
        TestConcurrentWorkerCap._counter += 1
        self.sid = f"test_cap_{TestConcurrentWorkerCap._counter}"
        _thread_local.current_session_id = self.sid
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.current_agent.config = {}
        _thread_local._current_model = ""
        _thread_local.event_callback = None
        execution._config_cache = {
            "workers_enabled": True,
            "auto_threshold_bytes": 8192,
            "worker_timeout_seconds": 120,
            "max_concurrent_workers_per_session": 2,
            "summariser_max_input_chars": 32000,
            "profiles": dict(execution.DEFAULT_PROFILES),
        }
        execution._config_cache_time = time.time()

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.current_agent = None
        _thread_local.in_worker_subagent = False
        _thread_local.current_worker_id = None
        execution._config_cache = None

    @patch('claude_cli._register_artifact_version', return_value=None)
    @patch('execution._summarise_tool_result')
    def test_cap_blocks_third_worker(self, mock_sum, mock_reg):
        mock_sum.return_value = ("summary", [])
        registry = get_worker_registry()

        # Pre-register 2 active workers for this session
        for i in range(2):
            w = Worker(
                worker_id=f"wkr_cap_{self.sid}_{i}",
                session_id=self.sid,
                parent_call_id=f"tc_{i}",
                agent_id="main",
                tool_name="exa_search",
                state=WorkerState.RUNNING,
                started_at=time.time(),
            )
            registry.register(w)

        # Third worker should be rejected
        _thread_local.tool_use_id = f"tu_cap3_{self.sid}"
        result = execution.run_worker_subagent("exa_search", {"q": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertIn("error", data)
        self.assertIn("limit reached", data["error"])

    @patch('claude_cli._register_artifact_version', return_value=None)
    @patch('execution._summarise_tool_result')
    def test_cap_allows_after_completion(self, mock_sum, mock_reg):
        mock_sum.return_value = ("summary", [])
        registry = get_worker_registry()

        # Register 2 workers, one completed
        w1 = Worker(
            worker_id=f"wkr_done_{self.sid}",
            session_id=self.sid,
            parent_call_id="tc_d",
            agent_id="main",
            tool_name="exa_search",
            state=WorkerState.COMPLETED,
            started_at=time.time(),
        )
        w2 = Worker(
            worker_id=f"wkr_active_{self.sid}",
            session_id=self.sid,
            parent_call_id="tc_a",
            agent_id="main",
            tool_name="web_fetch",
            state=WorkerState.RUNNING,
            started_at=time.time(),
        )
        registry.register(w1)
        registry.register(w2)

        # Only 1 active, so third should be allowed (cap=2)
        _thread_local.tool_use_id = f"tu_ok_{self.sid}"
        result = execution.run_worker_subagent("exa_search", {"q": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertTrue(data.get("worker"))


class TestSessionDeleteCascade(unittest.TestCase):

    def test_abort_on_delete(self):
        registry = get_worker_registry()
        sid = "test_cascade_del_001"
        w1 = Worker(
            worker_id="wkr_del_1", session_id=sid,
            parent_call_id="tc1", agent_id="main", tool_name="test",
            state=WorkerState.RUNNING, started_at=time.time(),
        )
        w2 = Worker(
            worker_id="wkr_del_2", session_id=sid,
            parent_call_id="tc2", agent_id="main", tool_name="test",
            state=WorkerState.PAUSED, started_at=time.time(),
        )
        registry.register(w1)
        registry.register(w2)
        _thread_local.event_callback = None

        count = registry.abort_session(sid, "session_deleted")
        self.assertEqual(count, 2)
        self.assertEqual(w1.state, WorkerState.ABORTED)
        self.assertEqual(w2.state, WorkerState.ABORTED)
        self.assertTrue(w1.cancel_event.is_set())
        self.assertTrue(w2.cancel_event.is_set())
        self.assertEqual(w1.abort_reason, "session_deleted")


class TestWorkerAskUser(unittest.TestCase):

    def test_ask_user_outside_worker(self):
        from claude_cli import tool_worker_ask_user
        _thread_local.current_worker_id = None
        result = json.loads(tool_worker_ask_user({"question": "test?"}))
        self.assertIn("error", result)

    def test_ask_user_missing_question(self):
        from claude_cli import tool_worker_ask_user
        _thread_local.current_worker_id = "wkr_test"
        try:
            result = json.loads(tool_worker_ask_user({}))
            self.assertIn("error", result)
        finally:
            _thread_local.current_worker_id = None


class TestGetArtifactDetail(unittest.TestCase):

    def setUp(self):
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        # Create a test artifact
        from claude_cli import AGENTS_DIR
        self.artifact_dir = os.path.join(AGENTS_DIR, "main", "artifacts", "test_detail")
        os.makedirs(self.artifact_dir, exist_ok=True)
        self.artifact_path = os.path.join(self.artifact_dir, "worker_test_detail.json")
        with open(self.artifact_path, "w") as f:
            json.dump({
                "tool": "exa_search",
                "args": {"query": "test"},
                "raw_result": "line 1: hello\nline 2: world\nline 3: foo bar\nline 4: baz",
                "size_bytes": 50,
            }, f)

    def tearDown(self):
        _thread_local.current_agent = None
        if os.path.exists(self.artifact_path):
            os.unlink(self.artifact_path)
        try:
            os.rmdir(self.artifact_dir)
        except OSError:
            pass

    def test_retrieve_full(self):
        from claude_cli import tool_get_artifact_detail
        result = json.loads(tool_get_artifact_detail({"artifact_id": "worker_test_detail.json"}))
        self.assertIn("content", result)
        self.assertIn("hello", result["content"])
        self.assertIn("world", result["content"])

    def test_retrieve_with_query(self):
        from claude_cli import tool_get_artifact_detail
        result = json.loads(tool_get_artifact_detail({
            "artifact_id": "worker_test_detail.json",
            "query": "foo",
        }))
        self.assertIn("foo bar", result["content"])

    def test_retrieve_with_limit(self):
        from claude_cli import tool_get_artifact_detail
        result = json.loads(tool_get_artifact_detail({
            "artifact_id": "worker_test_detail.json",
            "limit": 20,
        }))
        self.assertLessEqual(len(result["content"]), 100)  # 20 + truncation message

    def test_not_found(self):
        from claude_cli import tool_get_artifact_detail
        result = json.loads(tool_get_artifact_detail({"artifact_id": "nonexistent.json"}))
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
