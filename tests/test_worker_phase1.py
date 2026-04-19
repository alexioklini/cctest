"""
Worker Subagent Phase 1 — Core tests.

Tests the routing, artifact creation, idempotency, and feature flag
behaviour of the execution.py worker wrapper.
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
from claude_cli import _ok, _err, _thread_local


def _mock_tool(name: str, args: dict) -> str:
    """Deterministic mock tool that returns a result based on name/args."""
    return _ok({"mock": True, "tool": name, "echo": args})


def _mock_tool_large(name: str, args: dict) -> str:
    """Mock tool that returns >8KB output."""
    return _ok({"mock": True, "data": "x" * 10000})


def _mock_tool_small(name: str, args: dict) -> str:
    """Mock tool that returns <8KB output."""
    return _ok({"mock": True, "data": "small"})


class TestHeavinessResolution(unittest.TestCase):

    def test_default_profiles_heavy(self):
        h = execution._resolve_heaviness("exa_search", {})
        self.assertEqual(h, "heavy")

    def test_default_profiles_light(self):
        h = execution._resolve_heaviness("read_file", {})
        self.assertEqual(h, "light")

    def test_unknown_tool_auto(self):
        h = execution._resolve_heaviness("some_unknown_tool_xyz", {})
        self.assertEqual(h, "auto")

    def test_agent_override_bool(self):
        _thread_local.execution_overrides = {"exa_search": False}
        try:
            h = execution._resolve_heaviness("exa_search", {})
            self.assertEqual(h, "light")
        finally:
            _thread_local.execution_overrides = {}

    def test_agent_override_string(self):
        _thread_local.execution_overrides = {"read_file": "heavy"}
        try:
            h = execution._resolve_heaviness("read_file", {})
            self.assertEqual(h, "heavy")
        finally:
            _thread_local.execution_overrides = {}


class TestRouterDirect(unittest.TestCase):
    """Test that light tools pass through unchanged."""

    def test_light_tool_passthrough(self):
        result = execution.route_tool_execution("read_file", {"path": "/tmp/x"}, _mock_tool)
        data = json.loads(result)
        self.assertTrue(data.get("mock"))
        self.assertEqual(data["tool"], "read_file")
        self.assertNotIn("worker", data)

    def test_feature_flag_off(self):
        execution._config_cache = {
            "workers_enabled": False,
            "auto_threshold_bytes": 8192,
            "worker_timeout_seconds": 120,
            "max_concurrent_workers_per_session": 3,
            "summariser_max_input_chars": 32000,
            "profiles": dict(execution.DEFAULT_PROFILES),
        }
        execution._config_cache_time = time.time()
        try:
            result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
            data = json.loads(result)
            self.assertTrue(data.get("mock"))
            self.assertNotIn("worker", data)
        finally:
            execution._config_cache = None

    def test_nested_worker_goes_direct(self):
        _thread_local.in_worker_subagent = True
        try:
            result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
            data = json.loads(result)
            self.assertTrue(data.get("mock"))
            self.assertNotIn("worker", data)
        finally:
            _thread_local.in_worker_subagent = False


class TestWorkerExecution(unittest.TestCase):
    """Test that heavy tools produce the worker envelope + artifact."""

    _counter = 0

    def setUp(self):
        TestWorkerExecution._counter += 1
        _thread_local.current_session_id = f"test_session_{TestWorkerExecution._counter}"
        _thread_local.tool_use_id = f"tu_{TestWorkerExecution._counter}_{int(time.time())}"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.event_callback = None
        execution._config_cache = None

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.tool_use_id = None
        _thread_local.current_agent = None
        _thread_local.in_worker_subagent = False

    @patch('claude_cli._register_artifact_version', return_value=None)
    def test_heavy_tool_envelope(self, mock_reg):
        result = execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)
        data = json.loads(result)
        self.assertTrue(data.get("worker"))
        self.assertIn(data["worker_phase"], (1, 2))
        self.assertIn("artifacts", data)
        self.assertEqual(len(data["artifacts"]), 1)
        self.assertIn("summary", data)
        self.assertIn("duration_seconds", data)
        artifact = data["artifacts"][0]
        self.assertEqual(artifact["tool"], "exa_search")
        self.assertTrue(os.path.exists(artifact["path"]))
        os.unlink(artifact["path"])

    @patch('claude_cli._register_artifact_version', return_value=None)
    def test_artifact_content(self, mock_reg):
        result = execution.route_tool_execution("exa_search", {"query": "hello"}, _mock_tool)
        data = json.loads(result)
        artifact_path = data["artifacts"][0]["path"]
        with open(artifact_path) as f:
            artifact = json.load(f)
        self.assertEqual(artifact["tool"], "exa_search")
        raw = json.loads(artifact["raw_result"])
        self.assertTrue(raw.get("mock"))
        os.unlink(artifact_path)


class TestAutoIsolation(unittest.TestCase):
    """Test auto-threshold retroactive isolation."""

    def setUp(self):
        _thread_local.current_session_id = "test_auto_001"
        _thread_local.tool_use_id = f"tu_auto_{int(time.time())}"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.event_callback = None
        execution._config_cache = {
            "workers_enabled": True,
            "auto_threshold_bytes": 8192,
            "worker_timeout_seconds": 120,
            "max_concurrent_workers_per_session": 3,
            "summariser_max_input_chars": 32000,
            "profiles": dict(execution.DEFAULT_PROFILES),
        }
        execution._config_cache_time = time.time()

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.tool_use_id = None
        _thread_local.current_agent = None
        execution._config_cache = None

    @patch('claude_cli._register_artifact_version', return_value=None)
    def test_auto_large_output_isolated(self, mock_reg):
        result = execution.route_tool_execution("some_auto_tool", {}, _mock_tool_large)
        data = json.loads(result)
        self.assertTrue(data.get("worker"))
        self.assertTrue(data.get("auto_isolated"))
        artifact_path = data["artifacts"][0]["path"]
        if os.path.exists(artifact_path):
            os.unlink(artifact_path)

    def test_auto_small_output_passthrough(self):
        result = execution.route_tool_execution("some_auto_tool", {}, _mock_tool_small)
        data = json.loads(result)
        self.assertTrue(data.get("mock"))
        self.assertNotIn("worker", data)


class TestIdempotency(unittest.TestCase):
    """Test that concurrent calls with same key only run once."""

    def setUp(self):
        _thread_local.current_session_id = "test_idem_001"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        _thread_local.event_callback = None
        execution._config_cache = None

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.current_agent = None

    @patch('claude_cli._register_artifact_version', return_value=None)
    def test_concurrent_same_key(self, mock_reg):
        call_count = {"n": 0}
        shared_tool_use_id = "tu_shared_123"

        def slow_tool(name, args):
            call_count["n"] += 1
            time.sleep(0.2)
            return _ok({"mock": True, "tool": name})

        results = [None, None]
        errors = [None, None]

        def run(idx):
            _thread_local.current_session_id = "test_idem_001"
            _thread_local.tool_use_id = shared_tool_use_id
            _thread_local.current_agent = MagicMock()
            _thread_local.current_agent.agent_id = "main"
            _thread_local.event_callback = None
            try:
                results[idx] = execution.run_worker_subagent("exa_search", {"q": "test"}, slow_tool)
            except Exception as e:
                errors[idx] = e

        t1 = threading.Thread(target=run, args=(0,))
        t2 = threading.Thread(target=run, args=(1,))
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertIsNone(errors[0])
        self.assertIsNone(errors[1])
        self.assertEqual(call_count["n"], 1, "Tool should only execute once")
        self.assertEqual(results[0], results[1], "Both threads should get same result")

        data = json.loads(results[0])
        if "artifacts" in data:
            for a in data["artifacts"]:
                if os.path.exists(a["path"]):
                    os.unlink(a["path"])


class TestSSEEvents(unittest.TestCase):
    """Test that worker.started and worker.finished events are emitted."""

    def setUp(self):
        _thread_local.current_session_id = "test_sse_001"
        _thread_local.tool_use_id = f"tu_sse_{int(time.time())}"
        _thread_local.current_agent = MagicMock()
        _thread_local.current_agent.agent_id = "main"
        execution._config_cache = None

    def tearDown(self):
        _thread_local.current_session_id = None
        _thread_local.tool_use_id = None
        _thread_local.current_agent = None
        _thread_local.event_callback = None

    @patch('claude_cli._register_artifact_version', return_value=None)
    def test_events_emitted(self, mock_reg):
        events = []
        _thread_local.event_callback = lambda etype, payload: events.append((etype, payload))

        execution.route_tool_execution("exa_search", {"query": "test"}, _mock_tool)

        event_types = [e[0] for e in events]
        self.assertIn("worker.started", event_types)
        self.assertIn("worker.finished", event_types)

        started = [e for e in events if e[0] == "worker.started"][0][1]
        self.assertEqual(started["tool_name"], "exa_search")

        finished = [e for e in events if e[0] == "worker.finished"][0][1]
        self.assertEqual(finished["tool_name"], "exa_search")
        self.assertIn("duration_seconds", finished)
        self.assertEqual(finished["artifact_count"], 1)


if __name__ == "__main__":
    unittest.main()
