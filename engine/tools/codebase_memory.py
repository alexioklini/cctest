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

# Extensions cbm can extract code symbols from (the common subset of its 158
# langs). Used only to classify a 0-symbol file as "genuine code that failed to
# index" vs "non-source file that is never indexed by design" (.md/.yaml/…).
_CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs",
    ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash", ".zsh", ".lua",
    ".zig", ".dart", ".m", ".mm", ".pl", ".pm", ".r", ".jl", ".ex", ".exs",
    ".erl", ".hs", ".ml", ".mli", ".clj", ".cljs", ".vue", ".svelte", ".sql",
}


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


# ─── Index introspection (for the UI: per-file state, graph view, status) ────
def index_status(cache_dir: str) -> dict:
    """Project-level index status for a tenant cache: project name + node/edge
    counts, or {indexed: False} if nothing built yet."""
    out = _run("list_projects", {}, cache_dir, want_project=False)
    projs = (out or {}).get("projects") or []
    if not projs:
        return {"indexed": False}
    p = projs[0]
    return {"indexed": True, "project": p.get("name"),
            "nodes": p.get("nodes"), "edges": p.get("edges"),
            "root_path": p.get("root_path")}


def per_file_state(working_dir: str, cache_dir: str) -> dict:
    """Map each source file under working_dir to an index state for the UI:
      indexed  — in the cbm index, node count returned, mtime <= index time
      stale    — in the index but the file's mtime is newer than the last index
      not_indexed — a source file the index doesn't know
    Derived from cbm (node counts per file via Cypher) + filesystem mtime, no
    separate tracker. Returns {files: {relpath: {state, nodes}}, indexed_at}."""
    st = index_status(cache_dir)
    if not st.get("indexed"):
        return {"indexed": False, "files": {}}
    project = st["project"]
    # Count only SYMBOL nodes per file (Function/Method/Class/…), NOT the
    # per-file File/Module/Folder wrapper nodes — otherwise a non-code file
    # (.md/.yaml) that only gets a File node would look "indexed" with count 1.
    cy = ("MATCH (n) WHERE n.file_path IS NOT NULL "
          "AND NOT (n:File OR n:Module OR n:Folder OR n:Project) "
          "RETURN n.file_path AS f, count(n) AS c")
    res = _run("query_graph", {"query": cy, "project": project}, cache_dir,
               want_project=False)
    # cbm returns file_path as repo-RELATIVE (e.g. "docs/api_reference.md"), so
    # key counts by the normalised relpath and look them up by the file's relpath
    # under working_dir (NOT abspath — that resolves against cwd and never
    # matches, the bug that made every file read "not_indexed").
    counts: dict[str, int] = {}
    for row in (res or {}).get("rows", []) or []:
        # rows may be list-or-dict depending on cbm; handle both
        if isinstance(row, dict):
            f, c = row.get("f"), row.get("c")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            f, c = row[0], row[1]
        else:
            continue
        if f:
            try:
                cnt = int(c)
            except (TypeError, ValueError):
                cnt = 0
            counts[os.path.normpath(str(f))] = cnt
    # cbm index time ≈ the cache dir mtime (rewritten each index)
    try:
        idx_mtime = os.path.getmtime(cache_dir)
    except OSError:
        idx_mtime = 0
    files: dict[str, dict] = {}
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cbm-cache",
            ".brain-extracted", ".trash", "dist", "build"}
    wd_abs = os.path.abspath(working_dir)
    for dirpath, dirnames, filenames in os.walk(wd_abs):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, wd_abs)
            n = counts.get(os.path.normpath(rel))
            try:
                fmt = os.path.getmtime(fp)
            except OSError:
                fmt = 0
            is_source = os.path.splitext(fn)[1].lower() in _CODE_EXTS
            if n:
                # has symbols → indexed (or stale if edited after the last index)
                state = "stale" if fmt > idx_mtime else "indexed"
            elif not is_source:
                # non-code file (.md/.html/.txt/.yaml/…): never indexed by design
                state = "not_source"
            else:
                # a code file with NO symbols: either empty/unparseable, or a
                # genuine index miss (should have been indexed but wasn't)
                state = "not_indexed"
            files[rel] = {"state": state, "nodes": n or 0}
    return {"indexed": True, "files": files, "indexed_at": idx_mtime,
            "nodes": st.get("nodes"), "edges": st.get("edges")}


# ─── Editor support: symbol search / callers / definition (for the UI) ───────
# These feed the in-editor features (symbol palette, go-to-def, who-calls,
# autocomplete, hover) via the .../code-index/symbols endpoint. They reuse the
# cbm CLI but shape the payload for the frontend: BM25 `query` mode is the only
# search mode that returns start_line/end_line per result (name_pattern returns
# null lines), so jump-to-definition is built on it. file_path comes back
# repo-RELATIVE — the frontend joins it to working_dir for terminalOpenFile.
_SYMBOL_LABELS = {"Function", "Method", "Class", "Variable", "Decorator",
                  "Interface", "Struct", "Enum", "Trait", "Constant", "Field"}


def code_symbols(query: str, cache_dir: str, limit: int = 30) -> dict:
    """Fuzzy symbol search for the palette/autocomplete. BM25 over the index;
    returns code symbols only (drops File/Module/Folder/Project/Section wrapper
    nodes), each with file_path (repo-relative) + start_line for jumping."""
    q = (query or "").strip()
    if not q:
        return {"symbols": []}
    d = _run("search_graph", {"query": q, "limit": max(1, min(int(limit or 30), 100))},
             cache_dir)
    if d.get("error"):
        return {"error": d["error"], "symbols": []}
    out = []
    for r in d.get("results", []) or []:
        if r.get("label") not in _SYMBOL_LABELS:
            continue
        out.append({
            "name": r.get("name"),
            "label": r.get("label"),
            "qualified_name": r.get("qualified_name"),
            "file": r.get("file_path"),          # repo-relative
            "line": r.get("start_line"),
            "end_line": r.get("end_line"),
        })
    return {"symbols": out}


def code_callers(qualified_or_name: str, cache_dir: str) -> dict:
    """Inbound callers of a symbol (who-calls), each with file_path + line so
    the editor can jump. Accepts a bare name or qualified name."""
    fn = (qualified_or_name or "").strip()
    if not fn:
        return {"callers": []}
    # trace_path keys on the bare function name; strip any qualifier
    bare = fn.split(".")[-1]
    d = _run("trace_path", {"function_name": bare, "direction": "inbound",
                            "mode": "calls"}, cache_dir)
    if d.get("error"):
        return {"error": d["error"], "callers": []}
    out = []
    for c in d.get("callers", []) or []:
        if isinstance(c, dict):
            out.append({"name": c.get("name") or c.get("function"),
                        "qualified_name": c.get("qualified_name"),
                        "file": c.get("file_path"), "line": c.get("start_line")})
        elif isinstance(c, str):
            out.append({"name": c})
    return {"callers": out, "function": bare}


def code_def(qualified_or_name: str, cache_dir: str) -> dict:
    """Definition + metadata for a symbol (go-to-def + hover): absolute
    file_path, line range, signature, docstring, caller/callee counts. Resolves
    a bare name to its qualified name via BM25 first when needed."""
    qn = (qualified_or_name or "").strip()
    if not qn:
        return {"error": "no symbol"}
    # get_code_snippet needs the qualified_name; if a bare name was given,
    # resolve the best match via BM25 search first.
    if "." not in qn:
        s = code_symbols(qn, cache_dir, limit=10)
        cand = next((x for x in s.get("symbols", []) if x.get("name") == qn), None)
        cand = cand or (s.get("symbols") or [None])[0]
        if cand and cand.get("qualified_name"):
            qn = cand["qualified_name"]
    d = _run("get_code_snippet", {"qualified_name": qn}, _tenant_cache_dir()
             if cache_dir is None else cache_dir, want_project=True)
    if d.get("error"):
        return {"error": d["error"]}
    return {
        "name": d.get("name"), "qualified_name": d.get("qualified_name"),
        "label": d.get("label"), "file": d.get("file_path"),  # absolute here
        "start_line": d.get("start_line"), "end_line": d.get("end_line"),
        "signature": d.get("signature"), "docstring": d.get("docstring"),
        "callers": d.get("callers"), "callees": d.get("callees"),
        "complexity": d.get("complexity"), "is_test": d.get("is_test"),
        "source": d.get("source"),
    }


def graph_overview(cache_dir: str, limit: int = 200) -> dict:
    """Lightweight graph-view payload: top nodes by degree + their edges, for a
    project graph visualisation. Best-effort."""
    st = index_status(cache_dir)
    if not st.get("indexed"):
        return {"indexed": False, "nodes": [], "edges": []}
    project = st["project"]
    res = _run("get_architecture", {"project": project}, cache_dir, want_project=False)
    return {"indexed": True, "project": project,
            "architecture": res if not (res or {}).get("error") else None,
            "nodes": st.get("nodes"), "edges": st.get("edges")}


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
