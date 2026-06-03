"""Per-wing collection name mapping + flag gating (engine/wing_collections).

This is the foundation of MemPalace fault isolation: each wing → its own Chroma
collection. The mapping MUST be:
  - chroma-legal (3-512 chars, [A-Za-z0-9._-], starts+ends alphanumeric),
  - injective (distinct wings NEVER collide — else one wing's delete hits
    another's index, defeating the whole point),
  - deterministic (same wing → same name across boots),
  - flag-gated (OFF → the legacy shared name, byte-identical to today).

Run: python3 -m unittest tests.test_wing_collections
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.wing_collections as wc  # noqa: E402

# Real wing forms seen in production (from the live palace + the wing scheme).
REAL_WINGS = [
    "brain_code",
    "main_artifacts",
    "project__45e2a66dc68d",
    "project__f201b24ff6a2",
    "project_chat__f201b24ff6a2",
    "user__17368b7961d3",
    "user__alex@me.com",          # email-id user → '@' is illegal in chroma names
    "team__acme-eng",
    "default",
]

# A chroma collection name: 3-512 chars, only [A-Za-z0-9._-], start+end alnum.
_CHROMA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,510}[A-Za-z0-9]$")


class TestNameMapping(unittest.TestCase):
    def test_all_real_wings_produce_legal_names(self):
        for w in REAL_WINGS:
            d, c = wc.wing_to_collection(w)
            self.assertRegex(d, _CHROMA_NAME, f"illegal drawers name for {w!r}: {d!r}")
            self.assertRegex(c, _CHROMA_NAME, f"illegal closets name for {w!r}: {c!r}")

    def test_drawers_and_closets_differ(self):
        for w in REAL_WINGS:
            d, c = wc.wing_to_collection(w)
            self.assertNotEqual(d, c)

    def test_injective_no_collisions(self):
        # The critical property: distinct wings → distinct collections.
        seen = {}
        wings = REAL_WINGS + [
            "", "   ", "@@@", "a", "ab",                      # degenerate
            "user__a", "user_-a", "user__a ",                 # near-misses
            "x" * 600, "x" * 600 + "y",                       # over-length, differ in tail
            "Project__ABC", "project__abc",                   # case-sensitive distinct
        ]
        for w in wings:
            d, _ = wc.wing_to_collection(w)
            self.assertNotIn(d, seen,
                             f"COLLISION: {w!r} and {seen.get(d)!r} both → {d!r}")
            seen[d] = w

    def test_deterministic(self):
        for w in REAL_WINGS:
            self.assertEqual(wc.wing_to_collection(w), wc.wing_to_collection(w))

    def test_legal_wing_kept_verbatim_no_hash(self):
        # An already-legal wing should map to a readable prefixed name with NO
        # hash suffix (keeps on-disk collections human-readable in the common case).
        d, c = wc.wing_to_collection("project__f201b24ff6a2")
        self.assertEqual(d, "wd_project__f201b24ff6a2")
        self.assertEqual(c, "wc_project__f201b24ff6a2")

    def test_illegal_wing_gets_hash(self):
        # The '@' is lossy → a hash suffix must be appended for injectivity.
        d, _ = wc.wing_to_collection("user__alex@me.com")
        self.assertTrue(d.startswith("wd_user__alex-me.com_"), d)
        self.assertRegex(d, r"_[0-9a-f]{12}$")

    def test_empty_wing_never_bare(self):
        d, c = wc.wing_to_collection("")
        self.assertRegex(d, _CHROMA_NAME)
        self.assertRegex(c, _CHROMA_NAME)
        # two different degenerate wings still differ
        self.assertNotEqual(wc.wing_to_collection("")[0],
                            wc.wing_to_collection("   ")[0])

    def test_long_wing_within_chroma_limit(self):
        d, c = wc.wing_to_collection("project__" + "z" * 5000)
        self.assertLessEqual(len(d), 512)
        self.assertLessEqual(len(c), 512)
        self.assertRegex(d, _CHROMA_NAME)


class TestAlwaysPerWing(unittest.TestCase):
    """Per-wing is ALWAYS on — there is no flag and no shared-collection fallback
    at runtime. `collection_names_for` always returns the per-wing name."""

    def test_names_are_always_per_wing(self):
        self.assertEqual(wc.collection_names_for("project__f201b24ff6a2", kind="drawers"),
                         "wd_project__f201b24ff6a2")
        self.assertEqual(wc.collection_names_for("project__f201b24ff6a2", kind="closets"),
                         "wc_project__f201b24ff6a2")

    def test_distinct_wings_distinct_collections(self):
        a = wc.collection_names_for("project__aaa", kind="drawers")
        b = wc.collection_names_for("project__bbb", kind="drawers")
        self.assertNotEqual(a, b)

    def test_never_returns_legacy_shared_name(self):
        # The whole point: no wing ever resolves to the old shared collection.
        for w in REAL_WINGS:
            self.assertNotEqual(wc.collection_names_for(w, kind="drawers"), wc.LEGACY_DRAWERS)
            self.assertNotEqual(wc.collection_names_for(w, kind="closets"), wc.LEGACY_CLOSETS)

    def test_bad_kind_rejected(self):
        with self.assertRaises(ValueError):
            wc.collection_names_for("project__abc", kind="bogus")


class TestMinerPatchGuard(unittest.TestCase):
    """The vendored miner patch is REQUIRED (no fallback). Its absence must fail
    LOUD at startup via assert_miner_patch, never silently mis-route."""

    def setUp(self):
        self._saved = dict(wc._PATCH_CHECK)

    def tearDown(self):
        wc._PATCH_CHECK.clear()
        wc._PATCH_CHECK.update(self._saved)

    def test_assert_raises_when_patch_absent(self):
        wc._PATCH_CHECK.update(done=True, ok=False)   # simulate wiped patch
        with self.assertRaises(RuntimeError):
            wc.assert_miner_patch()

    def test_assert_passes_when_patch_present(self):
        wc._PATCH_CHECK.update(done=True, ok=True)
        wc.assert_miner_patch()  # must not raise


if __name__ == "__main__":
    unittest.main()
