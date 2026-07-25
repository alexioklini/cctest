"""thinking_switch_costs_prefix — warn on exactly one case, stay silent otherwise.

The dial is meant to be turned freely: graduating low↔medium↔high never changes
the tokenised prompt, so it must never warn. Only an on↔off flip on a provider
whose chat template renders enable_thinking INTO the prompt (oMLX/vLLM) throws
away a warm KV prefix — and only when a prefix is actually warm.

A false warning is worse than a missed one: it trains the user to dismiss the
toast, and then the one warning that matters is ignored too.

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
                    brain.thinking_switch_costs_prefix("local-model", old, new))

    def test_cloud_flip_never_warns(self):
        """On cloud models thinking is reasoning_effort — an API field. Flipping
        it leaves the prompt, and therefore the prefix, untouched."""
        self._patch(thinking_relevant=False, warm=True)
        self.assertFalse(
            brain.thinking_switch_costs_prefix("cloud-model", "none", "high"))
        self.assertFalse(
            brain.thinking_switch_costs_prefix("cloud-model", "high", "none"))

    def test_local_flip_on_warm_prefix_warns(self):
        """The one real case: on↔off on a chat-template provider, prefix warm."""
        self._patch(thinking_relevant=True, warm=True)
        self.assertTrue(
            brain.thinking_switch_costs_prefix("local-model", "none", "high"))
        self.assertTrue(
            brain.thinking_switch_costs_prefix("local-model", "medium", "none"))
        # '' and 'off' count as off, matching the rest of the codebase.
        self.assertTrue(
            brain.thinking_switch_costs_prefix("local-model", "", "low"))
        self.assertTrue(
            brain.thinking_switch_costs_prefix("local-model", "off", "high"))

    def test_local_flip_on_cold_prefix_stays_silent(self):
        """Nothing warm to lose → nothing to warn about."""
        self._patch(thinking_relevant=True, warm=False)
        self.assertFalse(
            brain.thinking_switch_costs_prefix("local-model", "none", "high"))

    def test_unknown_prefix_stays_silent(self):
        """Prefix id unavailable = uncertainty. Prefer a missed warning (costs
        one prefill) over a false one (costs the warning's credibility)."""
        self._patch(thinking_relevant=True, warm=True, prefix_id=None)
        self.assertFalse(
            brain.thinking_switch_costs_prefix("local-model", "none", "high"))


if __name__ == "__main__":
    unittest.main()
