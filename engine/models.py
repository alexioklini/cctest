# Extracted from claude_cli.py — model configuration, provider resolution, proxy channel, CLI rendering
#
# Cross-module dependencies (all live in claude_cli.py — imported at call-sites or injected):
#   _thread_local          — thread.local() with current_agent, current_session_id, current_user_id
#   _current_agent         — fallback AgentConfig when _thread_local.current_agent is unset
#   _audit_log             — AuditLog | None singleton
#   _models_config         — dict populated by init_models_config(); also defined here as module-level
#   _provider_queue        — LocalProviderQueue singleton from claude_cli.py
#   resolve_provider_for_model — function in claude_cli.py
#   get_api_model_id       — defined here (self-referential within this module)
#   is_model_local         — defined in claude_cli.py (referenced by gdpr_pick_model_for_background)
#   _get_gdpr_scanner_config  — defined in claude_cli.py
#   _pii_scan_text         — defined in claude_cli.py / engine/analytics/pii.py
#   _pii_worst_action      — defined in claude_cli.py / engine/analytics/pii.py
#   DEFAULT_MAX_CONTEXT_TOKENS — constant in claude_cli.py (131072)
#   _is_local_base_url     — defined in claude_cli.py
#   TaskCancelled          — exception class in claude_cli.py
#   _CLIENT_PROXY_TOOLS_DEFAULT — ["exa_search"] in claude_cli.py
#   _execution_mode_cache, _execution_mode_cache_time, _client_proxy_tools_cache
#                          — module-level cache vars; also defined here for standalone use
#   queue                  — stdlib queue module (used by ProxyChannel)
#   shutil, sys, random    — stdlib (used by Spinner, markdown helpers)

import json
import os
import queue
import random
import re
import shutil
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# --- Client-hosted local models manifest ----------------------------------
# ---------------------------------------------------------------------------
# Server declares GGUF model weights that clients (Electron desktop app) may
# download and run locally. Family string is the compat key — server-side oMLX
# model and client-side GGUF are "the same model" for routing purposes when
# their family matches, even if quant/format differ. See CLAUDE.md.

_client_models_cache = None
_client_models_cache_time = 0.0
_CLIENT_MODELS_TTL = 10.0


def _load_client_models() -> list:
    """Read config.json → client_models: [{id, family, gguf_path, sha256,
    size_bytes, auto_download}]. 10s cache. Returns [] on any error."""
    global _client_models_cache, _client_models_cache_time
    now = time.time()
    if _client_models_cache is not None and (now - _client_models_cache_time) < _CLIENT_MODELS_TTL:
        return _client_models_cache
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    entries = []
    try:
        with open(cfg_path) as f:
            raw = json.load(f).get("client_models", []) or []
        if isinstance(raw, list):
            entries = [e for e in raw if isinstance(e, dict) and e.get("id") and e.get("family")]
    except (OSError, json.JSONDecodeError):
        entries = []
    _client_models_cache = entries
    _client_models_cache_time = now
    return entries


def _invalidate_client_models_cache():
    """Called from server.py when client_models config changes."""
    global _client_models_cache, _client_models_cache_time
    _client_models_cache = None
    _client_models_cache_time = 0.0


def get_client_model(model_id: str) -> dict | None:
    """Return the client_models manifest entry matching `model_id` (by id),
    or None if the model isn't in the client-eligible list."""
    for entry in _load_client_models():
        if entry.get("id") == model_id:
            return entry
    return None


def get_client_model_by_family(family: str) -> dict | None:
    """Return the first manifest entry with the given family string, or None."""
    if not family:
        return None
    for entry in _load_client_models():
        if entry.get("family") == family:
            return entry
    return None


def is_model_client_executable(capabilities: dict | None, model_id: str) -> tuple[bool, str]:
    """Decide whether a request for `model_id` should be routed to client-
    hosted inference instead of running on the server, given the client-
    declared capabilities dict (from Session.client_capabilities).

    Returns (True, family) if:
      - capabilities.enabled is True
      - model_id has a manifest entry (i.e. is a client-eligible model)
      - the manifest entry's family appears in capabilities.families

    Returns (False, "") otherwise. The caller is expected to have verified
    the request is interactive (has event_callback) — background/scheduled
    requests never route to clients regardless of capabilities.
    """
    if not capabilities or not model_id:
        return False, ""
    if not capabilities.get("enabled"):
        return False, ""
    families = capabilities.get("families") or []
    if not families:
        return False, ""
    entry = get_client_model(model_id)
    if not entry:
        return False, ""
    family = entry.get("family", "")
    if family and family in families:
        return True, family
    return False, ""


class GDPRBlockedError(RuntimeError):
    """Raised by gdpr_pick_model_for_background when PII is detected, the
    server is configured in hard-block mode, and no safe local route exists.

    Callers catch this and decide what to skip (e.g. drop the background call,
    return a static summary, emit a delegate error). Not raised for the main
    chat path — that surface has its own RuntimeError branch with a different
    message aimed at the end user.
    """


def gdpr_pick_model_for_background(model: str, texts, purpose: str = "") -> str:
    """Decide which model to use for a background/worker LLM call.

    Behavior when the scanner is enabled AND `texts` contain PII:
      - current model is local           → return model unchanged
      - local fallback configured + OK   → swap to fallback (logged as pii_auto_fallback)
      - no usable fallback, block off    → return model unchanged (warn-only)
      - no usable fallback, block on     → raise GDPRBlockedError

    Every PII detection at this layer emits a `pii_detected` audit row with
    `source=background`, independent of whether the model is swapped.

    `texts` accepts str or iterable-of-str. Unexpected errors in scanning or
    config access fall open (return model) — never block a background call on
    scanner bugs.

    Used by: next-prompt suggestions, chat summary, memory classifier, worker
    tool-result summariser, _run_delegate (delegate tool + scheduler + agent
    tasks).
    """
    try:
        cfg = _get_gdpr_scanner_config()
    except Exception:
        return model
    if not cfg.get("enabled", True):
        return model

    # Normalise texts up-front so we can scan regardless of fallback state.
    if isinstance(texts, str):
        samples = [texts]
    else:
        try:
            samples = [t for t in texts if isinstance(t, str)]
        except TypeError:
            return model
    if not samples:
        return model

    # Scan. Fail open on scanner errors.
    try:
        findings = []
        for s in samples:
            if not s:
                continue
            findings.extend(_pii_scan_text(s, max_findings=5, cfg=cfg))
            if findings:
                break
    except Exception:
        return model
    if not findings:
        return model

    _agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    _agent_id = _agent.agent_id if _agent else "main"
    _sid = getattr(_thread_local, 'current_session_id', None) or ""
    _n = len(findings)
    _log_audit = bool(cfg.get("server_log", True) and _audit_log)
    # server_block is the master switch but the per-finding action is what
    # decides refusal — a warn-category finding never blocks even if the master
    # switch is on. _pii_worst_action returns "block" only if both are true.
    _worst_action = _pii_worst_action(findings)
    _server_block = (_worst_action == "block")

    # Always record the detection at the background layer, independent of any
    # swap decision. Best-effort.
    try:
        print(f"[gdpr] session={_sid} agent={_agent_id} purpose={purpose} "
              f"findings={_n} (background)", flush=True)
    except Exception:
        pass
    if _log_audit:
        try:
            _audit_log.log_action(
                agent=_agent_id,
                action_type="pii_detected",
                tool_name="gdpr_scanner",
                args_summary=f"{_n} findings",
                result_summary=f"purpose={purpose or '-'} model={model}",
                result_status="warning",
                session_id=_sid or None,
                source="background",
            )
        except Exception:
            pass

    # Already on a local model — nothing to reroute, nothing to block.
    try:
        model_is_local = is_model_local(model)
    except Exception:
        model_is_local = False
    if model_is_local:
        return model

    # Attempt the swap. fallback == model is treated as no-swap.
    fallback = (cfg.get("default_local_fallback_model") or "").strip()
    swap_ok = False
    if fallback and fallback != model:
        try:
            fcfg = (_models_config or {}).get(fallback) or {}
            if fcfg.get("enabled") and is_model_local(fallback):
                swap_ok = True
        except Exception:
            swap_ok = False

    if swap_ok:
        try:
            print(f"[gdpr] auto-fallback session={_sid} agent={_agent_id} "
                  f"purpose={purpose} {model} -> {fallback} ({_n} findings)", flush=True)
        except Exception:
            pass
        if _log_audit:
            try:
                _audit_log.log_action(
                    agent=_agent_id,
                    action_type="pii_auto_fallback",
                    tool_name="gdpr_scanner",
                    args_summary=f"{model} -> {fallback}",
                    result_summary=f"purpose={purpose or '-'} findings={_n}",
                    result_status="ok",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        return fallback

    # No swap possible and model is cloud.
    if _server_block:
        try:
            print(f"[gdpr] BLOCK session={_sid} agent={_agent_id} purpose={purpose} "
                  f"model={model} findings={_n} (no local fallback available)",
                  flush=True)
        except Exception:
            pass
        if _log_audit:
            try:
                _audit_log.log_action(
                    agent=_agent_id,
                    action_type="pii_blocked",
                    tool_name="gdpr_scanner",
                    args_summary=f"model={model}",
                    result_summary=f"purpose={purpose or '-'} findings={_n} "
                                   f"fallback={fallback or '-'}",
                    result_status="blocked",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        raise GDPRBlockedError(
            f"[GDPR block] Background call refused (purpose={purpose or '-'}): "
            f"{_n} personal-data finding(s) in payload and no usable local "
            f"fallback model is configured. Set "
            f"gdpr_scanner.default_local_fallback_model in Settings, or "
            f"disable server_block."
        )

    # Warn-only mode: leave the caller to use the cloud model.
    return model


_execution_mode_cache = None
_execution_mode_cache_time = 0.0
_client_proxy_tools_cache = None

_CLIENT_PROXY_TOOLS_DEFAULT = ["exa_search"]


def _get_execution_mode() -> str:
    """Read execution_mode from config.json. 30s cache. Returns 'server' or 'client'."""
    global _execution_mode_cache, _execution_mode_cache_time, _client_proxy_tools_cache
    now = time.time()
    if _execution_mode_cache is not None and (now - _execution_mode_cache_time) < 30:
        return _execution_mode_cache
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    mode = "server"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        mode = cfg.get("execution_mode", "server") or "server"
        tools = cfg.get("client_proxy_tools")
        _client_proxy_tools_cache = set(tools) if isinstance(tools, list) else None
    except (OSError, json.JSONDecodeError):
        pass
    _execution_mode_cache = mode
    _execution_mode_cache_time = now
    return mode


def _get_client_proxy_tools() -> set:
    """Return the set of tool names to proxy through the browser in client mode."""
    _get_execution_mode()
    if _client_proxy_tools_cache is not None:
        return _client_proxy_tools_cache
    return set(_CLIENT_PROXY_TOOLS_DEFAULT)


class ProxyChannel:
    """Thread-safe channel for proxying LLM calls and web tool execution through a browser client.

    In client execution mode, the agentic loop emits proxy_request/proxy_tool events via
    event_callback, then blocks on this channel waiting for the browser to stream back results.
    """

    def __init__(self):
        self.response_queue = queue.Queue()
        self.tool_results = {}  # tool_call_id -> result string
        self._tool_events = {}  # tool_call_id -> threading.Event

    def wait_for_llm_lines(self, escape_watcher=None):
        """Yield SSE lines from the browser's proxied LLM response.
        Implements the same iterator interface as urllib response (for line in response:).
        """
        while True:
            if escape_watcher and escape_watcher.cancelled:
                raise TaskCancelled()
            try:
                item = self.response_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item.encode("utf-8") if isinstance(item, str) else item

    def feed_llm_line(self, line: str):
        """Browser sends an SSE line from the provider response."""
        self.response_queue.put(line)

    def feed_llm_done(self):
        """Browser signals the LLM response stream is complete."""
        self.response_queue.put(None)

    def feed_llm_error(self, message: str):
        """Browser signals an error during LLM call."""
        self.response_queue.put(RuntimeError(f"Client proxy error: {message}"))

    def request_tool_result(self, tool_call_id: str) -> threading.Event:
        """Create a wait event for a proxied tool result."""
        evt = threading.Event()
        self._tool_events[tool_call_id] = evt
        return evt

    def feed_tool_result(self, tool_call_id: str, result: str):
        """Browser sends the result of a proxied web tool."""
        self.tool_results[tool_call_id] = result
        evt = self._tool_events.pop(tool_call_id, None)
        if evt:
            evt.set()

    def get_tool_result(self, tool_call_id: str, timeout: float = 120.0,
                        escape_watcher=None) -> str:
        """Block until the browser returns the tool result."""
        evt = self._tool_events.get(tool_call_id)
        if not evt:
            evt = self.request_tool_result(tool_call_id)
        start = time.time()
        while not evt.wait(timeout=1.0):
            if escape_watcher and escape_watcher.cancelled:
                raise TaskCancelled()
            if time.time() - start > timeout:
                return json.dumps({"error": f"Client proxy timeout after {timeout}s"})
        return self.tool_results.pop(tool_call_id, json.dumps({"error": "No result received"}))

    def reset(self):
        """Clear state for a new LLM call."""
        while not self.response_queue.empty():
            try:
                self.response_queue.get_nowait()
            except queue.Empty:
                break
        self.tool_results.clear()
        self._tool_events.clear()


_proxy_channels = {}  # session_id -> ProxyChannel
_proxy_channels_lock = threading.Lock()


def get_proxy_channel(session_id: str) -> ProxyChannel:
    """Get or create a proxy channel for a session."""
    with _proxy_channels_lock:
        if session_id not in _proxy_channels:
            _proxy_channels[session_id] = ProxyChannel()
        return _proxy_channels[session_id]


def cleanup_proxy_channel(session_id: str):
    """Remove proxy channel when session is done."""
    with _proxy_channels_lock:
        _proxy_channels.pop(session_id, None)


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

def make_headers(api_key: str) -> dict:
    """Build request headers. All providers are OpenAI-compatible."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


_MEMORY_CLASSIFIER_PROMPT = (
    "Classify this chat exchange into exactly one category. "
    "Reply with ONLY the category name, nothing else.\n"
    "Categories:\n"
    "- fact: contains user-specific data, measurements, names, dates, results\n"
    "- preference: user expresses a preference, like, dislike, or personal choice\n"
    "- decision: a decision was made, an action agreed upon, a plan set\n"
    "- reference: mentions a specific resource, URL, tool, project, or document\n"
    "- generic: generic advice, how-to steps, or information not specific to the user\n"
    "- refusal: assistant says it can't help, doesn't have access, or doesn't know\n"
    "- chitchat: greetings, small talk, jokes, acknowledgements with no substance"
)


def classify_chat_for_memory(user_text: str, assistant_text: str,
                             model: str, timeout: int = 15) -> str | None:
    """Classify a user+assistant exchange for memory filing. Returns category or None on error."""
    try:
        # GDPR auto-fallback before any cloud HTTP call. Hard-block without
        # a local fallback skips classification; the chat pair stays unfiled.
        try:
            model = gdpr_pick_model_for_background(
                model, [user_text, assistant_text], purpose="memory_classifier")
        except GDPRBlockedError:
            return None
        provider = resolve_provider_for_model(model)
        headers = make_headers(provider["api_key"])
        endpoint = f"{provider['base_url']}/chat/completions"
        payload = json.dumps({
            "model": get_api_model_id(model),
            "max_tokens": 20,
            "temperature": 0,
            "stream": False,
            "messages": [
                {"role": "system", "content": _MEMORY_CLASSIFIER_PROMPT},
                {"role": "user", "content": f"User: {user_text[:2000]}\nAssistant: {assistant_text[:2000]}"},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        _provider_name = provider.get("provider_name", "") or "default"
        with _provider_queue.acquire_if(
            _provider_name, label="mempalace_classify",
            session_id=None, agent_id=None, user_id=None,
            model=model, event_callback=None, cancel_token=None,
            timeout=max(timeout * 2, 30),
        ):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip().lower()
        content = content.strip('"').strip("'").strip()
        valid = {"fact", "preference", "decision", "reference", "generic", "refusal", "chitchat"}
        return content if content in valid else None
    except Exception as e:
        print(f"[mempalace-classifier] error: {e}", file=sys.stderr, flush=True)
        return None


def get_available_models(api_key: str, base_url: str) -> list[str]:
    """Fetch available models from the API and return as a list."""
    headers = make_headers(api_key)
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
    "claude-opus": {"icon": "\U0001f7e3", "priority": 100, "max_context": 200000, "max_output": 32768, "capabilities": ["coding", "analysis", "agentic", "creative"], "inference": {"temperature": 1.0}, "raw_formats": ["image/*"]},
    "claude-sonnet": {"icon": "\U0001f7e0", "priority": 80, "max_context": 200000, "max_output": 16384, "capabilities": ["coding", "analysis", "fast"], "inference": {"temperature": 1.0}, "raw_formats": ["image/*"]},
    "claude-haiku": {"icon": "\U0001f7e2", "priority": 60, "max_context": 200000, "max_output": 8192, "capabilities": ["fast"], "inference": {"temperature": 1.0}, "raw_formats": ["image/*"]},
    "gemini": {"icon": "\U0001f48e", "priority": 70, "max_context": 1000000, "max_output": 65536, "capabilities": ["coding", "analysis"], "inference": {"temperature": 1.0, "top_p": 0.95}, "raw_formats": ["image/*", "application/pdf"]},
    "qwen": {"icon": "\U0001f43c", "priority": 50, "max_context": 131072, "max_output": 16384, "capabilities": ["coding", "analysis"], "inference": {"temperature": 0.7, "top_p": 0.9}, "raw_formats": []},
    "qwen-vl": {"icon": "\U0001f43c", "priority": 50, "max_context": 131072, "max_output": 16384, "capabilities": ["coding", "analysis"], "inference": {"temperature": 0.7, "top_p": 0.9}, "raw_formats": ["image/*"]},
    "gemma": {"icon": "\U0001f48e", "priority": 35, "max_context": 32768, "max_output": 8192, "capabilities": ["fast", "local"], "inference": {"temperature": 0.7, "top_p": 0.9}, "raw_formats": ["image/*"]},
    "crow": {"icon": "\U0001f426‍⬛", "priority": 30, "max_context": 32768, "max_output": 4096, "capabilities": ["fast", "local"], "inference": {"temperature": 0.7, "top_p": 0.9, "min_p": 0.05}, "raw_formats": []},
    "llama": {"icon": "\U0001f999", "priority": 40, "max_context": 131072, "max_output": 16384, "capabilities": ["coding", "local"], "inference": {"temperature": 0.7, "top_p": 0.9}, "raw_formats": []},
    "mistral": {"icon": "\U0001f32c️", "priority": 45, "max_context": 131072, "max_output": 16384, "capabilities": ["coding", "analysis"], "inference": {"temperature": 0.7}, "raw_formats": []},
    "pixtral": {"icon": "\U0001f32c️", "priority": 45, "max_context": 131072, "max_output": 16384, "capabilities": ["coding", "analysis"], "inference": {"temperature": 0.7}, "raw_formats": ["image/*"]},
    "minimax": {"icon": "\U0001f4ab", "priority": 55, "max_context": 131072, "max_output": 32768, "capabilities": ["coding", "analysis"], "inference": {"temperature": 0.7}, "raw_formats": []},
    "devstral": {"icon": "\U0001f32c️", "priority": 50, "max_context": 256000, "max_output": 65536, "capabilities": ["coding", "analysis", "agentic"], "inference": {"temperature": 0.7}, "raw_formats": []},
}

CAPABILITY_VALUES = ["coding", "analysis", "agentic", "fast", "creative", "local"]

_models_config: dict = {}


def _detect_thinking_format(model_id: str, provider: str = "") -> str:
    """Infer thinking_format from model id (and optional provider). Returns one of:
    "none" — model has no reasoning phase (default).
    "inline_tags" — emits <think>...</think> in content string (Qwen3, DeepSeek-R1 distills, GLM-Zero served via third parties).
    "reasoning_field" — emits separate message.reasoning_content / delta.reasoning_content (oMLX for all its models, cliproxyapi Gemini with reasoning_effort, DeepSeek-R1 direct).
    "mistral_blocks" — emits content as a list with {type: "thinking", thinking: [{type: "text", text: ...}]} blocks (Mistral API for magistral-* and mistral-small-2603+ when reasoning_effort is set).
    "openai_opaque" — reasoning tokens hidden server-side, only usage.completion_tokens_details.reasoning_tokens exposed (OpenAI o-series).

    Routing note: these choices assume DIRECT provider connections. Proxies that re-serialize via typed
    structs (e.g. Bifrost) may silently drop reasoning_content and the nested Mistral thinking array.
    Brain routes thinking-capable models direct to their provider for exactly this reason.
    """
    m = model_id.lower()
    p = (provider or "").lower()
    # Provider-aware match: cliproxyapi serves Gemini 2.5 reasoning even when
    # the stored id is bare ("gemini-2.5-flash") with no scoped prefix. The
    # id-only patterns below still cover the prefixed form for back-compat.
    if p == "cliproxyapi" and "gemini-2.5" in m:
        return "reasoning_field"
    # oMLX provider catch-all for any reasoning-capable model served locally,
    # even when the id doesn't carry an OMLX/ prefix. Non-reasoning models on
    # oMLX (Gemma 3, crow) still hit the default 'none' below because none of
    # the id patterns match them. Gemma 4 IS a reasoning model (channel-token
    # output, enable_thinking kwarg) so it's included here.
    if p == "omlx" and (
        "qwen3" in m or "qwen-3" in m
        or "deepseek-r1" in m or "deepseek/r1" in m
        or "glm-zero" in m or "glm4-zero" in m
        or "thinking" in m
        or "magistral" in m
        or "gemma-4" in m or "gemma4" in m
    ):
        return "reasoning_field"
    # OpenAI o-series — opaque reasoning
    for p in ("o1-", "o1/", "/o1", "o3-", "o3/", "/o3", "o4-mini"):
        if p in m:
            return "openai_opaque"
    # Mistral reasoning models — content-block array with nested thinking[]
    # (magistral-* and mistral-small 2603+ with reasoning_effort set). Checked
    # before the generic "magistral" branch in the inline_tags section so Mistral wins.
    if (
        "magistral" in m
        or "mistral-small-2603" in m
        or "mistral-small-latest" in m  # alias commonly resolves to the newest small
    ):
        return "mistral_blocks"
    # oMLX serves every reasoning-capable model via a unified API that exposes
    # reasoning_content as a sibling field on the message (even for gemma, when
    # enable_thinking is set). Catch the OMLX/ prefix here so we don't miss any.
    if m.startswith("omlx/") or "/omlx/" in m:
        return "reasoning_field"
    # cliproxyapi routes Gemini. 2.5-series has reasoning, returned in reasoning_content
    # when reasoning_effort is set on the request.
    if m.startswith("cliproxyapi/gemini-2.5") or "/gemini-2.5-" in m:
        return "reasoning_field"
    # DeepSeek-R1 official API uses reasoning_content field; distills via third-party often use inline tags.
    if "deepseek-r1" in m or "deepseek/r1" in m:
        return "reasoning_field" if "deepseek.com" in m or m.startswith("deepseek/") else "inline_tags"
    # Inline <think> tag emitters — Qwen3 / GLM-Zero when served by a provider that
    # does NOT strip reasoning_content. Kept as the fallback because oMLX's OMLX/qwen3
    # would have matched the reasoning_field branch above.
    if (
        "qwen3" in m or "qwen-3" in m
        or "glm-zero" in m or "glm4-zero" in m
        or "thinking" in m  # e.g. claude-*-thinking variants, custom builds
    ):
        return "inline_tags"
    return "none"


def _match_known_model(model_id: str, provider: str = "") -> dict:
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
                "display_name": shortname,
                "icon": defaults["icon"],
                "priority": defaults["priority"],
                "capabilities": list(defaults["capabilities"]),
                "thinking_format": _detect_thinking_format(model_id, provider),
            }
            if "max_context" in defaults:
                result["max_context"] = defaults["max_context"]
            if "max_output" in defaults:
                result["max_output"] = defaults["max_output"]
            if "inference" in defaults:
                result["inference"] = dict(defaults["inference"])
            if "raw_formats" in defaults:
                result["raw_formats"] = list(defaults["raw_formats"])
            return result
    # Unknown model — enabled with low priority
    shortname = model_id.split("/")[-1]
    return {
        "enabled": True,
        "shortname": shortname,
        "display_name": shortname,
        "icon": "\U0001f916",
        "priority": 10,
        "capabilities": [],
        "raw_formats": [],
        "thinking_format": _detect_thinking_format(model_id, provider),
    }


def init_models_config(providers: dict, existing_models: dict | None = None,
                       all_providers: dict | None = None,
                       deleted_models: set | list | None = None) -> dict:
    """Auto-populate models config from provider model lists.

    Merges with existing config (preserves user edits). Returns the models dict.
    all_providers: when syncing a subset, pass full provider dict for orphan cleanup.
    deleted_models: tombstone set — model ids (and provider/scoped variants) that
        the user explicitly deleted. We never auto-rediscover these. Cleared per
        provider only by the resync_provider action.
    """
    global _models_config
    if existing_models:
        # Deep copy: per-model cfg dicts are mutated in place by the upgrade
        # paths below (e.g. forward-looking thinking_format re-detect). A
        # shallow copy would alias the value dicts back to the caller's
        # snapshot, defeating the diff-based persist branch in server.py.
        _models_config = {k: dict(v) for k, v in existing_models.items()}

    tombstones = set(deleted_models or ())

    # Discover models from all providers
    for name, p in providers.items():
        try:
            models = get_available_models(
                p.get("api_key", ""), p.get("base_url", ""))
        except Exception:
            models = []
        discovered = set(models)
        # Auto-pick profile based on endpoint: local → "speed", cloud → "balanced".
        # Explicit per-model overrides survive because this only fires for new entries.
        default_profile = "speed" if _is_local_base_url(p.get("base_url", "")) else "balanced"
        for model_id in models:
            scoped_key = f"{name}/{model_id}"
            # Honor user deletions: if either the bare id or the provider-scoped
            # id is tombstoned, skip discovery entirely.
            if model_id in tombstones or scoped_key in tombstones:
                continue
            if model_id not in _models_config:
                entry = _match_known_model(model_id, name)
                entry["provider"] = name
                entry["profile"] = default_profile
                _models_config[model_id] = entry
            elif _models_config[model_id].get("provider") != name:
                # Same model ID exists under a different provider — add with provider-scoped key
                if scoped_key not in _models_config:
                    entry = _match_known_model(model_id, name)
                    entry["provider"] = name
                    entry["base_model_id"] = model_id
                    entry["profile"] = default_profile
                    _models_config[scoped_key] = entry
                discovered.add(scoped_key)
            else:
                if "provider" not in _models_config[model_id]:
                    _models_config[model_id]["provider"] = name
        # Remove models from this provider that are no longer in the /models list
        if discovered:
            stale = [
                mid for mid, cfg in _models_config.items()
                if cfg.get("provider") == name
                and mid not in discovered
                and cfg.get("base_model_id", mid) not in discovered
                and not cfg.get("manual")
            ]
            for mid in stale:
                del _models_config[mid]

    # Remove orphaned models from providers that no longer exist
    all_prov_names = set((all_providers or providers).keys())
    orphaned = [
        mid for mid, cfg in _models_config.items()
        if cfg.get("provider") and cfg["provider"] not in all_prov_names
        and not cfg.get("manual")
    ]
    for mid in orphaned:
        del _models_config[mid]

    # Backfill defaults from KNOWN_MODELS for all models missing fields
    for model_id, cfg in _models_config.items():
        prov = cfg.get("provider", "") or ""
        known = _match_known_model(model_id, prov)
        if "max_context" not in cfg and "max_context" in known:
            cfg["max_context"] = known["max_context"]
        if "raw_formats" not in cfg and "raw_formats" in known:
            cfg["raw_formats"] = known["raw_formats"]
        if "thinking_format" not in cfg:
            cfg["thinking_format"] = known.get("thinking_format", "none")
        else:
            # Forward-looking re-detect: when the stored format is the
            # conservative default ('none') but the provider-aware detector
            # now produces a real format (e.g. cliproxyapi/gemini-2.5
            # gained provider-aware matching post-8.17.x), upgrade in place.
            # We never go in the other direction — that would clobber a
            # deliberate user "off" choice on a thinking-capable model.
            if cfg["thinking_format"] == "none":
                redetected = known.get("thinking_format", "none")
                if redetected != "none":
                    cfg["thinking_format"] = redetected

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


def get_api_model_id(model: str) -> str:
    """Return the actual model ID to send to the API.

    For provider-scoped models (e.g. 'mistral/devstral-small-latest'),
    returns the base model ID ('devstral-small-latest').
    """
    cfg = _models_config.get(model, {})
    return cfg.get("base_model_id", model)


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


def get_model_raw_formats(model: str) -> list[str]:
    """Return list of MIME patterns the model handles natively as multimodal.
    Checks models_config first, then falls back to KNOWN_MODELS family defaults."""
    cfg = _models_config.get(model, {})
    if "raw_formats" in cfg:
        return cfg["raw_formats"]
    # Fall back to KNOWN_MODELS family defaults
    ml = model.lower()
    for prefix, defaults in KNOWN_MODELS.items():
        if ml.startswith(prefix) or prefix in ml:
            return list(defaults.get("raw_formats", []))
    return []


def _mime_matches(mime_type: str, patterns: list[str]) -> bool:
    """Check if a MIME type matches any pattern (supports wildcard like 'image/*')."""
    for pat in patterns:
        if pat == mime_type:
            return True
        if pat.endswith("/*") and mime_type.startswith(pat[:-1]):
            return True
    return False


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


def _apply_inference_to_payload(payload: dict, params: dict, provider: str = "", scoped_model: str = "") -> None:
    """Apply resolved inference params to an OpenAI-compatible payload."""
    # Note: don't early-return when params is empty. We may still need to set
    # chat_template_kwargs.enable_thinking=false on oMLX thinking-capable models,
    # since their chat templates default thinking to ON when the kwarg is absent.
    params = params or {}

    is_omlx = provider == "omlx"

    for key in _INFERENCE_STANDARD_KEYS:
        if key in params:
            payload[key] = params[key]

    for key in _INFERENCE_OPENAI_KEYS:
        if key in params:
            payload[key] = params[key]

    for key in _INFERENCE_OMLX_KEYS:
        if key in params and is_omlx:
            payload[key] = params[key]

    # reasoning_effort is passed through verbatim for providers that support it
    if "reasoning_effort" in params:
        payload["reasoning_effort"] = params["reasoning_effort"]

    # Thinking routing: translate the UI's thinking_level string (low/medium/high)
    # into whatever the model's provider expects, based on the per-model thinking_format.
    _thinking_level = params.get("thinking_level")
    if _thinking_level and _thinking_level != "none":
        _scoped_id = scoped_model or payload.get("model", "")
        _fmt = _models_config.get(_scoped_id, {}).get("thinking_format", "none")
        if _fmt == "mistral_blocks":
            # Mistral accepts only "none" and "high" on reasoning_effort; any non-none
            # UI level maps to "high".
            payload["reasoning_effort"] = "high"
        elif _fmt in ("reasoning_field", "openai_opaque"):
            # OpenAI / Gemini via cliproxy / DeepSeek all accept low|medium|high.
            payload["reasoning_effort"] = _thinking_level

    # Legacy/oMLX chat template kwarg (kept for models that use it instead of reasoning_effort).
    # Qwen3 chat templates default enable_thinking=True when the kwarg is absent —
    # so we must send it EXPLICITLY in both directions, not only when turning on.
    # Otherwise the off toggle in the UI is silently ignored.
    if is_omlx:
        _scoped_id = scoped_model or payload.get("model", "")
        _fmt = _models_config.get(_scoped_id, {}).get("thinking_format", "none")
        if _fmt != "none":
            want_thinking = bool(params.get("thinking")) or (
                params.get("thinking_level") not in (None, "", "none")
            )
            payload.setdefault("chat_template_kwargs", {})["enable_thinking"] = want_thinking


def list_models(api_key: str, base_url: str) -> None:
    """List available models from the API."""
    models = get_available_models(api_key, base_url)
    if models:
        print("Available models:")
        for model_id in models:
            print(f"  {model_id}")
    else:
        print("No models available")
