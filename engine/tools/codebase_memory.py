"""
engine/tools/codebase_memory.py — code intelligence backed by the
codebase-memory-mcp binary (replaces the in-tree CodeGraph as of the
2026-06-27 cutover; see memory: project_codegraph_replacement_eval).

Integration shape: MemPalace-style — the binary is brain-managed, NOT wired as
an MCP server. We shell out to its CLI (`cbm cli <tool> '<json>'`) once per tool
call, with a HARD-KILL timeout (subprocess.run timeout → SIGKILL → CPU
reclaimed; a thread-based timeout can only abandon a CPU-bound child, see
memory: project_pdf_subprocess_timeout_fix). Results are JSON on stdout.

Per-tenant routing mirrors the old per-tenant CodeGraph (v9.210.0): the active
project's index lives under its own CBM_CACHE_DIR, resolved per call from
request_context.code_graph_db (repurposed to hold the tenant CACHE DIR, not a
sqlite path). Empty → the global brain-source tenant.

Config (config.json → codebase_memory, per-machine, gitignored):
  {"enabled": true, "bin": "<abs path to binary>", "cache_root": "<dir>"}
"""
import json
import os
import subprocess

from engine.context import get_request_context
from engine.tool_exec import _ok, _err

_CLI_TIMEOUT = 90  # seconds; hard SIGKILL on overrun


def _cfg() -> dict:
    import brain as _brain
    return (_brain._server_config() or {}).get("codebase_memory", {}) or {}


def _bin() -> str:
    return (_cfg().get("bin") or "").strip()


def _global_cache_root() -> str:
    # default lives beside the agents dir so it's per-machine, not the user HOME
    import brain as _brain
    root = (_cfg().get("cache_root") or "").strip()
    if root:
        return root
    return os.path.join(_brain.AGENTS_DIR, "main", ".cbm-cache")


def _tenant_cache_dir() -> str:
    """Per-request cache dir. request_context.code_graph_db carries the tenant
    cache DIR for a code-mode project (set by apply_domain_context); empty/None
    → the global brain-source tenant under cache_root/_global."""
    try:
        t = get_request_context().code_graph_db
    except Exception:
        t = None
    if t:
        return str(t)
    return os.path.join(_global_cache_root(), "_global")


def _project_name_for(cache_dir: str) -> str:
    """cbm keys projects by a slug derived from the indexed repo_path. We let
    cbm pick the slug at index time and discover it via list_projects scoped to
    this cache dir — there is exactly one project per tenant cache dir by
    construction, so the single entry is unambiguous."""
    out = _run("list_projects", {}, cache_dir, want_project=False)
    if isinstance(out, dict):
        projs = out.get("projects") or []
        if len(projs) == 1:
            return projs[0].get("name", "")
        if projs:
            # multiple (shouldn't happen per-tenant): prefer the one whose
            # root_path matches an existing dir, else first
            return projs[0].get("name", "")
    return ""


def _run(tool: str, payload: dict, cache_dir: str, *, want_project: bool = True):
    """Invoke `cbm cli <tool> '<json>'` with cache_dir, hard-kill timeout.
    Returns parsed dict, or {'error': ...}. When want_project, injects the
    discovered project name (every query tool requires it)."""
    binp = _bin()
    if not binp or not os.path.exists(binp):
        return {"error": "codebase_memory binary not configured or missing "
                         "(config.json → codebase_memory.bin)"}
    args = dict(payload)
    if want_project and "project" not in args:
        pname = _project_name_for(cache_dir)
        if not pname:
            return {"error": "no index for this project yet — build it first "
                             "(code index is created on code-mode entry)."}
        args["project"] = pname
    env = dict(os.environ, CBM_CACHE_DIR=cache_dir)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        p = subprocess.run(
            [binp, "cli", tool, json.dumps(args)],
            capture_output=True, text=True, env=env, timeout=_CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"codebase_memory '{tool}' timed out after {_CLI_TIMEOUT}s"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    # CLI prints info log lines then a JSON object; take the last JSON line
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"error": f"no JSON from '{tool}'", "stderr": (p.stderr or "")[:300]}


# ─── Index lifecycle (called by brain on code-mode entry / re-index) ──────────
def index_repository(repo_path: str, cache_dir: str | None = None) -> dict:
    """Build/refresh the index for a repo into its tenant cache dir. Explicit
    re-index (repos may not be git, so we don't rely on cbm's git-watcher)."""
    cache_dir = cache_dir or _tenant_cache_dir()
    return _run("index_repository", {"repo_path": os.path.abspath(repo_path)},
                cache_dir, want_project=False)


# ─── Agent tools (TOOL_DISPATCH entries) ─────────────────────────────────────
def tool_code_search(args: dict) -> str:
    """search_graph: discover code by BM25 (query), regex (name_pattern), or
    embedding (semantic_query=array). The discovery workhorse."""
    cache = _tenant_cache_dir()
    payload = {}
    for k in ("query", "name_pattern", "semantic_query", "label", "limit"):
        if k in args and args[k] not in (None, ""):
            payload[k] = args[k]
    if not payload:
        return _err("code_search: provide query, name_pattern, or semantic_query")
    d = _run("search_graph", payload, cache)
    if d.get("error"):
        return _err(d["error"])
    return _ok(d)


def tool_code_trace(args: dict) -> str:
    """trace_path: callers/callees. direction inbound|outbound|both, mode
    calls|data_flow."""
    fn = (args.get("function_name") or "").strip()
    if not fn:
        return _err("code_trace: function_name is required")
    payload = {"function_name": fn,
               "direction": args.get("direction", "inbound"),
               "mode": args.get("mode", "calls")}
    if "depth" in args:
        payload["depth"] = args["depth"]
    d = _run("trace_path", payload, _tenant_cache_dir())
    if d.get("error"):
        return _err(d["error"])
    return _ok(d)


def tool_code_query(args: dict) -> str:
    """query_graph: read-only Cypher over the code graph."""
    q = (args.get("query") or "").strip()
    if not q:
        return _err("code_query: a Cypher query string is required")
    d = _run("query_graph", {"query": q}, _tenant_cache_dir())
    if d.get("error"):
        return _err(d["error"])
    return _ok(d)


def tool_code_snippet(args: dict) -> str:
    """get_code_snippet: read source for a symbol by qualified_name/name."""
    qn = (args.get("qualified_name") or "").strip()
    if not qn:
        return _err("code_snippet: qualified_name is required")
    payload = {"qualified_name": qn}
    if args.get("include_neighbors"):
        payload["include_neighbors"] = True
    d = _run("get_code_snippet", payload, _tenant_cache_dir())
    if d.get("error"):
        return _err(d["error"])
    return _ok(d)
