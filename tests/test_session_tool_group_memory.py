"""Per-session, per-model MONOTONE tool-group memory (handlers.chat).

WHY this file exists (chat aa6cab7d): a session's turn 2 was classified [web]
(image search ran, found real pictures), the correction follow-up "ich meinte
Lara pulver" was classified [context, memory] — the fresh per-turn trim DROPPED
web, the model could no longer search and hallucinated dead image URLs. The fix
generalizes the 9.277.2 cache-freeze union to every model: within a session a
model's tool groups may only GROW (union per turn), never shrink. These tests
pin that invariant — they fail if anyone reintroduces per-turn fresh trims.

Run: python3 -m unittest tests.test_session_tool_group_memory
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from handlers.chat import _cache_freeze_groups, _remember_tool_groups  # noqa: E402


class _DummySession:
    """Bare attribute bag — _cache_freeze_groups lazily attaches its dict."""


class TestRememberToolGroups(unittest.TestCase):
    def setUp(self):
        self.session = _DummySession()

    def test_first_sighting_stores_fresh_trim(self):
        got = _remember_tool_groups(self.session, "m1", ["context", "memory"])
        self.assertEqual(got, ["context", "memory"])
        self.assertEqual(_cache_freeze_groups(self.session)["m1"],
                         ["context", "memory"])

    def test_union_grows_never_shrinks(self):
        # The aa6cab7d sequence: [context,memory] → [web] → [context,memory].
        # After turn 3 web MUST still be present (the whole point of the fix).
        _remember_tool_groups(self.session, "m1", ["context", "memory"])
        _remember_tool_groups(self.session, "m1", ["web"])
        got = _remember_tool_groups(self.session, "m1", ["context", "memory"])
        self.assertEqual(got, ["context", "memory", "web"])

    def test_no_growth_turn_is_stable(self):
        # A turn needing nothing new must return the IDENTICAL set (no churn —
        # a byte-stable prefix is what makes provider prompt caches hit).
        first = _remember_tool_groups(self.session, "m1", ["web", "core"])
        again = _remember_tool_groups(self.session, "m1", ["web"])
        self.assertEqual(sorted(first), again)

    def test_empty_list_is_a_valid_signal_but_cannot_shrink(self):
        # [] = "this turn needs no groups" (greeting). On FIRST sighting it is
        # a real trim-to-floor; on later turns it must NOT erase the memory.
        got = _remember_tool_groups(self.session, "m1", [])
        self.assertEqual(got, [])
        _remember_tool_groups(self.session, "m1", ["web"])
        got = _remember_tool_groups(self.session, "m1", [])
        self.assertEqual(got, ["web"])

    def test_none_groups_treated_as_empty(self):
        _remember_tool_groups(self.session, "m1", ["web"])
        got = _remember_tool_groups(self.session, "m1", None)
        self.assertEqual(got, ["web"])

    def test_per_model_keying_is_independent(self):
        # Switching models mid-session: each model accumulates its OWN set —
        # m2 must not inherit m1's groups (different floors/capabilities), and
        # m1's memory survives the excursion to m2.
        _remember_tool_groups(self.session, "m1", ["web"])
        got_m2 = _remember_tool_groups(self.session, "m2", ["code_exec"])
        self.assertEqual(got_m2, ["code_exec"])
        got_m1 = _remember_tool_groups(self.session, "m1", [])
        self.assertEqual(got_m1, ["web"])

    def test_unseen_model_reads_back_none(self):
        # Absent entry = None = full set downstream — never-classified models
        # (warmup-protected locals) must keep their untouched full prefix.
        self.assertIsNone(_cache_freeze_groups(self.session).get("never-seen"))


if __name__ == "__main__":
    unittest.main()
