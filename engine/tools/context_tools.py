# Lossless Context Manager (LCM) tool bodies (extracted from brain.py, E4).
#
# The 3 agent-facing LCM tools — context_search / context_detail /
# context_recall. Pure relocation: JSON envelopes + error strings are
# byte-identical to pre-E4 brain.py.
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `_thread_local` from engine.context.
#   - the ContextManager singleton (`_context_manager`) and the delegate
#     credential globals (`_delegate_fallback_model` / `_delegate_api_key` /
#     `_delegate_base_url`) STAY in brain — reached lazily via `import brain
#     as _brain`. A top-level `import brain` would cycle (brain imports this
#     module for TOOL_DISPATCH).
#
# brain.py re-exports all 3 via `from engine.tools.context_tools import (...)`
# so `brain.tool_context_search` + the TOOL_DISPATCH entries resolve unchanged.

from __future__ import annotations

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


def tool_context_search(args: dict) -> str:
    """Search compacted conversation history."""
    import brain as _brain
    if not _brain._context_manager:
        return _err("Context manager not initialized")
    session_id = get_request_context().current_session_id or ""
    if not session_id:
        return _err("No active session")
    query = args.get("query", "")
    if not query:
        return _err("Missing query")
    limit = args.get("limit", 10)
    results = _brain._context_manager.search(session_id, query, limit=limit)
    return _ok({"results": results, "count": len(results), "query": query})


def tool_context_detail(args: dict) -> str:
    """Expand a summary to see original messages."""
    import brain as _brain
    if not _brain._context_manager:
        return _err("Context manager not initialized")
    summary_id = args.get("summary_id", "")
    if not summary_id:
        return _err("Missing summary_id")
    detail = _brain._context_manager.get_detail(summary_id)
    return _ok(detail) if "error" not in detail else _err(detail["error"])


def tool_context_recall(args: dict) -> str:
    """Deep recall from compacted conversation history."""
    import brain as _brain
    if not _brain._context_manager:
        return _err("Context manager not initialized")
    session_id = get_request_context().current_session_id or ""
    if not session_id:
        return _err("No active session")
    query = args.get("query", "")
    if not query:
        return _err("Missing query")
    # Get API credentials from thread local or delegate globals
    model = get_request_context().current_model or _brain._delegate_fallback_model or ""
    api_key = _brain._delegate_api_key or ""
    base_url = _brain._delegate_base_url or ""
    result = _brain._context_manager.recall(session_id, query, model, api_key, base_url)
    return _ok({"answer": result, "query": query})
