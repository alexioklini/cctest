"""M8 (G11) — Citation-Validator runs AFTER the de-anonymise pass.

The citation validator byte-matches each quote in the assistant reply against
the REAL source files on disk. When a GDPR mapping is active the reply carries
FAKE tokens until `deanonymize_text` reverses them, so a quote containing a
protected value can only verify once the reply is de-anonymised. Running the
validator BEFORE the reverse pass (the pre-M8 bug) marked every such quote
"unverified" and hung a spurious fidelity warning on practically every
research-mode answer — without the answer quality actually dropping.

This is an ORDERING invariant inside the (very large, not unit-testable) chat
worker, so the guard is a source-order assertion: in `handlers/chat.py`, the
`validate_citations_in_response(...)` call MUST appear after the reply-level
`deanonymize_text(...)` call. If anyone moves the validator block back above
the reverse pass, this test fails loudly.

Run: python3 -m unittest tests.test_citation_after_deanon -v
"""

from __future__ import annotations

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CHAT_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "handlers", "chat.py",
)


def _call_line_numbers(func_name: str, source: str) -> list[int]:
    """Line numbers of every `<...>.func_name(...)` OR bare `func_name(...)`
    call in `source`, via AST (comments/strings can't produce a false hit)."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name == func_name:
            hits.append(node.lineno)
    return sorted(hits)


def _reply_level_deanon_line(source: str) -> int:
    """Line of the REPLY-level reverse pass specifically — the one whose result
    is bound to `_deanon_reply` (`_deanon_reply, _restored = deanonymize_text(...)`).
    Distinct from the live streaming-delta reverses (which bind `full_denon`);
    those always precede the validator and would mask the ordering bug. This is
    the call the validator must run AFTER."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # Target is the tuple `(_deanon_reply, _restored)`.
        tgt = node.targets[0] if node.targets else None
        names = []
        if isinstance(tgt, ast.Tuple):
            names = [e.id for e in tgt.elts if isinstance(e, ast.Name)]
        if "_deanon_reply" not in names:
            continue
        val = node.value
        f = getattr(val, "func", None)
        fname = getattr(f, "attr", None) or getattr(f, "id", None)
        if isinstance(val, ast.Call) and fname == "deanonymize_text":
            return node.lineno
    return -1


class TestCitationAfterDeanon(unittest.TestCase):
    def setUp(self):
        with open(_CHAT_PY, "r", encoding="utf-8") as fh:
            self.src = fh.read()

    def test_validator_call_exists_exactly_once(self):
        # A relocation, not a duplication: exactly one live validator call.
        val = _call_line_numbers("validate_citations_in_response", self.src)
        self.assertEqual(
            len(val), 1,
            f"expected exactly one validate_citations_in_response call, "
            f"found {len(val)} at lines {val}",
        )

    def test_validator_runs_after_reply_deanonymise(self):
        val = _call_line_numbers("validate_citations_in_response", self.src)
        self.assertTrue(val, "no validate_citations_in_response call found")
        validator_line = val[0]

        reply_deanon_line = _reply_level_deanon_line(self.src)
        self.assertGreater(
            reply_deanon_line, 0,
            "could not find the reply-level `_deanon_reply, _restored = "
            "deanonymize_text(...)` call — the M8 invariant can't be checked",
        )

        # THE invariant: the validator runs on the reply AFTER it was reversed.
        # Compared against the reply-level reverse specifically (not the live
        # streaming-delta reverses, which always precede everything and would
        # mask a regression).
        self.assertGreater(
            validator_line, reply_deanon_line,
            f"validate_citations_in_response (line {validator_line}) runs "
            f"BEFORE the reply-level deanonymize_text (line {reply_deanon_line}) "
            f"— this is the pre-M8 G11 bug: the validator matches FAKE quotes "
            f"against the real source files and marks them all 'unverified'.",
        )


if __name__ == "__main__":
    unittest.main()
