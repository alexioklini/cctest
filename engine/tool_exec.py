"""engine/tool_exec.py — tool-execution helper layer.

Extracted from brain.py (refactor C2). Owns the PURE, deterministic helpers
that wrap the agentic loop's tool dispatch — not the tool implementations
themselves (those stay in brain.py), but the envelopes, the loop-breaker,
the per-session read-path tracker, and the tool-result
sanitise/compress/budget/microcompact pipeline.

Owns:
  - `_ok` / `_err` — universal JSON tool-result envelopes. Pure (json only).
  - `_get_artifact_session_folder` — deterministic `<date>_<session_id>`
    artifact-folder naming (the miner classifies folders by this shape).
  - dedup cluster: `_tool_dedup_lock`, `_tool_dedup`, `_TOOL_DEDUP_TTL`,
    `_dedup_sid`, `_dedup_state`, `_dedup_gc_locked`, `_check_tool_dedup`,
    `reset_tool_dedup` — session-scoped loop-breaker (1 dup = error,
    2 = TaskCancelled).
  - read-path tracker: `_session_read_paths_lock`, `_session_read_paths`,
    `_session_read_paths_sid`, `_SESSION_READ_PATHS_MAX`,
    `_record_session_read_path`, `_read_doc_cache_session_paths` — feeds the
    citation validator.
  - result processing: `_BASE64_DATA_RE`, `_BASE64_RAW_RE`,
    `_sanitize_tool_result`, `_compress_old_tool_results`,
    `TOOL_RESULT_BUDGET_THRESHOLD`, `TOOL_RESULT_PREVIEW_SIZE`,
    `_apply_tool_result_budget`, `_MICROCOMPACT_TOOLS`, `_COMPACT_TOOL_ARGS`,
    `_MICROCOMPACT_EXEMPT`, `_microcompact`, `_find_tool_name_for_result`,
    `_find_tool_name_for_block`.

Seams:
  - `get_request_context()` comes from engine.context (low-level base, no cycle).
    The dedup + read-path stores key off `get_request_context().current_session_id`.
  - brain-runtime symbols (`TaskCancelled`, `AGENTS_DIR`, `_current_agent`)
    are reached lazily via the `_LazyBrain` proxy (`_brain.<name>`) — a
    top-level `import brain` would be a cycle (brain imports this module).
    INVARIANT: `_check_tool_dedup` raises `_brain.TaskCancelled`, which is
    the SAME class object `brain.TaskCancelled` resolves to, so callers'
    `except brain.TaskCancelled` / test `assertRaises(brain.TaskCancelled)`
    keep working.

brain.py re-exports every symbol defined here via
`from engine.tool_exec import (...)` so existing callers
(`brain._ok`, `brain._check_tool_dedup`, the hundreds of in-brain bare
`_ok(...)`/`_err(...)` calls, the characterization tests) resolve unchanged.
The mutable globals (`_tool_dedup`, `_session_read_paths`, the locks) live
HERE as the single instance — brain.py's re-export binds the same objects,
so `brain._tool_dedup is engine.tool_exec._tool_dedup`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from engine.context import get_request_context


class _LazyBrain:
    """Lazy proxy to the live `brain` module. A top-level `import brain`
    here would be a cycle (brain imports this module); resolving the
    attribute on first access defers the import until after brain has
    finished loading. Brain-runtime symbols this layer touches
    (TaskCancelled, AGENTS_DIR, _current_agent) are reached as `_brain.<name>`.
    """
    __slots__ = ()

    def __getattr__(self, name):
        import brain as _b
        return getattr(_b, name)


_brain = _LazyBrain()


# --- Tool-result envelopes ------------------------------------------------

def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


# --- Artifact session folder ----------------------------------------------

def _get_artifact_session_folder(session_id: str) -> str:
    """Return session folder name for artifacts: <date>_<session_prefix>"""
    cache_key = f"_artifact_folder_{session_id}"
    cached = get_request_context()._dynamic.get(cache_key)
    if cached:
        return cached
    from datetime import datetime as _dt
    folder = f"{_dt.now().strftime('%Y-%m-%d')}_{session_id}"
    get_request_context()._dynamic[cache_key] = folder
    return folder


# --- Code-Mode chat folder -------------------------------------------------
# In Code Mode the file tools write into the PROJECT (working_dir), not into an
# artifact folder — so generated helper scripts / reports need a home that does
# not pollute the source tree AND does not throw every chat's output into one
# shared bucket. Scheme (user's call): <project>/chats/<title>_<date>_<id>/…
# Mirrors the per-chat isolation the artifact folders already have outside code
# mode; the title makes the folder recognisable when browsing the file tree, the
# id keeps it unique (two chats can share a title), the date sorts.

def _slug_for_folder(text: str, cap: int = 40) -> str:
    """Filesystem-safe slug: lowercase, word chars + hyphens only, capped on a
    word boundary. Umlauts survive (Unicode \\w). Empty → ''."""
    import re as _re
    s = _re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    s = _re.sub(r"[\s_]+", "-", s).strip("-")
    if len(s) > cap:
        s = s[:cap].rsplit("-", 1)[0] or s[:cap]
    return s.strip("-")


def get_code_mode_chat_folder(session_id: str) -> str:
    """Relative folder for THIS chat's generated files inside a code-mode project:
    `chats/<title>_<date>_<id>`. Falls back to `chats/<date>_<id>` when the chat
    has no title yet (the title is derived from the first user message, so in
    practice it is always set before the first tool call — the fallback covers
    session-less/scheduler paths).

    A SUB-AGENT (detached background task) gets its OWN subfolder underneath:
    `chats/<title>_<date>_<id>/subagents/<task_id>/`. The chat identity stays the
    CHAT's (title + session id — a sub-agent has no title of its own and belongs
    to the chat that spawned it), but each task is isolated: several sub-agents of
    one fan-out run CONCURRENTLY and would otherwise overwrite each other's
    `report.html` / read each other's half-written intermediates.

    Cached per request so every tool call of a turn agrees on one folder even if
    the chat title changes mid-turn (it would otherwise move under the model's
    feet).
    """
    if not session_id:
        return ""
    ctx = get_request_context()
    task_id = (ctx.current_bg_task_id or "") if ctx.current_bg_task else ""
    ck = f"_codemode_chat_folder_{session_id}_{task_id}"
    cached = ctx._dynamic.get(ck)
    if cached:
        return cached
    from datetime import datetime as _dt
    # Title source: the DB row. On the FIRST turn the title is derived just before
    # this call but not yet persisted, so the caller may hand it over via the
    # request context (`_codemode_chat_title`) — without that the first turn would
    # get a title-less folder while every later turn carries the title, i.e. TWO
    # folders for one chat.
    title = _slug_for_folder(ctx._dynamic.get("_codemode_chat_title") or "")
    if not title:
        try:
            from server_lib.db import ChatDB
            row = ChatDB.get_session_info(session_id) or {}
            title = _slug_for_folder(row.get("title") or "")
        except Exception:
            title = ""
    stamp = f"{_dt.now().strftime('%Y-%m-%d')}_{session_id}"
    folder = f"chats/{title + '_' if title else ''}{stamp}"
    if task_id:
        folder = f"{folder}/subagents/{task_id}"
    ctx._dynamic[ck] = folder
    return folder


# --- Tool-call dedup (session-scoped loop-breaker) ------------------------

_tool_dedup_lock = threading.Lock()
_tool_dedup: dict[str, dict] = {}  # sid -> {"calls": set[str], "consecutive_dupes": int, "last_touch": float}
_TOOL_DEDUP_TTL = 3600  # seconds; drop state for sessions untouched this long


def _dedup_sid() -> str:
    """Resolve the current dedup scope key. Session id when known, else a per-thread
    sentinel so unrelated CLI invocations don't contaminate each other."""
    sid = get_request_context().current_session_id
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
    _DEDUP_EXEMPT = {"wiki_read", "wiki_structure", "delegate_task", "task_status",
                     "schedule_list", "schedule_history"}
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
                raise _brain.TaskCancelled()
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


# --- Live tool subprocess registry (per-tool kill) ---------------------------
#
# python_exec / execute_command spawn a real subprocess (own process group via
# start_new_session=True). We register each live Popen under (turn_id,
# tool_use_id) so a per-tool cancel (POST /v1/background-tasks/cancel-tool →
# kill_tool_process) can SIGKILL the process group mid-run — a true kill, not
# just abandoning the wait. Unregistered in the tool's finally. Other tools
# (network/in-process) have no killable handle and aren't registered; for those
# the sidecar's loop-unblock remains the only cancellation. Keyed on the pair so
# concurrent tasks/tools never collide.
_tool_procs_lock = threading.Lock()
_tool_procs: dict[tuple, object] = {}  # (turn_id, tool_use_id) -> subprocess.Popen


def register_tool_process(proc) -> tuple | None:
    """Register a live subprocess under the current turn's (turn_id, tool_use_id)
    so kill_tool_process can reach it. Reads both off the request context. Returns
    the key (pass to unregister_tool_process) or None when not addressable (no
    ids — e.g. a non-sidecar caller); the caller then just skips unregister."""
    ctx = get_request_context()
    turn_id = getattr(ctx, "current_turn_id", "") or ""
    tool_use_id = getattr(ctx, "tool_use_id", "") or ""
    if not turn_id or not tool_use_id:
        return None
    key = (turn_id, tool_use_id)
    with _tool_procs_lock:
        _tool_procs[key] = proc
    return key


def unregister_tool_process(key) -> None:
    if not key:
        return
    with _tool_procs_lock:
        _tool_procs.pop(key, None)


def kill_tool_process(turn_id: str, tool_use_id: str) -> bool:
    """SIGKILL the registered subprocess (its whole process group) for one
    in-flight tool call. Returns True if a live process was found + signalled.
    The tool's own communicate() then returns and the function completes with a
    killed-process result — the loop gets a real, prompt tool_result."""
    if not turn_id or not tool_use_id:
        return False
    with _tool_procs_lock:
        proc = _tool_procs.get((turn_id, tool_use_id))
    if proc is None:
        return False
    import signal as _sig
    try:
        os.killpg(proc.pid, _sig.SIGKILL)
        return True
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
            return True
        except Exception:
            return False


# --- Per-session read-path tracker -------------------------------------
#
# Thin record of which files the model has called read_document / read_file
# on in this session. Used by the citation validator to grep verbatim quotes
# against the right files on disk. No content cached, no stubs, no eviction
# beyond session-delete cleanup — within a single turn the `tool_dedup` guard
# kills accidental double-reads; across turns the model is expected to re-read
# (we deliberately don't carry tool_results across turns, so the model has no
# in-context view of a prior turn's read anyway).
_session_read_paths_lock = threading.Lock()
_session_read_paths: dict[str, set[str]] = {}  # sid -> {abs_path, ...}


def _session_read_paths_sid() -> str:
    sid = get_request_context().current_session_id
    if sid:
        return sid
    return f"_thread:{threading.get_ident()}"


_SESSION_READ_PATHS_MAX = 256  # soft cap per session


def _record_session_read_path(path: str) -> None:
    """Note that the current session called read_document / read_file on this
    file. Used by the citation validator to know which on-disk sources to grep
    quotes against."""
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return
    sid = _session_read_paths_sid()
    with _session_read_paths_lock:
        bucket = _session_read_paths.get(sid)
        if bucket is None:
            bucket = set()
            _session_read_paths[sid] = bucket
        # Sets don't preserve insertion order — when capped, the drop is
        # arbitrary. 256 is generous for chat workloads; if we exceed it the
        # session is probably an outlier and the validator can live with
        # whatever subset remains.
        if len(bucket) >= _SESSION_READ_PATHS_MAX and abs_path not in bucket:
            return
        bucket.add(abs_path)


# --- Matched-regions tracker -------------------------------------------------
# mempalace_query records, per session, which chunk_indices of each file matched
# the query. read_document consults this so that a follow-up read of a
# mempalace-sourced file returns ONLY the matched regions (union of windows
# around each matched chunk) instead of the whole file — files commonly match
# on several SCATTERED chunks (measured: a Löschkonzept matched chunks 2/18/20/48),
# so a single window misses most and a full read drags the entire doc in.
# Keyed by the absolute .brain-extracted/*.md path (what the chunk store keys on
# AND what read_path resolves to). Unknown file → caller falls back to full read.
_session_match_regions_lock = threading.Lock()
_session_match_regions: dict[str, dict[str, set]] = {}  # sid -> {abs_md_path -> {chunk_index,...}}


def _record_match_regions(md_path: str, chunk_indices) -> None:
    """mempalace_query → note which chunk_indices of md_path matched this turn."""
    if not md_path:
        return
    cis = {int(c) for c in chunk_indices if c is not None}
    if not cis:
        return
    try:
        abs_path = os.path.abspath(os.path.expanduser(md_path))
    except Exception:
        return
    sid = _session_read_paths_sid()
    with _session_match_regions_lock:
        bysid = _session_match_regions.setdefault(sid, {})
        bysid.setdefault(abs_path, set()).update(cis)


def _get_match_regions(md_path: str) -> set:
    """read_document → matched chunk_indices for md_path this session, or empty."""
    try:
        abs_path = os.path.abspath(os.path.expanduser(md_path))
    except Exception:
        return set()
    sid = _session_read_paths_sid()
    with _session_match_regions_lock:
        return set((_session_match_regions.get(sid) or {}).get(abs_path) or set())


def _session_match_region_paths(session_id: str | None = None) -> set:
    """All md source paths surfaced by mempalace_query this session. The
    citation validator uses these as verifiable sources too — a turn that
    grounds purely on mempalace_query drawers (no read_document) still has its
    quotes checked against the drawers' on-disk companion files."""
    sid = session_id or _session_read_paths_sid()
    with _session_match_regions_lock:
        return set((_session_match_regions.get(sid) or {}).keys())


# --- brain_code fetch-trim tracker -------------------------------------------
# Brainy (helpdesk) finds source via mempalace_query against the `brain_code`
# wing, then reads the WHOLE file from GitHub raw (live main — there's no local
# checkout in prod). The query already knows which chunk(s) matched; this tracker
# remembers their TEXT per session, keyed by repo-relative path, so web_fetch can
# return ONLY the matched regions of the fetched file to the LLM instead of the
# whole source. Text-keyed (not chunk_index) because brain_code drawers carry no
# line/char positions — we relocate each chunk in the fetched file by fingerprint.
_brain_code_regions_lock = threading.Lock()
_brain_code_regions: dict[str, dict[str, list]] = {}  # sid -> {repo_rel_path -> [chunk_text,...]}


def _record_brain_code_region(repo_path: str, chunk_text: str) -> None:
    """brain_code query hit → remember this chunk's text for later fetch-trim."""
    if not repo_path or not chunk_text or not chunk_text.strip():
        return
    sid = _session_read_paths_sid()
    with _brain_code_regions_lock:
        bysid = _brain_code_regions.setdefault(sid, {})
        lst = bysid.setdefault(repo_path, [])
        if chunk_text not in lst and len(lst) < 32:
            lst.append(chunk_text)


def _get_brain_code_regions(repo_path: str) -> list:
    """web_fetch → matched chunk texts for this repo-relative path this session."""
    if not repo_path:
        return []
    sid = _session_read_paths_sid()
    with _brain_code_regions_lock:
        return list((_brain_code_regions.get(sid) or {}).get(repo_path) or [])


def _read_doc_cache_session_paths(session_id: str | None = None) -> list[str]:
    """Return absolute paths the given session has read via read_document /
    read_file. Name kept for backward compatibility with the citation
    validator; the underlying store is no longer a cache, just a path set."""
    sid = session_id or _session_read_paths_sid()
    with _session_read_paths_lock:
        bucket = _session_read_paths.get(sid)
        if not bucket:
            return []
        return list(bucket)


# --- Tool-result sanitisation: strip base64 image blobs -------------------

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


# --- Tool Result Budget: persist large results to disk ---
# Called unconditionally from handlers/chat.py before every turn; the variance
# flag that used to gate this middleware was retired in Phase 5 step 6 but the
# preprocessing call site stays until step 7's broader cleanup.

TOOL_RESULT_BUDGET_THRESHOLD = 50000  # chars — persist results larger than this
TOOL_RESULT_PREVIEW_SIZE = 2000  # chars — preview kept in context

# NOTE: _apply_tool_result_budget is currently NOT called on the interactive
# chat path — the sidecar owns the per-turn ephemeral tool exchange and tool
# results never live in session.messages, so it would be a no-op there (see
# handlers/chat.py and CHANGELOG 9.46.5). Kept for any future native/background
# caller. read_document returns its content VERBATIM (tool_mcp hard rule); the
# only real ceiling on a big read is the model's context window.


def _apply_tool_result_budget(messages: list[dict], session_id: str | None = None,
                               agent_id: str | None = None) -> int:
    """Persist oversized tool results to disk and replace with truncated previews."""
    _threshold = TOOL_RESULT_BUDGET_THRESHOLD
    _preview_size = TOOL_RESULT_PREVIEW_SIZE
    if not session_id:
        session_id = get_request_context().current_session_id or ""
    if not agent_id:
        agent = get_request_context().current_agent or _brain._current_agent
        agent_id = agent.agent_id if agent else "main"

    persisted = 0
    results_dir = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts",
                                _get_artifact_session_folder(session_id), "tool-results")

    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > _threshold:
                tool_id = msg.get("tool_call_id", "unknown")
                filepath = os.path.join(results_dir, f"{tool_id}.txt")
                if not os.path.exists(filepath):
                    os.makedirs(results_dir, exist_ok=True)
                    try:
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)
                    except OSError:
                        continue
                preview = content[:_preview_size]
                size_kb = len(content) // 1024
                msg["content"] = (
                    f"[Output too large ({size_kb}KB). Full output saved to: {filepath}]\n"
                    f"Preview (first {_preview_size} chars):\n{preview}\n...\n"
                    f"[To read more, call read_document/read_file on {filepath} with "
                    f"offset+limit (line numbers), or grep it via execute_command — "
                    f"do NOT re-read the same range.]"
                )
                persisted += 1

        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                content = block.get("content", "")
                if isinstance(content, str) and len(content) > _threshold:
                    tool_id = block.get("tool_use_id", "unknown")
                    filepath = os.path.join(results_dir, f"{tool_id}.txt")
                    if not os.path.exists(filepath):
                        os.makedirs(results_dir, exist_ok=True)
                        try:
                            with open(filepath, "w", encoding="utf-8") as f:
                                f.write(content)
                        except OSError:
                            continue
                    preview = content[:_preview_size]
                    size_kb = len(content) // 1024
                    block["content"] = (
                        f"[Output too large ({size_kb}KB). Full output saved to: {filepath}]\n"
                        f"Preview (first {_preview_size} chars):\n{preview}\n...\n"
                        f"[To read more, call read_document/read_file on {filepath} with "
                        f"offset+limit (line numbers), or grep it via execute_command — "
                        f"do NOT re-read the same range.]"
                    )
                    persisted += 1
    return persisted


# --- Microcompact: strip stale tool results ---
# Called unconditionally from handlers/chat.py before every turn.

_MICROCOMPACT_TOOLS = {
    "read_file", "execute_command", "search_files", "list_directory",
    "web_fetch", "exa_search", "read_document", "code_graph_query",
    "write_file", "edit_file",
    "python_exec",
}
_COMPACT_TOOL_ARGS = {"python_exec": "code", "execute_command": "command"}
_MICROCOMPACT_EXEMPT = {
    "memory_recall", "memory_shared", "delegate_task", "task_status",
    "context_search", "context_detail", "context_recall",
}

def _microcompact(messages: list[dict], keep_recent: int = 5) -> tuple[list[dict], int]:
    """Clear old tool results for compactable tools. Returns (messages, tokens_freed)."""
    tokens_freed = 0
    tool_entries = []  # (msg_index, tool_name, content_size, is_openai)
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            content_size = len(content) if isinstance(content, str) else 0
            tool_name = _find_tool_name_for_result(messages, i, msg.get("tool_call_id"))
            tool_entries.append((i, tool_name, content_size, True))
        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content", "")
                    content_size = len(content) if isinstance(content, str) else 0
                    tool_name = _find_tool_name_for_block(messages, block.get("tool_use_id"))
                    tool_entries.append((i, tool_name, content_size, False))

    compactable = [e for e in tool_entries
                   if e[1] and e[1] in _MICROCOMPACT_TOOLS and e[1] not in _MICROCOMPACT_EXEMPT]
    if len(compactable) <= keep_recent:
        return messages, 0
    to_clear = compactable[:-keep_recent]

    cleared_indices = set()
    for idx, tool_name, content_size, is_openai in to_clear:
        if content_size <= 100:
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

    for idx in cleared_indices:
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
    if not tool_use_id:
        return None
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("id") == tool_use_id:
                        return block.get("name")
    return None
