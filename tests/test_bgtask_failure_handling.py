"""Fan-out/subagent failure-handling hardening (2026-07-13).

Covers the WHY of each guarantee:
  - retry cap: a failed task may be retried EXACTLY once, never a retry-of-a-
    retry, never a user-cancelled task — the model must not be able to loop.
  - failure classes: timeout/empty are distinct statuses (they drive different
    delivery-preamble guidance than error/cancelled).
  - delivery preamble: failed members carry their task_id (the retry handle),
    error/timeout/empty produce retry instructions, USER-cancel produces a
    do-NOT-restart instruction, and a retry group re-attaches the original
    group's already-consumed sibling outputs.
  - wall-clock timeout in run_turn_blocking: timeout_s is enforced (it was a
    dead parameter before) and surfaces loud (`timed_out` + error).
  - Stopp-cascade scoping: cancel_session_tasks(spawn_turn_id=…) touches only
    the tasks of that turn.

Run: python3 -m unittest tests.test_bgtask_failure_handling
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server_lib.db import ChatDB, _db_conn  # noqa: E402


def _wipe(session_id):
    with _db_conn() as c:
        c.execute("DELETE FROM background_tasks WHERE session_id=?", (session_id,))
        c.commit()


class TestRetryCap(unittest.TestCase):
    SID = "ut-bgtask-retry"

    @classmethod
    def setUpClass(cls):
        ChatDB.init()

    def setUp(self):
        _wipe(self.SID)

    def tearDown(self):
        _wipe(self.SID)

    def test_retry_exists_gate(self):
        ChatDB.create_background_task("orig", self.SID, "main", "m", "T", "p",
                                      group_id="g1")
        ChatDB.finish_background_task("orig", "error", error="boom")
        self.assertFalse(ChatDB.background_task_retry_exists(self.SID, "orig"))
        # Spawn the retry clone → a second retry must be refused by the gate.
        ChatDB.create_background_task("retry1", self.SID, "main", "m", "T", "p",
                                      group_id="g2", retry_of="orig")
        self.assertTrue(ChatDB.background_task_retry_exists(self.SID, "orig"))

    def test_retry_of_is_persisted_and_listed(self):
        ChatDB.create_background_task("r", self.SID, "main", "m", "T", "p",
                                      group_id="g", retry_of="someorig",
                                      spawn_turn_id="turn-1")
        row = ChatDB.get_background_task("r")
        self.assertEqual(row["retry_of"], "someorig")
        self.assertEqual(row["spawn_turn_id"], "turn-1")
        listed = ChatDB.list_background_tasks(self.SID)[0]
        self.assertEqual(listed["retry_of"], "someorig")
        self.assertEqual(listed["spawn_turn_id"], "turn-1")

    def test_tool_refuses_second_retry_and_user_cancel(self):
        # Tool-level checks that don't need a live session manager: they fail
        # BEFORE the session lookup.
        from engine.context import request_context, get_request_context
        from engine.tools.delegation_tools import tool_retry_background_task
        ChatDB.create_background_task("o1", self.SID, "main", "m", "T", "p")
        ChatDB.finish_background_task("o1", "cancelled", output="partial")
        ChatDB.create_background_task("o2", self.SID, "main", "m", "T", "p",
                                      retry_of="elsewhere")
        ChatDB.finish_background_task("o2", "error", error="x")
        ChatDB.create_background_task("o3", self.SID, "main", "m", "T", "p")
        ChatDB.finish_background_task("o3", "error", error="x")
        ChatDB.create_background_task("o3-r", self.SID, "main", "m", "T", "p",
                                      retry_of="o3")
        with request_context():
            ctx = get_request_context()
            ctx.current_session_id = self.SID
            # user-cancelled → refused with the do-not-restart guidance
            res = tool_retry_background_task({"task_id": "o1"})
            self.assertIn("BY THE USER", res)
            # a retry itself → refused
            res = tool_retry_background_task({"task_id": "o2"})
            self.assertIn("already a retry", res)
            # already retried once → refused
            res = tool_retry_background_task({"task_id": "o3"})
            self.assertIn("already retried", res)
            # nesting guard: no retry from inside a background task
            ctx.current_bg_task = True
            res = tool_retry_background_task({"task_id": "o3"})
            self.assertIn("inside a background", res)


class TestDeliveryPreambleClasses(unittest.TestCase):
    SID = "ut-bgtask-preamble"

    @classmethod
    def setUpClass(cls):
        ChatDB.init()

    def setUp(self):
        _wipe(self.SID)

    def tearDown(self):
        _wipe(self.SID)

    def _preamble(self, members):
        from handlers.chat import _build_group_preamble
        return _build_group_preamble(members)

    def test_error_member_gets_retry_instruction_with_task_id(self):
        p = self._preamble([
            {"id": "t1", "session_id": self.SID, "title": "A", "status": "done",
             "output": "OK", "error": "", "follow_up": "combine"},
            {"id": "t2", "session_id": self.SID, "title": "B", "status": "error",
             "output": "", "error": "HTTP 500", "follow_up": ""},
        ])
        self.assertIn("task_id: t2", p)
        self.assertIn("retry_background_task", p)
        self.assertIn("EIN Neustart", p)
        self.assertIn("combine", p)

    def test_timeout_and_empty_classified(self):
        p = self._preamble([
            {"id": "t1", "session_id": self.SID, "title": "A", "status": "timeout",
             "output": "partial", "error": "Zeitlimit", "follow_up": ""},
            {"id": "t2", "session_id": self.SID, "title": "B", "status": "empty",
             "output": "", "error": "Leere Antwort", "follow_up": ""},
        ])
        self.assertIn("Zeitlimit überschritten — Teilergebnis", p)
        self.assertIn("leere Antwort", p)
        self.assertIn("retry_background_task", p)

    def test_user_cancel_gets_do_not_restart_and_no_retry_hint(self):
        p = self._preamble([
            {"id": "t1", "session_id": self.SID, "title": "A", "status": "cancelled",
             "output": "partial", "error": "", "follow_up": ""},
        ])
        self.assertIn("VOM NUTZER", p)
        self.assertIn("NICHT neu", p)
        self.assertNotIn("retry_background_task", p)

    def test_clean_group_has_no_decision_tail(self):
        p = self._preamble([
            {"id": "t1", "session_id": self.SID, "title": "A", "status": "done",
             "output": "OK", "error": "", "follow_up": ""},
        ])
        self.assertNotIn("retry_background_task", p)
        self.assertNotIn("VOM NUTZER", p)

    def test_retry_group_reattaches_original_siblings(self):
        # Original group: sibling done + orig failed; retry group contains the
        # retry clone. The sibling's output must be re-delivered alongside.
        ChatDB.create_background_task("sib", self.SID, "main", "m", "Sibling", "p",
                                      group_id="gorig")
        ChatDB.finish_background_task("sib", "done", output="SIBLING-RESULT")
        ChatDB.create_background_task("orig", self.SID, "main", "m", "Failed", "p",
                                      group_id="gorig")
        ChatDB.finish_background_task("orig", "error", error="boom")
        p = self._preamble([
            {"id": "rt", "session_id": self.SID, "title": "Failed",
             "status": "done", "output": "RETRY-RESULT", "error": "",
             "follow_up": "combine", "retry_of": "orig"},
        ])
        self.assertIn("RETRY-RESULT", p)
        self.assertIn("SIBLING-RESULT", p)
        self.assertIn("ursprünglichen", p)


class TestBlockingTimeout(unittest.TestCase):
    def test_run_turn_blocking_enforces_wall_clock(self):
        # A loop that never observes cancellation would spin forever; the
        # deadline must flip is_cancelled and surface timed_out + a loud error.
        # Patch run_loop with a fake that finishes only when cancelled.
        import time as _time
        from handlers import sidecar_proxy

        def fake_run_loop(**kw):
            t0 = _time.time()
            while not kw["is_cancelled"]():
                if _time.time() - t0 > 10:
                    raise AssertionError("deadline never tripped is_cancelled")
                _time.sleep(0.05)
            return {"final_text": "partial", "stop_reason": "cancelled",
                    "rounds": 1, "tool_calls_total": 0, "usage_total": {},
                    "tool_events": []}

        from engine import llm_loop
        orig = llm_loop.run_loop
        llm_loop.run_loop = fake_run_loop
        try:
            res = sidecar_proxy.run_turn_blocking(
                messages=[{"role": "user", "content": "x"}],
                model="m", api_key="", base_url="http://localhost:0",
                system_prompt="", tool_context={"agent_id": "main"},
                sampling={}, thinking_level=None, max_tokens=10,
                max_rounds=1, timeout_s=1.0)
        finally:
            llm_loop.run_loop = orig
        self.assertTrue(res["timed_out"])
        self.assertIn("timeout", (res["error"] or "").lower())
        self.assertEqual(res["reply"], "partial")  # partial is KEPT


class TestCancelCascadeScoping(unittest.TestCase):
    SID = "ut-bgtask-cascade"

    @classmethod
    def setUpClass(cls):
        ChatDB.init()

    def setUp(self):
        _wipe(self.SID)

    def tearDown(self):
        _wipe(self.SID)

    def test_cancel_session_tasks_scopes_to_spawn_turn(self):
        from engine.background_tasks import background_task_runner
        ChatDB.create_background_task("old", self.SID, "main", "m", "Old", "p",
                                      spawn_turn_id="turn-old")
        ChatDB.create_background_task("new1", self.SID, "main", "m", "N1", "p",
                                      spawn_turn_id="turn-new")
        ChatDB.create_background_task("new2", self.SID, "main", "m", "N2", "p",
                                      spawn_turn_id="turn-new")
        cancelled_ids = []
        orig_cancel = background_task_runner.cancel
        background_task_runner.cancel = lambda tid: (cancelled_ids.append(tid) or True)
        try:
            n = background_task_runner.cancel_session_tasks(
                self.SID, spawn_turn_id="turn-new")
        finally:
            background_task_runner.cancel = orig_cancel
        self.assertEqual(n, 2)
        self.assertEqual(sorted(cancelled_ids), ["new1", "new2"])

    def test_cancel_session_tasks_all(self):
        from engine.background_tasks import background_task_runner
        ChatDB.create_background_task("a", self.SID, "main", "m", "A", "p",
                                      spawn_turn_id="t1")
        ChatDB.create_background_task("b", self.SID, "main", "m", "B", "p",
                                      spawn_turn_id="t2")
        ChatDB.finish_background_task("b", "done", output="x")  # not running
        cancelled_ids = []
        orig_cancel = background_task_runner.cancel
        background_task_runner.cancel = lambda tid: (cancelled_ids.append(tid) or True)
        try:
            n = background_task_runner.cancel_session_tasks(self.SID)
        finally:
            background_task_runner.cancel = orig_cancel
        self.assertEqual(n, 1)
        self.assertEqual(cancelled_ids, ["a"])


if __name__ == "__main__":
    unittest.main()
