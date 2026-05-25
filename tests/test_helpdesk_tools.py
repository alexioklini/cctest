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


class TestHelpdeskToolSet(unittest.TestCase):
    """The read-only contract: Brainy may read + look things up freely, but the
    resolved helpdesk tool set must NEVER include a write/exec/mutate tool. The
    dispatcher enforces the set (tool_mcp.handle_tools_call), so this guards the
    source of truth that feeds it."""

    def setUp(self):
        self.names = {t["name"] for t in brain.resolve_active_tools(
            purpose="helpdesk", agent_id="main")}

    def test_read_and_lookup_tools_present(self):
        for t in ("use_skill", "read_file", "read_document", "list_directory",
                  "search_files", "mempalace_query", "context_search",
                  "helpdesk_session_info", "helpdesk_user_context",
                  "helpdesk_user_activity"):
            self.assertIn(t, self.names, f"{t} should be available to Brainy")

    def test_no_write_or_exec_tools(self):
        forbidden = {"write_file", "edit_file", "execute_command", "python_exec",
                     "git_command", "github_command", "gmail_send", "gmail_reply",
                     "write_document", "edit_document", "delegate_task",
                     "generate_image", "schedule_modify"}
        leaked = forbidden & self.names
        self.assertEqual(leaked, set(), f"write/exec tools leaked into helpdesk: {leaked}")


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


class TestHelpdeskHistoryPaging(unittest.TestCase):
    """Paginated newest-first reads + scoped single/range delete (the UI's
    history feature). Each test seeds its OWN fresh rows (under a per-test uid)
    so the delete tests can't undercut each other — order-independent."""

    def setUp(self):
        self.uid = "test-hist-%d-%d" % (int(time.time()), id(self))
        for i in range(6):  # 6 exchanges (12 rows), oldest-first
            ChatDB.append_helpdesk_message(self.uid, "user", f"frage {i}")
            ChatDB.append_helpdesk_message(self.uid, "assistant", f"antwort {i}")

    def tearDown(self):
        ChatDB.clear_helpdesk_history(self.uid)

    def test_page_is_newest_first_with_ids_and_ts(self):
        page = ChatDB.load_helpdesk_history_page(self.uid, limit=4)
        self.assertEqual(len(page), 4)
        # newest-first → descending ids
        ids = [r["id"] for r in page]
        self.assertEqual(ids, sorted(ids, reverse=True))
        for r in page:
            self.assertIn("created_at", r)
            self.assertIn("content", r)

    def test_cursor_pagination_is_lossless(self):
        seen, cursor, guard = [], None, 0
        while guard < 20:
            guard += 1
            page = ChatDB.load_helpdesk_history_page(self.uid, before_id=cursor, limit=4)
            if not page:
                break
            seen += [r["id"] for r in page]
            cursor = page[-1]["id"]   # oldest in this newest-first page
        self.assertEqual(len(seen), 12)            # all 12 rows, none skipped
        self.assertEqual(len(set(seen)), 12)       # none duplicated

    def test_single_delete_is_user_scoped(self):
        page = ChatDB.load_helpdesk_history_page(self.uid, limit=1)
        rid = page[0]["id"]
        self.assertFalse(ChatDB.delete_helpdesk_message("someone-else", rid))  # scoped
        self.assertTrue(ChatDB.delete_helpdesk_message(self.uid, rid))
        remaining = {r["id"] for r in ChatDB.load_helpdesk_history_page(self.uid, limit=50)}
        self.assertNotIn(rid, remaining)

    def test_range_delete(self):
        rows = ChatDB.load_helpdesk_history_page(self.uid, limit=50)
        if not rows:
            self.skipTest("no rows")
        ts = [r["created_at"] for r in rows]
        n = ChatDB.delete_helpdesk_range(self.uid, min(ts), max(ts) + 1)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(ChatDB.load_helpdesk_history_page(self.uid, limit=50), [])


if __name__ == "__main__":
    unittest.main()
