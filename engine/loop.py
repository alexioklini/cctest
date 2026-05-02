# Extracted from claude_cli.py — agentic loop, tool dispatch, send_message, middleware
# Cross-module deps: see claude_cli.py for full globals context
import collections, contextlib, datetime, fnmatch, hashlib, io, json, logging
import os, queue, random, re, select, shutil, signal, socket, sqlite3
import subprocess, sys, threading, time, traceback, urllib.request, urllib.error, uuid

# --- Tool imports (needed at module level for TOOL_DISPATCH) ---
from engine.tools.files import (
    tool_read_file, tool_write_file, tool_edit_file, tool_list_directory,
    tool_search_files, tool_execute_command, tool_python_exec,
    tool_read_document, tool_write_document, tool_edit_document,
)
from engine.tools.web import tool_web_fetch, exa_search
from engine.tools.email import (
    tool_gmail_inbox, tool_gmail_read, tool_gmail_search,
    tool_gmail_send, tool_gmail_reply,
)
from engine.tools.code_graph import (
    tool_code_graph_build, tool_code_graph_query,
    tool_code_graph_impact, tool_code_graph_enhance,
)
from engine.tools.git import tool_git_command, tool_github_command
from engine.memory.mempalace import (
    tool_mempalace_query, tool_mempalace_kg_query,
    tool_mempalace_kg_search, tool_mempalace_kg_neighbors,
    tool_save_chat_to_memory,
)
from engine.memory.store import (
    tool_memory_store, tool_memory_recall, tool_memory_delete,
    tool_memory_shared, tool_use_skill,
)
from engine.tasks import tool_delegate_task, tool_task_status, tool_task_cancel
from engine.context import (
    tool_list_nodes, tool_context_search, tool_context_detail, tool_context_recall,
    tool_schedule_list, tool_schedule_history,
    tool_mcp_connect, tool_mcp_disconnect, tool_mcp_servers,
    DEFAULT_MAX_CONTEXT_TOKENS,  # noqa: F401 — needed for TUI helper default args
)

# Functions not yet extracted to a submodule — pulled from claude_cli_original
# (tool_ask_user, deliver_ask_user_answer, tool_worker_*, tool_get_artifact_detail)
# Import lazily via _lazy_tool so missing ones don't break module load.
def _lazy_tool(name):
    """Return a stub that imports the named function from claude_cli_original at call time."""
    def _stub(args):
        import importlib
        mod = importlib.import_module("claude_cli_original")
        return getattr(mod, name)(args)
    _stub.__name__ = name
    return _stub

def _lazy_deliver(name):
    def _stub(*a, **kw):
        import importlib
        mod = importlib.import_module("claude_cli_original")
        return getattr(mod, name)(*a, **kw)
    _stub.__name__ = name
    return _stub

tool_get_artifact_detail = _lazy_tool("tool_get_artifact_detail")
tool_worker_status       = _lazy_tool("tool_worker_status")
tool_worker_abort        = _lazy_tool("tool_worker_abort")
tool_worker_pause        = _lazy_tool("tool_worker_pause")
tool_worker_resume       = _lazy_tool("tool_worker_resume")
tool_worker_send         = _lazy_tool("tool_worker_send")
tool_worker_ask_user     = _lazy_tool("tool_worker_ask_user")
tool_ask_user            = _lazy_tool("tool_ask_user")
deliver_ask_user_answer  = _lazy_deliver("deliver_ask_user_answer")

TOOL_DISPATCH = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_directory": tool_list_directory,
    "search_files": tool_search_files,
    "execute_command": tool_execute_command,
    "python_exec": tool_python_exec,
    "web_fetch": tool_web_fetch,
    "exa_search": lambda args: exa_search(
        query=args.get("query", ""),
        num_results=args.get("num_results", 5),
        category=args.get("category"),
    ),
    "mempalace_query": tool_mempalace_query,
    "mempalace_kg_query": lambda args: tool_mempalace_kg_query(args),
    "mempalace_kg_search": lambda args: tool_mempalace_kg_search(args),
    "mempalace_kg_neighbors": lambda args: tool_mempalace_kg_neighbors(args),
    "save_chat_to_memory": tool_save_chat_to_memory,
    "memory_store": tool_memory_store,
    "memory_recall": tool_memory_recall,
    "memory_delete": tool_memory_delete,
    "memory_shared": tool_memory_shared,
    "gmail_inbox": tool_gmail_inbox,
    "gmail_read": tool_gmail_read,
    "gmail_search": tool_gmail_search,
    "gmail_send": tool_gmail_send,
    "gmail_reply": tool_gmail_reply,
    "delegate_task": tool_delegate_task,
    "task_status": tool_task_status,
    "task_cancel": tool_task_cancel,
    "use_skill": tool_use_skill,
    "list_nodes": tool_list_nodes,
    "context_search": tool_context_search,
    "context_detail": tool_context_detail,
    "context_recall": tool_context_recall,
    "schedule_list": tool_schedule_list,
    "schedule_history": tool_schedule_history,
    "read_document": tool_read_document,
    "write_document": tool_write_document,
    "edit_document": tool_edit_document,
    "mcp_connect": tool_mcp_connect,
    "mcp_disconnect": tool_mcp_disconnect,
    "mcp_servers": tool_mcp_servers,
    "code_graph_build": tool_code_graph_build,
    "code_graph_query": tool_code_graph_query,
    "code_graph_impact": tool_code_graph_impact,
    "code_graph_enhance": tool_code_graph_enhance,
    "git_command": tool_git_command,
    "github_command": tool_github_command,
    "tool_search": lambda args: _tool_search(args),
    "get_artifact_detail": tool_get_artifact_detail,
    "worker_status": tool_worker_status,
    "worker_abort": tool_worker_abort,
    "worker_pause": tool_worker_pause,
    "worker_resume": tool_worker_resume,
    "worker_send": tool_worker_send,
    "worker_ask_user": tool_worker_ask_user,
    "ask_user": tool_ask_user,
}


def _tool_search(args: dict) -> str:
    """Search for available tools by name or description.

    Returns matching tool schemas from both built-in and MCP tools.
    Discovered tools are tracked per-session and included in subsequent API calls.
    """
    query = args.get("query", "").lower()
    max_results = args.get("max_results", 5)

    if not query:
        return _err("query is required")

    # Search built-in tools
    matches = []
    for td in TOOL_DEFINITIONS:
        name = td.get("name", "")
        desc = td.get("description", "")
        if isinstance(desc, tuple):
            desc = " ".join(desc)
        score = 0
        if query in name.lower():
            score += 10
        if query in desc.lower():
            score += 5
        # Fuzzy: match individual words
        for word in query.split():
            if word in name.lower():
                score += 3
            if word in desc.lower():
                score += 1
        if score > 0:
            matches.append((score, {"name": name, "description": desc[:200],
                                     "input_schema": td.get("input_schema", {})}))

    # Search MCP tools
    mcp_mgr = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    if mcp_mgr:
        try:
            for mcp_td in mcp_mgr.get_tool_definitions():
                name = mcp_td.get("name", "")
                desc = mcp_td.get("description", "")
                score = 0
                if query in name.lower():
                    score += 10
                if query in desc.lower():
                    score += 5
                for word in query.split():
                    if word in name.lower():
                        score += 3
                    if word in desc.lower():
                        score += 1
                if score > 0:
                    matches.append((score, {"name": name, "description": desc[:200],
                                             "input_schema": mcp_td.get("input_schema", {})}))
        except Exception:
            pass

    # Sort by score descending, take top results
    matches.sort(key=lambda x: x[0], reverse=True)
    results = [m[1] for m in matches[:max_results]]

    # Track discovered tools for deferred loading
    discovered = getattr(_thread_local, '_discovered_tools', set())
    for r in results:
        discovered.add(r["name"])
    _thread_local._discovered_tools = discovered

    if not results:
        return _ok({"matches": [], "message": f"No tools found matching '{query}'"})
    return _ok({"matches": results, "count": len(results)})


# Per-thread tool call dedup tracking
# Session-scoped tool-call dedup state. Replaces the prior threading.local() impl,
# which silently leaked dupes whenever tool execution crossed a thread boundary
# (worker-subagent wrappers, ThreadPoolExecutor in _execute_tools_batch, etc.).
# Keyed by session_id (falls back to a sentinel when no session is available,
# e.g. CLI one-shot invocations).
_tool_dedup_lock = threading.Lock()
_tool_dedup: dict[str, dict] = {}  # sid -> {"calls": set[str], "consecutive_dupes": int, "last_touch": float}
_TOOL_DEDUP_TTL = 3600  # seconds; drop state for sessions untouched this long


def _dedup_sid() -> str:
    """Resolve the current dedup scope key. Session id when known, else a per-thread
    sentinel so unrelated CLI invocations don't contaminate each other."""
    sid = getattr(_thread_local, 'current_session_id', None)
    if sid:
        return sid
    # No session context (CLI one-shots, warmup, etc.) — fall back to thread id.
    return f"_thread:{threading.get_ident()}"


def _dedup_state(sid: str) -> dict:
    """Get-or-create the per-session dedup bucket. Caller must hold the lock."""
    st = _tool_dedup.get(sid)
    if st is None:
        st = {"calls": set(), "consecutive_dupes": 0, "last_touch": time.time()}
        _tool_dedup[sid] = st
    else:
        st["last_touch"] = time.time()
    return st


def _dedup_gc_locked() -> None:
    """Drop buckets we haven't touched in a while. Caller holds the lock."""
    now = time.time()
    stale = [k for k, v in _tool_dedup.items() if now - v.get("last_touch", 0) > _TOOL_DEDUP_TTL]
    for k in stale:
        _tool_dedup.pop(k, None)


def _check_tool_dedup(name: str, args: dict) -> str | None:
    """Check if this exact tool call was already made in the current session.
    Raises TaskCancelled after 2 dupes so the agentic loop stops banging on the
    same tool. Exempt tools that legitimately repeat with identical args.

    Session-scoped (not thread-scoped) so the check survives worker-subagent
    threads and ThreadPoolExecutor batches that would otherwise each get a
    fresh, empty dedup set and miss every duplicate.
    """
    _DEDUP_EXEMPT = {"memory_recall", "memory_shared", "delegate_task", "task_status",
                     "schedule_list", "schedule_history",
                     # read_document / read_file have their own per-session
                     # cache that returns a "already read in turn N, unchanged"
                     # stub on duplicate calls — the bare-string dedup would
                     # otherwise abort the loop on the second cache-hit case.
                     "read_document", "read_file"}
    if name in _DEDUP_EXEMPT:
        return None

    sid = _dedup_sid()
    key = f"{name}:{json.dumps(args, sort_keys=True)}"
    with _tool_dedup_lock:
        st = _dedup_state(sid)
        if key in st["calls"]:
            st["consecutive_dupes"] += 1
            if st["consecutive_dupes"] >= 2:
                # Hard abort — model is stuck in a loop
                raise TaskCancelled()
            return _err(
                f"Duplicate tool call detected. You already called {name} with these exact arguments. "
                "Use the previous result or try a different approach."
            )
        st["calls"].add(key)
        st["consecutive_dupes"] = 0
        # Keep bucket bounded so long sessions don't grow unbounded.
        if len(st["calls"]) > 100:
            st["calls"] = set(list(st["calls"])[-50:])
    return None


def reset_tool_dedup():
    """Reset the dedup tracker for the current session. Called at turn start so a
    user sending a new question after a tool-heavy prior turn gets a clean slate."""
    sid = _dedup_sid()
    with _tool_dedup_lock:
        _tool_dedup.pop(sid, None)
        _dedup_gc_locked()


# --- Per-session read_document / read_file cache (token-saver) ---
#
# Same chat session, same file, same call shape (no pagination args) → second
# call returns a stub instead of the full content. Cuts the "model re-reads
# the same .md companion every turn" cost which is the dominant driver of
# token growth on policy-Q&A sessions where mempalace_query keeps returning
# the same drawer pointing at the same source.
#
# Cache key: (sid, abs_path). Value: (mtime, size, turn_first_read, content_hash).
# On hit: stat() the file, compare mtime+size. Match → emit stub. Diff → bypass
# cache, refresh entry with new content, return fresh result. Cache is
# invalidated explicitly by _after_file_write() when the agent writes/edits the
# same path in-session (the inotify-style fast path; mtime check would catch it
# anyway but explicit invalidation avoids one wasted read on the model's next
# turn).
_read_doc_cache_lock = threading.Lock()
_read_doc_cache: dict[str, dict[str, dict]] = {}  # sid -> path -> entry
_READ_DOC_CACHE_TTL = 3600  # drop sessions untouched this long
_READ_DOC_CACHE_MAX_PER_SESSION = 64

# --- Cross-encoder reranker (lazy, process-wide) -------------------------
# Loaded on first mempalace_query when reranker.enabled=true; held in memory
# afterwards. Default model BAAI/bge-reranker-v2-m3 is multilingual (100+
# languages incl. German), 560M params, MIT license, fits comfortably in
# a few hundred MB on Apple Silicon MPS. Loading takes ~5-8s the first
# time (incl. HF hub download on cold start), <1s on subsequent process
# starts (HF cache hit).
_reranker_lock = threading.Lock()
_reranker_cache: dict[tuple[str, str], object] = {}


def _get_reranker_model(model_id: str, device_pref: str = "auto"):
    """Return a CrossEncoder instance, loading lazily and caching by
    (model_id, resolved_device). Returns None if sentence-transformers
    isn't installed or device resolution fails — caller falls back to
    unreranked order."""
    if not model_id:
        return None
    # Resolve device preference. "auto" → mps on Apple Silicon, cuda on
    # NVIDIA, cpu otherwise. Caller can pin via config.
    if device_pref == "auto":
        try:
            import torch
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        except Exception:
            device = "cpu"
    else:
        device = device_pref
    key = (model_id, device)
    with _reranker_lock:
        m = _reranker_cache.get(key)
        if m is not None:
            return m
        # Make sure the mempalace venv site-packages is on sys.path so we
        # find sentence_transformers / torch (we install it there to keep
        # all heavy ML deps in one place).
        ok, _err = _ensure_mempalace_importable()
        if not ok:
            return None
        try:
            from sentence_transformers import CrossEncoder
        except Exception:
            return None
        try:
            t0 = time.time()
            m = CrossEncoder(model_id, device=device, max_length=512)
            try:
                logging.info(f"[reranker] loaded {model_id} on {device} in {time.time()-t0:.1f}s")
            except Exception:
                pass
            _reranker_cache[key] = m
            return m
        except Exception as e:
            try:
                logging.warning(f"[reranker] failed to load {model_id} on {device}: {e}")
            except Exception:
                pass
            return None


def _read_doc_cache_sid() -> str:
    sid = getattr(_thread_local, 'current_session_id', None)
    if sid:
        return sid
    return f"_thread:{threading.get_ident()}"


def _read_doc_cache_gc_locked() -> None:
    now = time.time()
    stale = [k for k, v in _read_doc_cache.items() if v.get("_last_touch", 0) < now - _READ_DOC_CACHE_TTL]
    for k in stale:
        _read_doc_cache.pop(k, None)


def _read_doc_cache_lookup(path: str) -> str | None:
    """Return a stub tool_result if a prior turn read this exact file with the
    same on-disk identity (mtime+size). Otherwise None — caller proceeds with
    the real read.
    """
    sid = _read_doc_cache_sid()
    try:
        st = os.stat(path)
    except OSError:
        return None
    with _read_doc_cache_lock:
        bucket = _read_doc_cache.get(sid)
        if not bucket:
            return None
        entry = bucket.get(path)
        if not entry:
            return None
        if entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
            # File changed since last read in this session — drop and miss.
            bucket.pop(path, None)
            return None
        bucket["_last_touch"] = time.time()
        turn = entry.get("turn", 0)
        chash = entry.get("content_hash", "")
    return _ok({
        "path": path,
        "cached": True,
        "first_read_in_turn": turn,
        "content_hash": chash,
        "note": (
            f"Bereits in Turn {turn} dieser Sitzung vollständig gelesen — "
            "Inhalt ist seitdem unverändert (mtime+size match). Nutze den "
            "vorigen tool_result. Falls du explizit eine andere Stelle der "
            "Datei brauchst, rufe read_document mit offset/limit/pages auf "
            "(seitenweises Lesen umgeht den Cache)."
        ),
    })


def _read_doc_cache_store(path: str, content: str, tool_round: int = 0) -> None:
    sid = _read_doc_cache_sid()
    try:
        st = os.stat(path)
    except OSError:
        return
    chash = hashlib.sha256((content or "").encode("utf-8", errors="replace")).hexdigest()[:16]
    with _read_doc_cache_lock:
        bucket = _read_doc_cache.get(sid)
        if bucket is None:
            bucket = {"_last_touch": time.time()}
            _read_doc_cache[sid] = bucket
            _read_doc_cache_gc_locked()
        # Bound per-session size; drop oldest by turn.
        if len([k for k in bucket if not k.startswith("_")]) >= _READ_DOC_CACHE_MAX_PER_SESSION:
            kept = [(k, v) for k, v in bucket.items() if not k.startswith("_")]
            kept.sort(key=lambda kv: kv[1].get("turn", 0))
            for k, _ in kept[:max(1, len(kept) // 4)]:
                bucket.pop(k, None)
        bucket[path] = {
            "mtime": st.st_mtime,
            "size": st.st_size,
            "turn": tool_round,
            "content_hash": chash,
        }
        bucket["_last_touch"] = time.time()


def _read_doc_cache_invalidate(path: str) -> None:
    """Drop one path from every session's cache. Called by _after_file_write so
    a follow-up read in the same session always re-reads after the model
    writes/edits the file."""
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return
    with _read_doc_cache_lock:
        for bucket in _read_doc_cache.values():
            bucket.pop(abs_path, None)


def _read_doc_cache_session_paths(session_id: str | None = None) -> list[str]:
    """Return the list of absolute paths the given session has read via
    `read_document` / `read_file`. Used by the citation validator to know
    which files to grep verbatim quotes against."""
    sid = session_id or _read_doc_cache_sid()
    out: list[str] = []
    with _read_doc_cache_lock:
        bucket = _read_doc_cache.get(sid)
        if not bucket:
            return out
        for k in bucket:
            if not k.startswith("_"):
                out.append(k)
    return out


# --- Citation Validator (Phase 1: validate + annotate, no re-round) ---
# Scans an assistant response for `[Quelle: <basename> — "<verbatim quote>"]`
# brackets and verifies each quote actually appears in a file the session has
# read. Counts factual sentences/bullets that lack any bracket. Emits an
# annotation block appended to the response when discipline issues are found,
# so the user can see how reliable the citations are.

_CITATION_BRACKET_RE = re.compile(
    # Match [Quelle: <basename> — "<quote>"] tolerantly:
    #   • allow em-dash, en-dash, or hyphen as separator
    #   • allow straight or smart quotes around the quote
    #   • basename is everything up to the separator (lazy)
    #   • quote spans across the closing bracket boundaries with non-greedy
    r'\[\s*Quelle:\s*([^—\-–"\]]+?)\s*[—\-–]\s*[\"“»]([^\"”«\]]+?)[\"”«]\s*\]',
)
_CITATION_BARE_RE = re.compile(r'\[\s*Quelle:\s*([^\]]+?)\]')

# Sentence-with-claim heuristic: lines that look like they make a factual
# statement but contain no [Quelle: …] bracket. Used to count uncited
# claims. Bullet markers and numbered list markers count as separate items.
_BULLET_RE = re.compile(r'^\s*(?:[-*•·]|\d+\.|[a-z]\))\s+', re.MULTILINE)


def _normalize_quote(s: str) -> str:
    """Whitespace + punctuation lenient match for verifying quotes are in source."""
    s = s.replace("„", "\"").replace("“", "\"").replace("”", "\"").replace("«", "\"").replace("»", "\"")
    s = s.replace("‚", "'").replace("‘", "'").replace("’", "'")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.lower()
    return s


def _strip_md_companion_suffix(basename: str) -> str:
    """`policy.pdf.md` → `policy.pdf`; `something.md` (no dotted-ext before .md) is left alone."""
    if basename.endswith(".md"):
        stem = basename[:-3]
        # Only strip if there's a "real" extension before .md (e.g. .pdf, .docx)
        if "." in stem:
            return stem
    return basename


def _find_source_path_by_basename(basename: str, session_paths: list[str]) -> str | None:
    """Best-match a citation basename against paths the session has read.
    Tries: exact basename match, .md-suffixed match, prefix substring."""
    target = _strip_md_companion_suffix(basename.strip()).lower()
    candidates = []
    for p in session_paths:
        bn = os.path.basename(p)
        bn_stripped = _strip_md_companion_suffix(bn).lower()
        if bn_stripped == target:
            return p
        if target in bn_stripped or bn_stripped in target:
            candidates.append(p)
    return candidates[0] if candidates else None


def _read_file_cached_for_validation(path: str, _vcache: dict) -> str | None:
    """Lazy read with per-call cache (one validation pass)."""
    if path in _vcache:
        return _vcache[path]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        _vcache[path] = None
        return None
    _vcache[path] = content
    return content


def validate_citations_in_response(text: str, session_id: str | None = None,
                                   max_quote_len: int = 400) -> dict:
    """Scan an assistant response for citation brackets and verify each quote
    against the corresponding source file the session read.

    Returns a dict:
      {
        "verified": int,        # brackets where quote was found in source
        "unverified": list,     # [(basename, quote_excerpt, reason), ...]
        "uncited_claims": int,  # bulleted/sentence claims with no bracket
        "claim_total": int,     # total bullets + claim-like sentences detected
        "total_brackets": int,
        "annotation": str | None,  # markdown block to append to the response
      }

    No re-round, no rewriting of `text`. Caller decides whether to append
    `annotation` to the message.
    """
    out = {"verified": 0, "unverified": [], "uncited_claims": 0,
           "claim_total": 0, "total_brackets": 0, "annotation": None}
    if not text or not text.strip():
        return out

    session_paths = _read_doc_cache_session_paths(session_id)
    _vcache: dict = {}

    # 1. Find every [Quelle: X — "Y"] bracket and verify Y appears in X.
    matches = list(_CITATION_BRACKET_RE.finditer(text))
    bare_matches = list(_CITATION_BARE_RE.finditer(text))
    out["total_brackets"] = len(bare_matches)
    parsed_basenames = set()
    for m in matches:
        bn_raw, quote = m.group(1), m.group(2)
        parsed_basenames.add(bn_raw.strip())
        path = _find_source_path_by_basename(bn_raw, session_paths) if session_paths else None
        if not path:
            out["unverified"].append((bn_raw.strip(), quote[:120],
                                      "source not in session reads"))
            continue
        content = _read_file_cached_for_validation(path, _vcache)
        if content is None:
            out["unverified"].append((bn_raw.strip(), quote[:120],
                                      f"could not read {path}"))
            continue
        if _normalize_quote(quote) in _normalize_quote(content):
            out["verified"] += 1
        else:
            out["unverified"].append((bn_raw.strip(), quote[:120],
                                      "quote not found in source"))

    # 2. Count brackets that didn't match the verbatim-quote shape (bare
    # `[Quelle: …]` without a quote part) — these are partial citations.
    bare_only = max(0, len(bare_matches) - len(matches))

    # 3. Count claim-like items (bullets + numbered list items + standalone
    # sentences) that have no bracket on the same logical line/bullet. This
    # is a heuristic — bullets are easy to count; standalone-sentence claim
    # detection is intentionally conservative to avoid false positives on
    # connector text.
    bullets = list(_BULLET_RE.finditer(text))
    out["claim_total"] = len(bullets)
    if bullets:
        # Build line-level bullet ranges
        for i, mb in enumerate(bullets):
            start = mb.end()
            end = bullets[i + 1].start() if i + 1 < len(bullets) else len(text)
            chunk = text[start:end]
            if "[Quelle:" not in chunk:
                # Probably a connector ("Zusammenfassend:", a heading…) if
                # it has < 8 words AND no period; skip those.
                words = len(re.findall(r"\w+", chunk))
                has_period = "." in chunk or "!" in chunk or ":" in chunk
                if words >= 8 or has_period:
                    out["uncited_claims"] += 1

    # 4. Build human-readable annotation if there's anything to flag.
    if out["unverified"] or out["uncited_claims"] > 0 or bare_only > 0:
        lines = ["", "---", "**🛈 Citation-Validation:**"]
        if out["verified"] or out["unverified"]:
            lines.append(
                f"- {out['verified']} von {out['verified'] + len(out['unverified'])} "
                f"Zitat-Quotes konnten gegen die gelesenen Quelldateien verifiziert werden."
            )
        if out["unverified"]:
            lines.append("- ⚠ **Nicht verifizierte Zitate:**")
            for bn, q, reason in out["unverified"][:5]:
                excerpt = (q[:80] + "…") if len(q) > 80 else q
                lines.append(f"  - `{bn}` — \"{excerpt}\" *({reason})*")
            if len(out["unverified"]) > 5:
                lines.append(f"  - … und {len(out['unverified']) - 5} weitere.")
        if bare_only > 0:
            lines.append(
                f"- ⚠ {bare_only} Zitate ohne wörtliches Zitat (Form `[Quelle: X]` "
                f"statt `[Quelle: X — \"...\"]`) — pro CITATION DISCIPLINE bitte ergänzen."
            )
        if out["uncited_claims"] > 0:
            lines.append(
                f"- ⚠ {out['uncited_claims']} von {out['claim_total']} Bullet-Points / "
                f"Behauptungen ohne `[Quelle: ...]`-Citation — pro Project-Instructions "
                f"sollte jeder faktische Claim sein eigenes Zitat tragen."
            )
        out["annotation"] = "\n".join(lines)
    return out


# Public message-cleanup helper used by both send_message's main path and
# the citation re-round helper. Mirrors the inline filter used at line ~22061.
_CLEAN_MSG_ALLOWED_KEYS = {"role", "content", "tool_calls", "tool_call_id", "name"}
_CLEAN_MSG_INTERNAL_ROLES = {"thinking"}


def clean_messages_for_api(messages: list) -> list:
    """Return a wire-safe copy of `messages`: internal roles dropped, only
    OpenAI-protocol fields kept. Used for both main send_message and
    out-of-band helpers (citation re-round, etc.) so they share one filter."""
    out = []
    for msg in messages:
        if msg.get("role") in _CLEAN_MSG_INTERNAL_ROLES:
            continue
        out.append({k: v for k, v in msg.items() if k in _CLEAN_MSG_ALLOWED_KEYS})
    return out


def citation_reround_needed(validation: dict,
                            uncited_ratio_threshold: float = 0.30,
                            unverified_threshold: int = 2) -> bool:
    """Decide whether the validation result warrants a re-round.

    Fires when EITHER:
      - more than `uncited_ratio_threshold` of detected bullet/claim items
        carry no [Quelle: ...] bracket (default 30%); OR
      - at least `unverified_threshold` quote brackets failed to verify
        against the read source files (default 2).

    Returns False if `claim_total == 0` and no unverified — nothing actionable.
    """
    if not validation:
        return False
    claim_total = int(validation.get("claim_total") or 0)
    uncited = int(validation.get("uncited_claims") or 0)
    unverified = len(validation.get("unverified") or [])
    if unverified >= unverified_threshold:
        return True
    if claim_total > 0 and (uncited / claim_total) > uncited_ratio_threshold:
        return True
    return False


def build_citation_reround_feedback(validation: dict, original_reply: str) -> str:
    """Build the user-message that gets injected for a citation re-round.

    Tells the model exactly what failed and how to fix it. The injected
    message references the model's previous answer (already in the message
    history at this point) so the model can rewrite it directly."""
    parts = ["Deine letzte Antwort verletzt die CITATION DISCIPLINE des Projekts. Korrigiere sie."]

    claim_total = int(validation.get("claim_total") or 0)
    uncited = int(validation.get("uncited_claims") or 0)
    unverified = validation.get("unverified") or []

    if claim_total > 0 and uncited > 0:
        parts.append(
            f"\n**{uncited} von {claim_total} Bullet-Points / Behauptungen** in "
            f"deiner Antwort haben keine `[Quelle: <basename> — \"<wörtliches Zitat>\"]`-"
            f"Citation. Jede faktische Aussage muss ihre eigene Citation tragen."
        )

    if unverified:
        parts.append(f"\n**{len(unverified)} Zitat(e) konnten nicht in der Quelldatei verifiziert werden:**")
        for bn, quote, reason in unverified[:5]:
            excerpt = (quote[:100] + "…") if len(quote) > 100 else quote
            parts.append(f"- `{bn}` — \"{excerpt}\" *({reason})*")
        parts.append(
            "Stelle sicher dass jedes Zitat WÖRTLICH aus dem `read_document`-Output "
            "kopiert ist, character-by-character. Paraphrasen sind keine Citations."
        )

    parts.append(
        "\n**Anweisung**: Schreibe die Antwort nochmal. Behalte den richtigen Inhalt, "
        "ergänze fehlende Citations mit verbatim Quotes aus dem `read_document`-Output. "
        "Wenn du für eine Behauptung keine verbatim-Quote findest — **lösche die Behauptung**. "
        "Eine kürzere, vollständig zitierte Antwort ist besser als eine lange Antwort mit "
        "nicht-zitierten Aussagen. Antworte nur mit dem korrigierten Antworttext, keine Meta-Kommentare."
    )
    return "\n".join(parts)


def run_citation_reround(messages: list, original_reply: str, validation: dict,
                         model: str, api_key: str, base_url: str,
                         temperature: float = 0.2, top_p: float = 0.85,
                         timeout: float = 180.0) -> tuple[str, dict]:
    """Single non-streaming retry to fix citation discipline. Returns
    (corrected_reply, retry_validation). On any error, returns (original_reply, {})
    so the caller can fall back gracefully.

    `messages` should be the full conversation INCLUDING the assistant's
    original reply (not yet persisted). We append a synthetic user-message
    with the validator feedback and ask for a corrected answer.
    """
    import urllib.request
    import urllib.error

    feedback = build_citation_reround_feedback(validation, original_reply)
    # Build a clean message list: original convo + the assistant's first
    # answer + our feedback as a new user turn. The model sees: question,
    # tool-call rounds (with results), original answer, our feedback.
    new_messages = list(messages)  # copy
    # Make sure the assistant's latest answer is in the list (callers vary).
    if not new_messages or new_messages[-1].get("role") != "assistant":
        new_messages.append({"role": "assistant", "content": original_reply})
    new_messages.append({"role": "user", "content": feedback})

    body = {
        "model": get_api_model_id(model),
        "messages": new_messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": 4096,
        # Intentionally NO tools — re-round is composition-only, the model
        # already has all retrieval results in the message history.
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            outer = json.loads(resp.read().decode("utf-8"))
        choices = outer.get("choices") or []
        if not choices:
            return original_reply, {}
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not content:
            return original_reply, {}
        # Validate the corrected answer too — useful diagnostic, even if we
        # accept it unconditionally (per design: max 1 re-round).
        sid = getattr(_thread_local, 'current_session_id', None)
        retry_val = validate_citations_in_response(content, session_id=sid)
        return content, retry_val
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        try: print(f"[citation-reround] error: {type(e).__name__}: {e}")
        except Exception: pass
        return original_reply, {}


# --- Hook Runner ---

class HookRunner:
    """Runs external hook scripts for tool execution lifecycle events."""

    def __init__(self, agent_id: str = "main"):
        self.agent_id = agent_id
        self._hooks = self._load_hooks()

    def _load_hooks(self) -> list[dict]:
        """Load hook config from agent.json."""
        try:
            cfg = AgentConfig(self.agent_id)
            hooks_cfg = cfg.config.get("hooks", {})
            if not hooks_cfg.get("enabled", False):
                return []
            return [h for h in hooks_cfg.get("scripts", []) if h.get("enabled", True)]
        except Exception:
            return []

    def reload(self):
        self._hooks = self._load_hooks()

    def _get_timeout(self) -> int:
        try:
            cfg = AgentConfig(self.agent_id)
            return cfg.config.get("hooks", {}).get("timeout", 5000)
        except Exception:
            return 5000

    def get_hooks(self, hook_type: str, tool_name: str = "") -> list[dict]:
        """Get matching hooks for a type and tool."""
        result = []
        for h in self._hooks:
            if h.get("type") != hook_type:
                continue
            tools = h.get("tools", ["*"])
            if "*" in tools or tool_name in tools:
                result.append(h)
        return result

    def run_hook(self, hook: dict, env_extra: dict) -> tuple[int, str]:
        """Run a hook script. Returns (exit_code, stdout)."""
        script = hook.get("script", "")
        if not script:
            return 0, ""

        agent_dir = os.path.join(AGENTS_DIR, self.agent_id)
        script_path = os.path.join(agent_dir, script)
        if not os.path.isfile(script_path):
            logging.warning(f"Hook script not found: {script_path}")
            return 0, ""

        env = os.environ.copy()
        env["HOOK_AGENT"] = self.agent_id
        env["HOOK_SESSION_ID"] = getattr(_thread_local, 'session_id', "") or ""
        env["HOOK_TIMESTAMP"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        env.update({k: str(v) for k, v in env_extra.items()})

        timeout_s = self._get_timeout() / 1000.0
        stdin_data = json.dumps(env_extra).encode("utf-8")

        try:
            proc = subprocess.Popen(
                ["bash", script_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.PIPE, env=env,
                cwd=agent_dir, start_new_session=True,
            )
            stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout_s)
            output = stdout.decode("utf-8", errors="replace").strip()
            if stderr:
                logging.debug(f"Hook {hook.get('name','?')} stderr: {stderr.decode('utf-8', errors='replace')[:200]}")
            return proc.returncode, output
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
            proc.communicate(timeout=2)
            logging.warning(f"Hook {hook.get('name','?')} timed out after {timeout_s}s")
            return 1, f"Hook timed out after {timeout_s}s"
        except Exception as e:
            logging.warning(f"Hook {hook.get('name','?')} failed: {e}")
            return 0, ""  # fail-open

    def run_pre_hooks(self, tool_name: str, args: dict) -> str | None:
        """Run pre-hooks. Returns error message if blocked, None if allowed."""
        hooks = self.get_hooks("pre", tool_name)
        for h in hooks:
            env = {
                "HOOK_TYPE": "pre",
                "HOOK_TOOL_NAME": tool_name,
                "HOOK_TOOL_ARGS": json.dumps(args),
            }
            code, output = self.run_hook(h, env)
            if code == 1:
                hook_name = h.get("name", "unknown")
                msg = output or f"Blocked by hook: {hook_name}"
                logging.info(f"Hook {hook_name} blocked {tool_name}: {msg[:100]}")
                return _err(f"HOOK BLOCKED ({hook_name}): {msg}")
            if code == 2:
                break  # skip remaining hooks
        return None

    def run_post_hooks(self, tool_name: str, args: dict, result: str) -> str:
        """Run post-hooks. Returns (possibly modified) result."""
        hooks = self.get_hooks("post", tool_name)
        for h in hooks:
            env = {
                "HOOK_TYPE": "post",
                "HOOK_TOOL_NAME": tool_name,
                "HOOK_TOOL_ARGS": json.dumps(args),
                "HOOK_TOOL_RESULT": result[:50000],  # cap env var size
            }
            code, output = self.run_hook(h, env)
            if code == 0 and output:
                result = output  # modify result
            if code == 2:
                break
        return result

    def run_after_file_write(self, file_path: str, action: str):
        """Run after_file_write hooks."""
        hooks = self.get_hooks("after_file_write")
        for h in hooks:
            env = {
                "HOOK_TYPE": "after_file_write",
                "HOOK_FILE_PATH": file_path,
                "HOOK_FILE_ACTION": action,
            }
            self.run_hook(h, env)


# Cache hook runners per agent
_hook_runners: dict[str, HookRunner] = {}
_hook_runners_lock = threading.Lock()


def _get_hook_runner(agent_id: str = "main") -> HookRunner:
    """Get or create a HookRunner for an agent."""
    with _hook_runners_lock:
        if agent_id not in _hook_runners:
            _hook_runners[agent_id] = HookRunner(agent_id)
        return _hook_runners[agent_id]


# --- Artifact Helpers ---

_ARTIFACT_TYPE_MAP = {
    "html": "html", "htm": "html",
    "svg": "svg",
    "md": "markdown", "markdown": "markdown",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image", "bmp": "image",
    "pdf": "document", "docx": "document", "xlsx": "document", "pptx": "document",
    "txt": "text",
}

# Intermediate = working data or helper scripts the agent uses during a run;
# the output role (default) is the deliverable the user actually cares about.
# Keeps the run-detail and browse views decluttered by default. Text/markdown
# is always "output" since that's typically the final report.
_ARTIFACT_INTERMEDIATE_EXTS = {
    "py", "sh", "bash", "zsh", "js", "ts", "rb", "pl",
    "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg",
    "csv", "tsv", "log",
}

def _is_artifact_path(path: str) -> bool:
    """Check if path is under agents/<name>/artifacts/"""
    try:
        agents_dir = os.path.realpath(AGENTS_DIR)
        real_path = os.path.realpath(path)
        if not real_path.startswith(agents_dir + os.sep):
            return False
        parts = real_path[len(agents_dir) + 1:].split(os.sep)
        return len(parts) >= 3 and parts[1] == "artifacts"
    except Exception:
        return False

def _register_artifact_version(path: str, action: str, agent_id: str):
    """Register or update an artifact in the DB, capturing content snapshot.
    Returns (artifact_id, version, type) or None on failure."""
    try:
        from server import ChatDB
        import uuid as _uuid_mod

        session_id = getattr(_thread_local, 'current_session_id', None) or ""
        if not session_id:
            return None

        name = os.path.basename(path)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        artifact_type = _ARTIFACT_TYPE_MAP.get(ext, "code")
        artifact_role = "intermediate" if ext in _ARTIFACT_INTERMEDIATE_EXTS else "output"

        # Read content snapshot (cap at 5MB)
        try:
            with open(path, "rb") as f:
                content = f.read(5 * 1024 * 1024)
            size = os.path.getsize(path)
            if size > 5 * 1024 * 1024:
                content = None  # too large, disk-only
        except Exception:
            content = None
            size = 0

        # Check if artifact already exists for this path+session
        existing = ChatDB.get_artifact_by_path(session_id, path)
        if existing:
            artifact_id = existing["id"]
            next_version = existing["latest_version"] + 1
        else:
            artifact_id = str(_uuid_mod.uuid4())[:12]
            ChatDB.create_artifact(artifact_id, session_id, agent_id, name, path, artifact_type, artifact_role)
            next_version = 1

        ChatDB.add_artifact_version(artifact_id, next_version, content, size, None, action)
        return (artifact_id, next_version, artifact_type)
    except Exception as e:
        print(f"  [WARN] artifact registration: {e}", flush=True)
        return None


# --- Centralized File-Write Pipeline ---

def _after_file_write(path: str, action: str = "created", agent_id: str = ""):
    """Centralized post-file-write pipeline. Called from tool_write_file and tool_edit_file.
    Replaces scattered _maybe_qmd_reindex(), _extract_entities(), file_created calls."""
    # Invalidate any cached read for this file so the model's next read in this
    # session fetches the just-written content instead of a stale stub.
    _read_doc_cache_invalidate(path)
    is_artifact = _is_artifact_path(path)

    # MemPalace migration: QMD reindex + entity extraction + KG update removed.
    # Memory layer is now mempalace MCP — agents file content via
    # mempalace_add_drawer themselves; QMD/KG are no longer in the picture.

    # 3. File/artifact event emission (for UI)
    ecb = getattr(_thread_local, 'event_callback', None)
    if ecb:
        try:
            if is_artifact:
                art_result = _register_artifact_version(path, action, agent_id)
                if art_result:
                    art_id, art_ver, art_type = art_result
                    ecb("artifact_updated", {
                        "path": path,
                        "name": os.path.basename(path),
                        "size": os.path.getsize(path),
                        "action": action,
                        "artifact_id": art_id,
                        "artifact_version": art_ver,
                        "artifact_type": art_type,
                    })
                else:
                    # Fallback to regular file event if artifact registration failed
                    ecb("file_created", {
                        "path": path,
                        "name": os.path.basename(path),
                        "size": os.path.getsize(path),
                        "action": action,
                    })
            else:
                ecb("file_created", {
                    "path": path,
                    "name": os.path.basename(path),
                    "size": os.path.getsize(path),
                    "action": action,
                })
        except Exception:
            pass

    # 4. Update code graph if source file
    _maybe_update_code_graph(path)

    # 5. External after_file_write hooks
    if agent_id:
        try:
            runner = _get_hook_runner(agent_id)
            runner.run_after_file_write(path, action)
        except Exception:
            pass


_DATA_URI_RE = re.compile(r'data:([a-zA-Z0-9][a-zA-Z0-9!#$&\-^_]*/[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*);base64,([A-Za-z0-9+/=]+)')

_MIME_TO_EXT = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
    "audio/wav": "wav", "audio/wave": "wav", "audio/x-wav": "wav",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
    "audio/flac": "flac", "audio/aac": "aac",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    "video/x-msvideo": "avi", "video/mpeg": "mpeg",
    "application/pdf": "pdf", "application/zip": "zip",
}

def _mime_to_ext(mime: str) -> str:
    return _MIME_TO_EXT.get(mime) or mime.split("/")[-1].split("+")[0].split(";")[0]


def _extract_and_save_mcp_blobs_direct(raw_result: str, session_id: str) -> str:
    """Extract any binary blobs from a tool result and save as artifacts.

    Handles _mcp_images/_mcp_blobs structured blocks and data:<mime>;base64,...
    URIs anywhere in the result text. Works for all MIME types.
    Returns cleaned result string.
    """
    import base64 as _b64
    try:
        parsed = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return raw_result
    if not isinstance(parsed, dict):
        return raw_result

    _sid = session_id or getattr(_thread_local, 'current_session_id', None) or ""
    _agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    _agent_id = _agent.agent_id if _agent else "main"
    if not _sid or not _agent:
        return raw_result

    folder = _get_artifact_session_folder(_sid)
    artifact_dir = os.path.join(AGENTS_DIR, _agent_id, "artifacts", folder)
    os.makedirs(artifact_dir, exist_ok=True)

    counter = [0]
    saved_paths = []

    def _save(mime: str, b64_data: str) -> str | None:
        try:
            ext = _mime_to_ext(mime)
            raw_bytes = _b64.b64decode(b64_data)
            counter[0] += 1
            suffix = f"_{counter[0]}" if counter[0] > 1 else ""
            hint = "mcp_screenshot" if mime.startswith("image/") else "mcp_output"
            fname = f"{hint}{suffix}.{ext}"
            fpath = os.path.join(artifact_dir, fname)
            with open(fpath, "wb") as f:
                f.write(raw_bytes)
            _after_file_write(fpath, "created", _agent_id)
            return fpath
        except Exception as e:
            return f"(blob save failed: {e})"

    # Structured blob blocks from MCP clients
    for key in ("_mcp_images", "_mcp_blobs"):
        for blob in parsed.pop(key, []):
            mime = blob.get("mimeType") or blob.get("media_type") or "application/octet-stream"
            p = _save(mime, blob.get("data", ""))
            if p:
                saved_paths.append(p)

    # data:<mime>;base64,<data> URIs anywhere in result text
    result_text = parsed.get("result", "")
    if ";base64," in result_text:
        def _replace(m: re.Match) -> str:
            p = _save(m.group(1), m.group(2))
            if p:
                saved_paths.append(p)
                return f"[saved as artifact: {os.path.basename(p)}]"
            return m.group(0)
        parsed["result"] = _DATA_URI_RE.sub(_replace, result_text)

    if saved_paths:
        names = ", ".join(os.path.basename(p) for p in saved_paths)
        parsed["result"] = (parsed.get("result") or "").rstrip() + f"\n\nSaved as artifact: {names}"

    return json.dumps(parsed)


# --- Concurrent Tool Execution (Phase 5) ---
# Tools classified as concurrency-safe (read-only, no side effects)
_CONCURRENT_SAFE_TOOLS = {
    "read_file", "list_directory", "search_files", "read_document",
    "memory_recall", "memory_shared",
    "exa_search", "web_fetch",
    "code_graph_query",
    "schedule_list", "schedule_history", "list_nodes", "task_status",
    "context_search", "context_detail", "context_recall",
    "git_command",  # read-only git commands are safe (status, log, diff, blame)
}


def _execute_tools_batch(tool_calls: list[dict], event_callback=None, tool_round: int = 0) -> list[dict]:
    """Execute a batch of tool calls with concurrent-safe parallelism.

    Partitions tool calls into batches:
    - Consecutive concurrent-safe tools run in parallel (ThreadPoolExecutor)
    - Unsafe tools run sequentially, one at a time
    Results are returned in the original order.

    Each tool_call dict: {"id": str, "name": str, "input": dict}
    Returns list of {"tool_use_id": str, "result": str} in order.
    """
    if not tool_calls:
        return []
    # Surface the round number to tools that want to attribute "first read"
    # turns in cache stubs (read_document / read_file).
    try:
        _thread_local.tool_round = tool_round
    except Exception:
        pass

    # Partition into batches: [(is_concurrent, [tool_calls...])]
    batches = []
    current_batch = []
    current_is_concurrent = None

    for tc in tool_calls:
        is_safe = tc["name"] in _CONCURRENT_SAFE_TOOLS
        # git_command: only safe for read-only subcommands
        if tc["name"] == "git_command":
            subcmd = tc["input"].get("subcommand", "")
            if subcmd not in ("status", "log", "diff", "blame", "show", "branch", "tag", "remote"):
                is_safe = False

        if current_is_concurrent is None:
            current_is_concurrent = is_safe
        elif is_safe != current_is_concurrent:
            batches.append((current_is_concurrent, current_batch))
            current_batch = []
            current_is_concurrent = is_safe
        current_batch.append(tc)

    if current_batch:
        batches.append((current_is_concurrent, current_batch))

    # Execute batches
    results = []
    for is_concurrent, batch in batches:
        if is_concurrent and len(batch) > 1:
            # Parallel execution
            from concurrent.futures import ThreadPoolExecutor, as_completed
            # Snapshot parent thread-local context so worker threads inherit it.
            # Without this, session-scoped dedup and MCP routing silently start
            # fresh per thread and miss duplicate calls / agent context.
            _parent_ctx = {
                "current_session_id": getattr(_thread_local, 'current_session_id', None),
                "session_id": getattr(_thread_local, 'session_id', None),
                "current_agent": getattr(_thread_local, 'current_agent', None),
                "mcp_manager": getattr(_thread_local, 'mcp_manager', None),
                "current_user_id": getattr(_thread_local, 'current_user_id', None),
            }
            futures = {}
            with ThreadPoolExecutor(max_workers=min(5, len(batch))) as executor:
                for tc in batch:
                    if event_callback:
                        event_callback("tool_call", {"name": tc["name"], "args": tc["input"], "tool_round": tool_round})
                    _display_tool_call(tc["name"], tc["input"])
                    future = executor.submit(_execute_tool_in_thread, tc["name"], tc["input"],
                                             tc["id"], event_callback, _parent_ctx)
                    futures[future] = tc

                # Collect results preserving order
                result_map = {}
                for future in as_completed(futures):
                    tc = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        result = _err(f"Tool execution error: {e}")
                    result_map[tc["id"]] = result
                    _display_tool_result(tc["name"], result)
                    if event_callback:
                        event_callback("tool_result", {"name": tc["name"], "result": result, "tool_round": tool_round})

            # Append in original order
            for tc in batch:
                results.append({"tool_use_id": tc["id"], "result": result_map[tc["id"]]})
        else:
            # Sequential execution
            for tc in batch:
                _display_tool_call(tc["name"], tc["input"])
                if event_callback:
                    event_callback("tool_call", {"name": tc["name"], "args": tc["input"], "tool_round": tool_round})
                _thread_local.event_callback = event_callback
                _thread_local.tool_use_id = tc["id"]
                try:
                    result = _execute_tool(tc["name"], tc["input"])
                finally:
                    _thread_local.event_callback = None
                    _thread_local.tool_use_id = None
                _display_tool_result(tc["name"], result)
                if event_callback:
                    event_callback("tool_result", {"name": tc["name"], "result": result, "tool_round": tool_round})
                results.append({"tool_use_id": tc["id"], "result": result})
    return results


def _execute_tool_in_thread(name: str, args: dict, tool_id: str, event_callback,
                             parent_ctx: dict | None = None) -> str:
    """Execute a single tool in a worker thread with proper thread-local setup.

    parent_ctx carries thread-local state that must travel into the worker thread
    so downstream code (session-scoped dedup, worker idempotency keys, MCP routing,
    agent context) sees the same environment as the calling agentic-loop thread.
    """
    ctx = parent_ctx or {}
    _thread_local.event_callback = event_callback
    _thread_local.tool_use_id = tool_id
    # Propagate parent session/agent/mcp context so session-scoped state
    # (dedup, worker keys) isn't silently duplicated per thread.
    _thread_local.current_session_id = ctx.get("current_session_id")
    _thread_local.session_id = ctx.get("session_id")
    _thread_local.current_agent = ctx.get("current_agent")
    _thread_local.mcp_manager = ctx.get("mcp_manager")
    _thread_local.current_user_id = ctx.get("current_user_id")
    try:
        return _execute_tool(name, args)
    finally:
        _thread_local.event_callback = None
        _thread_local.tool_use_id = None
        _thread_local.current_session_id = None
        _thread_local.session_id = None
        _thread_local.current_agent = None
        _thread_local.mcp_manager = None
        _thread_local.current_user_id = None


def _execute_tool(name: str, args: dict) -> str:
    """Tool dispatch entry point. Routes through the worker subagent wrapper
    for heavy tools, direct execution for light tools. (v8.0.0+)"""
    from execution import route_tool_execution
    return route_tool_execution(name, args, _execute_tool_inner)


def _execute_tool_inner(name: str, args: dict) -> str:
    """Execute a tool directly — no worker routing. Called by the router
    and by workers themselves."""
    # --- Built-in pre-hooks ---
    # Plan mode: block non-readonly tools
    if getattr(_thread_local, 'plan_mode', False):
        if name == "memory_shared" and args.get("action") == "store":
            return _err("Blocked in plan mode. Describe what you would do instead.")
        if name not in READONLY_TOOLS:
            return _err("Blocked in plan mode. Describe what you would do instead.")
    # Workflow tool restriction (was dead code — now enforced)
    workflow_tools = getattr(_thread_local, 'workflow_allowed_tools', None)
    if workflow_tools is not None and name not in workflow_tools:
        return _err(f"Tool '{name}' not allowed in this workflow stage.")
    # Dedup check
    dedup = _check_tool_dedup(name, args)
    if dedup:
        return dedup

    # --- External pre-hooks ---
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    agent_id = agent.agent_id if agent else "main"
    session_id = getattr(_thread_local, 'session_id', None)
    try:
        runner = _get_hook_runner(agent_id)
        blocked = runner.run_pre_hooks(name, args)
        if blocked:
            return blocked
    except Exception:
        pass

    # --- Tracing: start span ---
    tool_span = None
    if _trace_manager:
        parent_span = getattr(_thread_local, 'current_trace_span', None)
        trace_id = parent_span["trace_id"] if parent_span else getattr(_thread_local, 'trace_id', None)
        parent_id = parent_span["id"] if parent_span else None
        tool_span = _trace_manager.start_span(
            "tool_call", name, agent=agent_id, model="",
            parent_id=parent_id, trace_id=trace_id, session_id=session_id,
        )

    tool_start = time.time()
    result_status = "success"
    result = None
    try:
        # Client execution mode: proxy web tools through connected browser
        if _get_execution_mode() == "client" and name in _get_client_proxy_tools():
            _cb = getattr(_thread_local, 'event_callback', None)
            _sid = getattr(_thread_local, 'current_session_id', None) or ""
            _tcid = getattr(_thread_local, 'tool_use_id', None) or name
            if _cb and _sid:
                channel = get_proxy_channel(_sid)
                channel.request_tool_result(_tcid)
                _cb("proxy_tool", {
                    "tool_call_id": _tcid,
                    "name": name,
                    "args": args,
                })
                ew = getattr(_thread_local, '_escape_watcher', None)
                result = channel.get_tool_result(_tcid, escape_watcher=ew)
            else:
                result = _err(f"Client execution mode requires an active browser connection for {name}")
        # Check MCP tools first (prefer thread-local for concurrent requests)
        elif (mcp := (getattr(_thread_local, 'mcp_manager', None) or _mcp_manager)) and mcp.is_mcp_tool(name):
            result = mcp.call_tool(name, args)
        else:
            fn = TOOL_DISPATCH.get(name)
            if fn:
                result = fn(args)
            else:
                result = _err(f"Unknown tool: {name}")
    except Exception as e:
        result_status = "error"
        result = _err(str(e))
    finally:
        duration_ms = int((time.time() - tool_start) * 1000)
        # --- Built-in post-hooks ---
        # Determine audit status from result
        if result and result_status == "success":
            try:
                rdata = json.loads(result) if result else {}
                if isinstance(rdata, dict) and rdata.get("error"):
                    result_status = "error"
            except (json.JSONDecodeError, TypeError):
                pass

        # End trace span
        if _trace_manager and tool_span:
            _trace_manager.end_span(tool_span, status=result_status,
                                     result_summary=(result or "")[:200])

        # Audit log
        if _audit_log and result is not None:
            action_type = _AUDIT_ACTION_MAP.get(name, "mcp_tool_call" if name.startswith("mcp_") else "unknown")
            try:
                _audit_log.log_action(
                    agent=agent_id,
                    action_type=action_type,
                    tool_name=name,
                    args_summary=_audit_summarize_args(name, args),
                    result_summary=_audit_summarize_result(name, result),
                    result_status=result_status,
                    duration_ms=duration_ms,
                    session_id=session_id,
                    source=getattr(_thread_local, 'audit_source', 'chat'),
                )
            except Exception:
                pass

        # --- External post-hooks ---
        if result is not None:
            try:
                runner = _get_hook_runner(agent_id)
                result = runner.run_post_hooks(name, args, result)
            except Exception:
                pass

    return result


MAX_TOOL_ROUNDS = 15  # Maximum number of tool-use round trips before forcing a text response
MAX_TOOL_ROUNDS_PROXY = 8  # Tighter limit for CLIProxyAPI (shares personal OAuth quota)
MAX_OUTPUT_RECOVERY_LIMIT = 3  # Max resume attempts when model hits output token limit
# Diminishing-returns guard: after MIN_ROUND rounds, if the last WINDOW completion-token deltas
# are all below THRESHOLD, stop — the model is plateauing in the tool loop.
DIMINISHING_MIN_ROUND = 3
DIMINISHING_WINDOW = 2
DIMINISHING_THRESHOLD_TOKENS = 500
_MAX_OUTPUT_RESUME_MSG = (
    "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
    "Pick up mid-thought, mid-sentence, mid-code exactly where you stopped."
)

# Loop transition reasons (for debugging / state machine tracking)
# Modeled on Claude Code's query.ts transition types
TRANSITION_NEXT_TURN = "next_turn"
TRANSITION_MAX_OUTPUT_RECOVERY = "max_output_recovery"
TRANSITION_REACTIVE_COMPACT = "reactive_compact_retry"
TRANSITION_COMPLETED = "completed"
TRANSITION_MAX_TURNS = "max_turns"
TRANSITION_ABORTED = "aborted"


# --- Middleware Pipeline (Phase 9) ---
# Composable pre-turn middleware for the direct agentic loop.
# Each middleware: fn(messages, tool_round, event_callback, **ctx) -> (messages, should_continue)

def _middleware_cancel_check(messages, tool_round, event_callback, **ctx):
    """Check cancel token before each turn."""
    watcher = ctx.get("escape_watcher")
    if watcher and watcher.cancelled:
        raise TaskCancelled()
    return messages, True

def _middleware_tool_result_budget(messages, tool_round, event_callback, **ctx):
    """Persist oversized tool results to disk (Layer 1)."""
    _apply_tool_result_budget(messages, session_id=ctx.get("session_id"))
    return messages, True

def _middleware_microcompact(messages, tool_round, event_callback, **ctx):
    """Clear stale tool results every 2 rounds (Layer 2)."""
    if tool_round > 0 and tool_round % 2 == 0:
        messages, freed = _microcompact(messages, keep_recent=5)
    return messages, True

def _middleware_compress_old(messages, tool_round, event_callback, **ctx):
    """Compress old tool results when accumulated budget exceeded (Layer 3)."""
    if tool_round > 0:
        accumulated = getattr(_thread_local, '_tool_results_tokens', 0)
        _limit = _get_agent_limits().get("tool_results_total_tokens", MAX_TOOL_RESULTS_TOKENS)
        if accumulated > _limit:
            _compress_old_tool_results(messages, keep_recent=4)
    return messages, True

def _middleware_compaction(messages, tool_round, event_callback, **ctx):
    """Full LLM summarization every 3 rounds (Layer 4)."""
    if tool_round > 0 and tool_round % 3 == 0:
        model = ctx.get("model", "")
        api_key = ctx.get("api_key", "")
        base_url = ctx.get("base_url", "")
        session_id = ctx.get("session_id", "")
        max_ctx = get_model_max_context(model)
        messages, compacted = _check_and_compact(
            messages, model, api_key, base_url,
            max_tokens=max_ctx, session_id=session_id,
        )
        if compacted and event_callback:
            event_callback("compacted", {})
    return messages, True

# Tools that python_exec can replace when chained
_PYEXEC_CONSOLIDATABLE = {
    "read_file", "list_directory", "search_files",
    "read_document", "write_document", "edit_document",
    "write_file", "edit_file",
}

def _middleware_pyexec_hint(messages, tool_round, event_callback, **ctx):
    """Suggest python_exec when the model chains 3+ file/doc tool calls."""
    if tool_round < 1:
        return messages, True
    # Only if agent has code_exec enabled
    allowed = _get_agent_tool_names()
    if allowed is not None and "python_exec" not in allowed:
        return messages, True
    # Don't hint if already using python_exec this session
    if getattr(_thread_local, '_pyexec_hint_sent', False):
        return messages, True

    # Count consolidatable tool calls in the current turn (all rounds since last user message)
    consolidatable_count = 0
    for msg in reversed(messages):
        if msg.get("role") == "user" and "tool_call_id" not in msg and "[Efficiency hint" not in msg.get("content", ""):
            break
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                name = tc.get("function", {}).get("name", "")
                if name in _PYEXEC_CONSOLIDATABLE:
                    consolidatable_count += 1

    if consolidatable_count >= 3:
        _thread_local._pyexec_hint_sent = True
        hint = (
            "[Efficiency hint: You made multiple file/document tool calls that could be consolidated "
            "into a single python_exec call. For multi-file reads, data processing, or bulk operations, "
            "write one Python script instead of chaining tool calls — each tool round re-sends the full "
            "context to the LLM. Use python_exec for the remaining work if applicable.]"
        )
        messages.append({"role": "user", "content": hint})
    return messages, True

# Ordered middleware pipeline — runs before each tool-loop iteration
_MIDDLEWARE_PIPELINE = [
    _middleware_cancel_check,
    _middleware_tool_result_budget,
    _middleware_pyexec_hint,
    _middleware_microcompact,
    _middleware_compress_old,
    _middleware_compaction,
]

def _run_middleware(messages, tool_round, event_callback, **ctx):
    """Run the pre-turn middleware pipeline. Returns (messages, should_continue)."""
    for mw in _MIDDLEWARE_PIPELINE:
        messages, should_continue = mw(messages, tool_round, event_callback, **ctx)
        if not should_continue:
            return messages, False
    return messages, True
MAX_TOOL_RESULT_CHARS = 30000  # ~7,500 tokens — truncate individual tool results beyond this
MAX_TOOL_RESULTS_TOKENS = 50000  # Cap accumulated tool results per turn before compressing old ones

# Per-agent runtime limits (overridable via agent.json "limits" block)
AGENT_LIMITS_DEFAULTS = {
    "max_tool_rounds": MAX_TOOL_ROUNDS,
    "tool_result_char_limit": MAX_TOOL_RESULT_CHARS,
    "tool_results_total_tokens": MAX_TOOL_RESULTS_TOKENS,
    "context_safety_ratio": 0.95,
}


def _get_agent_limits(agent_id: str | None = None) -> dict:
    """Get runtime limits for an agent, merged with defaults.

    Precedence (lowest → highest):
      AGENT_LIMITS_DEFAULTS < model profile overlay < agent limits
    """
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    result = dict(AGENT_LIMITS_DEFAULTS)

    # Overlay model profile's limits fragment if a model is bound
    mid = getattr(_thread_local, '_current_model', None)
    if mid:
        prof = resolve_profile_limits(mid)
        for k, v in prof.items():
            if k in result:
                result[k] = v

    if not agent:
        return result
    cfg = agent.config.get("limits", {})
    if isinstance(cfg, dict):
        for k in AGENT_LIMITS_DEFAULTS:
            if k in cfg and cfg[k] is not None:
                result[k] = cfg[k]
    return result

# Base64 image data pattern (matches "data": "...long base64..." in JSON)
_BASE64_DATA_RE = re.compile(r'"data"\s*:\s*"[A-Za-z0-9+/=]{500,}"')
# Raw base64 strings > 1000 chars inside quotes
_BASE64_RAW_RE = re.compile(r'(?<=")[A-Za-z0-9+/=]{1000,}(?=")')


def _sanitize_tool_result(name: str, result: str) -> str:
    """Strip base64 image data and enforce size limits on tool results.

    Applied before appending tool results to messages so that large blobs
    (especially MCP puppeteer screenshots) don't snowball the context on
    subsequent API calls.
    """
    # MCP image results carry _mcp_images — preserve the base64 there so the
    # caller can forward them as multimodal content blocks to the model.
    # Only strip stray base64 blobs outside that key.
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "_mcp_images" in parsed:
            # Sanitize only the text portion; leave _mcp_images intact
            text_part = json.dumps({"result": parsed.get("result", "")})
            text_part = _BASE64_DATA_RE.sub('"data": "[base64 image removed — already processed]"', text_part)
            text_part = _BASE64_RAW_RE.sub('[base64 data removed]', text_part)
            parsed["result"] = json.loads(text_part).get("result", "")
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    # Replace base64 image blobs with placeholder
    result = _BASE64_DATA_RE.sub('"data": "[base64 image removed — already processed]"', result)
    result = _BASE64_RAW_RE.sub('[base64 data removed]', result)

    # Truncate oversized results
    _char_limit = _get_agent_limits().get("tool_result_char_limit", MAX_TOOL_RESULT_CHARS)
    if len(result) > _char_limit:
        result = result[:_char_limit] + \
            f"\n\n[Result truncated from {len(result):,} to {_char_limit:,} chars]"
    return result


def _compress_old_tool_results(messages: list[dict], keep_recent: int = 4):
    """Compress tool results in older messages to free context budget.

    Walks messages backwards, skipping the most recent `keep_recent` tool-result
    messages, and truncates older tool results to a short summary.
    """
    # Find indices of tool-result messages (Anthropic: user with tool_result blocks, OpenAI: role=tool)
    tool_result_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_result_indices.append(i)
        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in msg["content"]):
                tool_result_indices.append(i)

    # Skip the most recent ones
    to_compress = tool_result_indices[:-keep_recent] if len(tool_result_indices) > keep_recent else []

    for idx in to_compress:
        msg = messages[idx]
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > 500:
                msg["content"] = content[:200] + "\n[...compressed...]"
        elif isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content", "")
                    if isinstance(content, str) and len(content) > 500:
                        block["content"] = content[:200] + "\n[...compressed...]"


# --- Tool Result Budget (Phase 3): Persist large results to disk ---

TOOL_RESULT_BUDGET_THRESHOLD = 50000  # chars — persist results larger than this
TOOL_RESULT_PREVIEW_SIZE = 2000  # chars — preview kept in context

def _apply_tool_result_budget(messages: list[dict], session_id: str | None = None,
                               agent_id: str | None = None) -> int:
    """Persist oversized tool results to disk and replace with truncated previews.

    Modeled on Claude Code's applyToolResultBudget (toolResultStorage.ts).
    Returns the number of results persisted.
    """
    if not session_id:
        session_id = getattr(_thread_local, 'current_session_id', None) or ""
    if not agent_id:
        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
        agent_id = agent.agent_id if agent else "main"

    persisted = 0
    results_dir = os.path.join(AGENTS_DIR, agent_id, "artifacts",
                                _get_artifact_session_folder(session_id), "tool-results")

    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > TOOL_RESULT_BUDGET_THRESHOLD:
                tool_id = msg.get("tool_call_id", "unknown")
                filepath = os.path.join(results_dir, f"{tool_id}.txt")
                if not os.path.exists(filepath):
                    os.makedirs(results_dir, exist_ok=True)
                    try:
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)
                    except OSError:
                        continue
                preview = content[:TOOL_RESULT_PREVIEW_SIZE]
                size_kb = len(content) // 1024
                msg["content"] = (
                    f"[Output too large ({size_kb}KB). Full output saved to: {filepath}]\n"
                    f"Preview (first {TOOL_RESULT_PREVIEW_SIZE} chars):\n{preview}\n..."
                )
                persisted += 1

        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                content = block.get("content", "")
                if isinstance(content, str) and len(content) > TOOL_RESULT_BUDGET_THRESHOLD:
                    tool_id = block.get("tool_use_id", "unknown")
                    filepath = os.path.join(results_dir, f"{tool_id}.txt")
                    if not os.path.exists(filepath):
                        os.makedirs(results_dir, exist_ok=True)
                        try:
                            with open(filepath, "w", encoding="utf-8") as f:
                                f.write(content)
                        except OSError:
                            continue
                    preview = content[:TOOL_RESULT_PREVIEW_SIZE]
                    size_kb = len(content) // 1024
                    block["content"] = (
                        f"[Output too large ({size_kb}KB). Full output saved to: {filepath}]\n"
                        f"Preview (first {TOOL_RESULT_PREVIEW_SIZE} chars):\n{preview}\n..."
                    )
                    persisted += 1
    return persisted


# --- Microcompact (Phase 4): Strip stale tool results ---

# Tools whose results become stale quickly and can be safely cleared
_MICROCOMPACT_TOOLS = {
    "read_file", "execute_command", "search_files", "list_directory",
    "web_fetch", "exa_search", "read_document", "code_graph_query",
    "write_file", "edit_file",  # write results are just confirmations
    "python_exec",
}
# Tool call arguments that should be truncated in old assistant messages (model already knows what it wrote)
_COMPACT_TOOL_ARGS = {"python_exec": "code", "execute_command": "command"}
# Tools whose results are context-critical and must never be cleared
_MICROCOMPACT_EXEMPT = {
    "memory_recall", "memory_shared", "delegate_task", "task_status",
    "context_search", "context_detail", "context_recall",
}

def _microcompact(messages: list[dict], keep_recent: int = 5) -> tuple[list[dict], int]:
    """Lightweight compaction: clear old tool results for compactable tools.

    Modeled on Claude Code's microcompactMessages. Unlike _compress_old_tool_results
    which truncates to 200 chars, this completely replaces stale content with a
    minimal marker, and is tool-aware (only clears known-safe tools).

    Returns (messages, estimated_tokens_freed).
    """
    tokens_freed = 0

    # Collect (index, tool_name, content_size) for all tool results
    tool_entries = []  # (msg_index, tool_name, content_size, is_openai)
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            content_size = len(content) if isinstance(content, str) else 0
            # Try to find the tool name from the preceding assistant message
            tool_name = _find_tool_name_for_result(messages, i, msg.get("tool_call_id"))
            tool_entries.append((i, tool_name, content_size, True))
        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content", "")
                    content_size = len(content) if isinstance(content, str) else 0
                    tool_name = _find_tool_name_for_block(messages, block.get("tool_use_id"))
                    tool_entries.append((i, tool_name, content_size, False))

    # Filter to compactable tools only
    compactable = [e for e in tool_entries
                   if e[1] and e[1] in _MICROCOMPACT_TOOLS and e[1] not in _MICROCOMPACT_EXEMPT]

    # Keep the most recent N, clear the rest
    if len(compactable) <= keep_recent:
        return messages, 0

    to_clear = compactable[:-keep_recent]

    cleared_indices = set()
    for idx, tool_name, content_size, is_openai in to_clear:
        if content_size <= 100:  # Already cleared or tiny
            continue
        msg = messages[idx]
        marker = f"[Old {tool_name} result cleared]"
        if is_openai and msg.get("role") == "tool":
            tokens_freed += content_size // 4
            msg["content"] = marker
        elif isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content", "")
                    if isinstance(content, str) and len(content) > 100:
                        tokens_freed += len(content) // 4
                        block["content"] = marker
        cleared_indices.add(idx)

    # Also truncate large tool call arguments in old assistant messages
    # (the model already knows what code it wrote — no need to re-send it)
    for idx in cleared_indices:
        # Find the assistant message that owns this tool result
        tool_call_id = messages[idx].get("tool_call_id")
        if not tool_call_id:
            continue
        for i in range(idx - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if tc.get("id") == tool_call_id:
                        fn_name = tc.get("function", {}).get("name", "")
                        arg_key = _COMPACT_TOOL_ARGS.get(fn_name)
                        if arg_key:
                            try:
                                args = json.loads(tc["function"]["arguments"])
                                if arg_key in args and len(str(args[arg_key])) > 50:
                                    tokens_freed += len(str(args[arg_key])) // 4
                                    args[arg_key] = f"[{len(str(args[arg_key]))} chars cleared]"
                                    tc["function"]["arguments"] = json.dumps(args)
                            except (json.JSONDecodeError, KeyError):
                                pass
                break

    return messages, tokens_freed


def _find_tool_name_for_result(messages: list[dict], tool_msg_idx: int,
                                tool_call_id: str | None) -> str | None:
    """Find the tool name for an OpenAI-style tool result by scanning backwards."""
    if not tool_call_id:
        return None
    for i in range(tool_msg_idx - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("id") == tool_call_id:
                    return tc.get("function", {}).get("name")
    return None


def _find_tool_name_for_block(messages: list[dict], tool_use_id: str | None) -> str | None:
    """Find the tool name for an Anthropic-style tool_result by scanning backwards."""
    if not tool_use_id:
        return None
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("id") == tool_use_id:
                        return block.get("name")
    return None


def _log_call_cost(model: str, tokens_in: int, tokens_out: int,
                   session_id: str | None = None, tool_round: int = 0):
    """Log an LLM call to the cost tracker (if initialized)."""
    if not _cost_tracker:
        return
    if tokens_in == 0 and tokens_out == 0:
        return  # Skip if no usage data available
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    agent_id = agent.agent_id if agent else "main"
    provider = _models_config.get(model, {}).get("provider", "")
    user_id = getattr(_thread_local, 'current_user_id', "") or ""
    try:
        _cost_tracker.log_call(agent_id, session_id, model, provider,
                               tokens_in, tokens_out, tool_round, user_id=user_id)
        # Record in rate limiter too
        if _rate_limiter:
            cost = _compute_cost(model, tokens_in, tokens_out)
            _rate_limiter.record_usage(agent_id, tokens_in + tokens_out, cost)
    except Exception as e:
        logging.warning(f"Cost logging error: {e}")


_system_prompt_cache: dict[str, tuple[str, float]] = {}  # session_id → (prompt, timestamp)
_SYSTEM_PROMPT_CACHE_TTL = 60  # seconds — cache for 1 min (covers tool loop iterations)


def _build_system_prompt(include_memory_summary: bool = True) -> str:
    """Build the full system instruction for the current agent.

    Assembles soul.md, agent context, memory summary, project context,
    team info, skills, scheduler status, MCP servers, tools guide, etc.
    Reads from thread-local state and globals as needed.

    Caches per session to avoid disk I/O on every tool loop iteration.
    Memory summary is only included on _tool_round==0 (controlled by caller).

    Used by both the direct send_message loop and the Agent SDK backend.
    """
    import time as _time
    session_id = getattr(_thread_local, 'current_session_id', None) or ""
    caveman_chat = getattr(_thread_local, 'caveman_chat', 0) or 0
    caveman_system = getattr(_thread_local, 'caveman_system', 0) or 0
    plan_mode = bool(getattr(_thread_local, 'plan_mode', False))
    # Cache key covers ONLY things that change the disk-read prose. Caveman
    # levels and plan mode are deterministic string post-processing applied
    # after the cache lookup — keeping them out of the key means flipping
    # caveman or plan mode mid-session reuses the cached base prose instead
    # of triggering a fresh read of soul.md / skills / scheduler / MCP / etc.
    cache_key = f"{session_id}:{include_memory_summary}"
    cached = _system_prompt_cache.get(cache_key)
    if cached and (_time.time() - cached[1]) < _SYSTEM_PROMPT_CACHE_TTL:
        return _apply_system_prompt_postprocess(
            cached[0], caveman_system, caveman_chat, plan_mode)
    import platform
    from datetime import datetime as _dt

    cwd = os.getcwd()
    os_name = platform.system()

    # Load agent soul and tools guide (prefer thread-local for concurrent requests)
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    agent_id = agent.agent_id if agent else "main"
    soul = agent.soul if agent else ""
    tools_guide = agent.tools_guide if agent else ""

    # If no agent-specific tools guide, try global
    if not tools_guide:
        tools_md_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.md")
        try:
            with open(tools_md_path, "r") as f:
                tools_guide = f.read()
        except (OSError, IOError):
            pass

    # Build agent registry
    agent_registry = build_agent_registry(for_agent_id=agent_id)

    system_instruction = ""
    if soul:
        system_instruction += f"{soul}\n\n"
    # Round timestamp to the hour so the KV-prefix stays stable across
    # warmup → real-request boundaries. Minute-level precision broke prompt
    # cache reuse on every request (~15s extra first-token latency).
    # NOTE: this prompt must stay user-agnostic. Per-user greeting (see
    # `greeting_name` pref) is injected as a one-time preamble on the first
    # user message in send_message — keeping it OUT of the system prompt
    # preserves the warm-pool KV-prefix match across users.
    # When a project is active, drop the "use tools proactively" framing —
    # project chats need restraint (refuse on missing answers) more than
    # initiative. The "proactive" line was pushing Mistral toward fabrication
    # when retrieval came up empty (measured on F1/F2/F3 refusal canaries).
    # Validated 2026-05-01 in eval/results/20260501T110032: brain mean 0.65 → 0.75.
    _project_active = bool(getattr(_thread_local, 'project', None))
    _proactive_line = (
        "Answer based ONLY on what you can verify from tools and source documents. "
        "When tools return nothing relevant, refuse cleanly per project instructions — "
        "do not synthesize from training-data knowledge."
        if _project_active else
        "Use tools proactively to accomplish tasks. You can chain multiple tool calls."
    )
    system_instruction += (
        f"You are agent '{agent_id}' in the Brain Agent system. "
        f"Current date and time: {_dt.now().strftime('%Y-%m-%d %H:00 %Z').strip()}\n"
        f"Current working directory: {cwd}\n"
        f"Operating system: {os_name}\n\n"
        f"{_proactive_line} "
        "For web searches, ALWAYS use exa_search — NEVER use duckduckgo or other search tools. "
        "You have no restrictions beyond what the operating system enforces.\n\n"
        "MEMORY: You have long-term memory via mempalace_query and save_chat_to_memory.\n"
        "- Use mempalace_query to recall past conversations, decisions, user preferences, or previously discussed topics\n"
        "- When the user asks 'do you remember' or references something from the past, search with mempalace_query\n"
        "- When the user says 'remember this' or wants to save the conversation, call save_chat_to_memory\n\n"
    )
    # MemPalace migration: built-in memory summary injection removed.
    # Agents now query mempalace MCP tools (mempalace_status, mempalace_search,
    # mempalace_kg_query, mempalace_diary_read) on their own when they need
    # context.
    tcfg = _get_token_config()
    # Inject project context if a project is active
    active_project = getattr(_thread_local, 'project', None)
    if active_project:
        proj_cfg = ProjectManager.get_project(agent_id, active_project)
        if proj_cfg:
            proj_desc = proj_cfg.get("description", "")
            system_instruction += (
                f"PROJECT CONTEXT: You are working in project '{proj_cfg.get('name', active_project)}'."
            )
            if proj_desc:
                system_instruction += f" {proj_desc}"
            try:
                _kg_enabled_for_prompt = bool(
                    (_load_mempalace_config().get("kg") or {}).get(
                        "enabled", True))
            except Exception:
                _kg_enabled_for_prompt = True
            # The dynamic counts + the on-disk input-folder list moved out
            # of the system prompt into a per-session first-user-turn preamble
            # (see _project_preamble_text() called from send_message round 0).
            # Keeps this block KV-cache-stable across warm-pool sessions and
            # avoids re-billing the index on every fresh project chat.
            system_instruction += (
                "\nPROJECT MEMORY — IMPORTANT:\n"
                "This project has a dedicated, isolated memory store. By default, "
                "`mempalace_query` searches ONLY this knowledge layer (mined "
                "documents) — it does NOT include past chat turns or chat "
                "summaries. This is deliberate: a wrong answer in an earlier "
                "turn must never outrank the underlying source document. If "
                "the user explicitly asks about something said earlier in this "
                "project's chats ('what did we discuss', 'remember when I "
                "said'), call `mempalace_query` again with "
                "`include_chat_history=true` to search the chat layer.\n"
                "BEFORE answering ANY question that could draw on project "
                "knowledge — the user's documents, files in their input folders, "
                "facts they previously told you, project decisions — you MUST "
                "consult the project's memory tools first. Do not guess or rely "
                "on general knowledge when the project may have specifics.\n"
                "\n"
                "**MANDATORY 3-STEP FLOW for every project-knowledge question**:\n"
                "  Step 1: Call `mempalace_query` with the user's question (or "
                "rephrased terms). This returns drawers — short ~800-char "
                "search-result snippets, NOT the full document. Drawer text "
                "is for ranking and pointing you at the right file; it is "
                "NEVER sufficient to answer from on its own.\n"
                "  Step 2: For EACH top-ranked drawer that looks relevant, "
                "call `read_document` to load the FULL converted markdown "
                "of the underlying document. Read enough of it to actually "
                "find the information — formulas, tables, full paragraphs.\n"
                "    **HOW TO CALL READ_DOCUMENT**: every drawer carries a "
                "`read_path` field — absolute path to the curated "
                "`.brain-extracted/<name>.<ext>.md` companion. Pass it "
                "verbatim as `path`. Do NOT pass `source_file=...` (wrong "
                "parameter name; call silently fails). Do NOT construct "
                "paths from basenames + input-folder roots (the file may "
                "live in a subfolder you don't know about).\n"
                "    **Always prefer `read_path` (the .md)** over "
                "`read_path_original` (the binary). The .md is what "
                "Microsoft markitdown produced from the binary — better "
                "table structure, heading hierarchy, OCR — and it's the "
                "exact text the drawer search ranked. Reading the original "
                "PDF re-extracts with a different (worse) extractor; "
                "you'd lose the curation. Use `read_path_original` only as "
                "a fallback when `read_path` errors or is empty.\n"
                "    Worked example: drawer returns "
                "`read_path=\"/private/tmp/kg-real-policies/.../.brain-extracted/"
                "20_2 Informationssicherheit/20_2_1_2_ARL_ISMS "
                "Risikomanagement Handbuch.pdf.md\"`. Call: "
                "`read_document(path=\"<that read_path>\")` verbatim. The "
                "result is the full curated markdown, ready to read end-"
                "to-end for formulas, tables, full sections.\n"
                "  Step 3: Answer ONLY from what you read in Step 2. The "
                "drawer snippet from Step 1 is a pointer, not a quotation. "
                "If `read_document` errors (file not found, wrong path, etc.) "
                "do NOT answer from training data — re-issue the call with "
                "the corrected path, or refuse cleanly per REFUSAL "
                "DISCIPLINE below. **An errored read is a missing answer, "
                "not an invitation to fall back to general knowledge.** "
                "Every measured hallucination on this corpus has been "
                "either: (a) answering from drawer text alone, or (b) "
                "answering from training data after a read_document error.\n"
                "Skipping Step 2 — or proceeding past a Step 2 error — is "
                "the documented cause of wrong answers on this project.\n"
                "\n")
            if _kg_enabled_for_prompt:
                system_instruction += (
                    "OPTIONAL STRUCTURED LOOKUP (knowledge graph):\n"
                    "  `mempalace_kg_search` — structured triple search by "
                    "predicate. Useful for: 'which laws are cited' "
                    "(predicate=cites), 'who is responsible for X' "
                    "(predicate=responsible_party), 'what does X require' "
                    "(predicate=requires), contradiction-detection ('all "
                    "requires-triples about retention'), coverage analysis, "
                    "responsibility matrices.\n"
                    "  `mempalace_kg_query` — entity neighbourhood. Useful "
                    "for 'what do we say about <specific entity>'.\n"
                    "Use these ONLY for structural / list questions where the "
                    "answer is a flat enumeration of facts the KG already "
                    "captures. For narrative or 'how is X calculated' / "
                    "'what does the policy say about X' questions, ALWAYS "
                    "use the 3-step flow above (`mempalace_query` then "
                    "`read_document`) — KG triples are abstractions and lack "
                    "the surrounding context the user needs.\n"
                    "Examples:\n"
                    "  • 'Was steht in der Richtlinie zu X?' → 3-step flow "
                    "(mempalace_query → read_document)\n"
                    "  • 'Wie wird X berechnet?' → 3-step flow (the formula "
                    "lives in the document, not in triples)\n"
                    "  • 'Welche Gesetze werden zitiert?' → "
                    "`mempalace_kg_search(predicate='cites')` is fine\n"
                    "  • 'Wer ist verantwortlich für IT-Security?' → "
                    "`mempalace_kg_search(predicate='responsible_party')`\n"
                    "Do NOT pass a `wing` argument — it is set automatically.\n"
                    "Do NOT pass a `room` argument either, unless you have "
                    "verified the exact room name from a previous successful "
                    "result. Brain's project miner uses room='general' for "
                    "all policy/document content; invented values like "
                    "'document' or 'documentation' silently return zero "
                    "drawers and lead to fabricated 'not in the documents' "
                    "answers. The default (no room filter) searches "
                    "everything in the wing — that is what you want.\n"
                    "Do NOT pass `include_chat_history=true` for "
                    "'how is X calculated' / 'what does the policy say' "
                    "questions — that flag switches the search to the "
                    "PROJECT CHAT wing (past conversations) instead of the "
                    "PROJECT KNOWLEDGE wing (the actual indexed documents). "
                    "Use it ONLY when the user explicitly references prior "
                    "chat ('what did we discuss about X', 'remember when I "
                    "said Y').\n"
                    "\n")
            else:
                system_instruction += (
                    "(The knowledge graph is currently disabled for this "
                    "deployment; only `mempalace_query` + `read_document` "
                    "are available for project knowledge.)\n\n")
            # REFUSAL + PRECISION + CITATION discipline blocks moved into the
            # per-project Instructions field (see DEFAULT_PROJECT_INSTRUCTIONS).
            # They surface again below via the proj_instructions branch — the
            # default kicks in for projects that haven't customised the field,
            # and project owners can edit them in the right-pane Instructions
            # textarea to tune behavior per project.
            #
            # PROJECT INPUT FOLDERS list + path-join example moved into the
            # per-session preamble (see _project_preamble_text). Static
            # binary-companion guidance stays here because it doesn't depend
            # on which folders the project has, only on Brain's pipeline.
            system_instruction += (
                "BINARY DOCUMENTS (PDF, DOCX, PPTX, XLSX, EML, MSG) in project "
                "input folders are auto-converted into companion `.md` files "
                "under the hidden `.brain-extracted/` subdirectory before "
                "mining. So a drawer with `source_file` like "
                "`.brain-extracted/policy.pdf.md` actually came from "
                "`policy.pdf` in the same folder — open the ORIGINAL binary "
                "with read_document for full fidelity (tables, page layout, "
                "complete spreadsheet rows beyond the preview). The `.md` is "
                "a text preview optimised for retrieval and triple "
                "extraction; use it only when you don't need the original "
                "layout.\n\n"
            )
            # Inject project Instructions. When the project hasn't set the
            # field, fall back to DEFAULT_PROJECT_INSTRUCTIONS (the v8.22.0
            # REFUSAL + PRECISION + CITATION disciplines) so every project
            # gets sane defaults out of the box. Owners can override by
            # writing their own text in the right-pane Instructions editor —
            # the override REPLACES the default rather than appending, so a
            # project owner can opt out of the citation requirement entirely
            # by setting their own (shorter or different) instructions.
            proj_instructions = (proj_cfg.get("instructions") or "").strip()
            if not proj_instructions:
                proj_instructions = DEFAULT_PROJECT_INSTRUCTIONS
                _instr_label = (
                    "PROJECT INSTRUCTIONS (Brain default; the project owner "
                    "can replace these by editing the project's Instructions "
                    "field):"
                )
            else:
                _instr_label = "PROJECT INSTRUCTIONS (set by the user for this project):"
            system_instruction += f"{_instr_label}\n{proj_instructions}\n\n"
    # Inject note context for AI-assisted note editing
    note_context = getattr(_thread_local, 'note_context', None)
    if note_context:
        note_path = note_context.replace("note_editing:", "").strip() if note_context.startswith("note_editing:") else ""
        notes_dir = os.path.dirname(note_path) if note_path else ""
        system_instruction += (
            "\n\nNOTE EDITING MODE:\n"
            f"You are helping the user edit a markdown note{' at: ' + note_path if note_path else ''}.\n"
            "The user will provide the current note content in their message.\n"
            "When the user asks you to ADD, EDIT, or MODIFY the note, use the edit_file or write_file tool "
            "to make changes directly to the note file. The editor will auto-reload.\n"
            f"You can also CREATE NEW notes in the same project by writing to: {notes_dir}/<new-name>.md\n"
            "For questions or explanations, respond normally without editing files.\n\n"
        )
    # Inject team context for interactive sessions
    team_info = _get_agent_team_info(agent_id)
    if team_info:
        if team_info["is_head"]:
            peers = [m for m in team_info["members"] if m != agent_id]
            system_instruction += (
                f"TEAM: You are the head of team '{team_info['name']}'. "
                f"Your team members: {', '.join(peers)}\n"
                "Delegate sub-tasks to your team members when appropriate.\n\n"
            )
        else:
            peers = [m for m in team_info["members"] if m != agent_id and m != team_info["head"]]
            system_instruction += f"TEAM: You are a member of team '{team_info['name']}'.\n"
            system_instruction += f"Team head: {team_info['head']}\n"
            if peers:
                system_instruction += f"Team peers: {', '.join(peers)}\n"
            system_instruction += "\n"

    if agent_registry:
        system_instruction += f"\n{agent_registry}\n\n"

    # Build skills registry (names + descriptions only, load on demand)
    _agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    if _agent:
        skills = _agent.list_skills()
        if skills:
            system_instruction += "\nSKILLS AVAILABLE — call use_skill(skill=\"slug\") to load instructions before performing the task:\n"
            for s in skills:
                slug = s.get('slug', s['name'])
                source_tag = f" (from {s['source']})" if s['source'] != agent_id else ""
                display = s['name'] if s['name'] != slug else ""
                label = f"{slug}" + (f" ({display})" if display else "")
                system_instruction += f"  - {label}: {s['description']}{source_tag}\n"
            system_instruction += "\n"

    # Scheduler status
    if _scheduler:
        schedules = [s for s in _scheduler.list_all() if not s["name"].startswith("_memory_summary_")]
        if schedules:
            system_instruction += "\nSCHEDULER — active scheduled tasks:\n"
            for s in schedules:
                status = "active" if s["enabled"] else "paused"
                next_r = s.get("next_run", "")[:16] if s.get("next_run") else "—"
                system_instruction += f"  - {s['name']} [{status}]: {s['task'][:80]} (next: {next_r})\n"
            system_instruction += "Use schedule_list and schedule_history tools to query scheduler state.\n\n"

    # MCP servers (prefer thread-local for concurrent requests)
    mcp_mgr = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    if mcp_mgr and mcp_mgr.clients:
        system_instruction += "\nMCP SERVERS — external tools available via connected servers:\n"
        for srv in mcp_mgr.list_servers():
            tools_list = ", ".join(srv["tools"][:5])
            more = f" +{srv['tool_count']-5}" if srv["tool_count"] > 5 else ""
            system_instruction += f"  - {srv['name']} ({srv['transport']}): {tools_list}{more}\n"
        system_instruction += "MCP tools are prefixed with mcp_<server>_ — use them like any other tool.\n\n"

    # Note about deferred built-in tool groups
    _deferred_groups = [g for g in (tcfg.get("deferred_tool_groups") or []) if g in TOOL_GROUPS]
    if _deferred_groups:
        system_instruction += "DEFERRED TOOLS: These tool groups are available but not loaded. Use tool_search to discover and activate them when needed:\n"
        for _dg in _deferred_groups:
            system_instruction += f"  - {_dg}: {', '.join(sorted(TOOL_GROUPS[_dg]))}\n"
        system_instruction += "\n"

    if tools_guide and tcfg.get("include_tools_guide", True):
        system_instruction += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

    # Cache the BASE prose (no caveman, no plan suffix). Post-processing
    # is applied below so the cached value is reusable across caveman/plan
    # toggles within the same session.
    import time as _time
    _system_prompt_cache[cache_key] = (system_instruction, _time.time())
    # Evict stale entries (keep cache small)
    if len(_system_prompt_cache) > 20:
        cutoff = _time.time() - _SYSTEM_PROMPT_CACHE_TTL
        for k in list(_system_prompt_cache):
            if _system_prompt_cache[k][1] < cutoff:
                del _system_prompt_cache[k]

    return _apply_system_prompt_postprocess(
        system_instruction, caveman_system, caveman_chat, plan_mode)


def _apply_system_prompt_postprocess(base: str, caveman_system: int,
                                      caveman_chat: int,
                                      plan_mode: bool) -> str:
    """Apply caveman compression + plan-mode suffix to a cached base prose.

    Pure string transform; runs in microseconds. Kept out of the cache key
    so a session that flips caveman levels or plan mode mid-stream reuses
    the cached base instead of triggering a fresh disk read.
    """
    out = base
    if caveman_system and caveman_system in CAVEMAN_SYSTEM_PROMPTS:
        out = CAVEMAN_SYSTEM_PROMPTS[caveman_system] + _caveman_compress_text(out, caveman_system)
    if caveman_chat and caveman_chat in CAVEMAN_CHAT_PROMPTS:
        out += CAVEMAN_CHAT_PROMPTS[caveman_chat]
    if plan_mode:
        out += PLAN_MODE_PROMPT
    return out


def _project_preamble_text(agent_id: str, project_name: str) -> str:
    """Build the per-session project preamble injected on the first user
    message. Carries the project's dynamic state — drawer count, attachment
    count, list of input folders, path-join example — that used to live in
    `_build_system_prompt`. Moving it here keeps the system prompt
    project-agnostic in shape (KV-cache stable across project chats and warm
    pool) while still giving the model the concrete absolute paths it needs
    to resolve relative drawer source_files.

    Returns "" when the project doesn't exist or has no useful state to
    report — the preamble is then skipped entirely and no extra block
    appears in the user message.
    """
    try:
        proj_cfg = ProjectManager.get_project(agent_id, project_name)
    except Exception:
        proj_cfg = None
    if not proj_cfg:
        return ""
    input_folders = proj_cfg.get("input_folders") or []
    try:
        attachment_count = int(proj_cfg.get("chunks") or 0)
    except (TypeError, ValueError):
        attachment_count = 0
    try:
        total_drawers = int(
            (proj_cfg.get("sync_status") or {}).get("total_indexed") or 0)
    except (TypeError, ValueError):
        total_drawers = 0
    try:
        total_files = int(
            (proj_cfg.get("sync_status") or {}).get("total_files") or 0)
    except (TypeError, ValueError):
        total_files = 0
    lines: list[str] = []
    # State summary — gives the model a sense of how much is indexed.
    state_bits = []
    if total_drawers:
        state_bits.append(f"{total_drawers} indexed chunks")
    if total_files:
        state_bits.append(f"{total_files} source files")
    if attachment_count:
        state_bits.append(f"{attachment_count} manual attachment(s)")
    if input_folders:
        state_bits.append(f"{len(input_folders)} input folder(s)")
    if state_bits:
        lines.append("Project memory state: " + ", ".join(state_bits) + ".")
    # Folder list with the path-join example. This is the part that
    # genuinely depends on absolute paths the model can't otherwise see.
    folder_lines: list[str] = []
    for entry in input_folders:
        p = (entry or {}).get("path", "").strip()
        if not p:
            continue
        rec = " (recursive)" if entry.get("recursive", True) else " (top-level only)"
        folder_lines.append(f"  • {p}{rec}")
    if folder_lines:
        lines.append("Input folders on disk:")
        lines.extend(folder_lines)
        first_root = (input_folders[0] or {}).get("path", "") or ""
        if first_root:
            lines.append(
                "When a mempalace_query drawer's `source_file` is a relative "
                "path, JOIN it with one of the absolute folder roots above "
                "before calling read_document. Example: source_file "
                f"`screen.py` mined under `{first_root}` becomes "
                f"`{os.path.join(first_root, 'screen.py')}`."
            )
    if not lines:
        return ""
    return "[Project context (this session):\n- " + "\n- ".join(lines) + "]"


def send_message(messages: list[dict], model: str, api_key: str, base_url: str,
                 silent: bool = False,
                 tools: bool = True,
                 escape_watcher: EscapeWatcher | CancelToken | None = None,
                 _tool_round: int = 0,
                 event_callback=None,
                 inference_params: dict | None = None,
                 session_id: str | None = None) -> str | None:
    """Send messages and stream the response.

    If silent=True, collects without printing (for TUI mode).
    If tools=True, includes tool definitions and handles tool-use loops.
    If event_callback is provided, called with (event_type, data) for streaming:
        ("text_delta", {"text": "..."})
        ("tool_call", {"name": "...", "args": {...}})
        ("tool_result", {"name": "...", "result": "..."})
        ("done", {"text": "full response"})
        ("error", {"message": "..."})
    Returns the assistant's full response text on success, None on model-related errors.
    Raises TaskCancelled if escape_watcher detects cancellation.
    """
    # Reset dedup tracker and accumulated token counter at the start of each conversation turn
    if _tool_round == 0:
        reset_tool_dedup()
        _thread_local._tool_results_tokens = 0
        # Mark model as freshly used so the warmup keeper won't re-prefill it
        try:
            mark_model_used(model)
        except Exception:
            pass
        _thread_local._pyexec_hint_sent = False
        _thread_local._max_output_recovery_count = 0
        _thread_local._has_attempted_reactive_compact = False
        _thread_local._escape_watcher = escape_watcher
        _thread_local._round_deltas = []

        # First-turn greeting: prepend the user's preferred name to the first
        # user message of a session. Kept OUT of the system prompt so the
        # warm-pool KV-prefix stays user-agnostic and matches across users.
        # Fires when:
        #   - this is round 0 of the first turn (no assistant message yet)
        #   - we know the user_id (auth on)
        #   - the user has a non-default greeting_name OR a display_name
        # Idempotent: a sentinel marker on the message metadata prevents
        # double-prepending if send_message is retried for the same turn.
        try:
            _has_assistant = any(m.get("role") == "assistant" for m in messages)
        except Exception:
            _has_assistant = True
        if not _has_assistant:
            # Three preamble blocks may attach to the first user message of a
            # session, in this order: project context (B1) → user greeting +
            # comm prefs → long-form auto-maintained user profile. The
            # project block is computed unconditionally (anonymous sessions
            # in projects still need the absolute paths), the user blocks
            # only fire when an authenticated user is on the request.
            _project_pre = ""
            try:
                _proj_name_pre = getattr(_thread_local, 'project', None) or ""
                _agent_pre_obj = getattr(_thread_local, 'current_agent', None)
                _agent_id_pre = getattr(_agent_pre_obj, 'agent_id', "") or ""
                if _proj_name_pre and _agent_id_pre:
                    _project_pre = _project_preamble_text(_agent_id_pre, _proj_name_pre)
            except Exception:
                _project_pre = ""

            _preamble_lines: list[str] = []
            _profile_doc = ""
            _greeting_uid = getattr(_thread_local, 'current_user_id', "") or ""
            if _greeting_uid:
                try:
                    import auth as _auth_mod_local
                    _u = _auth_mod_local.AuthDB.get_user(_greeting_uid)
                except Exception:
                    _u = None
                if _u:
                    _prefs = _u.get("preferences") or {}
                    _greet = (_prefs.get("greeting_name") or "").strip() \
                             or (_u.get("display_name") or "").strip() \
                             or (_u.get("username") or "").strip()
                    _job = (_prefs.get("job_description") or "").strip()
                    _commprefs = (_prefs.get("communication_preferences") or "").strip()
                    # Long-form auto-maintained profile (agents/main/user_profiles/<uid>.md).
                    # Loaded once on round 0; capped at 4KB so it can't blow up the prompt.
                    # The on-disk file is always clean prose. When the chat is in
                    # caveman mode, compress at read-time so the profile preamble
                    # matches the rest of the prompt — single source of truth is
                    # the chat composer's caveman toggle.
                    try:
                        _safe_uid = "".join(c for c in _greeting_uid if c.isalnum() or c in "-_")
                        _profile_path = os.path.join(
                            AGENTS_DIR, "main", "user_profiles", f"{_safe_uid}.md")
                        if os.path.isfile(_profile_path):
                            with open(_profile_path, "r", encoding="utf-8") as _pf:
                                _profile_doc = _pf.read().strip()
                            if len(_profile_doc) > 4096:
                                _profile_doc = _profile_doc[:4096] + "\n…[truncated]"
                    except Exception:
                        _profile_doc = ""
                    if _profile_doc:
                        _pp_cav = getattr(_thread_local, 'caveman_chat', 0) or 0
                        if _pp_cav in (1, 2, 3):
                            _profile_doc = _caveman_compress_text(_profile_doc, _pp_cav)
                    if _greet:
                        _preamble_lines.append(
                            f"You are talking to {_greet}. Address them by "
                            f"this name when natural."
                        )
                    if _job:
                        _preamble_lines.append(f"Their role: {_job}")
                    if _commprefs:
                        _preamble_lines.append(f"How they like to communicate: {_commprefs}")
            if _preamble_lines or _profile_doc or _project_pre:
                for _idx, _m in enumerate(messages):
                    if _m.get("role") != "user":
                        continue
                    if _m.get("_greeting_injected"):
                        break
                    _bits: list[str] = []
                    if _project_pre:
                        _bits.append(_project_pre)
                    if _preamble_lines:
                        _bits.append(
                            "[Context about this user:\n- "
                            + "\n- ".join(_preamble_lines)
                            + "]"
                        )
                    if _profile_doc:
                        # The auto-maintained profile is its own block
                        # so the model can tell user-set comm prefs
                        # apart from inferred long-form context.
                        _bits.append(
                            "[Auto-maintained user profile (from chat "
                            "history; treat as background context, "
                            "not as ground truth for the current "
                            "request):\n" + _profile_doc + "]"
                        )
                    _preamble = "\n\n".join(_bits) + "\n\n"
                    _content = _m.get("content")
                    if isinstance(_content, str):
                        _m["content"] = _preamble + _content
                    elif isinstance(_content, list):
                        _m["content"] = ([{"type": "text", "text": _preamble}]
                                          + _content)
                    _m["_greeting_injected"] = True
                    break

        # GDPR/PII scan on first round only. Assistant/tool rounds reuse the
        # same user content, so re-scanning every round is wasted work.
        _gdpr_cfg = _get_gdpr_scanner_config()
        if _gdpr_cfg.get("enabled", True):
            try:
                _pii_findings = _pii_scan_messages(messages, max_findings=100, cfg=_gdpr_cfg)
            except Exception:
                _pii_findings = []
            if _pii_findings:
                _pii_counts = _pii_summarize(_pii_findings)
                _pii_summary = ", ".join(f"{n} {lbl.lower()}" + ("" if n == 1 else "s")
                                          for lbl, n in _pii_counts.items())
                _worst = _pii_worst_action(_pii_findings)
                _agent = getattr(_thread_local, 'current_agent', None) or _current_agent
                _agent_id = _agent.agent_id if _agent else "main"
                print(f"[gdpr] session={session_id} agent={_agent_id} "
                      f"findings={len(_pii_findings)} worst={_worst} ({_pii_summary})", flush=True)
                if _gdpr_cfg.get("server_log", True) and _audit_log:
                    try:
                        _audit_log.log_action(
                            agent=_agent_id,
                            action_type="pii_detected",
                            tool_name="gdpr_scanner",
                            args_summary=f"{len(_pii_findings)} findings ({_worst})",
                            result_summary=_pii_summary[:200],
                            result_status="warning" if _worst != "block" else "blocked",
                            session_id=session_id,
                            source="llm_request",
                        )
                    except Exception:
                        pass
                # Block only when (a) at least one finding has action='block'
                # (which already implies server_block=true via _pii_effective_action
                # downgrade) AND (b) the outgoing request targets a cloud provider.
                if _worst == "block":
                    try:
                        _model_is_local = is_model_local(model)
                    except Exception:
                        _model_is_local = False
                    if not _model_is_local:
                        raise RuntimeError(
                            f"[GDPR block] Outgoing message contains high-severity "
                            f"personal data: {_pii_summary}. Select a local model, "
                            f"remove the data, or adjust the category action in "
                            f"Settings → GDPR."
                        )

        # Per-user cost quota gate. Local models bypass; force_local swaps
        # silently to the configured fallback; hard_block refuses with a
        # user-visible RuntimeError. warn_only is purely UI-side.
        if _quota_manager:
            _quota_user = getattr(_thread_local, 'current_user_id', "") or ""
            try:
                _qd, _qr = _quota_manager.check_request(_quota_user, model)
            except Exception:
                _qd, _qr = "allow", ""
            if _qd == "force_local":
                _qcfg = _quota_manager.get_config()
                _fb = (_qcfg.get("default_local_fallback_model") or "").strip()
                # Validate fallback: must exist, be enabled, and resolve as local
                _fb_ok = False
                if _fb and _fb in _models_config and _models_config.get(_fb, {}).get("enabled", True):
                    try:
                        _fb_ok = is_model_local(_fb)
                    except Exception:
                        _fb_ok = False
                _agent_qa = getattr(_thread_local, 'current_agent', None) or _current_agent
                _agent_qid = _agent_qa.agent_id if _agent_qa else "main"
                if _fb_ok:
                    print(f"[quota] session={session_id} user={_quota_user} "
                          f"force_local: {model} -> {_fb} ({_qr})", flush=True)
                    if _audit_log:
                        try:
                            _audit_log.log_action(
                                agent=_agent_qid,
                                action_type="quota_force_local",
                                tool_name="quota",
                                args_summary=f"{model} -> {_fb}",
                                result_summary=_qr[:200],
                                result_status="warning",
                                session_id=session_id,
                                source="llm_request",
                            )
                        except Exception:
                            pass
                    model = _fb
                else:
                    if _audit_log:
                        try:
                            _audit_log.log_action(
                                agent=_agent_qid,
                                action_type="quota_blocked",
                                tool_name="quota",
                                args_summary=f"force_local fallback unusable ({_fb or 'none'})",
                                result_summary=_qr[:200],
                                result_status="blocked",
                                session_id=session_id,
                                source="llm_request",
                            )
                        except Exception:
                            pass
                    raise QuotaExceededError(
                        f"[Quota exceeded] {_qr}. Switch to a local model "
                        f"or ask an admin to raise the limit (Settings → Quotas).",
                        level="red", reason=_qr,
                    )
            elif _qd == "block":
                _agent_qa = getattr(_thread_local, 'current_agent', None) or _current_agent
                _agent_qid = _agent_qa.agent_id if _agent_qa else "main"
                if _audit_log:
                    try:
                        _audit_log.log_action(
                            agent=_agent_qid,
                            action_type="quota_blocked",
                            tool_name="quota",
                            args_summary=f"hard_block on {model}",
                            result_summary=_qr[:200],
                            result_status="blocked",
                            session_id=session_id,
                            source="llm_request",
                        )
                    except Exception:
                        pass
                raise QuotaExceededError(
                    f"[Quota exceeded] {_qr}. Ask an admin to raise the limit "
                    f"(Settings → Quotas).",
                    level="red", reason=_qr,
                )

        # Start a request-level trace span for the full conversation turn
        if _trace_manager:
            agent = getattr(_thread_local, 'current_agent', None) or _current_agent
            agent_id = agent.agent_id if agent else "main"
            # Extract message preview for trace name
            msg_preview = ""
            if messages:
                last_msg = messages[-1]
                content = last_msg.get("content", "")
                if isinstance(content, str):
                    msg_preview = content[:60]
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            msg_preview = block.get("text", "")[:60]
                            break
            request_span = _trace_manager.start_span(
                "request", msg_preview or "user message",
                agent=agent_id, model=model, session_id=session_id,
            )
            _thread_local.trace_id = request_span["trace_id"]
            _thread_local.request_trace_span = request_span
            _thread_local.session_id = session_id

    # Start an LLM call span
    llm_span = None
    if _trace_manager:
        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
        agent_id = agent.agent_id if agent else "main"
        parent_span = getattr(_thread_local, 'request_trace_span', None)
        trace_id = parent_span["trace_id"] if parent_span else getattr(_thread_local, 'trace_id', None)
        parent_id = parent_span["id"] if parent_span else None
        llm_span = _trace_manager.start_span(
            "llm_call", f"{model} call (round {_tool_round})",
            agent=agent_id, model=model,
            parent_id=parent_id, trace_id=trace_id, session_id=session_id,
        )
        _thread_local.current_trace_span = llm_span

    # Soft stop after max tool rounds — use thread-local override if set (delegation),
    # then per-agent limits, then CLIProxyAPI tightening, else global default.
    _agent_limits = _get_agent_limits()
    effective_max_rounds = getattr(_thread_local, 'max_tool_rounds', None)
    if not effective_max_rounds:
        if ":8317" in base_url or "cliproxy" in base_url.lower():
            effective_max_rounds = MAX_TOOL_ROUNDS_PROXY
        else:
            effective_max_rounds = _agent_limits["max_tool_rounds"]
    if _tool_round >= effective_max_rounds:
        tools = False
    # Hard stop: terminate the loop entirely at 1.5x the soft cap to prevent runaway recursion
    if _tool_round >= int(effective_max_rounds * 1.5):
        return "[Tool round limit reached — stopping to prevent runaway loop. Check chat for partial progress.]"
    # Diminishing-returns guard: if recent rounds added very little, the model is plateauing.
    _deltas = getattr(_thread_local, '_round_deltas', None) or []
    if (tools
            and _tool_round >= DIMINISHING_MIN_ROUND
            and len(_deltas) >= DIMINISHING_WINDOW
            and all(d < DIMINISHING_THRESHOLD_TOKENS for d in _deltas[-DIMINISHING_WINDOW:])):
        if event_callback:
            event_callback("tool_loop_stop", {
                "reason": "diminishing_returns",
                "tool_round": _tool_round,
                "recent_deltas": _deltas[-DIMINISHING_WINDOW:],
            })
        print(f"[diminishing returns: last {DIMINISHING_WINDOW} rounds added "
              f"{_deltas[-DIMINISHING_WINDOW:]} tokens — stopping tool loop]", flush=True)
        tools = False
    headers = make_headers(api_key)
    endpoint = f"{base_url}/chat/completions"

    # Strip metadata from messages — API providers don't accept extra fields
    # Keep fields required by OpenAI tool call protocol.
    # Also drop internal roles: "thinking" is a transcript artifact we store for UI
    # display, but providers reject the role string.
    _ALLOWED_MSG_KEYS = {"role", "content", "tool_calls", "tool_call_id", "name"}
    _INTERNAL_ROLES = {"thinking"}
    augmented_messages = []
    for msg in messages:
        if msg.get("role") in _INTERNAL_ROLES:
            continue
        clean = {k: v for k, v in msg.items() if k in _ALLOWED_MSG_KEYS}
        augmented_messages.append(clean)
    tcfg = _get_token_config()
    if tools:
        system_instruction = _build_system_prompt(
            include_memory_summary=(_tool_round == 0),
        )
        augmented_messages.insert(0, {"role": "system", "content": system_instruction})

    payload = {
        "model": get_api_model_id(model),
        "max_tokens": get_model_max_output(model),
        "messages": augmented_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # Apply inference parameters (temperature, top_p, thinking, etc.).
    # Always invoke — even with empty params, oMLX thinking-capable models
    # need an explicit enable_thinking=false to suppress the template default.
    provider = _models_config.get(model, {}).get("provider", "")
    _apply_inference_to_payload(payload, inference_params or {}, provider, scoped_model=model)

    if tools:
        mcp_mgr = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
        allowed = _get_agent_tool_names()

        # Deferred tool loading: skip MCP tool schemas when there are many,
        # and let the model discover them via tool_search instead
        defer_mcp = tcfg.get("defer_mcp_tools", "auto")
        discovered_tools = getattr(_thread_local, '_discovered_tools', set())

        all_tools = _filter_tools(TOOL_DEFINITIONS_OPENAI, allowed, is_openai=True)

        # Defer built-in tool groups: remove tools in deferred groups unless discovered
        deferred_groups = set(tcfg.get("deferred_tool_groups") or [])
        if deferred_groups:
            deferred_tool_names = set()
            for dg in deferred_groups:
                deferred_tool_names.update(TOOL_GROUPS.get(dg, set()))
            all_tools = [t for t in all_tools
                         if t["function"]["name"] not in deferred_tool_names
                         or t["function"]["name"] in discovered_tools]

        if mcp_mgr:
            mcp_tools = mcp_mgr.get_tool_definitions_openai()
            mcp_tools = _filter_mcp_tools(mcp_tools, is_openai=True)
            should_defer = _should_defer_mcp(defer_mcp, mcp_tools, model, is_openai=True)
            if should_defer:
                mcp_tools = [t for t in mcp_tools
                             if t.get("function", {}).get("name", "") in discovered_tools]
            all_tools.extend(mcp_tools)
            all_tools.sort(key=lambda t: t.get("function", {}).get("name", ""))

        payload["tools"] = all_tools
        # Parallel tool calls: let the model emit multiple tool calls in one response
        model_cfg = resolve_model_settings(model)
        if model_cfg.get("parallel_tool_calls", True):
            payload["parallel_tool_calls"] = True

    # Emit request snapshot for inspector
    if event_callback:
        # System prompt: from first system message (OpenAI wire format)
        _sys = ""
        _tool_defs = payload.get("tools", [])
        _tool_names = []
        for _td in _tool_defs:
            if isinstance(_td, dict):
                _tn = (_td.get("function", {}) or {}).get("name", "")
                if _tn:
                    _tool_names.append(_tn)
        _hist_msgs = []
        _user_msg = ""
        for _m in payload.get("messages", []):
            if _m.get("role") == "system":
                if not _sys:
                    _c = _m.get("content", "")
                    _sys = _c if isinstance(_c, str) else str(_c)
                continue
            if _m is payload["messages"][-1] and _m.get("role") == "user":
                _c = _m.get("content", "")
                _user_msg = _c if isinstance(_c, str) else str(_c)
            else:
                _c = _m.get("content", "")
                _hist_msgs.append({"role": _m.get("role", ""), "content": _c if isinstance(_c, str) else str(_c)})
        event_callback("request_payload", {
            "tool_round": _tool_round,
            "system_prompt": _sys,
            "system_tokens": len(_sys) // 4,
            "tools_count": len(_tool_defs),
            "tools_tokens": len(json.dumps(_tool_defs)) // 4,
            "tool_names": _tool_names,
            "history": _hist_msgs,
            "history_tokens": sum(len(str(m.get("content", ""))) // 4 for m in _hist_msgs),
            "user_message": _user_msg,
            "user_tokens": len(_user_msg) // 4,
            "total_payload_tokens": len(json.dumps(payload)) // 4,
        })

    # Context safety pre-flight: refuse the request if estimated prompt tokens
    # exceed (max_context * safety_ratio). Prevents provider 400s and runaway bills.
    try:
        _est_prompt_tokens = len(json.dumps(payload)) // 4
        _max_ctx = get_model_max_context(model) or 0
        _safety_ratio = float(_agent_limits.get("context_safety_ratio", 0.95))
        if _max_ctx and _est_prompt_tokens > int(_max_ctx * _safety_ratio):
            raise RuntimeError(
                f"Context would exceed {int(_safety_ratio*100)}% of max_context "
                f"(~{_est_prompt_tokens:,} / {_max_ctx:,} tokens). "
                f"Compact the chat or switch to a larger-context model."
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # estimation failures should never block a request

    # Check for cancellation before making the request
    if escape_watcher and escape_watcher.cancelled:
        raise TaskCancelled()

    # Rate limiter check
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    agent_id = agent.agent_id if agent else "main"
    if _rate_limiter and _tool_round == 0:
        allowed, reason, _usage_info = _rate_limiter.check(agent_id)
        if not allowed:
            raise RuntimeError(reason)

    data = json.dumps(payload).encode("utf-8")

    # Client-hosted local inference: if the session declared it can serve this
    # model's family on the Electron/browser side, transfer execution to the
    # client instead of running the LLM on the server. This is independent of
    # execution_mode — it works in both server mode AND client mode, because
    # the decision is per-request (based on the session's capability handshake)
    # rather than a global server toggle. Tasks, scheduler, and background
    # calls never reach this branch because they don't set
    # client_capabilities on the thread-local.
    _client_caps = getattr(_thread_local, "client_capabilities", None)
    _client_executable, _client_family = is_model_client_executable(_client_caps, model)
    if _client_executable and event_callback and session_id:
        channel = get_proxy_channel(session_id)
        channel.reset()
        event_callback("local_inference_request", {
            "type": "llm_local",
            "model": model,
            "family": _client_family,
            "payload": json.loads(data.decode("utf-8")),
        })
        # Audit-log the transfer so ops can reconstruct where inference ran.
        try:
            _agent_ctx = getattr(_thread_local, "current_agent", None)
            _agent_id = _agent_ctx.agent_id if _agent_ctx else ""
            _user_id = getattr(_thread_local, "current_user_id", "") or ""
            if _audit_log:
                _audit_log.log_action(
                    agent=_agent_id or _user_id or "anonymous",
                    action_type="client_inference",
                    tool_name="send_message",
                    args_summary=f"model={model} family={_client_family}",
                    session_id=session_id,
                    source="chat",
                )
        except Exception:
            pass
        try:
            proxy_iter = channel.wait_for_llm_lines(escape_watcher)
            return _handle_openai_response(
                proxy_iter, payload, messages, model, api_key, base_url,
                silent, tools, headers, endpoint, escape_watcher,
                _tool_round, event_callback, inference_params, session_id)
        except RuntimeError as e:
            # Fallback policy: surface error, do NOT retry server-side. This was
            # an explicit design decision — retrying would double latency on
            # failures and mask real problems with the client's local model.
            error_msg = f"Client local inference failed: {str(e)}"
            print(f"  [client-inference] {error_msg[:200]}", file=sys.stderr, flush=True)
            if event_callback:
                event_callback("error", {"message": error_msg})
            return None

    # Client execution mode: proxy LLM call through connected browser.
    # Skip the proxy for server-local models — the server can reach them directly,
    # so there's no point round-tripping through the client. Saves a hop on every
    # turn that routes to oMLX/CLIProxyAPI/etc in client mode.
    _exec_mode = _get_execution_mode()
    if _exec_mode == "client" and event_callback and session_id and not is_model_local(model):
        channel = get_proxy_channel(session_id)
        channel.reset()
        event_callback("proxy_request", {
            "type": "llm",
            "endpoint": endpoint,
            "headers": headers,
            "payload": json.loads(data.decode("utf-8")),
        })
        try:
            proxy_iter = channel.wait_for_llm_lines(escape_watcher)
            return _handle_openai_response(
                proxy_iter, payload, messages, model, api_key, base_url,
                silent, tools, headers, endpoint, escape_watcher,
                _tool_round, event_callback, inference_params, session_id)
        except RuntimeError as e:
            error_msg = str(e)
            print(f"  [proxy] LLM proxy error: {error_msg[:200]}", file=sys.stderr, flush=True)
            if event_callback:
                event_callback("error", {"message": error_msg})
            return None

    request = urllib.request.Request(
        endpoint, data=data, headers=headers, method="POST",
    )

    _provider_name = _models_config.get(model, {}).get("provider", "") or "default"
    _agent_ctx = getattr(_thread_local, "current_agent", None)
    _agent_id_ctx = _agent_ctx.agent_id if _agent_ctx else None
    _user_id_ctx = getattr(_thread_local, "current_user_id", None)
    _queue_label = "chat" if _tool_round == 0 else f"chat_round_{_tool_round}"
    # Manual enter/exit so the handler can release the slot as soon as the
    # SSE stream drains — tool execution and the recursive send_message then
    # run with the slot freed, so other chats can use the gateway in parallel.
    _queue_cm = _provider_queue.acquire_if(
        _provider_name, label=_queue_label,
        session_id=session_id, agent_id=_agent_id_ctx, user_id=_user_id_ctx,
        model=model, event_callback=event_callback,
        cancel_token=escape_watcher,
    )
    _queue_cm.__enter__()
    _queue_released = [False]
    def _release_queue_slot():
        if _queue_released[0]:
            return
        _queue_released[0] = True
        try:
            _queue_cm.__exit__(None, None, None)
        except Exception:
            pass
    try:
        try:
            with urllib.request.urlopen(request) as response:
                return _handle_openai_response(
                    response, payload, messages, model, api_key, base_url,
                    silent, tools, headers, endpoint, escape_watcher,
                    _tool_round, event_callback, inference_params, session_id,
                    release_slot=_release_queue_slot)
        finally:
            # Safety net: if handler didn't release (exception before stream
            # drained), release here so we never leak the slot.
            _release_queue_slot()

    except urllib.error.HTTPError as e:
        error_msg = f"HTTP Error {e.code}: {e.reason}"
        try:
            error_body = e.read().decode("utf-8")
            error_msg += f" — {error_body[:200]}"
        except:
            pass
        if e.code == 400:
            # Reactive compact recovery (Phase 8): if prompt too long, try compaction
            _prompt_too_long = ("prompt is too long" in error_msg.lower() or
                                "maximum context length" in error_msg.lower() or
                                "too many tokens" in error_msg.lower() or
                                "context_length_exceeded" in error_msg.lower())
            _has_attempted = getattr(_thread_local, '_has_attempted_reactive_compact', False)
            if _prompt_too_long and not _has_attempted and _tool_round > 0:
                _thread_local._has_attempted_reactive_compact = True
                logging.info(f"Prompt too long at round {_tool_round}, attempting reactive compact")
                if event_callback:
                    event_callback("compacting", {"reason": "prompt_too_long"})
                # Layer 1: try microcompact
                messages, mc_freed = _microcompact(messages, keep_recent=3)
                if mc_freed > 0:
                    if event_callback:
                        event_callback("compacted", {"method": "microcompact", "freed": mc_freed})
                    return send_message(messages, model, api_key, base_url,
                                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                                        _tool_round=_tool_round, event_callback=event_callback,
                                        inference_params=inference_params, session_id=session_id)
                # Layer 2: try full LLM compaction
                _sid = session_id or getattr(_thread_local, 'current_session_id', None) or ""
                max_ctx = get_model_max_context(model)
                messages, compacted = _check_and_compact(
                    messages, model, api_key, base_url,
                    max_tokens=max_ctx, session_id=_sid, force=True,
                )
                if compacted:
                    if event_callback:
                        event_callback("compacted", {"method": "reactive_compact"})
                    return send_message(messages, model, api_key, base_url,
                                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                                        _tool_round=_tool_round, event_callback=event_callback,
                                        inference_params=inference_params, session_id=session_id)
            print(error_msg, file=sys.stderr)
            _thread_local._last_send_error = {"code": e.code, "message": error_msg, "permanent": True}
            return None
        print(error_msg, file=sys.stderr)
        # Transient errors: return None to trigger retry/fallback
        _TRANSIENT_CODES = {429, 500, 502, 503, 504, 529}
        if e.code in _TRANSIENT_CODES:
            # Store error info for fallback logic
            _thread_local._last_send_error = {"code": e.code, "message": error_msg}
            return None
        # Permanent errors (401, 403, 404, etc.)
        _thread_local._last_send_error = {"code": e.code, "message": error_msg, "permanent": True}
        if event_callback:
            raise RuntimeError(error_msg)
        sys.exit(1)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        error_msg = f"Connection error: {e}"
        print(error_msg, file=sys.stderr)
        _thread_local._last_send_error = {"code": 0, "message": error_msg}
        return None


def _parse_gemma_tool_calls(text: str) -> tuple[list[dict], str]:
    """Parse gemma4-style tool calls from raw text.

    Format: <|tool_call>call:name{key:<|"|>val<|"|>,...}<tool_call|>
    Returns (list of tool_use dicts, cleaned text with tool calls removed).
    """
    tool_uses = []
    pattern = r'<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>'
    for match in re.finditer(pattern, text):
        name = match.group(1)
        args_raw = match.group(2)
        # Parse key-value pairs: key:<|"|>value<|"|>
        args = {}
        kv_pattern = r'(\w+):<\|"\|>(.*?)<\|"\|>'
        for kv in re.finditer(kv_pattern, args_raw):
            args[kv.group(1)] = kv.group(2)
        tool_uses.append({
            "id": f"gemma_{_uuid.uuid4().hex[:8]}",
            "name": name,
            "input": args,
            "input_json": json.dumps(args),
        })
    cleaned = re.sub(pattern, '', text)
    return tool_uses, cleaned




class _InlineThinkingSplitter:
    """Streaming splitter for reasoning models that emit <think>...</think> in content.

    Feed raw content chunks via feed(chunk) -> returns a list of (kind, text)
    tuples where kind is 'text' or 'thinking' or 'enter' (enters thinking block,
    no text) or 'exit' (exits thinking block). Text outside tags is 'text';
    inside tags is 'thinking'. 'enter'/'exit' are edge markers so callers can
    emit thinking_start / thinking_done events exactly once per block.

    Handles split-at-boundary cases: if a chunk ends with a partial tag like
    '<thi', the partial is held until the next chunk completes it.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self):
        self._in_thinking = False
        self._pending = ""  # carry-over for partial tag matches
        self._ever_opened = False

    def _starts_partial(self, s: str, tag: str) -> bool:
        # True iff s is a non-empty proper prefix of tag (len(s) < len(tag) and s == tag[:len(s)])
        return 0 < len(s) < len(tag) and tag.startswith(s)

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        if not chunk:
            return []
        buf = self._pending + chunk
        self._pending = ""
        out: list[tuple[str, str]] = []
        while buf:
            tag = self._CLOSE if self._in_thinking else self._OPEN
            idx = buf.find(tag)
            if idx >= 0:
                # Emit everything before the tag in current mode
                pre = buf[:idx]
                if pre:
                    out.append(("thinking" if self._in_thinking else "text", pre))
                # Flip mode
                if self._in_thinking:
                    out.append(("exit", ""))
                    self._in_thinking = False
                else:
                    out.append(("enter", ""))
                    self._in_thinking = True
                    self._ever_opened = True
                buf = buf[idx + len(tag):]
                continue
            # No complete tag in buf. Check whether the tail is a partial tag.
            hold_len = 0
            for k in range(1, min(len(tag), len(buf)) + 1):
                if self._starts_partial(buf[-k:], tag):
                    hold_len = k  # keep the longest partial match
            if hold_len:
                emit = buf[:-hold_len]
                self._pending = buf[-hold_len:]
                if emit:
                    out.append(("thinking" if self._in_thinking else "text", emit))
                buf = ""
            else:
                out.append(("thinking" if self._in_thinking else "text", buf))
                buf = ""
        return out

    def flush(self) -> list[tuple[str, str]]:
        """At end-of-stream, any held pending text has no closing tag — flush it
        in the current mode so we don't lose characters."""
        out: list[tuple[str, str]] = []
        if self._pending:
            out.append(("thinking" if self._in_thinking else "text", self._pending))
            self._pending = ""
        if self._in_thinking:
            # Unclosed thinking block — force-close so UI state is sane.
            out.append(("exit", ""))
            self._in_thinking = False
        return out


def _handle_openai_response(response, payload, messages, model, api_key,
                             base_url, silent, tools,
                             headers, endpoint,
                             escape_watcher=None,
                             _tool_round: int = 0,
                             event_callback=None,
                             inference_params: dict | None = None,
                             session_id: str | None = None,
                             release_slot=None) -> str | None:
    """Handle OpenAI SSE response, including tool-use agentic loop.

    `release_slot`: optional zero-arg callable. Invoked exactly once, right
    after the SSE stream completes and before any tool dispatch or recursive
    send_message, so the provider queue slot is freed for other chats while
    local tool execution runs. Idempotent — safe to call multiple times.
    """
    # Wrap release_slot into a one-shot local helper
    _slot_released = [False]
    def _release_once():
        if _slot_released[0] or release_slot is None:
            return
        _slot_released[0] = True
        try:
            release_slot()
        except Exception:
            pass
    collected_text = []
    collected_thinking = []
    tool_calls_map = {}  # index -> {id, name, arguments_str}
    _usage_in = 0
    _usage_out = 0
    _reasoning_tokens = 0  # openai_opaque: reported in usage.completion_tokens_details.reasoning_tokens
    finish_reason = None
    # Thinking format drives how we parse reasoning content out of the stream.
    _tfmt = _models_config.get(model, {}).get("thinking_format", "none")
    _think_splitter = _InlineThinkingSplitter() if _tfmt == "inline_tags" else None
    _thinking_started = False
    # For mistral_blocks: track whether we've opened a thinking block (first non-empty thinking delta).
    _mistral_thinking_open = False

    def _emit_thinking_delta(t: str):
        """Helper: emit thinking_start (first time) + thinking_delta."""
        nonlocal _thinking_started
        if not _thinking_started:
            _thinking_started = True
            if event_callback:
                event_callback("thinking_start", {"tool_round": _tool_round})
        collected_thinking.append(t)
        if event_callback:
            event_callback("thinking_delta", {"text": t, "tool_round": _tool_round})

    def _emit_thinking_done():
        """Helper: emit thinking_done with current round's text + round number.
        Server uses this as the signal to persist a 'thinking' message row so
        reasoning appears inline in its correct chronological position."""
        if event_callback:
            event_callback("thinking_done", {
                "text": "".join(collected_thinking),
                "tool_round": _tool_round,
            })

    def _emit_text_delta(t: str):
        if not silent:
            print(t, end="", flush=True)
        if event_callback:
            event_callback("text_delta", {"text": t})
        collected_text.append(t)

    for line in response:
        if escape_watcher and escape_watcher.cancelled:
            raise TaskCancelled()
        line = line.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        payload_str = line[6:]
        if payload_str == "[DONE]":
            break
        try:
            event = json.loads(payload_str)
            # Extract usage from final chunk (OpenAI stream_options)
            usage = event.get("usage")
            if usage:
                _usage_in = usage.get("prompt_tokens", 0)
                _usage_out = usage.get("completion_tokens", 0)
                ctd = usage.get("completion_tokens_details") or {}
                _reasoning_tokens = int(ctd.get("reasoning_tokens") or 0)
            choices = event.get("choices", [])
            if not choices:
                continue
            # Track finish reason for max_tokens recovery
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr
            delta = choices[0].get("delta") or {}

            # reasoning_field path: reasoning_content is a sibling field on the delta.
            # (oMLX, cliproxyapi Gemini 2.5, DeepSeek-R1 direct). Emits thinking deltas
            # independently of content; caller emits thinking_done on first non-reasoning
            # content delta.
            if _tfmt == "reasoning_field":
                rc = delta.get("reasoning_content")
                if rc:
                    _emit_thinking_delta(_unescape(rc))

            content = delta.get("content")
            if content:
                # mistral_blocks path: content is a list of blocks like
                # [{type: "thinking", thinking: [{type: "text", text: "partial"}]}]
                # or [{type: "text", text: "partial"}].
                if _tfmt == "mistral_blocks" and isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "thinking":
                            _mistral_thinking_open = True
                            for sub in (block.get("thinking") or []):
                                if isinstance(sub, dict):
                                    t = sub.get("text")
                                    if t:
                                        _emit_thinking_delta(_unescape(t))
                        elif btype == "text":
                            if _mistral_thinking_open:
                                _mistral_thinking_open = False
                                if event_callback:
                                    _emit_thinking_done()
                            t = block.get("text")
                            if t:
                                _emit_text_delta(_unescape(t))
                elif isinstance(content, str):
                    content = _unescape(content)
                    if _tfmt == "reasoning_field" and _thinking_started:
                        # First non-empty text chunk closes the reasoning phase.
                        if event_callback:
                            _emit_thinking_done()
                        _thinking_started = False  # don't emit thinking_done twice
                        _emit_text_delta(content)
                    elif _think_splitter is None:
                        _emit_text_delta(content)
                    else:
                        for kind, text in _think_splitter.feed(content):
                            if kind == "enter":
                                if not _thinking_started:
                                    _thinking_started = True
                                    if event_callback:
                                        event_callback("thinking_start", {"tool_round": _tool_round})
                            elif kind == "exit":
                                _emit_thinking_done()
                                _thinking_started = False
                            elif kind == "thinking":
                                collected_thinking.append(text)
                                if event_callback:
                                    event_callback("thinking_delta", {"text": text, "tool_round": _tool_round})
                            elif kind == "text":
                                _emit_text_delta(text)

            # Accumulate tool calls (guard against null — Gemini returns tool_calls: null)
            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": "",
                    }
                if tc.get("id"):
                    tool_calls_map[idx]["id"] = tc["id"]
                if tc.get("function", {}).get("name"):
                    tool_calls_map[idx]["name"] = tc["function"]["name"]
                tool_calls_map[idx]["arguments"] += tc.get("function", {}).get("arguments", "")

        except json.JSONDecodeError:
            pass

    # End-of-stream flush for the thinking splitter: drain any held partial-tag
    # characters and auto-close an unclosed <think> block so UI state stays sane.
    if _think_splitter is not None:
        for kind, text in _think_splitter.flush():
            if kind == "exit":
                _emit_thinking_done()
                _thinking_started = False
            elif kind == "thinking":
                collected_thinking.append(text)
                if event_callback:
                    event_callback("thinking_delta", {"text": text, "tool_round": _tool_round})
            elif kind == "text":
                _emit_text_delta(text)

    # mistral_blocks: close an unclosed thinking block (rare — usually we see a text block that triggers closure).
    if _mistral_thinking_open:
        _mistral_thinking_open = False
        if event_callback:
            _emit_thinking_done()
        _thinking_started = False

    # reasoning_field: if thinking was opened but no non-empty text followed
    # (rare, e.g. model emitted only reasoning and hit max tokens), close it on EOS.
    if _tfmt == "reasoning_field" and _thinking_started:
        if event_callback:
            _emit_thinking_done()
        _thinking_started = False

    # openai_opaque: no thinking text available from the provider, but we know
    # how many tokens were burned on reasoning. Surface as a summary event so the
    # UI can render a "Thought for N tokens" badge.
    if _tfmt == "openai_opaque" and _reasoning_tokens > 0 and event_callback:
        event_callback("thinking_summary", {
            "format": "openai_opaque",
            "reasoning_tokens": _reasoning_tokens,
        })
        # If we saw any thinking content but never emitted the done event
        # (stream truncated mid-block was handled by flush; this covers
        # the case where open fired and close is the natural EOS — already
        # handled by flush's exit event). Defensive.
        if _thinking_started and collected_thinking and event_callback:
            # Ensure UI gets a final done with the full text, idempotent if dup.
            pass  # flush already handled it

    full_text = "".join(collected_text)

    # SSE stream has fully drained — release the provider queue slot so the
    # next chat/delegate can start its LLM call while we run tools locally.
    # Tool execution + the recursive send_message re-acquire on their own.
    _release_once()

    # Parse gemma4-style tool calls from raw text (oMLX doesn't convert these)
    if not tool_calls_map and "<|tool_call>" in full_text:
        parsed, cleaned = _parse_gemma_tool_calls(full_text)
        if parsed:
            for i, tc in enumerate(parsed):
                tool_calls_map[i] = {
                    "id": tc["id"],
                    "name": tc["name"],
                    "arguments": json.dumps(tc["input"]),
                }
            full_text = cleaned.strip()
            collected_text = [full_text] if full_text else []

    # Log cost for this API call
    _log_call_cost(model, _usage_in, _usage_out, session_id, _tool_round)

    # Emit usage event so callers can capture token counts
    if event_callback:
        event_callback("usage", {
            "tokens_in": _usage_in,
            "tokens_out": _usage_out,
            "tool_round": _tool_round,
        })

    # Track per-round completion-token deltas for the diminishing-returns guard (checked at top of next round)
    _deltas_list = getattr(_thread_local, '_round_deltas', None)
    if _deltas_list is not None:
        _deltas_list.append(_usage_out)

    # End LLM trace span (OpenAI handler)
    _llm_span_oai = getattr(_thread_local, 'current_trace_span', None)
    if _trace_manager and _llm_span_oai and _llm_span_oai.get("type") == "llm_call":
        _trace_manager.end_span(_llm_span_oai, status="ok",
                                 tokens_in=_usage_in, tokens_out=_usage_out)

    # Detect truncated tool calls: finish_reason == "length" with incomplete JSON args
    if finish_reason == "length" and tool_calls_map:
        has_truncated = False
        for tc in tool_calls_map.values():
            try:
                json.loads(tc["arguments"])
            except (json.JSONDecodeError, ValueError):
                has_truncated = True
                break
        if has_truncated:
            print(f"[thinking model: truncated tool call detected, discarding incomplete tools]",
                  file=sys.stderr)
            tool_calls_map.clear()

    # Max output token recovery (Phase 2): if model hit output limit, auto-resume
    if finish_reason == "length" and not tool_calls_map:
        recovery_count = getattr(_thread_local, '_max_output_recovery_count', 0)
        if recovery_count < MAX_OUTPUT_RECOVERY_LIMIT:
            _thread_local._max_output_recovery_count = recovery_count + 1
            if event_callback:
                event_callback("max_tokens_recovery", {
                    "attempt": recovery_count + 1,
                    "max_attempts": MAX_OUTPUT_RECOVERY_LIMIT,
                })
            # Thinking models consume max_tokens with invisible reasoning tokens.
            # If visible output is <25% of completion budget, double max_tokens.
            recovery_params = dict(inference_params) if inference_params else {}
            visible_tokens = len(full_text) // 4
            current_max = payload.get("max_tokens", get_model_max_output(model))
            if _usage_out > 0 and visible_tokens < _usage_out * 0.25:
                max_cap = get_model_max_context(model)
                boosted = min(current_max * 2, max_cap)
                if boosted > current_max:
                    recovery_params["max_tokens"] = boosted
                    print(f"[thinking model: boosting max_tokens {current_max} → {boosted}]",
                          file=sys.stderr)
            # Build continuation: assistant partial + resume prompt
            if full_text:
                messages.append({"role": "assistant", "content": full_text})
                messages.append({"role": "user", "content": _MAX_OUTPUT_RESUME_MSG})
                return send_message(messages, model, api_key, base_url,
                                    silent=silent, tools=tools, escape_watcher=escape_watcher,
                                    _tool_round=_tool_round, event_callback=event_callback,
                                    inference_params=recovery_params, session_id=session_id)
        # Recovery exhausted or no text to resume from — inform the user
        current_max = payload.get("max_tokens", get_model_max_output(model))
        hint = (f"Output token limit reached (max_tokens={current_max}) and recovery "
                f"attempts exhausted ({MAX_OUTPUT_RECOVERY_LIMIT}/{MAX_OUTPUT_RECOVERY_LIMIT}). "
                f"This model may be using too many tokens on internal reasoning. "
                f"Try: increase max_output in model settings, use a simpler prompt, "
                f"or switch to a non-thinking model.")
        print(f"[max_tokens exhausted] {hint}", file=sys.stderr)
        if event_callback:
            event_callback("max_tokens_exhausted", {
                "message": hint,
                "max_tokens": current_max,
                "model": model,
            })
        if full_text:
            full_text += f"\n\n⚠️ *{hint}*"
        else:
            full_text = f"⚠️ *{hint}*"
        _thread_local._max_output_recovery_count = 0
        return full_text

    if not tool_calls_map:
        if not silent and full_text:
            print()
        # End request trace span for final response
        _req_span_oai = getattr(_thread_local, 'request_trace_span', None)
        if _trace_manager and _req_span_oai:
            _trace_manager.end_span(_req_span_oai, status="ok",
                                     tokens_in=_usage_in, tokens_out=_usage_out)
            _thread_local.request_trace_span = None
        # Reset recovery counter on successful completion
        _thread_local._max_output_recovery_count = 0
        return full_text

    if full_text:
        print()

    # Build assistant message with tool_calls
    assistant_msg = {"role": "assistant", "content": full_text or None}
    tc_list = []
    for idx in sorted(tool_calls_map.keys()):
        tc = tool_calls_map[idx]
        tc_list.append({
            "id": tc["id"],
            "type": "function",
            "function": {"name": tc["name"], "arguments": tc["arguments"]},
        })
    assistant_msg["tool_calls"] = tc_list
    messages.append(assistant_msg)

    # Execute tools with concurrent-safe parallelism (Phase 5)
    batch_calls = []
    for tc in tc_list:
        try:
            args = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}
        batch_calls.append({"id": tc["id"], "name": tc["function"]["name"], "input": args})

    batch_results = _execute_tools_batch(batch_calls, event_callback=event_callback, tool_round=_tool_round)

    # Immediately compact large tool call arguments (e.g. python_exec code)
    # The model already knows what it wrote — no need to re-send on the next turn
    for tc in assistant_msg.get("tool_calls", []):
        fn_name = tc.get("function", {}).get("name", "")
        arg_key = _COMPACT_TOOL_ARGS.get(fn_name)
        if arg_key:
            try:
                args = json.loads(tc["function"]["arguments"])
                val = str(args.get(arg_key, ""))
                if len(val) > 50:
                    args[arg_key] = f"[{len(val)} chars — see tool result]"
                    tc["function"]["arguments"] = json.dumps(args)
            except (json.JSONDecodeError, KeyError):
                pass

    for br in batch_results:
        sanitized = _sanitize_tool_result(
            next((bc["name"] for bc in batch_calls if bc["id"] == br["tool_use_id"]), ""),
            br["result"],
        )
        # Extract MCP images (both _mcp_images blocks and data: URIs) → artifacts
        sanitized = _extract_and_save_mcp_blobs_direct(sanitized, session_id)

        messages.append({
            "role": "tool",
            "tool_call_id": br["tool_use_id"],
            "content": sanitized,
        })

    # Run middleware pipeline (context management, compaction, cancel check)
    _sid = session_id or getattr(_thread_local, 'current_session_id', None) or ""
    messages, should_continue = _run_middleware(
        messages, _tool_round, event_callback,
        model=model, api_key=api_key, base_url=base_url,
        session_id=_sid, escape_watcher=escape_watcher,
    )

    return send_message(messages, model, api_key, base_url,
                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                        _tool_round=_tool_round + 1, event_callback=event_callback,
                        inference_params=inference_params, session_id=session_id)




def _classify_error_transient(error_info: dict | None) -> bool:
    """Check if the last send error is transient (retryable) vs permanent."""
    if not error_info:
        return True  # Unknown error, assume transient
    if error_info.get("permanent"):
        return False
    code = error_info.get("code", 0)
    # Transient: 429, 500, 502, 503, 504, 529, connection errors (code=0)
    return code in {0, 429, 500, 502, 503, 504, 529}


def _retry_with_backoff(messages, model, api_key, base_url,
                        silent, tools, escape_watcher, event_callback,
                        inference_params, session_id, max_retries=2):
    """Try sending a message with exponential backoff retries for transient errors.

    Returns (result, last_error_info). result is None if all retries failed.
    """
    _thread_local._last_send_error = None
    result = send_message(messages, model, api_key, base_url,
                          silent=silent, tools=tools, escape_watcher=escape_watcher,
                          event_callback=event_callback,
                          inference_params=inference_params,
                          session_id=session_id)
    if result is not None:
        return result, None

    error_info = getattr(_thread_local, '_last_send_error', None)
    # If permanent error, don't retry
    if not _classify_error_transient(error_info):
        return None, error_info

    # Retry with exponential backoff
    for attempt in range(1, max_retries + 1):
        delay = min(1.0 * (2 ** (attempt - 1)), 30.0) + random.uniform(0, 0.5)
        error_msg = (error_info or {}).get("message", "unknown error")
        print(f"  Retrying {model} in {delay:.1f}s (attempt {attempt}/{max_retries}, error: {error_msg})", flush=True)
        if event_callback:
            event_callback("fallback", {
                "status": "retry",
                "model": model,
                "attempt": attempt,
                "max_retries": max_retries,
                "delay": round(delay, 1),
                "reason": error_msg,
            })
        time.sleep(delay)

        # Check for cancellation
        if escape_watcher and escape_watcher.cancelled:
            from brain import TaskCancelled
            raise TaskCancelled()

        _thread_local._last_send_error = None
        result = send_message(messages, model, api_key, base_url,
                              silent=silent, tools=tools, escape_watcher=escape_watcher,
                              event_callback=event_callback,
                              inference_params=inference_params,
                              session_id=session_id)
        if result is not None:
            return result, None

        error_info = getattr(_thread_local, '_last_send_error', None)
        if not _classify_error_transient(error_info):
            break  # Permanent error, stop retrying

    return None, error_info


def send_message_with_fallback(messages: list[dict], model: str, api_key: str,
                               base_url: str,
                               silent: bool = False,
                               tools: bool = True,
                               escape_watcher=None,
                               event_callback=None,
                               provider_resolver=None,
                               inference_params: dict | None = None,
                               purpose: str | None = None,
                               session_id: str | None = None) -> str | None:
    """Send messages with retry + fallback chain.

    Retry logic: transient errors (502, 503, 429, timeout) retry 2x with exponential backoff.
    Permanent errors (400, 401, 404) skip retries and go straight to fallback.
    Fallbacks field in _models_config: ordered list of fallback model IDs.
    If provider_resolver is provided, it's called with (model) -> {api_key, base_url}.
    Emits ("fallback", {...}) events via event_callback for UI display.
    """
    # Track which model actually responded (for done event)
    _thread_local._fallback_model_used = None

    # Snapshot message count before primary attempt — if it fails mid-tool-loop,
    # intermediate messages must be stripped before trying fallback models
    msg_count_original = len(messages)

    # Try primary model with retries
    result, error_info = _retry_with_backoff(
        messages, model, api_key, base_url,
        silent, tools, escape_watcher, event_callback,
        inference_params, session_id, max_retries=2)
    if result is not None:
        return result

    # Strip any intermediate tool-loop messages from failed primary attempt
    if len(messages) > msg_count_original:
        del messages[msg_count_original:]

    primary_error = (error_info or {}).get("message", "unknown error")

    # Build fallback list — use explicit fallbacks from config, then capability-aware auto-fallback
    fallback_models = []
    if _models_config:
        model_cfg = _models_config.get(model, {})
        explicit_fallbacks = model_cfg.get("fallbacks", [])
        if explicit_fallbacks:
            fallback_models = list(explicit_fallbacks)
        else:
            # Auto-build fallback order: same provider first, then by priority
            failed_provider = model_cfg.get("provider", "")
            failed_caps = set(model_cfg.get("capabilities", []))
            candidates = []
            for mid, cfg in _models_config.items():
                if mid == model or not cfg.get("enabled", True):
                    continue
                same_provider = 1 if cfg.get("provider") == failed_provider else 0
                matching_caps = len(failed_caps & set(cfg.get("capabilities", [])))
                priority = cfg.get("priority", 0)
                # Sort key: same provider first, then capability match, then priority
                candidates.append((mid, same_provider, matching_caps, priority))
            candidates.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
            fallback_models = [mid for mid, _, _, _ in candidates]
    else:
        fallback_models = get_available_models(api_key, base_url)

    if not fallback_models:
        msg = f"Error: Model '{model}' is not available and no fallback models found."
        print(msg, file=sys.stderr)
        if event_callback:
            raise RuntimeError(msg)
        sys.exit(1)

    tried_models = {model}
    for fallback_model in fallback_models:
        if fallback_model in tried_models:
            continue
        tried_models.add(fallback_model)

        # Re-resolve provider for fallback model
        fb_api_key, fb_base_url = api_key, base_url
        if provider_resolver:
            try:
                prov = provider_resolver(fallback_model)
                fb_api_key = prov.get("api_key", api_key)
                fb_base_url = prov.get("base_url", base_url)
            except Exception:
                continue

        print(f"Note: Model '{model}' failed, trying fallback '{fallback_model}'.", flush=True)
        if event_callback:
            event_callback("fallback", {
                "status": "switch",
                "from": model,
                "to": fallback_model,
                "reason": primary_error,
            })

        fb_params = get_inference_params(fallback_model, purpose)
        # Snapshot message count: if the fallback fails mid-tool-loop, intermediate
        # messages will corrupt the session (thinking blocks with invalid signatures).
        msg_count_before = len(messages)
        result, fb_error = _retry_with_backoff(
            messages, fallback_model, fb_api_key, fb_base_url,
            silent, tools, escape_watcher, event_callback,
            fb_params, session_id, max_retries=1)
        if result is not None:
            # Strip intermediate tool-loop messages appended by the fallback model.
            # The final text reply is returned; server adds it properly to session.
            if len(messages) > msg_count_before:
                del messages[msg_count_before:]
            _thread_local._fallback_model_used = fallback_model
            # Log fallback event to cost tracker
            if _cost_tracker:
                try:
                    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
                    agent_id = agent.agent_id if agent else "main"
                    logging.info(f"Fallback: {model} -> {fallback_model} for agent {agent_id} (reason: {primary_error})")
                except Exception:
                    pass
            return result

    msg = f"Error: No working models found. Tried: {', '.join(tried_models)}"
    print(msg, file=sys.stderr)
    if event_callback:
        raise RuntimeError(msg)
    sys.exit(1)


# --- TUI helpers ---

def _draw_status_bar(model: str, history: list[dict] | None = None,
                     max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> None:
    """Draw a status bar on the last terminal line with black background."""
    cols = shutil.get_terminal_size().columns
    rows = shutil.get_terminal_size().lines

    # Context usage
    ctx_part = ""
    ctx_visible = 0
    if history is not None:
        est = _estimate_conversation_tokens(history)
        pct = min(99, int(est / max_tokens * 100))
        if pct >= 75:
            color = RED
        elif pct >= 50:
            color = YELLOW
        else:
            color = FG_GRAY
        # Show token count in k for readability
        if est >= 1000:
            tok_str = f"{est // 1000}k"
        else:
            tok_str = str(est)
        ctx_label = f"{tok_str}/{max_tokens // 1000}k"
        ctx_part = f" {DIM}│{RESET}{BG_DARK} {color}{ctx_label}{RESET}{BG_DARK}"
        ctx_visible = 4 + len(ctx_label)  # " │ Nk/Nk"

    # Agent name
    agent_part = ""
    agent_visible = 0
    if _current_agent and _current_agent.agent_id != "main":
        agent_part = f" {CYAN}{_current_agent.agent_id}{RESET}{BG_DARK} {DIM}│{RESET}{BG_DARK}"
        agent_visible = 1 + len(_current_agent.agent_id) + 3  # " name │"

    label = f" {agent_part}{FG_GRAY}Model:{RESET}{BG_DARK} {GREEN}{BOLD}{model}{RESET}{BG_DARK}{ctx_part} "
    visible_len = 1 + agent_visible + 8 + len(model) + ctx_visible + 1
    padding = max(0, cols - visible_len)
    bar = f"\033[48;5;235m{label}{' ' * padding}{RESET}"
    sys.stdout.write(f"\0337\033[{rows};1H{bar}\0338")
    sys.stdout.flush()


def _setup_scroll_region() -> None:
    """Reserve the bottom line for the status bar."""
    rows = shutil.get_terminal_size().lines
    sys.stdout.write(f"\033[1;{rows - 1}r")
    sys.stdout.write(f"\033[1;1H")
    sys.stdout.flush()


def _restore_scroll_region() -> None:
    """Restore full terminal scroll region."""
    rows = shutil.get_terminal_size().lines
    sys.stdout.write(f"\033[1;{rows}r")
    sys.stdout.write(f"\033[{rows};1H\033[K")
    sys.stdout.flush()


# --- Main ---

