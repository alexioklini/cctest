"""Auto-route benchmark ranking + deterministic benchmark scoring.

Two behaviors guarded here, both regressions we actually hit:

1. SPEED IS BUCKETED before it ranks (brain._tps_bucket / _bench_rank_key).
   The bug: chat 641f89ef routed a trivial "fast" turn to mistral-medium over
   mistral-small because Medium's measured throughput was 11.5 tok/s vs Small's
   11.2 — a 0.3-tok/s (noise-sized, n=2) difference that PREEMPTED the cost axis
   where Small is ~20× cheaper. Intent: two capability-tied cloud models whose
   speeds are within one bucket must tie on speed and let COST decide; a model
   that is genuinely (>~15%) faster still wins on speed.

2. DETERMINISTIC SCORING (engine.model_bench._deterministic_score): prompts with
   an objective answer are graded 0/100 by code, not the LLM judge — so the
   capability score actually reflects correctness (a wrong dedupe that loses
   order scores 0), which is what makes the capability FLOOR discriminate.

Run: python3 -m unittest tests.test_auto_route_ranking
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import engine.model_bench as mb  # noqa: E402


class TestTpsBucket(unittest.TestCase):
    def test_noise_sized_delta_same_bucket(self):
        # 11.2 vs 11.5 tok/s (the real chat-641f89ef numbers) → same band.
        self.assertEqual(brain._tps_bucket(11.2), brain._tps_bucket(11.5))

    def test_genuine_speedup_different_bucket(self):
        # ~1.7× faster (107.8 → 189.1, the real math-cell numbers) → faster band.
        self.assertGreater(brain._tps_bucket(189.1), brain._tps_bucket(107.8))

    def test_zero_and_negative_safe(self):
        self.assertEqual(brain._tps_bucket(0), 0)
        self.assertEqual(brain._tps_bucket(-5), 0)

    def test_monotonic(self):
        prev = -1
        for tps in (1, 5, 11, 12, 20, 50, 100, 200):
            b = brain._tps_bucket(tps)
            self.assertGreaterEqual(b, prev)
            prev = b


class TestRankKeyTiebreak(unittest.TestCase):
    """The rank key is (capable?, local?, -tps_bucket, cost, prio). With speed
    bucketed, a within-bucket speed tie must fall through to cost."""

    def _key(self, cap, local, tps, cost, prio):
        # Build a key matching brain._bench_rank_key's tuple shape directly,
        # so the test is independent of config.json contents.
        has_cap = cap >= 30
        return (0 if has_cap else 1, 1 if local else 0,
                -brain._tps_bucket(tps), cost, -prio)

    def test_cheap_wins_when_speed_ties(self):
        # Both cloud, both capable, speeds within a bucket (11.2 vs 11.5):
        # the cheaper one must sort first.
        medium = self._key(100, False, 11.5, 9.0, 60)   # 1.5+7.5
        small = self._key(100, False, 11.2, 0.4, 45)    # 0.1+0.3
        self.assertLess(small, medium, "cheap model must win a within-bucket speed tie")

    def test_genuinely_faster_wins_over_cheaper(self):
        # If the pricier model is meaningfully faster (different bucket), speed
        # leads cost (the user's stated capable→fast→cheap order).
        fast_pricey = self._key(100, False, 200.0, 9.0, 60)
        slow_cheap = self._key(100, False, 11.0, 0.4, 45)
        self.assertLess(fast_pricey, slow_cheap)

    def test_cloud_beats_local_even_if_local_faster(self):
        cloud = self._key(100, False, 11.0, 9.0, 60)
        local_fast = self._key(100, True, 160.0, 0.0, 45)
        self.assertLess(cloud, local_fast, "no local model outranks a capable cloud model")


class TestDeterministicScoring(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(mb._deterministic_score({"type": "exact", "answer": "canberra"}, "Canberra."), 100)
        self.assertEqual(mb._deterministic_score({"type": "exact", "answer": "canberra"}, "Sydney"), 0)

    def test_regex(self):
        self.assertEqual(mb._deterministic_score({"type": "regex", "pattern": r"\b391\b"}, "= 391 total"), 100)
        self.assertEqual(mb._deterministic_score({"type": "regex", "pattern": r"\b391\b"}, "390"), 0)

    def test_pyfunc_correct_vs_wrong(self):
        good = ("def dedupe_stable(xs):\n seen=set();o=[]\n for x in xs:\n"
                "  if x not in seen:seen.add(x);o.append(x)\n return o")
        bad = "def dedupe_stable(xs):\n return list(set(xs))"  # loses order
        cases = [[[[3, 1, 3, 2]], [3, 1, 2]]]
        self.assertEqual(mb._deterministic_score(
            {"type": "pyfunc", "name": "dedupe_stable", "cases": cases}, good), 100)
        self.assertEqual(mb._deterministic_score(
            {"type": "pyfunc", "name": "dedupe_stable", "cases": cases}, bad), 0)

    def test_pyfunc_rejects_io(self):
        # A model that ignores the prompt and emits import/open fails the check.
        self.assertEqual(mb._deterministic_score(
            {"type": "pyfunc", "name": "f", "cases": [[[1], 1]]},
            "import os\ndef f(x): return x"), 0)

    def test_pyfunc_strips_fences(self):
        fenced = "```python\ndef f(x):\n return x*2\n```"
        self.assertEqual(mb._deterministic_score(
            {"type": "pyfunc", "name": "f", "cases": [[[3], 6]]}, fenced), 100)

    def test_open_ended_returns_none(self):
        # check=None / unknown type → caller falls through to the LLM judge.
        self.assertIsNone(mb._deterministic_score(None, "anything"))
        self.assertIsNone(mb._deterministic_score({"type": "mystery"}, "x"))

    def test_bench_tasks_pyfunc_cells_have_passing_reference(self):
        """Every pyfunc cell must be satisfiable — a correct implementation
        passes all its cases (guards against malformed case tuples like the
        unwrapped-args bug)."""
        refs = {
            "dedupe_stable": ("def dedupe_stable(xs):\n s=set();o=[]\n for x in xs:\n"
                              "  if x not in s:s.add(x);o.append(x)\n return o"),
            "binary_search": ("def binary_search(a,x):\n lo,hi=0,len(a)-1\n"
                              " while lo<=hi:\n  m=(lo+hi)//2\n  if a[m]==x:return m\n"
                              "  if a[m]<x:lo=m+1\n  else:hi=m-1\n return -1"),
            "roman": ("def roman(n):\n v=[(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),"
                      "(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),"
                      "(5,'V'),(4,'IV'),(1,'I')]\n r=''\n for val,s in v:\n"
                      "  while n>=val:r+=s;n-=val\n return r"),
        }
        for task, items in mb.BENCH_TASKS.items():
            for it in items:
                ch = it.get("check")
                if ch and ch.get("type") == "pyfunc":
                    name = ch["name"]
                    self.assertIn(name, refs, f"no reference impl for {name}")
                    self.assertTrue(
                        mb._run_pyfunc_check(refs[name], name, ch["cases"]),
                        f"reference impl for {name} failed its own benchmark cases")


if __name__ == "__main__":
    unittest.main()
