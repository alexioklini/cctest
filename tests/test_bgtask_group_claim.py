"""Characterization + correctness tests for the background-task fan-out JOIN.

The atomic group-claim is the correctness core of fan-out/join (v9.47.0):
"the last finisher delivers the group exactly once" must hold under concurrent
completions. These tests assert that guarantee directly against ChatDB.

Run: python3 -m unittest tests.test_bgtask_group_claim
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server_lib.db import ChatDB, _db_conn  # noqa: E402


def _wipe(session_id):
    with _db_conn() as c:
        c.execute("DELETE FROM background_tasks WHERE session_id=?", (session_id,))
        c.commit()


class TestGroupClaim(unittest.TestCase):
    SID = "ut-bgtask-claim"

    @classmethod
    def setUpClass(cls):
        ChatDB.init()

    def setUp(self):
        _wipe(self.SID)

    def tearDown(self):
        _wipe(self.SID)

    def _members(self, gid, n, follow_up="combine all"):
        for i in range(n):
            ChatDB.create_background_task(
                f"{gid}-task-{i}", self.SID, "main", "m", f"T{i}", "p",
                group_id=gid, follow_up=follow_up)

    def test_not_claimable_while_running(self):
        self._members("g1", 3)
        self.assertIsNone(ChatDB.claim_background_group("g1"))
        ChatDB.finish_background_task("g1-task-0", "done", output="A")
        # still two running
        self.assertIsNone(ChatDB.claim_background_group("g1"))

    def test_claims_when_all_terminal_incl_error(self):
        # deliver-with-failures: an error member must NOT block the group.
        self._members("g2", 3)
        ChatDB.finish_background_task("g2-task-0", "done", output="A")
        ChatDB.finish_background_task("g2-task-1", "error", error="boom")
        ChatDB.finish_background_task("g2-task-2", "cancelled", output="partial")
        members = ChatDB.claim_background_group("g2")
        self.assertIsNotNone(members)
        self.assertEqual(len(members), 3)
        self.assertEqual(sorted(m["status"] for m in members),
                         ["cancelled", "done", "error"])
        self.assertTrue(any(m["follow_up"] == "combine all" for m in members))

    def test_concurrent_claim_single_flight(self):
        # The guarantee: 8 threads race to claim a freshly-complete group;
        # EXACTLY ONE wins, the rest get None.
        self._members("g3", 3)
        for i in range(3):
            ChatDB.finish_background_task(f"g3-task-{i}", "done", output=str(i))
        results = []
        lock = threading.Lock()

        def claim():
            r = ChatDB.claim_background_group("g3")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wins = [r for r in results if r is not None]
        self.assertEqual(len(wins), 1, "exactly one claim must win")
        self.assertEqual(len(wins[0]), 3)

    def test_reclaim_after_win_returns_none(self):
        self._members("g4", 2)
        ChatDB.finish_background_task("g4-task-0", "done", output="A")
        ChatDB.finish_background_task("g4-task-1", "done", output="B")
        self.assertIsNotNone(ChatDB.claim_background_group("g4"))
        self.assertIsNone(ChatDB.claim_background_group("g4"))

    def test_group_of_one(self):
        # A single task assigned a group_id is a group-of-one: claimable once done.
        self._members("g5", 1, follow_up=None)
        self.assertIsNone(ChatDB.claim_background_group("g5"))
        ChatDB.finish_background_task("g5-task-0", "done", output="solo")
        members = ChatDB.claim_background_group("g5")
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["output"], "solo")

    def test_empty_group_id_is_none(self):
        self.assertIsNone(ChatDB.claim_background_group(None))
        self.assertIsNone(ChatDB.claim_background_group(""))

    def test_count_unconsumed_peek_does_not_consume(self):
        # The STANDALONE peek/pop (group_id IS NULL) — used by
        # deliver_background_results. Peek MUST NOT consume; pop does. Grouped
        # tasks are excluded from this path (they go via the group floor).
        ChatDB.create_background_task("s0", self.SID, "main", "m", "S0", "p")  # no group
        ChatDB.create_background_task("s1", self.SID, "main", "m", "S1", "p")
        ChatDB.finish_background_task("s0", "done", output="A")
        ChatDB.finish_background_task("s1", "done", output="B")
        self.assertEqual(ChatDB.count_unconsumed_background_tasks(self.SID), 2)
        self.assertEqual(ChatDB.count_unconsumed_background_tasks(self.SID), 2)  # no consume
        popped = ChatDB.pop_unconsumed_background_tasks(self.SID)
        self.assertEqual(len(popped), 2)
        self.assertEqual(ChatDB.count_unconsumed_background_tasks(self.SID), 0)

    def test_standalone_pop_excludes_grouped(self):
        # A grouped task must NOT appear in the standalone pop (prevents double
        # delivery — groups go via claim/floor).
        ChatDB.create_background_task("solo", self.SID, "main", "m", "Solo", "p")  # no group
        self._members("gx", 2)  # grouped
        ChatDB.finish_background_task("solo", "done", output="S")
        for i in range(2):
            ChatDB.finish_background_task(f"gx-task-{i}", "done", output=str(i))
        popped = ChatDB.pop_unconsumed_background_tasks(self.SID)
        self.assertEqual([p["id"] for p in popped], ["solo"])

    def test_undelivered_group_floor(self):
        # A claimed-but-not-proactively-delivered group is picked up by the
        # next-turn floor (pop_undelivered_groups), then marked consumed.
        self._members("gf", 2)
        for i in range(2):
            ChatDB.finish_background_task(f"gf-task-{i}", "done", output=str(i))
        # claim (as the last finisher would) but do NOT mark consumed (busy bail)
        self.assertIsNotNone(ChatDB.claim_background_group("gf"))
        floor = ChatDB.pop_undelivered_groups(self.SID)
        self.assertEqual(len(floor), 2)
        self.assertTrue(all(m["group_id"] == "gf" for m in floor))
        # consumed now — floor is empty on a second pass
        self.assertEqual(ChatDB.pop_undelivered_groups(self.SID), [])

    def test_mark_group_consumed_blocks_floor(self):
        # If proactive delivery fired (mark_group_consumed), the floor skips it.
        self._members("gc", 2)
        for i in range(2):
            ChatDB.finish_background_task(f"gc-task-{i}", "done", output=str(i))
        self.assertIsNotNone(ChatDB.claim_background_group("gc"))
        ChatDB.mark_group_consumed("gc")
        self.assertEqual(ChatDB.pop_undelivered_groups(self.SID), [])

    def test_sweep_stalled_group(self):
        # A group with one done + one long-running member past the deadline gets
        # the straggler force-failed so the group becomes claimable.
        self._members("gs", 2)
        ChatDB.finish_background_task("gs-task-0", "done", output="A")
        # gs-task-1 stays running; backdate its created_at to 20 min ago
        with _db_conn() as c:
            c.execute("UPDATE background_tasks SET created_at=strftime('%s','now')-1200 "
                      "WHERE id='gs-task-1'")
            c.commit()
        # deadline 600s → the straggler is past it
        affected = ChatDB.sweep_stalled_groups(600)
        self.assertIn((self.SID, "gs"), affected)
        # straggler now error, group fully terminal → claimable
        members = ChatDB.claim_background_group("gs")
        self.assertEqual(len(members), 2)
        statuses = sorted(m["status"] for m in members)
        self.assertEqual(statuses, ["done", "error"])
        timed = next(m for m in members if m["status"] == "error")
        self.assertIn("Timeout", timed["error"])

    def test_sweep_ignores_fresh_and_complete_groups(self):
        # A group still within the deadline is NOT swept.
        self._members("gfresh", 2)
        ChatDB.finish_background_task("gfresh-task-0", "done", output="A")
        # gfresh-task-1 running but created just now (within deadline)
        self.assertEqual(ChatDB.sweep_stalled_groups(600), [])
        # A fully-done group is also not swept (no running member).
        self._members("gdone", 2)
        for i in range(2):
            ChatDB.finish_background_task(f"gdone-task-{i}", "done", output=str(i))
        affected = ChatDB.sweep_stalled_groups(600)
        self.assertNotIn((self.SID, "gdone"), affected)

    def test_list_groups_rollup(self):
        self._members("g6", 3)
        ChatDB.finish_background_task("g6-task-0", "done", output="A")
        ChatDB.finish_background_task("g6-task-1", "error", error="x")
        groups = ChatDB.list_background_groups(self.SID)
        g6 = next((g for g in groups if g["group_id"] == "g6"), None)
        self.assertIsNotNone(g6)
        self.assertEqual(g6["total"], 3)
        self.assertEqual(g6["running"], 1)
        self.assertEqual(g6["done"], 1)
        self.assertEqual(g6["failed"], 1)


if __name__ == "__main__":
    unittest.main()
