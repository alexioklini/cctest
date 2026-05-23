"""Wing-isolation (security) characterization tests for the MemPalace glue.

These pin the SECURITY-CRITICAL behavior of `tool_mempalace_query` BEFORE it is
extracted from brain.py to `engine/mempalace_glue.py` (refactor Tier C, C3). The
contract: after extraction these tests must still pass — a project__* wing must
NEVER leak across projects/users.

THE GATE (two mechanisms):
  1. REFUSE-ON-MISSING-PROJECT-ID: when a chat is project-pinned but the project
     has no resolvable id, the query is REFUSED (error), it does NOT fall back to
     the user's own wing. Falling back would search the wrong scope; refusing is
     the leak-prevention branch. Pinned directly against the live function.
  2. CROSS-WING VISIBILITY FILTER: an unspecified-wing search drops every
     project__*/project_chat__* result, keeps only the caller's own user__ wing,
     keeps only team__ wings the caller belongs to, and treats bare/untyped wings
     as shared. Pinned against the module-level `_wing_visible` predicate.

C3 EXTRACTION REQUIREMENT: the visibility predicate currently lives as a closure
`_visible` inside `tool_mempalace_query`. The C3 extraction MUST promote it to a
module-level `engine.mempalace_glue._wing_visible(wing, own_user, own_teams) -> bool`
so the security gate is testable as a pure unit. Until then, this file imports it
from brain (where the extraction will re-export it). If the symbol is absent the
visibility tests are skipped with a loud message — they MUST be un-skipped and
green before C3 is accepted.

Run: python3 -m unittest tests.test_mempalace_wing_isolation -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import _thread_local  # noqa: E402


# The visibility predicate. Pre-C3 it's a closure (not importable); post-C3 it
# must be a module-level helper re-exported on brain. Resolve whichever exists.
_wing_visible = getattr(brain, "_wing_visible", None)


def _build_visible(own_user: str, own_teams: set[str]):
    """Wrap whatever predicate form exists into a uniform callable.

    Post-C3: brain._wing_visible(wing, own_user, own_teams).
    Pre-C3: predicate not exposed -> return None so tests skip loudly.
    """
    if _wing_visible is None:
        return None
    return lambda wing: _wing_visible(wing, own_user, own_teams)


class _MPFixture(unittest.TestCase):
    """Save/restore the thread-locals the query reads, and stub the MemPalace
    side-effects (config load + importability + isdir) so the pure scoping
    branches run without a live palace."""

    def setUp(self):
        self._saved = {k: getattr(_thread_local, k, None) for k in
                       ("project", "current_user_id", "current_team_ids", "current_agent")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                try:
                    delattr(_thread_local, k)
                except AttributeError:
                    pass
            else:
                setattr(_thread_local, k, v)

    def _ctx(self, *, project=None, user_id="", team_ids=None, agent_id="main"):
        if project is None:
            try:
                delattr(_thread_local, "project")
            except AttributeError:
                pass
        else:
            _thread_local.project = project
        _thread_local.current_user_id = user_id
        _thread_local.current_team_ids = team_ids or []
        _thread_local.current_agent = agent_id  # string form accepted by the resolver


class TestRefuseOnMissingProjectId(_MPFixture):
    """THE leak-prevention gate. INVARIANT: project-pinned + unresolvable id =>
    error, NOT a fall-back search of the user's own wing. Pinned against the
    live tool_mempalace_query (the refuse branch returns before any Chroma call,
    so it's reachable with the side-effects stubbed)."""

    def _run_query(self, get_project_return):
        with mock.patch.object(brain, "_load_mempalace_config",
                               return_value={"enabled": True, "palace_path": "/tmp"}), \
             mock.patch.object(brain, "_ensure_mempalace_importable",
                               return_value=(True, "")), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch.object(brain.ProjectManager, "get_project",
                               return_value=get_project_return):
            return brain.tool_mempalace_query({"query": "anything"})

    def test_project_pinned_no_id_refuses(self):
        self._ctx(project="someproject", user_id="alice", agent_id="main")
        out = self._run_query(get_project_return={"id": ""})
        parsed = json.loads(out)
        self.assertIn("error", parsed)
        self.assertIn("no id", parsed["error"])

    def test_refusal_does_not_leak_to_user_wing(self):
        # The decisive assertion: even though current_user_id is set, the refused
        # query must NOT have searched user__alice. The error return short-circuits
        # before any wing search — so a successful (non-error) result here would be
        # the leak. We assert it errored (no fall-through path exists).
        self._ctx(project="someproject", user_id="alice", agent_id="main")
        out = self._run_query(get_project_return=None)  # project lookup fails entirely
        parsed = json.loads(out)
        self.assertIn("error", parsed, "project-pinned query with no id must refuse, not fall back")


class TestCrossWingVisibility(_MPFixture):
    """The unspecified-wing visibility filter. INVARIANT: project wings always
    private; only the caller's own user__ wing + their team__ wings + bare
    (shared) wings are visible. This is what stops a broad search from surfacing
    another project's or another user's drawers."""

    def setUp(self):
        super().setUp()
        if _wing_visible is None:
            self.skipTest(
                "brain._wing_visible not exposed yet — C3 extraction MUST promote "
                "the tool_mempalace_query `_visible` closure to a module-level "
                "_wing_visible(wing, own_user, own_teams) and re-export it; then "
                "these wing-isolation tests un-skip and must pass.")

    def test_project_knowledge_wing_always_dropped(self):
        vis = _build_visible("user__alice", {"team__t1"})
        self.assertFalse(vis("project__proj1"))

    def test_project_chat_wing_always_dropped(self):
        vis = _build_visible("user__alice", {"team__t1"})
        self.assertFalse(vis("project_chat__proj1"))

    def test_own_user_wing_kept(self):
        vis = _build_visible("user__alice", set())
        self.assertTrue(vis("user__alice"))

    def test_other_user_wing_dropped(self):
        vis = _build_visible("user__alice", set())
        self.assertFalse(vis("user__bob"))

    def test_own_team_wing_kept(self):
        vis = _build_visible("user__alice", {"team__t1", "team__t2"})
        self.assertTrue(vis("team__t1"))

    def test_foreign_team_wing_dropped(self):
        vis = _build_visible("user__alice", {"team__t1"})
        self.assertFalse(vis("team__t3"))

    def test_bare_shared_wing_kept(self):
        vis = _build_visible("user__alice", set())
        self.assertTrue(vis("brain_code"))
        self.assertTrue(vis("shared_docs"))


if __name__ == "__main__":
    unittest.main()
