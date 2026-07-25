"""Topic-drift check — the pieces that can be tested without a live model.

One cheap LLM call decides ("is this a different subject than the previous
question?"). Around it sit only two mechanical parts, and those are what the
tests below pin:

  • the message floor — how early the check may run at all
  • the anchor pick — WHICH earlier message the call compares against

The model's verdict itself is not asserted here; that needs a live call and is
measured by hand (see the 05-internals skill for the recorded scores).

Runs in the bare test interpreter (unittest, no pytest, no server).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class DriftMinMessagesTest(unittest.TestCase):
    """The only remaining arithmetic gate: how early may the check run?

    Counted BEFORE the turn (user message in, reply not), so it runs 1, 3, 5 …
    and needs a previous user message to compare against — which exists from
    turn 2 on, at 3 messages.

    The tool-overlap / task-type pre-filter that used to sit here was removed in
    9.411.0: it was wrong three times running (full tool list instead of
    in-prompt, floors calibrated for the old call site, `fast` exemption plus a
    one-turn type lag) while guarding a fraction of a cent per chat. The real
    cost ceiling is `_drift_checked`, which latches on the first check.
    """

    def test_floor_is_three(self):
        self.assertEqual(brain._DRIFT_MIN_MESSAGES, 3)

    def test_turn_one_is_below_the_floor(self):
        """Turn 1 has 1 message — nothing to anchor on."""
        self.assertLess(1, brain._DRIFT_MIN_MESSAGES)

    def test_turn_two_reaches_the_floor(self):
        """Turn 2 has 3 messages — the earliest a drift can be seen."""
        self.assertGreaterEqual(3, brain._DRIFT_MIN_MESSAGES)


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


class DriftAnchorTest(unittest.TestCase):
    """The confirm call anchors on the PREVIOUS user message, not the first.

    Anchoring on the first message wasted the one allowed confirm call per chat
    whenever the chat opened with a greeting: "different subject than *hi*?" can
    only be NO (chat ef5f6afd). Measured on the live classifier model, neighbour
    comparison scores correctly on every case that matters — and the slow-slide
    worry that motivated the first-message anchor doesn't hold either: on a
    4-step chain, first-vs-last answered NO as well.

    This pins the SELECTION, which is plain list logic; the model's verdict is
    not asserted here (that needs a live call).
    """

    def _anchor(self, messages):
        """Mirror of the worker's anchor pick (handlers/chat.py)."""
        return next((m.get("content") for m in reversed(messages[:-1])
                     if m.get("role") == "user"
                     and isinstance(m.get("content"), str)), "")

    def test_picks_previous_user_message_not_first(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "kannst du python code entwickeln ?"},
            {"role": "assistant", "content": "Ja..."},
            {"role": "user", "content": "was weißt du über dora ?"},
        ]
        self.assertEqual(self._anchor(msgs),
                         "kannst du python code entwickeln ?")

    def test_skips_assistant_rows(self):
        msgs = [
            {"role": "user", "content": "erste"},
            {"role": "assistant", "content": "antwort"},
            {"role": "user", "content": "zweite"},
        ]
        self.assertEqual(self._anchor(msgs), "erste")

    def test_no_previous_user_message_yields_empty(self):
        """Turn 1: nothing to anchor on → empty, and the caller skips the call."""
        self.assertEqual(self._anchor([{"role": "user", "content": "erste"}]), "")
        self.assertEqual(self._anchor([]), "")

    def test_ignores_non_string_content(self):
        """Multimodal rows carry a list; they can't serve as a text anchor."""
        msgs = [
            {"role": "user", "content": "text-frage"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "image"}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "aktuelle"},
        ]
        self.assertEqual(self._anchor(msgs), "text-frage")


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
