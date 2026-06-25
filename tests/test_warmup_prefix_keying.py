"""Warmup is PREFIX-keyed, not model-keyed — the invariant that kills the
mid-session re-warm.

The bug (v9.205.x and earlier): warmup state was keyed by model alone, and the
"do I need to warm?" decision used PROXIES (a warmup_mode string compare, the
claim() shape, a hardcoded "full") instead of "is THIS KV prefix already warm?".
Three symptoms, all the same missing invariant:

  (a) MODE PING-PONG — the keeper wanted warmup_mode="minimal", session-warmup
      forced "full"; each kept flipping the model's single state entry, so the
      keeper re-primed every cycle.
  (b) PROJECT SWITCH — switching a normal chat <-> a project chat on the same
      local model re-warmed every time (the pool can't serve a project claim and
      session-warmup fired blind).
  (c) THINKING RE-PRIME — toggling thinking flipped thinking_primed against the
      keeper, which always primes thinking=False.

Once state is keyed by (model, prefix_id) and the ONLY decision is
prefix_is_warm(), (a)/(b)/(c) cease to exist without any per-scenario branch.
These tests pin that — a regression to model-keying breaks them.

Run: python3 -m unittest tests.test_warmup_prefix_keying
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class TestPrefixId(unittest.TestCase):
    def test_tool_order_does_not_change_prefix(self):
        # The tool SET determines the prefix, not the order we happen to iterate.
        a = brain.compute_prefix_id("SYS", {"read_file", "write_file"}, False)
        b = brain.compute_prefix_id("SYS", {"write_file", "read_file"}, False)
        self.assertEqual(a, b)

    def test_distinct_inputs_fork_the_prefix(self):
        base = brain.compute_prefix_id("SYS", {"read_file", "write_file"}, False)
        # A project chat's system prompt differs -> different prefix (this is WHY
        # a project switch is a genuinely different prefix, not a "cold" model).
        self.assertNotEqual(
            base, brain.compute_prefix_id("PROJECT SYS", {"read_file", "write_file"}, False))
        # A different tool set differs.
        self.assertNotEqual(
            base, brain.compute_prefix_id("SYS", {"read_file"}, False))
        # enable_thinking-in-prefix differs (only when it changes the tokens —
        # the caller decides via prefix_thinking_relevant).
        self.assertNotEqual(
            base, brain.compute_prefix_id("SYS", {"read_file", "write_file"}, True))


class TestPrefixIsWarm(unittest.TestCase):
    def setUp(self):
        # Isolate: clear any state for our synthetic models.
        for m in ("m-full", "m-min", "m-evict", "m-pingpong"):
            brain.invalidate_model_warmup(m)

    def test_warm_is_per_prefix(self):
        brain.set_warmup_state("m-full", "pA", state="warm", minimal=False)
        self.assertTrue(brain.prefix_is_warm("m-full", "pA"))
        # A DIFFERENT prefix on the same model is NOT warm — model-keying would
        # wrongly report it warm.
        self.assertFalse(brain.prefix_is_warm("m-full", "pB"))

    def test_minimal_need_satisfied_by_any_full_prefix(self):
        # SUBSET rule: a minimal prime only loads weights; ANY warm full prefix
        # already did that. This is what stops the mode ping-pong (a) — the
        # keeper's minimal need is covered by session-warmup's full prime.
        brain.set_warmup_state("m-min", "pFull", state="warm", minimal=False)
        self.assertTrue(
            brain.prefix_is_warm("m-min", brain.MINIMAL_PREFIX_ID, minimal=True))

    def test_minimal_only_does_not_cover_a_full_prefix(self):
        # The reverse must NOT hold: weights-only does not satisfy a real prefix.
        brain.set_warmup_state(
            "m-min", brain.MINIMAL_PREFIX_ID, state="warm", minimal=True)
        self.assertTrue(
            brain.prefix_is_warm("m-min", brain.MINIMAL_PREFIX_ID, minimal=True))
        self.assertFalse(brain.prefix_is_warm("m-min", "someFullPrefix"))

    def test_warming_counts_as_covered(self):
        # An in-flight prime must suppress a second prime of the same prefix.
        brain.set_warmup_state("m-full", "pX", state="warming", minimal=False)
        self.assertTrue(brain.prefix_is_warm("m-full", "pX"))

    def test_eviction_demotes_other_prefixes(self):
        # A fresh full prefill evicts the model's OTHER resident GPU prefixes;
        # the mirror must reflect that so a stale prefix isn't reported warm
        # (else it would suppress a needed re-prime).
        brain.set_warmup_state("m-evict", "p1", state="warm", minimal=False)
        brain.set_warmup_state("m-evict", "p2", state="warm", minimal=False)
        brain.evict_prefixes_except("m-evict", "p2")
        self.assertTrue(brain.prefix_is_warm("m-evict", "p2"))
        self.assertFalse(brain.prefix_is_warm("m-evict", "p1"))


class TestPingPongCannotRecur(unittest.TestCase):
    """The concrete (a) regression: a full prime then a minimal *need* must NOT
    each see the other as 'wrong mode' and re-prime."""

    def test_full_then_minimal_need_is_a_noop(self):
        brain.invalidate_model_warmup("m-pingpong")
        # Session-warmup primed a full prefix.
        brain.set_warmup_state("m-pingpong", "pBareFull", state="warm", minimal=False)
        # The keeper's minimal need is already covered -> it must NOT re-prime.
        self.assertTrue(
            brain.prefix_is_warm("m-pingpong", brain.MINIMAL_PREFIX_ID, minimal=True))
        # And the bare-full prefix the keeper/pool cares about is warm too, so a
        # full keeper would also skip.
        self.assertTrue(brain.prefix_is_warm("m-pingpong", "pBareFull"))


if __name__ == "__main__":
    unittest.main()
