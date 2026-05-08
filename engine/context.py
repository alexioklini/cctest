# Extracted from claude_cli.py — context manager, token helpers, cancellation primitives, misc tools
#
# Cross-module deps (not imported here — caller must ensure availability):
#   _ok, _err                  — from engine/tools/ or claude_cli.py
#   _thread_local              — threading.local() singleton in claude_cli.py
#   _context_manager           — ContextManager singleton, set at init time
#   _scheduler                 — Scheduler singleton
#   _mcp_manager               — MCPManager singleton
#   MCPManager                 — class from engine/mcp.py
#   AGENTS_DIR                 — from engine/constants.py
#   _run_delegate_with_fallback, _resolve_model_with_fallback — from claude_cli.py
#   _get_token_config          — from claude_cli.py
#   send_message               — from claude_cli.py
#   _toggle_tool_output        — from claude_cli.py
#   DIM, RESET                 — ANSI escape strings from claude_cli.py
#   _delegate_fallback_model, _delegate_api_key, _delegate_base_url — globals in claude_cli.py
#   _current_agent             — global in claude_cli.py

import hashlib
import json
import logging
import os
import select
import sqlite3
import sys
import threading
import time

from engine.agents import AGENTS_DIR  # noqa: F401 — needed at module level


# ---------------------------------------------------------------------------
# Misc tool wrappers (lines 14905–15055 of claude_cli.py)
# ---------------------------------------------------------------------------

def tool_list_nodes(args: dict) -> str:
    """List all registered remote nodes."""
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8420/v1/nodes", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        nodes = data.get("nodes", [])
        if not nodes:
            return _ok({"nodes": [], "count": 0, "message": "No nodes registered"})
        return _ok({"nodes": nodes, "count": len(nodes)})
    except Exception as e:
        return _err(f"Failed to list nodes: {e}")


def tool_context_search(args: dict) -> str:
    """Search compacted conversation history."""
    if not _context_manager:
        return _err("Context manager not initialized")
    session_id = getattr(_thread_local, 'current_session_id', None) or ""
    if not session_id:
        return _err("No active session")
    query = args.get("query", "")
    if not query:
        return _err("Missing query")
    limit = args.get("limit", 10)
    results = _context_manager.search(session_id, query, limit=limit)
    return _ok({"results": results, "count": len(results), "query": query})


def tool_context_detail(args: dict) -> str:
    """Expand a summary to see original messages."""
    if not _context_manager:
        return _err("Context manager not initialized")
    summary_id = args.get("summary_id", "")
    if not summary_id:
        return _err("Missing summary_id")
    detail = _context_manager.get_detail(summary_id)
    return _ok(detail) if "error" not in detail else _err(detail["error"])


def tool_context_recall(args: dict) -> str:
    """Deep recall from compacted conversation history."""
    if not _context_manager:
        return _err("Context manager not initialized")
    session_id = getattr(_thread_local, 'current_session_id', None) or ""
    if not session_id:
        return _err("No active session")
    query = args.get("query", "")
    if not query:
        return _err("Missing query")
    # Get API credentials from thread local or delegate globals
    model = getattr(_thread_local, 'current_model', None) or _delegate_fallback_model or ""
    api_key = _delegate_api_key or ""
    base_url = _delegate_base_url or ""
    result = _context_manager.recall(session_id, query, model, api_key, base_url)
    return _ok({"answer": result, "query": query})


def tool_schedule_list(args: dict) -> str:
    """List all scheduled tasks."""
    if not _scheduler:
        return _err("Scheduler not initialized")
    schedules = _scheduler.list_all()
    return _ok({"schedules": schedules, "count": len(schedules)})


def tool_schedule_history(args: dict) -> str:
    """Get execution history for scheduled tasks."""
    if not _scheduler:
        return _err("Scheduler not initialized")
    name = args.get("name")
    limit = args.get("limit", 20)
    history = _scheduler.get_history(name, limit)
    # Truncate long results
    for h in history:
        if h.get("result") and len(h["result"]) > 500:
            h["result"] = h["result"][:500] + "..."
    return _ok({"history": history, "count": len(history)})


# --- MCP Client Tools ---

def tool_mcp_connect(args: dict) -> str:
    """Connect to an MCP server at runtime."""
    url = args.get("url", "")
    name = args.get("name", "")
    transport = args.get("transport", "sse")
    persist = args.get("persist", False)

    if not url or not name:
        return _err("Both 'url' and 'name' are required")

    # Use thread-local MCP manager if available, otherwise global
    mcp = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    if not mcp:
        mcp = MCPManager()
        _thread_local.mcp_manager = mcp

    result = mcp.connect_runtime(url, name, transport)
    if result.get("error"):
        return _err(result["error"])

    # Persist to mcp.json if requested
    if persist:
        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
        agent_id = agent.agent_id if agent else "main"
        mcp_json_path = os.path.join(AGENTS_DIR, agent_id, "mcp.json")
        try:
            existing = {}
            if os.path.exists(mcp_json_path):
                with open(mcp_json_path, "r") as f:
                    existing = json.load(f)
            if transport == "stdio":
                parts = url.split()
                existing[name] = {"transport": "stdio", "command": parts[0], "args": parts[1:] if len(parts) > 1 else []}
            else:
                existing[name] = {"transport": "sse", "url": url}
            with open(mcp_json_path, "w") as f:
                json.dump(existing, f, indent=2)
            result["persisted"] = True
        except Exception as e:
            result["persist_error"] = str(e)

    return _ok(result)


def tool_mcp_disconnect(args: dict) -> str:
    """Disconnect from an MCP server."""
    name = args.get("name", "")
    if not name:
        return _err("'name' is required")

    mcp = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    if not mcp:
        return _err("No MCP manager available")

    result = mcp.disconnect_runtime(name)
    if result.get("error"):
        return _err(result["error"])
    return _ok(result)


def tool_mcp_servers(args: dict) -> str:
    """List all connected MCP servers."""
    mcp = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    if not mcp:
        return _ok({"servers": [], "count": 0})
    servers = mcp.list_servers()
    return _ok({"servers": servers, "count": len(servers)})


# ---------------------------------------------------------------------------
# Context Window Management (lines 16626–17139 of claude_cli.py)
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONTEXT_TOKENS = 131072
COMPACT_THRESHOLD = 0.75  # compact at 75% full
KEEP_RECENT_MESSAGES = 6  # always keep the last N messages untouched (legacy fallback)
CHARS_PER_TOKEN = 4       # conservative estimate

# --- Lossless Context Manager ---

CONTEXT_DB = os.path.join(AGENTS_DIR, "main", "context.db")

_CONTEXT_CONFIG_DEFAULTS = {
    "enabled": True,
    "fresh_tail_count": 16,
    "compact_threshold": 0.60,
    "summary_target_tokens": 1000,
    "condense_threshold": 4,
    "max_depth": 5,
    "summary_model": "gemini-2.5-flash",
    "summary_model_fallback": "claude-haiku-4-5-20251001",
    "messages_per_summary": 10,
}

_context_db_pool = threading.local()


def _context_conn():
    """Thread-local SQLite connection for the context DB."""
    conn = getattr(_context_db_pool, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(CONTEXT_DB), exist_ok=True)
        conn = sqlite3.connect(CONTEXT_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _context_db_pool.conn = conn
    return conn


def _context_init_db():
    conn = _context_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS summaries (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            depth INTEGER NOT NULL DEFAULT 0,
            token_count INTEGER NOT NULL,
            content TEXT NOT NULL,
            parent_ids TEXT DEFAULT '[]',
            message_range_start INTEGER NOT NULL,
            message_range_end INTEGER NOT NULL,
            created_at REAL DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_summary_session ON summaries(session_id);
        CREATE INDEX IF NOT EXISTS idx_summary_depth ON summaries(session_id, depth);
        CREATE TABLE IF NOT EXISTS context_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()


class ContextManager:
    """Lossless context management with DAG-based hierarchical summarization."""

    def __init__(self):
        _context_init_db()

    def get_config(self) -> dict:
        cfg = dict(_CONTEXT_CONFIG_DEFAULTS)
        try:
            conn = _context_conn()
            rows = conn.execute("SELECT key, value FROM context_config").fetchall()
            for k, v in rows:
                if k in cfg:
                    # Coerce types
                    default = _CONTEXT_CONFIG_DEFAULTS[k]
                    if isinstance(default, bool):
                        cfg[k] = v.lower() in ("true", "1", "yes")
                    elif isinstance(default, int):
                        cfg[k] = int(v)
                    elif isinstance(default, float):
                        cfg[k] = float(v)
                    else:
                        cfg[k] = v
        except Exception:
            pass
        return cfg

    def save_config(self, updates: dict):
        conn = _context_conn()
        for k, v in updates.items():
            if k in _CONTEXT_CONFIG_DEFAULTS:
                conn.execute(
                    "INSERT OR REPLACE INTO context_config (key, value) VALUES (?, ?)",
                    (k, str(v))
                )
        conn.commit()

    def _resolve_summary_model(self) -> tuple[str | None, str | None]:
        """Return (primary_model, fallback_model) for context summarization."""
        cfg = self.get_config()
        primary = cfg.get("summary_model") or "gemini-2.5-flash"
        fallback = cfg.get("summary_model_fallback") or "claude-haiku-4-5-20251001"
        p, f = _resolve_model_with_fallback(primary, fallback, "claude-haiku-4-5-20251001")
        return p, f

    def _extract_message_text(self, msg: dict) -> str:
        """Extract plain text from a message (handles string and block content)."""
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            return f"{role}: {content}"
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[Tool: {block.get('name', '')}]")
                    elif block.get("type") == "tool_result":
                        c = block.get("content", "")
                        parts.append(f"[Result: {str(c)[:300]}]")
            return f"{role}: {' '.join(parts)}"
        return f"{role}: {str(content)[:500]}"

    def summarize_chunk(self, messages: list[dict], session_id: str,
                        range_start: int, range_end: int,
                        model: str, api_key: str, base_url: str,
                        fallback_model: str | None = None) -> str | None:
        """Summarize a chunk of messages into a leaf summary (depth 0). Returns summary ID."""
        text_parts = [self._extract_message_text(m) for m in messages]
        source_text = "\n".join(text_parts)

        summary_text = None
        try:
            result = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": (
                    "Summarize this conversation segment concisely. Preserve: key facts, decisions, "
                    "file paths, commands run, errors encountered, and task context. "
                    "Be factual and specific, not vague. ~200-300 words.\n\n" + source_text
                )}],
                primary_model=model, fallback_model=fallback_model,
                system_prompt="You are a precise conversation summarizer. Output only the summary.",
                memory_store=None,
                inference_params={"max_tokens": 2000, "temperature": 0.1},
                tools=False,
            )
            if result and not result.startswith("Delegation error"):
                summary_text = result.strip()
        except Exception:
            pass

        if not summary_text:
            # Fallback: truncation
            summary_text = source_text[:3000]
            if len(source_text) > 3000:
                summary_text += "\n...(truncated)"

        sid = hashlib.sha256(f"{session_id}:{range_start}:{range_end}:{time.time()}".encode()).hexdigest()[:16]
        token_count = len(summary_text) // CHARS_PER_TOKEN

        conn = _context_conn()
        conn.execute(
            "INSERT OR REPLACE INTO summaries (id, session_id, depth, token_count, content, parent_ids, message_range_start, message_range_end) "
            "VALUES (?, ?, 0, ?, ?, '[]', ?, ?)",
            (sid, session_id, token_count, summary_text, range_start, range_end)
        )
        conn.commit()
        return sid

    def condense(self, session_id: str) -> bool:
        """Merge same-depth summaries into higher-depth summaries. Returns True if any merged."""
        cfg = self.get_config()
        threshold = cfg.get("condense_threshold", 4)
        max_depth = cfg.get("max_depth", 5)
        conn = _context_conn()
        merged_any = False

        for depth in range(max_depth):
            rows = conn.execute(
                "SELECT id, content, token_count, message_range_start, message_range_end "
                "FROM summaries WHERE session_id = ? AND depth = ? ORDER BY message_range_start",
                (session_id, depth)
            ).fetchall()
            if len(rows) < threshold:
                continue

            # Merge all same-depth summaries into one higher-depth summary
            combined_text = "\n\n---\n\n".join(r[1] for r in rows)
            parent_ids = [r[0] for r in rows]
            range_start = rows[0][3]
            range_end = rows[-1][4]

            # Summarize the combined text
            c_model, c_fallback = self._resolve_summary_model()
            condensed_text = None
            if c_model:
                try:
                    result = _run_delegate_with_fallback(
                        messages=[{"role": "user", "content": (
                            f"Condense these {len(rows)} conversation summaries into one cohesive summary. "
                            "Preserve all key facts, decisions, and context. ~300-500 words.\n\n" + combined_text
                        )}],
                        primary_model=c_model, fallback_model=c_fallback,
                        system_prompt="You are a precise summarizer. Output only the condensed summary.",
                        memory_store=None,
                        inference_params={"max_tokens": 3000, "temperature": 0.1},
                        tools=False,
                    )
                    if result and not result.startswith("Delegation error"):
                        condensed_text = result.strip()
                except Exception:
                    pass

            if not condensed_text:
                condensed_text = combined_text[:4000]

            new_id = hashlib.sha256(f"condense:{session_id}:{depth+1}:{time.time()}".encode()).hexdigest()[:16]
            token_count = len(condensed_text) // CHARS_PER_TOKEN

            conn.execute(
                "INSERT OR REPLACE INTO summaries (id, session_id, depth, token_count, content, parent_ids, message_range_start, message_range_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, session_id, depth + 1, token_count, condensed_text,
                 json.dumps(parent_ids), range_start, range_end)
            )
            # Remove merged lower-depth summaries
            conn.executemany("DELETE FROM summaries WHERE id = ?", [(pid,) for pid in parent_ids])
            conn.commit()
            merged_any = True

        return merged_any

    def assemble_context(self, session_id: str, messages: list[dict],
                         system_prompt: str = "", max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> list[dict]:
        """Assemble context: summaries + fresh tail within token budget."""
        cfg = self.get_config()
        fresh_tail_count = cfg.get("fresh_tail_count", 32)

        # Ensure we don't exceed available messages
        fresh_tail_count = min(fresh_tail_count, len(messages))
        fresh_tail = messages[-fresh_tail_count:] if fresh_tail_count > 0 else messages

        # Calculate token budget
        system_tokens = len(system_prompt) // CHARS_PER_TOKEN if system_prompt else 0
        fresh_tokens = sum(_estimate_tokens_message(m) for m in fresh_tail)
        response_reserve = 4000  # reserve tokens for response
        budget = max_tokens - system_tokens - fresh_tokens - response_reserve

        if budget <= 0:
            return fresh_tail

        # Load summaries, highest depth first
        conn = _context_conn()
        rows = conn.execute(
            "SELECT id, depth, token_count, content, message_range_start, message_range_end "
            "FROM summaries WHERE session_id = ? ORDER BY depth DESC, message_range_start ASC",
            (session_id,)
        ).fetchall()

        if not rows:
            return fresh_tail

        # Fill budget with summaries
        summary_parts = []
        used_tokens = 0
        for row in rows:
            sid, depth, tc, content, rs, re_ = row
            if used_tokens + tc > budget:
                continue
            summary_parts.append({
                "id": sid, "depth": depth, "tokens": tc,
                "content": content, "range": f"messages {rs}-{re_}"
            })
            used_tokens += tc

        if not summary_parts:
            return fresh_tail

        # Sort by message range for chronological order
        summary_parts.sort(key=lambda s: s["range"])

        # Build summary message
        summary_text = "## Compacted Conversation History\n\n"
        summary_text += "The following summaries cover earlier parts of this conversation. "
        summary_text += "Use context_search, context_detail, or context_recall tools to access original details.\n\n"
        for s in summary_parts:
            summary_text += f"### Summary (depth {s['depth']}, {s['range']})\n{s['content']}\n\n"

        assembled = [
            {"role": "user", "content": f"[Conversation Context]\n{summary_text}"},
            {"role": "assistant", "content": "I have the context from our earlier conversation. I can use context_search, context_detail, or context_recall to access specific details if needed."},
        ]
        assembled.extend(fresh_tail)
        return assembled

    def check_and_compact(self, messages: list[dict], session_id: str,
                          model: str, api_key: str, base_url: str,
                          system_prompt: str = "",
                          max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
                          force: bool = False) -> tuple[list[dict], bool]:
        """Check if compaction needed and perform hierarchical summarization."""
        cfg = self.get_config()
        if not cfg.get("enabled", True) and not force:
            return messages, False

        estimated = _estimate_conversation_tokens(messages, system_prompt)
        if not force:
            threshold_pct = cfg.get("compact_threshold", 0.75)
            threshold = int(max_tokens * threshold_pct)
            if estimated < threshold:
                return messages, False

        fresh_tail_count = cfg.get("fresh_tail_count", 32)
        msgs_per_summary = cfg.get("messages_per_summary", 10)

        # Determine which messages to summarize (everything before fresh tail)
        if len(messages) <= fresh_tail_count:
            if force and len(messages) > msgs_per_summary:
                # Force mode: use half the messages as tail to allow some summarization
                fresh_tail_count = len(messages) // 2
            else:
                return messages, False

        old_messages = messages[:-fresh_tail_count]

        # Find what's already summarized (by checking existing summary ranges)
        conn = _context_conn()
        existing = conn.execute(
            "SELECT MAX(message_range_end) FROM summaries WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        already_summarized = existing[0] if existing and existing[0] else 0

        # Calculate message indices (1-based, relative to session)
        total_msg_count = len(messages)
        old_count = len(old_messages)

        # Only summarize messages not yet covered
        unsummarized_start = already_summarized
        unsummarized_msgs = old_messages[unsummarized_start:]

        if len(unsummarized_msgs) < msgs_per_summary:
            # Not enough new messages to summarize, just assemble
            return self.assemble_context(session_id, messages, system_prompt, max_tokens), True

        # Use summary model (Gemini Flash default, Haiku fallback)
        summary_model, summary_model_fallback = self._resolve_summary_model()
        summary_model = summary_model or model
        summary_model_fallback = summary_model_fallback or model

        pct = int(estimated / max_tokens * 100)
        logging.info(f"Context {pct}% full (~{estimated:,} tokens), creating hierarchical summaries...")

        # Create leaf summaries in chunks
        for i in range(0, len(unsummarized_msgs), msgs_per_summary):
            chunk = unsummarized_msgs[i:i + msgs_per_summary]
            if len(chunk) < 3:  # skip tiny remnants
                continue
            range_start = unsummarized_start + i
            range_end = unsummarized_start + i + len(chunk)
            self.summarize_chunk(chunk, session_id, range_start, range_end,
                                 summary_model, api_key, base_url,
                                 fallback_model=summary_model_fallback)

        # Try condensation
        self.condense(session_id)

        # Assemble final context
        assembled = self.assemble_context(session_id, messages, system_prompt, max_tokens)
        new_estimated = _estimate_conversation_tokens(assembled, system_prompt)
        new_pct = int(new_estimated / max_tokens * 100)
        logging.info(f"Compacted: {pct}% → {new_pct}% (~{new_estimated:,} tokens)")

        return assembled, True

    def search(self, session_id: str, query: str, regex: bool = False, limit: int = 10) -> list[dict]:
        """Search original messages in ChatDB by keyword/regex."""
        results = []
        try:
            from server_lib.db import _db_conn as chat_db_conn
            conn = chat_db_conn()
            conn.row_factory = sqlite3.Row
            if regex:
                # SQLite doesn't support regex natively, use LIKE as fallback
                rows = conn.execute(
                    "SELECT id, role, content, created_at FROM messages WHERE session_id = ? AND content LIKE ? ORDER BY id LIMIT ?",
                    (session_id, f"%{query}%", limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, created_at FROM messages WHERE session_id = ? AND content LIKE ? ORDER BY id LIMIT ?",
                    (session_id, f"%{query}%", limit)
                ).fetchall()
            for r in rows:
                content = r["content"]
                # Extract snippet around match
                idx = content.lower().find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 80)
                    end = min(len(content), idx + len(query) + 120)
                    snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                else:
                    snippet = content[:200]
                results.append({
                    "message_id": r["id"],
                    "role": r["role"],
                    "snippet": snippet,
                    "timestamp": r["created_at"],
                })
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_detail(self, summary_id: str) -> dict:
        """Get a summary and its original messages."""
        conn = _context_conn()
        row = conn.execute(
            "SELECT * FROM summaries WHERE id = ?", (summary_id,)
        ).fetchone()
        if not row:
            return {"error": f"Summary {summary_id} not found"}

        summary = {
            "id": row[0], "session_id": row[1], "depth": row[2],
            "token_count": row[3], "content": row[4],
            "parent_ids": json.loads(row[5] or "[]"),
            "message_range": f"{row[6]}-{row[7]}",
        }

        # Load original messages from ChatDB
        try:
            from server_lib.db import _db_conn as chat_db_conn
            cconn = chat_db_conn()
            cconn.row_factory = sqlite3.Row
            msgs = cconn.execute(
                "SELECT id, role, content FROM messages WHERE session_id = ? AND id >= ? AND id <= ? ORDER BY id",
                (row[1], row[6], row[7])
            ).fetchall()
            summary["original_messages"] = [{"id": m["id"], "role": m["role"], "content": m["content"][:500]} for m in msgs]
        except Exception:
            summary["original_messages"] = []

        return summary

    def recall(self, session_id: str, query: str,
               model: str, api_key: str, base_url: str) -> str:
        """Deep recall: search summaries and original messages, then answer with a focused sub-query."""
        # Find relevant summaries
        conn = _context_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, content, message_range_start, message_range_end FROM summaries WHERE session_id = ? ORDER BY depth DESC",
            (session_id,)
        ).fetchall()

        relevant_context = []
        for r in rows:
            if query.lower() in r["content"].lower():
                relevant_context.append(r["content"])

        # Also search original messages
        msg_results = self.search(session_id, query, limit=5)
        for mr in msg_results:
            if "snippet" in mr:
                relevant_context.append(f"[{mr['role']}]: {mr['snippet']}")

        if not relevant_context:
            return f"No relevant context found for: {query}"

        context_text = "\n\n---\n\n".join(relevant_context[:10])
        r_model, r_fallback = self._resolve_summary_model()
        r_model = r_model or model

        try:
            result = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": (
                    f"Based on this conversation history, answer the following query precisely:\n\n"
                    f"**Query:** {query}\n\n**Context:**\n{context_text}"
                )}],
                primary_model=r_model, fallback_model=r_fallback,
                system_prompt="Answer based only on the provided context. Be specific and cite details.",
                memory_store=None,
                inference_params={"max_tokens": 2000, "temperature": 0.1},
                tools=False,
            )
            if result and not result.startswith("Delegation error"):
                return result.strip()
        except Exception as e:
            return f"Recall failed: {e}"

        return f"Could not generate answer for: {query}"

    def get_stats(self, session_id: str) -> dict:
        """Get context stats for a session."""
        conn = _context_conn()
        rows = conn.execute(
            "SELECT depth, COUNT(*), SUM(token_count) FROM summaries WHERE session_id = ? GROUP BY depth",
            (session_id,)
        ).fetchall()
        depth_stats = {r[0]: {"count": r[1], "tokens": r[2]} for r in rows}
        total_summaries = sum(s["count"] for s in depth_stats.values())
        total_tokens = sum(s["tokens"] for s in depth_stats.values())
        return {
            "session_id": session_id,
            "total_summaries": total_summaries,
            "total_summary_tokens": total_tokens,
            "depth_distribution": depth_stats,
            "config": self.get_config(),
        }


_context_manager: ContextManager | None = None


# ---------------------------------------------------------------------------
# Token estimation helpers (lines 17143–17177 of claude_cli.py)
# ---------------------------------------------------------------------------

def _estimate_tokens_str(text: str) -> int:
    """Estimate token count for a string."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _estimate_tokens_message(msg: dict) -> int:
    """Estimate token count for a single message (any format)."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return _estimate_tokens_str(content) + 4  # role overhead
    elif isinstance(content, list):
        # Anthropic-style content blocks (text, tool_use, tool_result, etc.)
        total = 4
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += _estimate_tokens_str(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    total += _estimate_tokens_str(json.dumps(block.get("input", {}))) + 20
                elif block.get("type") == "tool_result":
                    total += _estimate_tokens_str(str(block.get("content", "")))
                else:
                    total += _estimate_tokens_str(json.dumps(block))
            elif isinstance(block, str):
                total += _estimate_tokens_str(block)
        return total
    return 10


def _estimate_conversation_tokens(messages: list[dict], system_prompt: str = "") -> int:
    """Estimate total token count for the full conversation."""
    total = _estimate_tokens_str(system_prompt) if system_prompt else 0
    for msg in messages:
        total += _estimate_tokens_message(msg)
    return total


def _compact_conversation(messages: list[dict], model: str, api_key: str,
                          base_url: str,
                          max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> list[dict]:
    """Compact the conversation by summarizing older messages.

    Strategy:
    1. Keep the last KEEP_RECENT_MESSAGES as-is
    2. Summarize everything before that into a single system-style message
    3. If the model is available, use it to summarize; otherwise do a simple truncation
    """
    if len(messages) <= KEEP_RECENT_MESSAGES:
        return messages

    # Split into old and recent
    recent = messages[-KEEP_RECENT_MESSAGES:]
    old = messages[:-KEEP_RECENT_MESSAGES]

    # Build a text representation of old messages for summarization
    old_text_parts = []
    for msg in old:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_pieces = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_pieces.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        text_pieces.append(f"[Called tool: {block.get('name', '')}]")
                    elif block.get("type") == "tool_result":
                        c = block.get("content", "")
                        text_pieces.append(f"[Tool result: {str(c)[:200]}]")
                    else:
                        text_pieces.append(f"[{block.get('type', 'block')}]")
            text = " ".join(text_pieces)
        else:
            text = str(content)

        # Truncate individual messages in the summary source
        if len(text) > 500:
            text = text[:500] + "..."
        old_text_parts.append(f"{role}: {text}")

    old_summary_source = "\n".join(old_text_parts)

    # Try to use the model to summarize
    summary = None
    try:
        summary_messages = [{
            "role": "user",
            "content": (
                "Summarize the following conversation history in 2-3 concise paragraphs. "
                "Focus on: key decisions made, information learned, files modified, "
                "and current task context. Be factual and brief.\n\n"
                f"{old_summary_source}"
            )
        }]
        summary = send_message(
            summary_messages, model, api_key, base_url,
            silent=True, tools=False, _tool_round=0,
        )
    except Exception:
        pass

    if not summary:
        # Fallback: simple truncation
        if len(old_summary_source) > 2000:
            summary = old_summary_source[:2000] + "\n...(earlier conversation truncated)"
        else:
            summary = old_summary_source

    # Build compacted conversation
    compacted = [
        {"role": "user", "content": f"[Previous conversation summary]\n{summary}"},
        {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I help?"},
    ]
    compacted.extend(recent)

    return compacted


def _check_and_compact(messages: list[dict], model: str, api_key: str,
                       base_url: str,
                       system_prompt: str = "",
                       max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
                       session_id: str = "",
                       force: bool = False) -> tuple[list[dict], bool]:
    """Check if conversation needs compaction and do it if necessary.

    Returns (messages, was_compacted).
    Uses lossless ContextManager if enabled, otherwise falls back to legacy compaction.
    """
    # Check for per-agent compact_threshold override
    tcfg = _get_token_config()
    agent_threshold = tcfg.get("compact_threshold")

    # Lossless context management (if enabled and session_id available)
    if _context_manager and session_id:
        try:
            cfg = _context_manager.get_config()
            if cfg.get("enabled", True):
                # Apply agent-level threshold override if set
                effective_max = max_tokens
                if agent_threshold is not None:
                    # Temporarily adjust max_tokens to shift the threshold
                    # ContextManager uses its own config threshold internally
                    pass
                return _context_manager.check_and_compact(
                    messages, session_id, model, api_key, base_url,
                    system_prompt=system_prompt, max_tokens=max_tokens,
                )
        except Exception as e:
            logging.warning(f"ContextManager failed, falling back to legacy: {e}")

    # Legacy flat compaction
    estimated = _estimate_conversation_tokens(messages, system_prompt)
    threshold_pct = agent_threshold if agent_threshold is not None else COMPACT_THRESHOLD
    threshold = int(max_tokens * threshold_pct)

    if estimated < threshold and not force:
        return messages, False

    # Show compaction notice
    pct = int(estimated / max_tokens * 100)
    print(f"\n  {DIM}⟳ Context {pct}% full (~{estimated:,} tokens), compacting...{RESET}")
    sys.stdout.flush()

    compacted = _compact_conversation(messages, model, api_key, base_url, max_tokens)
    new_estimated = _estimate_conversation_tokens(compacted, system_prompt)
    new_pct = int(new_estimated / max_tokens * 100)
    print(f"  {DIM}✔ Compacted: {pct}% → {new_pct}% (~{new_estimated:,} tokens){RESET}")

    return compacted, True


# ---------------------------------------------------------------------------
# Task cancellation (lines 17318–17403 of claude_cli.py)
# ---------------------------------------------------------------------------

# --- Task cancellation ---

class TaskCancelled(Exception):
    """Raised when the user presses Escape to cancel the current task."""
    pass


class CancelToken:
    """Simple cancellation token — compatible with EscapeWatcher interface."""

    def __init__(self):
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self):
        self._cancelled.set()

    def start(self):
        pass  # No-op — no background thread needed

    def stop(self):
        pass  # No-op


class EscapeWatcher:
    """Background thread that monitors for Escape key press in raw terminal mode."""

    def __init__(self):
        self._cancelled = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._old_settings = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def start(self):
        self._cancelled.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)

    def _watch(self):
        import termios
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            return
        try:
            # Set input to non-canonical mode WITHOUT disabling output processing.
            # tty.setraw() clears OPOST which breaks \n -> \r\n conversion for
            # all print() output. Instead, only change what we need for input.
            new_settings = termios.tcgetattr(fd)
            # Disable canonical mode (ICANON) and echo (ECHO) in local flags
            new_settings[3] = new_settings[3] & ~(termios.ICANON | termios.ECHO)
            # Set minimum chars to read = 0, timeout = 0 (non-blocking)
            new_settings[6][termios.VMIN] = 0
            new_settings[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new_settings)

            while not self._stop.is_set():
                if select.select([fd], [], [], 0.1)[0]:
                    ch = os.read(fd, 1)
                    if ch == b'\x1b':  # Escape key
                        self._cancelled.set()
                        break
                    elif ch in (b'o', b'O'):
                        # Toggle tool output visibility live
                        _toggle_tool_output()
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except termios.error:
                pass


# --- Client Execution Mode (proxy LLM + web tools through browser) ---
