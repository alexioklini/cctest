#!/usr/bin/env python3
"""Brain Agent — Agentic CLI for interacting with LLM APIs."""

VERSION = "0.8.0"
VERSION_DATE = "2026-03-14"
CHANGELOG = [
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
import fnmatch
import glob as globmod
import json
import os
import random
import re
import select
import signal
import shutil
import subprocess
import sys
import termios
import threading
import time
import tty
import urllib.request
import urllib.error


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
            "Returns matching memories ranked by relevance."
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
            "Access the main agent's shared memory. This contains global knowledge like "
            "infrastructure details, user preferences, project-wide decisions, and reference data "
            "that applies across all agents. Use this when you need context that isn't in your own memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform", "enum": ["recall", "store"]},
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
            "Delegate a task to another agent. The target agent runs in its own context "
            "with its own soul.md, tools, and memory. Returns the agent's final response. "
            "Use this when a task is better suited to a specialized agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent ID (e.g. 'research', 'health')"},
                "task": {"type": "string", "description": "Task description for the target agent"},
            },
            "required": ["agent", "task"],
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


def tool_read_file(args: dict) -> str:
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


def tool_write_file(args: dict) -> str:
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
        return _ok({"path": path, "replacements": count if replace_all else 1, "status": "edited"})
    except Exception as e:
        return _err(f"edit_file: {e}")


def tool_list_directory(args: dict) -> str:
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


def tool_execute_command(args: dict) -> str:
    command = args.get("command", "")
    cwd = args.get("cwd")
    timeout = args.get("timeout", 15)
    try:
        if cwd:
            cwd = os.path.expanduser(cwd)

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


def tool_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    method = args.get("method", "GET")
    headers = args.get("headers", {})
    body = args.get("body")
    max_length = args.get("max_length", 50000)
    try:
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req_headers.update(headers)
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        return _ok({"url": url, "status": resp.status, "length": len(text), "content": text})
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:5000]
        except Exception:
            pass
        return _err(f"web_fetch: HTTP {e.code} {e.reason}\n{body_text}")
    except Exception as e:
        return _err(f"web_fetch: {e}")


def exa_search(query: str, num_results: int = 5, category: str | None = None) -> str:
    """Execute an Exa web search and return JSON results. Uses stdlib only."""
    api_key = os.environ.get("EXA_API_KEY", "97dbd594-f7b4-4866-9a8e-6a297e3df576")

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


# --- Agent System ---

AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")


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
                    "max_context": None,
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
        return self.config.get("model")

    @property
    def max_context(self) -> int | None:
        return self.config.get("max_context")

    @property
    def memory_dir(self) -> str:
        return self.dir  # memory.db lives alongside soul.md


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
    """Get agent ID + description + soul summary for all agents."""
    result = []
    for agent_id in list_agents():
        cfg = AgentConfig(agent_id)
        # Extract first meaningful paragraph from soul.md as capability summary
        soul = cfg.soul.strip()
        summary = ""
        for line in soul.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                summary = line
                break
        result.append({
            "id": agent_id,
            "description": cfg.description,
            "soul_summary": summary,
            "model": cfg.preferred_model,
        })
    return result


def build_agent_registry() -> str:
    """Build a text block describing all agents for injection into system prompts."""
    agents = get_agent_summaries()
    if len(agents) <= 1:
        return ""
    lines = ["AGENT REGISTRY — use delegate_task to send tasks to specialized agents:"]
    for a in agents:
        model_note = f" (model: {a['model']})" if a.get("model") else ""
        desc = a.get("description", "")
        soul = a.get("soul_summary", "")
        detail = soul if soul else desc
        lines.append(f"  - {a['id']}: {detail}{model_note}")
    lines.append("")
    lines.append(
        "Before performing a task, consider if another agent is better suited. "
        "Delegate when the task clearly matches another agent's specialty. "
        "Do NOT delegate simple tasks you can handle yourself."
    )
    return "\n".join(lines)


# Current active agent (set in _run_interactive)
_current_agent: AgentConfig | None = None


# --- Memory System (SQLite FTS5) ---

import sqlite3
import hashlib

class MemoryStore:
    """Per-agent memory store backed by SQLite FTS5 and markdown files."""

    def __init__(self, agent_id: str = "main", base_dir: str | None = None):
        self.agent_id = agent_id
        if base_dir:
            self.dir = base_dir
        else:
            self.dir = os.path.join(AGENTS_DIR, agent_id)
        os.makedirs(self.dir, exist_ok=True)
        self.db_path = os.path.join(self.dir, "memory.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    type TEXT,
                    content TEXT NOT NULL,
                    file_path TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    name, description, type, content,
                    content=memories,
                    content_rowid=rowid
                )
            """)
            # Triggers to keep FTS in sync
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, name, description, type, content)
                    VALUES (new.rowid, new.name, new.description, new.type, new.content);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, name, description, type, content)
                    VALUES ('delete', old.rowid, old.name, old.description, old.type, old.content);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, name, description, type, content)
                    VALUES ('delete', old.rowid, old.name, old.description, old.type, old.content);
                    INSERT INTO memories_fts(rowid, name, description, type, content)
                    VALUES (new.rowid, new.name, new.description, new.type, new.content);
                END
            """)
            conn.commit()

    def _make_id(self, name: str) -> str:
        """Generate a stable ID from name."""
        return hashlib.sha256(name.encode()).hexdigest()[:12]

    def _name_to_filename(self, name: str) -> str:
        """Convert a memory name to a safe filename."""
        safe = re.sub(r'[^\w\s-]', '', name).strip().lower()
        safe = re.sub(r'[\s]+', '_', safe)
        return safe[:60] + ".md"

    def store(self, name: str, content: str, description: str = "",
              mem_type: str = "general") -> dict:
        """Store or update a memory. Also writes a .md file."""
        mem_id = self._make_id(name)
        filename = self._name_to_filename(name)
        file_path = os.path.join(self.dir, filename)

        # Write markdown file with frontmatter
        md_content = f"""---
name: {name}
description: {description}
type: {mem_type}
agent: {self.agent_id}
---

{content}
"""
        with open(file_path, "w") as f:
            f.write(md_content)

        # Upsert into database
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE memories SET name=?, description=?, type=?, content=?,
                    file_path=?, updated_at=datetime('now') WHERE id=?
                """, (name, description, mem_type, content, file_path, mem_id))
            else:
                conn.execute("""
                    INSERT INTO memories (id, name, description, type, content, file_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (mem_id, name, description, mem_type, content, file_path))
            conn.commit()

        return {"id": mem_id, "name": name, "file": filename, "status": "stored"}

    def recall(self, query: str, limit: int = 10, mem_type: str | None = None) -> list[dict]:
        """Search memories using FTS5 BM25 ranking."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # FTS5 query — escape special chars
            fts_query = re.sub(r'["\'\(\)\*]', ' ', query).strip()
            # Split into terms and join with OR for broader matching
            terms = fts_query.split()
            if not terms:
                return []
            fts_expr = " OR ".join(f'"{t}"' for t in terms)

            if mem_type:
                rows = conn.execute("""
                    SELECT m.id, m.name, m.description, m.type, m.content, m.file_path,
                           m.created_at, m.updated_at,
                           rank
                    FROM memories_fts fts
                    JOIN memories m ON m.rowid = fts.rowid
                    WHERE memories_fts MATCH ? AND m.type = ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_expr, mem_type, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT m.id, m.name, m.description, m.type, m.content, m.file_path,
                           m.created_at, m.updated_at,
                           rank
                    FROM memories_fts fts
                    JOIN memories m ON m.rowid = fts.rowid
                    WHERE memories_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_expr, limit)).fetchall()

            return [dict(r) for r in rows]

    def delete(self, name: str) -> dict:
        """Delete a memory by name."""
        mem_id = self._make_id(name)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT file_path FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
            if not row:
                return {"error": f"Memory '{name}' not found"}
            file_path = row[0]
            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            conn.commit()
        # Remove .md file
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return {"name": name, "status": "deleted"}

    def list_all(self, mem_type: str | None = None) -> list[dict]:
        """List all memories, optionally filtered by type."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if mem_type:
                rows = conn.execute(
                    "SELECT id, name, description, type, created_at, updated_at FROM memories WHERE type = ? ORDER BY updated_at DESC",
                    (mem_type,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name, description, type, created_at, updated_at FROM memories ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def reindex(self) -> dict:
        """Rebuild the index from .md files on disk."""
        count = 0
        for fname in os.listdir(self.dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(self.dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read()
                # Parse frontmatter
                fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw, re.DOTALL)
                if fm_match:
                    fm_text, body = fm_match.groups()
                    fm = {}
                    for line in fm_text.split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            fm[k.strip()] = v.strip()
                    name = fm.get("name", fname.replace(".md", ""))
                    desc = fm.get("description", "")
                    mtype = fm.get("type", "general")
                else:
                    name = fname.replace(".md", "")
                    desc = ""
                    mtype = "general"
                    body = raw

                self.store(name, body.strip(), desc, mtype)
                count += 1
            except Exception:
                continue
        return {"agent": self.agent_id, "reindexed": count}


# Global memory store instance (set in _run_interactive)
_memory_store: MemoryStore | None = None


def tool_memory_store(args: dict) -> str:
    """Store a memory."""
    if not _memory_store:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    content = args.get("content", "")
    description = args.get("description", "")
    mem_type = args.get("type", "general")
    if not name or not content:
        return _err("memory_store: name and content are required")
    result = _memory_store.store(name, content, description, mem_type)
    return _ok(result)


def tool_memory_recall(args: dict) -> str:
    """Recall memories by searching."""
    if not _memory_store:
        return _err("Memory store not initialized")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    mem_type = args.get("type")
    if not query:
        # List all if no query
        results = _memory_store.list_all(mem_type)
        return _ok({"query": "", "results": results, "count": len(results)})
    results = _memory_store.recall(query, limit, mem_type)
    # Truncate content in results for token efficiency
    for r in results:
        if r.get("content") and len(r["content"]) > 1000:
            r["content"] = r["content"][:1000] + "..."
    return _ok({"query": query, "results": results, "count": len(results)})


def tool_memory_delete(args: dict) -> str:
    """Delete a memory."""
    if not _memory_store:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    if not name:
        return _err("memory_delete: name is required")
    result = _memory_store.delete(name)
    return _ok(result)


def tool_memory_shared(args: dict) -> str:
    """Access the main agent's shared memory."""
    action = args.get("action", "recall")
    # Always use the main agent's memory store
    main_agent = AgentConfig("main")
    shared_store = MemoryStore(agent_id="main", base_dir=main_agent.memory_dir)

    if action == "store":
        name = args.get("name", "")
        content = args.get("content", "")
        description = args.get("description", "")
        mem_type = args.get("type", "general")
        if not name or not content:
            return _err("memory_shared store: name and content are required")
        result = shared_store.store(name, content, description, mem_type)
        result["source"] = "main (shared)"
        return _ok(result)
    else:  # recall
        query = args.get("query", "")
        limit = args.get("limit", 10)
        mem_type = args.get("type")
        if not query:
            results = shared_store.list_all(mem_type)
        else:
            results = shared_store.recall(query, limit, mem_type)
            for r in results:
                if r.get("content") and len(r["content"]) > 1000:
                    r["content"] = r["content"][:1000] + "..."
        return _ok({"query": query, "source": "main (shared)", "results": results, "count": len(results)})


def tool_delegate_task(args: dict) -> str:
    """Delegate a task to another agent, running in a separate context."""
    agent_id = args.get("agent", "")
    task = args.get("task", "")
    if not agent_id or not task:
        return _err("delegate_task: agent and task are required")

    # Check agent exists
    available = list_agents()
    if agent_id not in available:
        return _err(f"delegate_task: agent '{agent_id}' not found. Available: {', '.join(available)}")

    # Load target agent config
    target = AgentConfig(agent_id)
    target_memory = MemoryStore(agent_id, base_dir=target.memory_dir)

    # Determine model — use agent's preferred model, or fall back to current
    model = target.preferred_model
    if not model and _current_agent:
        model = _current_agent.preferred_model
    if not model:
        # Fall back to whatever the CLI was started with — stored in a global
        model = _delegate_fallback_model or "claude-opus-4-5-20251101"

    # Build system prompt for target agent
    import platform
    cwd = os.getcwd()
    os_name = platform.system()
    soul = target.soul
    tools_guide = target.tools_guide

    system_prompt = (
        f"{soul}\n\n"
        f"You are agent '{agent_id}'. Current working directory: {cwd}\n"
        f"Operating system: {os_name}\n\n"
        "You have been delegated a task by another agent. "
        "Complete it thoroughly and return your findings/results.\n\n"
        "You have access to all tools including memory_store/memory_recall for your own memory.\n"
        "For web searches, ALWAYS use exa_search — NEVER use duckduckgo or other search tools.\n"
    )
    if tools_guide:
        system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

    # Run in a fresh conversation
    messages = [{"role": "user", "content": task}]

    # Temporarily swap memory store for the target agent
    global _memory_store
    original_memory = _memory_store
    _memory_store = target_memory

    try:
        # Use send_message directly with system prompt
        # We need to build the payload manually since send_message builds its own system prompt
        result = _run_delegate(messages, model, system_prompt)
    finally:
        _memory_store = original_memory

    if result:
        return _ok({
            "agent": agent_id,
            "task": task,
            "response": result,
        })
    return _err(f"delegate_task: agent '{agent_id}' returned no response")


def _run_delegate(messages: list[dict], model: str, system_prompt: str) -> str | None:
    """Run a delegated task in a fresh context. Returns the final text response."""
    # Use globals for API config (set during _run_interactive)
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
        "max_tokens": 4096,
        "messages": aug_messages,
        "stream": True,
        "tools": TOOL_DEFINITIONS if api_type != "openai" else TOOL_DEFINITIONS_OPENAI,
    }
    if api_type != "openai":
        payload["system"] = system_prompt

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request) as response:
            if api_type == "openai":
                return _handle_openai_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, True, True, headers, endpoint, None, 0)
            else:
                return _handle_anthropic_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, True, True, headers, endpoint, None, 0)
    except Exception as e:
        return f"Delegation error: {e}"


# Globals for delegation (set in _run_interactive)
_delegate_fallback_model: str | None = None
_delegate_api_key: str = ""
_delegate_base_url: str = ""
_delegate_api_type: str = "anthropic"


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
    "delegate_task": ">",
}

TOOL_VERBS = {
    "read_file": "Reading", "write_file": "Writing", "edit_file": "Editing",
    "list_directory": "Listing", "search_files": "Searching", "execute_command": "Executing",
    "web_fetch": "Fetching", "exa_search": "Searching",
    "memory_store": "Remembering", "memory_recall": "Recalling", "memory_delete": "Forgetting", "memory_shared": "Shared Memory",
    "delegate_task": "Delegating",
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
        if action == "store":
            lines.append(_box_mid(f"{CYAN}store → main:{RESET} {MAGENTA}{args.get('name', '')}{RESET}"))
        else:
            q = args.get("query", "(list all)")
            lines.append(_box_mid(f"{CYAN}recall ← main:{RESET} {MAGENTA}{q}{RESET}"))
    elif name == "memory_delete":
        lines.append(_box_mid(f"{RED}{args.get('name', '')}{RESET}"))
    elif name == "delegate_task":
        lines.append(_box_mid(f"{CYAN}agent:{RESET} {BOLD}{args.get('agent', '')}{RESET}"))
        task_preview = args.get("task", "")[:max_w]
        lines.append(_box_mid(f"{DIM}{task_preview}{RESET}"))
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
    "delegate_task": tool_delegate_task,
}


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    fn = TOOL_DISPATCH.get(name)
    if fn:
        return fn(args)
    return _err(f"Unknown tool: {name}")


MAX_TOOL_ROUNDS = 15  # Maximum number of tool-use round trips before forcing a text response


def send_message(messages: list[dict], model: str, api_key: str, base_url: str,
                 api_type: str, silent: bool = False,
                 tools: bool = True,
                 escape_watcher: EscapeWatcher | None = None,
                 _tool_round: int = 0) -> str | None:
    """Send messages and stream the response.

    If silent=True, collects without printing (for TUI mode).
    If tools=True, includes tool definitions and handles tool-use loops.
    Returns the assistant's full response text on success, None on model-related errors.
    Raises TaskCancelled if escape_watcher detects Escape key.
    """
    # Stop tool loops after MAX_TOOL_ROUNDS
    if _tool_round >= MAX_TOOL_ROUNDS:
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

        # Load agent soul and tools guide
        agent = _current_agent
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
        agent_registry = build_agent_registry()

        system_instruction = ""
        if soul:
            system_instruction += f"{soul}\n\n"
        system_instruction += (
            f"You are agent '{agent_id}' in the Brain Agent system. "
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
            "SHARED MEMORY: Use memory_shared to access the main agent's memory.\n"
            "- Contains global knowledge: infrastructure, user prefs, project-wide decisions\n"
            "- All agents can read from shared memory; store shared facts there too\n"
            "- Check shared memory when your own memory doesn't have what you need\n\n"
        )
        if agent_registry:
            system_instruction += f"\n{agent_registry}\n\n"
        if tools_guide:
            system_instruction += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"
        if api_type == "openai":
            augmented_messages.insert(0, {"role": "system", "content": system_instruction})
        else:
            pass  # handled below via payload["system"]

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": augmented_messages,
        "stream": True,
    }

    if tools:
        if api_type == "openai":
            payload["tools"] = TOOL_DEFINITIONS_OPENAI
        else:
            payload["tools"] = TOOL_DEFINITIONS
            payload["system"] = system_instruction

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=data, headers=headers, method="POST",
    )

    # Check for cancellation before making the request
    if escape_watcher and escape_watcher.cancelled:
        raise TaskCancelled()

    try:
        with urllib.request.urlopen(request) as response:
            if api_type == "openai":
                return _handle_openai_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, silent, tools, headers, endpoint, escape_watcher,
                    _tool_round)
            else:
                return _handle_anthropic_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, silent, tools, headers, endpoint, escape_watcher,
                    _tool_round)

    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        try:
            error_body = e.read().decode("utf-8")
            print(error_body, file=sys.stderr)
        except:
            pass
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _handle_anthropic_response(response, payload, messages, model, api_key,
                                base_url, api_type, silent, tools,
                                headers, endpoint,
                                escape_watcher: EscapeWatcher | None = None,
                                _tool_round: int = 0) -> str | None:
    """Handle Anthropic SSE response, including tool-use agentic loop."""
    # Parse the full SSE stream to get content blocks and stop reason
    collected_text = []
    tool_uses = []
    current_event = None
    current_block_type = None
    current_block = {}
    stop_reason = None

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

                if etype == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason")

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

    # If no tool calls, just return the text
    if not tool_uses:
        if not silent and full_text:
            print()
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
        result = _execute_tool(tu["name"], tu["input"])
        _display_tool_result(tu["name"], result)

        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": result,
        })

    messages.append({"role": "user", "content": tool_results})

    # Recurse to get the model's final response (or more tool calls)
    return send_message(messages, model, api_key, base_url, api_type,
                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                        _tool_round=_tool_round + 1)


def _handle_openai_response(response, payload, messages, model, api_key,
                             base_url, api_type, silent, tools,
                             headers, endpoint,
                             escape_watcher: EscapeWatcher | None = None,
                             _tool_round: int = 0) -> str | None:
    """Handle OpenAI SSE response, including tool-use agentic loop."""
    collected_text = []
    tool_calls_map = {}  # index -> {id, name, arguments_str}

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
            choices = event.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if content:
                content = _unescape(content)
                if not silent:
                    print(content, end="", flush=True)
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

    if not tool_calls_map:
        if not silent and full_text:
            print()
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
        result = _execute_tool(tool_name, args)
        _display_tool_result(tool_name, result)

        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    return send_message(messages, model, api_key, base_url, api_type,
                        silent=silent, tools=tools, escape_watcher=escape_watcher,
                        _tool_round=_tool_round + 1)


def send_message_with_fallback(messages: list[dict], model: str, api_key: str,
                               base_url: str, api_type: str,
                               silent: bool = False,
                               tools: bool = True,
                               escape_watcher: EscapeWatcher | None = None) -> str | None:
    """Send messages, falling back to available models if the requested one fails."""
    result = send_message(messages, model, api_key, base_url, api_type,
                          silent=silent, tools=tools, escape_watcher=escape_watcher)
    if result is not None:
        return result

    available_models = get_available_models(api_key, base_url, api_type)
    if not available_models:
        print(f"Error: Model '{model}' is not available and no fallback models found.", file=sys.stderr)
        sys.exit(1)

    tried_models = {model}
    for fallback_model in available_models:
        if fallback_model in tried_models:
            continue
        tried_models.add(fallback_model)
        print(f"Note: Model '{model}' not available, using '{fallback_model}'.", flush=True)
        result = send_message(messages, fallback_model, api_key, base_url, api_type,
                              silent=silent, tools=tools, escape_watcher=escape_watcher)
        if result is not None:
            return result

    print(f"Error: No working models found. Tried: {', '.join(tried_models)}", file=sys.stderr)
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
    """Print the Brain Agent startup banner — compact, left-aligned, Claude Code style."""
    cwd = os.getcwd()
    latest = CHANGELOG[0] if CHANGELOG else None

    icon = [
        f"{FG_ORANGE}  ⣠⣴⣶⣶⣦⣄{RESET}",
        f"{FG_ORANGE} ⣿⣿⠛⠛⣿⣿{RESET}",
        f"{FG_ORANGE} ⠻⣿⣿⣿⣿⠟{RESET}",
    ]

    agent_label = f" {CYAN}{BOLD}{agent_id}{RESET}" if agent_id != "main" else ""
    info = [
        f"  {BOLD}Brain Agent{RESET}{agent_label} {DIM}v{VERSION}{RESET}",
        f"  {DIM}{model}{RESET}",
        f"  {DIM}{cwd}{RESET}",
    ]

    print()
    for i in range(len(icon)):
        print(f"{icon[i]}{info[i]}")

    # Agent info
    agents = list_agents()
    if len(agents) > 1:
        print(f"  {DIM}Agents:{RESET} {DIM}{', '.join(agents)}{RESET}")

    # Tools summary
    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
    print(f"  {DIM}Tools:{RESET} {DIM}{', '.join(tool_names)}{RESET}")
    print(f"  {DIM}/new /agent /model /models /tools  Esc cancel  exit quit{RESET}")

    # Tip / changelog line
    if latest:
        print(f"\n {DIM}↑ {latest[2]}{RESET}")

    print()


def _readline(prompt: str, input_history: list[str], history_idx_ref: list[int]) -> str | None:
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
    global _current_agent, _memory_store
    agent = AgentConfig(agent_id)
    _current_agent = agent
    _memory_store = MemoryStore(agent_id=agent_id, base_dir=agent.memory_dir)
    # Use agent's preferred model if set, otherwise keep current
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
                    # Direct switch
                    if arg not in agents:
                        # Create new agent
                        print(f"  {DIM}Creating new agent:{RESET} {BOLD}{arg}{RESET}")
                    current_model, _ = _switch_agent(arg, args)
                    history = []  # fresh context for new agent
                    print(f"  {DIM}Switched to agent:{RESET} {BOLD}{arg}{RESET} {DIM}(model: {current_model}){RESET}")
                else:
                    # List agents
                    print()
                    for idx, aid in enumerate(agents, 1):
                        cfg = AgentConfig(aid)
                        active = " (active)" if _current_agent and aid == _current_agent.agent_id else ""
                        model_info = f" [{cfg.preferred_model}]" if cfg.preferred_model else ""
                        if active:
                            print(f"  {GREEN}{BOLD}{idx}. {aid}{active}{model_info}{RESET}")
                        else:
                            print(f"  {DIM}{idx}. {aid}{model_info} — {cfg.description}{RESET}")
                    # Prompt for selection
                    choice = _readline(f"  {DIM}Select (number or name, empty to cancel):{RESET} ", [], [0])
                    if choice is None or not choice.strip():
                        continue
                    choice = choice.strip()
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(agents):
                            choice = agents[idx]
                        else:
                            print(f"  {DIM}Invalid index.{RESET}")
                            continue
                    except ValueError:
                        pass  # use as agent name directly
                    current_model, _ = _switch_agent(choice, args)
                    history = []
                    print(f"  {DIM}Switched to agent:{RESET} {BOLD}{choice}{RESET} {DIM}(model: {current_model}){RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if stripped == "/models":
                models = get_available_models(args.api_key, args.base_url, args.api_type)
                if models:
                    print()
                    for idx, mid in enumerate(models, 1):
                        if mid == current_model:
                            print(f"  {GREEN}{BOLD}{idx}. {mid} (active){RESET}")
                        else:
                            print(f"  {DIM}{idx}. {mid}{RESET}")
                else:
                    print(f"{DIM}No models available{RESET}")
                continue

            if stripped.startswith("/model"):
                arg = message.strip()[6:].strip()
                models = get_available_models(args.api_key, args.base_url, args.api_type)
                if arg:
                    try:
                        idx = int(arg) - 1
                        if 0 <= idx < len(models):
                            current_model = models[idx]
                        else:
                            print(f"  {DIM}Invalid index. Use 1-{len(models)}{RESET}")
                            continue
                    except ValueError:
                        current_model = arg
                    print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                elif models:
                    print()
                    for idx, mid in enumerate(models, 1):
                        if mid == current_model:
                            print(f"  {GREEN}{BOLD}{idx}. {mid} (active){RESET}")
                        else:
                            print(f"  {DIM}{idx}. {mid}{RESET}")
                    choice = _readline(f"  {DIM}Select (number or name):{RESET} ", [], [0])
                    if choice is None:
                        continue
                    choice = choice.strip()
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(models):
                            current_model = models[idx]
                        else:
                            print(f"  {DIM}Invalid index.{RESET}")
                            continue
                    except ValueError:
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
        signal.signal(signal.SIGWINCH, old_sigwinch)
        _restore_scroll_region()


if __name__ == "__main__":
    main()
