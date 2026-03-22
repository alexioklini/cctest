#!/usr/bin/env python3
"""Brain Agent — Agentic CLI for interacting with LLM APIs."""

VERSION = "3.4.0"
VERSION_DATE = "2026-03-22"
CHANGELOG = [
    ("3.4.0", "2026-03-22", "Remote nodes: list_nodes tool, node settings UI (token, allowed tools, max concurrent, timeout), node.py launchd install/uninstall/status, node connection logging, dynamic sidebar refresh on async LLM summary"),
    ("3.3.0", "2026-03-22", "Enhanced projects: AI note editing via tools, chat transcript indexing in QMD, LLM chat summaries, deep chat search, project panel search + counts + delete, chat attachments in sidebar, auto-refresh polling, prompt refinement in notes"),
    ("3.2.0", "2026-03-22", "Project Notes system with AI-assisted editing, 3-column layout (sidebar + center + project panel), notes as first-class knowledge graph citizens, note editor with formatting toolbar and AI chat sidebar"),
    ("3.1.0", "2026-03-21", "Auto memory creation, continuous session summarization, knowledge graph visualization + auto-discovery, chat file attachments, model-aware max_tokens, Bootstrap Icons avatars, sidebar redesign (Projects + Chats), Tools settings, improved fallback ordering, prompt refinement improvements"),
    ("3.0.0", "2026-03-20", "All P2 features: provider fallback with retry, backup/export/import, notifications (webhook/email/in-app), observability tracing + audit trail, dynamic MCP client, multi-modal (image upload/vision), remote nodes, multi-messaging adapter framework"),
    ("2.1.0", "2026-03-20", "Agent workflows (YAML stages with approval gates), Web UI left sidebar replacing top agent cards, consolidated status bar, mobile responsive"),
    ("2.0.0", "2026-03-20", "Projects system, document ingestion (PDF/DOCX/HTML/URL), watched folders, knowledge graph memory with relationship traversal, chat scoping per project"),
    ("1.7.0", "2026-03-20", "Plan mode, web result caching, streaming tool output, cost tracking + rate limiting, custom slash commands, LLM input refinement"),
    ("1.6.0", "2026-03-20", "TUI feature parity (30+ slash commands), slash command popup menus in both TUI and Web UI, roadmap"),
    ("1.5.3", "2026-03-20", "Thread-safe agent context, fix old chat provider resolution, per-collection QMD debounce, YAML-safe frontmatter, memory filename collision prevention, concurrent scheduler, thread-local cleanup, recall content limit increase"),
    ("1.5.2", "2026-03-20", "Fix memory summary refresh (direct execution instead of scheduler indirection), fix QMD index path normalization (underscore/hyphen mismatch), QMD collection health stats in settings UI"),
    ("1.5.1", "2026-03-18", "MiniMax provider support, Add Model UI, fix QMD session leak, memory_shared returns full content, in-process Telegram bot, lightweight QMD health check"),
    ("1.5.0", "2026-03-18", "Settings dashboard (Server/QMD/Models/Telegram/Providers), agent activity indicators, QMD document browser with index health, smart model routing, self-healing QMD index keeper"),
    ("1.4.0", "2026-03-17", "QMD hybrid memory search, SSE error handling, server resilience"),
    ("1.2.0", "2026-03-16", "Multi-provider routing, Gmail, scheduler dashboard, SQLite resilience, Cloudflare deployment"),
    ("1.1.0", "2026-03-14", "MCP support: stdio + SSE transports, per-agent + global servers"),
    ("1.0.0", "2026-03-14", "Background threads per agent, async delegation, task status/cancel"),
    ("0.9.0", "2026-03-14", "Skills system: on-demand SKILL.md loading, per-agent + global"),
    ("0.8.0", "2026-03-14", "Multi-agent system with soul.md, delegation, /agent switching"),
    ("0.7.0", "2026-03-14", "Persistent memory system with SQLite FTS5, per-agent isolation"),
    ("0.6.0", "2026-03-14", "Context window management with auto-compaction at 75%"),
    ("0.5.0", "2026-03-14", "Full agent toolkit: file ops, shell, search, web fetch, edit"),
    ("0.4.0", "2026-03-14", "Escape to cancel, dynamic terminal rendering, startup greeting"),
    ("0.3.0", "2026-03-13", "Exa web search tool with agentic tool-use loop"),
    ("0.2.0", "2026-03-12", "Interactive TUI with spinner, markdown rendering, model switching"),
    ("0.1.0", "2026-03-10", "Initial release — streaming chat, model fallback, SSE parsing"),
]

import argparse
import collections
import datetime
import fnmatch
import logging
import glob as globmod
import json
import os
import random
import re
import select
import signal
import shutil
import socket
import subprocess
import sys
import termios
import threading
import time
import tty
import urllib.request
import urllib.error


# --- Web Result Cache ---

class WebCache:
    """Thread-safe LRU cache with TTL for web results."""

    def __init__(self, max_entries: int = 200, ttl: int = 900):
        self._cache = collections.OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                del self._cache[key]
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: dict):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            self._cache[key] = (time.time(), value)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "ttl": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(1, self.hits + self.misses) * 100, 1),
            }


_web_cache = WebCache()


# --- Plan Mode ---

READONLY_TOOLS = frozenset({
    "read_file", "list_directory", "search_files", "web_fetch", "exa_search",
    "memory_recall", "memory_shared", "task_status", "list_nodes", "schedule_list",
    "schedule_history", "use_skill", "gmail_inbox", "gmail_read", "gmail_search",
})

PLAN_MODE_PROMPT = (
    "\n\nPLAN MODE ACTIVE: You are in read-only planning mode. "
    "You may ONLY use read-only tools (read_file, list_directory, search_files, "
    "web_fetch, exa_search, memory_recall, memory_shared, task_status, etc.). "
    "Do NOT attempt to write files, execute commands, store memory, send emails, "
    "or delegate tasks. Instead, describe a detailed plan of what you WOULD do, "
    "including specific file paths, commands, and steps.\n"
)


# --- Tool Definitions ---

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns the full text content. "
            "Use offset and limit to read a specific range of lines from large files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path to read"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-based, default: 1)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default: all)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or overwrite an existing file with the given content. "
            "Creates parent directories automatically if they don't exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write to"},
                "content": {"type": "string", "description": "The full content to write to the file"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit an existing file by replacing an exact string match with new content. "
            "The old_string must match exactly (including whitespace/indentation). "
            "Use replace_all=true to replace every occurrence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at a given path. "
            "Supports glob patterns (e.g. '*.py', '**/*.js'). "
            "Returns file names, sizes, and types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
                "pattern": {"type": "string", "description": "Glob pattern to filter results (e.g. '*.py', '**/*.ts')"},
                "recursive": {"type": "boolean", "description": "List recursively (default: false)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a regex pattern across files. Returns matching lines with file paths and line numbers. "
            "Similar to grep/ripgrep. Use glob to filter which files to search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: current directory)"},
                "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
                "max_results": {"type": "integer", "description": "Maximum number of matches to return (default: 50)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "execute_command",
        "description": (
            "Execute a shell command and return its output (stdout + stderr). "
            "Commands run in the current working directory with no TTY (non-interactive). "
            "IMPORTANT: Only use non-interactive commands. For example use 'top -l 1' (not 'top'), "
            "'ps aux' (not 'htop'), 'cat' (not 'less'). "
            "Use this for: running scripts, git commands, package managers, compiling, testing, "
            "system administration, or any shell operation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory for the command (default: current directory)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch content from a URL. Returns the response body as text. "
            "Works with web pages, APIs, raw files, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "method": {"type": "string", "description": "HTTP method (default: GET)", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                "headers": {"type": "object", "description": "Additional HTTP headers as key-value pairs"},
                "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)"},
                "max_length": {"type": "integer", "description": "Max response length in characters (default: 50000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "gmail_inbox",
        "description": "List recent emails from Gmail inbox. Returns subject, from, date for each email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of emails to return (default: 10)"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": [],
        },
    },
    {
        "name": "gmail_read",
        "description": "Read a specific email by its ID. Returns full body, attachments list, headers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID from gmail_inbox or gmail_search"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "gmail_search",
        "description": "Search emails using Gmail search syntax (from:, subject:, is:unread, after:, has:attachment, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_send",
        "description": "Send an email via Gmail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text)"},
                "cc": {"type": "string", "description": "CC email address (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply",
        "description": "Reply to an existing email by its ID. Preserves threading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID to reply to"},
                "body": {"type": "string", "description": "Reply body (plain text)"},
            },
            "required": ["id", "body"],
        },
    },
    {
        "name": "exa_search",
        "description": (
            "Search the web using Exa AI for current, relevant information. "
            "Use this tool whenever the user asks to search the web, look something up, "
            "find recent news, or get current information about any topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or topic to look up",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 5)",
                    "minimum": 1,
                    "maximum": 20,
                },
                "category": {
                    "type": "string",
                    "description": "Optional category: news, research paper, tweet, company, people",
                    "enum": ["news", "research paper", "tweet", "company", "people"],
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_store",
        "description": (
            "Store a memory for later recall. Use this to remember important information, "
            "user preferences, decisions, project context, or anything that should persist "
            "across conversations. Memories are searchable by keyword."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short unique name for this memory (used as identifier)"},
                "content": {"type": "string", "description": "The memory content to store"},
                "description": {"type": "string", "description": "One-line description for search indexing"},
                "type": {"type": "string", "description": "Memory type", "enum": ["user", "project", "feedback", "reference", "general"]},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "memory_recall",
        "description": (
            "Search and recall stored memories. Use this when you need context from previous "
            "conversations, user preferences, project decisions, or any previously stored information. "
            "Returns matching memories ranked by relevance, plus related memories discovered via "
            "the knowledge graph (automatically follows relationship links for richer context)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords). Leave empty to list all memories."},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
                "type": {"type": "string", "description": "Filter by memory type", "enum": ["user", "project", "feedback", "reference", "general"]},
            },
            "required": [],
        },
    },
    {
        "name": "memory_delete",
        "description": "Delete a stored memory by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the memory to delete"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "memory_shared",
        "description": (
            "Access shared memory at global or team scope. Global scope (default) accesses the main agent's "
            "memory containing infrastructure, user preferences, and project-wide decisions. "
            "Team scope accesses the team head's memory for team-level knowledge. "
            "Use this when you need context that isn't in your own memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform", "enum": ["recall", "store"]},
                "scope": {"type": "string", "description": "Memory scope: 'global' for main agent (default), 'team' for team head's memory", "enum": ["global", "team"], "default": "global"},
                "query": {"type": "string", "description": "Search query for recall, or empty to list all"},
                "name": {"type": "string", "description": "Memory name (required for store)"},
                "content": {"type": "string", "description": "Content to store (required for store)"},
                "description": {"type": "string", "description": "One-line description (for store)"},
                "type": {"type": "string", "description": "Memory type", "enum": ["user", "project", "feedback", "reference", "general"]},
                "limit": {"type": "integer", "description": "Max results for recall (default: 10)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "delegate_task",
        "description": (
            "Delegate a task to another agent. Runs in a background thread with its own context. "
            "By default waits for result (wait=true). Set wait=false for async execution, "
            "then use task_status to poll for completion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent ID (e.g. 'research', 'health')"},
                "task": {"type": "string", "description": "Task description for the target agent"},
                "wait": {"type": "boolean", "description": "Wait for result (default: true). Set false for async."},
                "model": {"type": "string", "description": "Override model for this task (optional)"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "task_status",
        "description": (
            "Check status of background tasks. Call with task_id to check a specific task, "
            "or without to list all tasks. Returns status (running/completed/cancelled/error) and result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to check (optional, lists all if empty)"},
            },
            "required": [],
        },
    },
    {
        "name": "task_cancel",
        "description": "Cancel a running background task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to cancel"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "use_skill",
        "description": (
            "Load a skill's instructions into context. Skills provide specialized knowledge "
            "for specific tasks (e.g. github, docker, swift). Call this BEFORE performing a task "
            "that matches a skill. The skill's instructions will be returned as text — follow them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Name of the skill to load"},
            },
            "required": ["skill"],
        },
    },
    {
        "name": "schedule_list",
        "description": "List all scheduled tasks with their status, next run time, and configuration.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_nodes",
        "description": (
            "List all registered remote nodes with their status, hostname, OS, tags, "
            "allowed tools, and resource usage. Use this to check what remote nodes are "
            "available before routing commands to them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "schedule_history",
        "description": "Get execution history for scheduled tasks. Shows status, results, and timestamps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by schedule name (optional)"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "mcp_connect",
        "description": (
            "Connect to an MCP server at runtime. Discovers tools from the server and makes them "
            "available as mcp_<name>_<tool> tools. Use transport='sse' for HTTP servers, 'stdio' for local commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "MCP server URL (for SSE) or command (for stdio)"},
                "name": {"type": "string", "description": "Friendly name for this connection"},
                "transport": {"type": "string", "description": "Transport type: 'sse' (default) or 'stdio'", "enum": ["sse", "stdio"]},
                "persist": {"type": "boolean", "description": "Save to mcp.json for reconnect on restart (default: false)"},
            },
            "required": ["url", "name"],
        },
    },
    {
        "name": "mcp_disconnect",
        "description": "Disconnect from a runtime MCP server. Its tools will no longer be available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the MCP server to disconnect"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "mcp_servers",
        "description": "List all connected MCP servers with their tools and status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# Build OpenAI-compatible format automatically
TOOL_DEFINITIONS_OPENAI = []
for _td in TOOL_DEFINITIONS:
    TOOL_DEFINITIONS_OPENAI.append({
        "type": "function",
        "function": {
            "name": _td["name"],
            "description": _td["description"],
            "parameters": {
                "type": _td["input_schema"]["type"],
                "properties": _td["input_schema"]["properties"],
                "required": _td["input_schema"].get("required", []),
            },
        },
    })


# --- Tool Execution ---

def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _route_to_node(tool_name: str, args: dict) -> str | None:
    """If args contain 'node', route to remote node via server API. Returns result string or None for local."""
    node = args.pop("node", None)
    if not node:
        return None
    try:
        import urllib.request
        body = json.dumps({"node": node, "tool": tool_name, "params": args}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8420/v1/nodes/execute",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if "error" in result:
            return _err(f"Node '{node}': {result['error']}")
        return _ok(result)
    except Exception as e:
        return _err(f"Node routing error: {e}")


def tool_read_file(args: dict) -> str:
    node_result = _route_to_node("read_file", args)
    if node_result is not None:
        return node_result
    path = args.get("path", "")
    offset = args.get("offset", 1)
    limit = args.get("limit")
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(0, offset - 1)
        end = start + limit if limit else total
        selected = lines[start:end]
        # Number lines
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>6}\t{line.rstrip()}")
        content = "\n".join(numbered)
        return _ok({"path": path, "total_lines": total, "showing": f"{start+1}-{min(end, total)}", "content": content})
    except Exception as e:
        return _err(f"read_file: {e}")


def _maybe_qmd_reindex(path: str) -> None:
    """If path is a .md file inside an agent dir, trigger debounced QMD reindex."""
    if not path.endswith(".md"):
        return
    agents_dir = os.path.realpath(AGENTS_DIR)
    real_path = os.path.realpath(path)
    if not real_path.startswith(agents_dir + os.sep):
        return
    rel = real_path[len(agents_dir) + 1:]
    collection = rel.split(os.sep)[0]
    _qmd_debounced_embed(collection)


def tool_write_file(args: dict) -> str:
    node_result = _route_to_node("write_file", args)
    if node_result is not None:
        return node_result
    path = args.get("path", "")
    content = args.get("content", "")
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        size = os.path.getsize(path)
        _maybe_qmd_reindex(path)
        # Emit file_created event for attachment tracking
        ecb = getattr(_thread_local, 'event_callback', None)
        if ecb:
            ecb("file_created", {
                "path": path,
                "name": os.path.basename(path),
                "size": size,
                "action": "created",
            })
        return _ok({"path": path, "size": size, "status": "written"})
    except Exception as e:
        return _err(f"write_file: {e}")


def tool_edit_file(args: dict) -> str:
    path = args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        with open(path, "r") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return _err(f"edit_file: old_string not found in {path}")
        if count > 1 and not replace_all:
            return _err(f"edit_file: old_string found {count} times — use replace_all=true or provide a more specific match")
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        _maybe_qmd_reindex(path)
        # Emit file_created event for attachment tracking
        ecb = getattr(_thread_local, 'event_callback', None)
        if ecb:
            ecb("file_created", {
                "path": path,
                "name": os.path.basename(path),
                "size": os.path.getsize(path),
                "action": "modified",
            })
        return _ok({"path": path, "replacements": count if replace_all else 1, "status": "edited"})
    except Exception as e:
        return _err(f"edit_file: {e}")


def tool_list_directory(args: dict) -> str:
    node_result = _route_to_node("list_directory", args)
    if node_result is not None:
        return node_result
    path = args.get("path", ".")
    pattern = args.get("pattern")
    recursive = args.get("recursive", False)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        if pattern:
            if recursive or "**" in pattern:
                full_pattern = os.path.join(path, pattern)
                entries = globmod.glob(full_pattern, recursive=True)
            else:
                full_pattern = os.path.join(path, pattern)
                entries = globmod.glob(full_pattern)
        elif recursive:
            entries = []
            for root, dirs, files in os.walk(path):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if not f.startswith("."):
                        entries.append(os.path.join(root, f))
        else:
            entries = [os.path.join(path, e) for e in os.listdir(path)]

        results = []
        for entry in sorted(entries)[:500]:
            try:
                st = os.stat(entry)
                is_dir = os.path.isdir(entry)
                results.append({
                    "name": os.path.relpath(entry, path),
                    "type": "directory" if is_dir else "file",
                    "size": st.st_size if not is_dir else None,
                })
            except OSError:
                results.append({"name": os.path.relpath(entry, path), "type": "unknown"})

        return _ok({"path": path, "count": len(results), "entries": results})
    except Exception as e:
        return _err(f"list_directory: {e}")


def tool_search_files(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    file_glob = args.get("glob")
    case_insensitive = args.get("case_insensitive", False)
    max_results = args.get("max_results", 50)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        matches = []
        files_searched = 0

        if os.path.isfile(path):
            file_list = [path]
        else:
            file_list = []
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
                for f in files:
                    if f.startswith("."):
                        continue
                    fp = os.path.join(root, f)
                    if file_glob and not fnmatch.fnmatch(f, file_glob):
                        continue
                    file_list.append(fp)

        for fp in file_list:
            if len(matches) >= max_results:
                break
            files_searched += 1
            try:
                with open(fp, "r", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if regex.search(line):
                            matches.append({
                                "file": os.path.relpath(fp, path) if not os.path.isfile(path) else fp,
                                "line": lineno,
                                "text": line.rstrip()[:500],
                            })
                            if len(matches) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue

        return _ok({"pattern": pattern, "path": path, "files_searched": files_searched,
                     "match_count": len(matches), "matches": matches})
    except re.error as e:
        return _err(f"search_files: invalid regex: {e}")
    except Exception as e:
        return _err(f"search_files: {e}")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences, control chars, and normalize whitespace."""
    text = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\[\?[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\([A-Z]", "", text)
    text = re.sub(r"\033][^\a]*\a", "", text)  # OSC sequences
    text = re.sub(r"\r", "", text)  # Carriage returns
    text = text.replace("\t", "    ")  # Expand tabs
    return text


def _streaming_execute_command(command: str, timeout: int, cwd: str | None,
                               event_callback, tool_use_id: str) -> str:
    """Execute command with streaming output via event_callback."""
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["PAGER"] = "cat"
    env["COLUMNS"] = "200"
    env["LINES"] = "50"

    proc = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, cwd=cwd, env=env,
        start_new_session=True,
    )
    output_lines = []
    import io
    deadline = time.time() + timeout
    try:
        # Read stdout line by line, emitting events
        for raw_line in iter(proc.stdout.readline, b''):
            if time.time() > deadline:
                import signal as sig
                try:
                    os.killpg(proc.pid, sig.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait(timeout=5)
                line_text = _strip_ansi(raw_line.decode("utf-8", errors="replace"))
                output_lines.append(line_text)
                output = "".join(output_lines)
                if len(output) > 50000:
                    output = output[:50000] + "\n... (truncated)"
                return _err(f"execute_command: timed out after {timeout}s\n{output}")
            line_text = _strip_ansi(raw_line.decode("utf-8", errors="replace"))
            output_lines.append(line_text)
            if event_callback:
                event_callback("tool_output", {
                    "tool_use_id": tool_use_id,
                    "line": line_text.rstrip("\n"),
                })
        proc.stdout.close()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        import signal as sig
        try:
            os.killpg(proc.pid, sig.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)

    output = "".join(output_lines)
    stderr_data = proc.stderr.read() if proc.stderr else b""
    proc.stderr.close()
    if stderr_data:
        err_text = _strip_ansi(stderr_data.decode("utf-8", errors="replace"))
        output += ("\n--- stderr ---\n" + err_text) if output else err_text
    if len(output) > 50000:
        output = output[:50000] + "\n... (truncated)"
    return _ok({"command": command, "exit_code": proc.returncode, "output": output})


def tool_execute_command(args: dict) -> str:
    node_result = _route_to_node("execute_command", args)
    if node_result is not None:
        return node_result
    command = args.get("command", "")
    cwd = args.get("cwd")
    # Read default timeout from tools_config (per-call timeout still overrides)
    _exec_cfg = get_tool_config().get("execute_command", {})
    default_timeout = _exec_cfg.get("timeout", 15)
    timeout = args.get("timeout", default_timeout)
    # Check banned commands
    banned = _exec_cfg.get("banned_commands", [])
    for b in banned:
        if b and b in command:
            return _err(f"execute_command: command contains banned pattern '{b}'")
    try:
        if cwd:
            cwd = os.path.expanduser(cwd)

        # Use streaming version if event_callback is available
        ecb = getattr(_thread_local, 'event_callback', None)
        tuid = getattr(_thread_local, 'tool_use_id', None)
        if ecb and tuid:
            return _streaming_execute_command(command, timeout, cwd, ecb, tuid)

        # Force non-interactive environment
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        env["PAGER"] = "cat"
        env["COLUMNS"] = "200"
        env["LINES"] = "50"

        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, cwd=cwd, env=env,
            start_new_session=True,  # own process group so we can kill the tree
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            import signal as sig
            try:
                os.killpg(proc.pid, sig.SIGKILL)
            except OSError:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            output = _strip_ansi(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output += "\n--- stderr ---\n" + _strip_ansi(stderr.decode("utf-8", errors="replace"))
            if len(output) > 50000:
                output = output[:50000] + "\n... (truncated)"
            return _err(f"execute_command: timed out after {timeout}s (partial output below). Use non-interactive commands, e.g. 'top -l 1' not 'top'.\n{output}")

        output = _strip_ansi(stdout.decode("utf-8", errors="replace"))
        if stderr:
            err_text = _strip_ansi(stderr.decode("utf-8", errors="replace"))
            output += ("\n--- stderr ---\n" + err_text) if output else err_text
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"
        return _ok({"command": command, "exit_code": proc.returncode, "output": output})
    except Exception as e:
        return _err(f"execute_command: {e}")


# --- Gmail Tools ---

import imaplib
import smtplib
import email
import email.mime.text
import email.mime.multipart
from email.header import decode_header as _decode_header


def _gmail_config():
    """Load Gmail credentials from tools_config, falling back to gmail.json."""
    # Check tools_config first
    tcfg = get_tool_config().get("gmail", {})
    if tcfg.get("email") and tcfg.get("app_password"):
        return {"email": tcfg["email"], "app_password": tcfg["app_password"]}
    # Fall back to gmail.json
    config_path = os.path.join(AGENTS_DIR, "main", "gmail.json")
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _decode_mime_header(raw):
    """Decode a MIME-encoded header value."""
    if not raw:
        return ""
    parts = _decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(data))
    return " ".join(decoded)


def _get_email_body(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback to HTML
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    # Strip HTML tags roughly
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def tool_gmail_inbox(args: dict) -> str:
    """List recent emails from Gmail inbox."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured. Create agents/main/gmail.json with email and app_password.")
    limit = args.get("limit", 10)
    folder = args.get("folder", "INBOX")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select(folder, readonly=True)
        _, data = imap.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:]  # most recent
        ids.reverse()

        emails = []
        for eid in ids:
            _, msg_data = imap.fetch(eid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append({
                "id": eid.decode(),
                "from": _decode_mime_header(msg.get("From", "")),
                "subject": _decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
            })
        imap.logout()
        return _ok({"folder": folder, "count": len(emails), "emails": emails})
    except Exception as e:
        return _err(f"gmail_inbox: {e}")


def tool_gmail_read(args: dict) -> str:
    """Read a specific email by ID."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    email_id = args.get("id", "")
    folder = args.get("folder", "INBOX")
    if not email_id:
        return _err("gmail_read: email id is required")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select(folder, readonly=True)
        _, msg_data = imap.fetch(email_id.encode(), "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        body = _get_email_body(msg)
        # Truncate long bodies
        if len(body) > 10000:
            body = body[:10000] + "\n...(truncated)"
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                fn = part.get_filename()
                if fn:
                    attachments.append(_decode_mime_header(fn))
        imap.logout()
        return _ok({
            "id": email_id,
            "from": _decode_mime_header(msg.get("From", "")),
            "to": _decode_mime_header(msg.get("To", "")),
            "cc": _decode_mime_header(msg.get("Cc", "")),
            "subject": _decode_mime_header(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
            "body": body,
            "attachments": attachments,
            "message_id": msg.get("Message-ID", ""),
        })
    except Exception as e:
        return _err(f"gmail_read: {e}")


def tool_gmail_search(args: dict) -> str:
    """Search emails using Gmail search syntax."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    if not query:
        return _err("gmail_search: query is required")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select("INBOX", readonly=True)
        # Gmail supports X-GM-RAW for full Gmail search syntax
        _, data = imap.search(None, f'X-GM-RAW "{query}"')
        ids = data[0].split()
        ids = ids[-limit:]
        ids.reverse()

        emails = []
        for eid in ids:
            _, msg_data = imap.fetch(eid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append({
                "id": eid.decode(),
                "from": _decode_mime_header(msg.get("From", "")),
                "subject": _decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
            })
        imap.logout()
        return _ok({"query": query, "count": len(emails), "emails": emails})
    except Exception as e:
        return _err(f"gmail_search: {e}")


def tool_gmail_send(args: dict) -> str:
    """Send an email via Gmail SMTP."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    cc = args.get("cc", "")
    if not to or not subject:
        return _err("gmail_send: to and subject are required")
    try:
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = cfg["email"]
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(cfg["email"], cfg["app_password"])
            recipients = [to] + ([cc] if cc else [])
            smtp.send_message(msg, to_addrs=recipients)

        return _ok({"status": "sent", "to": to, "subject": subject})
    except Exception as e:
        return _err(f"gmail_send: {e}")


def tool_gmail_reply(args: dict) -> str:
    """Reply to an email."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    email_id = args.get("id", "")
    body = args.get("body", "")
    if not email_id or not body:
        return _err("gmail_reply: id and body are required")
    try:
        # Fetch original email to get headers
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select("INBOX", readonly=True)
        _, msg_data = imap.fetch(email_id.encode(), "(RFC822)")
        raw = msg_data[0][1]
        original = email.message_from_bytes(raw)
        imap.logout()

        # Build reply
        reply_to = original.get("Reply-To") or original.get("From", "")
        subject = original.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = cfg["email"]
        msg["To"] = reply_to
        msg["Subject"] = subject
        msg["In-Reply-To"] = original.get("Message-ID", "")
        msg["References"] = original.get("Message-ID", "")
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(cfg["email"], cfg["app_password"])
            smtp.send_message(msg)

        return _ok({"status": "replied", "to": reply_to, "subject": subject})
    except Exception as e:
        return _err(f"gmail_reply: {e}")


def tool_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    method = args.get("method", "GET")
    headers = args.get("headers", {})
    body = args.get("body")
    max_length = args.get("max_length", 50000)
    force_fresh = args.get("force_fresh", False)
    # Read timeout and max_size from tools_config
    _wf_cfg = get_tool_config().get("web_fetch", {})
    _wf_timeout = _wf_cfg.get("timeout", 30)
    _wf_max_size_mb = _wf_cfg.get("max_size_mb", 10)

    # Check cache for GET requests without body
    cache_key = url if method == "GET" and not body else None
    if cache_key and not force_fresh:
        cached = _web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return _ok(cached)

    try:
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req_headers.update(headers)
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=_wf_timeout) as resp:
            raw = resp.read(_wf_max_size_mb * 1024 * 1024)
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        result = {"url": url, "status": resp.status, "length": len(text), "content": text}
        if cache_key:
            _web_cache.put(cache_key, dict(result))
        return _ok(result)
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:5000]
        except Exception:
            pass
        return _err(f"web_fetch: HTTP {e.code} {e.reason}\n{body_text}")
    except Exception as e:
        return _err(f"web_fetch: {e}")


def exa_search(query: str, num_results: int = 5, category: str | None = None,
               force_fresh: bool = False) -> str:
    """Execute an Exa web search and return JSON results. Uses stdlib only."""
    # Check cache
    cache_key = f"exa:{query}:{num_results}:{category or ''}"
    if not force_fresh:
        cached = _web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return json.dumps(cached, indent=1)

    # Read API key from tools_config, fall back to env var, then hardcoded default
    _tcfg = get_tool_config().get("exa_search", {})
    api_key = _tcfg.get("api_key") or os.environ.get("EXA_API_KEY", "97dbd594-f7b4-4866-9a8e-6a297e3df576")

    body = {
        "query": query,
        "type": "auto",
        "num_results": num_results,
        "contents": {"highlights": {"max_characters": 4000}},
    }
    if category:
        body["category"] = category

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        req = urllib.request.Request(
            "https://api.exa.ai/search",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # Handle gzip encoding if server sends it anyway
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            response_data = json.loads(raw.decode("utf-8"))

        results = []
        for r in response_data.get("results", []):
            highlights = r.get("highlights", [])
            snippet = " ".join(highlights) if highlights else ""
            results.append({
                "title": r.get("title", ""),
                "link": r.get("url", ""),
                "snippet": snippet,
            })

        search_info = {"query": query, "results": results, "result_count": len(results)}
        if category:
            search_info["category"] = category
        if not results:
            search_info["message"] = "No search results found. Try a different query."
        if results:
            _web_cache.put(cache_key, dict(search_info))
        return json.dumps(search_info, indent=1)

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"query": query, "results": [], "error": f"HTTP {e.code}: {error_body}"})
    except Exception as e:
        return json.dumps({"query": query, "results": [], "error": str(e)})


# --- MCP Client ---

class MCPStdioClient:
    """MCP client over stdio — launches a subprocess and communicates via JSON-RPC."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.process = None
        self._request_id = 0
        self._lock = threading.Lock()
        self.tools: list[dict] = []

    def start(self) -> bool:
        """Start the MCP server subprocess."""
        try:
            run_env = os.environ.copy()
            if self.env:
                run_env.update(self.env)
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=run_env, start_new_session=True,
            )
            # Initialize
            resp = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "brain-agent", "version": VERSION},
            })
            if resp and not resp.get("error"):
                # Send initialized notification
                self._send_notification("notifications/initialized", {})
                # List tools
                tools_resp = self._send_request("tools/list", {})
                if tools_resp and tools_resp.get("result"):
                    self.tools = tools_resp["result"].get("tools", [])
                return True
            return False
        except Exception:
            return False

    def stop(self):
        """Stop the subprocess."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the MCP server. Returns JSON string."""
        resp = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if resp and resp.get("result"):
            content = resp["result"].get("content", [])
            # Extract text from content blocks
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    else:
                        texts.append(json.dumps(block))
                elif isinstance(block, str):
                    texts.append(block)
            return json.dumps({"result": "\n".join(texts)})
        elif resp and resp.get("error"):
            return json.dumps({"error": resp["error"].get("message", str(resp["error"]))})
        return json.dumps({"error": "No response from MCP server"})

    def _send_request(self, method: str, params: dict) -> dict | None:
        with self._lock:
            self._request_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            return self._send_and_receive(msg)

    def _send_notification(self, method: str, params: dict):
        with self._lock:
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            self._write(msg)

    def _send_and_receive(self, msg: dict) -> dict | None:
        try:
            self._write(msg)
            return self._read()
        except Exception:
            return None

    def _write(self, msg: dict):
        if not self.process or not self.process.stdin:
            return
        data = json.dumps(msg)
        self.process.stdin.write(f"{data}\n".encode("utf-8"))
        self.process.stdin.flush()

    def _read(self) -> dict | None:
        if not self.process or not self.process.stdout:
            return None
        # Read lines until we get a JSON-RPC response (skip notifications)
        while True:
            line = self.process.stdout.readline()
            if not line:
                return None
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if "id" in msg:  # It's a response
                    return msg
                # Skip notifications
            except json.JSONDecodeError:
                continue


class MCPSSEClient:
    """MCP client over SSE/HTTP — connects to a running server."""

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self._request_id = 0
        self.tools: list[dict] = []

    def start(self) -> bool:
        """Initialize connection and list tools."""
        try:
            resp = self._post("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "brain-agent", "version": VERSION},
            })
            if resp and not resp.get("error"):
                self._post("notifications/initialized", {}, is_notification=True)
                tools_resp = self._post("tools/list", {})
                if tools_resp and tools_resp.get("result"):
                    self.tools = tools_resp["result"].get("tools", [])
                return True
            return False
        except Exception:
            return False

    def stop(self):
        pass  # No cleanup needed for HTTP

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the MCP server."""
        resp = self._post("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if resp and resp.get("result"):
            content = resp["result"].get("content", [])
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    else:
                        texts.append(json.dumps(block))
                elif isinstance(block, str):
                    texts.append(block)
            return json.dumps({"result": "\n".join(texts)})
        elif resp and resp.get("error"):
            return json.dumps({"error": resp["error"].get("message", str(resp["error"]))})
        return json.dumps({"error": "No response from MCP server"})

    def _post(self, method: str, params: dict, is_notification: bool = False) -> dict | None:
        self._request_id += 1
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if not is_notification:
            msg["id"] = self._request_id

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.headers)

        try:
            data = json.dumps(msg).encode("utf-8")
            req = urllib.request.Request(
                f"{self.url}/message" if "/message" not in self.url else self.url,
                data=data, headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                if body.strip():
                    return json.loads(body)
                return {} if is_notification else None
        except Exception:
            return None


class MCPManager:
    """Manages MCP server connections for an agent."""

    def __init__(self):
        self.clients: dict[str, MCPStdioClient | MCPSSEClient] = {}
        self._tool_to_server: dict[str, str] = {}  # tool_name -> server_name

    def load_config(self, config_path: str) -> int:
        """Load MCP servers from a mcp.json config file. Returns count of servers started."""
        if not os.path.exists(config_path):
            return 0
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        for name, cfg in config.items():
            transport = cfg.get("transport", "stdio")
            if transport == "stdio":
                client = MCPStdioClient(
                    name=name,
                    command=cfg.get("command", ""),
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
            elif transport in ("sse", "http"):
                client = MCPSSEClient(
                    name=name,
                    url=cfg.get("url", ""),
                    headers=cfg.get("headers"),
                )
            else:
                continue

            if client.start():
                self.clients[name] = client
                # Map tool names to server
                for tool in client.tools:
                    tool_name = f"mcp_{name}_{tool['name']}"
                    self._tool_to_server[tool_name] = name
                count += 1
        return count

    def get_tool_definitions(self) -> list[dict]:
        """Get all MCP tool definitions in Anthropic format."""
        defs = []
        for server_name, client in self.clients.items():
            for tool in client.tools:
                # Prefix tool names to avoid conflicts
                prefixed_name = f"mcp_{server_name}_{tool['name']}"
                defs.append({
                    "name": prefixed_name,
                    "description": f"[MCP:{server_name}] {tool.get('description', '')}",
                    "input_schema": tool.get("inputSchema", {
                        "type": "object", "properties": {}, "required": [],
                    }),
                })
        return defs

    def get_tool_definitions_openai(self) -> list[dict]:
        """Get all MCP tool definitions in OpenAI format."""
        defs = []
        for td in self.get_tool_definitions():
            defs.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": {
                        "type": td["input_schema"].get("type", "object"),
                        "properties": td["input_schema"].get("properties", {}),
                        "required": td["input_schema"].get("required", []),
                    },
                },
            })
        return defs

    def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Call an MCP tool by its prefixed name."""
        server_name = self._tool_to_server.get(prefixed_name)
        if not server_name or server_name not in self.clients:
            return json.dumps({"error": f"MCP tool '{prefixed_name}' not found"})
        # Strip prefix to get original tool name
        prefix = f"mcp_{server_name}_"
        original_name = prefixed_name[len(prefix):]
        return self.clients[server_name].call_tool(original_name, arguments)

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tool_to_server

    def list_servers(self) -> list[dict]:
        """List all connected MCP servers and their tools."""
        result = []
        for name, client in self.clients.items():
            result.append({
                "name": name,
                "transport": "stdio" if isinstance(client, MCPStdioClient) else "sse",
                "tools": [t["name"] for t in client.tools],
                "tool_count": len(client.tools),
            })
        return result

    def connect_runtime(self, url: str, name: str, transport: str = "sse") -> dict:
        """Connect to an MCP server at runtime. Returns status dict with discovered tools."""
        if name in self.clients:
            return {"error": f"Server '{name}' is already connected"}
        if transport == "stdio":
            # url is treated as command, split on spaces for args
            parts = url.split()
            client = MCPStdioClient(name=name, command=parts[0], args=parts[1:] if len(parts) > 1 else [])
        else:
            client = MCPSSEClient(name=name, url=url)

        if client.start():
            self.clients[name] = client
            for tool in client.tools:
                tool_name = f"mcp_{name}_{tool['name']}"
                self._tool_to_server[tool_name] = name
            return {
                "status": "connected",
                "name": name,
                "transport": transport,
                "url": url,
                "tools": [{"name": t["name"], "description": t.get("description", "")} for t in client.tools],
                "tool_count": len(client.tools),
            }
        return {"error": f"Failed to connect to MCP server '{name}' at {url}"}

    def disconnect_runtime(self, name: str) -> dict:
        """Disconnect a runtime MCP server."""
        if name not in self.clients:
            return {"error": f"Server '{name}' is not connected"}
        client = self.clients[name]
        client.stop()
        # Remove tool mappings
        to_remove = [k for k, v in self._tool_to_server.items() if v == name]
        for k in to_remove:
            del self._tool_to_server[k]
        del self.clients[name]
        return {"status": "disconnected", "name": name}

    def stop_all(self):
        """Stop all MCP server connections."""
        for client in self.clients.values():
            client.stop()
        self.clients.clear()
        self._tool_to_server.clear()


# Global MCP manager
_mcp_manager: MCPManager | None = None


# --- Agent System ---

AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")

# --- Tool Configuration ---

_TOOLS_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools_config.json")

_TOOLS_CONFIG_DEFAULTS = {
    "exa_search": {
        "enabled": True,
        "api_key": "",
        "default_num_results": 5,
    },
    "gmail": {
        "enabled": True,
        "email": "",
        "app_password": "",
    },
    "execute_command": {
        "enabled": True,
        "timeout": 120,
        "banned_commands": ["rm -rf /", "mkfs", "dd if="],
    },
    "web_fetch": {
        "enabled": True,
        "timeout": 30,
        "max_size_mb": 10,
    },
    "refinement": {
        "enabled": True,
        "model": "",  # empty = auto-select (Haiku > Sonnet > cheapest)
    },
}


def get_tool_config() -> dict:
    """Load tool configuration from tools_config.json, falling back to defaults."""
    cfg = {}
    if os.path.exists(_TOOLS_CONFIG_PATH):
        try:
            with open(_TOOLS_CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            cfg = {}
    # Merge with defaults (defaults provide missing keys)
    merged = {}
    for tool_name, defaults in _TOOLS_CONFIG_DEFAULTS.items():
        tool_cfg = cfg.get(tool_name, {})
        merged[tool_name] = {**defaults, **tool_cfg}
    return merged


def save_tool_config(cfg: dict) -> dict:
    """Save tool configuration to tools_config.json. Returns saved config."""
    # Merge with existing to preserve fields not in the incoming payload
    existing = get_tool_config()
    for tool_name, tool_cfg in cfg.items():
        if tool_name in existing:
            existing[tool_name].update(tool_cfg)
        else:
            existing[tool_name] = tool_cfg
    try:
        with open(_TOOLS_CONFIG_PATH, "w") as f:
            json.dump(existing, f, indent=2)
    except OSError as e:
        return {"error": str(e)}
    return existing


def get_tool_status() -> dict:
    """Return status of each configurable tool, checking all fallback sources."""
    cfg = get_tool_config()
    status = {}
    for tool_name, tool_cfg in cfg.items():
        tool_cfg = dict(tool_cfg)  # copy to avoid mutating defaults
        enabled = tool_cfg.get("enabled", True)
        if not enabled:
            s = "disabled"
        elif tool_name == "exa_search":
            exa_key = tool_cfg.get("api_key") or os.environ.get("EXA_API_KEY", "")
            # Check hardcoded fallback in tool function
            if not exa_key:
                exa_key = "97dbd594-f7b4-4866-9a8e-6a297e3df576"  # built-in default
                tool_cfg["_source"] = "built-in default"
            elif not tool_cfg.get("api_key") and os.environ.get("EXA_API_KEY"):
                tool_cfg["_source"] = "environment variable"
            s = "configured" if exa_key else "not configured"
        elif tool_name == "gmail":
            has_gmail = bool(tool_cfg.get("email") and tool_cfg.get("app_password"))
            if not has_gmail:
                gmail_fb = _gmail_config()
                if gmail_fb and gmail_fb.get("email") and gmail_fb.get("app_password"):
                    has_gmail = True
                    tool_cfg["email"] = gmail_fb["email"]
                    tool_cfg["_source"] = "gmail.json"
            s = "configured" if has_gmail else "not configured"
        elif tool_name == "refinement":
            s = "configured" if tool_cfg.get("model") else "auto (Haiku > cheapest)"
        else:
            s = "configured"
        status[tool_name] = {"enabled": enabled, "status": s, "config": tool_cfg}
    return status


class AgentConfig:
    """Configuration and file management for a single agent."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.dir = os.path.join(AGENTS_DIR, agent_id)
        os.makedirs(self.dir, exist_ok=True)
        self._ensure_defaults()

    def _ensure_defaults(self):
        """Create default files if they don't exist."""
        soul_path = os.path.join(self.dir, "soul.md")
        if not os.path.exists(soul_path):
            with open(soul_path, "w") as f:
                f.write(f"""# {self.agent_id}

You are the **{self.agent_id}** agent.
Adapt your behavior to the tasks you are given.
""")
        config_path = os.path.join(self.dir, "agent.json")
        if not os.path.exists(config_path):
            with open(config_path, "w") as f:
                json.dump({
                    "description": f"{self.agent_id} agent",
                    "model": None,
                }, f, indent=2)

    @property
    def soul(self) -> str:
        """Load soul.md content."""
        path = os.path.join(self.dir, "soul.md")
        try:
            with open(path, "r") as f:
                return f.read()
        except OSError:
            return ""

    @property
    def tools_guide(self) -> str:
        """Load per-agent tools.md, falling back to global tools.md."""
        agent_tools = os.path.join(self.dir, "tools.md")
        if os.path.exists(agent_tools):
            try:
                with open(agent_tools, "r") as f:
                    return f.read()
            except OSError:
                pass
        # Fall back to global tools.md
        global_tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.md")
        try:
            with open(global_tools, "r") as f:
                return f.read()
        except OSError:
            return ""

    @property
    def config(self) -> dict:
        """Load agent.json config."""
        path = os.path.join(self.dir, "agent.json")
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    @property
    def description(self) -> str:
        return self.config.get("description", self.agent_id)

    @property
    def preferred_model(self) -> str | None:
        raw = self.config.get("model")
        if not raw:
            return None
        purpose = self.config.get("model_purpose")
        resolved = resolve_model(raw, purpose) if _models_config else raw
        return resolved or raw

    @property
    def max_context(self) -> int | None:
        """Return model's max_context if a preferred model is set."""
        model = self.preferred_model
        if model:
            return get_model_max_context(model)
        return None

    @property
    def memory_dir(self) -> str:
        return self.dir  # memory.db lives alongside soul.md

    @property
    def skills_dir(self) -> str:
        return os.path.join(self.dir, "skills")

    @property
    def mcp_config_path(self) -> str:
        return os.path.join(self.dir, "mcp.json")

    def load_commands(self) -> list[dict]:
        """Load custom slash commands from commands.json."""
        path = os.path.join(self.dir, "commands.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save_commands(self, commands: list[dict]):
        """Save custom slash commands to commands.json."""
        path = os.path.join(self.dir, "commands.json")
        with open(path, "w") as f:
            json.dump(commands, f, indent=2)

    def list_skills(self) -> list[dict]:
        """List all skills for this agent (own + main's global skills)."""
        skills = {}
        # Load main's skills first (global)
        if self.agent_id != "main":
            main_skills_dir = os.path.join(AGENTS_DIR, "main", "skills")
            skills.update(self._scan_skills(main_skills_dir, source="main"))
        # Load own skills (override globals if same name)
        skills.update(self._scan_skills(self.skills_dir, source=self.agent_id))
        return list(skills.values())

    def _scan_skills(self, skills_dir: str, source: str) -> dict[str, dict]:
        """Scan a skills directory and return {name: skill_info}."""
        result = {}
        if not os.path.isdir(skills_dir):
            return result
        for name in sorted(os.listdir(skills_dir)):
            skill_dir = os.path.join(skills_dir, name)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_file):
                continue
            try:
                with open(skill_file, "r") as f:
                    raw = f.read()
                # Parse YAML frontmatter
                fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw, re.DOTALL)
                if fm_match:
                    fm_text, body = fm_match.groups()
                    fm = {}
                    for line in fm_text.split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            fm[k.strip()] = v.strip().strip('"').strip("'")
                    result[name] = {
                        "name": fm.get("name", name),
                        "slug": name,
                        "description": fm.get("description", ""),
                        "source": source,
                        "path": skill_file,
                    }
                else:
                    result[name] = {
                        "name": name,
                        "slug": name,
                        "description": "",
                        "source": source,
                        "path": skill_file,
                    }
            except OSError:
                continue
        return result

    def load_skill(self, skill_name: str) -> str | None:
        """Load the full SKILL.md body for a specific skill.
        Accepts either the directory name (slug) or the display name."""
        # Try direct match first (slug = directory name)
        own_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
        if os.path.isfile(own_path):
            return self._read_skill_body(own_path)
        # Fall back to main's skills
        if self.agent_id != "main":
            main_path = os.path.join(AGENTS_DIR, "main", "skills", skill_name, "SKILL.md")
            if os.path.isfile(main_path):
                return self._read_skill_body(main_path)
        # Try matching by display name → slug lookup
        for s in self.list_skills():
            if s.get("name", "").lower() == skill_name.lower() or s.get("slug", "").lower() == skill_name.lower():
                slug = s.get("slug", "")
                if slug:
                    own_path = os.path.join(self.skills_dir, slug, "SKILL.md")
                    if os.path.isfile(own_path):
                        return self._read_skill_body(own_path)
                    if self.agent_id != "main":
                        main_path = os.path.join(AGENTS_DIR, "main", "skills", slug, "SKILL.md")
                        if os.path.isfile(main_path):
                            return self._read_skill_body(main_path)
        return None

    @staticmethod
    def _read_skill_body(path: str) -> str:
        """Read a SKILL.md and return just the body (after frontmatter)."""
        with open(path, "r") as f:
            raw = f.read()
        fm_match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)$', raw, re.DOTALL)
        if fm_match:
            return fm_match.group(1).strip()
        return raw.strip()


def list_agents() -> list[str]:
    """List all available agent IDs."""
    if not os.path.isdir(AGENTS_DIR):
        return ["main"]
    agents = []
    for name in sorted(os.listdir(AGENTS_DIR)):
        if os.path.isdir(os.path.join(AGENTS_DIR, name)) and not name.startswith("."):
            agents.append(name)
    if not agents:
        agents = ["main"]
    return agents


def get_agent_summaries() -> list[dict]:
    """Get agent ID + description + soul summary for all agents, with team metadata."""
    # First pass: collect raw summaries and scan for teams
    raw = []
    # team_id (the agent whose config holds the team) -> team config
    teams_cfg: dict[str, dict] = {}
    for agent_id in list_agents():
        cfg = AgentConfig(agent_id)
        config = cfg.config
        soul = cfg.soul.strip()
        summary = ""
        for line in soul.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                summary = line
                break
        entry = {
            "id": agent_id,
            "display_name": config.get("display_name", ""),
            "description": cfg.description,
            "soul_summary": summary,
            "model": cfg.preferred_model,
            "avatar": config.get("avatar"),
            "paused": config.get("paused", False),
        }
        team_cfg = config.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            teams_cfg[agent_id] = team_cfg
        raw.append(entry)

    # Second pass: compute team metadata per agent
    for entry in raw:
        aid = entry["id"]
        # Which teams is this agent a member of?
        member_of = []
        is_head_of = None
        for cfg_holder, tcfg in teams_cfg.items():
            members = tcfg.get("members", [])
            head_id = tcfg.get("head", members[0] if members else cfg_holder)
            if aid in members:
                team_name = tcfg.get("name", cfg_holder)
                member_of.append({"team_id": cfg_holder, "team_name": team_name})
                if aid == head_id:
                    is_head_of = cfg_holder
        entry["teams"] = member_of
        entry["is_team_head"] = is_head_of is not None
        if is_head_of:
            tcfg = teams_cfg[is_head_of]
            entry["team_config_holder"] = is_head_of
            entry["team_members"] = list(tcfg.get("members", []))
            entry["team_head"] = tcfg.get("head", entry["team_members"][0] if entry["team_members"] else is_head_of)
            entry["team_name"] = tcfg.get("name", "")
            entry["team_description"] = tcfg.get("description", "")
            entry["team_avatar"] = tcfg.get("avatar", "")
    return raw


def get_team_structure() -> dict:
    """Return hierarchical team structure for UI and API consumption."""
    agents = get_agent_summaries()
    agent_map = {a["id"]: a for a in agents}
    teams = {}
    in_team = set()  # agents that appear in at least one team

    # Find all agents that hold team configs
    for agent_id in list_agents():
        cfg = AgentConfig(agent_id).config
        team_cfg = cfg.get("team")
        if not isinstance(team_cfg, dict) or not team_cfg.get("members"):
            continue
        members_ids = team_cfg["members"]
        head_id = team_cfg.get("head", members_ids[0] if members_ids else agent_id)
        members = []
        for mid in members_ids:
            if mid in agent_map:
                members.append(agent_map[mid])
                in_team.add(mid)
        teams[agent_id] = {
            "head": head_id,
            "head_agent": agent_map.get(head_id),
            "members": members,
            "name": team_cfg.get("name") or agent_id,
            "description": team_cfg.get("description", ""),
            "avatar": team_cfg.get("avatar", ""),
            "config_holder": agent_id,
        }

    standalone = [a for a in agents if a["id"] not in in_team and a["id"] != "main"]
    main_agent = agent_map.get("main")
    return {"teams": teams, "standalone": standalone, "main": main_agent}


def _get_delegation_scope(caller_agent_id: str) -> list[str]:
    """Return list of agent IDs the caller is allowed to delegate to."""
    all_agents = list_agents()

    # Collect all team configs
    team_cfgs = {}  # config_holder_id -> team_cfg
    for aid in all_agents:
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            team_cfgs[aid] = team_cfg

    in_team = set()
    head_ids = set()
    for holder, tcfg in team_cfgs.items():
        head_id = tcfg.get("head", tcfg["members"][0] if tcfg["members"] else holder)
        head_ids.add(head_id)
        for m in tcfg["members"]:
            in_team.add(m)

    if caller_agent_id == "main":
        # Main can delegate to team heads and standalone agents (not regular members)
        return [a for a in all_agents if a != "main" and (a in head_ids or a not in in_team)]

    # Check if caller is a team head
    for holder, tcfg in team_cfgs.items():
        head_id = tcfg.get("head", tcfg["members"][0] if tcfg["members"] else holder)
        if caller_agent_id == head_id:
            # Team head can delegate to its members (excluding self)
            return [m for m in tcfg["members"] if m != caller_agent_id and m in all_agents]

    # Regular member: can delegate to peers in same team + team head
    reachable = set()
    for holder, tcfg in team_cfgs.items():
        if caller_agent_id in tcfg["members"]:
            for m in tcfg["members"]:
                if m != caller_agent_id and m in all_agents:
                    reachable.add(m)
    return list(reachable) if reachable else [a for a in all_agents if a != caller_agent_id]


def _find_team_head(agent_id: str) -> str | None:
    """Find the team head for a given agent. Returns None if not in any team."""
    for aid in list_agents():
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            if agent_id in team_cfg["members"]:
                return team_cfg.get("head", team_cfg["members"][0] if team_cfg["members"] else aid)
    return None


def _get_agent_team_info(agent_id: str) -> dict | None:
    """Get team info for an agent. Returns dict with name, head, members, is_head, or None."""
    for aid in list_agents():
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            if agent_id in team_cfg["members"]:
                head = team_cfg.get("head", team_cfg["members"][0])
                return {
                    "name": team_cfg.get("name", aid),
                    "head": head,
                    "members": team_cfg["members"],
                    "is_head": agent_id == head,
                    "config_holder": aid,
                }
    return None


def build_agent_registry(for_agent_id: str | None = None) -> str:
    """Build a text block describing available agents for injection into system prompts.
    Respects team hierarchy: team heads see members, main sees heads + standalone."""
    agents = get_agent_summaries()
    if len(agents) <= 1:
        return ""

    caller = for_agent_id or "main"
    scope = _get_delegation_scope(caller)
    agent_map = {a["id"]: a for a in agents}

    # Check if caller is a team head
    caller_team_info = _get_agent_team_info(caller) if caller != "main" else None
    caller_is_head = caller_team_info and caller_team_info["is_head"]

    lines = ["AGENT REGISTRY — use delegate_task to send tasks to these agents:"]

    if caller == "main":
        # Group by teams + standalone
        struct = get_team_structure()
        if struct["teams"]:
            lines.append("  TEAMS:")
            for tid, team in struct["teams"].items():
                team_name = team.get("name", tid)
                head_id = team["head"]
                head_agent = team.get("head_agent", {})
                detail = head_agent.get("soul_summary") or head_agent.get("description", "")
                model_note = f" (model: {head_agent.get('model')})" if head_agent.get("model") else ""
                member_names = ", ".join(m["id"] for m in team["members"])
                lines.append(f"    - {head_id} (head of '{team_name}'): {detail}{model_note}")
                lines.append(f"      Members: {member_names}")
        if struct["standalone"]:
            lines.append("  STANDALONE AGENTS:")
            for a in struct["standalone"]:
                detail = a.get("soul_summary") or a.get("description", "")
                model_note = f" (model: {a['model']})" if a.get("model") else ""
                lines.append(f"    - {a['id']}: {detail}{model_note}")
    elif caller_is_head:
        lines.append("  YOUR TEAM MEMBERS:")
        for mid in scope:
            a = agent_map.get(mid, {})
            detail = a.get("soul_summary") or a.get("description", mid)
            model_note = f" (model: {a.get('model')})" if a.get("model") else ""
            lines.append(f"    - {mid}: {detail}{model_note}")
    else:
        for aid in scope:
            a = agent_map.get(aid, {})
            detail = a.get("soul_summary") or a.get("description", aid)
            model_note = f" (model: {a.get('model')})" if a.get("model") else ""
            lines.append(f"  - {aid}: {detail}{model_note}")

    lines.append("")
    lines.append(
        "Before performing a task, consider if another agent is better suited. "
        "Delegate when the task clearly matches another agent's specialty. "
        "Do NOT delegate simple tasks you can handle yourself."
    )
    return "\n".join(lines)


# Current active agent (set in _run_interactive)
_current_agent: AgentConfig | None = None


# --- Memory System (QMD hybrid search) ---

import hashlib
import urllib.request
import urllib.error

# QMD HTTP MCP daemon endpoint
_QMD_URL = "http://localhost:8181/mcp"
_QMD_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# Shared MCP session ID (set on first successful init)
_qmd_session_id: str | None = None
_qmd_session_lock = threading.Lock()
# Per-collection debounce timers for embedding after writes
_qmd_embed_timers: dict[str, threading.Timer] = {}
_qmd_embed_lock = threading.Lock()

# Files to skip when indexing (not memory files)
_QMD_IGNORE_FILES = {"soul.md", "tools.md"}


def _qmd_rpc(method: str, params: dict | None = None) -> dict | None:
    """Send a JSON-RPC request to the QMD MCP HTTP daemon. Returns result or None on failure."""
    global _qmd_session_id
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    data = json.dumps(payload).encode()
    headers = dict(_QMD_HEADERS)
    if _qmd_session_id:
        headers["Mcp-Session-Id"] = _qmd_session_id
    req = urllib.request.Request(_QMD_URL, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Capture session ID from response
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                _qmd_session_id = sid
            body = json.loads(resp.read().decode())
            if "error" in body:
                logging.warning("QMD RPC error: %s", body["error"])
                return None
            return body.get("result")
    except Exception as e:
        logging.debug("QMD unreachable: %s", e)
        return None


def _qmd_init_session() -> bool:
    """Initialize an MCP session with QMD. Returns True if successful.
    Thread-safe: only one session is created even under concurrent access."""
    with _qmd_session_lock:
        if _qmd_session_id:
            return True  # Another thread already initialized
        result = _qmd_rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "brain-agent", "version": "1.0"},
        })
        return result is not None


def _qmd_ensure_collection(name: str, directory: str):
    """Register a collection with QMD if it doesn't exist (via CLI)."""
    try:
        result = subprocess.run(
            ["qmd", "collection", "show", name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return  # Already exists
        subprocess.run(
            ["qmd", "collection", "add", directory,
             "--name", name, "--pattern", "*.md",
             "--ignore", ",".join(_QMD_IGNORE_FILES)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        logging.debug("QMD collection setup failed for %s: %s", name, e)


def _qmd_debounced_embed(collection: str):
    """Schedule a debounced qmd update+embed for a collection (2s delay).
    Each collection gets its own timer so concurrent writes to different
    collections don't cancel each other's embed."""
    def _do_embed():
        try:
            subprocess.run(
                ["qmd", "update"], capture_output=True, text=True, timeout=30,
            )
            subprocess.run(
                ["qmd", "embed", "-c", collection],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            logging.debug("QMD embed failed for %s: %s", collection, e)
        finally:
            with _qmd_embed_lock:
                _qmd_embed_timers.pop(collection, None)
    with _qmd_embed_lock:
        old = _qmd_embed_timers.get(collection)
        if old:
            old.cancel()
        timer = threading.Timer(2.0, _do_embed)
        timer.daemon = True
        _qmd_embed_timers[collection] = timer
        timer.start()


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (metadata, body)."""
    # Normalize line endings for cross-platform compatibility
    raw = raw.replace("\r\n", "\n")
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw, re.DOTALL)
    if fm_match:
        fm_text, body = fm_match.groups()
        fm = {}
        for line in fm_text.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        return fm, body.strip()
    return {}, raw.strip()


def _yaml_escape(value: str) -> str:
    """Escape a string for safe inclusion in YAML frontmatter."""
    if not value:
        return '""'
    # Quote if contains special YAML chars
    if any(c in value for c in (':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`', '\n')):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
        return f'"{escaped}"'
    return value


class MemoryStore:
    """Per-agent memory store backed by QMD hybrid search and markdown files."""

    _ensured_collections: set[str] = set()

    def __init__(self, agent_id: str = "main", base_dir: str | None = None):
        self.agent_id = agent_id
        if base_dir:
            self.dir = base_dir
        else:
            self.dir = os.path.join(AGENTS_DIR, agent_id)
        os.makedirs(self.dir, exist_ok=True)
        self._collection = agent_id
        # Ensure QMD knows about this collection (once per collection, background)
        if agent_id not in MemoryStore._ensured_collections:
            MemoryStore._ensured_collections.add(agent_id)
            threading.Thread(
                target=_qmd_ensure_collection,
                args=(self._collection, self.dir),
                daemon=True,
            ).start()

    def _make_id(self, name: str) -> str:
        """Generate a stable ID from name."""
        return hashlib.sha256(name.encode()).hexdigest()[:12]

    def _name_to_filename(self, name: str) -> str:
        """Convert a memory name to a safe filename with hash suffix to avoid collisions."""
        safe = re.sub(r'[^\w\s-]', '', name).strip().lower()
        safe = re.sub(r'[\s]+', '_', safe)
        # Add short hash to prevent collisions between similar names
        h = hashlib.sha256(name.encode()).hexdigest()[:6]
        base = safe[:50]
        return f"{base}_{h}.md" if base else f"{h}.md"

    def _find_file_for_name(self, name: str) -> str | None:
        """Find existing file for a memory name by checking frontmatter."""
        for fname in os.listdir(self.dir):
            if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
                continue
            fpath = os.path.join(self.dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read(500)  # only need frontmatter
                fm, _ = _parse_frontmatter(raw)
                if fm.get("name") == name:
                    return fpath
            except Exception:
                continue
        return None

    def store(self, name: str, content: str, description: str = "",
              mem_type: str = "general") -> dict:
        """Store or update a memory. Writes .md file and triggers QMD reindex."""
        mem_id = self._make_id(name)
        filename = self._name_to_filename(name)
        file_path = os.path.join(self.dir, filename)

        # Check if memory already exists under a different filename (migration)
        existing = self._find_file_for_name(name)
        if existing and existing != file_path:
            file_path = existing  # update in place
            filename = os.path.basename(existing)

        # Write markdown file with properly escaped frontmatter
        md_content = f"""---
name: {_yaml_escape(name)}
description: {_yaml_escape(description)}
type: {mem_type}
agent: {self.agent_id}
---

{content}
"""
        with open(file_path, "w") as f:
            f.write(md_content)

        # Trigger debounced QMD update+embed
        _qmd_debounced_embed(self._collection)

        # --- Entity extraction auto-linking (Mechanism 2) ---
        try:
            entities = _extract_entities(content)
            if entities:
                # Find other files sharing entities
                matches = _find_entity_matches(self.agent_id, filename, entities)
                for other_fname in matches[:10]:  # limit to avoid excessive linking
                    other_path = os.path.join(self.dir, other_fname)
                    if os.path.exists(other_path):
                        _add_related_to_file(file_path, other_fname, "same_topic")
                        _add_related_to_file(other_path, filename, "same_topic")
                # Update entity index with new file
                _update_entity_index(self.agent_id, filename, entities)
                if matches:
                    _qmd_debounced_embed(self._collection)
        except Exception:
            pass  # Entity linking is best-effort, never block store

        return {"id": mem_id, "name": name, "file": filename, "status": "stored"}

    def recall(self, query: str, limit: int = 10, mem_type: str | None = None) -> list[dict]:
        """Search memories using QMD hybrid search (BM25 + vector + reranking).
        Falls back to file-scan substring matching if QMD is unreachable."""
        # Try QMD first
        results = self._qmd_query(query, limit, mem_type)
        if results is not None:
            return results
        # Fallback: scan files
        return self._fallback_search(query, limit, mem_type)

    def _qmd_query(self, query: str, limit: int, mem_type: str | None) -> list[dict] | None:
        """Query QMD via MCP HTTP. Returns list of results or None if unavailable."""
        global _qmd_session_id
        # Ensure session
        if not _qmd_session_id:
            if not _qmd_init_session():
                return None

        searches = [
            {"type": "lex", "query": query},
            {"type": "vec", "query": query},
        ]
        result = _qmd_rpc("tools/call", {
            "name": "query",
            "arguments": {
                "searches": searches,
                "collections": [self._collection],
                "limit": limit * 2 if mem_type else limit,  # over-fetch if filtering
            },
        })
        if not result:
            # Session may have expired, retry once with lock to prevent stampede
            with _qmd_session_lock:
                _qmd_session_id = None
            if not _qmd_init_session():
                return None
            result = _qmd_rpc("tools/call", {
                "name": "query",
                "arguments": {
                    "searches": searches,
                    "collections": [self._collection],
                    "limit": limit * 2 if mem_type else limit,
                },
            })
            if not result:
                return None

        # Parse structured results
        structured = result.get("structuredContent", {})
        qmd_results = structured.get("results", [])
        memories = []
        for r in qmd_results:
            file_rel = r.get("file", "")
            # Strip collection prefix (e.g. "main/foo.md" -> "foo.md")
            if "/" in file_rel:
                fname = file_rel.split("/", 1)[1]
            else:
                fname = file_rel
            fpath = os.path.join(self.dir, fname)
            # Read the actual file for full content + frontmatter
            try:
                with open(fpath, "r") as f:
                    raw = f.read()
                fm, body = _parse_frontmatter(raw)
            except FileNotFoundError:
                fm = {"name": r.get("title", fname), "type": "general"}
                body = r.get("snippet", "")

            mem = {
                "id": self._make_id(fm.get("name", fname)),
                "name": fm.get("name", fname.replace(".md", "")),
                "description": fm.get("description", ""),
                "type": fm.get("type", "general"),
                "content": body,
                "file_path": fpath,
                "score": r.get("score", 0),
            }
            # Post-filter by type
            if mem_type and mem["type"] != mem_type:
                continue
            memories.append(mem)
            if len(memories) >= limit:
                break
        return memories

    def _fallback_search(self, query: str, limit: int, mem_type: str | None) -> list[dict]:
        """Fallback: scan .md files and do substring matching."""
        terms = query.lower().split()
        if not terms:
            return self.list_all(mem_type)[:limit]
        results = []
        # Directories to scan: agent root + chats-indexed subdir
        scan_dirs = [self.dir]
        chats_dir = os.path.join(self.dir, "chats-indexed")
        if os.path.isdir(chats_dir):
            scan_dirs.append(chats_dir)
        for scan_dir in scan_dirs:
            for fname in os.listdir(scan_dir):
                if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
                    continue
                fpath = os.path.join(scan_dir, fname)
                try:
                    with open(fpath, "r") as f:
                        raw = f.read()
                    fm, body = _parse_frontmatter(raw)
                    mtype = fm.get("type", "general")
                    if mem_type and mtype != mem_type:
                        continue
                    searchable = (fm.get("name", "") + " " + fm.get("description", "") + " " + body).lower()
                    hits = sum(1 for t in terms if t in searchable)
                    if hits > 0:
                        results.append({
                            "id": self._make_id(fm.get("name", fname)),
                            "name": fm.get("name", fname.replace(".md", "")),
                            "description": fm.get("description", ""),
                            "type": mtype,
                            "content": body,
                            "file_path": fpath,
                            "score": hits / len(terms),
                        })
                except Exception:
                    continue
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def delete(self, name: str) -> dict:
        """Delete a memory by name."""
        filename = self._name_to_filename(name)
        file_path = os.path.join(self.dir, filename)
        if not os.path.exists(file_path):
            # Try scanning for a file with matching frontmatter name
            found = self._find_file_for_name(name)
            if found:
                file_path = found
            else:
                return {"error": f"Memory '{name}' not found"}
        os.remove(file_path)
        # Trigger QMD reindex
        _qmd_debounced_embed(self._collection)
        return {"name": name, "status": "deleted"}

    def list_all(self, mem_type: str | None = None) -> list[dict]:
        """List all memories by scanning .md files (no QMD needed)."""
        results = []
        for fname in os.listdir(self.dir):
            if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
                continue
            fpath = os.path.join(self.dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read()
                fm, body = _parse_frontmatter(raw)
                mtype = fm.get("type", "general")
                if mem_type and mtype != mem_type:
                    continue
                mtime = os.path.getmtime(fpath)
                results.append({
                    "id": self._make_id(fm.get("name", fname)),
                    "name": fm.get("name", fname.replace(".md", "")),
                    "description": fm.get("description", ""),
                    "type": mtype,
                    "content": body,
                    "updated_at": datetime.datetime.fromtimestamp(mtime).isoformat(),
                })
            except Exception:
                continue
        results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return results

    def reindex(self) -> dict:
        """Trigger QMD update+embed for this collection."""
        try:
            r1 = subprocess.run(
                ["qmd", "update"], capture_output=True, text=True, timeout=30,
            )
            r2 = subprocess.run(
                ["qmd", "embed", "-c", self._collection],
                capture_output=True, text=True, timeout=60,
            )
            return {"agent": self.agent_id, "status": "reindexed",
                    "update": r1.returncode == 0, "embed": r2.returncode == 0}
        except Exception as e:
            return {"agent": self.agent_id, "status": "error", "error": str(e)}


# Global memory store instance (set in _run_interactive)
_memory_store: MemoryStore | None = None


# ─── Projects System ──────────────────────────────────────────────────

class ProjectManager:
    """CRUD operations for per-agent projects."""

    @staticmethod
    def _project_dir(agent_id: str, name: str) -> str:
        return os.path.join(AGENTS_DIR, agent_id, "projects", name)

    @staticmethod
    def _projects_base(agent_id: str) -> str:
        return os.path.join(AGENTS_DIR, agent_id, "projects")

    @staticmethod
    def list_projects(agent_id: str) -> list[dict]:
        """List all projects for an agent."""
        base = ProjectManager._projects_base(agent_id)
        if not os.path.isdir(base):
            return []
        projects = []
        for name in sorted(os.listdir(base)):
            pdir = os.path.join(base, name)
            if not os.path.isdir(pdir) or name.startswith("."):
                continue
            cfg_path = os.path.join(pdir, "project.json")
            cfg = {}
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r") as f:
                        cfg = json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass
            # Count ingested docs
            ingested_dir = os.path.join(pdir, "ingested")
            chunk_count = 0
            if os.path.isdir(ingested_dir):
                chunk_count = sum(1 for fn in os.listdir(ingested_dir) if fn.startswith("ingest-") and fn.endswith(".md"))
            # Count memory files
            mem_count = sum(1 for fn in os.listdir(pdir) if fn.endswith(".md") and not fn.startswith("ingest-") and fn not in _QMD_IGNORE_FILES)
            projects.append({
                "name": name,
                "description": cfg.get("description", ""),
                "icon": cfg.get("icon", "folder"),
                "created_at": cfg.get("created_at", ""),
                "tags": cfg.get("tags", []),
                "watch_folders": cfg.get("watch_folders", []),
                "status": cfg.get("status", "active"),
                "chunks": chunk_count,
                "memories": mem_count,
            })
        return projects

    @staticmethod
    def create_project(agent_id: str, name: str, description: str = "",
                       config: dict | None = None) -> dict:
        """Create a new project directory with project.json."""
        # Validate name
        safe_name = re.sub(r'[^\w\s-]', '', name).strip().lower().replace(' ', '-')
        if not safe_name:
            return {"error": "Invalid project name"}
        pdir = ProjectManager._project_dir(agent_id, safe_name)
        if os.path.exists(pdir):
            return {"error": f"Project '{safe_name}' already exists"}
        os.makedirs(pdir, exist_ok=True)
        os.makedirs(os.path.join(pdir, "ingested"), exist_ok=True)
        os.makedirs(os.path.join(pdir, "notes"), exist_ok=True)
        cfg = {
            "name": config.get("name", name) if config else name,
            "description": description,
            "icon": (config or {}).get("icon", "📁"),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "watch_folders": (config or {}).get("watch_folders", []),
            "tags": (config or {}).get("tags", []),
            "model": (config or {}).get("model"),
        }
        cfg_path = os.path.join(pdir, "project.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)
        # Register QMD collection for this project
        collection_name = f"{agent_id}/{safe_name}"
        threading.Thread(
            target=_qmd_ensure_collection,
            args=(collection_name, pdir),
            daemon=True,
        ).start()
        return {"name": safe_name, "status": "created", "path": pdir}

    @staticmethod
    def get_project(agent_id: str, name: str) -> dict | None:
        """Read project.json for a project."""
        pdir = ProjectManager._project_dir(agent_id, name)
        cfg_path = os.path.join(pdir, "project.json")
        if not os.path.exists(cfg_path):
            return None
        try:
            with open(cfg_path, "r") as f:
                cfg = json.load(f)
            # Add computed stats
            ingested_dir = os.path.join(pdir, "ingested")
            chunk_count = 0
            if os.path.isdir(ingested_dir):
                chunk_count = sum(1 for fn in os.listdir(ingested_dir) if fn.startswith("ingest-") and fn.endswith(".md"))
            cfg["chunks"] = chunk_count
            cfg["dir"] = pdir
            return cfg
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def update_project(agent_id: str, name: str, updates: dict) -> dict:
        """Update project.json fields."""
        pdir = ProjectManager._project_dir(agent_id, name)
        cfg_path = os.path.join(pdir, "project.json")
        if not os.path.exists(cfg_path):
            return {"error": f"Project '{name}' not found"}
        try:
            with open(cfg_path, "r") as f:
                cfg = json.load(f)
            for k in ("description", "watch_folders", "tags", "model", "name", "icon", "status"):
                if k in updates:
                    cfg[k] = updates[k]
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
            return {"name": name, "status": "updated"}
        except (OSError, json.JSONDecodeError) as e:
            return {"error": str(e)}

    @staticmethod
    def delete_project(agent_id: str, name: str) -> dict:
        """Soft-delete a project (move to .trash)."""
        pdir = ProjectManager._project_dir(agent_id, name)
        if not os.path.isdir(pdir):
            return {"error": f"Project '{name}' not found"}
        trash_dir = os.path.join(AGENTS_DIR, ".trash")
        os.makedirs(trash_dir, exist_ok=True)
        dest = os.path.join(trash_dir, f"{agent_id}_project_{name}_{int(time.time())}")
        shutil.move(pdir, dest)
        # Remove QMD collection
        collection_name = f"{agent_id}/{name}"
        try:
            subprocess.run(
                ["qmd", "collection", "remove", collection_name],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            pass
        return {"name": name, "status": "deleted", "moved_to": dest}


class NoteManager:
    """CRUD operations for project notes (markdown files in notes/ subdirectory)."""

    @staticmethod
    def _notes_dir(agent_id: str, project_name: str) -> str:
        return os.path.join(AGENTS_DIR, agent_id, "projects", project_name, "notes")

    @staticmethod
    def list_notes(agent_id: str, project_name: str) -> list[dict]:
        """Walk notes/ tree recursively, return metadata for each .md file."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        if not os.path.isdir(notes_dir):
            return []
        results = []
        for dirpath, _, filenames in os.walk(notes_dir):
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, notes_dir)
                try:
                    stat = os.stat(fpath)
                    with open(fpath, "r", errors="replace") as f:
                        raw = f.read(2000)
                    fm, _ = _parse_frontmatter(raw)
                    results.append({
                        "path": rel_path,
                        "name": fm.get("name", fname.replace(".md", "")),
                        "type": fm.get("type", "note"),
                        "size": stat.st_size,
                        "created_at": fm.get("created_at", ""),
                        "updated_at": fm.get("updated_at", ""),
                    })
                except Exception:
                    continue
        return results

    @staticmethod
    def get_note(agent_id: str, project_name: str, path: str) -> dict | None:
        """Read a note file and return its content with metadata."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        fpath = os.path.join(notes_dir, path)
        if not os.path.isfile(fpath):
            return None
        try:
            stat = os.stat(fpath)
            with open(fpath, "r", errors="replace") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
            return {
                "path": path,
                "name": fm.get("name", os.path.basename(path).replace(".md", "")),
                "content": body,
                "frontmatter": fm,
                "size": stat.st_size,
                "created_at": fm.get("created_at", ""),
                "updated_at": fm.get("updated_at", ""),
            }
        except Exception:
            return None

    @staticmethod
    def create_note(agent_id: str, project_name: str, path: str, content: str = "") -> dict:
        """Create a note with YAML frontmatter, entity extraction, and QMD reindex."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        fpath = os.path.join(notes_dir, path)
        # Create parent dirs if needed
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        if os.path.exists(fpath):
            return {"error": f"Note '{path}' already exists"}

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        name = os.path.basename(path).replace(".md", "")
        md_content = f"""---
name: {_yaml_escape(name)}
type: note
created_at: {now}
updated_at: {now}
agent: {agent_id}
project: {project_name}
---

{content}
"""
        with open(fpath, "w") as f:
            f.write(md_content)

        # Entity extraction + auto-link
        try:
            entities = _extract_entities(content)
            if entities:
                filename = os.path.basename(fpath)
                matches = _find_entity_matches(agent_id, filename, entities)
                for other_fname in matches[:10]:
                    other_path = os.path.join(os.path.dirname(fpath), other_fname)
                    if not os.path.exists(other_path):
                        # Try agent dir
                        other_path = os.path.join(AGENTS_DIR, agent_id, other_fname)
                    if os.path.exists(other_path):
                        _add_related_to_file(fpath, other_fname, "same_topic")
                        _add_related_to_file(other_path, filename, "same_topic")
                _update_entity_index(agent_id, filename, entities)
        except Exception:
            pass

        # Add same_folder relationships with sibling notes
        try:
            folder = os.path.dirname(fpath)
            fname = os.path.basename(fpath)
            for sibling in os.listdir(folder):
                if sibling.endswith(".md") and sibling != fname:
                    sibling_path = os.path.join(folder, sibling)
                    _add_related_to_file(fpath, sibling, "same_folder")
                    _add_related_to_file(sibling_path, fname, "same_folder")
        except Exception:
            pass

        # Trigger QMD reindex
        collection = f"{agent_id}/{project_name}"
        _qmd_debounced_embed(collection)

        return {"path": path, "status": "created"}

    @staticmethod
    def update_note(agent_id: str, project_name: str, path: str, content: str) -> dict:
        """Update a note's content, preserving frontmatter and updating timestamp."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        fpath = os.path.join(notes_dir, path)
        if not os.path.isfile(fpath):
            return {"error": f"Note '{path}' not found"}

        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, _ = _parse_frontmatter(raw)
        except Exception:
            fm = {}

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fm["updated_at"] = now

        # Rebuild frontmatter
        fm_lines = []
        for k, v in fm.items():
            if k == "related":
                continue  # related is multi-line, handle separately
            fm_lines.append(f"{k}: {v}")

        # Preserve related section if it exists
        related_section = ""
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            rel_match = re.search(r'(related:.*)', fm_text, re.DOTALL)
            if rel_match:
                related_section = "\n" + rel_match.group(1)

        md_content = f"---\n" + "\n".join(fm_lines) + related_section + f"\n---\n\n{content}\n"
        with open(fpath, "w") as f:
            f.write(md_content)

        # Re-run entity extraction
        try:
            entities = _extract_entities(content)
            if entities:
                filename = os.path.basename(fpath)
                _update_entity_index(agent_id, filename, entities)
        except Exception:
            pass

        # Trigger QMD reindex
        collection = f"{agent_id}/{project_name}"
        _qmd_debounced_embed(collection)

        return {"path": path, "status": "updated"}

    @staticmethod
    def delete_note(agent_id: str, project_name: str, path: str) -> dict:
        """Remove a note file and trigger QMD reindex."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        fpath = os.path.join(notes_dir, path)
        if not os.path.isfile(fpath):
            return {"error": f"Note '{path}' not found"}

        os.remove(fpath)

        # Clean up empty parent directories
        parent = os.path.dirname(fpath)
        while parent != notes_dir:
            try:
                if not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break
            except Exception:
                break

        # Trigger QMD reindex
        collection = f"{agent_id}/{project_name}"
        _qmd_debounced_embed(collection)

        return {"path": path, "status": "deleted"}

    @staticmethod
    def rename_note(agent_id: str, project_name: str, old_path: str, new_path: str) -> dict:
        """Rename/move a note file and update its frontmatter name."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        old_fpath = os.path.join(notes_dir, old_path)
        new_fpath = os.path.join(notes_dir, new_path)
        if not os.path.isfile(old_fpath):
            return {"error": f"Note '{old_path}' not found"}
        if os.path.exists(new_fpath):
            return {"error": f"Note '{new_path}' already exists"}

        # Create parent dirs for new path if needed
        os.makedirs(os.path.dirname(new_fpath), exist_ok=True)
        os.rename(old_fpath, new_fpath)

        # Update frontmatter name field
        try:
            with open(new_fpath, "r") as f:
                raw = f.read()
            new_name = os.path.basename(new_path).replace(".md", "")
            raw = re.sub(r'^(name:\s*).*$', rf'\g<1>{_yaml_escape(new_name)}', raw, count=1, flags=re.MULTILINE)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            raw = re.sub(r'^(updated_at:\s*).*$', rf'\g<1>{now}', raw, count=1, flags=re.MULTILINE)
            with open(new_fpath, "w") as f:
                f.write(raw)
        except Exception:
            pass

        # Trigger QMD reindex
        collection = f"{agent_id}/{project_name}"
        _qmd_debounced_embed(collection)

        return {"old_path": old_path, "new_path": new_path, "status": "renamed"}

    @staticmethod
    def create_folder(agent_id: str, project_name: str, folder_path: str) -> dict:
        """Create a folder within the notes directory."""
        notes_dir = NoteManager._notes_dir(agent_id, project_name)
        fpath = os.path.join(notes_dir, folder_path)
        os.makedirs(fpath, exist_ok=True)
        return {"path": folder_path, "status": "created"}


# ─── Document Ingestion Engine ────────────────────────────────────────

class DocumentParser:
    """Parse various document formats to plain text."""

    @staticmethod
    def parse_pdf(path: str) -> str:
        """Parse PDF to text using pymupdf."""
        try:
            import fitz  # pymupdf
        except ImportError:
            raise ImportError("Install pymupdf for PDF support: pip3 install pymupdf")
        doc = fitz.open(path)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n\n".join(pages)

    @staticmethod
    def parse_docx(path: str) -> str:
        """Parse DOCX to text using python-docx."""
        try:
            import docx
        except ImportError:
            raise ImportError("Install python-docx for DOCX support: pip3 install python-docx")
        doc = docx.Document(path)
        paragraphs = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                # Preserve heading structure
                if para.style and para.style.name and para.style.name.startswith("Heading"):
                    level = 1
                    try:
                        level = int(para.style.name.replace("Heading", "").strip()) or 1
                    except ValueError:
                        pass
                    text = "#" * level + " " + text
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    @staticmethod
    def parse_txt(path: str) -> str:
        """Parse plain text file."""
        with open(path, "r", errors="replace") as f:
            return f.read()

    @staticmethod
    def parse_md(path: str) -> str:
        """Parse markdown file (keep as-is)."""
        with open(path, "r", errors="replace") as f:
            return f.read()

    @staticmethod
    def parse_html(content: str) -> str:
        """Strip HTML tags and extract text content."""
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts: list[str] = []
                self._skip = False
                self._skip_tags = {"script", "style", "nav", "header", "footer", "noscript"}

            def handle_starttag(self, tag, attrs):
                if tag in self._skip_tags:
                    self._skip = True
                elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                    self._parts.append("\n")
                    if tag.startswith("h"):
                        level = int(tag[1])
                        self._parts.append("#" * level + " ")

            def handle_endtag(self, tag):
                if tag in self._skip_tags:
                    self._skip = False
                elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
                    self._parts.append("\n")

            def handle_data(self, data):
                if not self._skip:
                    self._parts.append(data)

        extractor = _TextExtractor()
        extractor.feed(content)
        text = "".join(extractor._parts)
        # Clean up whitespace
        lines = [line.strip() for line in text.split("\n")]
        cleaned = "\n".join(lines)
        # Collapse multiple blank lines
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    @staticmethod
    def parse_url(url: str) -> str:
        """Fetch URL and parse HTML to text."""
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
        return DocumentParser.parse_html(html)

    @staticmethod
    def parse(path_or_url: str) -> tuple[str, str]:
        """Auto-detect format and parse. Returns (text, source_type)."""
        if path_or_url.startswith(("http://", "https://")):
            return DocumentParser.parse_url(path_or_url), "url"
        ext = os.path.splitext(path_or_url)[1].lower()
        parsers = {
            ".pdf": ("pdf", DocumentParser.parse_pdf),
            ".docx": ("docx", DocumentParser.parse_docx),
            ".txt": ("txt", DocumentParser.parse_txt),
            ".md": ("md", DocumentParser.parse_md),
            ".html": ("html", lambda p: DocumentParser.parse_html(open(p, "r", errors="replace").read())),
            ".htm": ("html", lambda p: DocumentParser.parse_html(open(p, "r", errors="replace").read())),
        }
        if ext not in parsers:
            raise ValueError(f"Unsupported format: {ext}. Supported: {', '.join(parsers.keys())}")
        source_type, parser_fn = parsers[ext]
        return parser_fn(path_or_url), source_type


class DocumentChunker:
    """Split text into overlapping chunks with section header preservation."""

    @staticmethod
    def chunk(text: str, chunk_size: int = 1500, chunk_overlap: int = 200,
              min_chunk_size: int = 100) -> list[dict]:
        """Split text into chunks. chunk_size/overlap are in ~tokens (chars/4 approximation).
        Returns list of {text, index, total, header}."""
        # Convert token counts to char approximation
        max_chars = chunk_size * 4
        overlap_chars = chunk_overlap * 4
        min_chars = min_chunk_size * 4

        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks: list[dict] = []
        current_parts: list[str] = []
        current_len = 0
        last_header = ""

        def _flush():
            nonlocal current_parts, current_len
            if not current_parts:
                return
            chunk_text = "\n\n".join(current_parts)
            if len(chunk_text) < min_chars:
                return
            # Prepend section header if available
            if last_header and not chunk_text.startswith("#"):
                chunk_text = last_header + "\n\n" + chunk_text
            chunks.append({
                "text": chunk_text,
                "index": len(chunks),
                "total": 0,  # filled in later
                "header": last_header,
            })
            # Keep overlap: take text from end of current chunk
            if overlap_chars > 0:
                overlap_text = chunk_text[-overlap_chars:]
                current_parts = [overlap_text]
                current_len = len(overlap_text)
            else:
                current_parts = []
                current_len = 0

        for para in paragraphs:
            # Track section headers
            header_match = re.match(r'^(#{1,6}\s+.+)', para.split('\n')[0])
            if header_match:
                last_header = header_match.group(1)

            # If a single paragraph exceeds max_chars, split it
            if len(para) > max_chars:
                # Flush current buffer first
                if current_parts:
                    _flush()
                # Split on sentences
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sent in sentences:
                    if len(sent) > max_chars:
                        # Split on words as last resort
                        words = sent.split()
                        for word in words:
                            if current_len + len(word) + 1 > max_chars:
                                _flush()
                            current_parts.append(word)
                            current_len += len(word) + 1
                    else:
                        if current_len + len(sent) + 1 > max_chars:
                            _flush()
                        current_parts.append(sent)
                        current_len += len(sent) + 1
            else:
                if current_len + len(para) + 2 > max_chars:
                    _flush()
                current_parts.append(para)
                current_len += len(para) + 2

        # Flush remaining
        if current_parts:
            chunk_text = "\n\n".join(current_parts)
            if len(chunk_text) >= min_chars:
                if last_header and not chunk_text.startswith("#"):
                    chunk_text = last_header + "\n\n" + chunk_text
                chunks.append({
                    "text": chunk_text,
                    "index": len(chunks),
                    "total": 0,
                    "header": last_header,
                })

        # Fill in total count
        for c in chunks:
            c["total"] = len(chunks)

        return chunks


class IngestManager:
    """Ingest files and URLs into agent or project memory as chunked markdown."""

    @staticmethod
    def _source_hash(source: str) -> str:
        """6-char hash of source name/URL."""
        return hashlib.sha256(source.encode()).hexdigest()[:6]

    @staticmethod
    def _ingest_dir(agent_id: str, project_name: str | None = None) -> str:
        """Get the directory where ingested chunks are stored."""
        if project_name:
            d = os.path.join(AGENTS_DIR, agent_id, "projects", project_name, "ingested")
        else:
            d = os.path.join(AGENTS_DIR, agent_id, "ingested")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _collection_name(agent_id: str, project_name: str | None = None) -> str:
        """QMD collection name for embedding."""
        if project_name:
            return f"{agent_id}/{project_name}"
        return agent_id

    @staticmethod
    def ingest_file(agent_id: str, file_path: str,
                    project_name: str | None = None,
                    tags: list[str] | None = None,
                    chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Parse, chunk, and store a file as ingested memory chunks."""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        source_name = os.path.basename(file_path)
        try:
            text, source_type = DocumentParser.parse(file_path)
        except (ImportError, ValueError) as e:
            return {"error": str(e)}
        return IngestManager._store_chunks(
            agent_id, project_name, source_name, source_type, text,
            tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )

    @staticmethod
    def ingest_url(agent_id: str, url: str,
                   project_name: str | None = None,
                   tags: list[str] | None = None,
                   chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Fetch URL, parse HTML, chunk, and store."""
        try:
            text = DocumentParser.parse_url(url)
        except Exception as e:
            return {"error": f"Failed to fetch URL: {e}"}
        return IngestManager._store_chunks(
            agent_id, project_name, url, "url", text,
            tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )

    @staticmethod
    def _store_chunks(agent_id: str, project_name: str | None,
                      source: str, source_type: str, text: str,
                      tags: list[str] | None = None,
                      chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Chunk text and write as ingest-*.md files with frontmatter."""
        src_hash = IngestManager._source_hash(source)
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        collection = IngestManager._collection_name(agent_id, project_name)

        # Delete existing chunks for this source (re-ingest)
        existing = [f for f in os.listdir(ingest_dir) if f.startswith(f"ingest-{src_hash}-") and f.endswith(".md")]
        for f in existing:
            os.remove(os.path.join(ingest_dir, f))

        # Chunk
        chunks = DocumentChunker.chunk(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            return {"error": "No content extracted from document"}

        all_tags = ["ingested"]
        if tags:
            all_tags.extend(tags)
        # Add source name as tag (sanitized)
        safe_source_tag = re.sub(r'[^\w-]', '', source.split("/")[-1].split(".")[0].lower())
        if safe_source_tag:
            all_tags.append(safe_source_tag)
        tags_yaml = "\n".join(f"  - {t}" for t in all_tags)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files_written = []
        for chunk in chunks:
            idx = chunk["index"]
            total = chunk["total"]
            title = chunk["header"] or f"{source} - Chunk {idx + 1}"

            # Build related links
            related_lines = []
            if idx > 0:
                prev_file = f"ingest-{src_hash}-{idx - 1:03d}.md"
                related_lines.append(f"  - file: {prev_file}\n    type: prev_chunk")
            if idx < total - 1:
                next_file = f"ingest-{src_hash}-{idx + 1:03d}.md"
                related_lines.append(f"  - file: {next_file}\n    type: next_chunk")
            if idx != 0:
                first_file = f"ingest-{src_hash}-000.md"
                related_lines.append(f"  - file: {first_file}\n    type: same_source")
            related_yaml = ""
            if related_lines:
                related_yaml = "related:\n" + "\n".join(related_lines) + "\n"

            filename = f"ingest-{src_hash}-{idx:03d}.md"
            md_content = f"""---
title: {_yaml_escape(title)}
source: {_yaml_escape(source)}
source_type: {source_type}
ingested_at: "{now}"
chunk_index: {idx}
total_chunks: {total}
agent: {agent_id}
tags:
{tags_yaml}
{related_yaml}---

{chunk['text']}
"""
            fpath = os.path.join(ingest_dir, filename)
            with open(fpath, "w") as f:
                f.write(md_content)
            files_written.append(filename)

        # Trigger QMD indexing
        _qmd_debounced_embed(collection)

        word_count = len(text.split())
        return {
            "source": source,
            "source_type": source_type,
            "source_hash": src_hash,
            "chunks": len(chunks),
            "words": word_count,
            "files": files_written,
            "agent": agent_id,
            "project": project_name,
            "status": "ingested",
        }

    @staticmethod
    def list_ingested(agent_id: str, project_name: str | None = None) -> list[dict]:
        """List ingested documents grouped by source."""
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        if not os.path.isdir(ingest_dir):
            return []
        # Group by source hash
        groups: dict[str, dict] = {}
        for fname in os.listdir(ingest_dir):
            if not fname.startswith("ingest-") or not fname.endswith(".md"):
                continue
            fpath = os.path.join(ingest_dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read(800)
                fm, _ = _parse_frontmatter(raw)
            except Exception:
                continue
            source = fm.get("source", "unknown")
            src_hash = fname.split("-")[1] if "-" in fname else "?"
            if src_hash not in groups:
                groups[src_hash] = {
                    "source": source,
                    "source_type": fm.get("source_type", "unknown"),
                    "source_hash": src_hash,
                    "chunks": 0,
                    "ingested_at": fm.get("ingested_at", ""),
                    "tags": [],
                }
            groups[src_hash]["chunks"] += 1
            # Parse tags from frontmatter
            tags_str = fm.get("tags", "")
            if isinstance(tags_str, str) and tags_str:
                for t in tags_str.split(","):
                    t = t.strip().strip("-").strip()
                    if t and t not in groups[src_hash]["tags"]:
                        groups[src_hash]["tags"].append(t)
        return sorted(groups.values(), key=lambda x: x.get("ingested_at", ""), reverse=True)

    @staticmethod
    def delete_ingested(agent_id: str, source_hash: str,
                        project_name: str | None = None) -> dict:
        """Delete all chunks for a source hash."""
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        if not os.path.isdir(ingest_dir):
            return {"error": "No ingested documents found"}
        deleted = 0
        source_name = ""
        for fname in os.listdir(ingest_dir):
            if fname.startswith(f"ingest-{source_hash}-") and fname.endswith(".md"):
                if not source_name:
                    fpath = os.path.join(ingest_dir, fname)
                    try:
                        with open(fpath, "r") as f:
                            fm, _ = _parse_frontmatter(f.read(500))
                        source_name = fm.get("source", "unknown")
                    except Exception:
                        pass
                os.remove(os.path.join(ingest_dir, fname))
                deleted += 1
        collection = IngestManager._collection_name(agent_id, project_name)
        _qmd_debounced_embed(collection)
        return {"source": source_name, "source_hash": source_hash, "deleted": deleted}


# ─── Watched Folders (Auto-Ingestion) ────────────────────────────────

class IngestWatcher:
    """Background thread that polls watched folders and auto-ingests new/modified files."""

    POLL_INTERVAL = 30  # seconds

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Start the background watcher thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ingest_watcher")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        """Poll all watched folders across all agents and projects."""
        while not self._stop.is_set():
            try:
                self._scan_all()
            except Exception as e:
                logging.debug("IngestWatcher error: %s", e)
            self._stop.wait(self.POLL_INTERVAL)

    def _scan_all(self):
        """Scan watched folders for all agents and projects."""
        if not os.path.isdir(AGENTS_DIR):
            return
        for agent_name in os.listdir(AGENTS_DIR):
            if agent_name.startswith("."):
                continue
            agent_dir = os.path.join(AGENTS_DIR, agent_name)
            if not os.path.isdir(agent_dir):
                continue
            # Check agent-level watches (from agent.json)
            agent_json_path = os.path.join(agent_dir, "agent.json")
            if os.path.exists(agent_json_path):
                try:
                    with open(agent_json_path, "r") as f:
                        agent_cfg = json.load(f)
                    watches = agent_cfg.get("ingest_watch", [])
                    if watches:
                        self._process_watches(agent_name, None, watches, agent_dir)
                except (OSError, json.JSONDecodeError):
                    pass
            # Check project-level watches
            projects_dir = os.path.join(agent_dir, "projects")
            if os.path.isdir(projects_dir):
                for proj_name in os.listdir(projects_dir):
                    proj_dir = os.path.join(projects_dir, proj_name)
                    proj_json = os.path.join(proj_dir, "project.json")
                    if not os.path.exists(proj_json):
                        continue
                    try:
                        with open(proj_json, "r") as f:
                            proj_cfg = json.load(f)
                        watches = proj_cfg.get("watch_folders", [])
                        if watches:
                            self._process_watches(agent_name, proj_name, watches, proj_dir)
                    except (OSError, json.JSONDecodeError):
                        pass

    def _process_watches(self, agent_id: str, project_name: str | None,
                         watches: list[dict], base_dir: str):
        """Process watched folders, detect changes, ingest as needed."""
        registry_path = os.path.join(base_dir, "ingest_registry.json")
        registry = {}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    registry = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        watches_reg = registry.get("watches", {})
        changed = False

        for watch in watches:
            watch_path = watch.get("path", "")
            if not watch_path or not os.path.isdir(watch_path):
                continue
            pattern = watch.get("pattern", "*")
            recursive = watch.get("recursive", False)
            tags = watch.get("tags", [])
            chunk_size = watch.get("chunk_size", 1500)

            # Get or create registry entry for this watch
            wreg = watches_reg.get(watch_path, {"files": {}, "last_scan": ""})

            # Scan for matching files
            if recursive:
                matched_files = []
                for root, _dirs, files in os.walk(watch_path):
                    for fn in files:
                        if fnmatch.fnmatch(fn, pattern):
                            matched_files.append(os.path.join(root, fn))
            else:
                matched_files = [
                    os.path.join(watch_path, fn) for fn in os.listdir(watch_path)
                    if fnmatch.fnmatch(fn, pattern) and os.path.isfile(os.path.join(watch_path, fn))
                ]

            current_files = set()
            for fpath in matched_files:
                fname = os.path.basename(fpath)
                current_files.add(fname)
                try:
                    stat = os.stat(fpath)
                except OSError:
                    continue
                prev = wreg["files"].get(fname, {})
                if prev.get("mtime") == stat.st_mtime and prev.get("size") == stat.st_size:
                    continue  # unchanged

                # New or modified file — ingest
                try:
                    result = IngestManager.ingest_file(
                        agent_id, fpath, project_name=project_name,
                        tags=tags, chunk_size=chunk_size,
                    )
                    if "error" not in result:
                        wreg["files"][fname] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "hash": result.get("source_hash", ""),
                            "chunks": result.get("chunks", 0),
                            "ingested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        }
                        changed = True
                except Exception as e:
                    logging.debug("IngestWatcher: failed to ingest %s: %s", fpath, e)

            # Detect deleted files
            for fname in list(wreg["files"].keys()):
                if fname not in current_files:
                    src_hash = wreg["files"][fname].get("hash", "")
                    if src_hash:
                        IngestManager.delete_ingested(agent_id, src_hash, project_name)
                    del wreg["files"][fname]
                    changed = True

            wreg["last_scan"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            watches_reg[watch_path] = wreg

        if changed:
            registry["watches"] = watches_reg
            try:
                with open(registry_path, "w") as f:
                    json.dump(registry, f, indent=2)
            except OSError:
                pass


# Global IngestWatcher instance (started alongside scheduler in server.py)
_ingest_watcher: IngestWatcher | None = None


def _get_memory_store() -> MemoryStore | None:
    """Get the active memory store: thread-local (delegation/scheduler) or global (main thread)."""
    return getattr(_thread_local, 'memory_store', None) or _memory_store


def tool_memory_store(args: dict) -> str:
    """Store a memory. When a project is active, writes to project directory."""
    ms = _get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    content = args.get("content", "")
    description = args.get("description", "")
    mem_type = args.get("type", "general")
    if not name or not content:
        return _err("memory_store: name and content are required")
    # If project is active, store in project directory
    project = getattr(_thread_local, 'project', None)
    if project:
        agent_id = ms.agent_id
        proj_dir = os.path.join(AGENTS_DIR, agent_id, "projects", project)
        if os.path.isdir(proj_dir):
            proj_store = MemoryStore(agent_id=f"{agent_id}/{project}", base_dir=proj_dir)
            result = proj_store.store(name, content, description, mem_type)
            result["project"] = project
            return _ok(result)
    result = ms.store(name, content, description, mem_type)
    # Trigger near-term memory summary refresh when user-facing memories are stored
    # (skip if this IS the memory summary being written)
    if name != "Memory Summary" and mem_type in ("user", "feedback", "project"):
        try:
            agent_id = ms.agent_id if hasattr(ms, 'agent_id') else "main"
            trigger_memory_summary_refresh(agent_id)
        except Exception:
            pass
    return _ok(result)


def _graph_expand_results(results: list[dict], base_dir: str, ingest_dir: str,
                          max_hops: int = 1) -> list[dict]:
    """Follow 'related' frontmatter links from matched results for context expansion."""
    seen_files = {r.get("file_path", "") for r in results}
    expanded = list(results)
    frontier = list(results)
    for _hop in range(max_hops):
        next_frontier = []
        for r in frontier:
            fpath = r.get("file_path", "")
            if not fpath or not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "r") as f:
                    raw = f.read(2000)
                fm, _ = _parse_frontmatter(raw)
            except Exception:
                continue
            # Parse related field (simple YAML list parsing)
            related_raw = fm.get("related", "")
            if not related_raw:
                continue
            # related is stored as multi-line YAML in frontmatter, parse linked files
            related_files = re.findall(r'file:\s*(\S+\.md)', raw)
            for rel_file in related_files:
                # Try ingest_dir first, then base_dir
                for search_dir in (ingest_dir, base_dir):
                    rel_path = os.path.join(search_dir, rel_file)
                    if rel_path in seen_files or not os.path.exists(rel_path):
                        continue
                    seen_files.add(rel_path)
                    try:
                        with open(rel_path, "r") as f:
                            rel_raw = f.read()
                        rel_fm, rel_body = _parse_frontmatter(rel_raw)
                        mem = {
                            "id": hashlib.sha256(rel_fm.get("name", rel_file).encode()).hexdigest()[:12],
                            "name": rel_fm.get("name", rel_fm.get("title", rel_file.replace(".md", ""))),
                            "description": rel_fm.get("description", ""),
                            "type": rel_fm.get("type", "general"),
                            "content": rel_body,
                            "file_path": rel_path,
                            "score": max(0, (r.get("score", 0.5) - 0.2)),
                            "source_scope": "related",
                        }
                        expanded.append(mem)
                        next_frontier.append(mem)
                    except Exception:
                        continue
                    break  # found in one dir, skip the other
        frontier = next_frontier
    return expanded


def tool_memory_recall(args: dict) -> str:
    """Recall memories by searching. When a project is active, searches project first."""
    ms = _get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    mem_type = args.get("type")
    mode = args.get("mode", "")

    # Project-scoped search: search project collection first, then agent
    project = getattr(_thread_local, 'project', None)
    if project and query:
        agent_id = ms.agent_id
        proj_dir = os.path.join(AGENTS_DIR, agent_id, "projects", project)
        if os.path.isdir(proj_dir):
            proj_store = MemoryStore(agent_id=f"{agent_id}/{project}", base_dir=proj_dir)
            # Also search ingested subdir
            ingest_dir = os.path.join(proj_dir, "ingested")
            proj_results = proj_store.recall(query, limit, mem_type)
            # Tag project results
            for r in proj_results:
                r["source_scope"] = "project"
            # Then agent-level results
            agent_results = ms.recall(query, max(2, limit - len(proj_results)), mem_type)
            for r in agent_results:
                r["source_scope"] = "agent"
            results = proj_results + agent_results
            # Always expand via graph relationships (follow related links 1 hop)
            if results:
                results = _graph_expand_results(results, proj_dir, ingest_dir,
                                                max_hops=2 if mode == "graph" else 1)
            for r in results:
                if r.get("content") and len(r["content"]) > 4000:
                    r["content"] = r["content"][:4000] + "..."
            return _ok({"query": query, "project": project, "results": results[:limit], "count": len(results[:limit])})

    if not query:
        results = ms.list_all(mem_type)
        return _ok({"query": "", "results": results, "count": len(results)})
    results = ms.recall(query, limit, mem_type)

    # Always expand via graph relationships (1 hop default, 2 hops for explicit graph mode)
    if results:
        agent_id = ms.agent_id
        agent_dir = os.path.join(AGENTS_DIR, agent_id)
        ingest_dir = os.path.join(agent_dir, "ingested")
        results = _graph_expand_results(results, agent_dir, ingest_dir,
                                        max_hops=2 if mode == "graph" else 1)

    for r in results:
        if r.get("content") and len(r["content"]) > 4000:
            r["content"] = r["content"][:4000] + "..."

    # --- Co-recall tracking (Mechanism 3) ---
    if query and len(results) >= 2:
        try:
            result_files = [os.path.basename(r.get("file_path", "")) for r in results if r.get("file_path")]
            agent_id = ms.agent_id
            agent_dir = os.path.join(AGENTS_DIR, agent_id)
            threading.Thread(
                target=_record_recall_cooccurrence,
                args=(result_files, agent_id, agent_dir),
                daemon=True,
            ).start()
        except Exception:
            pass  # Co-recall tracking is best-effort

    return _ok({"query": query, "results": results, "count": len(results)})


def tool_memory_delete(args: dict) -> str:
    """Delete a memory."""
    ms = _get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    if not name:
        return _err("memory_delete: name is required")
    result = ms.delete(name)
    return _ok(result)


def tool_memory_shared(args: dict) -> str:
    """Access shared memory — global (main) or team (team head) scope."""
    action = args.get("action", "recall")
    scope = args.get("scope", "global")

    # Determine which agent's memory to use
    if scope == "team":
        # Find the team head for the calling agent
        caller_id = getattr(_thread_local, "delegate_agent_id", None)
        if not caller_id:
            agent = getattr(_thread_local, 'current_agent', None) or _current_agent
            caller_id = agent.agent_id if agent else "main"
        team_info = _get_agent_team_info(caller_id)
        if not team_info:
            return _err("memory_shared: agent is not in any team — use scope='global' instead")
        team_head_id = team_info["head"]
        target_agent = AgentConfig(team_head_id)
        source_label = f"{team_info['name']} (team)"
    else:
        target_agent = AgentConfig("main")
        source_label = "main (shared)"

    shared_store = MemoryStore(agent_id=target_agent.agent_id, base_dir=target_agent.memory_dir)

    if action == "store":
        name = args.get("name", "")
        content = args.get("content", "")
        description = args.get("description", "")
        mem_type = args.get("type", "general")
        if not name or not content:
            return _err("memory_shared store: name and content are required")
        result = shared_store.store(name, content, description, mem_type)
        result["source"] = source_label
        return _ok(result)
    else:  # recall
        query = args.get("query", "")
        limit = args.get("limit", 10)
        mem_type = args.get("type")
        if not query:
            results = shared_store.list_all(mem_type)
        else:
            results = shared_store.recall(query, limit, mem_type)
            # Graph expansion on shared memory too
            if results:
                shared_dir = os.path.join(AGENTS_DIR, target_agent.agent_id)
                shared_ingest = os.path.join(shared_dir, "ingested")
                results = _graph_expand_results(results, shared_dir, shared_ingest, max_hops=1)
            for r in results:
                if r.get("content") and len(r["content"]) > 4000:
                    r["content"] = r["content"][:4000] + "..."
        return _ok({"query": query, "source": source_label, "results": results[:limit], "count": len(results[:limit])})


def tool_use_skill(args: dict) -> str:
    """Load a skill's instructions into context."""
    skill_name = args.get("skill", "")
    if not skill_name:
        return _err("use_skill: skill name is required")
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    if not agent:
        return _err("use_skill: no active agent")

    body = agent.load_skill(skill_name)
    if body is None:
        available = [s.get("slug", s["name"]) for s in agent.list_skills()]
        return _err(f"use_skill: skill '{skill_name}' not found. Available: {', '.join(available) or 'none'}")

    return _ok({"skill": skill_name, "instructions": body})



# ─── Memory Summary ────────────────────────────────────────────────
# Automatic periodic synthesis of chat history and scheduled task results.
# Configured per-agent in agent.json: { "memory_summary": { "enabled": true, "frequency": "every 24h", "start_time": "03:00" } }

MEMORY_SUMMARY_DEFAULTS = {
    "enabled": True,
    "paused": False,
    "frequency": "every 24h",
    "start_time": "03:00",
}


def _get_memory_summary_config(agent_id: str) -> dict:
    """Read memory_summary settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    ms_cfg = cfg.get("memory_summary", {})
    result = dict(MEMORY_SUMMARY_DEFAULTS)
    if isinstance(ms_cfg, dict):
        for k in ("enabled", "paused", "frequency", "start_time"):
            if k in ms_cfg:
                result[k] = ms_cfg[k]
    elif isinstance(ms_cfg, bool):
        result["enabled"] = ms_cfg
    return result


def _memory_summary_schedule_name(agent_id: str) -> str:
    return f"_memory_summary_{agent_id}"


def _memory_summary_schedule_str(cfg: dict) -> str:
    """Convert memory_summary config to a scheduler schedule string.
    e.g. 'daily 03:00' or 'every 24h' depending on whether start_time is set."""
    freq = cfg.get("frequency", "every 24h").strip().lower()
    start_time = cfg.get("start_time", "").strip()
    # If frequency is a simple 'every Xh/Xd' and start_time is provided, use 'daily HH:MM'
    if start_time and re.match(r'^\d{1,2}:\d{2}$', start_time):
        # For 24h frequency, use daily at start_time
        m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if (unit == 'h' and val == 24) or (unit == 'd' and val == 1):
                return f"daily {start_time}"
            elif unit == 'h' and val < 24:
                return freq  # sub-daily interval, ignore start_time
            elif unit == 'd' and val > 1:
                return freq  # multi-day, keep as interval
    return freq


def _gather_recent_chat_history(agent_id: str, hours: int = 48, max_sessions: int = 10,
                                 max_messages_per_session: int = 20) -> str:
    """Read recent chat sessions and messages for an agent directly from chats.db.
    Returns a formatted text digest suitable for inclusion in a prompt."""
    # Chat DB is always in main agent dir (shared across all agents)
    chat_db_path = os.path.join(AGENTS_DIR, "main", "chats.db")
    if not os.path.exists(chat_db_path):
        return ""
    conn = None
    try:
        conn = sqlite3.connect(chat_db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - (hours * 3600)
        sessions = conn.execute(
            "SELECT id, title, model, status, created_at, last_active FROM sessions "
            "WHERE agent_id = ? AND last_active > ? "
            "AND status IN ('active', 'archived') "
            "ORDER BY last_active DESC LIMIT ?",
            (agent_id, cutoff, max_sessions)
        ).fetchall()
        # Note: 'incognito' status sessions are excluded by the IN clause above
        if not sessions:
            conn.close()
            return ""
        lines = []
        for sess in sessions:
            ts = datetime.datetime.fromtimestamp(sess["last_active"]).strftime("%Y-%m-%d %H:%M") if sess["last_active"] else "?"
            title = sess["title"] or "(untitled)"
            status_tag = " [archived]" if sess["status"] == "archived" else ""
            lines.append(f"\n### Session: {title} ({ts}){status_tag}")
            msgs = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                (sess["id"],)
            ).fetchall()
            # Take first + last messages to stay within budget
            msg_list = list(msgs)
            if len(msg_list) > max_messages_per_session:
                selected = msg_list[:max_messages_per_session // 2] + msg_list[-(max_messages_per_session // 2):]
                lines.append(f"  ({len(msg_list)} messages total, showing first and last {max_messages_per_session // 2})")
            else:
                selected = msg_list
            for msg in selected:
                role = msg["role"].upper()
                content = msg["content"]
                # Parse JSON content (tool calls etc)
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        # Multi-part message (text + tool_use blocks)
                        text_parts = [p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") == "text"]
                        content = " ".join(text_parts).strip() or "(tool calls)"
                    elif isinstance(parsed, dict):
                        content = parsed.get("text", str(parsed))
                    elif isinstance(parsed, str):
                        content = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                # Truncate long messages
                if isinstance(content, str) and len(content) > 400:
                    content = content[:400] + "..."
                if content and content != "(tool calls)":
                    lines.append(f"  [{role}] {content}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Error reading chat history: {e})"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _gather_recent_schedule_history(agent_id: str, hours: int = 48, limit: int = 15) -> str:
    """Read recent scheduled task execution results for an agent from scheduler.db.
    Returns a formatted text digest."""
    if not _scheduler:
        return ""
    try:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
            rows = conn.execute(
                "SELECT schedule_name, task, status, result, started_at, finished_at "
                "FROM schedule_history WHERE agent = ? AND finished_at > ? "
                "AND schedule_name NOT LIKE '_memory_summary_%' "
                "ORDER BY finished_at DESC LIMIT ?",
                (agent_id, cutoff, limit)
            ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            ts = r["finished_at"][:16] if r["finished_at"] else "?"
            status = r["status"] or "unknown"
            name = r["schedule_name"] or "(unnamed)"
            lines.append(f"\n### Task: {name} [{status}] ({ts})")
            lines.append(f"  Prompt: {(r['task'] or '')[:200]}")
            result = r["result"] or ""
            if len(result) > 1000:
                result = result[:1000] + "..."
            if result:
                lines.append(f"  Result: {result}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Error reading schedule history: {e})"


def _build_memory_summary_prompt(agent_id: str) -> str:
    """Build the task prompt for the memory summary scheduled task.
    Gathers real chat and schedule history and injects it into the prompt."""
    now = datetime.datetime.now()
    # Determine lookback based on frequency config
    ms_cfg = _get_memory_summary_config(agent_id)
    freq = ms_cfg.get("frequency", "every 24h")
    # Parse hours from frequency for lookback window (double it for overlap)
    lookback_hours = 48  # default
    m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq.lower())
    if m:
        val, unit = int(m.group(1)), m.group(2)[0]
        if unit == 'h':
            lookback_hours = val * 2
        elif unit == 'd':
            lookback_hours = val * 48

    chat_digest = _gather_recent_chat_history(agent_id, hours=lookback_hours)
    schedule_digest = _gather_recent_schedule_history(agent_id, hours=lookback_hours)

    # Build the data section
    data_section = ""
    if chat_digest:
        data_section += f"\n## Recent Conversations (last {lookback_hours}h)\n{chat_digest}\n"
    else:
        data_section += "\n## Recent Conversations\nNo conversations found in this period.\n"

    if schedule_digest:
        data_section += f"\n## Recent Scheduled Task Results (last {lookback_hours}h)\n{schedule_digest}\n"
    else:
        data_section += "\n## Recent Scheduled Task Results\nNo scheduled task executions found in this period.\n"

    return f"""Perform a Memory Summary update for agent '{agent_id}'.

Your job is to create or update a structured synthesis of recent activity. Below is the actual data from your recent conversations and scheduled task executions.

{data_section}

---

Now do the following:

1. Use memory_recall with query "Memory Summary" to find any existing summary.

2. Analyze the conversation and task data above, then write or update the synthesis using the following structured sections. Keep each section concise — omit a section if there is nothing relevant for it.

   **## User Profile & Context**
   Who the user is, their role, responsibilities, and domain knowledge. What kind of collaboration they prefer.

   **## Communication & Working Style**
   Communication preferences, response style, level of detail they expect, what to avoid.

   **## Technical Preferences**
   Coding style, preferred languages/tools/frameworks, conventions, architecture decisions.

   **## Active Projects & Ongoing Work**
   Current projects, their status, key decisions made, open questions, deadlines.

   **## Task Execution Insights**
   Patterns from scheduled task outcomes — what works, what fails, recurring issues.

   **## Key Decisions & Context**
   Important decisions, user feedback, and context that should inform future interactions.

3. Store the updated synthesis using memory_store with:
   - name: "Memory Summary"
   - type: "general"
   - description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
   - content: The structured synthesis (300-800 words)

Focus on actionable insights, not a chronological log. If an existing summary exists, integrate new information — preserve important older context while adding recent developments. Remove information from conversations that no longer appear in the data above (they may have been deleted by the user). Drop stale details that are no longer relevant.

Current date and time: {now.strftime('%Y-%m-%d %H:%M')}"""


def ensure_memory_summary_schedules():
    """Ensure scheduler entries exist for all agents with memory_summary enabled.
    Called once on server/CLI startup after scheduler is initialized."""
    if not _scheduler:
        return
    agents = list_agents()
    existing = {s["name"]: s for s in _scheduler.list_all()}

    for agent_id in agents:
        sched_name = _memory_summary_schedule_name(agent_id)
        ms_cfg = _get_memory_summary_config(agent_id)

        if ms_cfg["enabled"] and not ms_cfg.get("paused"):
            schedule_str = _memory_summary_schedule_str(ms_cfg)
            task_prompt = _build_memory_summary_prompt(agent_id)

            if sched_name in existing:
                # Update if schedule changed
                old = existing[sched_name]
                if old["schedule"] != schedule_str or old.get("enabled") != 1:
                    _scheduler.remove(sched_name)
                    _scheduler.add(sched_name, task_prompt, schedule_str,
                                   agent=agent_id, timeout=600)
            else:
                _scheduler.add(sched_name, task_prompt, schedule_str,
                               agent=agent_id, timeout=600)
        else:
            # Disabled or paused — remove schedule if exists
            if sched_name in existing:
                _scheduler.remove(sched_name)


def get_memory_summary(agent_id: str) -> str | None:
    """Load the latest memory summary for an agent, if it exists.
    Returns None if memory summary is paused."""
    # Check if paused
    ms_cfg = _get_memory_summary_config(agent_id)
    if ms_cfg.get("paused"):
        return None
    ms = MemoryStore(agent_id)
    # Try to find the memory summary file
    for fname in os.listdir(ms.dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        fpath = os.path.join(ms.dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
            if fm.get("name") == "Memory Summary":
                return body.strip()
        except Exception:
            continue
    return None


def reset_memory_summary(agent_id: str) -> dict:
    """Delete the memory summary file for an agent (Gap 5: reset)."""
    ms = MemoryStore(agent_id)
    deleted = []
    for fname in os.listdir(ms.dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        fpath = os.path.join(ms.dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, _ = _parse_frontmatter(raw)
            if fm.get("name") == "Memory Summary":
                os.remove(fpath)
                deleted.append(fname)
        except Exception:
            continue
    if deleted:
        _qmd_debounced_embed(agent_id)
    return {"agent": agent_id, "deleted": deleted, "status": "reset"}


def trigger_memory_summary_refresh(agent_id: str):
    """Trigger an immediate memory summary refresh for an agent.
    Runs directly in a background thread instead of waiting for the scheduler loop."""
    if not _scheduler:
        return
    ms_cfg = _get_memory_summary_config(agent_id)
    if not ms_cfg.get("enabled") or ms_cfg.get("paused"):
        return
    sched_name = _memory_summary_schedule_name(agent_id)
    # Check if the schedule exists
    existing = {s["name"]: s for s in _scheduler.list_all()}
    if sched_name not in existing:
        return
    # Execute immediately in a background thread instead of relying on scheduler loop
    task_row = existing[sched_name]
    def _run():
        try:
            _scheduler._execute_scheduled(task_row)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True, name=f"memory_summary_refresh_{agent_id}").start()


# ─── Relationship Discovery System ────────────────────────────────────

# --- Mechanism 2: Entity Extraction + Auto-linking ---

_RE_CAPITALIZED = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b')
_RE_MENTIONS = re.compile(r'@\w+')
_RE_URLS = re.compile(r'https?://\S+')
_RE_FILEPATHS = re.compile(r'(?:/[\w.-]+)+\.\w+')
_RE_HASHTAGS = re.compile(r'#\w+')


def _extract_entities(text: str) -> set[str]:
    """Extract entities from text using fast regex heuristics (no LLM).
    Returns set of entity strings."""
    entities = set()
    for m in _RE_CAPITALIZED.finditer(text):
        entities.add(m.group())
    for m in _RE_MENTIONS.finditer(text):
        entities.add(m.group())
    for m in _RE_URLS.finditer(text):
        entities.add(m.group().rstrip('.,;)'))
    for m in _RE_FILEPATHS.finditer(text):
        entities.add(m.group())
    for m in _RE_HASHTAGS.finditer(text):
        entities.add(m.group())
    return entities


# Entity index: maps entity string -> set of filenames mentioning it
# Keyed per agent: _entity_indices[agent_id] = {entity: {filename, ...}}
_entity_indices: dict[str, dict[str, set[str]]] = {}
_entity_index_lock = threading.Lock()
_entity_index_initialized: set[str] = set()


def _rebuild_entity_index(agent_id: str):
    """Full rescan of memory files to build entity index for an agent."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return
    index: dict[str, set[str]] = {}
    # Scan top-level agent dir
    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        # Skip ingested chunks — only agent-created memories
        if fname.startswith("ingest-"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            _, body = _parse_frontmatter(raw)
            entities = _extract_entities(body)
            for ent in entities:
                index.setdefault(ent, set()).add(fname)
        except Exception:
            continue
    # Also scan chats-indexed/ subdirectory for chat transcript entities
    chats_dir = os.path.join(agent_dir, "chats-indexed")
    if os.path.isdir(chats_dir):
        for fname in os.listdir(chats_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(chats_dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read()
                _, body = _parse_frontmatter(raw)
                entities = _extract_entities(body)
                for ent in entities:
                    index.setdefault(ent, set()).add(fname)
            except Exception:
                continue
    with _entity_index_lock:
        _entity_indices[agent_id] = index
        _entity_index_initialized.add(agent_id)


def _ensure_entity_index(agent_id: str):
    """Lazy-initialize entity index on first access."""
    if agent_id not in _entity_index_initialized:
        _rebuild_entity_index(agent_id)


def _update_entity_index(agent_id: str, filename: str, entities: set[str]):
    """Incrementally update entity index when a memory is stored."""
    _ensure_entity_index(agent_id)
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        # Remove old entries for this filename
        for ent in list(idx.keys()):
            idx[ent].discard(filename)
            if not idx[ent]:
                del idx[ent]
        # Add new entries
        for ent in entities:
            idx.setdefault(ent, set()).add(filename)
        _entity_indices[agent_id] = idx


def _find_entity_matches(agent_id: str, filename: str, entities: set[str]) -> list[str]:
    """Find other files sharing entities with the given file. Returns list of filenames."""
    _ensure_entity_index(agent_id)
    matches = set()
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        for ent in entities:
            for other_file in idx.get(ent, set()):
                if other_file != filename:
                    matches.add(other_file)
    return list(matches)


def _add_related_to_file(file_path: str, rel_file: str, rel_type: str) -> bool:
    """Add a related entry to a memory file's frontmatter if not already present.
    Returns True if file was modified."""
    try:
        with open(file_path, "r") as f:
            raw = f.read()
    except Exception:
        return False

    # Check if relationship already exists
    existing_rels = re.findall(r'file:\s*(\S+\.md)', raw)
    if rel_file in existing_rels:
        return False

    # Find the end of frontmatter to insert before the closing ---
    fm_match = re.match(r'^(---\s*\n)(.*?)(\n---\s*\n)(.*)$', raw, re.DOTALL)
    if not fm_match:
        return False

    opener, fm_text, closer, body = fm_match.groups()

    # Check if related: section already exists
    new_entry = f"  - file: {rel_file}\n    type: {rel_type}"
    if "related:" in fm_text:
        # Append to existing related section
        fm_text = fm_text.rstrip() + "\n" + new_entry
    else:
        # Add new related section
        fm_text = fm_text.rstrip() + "\nrelated:\n" + new_entry

    new_raw = opener + fm_text + closer + body
    try:
        with open(file_path, "w") as f:
            f.write(new_raw)
        return True
    except Exception:
        return False


# --- Mechanism 3: Co-recall Linking ---

# Tracks co-occurrence of files recalled together
# Key: frozenset({file1, file2}), Value: count
_recall_cooccurrence: dict[frozenset, int] = {}
_recall_cooccurrence_lock = threading.Lock()
_CO_RECALL_THRESHOLD = 3  # Auto-link after this many co-recalls


def _record_recall_cooccurrence(result_files: list[str], agent_id: str, base_dir: str):
    """Record which files appeared together in a recall result.
    When threshold is reached, auto-add co_recalled relationship."""
    if len(result_files) < 2:
        return
    # Only consider first 10 results to limit combinatorial explosion
    files = result_files[:10]
    pairs_to_link = []
    with _recall_cooccurrence_lock:
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                pair = frozenset({files[i], files[j]})
                _recall_cooccurrence[pair] = _recall_cooccurrence.get(pair, 0) + 1
                if _recall_cooccurrence[pair] == _CO_RECALL_THRESHOLD:
                    pairs_to_link.append((files[i], files[j]))

    # Auto-link pairs that reached threshold (outside lock)
    for f1, f2 in pairs_to_link:
        f1_path = os.path.join(base_dir, f1) if not os.path.isabs(f1) else f1
        f2_path = os.path.join(base_dir, f2) if not os.path.isabs(f2) else f2
        modified = False
        if os.path.exists(f1_path) and os.path.exists(f2_path):
            f1_name = os.path.basename(f1)
            f2_name = os.path.basename(f2)
            if _add_related_to_file(f1_path, f2_name, "co_recalled"):
                modified = True
            if _add_related_to_file(f2_path, f1_name, "co_recalled"):
                modified = True
        if modified:
            _qmd_debounced_embed(agent_id)


# --- Mechanism 1: LLM-based Relationship Discovery ---

RELATIONSHIP_DISCOVERY_DEFAULTS = {
    "enabled": True,
    "frequency": "every 24h",
    "start_time": "04:15",  # 15 min after memory summary default (03:00)
}


def _get_relationship_discovery_config(agent_id: str) -> dict:
    """Read relationship_discovery settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    rd_cfg = cfg.get("relationship_discovery", {})
    result = dict(RELATIONSHIP_DISCOVERY_DEFAULTS)
    if isinstance(rd_cfg, dict):
        for k in ("enabled", "frequency", "start_time"):
            if k in rd_cfg:
                result[k] = rd_cfg[k]
    elif isinstance(rd_cfg, bool):
        result["enabled"] = rd_cfg
    return result


def _get_auto_memory_config(agent_id: str) -> dict:
    """Read auto_memory settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    am = cfg.get("auto_memory", {})
    return {
        "enabled": am.get("enabled", True),
        "min_message_length": am.get("min_message_length", 20),
    }


def _auto_memory_extract(agent_id: str, user_message: str, assistant_response: str):
    """Background: check if the conversation exchange contains info worth auto-storing.
    Uses lightweight heuristics first, then optional LLM for borderline cases."""

    # Check config
    am_cfg = _get_auto_memory_config(agent_id)
    if not am_cfg.get("enabled", True):
        return

    min_len = am_cfg.get("min_message_length", 20)

    # Skip if messages are too short (small talk, acknowledgments)
    if len(user_message) < min_len or len(assistant_response) < 50:
        return

    # Skip if this looks like a tool-heavy response (already captured by tool actions)
    if assistant_response.count('tool_use') > 3:
        return

    # Heuristic detection of memorable content
    memorable_patterns = []

    # 1. User corrections / feedback
    correction_words = ['don\'t', 'stop', 'no,', 'actually', 'instead', 'wrong', 'not like that',
                        'prefer', 'always', 'never', 'remember that', 'keep in mind']
    user_lower = user_message.lower()
    for word in correction_words:
        if word in user_lower:
            memorable_patterns.append(('feedback', f'User correction/preference detected: "{word}"'))
            break

    # 2. User shares personal/role info
    identity_patterns = ['i am ', 'i\'m a', 'my role', 'i work ', 'my name', 'my team',
                         'my company', 'we use', 'our team', 'our project']
    for pat in identity_patterns:
        if pat in user_lower:
            memorable_patterns.append(('user', f'User identity/role info detected: "{pat}"'))
            break

    # 3. Decisions / commitments
    decision_patterns = ['let\'s go with', 'we decided', 'the plan is', 'going forward',
                         'from now on', 'the approach', 'we\'ll use', 'agreed']
    for pat in decision_patterns:
        if pat in user_lower:
            memorable_patterns.append(('project', f'Decision detected: "{pat}"'))
            break

    # 4. References to external resources
    if any(p in user_lower for p in ['http://', 'https://', 'the repo', 'the doc', 'the wiki',
                                      'slack channel', 'linear', 'jira', 'confluence']):
        memorable_patterns.append(('reference', 'External resource reference detected'))

    if not memorable_patterns:
        return  # Nothing worth storing

    # Use a quick LLM call to extract and format the memory
    mem_type, trigger = memorable_patterns[0]

    # Build a focused extraction prompt
    prompt = (
        f"Extract a concise, actionable memory from this conversation exchange.\n\n"
        f"USER: {user_message[:500]}\n\n"
        f"A: {assistant_response[:500]}\n\n"
        f"Trigger: {trigger}\n"
        f"Memory type: {mem_type}\n\n"
        f"Rules:\n"
        f'- Output ONLY a JSON object: {{"name": "short-title", "content": "the key fact/preference/decision", "type": "{mem_type}", "description": "one-line summary"}}\n'
        f"- Content should be 1-3 sentences, factual, no fluff\n"
        f'- If the exchange doesn\'t actually contain memorable info, output: {{"skip": true}}\n'
        f"- Do NOT output anything except the JSON object"
    )

    try:
        # Use cheapest model
        model = None
        if _models_config:
            for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('cost_input', 999)):
                if cfg.get('enabled', True):
                    ml = mid.lower()
                    if 'haiku' in ml:
                        model = mid
                        break
            if not model:
                for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('priority', 0)):
                    if cfg.get('enabled', True):
                        model = mid
                        break

        if not model or not _delegate_api_key:
            return

        ms = MemoryStore(agent_id)
        result = _run_delegate(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt="You are a memory extraction assistant. Output only valid JSON.",
            memory_store=ms,
            inference_params={"max_tokens": 256, "temperature": 0.1},
        )

        if not result or 'skip' in result.lower():
            return

        # Parse JSON
        json_match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if not json_match:
            return

        data = json.loads(json_match.group())
        if data.get('skip'):
            return

        name = data.get('name', '').strip()
        content = data.get('content', '').strip()
        mem_type_extracted = data.get('type', mem_type)
        description = data.get('description', '').strip()

        if not name or not content:
            return

        # Check if similar memory already exists
        existing = ms.recall(name, limit=3)
        for e in existing:
            if e.get('score', 0) > 0.8 and e.get('name', '').lower() == name.lower():
                return  # Already stored

        # Store it
        ms.store(name, content, description, mem_type_extracted)
        logging.info(f"Auto-memory stored for {agent_id}: {name} ({mem_type_extracted})")

    except Exception as e:
        logging.debug(f"Auto-memory extraction failed for {agent_id}: {e}")


def _build_relationship_discovery_prompt(agent_id: str) -> str:
    """Build a prompt listing agent memories for LLM-based relationship discovery.
    Loads up to 30 non-ingested memory files."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return ""

    memories = []
    for fname in sorted(os.listdir(agent_dir)):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        if fname.startswith("ingest-"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
            memories.append({
                "file": fname,
                "name": fm.get("name", fname.replace(".md", "")),
                "type": fm.get("type", "general"),
                "preview": body[:200].replace("\n", " "),
            })
        except Exception:
            continue
        if len(memories) >= 30:
            break

    if len(memories) < 2:
        return ""

    listing = "\n".join(
        f"- **{m['file']}** (name: {m['name']}, type: {m['type']}): {m['preview']}"
        for m in memories
    )

    return f"""Analyze the following memories for agent '{agent_id}' and identify relationships between them.

## Memories

{listing}

## Task

Given these memories, identify pairs that are related. For each pair, specify:
- from_file: the source memory filename
- to_file: the target memory filename
- type: one of (references, same_topic, depends_on, contradicts, extends)
- reason: brief explanation (under 20 words)

Output ONLY a JSON array of objects with those fields. If no relationships found, output [].

Example:
[{{"from_file": "foo_abc123.md", "to_file": "bar_def456.md", "type": "same_topic", "reason": "Both discuss authentication patterns"}}]

Be selective — only report meaningful relationships, not superficial ones. Aim for precision over recall."""


def _apply_discovered_relationships(agent_id: str, relationships: list[dict]):
    """Apply discovered relationships to memory files.
    Updates frontmatter of both files for each relationship."""
    if not relationships:
        return
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    modified = False
    for rel in relationships:
        from_file = rel.get("from_file", "")
        to_file = rel.get("to_file", "")
        rel_type = rel.get("type", "references")
        # Validate type
        if rel_type not in ("references", "same_topic", "depends_on", "contradicts", "extends"):
            rel_type = "references"
        if not from_file or not to_file:
            continue
        from_path = os.path.join(agent_dir, from_file)
        to_path = os.path.join(agent_dir, to_file)
        if not os.path.exists(from_path) or not os.path.exists(to_path):
            continue
        if _add_related_to_file(from_path, to_file, rel_type):
            modified = True
        # Add reverse link (bidirectional)
        reverse_type = rel_type
        if rel_type == "depends_on":
            reverse_type = "extends"  # reverse of depends_on
        elif rel_type == "extends":
            reverse_type = "depends_on"
        if _add_related_to_file(to_path, from_file, reverse_type):
            modified = True
    if modified:
        _qmd_debounced_embed(agent_id)


def trigger_relationship_discovery(agent_id: str):
    """Run LLM-based relationship discovery immediately in a background thread."""
    if not _delegate_api_key:
        logging.warning("Relationship discovery skipped: delegate API not configured")
        return
    prompt = _build_relationship_discovery_prompt(agent_id)
    if not prompt:
        return

    def _run():
        try:
            # Use the delegate model (same as scheduled tasks)
            target = AgentConfig(agent_id)
            model = target.preferred_model or _delegate_fallback_model or "claude-sonnet-4-6"
            ms = MemoryStore(agent_id)
            logging.info(f"Relationship discovery starting for {agent_id} using {model}")
            result_text = _run_delegate(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                system_prompt="You are a relationship analysis assistant. Analyze the given memories and output ONLY a valid JSON array of relationships. No explanations, no markdown, just the JSON array.",
                memory_store=ms,
                inference_params={"max_tokens": 16384, "temperature": 0.2},
            )
            if not result_text:
                logging.warning(f"Relationship discovery for {agent_id}: empty response")
                return
            if result_text.startswith("Delegation error:"):
                logging.warning(f"Relationship discovery for {agent_id}: {result_text}")
                return
            # Extract JSON from response (may have markdown code fences)
            json_match = re.search(r'\[.*?\]', result_text, re.DOTALL)
            if json_match:
                relationships = json.loads(json_match.group())
                if isinstance(relationships, list) and relationships:
                    _apply_discovered_relationships(agent_id, relationships)
                    logging.info(f"Relationship discovery for {agent_id}: found {len(relationships)} relationships")
                else:
                    logging.info(f"Relationship discovery for {agent_id}: no relationships found")
            else:
                logging.warning(f"Relationship discovery for {agent_id}: no JSON in response: {result_text[:200]}")
        except Exception as e:
            logging.warning(f"Relationship discovery failed for {agent_id}: {e}")
            import traceback
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True, name=f"rel_discovery_{agent_id}").start()


def _relationship_discovery_schedule_name(agent_id: str) -> str:
    return f"_relationship_discovery_{agent_id}"


def _relationship_discovery_schedule_str(cfg: dict) -> str:
    """Convert relationship_discovery config to a scheduler schedule string."""
    freq = cfg.get("frequency", "every 24h").strip().lower()
    start_time = cfg.get("start_time", "").strip()
    if start_time and re.match(r'^\d{1,2}:\d{2}$', start_time):
        m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if (unit == 'h' and val == 24) or (unit == 'd' and val == 1):
                return f"daily {start_time}"
            elif unit == 'h' and val < 24:
                return freq
            elif unit == 'd' and val > 1:
                return freq
    return freq


def _build_relationship_discovery_task_prompt(agent_id: str) -> str:
    """Build the scheduled task prompt for relationship discovery."""
    return f"""Perform relationship discovery for agent '{agent_id}'.

Use memory_recall with an empty query to list all memories, then analyze them for relationships.

For each pair of related memories, use memory_store to update the related frontmatter.
Focus on meaningful relationships: references, same_topic, depends_on, contradicts, extends.

Be selective — only report meaningful relationships, not superficial ones."""


def ensure_relationship_discovery_schedules():
    """Ensure scheduler entries exist for all agents with relationship_discovery enabled."""
    if not _scheduler:
        return
    agents = list_agents()
    existing = {s["name"]: s for s in _scheduler.list_all()}

    for agent_id in agents:
        sched_name = _relationship_discovery_schedule_name(agent_id)
        rd_cfg = _get_relationship_discovery_config(agent_id)

        if rd_cfg["enabled"]:
            schedule_str = _relationship_discovery_schedule_str(rd_cfg)
            task_prompt = _build_relationship_discovery_task_prompt(agent_id)

            if sched_name in existing:
                old = existing[sched_name]
                if old["schedule"] != schedule_str or old.get("enabled") != 1:
                    _scheduler.remove(sched_name)
                    _scheduler.add(sched_name, task_prompt, schedule_str,
                                   agent=agent_id, timeout=600)
            else:
                _scheduler.add(sched_name, task_prompt, schedule_str,
                               agent=agent_id, timeout=600)
        else:
            if sched_name in existing:
                _scheduler.remove(sched_name)


def get_graph_stats(agent_id: str) -> dict:
    """Return knowledge graph statistics for an agent."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return {"error": f"Agent not found: {agent_id}"}

    total_nodes = 0
    total_edges = 0
    auto_discovered_edges = 0
    edge_type_counts: dict[str, int] = {}
    entity_count = 0

    auto_edge_types = {"same_topic", "co_recalled", "depends_on", "contradicts", "extends"}

    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        if fname.startswith("ingest-"):
            continue
        total_nodes += 1
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read(2000)
            # Count edges (related entries)
            rel_files = re.findall(r'file:\s*(\S+\.md)', raw)
            rel_types = re.findall(r'type:\s*(\w+)', raw)
            for i, _ in enumerate(rel_files):
                total_edges += 1
                rtype = rel_types[i] if i < len(rel_types) else "references"
                edge_type_counts[rtype] = edge_type_counts.get(rtype, 0) + 1
                if rtype in auto_edge_types:
                    auto_discovered_edges += 1
        except Exception:
            continue

    # Count entities from entity index
    _ensure_entity_index(agent_id)
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        entity_count = len(idx)

    return {
        "agent": agent_id,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "auto_discovered_edges": auto_discovered_edges,
        "entity_count": entity_count,
        "edge_types": edge_type_counts,
    }


_thread_local = threading.local()


MAX_DELEGATE_TOOL_ROUNDS = 10  # Limit for delegated/scheduled tasks (timeout is the real safety net)


def _run_delegate(messages: list[dict], model: str, system_prompt: str,
                  memory_store: MemoryStore | None = None,
                  cancel_token: CancelToken | None = None,
                  event_callback=None,
                  inference_params: dict | None = None) -> str | None:
    """Run a delegated task in a fresh context. Returns the final text response.
    Thread-safe: uses thread-local storage for memory instead of swapping globals."""
    # Store memory in thread-local so tool_memory_* can find it
    if memory_store:
        _thread_local.memory_store = memory_store

    api_key = _delegate_api_key
    base_url = _delegate_base_url
    api_type = _delegate_api_type

    headers = make_headers(api_key, api_type)

    if api_type == "openai":
        endpoint = f"{base_url}/chat/completions"
        aug_messages = [{"role": "system", "content": system_prompt}] + messages
    else:
        endpoint = f"{base_url}/messages"
        aug_messages = list(messages)

    payload = {
        "model": model,
        "max_tokens": get_model_max_output(model),
        "messages": aug_messages,
        "stream": True,
        "tools": TOOL_DEFINITIONS if api_type != "openai" else TOOL_DEFINITIONS_OPENAI,
    }
    if inference_params:
        provider = _models_config.get(model, {}).get("provider", "")
        _apply_inference_to_payload(payload, inference_params, api_type, provider)
    if api_type != "openai":
        payload["system"] = system_prompt

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        # Use stricter tool round limit via thread-local (thread-safe)
        _thread_local.max_tool_rounds = MAX_DELEGATE_TOOL_ROUNDS
        try:
            with urllib.request.urlopen(request) as response:
                if api_type == "openai":
                    return _handle_openai_response(
                        response, payload, messages, model, api_key, base_url,
                        api_type, True, True, headers, endpoint,
                        cancel_token, 0, event_callback)
                else:
                    return _handle_anthropic_response(
                        response, payload, messages, model, api_key, base_url,
                        api_type, True, True, headers, endpoint,
                        cancel_token, 0, event_callback)
        finally:
            _thread_local.max_tool_rounds = None
    except Exception as e:
        return f"Delegation error: {e}"


# Globals for delegation (set in _run_interactive)
_delegate_fallback_model: str | None = None
_delegate_api_key: str = ""
_delegate_base_url: str = ""
_delegate_api_type: str = "anthropic"


# --- Background Task Runner ---

import uuid as _uuid


class TaskRunner:
    """Manages background agent tasks with status tracking and cancellation."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}  # task_id -> task_info
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, agent_id: str, task: str, model: str | None = None) -> str:
        """Submit a task to run in a background thread. Returns task_id."""
        task_id = _uuid.uuid4().hex[:8]
        cancel_flag = threading.Event()

        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "agent": agent_id,
                "task": task,
                "model": model,
                "status": "running",
                "result": None,
                "error": None,
                "submitted_at": datetime.datetime.now().isoformat(),
                "finished_at": None,
            }
            self._cancel_flags[task_id] = cancel_flag

        thread = threading.Thread(
            target=self._run_task, args=(task_id, agent_id, task, model, cancel_flag),
            daemon=True)
        self._threads[task_id] = thread
        thread.start()
        return task_id

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            return self._tasks.get(task_id, {}).copy() if task_id in self._tasks else None

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [t.copy() for t in self._tasks.values()]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            if self._tasks[task_id]["status"] != "running":
                return False
            self._cancel_flags[task_id].set()
            self._tasks[task_id]["status"] = "cancelled"
            self._tasks[task_id]["finished_at"] = datetime.datetime.now().isoformat()
            return True

    def get_result(self, task_id: str) -> dict | None:
        """Get result, blocking until complete if still running. Timeout 0.1s poll."""
        if task_id not in self._threads:
            return self.get_status(task_id)
        # Wait for thread to finish (with timeout so we don't block forever)
        self._threads[task_id].join(timeout=300)
        return self.get_status(task_id)

    def _run_task(self, task_id: str, agent_id: str, task: str,
                  model: str | None, cancel_flag: threading.Event):
        """Execute a task in a background thread."""
        target = AgentConfig(agent_id)
        target_memory = MemoryStore(agent_id, base_dir=target.memory_dir)

        if not model:
            model = target.preferred_model or _delegate_fallback_model or "claude-opus-4-5-20251101"

        import platform
        cwd = os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        from datetime import datetime as _dt
        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{agent_id}' running a background task.\n"
            f"Current date and time: {_dt.now().strftime('%Y-%m-%d %H:%M %Z').strip()}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "Complete the task and provide a concise result summary.\n"
        )
        # Inject team context
        team_info = _get_agent_team_info(agent_id)
        if team_info:
            if team_info["is_head"]:
                peers = [m for m in team_info["members"] if m != agent_id]
                system_prompt += (
                    f"\nTEAM: You are the head of team '{team_info['name']}'. "
                    f"Your team members: {', '.join(peers)}\n"
                    "Delegate sub-tasks to your team members when appropriate.\n"
                    "Use memory_shared(scope='team') for team-level shared knowledge.\n"
                )
            else:
                peers = [m for m in team_info["members"] if m != agent_id and m != team_info["head"]]
                system_prompt += (
                    f"\nTEAM: You are a member of team '{team_info['name']}'.\n"
                    f"Team head: {team_info['head']}\n"
                )
                if peers:
                    system_prompt += f"Team peers: {', '.join(peers)}\n"
                system_prompt += "Use memory_shared(scope='team') for team-level shared knowledge.\n"

        if tools_guide:
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        # Build team-aware agent registry for this delegate
        agent_registry = build_agent_registry(for_agent_id=agent_id)
        if agent_registry:
            system_prompt += f"\n\n{agent_registry}\n"

        messages = [{"role": "user", "content": task}]

        result_text = ""
        status = "completed"
        try:
            # Store delegate agent ID in thread-local for delegation scoping
            _thread_local.delegate_agent_id = agent_id
            if cancel_flag.is_set():
                status = "cancelled"
            else:
                delegate_inf = get_inference_params(model, target.config.get("model_purpose"))
                result_text = _run_delegate(messages, model, system_prompt,
                                            memory_store=target_memory,
                                            inference_params=delegate_inf) or ""
                if cancel_flag.is_set():
                    status = "cancelled"
        except Exception as e:
            result_text = str(e)
            status = "error"
        finally:
            # Clean up thread-local state
            _thread_local.delegate_agent_id = None
            _thread_local.memory_store = None

        with self._lock:
            self._tasks[task_id]["status"] = status
            self._tasks[task_id]["result"] = result_text
            self._tasks[task_id]["finished_at"] = datetime.datetime.now().isoformat()
            if status == "error":
                self._tasks[task_id]["error"] = result_text
            # Clean up thread reference
            self._threads.pop(task_id, None)
            self._cancel_flags.pop(task_id, None)


# Global task runner
_task_runner: TaskRunner | None = None


# --- Workflow Engine ---

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # Graceful fallback — suggest pip3 install pyyaml


class WorkflowEngine:
    """Manages workflow definitions stored as YAML files per agent."""

    @staticmethod
    def _workflows_dir(agent_id: str) -> str:
        return os.path.join(AGENTS_DIR, agent_id, "workflows")

    @staticmethod
    def list_workflows(agent_id: str) -> list[dict]:
        """Scan agents/<name>/workflows/*.yaml and return summaries."""
        wdir = WorkflowEngine._workflows_dir(agent_id)
        if not os.path.isdir(wdir):
            return []
        results = []
        for fname in sorted(os.listdir(wdir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            wf = WorkflowEngine.get_workflow(agent_id, fname.rsplit(".", 1)[0])
            if wf:
                results.append({
                    "name": wf.get("name", fname),
                    "file": fname,
                    "description": wf.get("description", ""),
                    "stages": len(wf.get("stages", [])),
                    "variables": [v.get("name", "") for v in wf.get("variables", [])],
                })
        return results

    @staticmethod
    def get_workflow(agent_id: str, name: str) -> dict | None:
        """Parse a workflow YAML file. Returns dict or None."""
        if not _yaml:
            return None
        wdir = WorkflowEngine._workflows_dir(agent_id)
        for ext in (".yaml", ".yml"):
            fpath = os.path.join(wdir, name + ext)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r") as f:
                        return _yaml.safe_load(f)
                except Exception:
                    return None
        return None

    @staticmethod
    def save_workflow(agent_id: str, name: str, definition: dict | str) -> str:
        """Write a workflow YAML file. Returns the file path."""
        if not _yaml:
            raise RuntimeError("PyYAML is not installed. Run: pip3 install pyyaml")
        wdir = WorkflowEngine._workflows_dir(agent_id)
        os.makedirs(wdir, exist_ok=True)
        fpath = os.path.join(wdir, name + ".yaml")
        with open(fpath, "w") as f:
            if isinstance(definition, str):
                f.write(definition)
            else:
                _yaml.dump(definition, f, default_flow_style=False, sort_keys=False)
        return fpath

    @staticmethod
    def delete_workflow(agent_id: str, name: str) -> bool:
        """Remove a workflow file. Returns True if deleted."""
        wdir = WorkflowEngine._workflows_dir(agent_id)
        for ext in (".yaml", ".yml"):
            fpath = os.path.join(wdir, name + ext)
            if os.path.exists(fpath):
                os.remove(fpath)
                return True
        return False


class WorkflowExecution:
    """Runs a workflow: sequential stage execution with approval gates."""

    def __init__(self, workflow: dict, variables: dict, agent_id: str,
                 model: str | None = None, execution_id: str | None = None):
        self.workflow = workflow
        self.variables = variables or {}
        self.agent_id = agent_id
        self.model = model
        self.execution_id = execution_id or _uuid.uuid4().hex[:10]
        self.status = "pending"  # pending / running / waiting_approval / completed / failed / cancelled
        self.current_stage_idx = -1
        self.current_stage_name = ""
        self.stage_results: dict[str, dict] = {}  # stage_name -> {status, output, elapsed}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self._cancel = threading.Event()
        self._approval_event = threading.Event()
        self._approval_result: str | None = None  # "approved" or "rejected"
        self._thread: threading.Thread | None = None

    @property
    def stages(self) -> list[dict]:
        return self.workflow.get("stages", [])

    def _substitute(self, text: str) -> str:
        """Replace {{variable}} and {{stages.X.output}} placeholders."""
        if not text:
            return text
        import re
        # Replace user variables: {{var_name}}
        for k, v in self.variables.items():
            text = text.replace("{{" + k + "}}", str(v))
        # Replace stage references: {{stages.X.output}}, {{stages.X.status}}
        def _stage_ref(m):
            stage_name = m.group(1)
            field = m.group(2)
            sr = self.stage_results.get(stage_name, {})
            return str(sr.get(field, f"[{stage_name}.{field} not available]"))
        text = re.sub(r"\{\{stages\.(\w+)\.(\w+)\}\}", _stage_ref, text)
        return text

    def _build_context(self) -> str:
        """Build accumulated context string from all completed stages."""
        parts = []
        for stage in self.stages:
            sname = stage.get("name", "")
            sr = self.stage_results.get(sname)
            if sr and sr.get("status") == "completed" and sr.get("output"):
                parts.append(f"=== Stage '{sname}' result ===\n{sr['output']}")
        return "\n\n".join(parts)

    def run(self):
        """Start the workflow in a background thread."""
        self.status = "running"
        self.started_at = datetime.datetime.now().isoformat()
        self._thread = threading.Thread(
            target=self._execute, daemon=True,
            name=f"workflow-{self.execution_id}")
        self._thread.start()

    def _execute(self):
        """Sequential stage execution."""
        try:
            for idx, stage in enumerate(self.stages):
                if self._cancel.is_set():
                    self.status = "cancelled"
                    self.finished_at = datetime.datetime.now().isoformat()
                    return

                sname = stage.get("name", f"stage_{idx}")
                stype = stage.get("type", "prompt")
                self.current_stage_idx = idx
                self.current_stage_name = sname

                if stype == "approval":
                    self._run_approval_stage(sname, stage)
                    if self._cancel.is_set() or self._approval_result == "rejected":
                        if self._approval_result == "rejected":
                            self.stage_results[sname] = {
                                "status": "rejected", "output": "Approval rejected by user.",
                                "elapsed": 0,
                            }
                            self.status = "failed"
                            self.error = f"Approval rejected at stage '{sname}'"
                        else:
                            self.status = "cancelled"
                        self.finished_at = datetime.datetime.now().isoformat()
                        return
                else:
                    self._run_prompt_stage(sname, stage)

                # Check if stage failed
                sr = self.stage_results.get(sname, {})
                if sr.get("status") == "error":
                    self.status = "failed"
                    self.error = f"Stage '{sname}' failed: {sr.get('output', 'unknown error')}"
                    self.finished_at = datetime.datetime.now().isoformat()
                    return

            self.status = "completed"
            self.finished_at = datetime.datetime.now().isoformat()

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            self.finished_at = datetime.datetime.now().isoformat()

    def _run_prompt_stage(self, sname: str, stage: dict):
        """Execute a prompt stage using _run_delegate."""
        start = datetime.datetime.now()
        prompt_template = stage.get("prompt", "")
        prompt = self._substitute(prompt_template)

        # Build context from previous stages
        context = self._build_context()
        full_prompt = prompt
        if context:
            full_prompt = f"Previous workflow results:\n{context}\n\n---\n\nCurrent task:\n{prompt}"

        # Resolve agent and model
        target_agent_id = stage.get("agent", self.agent_id)
        target = AgentConfig(target_agent_id)
        target_memory = MemoryStore(target_agent_id, base_dir=target.memory_dir)

        stage_model = self.model or target.preferred_model or _delegate_fallback_model or "claude-sonnet-4-6"

        import platform
        cwd = os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{target_agent_id}' executing workflow stage '{sname}'.\n"
            f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "Complete the task and provide a concise result summary.\n"
        )
        if tools_guide:
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        # Tool restriction (set via thread-local if stage specifies allowed tools)
        restricted_tools = stage.get("tools")

        self.stage_results[sname] = {"status": "running", "output": "", "elapsed": 0}

        messages = [{"role": "user", "content": full_prompt}]

        try:
            _thread_local.delegate_agent_id = target_agent_id
            if restricted_tools:
                _thread_local.workflow_allowed_tools = set(restricted_tools)

            cancel_token = CancelToken()
            # Link our cancel event to the cancel token
            def _watch_cancel():
                self._cancel.wait()
                cancel_token.cancel()
            watcher = threading.Thread(target=_watch_cancel, daemon=True)
            watcher.start()

            delegate_inf = get_inference_params(stage_model, target.config.get("model_purpose"))
            result_text = _run_delegate(
                messages, stage_model, system_prompt,
                memory_store=target_memory,
                cancel_token=cancel_token,
                inference_params=delegate_inf,
            ) or ""

            elapsed = (datetime.datetime.now() - start).total_seconds()
            if self._cancel.is_set():
                self.stage_results[sname] = {"status": "cancelled", "output": result_text, "elapsed": elapsed}
            else:
                self.stage_results[sname] = {"status": "completed", "output": result_text, "elapsed": elapsed}

        except Exception as e:
            elapsed = (datetime.datetime.now() - start).total_seconds()
            self.stage_results[sname] = {"status": "error", "output": str(e), "elapsed": elapsed}
        finally:
            _thread_local.delegate_agent_id = None
            _thread_local.memory_store = None
            _thread_local.workflow_allowed_tools = None

    def _run_approval_stage(self, sname: str, stage: dict):
        """Pause for human approval."""
        message = self._substitute(stage.get("message", "Approval required."))
        self.stage_results[sname] = {
            "status": "waiting_approval", "output": message, "elapsed": 0,
        }
        self.status = "waiting_approval"
        self._approval_event.clear()
        self._approval_result = None

        # Wait until approved, rejected, or cancelled
        while not self._approval_event.is_set() and not self._cancel.is_set():
            self._approval_event.wait(timeout=1.0)

        if self._cancel.is_set():
            self.stage_results[sname] = {"status": "cancelled", "output": message, "elapsed": 0}
            return

        if self._approval_result == "approved":
            self.stage_results[sname] = {"status": "completed", "output": "Approved.", "elapsed": 0}
            self.status = "running"
        # "rejected" handled by caller

    def approve(self):
        """Approve the current approval gate."""
        self._approval_result = "approved"
        self._approval_event.set()

    def reject(self):
        """Reject the current approval gate."""
        self._approval_result = "rejected"
        self._approval_event.set()

    def cancel(self):
        """Cancel the workflow execution."""
        self._cancel.set()
        self._approval_event.set()  # Unblock approval wait

    def to_dict(self) -> dict:
        """Serialize execution state."""
        stages_info = []
        for idx, stage in enumerate(self.stages):
            sname = stage.get("name", f"stage_{idx}")
            sr = self.stage_results.get(sname, {})
            stages_info.append({
                "name": sname,
                "type": stage.get("type", "prompt"),
                "status": sr.get("status", "pending"),
                "output": sr.get("output", ""),
                "elapsed": sr.get("elapsed", 0),
            })
        return {
            "execution_id": self.execution_id,
            "workflow_name": self.workflow.get("name", ""),
            "agent": self.agent_id,
            "model": self.model,
            "status": self.status,
            "current_stage": self.current_stage_name,
            "current_stage_idx": self.current_stage_idx,
            "total_stages": len(self.stages),
            "stages": stages_info,
            "variables": self.variables,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


# Global workflow execution registry
_workflow_executions: dict[str, WorkflowExecution] = {}
_workflow_lock = threading.Lock()


def workflow_start(agent_id: str, workflow_name: str, variables: dict,
                   model: str | None = None) -> WorkflowExecution:
    """Start a workflow execution. Returns the execution object."""
    if not _yaml:
        raise RuntimeError("PyYAML is not installed. Run: pip3 install pyyaml")
    wf = WorkflowEngine.get_workflow(agent_id, workflow_name)
    if not wf:
        raise ValueError(f"Workflow '{workflow_name}' not found for agent '{agent_id}'")
    execution = WorkflowExecution(wf, variables, agent_id, model)
    with _workflow_lock:
        _workflow_executions[execution.execution_id] = execution
    execution.run()
    return execution


def workflow_get_execution(execution_id: str) -> WorkflowExecution | None:
    with _workflow_lock:
        return _workflow_executions.get(execution_id)


def workflow_list_executions() -> list[dict]:
    with _workflow_lock:
        return [ex.to_dict() for ex in _workflow_executions.values()]


def workflow_cleanup_old(max_age_hours: int = 24):
    """Remove completed/failed executions older than max_age_hours."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)
    with _workflow_lock:
        to_remove = []
        for eid, ex in _workflow_executions.items():
            if ex.status in ("completed", "failed", "cancelled") and ex.finished_at:
                try:
                    finished = datetime.datetime.fromisoformat(ex.finished_at)
                    if finished < cutoff:
                        to_remove.append(eid)
                except (ValueError, TypeError):
                    pass
        for eid in to_remove:
            del _workflow_executions[eid]


def tool_delegate_task(args: dict) -> str:
    """Delegate a task to another agent — runs in a background thread."""
    agent_id = args.get("agent", "")
    task = args.get("task", "")
    wait = args.get("wait", True)
    if not agent_id or not task:
        return _err("delegate_task: agent and task are required")

    available = list_agents()
    if agent_id not in available:
        return _err(f"delegate_task: agent '{agent_id}' not found. Available: {', '.join(available)}")

    # Team-aware delegation scoping (prefer thread-local for concurrent requests)
    caller_id = getattr(_thread_local, "delegate_agent_id", None)
    if not caller_id:
        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
        caller_id = agent.agent_id if agent else None
    if caller_id:
        scope = _get_delegation_scope(caller_id)
        if agent_id not in scope:
            return _err(f"delegate_task: '{caller_id}' cannot delegate to '{agent_id}'. Allowed: {', '.join(scope)}")

    if not _task_runner:
        return _err("Task runner not initialized")

    task_id = _task_runner.submit(agent_id, task, args.get("model"))

    if wait:
        # Synchronous: wait for result
        result = _task_runner.get_result(task_id)
        if result and result.get("status") == "completed":
            return _ok({
                "task_id": task_id,
                "agent": agent_id,
                "task": task,
                "response": result.get("result", ""),
            })
        elif result:
            return _err(f"delegate_task: {result.get('status')} — {result.get('error', '')}")
        return _err("delegate_task: no result")
    else:
        # Async: return task_id immediately
        return _ok({
            "task_id": task_id,
            "agent": agent_id,
            "task": task,
            "status": "running",
            "message": f"Task submitted. Use task_status(task_id='{task_id}') to check progress.",
        })


def tool_task_status(args: dict) -> str:
    """Check status of a background task."""
    if not _task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if task_id:
        status = _task_runner.get_status(task_id)
        if not status:
            return _err(f"Task '{task_id}' not found")
        # Truncate long results
        if status.get("result") and len(status["result"]) > 2000:
            status["result"] = status["result"][:2000] + "..."
        return _ok(status)
    else:
        # List all tasks
        tasks = _task_runner.list_tasks()
        for t in tasks:
            if t.get("result") and len(t["result"]) > 200:
                t["result"] = t["result"][:200] + "..."
        return _ok({"tasks": tasks, "count": len(tasks)})


def tool_task_cancel(args: dict) -> str:
    """Cancel a running background task."""
    if not _task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if not task_id:
        return _err("task_cancel: task_id is required")
    if _task_runner.cancel(task_id):
        return _ok({"task_id": task_id, "status": "cancelled"})
    return _err(f"Cannot cancel task '{task_id}' — not found or not running")


# --- Scheduler ---

import sqlite3

SCHEDULER_DB = os.path.join(AGENTS_DIR, "main", "scheduler.db")

_sched_db_lock = threading.Lock()
_sched_db_pool: dict[int, sqlite3.Connection] = {}


def _sched_conn():
    """Get a thread-safe reusable SQLite connection for the scheduler DB."""
    tid = threading.current_thread().ident
    with _sched_db_lock:
        conn = _sched_db_pool.get(tid)
        if conn is None:
            conn = sqlite3.connect(SCHEDULER_DB, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            _sched_db_pool[tid] = conn
    return conn


class Scheduler:
    """Background task scheduler with cron-like scheduling."""

    def __init__(self):
        os.makedirs(os.path.dirname(SCHEDULER_DB), exist_ok=True)
        self._init_db()
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._running_tasks: dict[str, dict] = {}

    def _init_db(self):
        with _sched_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    task TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    agent TEXT DEFAULT 'main',
                    model TEXT,
                    enabled INTEGER DEFAULT 1,
                    last_run TEXT,
                    next_run TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedule_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    schedule_name TEXT,
                    agent TEXT,
                    task TEXT,
                    status TEXT,
                    result TEXT,
                    started_at TEXT,
                    finished_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (schedule_id) REFERENCES schedules(id)
                )
            """)
            # Migration: add timeout column if missing
            try:
                conn.execute("ALTER TABLE schedules ADD COLUMN timeout INTEGER DEFAULT 300")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def add(self, name: str, task: str, schedule: str,
            agent: str = "main", model: str | None = None,
            timeout: int = 300) -> dict:
        """Add a scheduled task. timeout in seconds (default: 300 = 5 min)."""
        next_run = self._calc_next_run(schedule)
        if next_run is None:
            return {"error": f"Invalid schedule format: {schedule}"}
        try:
            with _sched_conn() as conn:
                conn.execute("""
                    INSERT INTO schedules (name, task, schedule, agent, model, next_run, timeout)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (name, task, schedule, agent, model, next_run.isoformat(), timeout))
                conn.commit()
            return {"name": name, "schedule": schedule, "agent": agent,
                    "next_run": next_run.isoformat(), "timeout": timeout, "status": "created"}
        except sqlite3.IntegrityError:
            return {"error": f"Schedule '{name}' already exists"}

    def remove(self, name: str) -> dict:
        with _sched_conn() as conn:
            r = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
            conn.commit()
            if r.rowcount == 0:
                return {"error": f"Schedule '{name}' not found"}
        return {"name": name, "status": "deleted"}

    def pause(self, name: str) -> dict:
        with _sched_conn() as conn:
            r = conn.execute("UPDATE schedules SET enabled = 0 WHERE name = ?", (name,))
            conn.commit()
            if r.rowcount == 0:
                return {"error": f"Schedule '{name}' not found"}
        return {"name": name, "status": "paused"}

    def resume(self, name: str) -> dict:
        next_run = None
        with _sched_conn() as conn:
            row = conn.execute("SELECT schedule FROM schedules WHERE name = ?", (name,)).fetchone()
            if not row:
                return {"error": f"Schedule '{name}' not found"}
            next_run = self._calc_next_run(row[0])
            conn.execute("UPDATE schedules SET enabled = 1, next_run = ? WHERE name = ?",
                         (next_run.isoformat() if next_run else None, name))
            conn.commit()
        return {"name": name, "status": "resumed", "next_run": next_run.isoformat() if next_run else None}

    def list_all(self) -> list[dict]:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    def get_history(self, name: str | None = None, limit: int = 20) -> list[dict]:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            if name:
                rows = conn.execute(
                    "SELECT * FROM schedule_history WHERE schedule_name = ? ORDER BY finished_at DESC LIMIT ?",
                    (name, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedule_history ORDER BY finished_at DESC LIMIT ?",
                    (limit,)).fetchall()
            return [dict(r) for r in rows]

    def _calc_next_run(self, schedule: str) -> datetime.datetime | None:
        """Calculate next run time from schedule string."""
        now = datetime.datetime.now()
        s = schedule.strip().lower()

        # every Xm, every Xh, every Xd
        m = re.match(r'every\s+(\d+)\s*(m|min|h|hour|d|day)s?', s)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if unit == 'm':
                return now + datetime.timedelta(minutes=val)
            elif unit == 'h':
                return now + datetime.timedelta(hours=val)
            elif unit == 'd':
                return now + datetime.timedelta(days=val)

        # daily HH:MM
        m = re.match(r'daily\s+(\d{1,2}):(\d{2})', s)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return target

        # weekly DOW HH:MM
        days_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        m = re.match(r'weekly\s+(\w{3})\s+(\d{1,2}):(\d{2})', s)
        if m:
            dow_str, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
            target_dow = days_map.get(dow_str[:3])
            if target_dow is None:
                return None
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days_ahead = (target_dow - now.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            target += datetime.timedelta(days=days_ahead)
            return target

        # once YYYY-MM-DD HH:MM
        m = re.match(r'once\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})', s)
        if m:
            date_str, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
            try:
                target = datetime.datetime.fromisoformat(f"{date_str}T{hour:02d}:{minute:02d}:00")
                return target
            except ValueError:
                return None

        return None

    def _calc_next_from_last(self, schedule: str, last_run: str) -> datetime.datetime | None:
        """Calculate next run based on last run time (for intervals)."""
        s = schedule.strip().lower()
        m = re.match(r'every\s+(\d+)\s*(m|min|h|hour|d|day)s?', s)
        if m:
            try:
                last = datetime.datetime.fromisoformat(last_run)
            except (ValueError, TypeError):
                return self._calc_next_run(schedule)
            val, unit = int(m.group(1)), m.group(2)[0]
            if unit == 'm':
                return last + datetime.timedelta(minutes=val)
            elif unit == 'h':
                return last + datetime.timedelta(hours=val)
            elif unit == 'd':
                return last + datetime.timedelta(days=val)
        return self._calc_next_run(schedule)

    def get_due_tasks(self) -> list[dict]:
        """Get tasks that are due for execution."""
        now = datetime.datetime.now().isoformat()
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ?
            """, (now,)).fetchall()
            return [dict(r) for r in rows]

    def mark_executed(self, schedule_id: int, name: str, agent: str, task: str,
                      status: str, result: str, started_at: str = None,
                      tool_calls: int = 0):
        """Record execution and update next_run."""
        now = datetime.datetime.now()
        start = started_at or now.isoformat()
        try:
            start_dt = datetime.datetime.fromisoformat(start)
            duration = (now - start_dt).total_seconds()
        except (ValueError, TypeError):
            duration = 0
        with _sched_conn() as conn:
            # Record history with duration and tool_calls
            conn.execute("""
                INSERT INTO schedule_history (schedule_id, schedule_name, agent, task, status, result, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (schedule_id, name, agent, task, status,
                  f"[Duration: {duration:.0f}s | Tools: {tool_calls}]\n\n{result[:10000]}",
                  start, now.isoformat()))

            # Get schedule to calc next run
            row = conn.execute("SELECT schedule FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            if row:
                schedule_str = row[0]
                if schedule_str.strip().lower().startswith("once"):
                    # One-shot: disable after execution
                    conn.execute("UPDATE schedules SET enabled = 0, last_run = ? WHERE id = ?",
                                 (now.isoformat(), schedule_id))
                else:
                    next_run = self._calc_next_from_last(schedule_str, now.isoformat())
                    conn.execute("UPDATE schedules SET last_run = ?, next_run = ? WHERE id = ?",
                                 (now.isoformat(),
                                  next_run.isoformat() if next_run else None,
                                  schedule_id))
            conn.commit()

    def start(self):
        """Start the background scheduler thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        """Background loop that checks for due tasks every 30s.
        Each task runs in its own thread to avoid blocking other due tasks."""
        while not self._stop.is_set():
            try:
                due = self.get_due_tasks()
                for task_row in due:
                    if self._stop.is_set():
                        break
                    t = threading.Thread(
                        target=self._execute_scheduled, args=(task_row,),
                        daemon=True, name=f"sched_{task_row.get('name', '?')}")
                    t.start()
            except Exception:
                pass
            self._stop.wait(30)

    def get_running_tasks(self) -> list[dict]:
        """Get currently running scheduled tasks with live stats."""
        with self._lock:
            return [dict(t) for t in self._running_tasks.values()]

    def cancel_running_task(self, name: str) -> bool:
        """Cancel a running scheduled task."""
        with self._lock:
            task = self._running_tasks.get(name)
            if task and task.get("cancel_token"):
                task["cancel_token"].cancel()
                task["status"] = "cancelling"
                return True
        return False

    def _execute_scheduled(self, task_row: dict):
        """Execute a single scheduled task."""
        agent_id = task_row.get("agent", "main")
        task = task_row.get("task", "")
        model = task_row.get("model")
        schedule_id = task_row.get("id")
        name = task_row.get("name", "")

        # Memory summary tasks: regenerate prompt fresh with live data
        if name.startswith("_memory_summary_"):
            try:
                task = _build_memory_summary_prompt(agent_id)
            except Exception:
                pass

        # Track this execution
        cancel_token = CancelToken()
        run_info = {
            "name": name,
            "agent": agent_id,
            "task": task[:200],
            "model": model,
            "status": "running",
            "started_at": datetime.datetime.now().isoformat(),
            "tool_calls": 0,
            "tool_log": [],
            "cancel_token": cancel_token,
        }
        with self._lock:
            if not hasattr(self, '_running_tasks'):
                self._running_tasks = {}
            self._running_tasks[name] = run_info

        # Use delegation infrastructure
        target = AgentConfig(agent_id)
        target_memory = MemoryStore(agent_id, base_dir=target.memory_dir)

        if not model:
            model = target.preferred_model or _delegate_fallback_model or "claude-opus-4-5-20251101"

        # Build system prompt
        import platform
        cwd = os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{agent_id}' executing a scheduled task: '{name}'.\n"
            f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "IMPORTANT RULES FOR SCHEDULED TASKS:\n"
            "- Complete the task QUICKLY and CONCISELY. You have a limited number of tool calls.\n"
            "- Do NOT repeat the same tool call. If a search returns results, use them — don't search again.\n"
            "- Do NOT loop. One search per topic is enough. Summarize what you found.\n"
            "- Provide a concise result summary within 3-5 tool calls maximum.\n"
            "- If you can't find what you need in 2 searches, summarize what you have and stop.\n\n"
        )
        # Inject memory summary for context (skip for the summary task itself)
        if not name.startswith("_memory_summary_"):
            try:
                mem_summary = get_memory_summary(agent_id)
                if mem_summary:
                    system_prompt += (
                        "MEMORY SUMMARY (auto-generated synthesis of recent activity — use as context):\n"
                        f"{mem_summary}\n\n"
                    )
            except Exception:
                pass
        if tools_guide:
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        messages = [{"role": "user", "content": task}]

        # Get timeout (default 5 min)
        task_timeout = task_row.get("timeout") or 300

        # Run with isolated memory and live tracking
        result_text = ""
        status = "success"

        def on_event(event_type, data):
            if event_type == "tool_call":
                run_info["tool_calls"] += 1
                entry = f"{data.get('name','')}({str(data.get('args',{}))[:80]})"
                run_info["tool_log"].append(entry)
                if len(run_info["tool_log"]) > 50:
                    run_info["tool_log"] = run_info["tool_log"][-50:]

        # Timeout watchdog — cancels the task after timeout seconds
        def watchdog():
            if not cancel_token._cancelled.wait(task_timeout):
                cancel_token.cancel()
                run_info["status"] = "timeout"

        timer = threading.Thread(target=watchdog, daemon=True)
        timer.start()

        try:
            sched_inf = get_inference_params(model, target.config.get("model_purpose"))
            result_text = _run_delegate(messages, model, system_prompt,
                                        memory_store=target_memory,
                                        cancel_token=cancel_token,
                                        event_callback=on_event,
                                        inference_params=sched_inf) or ""
            # Check if _run_delegate returned an error string instead of raising
            if result_text.startswith("Delegation error:"):
                status = "error"
                result_text = f"[DELEGATION ERROR] {result_text}"
        except TaskCancelled:
            if run_info.get("status") == "timeout":
                elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(run_info["started_at"])).total_seconds()
                result_text = (result_text or "") + f"\n\n[TIMEOUT] Task timed out after {elapsed:.0f}s (limit: {task_timeout}s). Tool calls made: {run_info['tool_calls']}."
                status = "timeout"
            else:
                result_text = (result_text or "") + f"\n\n[CANCELLED] Task was cancelled. Tool calls made: {run_info['tool_calls']}."
                status = "cancelled"
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")[:500]
            except Exception:
                pass
            result_text = f"[HTTP ERROR] {e.code} {e.reason}\n{error_body}"
            status = "error"
        except urllib.error.URLError as e:
            result_text = f"[CONNECTION ERROR] Could not reach LLM API: {e.reason}"
            status = "error"
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            result_text = f"[ERROR] {type(e).__name__}: {e}\n\n{tb[-500:]}"
            status = "error"
        finally:
            cancel_token.cancel()  # stop the watchdog if still running
            with self._lock:
                self._running_tasks.pop(name, None)

        self.mark_executed(schedule_id, name, agent_id, task, status, result_text,
                           started_at=run_info.get("started_at"),
                           tool_calls=run_info.get("tool_calls", 0))

        # Chain relationship discovery after memory summary completes
        if name.startswith("_memory_summary_") and status == "success":
            rd_cfg = _get_relationship_discovery_config(agent_id)
            if rd_cfg.get("enabled"):
                try:
                    trigger_relationship_discovery(agent_id)
                except Exception:
                    pass

        # Fire notification hook for task completion/failure
        if _notification_hook:
            try:
                if status in ("error", "timeout"):
                    evt = "task_timeout" if status == "timeout" else "task_failed"
                    sev = "warning" if status == "timeout" else "error"
                    _notification_hook(evt, f"Scheduled task: {name}",
                                       result_text[:300], severity=sev,
                                       agent=agent_id,
                                       metadata={"task_name": name, "status": status,
                                                  "tool_calls": run_info.get("tool_calls", 0)})
                elif status == "success" and not name.startswith("_memory_summary_"):
                    _notification_hook("task_complete", f"Task completed: {name}",
                                       f"Agent {agent_id} completed '{name}' with {run_info.get('tool_calls', 0)} tool calls.",
                                       severity="info", agent=agent_id,
                                       metadata={"task_name": name})
            except Exception:
                pass


# Global scheduler instance
_scheduler: Scheduler | None = None

# Notification hook — set by server.py to dispatch notifications
_notification_hook = None


# --- Cost Tracking ---

COST_DB = os.path.join(AGENTS_DIR, "main", "costs.db")

_cost_db_lock = threading.Lock()
_cost_db_pool: dict[int, sqlite3.Connection] = {}


def _cost_conn():
    """Get a thread-safe reusable SQLite connection for the cost DB."""
    tid = threading.current_thread().ident
    with _cost_db_lock:
        conn = _cost_db_pool.get(tid)
        if conn is None:
            conn = sqlite3.connect(COST_DB, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            _cost_db_pool[tid] = conn
    return conn


# Default cost rates per 1M tokens — 0 for free providers
_cost_rates: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-opus-4-5-20251101": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5-20241022": {"input": 3.0, "output": 15.0},
    "claude-haiku-3.5": {"input": 0.80, "output": 4.0},
}


def _get_cost_rate(model: str) -> dict[str, float]:
    """Look up cost rate for a model. Checks _models_config first, then defaults."""
    cfg = _models_config.get(model, {})
    ci = cfg.get("cost_input")
    co = cfg.get("cost_output")
    if ci is not None and co is not None:
        return {"input": float(ci), "output": float(co)}
    # Check built-in rates
    if model in _cost_rates:
        return _cost_rates[model]
    # Try prefix matching (e.g. "claude-opus-4-6" matches "claude-opus-4-6-20260101")
    ml = model.lower()
    for pattern, rate in _cost_rates.items():
        if ml.startswith(pattern.lower()) or pattern.lower() in ml:
            return rate
    return {"input": 0.0, "output": 0.0}


def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Compute estimated cost in USD."""
    rate = _get_cost_rate(model)
    return (tokens_in * rate["input"] + tokens_out * rate["output"]) / 1_000_000


class CostTracker:
    """Thread-safe cost tracking with SQLite persistence."""

    def __init__(self):
        os.makedirs(os.path.dirname(COST_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _cost_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    tool_round INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_log(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_created ON cost_log(created_at)")
            conn.commit()

    def log_call(self, agent: str, session_id: str, model: str, provider: str,
                 tokens_in: int, tokens_out: int, tool_round: int = 0):
        """Log an LLM call with cost estimation."""
        cost = _compute_cost(model, tokens_in, tokens_out)
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, model, provider, tokens_in, tokens_out, cost_usd, tool_round)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", model, provider, tokens_in, tokens_out, cost, tool_round))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost tracking error: {e}")

    def get_stats(self, agent: str | None = None, hours: int = 24) -> dict:
        """Get aggregate stats for the last N hours."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE created_at >= datetime('now', ?)"
                params: list = [f"-{hours} hours"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                row = conn.execute(f"""
                    SELECT COUNT(*) as total_calls,
                           COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                           COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as total_cost
                    FROM cost_log {where}
                """, params).fetchone()
                # Per-agent breakdown
                agents_rows = conn.execute(f"""
                    SELECT agent,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY agent ORDER BY cost DESC
                """, params).fetchall()
                # Per-model breakdown
                models_rows = conn.execute(f"""
                    SELECT model,
                           COUNT(*) as calls,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY model ORDER BY cost DESC
                """, params).fetchall()
                return {
                    "total_calls": row["total_calls"],
                    "total_tokens_in": row["total_tokens_in"],
                    "total_tokens_out": row["total_tokens_out"],
                    "total_cost": round(row["total_cost"], 4),
                    "hours": hours,
                    "agent_filter": agent,
                    "by_agent": [dict(r) for r in agents_rows],
                    "by_model": [dict(r) for r in models_rows],
                }
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost stats error: {e}")
            return {"total_calls": 0, "total_tokens_in": 0, "total_tokens_out": 0,
                    "total_cost": 0.0, "hours": hours, "agent_filter": agent,
                    "by_agent": [], "by_model": []}

    def get_daily(self, agent: str | None = None, days: int = 7) -> list[dict]:
        """Get daily breakdown for the last N days."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE created_at >= datetime('now', ?)"
                params: list = [f"-{days} days"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                rows = conn.execute(f"""
                    SELECT date(created_at) as day,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY date(created_at) ORDER BY day DESC
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost daily error: {e}")
            return []

    def get_session_cost(self, session_id: str) -> dict:
        """Get cost for a specific session."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("""
                    SELECT COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log WHERE session_id = ?
                """, (session_id,)).fetchone()
                return dict(row) if row else {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        except (sqlite3.Error, OSError):
            return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}


_cost_tracker: CostTracker | None = None


# --- Observability: Trace Manager ---

TRACES_DB = os.path.join(AGENTS_DIR, "main", "traces.db")

_traces_db_lock = threading.Lock()
_traces_db_pool: dict[int, sqlite3.Connection] = {}


def _traces_conn():
    """Get a thread-safe reusable SQLite connection for the traces DB."""
    tid = threading.current_thread().ident
    with _traces_db_lock:
        conn = _traces_db_pool.get(tid)
        if conn is None:
            conn = sqlite3.connect(TRACES_DB, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            _traces_db_pool[tid] = conn
    return conn


class TraceManager:
    """Thread-safe span-based tracing with SQLite persistence."""

    def __init__(self):
        os.makedirs(os.path.dirname(TRACES_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _traces_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_id TEXT,
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_ms INTEGER,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    model TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_trace ON traces(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_parent ON traces(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_type ON traces(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id)")
            conn.commit()

    def start_span(self, span_type: str, name: str, agent: str = "main",
                   model: str = "", parent_id: str | None = None,
                   trace_id: str | None = None,
                   session_id: str | None = None) -> dict:
        """Start a new span. Returns span dict with id, start time, etc."""
        import uuid as _uuid
        span_id = _uuid.uuid4().hex[:16]
        if not trace_id:
            trace_id = _uuid.uuid4().hex[:16]
        now = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        return {
            "id": span_id,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "agent": agent,
            "session_id": session_id,
            "type": span_type,
            "name": name,
            "model": model,
            "status": "ok",
            "started_at": now,
            "_start_time": time.time(),
            "tokens_in": 0,
            "tokens_out": 0,
            "metadata": {},
        }

    def end_span(self, span: dict, status: str = "ok",
                 result_summary: str = "", tokens_in: int = 0, tokens_out: int = 0):
        """End a span: compute duration and persist to DB."""
        now = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        duration_ms = int((time.time() - span.get("_start_time", time.time())) * 1000)
        span["ended_at"] = now
        span["duration_ms"] = duration_ms
        span["status"] = status
        if tokens_in:
            span["tokens_in"] = tokens_in
        if tokens_out:
            span["tokens_out"] = tokens_out
        if result_summary:
            span["metadata"]["result_summary"] = result_summary[:500]
        try:
            with _traces_conn() as conn:
                conn.execute("""
                    INSERT INTO traces (id, trace_id, parent_id, agent, session_id,
                        type, name, status, started_at, ended_at, duration_ms,
                        tokens_in, tokens_out, model, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    span["id"], span["trace_id"], span.get("parent_id"),
                    span["agent"], span.get("session_id"),
                    span["type"], span["name"], span["status"],
                    span["started_at"], span["ended_at"], duration_ms,
                    span.get("tokens_in", 0), span.get("tokens_out", 0),
                    span.get("model", ""),
                    json.dumps(span.get("metadata", {})),
                ))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace write error: {e}")

    def get_traces(self, agent: str | None = None, hours: int = 24,
                   limit: int = 50) -> list[dict]:
        """Get recent root-level traces (parent_id IS NULL)."""
        try:
            with _traces_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE started_at >= datetime('now', ?) AND parent_id IS NULL"
                params: list = [f"-{hours} hours"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                params.append(limit)
                rows = conn.execute(f"""
                    SELECT t.*, (SELECT COUNT(*) FROM traces c WHERE c.trace_id = t.trace_id) as span_count
                    FROM traces t {where}
                    ORDER BY started_at DESC LIMIT ?
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace read error: {e}")
            return []

    def get_trace(self, trace_id: str) -> list[dict]:
        """Get all spans for a trace, ordered by start time."""
        try:
            with _traces_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM traces WHERE trace_id = ? ORDER BY started_at",
                    (trace_id,)
                ).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace read error: {e}")
            return []

    def cleanup(self, retention_days: int = 30):
        """Delete traces older than retention period."""
        try:
            with _traces_conn() as conn:
                conn.execute(
                    "DELETE FROM traces WHERE started_at < datetime('now', ?)",
                    (f"-{retention_days} days",)
                )
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Trace cleanup error: {e}")


_trace_manager: TraceManager | None = None


# --- Audit Trail ---

AUDIT_DB = os.path.join(AGENTS_DIR, "main", "audit.db")

_audit_db_lock = threading.Lock()
_audit_db_pool: dict[int, sqlite3.Connection] = {}


def _audit_conn():
    """Get a thread-safe reusable SQLite connection for the audit DB."""
    tid = threading.current_thread().ident
    with _audit_db_lock:
        conn = _audit_db_pool.get(tid)
        if conn is None:
            conn = sqlite3.connect(AUDIT_DB, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            _audit_db_pool[tid] = conn
    return conn


_AUDIT_ACTION_MAP = {
    "read_file": "file_read",
    "write_file": "file_write",
    "edit_file": "file_write",
    "execute_command": "command_execute",
    "gmail_send": "email_send",
    "gmail_reply": "email_reply",
    "gmail_inbox": "email_read",
    "gmail_read": "email_read",
    "gmail_search": "email_read",
    "web_fetch": "web_fetch",
    "exa_search": "web_search",
    "memory_store": "memory_store",
    "memory_delete": "memory_delete",
    "memory_recall": "memory_recall",
    "memory_shared": "memory_shared",
    "delegate_task": "delegation",
    "task_cancel": "task_cancel",
    "use_skill": "skill_use",
    "list_directory": "file_read",
    "search_files": "file_read",
    "mcp_connect": "mcp_tool_call",
    "mcp_disconnect": "mcp_tool_call",
    "mcp_servers": "mcp_tool_call",
}


def _audit_summarize_args(tool_name: str, args: dict) -> str:
    """Generate human-readable summary of tool arguments, max 200 chars."""
    if tool_name == "execute_command":
        return args.get("command", "")[:200]
    elif tool_name in ("gmail_send", "gmail_reply"):
        return f"To: {args.get('to', '?')}, Subject: {args.get('subject', '?')}"[:200]
    elif tool_name in ("write_file", "edit_file"):
        content = args.get("content", "")
        return f"{args.get('path', '?')} ({len(content)} bytes)"[:200]
    elif tool_name == "read_file":
        return f"{args.get('path', '?')}"[:200]
    elif tool_name == "delegate_task":
        return f"-> {args.get('agent', '?')}: {args.get('task', '')[:100]}"[:200]
    elif tool_name in ("memory_store", "memory_delete"):
        return f"{args.get('name', '?')}"[:200]
    elif tool_name in ("memory_recall", "memory_shared"):
        return f"query={args.get('query', '?')}"[:200]
    elif tool_name == "exa_search":
        return f"query={args.get('query', '')}"[:200]
    elif tool_name == "web_fetch":
        return f"{args.get('url', '?')}"[:200]
    elif tool_name == "use_skill":
        return f"skill={args.get('skill', '?')}"[:200]
    elif tool_name == "list_directory":
        return f"{args.get('path', '.')}"[:200]
    elif tool_name == "search_files":
        return f"pattern={args.get('pattern', '?')} in {args.get('path', '.')}"[:200]
    else:
        return str(args)[:200]


def _audit_summarize_result(tool_name: str, result_str: str) -> str:
    """Generate human-readable result summary, max 200 chars."""
    try:
        rdata = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return str(result_str)[:200]
    if isinstance(rdata, str):
        return rdata[:200]
    if rdata.get("error"):
        return f"ERROR: {rdata['error']}"[:200]
    if tool_name == "execute_command":
        ec = rdata.get("exit_code", -1)
        return f"exit_code={ec}"[:200]
    if tool_name == "exa_search":
        return f"{rdata.get('result_count', 0)} results"[:200]
    if tool_name in ("memory_recall", "memory_shared"):
        return f"{rdata.get('count', 0)} memories"[:200]
    if tool_name == "delegate_task":
        return f"{rdata.get('agent', '')} responded"[:200]
    return str(rdata)[:200]


class AuditLog:
    """Append-only audit log with SQLite persistence. No UPDATE or DELETE."""

    def __init__(self):
        os.makedirs(os.path.dirname(AUDIT_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _audit_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    action_type TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_summary TEXT,
                    result_summary TEXT,
                    result_status TEXT NOT NULL DEFAULT 'success',
                    duration_ms INTEGER,
                    source TEXT NOT NULL DEFAULT 'chat'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent_time ON audit_log(agent, timestamp)")
            conn.commit()

    def log_action(self, agent: str, action_type: str, tool_name: str,
                   args_summary: str = "", result_summary: str = "",
                   result_status: str = "success", duration_ms: int | None = None,
                   session_id: str | None = None, source: str = "chat"):
        """Insert an audit log entry. Append-only."""
        try:
            with _audit_conn() as conn:
                conn.execute("""
                    INSERT INTO audit_log (agent, session_id, action_type, tool_name,
                        args_summary, result_summary, result_status, duration_ms, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", action_type, tool_name,
                      args_summary[:200], result_summary[:200], result_status,
                      duration_ms, source))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit log error: {e}")

    def query(self, agent: str | None = None, action_type: str | None = None,
              from_ts: str | None = None, limit: int = 50) -> list[dict]:
        """Query audit log entries."""
        try:
            with _audit_conn() as conn:
                conn.row_factory = sqlite3.Row
                where_parts = ["1=1"]
                params: list = []
                if agent:
                    where_parts.append("agent = ?")
                    params.append(agent)
                if action_type:
                    where_parts.append("action_type = ?")
                    params.append(action_type)
                if from_ts:
                    where_parts.append("timestamp >= ?")
                    params.append(from_ts)
                where = " AND ".join(where_parts)
                params.append(limit)
                rows = conn.execute(f"""
                    SELECT * FROM audit_log WHERE {where}
                    ORDER BY timestamp DESC LIMIT ?
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit query error: {e}")
            return []

    def export_csv(self, agent: str | None = None,
                   from_ts: str | None = None,
                   to_ts: str | None = None) -> str:
        """Export audit log as CSV string."""
        try:
            with _audit_conn() as conn:
                conn.row_factory = sqlite3.Row
                where_parts = ["1=1"]
                params: list = []
                if agent:
                    where_parts.append("agent = ?")
                    params.append(agent)
                if from_ts:
                    where_parts.append("timestamp >= ?")
                    params.append(from_ts)
                if to_ts:
                    where_parts.append("timestamp <= ?")
                    params.append(to_ts)
                where = " AND ".join(where_parts)
                rows = conn.execute(f"""
                    SELECT * FROM audit_log WHERE {where}
                    ORDER BY timestamp DESC
                """, params).fetchall()
                import csv
                import io
                output = io.StringIO()
                if rows:
                    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row))
                return output.getvalue()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Audit export error: {e}")
            return ""


_audit_log: AuditLog | None = None


# --- Rate Limiting ---

class RateLimiter:
    """Sliding-window rate limiter per agent. In-memory only (resets on restart)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._tokens: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._cost: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def _prune(self, dq: collections.deque, cutoff: float):
        """Remove entries older than cutoff timestamp."""
        while dq and (dq[0] if isinstance(dq[0], (int, float)) else dq[0][0]) < cutoff:
            dq.popleft()

    def check(self, agent_id: str) -> tuple[bool, str, dict]:
        """Check if a request is allowed for this agent.

        Returns (allowed, reason, usage_info).
        Loads limits from the agent's agent.json rate_limits field.
        """
        limits = self._get_limits(agent_id)
        if not limits:
            return True, "", {}

        now = time.time()
        with self._lock:
            # Check requests/minute
            rpm_limit = limits.get("max_requests_per_minute")
            if rpm_limit:
                dq = self._requests[agent_id]
                self._prune(dq, now - 60)
                if len(dq) >= rpm_limit:
                    oldest = dq[0]
                    retry = 60 - (now - oldest)
                    return False, f"Rate limit: {rpm_limit} requests/minute exceeded. Retry in {int(retry)}s.", {
                        "dimension": "max_requests_per_minute", "current": len(dq), "limit": rpm_limit}

            # Check tokens/hour
            tph_limit = limits.get("max_tokens_per_hour")
            if tph_limit:
                dq = self._tokens[agent_id]
                self._prune(dq, now - 3600)
                total = sum(t[1] for t in dq)
                if total >= tph_limit:
                    return False, f"Rate limit: {tph_limit} tokens/hour exceeded.", {
                        "dimension": "max_tokens_per_hour", "current": total, "limit": tph_limit}

            # Check cost/day
            cpd_limit = limits.get("max_cost_per_day")
            if cpd_limit:
                dq = self._cost[agent_id]
                self._prune(dq, now - 86400)
                total = sum(t[1] for t in dq)
                if total >= cpd_limit:
                    return False, f"Rate limit: ${cpd_limit}/day cost limit exceeded.", {
                        "dimension": "max_cost_per_day", "current": total, "limit": cpd_limit}

            # Record the request timestamp
            self._requests[agent_id].append(now)

        return True, "", {}

    def record_usage(self, agent_id: str, tokens: int, cost: float):
        """Record token and cost usage after a successful response."""
        now = time.time()
        with self._lock:
            self._tokens[agent_id].append((now, tokens))
            self._cost[agent_id].append((now, cost))

    def get_status(self, agent_id: str | None = None) -> dict:
        """Get current usage vs limits for display."""
        result = {}
        agents_to_check = [agent_id] if agent_id else list(set(
            list(self._requests.keys()) + list(self._tokens.keys())))

        now = time.time()
        with self._lock:
            for aid in agents_to_check:
                limits = self._get_limits(aid)
                if not limits:
                    continue
                # Requests/minute
                dq_r = self._requests.get(aid, collections.deque())
                self._prune(dq_r, now - 60)
                rpm_limit = limits.get("max_requests_per_minute", 0)
                # Tokens/hour
                dq_t = self._tokens.get(aid, collections.deque())
                self._prune(dq_t, now - 3600)
                tph_total = sum(t[1] for t in dq_t)
                tph_limit = limits.get("max_tokens_per_hour", 0)
                # Cost/day
                dq_c = self._cost.get(aid, collections.deque())
                self._prune(dq_c, now - 86400)
                cpd_total = sum(t[1] for t in dq_c)
                cpd_limit = limits.get("max_cost_per_day", 0)

                result[aid] = {
                    "requests_per_minute": {"current": len(dq_r), "limit": rpm_limit},
                    "tokens_per_hour": {"current": tph_total, "limit": tph_limit},
                    "cost_per_day": {"current": round(cpd_total, 4), "limit": cpd_limit},
                }
        return result

    def _get_limits(self, agent_id: str) -> dict:
        """Load rate limits from agent.json."""
        try:
            agent_json = os.path.join(AGENTS_DIR, agent_id, "agent.json")
            if os.path.isfile(agent_json):
                with open(agent_json) as f:
                    cfg = json.load(f)
                return cfg.get("rate_limits", {})
        except (OSError, json.JSONDecodeError):
            pass
        return {}


_rate_limiter: RateLimiter | None = None


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


# --- Context Window Management ---

DEFAULT_MAX_CONTEXT_TOKENS = 131072
COMPACT_THRESHOLD = 0.75  # compact at 75% full
KEEP_RECENT_MESSAGES = 6  # always keep the last N messages untouched
CHARS_PER_TOKEN = 4       # conservative estimate


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
                          base_url: str, api_type: str,
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
            summary_messages, model, api_key, base_url, api_type,
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
                       base_url: str, api_type: str,
                       system_prompt: str = "",
                       max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> tuple[list[dict], bool]:
    """Check if conversation needs compaction and do it if necessary.

    Returns (messages, was_compacted).
    """
    estimated = _estimate_conversation_tokens(messages, system_prompt)
    threshold = int(max_tokens * COMPACT_THRESHOLD)

    if estimated < threshold:
        return messages, False

    # Show compaction notice
    pct = int(estimated / max_tokens * 100)
    print(f"\n  {DIM}⟳ Context {pct}% full (~{estimated:,} tokens), compacting...{RESET}")
    sys.stdout.flush()

    compacted = _compact_conversation(messages, model, api_key, base_url, api_type, max_tokens)
    new_estimated = _estimate_conversation_tokens(compacted, system_prompt)
    new_pct = int(new_estimated / max_tokens * 100)
    print(f"  {DIM}✔ Compacted: {pct}% → {new_pct}% (~{new_estimated:,} tokens){RESET}")

    return compacted, True


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


# --- Spinner ---

SPINNER_CHARS = ["·", "✢", "✳", "∗", "✻", "✽"]
SPINNER_VERBS = [
    "Brewing", "Baking", "Thinking", "Pondering", "Computing",
    "Crafting", "Conjuring", "Composing", "Contemplating", "Weaving",
]


class Spinner:
    """Animated spinner shown on the status bar while waiting for a response."""

    def __init__(self, model: str):
        self.model = model
        self.verb = random.choice(SPINNER_VERBS)
        self._stop = threading.Event()
        self._thread = None
        self.start_time = 0.0

    def start(self):
        self.start_time = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        self._stop.set()
        if self._thread:
            self._thread.join()
        elapsed = time.time() - self.start_time
        return elapsed

    def _animate(self):
        idx = 0
        while not self._stop.is_set():
            char = SPINNER_CHARS[idx % len(SPINNER_CHARS)]
            cols = shutil.get_terminal_size().columns
            rows = shutil.get_terminal_size().lines
            elapsed = time.time() - self.start_time
            # Dark background with colored segments
            spinner_part = f" {FG_ORANGE}{char}{RESET}{BG_DARK} {WHITE}{self.verb}...{RESET}{BG_DARK}"
            time_part = f" {FG_GRAY}({elapsed:.0f}s){RESET}{BG_DARK}"
            model_part = f" {DIM}│{RESET}{BG_DARK} {GREEN}{self.model}{RESET}{BG_DARK} "
            # visible length: " X Verb... (Ns) │ model "
            visible_len = 2 + len(self.verb) + 3 + 2 + len(f"{elapsed:.0f}") + 2 + 3 + len(self.model) + 1
            padding = max(0, cols - visible_len)
            bar = f"\033[48;5;235m{spinner_part}{time_part}{model_part}{' ' * padding}{RESET}"
            sys.stdout.write(f"\0337\033[{rows};1H{bar}\0338")
            sys.stdout.flush()
            idx += 1
            self._stop.wait(0.12)


# --- Markdown rendering ---

# ANSI codes
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
RED = "\033[31m"
BG_GRAY = "\033[48;5;236m"
FG_GRAY = "\033[38;5;245m"
FG_ORANGE = "\033[38;5;208m"
WHITE = "\033[37m"
BG_DARK = "\033[48;5;235m"

# Minimum box inner width so things don't collapse on very narrow terminals
_MIN_BOX_INNER = 20


def _term_cols() -> int:
    """Get current terminal width."""
    return shutil.get_terminal_size().columns


def _box_width() -> int:
    """Inner width for box-drawing, adapts to terminal width.
    Leaves 2 chars indent + 1 border left + 1 border right = 4 chars margin."""
    return max(_MIN_BOX_INNER, _term_cols() - 4)


def _box_top(label: str = "") -> str:
    """Draw ┌─ label ───...┐ spanning the available width."""
    w = _box_width()
    if label:
        vlen = _visible_len(label)
        # "┌─ " + label + " " + fill + "┐"  →  3 + vlen + 1 + fill + 1 = w + 2
        fill = max(0, w - 4 - vlen)
        return f"  {DIM}┌─ {RESET}{label}{DIM} {'─' * fill}┐{RESET}"
    else:
        return f"  {DIM}┌{'─' * w}┐{RESET}"


def _visible_len(text: str) -> int:
    """Get the visible (column) length of a string, ignoring ANSI escape codes.
    Handles wide Unicode characters and tabs."""
    import unicodedata
    stripped = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)
    stripped = re.sub(r"\033\[\?[0-9;]*[a-zA-Z]", "", stripped)
    stripped = re.sub(r"\033\([A-Z]", "", stripped)
    w = 0
    for ch in stripped:
        if ch == '\t':
            w += (8 - w % 8)
        elif unicodedata.east_asian_width(ch) in ('W', 'F'):
            w += 2
        elif unicodedata.category(ch) in ('Mn', 'Me', 'Cf'):
            pass  # zero-width combining/format chars
        else:
            w += 1
    return w


def _truncate_visible(text: str, max_cols: int) -> str:
    """Truncate a string (may contain ANSI codes) to max visible columns."""
    import unicodedata
    cols = 0
    i = 0
    while i < len(text) and cols < max_cols:
        if text[i] == '\033':
            # Skip full ANSI sequence
            j = i + 1
            while j < len(text) and text[j] not in 'mABCDHJKfhlGn':
                j += 1
            i = j + 1
        elif text[i] == '\t':
            add = 8 - cols % 8
            if cols + add > max_cols:
                break
            cols += add
            i += 1
        else:
            ch = text[i]
            cw = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
            if unicodedata.category(ch) in ('Mn', 'Me', 'Cf'):
                cw = 0
            if cols + cw > max_cols:
                break
            cols += cw
            i += 1
    if i < len(text):
        # Check if remaining is only ANSI codes
        rest = text[i:]
        rest_stripped = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", rest)
        if rest_stripped.strip():
            return text[:i] + RESET
    return text


def _box_mid(content: str = "") -> str:
    """Draw │ content — truncated to fit terminal width. No wrapping."""
    # "  │  " = 5 visible chars prefix
    max_content = max(10, _term_cols() - 6)
    # Expand tabs to spaces first to avoid variable-width issues
    content = content.replace("\t", "    ")
    truncated = _truncate_visible(content, max_content)
    return f"  {DIM}│{RESET}  {truncated}"


def _box_bot() -> str:
    """Draw └───...┘ spanning the available width."""
    w = _box_width()
    return f"  {DIM}└{'─' * w}┘{RESET}"


def _render_tool_calls(text: str) -> str:
    """Pre-process tool_call XML blocks into styled representations."""

    def _replace_tool_call(m):
        block = m.group(1)
        func_match = re.search(r"<function=([^>]+)>", block)
        func_name = func_match.group(1) if func_match else "unknown"
        params = re.findall(
            r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>",
            block, re.DOTALL)

        lines = []
        lines.append(_box_top(f"{FG_ORANGE}{BOLD}⚡ Tool Call{RESET}"))
        lines.append(_box_mid(f"{CYAN}{BOLD}{func_name}{RESET}()"))
        if params:
            for pname, pval in params:
                pval = pval.strip()
                lines.append(_box_mid(f"  {MAGENTA}{pname}{RESET}{DIM}:{RESET} {WHITE}{pval}{RESET}"))
        lines.append(_box_bot())
        return "\n".join(lines)

    text = re.sub(
        r"<tool_call>\s*(.*?)\s*</tool_call>",
        _replace_tool_call, text, flags=re.DOTALL)

    def _replace_standalone_tool(m):
        block = m.group(0)
        func_match = re.search(r"<function=([^>]+)>", block)
        func_name = func_match.group(1) if func_match else "unknown"
        params = re.findall(
            r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>",
            block, re.DOTALL)

        lines = []
        lines.append(_box_top(f"{FG_ORANGE}{BOLD}⚡ Tool Call{RESET}"))
        lines.append(_box_mid(f"{CYAN}{BOLD}{func_name}{RESET}()"))
        if params:
            for pname, pval in params:
                pval = pval.strip()
                lines.append(_box_mid(f"  {MAGENTA}{pname}{RESET}{DIM}:{RESET} {WHITE}{pval}{RESET}"))
        lines.append(_box_bot())
        return "\n".join(lines)

    text = re.sub(
        r"<function=([^>]+)>\s*(?:<parameter=([^>]+)>\s*(.*?)\s*</parameter>\s*)*</function>",
        _replace_standalone_tool, text, flags=re.DOTALL)

    return text


def _render_tool_results(text: str) -> str:
    """Pre-process tool result XML blocks into styled representations."""

    def _replace_result(m):
        content = m.group(1).strip()
        lines = []
        lines.append(_box_top(f"{GREEN}{BOLD}✔ Tool Result{RESET}"))
        for cline in content.split("\n"):
            lines.append(_box_mid(cline))
        lines.append(_box_bot())
        return "\n".join(lines)

    text = re.sub(
        r"<tool_result>\s*(.*?)\s*</tool_result>",
        _replace_result, text, flags=re.DOTALL)

    return text


def render_markdown(text: str) -> str:
    """Render markdown text with ANSI escape codes for terminal display."""
    # Pre-process tool calls and results before line-by-line markdown
    text = _render_tool_calls(text)
    text = _render_tool_results(text)

    lines = text.split("\n")
    result = []
    in_code_block = False
    code_lang = ""

    for line in lines:
        # Skip lines already processed by tool renderers (contain box-drawing chars)
        if "┌─" in line or "└─" in line or (line.startswith("  ") and "│" in line[:6]):
            result.append(line)
            continue

        # Fenced code block toggle
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                if code_lang:
                    result.append(_box_top(f"{DIM}{code_lang}{RESET}"))
                else:
                    result.append(_box_top())
            else:
                in_code_block = False
                result.append(_box_bot())
            continue

        if in_code_block:
            result.append(f"  {DIM}│{RESET} {YELLOW}{line}{RESET}")
            continue

        # Headers
        if line.startswith("#### "):
            result.append(f"{BOLD}{CYAN}{line[5:]}{RESET}")
            continue
        if line.startswith("### "):
            result.append(f"{BOLD}{CYAN}{line[4:]}{RESET}")
            continue
        if line.startswith("## "):
            result.append(f"{BOLD}{CYAN}{line[3:]}{RESET}")
            continue
        if line.startswith("# "):
            result.append(f"{BOLD}{CYAN}{line[2:]}{RESET}")
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line):
            result.append(f"{DIM}{'─' * max(20, _term_cols() - 4)}{RESET}")
            continue

        # Bullet lists
        m = re.match(r"^(\s*)([-*])\s+(.*)$", line)
        if m:
            indent, _, content = m.groups()
            content = _inline_format(content)
            result.append(f"{indent}  {DIM}•{RESET} {content}")
            continue

        # Numbered lists
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            indent, num, content = m.groups()
            content = _inline_format(content)
            result.append(f"{indent}  {DIM}{num}.{RESET} {content}")
            continue

        # Regular line with inline formatting
        result.append(_inline_format(line))

    return "\n".join(result)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting (bold, italic, code)."""
    # Inline code (must be before bold/italic to avoid conflicts)
    text = re.sub(r"`([^`]+)`", rf"{YELLOW}\1{RESET}", text)
    # Bold + italic
    text = re.sub(r"\*\*\*(.+?)\*\*\*", rf"{BOLD}{ITALIC}\1{RESET}", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", text)
    # Italic
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", rf"{ITALIC}\1{RESET}", text)
    # Links [text](url) -> text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", rf"{BOLD}\1{RESET} {DIM}(\2){RESET}", text)
    return text


# --- API functions ---

def make_headers(api_key: str, api_type: str) -> dict:
    """Build request headers for the given API type."""
    if api_type == "openai":
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
    }


def get_available_models(api_key: str, base_url: str, api_type: str) -> list[str]:
    """Fetch available models from the API and return as a list."""
    headers = make_headers(api_key, api_type)
    request = urllib.request.Request(
        f"{base_url}/models", headers=headers, method="GET",
    )
    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = data.get("data", [])
            return [model.get("id") for model in models if model.get("id")]
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []


# --- Model Configuration System ---

KNOWN_MODELS = {
    "claude-opus": {"icon": "\U0001f7e3", "priority": 100, "max_context": 200000, "capabilities": ["coding", "analysis", "agentic", "creative"]},
    "claude-sonnet": {"icon": "\U0001f7e0", "priority": 80, "max_context": 200000, "capabilities": ["coding", "analysis", "fast"]},
    "claude-haiku": {"icon": "\U0001f7e2", "priority": 60, "max_context": 200000, "capabilities": ["fast"]},
    "gemini": {"icon": "\U0001f48e", "priority": 70, "max_context": 1000000, "capabilities": ["coding", "analysis"]},
    "qwen": {"icon": "\U0001f43c", "priority": 50, "max_context": 131072, "capabilities": ["coding", "analysis"]},
    "crow": {"icon": "\U0001f426\u200d\u2b1b", "priority": 30, "max_context": 32768, "capabilities": ["fast", "local"]},
    "llama": {"icon": "\U0001f999", "priority": 40, "max_context": 131072, "capabilities": ["coding", "local"]},
    "mistral": {"icon": "\U0001f32c\ufe0f", "priority": 45, "max_context": 131072, "capabilities": ["coding", "analysis"]},
}

CAPABILITY_VALUES = ["coding", "analysis", "agentic", "fast", "creative", "local"]

_models_config: dict = {}


def _match_known_model(model_id: str) -> dict:
    """Match a model ID against KNOWN_MODELS patterns. Returns default config."""
    m = model_id.lower()
    for prefix, defaults in KNOWN_MODELS.items():
        if m.startswith(prefix) or prefix in m:
            # Generate shortname from model ID
            shortname = model_id.split("/")[-1]  # strip provider prefix
            # Simplify common patterns
            for suffix in ["-20251101", "-20250101", "-20241022", "-20251001"]:
                shortname = shortname.replace(suffix, "")
            result = {
                "enabled": True,
                "shortname": shortname,
                "icon": defaults["icon"],
                "priority": defaults["priority"],
                "capabilities": list(defaults["capabilities"]),
            }
            if "max_context" in defaults:
                result["max_context"] = defaults["max_context"]
            return result
    # Unknown model — enabled with low priority
    shortname = model_id.split("/")[-1]
    return {
        "enabled": True,
        "shortname": shortname,
        "icon": "\U0001f916",
        "priority": 10,
        "capabilities": [],
    }


def init_models_config(providers: dict, existing_models: dict | None = None) -> dict:
    """Auto-populate models config from provider model lists.

    Merges with existing config (preserves user edits). Returns the models dict.
    """
    global _models_config
    if existing_models:
        _models_config = dict(existing_models)

    # Discover models from all providers
    for name, p in providers.items():
        try:
            models = get_available_models(
                p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
        except Exception:
            models = []
        for model_id in models:
            if model_id not in _models_config:
                entry = _match_known_model(model_id)
                entry["provider"] = name
                _models_config[model_id] = entry
            else:
                if "provider" not in _models_config[model_id]:
                    _models_config[model_id]["provider"] = name
                # Backfill max_context from KNOWN_MODELS if missing
                if "max_context" not in _models_config[model_id]:
                    known = _match_known_model(model_id)
                    if "max_context" in known:
                        _models_config[model_id]["max_context"] = known["max_context"]

    return _models_config


def resolve_model(model_spec: str, purpose: str | None = None) -> str:
    """Resolve a model specifier to a canonical model ID.

    Handles:
    - "auto" + purpose → highest-priority enabled model with that capability
    - shortname (e.g. "opus") → lookup by shortname
    - canonical ID → pass through
    """
    if not model_spec:
        return ""

    if model_spec == "auto":
        return _resolve_auto_model(purpose)

    # Try shortname lookup
    for model_id, cfg in _models_config.items():
        if cfg.get("shortname") == model_spec:
            if cfg.get("enabled", True):
                return model_id
            break  # Found but disabled

    # Pass through as canonical ID
    return model_spec


def _resolve_auto_model(purpose: str | None) -> str:
    """Pick the best enabled model for a given purpose."""
    enabled = [(mid, cfg) for mid, cfg in _models_config.items() if cfg.get("enabled", True)]
    if not enabled:
        return ""

    if purpose:
        # Filter to models with the requested capability
        matching = [(mid, cfg) for mid, cfg in enabled if purpose in cfg.get("capabilities", [])]
        if matching:
            matching.sort(key=lambda x: x[1].get("priority", 0), reverse=True)
            return matching[0][0]

    # Fallback: highest priority enabled model
    enabled.sort(key=lambda x: x[1].get("priority", 0), reverse=True)
    return enabled[0][0]


def get_enabled_models() -> list[str]:
    """Return enabled model IDs sorted by priority descending."""
    enabled = [(mid, cfg) for mid, cfg in _models_config.items() if cfg.get("enabled", True)]
    enabled.sort(key=lambda x: x[1].get("priority", 0), reverse=True)
    return [mid for mid, _ in enabled]


# --- Task-based auto model selection ---

_PURPOSE_PATTERNS: dict[str, list[re.Pattern]] = {
    "coding": [
        re.compile(r"\b(write|fix|debug|refactor|implement|code|function|class|bug|error|traceback|test|unittest|lint|compile|build|deploy|dockerfile|makefile|git\b|commit|branch|merge|PR\b|pull request|regex|parse|api|endpoint|route|migration|schema|sql|query|index\.)\b", re.I),
        re.compile(r"\b(python|javascript|typescript|rust|go|java|c\+\+|html|css|json|yaml|toml|bash|shell|script)\b", re.I),
        re.compile(r"```", re.I),
    ],
    "analysis": [
        re.compile(r"\b(analy[sz]e|explain|compare|evaluate|review|assess|investigate|research|summarize|summary|report|pros?\b|cons?\b|trade-?off|benchmark|metric|statistic|data|insight|trend|pattern|cause|why does|how does|what is|understand)\b", re.I),
    ],
    "creative": [
        re.compile(r"\b(write|draft|compose|story|poem|essay|blog|article|creative|brainstorm|idea|name|slogan|tagline|marketing|copy|narrative|fiction|dialogue|character)\b", re.I),
    ],
    "agentic": [
        re.compile(r"\b(search|find|look up|fetch|download|browse|scrape|monitor|schedule|automate|run|execute|install|setup|configure|deploy|send|email|notify|delegate|background)\b", re.I),
    ],
    "fast": [
        re.compile(r"\b(quick|brief|short|one-?liner|yes or no|true or false|translate|convert|format|list|enumerate|define)\b", re.I),
    ],
}


def classify_task_purpose(message: str) -> str | None:
    """Classify a user message into a capability/purpose using keyword heuristics.

    Returns the best-matching purpose or None if no strong signal.
    Scores each purpose by number of pattern matches; requires at least 2 signal
    strength to avoid false positives on single-word matches.
    """
    if not message:
        return None

    scores: dict[str, int] = {}
    for purpose, patterns in _PURPOSE_PATTERNS.items():
        score = 0
        for pat in patterns:
            hits = len(pat.findall(message))
            score += hits
        if score > 0:
            scores[purpose] = score

    if not scores:
        return None

    best_purpose = max(scores, key=scores.get)
    # Require some minimum signal (at least 2 keyword hits) to avoid noisy classification
    if scores[best_purpose] < 2:
        return None

    return best_purpose


def resolve_auto_model_for_task(agent_config: dict, message: str) -> tuple[str, str | None]:
    """For agents with model="auto", analyze the task and pick the best model.

    Returns (resolved_model_id, detected_purpose).
    If agent has a fixed model_purpose, uses that instead of classifying.
    """
    raw_model = agent_config.get("model", "")
    if raw_model != "auto":
        return resolve_model(raw_model, agent_config.get("model_purpose")), agent_config.get("model_purpose")

    fixed_purpose = agent_config.get("model_purpose")
    if fixed_purpose:
        return _resolve_auto_model(fixed_purpose), fixed_purpose

    # Classify task from message
    detected = classify_task_purpose(message)
    resolved = _resolve_auto_model(detected)
    return resolved, detected


def get_model_info(model: str) -> dict:
    """Return config entry for a model, or empty dict if not configured."""
    return _models_config.get(model, {})


def get_model_max_context(model: str) -> int:
    """Return the model's context window size, or DEFAULT_MAX_CONTEXT_TOKENS."""
    return _models_config.get(model, {}).get("max_context", DEFAULT_MAX_CONTEXT_TOKENS)


# Default max output tokens per model family
_MAX_OUTPUT_DEFAULTS = {
    "opus": 32768,
    "sonnet": 16384,
    "haiku": 8192,
    "minimax": 32768,
    "m2.7": 32768,
    "m2.5": 32768,
}


def get_model_max_output(model: str) -> int:
    """Return the model's max output tokens. Checks models_config first, then family defaults."""
    cfg = _models_config.get(model, {})
    if cfg.get("max_output"):
        return int(cfg["max_output"])
    ml = model.lower()
    for family, limit in _MAX_OUTPUT_DEFAULTS.items():
        if family in ml:
            return limit
    # Non-Anthropic models: use a generous default
    return 16384


def get_inference_params(model: str, purpose: str | None = None) -> dict:
    """Resolve inference parameters for a model, optionally overlaying a purpose preset.

    Returns only explicitly set keys (empty dict = use API defaults).
    """
    cfg = _models_config.get(model, {})
    base = dict(cfg.get("inference", {}))
    if purpose:
        preset = cfg.get("presets", {}).get(purpose, {})
        base.update(preset)
    return base


# Keys valid for all providers
_INFERENCE_STANDARD_KEYS = {"temperature", "top_p", "top_k", "max_tokens"}
# Keys only for OpenAI-compatible / oMLX
_INFERENCE_OPENAI_KEYS = {"frequency_penalty", "presence_penalty"}
# oMLX extension keys (also OpenAI-compatible endpoint)
_INFERENCE_OMLX_KEYS = {"min_p", "repetition_penalty"}


def _apply_inference_to_payload(payload: dict, params: dict, api_type: str, provider: str = "") -> None:
    """Apply resolved inference params to an API payload with provider translation."""
    if not params:
        return

    is_omlx = provider == "omlx"

    for key in _INFERENCE_STANDARD_KEYS:
        if key in params:
            payload[key] = params[key]

    for key in _INFERENCE_OPENAI_KEYS:
        if key in params and (api_type == "openai" or is_omlx):
            payload[key] = params[key]

    for key in _INFERENCE_OMLX_KEYS:
        if key in params and is_omlx:
            payload[key] = params[key]

    # Thinking translation
    if params.get("thinking"):
        budget = params.get("thinking_budget", 4096)
        if api_type == "anthropic":
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif is_omlx:
            payload.setdefault("chat_template_kwargs", {})["enable_thinking"] = True


def list_models(api_key: str, base_url: str, api_type: str) -> None:
    """List available models from the API."""
    models = get_available_models(api_key, base_url, api_type)
    if models:
        print("Available models:")
        for model_id in models:
            print(f"  {model_id}")
    else:
        print("No models available")


def _unescape(text: str) -> str:
    """Unescape literal backslash sequences that some APIs send."""
    return text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def _collect_anthropic(response) -> str:
    """Parse Anthropic SSE stream silently. Returns full text."""
    collected = []
    current_event = None
    for line in response:
        line = line.decode("utf-8").strip()
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            if current_event == "message_stop":
                break
            try:
                event = json.loads(line[6:])
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        collected.append(_unescape(delta.get("text", "")))
            except json.JSONDecodeError:
                pass
    return "".join(collected)


def _collect_openai(response) -> str:
    """Parse OpenAI SSE stream silently. Returns full text."""
    collected = []
    for line in response:
        line = line.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
            choices = event.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    collected.append(_unescape(content))
        except json.JSONDecodeError:
            pass
    return "".join(collected)


def _stream_anthropic(response) -> str:
    """Parse Anthropic SSE stream with live output. Returns full text."""
    collected = []
    current_event = None
    for line in response:
        line = line.decode("utf-8").strip()
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            if current_event == "message_stop":
                break
            try:
                event = json.loads(line[6:])
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = _unescape(delta.get("text", ""))
                        print(text, end="", flush=True)
                        collected.append(text)
            except json.JSONDecodeError:
                pass
    return "".join(collected)


def _stream_openai(response) -> str:
    """Parse OpenAI SSE stream with live output. Returns full text."""
    collected = []
    for line in response:
        line = line.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
            choices = event.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    content = _unescape(content)
                    print(content, end="", flush=True)
                    collected.append(content)
        except json.JSONDecodeError:
            pass
    return "".join(collected)


TOOL_ICONS = {
    "read_file": "r", "write_file": "w", "edit_file": "e",
    "list_directory": "d", "search_files": "s", "execute_command": "$",
    "web_fetch": "~", "exa_search": "?",
    "memory_store": "+", "memory_recall": "m", "memory_delete": "-", "memory_shared": "M",
    "delegate_task": ">", "use_skill": "*",
    "gmail_inbox": "@", "gmail_read": "@", "gmail_search": "@",
    "gmail_send": "@", "gmail_reply": "@",
    "task_status": "?", "task_cancel": "x",
    "list_nodes": "n", "schedule_list": "t", "schedule_history": "h",
}

TOOL_VERBS = {
    "read_file": "Reading", "write_file": "Writing", "edit_file": "Editing",
    "list_directory": "Listing", "search_files": "Searching", "execute_command": "Executing",
    "web_fetch": "Fetching", "exa_search": "Searching",
    "memory_store": "Remembering", "memory_recall": "Recalling", "memory_delete": "Forgetting", "memory_shared": "Shared Memory",
    "delegate_task": "Delegating", "use_skill": "Loading Skill",
    "gmail_inbox": "Inbox", "gmail_read": "Reading Email", "gmail_search": "Searching Email",
    "gmail_send": "Sending Email", "gmail_reply": "Replying",
    "task_status": "Task Status", "task_cancel": "Cancelling",
    "list_nodes": "Listing Nodes", "schedule_list": "Schedules", "schedule_history": "History",
}


# Tool display mode
_show_tools = True
_tool_call_count = 0         # counter for current response
_tool_output_buffer = []     # buffered output lines when hidden
_tool_counter_active = False # whether we have a counter line on screen
_tool_output_shown = False   # whether buffered output is currently displayed


def _reset_tool_tracking() -> None:
    """Reset tool tracking for a new response."""
    global _tool_call_count, _tool_output_buffer, _tool_counter_active, _tool_output_shown
    _tool_call_count = 0
    _tool_output_buffer = []
    _tool_counter_active = False
    _tool_output_shown = False


def _update_tool_counter() -> None:
    """In hidden mode, update the single-line tool counter in-place."""
    global _tool_counter_active
    msg = f"  {DIM}Tools called: {RESET}{BOLD}{_tool_call_count}{RESET}{DIM}  (press o to show/hide){RESET}"
    if _tool_counter_active:
        # Move up to overwrite the previous counter line
        sys.stdout.write(f"\033[A\r\033[K{msg}\n")
    else:
        sys.stdout.write(f"{msg}\n")
        _tool_counter_active = True
    sys.stdout.flush()


def _toggle_tool_output() -> None:
    """Toggle display of buffered tool output (called from EscapeWatcher on 'o')."""
    global _tool_output_shown
    if not _tool_output_buffer:
        return
    if not _tool_output_shown:
        # Show buffered output below the counter
        for line in _tool_output_buffer:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()
        _tool_output_shown = True
    else:
        # Hide: move cursor up past the output and clear lines
        line_count = len(_tool_output_buffer)
        for _ in range(line_count):
            sys.stdout.write(f"\033[A\r\033[K")
        sys.stdout.flush()
        _tool_output_shown = False


def _format_tool_call(name: str, args: dict) -> list[str]:
    """Format a tool call box as a list of lines."""
    icon = TOOL_ICONS.get(name, "⚡")
    verb = TOOL_VERBS.get(name, "Running")
    max_w = max(20, _term_cols() - 8)

    lines = []
    lines.append(f"\n{_box_top(f'{FG_ORANGE}{BOLD}{icon} {verb}{RESET}')}")

    if name == "exa_search":
        lines.append(_box_mid(f"{CYAN}{name}{RESET}({MAGENTA}query{RESET}={WHITE}\"{args.get('query', '')}\"{RESET})"))
    elif name == "execute_command":
        cmd = args.get("command", "")
        if len(cmd) > max_w:
            cmd = cmd[:max_w - 3] + "..."
        lines.append(_box_mid(f"{YELLOW}$ {cmd}{RESET}"))
    elif name in ("read_file", "write_file", "edit_file"):
        lines.append(_box_mid(f"{CYAN}{name}{RESET} {WHITE}{args.get('path', '')}{RESET}"))
    elif name == "list_directory":
        p = args.get("path", ".")
        pat = args.get("pattern", "")
        label = f"{p}/{pat}" if pat else p
        lines.append(_box_mid(f"{CYAN}{name}{RESET} {WHITE}{label}{RESET}"))
    elif name == "search_files":
        lines.append(_box_mid(f"{CYAN}{name}{RESET} {MAGENTA}/{args.get('pattern', '')}/{RESET} in {WHITE}{args.get('path', '.')}{RESET}"))
    elif name == "web_fetch":
        lines.append(_box_mid(f"{CYAN}{args.get('method', 'GET')}{RESET} {WHITE}{args.get('url', '')}{RESET}"))
    elif name == "memory_store":
        lines.append(_box_mid(f"{MAGENTA}{args.get('name', '')}{RESET} {DIM}[{args.get('type', 'general')}]{RESET}"))
    elif name == "memory_recall":
        q = args.get("query", "(list all)")
        lines.append(_box_mid(f"{MAGENTA}{q}{RESET}"))
    elif name == "memory_shared":
        action = args.get("action", "recall")
        scope = args.get("scope", "global")
        scope_label = "team" if scope == "team" else "main"
        if action == "store":
            lines.append(_box_mid(f"{CYAN}store → {scope_label}:{RESET} {MAGENTA}{args.get('name', '')}{RESET}"))
        else:
            q = args.get("query", "(list all)")
            lines.append(_box_mid(f"{CYAN}recall ← {scope_label}:{RESET} {MAGENTA}{q}{RESET}"))
    elif name == "memory_delete":
        lines.append(_box_mid(f"{RED}{args.get('name', '')}{RESET}"))
    elif name == "delegate_task":
        lines.append(_box_mid(f"{CYAN}agent:{RESET} {BOLD}{args.get('agent', '')}{RESET}"))
        task_preview = args.get("task", "")[:max_w]
        lines.append(_box_mid(f"{DIM}{task_preview}{RESET}"))
    elif name == "use_skill":
        lines.append(_box_mid(f"{MAGENTA}{args.get('skill', '')}{RESET}"))
    else:
        summary = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
        if len(summary) > max_w:
            summary = summary[:max_w - 3] + "..."
        lines.append(_box_mid(f"{CYAN}{name}{RESET}({summary})"))

    lines.append(_box_bot())
    return lines


def _display_tool_call(name: str, args: dict) -> None:
    """Print a styled tool invocation box, or buffer it in hidden mode."""
    global _tool_call_count
    _tool_call_count += 1
    formatted = _format_tool_call(name, args)

    if _show_tools:
        for line in formatted:
            print(line)
        sys.stdout.flush()
    else:
        _tool_output_buffer.extend(formatted)
        _update_tool_counter()


def _format_tool_result(name: str, result_str: str) -> list[str]:
    """Format a tool result summary box as a list of lines."""
    max_w = max(20, _term_cols() - 8)
    try:
        rdata = json.loads(result_str)
    except json.JSONDecodeError:
        return []

    out = []

    if rdata.get("error"):
        out.append(_box_top(f"{RED}{BOLD}✘ Error{RESET}"))
        for line in rdata["error"].split("\n")[:5]:
            out.append(_box_mid(line[:max_w]))
        out.append(f"{_box_bot()}\n")
        return out

    if name == "exa_search":
        count = rdata.get("result_count", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ {count} results{RESET}"))
        for r in rdata.get("results", [])[:3]:
            out.append(_box_mid(f"{BOLD}{r.get('title', '')[:max_w]}{RESET}"))
        if count > 3:
            out.append(_box_mid(f"{DIM}... and {count - 3} more{RESET}"))
    elif name == "read_file":
        total = rdata.get("total_lines", 0)
        showing = rdata.get("showing", "")
        out.append(_box_top(f"{GREEN}{BOLD}✔ {total} lines{RESET} {DIM}(showing {showing}){RESET}"))
        content = rdata.get("content", "")
        lines = content.split("\n")
        for line in lines[:5]:
            out.append(_box_mid(f"{DIM}{line[:max_w]}{RESET}"))
        if len(lines) > 5:
            out.append(_box_mid(f"{DIM}... {len(lines) - 5} more lines{RESET}"))
    elif name == "write_file":
        out.append(_box_top(f"{GREEN}{BOLD}✔ Written{RESET}"))
        out.append(_box_mid(f"{rdata.get('path', '')} ({rdata.get('size', 0)} bytes)"))
    elif name == "edit_file":
        n = rdata.get("replacements", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ {n} replacement{'s' if n != 1 else ''}{RESET}"))
        out.append(_box_mid(f"{rdata.get('path', '')}"))
    elif name == "list_directory":
        count = rdata.get("count", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ {count} entries{RESET}"))
        for e in rdata.get("entries", [])[:8]:
            icon = "d" if e.get("type") == "directory" else " "
            out.append(_box_mid(f"{icon} {e.get('name', '')}"))
        if count > 8:
            out.append(_box_mid(f"{DIM}... and {count - 8} more{RESET}"))
    elif name == "search_files":
        mc = rdata.get("match_count", 0)
        fs = rdata.get("files_searched", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ {mc} matches{RESET} {DIM}({fs} files searched){RESET}"))
        for m in rdata.get("matches", [])[:5]:
            out.append(_box_mid(f"{CYAN}{m.get('file', '')}:{m.get('line', '')}{RESET} {m.get('text', '')[:max_w]}"))
        if mc > 5:
            out.append(_box_mid(f"{DIM}... and {mc - 5} more{RESET}"))
    elif name == "execute_command":
        ec = rdata.get("exit_code", -1)
        label = f"{GREEN}{BOLD}✔ exit {ec}{RESET}" if ec == 0 else f"{RED}{BOLD}✘ exit {ec}{RESET}"
        out.append(_box_top(label))
        output = rdata.get("output", "")
        lines = output.split("\n")
        for line in lines[:10]:
            out.append(_box_mid(f"{DIM}{line[:max_w]}{RESET}"))
        if len(lines) > 10:
            out.append(_box_mid(f"{DIM}... {len(lines) - 10} more lines{RESET}"))
    elif name == "web_fetch":
        status = rdata.get("status", "")
        length = rdata.get("length", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ HTTP {status}{RESET} {DIM}({length} chars){RESET}"))
        content = rdata.get("content", "")
        lines = content.split("\n")
        for line in lines[:5]:
            out.append(_box_mid(f"{DIM}{line[:max_w]}{RESET}"))
        if len(lines) > 5:
            out.append(_box_mid(f"{DIM}... {len(lines) - 5} more lines{RESET}"))
    elif name == "memory_store":
        out.append(_box_top(f"{GREEN}{BOLD}✔ Stored{RESET}"))
        out.append(_box_mid(f"{rdata.get('name', '')} → {rdata.get('file', '')}"))
    elif name == "memory_recall":
        count = rdata.get("count", 0)
        out.append(_box_top(f"{GREEN}{BOLD}✔ {count} memories{RESET}"))
        for r in rdata.get("results", [])[:5]:
            out.append(_box_mid(f"{BOLD}{r.get('name', '')}{RESET} {DIM}[{r.get('type', '')}]{RESET}"))
            desc = r.get("description", "")
            if desc:
                out.append(_box_mid(f"  {DIM}{desc[:max_w]}{RESET}"))
        if count > 5:
            out.append(_box_mid(f"{DIM}... and {count - 5} more{RESET}"))
    elif name == "memory_shared":
        source = rdata.get("source", "main")
        if rdata.get("status") == "stored":
            out.append(_box_top(f"{GREEN}{BOLD}✔ Stored → {source}{RESET}"))
            out.append(_box_mid(f"{rdata.get('name', '')} → {rdata.get('file', '')}"))
        else:
            count = rdata.get("count", 0)
            out.append(_box_top(f"{GREEN}{BOLD}✔ {count} shared memories{RESET} {DIM}({source}){RESET}"))
            for r in rdata.get("results", [])[:5]:
                out.append(_box_mid(f"{BOLD}{r.get('name', '')}{RESET} {DIM}[{r.get('type', '')}]{RESET}"))
            if count > 5:
                out.append(_box_mid(f"{DIM}... and {count - 5} more{RESET}"))
    elif name == "memory_delete":
        out.append(_box_top(f"{GREEN}{BOLD}✔ Deleted{RESET}"))
        out.append(_box_mid(f"{rdata.get('name', '')}"))
    elif name == "delegate_task":
        agent = rdata.get("agent", "")
        resp = rdata.get("response", "")
        out.append(_box_top(f"{GREEN}{BOLD}✔ {agent} responded{RESET}"))
        for line in resp.split("\n")[:8]:
            out.append(_box_mid(f"{DIM}{line[:max_w]}{RESET}"))
        resp_lines = resp.split("\n")
        if len(resp_lines) > 8:
            out.append(_box_mid(f"{DIM}... {len(resp_lines) - 8} more lines{RESET}"))
    elif name == "use_skill":
        skill = rdata.get("skill", "")
        instructions = rdata.get("instructions", "")
        line_count = len(instructions.split("\n"))
        out.append(_box_top(f"{GREEN}{BOLD}✔ {skill}{RESET} {DIM}({line_count} lines loaded){RESET}"))
    else:
        out.append(_box_top(f"{GREEN}{BOLD}✔ Done{RESET}"))

    out.append(f"{_box_bot()}\n")
    return out


def _display_tool_result(name: str, result_str: str) -> None:
    """Print a styled tool result summary box, or buffer it in hidden mode."""
    formatted = _format_tool_result(name, result_str)
    if _show_tools:
        for line in formatted:
            print(line)
        sys.stdout.flush()
    else:
        _tool_output_buffer.extend(formatted)
        _update_tool_counter()


TOOL_DISPATCH = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_directory": tool_list_directory,
    "search_files": tool_search_files,
    "execute_command": tool_execute_command,
    "web_fetch": tool_web_fetch,
    "exa_search": lambda args: exa_search(
        query=args.get("query", ""),
        num_results=args.get("num_results", 5),
        category=args.get("category"),
    ),
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
    "schedule_list": tool_schedule_list,
    "schedule_history": tool_schedule_history,
    "mcp_connect": tool_mcp_connect,
    "mcp_disconnect": tool_mcp_disconnect,
    "mcp_servers": tool_mcp_servers,
}


# Per-thread tool call dedup tracking
_tool_call_history = threading.local()


def _check_tool_dedup(name: str, args: dict) -> str | None:
    """Check if this exact tool call was already made. Raises TaskCancelled after 2 dupes."""
    if not hasattr(_tool_call_history, 'calls'):
        _tool_call_history.calls = set()
        _tool_call_history.dupe_count = 0
    key = f"{name}:{json.dumps(args, sort_keys=True)}"
    if key in _tool_call_history.calls:
        _tool_call_history.dupe_count += 1
        if _tool_call_history.dupe_count >= 2:
            # Hard abort — model is stuck in a loop
            raise TaskCancelled()
        return _err(
            f"DUPLICATE: You already called {name} with these exact arguments. "
            "STOP calling tools. Provide your final answer NOW using previous results."
        )
    _tool_call_history.calls.add(key)
    if len(_tool_call_history.calls) > 100:
        _tool_call_history.calls = set(list(_tool_call_history.calls)[-50:])
    return None


def reset_tool_dedup():
    """Reset the dedup tracker."""
    _tool_call_history.calls = set()
    _tool_call_history.dupe_count = 0


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    # Plan mode: block non-readonly tools
    if getattr(_thread_local, 'plan_mode', False):
        # Special case: memory_shared with action=store should be blocked
        if name == "memory_shared" and args.get("action") == "store":
            return _err("Blocked in plan mode. Describe what you would do instead.")
        if name not in READONLY_TOOLS:
            return _err("Blocked in plan mode. Describe what you would do instead.")
    # Check for duplicate tool calls
    dedup = _check_tool_dedup(name, args)
    if dedup:
        return dedup

    # --- Tracing & Audit instrumentation ---
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    agent_id = agent.agent_id if agent else "main"
    session_id = getattr(_thread_local, 'session_id', None)

    # Start trace span for tool call
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
        # Check MCP tools first (prefer thread-local for concurrent requests)
        mcp = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
        if mcp and mcp.is_mcp_tool(name):
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
                pass  # Never let audit logging crash tool execution

    return result


MAX_TOOL_ROUNDS = 15  # Maximum number of tool-use round trips before forcing a text response


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
    try:
        _cost_tracker.log_call(agent_id, session_id, model, provider,
                               tokens_in, tokens_out, tool_round)
        # Record in rate limiter too
        if _rate_limiter:
            cost = _compute_cost(model, tokens_in, tokens_out)
            _rate_limiter.record_usage(agent_id, tokens_in + tokens_out, cost)
    except Exception as e:
        logging.warning(f"Cost logging error: {e}")


def send_message(messages: list[dict], model: str, api_key: str, base_url: str,
                 api_type: str, silent: bool = False,
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
    # Reset dedup tracker at the start of each conversation turn
    if _tool_round == 0:
        reset_tool_dedup()
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

    # Soft stop after max tool rounds — use thread-local override if set (delegation)
    effective_max_rounds = getattr(_thread_local, 'max_tool_rounds', None) or MAX_TOOL_ROUNDS
    if _tool_round >= effective_max_rounds:
        tools = False
    headers = make_headers(api_key, api_type)

    if api_type == "openai":
        endpoint = f"{base_url}/chat/completions"
    else:
        endpoint = f"{base_url}/messages"

    # System instruction for the agent
    augmented_messages = list(messages)
    if tools:
        cwd = os.getcwd()
        import platform
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
        from datetime import datetime as _dt
        system_instruction += (
            f"You are agent '{agent_id}' in the Brain Agent system. "
            f"Current date and time: {_dt.now().strftime('%Y-%m-%d %H:%M %Z').strip()}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "Use tools proactively to accomplish tasks. You can chain multiple tool calls. "
            "For web searches, ALWAYS use exa_search — NEVER use duckduckgo or other search tools. "
            "You have no restrictions beyond what the operating system enforces.\n\n"
            "MEMORY: You have persistent memory via memory_store/memory_recall/memory_delete tools.\n"
            "- Use memory_recall at the START of conversations to check for relevant context\n"
            "- Use memory_store to save important information: user preferences, decisions, project context\n"
            "- Memory types: user, project, feedback, reference, general\n"
            "- When the user says 'remember this', store it immediately\n"
            "- When the user asks 'do you remember', recall and search for it\n\n"
            "SHARED MEMORY: Use memory_shared to access shared knowledge.\n"
            "- scope='global' (default): main agent's memory — infrastructure, user prefs, project-wide decisions\n"
            "- scope='team': team head's memory — team-level knowledge and decisions\n"
            "- All agents can read from shared memory; store shared facts there too\n"
            "- Check shared memory when your own memory doesn't have what you need\n\n"
        )
        # Inject memory summary (auto-generated synthesis) if available
        try:
            mem_summary = get_memory_summary(agent_id)
            if mem_summary:
                system_instruction += (
                    "MEMORY SUMMARY (auto-generated synthesis of recent activity — use as context):\n"
                    f"{mem_summary}\n\n"
                )
        except Exception:
            pass
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
                system_instruction += (
                    "\nPrioritize project-specific documents when answering. "
                    "Memory operations (store/recall) are scoped to this project.\n\n"
                )
        # Inject note context for AI-assisted note editing
        note_context = getattr(_thread_local, 'note_context', None)
        if note_context:
            # note_context format: "note_editing:/path/to/note.md"
            note_path = note_context.replace("note_editing:", "").strip() if note_context.startswith("note_editing:") else ""
            # Extract notes directory from note path for new note creation
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
        if _current_agent:
            skills = _current_agent.list_skills()
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

        if tools_guide:
            system_instruction += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"
        # Append plan mode prompt if active
        if getattr(_thread_local, 'plan_mode', False):
            system_instruction += PLAN_MODE_PROMPT
        if api_type == "openai":
            augmented_messages.insert(0, {"role": "system", "content": system_instruction})
        else:
            pass  # handled below via payload["system"]

    payload = {
        "model": model,
        "max_tokens": get_model_max_output(model),
        "messages": augmented_messages,
        "stream": True,
    }

    # Request usage stats in streaming responses (OpenAI-compatible APIs)
    if api_type == "openai":
        payload["stream_options"] = {"include_usage": True}

    # Apply inference parameters (temperature, top_p, thinking, etc.)
    if inference_params:
        provider = _models_config.get(model, {}).get("provider", "")
        _apply_inference_to_payload(payload, inference_params, api_type, provider)

    if tools:
        mcp_mgr = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
        if api_type == "openai":
            all_tools = list(TOOL_DEFINITIONS_OPENAI)
            if mcp_mgr:
                all_tools.extend(mcp_mgr.get_tool_definitions_openai())
            payload["tools"] = all_tools
        else:
            all_tools = list(TOOL_DEFINITIONS)
            if mcp_mgr:
                all_tools.extend(mcp_mgr.get_tool_definitions())
            payload["tools"] = all_tools
            payload["system"] = system_instruction

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=data, headers=headers, method="POST",
    )

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

    try:
        with urllib.request.urlopen(request) as response:
            if api_type == "openai":
                return _handle_openai_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, silent, tools, headers, endpoint, escape_watcher,
                    _tool_round, event_callback, inference_params, session_id)
            else:
                return _handle_anthropic_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, silent, tools, headers, endpoint, escape_watcher,
                    _tool_round, event_callback, inference_params, session_id)

    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None
        error_msg = f"HTTP Error {e.code}: {e.reason}"
        try:
            error_body = e.read().decode("utf-8")
            error_msg += f" — {error_body[:200]}"
        except:
            pass
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


def _handle_anthropic_response(response, payload, messages, model, api_key,
                                base_url, api_type, silent, tools,
                                headers, endpoint,
                                escape_watcher=None,
                                _tool_round: int = 0,
                                event_callback=None,
                                inference_params: dict | None = None,
                                session_id: str | None = None) -> str | None:
    """Handle Anthropic SSE response, including tool-use agentic loop."""
    # Parse the full SSE stream to get content blocks and stop reason
    collected_text = []
    tool_uses = []
    current_event = None
    current_block_type = None
    current_block = {}
    stop_reason = None
    _usage_in = 0
    _usage_out = 0

    for line in response:
        if escape_watcher and escape_watcher.cancelled:
            raise TaskCancelled()
        line = line.decode("utf-8").strip()
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            if current_event == "message_stop":
                break
            try:
                event = json.loads(line[6:])
                etype = event.get("type")

                if etype == "message_start":
                    msg = event.get("message", {})
                    usage = msg.get("usage", {})
                    _usage_in = usage.get("input_tokens", 0)

                elif etype == "error":
                    err = event.get("error", {})
                    err_msg = err.get("message", "Unknown API error")
                    err_type = err.get("type", "error")
                    if not silent:
                        print(f"\nAPI error: {err_msg}", file=sys.stderr)
                    if event_callback:
                        event_callback("error", {"message": f"{err_type}: {err_msg}"})
                    return None

                elif etype == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason")
                    usage = event.get("usage", {})
                    if usage.get("output_tokens"):
                        _usage_out = usage["output_tokens"]

                elif etype == "content_block_start":
                    block = event.get("content_block", {})
                    current_block_type = block.get("type")
                    if current_block_type == "tool_use":
                        current_block = {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input_json": "",
                        }
                    elif current_block_type == "text":
                        pass  # text handled via deltas

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = _unescape(delta.get("text", ""))
                        if not silent:
                            print(text, end="", flush=True)
                        if event_callback:
                            event_callback("text_delta", {"text": text})
                        collected_text.append(text)
                    elif delta.get("type") == "input_json_delta":
                        current_block["input_json"] += delta.get("partial_json", "")

                elif etype == "content_block_stop":
                    if current_block_type == "tool_use" and current_block:
                        try:
                            current_block["input"] = json.loads(current_block["input_json"])
                        except json.JSONDecodeError:
                            current_block["input"] = {}
                        tool_uses.append(current_block)
                        current_block = {}
                    current_block_type = None

            except json.JSONDecodeError:
                pass

    full_text = "".join(collected_text)

    # Log cost for this API call
    _log_call_cost(model, _usage_in, _usage_out, session_id, _tool_round)

    # End LLM trace span
    _llm_span = getattr(_thread_local, 'current_trace_span', None)
    if _trace_manager and _llm_span and _llm_span.get("type") == "llm_call":
        _trace_manager.end_span(_llm_span, status="ok",
                                 tokens_in=_usage_in, tokens_out=_usage_out)

    # If no tool calls, just return the text
    if not tool_uses:
        if not silent and full_text:
            print()
        # End request trace span if this is the final response
        _req_span = getattr(_thread_local, 'request_trace_span', None)
        if _trace_manager and _req_span:
            _trace_manager.end_span(_req_span, status="ok",
                                     tokens_in=_usage_in, tokens_out=_usage_out)
            _thread_local.request_trace_span = None
        return full_text

    # Tool use detected — execute tools and loop
    if full_text:
        print()

    # Build the assistant message content blocks
    assistant_content = []
    if full_text:
        assistant_content.append({"type": "text", "text": full_text})
    for tu in tool_uses:
        assistant_content.append({
            "type": "tool_use",
            "id": tu["id"],
            "name": tu["name"],
            "input": tu["input"],
        })

    # Add assistant message to conversation
    messages.append({"role": "assistant", "content": assistant_content})

    # Execute each tool and build tool_result messages
    tool_results = []
    for tu in tool_uses:
        _display_tool_call(tu["name"], tu["input"])
        if event_callback:
            event_callback("tool_call", {"name": tu["name"], "args": tu["input"]})
        # Store event_callback and tool_use_id for streaming tool output
        _thread_local.event_callback = event_callback
        _thread_local.tool_use_id = tu["id"]
        try:
            result = _execute_tool(tu["name"], tu["input"])
        finally:
            _thread_local.event_callback = None
            _thread_local.tool_use_id = None
        _display_tool_result(tu["name"], result)
        if event_callback:
            event_callback("tool_result", {"name": tu["name"], "result": result})

        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": result,
        })

    messages.append({"role": "user", "content": tool_results})

    # Recurse to get the model's final response (or more tool calls)
    return send_message(messages, model, api_key, base_url, api_type,
                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                        _tool_round=_tool_round + 1, event_callback=event_callback,
                        inference_params=inference_params, session_id=session_id)


def _handle_openai_response(response, payload, messages, model, api_key,
                             base_url, api_type, silent, tools,
                             headers, endpoint,
                             escape_watcher=None,
                             _tool_round: int = 0,
                             event_callback=None,
                             inference_params: dict | None = None,
                             session_id: str | None = None) -> str | None:
    """Handle OpenAI SSE response, including tool-use agentic loop."""
    collected_text = []
    tool_calls_map = {}  # index -> {id, name, arguments_str}
    _usage_in = 0
    _usage_out = 0

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
            choices = event.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if content:
                content = _unescape(content)
                if not silent:
                    print(content, end="", flush=True)
                if event_callback:
                    event_callback("text_delta", {"text": content})
                collected_text.append(content)

            # Accumulate tool calls
            for tc in delta.get("tool_calls", []):
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

    full_text = "".join(collected_text)

    # Log cost for this API call
    _log_call_cost(model, _usage_in, _usage_out, session_id, _tool_round)

    # End LLM trace span (OpenAI handler)
    _llm_span_oai = getattr(_thread_local, 'current_trace_span', None)
    if _trace_manager and _llm_span_oai and _llm_span_oai.get("type") == "llm_call":
        _trace_manager.end_span(_llm_span_oai, status="ok",
                                 tokens_in=_usage_in, tokens_out=_usage_out)

    if not tool_calls_map:
        if not silent and full_text:
            print()
        # End request trace span for final response
        _req_span_oai = getattr(_thread_local, 'request_trace_span', None)
        if _trace_manager and _req_span_oai:
            _trace_manager.end_span(_req_span_oai, status="ok",
                                     tokens_in=_usage_in, tokens_out=_usage_out)
            _thread_local.request_trace_span = None
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

    # Execute tools
    for tc in tc_list:
        try:
            args = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_name = tc["function"]["name"]
        _display_tool_call(tool_name, args)
        if event_callback:
            event_callback("tool_call", {"name": tool_name, "args": args})
        # Store event_callback and tool_use_id for streaming tool output
        _thread_local.event_callback = event_callback
        _thread_local.tool_use_id = tc["id"]
        try:
            result = _execute_tool(tool_name, args)
        finally:
            _thread_local.event_callback = None
            _thread_local.tool_use_id = None
        _display_tool_result(tool_name, result)
        if event_callback:
            event_callback("tool_result", {"name": tool_name, "result": result})

        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    return send_message(messages, model, api_key, base_url, api_type,
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


def _retry_with_backoff(messages, model, api_key, base_url, api_type,
                        silent, tools, escape_watcher, event_callback,
                        inference_params, session_id, max_retries=2):
    """Try sending a message with exponential backoff retries for transient errors.

    Returns (result, last_error_info). result is None if all retries failed.
    """
    _thread_local._last_send_error = None
    result = send_message(messages, model, api_key, base_url, api_type,
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
            from claude_cli import TaskCancelled
            raise TaskCancelled()

        _thread_local._last_send_error = None
        result = send_message(messages, model, api_key, base_url, api_type,
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
                               base_url: str, api_type: str,
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
    If provider_resolver is provided, it's called with (model) -> {api_key, base_url, api_type}.
    Emits ("fallback", {...}) events via event_callback for UI display.
    """
    # Track which model actually responded (for done event)
    _thread_local._fallback_model_used = None

    # Try primary model with retries
    result, error_info = _retry_with_backoff(
        messages, model, api_key, base_url, api_type,
        silent, tools, escape_watcher, event_callback,
        inference_params, session_id, max_retries=2)
    if result is not None:
        return result

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
        fallback_models = get_available_models(api_key, base_url, api_type)

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
        fb_api_key, fb_base_url, fb_api_type = api_key, base_url, api_type
        if provider_resolver:
            try:
                prov = provider_resolver(fallback_model)
                fb_api_key = prov.get("api_key", api_key)
                fb_base_url = prov.get("base_url", base_url)
                fb_api_type = prov.get("api_type", api_type)
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
        result, fb_error = _retry_with_backoff(
            messages, fallback_model, fb_api_key, fb_base_url, fb_api_type,
            silent, tools, escape_watcher, event_callback,
            fb_params, session_id, max_retries=1)
        if result is not None:
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

def main():
    parser = argparse.ArgumentParser(
        description=f"Brain Agent v{VERSION} — Agentic CLI for LLM APIs"
    )
    parser.add_argument(
        "message", nargs="?",
        help="Message to send (or use -i for interactive mode)",
    )
    parser.add_argument(
        "-m", "--model", default="claude-opus-4-5-20251101",
        help="Model to use (default: claude-opus-4-5-20251101)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Interactive mode - continuous chat",
    )
    parser.add_argument(
        "-l", "--list-models", action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "--api-key", default="sk-Xk7kOHpIpZkLutwnyxHpRO9jn4ZwyPaS",
        help="API key for authentication",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8317/v1",
        help="Base URL for the API (default: http://localhost:8317/v1)",
    )
    parser.add_argument(
        "-t", "--api-type", choices=["anthropic", "openai"], default="anthropic",
        help="API type: anthropic or openai (default: anthropic)",
    )
    parser.add_argument(
        "--max-context", type=int, default=DEFAULT_MAX_CONTEXT_TOKENS,
        help=f"Max context window in tokens (default: {DEFAULT_MAX_CONTEXT_TOKENS})",
    )
    parser.add_argument(
        "--agent", default="main",
        help="Agent ID to start with (default: 'main')",
    )

    args = parser.parse_args()

    if args.list_models:
        list_models(args.api_key, args.base_url, args.api_type)
        sys.exit(0)

    if args.interactive:
        _run_interactive(args)
        sys.exit(0)

    # Single-message mode
    if not args.message:
        parser.print_help()
        sys.exit(1)

    messages = [{"role": "user", "content": args.message}]
    reply = send_message_with_fallback(
        messages, args.model, args.api_key, args.base_url, args.api_type,
        silent=True)
    if reply:
        print(render_markdown(reply))


def _print_greeting(model: str, agent_id: str = "default") -> None:
    """Print the Brain Agent startup banner."""
    cwd = os.getcwd()
    latest = CHANGELOG[0] if CHANGELOG else None

    # Color definitions for gradient brain
    C1 = "\033[38;5;213m"  # pink
    C2 = "\033[38;5;177m"  # purple
    C3 = "\033[38;5;141m"  # lavender
    C4 = "\033[38;5;105m"  # blue-purple
    C5 = "\033[38;5;69m"   # blue
    C6 = "\033[38;5;33m"   # deep blue
    CO = "\033[38;5;208m"  # orange accent

    brain = [
        f"  {C1}    ⣀⣀⣤⣤⣤⣤⣤⣤⣀⣀    {RESET}",
        f"  {C1}  ⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦  {RESET}",
        f"  {C2} ⣾⣿⣿⡟⠛⠛⣿⣿⡟⠛⠛⣿⣿⣿⣷ {RESET}",
        f"  {C2}⣸⣿⣿⡇  ⣸⣿⣿⡇  ⢸⣿⣿⣿⣇{RESET}",
        f"  {C3}⣿⣿⣿⣇  ⣿⣿⣿⣇  ⣸⣿⣿⣿⣿{RESET}",
        f"  {C3}⢿⣿⣿⣿⣦⣤⣿⣿⣿⣦⣤⣾⣿⣿⣿⡿{RESET}",
        f"  {C4} ⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟ {RESET}",
        f"  {C5}  ⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋  {RESET}",
        f"  {C6}    ⠉⠛⠿⣿⣿⣿⠿⠛⠉    {RESET}",
    ]

    # Title with gradient
    title = (
        f"  {C1}B{C2}r{C3}a{C4}i{C5}n{RESET} "
        f"{CO}{BOLD}Agent{RESET}"
    )
    ver = f" {DIM}v{VERSION}{RESET}"
    agent_label = f" {DIM}│{RESET} {CYAN}{BOLD}{agent_id}{RESET}" if agent_id != "main" else ""

    print()
    for line in brain:
        print(line)
    print()
    print(f"  {title}{ver}{agent_label}")
    print()

    # Info section with dim separators
    sep = f"{DIM}·{RESET}"
    print(f"  {DIM}Model{RESET}  {GREEN}{model}{RESET}")
    print(f"  {DIM}Path{RESET}   {DIM}{cwd}{RESET}")

    # Agents
    agents = list_agents()
    if len(agents) > 1:
        agent_list = []
        for a in agents:
            if a == agent_id:
                agent_list.append(f"{CYAN}{BOLD}{a}{RESET}")
            else:
                agent_list.append(f"{DIM}{a}{RESET}")
        print(f"  {DIM}Agents{RESET} {' {0} '.format(sep).join(agent_list)}")

    # Skills count
    if _current_agent:
        skills = _current_agent.list_skills()
        if skills:
            skill_names = [s["name"] for s in skills[:5]]
            more = f" {DIM}+{len(skills)-5} more{RESET}" if len(skills) > 5 else ""
            print(f"  {DIM}Skills{RESET} {DIM}{', '.join(skill_names)}{more}{RESET}")

    # Scheduled tasks count
    if _scheduler:
        schedules = _scheduler.list_all()
        active = sum(1 for s in schedules if s["enabled"])
        if schedules:
            print(f"  {DIM}Tasks{RESET}  {DIM}{active} scheduled ({len(schedules)} total){RESET}")

    print()
    print(f"  {DIM}Commands  /new /agent /model /models /tools /schedule{RESET}")
    print(f"  {DIM}Controls  Esc cancel · o toggle tools · exit quit{RESET}")

    if latest:
        print(f"\n  {DIM}↑ v{latest[0]}: {latest[2]}{RESET}")

    print()


# --- Slash commands registry ---

SLASH_COMMANDS = {
    "/help":     "Show this help",
    "/new":      "Start a new conversation",
    "/agent":    "Switch agent or list agents",
    "/model":    "Switch model",
    "/models":   "List available models",
    "/tools":    "Toggle tool call display",
    "/schedule": "Manage scheduled tasks (list/add/pause/resume/delete/history)",
}


def _print_help() -> None:
    """Print help for all slash commands."""
    print()
    print(f"  {BOLD}Commands{RESET}")
    print()
    for cmd, desc in SLASH_COMMANDS.items():
        print(f"  {GREEN}{BOLD}{cmd:12s}{RESET} {DIM}{desc}{RESET}")
    print()
    print(f"  {BOLD}Schedule subcommands{RESET}")
    print()
    for sub, desc in [
        ("list", "List all scheduled tasks"),
        ("add", "Create a new scheduled task"),
        ("pause NAME", "Pause a task"),
        ("resume NAME", "Resume a task"),
        ("delete NAME", "Delete a task"),
        ("history", "Show execution history"),
    ]:
        print(f"  {GREEN}{BOLD}  {sub:16s}{RESET} {DIM}{desc}{RESET}")
    print()
    print(f"  {BOLD}Keyboard{RESET}")
    print()
    for key, desc in [
        ("Esc", "Cancel current request"),
        ("Tab", "Autocomplete slash commands"),
        ("Up/Down", "Input history"),
        ("Ctrl+A/E", "Beginning/end of line"),
        ("Ctrl+W", "Delete word backward"),
        ("Ctrl+U", "Clear line"),
        ("o", "Toggle tool output (when hidden)"),
    ]:
        print(f"  {YELLOW}{key:12s}{RESET} {DIM}{desc}{RESET}")
    print()


def _select_menu(items: list[str], prompt: str = "Select",
                 labels: list[str] | None = None,
                 active: str | None = None) -> str | None:
    """Interactive arrow-key selection menu. Returns selected item or None on Esc/Ctrl-C."""
    if not items:
        return None
    display = labels if labels and len(labels) == len(items) else items
    selected = 0
    # Find active item
    if active and active in items:
        selected = items.index(active)

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return None

    # Count lines we'll draw so we can clean up
    menu_lines = len(items)

    try:
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ICANON | termios.ECHO)
        new_settings[6][termios.VMIN] = 0
        new_settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)

        # Draw initial menu
        print(f"\n  {DIM}{prompt}:{RESET}")
        for i, label in enumerate(display):
            _draw_menu_item(i, label, i == selected, items[i] == active if active else False)
        sys.stdout.flush()

        while True:
            if select.select([fd], [], [], 0.1)[0]:
                ch = os.read(fd, 1)
                if ch == b'\r' or ch == b'\n':  # Enter
                    break
                elif ch == b'\x1b':  # Escape or arrow
                    seq1 = os.read(fd, 1) if select.select([fd], [], [], 0.05)[0] else b''
                    if seq1 == b'[':
                        seq2 = os.read(fd, 1) if select.select([fd], [], [], 0.05)[0] else b''
                        if seq2 == b'A':  # Up
                            selected = (selected - 1) % len(items)
                        elif seq2 == b'B':  # Down
                            selected = (selected + 1) % len(items)
                        else:
                            # Bare Esc or unknown sequence — cancel
                            _erase_menu(menu_lines + 1)
                            return None
                    else:
                        # Bare Esc — cancel
                        _erase_menu(menu_lines + 1)
                        return None
                elif ch == b'\x03':  # Ctrl-C
                    _erase_menu(menu_lines + 1)
                    return None
                else:
                    continue

                # Redraw menu
                # Move cursor up to start of menu
                sys.stdout.write(f"\033[{menu_lines}A")
                for i, label in enumerate(display):
                    sys.stdout.write(f"\r\033[K")
                    _draw_menu_item(i, label, i == selected, items[i] == active if active else False)
                sys.stdout.flush()

        # Erase menu after selection
        _erase_menu(menu_lines + 1)
        return items[selected]

    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass


def _draw_menu_item(idx: int, label: str, is_selected: bool, is_active: bool):
    """Draw a single menu item line."""
    marker = f"{CYAN}{BOLD}❯{RESET}" if is_selected else " "
    active_tag = f" {GREEN}(active){RESET}" if is_active else ""
    if is_selected:
        print(f"  {marker} {BOLD}{label}{RESET}{active_tag}")
    else:
        print(f"  {marker} {DIM}{label}{RESET}{active_tag}")


def _erase_menu(lines: int):
    """Erase N lines above cursor."""
    for _ in range(lines):
        sys.stdout.write(f"\033[A\r\033[K")
    sys.stdout.flush()


def _readline(prompt: str, input_history: list[str], history_idx_ref: list[int],
              completions: list[str] | None = None) -> str | None:
    """Read a line with arrow-key history navigation and inline editing.

    Returns the entered string, or None on Ctrl-C / Ctrl-D.
    input_history is a list of previous inputs (newest last).
    history_idx_ref is a single-element list holding the current browse index.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write(prompt)
        sys.stdout.flush()

        buf = []        # current line characters
        pos = 0         # cursor position within buf
        saved_line = "" # stash current input when browsing history
        hist_idx = len(input_history)  # start past the end (= new input)

        while True:
            ch = os.read(fd, 1)

            if ch == b'\r' or ch == b'\n':  # Enter
                # Move cursor to end, print newline
                if pos < len(buf):
                    sys.stdout.write(f"\033[{len(buf) - pos}C")
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(buf)

            elif ch == b'\x03':  # Ctrl-C
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return None

            elif ch == b'\x04':  # Ctrl-D
                if not buf:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return None
                # Otherwise ignore

            elif ch == b'\x7f' or ch == b'\x08':  # Backspace
                if pos > 0:
                    buf.pop(pos - 1)
                    pos -= 1
                    # Redraw from cursor position
                    tail = "".join(buf[pos:])
                    sys.stdout.write(f"\033[D{tail} \033[{len(tail) + 1}D")
                    sys.stdout.flush()

            elif ch == b'\x15':  # Ctrl-U: clear line
                if pos > 0:
                    sys.stdout.write(f"\033[{pos}D")
                sys.stdout.write(" " * len(buf))
                if len(buf) > 0:
                    sys.stdout.write(f"\033[{len(buf)}D")
                buf.clear()
                pos = 0
                sys.stdout.flush()

            elif ch == b'\x01':  # Ctrl-A: beginning of line
                if pos > 0:
                    sys.stdout.write(f"\033[{pos}D")
                    pos = 0
                    sys.stdout.flush()

            elif ch == b'\x05':  # Ctrl-E: end of line
                if pos < len(buf):
                    sys.stdout.write(f"\033[{len(buf) - pos}C")
                    pos = len(buf)
                    sys.stdout.flush()

            elif ch == b'\x17':  # Ctrl-W: delete word backward
                if pos > 0:
                    old_pos = pos
                    # Skip trailing spaces
                    while pos > 0 and buf[pos - 1] == ' ':
                        pos -= 1
                    # Skip word
                    while pos > 0 and buf[pos - 1] != ' ':
                        pos -= 1
                    deleted = old_pos - pos
                    del buf[pos:old_pos]
                    sys.stdout.write(f"\033[{deleted}D")
                    tail = "".join(buf[pos:])
                    sys.stdout.write(f"{tail}{' ' * deleted}")
                    sys.stdout.write(f"\033[{len(tail) + deleted}D")
                    sys.stdout.flush()

            elif ch == b'\x1b':  # Escape sequence
                seq1 = os.read(fd, 1)
                if seq1 == b'[':
                    seq2 = os.read(fd, 1)

                    if seq2 == b'A':  # Up arrow
                        if hist_idx > 0:
                            if hist_idx == len(input_history):
                                saved_line = "".join(buf)
                            hist_idx -= 1
                            _replace_line(buf, pos, input_history[hist_idx])
                            buf[:] = list(input_history[hist_idx])
                            pos = len(buf)

                    elif seq2 == b'B':  # Down arrow
                        if hist_idx < len(input_history):
                            hist_idx += 1
                            if hist_idx == len(input_history):
                                _replace_line(buf, pos, saved_line)
                                buf[:] = list(saved_line)
                            else:
                                _replace_line(buf, pos, input_history[hist_idx])
                                buf[:] = list(input_history[hist_idx])
                            pos = len(buf)

                    elif seq2 == b'C':  # Right arrow
                        if pos < len(buf):
                            sys.stdout.write("\033[C")
                            pos += 1
                            sys.stdout.flush()

                    elif seq2 == b'D':  # Left arrow
                        if pos > 0:
                            sys.stdout.write("\033[D")
                            pos -= 1
                            sys.stdout.flush()

                    elif seq2 == b'H':  # Home
                        if pos > 0:
                            sys.stdout.write(f"\033[{pos}D")
                            pos = 0
                            sys.stdout.flush()

                    elif seq2 == b'F':  # End
                        if pos < len(buf):
                            sys.stdout.write(f"\033[{len(buf) - pos}C")
                            pos = len(buf)
                            sys.stdout.flush()

                    elif seq2 == b'3':  # Delete key (ESC [ 3 ~)
                        seq3 = os.read(fd, 1)  # consume '~'
                        if pos < len(buf):
                            buf.pop(pos)
                            tail = "".join(buf[pos:])
                            sys.stdout.write(f"{tail} \033[{len(tail) + 1}D")
                            sys.stdout.flush()

                # Bare Escape — ignore (don't break input)

            elif ch == b'\t':  # Tab — autocomplete
                current = "".join(buf)
                comp_list = completions or list(SLASH_COMMANDS.keys())
                if current.startswith("/"):
                    matches = [c for c in comp_list if c.startswith(current)]
                    if len(matches) == 1:
                        # Single match — complete it
                        completion = matches[0]
                        # Add space after completed command
                        if not completion.endswith(" "):
                            completion += " "
                        _replace_line(buf, pos, completion)
                        buf[:] = list(completion)
                        pos = len(buf)
                    elif len(matches) > 1:
                        # Multiple matches — find common prefix
                        prefix = os.path.commonprefix(matches)
                        if len(prefix) > len(current):
                            _replace_line(buf, pos, prefix)
                            buf[:] = list(prefix)
                            pos = len(buf)
                        else:
                            # Show options briefly below
                            sys.stdout.write(f"\n  {DIM}{' '.join(matches)}{RESET}")
                            sys.stdout.write(f"\033[A")  # move back up
                            # Redraw prompt + buffer
                            sys.stdout.write(f"\r{prompt}{''.join(buf)}")
                            sys.stdout.flush()

            elif ch >= b' ':  # Printable character
                char = ch.decode("utf-8", errors="replace")
                buf.insert(pos, char)
                pos += 1
                tail = "".join(buf[pos:])
                sys.stdout.write(f"{char}{tail}")
                if tail:
                    sys.stdout.write(f"\033[{len(tail)}D")
                sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _replace_line(buf: list, pos: int, new_text: str) -> None:
    """Clear the current input line and write new_text."""
    # Move cursor to start of input
    if pos > 0:
        sys.stdout.write(f"\033[{pos}D")
    # Clear old content
    sys.stdout.write(" " * len(buf))
    if len(buf) > 0:
        sys.stdout.write(f"\033[{len(buf)}D")
    # Write new content
    sys.stdout.write(new_text)
    sys.stdout.flush()



def _switch_agent(agent_id: str, args) -> tuple[str, AgentConfig]:
    """Switch to a different agent. Returns (model, agent_config)."""
    global _current_agent, _memory_store, _mcp_manager
    agent = AgentConfig(agent_id)
    _current_agent = agent
    _memory_store = MemoryStore(agent_id=agent_id, base_dir=agent.memory_dir)

    # Load MCP servers: main's (global) + agent-specific
    if _mcp_manager:
        _mcp_manager.stop_all()
    _mcp_manager = MCPManager()
    main_mcp = os.path.join(AGENTS_DIR, "main", "mcp.json")
    _mcp_manager.load_config(main_mcp)
    if agent_id != "main":
        _mcp_manager.load_config(agent.mcp_config_path)

    model = agent.preferred_model or args.model
    return model, agent


def _run_interactive(args):
    """Run the interactive TUI chat loop."""
    global _memory_store, _current_agent
    global _delegate_fallback_model, _delegate_api_key, _delegate_base_url, _delegate_api_type

    history = []
    input_history = []   # list of previous user inputs for arrow-key recall
    history_idx = [0]    # mutable ref for current position

    # Store API config for delegation
    _delegate_api_key = args.api_key
    _delegate_base_url = args.base_url
    _delegate_api_type = args.api_type
    _delegate_fallback_model = args.model

    # Initialize agent
    current_model, _ = _switch_agent(args.agent, args)

    # Start scheduler
    global _scheduler
    _scheduler = Scheduler()
    _scheduler.start()

    # Ensure memory summary schedules
    try:
        ensure_memory_summary_schedules()
    except Exception:
        pass

    # Initialize background task runner
    global _task_runner
    _task_runner = TaskRunner()

    # Clear screen and move cursor to top
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    _setup_scroll_region()
    _draw_status_bar(current_model, history, args.max_context)

    # Handle terminal resize
    def _on_resize(signum, frame):
        _setup_scroll_region()
        _draw_status_bar(current_model, history, args.max_context)

    old_sigwinch = signal.signal(signal.SIGWINCH, _on_resize)

    # Startup greeting
    _print_greeting(current_model, args.agent)

    try:
        while True:
            # Read input with history support
            message = _readline(f"\n{BOLD}{GREEN}❯{RESET} ", input_history, history_idx)
            if message is None:
                print(f"{DIM}Bye!{RESET}")
                break

            stripped = message.strip().lower()

            if stripped in ("exit", "quit"):
                print(f"{DIM}Bye!{RESET}")
                break

            if stripped == "/help":
                _print_help()
                continue

            if stripped == "/new":
                history = []
                print(f"\n{DIM}{'─' * 40}{RESET}")
                print(f"{DIM}  New chat started{RESET}")
                print(f"{DIM}{'─' * 40}{RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if stripped == "/tools":
                global _show_tools
                _show_tools = not _show_tools
                state = f"{GREEN}visible{RESET}" if _show_tools else f"{DIM}hidden{RESET}"
                print(f"  {DIM}Tool display:{RESET} {state}")
                continue

            if stripped.startswith("/agent"):
                arg = message.strip()[6:].strip()
                agents = list_agents()
                if arg:
                    if arg not in agents:
                        print(f"  {DIM}Creating new agent:{RESET} {BOLD}{arg}{RESET}")
                    current_model, _ = _switch_agent(arg, args)
                    history = []
                    print(f"  {DIM}Switched to agent:{RESET} {BOLD}{arg}{RESET} {DIM}(model: {current_model}){RESET}")
                else:
                    # Build labels with descriptions
                    labels = []
                    for aid in agents:
                        cfg = AgentConfig(aid)
                        model_info = f" [{cfg.preferred_model}]" if cfg.preferred_model else ""
                        labels.append(f"{aid}{model_info} — {cfg.description}")
                    current_aid = _current_agent.agent_id if _current_agent else "main"
                    choice = _select_menu(agents, "Select agent", labels=labels, active=current_aid)
                    if choice:
                        current_model, _ = _switch_agent(choice, args)
                        history = []
                        print(f"  {DIM}Switched to agent:{RESET} {BOLD}{choice}{RESET} {DIM}(model: {current_model}){RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if stripped.startswith("/schedule"):
                arg = message.strip()[9:].strip()
                if not _scheduler:
                    print(f"  {DIM}Scheduler not running{RESET}")
                    continue

                if arg == "" or arg == "list":
                    # List schedules
                    schedules = _scheduler.list_all()
                    if not schedules:
                        print(f"\n  {DIM}No scheduled tasks{RESET}")
                    else:
                        print()
                        for s in schedules:
                            status = f"{GREEN}active{RESET}" if s["enabled"] else f"{DIM}paused{RESET}"
                            next_r = s.get("next_run", "")[:16] if s.get("next_run") else "—"
                            print(f"  {BOLD}{s['name']}{RESET} [{status}] {DIM}{s['schedule']}{RESET}")
                            print(f"    {DIM}agent:{RESET} {s['agent']}  {DIM}next:{RESET} {next_r}")
                            print(f"    {DIM}task:{RESET} {s['task'][:60]}")
                    continue

                elif arg == "add":
                    # Interactive add
                    print(f"\n  {DIM}Add scheduled task{RESET}")
                    name = _readline(f"  {DIM}Name:{RESET} ", [], [0])
                    if not name or not name.strip():
                        continue
                    name = name.strip()
                    task = _readline(f"  {DIM}Task:{RESET} ", [], [0])
                    if not task or not task.strip():
                        continue
                    task = task.strip()
                    schedule = _readline(f"  {DIM}Schedule (every Xm/Xh/Xd, daily HH:MM, weekly DOW HH:MM):{RESET} ", [], [0])
                    if not schedule or not schedule.strip():
                        continue
                    schedule = schedule.strip()
                    agent = _readline(f"  {DIM}Agent (default: main):{RESET} ", [], [0])
                    agent = agent.strip() if agent and agent.strip() else "main"
                    model_in = _readline(f"  {DIM}Model (default: current):{RESET} ", [], [0])
                    model_val = model_in.strip() if model_in and model_in.strip() else None

                    result = _scheduler.add(name, task, schedule, agent, model_val)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {GREEN}✔ Created:{RESET} {BOLD}{name}{RESET} — next run: {result.get('next_run', '')[:16]}")
                    continue

                elif arg.startswith("pause "):
                    name = arg[6:].strip()
                    result = _scheduler.pause(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {DIM}Paused:{RESET} {name}")
                    continue

                elif arg.startswith("resume "):
                    name = arg[7:].strip()
                    result = _scheduler.resume(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {GREEN}Resumed:{RESET} {name} — next: {result.get('next_run', '')[:16]}")
                    continue

                elif arg.startswith("delete ") or arg.startswith("rm "):
                    name = arg.split(" ", 1)[1].strip()
                    result = _scheduler.remove(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {DIM}Deleted:{RESET} {name}")
                    continue

                elif arg == "history":
                    history_items = _scheduler.get_history(limit=10)
                    if not history_items:
                        print(f"\n  {DIM}No execution history{RESET}")
                    else:
                        print()
                        for h in history_items:
                            status_color = GREEN if h["status"] == "success" else RED
                            print(f"  {status_color}{h['status']}{RESET} {BOLD}{h['schedule_name']}{RESET} {DIM}({h['finished_at'][:16]}){RESET}")
                            if h.get("result"):
                                preview = h["result"][:80].replace("\n", " ")
                                print(f"    {DIM}{preview}{RESET}")
                    continue

                else:
                    print(f"  {DIM}Usage: /schedule [list|add|pause NAME|resume NAME|delete NAME|history]{RESET}")
                    continue

            if stripped == "/models":
                models = get_available_models(args.api_key, args.base_url, args.api_type)
                if models:
                    choice = _select_menu(models, "Select model", active=current_model)
                    if choice:
                        current_model = choice
                        print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                        _draw_status_bar(current_model, history, args.max_context)
                else:
                    print(f"  {DIM}No models available{RESET}")
                continue

            if stripped.startswith("/model"):
                arg = message.strip()[6:].strip()
                models = get_available_models(args.api_key, args.base_url, args.api_type)
                if arg:
                    current_model = arg
                    print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                elif models:
                    choice = _select_menu(models, "Select model", active=current_model)
                    if choice:
                        current_model = choice
                        print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                else:
                    print(f"  {DIM}No models available. Use: /model <name>{RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if not message.strip():
                continue

            # Save to input history (dedup consecutive)
            if not input_history or input_history[-1] != message.strip():
                input_history.append(message.strip())

            # Send message
            history.append({"role": "user", "content": message})
            _reset_tool_tracking()

            # Check context window and compact if needed
            history, was_compacted = _check_and_compact(
                history, current_model, args.api_key, args.base_url,
                args.api_type, max_tokens=args.max_context,
            )

            # Start spinner and escape watcher
            spinner = Spinner(current_model)
            escape_watcher = EscapeWatcher()
            spinner.start()
            escape_watcher.start()

            cancelled = False
            try:
                reply = send_message_with_fallback(
                    history, current_model, args.api_key, args.base_url,
                    args.api_type, silent=True, escape_watcher=escape_watcher)
            except TaskCancelled:
                cancelled = True
                reply = None
            finally:
                elapsed = spinner.stop()
                escape_watcher.stop()

            if cancelled:
                # Remove the user message that was cancelled
                history.pop()
                print(f"\n{DIM}✘ Cancelled (Esc){RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if reply:
                history.append({"role": "assistant", "content": reply})

                # Render formatted response
                print()
                rendered = render_markdown(reply)
                print(rendered)

                # Completion message
                verb = spinner.verb.rstrip("ing")
                # Make past tense
                past = spinner.verb[:-3] + "ed" if spinner.verb.endswith("ing") else spinner.verb
                if spinner.verb == "Thinking":
                    past = "Thought"
                elif spinner.verb == "Weaving":
                    past = "Woven"
                elif spinner.verb == "Computing":
                    past = "Computed"
                elif spinner.verb == "Brewing":
                    past = "Brewed"
                elif spinner.verb == "Baking":
                    past = "Baked"
                elif spinner.verb == "Crafting":
                    past = "Crafted"
                elif spinner.verb == "Conjuring":
                    past = "Conjured"
                elif spinner.verb == "Composing":
                    past = "Composed"
                elif spinner.verb == "Contemplating":
                    past = "Contemplated"
                elif spinner.verb == "Pondering":
                    past = "Pondered"

                print(f"\n{DIM}✻ {past} for {elapsed:.0f}s{RESET}")
            else:
                print(f"\n{DIM}(no response){RESET}")

            # Restore status bar after response
            _draw_status_bar(current_model, history, args.max_context)

    finally:
        if _scheduler:
            _scheduler.stop()
        if _mcp_manager:
            _mcp_manager.stop_all()
        signal.signal(signal.SIGWINCH, old_sigwinch)
        _restore_scroll_region()


if __name__ == "__main__":
    main()
