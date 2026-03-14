#!/usr/bin/env python3
"""Brain Agent — Agentic CLI for interacting with LLM APIs."""

VERSION = "0.4.0"
VERSION_DATE = "2026-03-14"
CHANGELOG = [
    ("0.4.0", "2026-03-14", "Escape to cancel, dynamic terminal rendering, startup greeting"),
    ("0.3.0", "2026-03-13", "Exa web search tool with agentic tool-use loop"),
    ("0.2.0", "2026-03-12", "Interactive TUI with spinner, markdown rendering, model switching"),
    ("0.1.0", "2026-03-10", "Initial release — streaming chat, model fallback, SSE parsing"),
]

import argparse
import json
import os
import random
import re
import select
import signal
import shutil
import sys
import termios
import threading
import time
import tty
import urllib.request
import urllib.error


# --- Tools ---

EXA_TOOL_DEFINITION = {
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
}

# OpenAI-compatible format
EXA_TOOL_DEFINITION_OPENAI = {
    "type": "function",
    "function": {
        "name": "exa_search",
        "description": EXA_TOOL_DEFINITION["description"],
        "parameters": {
            "type": "object",
            "properties": EXA_TOOL_DEFINITION["input_schema"]["properties"],
            "required": ["query"],
        },
    },
}


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
            tty.setraw(fd)
            while not self._stop.is_set():
                # Check if there's input available (non-blocking)
                if select.select([fd], [], [], 0.1)[0]:
                    ch = os.read(fd, 1)
                    if ch == b'\x1b':  # Escape key
                        self._cancelled.set()
                        break
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
        # label includes ANSI codes, so measure visible length separately
        visible_label = re.sub(r"\033\[[^m]*m", "", label)
        fill = max(0, w - 2 - len(visible_label))
        return f"  {DIM}┌─ {RESET}{label}{DIM} {'─' * fill}┐{RESET}"
    else:
        return f"  {DIM}┌{'─' * w}┐{RESET}"


def _box_mid(content: str = "") -> str:
    """Draw │ content  │ — content is NOT padded/truncated (let terminal wrap)."""
    return f"  {DIM}│{RESET}  {content}"


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


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    if name == "exa_search":
        return exa_search(
            query=args.get("query", ""),
            num_results=args.get("num_results", 5),
            category=args.get("category"),
        )
    return json.dumps({"error": f"Unknown tool: {name}"})


def send_message(messages: list[dict], model: str, api_key: str, base_url: str,
                 api_type: str, silent: bool = False,
                 tools: bool = True,
                 escape_watcher: EscapeWatcher | None = None) -> str | None:
    """Send messages and stream the response.

    If silent=True, collects without printing (for TUI mode).
    If tools=True, includes tool definitions and handles tool-use loops.
    Returns the assistant's full response text on success, None on model-related errors.
    Raises TaskCancelled if escape_watcher detects Escape key.
    """
    headers = make_headers(api_key, api_type)

    if api_type == "openai":
        endpoint = f"{base_url}/chat/completions"
    else:
        endpoint = f"{base_url}/messages"

    # Prepend system instruction to prefer exa_search over server-side search tools
    augmented_messages = list(messages)
    if tools:
        system_instruction = (
            "You have access to the `exa_search` tool for web searches. "
            "ALWAYS use `exa_search` when the user asks to search the web, look something up, "
            "or find information online. NEVER use duckduckgo_search or any other search tool. "
            "The ONLY search tool you are allowed to use is `exa_search`. "
            "If `exa_search` returns an error, report the error to the user — do NOT fall back to other search tools."
        )
        if api_type == "openai":
            augmented_messages.insert(0, {"role": "system", "content": system_instruction})
        else:
            # For Anthropic, use the top-level "system" field
            pass  # handled below in payload

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": augmented_messages,
        "stream": True,
    }

    if tools:
        if api_type == "openai":
            payload["tools"] = [EXA_TOOL_DEFINITION_OPENAI]
        else:
            payload["tools"] = [EXA_TOOL_DEFINITION]
            payload["system"] = (
                "You have access to the `exa_search` tool for web searches. "
                "ALWAYS use `exa_search` when the user asks to search the web, look something up, "
                "or find information online. NEVER use duckduckgo_search or any other search tool. "
                "The ONLY search tool you are allowed to use is `exa_search`. "
                "If `exa_search` returns an error, report the error to the user — do NOT fall back to other search tools."
            )

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
                    api_type, silent, tools, headers, endpoint, escape_watcher)
            else:
                return _handle_anthropic_response(
                    response, payload, messages, model, api_key, base_url,
                    api_type, silent, tools, headers, endpoint, escape_watcher)

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
                                escape_watcher: EscapeWatcher | None = None) -> str | None:
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
        # Always show tool invocation, even in silent/TUI mode
        query_str = tu['input'].get('query', '')
        print(f"\n{_box_top(f'{FG_ORANGE}{BOLD}⚡ Searching{RESET}')}")
        print(_box_mid(f"{CYAN}{tu['name']}{RESET}({MAGENTA}query{RESET}={WHITE}\"{query_str}\"{RESET})"))
        print(_box_bot())
        sys.stdout.flush()

        result = _execute_tool(tu["name"], tu["input"])

        # Always show result summary
        try:
            rdata = json.loads(result)
            count = rdata.get("result_count", 0)
            max_title = max(20, _term_cols() - 8)
            if rdata.get("error"):
                print(_box_top(f"{RED}{BOLD}✘ Error{RESET}"))
                print(_box_mid(rdata['error'][:max_title]))
                print(f"{_box_bot()}\n")
            else:
                print(_box_top(f"{GREEN}{BOLD}✔ {count} results{RESET}"))
                for r in rdata.get("results", [])[:3]:
                    title = r.get("title", "")[:max_title]
                    print(_box_mid(f"{BOLD}{title}{RESET}"))
                if count > 3:
                    print(_box_mid(f"{DIM}... and {count - 3} more{RESET}"))
                print(f"{_box_bot()}\n")
        except json.JSONDecodeError:
            pass
        sys.stdout.flush()

        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": result,
        })

    messages.append({"role": "user", "content": tool_results})

    # Recurse to get the model's final response (or more tool calls)
    return send_message(messages, model, api_key, base_url, api_type,
                        silent=silent, tools=tools, escape_watcher=escape_watcher)


def _handle_openai_response(response, payload, messages, model, api_key,
                             base_url, api_type, silent, tools,
                             headers, endpoint,
                             escape_watcher: EscapeWatcher | None = None) -> str | None:
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

        print(f"\n{_box_top(f'{FG_ORANGE}{BOLD}⚡ Searching{RESET}')}")
        print(_box_mid(f"{CYAN}{tc['function']['name']}{RESET}({MAGENTA}query{RESET}={WHITE}\"{args.get('query', '')}\"{RESET})"))
        print(_box_bot())
        sys.stdout.flush()

        result = _execute_tool(tc["function"]["name"], args)

        try:
            rdata = json.loads(result)
            count = rdata.get("result_count", 0)
            max_title = max(20, _term_cols() - 8)
            if rdata.get("error"):
                print(_box_top(f"{RED}{BOLD}✘ Error{RESET}"))
                print(_box_mid(rdata['error'][:max_title]))
                print(f"{_box_bot()}\n")
            else:
                print(_box_top(f"{GREEN}{BOLD}✔ {count} results{RESET}"))
                for r in rdata.get("results", [])[:3]:
                    title = r.get("title", "")[:max_title]
                    print(_box_mid(f"{BOLD}{title}{RESET}"))
                if count > 3:
                    print(_box_mid(f"{DIM}... and {count - 3} more{RESET}"))
                print(f"{_box_bot()}\n")
        except json.JSONDecodeError:
            pass
        sys.stdout.flush()

        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    return send_message(messages, model, api_key, base_url, api_type,
                        silent=silent, tools=tools, escape_watcher=escape_watcher)


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

def _draw_status_bar(model: str) -> None:
    """Draw a status bar on the last terminal line with black background."""
    cols = shutil.get_terminal_size().columns
    rows = shutil.get_terminal_size().lines
    # Build status with distinct colored segments on black background
    label = f" {FG_GRAY}Model:{RESET}{BG_DARK} {GREEN}{BOLD}{model}{RESET}{BG_DARK} "
    # Pad the rest with black background
    # visible length = " Model: " + model + " "
    visible_len = 9 + len(model)
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


def _print_greeting(model: str) -> None:
    """Print the Brain Agent startup banner — compact, left-aligned, Claude Code style."""
    cwd = os.getcwd()
    # Latest changelog entry for the tip line
    latest = CHANGELOG[0] if CHANGELOG else None

    # Brain icon (small, 3 lines tall to sit beside the text)
    # Uses orange/yellow tones like Claude Code's robot
    icon = [
        f"{FG_ORANGE}  ⣠⣴⣶⣶⣦⣄{RESET}",
        f"{FG_ORANGE} ⣿⣿⠛⠛⣿⣿{RESET}",
        f"{FG_ORANGE} ⠻⣿⣿⣿⣿⠟{RESET}",
    ]

    # Info lines (displayed to the right of the icon)
    info = [
        f"  {BOLD}Brain Agent{RESET} {DIM}v{VERSION}{RESET}",
        f"  {DIM}{model}{RESET}",
        f"  {DIM}{cwd}{RESET}",
    ]

    print()
    for i in range(len(icon)):
        print(f"{icon[i]}{info[i]}")

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


def _run_interactive(args):
    """Run the interactive TUI chat loop."""
    current_model = args.model
    history = []
    input_history = []   # list of previous user inputs for arrow-key recall
    history_idx = [0]    # mutable ref for current position

    # Clear screen and move cursor to top
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    _setup_scroll_region()
    _draw_status_bar(current_model)

    # Handle terminal resize
    def _on_resize(signum, frame):
        _setup_scroll_region()
        _draw_status_bar(current_model)

    old_sigwinch = signal.signal(signal.SIGWINCH, _on_resize)

    # Startup greeting
    _print_greeting(current_model)

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
                _draw_status_bar(current_model)
                continue

            if not message.strip():
                continue

            # Save to input history (dedup consecutive)
            if not input_history or input_history[-1] != message.strip():
                input_history.append(message.strip())

            # Send message
            history.append({"role": "user", "content": message})

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
                _draw_status_bar(current_model)
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
            _draw_status_bar(current_model)

    finally:
        signal.signal(signal.SIGWINCH, old_sigwinch)
        _restore_scroll_region()


if __name__ == "__main__":
    main()
