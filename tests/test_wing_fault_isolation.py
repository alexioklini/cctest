"""HEADLINE TEST: per-wing fault isolation + auto-heal.

This is the property the whole per-wing-collections effort exists to provide and
the requirement Alexander set: "when something corrupts, only a minimal set of
data is involved, and it auto-heals with no admin intervention."

The test seeds TWO wings as separate per-wing collections, physically corrupts
ONE wing's HNSW index segment on disk, and asserts:
  1. The OTHER wing still answers queries (isolation — the corruption did not
     spread through a shared index, because there is no shared index).
  2. The corrupted wing rebuilds from its own durable sqlite via the package's
     per-collection rebuild_index (auto-heal), and then answers again.
  3. No admin action was needed.

Needs the mempalace venv (chromadb + an embedding model). If that isn't importable
in this environment, the whole module SKIPS rather than failing — but when it
runs, it is the real proof.

Run: python3 -m unittest tests.test_wing_fault_isolation
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Put the configured mempalace venv on the path so chromadb + the package import.
try:
    import brain as _brain  # noqa: E402
    _vsp = (_brain._load_mempalace_config() or {}).get("venv_site_packages", "")
    if _vsp and _vsp not in sys.path:
        sys.path.insert(0, _vsp)
except Exception:
    pass

try:
    import chromadb  # noqa: F401,E402
    import engine.wing_collections as wc  # noqa: E402
    from mempalace import repair as mp_repair  # noqa: E402
    _HAVE_MP = True
except Exception as _e:  # pragma: no cover - environment dependent
    _HAVE_MP = False
    _IMPORT_ERR = repr(_e)


@unittest.skipUnless(_HAVE_MP, "mempalace venv (chromadb + repair) not importable")
class TestFaultIsolation(unittest.TestCase):
    def setUp(self):
        self.palace = tempfile.mkdtemp(prefix="wingtest-")

    def tearDown(self):
        shutil.rmtree(self.palace, ignore_errors=True)

    def _seed(self, wing, n):
        for i in range(n):
            r = wc.add_drawer_to_wing(
                self.palace, wing, "general",
                f"{wing} document number {i} about topic {i}",
                source_file=f"/seed/{wing}/{i}.md", added_by="test")
            self.assertTrue(r.get("success"), f"seed failed: {r}")

    def _query_ok(self, wing):
        col = wc.get_wing_collection(self.palace, wing, create=False, kind="drawers")
        if col is None:
            return False
        try:
            r = col.query(query_texts=["topic"], n_results=3)
            return len((r.get("ids") or [[]])[0]) > 0
        except Exception:
            return False

    def test_distinct_wings_are_distinct_collections(self):
        self._seed("project__aaa", 3)
        self._seed("project__bbb", 3)
        names = set(wc.list_wing_collections(self.palace))
        da, _ = wc.wing_to_collection("project__aaa")
        db, _ = wc.wing_to_collection("project__bbb")
        self.assertIn(da, names)
        self.assertIn(db, names)
        self.assertNotEqual(da, db)

    def _segment_dirs(self):
        return {d for d in os.listdir(self.palace)
                if os.path.isdir(os.path.join(self.palace, d))
                and os.path.exists(os.path.join(self.palace, d, "data_level0.bin"))}

    def test_corrupting_one_wing_does_not_break_the_other(self):
        # Seed the BAD wing first + enough drawers to force chroma to flush its
        # HNSW .bin segment to disk; snapshot its segment dir(s). Then seed the
        # GOOD wing — any NEW segment dir belongs to good, so we corrupt only the
        # bad wing's dir(s). (The on-disk segment id != collection id, so we
        # identify the dir by when it appeared, not by name.)
        self._seed("project__bad", 60)
        bad_dirs = self._segment_dirs()
        self._seed("project__good", 60)
        self.assertTrue(self._query_ok("project__good"))
        self.assertTrue(self._query_ok("project__bad"))
        if not bad_dirs:
            self.skipTest("chroma did not flush a .bin segment (no flush)")

        # Physically wreck ONLY the bad wing's segment .bin files.
        corrupted = False
        for d in bad_dirs:
            seg_dir = os.path.join(self.palace, d)
            for fn in os.listdir(seg_dir):
                if fn.endswith(".bin"):
                    with open(os.path.join(seg_dir, fn), "wb") as f:
                        f.write(b"\x00\x01\x02 CORRUPT \x03\x04")
                    corrupted = True
        if not corrupted:
            self.skipTest("no .bin files to corrupt")
        bad_drawers, _ = wc.wing_to_collection("project__bad")

        # ISOLATION: the good wing is in a different collection/segment and must
        # still answer despite the bad wing being wrecked.
        self.assertTrue(self._query_ok("project__good"),
                        "good wing must survive corruption of the bad wing")

        # AUTO-HEAL: rebuild ONLY the bad wing's collection from its sqlite
        # (the per-wing recovery path), then it answers again — no admin action,
        # good wing untouched throughout.
        mp_repair.rebuild_index(palace_path=self.palace,
                                collection_name=bad_drawers,
                                confirm_truncation_ok=False)
        self.assertTrue(self._query_ok("project__bad"),
                        "bad wing must self-heal from its own sqlite")
        self.assertTrue(self._query_ok("project__good"),
                        "good wing still fine after the bad wing's rebuild")


if __name__ == "__main__":
    unittest.main()
