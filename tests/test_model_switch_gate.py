"""Model-switch gate — the pieces that can be tested without a live turn.

From turn 2 on the prompt cache (provider cache-read discount on cloud, warm
prefill on local) is keyed to the model that answered so far; switching models
re-prefills the whole history at full price/latency. The worker therefore asks
the user BEFORE the turn runs (handlers/chat.py, before the anonymise stage):
continue on the new model / keep the old one / cancel. No answer within 5
minutes → continue on the new model.

What the tests pin is the mechanical part around the dialog:

  • `_prev_turn_model` — WHICH model counts as "the previous turn's model"
    (newest assistant row's metadata; fallback-swapped turns must NOT read as
    a user switch)
  • `deliver_model_switch_decision` — the pending-slot handshake the HTTP
    endpoint uses (late click after timeout → delivered=False)

The dialog itself and the 5-minute default are worker+client behavior and are
exercised by hand; they need a live turn.

Runs in the bare test interpreter (unittest, no pytest, no server).
"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from handlers import chat as chat_handlers  # noqa: E402


class _FakeSession:
    def __init__(self, messages):
        self.messages = messages


class PrevTurnModelTest(unittest.TestCase):
    """The gate compares against the model that ACTUALLY answered the previous
    turn — the newest assistant row's metadata.model."""

    def _prev(self, messages):
        # sid points nowhere; the DB fallback must swallow that silently.
        return chat_handlers._prev_turn_model(
            _FakeSession(messages), "no-such-session")

    def test_turn_one_has_no_previous_model(self):
        """Turn 1: no assistant reply yet → no gate."""
        self.assertEqual(self._prev([{"role": "user", "content": "hi"}]),
                         ("", ""))
        self.assertEqual(self._prev([]), ("", ""))

    def test_newest_assistant_row_wins(self):
        """A mid-session switch must compare against the NEWEST reply's model,
        not an older one — otherwise a chat that already switched once would
        warn again on every following turn."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "r1",
             "metadata": {"model": "old-model"}},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "r2",
             "metadata": {"model": "new-model"}},
        ]
        self.assertEqual(self._prev(msgs), ("new-model", ""))

    def test_thinking_rows_are_skipped(self):
        """role='thinking' rows sit between the reply and the next user
        message; they never carry the turn's model."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "r",
             "metadata": {"model": "m1"}},
            {"role": "thinking", "content": "…",
             "metadata": {"tool_round": 1}},
        ]
        self.assertEqual(self._prev(msgs), ("m1", ""))

    def test_fallback_swap_reports_original_model(self):
        """A quota/GDPR fallback ran the turn on another model
        (metadata.model=fb, original_model=the session's own). The session
        still sitting on original_model is NOT a user switch — the caller
        checks both values, so both must come back."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "r",
             "metadata": {"model": "local-fb", "original_model": "cloud-m"}},
        ]
        self.assertEqual(self._prev(msgs), ("local-fb", "cloud-m"))

    def test_metadata_stripped_rows_fall_back_without_crashing(self):
        """After a Brain restart load_from_db() strips metadata; with an
        unreachable DB the helper must return empty, never raise — the gate
        is advisory and must not break the turn."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "r"},   # no metadata
        ]
        self.assertEqual(self._prev(msgs), ("", ""))


class DeliverDecisionTest(unittest.TestCase):
    """The endpoint↔worker handshake: deliver into a waiting slot → True;
    nothing waiting (timeout already continued the turn) → False, so the
    client can tell the user their click arrived late."""

    def tearDown(self):
        chat_handlers._model_gate_pending.clear()

    def test_no_pending_dialog_returns_false(self):
        self.assertFalse(chat_handlers.deliver_model_switch_decision(
            "sess-x", "keep_old"))

    def test_pending_dialog_receives_decision_and_wakes_worker(self):
        ev = threading.Event()
        chat_handlers._model_gate_pending["sess-x"] = {
            "event": ev, "decision": None}
        self.assertTrue(chat_handlers.deliver_model_switch_decision(
            "sess-x", "keep_old"))
        self.assertTrue(ev.is_set())
        self.assertEqual(
            chat_handlers._model_gate_pending["sess-x"]["decision"],
            "keep_old")


if __name__ == "__main__":
    unittest.main()
