#!/usr/bin/env python3
"""
codegraph_e2e.py — AGENTIC end-to-end arm of the codegraph replacement eval.

The model DRIVES the retrieval engine itself (multi-round tool use), like real
code mode, then answers a concrete cctest coding task. We measure whether it
lands the right file:symbol and how much it spent getting there.

Mechanism (proven by eval/fanout_probe.py): POST to the SIDECAR /turn with
max_rounds>1, tools=[...], and tool_endpoint pointing at a LOCAL HTTP stub.
Unlike fanout_probe, our stub EXECUTES the query for real against the chosen
engine and returns real results — so the model gets a genuine agentic loop
without touching production brain.py / TOOL_DISPATCH.

Engines (arms):
  - current    : in-tree CodeGraph, exposed as tools code_graph_query/impact
  - a_parallel : codebase-memory-mcp CLI, exposed as search_graph/trace_path/query_graph

Models (production code models ONLY — NOT M4-7B which is background-only):
  - mistral-small-latest   (cloud, CLIProxyAPI)
  - mistral-medium-3.5     (cloud, CLIProxyAPI)
  - Ornith-1.0-35B-MXFP4   (local oMLX; REASONING model — emits thinking; give big max_tokens)

Per [[feedback_eval_single_run_noise]]: judged scoring needs >=3 reps; we run
REPS and report per-rep landed/not + tool-call counts + context tokens.

Usage:
  python3 eval/codegraph_e2e.py --engine a_parallel --model mistral-small-latest
  python3 eval/codegraph_e2e.py --all          # full matrix (slow, esp. Ornith)
"""
import argparse
import json
import os
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "/Users/alexander/Documents/dev/cctest"
SIDECAR = "http://127.0.0.1:8421"
CONFIG = os.path.join(REPO, "config.json")
SCRATCH = os.environ.get(
    "ARENA_SCRATCH",
    "/private/tmp/claude-501/-Users-alexander-Documents-dev-cctest/"
    "4e01ac63-6b3d-486f-a0ab-b3f88a30cdcb/scratchpad",
)
CBM_BIN = os.path.join(SCRATCH, "cbm", "codebase-memory-mcp")
CBM_CACHE = os.path.join(SCRATCH, "cbm-cache")
CBM_PROJECT = "Users-alexander-Documents-dev-cctest"
CURRENT_DB = os.path.join(SCRATCH, "cctest-current-cg.db")

MODELS = {
    "mistral-small-latest":   ("CLIProxyAPI",),
    "mistral-medium-3.5":     ("CLIProxyAPI",),
    "Ornith-1.0-35B-MXFP4":   ("Lokal",),
    "gemma-4-12B-it-qat-4bit": ("Lokal",),
}
REASONING_MODELS = {"Ornith-1.0-35B-MXFP4"}


def creds(model):
    c = json.load(open(CONFIG))
    prov = c["models"][model]["provider"]
    p = c["providers"][prov]
    bu = (p.get("base_url") or "").rstrip("/")
    if bu.endswith("/v1"):
        bu = bu[:-3]
    return p.get("api_key") or "", bu


# ---------------------------------------------------------------------------
# Real query execution per engine — called by the stub on each tool_use
# ---------------------------------------------------------------------------
def exec_cbm(name, args):
    payload = dict(args, project=CBM_PROJECT)
    env = dict(os.environ, CBM_CACHE_DIR=CBM_CACHE)
    try:
        p = subprocess.run([CBM_BIN, "cli", name, json.dumps(payload)],
                           capture_output=True, text=True, env=env, timeout=60)
        for line in reversed(p.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return line  # already JSON string
        return json.dumps({"error": "no json", "stderr": p.stderr[:200]})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


_CG = {"loaded": False}


def exec_current(name, args):
    if not _CG["loaded"]:
        import sys
        sys.path.insert(0, REPO)
        os.chdir(REPO)
        import engine.code_graph as cg
        import engine.context as ctx_mod
        _CG["cg"], _CG["ctx"] = cg, ctx_mod
        _CG["loaded"] = True
    cg, ctx_mod = _CG["cg"], _CG["ctx"]
    with ctx_mod.request_context(code_graph_db=CURRENT_DB):
        g = cg._get_code_graph()
        try:
            if name == "code_graph_query":
                res = g.query(args.get("query_type", ""), args.get("target", ""),
                              args.get("limit", 20))
                return json.dumps({"results": res, "count": len(res)})
            if name == "code_graph_impact":
                return json.dumps(g.impact_analysis(args.get("files", []),
                                                    depth=args.get("depth", 2)))
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
    return json.dumps({"error": f"unknown tool {name}"})


# ---------------------------------------------------------------------------
# Tool schemas exposed to the model, per engine
# ---------------------------------------------------------------------------
CBM_TOOLS = [
    {"name": "search_graph", "description":
     "Search the code graph. query=BM25 natural-language; name_pattern=regex; "
     "semantic_query=ARRAY of keywords (embedding search, results in semantic_results); "
     "label filters (Function/Method/Class). Always pass project.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "name_pattern": {"type": "string"},
         "semantic_query": {"type": "array", "items": {"type": "string"}},
         "label": {"type": "string"}, "limit": {"type": "integer"}}, "required": []}},
    {"name": "trace_path", "description":
     "Trace callers/callees. function_name + direction(inbound/outbound/both) + "
     "mode(calls/data_flow). inbound=callers.",
     "input_schema": {"type": "object", "properties": {
         "function_name": {"type": "string"}, "direction": {"type": "string"},
         "mode": {"type": "string"}, "depth": {"type": "integer"}},
         "required": ["function_name"]}},
    {"name": "query_graph", "description": "Cypher (read-only) over the graph. e.g. "
     "MATCH (c)-[:CALLS]->(f:Method) WHERE f.name='x' RETURN c.name, c.file_path",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]}},
]

CURRENT_TOOLS = [
    {"name": "code_graph_query", "description":
     "Query the code graph. query_type one of callers_of/callees_of/imports_of/"
     "importers_of/tests_for/inheritors_of/children_of/file_summary; target=name.",
     "input_schema": {"type": "object", "properties": {
         "query_type": {"type": "string"}, "target": {"type": "string"},
         "limit": {"type": "integer"}}, "required": ["query_type", "target"]}},
    {"name": "code_graph_impact", "description":
     "Blast-radius: given changed files, find affected symbols.",
     "input_schema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}},
         "depth": {"type": "integer"}}, "required": ["files"]}},
]

ENGINES = {
    "a_parallel": (CBM_TOOLS, exec_cbm),
    "current": (CURRENT_TOOLS, exec_current),
}


# ---------------------------------------------------------------------------
# Stub that executes real queries
# ---------------------------------------------------------------------------
class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.result_chars = 0
        self.exec = None


_S = _State()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        name = body.get("name", "")
        args = body.get("args", {}) or {}
        result = _S.exec(name, args)
        with _S.lock:
            _S.calls.append({"name": name, "args": args})
            _S.result_chars += len(result)
        out = json.dumps({"result": result, "is_error": False, "elapsed_ms": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def start_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


# ---------------------------------------------------------------------------
# Gold tasks — concrete cctest coding asks; expected = file + symbol(s) the
# model should identify. Ground truth spot-checked from repo.
# ---------------------------------------------------------------------------
TASKS = [
    {
        "id": "callers_resolve_active_tools",
        "user": "In this codebase, I want to add a new place that calls "
                "resolve_active_tools(). List every existing function that already "
                "calls it (just the function names + files), so I can model my new "
                "call on them. Use the code graph tools, don't guess.",
        "expect_any": ["sidecar_proxy", "scheduler", "admin_artifacts", "build_first_turn_prefix"],
        "expect_all_hint": "6 callers across handlers/engine/brain",
    },
    {
        "id": "find_pii_scanner",
        "user": "Where is the PII / GDPR detection logic implemented in this "
                "codebase? Find the module and the main scanning function. Use the "
                "code graph search tools.",
        "expect_any": ["pii_ner", "classification", "anonymise", "pii_scan"],
        "expect_all_hint": "engine/pii_ner.py",
    },
    {
        "id": "find_warmup",
        "user": "Find the code that decides whether a model's warm-up KV prefix is "
                "still valid (warm-pool prefix logic). Name the function and file. "
                "Use the code graph tools.",
        "expect_any": ["prefix_is_warm", "warm", "build_first_turn_prefix", "provider"],
        "expect_all_hint": "brain.py prefix_is_warm / engine/provider.py",
    },
]

SYSTEM = ("You are a coding assistant working in a large Python/JS codebase via a "
          "code knowledge graph. You have graph tools — USE THEM (multiple rounds "
          "if needed) instead of guessing. When you have the answer, state the "
          "file paths and symbol names plainly.")


def landed(task, text):
    low = (text or "").lower()
    return any(k.lower() in low for k in task["expect_any"])


def run_one(engine, model, task, reps=3, max_tokens=2000):
    tools, ex = ENGINES[engine]
    _S.exec = ex
    api, base = creds(model)
    if model in REASONING_MODELS:
        max_tokens = max(max_tokens, 4000)
    results = []
    for rep in range(reps):
        with _S.lock:
            _S.calls = []
            _S.result_chars = 0
        payload = {
            "model": model, "base_url": base, "api_key": api,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": task["user"]}],
            "tools": tools, "max_tokens": max_tokens, "max_rounds": 5,
            "tool_endpoint": _S.stub_url, "tool_endpoint_auth": "Bearer probe",
            "turn_id": uuid.uuid4().hex, "temperature": 0.2,
            "tool_context": {"session_id": "", "agent_id": "main"},
        }
        t0 = time.time()
        final, err = "", None
        try:
            req = urllib.request.Request(SIDECAR + "/turn?stream=false",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            r = urllib.request.urlopen(req, timeout=300)
            d = json.loads(r.read().decode())
            final, err = d.get("final_text", ""), d.get("error")
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        dt = time.time() - t0
        with _S.lock:
            ncalls, rchars = len(_S.calls), _S.result_chars
        results.append({"rep": rep, "landed": landed(task, final), "err": err,
                        "tool_calls": ncalls, "result_tokens": rchars // 4,
                        "latency": round(dt, 1)})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=list(ENGINES), default="a_parallel")
    ap.add_argument("--model", choices=list(MODELS), default="mistral-small-latest")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    import sys
    sys.stdout.reconfigure(line_buffering=True)  # stream progress live (see feedback_evals_line_buffered)
    srv, stub_url = start_stub()
    _S.stub_url = stub_url
    print("stub:", stub_url)

    combos = ([(e, m) for e in ENGINES for m in MODELS] if args.all
              else [(args.engine, args.model)])
    for engine, model in combos:
        print("\n" + "=" * 70)
        print("ENGINE=%s  MODEL=%s" % (engine, model))
        print("=" * 70)
        for task in TASKS:
            res = run_one(engine, model, task, reps=args.reps)
            land = sum(1 for r in res if r["landed"])
            avg_calls = sum(r["tool_calls"] for r in res) / len(res)
            avg_tok = sum(r["result_tokens"] for r in res) / len(res)
            avg_lat = sum(r["latency"] for r in res) / len(res)
            print("  %-30s landed %d/%d  calls~%.1f  ctx_tok~%.0f  %.1fs"
                  % (task["id"], land, len(res), avg_calls, avg_tok, avg_lat))
            for r in res:
                if r["err"]:
                    print("      rep%d ERROR: %s" % (r["rep"], r["err"]))


if __name__ == "__main__":
    main()
