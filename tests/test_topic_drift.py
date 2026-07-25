"""drift_candidate — the free stage-1 gate for the topic-drift hint.

This gate decides whether the paid stage-2 confirm call runs at all, so its job
is to say NO cheaply and often. The tests below pin the cases where a false YES
would either cost money for nothing or nag the user:

  • short chats — still finding their topic
  • tiny tool sets — one tool more or less swings the overlap wildly
  • ubiquitous tools (ask_user_question, think, …) — present regardless of
    subject, so counting them would mask the very difference we look for

Runs in the bare test interpreter (unittest, no pytest, no server).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class DriftSignatureTest(unittest.TestCase):

    def test_ubiquitous_tools_are_stripped(self):
        """Tools that appear on nearly every turn carry no topic information."""
        sig = brain._drift_tool_signature(
            ["read_file", "ask_user_question", "think", "git_status"])
        self.assertEqual(sig, frozenset({"read_file", "git_status"}))

    def test_empty_input_is_empty_signature(self):
        self.assertEqual(brain._drift_tool_signature(None), frozenset())
        self.assertEqual(brain._drift_tool_signature([]), frozenset())


class DriftCandidateTest(unittest.TestCase):

    # A tax question and a deploy question pull genuinely different tools.
    DOCS = frozenset({"read_document", "mempalace_query", "wiki_read", "web_fetch"})
    CODE = frozenset({"git_status", "execute_command", "read_file", "python_exec"})

    def test_disjoint_tool_sets_in_a_long_chat_are_candidates(self):
        self.assertTrue(brain.drift_candidate(self.DOCS, self.CODE, 10))

    def test_short_chat_never_triggers(self):
        """Below the message floor a chat is still establishing its topic —
        an early tool swing is normal, not drift."""
        for n in (0, 1, 4, 5):
            with self.subTest(messages=n):
                self.assertFalse(brain.drift_candidate(self.DOCS, self.CODE, n))

    def test_same_tools_never_trigger(self):
        self.assertFalse(brain.drift_candidate(self.DOCS, self.DOCS, 20))

    def test_high_overlap_never_triggers(self):
        """One extra tool on an otherwise identical set is a normal follow-up."""
        nearly = frozenset(self.DOCS | {"write_file"})
        self.assertFalse(brain.drift_candidate(self.DOCS, nearly, 20))

    def test_tiny_tool_sets_never_trigger(self):
        """With one or two tools the overlap ratio is noise, not signal."""
        self.assertFalse(brain.drift_candidate(
            frozenset({"read_file"}), frozenset({"git_status"}), 20))
        self.assertFalse(brain.drift_candidate(
            frozenset({"read_file", "write_file"}),
            frozenset({"git_status", "execute_command"}), 20))

    def test_empty_sides_never_trigger(self):
        self.assertFalse(brain.drift_candidate(frozenset(), self.CODE, 20))
        self.assertFalse(brain.drift_candidate(self.DOCS, frozenset(), 20))
        self.assertFalse(brain.drift_candidate(None, None, 20))

    def test_partial_overlap_at_the_boundary(self):
        """Sanity-check the threshold itself: 2 shared of 6 total = 0.33 ≤ 0.34
        is a candidate; 3 of 5 = 0.60 is not."""
        a = frozenset({"t1", "t2", "t3", "t4"})
        b = frozenset({"t3", "t4", "t5", "t6"})   # 2/6 = 0.33
        self.assertTrue(brain.drift_candidate(a, b, 20))
        c = frozenset({"t1", "t2", "t3"})
        d = frozenset({"t1", "t2", "t3", "t7", "t8"})   # 3/5 = 0.60
        self.assertFalse(brain.drift_candidate(c, d, 20))


class ThinkingSimulatedTest(unittest.TestCase):
    """Only models the API can't graduate may get the wire-suffix fallback.
    A stray depth instruction on a model that graduates natively would sit on
    top of a working dial — the exact thing this predicate guards against."""

    def setUp(self):
        self._orig = brain._models_config
        brain._models_config = {
            "mistral-x": {"thinking_format": "mistral_blocks"},
            "no-think": {"thinking_format": "none"},
            "cloud-x": {"thinking_format": "reasoning_field"},
            "omlx-x": {"thinking_format": "reasoning_field"},
        }

    def tearDown(self):
        brain._models_config = self._orig

    def test_only_ungraduatable_models_are_simulated(self):
        self.assertTrue(brain.thinking_is_simulated("mistral-x"))
        self.assertTrue(brain.thinking_is_simulated("no-think"))
        self.assertFalse(brain.thinking_is_simulated("cloud-x"))
        self.assertFalse(brain.thinking_is_simulated("omlx-x"))

    def test_unknown_model_is_not_simulated(self):
        """Reading a missing entry as 'none' would mark EVERY model simulated
        whenever the config isn't loaded (a bare `import brain` has none)."""
        self.assertFalse(brain.thinking_is_simulated("never-heard-of-it"))
        self.assertFalse(brain.thinking_is_simulated(""))


class DriftConfirmTest(unittest.TestCase):
    """Stage 2 must be fail-safe: any trouble means 'no drift', never an
    exception that would break the turn it is attached to."""

    def test_blank_input_short_circuits_without_a_call(self):
        called = []
        self.assertFalse(brain.drift_confirm("", "something", model="m"))
        self.assertFalse(brain.drift_confirm("something", "   ", model="m"))
        self.assertEqual(called, [])   # no LLM call attempted

    def test_missing_model_returns_false(self):
        """No classifier model configured → skip silently, don't guess."""
        self.assertFalse(brain.drift_confirm("earlier", "new", model=""))


if __name__ == "__main__":
    unittest.main()
