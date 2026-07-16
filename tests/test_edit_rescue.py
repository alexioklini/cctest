"""edit_file rescue (v9.309.0) — tolerant matching for almost-right old_strings.

Tests the PURE helpers in engine/tools/file_tools.py directly (no brain import,
no server): typographic-normalization region finding, whole-line matching with
trailing-whitespace tolerance + uniform indent delta, and the line-region
splice that re-indents new_string by the matched delta. The ambiguity contract
(N>1 tolerant matches → caller must refuse) is covered via region counts.

Runs in the bare test interpreter — no server, no network.
"""

import unittest

from engine.tools.file_tools import (
    _edit_rescue_unicode,
    _edit_rescue_lines,
    _edit_apply_line_regions,
    _edit_rescue_anchors,
    _EDIT_RESCUE3_MIN_CHARS,
)


class TestUnicodeRescue(unittest.TestCase):
    def test_curly_quotes_and_dash_drift(self):
        content = 'print("Hello – world")\n'
        # Model wrote straight quotes + hyphen; file has curly/en-dash? Inverse:
        # file has typographic chars, old_string has ASCII.
        old = 'print("Hello - world")'
        regions = _edit_rescue_unicode(content, old)
        self.assertEqual(len(regions), 1)
        start, end = regions[0]
        self.assertEqual(content[start:end], 'print("Hello – world")')

    def test_both_sides_typographic(self):
        # Drift on BOTH sides (file nbsp, old_string curly quote) still matches
        # because both are normalized to the same canonical form.
        content = "x = 'a b'\n"
        old = "x = ‘a b’"
        regions = _edit_rescue_unicode(content, old)
        self.assertEqual(len(regions), 1)

    def test_no_match_stays_empty(self):
        self.assertEqual(_edit_rescue_unicode("abc def\n", "xyz-string"), [])

    def test_ambiguous_returns_all_regions(self):
        content = 'a = "x – y"\nb = "x – y"\n'
        regions = _edit_rescue_unicode(content, 'x - y')
        self.assertEqual(len(regions), 2)

    def test_splice_preserves_surroundings(self):
        content = 'keep1\nval = "a – b"\nkeep2\n'
        regions = _edit_rescue_unicode(content, 'val = "a - b"')
        self.assertEqual(len(regions), 1)
        start, end = regions[0]
        out = content[:start] + 'val = "NEW"' + content[end:]
        self.assertEqual(out, 'keep1\nval = "NEW"\nkeep2\n')


class TestLineRescue(unittest.TestCase):
    def test_trailing_whitespace_drift(self):
        content = "def f():   \n    return 1\t\n"
        old = "def f():\n    return 1"
        regions = _edit_rescue_lines(content, old)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0], (0, 2, 0))

    def test_uniform_indent_delta(self):
        content = "class A:\n        def f(self):\n            return 1\n"
        # Model wrote it 4 spaces shallower — uniform delta +4 must match.
        old = "    def f(self):\n        return 1"
        regions = _edit_rescue_lines(content, old)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0][2], 4)

    def test_non_uniform_indent_refused(self):
        content = "  a = 1\n      b = 2\n"
        old = "a = 1\nb = 2"   # deltas 2 and 6 — not uniform → no match
        self.assertEqual(_edit_rescue_lines(content, old), [])

    def test_content_difference_refused(self):
        content = "    a = 1\n"
        self.assertEqual(_edit_rescue_lines(content, "a = 2"), [])

    def test_apply_reindents_new_string(self):
        content = "class A:\n        def f(self):\n            return 1\nrest\n"
        old = "    def f(self):\n        return 1"
        regions = _edit_rescue_lines(content, old)
        out = _edit_apply_line_regions(
            content, regions, "    def f(self):\n        return 2")
        # new_string arrives at the FILE's real indentation (delta +4 applied)
        self.assertEqual(
            out, "class A:\n        def f(self):\n            return 2\nrest\n")

    def test_apply_negative_delta(self):
        content = "def f():\n    return 1\n"
        old = "    def f():\n        return 1"   # model over-indented by 4
        regions = _edit_rescue_lines(content, old)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0][2], -4)
        out = _edit_apply_line_regions(
            content, regions, "    def f():\n        return 2")
        self.assertEqual(out, "def f():\n    return 2\n")

    def test_ambiguous_returns_all(self):
        content = "    x = 1\n\n    x = 1\n"
        regions = _edit_rescue_lines(content, "x = 1")
        self.assertEqual(len(regions), 2)

    def test_blank_line_in_old_matches_blank_only(self):
        content = "a = 1\n\nb = 2\n"
        regions = _edit_rescue_lines(content, "a = 1\n\nb = 2")
        self.assertEqual(len(regions), 1)
        # non-blank where old expects blank → refuse
        self.assertEqual(_edit_rescue_lines("a = 1\nX\nb = 2\n", "a = 1\n\nb = 2"), [])


class TestAnchorRescue(unittest.TestCase):
    """Rescue 3 (v9.353.2): anchor-based replacement of LONG spans whose middle
    the model garbled — the design-mode hero-SVG case (chat 58e3c521): the
    old_string was a huge URL-encoded data-URI reproduced imperfectly, rescues
    1+2 can't apply (no typographic drift, single line), edit_file failed and
    the model escaped via a shell rewrite."""

    def _blob(self, seed: str, n: int = 400) -> str:
        # Deterministic pseudo-encoded blob (data-URI-ish, one line, no spaces).
        out = []
        x = 0
        for i in range(n):
            x = (x * 31 + ord(seed[i % len(seed)]) + i) % 4096
            out.append(f"%{x % 256:02X}")
        return "".join(out)

    def _doc(self, blob: str) -> str:
        return ("<html><body>\n"
                f'<div class="hero"><img src="data:image/svg+xml;utf8,{blob}"/></div>\n'
                "<p>rest of page</p>\n</body></html>\n")

    def test_garbled_middle_matches_real_span(self):
        real = self._doc(self._blob("real"))
        true_span_start = real.index('<div class="hero">')
        true_span_end = real.index("/></div>") + len("/></div>")
        true_span = real[true_span_start:true_span_end]
        # Model reproduces prefix+suffix correctly but garbles a middle chunk
        # (~10% of the span) — count()==0, rescues 1+2 miss, anchors hit.
        mid = len(true_span) // 2
        garbled = true_span[:mid] + "%FF%EE%DD%CC" + true_span[mid + 12:]
        self.assertNotIn(garbled, real)
        regions = _edit_rescue_anchors(real, garbled)
        self.assertEqual(len(regions), 1)
        start, end = regions[0]
        self.assertEqual(real[start:end], true_span)

    def test_short_old_string_never_anchors(self):
        content = "x" * 500
        self.assertGreater(_EDIT_RESCUE3_MIN_CHARS, 64)
        self.assertEqual(_edit_rescue_anchors(content, "x" * 100), [])

    def test_dissimilar_span_refused(self):
        # Anchors present but the span between them is UNRELATED content —
        # the similarity floor must refuse (never swallow foreign content).
        pre, suf = "A" * 40, "B" * 40
        old = pre + self._blob("wanted") + suf
        content = "keep\n" + pre + ("<p>totally different text</p>" * 40) + suf + "\nkeep"
        self.assertEqual(_edit_rescue_anchors(content, old), [])

    def test_span_length_window_refused(self):
        # Suffix anchor exists but only far beyond the ±35% window → refuse.
        pre, suf = "A" * 40, "B" * 40
        old = pre + self._blob("wanted", 200) + suf
        content = pre + self._blob("wanted", 200) + ("X" * len(old) * 2) + suf
        self.assertEqual(_edit_rescue_anchors(content, old), [])

    def test_ambiguous_two_prefix_occurrences(self):
        blob = self._blob("dup")
        pre_span = f'<img src="data:image/svg+xml;utf8,{blob}"/>'
        content = pre_span + "\nmiddle\n" + pre_span + "\n"
        mid = len(pre_span) // 2
        garbled = pre_span[:mid] + "%00%11" + pre_span[mid + 6:]
        regions = _edit_rescue_anchors(content, garbled)
        self.assertEqual(len(regions), 2)


if __name__ == "__main__":
    unittest.main()
