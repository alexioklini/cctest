"""Tests for the deterministic, decision-ledger-driven wire-history rewrite.

The fix (chat 6f034721): once a value is anonymised, it must stay protected in
the wire-history on EVERY later turn — including turns where the user picks
'continue' for a NEW finding — while accepted/false-positive values keep going
out in clear. The rewrite is driven purely by the persisted pii_decisions
ledger (original→fake for anonymise decisions), not by re-scanning or a live
mapping. This locks that behaviour.

Run: python3 -m unittest tests.test_gdpr_decision_wire -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from handlers.chat import _apply_pii_decisions_to_wire  # noqa: E402


def _ledger(*entries):
    """entries: (rule_id, value, turn_action, fake_value, false_positive?)."""
    out = {}
    for i, e in enumerate(entries):
        rid, val, ta, fake = e[0], e[1], e[2], e[3]
        fp = e[4] if len(e) > 4 else False
        out[f"h{i}"] = {
            "rule_id": rid, "value": val, "turn_action": ta,
            "fake_value": fake, "false_positive": fp,
        }
    return out


class TestDecisionDrivenWire(unittest.TestCase):
    def test_anonymised_value_replaced_everywhere(self):
        """An anonymise decision replaces the original with its fake in BOTH
        the user message and the assistant reply across all turns."""
        decisions = _ledger(("email", "geheim@firma.de", "anonymise",
                             "jordan@example.de"))
        msgs = [
            {"role": "human", "content": "meine email ist geheim@firma.de"},
            {"role": "assistant",
             "content": "Notiert: geheim@firma.de ist deine Email."},
            {"role": "human", "content": "danke"},
        ]
        wire, n, counts = _apply_pii_decisions_to_wire(msgs, decisions)
        self.assertEqual(n, 2)                       # two occurrences rewritten
        self.assertEqual(counts.get("email"), 2)
        self.assertNotIn("geheim@firma.de", wire[0]["content"])
        self.assertIn("jordan@example.de", wire[0]["content"])
        self.assertNotIn("geheim@firma.de", wire[1]["content"])
        self.assertIn("jordan@example.de", wire[1]["content"])
        # The original messages list is NOT mutated (wire-only copy).
        self.assertIn("geheim@firma.de", msgs[0]["content"])

    def test_accepted_value_stays_clear(self):
        """A 'send'/continue decision (accepted in clear) is left verbatim —
        this is the user's explicit choice for that value."""
        decisions = _ledger(
            ("email", "geheim@firma.de", "anonymise", "jordan@example.de"),
            ("phone", "+4369917200119", "send", ""),
        )
        msgs = [
            {"role": "human", "content": "email geheim@firma.de"},
            {"role": "human", "content": "tel +4369917200119"},
        ]
        wire, n, _ = _apply_pii_decisions_to_wire(msgs, decisions)
        # email anonymised, phone untouched
        self.assertIn("jordan@example.de", wire[0]["content"])
        self.assertIn("+4369917200119", wire[1]["content"])   # stays clear
        self.assertEqual(n, 1)

    def test_false_positive_stays_clear(self):
        """A value marked false-positive is never rewritten even if it somehow
        also carries an anonymise turn_action."""
        decisions = _ledger(
            ("email", "real@me.de", "anonymise", "fake@x.de", True),
        )
        msgs = [{"role": "human", "content": "real@me.de"}]
        wire, n, _ = _apply_pii_decisions_to_wire(msgs, decisions)
        self.assertEqual(n, 0)
        self.assertIn("real@me.de", wire[0]["content"])

    def test_unseen_value_untouched(self):
        """A value with no decision (new this turn) is left alone — the current
        turn's own flow handles it, not the history pass."""
        decisions = _ledger(("email", "old@x.de", "anonymise", "fake@x.de"))
        msgs = [{"role": "human", "content": "brand new value 12345"}]
        wire, n, _ = _apply_pii_decisions_to_wire(msgs, decisions)
        self.assertEqual(n, 0)
        self.assertEqual(wire[0]["content"], "brand new value 12345")

    def test_list_content_blocks(self):
        """Multimodal list-of-blocks content is rewritten in its text blocks."""
        decisions = _ledger(("email", "a@b.de", "anonymise", "c@d.de"))
        msgs = [{
            "role": "human",
            "content": [
                {"type": "text", "text": "mail a@b.de"},
                {"type": "image_url", "image_url": {"url": "x"}},
            ],
        }]
        wire, n, _ = _apply_pii_decisions_to_wire(msgs, decisions)
        self.assertEqual(n, 1)
        self.assertEqual(wire[0]["content"][0]["text"], "mail c@d.de")
        # non-text block preserved untouched
        self.assertEqual(wire[0]["content"][1]["type"], "image_url")

    def test_longest_original_first(self):
        """Overlapping values: the longer original is replaced first so a
        shorter substring can't partially shadow it."""
        decisions = _ledger(
            ("name", "Alexander Klinsky", "anonymise", "John Doe"),
            ("name", "Alexander", "anonymise", "Mike"),
        )
        msgs = [{"role": "human", "content": "ich bin Alexander Klinsky"}]
        wire, _, _ = _apply_pii_decisions_to_wire(msgs, decisions)
        self.assertIn("John Doe", wire[0]["content"])
        self.assertNotIn("Alexander Klinsky", wire[0]["content"])

    def test_empty_ledger_noop(self):
        msgs = [{"role": "human", "content": "hi"}]
        wire, n, counts = _apply_pii_decisions_to_wire(msgs, {})
        self.assertEqual(n, 0)
        self.assertEqual(counts, {})
        self.assertEqual(wire, msgs)


if __name__ == "__main__":
    unittest.main()
