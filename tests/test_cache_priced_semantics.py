"""Three-state semantics of `brain.model_is_cache_priced` (v9.386.2).

The per-model `cost_cache_read` field now carries THREE states for the
routing-freeze / monotone-tool-gating decision — aligning the FREEZE trigger
with the cost display, which always defaulted unset→0.1× input:

    UNSET (None)  → DEFAULT: cloud ⇒ ON, local ⇒ OFF
    0 (explicit)  → OFF (operator opted out, no prefix-stability freeze)
    > 0           → ON (explicit per-1M rate)

Locality comes from `is_model_local` (provider `is_local` flag — the single
source of truth), mocked here so the test never touches config.json.

Run: python3 -m unittest tests.test_cache_priced_semantics
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class TestCachePricedThreeState(unittest.TestCase):
    def setUp(self):
        self._saved_mc = brain._models_config
        self._saved_iml = brain.is_model_local
        brain._models_config = {
            "cloud-unset": {},
            "cloud-zero": {"cost_cache_read": 0},
            "cloud-value": {"cost_cache_read": 0.05},
            "local-unset": {},
            "local-zero": {"cost_cache_read": 0},
            "local-value": {"cost_cache_read": 0.05},
            "cloud-bad": {"cost_cache_read": "garbage"},
        }
        # Locality by name prefix — stand-in for the provider is_local flag.
        brain.is_model_local = lambda m: m.startswith("local-")

    def tearDown(self):
        brain._models_config = self._saved_mc
        brain.is_model_local = self._saved_iml

    # ── DEFAULT (unset) ──────────────────────────────────────────────────
    def test_cloud_unset_is_on(self):
        # The common case: leave it blank on a cloud model ⇒ cache-priced.
        self.assertTrue(brain.model_is_cache_priced("cloud-unset"))

    def test_local_unset_is_off(self):
        # A local model keeps its own KV — no provider cache to protect.
        self.assertFalse(brain.model_is_cache_priced("local-unset"))

    # ── explicit 0 = OFF ─────────────────────────────────────────────────
    def test_cloud_explicit_zero_is_off(self):
        # The ONLY way to turn a cloud model off is an explicit 0.
        self.assertFalse(brain.model_is_cache_priced("cloud-zero"))

    def test_local_explicit_zero_is_off(self):
        self.assertFalse(brain.model_is_cache_priced("local-zero"))

    # ── explicit >0 = ON (both cloud and local) ──────────────────────────
    def test_cloud_value_is_on(self):
        self.assertTrue(brain.model_is_cache_priced("cloud-value"))

    def test_local_value_is_on(self):
        # An explicit positive rate opts a local model IN (rare, but honoured).
        self.assertTrue(brain.model_is_cache_priced("local-value"))

    # ── edge cases ───────────────────────────────────────────────────────
    def test_unparseable_falls_back_to_default(self):
        # A garbage value must not crash; treat as unset → cloud default ON.
        self.assertTrue(brain.model_is_cache_priced("cloud-bad"))

    def test_auto_and_empty_never_priced(self):
        self.assertFalse(brain.model_is_cache_priced("auto"))
        self.assertFalse(brain.model_is_cache_priced(""))


class TestToolReshapeAllowed(unittest.TestCase):
    """model_tool_reshape_allowed — the combined per-turn tool-gating gate.

    The collision the cloud-default change created: a warmup-protected CLOUD
    model is now cache-priced by default, but its warm KV prefix must NOT be
    reshaped. The bare `should_optimize OR cache_priced` let cache-priced win;
    the combined gate makes warmup protection win.
    """
    def setUp(self):
        self._saved_mc = brain._models_config
        self._saved_iml = brain.is_model_local
        brain._models_config = {
            "cloud-plain": {},                                   # cache-priced by default
            "cloud-warm-full": {"warmup": True},                 # cache-priced AND warm-protected
            "cloud-warm-minimal": {"warmup": True, "warmup_mode": "minimal"},
            "cloud-off": {"cost_cache_read": 0},                 # cache OFF, no warmup
            "local-plain": {},                                   # not cache-priced, never warms
            "local-warm-full": {"warmup": True},                 # warm-protected local
        }
        brain.is_model_local = lambda m: m.startswith("local-")

    def tearDown(self):
        brain._models_config = self._saved_mc
        brain.is_model_local = self._saved_iml

    def test_plain_cloud_reshapes(self):
        # No warm prefix, cache-priced-by-default → reshape (grows monotonically).
        self.assertTrue(brain.model_tool_reshape_allowed("cloud-plain"))

    def test_warm_full_cloud_does_not_reshape(self):
        # THE collision: cache-priced by default, BUT a full warmup prefix to
        # protect → must NOT reshape (the bug the combined gate fixes).
        self.assertFalse(brain.model_tool_reshape_allowed("cloud-warm-full"))

    def test_warm_minimal_cloud_reshapes(self):
        # Minimal prime = weights only, no tool-bearing prefix → reshape.
        self.assertTrue(brain.model_tool_reshape_allowed("cloud-warm-minimal"))

    def test_cache_off_cloud_still_reshapes(self):
        # cost_cache_read=0 turns off the FREEZE, but a plain no-warmup cloud
        # model is still optimizable via model_should_optimize_tools.
        self.assertTrue(brain.model_tool_reshape_allowed("cloud-off"))

    def test_plain_local_reshapes(self):
        # Never warms → nothing to protect (model_should_optimize_tools=True).
        self.assertTrue(brain.model_tool_reshape_allowed("local-plain"))

    def test_warm_full_local_does_not_reshape(self):
        self.assertFalse(brain.model_tool_reshape_allowed("local-warm-full"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
