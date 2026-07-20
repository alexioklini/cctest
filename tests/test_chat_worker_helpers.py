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

    def test_emits_simple_token_restored(self):
        """A delta containing a full token de-anonymises; the tail may be
        HELD BACK until flush (9.383.2 shape-fake holdback — a non-empty
        mapping always reserves a trailing window in which a partially
        streamed fake could still complete). The guarantee is the COMBINED
        output, not immediate emission."""
        mapping = ps.new_mapping()
        mapping.forward["alice@example.com"] = "<EMAIL_1_aaaa>"
        mapping.reverse["<EMAIL_1_aaaa>"] = "alice@example.com"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            out = sd.feed("Sent by <EMAIL_1_aaaa> at noon.")
            combined = out + sd.flush()
            self.assertEqual(combined, "Sent by alice@example.com at noon.")
            self.assertEqual(sd.restored_count, 1)
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

    def test_shape_fake_split_across_deltas_never_leaks(self):
        """Chat 80494e34: SHAPE fakes (email/IBAN/date — no angle brackets)
        stream in several small deltas. Without the trailing holdback the
        fake PREFIX was emitted before the value completed — irreversible,
        the user saw the fake (and a fake/real chimera after the offsets
        diverged) while the persisted reply was correct. The combined stream
        must equal the restored text and no emitted chunk may leak fake
        substance."""
        mapping = ps.new_mapping()
        mapping.forward["bonnie.stark@example.com"] = "hayden.scott@example.com"
        mapping.reverse["hayden.scott@example.com"] = "bonnie.stark@example.com"
        mapping.forward["05.02.1947"] = "13.02.1947"
        mapping.reverse["13.02.1947"] = "05.02.1947"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            # Table-cell style, token-sized deltas — the live failure shape.
            deltas = ["| **E-Mail** | ", "hayden", ".sc", "ott@", "example",
                      ".com | ", "geboren ", "13", ".02", ".1947", " |"]
            emitted = [sd.feed(c) for c in deltas]
            emitted.append(sd.flush())
            combined = "".join(emitted)
            self.assertEqual(
                combined,
                "| **E-Mail** | bonnie.stark@example.com | "
                "geboren 05.02.1947 |")
            self.assertNotIn("hayden", combined)
            self.assertNotIn("13.02.1947", combined)
        finally:
            ps.close_mapping(mapping.mapping_id)

    def test_emission_is_append_only_prefix_stable(self):
        """The client renders emitted chunks additively — an emitted prefix
        can never be retracted. Assert the invariant directly: after every
        feed, the previously emitted text must be a strict prefix of the
        final combined output."""
        mapping = ps.new_mapping()
        mapping.forward["DE89 3704 0044 0532 0130 00"] = \
            "DE19 8638 1288 4472 1512 23"
        mapping.reverse["DE19 8638 1288 4472 1512 23"] = \
            "DE89 3704 0044 0532 0130 00"
        try:
            sd = self.chat_mod.StreamingDeanonymizer(mapping)
            text = "IBAN: DE19 8638 1288 4472 1512 23 (bitte prüfen)"
            acc = ""
            for ch in [text[i:i + 3] for i in range(0, len(text), 3)]:
                acc += sd.feed(ch)
            final = acc + sd.flush()
            self.assertEqual(final,
                             "IBAN: DE89 3704 0044 0532 0130 00 (bitte prüfen)")
            self.assertTrue(final.startswith(acc))
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
        _, data = live.events[-1]
        self.assertEqual(data["status"], "error")


# ---------------------------------------------------------------------------
# _after_file_write callback factory — step 5 post-LLM deanonymisation hook
# ---------------------------------------------------------------------------


class _FakeSession:
    """Just enough Session shape for the callback's `sessions.peek()` lookup."""

    def __init__(self, sid):
        self.id = sid
        self.live_stream = FakeLiveStream()


class _FakeSessions:
    def __init__(self):
        self._map = {}

    def add(self, sid, sess):
        self._map[sid] = sess

    def peek(self, sid):
        return self._map.get(sid)


class TestGdprAfterFileWriteCallback(unittest.TestCase):
    """Exercises make_gdpr_after_file_write_cb end-to-end without a server."""

    def setUp(self):
        from handlers import chat as chat_mod
        self.chat_mod = chat_mod
        self.fake_db = FakeChatDB()
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = self.fake_db
        self._orig_sessions = getattr(chat_mod, "sessions", None)
        self.fake_sessions = _FakeSessions()
        chat_mod.sessions = self.fake_sessions

        # Stub _is_artifact_path so any path looks like an artifact (the real
        # check pokes at filesystem paths under agents/<id>/artifacts/).
        import brain
        self._orig_iap = brain._is_artifact_path
        brain._is_artifact_path = lambda _p: True

        # Mapping with a single IBAN substitution so we can verify
        # deanonymise actually fires and restores the original.
        self.mapping = ps.new_mapping()
        # Inject a known pair into the mapping without going through the
        # scanner — exercising the file walker is enough; we just need
        # forward+reverse populated.
        token = self.mapping.next_token("iban")
        self.mapping.record("DE89370400440532013000", token, "iban")
        self.token = token

    def tearDown(self):
        if self._orig_chatdb is not None:
            self.chat_mod.ChatDB = self._orig_chatdb
        if self._orig_sessions is not None:
            self.chat_mod.sessions = self._orig_sessions
        import brain
        brain._is_artifact_path = self._orig_iap
        ps.close_mapping(self.mapping.mapping_id)

    def test_callback_deanonymises_md_in_place_and_emits_pair(self):
        sid = "sid-deanon-1"
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)

        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.mapping.mapping_id,
            session_id=sid,
            agent_id="main",
        )

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.md")
            with open(path, "w") as f:
                f.write(f"Result: token = {self.token}")

            cb(path, "created", "main")

            with open(path) as f:
                self.assertIn("DE89370400440532013000", f.read())

        # One dispatch + one done event.
        events = [t for (t, _) in sess.live_stream.events]
        self.assertEqual(events, ["synthetic_tool_use", "synthetic_tool_result"])
        # Two persisted rows.
        roles = [r["role"] for r in self.fake_db.rows]
        self.assertEqual(roles, ["tool_use", "tool_result"])
        result = json.loads(self.fake_db.rows[1]["content"])["result"]
        self.assertEqual(result["restored"], 1)
        self.assertEqual(result["file"], "report.md")

    def test_callback_skips_unsupported_extension(self):
        sid = "sid-deanon-2"
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)

        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.mapping.mapping_id,
            session_id=sid,
            agent_id="main",
        )

        with tempfile.TemporaryDirectory() as td:
            # A truly-unsupported binary ext (not reversible, not lintable, not
            # an image warned by M7/G6 — .png/.jpg now fail loud, so use .bin).
            path = os.path.join(td, "blob.bin")
            with open(path, "wb") as f:
                f.write(b"\x00\x01\x02\x03")

            cb(path, "created", "main")

        # Nothing emitted — unsupported extensions are no-ops.
        self.assertEqual(sess.live_stream.events, [])
        self.assertEqual(self.fake_db.rows, [])

    def test_callback_reverses_non_artifact_paths(self):
        # M7/G5: a model-written file OUTSIDE the artifact tree is now reversed
        # too — the old `_is_artifact_path` bail let a .docx for a real meeting,
        # written to an absolute path with invented names, sail through. The
        # callback only ever fires for model-written paths, so reversing them
        # everywhere is correct. (Was: test_callback_skips_non_artifact_paths.)
        sid = "sid-deanon-3"
        sess = _FakeSession(sid)
        self.fake_sessions.add(sid, sess)
        import brain
        brain._is_artifact_path = lambda _p: False  # outside the tree

        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.mapping.mapping_id,
            session_id=sid,
            agent_id="main",
        )
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "outside.md")
            with open(path, "w") as f:
                f.write(f"random {self.token}")
            cb(path, "created", "main")
            # File de-anonymised in place (token restored to the original).
            with open(path) as f:
                self.assertNotIn(self.token, f.read())

    def test_callback_handles_missing_session_gracefully(self):
        # No session registered — callback must not crash.
        cb = self.chat_mod.make_gdpr_after_file_write_cb(
            mapping_id=self.mapping.mapping_id,
            session_id="missing-sid",
            agent_id="main",
        )
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.md")
            with open(path, "w") as f:
                f.write(f"hello {self.token}")
            cb(path, "created", "main")
            # File still gets de-anonymised even without a live session —
            # the absence of `live` only suppresses SSE; the on-disk
            # rewrite still runs.
            with open(path) as f:
                self.assertIn("DE89370400440532013000", f.read())


# ---------------------------------------------------------------------------
# Background-task next-turn injection — the core "does not pollute context"
# invariant. A finished task's full output must be folded into the NEXT turn
# wire-only, exactly once, then never re-appear.
# ---------------------------------------------------------------------------


class TestBackgroundTaskInjection(unittest.TestCase):
    """Uses a real (temp) ChatDB so the consumed_at bookkeeping is exercised end
    to end — that bookkeeping IS the invariant under test."""

    def setUp(self):
        import server_lib.db as db
        from handlers import chat as chat_mod
        self.db = db
        self.chat_mod = chat_mod
        self.tmp = tempfile.mkdtemp()
        self._orig_path = db.CHAT_DB
        db.CHAT_DB = os.path.join(self.tmp, "chats.db")
        db.ChatDB.init()
        # `handlers.chat` reaches ChatDB as a server-injected global (server.py
        # injects it at boot); under the bare test interpreter we inject it
        # ourselves so the helper resolves the name — same pattern the other
        # classes in this file use.
        self._had_chatdb = hasattr(chat_mod, "ChatDB")
        self._orig_chatdb = getattr(chat_mod, "ChatDB", None)
        chat_mod.ChatDB = db.ChatDB

    def tearDown(self):
        self.db.CHAT_DB = self._orig_path
        if self._had_chatdb:
            self.chat_mod.ChatDB = self._orig_chatdb
        else:
            delattr(self.chat_mod, "ChatDB")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finished_output_injected_once_then_consumed(self):
        ChatDB = self.db.ChatDB
        sid = "sess-bg-1"
        ChatDB.create_background_task("bg1", sid, "main", "m", "Recherche", "do it")
        ChatDB.finish_background_task("bg1", "done", output="DIE ANTWORT")

        # First turn: the preamble carries the full output.
        pre = self.chat_mod._build_background_task_preamble(sid)
        self.assertIn("DIE ANTWORT", pre)
        self.assertIn("Recherche", pre)

        # Wire injection must NOT mutate the stored messages (wire ≠ stored).
        stored = [{"role": "user", "content": "und jetzt?"}]
        wire = self.chat_mod._inject_web_preamble_into_wire(stored, pre)
        self.assertIn("DIE ANTWORT", wire[-1]["content"])
        self.assertEqual(stored[-1]["content"], "und jetzt?",
                         "stored history must stay clean — output never persists")

        # Second turn: already consumed → empty, so it never re-enters context.
        self.assertEqual(self.chat_mod._build_background_task_preamble(sid), "",
                         "a finished task's output must reach the model exactly once")

    def test_cancelled_partial_is_injected(self):
        ChatDB = self.db.ChatDB
        sid = "sess-bg-2"
        ChatDB.create_background_task("bg2", sid, "main", "m", "Lang", "x")
        ChatDB.finish_background_task("bg2", "cancelled", output="TEILERGEBNIS")
        pre = self.chat_mod._build_background_task_preamble(sid)
        self.assertIn("TEILERGEBNIS", pre)
        self.assertIn("abgebrochen", pre.lower())

    def test_running_task_not_injected(self):
        ChatDB = self.db.ChatDB
        sid = "sess-bg-3"
        ChatDB.create_background_task("bg3", sid, "main", "m", "Läuft", "x")
        # Still running → nothing to fold in yet.
        self.assertEqual(self.chat_mod._build_background_task_preamble(sid), "")


if __name__ == "__main__":
    unittest.main()
