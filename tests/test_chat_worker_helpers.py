"""Unit tests for the chat-worker-side helpers added in Step 3.

These are the bits we can test without a live server thread or HTTP socket:

  * StreamingDeanonymizer — does it hold partial tokens correctly across
    delta arrival, and does it flush right at the end?
  * deliver_gdpr_recovery_choice — does the wait/signal pair work?
  * _emit_synthetic_tool_event — does it persist the right rows and emit
    the matching SSE events?

End-to-end "POST /v1/chat with gdpr_action=anonymise" coverage waits for
step 4 (when the modal lands) and step 5 (file walkers) — at that point
we add HTTP-level smoke tests against the running daemon.

Run: python3 -m unittest tests.test_chat_worker_helpers -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLiveStream:
    """Records emitted events instead of broadcasting them. The real
    LiveStream lives in server.py and depends on a full Session; we don't
    need any of that for these tests."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.done = False

    def emit(self, event_type, data):
        self.events.append((event_type, dict(data)))
        if event_type in ("done", "error"):
            self.done = True


class FakeChatDB:
    """Captures save_message calls. Stand-in for the real ChatDB when we're
    not exercising the full SQLite path."""

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1000

    def save_message(self, session_id, role, content, metadata=None):
        rid = self._next_id
        self._next_id += 1
        self.rows.append({
            "id": rid, "session_id": session_id, "role": role,
            "content": content, "metadata": metadata,
        })
        return rid


def _install_fake_chatdb(handlers_chat_mod, fake):
    """The helper module references `ChatDB` as a bare global injected by
    server.py at boot. Tests don't run that injection, so wire it manually."""
    handlers_chat_mod.ChatDB = fake


# ---------------------------------------------------------------------------
# StreamingDeanonymizer
# ---------------------------------------------------------------------------


class TestStreamingDeanonymizer(unittest.TestCase):

    def setUp(self):
        # Import chat module fresh so the dynamic ChatDB binding can be set
        # per-test without leaking across tests.
        if "handlers.chat" in sys.modules:
            # Reload to get a clean module-level _gdpr_recovery_pending dict.
            import importlib
            importlib.reload(sys.modules["handlers.chat"])
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod

    def test_emits_simple_token_at_once(self):
        """A single delta containing a full token should de-anonymise
        immediately."""
        mapping = ps.new_mapping()
        mapping.forward["alice@example.com"] = "<EMAIL_1_aaaa>"
        mapping.reverse["<EMAIL_1_aaaa>"] = "alice@example.com"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            out = sd.feed("Sent by <EMAIL_1_aaaa> at noon.")
            self.assertEqual(out, "Sent by alice@example.com at noon.")
            self.assertEqual(sd.restored_count, 1)
            self.assertEqual(sd.flush(), "")
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_holds_back_partial_token_until_close(self):
        """If a token is split across deltas, the streamer must not emit
        the partial — it'd flash `<EMAIL_1_` to the user."""
        mapping = ps.new_mapping()
        mapping.forward["alice@example.com"] = "<EMAIL_1_aaaa>"
        mapping.reverse["<EMAIL_1_aaaa>"] = "alice@example.com"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            chunks = ["Sent by ", "<EMAIL_", "1_aaaa", "> at ", "noon."]
            emitted = []
            for c in chunks:
                emitted.append(sd.feed(c))
            emitted.append(sd.flush())
            combined = "".join(emitted)
            self.assertEqual(combined, "Sent by alice@example.com at noon.")
            self.assertEqual(sd.restored_count, 1)
            # Specifically check no token-shaped substring leaked.
            for chunk in emitted:
                self.assertNotIn("<EMAIL", chunk)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_stray_open_bracket_eventually_flushed(self):
        """A literal '<' that's not part of a token must come out at flush
        time (otherwise normal prose with '<' or '< 5' hangs forever)."""
        mapping = ps.new_mapping()
        # No mappings — streamer is just a passthrough that respects the
        # safety boundary.
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            # First delta ends with a stray '<' — held back.
            self.assertEqual(sd.feed("Result: x < "), "Result: x ")
            # Second delta resolves it.
            self.assertEqual(sd.feed("5 and y > 3."), "< 5 and y > 3.")
            self.assertEqual(sd.flush(), "")
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_final_text_returns_full_deanonymized(self):
        mapping = ps.new_mapping()
        mapping.forward["alice@example.com"] = "<EMAIL_1_aaaa>"
        mapping.reverse["<EMAIL_1_aaaa>"] = "alice@example.com"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            for c in ["Sent by ", "<EMAIL_", "1_aaaa", "> at "]:
                sd.feed(c)
            self.assertEqual(
                sd.final_text(), "Sent by alice@example.com at ")
        finally:
            ps.close_mapping(mapping.mapping_id)


# ---------------------------------------------------------------------------
# GDPR recovery wait pattern
# ---------------------------------------------------------------------------


class TestGdprRecoveryDelivery(unittest.TestCase):

    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        # Clear any leftover slots from prior tests (registry is module-level).
        with chat_mod._gdpr_recovery_lock:
            chat_mod._gdpr_recovery_pending.clear()

    def tearDown(self):
        with self.chat_mod._gdpr_recovery_lock:
            self.chat_mod._gdpr_recovery_pending.clear()

    def test_register_then_deliver_unblocks_event(self):
        ev = self.chat_mod._gdpr_recovery_register("sid-1")
        self.assertFalse(ev.is_set())
        ok = self.chat_mod.deliver_gdpr_recovery_choice("sid-1", "local_model")
        self.assertTrue(ok)
        self.assertTrue(ev.is_set())
        with self.chat_mod._gdpr_recovery_lock:
            self.assertEqual(
                self.chat_mod._gdpr_recovery_pending["sid-1"]["choice"],
                "local_model")

    def test_deliver_unknown_session_returns_false(self):
        ok = self.chat_mod.deliver_gdpr_recovery_choice("ghost", "cancel")
        self.assertFalse(ok)

    def test_invalid_action_rejected(self):
        self.chat_mod._gdpr_recovery_register("sid-x")
        ok = self.chat_mod.deliver_gdpr_recovery_choice(
            "sid-x", "send_to_cloud_anyway")
        self.assertFalse(ok, "no cloud-anyway action must be accepted")

    def test_clear_drops_pending_slot(self):
        ev = self.chat_mod._gdpr_recovery_register("sid-c")
        self.chat_mod._gdpr_recovery_clear("sid-c")
        # After clear, deliver is a no-op (slot is gone).
        ok = self.chat_mod.deliver_gdpr_recovery_choice("sid-c", "cancel")
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Synthetic tool-call persistence
# ---------------------------------------------------------------------------


class TestEmitSyntheticToolEvent(unittest.TestCase):

    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        self.fake_db = FakeChatDB()
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = self.fake_db

    def tearDown(self):
        if self._orig_chatdb is not None:
            self.chat_mod.ChatDB = self._orig_chatdb

    def test_dispatch_persists_tool_use_row(self):
        live = FakeLiveStream()
        mid = self.chat_mod._emit_synthetic_tool_event(
            live=live, sid="sid-1", kind="anonymise",
            tool_use_id="anon_xyz", phase="dispatch",
            args={"sources": ["chat_text"]},
        )
        self.assertIsNotNone(mid)
        self.assertEqual(len(self.fake_db.rows), 1)
        row = self.fake_db.rows[0]
        self.assertEqual(row["role"], "tool_use")
        # Content + metadata should be set with synthetic markers.
        content = json.loads(row["content"])
        self.assertEqual(content["name"], "anonymise")
        self.assertEqual(content["tool_use_id"], "anon_xyz")
        self.assertTrue(row["metadata"]["synthetic"])
        self.assertEqual(row["metadata"]["phase"], "dispatch")
        # Live event matches.
        self.assertEqual(len(live.events), 1)
        kind, data = live.events[0]
        self.assertEqual(kind, "synthetic_tool_use")
        self.assertEqual(data["tool_use_id"], "anon_xyz")

    def test_done_persists_tool_result_row(self):
        live = FakeLiveStream()
        self.chat_mod._emit_synthetic_tool_event(
            live=live, sid="sid-2", kind="anonymise",
            tool_use_id="anon_xyz", phase="dispatch",
            args={"sources": ["chat_text"]},
        )
        self.chat_mod._emit_synthetic_tool_event(
            live=live, sid="sid-2", kind="anonymise",
            tool_use_id="anon_xyz", phase="done",
            result={"findings": 2, "mapping_id": "m-1"},
            status="ok", duration_ms=42,
        )
        self.assertEqual(len(self.fake_db.rows), 2)
        done_row = self.fake_db.rows[1]
        self.assertEqual(done_row["role"], "tool_result")
        self.assertEqual(done_row["metadata"]["status"], "ok")
        self.assertEqual(done_row["metadata"]["duration_ms"], 42)
        content = json.loads(done_row["content"])
        self.assertEqual(content["result"]["findings"], 2)

    def test_error_status_marked(self):
        live = FakeLiveStream()
        self.chat_mod._emit_synthetic_tool_event(
            live=live, sid="sid-3", kind="anonymise",
            tool_use_id="anon_xyz", phase="done",
            result={"error": "boom"}, status="error", duration_ms=10,
        )
        row = self.fake_db.rows[-1]
        self.assertEqual(row["metadata"]["status"], "error")
        # The live event also carries the error status so the client can
        # render the row red.
        _, data = live.events[-1]
        self.assertEqual(data["status"], "error")


if __name__ == "__main__":
    unittest.main()
