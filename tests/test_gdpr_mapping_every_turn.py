"""M1 (G1) — "every turn has a mapping".

The Welle-2 headline invariant: a background / scheduled / delegated turn must
run inside the SAME anonymisation mapping as the chat that spawned it.

Why this has teeth: EVERY GDPR enforcement point keys off exactly one field,
`RequestContext._gdpr_mapping_id` —

    brain._gdpr_anon_tool_text   (tool-result seam)
    brain._gdpr_deanon_tool_args (args de-anonymiser)
    brain._gdpr_guard_web_args   (web-egress gate)

…and each of them NO-OPS when it is empty. Before this fix `_apply_bg_context`
rebuilt ~20 context fields for background turns and simply omitted that one, so
a scheduled run or a fan-out leaf could read the customer file in the clear and
google the real name. The entry prompt was gated; the whole agentic tail was not.

These tests would all have passed a "does it set an attribute" check. They are
written instead against the PROPERTY that makes the difference: after the bind,
does the gate actually fire?

Bare test interpreter — no server, no spaCy, no network.
"""

import unittest
from unittest import mock

import brain
from engine.context import get_request_context, request_context


class TestBindRehydrates(unittest.TestCase):
    """gdpr_bind_mapping must survive close_mapping().

    This is the sharp edge of M1 and the reason a bare `tl._gdpr_mapping_id = id`
    would NOT have been enough. The interactive worker calls
    `pseudonymizer.close_mapping()` in its finally — dropping the mapping from the
    in-memory registry the moment the spawning turn ends — while a detached
    background task may still run for another hour. A sub-turn that merely
    inherited the ID would then find get_mapping() → None, every seam would treat
    that as "no anonymisation active", and G1 would re-open through the back door
    on exactly the long fan-outs this fix exists for.
    """

    def test_binds_when_mapping_is_in_registry(self):
        import pseudonymizer as ps
        m = ps.new_mapping()
        try:
            with request_context():
                self.assertTrue(brain.gdpr_bind_mapping(m.mapping_id))
                self.assertEqual(
                    get_request_context()._gdpr_mapping_id, m.mapping_id)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_rehydrates_after_close_mapping(self):
        """The registry entry is gone (parent turn ended) but the encrypted
        chats.db row remains — bind must reload from it."""
        import pseudonymizer as ps
        m = ps.new_mapping()
        mid = m.mapping_id
        ps.close_mapping(mid)                      # parent turn's finally
        self.assertIsNone(ps.get_mapping(mid))     # registry really is empty

        with mock.patch.object(ps, "load_mapping", return_value=m) as loader:
            with request_context():
                self.assertTrue(brain.gdpr_bind_mapping(mid))
                self.assertEqual(get_request_context()._gdpr_mapping_id, mid)
            loader.assert_called_once_with(mid)
        ps.close_mapping(mid)

    def test_unresolvable_mapping_does_not_bind(self):
        """A mapping that is neither in the registry nor persisted must leave the
        context UNMAPPED and report failure — never silently pretend to protect."""
        with mock.patch("pseudonymizer.load_mapping", return_value=None):
            with request_context():
                self.assertFalse(brain.gdpr_bind_mapping("does-not-exist"))
                self.assertEqual(get_request_context()._gdpr_mapping_id, "")

    def test_empty_id_is_a_noop(self):
        with request_context():
            self.assertFalse(brain.gdpr_bind_mapping(""))
            self.assertEqual(get_request_context()._gdpr_mapping_id, "")


class TestBgContextCarriesMapping(unittest.TestCase):
    """_apply_bg_context is the receiving side for every non-interactive turn."""

    def test_bg_context_binds_mapping(self):
        import pseudonymizer as ps
        from handlers import sidecar_proxy
        m = ps.new_mapping()
        try:
            with request_context():
                sidecar_proxy._apply_bg_context({
                    "session_id": "s", "agent_id": "main",
                    "gdpr_mapping_id": m.mapping_id,
                })
                self.assertEqual(
                    get_request_context()._gdpr_mapping_id, m.mapping_id)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_bg_context_without_mapping_stays_empty(self):
        """Non-anonymising sessions must be completely untouched."""
        from handlers import sidecar_proxy
        with request_context():
            sidecar_proxy._apply_bg_context({
                "session_id": "s", "agent_id": "main",
            })
            self.assertEqual(get_request_context()._gdpr_mapping_id, "")


class TestBuildToolContextCarriesMapping(unittest.TestCase):
    def test_snapshots_mapping_id(self):
        with request_context():
            tc = brain.build_tool_context(
                session_id="s", agent_id="main", gdpr_mapping_id="mid-123")
            self.assertEqual(tc.get("gdpr_mapping_id"), "mid-123")

    def test_defaults_to_empty(self):
        with request_context():
            tc = brain.build_tool_context(session_id="s", agent_id="main")
            self.assertEqual(tc.get("gdpr_mapping_id"), "")


class TestGateIsLiveInBackgroundTurn(unittest.TestCase):
    """The payoff test — the one that actually encodes G1.

    Not "is the field set" but: does a background turn's web-egress gate REFUSE a
    search for a protected value? Before M1 this returned "no gate" because the
    mapping id never reached the background context.
    """

    def test_web_gate_refuses_protected_value_in_bg_turn(self):
        import pseudonymizer as ps
        from handlers import sidecar_proxy

        m = ps.new_mapping()
        # Register a protected value exactly as the chat turn would have.
        m.forward["Bonnie Stark"] = "Sam Mitchell"
        m.reverse["Sam Mitchell"] = "Bonnie Stark"
        m.categories["Bonnie Stark"] = "contact"

        try:
            with request_context():
                sidecar_proxy._apply_bg_context({
                    "session_id": "", "agent_id": "main",
                    "gdpr_mapping_id": m.mapping_id,
                })
                err, _args = brain._gdpr_guard_web_args(
                    "searxng_search", {"query": "Bonnie Stark obituary"})
            self.assertIsNotNone(
                err,
                "background turn googled a protected real name — G1 is open")
        finally:
            ps.close_mapping(m.mapping_id)

    def test_web_gate_inactive_without_mapping(self):
        """Control: a non-anonymising background turn is not gated at all."""
        from handlers import sidecar_proxy
        with request_context():
            sidecar_proxy._apply_bg_context({
                "session_id": "", "agent_id": "main",
            })
            err, _args = brain._gdpr_guard_web_args(
                "searxng_search", {"query": "Bonnie Stark obituary"})
        self.assertIsNone(err)


class TestDeanonFnCarriesMappingId(unittest.TestCase):
    """Detached agentic callers (scheduler, delegate, bg-task) read the minted
    mapping id off the returned deanon_fn. Every return path must expose it, or
    those callers would need a getattr dance and silently fall back to unmapped."""

    def test_identity_deanon_exposes_empty_mapping_id(self):
        self.assertEqual(brain._identity_deanon.mapping_id, "")


if __name__ == "__main__":
    unittest.main()
