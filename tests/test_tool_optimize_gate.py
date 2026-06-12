"""Per-turn classifier tool-optimization gate (brain.model_should_optimize_tools).

The gate decides whether the LLM classifier may reshape this turn's tool list
(defer un-needed groups out, pull needed groups in). It must NOT reshape when
that would invalidate a warm KV prefix — but it MUST reshape whenever there is
no tool-bearing prefix to protect, so a leanly-routed turn still gets a trimmed
prompt.

The subtle case this file pins (the reason it exists): warmup_mode="minimal".
A minimal-mode prime sends NO system prompt and NO tools — it keeps the model's
weights hot but primes no KV prefix. So a minimal-mode warmup model has nothing
for per-turn reshaping to invalidate and MUST be optimized, even though
warmup=true. The earlier gate keyed only on the warmup boolean and wrongly
protected it (cost: ~40s cold first response on a warm-but-unoptimized local
model that could have shipped a lean prompt). full-mode stays protected because
its prime DOES bake the static tool set into the prefix.

Run: python3 -m unittest tests.test_tool_optimize_gate
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class TestModelShouldOptimizeTools(unittest.TestCase):
    def setUp(self):
        # Snapshot + install a synthetic model table so the gate reads our cases
        # and never the real config.json.
        self._saved = brain._models_config
        brain._models_config = {
            "cloud-model":        {"warmup": False},
            "local-no-warmup":    {"warmup": False, "is_local": True},
            "local-warm-full":    {"warmup": True,  "warmup_mode": "full"},
            "local-warm-minimal": {"warmup": True,  "warmup_mode": "minimal"},
            # warmup=true with NO explicit mode resolves to "full" (the keeper's
            # default in server_daemons._warmup_keeper_loop) → protected.
            "local-warm-default": {"warmup": True},
            # A bogus mode string also falls back to "full" → protected (mirror
            # the keeper's `if desired_mode not in (...) : desired_mode = "full"`).
            "local-warm-bogus":   {"warmup": True,  "warmup_mode": "garbage"},
        }

    def tearDown(self):
        brain._models_config = self._saved

    def test_cloud_optimizes(self):
        # No reusable prefix; varying the tool list per turn is free.
        self.assertTrue(brain.model_should_optimize_tools("cloud-model"))

    def test_local_no_warmup_optimizes(self):
        # Never warmed → nothing to lose.
        self.assertTrue(brain.model_should_optimize_tools("local-no-warmup"))

    def test_full_mode_is_protected(self):
        # The prime bakes the static tool set into the KV prefix; reshaping
        # would prime a trimmed prefix that the next warm turn diverges from.
        self.assertFalse(brain.model_should_optimize_tools("local-warm-full"))

    def test_minimal_mode_optimizes(self):
        # THE point of this gate change: weights-only prime → no tool-bearing
        # prefix → safe (and desirable) to reshape per turn.
        self.assertTrue(brain.model_should_optimize_tools("local-warm-minimal"))

    def test_warmup_default_mode_is_protected(self):
        # warmup=true, no mode → keeper treats as "full" → protected.
        self.assertFalse(brain.model_should_optimize_tools("local-warm-default"))

    def test_bogus_mode_falls_back_to_full_protected(self):
        self.assertFalse(brain.model_should_optimize_tools("local-warm-bogus"))

    def test_auto_and_empty_are_conservative(self):
        # Unknown model id → don't reshape (can't reason about its prefix).
        self.assertFalse(brain.model_should_optimize_tools("auto"))
        self.assertFalse(brain.model_should_optimize_tools(""))

    def test_unknown_model_id_optimizes_as_non_warmup(self):
        # A model id absent from the table has no warmup flag → treated as a
        # plain (cloud/no-warmup) model → optimize. Documents the {} fallthrough.
        self.assertTrue(brain.model_should_optimize_tools("not-in-table"))


if __name__ == "__main__":
    unittest.main()
