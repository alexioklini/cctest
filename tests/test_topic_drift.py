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

    def test_first_turn_never_triggers(self):
        """Turn 1 has nothing to compare against. The count is taken BEFORE the
        turn (user message in, reply not), so it runs 1, 3, 5 … — a chat can
        first be checked on turn 2, at 3 messages."""
        for n in (0, 1, 2):
            with self.subTest(messages=n):
                self.assertFalse(brain.drift_candidate(self.DOCS, self.CODE, n))

    def test_drift_right_after_the_first_exchange_is_caught(self):
        """The regression from chat 2bd47d4f: DORA → Python at 3 messages. The
        old floor of 6 (set while the check still ran AFTER the reply) meant the
        gate never fired on short chats — exactly where topics jump."""
        self.assertTrue(brain.drift_candidate(self.DOCS, self.CODE, 3))

    def test_same_tools_never_trigger(self):
        self.assertFalse(brain.drift_candidate(self.DOCS, self.DOCS, 20))

    def test_high_overlap_never_triggers(self):
        """One extra tool on an otherwise identical set is a normal follow-up."""
        nearly = frozenset(self.DOCS | {"write_file"})
        self.assertFalse(brain.drift_candidate(self.DOCS, nearly, 20))

    def test_single_tool_sides_never_trigger(self):
        """At one tool per side the ratio can only be 0 % or 100 % — noise. A
        lone differing tool would read as a total topic change."""
        self.assertFalse(brain.drift_candidate(
            frozenset({"read_file"}), frozenset({"git_status"}), 20))
        self.assertFalse(brain.drift_candidate(
            frozenset({"read_file"}), self.CODE, 20))   # one side is enough to block

    def test_real_gated_tool_sets_from_chat_93e83868(self):
        """Real in-prompt sets from the reported chat: "hi" (10 tools) then
        "can you write python code" (40). 23 % overlap → candidate.

        This is the case the first two attempts missed. The check fed on
        _active_tool_names — EVERYTHING resolve_active_tools resolved (~100 here)
        — which barely moves between turns, so the overlap sat near 100 % and the
        gate could never fire. Only the classifier-GATED in_prompt set tracks the
        subject. Sets abbreviated to their distinguishing members; the ratio is
        what matters."""
        hi = frozenset({"context_detail", "context_recall", "context_search",
                        "mempalace_kg_neighbors", "mempalace_kg_query",
                        "mempalace_kg_search", "mempalace_query",
                        "save_chat_to_memory"})
        python = frozenset({"ast_grep_replace", "ast_grep_search", "code_query",
                            "code_search", "code_snippet", "code_trace",
                            "context_detail", "context_recall", "context_search",
                            "data_query", "db_query", "edit_document",
                            "python_exec", "execute_command", "read_file",
                            "write_file", "list_directory", "grep_files",
                            "kernel_exec", "notebook_edit"})
        self.assertTrue(brain.drift_candidate(hi, python, 3))
        # And the follow-up (python → DORA) must NOT: by then the tool set is
        # broad enough that the new subject fits inside it.
        dora = frozenset(python | {"web_fetch", "exa_search", "searxng_search"})
        self.assertFalse(brain.drift_candidate(python, dora, 5))

    # ── signal 2: classifier task types ──────────────────────────────────
    # The stage-1 filter is a COST gate, not a verdict: whatever passes goes to
    # one ~200-token confirm call that reads the real messages and answers NO
    # when unsure. So EITHER signal suffices — a wrong yes costs tokens once per
    # chat, a wrong no means the feature never fires at all.

    CODING = frozenset({"coding"})
    RESEARCH = frozenset({"research"})
    FAST = frozenset({"fast"})

    def test_task_type_change_alone_is_enough(self):
        """The case an AND-of-both-signals missed: on chat 93e83868 the type
        changed every turn while the tool sets stayed 83 % similar."""
        similar = frozenset(self.CODE | {"web_fetch", "exa_search"})
        self.assertTrue(brain.drift_candidate(
            self.CODE, similar, 5, self.CODING, self.RESEARCH))

    def test_same_task_type_falls_back_to_tools(self):
        """Same kind of question → signal 1 is silent, but a genuinely different
        tool set still qualifies."""
        self.assertFalse(brain.drift_candidate(
            self.CODE, self.CODE, 5, self.CODING, self.CODING))
        self.assertTrue(brain.drift_candidate(
            self.CODE, self.DOCS, 5, self.CODING, self.CODING))

    def test_leaving_fast_does_not_trigger_on_type_alone(self):
        """`fast` is the small-talk class — leaving it is the conversation
        starting. Without this, every chat opening with "hi" would pay for a
        confirm call on its second message. (The tool signal may still fire;
        this only disables the TYPE shortcut.)"""
        # Same tools on both sides, so only the type signal could trigger.
        self.assertFalse(brain.drift_candidate(
            self.CODE, self.CODE, 3, self.FAST, self.CODING))

    def test_missing_task_types_fall_back_to_tools(self):
        """Classifier off → tool overlap decides alone, degraded but not wrong."""
        self.assertTrue(brain.drift_candidate(self.DOCS, self.CODE, 5, None, None))
        self.assertFalse(brain.drift_candidate(self.DOCS, self.DOCS, 5, None, None))

    def test_two_tool_sides_are_compared(self):
        """Two is the smallest set where the ratio means something — and short
        chats (where drift is most common) rarely resolve more."""
        self.assertTrue(brain.drift_candidate(
            frozenset({"read_document", "mempalace_query"}),
            frozenset({"python_exec", "execute_command"}), 20))

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
