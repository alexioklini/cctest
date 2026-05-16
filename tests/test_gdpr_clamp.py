"""Unit tests for the transparent-anonymisation system-prompt clamp
(Step 6.1 of the rollout).

The clamp is appended in `brain._apply_system_prompt_postprocess` when
`gdpr_anon=True`. Its job is to tell the model the `<KIND_N_HEX>` tokens
in the user message are placeholders and must be copied verbatim into the
reply (the server then de-anonymises before showing the user).

We test the postprocess in isolation — exercising the full
`_build_system_prompt` would require setting up agents, soul.md, MCP, etc.
The postprocess is a pure string transform, so isolated coverage is
enough to lock in the behaviour.

Run: python3 -m unittest tests.test_gdpr_clamp -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class TestGdprClampPostprocess(unittest.TestCase):
    def test_clamp_absent_by_default(self):
        out = brain._apply_system_prompt_postprocess(
            "BASE", caveman_system=0, caveman_chat=0, plan_mode=False)
        self.assertEqual(out, "BASE")
        self.assertNotIn("PSEUDONYMISATION", out)

    def test_clamp_appended_when_active(self):
        out = brain._apply_system_prompt_postprocess(
            "BASE", caveman_system=0, caveman_chat=0, plan_mode=False,
            gdpr_anon=True)
        self.assertTrue(out.startswith("BASE"))
        self.assertIn("PSEUDONYMISATION", out)
        self.assertIn("verbatim", out)
        # Token shape mention so the model knows what to look for.
        self.assertIn("<KIND_N_HEX>", out)
        # Negative direction — shape-preserving fakes are NOT placeholders.
        self.assertIn("Shape-preserving fakes", out)

    def test_clamp_combines_with_plan_mode(self):
        out = brain._apply_system_prompt_postprocess(
            "BASE", caveman_system=0, caveman_chat=0, plan_mode=True,
            gdpr_anon=True)
        # Both blocks present; order is plan_mode-then-clamp per the body.
        self.assertIn("PSEUDONYMISATION", out)
        idx_plan = out.find(brain.PLAN_MODE_PROMPT.strip()[:20])
        idx_clamp = out.find("PSEUDONYMISATION")
        self.assertGreater(idx_clamp, idx_plan)

    def test_clamp_text_stable(self):
        # The clamp is read by frontier models — its shape was validated by
        # the 109-test benchmark cited in the handover. Lock in the marker
        # so accidental edits surface as test failures.
        self.assertIn("<EMAIL_1_a8k2>", brain._GDPR_ANON_CLAMP)
        self.assertIn("Copy each token verbatim", brain._GDPR_ANON_CLAMP)
        self.assertIn("salt suffix", brain._GDPR_ANON_CLAMP)


if __name__ == "__main__":
    unittest.main()
