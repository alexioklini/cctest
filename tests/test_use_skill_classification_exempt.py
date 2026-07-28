"""use_skill is exempt from the ARL classification gate (9.414.1).

WHY: the gate's keyword heuristic scans tool output as if it were a user
document. Skill bodies are Brain's own curated instruction text, and ordinary
English prose in them can collide with the WPB strict-keyword list — the real
incident (chat 376735a4db1e) had "ad-hoc HTML defeats that" in
document-markdown/SKILL.md match the strict keyword "Ad-hoc"
(case-insensitive substring, no word boundaries), which made the skill
unloadable on every cloud model: heuristic strict → strict-always-block.

The fix is the `classify` parameter on `_gdpr_anon_tool_text`: use_skill
passes classify=False, which skips ONLY the classification gate. The GDPR
pseudonymisation sweep must still run for use_skill (chat-generated skills
can quote real names from their source conversation — the v9.294 M3
rationale is untouched), so the exemption must not grow into "skip the
whole seam".

Bare test interpreter — no server, no spaCy, no network.
"""

import unittest
from unittest import mock

import brain
from engine.context import request_context


STRICT_BAIT = "Use a preset style; ad-hoc HTML defeats that."


class TestClassifyFlag(unittest.TestCase):
    """classify=False must be the ONLY thing that bypasses the gate."""

    def test_classify_false_never_reaches_gate(self):
        with request_context():
            with mock.patch.object(
                brain, "_classification_gate_tool_text"
            ) as gate:
                out = brain._gdpr_anon_tool_text(
                    STRICT_BAIT, "use_skill:document-markdown", classify=False
                )
        gate.assert_not_called()
        # No active mapping → text passes through unchanged.
        self.assertEqual(out, STRICT_BAIT)

    def test_default_still_gated(self):
        """Omitting the parameter keeps the pre-9.414.1 behaviour for the
        44 other callers — a gate raise must propagate."""
        with request_context():
            with mock.patch.object(
                brain,
                "_classification_gate_tool_text",
                side_effect=brain.ClassificationBlockedError("blocked"),
            ) as gate:
                with self.assertRaises(brain.ClassificationBlockedError):
                    brain._gdpr_anon_tool_text(STRICT_BAIT, "file:report.pdf")
        gate.assert_called_once()


class TestUseSkillCallsite(unittest.TestCase):
    """tool_use_skill must request the exemption — the flag alone is inert
    if the callsite doesn't pass it."""

    def test_use_skill_passes_classify_false(self):
        fake_agent = mock.Mock()
        fake_agent.load_skill.return_value = STRICT_BAIT
        fake_agent.list_skills.return_value = []
        fake_agent.list_user_skills.return_value = []
        with request_context(current_agent=fake_agent):
            with mock.patch.object(
                brain, "_gdpr_anon_tool_text", wraps=brain._gdpr_anon_tool_text
            ) as seam:
                from engine.tools.misc_tools import tool_use_skill
                out = tool_use_skill({"skill": "document-markdown"})
        seam.assert_called_once()
        self.assertFalse(seam.call_args.kwargs.get("classify", True))
        # The skill body reached the model despite the strict-keyword bait.
        self.assertIn("ad-hoc HTML", out)
        self.assertNotIn("Classification block", out)
