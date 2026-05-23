"""
Worker Subagent Execution Module
================================

Runtime wrapper around tool dispatch. Routes heavy tools through a worker
path that writes raw output to the artifact store and returns a compact
envelope with an LLM-generated summary.

Phase 2: summariser LLM pass, WorkerRegistry with lifecycle state machine,
control primitives (status/abort/pause/resume/send/ask_user).
"""

from __future__ import annotations
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# ---------- Configuration ----------

DEFAULT_PROFILES: dict[str, dict] = {
    "exa_search":             {"heavy": "auto", "timeout_seconds": 60},
    "web_fetch":              {"heavy": "auto", "timeout_seconds": 60},
    "gmail_search":           {"heavy": True,  "timeout_seconds": 60},
    "gmail_inbox":            {"heavy": True,  "timeout_seconds": 60},
    "gmail_read":             {"heavy": True,  "timeout_seconds": 30},
    "search_files":           {"heavy": True,  "timeout_seconds": 30},
    "python_exec":            {"heavy": True,  "timeout_seconds": 300},
    "execute_command":        {"heavy": True,  "timeout_seconds": 300},
    "read_file":              {"heavy": False},
    "read_document":          {"heavy": False},
    "write_file":             {"heavy": False},
    "edit_file":              {"heavy": False},
    "write_document":         {"heavy": False},
    "edit_document":          {"heavy": False},
    "list_directory":         {"heavy": False},
    "mempalace_query":        {"heavy": False},
    "save_chat_to_memory":    {"heavy": False},
    "schedule_list":          {"heavy": False},
    "schedule_history":       {"heavy": False},
    "delegate_task":          {"heavy": False},
    "task_status":            {"heavy": False},
    "task_cancel":            {"heavy": False},
    "worker_status":          {"heavy": False},
    "worker_abort":           {"heavy": False},
    "worker_pause":           {"heavy": False},
    "worker_resume":          {"heavy": False},
    "worker_send":            {"heavy": False},
    "get_artifact_detail":    {"heavy": False},
    "worker_ask_user":        {"heavy": False},
    "ask_user":               {"heavy": False},
}

_config_cache: dict | None = None
_config_cache_time: float = 0.0
_config_lock = threading.Lock()


def _load_config() -> dict:
    """Load the 'execution' block from config.json, merged with defaults.

    Cached for 10 seconds to avoid repeated disk reads during batch tool calls.
    """
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache and (now - _config_cache_time) < 10:
        return _config_cache

    with _config_lock:
        if _config_cache and (now - _config_cache_time) < 10:
            return _config_cache

        cfg = {
            "workers_enabled": True,
            "auto_threshold_bytes": 8192,
            "worker_timeout_seconds": 120,
            "max_concurrent_workers_per_session": 3,
            "summariser_max_input_chars": 32000,
            "profiles": dict(DEFAULT_PROFILES),
        }
        try:
            from brain import CONFIG_PATH
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    loaded = json.load(f)
                exec_block = loaded.get("execution", {})
                cfg["workers_enabled"] = exec_block.get("workers_enabled", True)
                cfg["auto_threshold_bytes"] = exec_block.get("auto_threshold_bytes", 8192)
                cfg["worker_timeout_seconds"] = exec_block.get("worker_timeout_seconds", 120)
                cfg["max_concurrent_workers_per_session"] = exec_block.get(
                    "max_concurrent_workers_per_session", 3
                )
                cfg["summariser_max_input_chars"] = exec_block.get(
                    "summariser_max_input_chars", 32000
                )
                user_profiles = exec_block.get("profiles", {})
                cfg["profiles"].update(user_profiles)
        except Exception as e:
            import logging
            logging.getLogger("execution").warning(f"execution config load failed: {e}")

        _config_cache = cfg
        _config_cache_time = now
        return cfg


# ---------- Heaviness Resolution ----------

def _resolve_heaviness(tool_name: str, args: dict) -> str:
    """Return one of "heavy", "light", "auto".

    Resolution order: agent override > config profiles > DEFAULT_PROFILES > "auto".
    """
    from brain import get_request_context
    agent_overrides = get_request_context().execution_overrides or {}
    if tool_name in agent_overrides:
        value = agent_overrides[tool_name]
        if isinstance(value, bool):
            return "heavy" if value else "light"
        if value in ("heavy", "light", "auto"):
            return value

    cfg = _load_config()
    profile = cfg["profiles"].get(tool_name, {})
    heavy_field = profile.get("heavy", "auto")
    if isinstance(heavy_field, bool):
        return "heavy" if heavy_field else "light"
    return heavy_field if heavy_field in ("heavy", "light", "auto") else "auto"


# ---------- Worker Idempotency (Phase 1 dedup — separate from WorkerRegistry) ----------

_dedup_events: dict[tuple[str, str], threading.Event] = {}
_dedup_results: dict[tuple[str, str], str] = {}
_dedup_lock = threading.Lock()


def _acquire_worker_slot(key: tuple[str, str]) -> tuple[threading.Event, bool]:
    """Return (event, am_i_the_runner). The runner must call _release_worker_slot
    when done. Non-runners wait on the event."""
    with _dedup_lock:
        if key in _dedup_events:
            return _dedup_events[key], False
        ev = threading.Event()
        _dedup_events[key] = ev
        return ev, True


def _release_worker_slot(key: tuple[str, str], result: str) -> None:
    with _dedup_lock:
        _dedup_results[key] = result
        ev = _dedup_events.get(key)
    if ev:
        ev.set()


# ---------- Worker Lifecycle State Machine ----------

class WorkerState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"


_TERMINAL_STATES = {WorkerState.COMPLETED, WorkerState.FAILED,
                    WorkerState.TIMED_OUT, WorkerState.ABORTED}

_VALID_TRANSITIONS = {
    WorkerState.QUEUED: {WorkerState.RUNNING, WorkerState.ABORTED},
    WorkerState.RUNNING: {WorkerState.PAUSED, WorkerState.WAITING_FOR_USER,
                          WorkerState.COMPLETED, WorkerState.FAILED,
                          WorkerState.TIMED_OUT, WorkerState.ABORTED},
    WorkerState.PAUSED: {WorkerState.RUNNING, WorkerState.ABORTED},
    WorkerState.WAITING_FOR_USER: {WorkerState.RUNNING, WorkerState.ABORTED},
}


@dataclass
class Worker:
    worker_id: str
    session_id: str
    parent_call_id: str
    agent_id: str
    tool_name: str
    state: WorkerState = WorkerState.QUEUED
    started_at: float = 0.0
    phase: str = ""
    last_message: str = ""

    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    input_queue: queue.Queue = field(default_factory=queue.Queue)
    pending_question: dict | None = None
    answer_event: threading.Event = field(default_factory=threading.Event)
    answer_value: str | None = None

    artifacts: list[dict] = field(default_factory=list)
    thread: threading.Thread | None = None
    duration: float = 0.0
    abort_reason: str = ""
    flow: list[dict] = field(default_factory=list)


class WorkerRegistry:
    """Per-process registry of active and recently-completed workers. Thread-safe."""

    def __init__(self):
        self._workers: dict[str, Worker] = {}
        self._lock = threading.Lock()

    def register(self, worker: Worker) -> None:
        with self._lock:
            self._workers[worker.worker_id] = worker

    def get(self, worker_id: str) -> Worker | None:
        with self._lock:
            return self._workers.get(worker_id)

    def list_session(self, session_id: str) -> list[Worker]:
        with self._lock:
            return [w for w in self._workers.values() if w.session_id == session_id]

    def update_state(self, worker_id: str, new_state: WorkerState,
                     phase: str | None = None, message: str | None = None) -> bool:
        with self._lock:
            w = self._workers.get(worker_id)
            if not w:
                return False
            if w.state in _TERMINAL_STATES:
                return False
            allowed = _VALID_TRANSITIONS.get(w.state, set())
            if new_state not in allowed:
                return False
            w.state = new_state
            if phase is not None:
                w.phase = phase
            if message is not None:
                w.last_message = message
            return True

    def cancel(self, worker_id: str, reason: str = "") -> bool:
        w = self.get(worker_id)
        if not w:
            return False
        if w.state in _TERMINAL_STATES:
            return True  # idempotent
        w.abort_reason = reason
        w.cancel_event.set()
        self.update_state(worker_id, WorkerState.ABORTED,
                          message=f"aborted: {reason}")
        _append_flow(w, "state", state="ABORTED", reason=reason)
        _emit_worker_event("worker.aborted", {
            "worker_id": worker_id,
            "reason": reason,
        })
        return True

    def pause(self, worker_id: str, reason: str = "") -> bool:
        w = self.get(worker_id)
        if not w or w.state != WorkerState.RUNNING:
            return False
        w.pause_event.set()
        self.update_state(worker_id, WorkerState.PAUSED, message=f"paused: {reason}")
        _append_flow(w, "state", state="PAUSED", reason=reason)
        _emit_worker_event("worker.paused", {
            "worker_id": worker_id,
            "reason": reason,
        })
        return True

    def resume(self, worker_id: str) -> bool:
        w = self.get(worker_id)
        if not w or w.state != WorkerState.PAUSED:
            return False
        w.pause_event.clear()
        self.update_state(worker_id, WorkerState.RUNNING, message="resumed")
        _append_flow(w, "state", state="RUNNING", reason="resumed")
        _emit_worker_event("worker.resumed", {"worker_id": worker_id})
        return True

    def send(self, worker_id: str, message: str, role: str = "user") -> bool:
        w = self.get(worker_id)
        if not w or w.state in _TERMINAL_STATES:
            return False
        w.input_queue.put({"role": role, "content": message})
        if w.state == WorkerState.PAUSED:
            w.pause_event.clear()
            self.update_state(worker_id, WorkerState.RUNNING, message="resumed with input")
            _emit_worker_event("worker.resumed", {"worker_id": worker_id})
        return True

    def ask_user(self, worker_id: str, question: str, options: list[str] | None = None,
                 context_summary: str = "", timeout_seconds: int = 300) -> str | None:
        """Blocking call from worker thread. Returns answer or None on timeout."""
        w = self.get(worker_id)
        if not w:
            return None
        w.pending_question = {
            "question": question,
            "options": options,
            "context_summary": context_summary,
        }
        w.answer_event.clear()
        w.answer_value = None
        self.update_state(worker_id, WorkerState.WAITING_FOR_USER,
                          phase="waiting for user answer")
        _append_flow(w, "question", question=question, options=options,
                     context_summary=context_summary)
        _emit_worker_event("worker.question", {
            "worker_id": worker_id,
            "question": question,
            "options": options,
            "context_summary": context_summary,
            "timeout_seconds": timeout_seconds,
        })
        answered = w.answer_event.wait(timeout=timeout_seconds)
        w.pending_question = None
        if not answered or w.cancel_event.is_set():
            self.cancel(worker_id, "question_timeout")
            return None
        self.update_state(worker_id, WorkerState.RUNNING, phase="resumed after answer")
        _append_flow(w, "phase", phase="resumed after answer")
        return w.answer_value

    def answer(self, worker_id: str, answer_text: str) -> bool:
        w = self.get(worker_id)
        if not w or w.state != WorkerState.WAITING_FOR_USER:
            return False
        w.answer_value = answer_text
        w.answer_event.set()
        _append_flow(w, "answer", answer=answer_text)
        _emit_worker_event("worker.answered", {
            "worker_id": worker_id,
            "answer": answer_text,
        })
        return True

    def abort_session(self, session_id: str, reason: str = "session_deleted") -> int:
        """Abort all workers in a session. Returns count aborted."""
        workers = self.list_session(session_id)
        count = 0
        for w in workers:
            if w.state not in _TERMINAL_STATES:
                self.cancel(w.worker_id, reason)
                count += 1
        return count

    def to_status_dict(self, worker: Worker) -> dict:
        elapsed = time.time() - worker.started_at if worker.started_at else 0
        return {
            "worker_id": worker.worker_id,
            "tool": worker.tool_name,
            "state": worker.state.value,
            "started_at": worker.started_at,
            "elapsed_seconds": round(elapsed, 1),
            "phase": worker.phase,
            "last_message": worker.last_message,
            "artifacts_so_far": len(worker.artifacts),
            "has_pending_question": worker.pending_question is not None,
            "flow": list(worker.flow),
        }


# Module-level singleton
_worker_registry = WorkerRegistry()


def get_worker_registry() -> WorkerRegistry:
    return _worker_registry


# ---------- Artifact Storage ----------

_DATA_URI_RE = re.compile(r'data:([a-zA-Z0-9][a-zA-Z0-9!#$&\-^_]*/[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*);base64,([A-Za-z0-9+/=]+)')


def _extract_and_save_mcp_images_no_worker(
    raw_result: str,
    session_id: str,
    agent_id: str,
) -> str:
    """Like _extract_and_save_mcp_images but without a worker context (auto-isolate path)."""
    return _extract_and_save_mcp_images(raw_result, session_id, agent_id, worker=None)


def _extract_and_save_mcp_images(
    raw_result: str,
    session_id: str,
    agent_id: str,
    worker: "Worker | None",
) -> str:
    """Extract images from a raw MCP tool result and save as artifacts.

    Handles two formats:
    - _mcp_images key: list of {type, mimeType, data} blocks (standard MCP image blocks)
    - data:image/...;base64,... URIs embedded in the result text (Puppeteer style)

    Fires artifact_updated SSE for each saved image. Returns cleaned result.
    """
    try:
        parsed = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return raw_result

    if not isinstance(parsed, dict):
        return raw_result

    from brain import (
        _get_artifact_session_folder, _after_file_write,
        AGENTS_DIR,
    )
    import base64 as _b64

    _MIME_TO_EXT = {
        "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
        "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
        "audio/wav": "wav", "audio/wave": "wav", "audio/x-wav": "wav",
        "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
        "audio/flac": "flac", "audio/aac": "aac",
        "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
        "video/x-msvideo": "avi", "video/mpeg": "mpeg",
        "application/pdf": "pdf", "application/zip": "zip",
        "application/json": "json",
    }

    def _mime_to_ext(mime: str) -> str:
        if mime in _MIME_TO_EXT:
            return _MIME_TO_EXT[mime]
        # fallback: last part of subtype, strip +suffix
        return mime.split("/")[-1].split("+")[0].split(";")[0]

    folder = _get_artifact_session_folder(session_id)
    artifact_dir = os.path.join(AGENTS_DIR, agent_id, "artifacts", folder)
    os.makedirs(artifact_dir, exist_ok=True)

    saved_paths = []
    counter = [0]

    def _save_blob(mime: str, b64_data: str, hint: str = "mcp_output") -> str | None:
        try:
            ext = _mime_to_ext(mime)
            raw_bytes = _b64.b64decode(b64_data)
            counter[0] += 1
            suffix = f"_{counter[0]}" if counter[0] > 1 else ""
            fname = f"{hint}{suffix}.{ext}"
            fpath = os.path.join(artifact_dir, fname)
            with open(fpath, "wb") as f:
                f.write(raw_bytes)
            _after_file_write(fpath, "created", agent_id)
            if worker is not None:
                _append_flow(worker, "artifact",
                             artifact_id=fname, name=fname,
                             artifact_kind=mime.split("/")[0],
                             size_bytes=len(raw_bytes))
            return fpath
        except Exception as e:
            return f"(blob save failed: {e})"

    # 1. MCP structured blob blocks (_mcp_images legacy key + any _mcp_blobs)
    for key in ("_mcp_images", "_mcp_blobs"):
        for blob in parsed.pop(key, []):
            mime = blob.get("mimeType") or blob.get("media_type") or "application/octet-stream"
            data = blob.get("data", "")
            hint = "mcp_screenshot" if mime.startswith("image/") else "mcp_output"
            p = _save_blob(mime, data, hint)
            if p:
                saved_paths.append(p)

    # 2. data:<mime>;base64,<data> URIs anywhere in the result text
    result_text = parsed.get("result", "")
    if ";base64," in result_text:
        def _replace_data_uri(m: re.Match) -> str:
            mime = m.group(1)
            hint = "mcp_screenshot" if mime.startswith("image/") else "mcp_output"
            p = _save_blob(mime, m.group(2), hint)
            if p:
                saved_paths.append(p)
                return f"[saved as artifact: {os.path.basename(p)}]"
            return m.group(0)
        parsed["result"] = _DATA_URI_RE.sub(_replace_data_uri, result_text)

    if saved_paths:
        existing = parsed.get("result", "").rstrip()
        names = ", ".join(os.path.basename(p) for p in saved_paths)
        parsed["result"] = existing + f"\n\nSaved as artifact: {names}"

    return json.dumps(parsed)


def _store_worker_artifact(
    tool_name: str,
    args: dict,
    raw_result: str,
    session_id: str,
    tool_use_id: str,
) -> dict:
    """Write the raw tool result to the agent's artifact folder. Returns
    artifact metadata dict."""
    from brain import (
        _get_artifact_session_folder,
        AGENTS_DIR, get_request_context,
    )

    agent = get_request_context().current_agent
    agent_id = agent.agent_id if agent else "main"

    folder = _get_artifact_session_folder(session_id)
    artifact_dir = os.path.join(AGENTS_DIR, agent_id, "artifacts", folder)
    os.makedirs(artifact_dir, exist_ok=True)

    artifact_name = f"worker_{tool_name}_{tool_use_id[:8]}.json"
    artifact_path = os.path.join(artifact_dir, artifact_name)

    payload = {
        "tool": tool_name,
        "args": args,
        "raw_result": raw_result,
        "captured_at": time.time(),
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "size_bytes": len(raw_result.encode("utf-8")),
    }
    with open(artifact_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    # Deliberately NOT calling _after_file_write: worker JSON envelopes are
    # internal plumbing accessed only via get_artifact_detail. Registering them
    # with the code graph / artifact DB makes them discoverable via list_directory
    # and causes the model to read them unnecessarily on subsequent turns.

    return {
        "artifact_id": os.path.basename(artifact_path),
        "path": artifact_path,
        "kind": "tool_output",
        "tool": tool_name,
        "size_bytes": payload["size_bytes"],
    }


# ---------- SSE Event Emission ----------

def _emit_worker_event(event_type: str, payload: dict) -> None:
    """Emit an SSE event through the current session's event callback.
    No-op if no callback is registered."""
    from brain import get_request_context
    cb = get_request_context().event_callback
    if cb:
        try:
            cb(event_type, payload)
        except Exception:
            pass


def _append_flow(worker: "Worker", kind: str, **fields) -> dict:
    """Append a timestamped entry to worker.flow and broadcast worker.progress.

    `kind` is one of: phase, artifact, question, answer, state, error.
    Returns the appended entry (callers may further mutate in rare cases,
    but downstream consumers should treat it as immutable).
    """
    entry = {"kind": kind, "ts": time.time(), **fields}
    worker.flow.append(entry)
    _emit_worker_event("worker.progress", {
        "worker_id": worker.worker_id,
        "tool_call_id": worker.parent_call_id,
        "tool_name": worker.tool_name,
        "entry": entry,
    })
    return entry


# ---------- Summariser LLM Pass ----------

_SUMMARISER_SYSTEM = (
    "You are a tool-result summariser. Given a tool's raw output, produce a concise summary "
    "that captures the key information the calling agent needs to continue its task. "
    "Be factual and specific — include numbers, names, and key findings. "
    "Do NOT add commentary or caveats. "
    "After the summary, on a new line, output a JSON array of sections the agent might want "
    "to drill into, formatted as: SECTIONS: [{\"label\": \"...\", \"line_start\": N, \"line_end\": N}] "
    "If no meaningful sections exist, output: SECTIONS: []"
)


def _summarise_tool_result(
    tool_name: str,
    args: dict,
    raw_result: str,
    artifact_meta: dict,
    session_id: str,
) -> tuple[str, list[dict], dict]:
    """Return a static description of a worker tool result.

    The LLM summariser was retired with the Phase 5 variance-flag deletion
    (`tool_result_summariser=False`). Kept as a function (not inlined) because
    `run_worker_subagent` and `maybe_retroactive_isolate` both expect the
    3-tuple return shape — collapsing the callers is a step-7 concern.
    """
    return _static_summary(tool_name, artifact_meta), [], {"tokens_in": 0, "tokens_out": 0, "model": ""}


def _static_summary(tool_name: str, artifact_meta: dict) -> str:
    return (
        f"Tool '{tool_name}' completed. "
        f"Raw output ({artifact_meta['size_bytes']} bytes) "
        f"stored as artifact '{artifact_meta['artifact_id']}'. "
        f"Use get_artifact_detail to retrieve content."
    )



def _extract_web_references(tool_name: str, raw_result: str) -> list[dict]:
    """Pull lightweight reference metadata out of a web tool's raw result so the
    UI can render the References panel even though the full body only lives in
    the artifact. Returns [] for non-web tools or on parse failure.

    Shape matches the client's expectation in extractReferencesFromToolResult:
    each entry has title, link, domain, and (optional) snippet.
    """
    if tool_name not in ("exa_search", "web_fetch") or not raw_result:
        return []
    try:
        data = json.loads(raw_result)
    except Exception:
        return []
    from urllib.parse import urlparse
    def _domain(url: str) -> str:
        try:
            h = urlparse(url).hostname or ""
            return h[4:] if h.startswith("www.") else h
        except Exception:
            return ""
    refs: list[dict] = []
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        for r in data["results"]:
            if not isinstance(r, dict):
                continue
            url = r.get("link") or r.get("url")
            if not url:
                continue
            d = _domain(url)
            refs.append({
                "title": r.get("title") or d or url,
                "link": url,
                "snippet": (r.get("snippet") or "")[:200],
                "domain": d,
            })
    elif isinstance(data, dict) and data.get("url"):
        url = data["url"]
        d = _domain(url)
        title = d
        # web_fetch often returns the page content with a <title>; quick scan.
        body = data.get("content") or ""
        if isinstance(body, str):
            import re as _re
            m = _re.search(r"<title[^>]*>([^<]+)</title>", body, _re.I)
            if m:
                title = m.group(1).strip()
        refs.append({"title": title, "link": url, "snippet": "", "domain": d})
    return refs


def _parse_summariser_output(text: str) -> tuple[str, list[dict]]:
    """Parse summary text + SECTIONS: [...] from summariser output."""
    sections = []
    summary = text

    idx = text.rfind("SECTIONS:")
    if idx >= 0:
        summary = text[:idx].strip()
        sections_str = text[idx + len("SECTIONS:"):].strip()
        try:
            sections = json.loads(sections_str)
            if not isinstance(sections, list):
                sections = []
        except (json.JSONDecodeError, TypeError):
            sections = []

    return summary, sections


# ---------- Worker Execution ----------

def _generate_worker_id() -> str:
    return f"wkr_{int(time.time())}_{uuid.uuid4().hex[:4]}"


def run_worker_subagent(tool_name: str, args: dict, inner_fn: Callable[[str, dict], str]) -> str:
    """Execute a tool inside a worker with summariser pass.

    Returns a JSON envelope with LLM-generated summary and artifact references.
    inner_fn is claude_cli._execute_tool_inner, passed to avoid circular import.
    """
    from brain import get_request_context, _err, _ok

    session_id = get_request_context().current_session_id or ""
    tool_use_id = get_request_context().tool_use_id or f"local_{uuid.uuid4().hex[:8]}"
    key = (session_id, tool_use_id)

    event, is_runner = _acquire_worker_slot(key)

    if not is_runner:
        timeout = _load_config()["worker_timeout_seconds"]
        if not event.wait(timeout=timeout):
            return _err(f"worker_subagent: wait timeout after {timeout}s")
        return _dedup_results.get(key, _err("worker_subagent: no result after wait"))

    # Enforce concurrent worker cap
    cfg = _load_config()
    max_concurrent = cfg.get("max_concurrent_workers_per_session", 3)
    active = [w for w in _worker_registry.list_session(session_id)
              if w.state not in _TERMINAL_STATES]
    if len(active) >= max_concurrent:
        _release_worker_slot(key, _err(
            f"Worker limit reached ({max_concurrent} concurrent workers per session). "
            f"Wait for a running worker to finish or abort one."
        ))
        return _dedup_results[key]

    # Register in WorkerRegistry
    agent = get_request_context().current_agent
    agent_id = agent.agent_id if agent else "main"
    worker_id = _generate_worker_id()
    worker = Worker(
        worker_id=worker_id,
        session_id=session_id,
        parent_call_id=tool_use_id,
        agent_id=agent_id,
        tool_name=tool_name,
    )
    _worker_registry.register(worker)
    worker.started_at = time.time()
    _worker_registry.update_state(worker_id, WorkerState.RUNNING,
                                   phase="executing tool")
    _append_flow(worker, "phase", phase="executing tool")

    _emit_worker_event("worker.started", {
        "worker_id": worker_id,
        "tool_call_id": tool_use_id,
        "tool_name": tool_name,
    })

    # Store worker_id in thread-local so worker_ask_user can find it
    get_request_context().in_worker_subagent = True
    get_request_context().current_worker_id = worker_id
    start = time.time()
    try:
        # Safepoint: check cancel before execution
        if worker.cancel_event.is_set():
            raw_result = _err(f"{tool_name}: aborted before execution")
        else:
            raw_result = inner_fn(tool_name, args)
    except Exception as e:
        raw_result = _err(f"{tool_name}: {e}")
        _worker_registry.update_state(worker_id, WorkerState.FAILED,
                                       message=str(e))
        _append_flow(worker, "error", message=str(e))
    finally:
        get_request_context().in_worker_subagent = False
        get_request_context().current_worker_id = None
    duration = time.time() - start
    worker.duration = duration

    # Safepoint: check cancel after execution
    if worker.cancel_event.is_set() and worker.state != WorkerState.FAILED:
        _worker_registry.update_state(worker_id, WorkerState.ABORTED,
                                       message=worker.abort_reason or "cancelled")

    # Extract MCP image blobs before storing the JSON artifact
    raw_result = _extract_and_save_mcp_images(raw_result, session_id, agent_id, worker)

    # Store artifact
    if worker.state == WorkerState.RUNNING:
        _worker_registry.update_state(worker_id, WorkerState.RUNNING,
                                       phase="storing artifact")
        _append_flow(worker, "phase", phase="storing artifact")
    artifact_meta = _store_worker_artifact(
        tool_name, args, raw_result, session_id, tool_use_id
    )
    worker.artifacts.append(artifact_meta)
    _append_flow(worker, "artifact",
                 artifact_id=artifact_meta.get("artifact_id"),
                 name=artifact_meta.get("artifact_id"),
                 artifact_kind=artifact_meta.get("kind"),
                 size_bytes=artifact_meta.get("size_bytes"))

    # Summariser pass (safepoint: skip if cancelled)
    summary = _static_summary(tool_name, artifact_meta)
    sections = []
    summariser_usage = {"tokens_in": 0, "tokens_out": 0, "model": ""}
    if worker.state == WorkerState.RUNNING and not worker.cancel_event.is_set():
        _worker_registry.update_state(worker_id, WorkerState.RUNNING,
                                       phase="summarising")
        _append_flow(worker, "phase", phase="summarising")
        summary, sections, summariser_usage = _summarise_tool_result(
            tool_name, args, raw_result, artifact_meta, session_id
        )
        if summariser_usage.get("tokens_in") or summariser_usage.get("tokens_out"):
            _append_flow(worker, "summariser",
                         tokens_in=summariser_usage.get("tokens_in", 0),
                         tokens_out=summariser_usage.get("tokens_out", 0),
                         model=summariser_usage.get("model", ""))
            # Forward to parent session usage so the turn-level total is accurate
            _emit_worker_event("worker_usage", {
                "worker_id": worker_id,
                "tool_call_id": tool_use_id,
                "tokens_in": summariser_usage.get("tokens_in", 0),
                "tokens_out": summariser_usage.get("tokens_out", 0),
                "source": "summariser",
                "model": summariser_usage.get("model", ""),
            })

    # Terminal state
    if worker.state == WorkerState.RUNNING:
        _worker_registry.update_state(worker_id, WorkerState.COMPLETED,
                                       phase="done", message=summary[:100])
        _append_flow(worker, "phase", phase="done")

    envelope_body = {
        "worker": True,
        "worker_id": worker_id,
        "worker_phase": 2,
        "summary": summary,
        "sections": sections,
        "artifacts": [artifact_meta],
        "duration_seconds": round(duration, 3),
        "state": worker.state.value,
        "flow": list(worker.flow),
        "summariser_usage": summariser_usage,
    }
    # Surface lightweight reference metadata for web tools so the client can
    # populate the References panel without fetching the full artifact.
    web_refs = _extract_web_references(tool_name, raw_result)
    if web_refs:
        envelope_body["references"] = web_refs
    envelope = _ok(envelope_body)

    _emit_worker_event("worker.finished", {
        "worker_id": worker_id,
        "tool_call_id": tool_use_id,
        "tool_name": tool_name,
        "duration_seconds": round(duration, 3),
        "artifact_count": len(worker.artifacts),
        "state": worker.state.value,
    })

    _release_worker_slot(key, envelope)
    return envelope


# ---------- Auto-Threshold Retroactive Isolation ----------

def maybe_retroactive_isolate(tool_name: str, args: dict, result: str) -> str:
    """For tools declared 'auto', check output size and wrap retroactively
    if it exceeds the configured threshold."""
    threshold = _load_config()["auto_threshold_bytes"]
    if len(result.encode("utf-8")) <= threshold:
        return result

    from brain import get_request_context, _ok

    session_id = get_request_context().current_session_id or ""
    tool_use_id = get_request_context().tool_use_id or f"auto_{uuid.uuid4().hex[:8]}"
    agent_id = get_request_context().current_agent
    agent_id = agent_id.agent_id if agent_id else "main"

    # Extract images before storing the JSON artifact
    result = _extract_and_save_mcp_images_no_worker(result, session_id, agent_id)

    artifact_meta = _store_worker_artifact(
        tool_name, args, result, session_id, tool_use_id
    )

    summary, sections, _summariser_usage = _summarise_tool_result(
        tool_name, args, result, artifact_meta, session_id
    )

    return _ok({
        "worker": True,
        "worker_phase": 2,
        "auto_isolated": True,
        "summary": summary,
        "sections": sections,
        "artifacts": [artifact_meta],
    })


# ---------- The Router ----------

def route_tool_execution(
    tool_name: str,
    args: dict,
    inner_fn: Callable[[str, dict], str],
) -> str:
    """Entry point — runs the tool inline.

    The worker-subagent / auto-isolation paths were retired with the Phase 5
    variance-flag deletion. This thin wrapper is kept because brain._execute_tool
    still imports it; step 7 (native-loop deletion) collapses it.
    """
    return inner_fn(tool_name, args)
