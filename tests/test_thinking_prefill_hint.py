"""thinking_switch_costs_prefill — hint on exactly one case, stay silent otherwise.

This is about LATENCY, not money. Two different caches are easy to conflate:
  • warm GPU prefill (local models) — losing it means the next reply starts
    seconds later. Costs nothing; it is our own hardware.
  • the provider's prompt cache (cloud) — that one is billing, and the thinking
    dial never disturbs it: reasoning_effort is a top-level field outside the
    messages, and the depth suffix rides on the last user message, which differs
    every turn anyway. Hence: no cloud model ever reports true.

So the only case worth a hint is an on↔off flip on a provider whose chat
template renders enable_thinking INTO the tokenised prompt (oMLX/vLLM), and only
when that prefill was actually warm.

A false hint is worse than a missed one: it trains the user to dismiss the
toast, and then the one message that matters is ignored too.

Runs in the bare test interpreter (unittest, no pytest) — the collaborators are
monkey-patched, so no server, no models config, no network.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class ThinkingPrefixWarningTest(unittest.TestCase):

    def setUp(self):
        self._orig = (brain.prefix_thinking_relevant,
                      brain._bare_full_prefix_id,
                      brain.prefix_is_warm)

    def tearDown(self):
        (brain.prefix_thinking_relevant,
         brain._bare_full_prefix_id,
         brain.prefix_is_warm) = self._orig

    def _patch(self, *, thinking_relevant, warm, prefix_id="pid-1"):
        brain.prefix_thinking_relevant = lambda m: thinking_relevant
        brain._bare_full_prefix_id = lambda m, *a, **k: prefix_id
        brain.prefix_is_warm = lambda m, pid, **k: warm

    def test_graduation_never_warns(self):
        """low↔medium↔high rides on an API field or a wire suffix — the prefix
        is byte-identical. Silent even on a local model with a warm prefix."""
        self._patch(thinking_relevant=True, warm=True)
        for old, new in [("low", "medium"), ("medium", "high"), ("high", "low"),
                         ("low", "low"), ("high", "medium")]:
            with self.subTest(old=old, new=new):
                self.assertFalse(
                    brain.thinking_switch_costs_prefill("local-model", old, new))

    def test_cloud_flip_never_warns(self):
        """Cloud models have no local prefill to lose, and the dial doesn't touch
        the provider's prompt cache either — so there is no latency AND no
        billing effect to hint at."""
        self._patch(thinking_relevant=False, warm=True)
        self.assertFalse(
            brain.thinking_switch_costs_prefill("cloud-model", "none", "high"))
        self.assertFalse(
            brain.thinking_switch_costs_prefill("cloud-model", "high", "none"))

    def test_local_flip_on_warm_prefix_warns(self):
        """The one real case: on↔off on a chat-template provider, prefix warm."""
        self._patch(thinking_relevant=True, warm=True)
        self.assertTrue(
            brain.thinking_switch_costs_prefill("local-model", "none", "high"))
        self.assertTrue(
            brain.thinking_switch_costs_prefill("local-model", "medium", "none"))
        # '' and 'off' count as off, matching the rest of the codebase.
        self.assertTrue(
            brain.thinking_switch_costs_prefill("local-model", "", "low"))
        self.assertTrue(
            brain.thinking_switch_costs_prefill("local-model", "off", "high"))

    def test_local_flip_on_cold_prefix_stays_silent(self):
        """Nothing warm to lose → nothing to warn about."""
        self._patch(thinking_relevant=True, warm=False)
        self.assertFalse(
            brain.thinking_switch_costs_prefill("local-model", "none", "high"))

    def test_unknown_prefix_stays_silent(self):
        """Prefix id unavailable = uncertainty. Prefer a missed hint (one slower
        turn) over a false one (costs the hint's credibility)."""
        self._patch(thinking_relevant=True, warm=True, prefix_id=None)
        self.assertFalse(
            brain.thinking_switch_costs_prefill("local-model", "none", "high"))


if __name__ == "__main__":
    unittest.main()
