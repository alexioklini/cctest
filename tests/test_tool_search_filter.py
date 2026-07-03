"""tool_search dispatchability filter (v9.276.1).

The regression (chat 2cb5a9dd): tool_search searched the raw TOOL_DEFINITIONS
catalog with no state check, so the globally-INACTIVE exa_search was served as
the top match. glm-5.2 — a strict function-caller that only calls declared
tools — told the user Exa was available, then spent a whole turn hunting a
tool that could never dispatch (the whitelist never contained it).

Intent: discovery must equal callability. tool_search only surfaces names in
the turn's dispatch whitelist (request-context `allowed_tools`, mirrored from
the same list the loop enforces); without a whitelist it must at least hide
globally-inactive built-ins.

Run: python3 -m unittest tests.test_tool_search_filter
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context  # noqa: E402


class TestToolSearchWhitelist(unittest.TestCase):
    def _search(self, query, allowed):
        with request_context(allowed_tools=allowed):
            return json.loads(brain._tool_search({"query": query, "max_results": 10}))

    def test_non_whitelisted_tool_hidden(self):
        # exa_search matches the query but is not dispatchable this turn →
        # it must not appear (the chat-2cb5a9dd failure).
        out = self._search("exa search web", ["searxng_search", "tool_search"])
        names = [m["name"] for m in out.get("matches", [])]
        self.assertNotIn("exa_search", names)

    def test_whitelisted_tool_found(self):
        out = self._search("searxng search web", ["searxng_search", "tool_search"])
        names = [m["name"] for m in out.get("matches", [])]
        self.assertIn("searxng_search", names)

    def test_hidden_tools_do_not_eat_result_slots(self):
        # With max_results=1 and exa filtered out, the slot must go to a
        # dispatchable match instead of returning empty.
        with request_context(allowed_tools=["searxng_search", "tool_search"]):
            out = json.loads(brain._tool_search(
                {"query": "search the web", "max_results": 1}))
        names = [m["name"] for m in out.get("matches", [])]
        self.assertEqual(names, ["searxng_search"])

    def test_discovery_tracks_only_visible_tools(self):
        with request_context(allowed_tools=["searxng_search", "tool_search"]):
            brain._tool_search({"query": "exa search web", "max_results": 5})
            discovered = brain.get_request_context()._discovered_tools or set()
        self.assertNotIn("exa_search", discovered)

    def test_no_whitelist_falls_back_without_crash(self):
        # Legacy/edge context (no whitelist): must not crash, and must apply
        # the global-state fallback for built-ins.
        with request_context():
            out = json.loads(brain._tool_search({"query": "search", "max_results": 5}))
        self.assertIn("matches", out)


if __name__ == "__main__":
    unittest.main()
