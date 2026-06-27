#!/usr/bin/env python3
"""
codegraph_arena.py — head-to-head eval of source-code indexing engines for
PRODUCTION code-mode (cctest-scale repos), to decide whether to replace the
in-tree CodeGraph with codebase-memory-mcp.

Three arms (see memory: project_codegraph_replacement_eval):
  - current     : in-tree engine/code_graph.py (name-based edges)
  - a_parallel  : codebase-memory-mcp binary, its OWN nomic-CPU semantic search
  - a_unified   : [Phase 2, not built yet] cbm structure + our MLX->Qdrant code search

Phase 1 = current vs a_parallel. We measure on the SAME gold set:
  - structural accuracy (callers/impact/imports) vs ground truth derived from repo
  - the AMBIGUOUS-name cases that expose type-resolution (the real differentiator)
  - semantic top-k recall (find code that does X, by description)
  - answer-payload TOKEN cost (the small-model-viability lever)
  - build time + per-query latency

Design notes:
  - Ground truth for structural Qs is derived by AST/grep over the repo and is
    spot-checkable; each gold item records how its truth was derived.
  - Per [[feedback_eval_single_run_noise]]: structural Qs are deterministic
    (1 run is fine); semantic/judged Qs would need >=3 reps — flagged per item.

Usage:
  python3 eval/codegraph_arena.py --build          # (re)build both engines on cctest
  python3 eval/codegraph_arena.py --run            # run gold set on both, print table
  python3 eval/codegraph_arena.py --run --arm current
  CBM_BIN=/path/to/binary python3 eval/codegraph_arena.py --run
"""
import argparse
import json
import os
import subprocess
import sys
import time

REPO = "/Users/alexander/Documents/dev/cctest"
SCRATCH = os.environ.get(
    "ARENA_SCRATCH",
    "/private/tmp/claude-501/-Users-alexander-Documents-dev-cctest/"
    "4e01ac63-6b3d-486f-a0ab-b3f88a30cdcb/scratchpad",
)
CBM_BIN = os.environ.get("CBM_BIN", os.path.join(SCRATCH, "cbm", "codebase-memory-mcp"))
CBM_CACHE = os.environ.get("CBM_CACHE_DIR", os.path.join(SCRATCH, "cbm-cache"))
CBM_PROJECT = "Users-alexander-Documents-dev-cctest"
CURRENT_DB = os.path.join(SCRATCH, "cctest-current-cg.db")


# ----------------------------------------------------------------------------
# token estimate — rough, char/4; what matters is RELATIVE payload size per arm
# ----------------------------------------------------------------------------
def toklen(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return max(1, len(s) // 4)


# ----------------------------------------------------------------------------
# Adapter: codebase-memory-mcp (a_parallel)
# ----------------------------------------------------------------------------
class CbmAdapter:
    name = "a_parallel"

    def _cli(self, tool, payload):
        payload = dict(payload, project=CBM_PROJECT)
        env = dict(os.environ, CBM_CACHE_DIR=CBM_CACHE)
        t0 = time.time()
        p = subprocess.run(
            [CBM_BIN, "cli", tool, json.dumps(payload)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        dt = time.time() - t0
        # CLI prints a level=info line then the JSON object; take last JSON line
        out = None
        for line in p.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out = json.loads(line)
                except Exception:
                    pass
        return out or {}, dt

    def build(self):
        env = dict(os.environ, CBM_CACHE_DIR=CBM_CACHE)
        t0 = time.time()
        subprocess.run(
            [CBM_BIN, "cli", "index_repository", json.dumps({"repo_path": REPO})],
            capture_output=True, text=True, env=env, timeout=600,
        )
        return time.time() - t0

    def callers(self, fn):
        d, dt = self._cli("trace_path", {
            "function_name": fn, "direction": "inbound", "mode": "calls", "depth": 1,
        })
        names = [c.get("name") for c in d.get("callers", [])]
        return names, d, dt

    def callers_qualified(self, qualified):
        # qualified like "ClassName.method" — cbm matches short name; we filter by qn
        short = qualified.split(".")[-1]
        d, dt = self._cli("trace_path", {
            "function_name": qualified, "direction": "inbound", "mode": "calls", "depth": 1,
        })
        if not d.get("callers"):
            d, dt = self._cli("trace_path", {
                "function_name": short, "direction": "inbound", "mode": "calls", "depth": 1,
            })
        names = [c.get("name") for c in d.get("callers", [])]
        return names, d, dt

    def semantic(self, keywords, limit=8):
        # NOTE: embedding results land in 'semantic_results', NOT 'results'
        # (results = BM25/structural). per-keyword min-cosine, conjunctive over
        # ALL keywords — diverse keyword sets score low; tight sets score high.
        d, dt = self._cli("search_graph", {"semantic_query": keywords, "limit": limit})
        nodes = d.get("semantic_results", [])
        files = [n.get("file_path") for n in nodes]
        return files, {"semantic_results": nodes}, dt

    def bm25(self, text, limit=8):
        # BM25 full-text with structural boosting — cbm's strongest discovery mode
        d, dt = self._cli("search_graph", {"query": text, "label": "Function", "limit": limit})
        nodes = d.get("results", d.get("nodes", []))
        files = [n.get("file_path") for n in nodes]
        return files, {"results": nodes}, dt


# ----------------------------------------------------------------------------
# Adapter: in-tree CodeGraph (current)
# ----------------------------------------------------------------------------
class CurrentAdapter:
    name = "current"

    def __init__(self):
        sys.path.insert(0, REPO)
        os.chdir(REPO)
        import engine.code_graph as cg
        import engine.context as ctx_mod
        self.cg = cg
        self.ctx_mod = ctx_mod

    def _g(self):
        return self.cg._get_code_graph()

    def build(self):
        with self.ctx_mod.request_context(code_graph_db=CURRENT_DB):
            t0 = time.time()
            self._g().build(REPO, incremental=False)
            return time.time() - t0

    def callers(self, fn):
        with self.ctx_mod.request_context(code_graph_db=CURRENT_DB):
            t0 = time.time()
            res = self._g().query("callers_of", fn, 50)
            dt = time.time() - t0
        # exclude tests to match cbm default; keep short caller name
        names = []
        for r in res:
            if r.get("kind") == "test":
                continue
            names.append(r.get("caller", "").split("::")[-1].split(".")[-1])
        return names, res, dt

    def callers_qualified(self, qualified):
        # current graph keys by file::Class.method — query by short name, then
        # we cannot disambiguate by class (that's the point of the test)
        short = qualified.split(".")[-1]
        with self.ctx_mod.request_context(code_graph_db=CURRENT_DB):
            t0 = time.time()
            res = self._g().query("callers_of", short, 50)
            dt = time.time() - t0
        names = [r.get("caller", "").split("::")[-1].split(".")[-1]
                 for r in res if r.get("kind") != "test"]
        return names, res, dt

    def semantic(self, keywords, limit=8):
        # current graph has NO semantic search — this is a known capability gap.
        return [], {"note": "current CodeGraph has no semantic search"}, 0.0


# ----------------------------------------------------------------------------
# GOLD SET — ground truth derived from repo (see derive_truth notes per item)
# ----------------------------------------------------------------------------
GOLD = [
    {
        "id": "callers_resolve_active_tools",
        "type": "callers",
        "target": "resolve_active_tools",
        "deterministic": True,
        # truth: grep "resolve_active_tools(" minus def, minus tests, minus CHANGELOG string
        "expected": {"_build_tool_list", "_handle_refine", "_execute_scheduled",
                     "get_tool_breakdown", "tool_purpose_matrix", "build_first_turn_prefix"},
        "note": "unique name — both engines expected to pass; baseline sanity",
    },
    {
        "id": "callers_ambiguous_supervisor_start",
        "type": "callers_qualified",
        "target": "ProcessSupervisor.start",
        "deterministic": True,
        # AMBIGUOUS via INHERITANCE: start() defined ONCE on ProcessSupervisor
        # (sidecar_supervisor.py:142); Sidecar/Searxng/Crawl4ai inherit it.
        # The 3 call sites (server.py ~4253/4260/4268) call it on instance vars
        # sidecar_supervisor/searxng_supervisor/crawl4ai_supervisor. A graph that
        # resolves instance types finds all 3; a pure name-graph finds them too
        # but ALSO conflates with the ~10 OTHER unrelated start() methods.
        # Truth: enclosing function in server.py that calls *.start() x3.
        "expected": None,  # manual scoring — see note; both echo, compare FP rate
        "note": "AMBIGUOUS via inheritance — tests whether the engine conflates "
                "this start() with the ~10 unrelated start() methods. Score by "
                "FALSE-POSITIVE rate, not just recall.",
    },
    {
        "id": "semantic_warmup",
        "type": "semantic",
        "keywords": ["warmup", "warm", "model", "prime", "prefix", "KV"],
        "deterministic": False,  # judged / recall — needs >=3 reps
        "expected_files": {"brain.py", "engine/provider.py"},
        "note": "fuzzy 'find code that warms up models'. current has NO semantic "
                "search (capability gap). reps>=3 for cbm.",
    },
    {
        "id": "semantic_pii_scan",
        "type": "semantic",
        "keywords": ["PII", "GDPR", "anonymise", "detect", "personal", "scan"],
        "deterministic": False,
        "expected_files": {"engine/pii_ner.py", "brain.py"},
        "note": "fuzzy 'find the PII/GDPR scanner'.",
    },
]


def run_item(adapter, item):
    t = item["type"]
    if t == "callers":
        got, raw, dt = adapter.callers(item["target"])
        got_set = set(got)
        exp = item["expected"]
        hits = got_set & exp
        score = len(hits) / len(exp) if exp else 0.0
        extra = len(got_set - exp)  # false positives (tests excluded already)
        return {"score": round(score, 3), "found": len(got_set), "hits": len(hits),
                "missing": sorted(exp - got_set), "fp": extra,
                "latency": round(dt, 3), "tokens": toklen(raw)}
    if t == "callers_qualified":
        got, raw, dt = adapter.callers_qualified(item["target"])
        return {"found": len(set(got)), "callers": sorted(set(got)),
                "latency": round(dt, 3), "tokens": toklen(raw),
                "note": "manual scoring vs derived truth (ambiguous)"}
    if t == "semantic":
        files, raw, dt = adapter.semantic(item["keywords"])
        topk = [os.path.relpath(f, REPO) if f and f.startswith("/") else f
                for f in files if f]
        exp = item["expected_files"]
        recall_hit = any(any(e in (f or "") for e in exp) for f in topk)
        return {"recall_hit": recall_hit, "topk": topk[:8],
                "latency": round(dt, 3), "tokens": toklen(raw)}
    return {"error": "unknown type"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--arm", choices=["current", "a_parallel", "both"], default="both")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # stream progress live (see feedback_evals_line_buffered)

    arms = []
    if args.arm in ("current", "both"):
        arms.append(CurrentAdapter())
    if args.arm in ("a_parallel", "both"):
        arms.append(CbmAdapter())

    if args.build:
        for a in arms:
            dt = a.build()
            print(f"[build] {a.name}: {dt:.1f}s")

    if args.run:
        for a in arms:
            print(f"\n========== ARM: {a.name} ==========")
            for item in GOLD:
                res = run_item(a, item)
                print(f"  {item['id']:38s} {json.dumps(res, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
