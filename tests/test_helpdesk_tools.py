"""Fast, deterministic tests for Brainy's read-only helpdesk tools + the
view-context formatter. These guard the data layer the LLM depends on — i.e.
that Brainy actually RECEIVES the right context for the current session/user —
without invoking a model. They catch the regression class from v9.21.5
(ChatDB/AuthDB not on `brain` → AttributeError → "kann nicht abrufen") and the
"no active session" / empty-context paths.

Run:  python3 -m unittest tests.test_helpdesk_tools -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402  (init the runtime — TOOL_DISPATCH, ChatDB wiring, etc.)
from engine.context import request_context, get_request_context  # noqa: E402
from server_lib.db import ChatDB  # noqa: E402
from engine.tools.helpdesk_tools import (  # noqa: E402
    tool_helpdesk_session_info,
    tool_helpdesk_user_context,
    tool_helpdesk_user_activity,
)
from handlers.helpdesk import _format_view_context  # noqa: E402


def _unwrap(envelope: str) -> dict:
    """Tools return an _ok/_err JSON string — parse it; fail loud on _err."""
    data = json.loads(envelope)
    assert "error" not in data, f"tool returned an error: {data.get('error')}"
    return data


class TestViewContextFormatter(unittest.TestCase):
    def test_empty_when_no_label(self):
        self.assertEqual(_format_view_context({}), "")
        self.assertEqual(_format_view_context({"view": "chat"}), "")  # no label

    def test_includes_label_project_chat(self):
        out = _format_view_context({
            "view": "chat", "label": 'Projekt-Chat „Acme"',
            "project": "Acme", "chat_title": "Angebot",
        })
        self.assertIn("Projekt-Chat", out)
        self.assertIn("Acme", out)
        self.assertIn("Angebot", out)
        # It's a prepended context note, not the message itself.
        self.assertTrue(out.startswith("[Kontext"))
        self.assertTrue(out.endswith("\n\n"))


class TestHelpdeskSessionInfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Self-contained fixture: a real session row + two visible turns.
        cls.sid = "test-helpdesk-" + str(int(time.time()))
        ChatDB.save_session(cls.sid, "main", "test-model",
                            "Brainy Testsitzung", "active",
                            time.time(), time.time(), "")
        ChatDB.save_message(cls.sid, "user", "Wie wird das Wetter morgen in Wien?")
        ChatDB.save_message(cls.sid, "assistant", "Morgen wird es in Wien sonnig.")

    @classmethod
    def tearDownClass(cls):
        try:
            ChatDB.delete_session(cls.sid)
        except Exception:
            pass

    def test_reads_session_metadata_and_messages(self):
        with request_context(helpdesk_mode=True):
            rc = get_request_context()
            rc.session_id = self.sid
            rc.current_session_id = self.sid
            data = _unwrap(tool_helpdesk_session_info({}))
        self.assertEqual(data["session_id"], self.sid)
        self.assertEqual(data["title"], "Brainy Testsitzung")
        self.assertEqual(data["model"], "test-model")
        self.assertGreaterEqual(data["message_count"], 2)
        # The actual conversation content must reach Brainy.
        joined = " ".join(m["content"] for m in data["recent_messages"])
        self.assertIn("Wien", joined)

    def test_no_active_session_is_a_clean_error(self):
        with request_context(helpdesk_mode=True):
            get_request_context().session_id = ""
            out = tool_helpdesk_session_info({})
        self.assertIn("error", json.loads(out))  # _err, not a crash


class TestHelpdeskUserContext(unittest.TestCase):
    def test_anonymous_returns_unauthenticated(self):
        with request_context(helpdesk_mode=True):
            get_request_context().current_user_id = ""
            data = _unwrap(tool_helpdesk_user_context({}))
        self.assertFalse(data["authenticated"])

    def test_does_not_raise_for_a_user(self):
        # The key regression guard: this used to throw AttributeError because
        # AuthDB wasn't on `brain`. Any real-or-missing uid must NOT crash.
        with request_context(helpdesk_mode=True):
            get_request_context().current_user_id = "nonexistent-uid"
            out = tool_helpdesk_user_context({})
        # Either authenticated:false-ish data or a clean _err — never a raise.
        json.loads(out)  # parses → no exception escaped the tool


class TestHelpdeskUserActivity(unittest.TestCase):
    def test_returns_shape_without_crashing(self):
        with request_context(helpdesk_mode=True):
            get_request_context().current_user_id = ""  # admin/all scope
            data = _unwrap(tool_helpdesk_user_activity({}))
        for key in ("sessions", "projects", "schedules",
                    "session_count", "project_count", "schedule_count"):
            self.assertIn(key, data)
        self.assertIsInstance(data["sessions"], list)


if __name__ == "__main__":
    unittest.main()
