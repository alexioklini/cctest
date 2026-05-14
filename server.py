#!/usr/bin/env python3
"""Brain Agent Server — HTTP API daemon for multi-frontend access."""

import argparse
import asyncio
import contextlib
import datetime
import hashlib
import io
import json
import os
import queue
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

_server_start_time = time.time()
_QMD_PORT = 8181
from frontends import telegram as _telegram_mod
from frontends import adapters as _adapters_mod
_QMD_PID_FILE = os.path.expanduser("~/.cache/qmd/mcp.pid")

import brain as engine
from server_lib import notifications as _notif_mod
from server_lib import tool_mcp as _tool_mcp_mod
from server_lib import auth as _auth_mod

# --- Notification Manager (initialized in main()) ---
_notification_manager: _notif_mod.NotificationManager | None = None

# Node Manager (in-memory registry for remote nodes) moved to server_lib/db.py.
# Re-exported here so handler mixins (which resolve names via globals
# inheritance) keep working.
from server_lib.db import (  # noqa: E402,F401
    _node_registry,
    _node_commands,
    _node_lock,
    _load_node_config,
    _save_node_config,
    _init_node_registry,
    _node_submit_command,
)

# --- Session Management with SQLite persistence ---
# Single source of truth lives in server_lib/db.py. Re-exported here so handler
# mixins (which resolve names from server.py's globals) keep working, and so
# `from server import CHAT_DB / _db_conn / _db_safe` callers continue to work.
from server_lib.db import CHAT_DB, _db_conn, _db_safe  # noqa: E402,F401


# ---------------------------------------------------------------------------
# MemPalaceClient — Phase 3 refactor
# ---------------------------------------------------------------------------
# Single access layer for all MemPalace operations in server.py.
# Initialized once at startup; all call sites use the singleton `_mp`.
# ---------------------------------------------------------------------------

class MemPalaceClient:
    """Thin wrapper around MemPalace imports + palace_path resolution.

    Initialized once at startup.  Exposes the three operation types:
      add_drawer(...)      – file a single drawer
      purge_by_prefix(...) – delete drawers whose source_file starts with prefix
      get_collection(...)  – raw ChromaDB collection handle (for callers that
                             need to iterate/query directly)
      get_closets_col(...) – closets collection handle
      mine(...)            – delegate to mempalace.miner.mine()

    All methods are safe to call even when MemPalace is disabled or not
    importable — they return None / empty results and log the reason once.
    """

    def __init__(self):
        self._palace_path: str = ""
        self._enabled: bool = False
        self._ready: bool = False
        self._import_error: str = ""
        # Lazily-imported references
        self._tool_add_drawer = None
        self._mp_miner = None
        self._get_collection = None
        self._get_closets_collection = None

    def _refresh(self):
        """Re-read config and (re)import MemPalace if needed. Idempotent."""
        try:
            mcfg = engine._load_mempalace_config()
        except Exception:
            return
        self._enabled = bool(mcfg.get("enabled", True))
        if not self._enabled:
            self._ready = False
            return
        self._palace_path = mcfg.get("palace_path", "")
        if self._ready:
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._import_error = err
            return
        try:
            from mempalace.mcp_server import tool_add_drawer
            from mempalace import miner as mp_miner
            from mempalace.palace import (
                get_collection,
                get_closets_collection,
            )
            self._tool_add_drawer = tool_add_drawer
            self._mp_miner = mp_miner
            self._get_collection = get_collection
            self._get_closets_collection = get_closets_collection
            self._ready = True
        except Exception as e:
            self._import_error = f"{type(e).__name__}: {e}"

    @property
    def palace_path(self) -> str:
        self._refresh()
        return self._palace_path

    @property
    def ready(self) -> bool:
        self._refresh()
        return self._ready

    def add_drawer(self, **kwargs):
        self._refresh()
        if not self._ready or not self._tool_add_drawer:
            return None
        return self._tool_add_drawer(**kwargs)

    def purge_by_prefix(self, wing: str, prefix: str) -> int:
        """Delete drawers + closets in `wing` whose source_file starts with
        prefix. Pass prefix='' to wipe the entire wing. Returns count deleted.
        """
        self._refresh()
        if not self._ready or not self._get_collection:
            return 0
        pp = self._palace_path
        if not pp or not os.path.isdir(pp):
            return 0
        deleted = 0
        try:
            # get_collection takes (palace_path, collection_name, create) —
            # no wing kwarg. Wing filtering must be done via metadata query.
            col = self._get_collection(pp, create=False)
            if col is None:
                return 0
            results = col.get(where={"wing": wing}, include=["metadatas"])
            ids_to_delete = [
                r_id for r_id, meta in zip(
                    results.get("ids", []),
                    results.get("metadatas", []))
                if (meta or {}).get("source_file", "").startswith(prefix)
            ]
            if ids_to_delete:
                col.delete(ids=ids_to_delete)
            deleted = len(ids_to_delete)
        except Exception as e:
            print(f"[MemPalaceClient] purge_by_prefix drawers failed "
                  f"wing={wing}: {type(e).__name__}: {e}", flush=True)
        # Also purge closets for this wing+prefix.
        try:
            ccol = self._get_closets_collection(pp, create=False)
            if ccol is not None:
                cres = ccol.get(where={"wing": wing}, include=["metadatas"])
                cids = [
                    cid for cid, meta in zip(
                        cres.get("ids", []),
                        cres.get("metadatas", []))
                    if (meta or {}).get("source_file", "").startswith(prefix)
                ]
                if cids:
                    ccol.delete(ids=cids)
        except Exception as e:
            print(f"[MemPalaceClient] purge_by_prefix closets failed "
                  f"wing={wing}: {type(e).__name__}: {e}", flush=True)
        return deleted

    def get_collection(self, wing: str = "", create: bool = False):
        self._refresh()
        if not self._ready or not self._get_collection:
            return None
        return self._get_collection(self._palace_path, wing=wing, create=create)

    def get_closets_col(self, wing: str = "", create: bool = False):
        self._refresh()
        if not self._ready or not self._get_closets_collection:
            return None
        return self._get_closets_collection(self._palace_path, wing=wing, create=create)

    def mine(self, **kwargs):
        self._refresh()
        if not self._ready or not self._mp_miner:
            return None
        if "palace_path" not in kwargs:
            kwargs["palace_path"] = self._palace_path
        return self._mp_miner.mine(**kwargs)


# Process-global singleton — initialized before any daemon thread starts.
_mp = MemPalaceClient()


# MemPalace wing/purge helpers moved to server_lib/db.py — re-exported here
# so handler mixins (which resolve names via globals inheritance) keep working.
from server_lib.db import (  # noqa: E402,F401
    _purge_mempalace_session,
    _purge_mempalace_turns,
    _project_wing,
    _project_chat_wing,
    _user_wing,
    _team_wing,
    _project_id_for_name,
    _resolve_session_wing,
    _memorize_mempalace_turns,
)


# ChatDB moved to server_lib/db.py — re-exported here so handler mixins
# (which resolve names via globals inheritance) keep working.
from server_lib.db import ChatDB  # noqa: E402,F401


class LiveStream:
    """Server-side event buffer for one in-progress chat turn.

    The agentic-loop worker thread runs independently of any HTTP connection.
    It pushes every SSE event into a LiveStream, which (a) appends the event to
    an ordered replay log and (b) fans it out to every currently-attached
    subscriber queue. A client opening `GET /v1/chat/stream?session_id=X` while a
    turn is in progress replays the log from the top, then receives live events
    until the terminal `done`/`error` — so reopening a streaming chat (or
    watching from a second tab) looks identical to having had it open all along.

    The worker survives a subscriber disconnect; only `/v1/chat/cancel` stops it.
    """

    def __init__(self):
        self.events: list[tuple] = []          # ordered (event_type, data) replay log
        self.subscribers: list = []            # list[queue.Queue]
        self.done = False                      # terminal: a `done` or `error` was emitted
        self.lock = threading.Lock()
        self.started_at = time.time()

    def emit(self, event_type, data):
        with self.lock:
            self.events.append((event_type, data))
            if event_type in ("done", "error"):
                self.done = True
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put((event_type, data))
            except Exception:
                pass

    def attach(self):
        """Register a new subscriber. Returns (queue, replay_snapshot, already_done)."""
        import queue as _q
        sub = _q.Queue()
        with self.lock:
            replay = list(self.events)
            already_done = self.done
            if not already_done:
                self.subscribers.append(sub)
        return sub, replay, already_done

    def detach(self, sub):
        with self.lock:
            try:
                self.subscribers.remove(sub)
            except ValueError:
                pass


class Session:
    """A conversation session with an agent."""

    def __init__(self, agent_id: str = "main", model: str | None = None,
                 api_key: str = "", base_url: str = "",
                 max_context: int = 131072, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        # Live event buffer for the in-progress turn (None when idle). Set by
        # _handle_chat before spawning the worker; cleared when the turn ends.
        self.live_stream: LiveStream | None = None
        self.model = model or ""
        self.api_key = api_key
        self.base_url = base_url
        self.max_context = max_context
        self.messages: list[dict] = []
        self.cancel_token = engine.CancelToken()
        self.created_at = time.time()
        self.last_active = time.time()
        self.title = ""
        self.status = "active"
        self.lock = threading.Lock()

        self.user_id: str = ""  # Owner user id (for MemPalace wing scoping)
        self.project: str | None = None  # Active project name (for scoped chat)
        self.note_context: str | None = None  # Note content for AI-assisted editing
        self.workflow_run_id: str = ""  # Bound workflow_history.execution_id
        self.summary: str = ""  # LLM-generated chat summary for sidebar
        self.sdk_session_id: str | None = None  # Agent SDK session ID for resume
        self._last_summary_at = 0  # Token count at last continuous summary
        self.save_to_memory: bool = False  # User toggle: always file to MemPalace
        self.caveman_mode: int = 0  # 0=off, 1=lite, 2=full, 3=ultra
        # Per-session research-mode override (sticky across turns).
        # None = use the project's `research_mode` default;
        # True/False = force the override for this chat. Set from the composer
        # button or session settings; persists in chats.db sessions table.
        self.research_mode_override: bool | None = None

        self._streaming = False  # True while a chat turn worker is running

        # Warmup state
        self._warmup_done = threading.Event()
        self._warmup_done.set()  # default: no warmup needed
        self._warmup_active = False
        self._warmup_cancel = threading.Event()
        self._warmup_lock = threading.Lock()

        self.agent = engine.AgentConfig(agent_id)
        self.memory = engine.MemoryStore(agent_id, base_dir=self.agent.memory_dir)

    def add_message(self, role: str, content, metadata=None):
        msg = {"role": role, "content": content}
        if metadata:
            msg["metadata"] = metadata
        with self.lock:
            self.messages.append(msg)
            self.last_active = time.time()
            # Auto-title from first user message
            if not self.title and role == "user":
                text = content if isinstance(content, str) else str(content)
                title = text[:80].strip()
                if len(title) > 60:
                    # Cut at last word boundary
                    title = title[:60].rsplit(' ', 1)[0]
                self.title = title
        ChatDB.save_message(self.id, role, content, metadata=metadata)
        ChatDB.save_session(self.id, self.agent_id, self.model, self.title,
                           self.status, self.created_at, self.last_active, self.project or "",
                           user_id=self.user_id)

    def switch_agent(self, agent_id: str, model: str | None = None):
        """Switch this session to a different agent (and optionally model)."""
        new_agent = engine.AgentConfig(agent_id)
        new_memory = engine.MemoryStore(agent_id, base_dir=new_agent.memory_dir)
        with self.lock:
            self.agent_id = agent_id
            self.agent = new_agent
            self.memory = new_memory
            if model:
                self.model = model

    def load_from_db(self):
        """Load messages from database (for restoring sessions)."""
        # Auto-repair corrupted sessions (dangling user messages, consecutive same-role)
        repaired = ChatDB.repair_session(self.id)
        if repaired:
            print(f"  Session {self.id[:12]}: repaired {repaired} dangling message(s)", flush=True)

        db_msgs = ChatDB.load_messages(self.id)
        loaded_messages = [{"role": m["role"], "content": m["content"]} for m in db_msgs]
        info = ChatDB.get_session_info(self.id)
        with self.lock:
            self.messages = loaded_messages
            if info:
                self.title = info.get("title", "")
                self.status = info.get("status", "active")
                self.created_at = info.get("created_at", self.created_at)
                self.last_active = info.get("last_active", self.last_active)
                self.project = info.get("project", "") or None
                self.summary = info.get("summary", "") or ""
                self.user_id = info.get("user_id", "") or ""
                self.save_to_memory = bool(info.get("save_to_memory", 0))
                self.caveman_mode = int(info.get("caveman_mode", 0) or 0)
                _rmo = info.get("research_mode_override", None)
                self.research_mode_override = (None if _rmo is None
                                                else bool(_rmo))
                self.workflow_run_id = info.get("workflow_run_id", "") or ""


class SessionManager:
    """Thread-safe session storage with SQLite persistence."""

    _LOADING_SENTINEL = object()  # Sentinel to prevent duplicate DB loads

    def __init__(self):
        self._sessions: dict[str, Session | object] = {}
        self._lock = threading.Lock()
        self._load_events: dict[str, threading.Event] = {}  # session_id -> Event for waiters
        ChatDB.init()
        from server_lib.favourites import FavouritesDB
        FavouritesDB.init()

    def create(self, **kwargs) -> Session:
        session = Session(**kwargs)
        ChatDB.save_session(session.id, session.agent_id, session.model,
                           session.title, session.status, session.created_at, session.last_active,
                           session.project or "")
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is self._LOADING_SENTINEL:
                # Another thread is loading this session — wait for it
                evt = self._load_events.get(session_id)
            elif s is not None:
                s.last_active = time.time()
                return s
            else:
                # Mark as loading to prevent duplicate construction
                self._sessions[session_id] = self._LOADING_SENTINEL
                evt = threading.Event()
                self._load_events[session_id] = evt
                s = None

        # If we were waiting on another thread's load
        if s is self._LOADING_SENTINEL:
            evt.wait(timeout=30)
            with self._lock:
                result = self._sessions.get(session_id)
                if result is not self._LOADING_SENTINEL and result is not None:
                    result.last_active = time.time()
                    return result
            return None

        # We are the loader — do DB load outside the lock
        info = ChatDB.get_session_info(session_id)
        if info:
            model = info.get("model", "")
            try:
                prov = BrainAgentHandler._resolve_provider_static(model) if model else {}
            except Exception:
                prov = {}
            loaded = Session(
                agent_id=info["agent_id"], model=model,
                api_key=prov.get("api_key", server_config.get("api_key", "")),
                base_url=prov.get("base_url", server_config.get("base_url", "")),
                max_context=engine.get_model_max_context(model) if model else server_config.get("max_context", 131072),
                session_id=session_id,
            )
            loaded.load_from_db()
            with self._lock:
                self._sessions[session_id] = loaded
                self._load_events.pop(session_id, None)
            evt.set()
            return loaded
        else:
            # Not in DB — remove sentinel
            with self._lock:
                if self._sessions.get(session_id) is self._LOADING_SENTINEL:
                    del self._sessions[session_id]
                self._load_events.pop(session_id, None)
            evt.set()
            return None

    def peek(self, session_id: str) -> Session | None:
        """Get a cached session without triggering DB load or updating last_active."""
        with self._lock:
            s = self._sessions.get(session_id)
            if s is self._LOADING_SENTINEL:
                return None
            return s

    def delete(self, session_id: str) -> bool:
        # Abort all running workers in this session
        try:
            from execution import get_worker_registry
            get_worker_registry().abort_session(session_id, "session_deleted")
        except Exception:
            pass
        with self._lock:
            self._sessions.pop(session_id, None)
        ChatDB.delete_session(session_id)
        return True

    def list_all(self) -> list[dict]:
        return ChatDB.list_sessions()

# --- Server globals ---

sessions = SessionManager()
server_config = {
    "api_key": "",
    "base_url": "http://localhost:8317/v1",
    "default_model": "claude-opus-4-5-20251101",
    "max_context": 131072,
}


# --- Request Handler ---

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# Keeper wake-up: set by callers that change the set of models the keeper should
# consider (model config save, warmup toggle) so the loop re-evaluates
# immediately instead of waiting up to interval_seconds.
_warmup_wakeup = threading.Event()


def _wake_warmup_keeper():
    _warmup_wakeup.set()


# Project-sync (input folders → project MemPalace wing) cross-thread state.
# - `_project_sync_wakeup`: Event the daemon waits on; setting it interrupts
#   the sleep so the next cycle starts now.
# - `_project_sync_requests`: set of (agent_id, project_name) the user pressed
#   "Sync now" on. The daemon drains it and processes those projects first.
# - `_project_sync_live`: dict[(agent, project)] -> {state, started_at, files_filed}
#   live snapshot the daemon updates so /sync-status reflects in-flight work.
_project_sync_wakeup = threading.Event()
_project_sync_requests: set[tuple[str, str]] = set()
_project_sync_request_triggers: dict[tuple[str, str], str] = {}
_project_sync_live: dict[tuple[str, str], dict] = {}
_project_sync_lock = threading.Lock()
# project_id strings of projects the user wants to cancel mid-sync.
_project_sync_cancel: set[str] = set()


def _project_sync_request(agent_id: str, project_name: str,
                          triggered_by: str = "manual"):
    with _project_sync_lock:
        _project_sync_requests.add((agent_id, project_name))
        _project_sync_request_triggers[(agent_id, project_name)] = triggered_by
    _project_sync_wakeup.set()


def _project_sync_cancel_request(project_id: str):
    with _project_sync_lock:
        _project_sync_cancel.add(project_id)


def _project_sync_cancel_check(project_id: str) -> bool:
    """Return True and consume the cancel signal if one is pending."""
    with _project_sync_lock:
        if project_id in _project_sync_cancel:
            _project_sync_cancel.discard(project_id)
            return True
    return False


def _project_sync_live_status(agent_id: str, project_name: str) -> dict:
    with _project_sync_lock:
        return dict(_project_sync_live.get((agent_id, project_name), {}))


def _project_sync_set_live(agent_id: str, project_name: str, **fields):
    with _project_sync_lock:
        cur = _project_sync_live.setdefault((agent_id, project_name), {})
        cur.update(fields)


def _project_sync_clear_live(agent_id: str, project_name: str):
    with _project_sync_lock:
        _project_sync_live.pop((agent_id, project_name), None)


def _filter_known_user_ids(ids) -> list[str]:
    """Validate a user-id list against the auth DB. Drops unknowns + duplicates,
    preserves order. Returns [] for non-list input."""
    if not isinstance(ids, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        if not isinstance(raw, str):
            continue
        uid = raw.strip()
        if not uid or uid in seen:
            continue
        if not _auth_mod.AuthDB.get_user(uid):
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _trigger_warmup(session):
    """Prime the KV cache for a session's model.

    Delegates to engine.run_model_warmup so there's a single payload shape
    (matches the keeper daemon's prime exactly) and the global _warmup_state
    registry gets updated — the UI's green/gray dot reads from that registry.
    """
    with session._warmup_lock:
        if session._warmup_active:
            session._warmup_cancel.set()
            session._warmup_done.wait(timeout=5)

        session._warmup_cancel.clear()
        session._warmup_done.clear()
        session._warmup_active = True

    def _do_warmup():
        try:
            if session._warmup_cancel.is_set():
                return
            mcfg = engine.resolve_model_settings(session.model) or {}
            wcfg = server_config.get("warmup", {}) or {}
            allow_cloud = bool(mcfg.get("warmup_allow_cloud",
                                        wcfg.get("allow_cloud", False)))
            # Session-level warmup is always "full": the user is about to
            # chat with this specific model, so priming its KV prefix is
            # exactly right even if it evicts other warmup models' caches.
            result = engine.run_model_warmup(
                session.model,
                allow_cloud=allow_cloud,
                agent_id=session.agent_id,
                timeout=int(wcfg.get("timeout_seconds", 30)),
                mode="full",
            )
            if session._warmup_cancel.is_set():
                print(f"  [warmup] {session.model} cancelled ({session.id[:8]})")
            elif result.get("ok"):
                print(f"  [warmup] {session.model} prefill done ({session.id[:8]}, {result.get('duration_ms')}ms)")
            else:
                print(f"  [warmup] {session.model} {result.get('state')}: {result.get('error','')}")
        finally:
            session._warmup_active = False
            session._warmup_done.set()
            # Kick the keeper so it can refill the pool now that this model is warm
            try:
                _wake_warmup_keeper()
            except Exception:
                pass

    threading.Thread(target=_do_warmup, daemon=True, name=f"warmup_{session.id[:8]}").start()


# --- Warm Session Pool ---
#
# Keeps at most one pre-built Session per warmup-flagged model, bound to the
# "main" agent and primed with a KV prefix. When the user creates a new chat
# on that model (with agent=main, no project), we hand them the pre-baked
# session instead of cold-starting one, then refill the pool in the background.
#
# Pooled sessions live in chats.db with status="warm_pool" so list_sessions
# naturally hides them from the sidebar (it only surfaces active/archived).

class WarmSessionPool:
    """Thread-safe, model-keyed pool of warm sessions bound to the main agent.

    Depth is configurable (warmup.pool_depth, default 3). All slots for a given
    model share the same KV prefix on the GPU (oMLX deduplicates by prompt
    content), so depth > 1 costs only a handful of KB of server RAM and a few
    extra rows in chats.db — zero GPU cost. The depth exists to absorb
    concurrent claims from multiple users hitting "new chat" at the same time.

    Slot state: a slot is 'ready' as soon as its Session wrapper is created
    (no per-slot prefill — the keeper's initial run_model_warmup already
    primed the KV prefix that every slot will reuse).
    """

    POOL_AGENT = "main"
    POOL_STATUS = "warm_pool"
    DEFAULT_DEPTH = 3

    def __init__(self):
        # model_id -> list[dict(session_id, built_at)]
        self._slots: dict[str, list[dict]] = {}
        # model_id -> int (how many build threads are currently in flight)
        self._building: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def target_depth(cls) -> int:
        """Read the configured pool depth, clamped to [1, 10]."""
        try:
            wcfg = server_config.get("warmup", {}) or {}
            d = int(wcfg.get("pool_depth", cls.DEFAULT_DEPTH))
            return max(1, min(10, d))
        except Exception:
            return cls.DEFAULT_DEPTH

    def _prune_dead(self, model: str):
        """Drop slots whose underlying Session no longer exists. Assumes lock held."""
        slots = self._slots.get(model) or []
        alive = [s for s in slots if s.get("session_id") and sessions.peek(s["session_id"])]
        if alive:
            self._slots[model] = alive
        elif model in self._slots:
            self._slots.pop(model, None)

    def ready_count(self, model: str) -> int:
        with self._lock:
            self._prune_dead(model)
            return len(self._slots.get(model, []))

    def building_count(self, model: str) -> int:
        with self._lock:
            return self._building.get(model, 0)

    def get_state(self, model: str) -> str:
        """Return 'empty' | 'building' | 'ready' | 'full'.

        'full' means the pool has reached target_depth; 'ready' means at least
        one slot is available; 'building' means zero ready but at least one
        build is in flight.
        """
        depth = self.target_depth()
        with self._lock:
            self._prune_dead(model)
            ready = len(self._slots.get(model, []))
            building = self._building.get(model, 0)
        if ready >= depth:
            return "full"
        if ready > 0:
            return "ready"
        if building > 0:
            return "building"
        return "empty"

    def all_states(self) -> dict[str, dict]:
        """Snapshot of all pool states for UI."""
        out = {}
        depth = self.target_depth()
        with self._lock:
            models = set(self._slots.keys()) | set(self._building.keys())
            for m in models:
                self._prune_dead(m)
                slots = self._slots.get(m, [])
                ready = len(slots)
                building = self._building.get(m, 0)
                if ready >= depth:
                    state = "full"
                elif ready > 0:
                    state = "ready"
                elif building > 0:
                    state = "building"
                else:
                    state = "empty"
                # Oldest build time = freshest slot the UI could hand out
                built_at = max((s.get("built_at", 0) for s in slots), default=0)
                out[m] = {
                    "state": state,
                    "ready": ready,
                    "building": building,
                    "target": depth,
                    "built_at": built_at,
                }
        return out

    def claim(self, model: str) -> "Session | None":
        """Pop and return one warm session for a model, if available. None otherwise.

        The caller is responsible for rebinding the session (user_id, status,
        project, etc.) and for triggering a pool refill.
        """
        with self._lock:
            self._prune_dead(model)
            slots = self._slots.get(model) or []
            if not slots:
                return None
            slot = slots.pop(0)  # FIFO: hand out oldest (likely warmest) first
            if not slots:
                self._slots.pop(model, None)
        sid = slot.get("session_id", "")
        if not sid:
            return None
        s = sessions.peek(sid)
        if s is None:
            s = sessions.get(sid)
        return s

    def invalidate_all(self, reason: str = ""):
        """Discard every pooled session (system prompt/agent config changed)."""
        with self._lock:
            victims: list[str] = []
            for slots in self._slots.values():
                for slot in slots:
                    sid = slot.get("session_id")
                    if sid:
                        victims.append(sid)
            self._slots.clear()
        for sid in victims:
            try:
                sessions.delete(sid)
            except Exception:
                pass
        if victims and reason:
            print(f"[warm-pool] invalidated {len(victims)} entries ({reason})")

    def invalidate_model(self, model: str, reason: str = ""):
        """Discard every pooled session for one model."""
        with self._lock:
            slots = self._slots.pop(model, None) or []
        if not slots:
            return
        for slot in slots:
            sid = slot.get("session_id")
            if sid:
                try:
                    sessions.delete(sid)
                except Exception:
                    pass
        if reason:
            print(f"[warm-pool] dropped {model} x{len(slots)} ({reason})")

    def try_build(self, model: str):
        """Top up the pool toward target_depth with background build threads.

        Each call fires one build thread per missing slot (cap at target_depth
        minus currently-building count). Safe to call repeatedly — races are
        guarded by the per-model building counter.
        """
        depth = self.target_depth()
        with self._lock:
            self._prune_dead(model)
            ready = len(self._slots.get(model, []))
            building = self._building.get(model, 0)
            needed = depth - ready - building
            if needed <= 0:
                return
            self._building[model] = building + needed

        def _build_one():
            try:
                provider = BrainAgentHandler._resolve_provider_static(model)
                session = sessions.create(
                    agent_id=self.POOL_AGENT,
                    model=model,
                    api_key=provider["api_key"],
                    base_url=provider["base_url"],
                    max_context=engine.get_model_max_context(model),
                )
                session.status = self.POOL_STATUS
                ChatDB.save_session(
                    session.id, session.agent_id, session.model,
                    session.title, session.status,
                    session.created_at, session.last_active,
                    session.project or "",
                )
                with self._lock:
                    self._slots.setdefault(model, []).append({
                        "session_id": session.id,
                        "built_at": time.time(),
                    })
                    ready_now = len(self._slots[model])
                print(f"[warm-pool] {model}: +1 ready ({session.id[:8]}, total {ready_now}/{depth})")
            except Exception as e:
                print(f"[warm-pool] {model}: build failed — {type(e).__name__}: {e}")
            finally:
                with self._lock:
                    remaining = self._building.get(model, 1) - 1
                    if remaining <= 0:
                        self._building.pop(model, None)
                    else:
                        self._building[model] = remaining

        for _ in range(needed):
            threading.Thread(
                target=_build_one, daemon=True,
                name=f"warm-pool-build-{model[:20]}",
            ).start()


warm_pool = WarmSessionPool()



from handlers.auth import AuthHandlerMixin
from handlers.chat import ChatHandlerMixin
from handlers.sessions_handler import SessionsHandlerMixin
from handlers.providers import ProvidersHandlerMixin
from handlers.projects import ProjectsHandlerMixin
from handlers.admin import AdminHandlerMixin
from handlers.favourites import FavouritesHandlerMixin
from handlers.translate import TranslateHandlerMixin
from handlers.share import ShareHandlerMixin

# Inject server-level globals into handler modules (they were originally
# defined in the same file and relied on shared module globals).
# We inject into each handler module's __dict__ so bare-name lookups work.
import sys as _sys
import types as _types

def _inject_server_globals():
    # server.py is the entry point so sys.modules['__main__'] == this module
    _srv = _sys.modules.get('__main__') or _sys.modules.get('server')
    if _srv is None:
        return
    _handler_mod_names = [
        AuthHandlerMixin.__module__,
        ChatHandlerMixin.__module__,
        SessionsHandlerMixin.__module__,
        ProvidersHandlerMixin.__module__,
        ProjectsHandlerMixin.__module__,
        AdminHandlerMixin.__module__,
        FavouritesHandlerMixin.__module__,
        TranslateHandlerMixin.__module__,
        ShareHandlerMixin.__module__,
    ]
    # All names from server module that handlers reference as bare globals.
    # Include modules aliased as simple names (e.g. engine, _auth_mod) since
    # some handler files use them bare without their own import.
    _to_inject = {k: v for k, v in vars(_srv).items()
                  if not k.startswith('__')}
    for _mod_name in _handler_mod_names:
        _mod = _sys.modules.get(_mod_name)
        if _mod:
            for _k, _v in _to_inject.items():
                if _k not in vars(_mod):
                    setattr(_mod, _k, _v)

_inject_server_globals()


class BrainAgentHandler(
    AuthHandlerMixin,
    ChatHandlerMixin,
    SessionsHandlerMixin,
    ProvidersHandlerMixin,
    ProjectsHandlerMixin,
    AdminHandlerMixin,
    FavouritesHandlerMixin,
    TranslateHandlerMixin,
    ShareHandlerMixin,
    BaseHTTPRequestHandler,
):
    """HTTP request handler for Brain Agent API."""
    # NOTE: Deliberately using HTTP/1.0 — SSE streaming works because we write
    # directly to the raw TCP socket (self.connection.sendall) with TCP_NODELAY.
    # Disable write buffering for real-time SSE streaming
    wbufsize = 0

    def log_message(self, format, *args):
        """Log requests to stdout."""
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        print(f"  {ts} {self.command} {self.path}", flush=True)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _get_session(self) -> Session | None:
        sid = self.headers.get("X-Session-ID", "")
        if not sid:
            body = self._read_json() if self.headers.get("Content-Length") else {}
            sid = body.get("session_id", "")
        return sessions.get(sid) if sid else None

    # --- Auth Middleware ---

    def _get_auth_user(self) -> dict | None:
        """Extract and validate JWT from Authorization: Bearer header."""
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        payload = _auth_mod.verify_token(token)
        if not payload:
            return None
        user = _auth_mod.AuthDB.get_user(payload["user_id"])
        if not user or user.get("disabled"):
            return None
        return user

    def _require_auth(self) -> dict | None:
        """Returns user dict or sends 401. Bypasses if auth disabled."""
        if not _auth_mod.auth_enabled():
            return _auth_mod.SYNTHETIC_ADMIN
        user = self._get_auth_user()
        if not user:
            self._send_json({"error": "Authentication required"}, 401)
            return None
        return user

    def _require_role(self, *roles) -> dict | None:
        """Require auth + specific role. Returns user or None (sends 403)."""
        user = self._require_auth()
        if not user:
            return None
        if user["role"] not in roles:
            self._send_json({"error": "Insufficient permissions"}, 403)
            return None
        return user

    def _require_capability(self, cap: str) -> dict | None:
        """Require auth + a capability flag. Admin always passes.
        Sends 403 on miss and returns None."""
        user = self._require_auth()
        if not user:
            return None
        if not _auth_mod.has_capability(user, cap):
            self._send_json({"error": f"Capability '{cap}' not granted"}, 403)
            return None
        return user

    # Path patterns that require specific capability flags. Checked by
    # _capability_gate() after _auth_gate() admits the request.
    # Each entry: (method, predicate_fn) -> capability name.
    @staticmethod
    def _path_requires_capability(method: str, path: str) -> str | None:
        """Return required capability name for this (method, path), or None."""
        # Projects -- any read/write under /v1/agents/<id>/projects* requires allow_projects
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sub = parts[1]
                if sub.startswith("projects") or sub == "ingest" or sub.startswith("ingested"):
                    return "allow_projects"
        return None

    # --- Routing ---

    # Paths that don't require auth
    _PUBLIC_GET_PATHS = {"/v1/status", "/v1/auth/me"}
    # /v1/tools/call has its own per-turn nonce auth (see server_lib/tool_mcp.py)
    # and is reached only from the sidecar over localhost — so it's exempt from
    # the user-auth gate. Bind to 127.0.0.1 only (see main()) to keep it safe.
    _PUBLIC_POST_PATHS = {"/v1/auth/login", "/v1/auth/refresh", "/v1/tools/call"}

    # Admin-only paths. Entries ending in "/" match as prefix (any subpath is
    # admin-only). Exact paths match exactly. These gate config mutations --
    # only admin can edit server/agent configuration. Per-user resources
    # (sessions, messages, projects, notes, artifacts) are gated separately
    # by ownership/ACL helpers, not by this whitelist.
    _ADMIN_GET_PATHS = {
        "/v1/auth/users",
        "/v1/providers",
        "/v1/mempalace/classifier",
        "/v1/mempalace/stats",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/services",
        "/v1/channels",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        "/v1/services/log",
        "/v1/traces/",
        "/v1/agents/",
    }
    _ADMIN_GET_PREFIXES = (
        "/v1/traces/",
    )
    _ADMIN_GET_EXACT = {
        "/v1/auth/users",
        "/v1/auth/audit",
        "/v1/providers",
        "/v1/mempalace/classifier",
        "/v1/mempalace/drawers",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/traces",
        "/v1/audit",
        "/v1/audit/export",
        "/v1/backup/info",
        "/v1/mcp/connections",
        "/v1/mcp/registry",
        "/v1/services",
        "/v1/quotas/config",
        "/v1/quotas/admin/users",
        "/v1/variance",
    }

    _ADMIN_POST_EXACT = {
        "/v1/auth/users",
        "/v1/auth/migrate",
        "/v1/auth/permissions",
        "/v1/restart",
        "/v1/providers",
        "/v1/providers/test",
        "/v1/providers/stats",
        "/v1/models/config",
        "/v1/services/server",
        "/v1/services/telegram",
        "/v1/settings/commands",
        "/v1/mempalace/classifier",
        "/v1/warmup/trigger",
        "/v1/backup",
        "/v1/restore",
        "/v1/mcp/connect",
        "/v1/mcp/disconnect",
        "/v1/tools/config",
        "/v1/context/config",
        "/v1/cache/clear",
        "/v1/channels",
        "/v1/agents/create",
        "/v1/agents/delete",
        "/v1/agents/rename",
        "/v1/skills/install-zip",
        "/v1/skills/remove",
        "/v1/skills/claude-code",
        "/v1/skills/claude-code/install",
        "/v1/commands/expand",
        "/v1/nodes",
        "/v1/variance",
    }
    _ADMIN_POST_PATHS = _ADMIN_POST_EXACT
    _ADMIN_POST_PREFIXES = (
        "/v1/channels/",
    )

    _ADMIN_AGENT_POST_SUBPATHS = (
        "file", "files", "hooks", "commands", "workflows", "soul-chat",
    )

    _ADMIN_DELETE_AGENT_SUBPATHS = (
        "workflows/", "ingested/",
    )

    def _is_admin_get(self, path: str) -> bool:
        if path in self._ADMIN_GET_EXACT:
            return True
        for p in self._ADMIN_GET_PREFIXES:
            if path.startswith(p):
                return True
        return False

    def _is_admin_post(self, path: str) -> bool:
        if path in self._ADMIN_POST_EXACT:
            return True
        for p in self._ADMIN_POST_PREFIXES:
            if path.startswith(p):
                return True
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2 and parts[0] not in ("create", "delete", "rename", "switch", "activity"):
                sub = parts[1]
                if sub.startswith("projects") or sub == "ingest" or sub.startswith("ingested"):
                    return False
                for allowed in self._ADMIN_AGENT_POST_SUBPATHS:
                    if sub == allowed or sub.startswith(allowed + "/"):
                        return True
        return False

    def _is_admin_delete(self, path: str) -> bool:
        if path.startswith("/v1/agents/"):
            rest = path[len("/v1/agents/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sub = parts[1]
                for prefix in self._ADMIN_DELETE_AGENT_SUBPATHS:
                    if sub.startswith(prefix):
                        return True
        return False

    def _auth_gate(self, path: str, public_paths: set, admin_paths: set, method: str = "GET") -> dict | None:
        """Check auth for API paths. Returns user dict or None (response already sent)."""
        if path in public_paths:
            return _auth_mod.SYNTHETIC_ADMIN if not _auth_mod.auth_enabled() else (self._get_auth_user() or _auth_mod.SYNTHETIC_ADMIN)
        user = self._require_auth()
        if not user:
            return None
        is_admin_required = False
        if method == "GET":
            is_admin_required = self._is_admin_get(path)
        elif method == "POST":
            is_admin_required = self._is_admin_post(path)
        elif method == "DELETE":
            is_admin_required = self._is_admin_delete(path)
        if not is_admin_required and path in admin_paths:
            is_admin_required = True
        if is_admin_required and user["role"] != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Admin access required"}, 403)
            return None
        needed_cap = self._path_requires_capability(method, path)
        if needed_cap and not _auth_mod.has_capability(user, needed_cap):
            self._send_json({"error": f"Capability '{needed_cap}' not granted"}, 403)
            return None
        return user

    # --- Shared helpers used across multiple mixins ---

    def _parse_agent_from_path(self, path: str) -> str:
        """Extract agent_id from /v1/agents/{id}/..."""
        parts = path.split("/")
        if len(parts) >= 4:
            return parts[3]
        return ""

    def _parse_project_from_path(self, path: str) -> str:
        """Extract project name from /v1/agents/{id}/projects/{name}/..."""
        parts = path.split("/")
        if len(parts) >= 6:
            return parts[5]
        return ""

    def _session_access_check(self, sid: str, *, require_manage: bool = False) -> dict | None:
        """Load session metadata and verify the caller can access it."""
        info = ChatDB.get_session_info(sid)
        if not info:
            self._send_json({"error": "Session not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        owner_uid = info.get("user_id") or ""
        team_id = info.get("team_id") or ""
        visibility = info.get("visibility") or "user"
        if user["role"] == "admin" or user["id"] == "__system__":
            return info
        if owner_uid and owner_uid == user["id"]:
            return info
        if not owner_uid:
            return info
        if visibility == "team" and team_id:
            my_teams = {t["id"]: t for t in _auth_mod.AuthDB.get_user_teams(user["id"])}
            if team_id in my_teams:
                if require_manage and my_teams[team_id]["head_user_id"] != user["id"]:
                    self._send_json({"error": "Only team head or session owner can modify"}, 403)
                    return None
                return info
        self._send_json({"error": "Access to this session is not permitted"}, 403)
        return None

    def _project_access_check(self, agent_id: str, proj_name: str, *, require_manage: bool = False) -> dict | None:
        """Load project.json, enforce visibility/ownership."""
        project = engine.ProjectManager.get_project(agent_id, proj_name)
        if not project:
            self._send_json({"error": f"Project '{proj_name}' not found"}, 404)
            return None
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_project(user, project):
            self._send_json({"error": "Access to this project is not permitted"}, 403)
            return None
        if require_manage:
            if not _auth_mod.can_manage_project(user, project):
                self._send_json({"error": "Only the project owner (or admin) can modify this project"}, 403)
                return None
        return project

    @staticmethod
    def _resolve_provider_static(model: str) -> dict:
        """Find the provider that has the given model."""
        if engine._models_config:
            model = engine.resolve_model(model)
        return engine.resolve_provider_for_model(model)

    def _resolve_provider(self, model: str) -> dict:
        return BrainAgentHandler._resolve_provider_static(model)

    # --- Dispatch ---

    _SDK_NATIVE_TOOLS = {
        "read_file", "write_file", "edit_file", "list_directory", "search_files",
        "execute_command", "web_fetch",
    }

    def do_GET(self):
        path = self.path.split("?")[0]

        # Serve static files without auth
        if path == "/" or path.startswith("/web/"):
            pass
        # Auth endpoints
        elif path == "/v1/auth/me":
            self._handle_auth_me()
            return
        elif path == "/v1/auth/profile-doc":
            self._handle_auth_profile_doc_get()
            return
        elif path == "/v1/auth/users":
            self._handle_auth_users_list()
            return
        elif path == "/v1/auth/users/lookup":
            self._handle_auth_users_lookup()
            return
        elif path == "/v1/auth/permissions":
            self._handle_auth_permissions_get()
            return
        elif path == "/v1/auth/audit":
            self._handle_auth_audit_list()
            return
        elif path == "/v1/user-teams":
            self._handle_user_teams_list()
            return

        # Auth gate for all /v1/* paths
        if path.startswith("/v1/"):
            user = self._auth_gate(path, self._PUBLIC_GET_PATHS, self._ADMIN_GET_PATHS, method="GET")
            if not user:
                return
            self._auth_user = user

        if path == "/v1/tools/list":
            self._handle_tools_list()
        elif path == "/v1/chat/stream":
            self._handle_chat_stream()
        elif path == "/v1/status":
            self._handle_status()
        elif path == "/v1/agents":
            self._handle_list_agents()
        elif path.startswith("/v1/agents/") and "/files" in path:
            self._handle_agent_files(path)
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_read(path)
        elif path == "/v1/models":
            self._handle_list_models()
        elif path == "/v1/sessions":
            self._handle_list_sessions()
        elif path.startswith("/v1/sessions/search"):
            self._handle_session_search()
        elif path.startswith("/v1/sessions/") and path.endswith("/inspect"):
            self._handle_session_inspect(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/files"):
            self._handle_get_session_files(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/messages"):
            self._handle_get_messages(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/next-prompt"):
            self._handle_next_prompt_suggestion(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup"):
            sid = path.split("/")[3]
            s = sessions.get(sid)
            self._send_json({"warming_up": s._warmup_active if s else False})
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup-status"):
            sid = path.split("/")[3]
            s = sessions.get(sid)
            if not s:
                self._send_json({"warmup": False, "warming_up": False})
            else:
                mcfg = engine.resolve_model_settings(s.model)
                warmup_enabled = bool(mcfg.get("warmup", False))
                self._send_json({"warmup": warmup_enabled, "warming_up": s._warmup_active})
        elif path == "/v1/schedule":
            self._handle_list_schedule()
        elif path == "/v1/translate/tts/voices":
            self._handle_translate_tts_voices()
        elif path == "/v1/translate/history":
            self._handle_translate_history_list()
        elif path.startswith("/v1/translate/history/") and "/file" in path:
            # /v1/translate/history/<id>/file?which=...
            tail = path[len("/v1/translate/history/"):]
            entry_id = tail.split("/", 1)[0]
            self._handle_translate_history_file(entry_id)
        elif path == "/v1/translate/glossaries":
            self._handle_glossaries_list()
        elif path.startswith("/v1/translate/glossaries/"):
            slug = path[len("/v1/translate/glossaries/"):]
            self._handle_glossary_get(slug)
        elif path.startswith("/v1/translate/jobs/") and path.endswith("/result"):
            jid = path[len("/v1/translate/jobs/"):-len("/result")]
            self._handle_translate_job_result(jid)
        elif path.startswith("/v1/translate/jobs/"):
            jid = path[len("/v1/translate/jobs/"):]
            self._handle_translate_job_status(jid)
        elif path.startswith("/v1/translate/live/"):
            sid = path[len("/v1/translate/live/"):]
            self._handle_live_stream(sid)
        elif path == "/v1/tasks":
            self._handle_list_tasks()
        elif path == "/v1/schedule/running":
            self._handle_running_tasks()
        elif path == "/v1/providers":
            self._handle_list_providers()
        elif path.startswith("/v1/providers/stats"):
            self._handle_provider_stats()
        elif path == "/v1/models/config":
            self._handle_models_config_get()
        elif path == "/v1/agents/activity":
            self._handle_agents_activity()
        elif path == "/v1/workflows/executions":
            self._handle_workflow_list_executions()
        elif path.startswith("/v1/workflows/executions/"):
            self._handle_workflow_get_execution(path)
        elif path == "/v1/workflows/history":
            self._handle_workflow_history(path)
        elif path.startswith("/v1/workflows/history/") and "/file" in path:
            # /v1/workflows/history/<exec_id>/file  → download a file the run touched
            # /v1/workflows/history/<exec_id>/file-preview → preview a file the run touched
            if "/file-preview" in path:
                self._handle_workflow_run_file_preview(path)
            else:
                self._handle_workflow_run_file_download(path)
        elif path.startswith("/v1/workflows/history/"):
            self._handle_workflow_history_get(path)
        elif path.startswith("/v1/agents/") and "/workflows" in path:
            self._handle_workflow_list(path)
        elif path == "/v1/teams":
            self._handle_teams_get()
        elif path == "/v1/services":
            self._handle_services_status()
        elif path.startswith("/v1/services/log"):
            self._handle_service_log()
        elif path.startswith("/v1/agents/") and path.endswith("/commands"):
            self._handle_agent_commands_get(path)
        elif path == "/v1/costs":
            self._handle_costs()
        elif path == "/v1/costs/daily":
            self._handle_costs_daily()
        elif path == "/v1/quotas/me":
            self._handle_quota_me()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_get()
        elif path == "/v1/variance":
            self._handle_variance_get()
        elif path == "/v1/quotas/admin/users":
            self._handle_quota_admin_users()
        elif path.startswith("/v1/quotas/admin/breakdown"):
            self._handle_quota_admin_breakdown()
        elif path == "/v1/cache/stats":
            self._send_json(engine._web_cache.stats())
        elif path == "/v1/warmup/status":
            self._handle_warmup_status()
        elif path == "/v1/queue/status":
            self._handle_queue_status()
        elif path == "/v1/traces" or path.startswith("/v1/traces?"):
            self._handle_traces_list()
        elif path.startswith("/v1/traces/"):
            self._handle_trace_detail(path)
        elif path == "/v1/audit" or path.startswith("/v1/audit?"):
            self._handle_audit_list()
        elif path.startswith("/v1/audit/export"):
            self._handle_audit_export()
        elif path.startswith("/v1/agents/") and path.endswith("/hooks"):
            self._handle_hooks_get(path)
        elif path == "/v1/context/config":
            self._handle_context_config_get()
        elif path.startswith("/v1/context/stats"):
            self._handle_context_stats()
        elif path == "/v1/mempalace/stats":
            self._handle_mempalace_stats()
        elif path == "/v1/mempalace/classifier":
            self._handle_mempalace_classifier_get()
        elif path == "/v1/mempalace/activity":
            self._send_json(engine.mempalace_activity.snapshot())
        elif path.startswith("/v1/mempalace/session-turns"):
            self._handle_mempalace_session_turns()
        elif path.startswith("/v1/mempalace/drawers"):
            self._handle_mempalace_drawers()
        elif path == "/v1/mempalace/kg/stats":
            self._handle_kg_stats_global()
        elif path == "/v1/mempalace/kg/wing":
            self._handle_kg_wing_detail(self._kg_qs())
        elif path == "/v1/mempalace/kg/entity":
            self._handle_kg_entity_detail(self._kg_qs())
        elif path == "/v1/mempalace/kg/extraction-log":
            self._handle_kg_extraction_log(self._kg_qs())
        elif path == "/v1/mempalace/kg/config":
            self._handle_kg_config_get()
        elif path == "/v1/mcp/connections":
            self._handle_mcp_list()
        elif path == "/v1/mcp/registry":
            self._handle_mcp_registry()
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes" in path:
            self._handle_notes(path, "GET")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs" in path:
            self._handle_project_docs(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/input-folders"):
            self._handle_project_input_folders_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-status"):
            self._handle_project_sync_status(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-runs"):
            self._handle_project_sync_runs(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/sync-runs/" in path:
            self._handle_project_sync_run_detail(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/image"):
            self._handle_project_image_get(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_get(path)
        elif path.startswith("/v1/agents/") and path.endswith("/projects"):
            self._handle_list_projects(path)
        elif path.startswith("/v1/agents/") and path.endswith("/ingested"):
            self._handle_list_ingested(path)
        elif path == "/v1/skills/claude-code":
            self._handle_cc_skills_list()
        elif path == "/v1/notifications":
            self._handle_notifications_list()
        elif path == "/v1/notifications/unread":
            self._handle_notifications_unread()
        elif path == "/v1/backup/info":
            self._handle_backup_info()
        elif path == "/v1/workers":
            self._handle_workers_list()
        elif path == "/v1/workers/recent":
            self._handle_workers_recent()
        elif path == "/v1/nodes":
            self._handle_nodes_list()
        elif path.startswith("/v1/nodes/poll"):
            self._handle_node_poll()
        elif path == "/v1/channels":
            self._handle_channels_list()
        elif path == "/v1/tools/config":
            self._handle_tools_config_get()
        elif path == "/v1/tools/status":
            self._handle_tools_status()
        elif path == "/v1/tools/breakdown":
            self._handle_tools_breakdown()
        elif path == "/v1/files/download":
            self._handle_file_download()
        elif path == "/v1/files/preview":
            self._handle_file_preview()
        elif path == "/v1/files/tree":
            self._handle_file_tree()
        elif path == "/v1/favourites":
            self._handle_favourites_list()
        elif path.startswith("/v1/favourites/image/"):
            self._handle_favourites_image_get(path)
        elif path == "/v1/share":
            self._handle_share_get()
        elif path == "/v1/artifacts":
            self._handle_artifacts_list()
        elif path == "/v1/artifacts/browse":
            self._handle_artifacts_browse()
        elif path.startswith("/v1/artifacts/") and path.endswith("/content"):
            self._handle_artifact_content(path)
        elif path.startswith("/v1/artifacts/") and path.endswith("/download"):
            self._handle_artifact_download(path)
        elif path == "/" or path.startswith("/web/") or path.endswith((".html", ".css", ".js", ".ico", ".woff2", ".woff")):
            self._serve_static(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        # Auth endpoints (public)
        if path == "/v1/auth/login":
            self._handle_auth_login()
            return
        elif path == "/v1/auth/register":
            self._handle_auth_register()
            return
        elif path == "/v1/auth/refresh":
            self._handle_auth_refresh()
            return
        elif path == "/v1/auth/password":
            self._handle_auth_password()
            return
        elif path == "/v1/auth/profile":
            self._handle_auth_profile()
            return
        elif path == "/v1/auth/preferences":
            self._handle_auth_preferences()
            return
        elif path == "/v1/auth/profile-doc":
            self._handle_auth_profile_doc_post()
            return
        elif path == "/v1/auth/profile-doc/update-now":
            self._handle_auth_profile_doc_update_now()
            return
        elif path == "/v1/auth/profile-doc/reset":
            self._handle_auth_profile_doc_reset()
            return
        elif path == "/v1/auth/users":
            self._handle_auth_users_manage()
            return
        elif path == "/v1/auth/migrate":
            self._handle_auth_migrate()
            return
        elif path == "/v1/auth/permissions":
            self._handle_auth_permissions_manage()
            return
        elif path == "/v1/user-teams":
            self._handle_user_teams_manage()
            return

        # Auth gate for all /v1/* paths
        if path.startswith("/v1/") or path == "/mcp":
            user = self._auth_gate(path, self._PUBLIC_POST_PATHS, self._ADMIN_POST_PATHS, method="POST")
            if not user:
                return
            self._auth_user = user

        if path == "/mcp":
            self._handle_mcp_jsonrpc()
        elif path == "/v1/favourites":
            self._handle_favourites_add()
        elif path.startswith("/v1/favourites/") and path.endswith("/image"):
            self._handle_favourites_image_upload(path)
        elif path == "/v1/share":
            self._handle_share_update()
        elif path == "/v1/share/transfer":
            self._handle_share_transfer()
        elif (path.startswith("/v1/agents/") and "/projects/" in path
              and path.endswith("/image")):
            self._handle_project_image_upload(path)
        elif path == "/v1/sessions":
            self._handle_create_session()
        elif path == "/v1/chat":
            self._handle_chat()
        elif path == "/v1/chat/cancel":
            self._handle_cancel()
        elif path == "/v1/tools/call":
            _tool_mcp_mod.handle_tools_call(self)
        elif path == "/v1/sessions/manage":
            self._handle_manage_session()
        elif path == "/v1/agents/switch":
            self._handle_switch_agent()
        elif path == "/v1/agents/create":
            self._handle_create_agent()
        elif path == "/v1/agents/delete":
            self._handle_delete_agent()
        elif path == "/v1/agents/rename":
            self._handle_rename_agent()
        elif path.startswith("/v1/agents/") and path.endswith("/soul-chat"):
            self._handle_soul_chat(path)
        elif path.startswith("/v1/agents/") and "/file" in path:
            self._handle_agent_file_write(path)
        elif path == "/v1/schedule":
            self._handle_modify_schedule()
        elif path == "/v1/schedule/upload":
            self._handle_schedule_upload()
        elif path == "/v1/providers":
            self._handle_save_providers()
        elif path == "/v1/providers/test":
            self._handle_test_provider()
        elif path == "/v1/models/config":
            self._handle_models_config_save()
        elif path == "/v1/queue/cancel":
            self._handle_queue_cancel()
        elif path == "/v1/warmup/trigger":
            self._handle_warmup_trigger()
        elif path == "/v1/skills/install-zip":
            self._handle_install_skill_zip()
        elif path == "/v1/schedule/cancel":
            self._handle_cancel_scheduled()
        elif path == "/v1/skills/remove":
            self._handle_remove_skill()
        elif path == "/v1/skills/claude-code":
            self._handle_cc_skills_manage()
        elif path == "/v1/skills/claude-code/browse":
            self._handle_cc_browse()
        elif path == "/v1/skills/claude-code/install":
            self._handle_cc_install()
        elif path == "/v1/commands/expand":
            self._handle_expand_command()
        elif path == "/v1/settings/commands":
            self._handle_settings_commands()
        elif path == "/v1/restart":
            self._handle_restart()
        elif path == "/v1/teams":
            self._handle_teams_post()
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/approve"):
            self._handle_workflow_approve(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/cancel"):
            self._handle_workflow_cancel(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/upload-file"):
            self._handle_workflow_upload_file(path)
        elif path.startswith("/v1/workflows/history/") and "/promote-session/" in path:
            self._handle_workflow_promote_session(path)
        elif path.startswith("/v1/workflows/history/") and path.endswith("/session"):
            self._handle_workflow_get_or_create_session(path)
        elif path.startswith("/v1/agents/") and "/workflows/" in path and "/run" in path:
            self._handle_workflow_run(path)
        elif path.startswith("/v1/agents/") and "/workflows" in path:
            self._handle_workflow_save(path)
        elif path == "/v1/services/telegram":
            self._handle_telegram_action()
        elif path == "/v1/services/server":
            self._handle_server_config()
        elif path == "/v1/mempalace/classifier":
            self._handle_mempalace_classifier_save()
        elif path == "/v1/mempalace/kg/config":
            self._handle_kg_config_save()
        elif path == "/v1/mempalace/kg/reextract":
            self._handle_kg_reextract()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_save()
        elif path == "/v1/variance":
            self._handle_variance_save()
        elif path == "/v1/cache/clear":
            engine._web_cache.clear()
            self._send_json({"status": "cleared"})
        elif path.startswith("/v1/sessions/") and path.endswith("/warmup"):
            sid = path.split("/")[3]
            s = sessions.get(sid)
            if not s:
                self._send_json({"error": "Session not found"}, 404)
                return
            body = self._read_json()
            new_model = body.get("model")
            if new_model and new_model != s.model:
                s.model = new_model
                try:
                    prov = self._resolve_provider(new_model)
                    s.api_key = prov["api_key"]
                    s.base_url = prov["base_url"]
                except Exception:
                    pass
            mcfg = engine.resolve_model_settings(s.model)
            warmup_enabled = bool(mcfg.get("warmup", False))
            if warmup_enabled and not s._warmup_active:
                _trigger_warmup(s)
            self._send_json({"warmup": warmup_enabled, "warming_up": s._warmup_active})
        elif path == "/v1/chat/answer":
            self._handle_chat_answer()
        elif path == "/v1/notifications/settings":
            self._handle_notifications_settings_post()
        elif path == "/v1/notifications/dismiss":
            self._handle_notifications_dismiss()
        elif path == "/v1/notifications/read":
            self._handle_notifications_read()
        elif path == "/v1/backup":
            self._handle_backup_create()
        elif path == "/v1/restore":
            self._handle_restore()
        elif path == "/v1/refine":
            self._handle_refine()
        elif path == "/v1/translate/tts/voices":
            self._handle_translate_tts_voices()
        elif path == "/v1/translate/tts":
            self._handle_translate_tts()
        elif path == "/v1/translate/detect":
            self._handle_translate_detect()
        elif path == "/v1/translate/text":
            self._handle_translate_text()
        elif path == "/v1/translate/document":
            self._handle_translate_document_upload()
        elif path == "/v1/translate/media":
            self._handle_translate_media_upload()
        elif path == "/v1/translate/live/start":
            self._handle_live_start()
        elif path.startswith("/v1/translate/live/") and path.endswith("/chunk"):
            sid = path[len("/v1/translate/live/"):-len("/chunk")]
            self._handle_live_chunk(sid)
        elif path.startswith("/v1/translate/live/") and path.endswith("/stop"):
            sid = path[len("/v1/translate/live/"):-len("/stop")]
            self._handle_live_stop(sid)
        elif path == "/v1/translate/glossaries":
            self._handle_glossary_save()
        elif path.startswith("/v1/agents/") and path.endswith("/commands"):
            self._handle_agent_commands_post(path)
        elif path == "/v1/mcp/connect":
            self._handle_mcp_connect()
        elif path == "/v1/mcp/disconnect":
            self._handle_mcp_disconnect()
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes" in path:
            self._handle_notes(path, "POST")
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/input-folders"):
            self._handle_project_input_folders_add(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/input-folders/" in path:
            self._handle_project_input_folders_update(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-now"):
            self._handle_project_sync_now(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/full-resync"):
            self._handle_project_full_resync(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-cancel"):
            self._handle_project_sync_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/ingest" in path:
            self._handle_project_ingest(path)
        elif path.startswith("/v1/agents/") and path.endswith("/projects"):
            self._handle_create_project(path)
        elif path.startswith("/v1/agents/") and path.endswith("/ingest"):
            self._handle_agent_ingest(path)
        elif path == "/v1/nodes":
            self._handle_nodes_action()
        elif path == "/v1/nodes/result":
            self._handle_node_result()
        elif path == "/v1/nodes/execute":
            self._handle_node_execute()
        elif path == "/v1/tools/config":
            self._handle_tools_config_save()
        elif path.startswith("/v1/agents/") and path.endswith("/hooks"):
            self._handle_hooks_save(path)
        elif path == "/v1/context/compact":
            self._handle_context_compact()
        elif path == "/v1/context/uncompact":
            self._handle_context_uncompact()
        elif path == "/v1/context/config":
            self._handle_context_config_save()
        elif path == "/v1/channels":
            self._handle_channels_action()
        elif path.startswith("/v1/channels/") and path.endswith("/start"):
            self._handle_channel_lifecycle(path, "start")
        elif path.startswith("/v1/channels/") and path.endswith("/stop"):
            self._handle_channel_lifecycle(path, "stop")
        elif path.startswith("/v1/channels/") and path.endswith("/restart"):
            self._handle_channel_lifecycle(path, "restart")
        elif path.startswith("/v1/workers/") and path.endswith("/answer"):
            self._handle_worker_answer(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if path.startswith("/v1/"):
            user = self._auth_gate(path, set(), set(), method="PUT")
            if not user:
                return
            self._auth_user = user
        if path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "PUT")
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_update(path)
        elif path.startswith("/v1/favourites/"):
            self._handle_favourites_patch(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/v1/"):
            user = self._auth_gate(path, set(), set(), method="DELETE")
            if not user:
                return
            self._auth_user = user
        if path.startswith("/v1/sessions/"):
            sid = path.split("/")[-1]
            if self._session_access_check(sid, require_manage=True) is None:
                return
            info = ChatDB.get_session_info(sid)
            if sessions.delete(sid):
                self._send_json({"status": "deleted"})
                if info:
                    agent = info.get("agent_id", "main")
                    try:
                        _cleanup_chat_index(sid, agent)
                    except Exception:
                        pass
                    try:
                        engine.trigger_memory_summary_refresh(agent)
                    except Exception:
                        pass
            else:
                self._send_json({"error": "Session not found"}, 404)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "DELETE")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs/" in path:
            self._handle_project_doc_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/input-folders/" in path:
            self._handle_project_input_folders_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/image"):
            self._handle_project_image_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path:
            self._handle_project_delete(path)
        elif path.startswith("/v1/agents/") and "/ingested/" in path:
            self._handle_agent_ingested_delete(path)
        elif path.startswith("/v1/agents/") and "/workflows/" in path:
            self._handle_workflow_delete(path)
        elif path.startswith("/v1/workflows/history/"):
            self._handle_workflow_history_delete_run(path)
        elif path == "/v1/workflows/history":
            self._handle_workflow_history_delete_bulk()
        elif path == "/v1/favourites":
            self._handle_favourites_remove_bulk()
        elif path.startswith("/v1/favourites/"):
            self._handle_favourites_remove(path)
        elif path.startswith("/v1/translate/glossaries/"):
            slug = path[len("/v1/translate/glossaries/"):]
            self._handle_glossary_delete(slug)
        elif path.startswith("/v1/translate/history/"):
            entry_id = path[len("/v1/translate/history/"):]
            self._handle_translate_history_delete(entry_id)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-ID, Authorization")
        self.end_headers()

    # --- MCP endpoint ---

    def _handle_mcp_jsonrpc(self):
        """POST /mcp -- MCP Streamable HTTP endpoint (JSON-RPC)."""
        body = self._read_json()
        msg_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        if method == "initialize":
            self._send_json({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "brain_agent", "version": "1.0"},
                },
            })
        elif method == "notifications/initialized":
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            tools = []
            for td in engine.TOOL_DEFINITIONS:
                if td["name"] in self._SDK_NATIVE_TOOLS:
                    continue
                if td["name"] not in engine.TOOL_DISPATCH:
                    continue
                desc = td["description"]
                if isinstance(desc, tuple):
                    desc = " ".join(desc)
                tools.append({
                    "name": td["name"],
                    "description": desc[:1000],
                    "inputSchema": td["input_schema"],
                })
            tools.sort(key=lambda t: t.get("name", ""))
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            agent_id = self.headers.get("X-Agent-Id", "main")
            session_id = self.headers.get("X-Session-Id")
            if not tool_name or tool_name not in engine.TOOL_DISPATCH:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
                return
            try:
                agent_config = engine.AgentConfig(agent_id)
                engine._thread_local.current_agent = agent_config
                engine._thread_local.memory_store = engine.MemoryStore(
                    agent_id, base_dir=agent_config.memory_dir)
                engine._thread_local.mcp_manager = engine._mcp_manager
                if session_id:
                    engine._thread_local.session_id = session_id
                    engine._thread_local.current_session_id = session_id
                    engine._thread_local.attachment_image_model = server_config.get("attachment_image_model", "")
                runner = engine._get_hook_runner(agent_id)
                if runner:
                    blocked = runner.run_pre_hooks(tool_name, tool_args)
                    if blocked:
                        self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                          "result": {"content": [{"type": "text", "text": f"Blocked by hook: {blocked}"}],
                                                      "isError": True}})
                        return
                result = engine.TOOL_DISPATCH[tool_name](tool_args)
                if runner:
                    result = runner.run_post_hooks(tool_name, tool_args, str(result)[:51200])
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "result": {"content": [{"type": "text", "text": str(result)}]}})
            except Exception as e:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                                              "isError": True}})
        elif method == "ping":
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            if msg_id is not None:
                self._send_json({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": -32601, "message": f"Unknown method: {method}"}})
            else:
                self.send_response(204)
                self.end_headers()

    def _handle_tools_list(self):
        """GET /v1/tools/list -- return tool schemas for MCP registration."""
        tools = []
        for td in engine.TOOL_DEFINITIONS:
            if td["name"] in self._SDK_NATIVE_TOOLS:
                continue
            if td["name"] not in engine.TOOL_DISPATCH:
                continue
            desc = td["description"]
            if isinstance(desc, tuple):
                desc = " ".join(desc)
            tools.append({
                "name": td["name"],
                "description": desc[:1000],
                "input_schema": td["input_schema"],
            })
        self._send_json({"tools": tools})

    def _handle_status(self):
        self._send_json({
            "status": "running",
            "version": engine.VERSION,
            "agents": engine.list_agents(),
            "sessions": len(sessions.list_all()),
            "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
            "changelog": [{"version": v, "date": d, "changes": c} for v, d, c in engine.CHANGELOG],
            "disabled_commands": server_config.get("disabled_commands", []),
        })

    def _handle_list_agents(self):
        agents = engine.get_agent_summaries()
        team_structure = engine.get_team_structure()
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            allowed = _auth_mod.AuthDB.get_user_allowed_agents(user["id"])
            if isinstance(agents, dict):
                agents = {aid: info for aid, info in agents.items() if aid in allowed}
            elif isinstance(agents, list):
                agents = [a for a in agents if (a.get("id") or a.get("name") or "") in allowed]
            if isinstance(team_structure, dict) and "teams" in team_structure:
                filtered_teams = {}
                for tid, team in (team_structure.get("teams") or {}).items():
                    visible_members = [m for m in (team.get("members") or [])
                                       if (m.get("id") or m.get("name")) in allowed]
                    if visible_members:
                        filtered_teams[tid] = {**team, "members": visible_members}
                team_structure = {**team_structure, "teams": filtered_teams,
                                  "standalone": [a for a in (team_structure.get("standalone") or [])
                                                 if (a.get("id") or a.get("name")) in allowed]}
        self._send_json({"agents": agents, "team_structure": team_structure})

    def _handle_list_models(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        show_all = params.get("all", "").lower() in ("true", "1")
        if engine._models_config and not show_all:
            models = engine.get_enabled_models()
        else:
            models = engine.get_available_models(
                server_config["api_key"], server_config["base_url"])
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if user and user["role"] != "admin" and user["id"] != "__system__":
            allowed = _auth_mod.AuthDB.get_user_allowed_models(user["id"])
            if isinstance(models, dict):
                models = {mid: info for mid, info in models.items() if mid in allowed}
            elif isinstance(models, list):
                def _mid(m):
                    if isinstance(m, str):
                        return m
                    return m.get("id") or m.get("name") or m.get("model") or ""
                models = [m for m in models if _mid(m) in allowed]
        self._send_json({"models": models})

    def _handle_list_schedule(self):
        if engine._scheduler:
            schedules = engine._scheduler.list_all()
            running = engine._scheduler.get_running_tasks()
            running_names = {r["name"] for r in running}
            user = getattr(self, "_auth_user", None)
            if user and user.get("role") != "admin" and user.get("id") != "__system__":
                # Generic sharing model: own + team-visible + global + extra-granted.
                # Legacy owner-less schedules stay admin-only (not in this list).
                def _sched_visible(s):
                    if not (s.get("user_id") or ""):
                        return False  # legacy → admin-only
                    blk = engine._schedule_share_block(s)
                    return _auth_mod.can_access(user, blk)
                schedules = [s for s in schedules if _sched_visible(s)]
            for s in schedules:
                s["is_running"] = s.get("name", "") in running_names
            self._send_json({"schedules": schedules, "running": [
                {k: v for k, v in r.items() if k != "cancel_token"} for r in running
            ]})
        else:
            self._send_json({"schedules": [], "running": []})

    def _handle_modify_schedule(self):
        body = self._read_json()
        action = body.get("action", "list")
        if not engine._scheduler:
            self._send_json({"error": "Scheduler not running"}, 500)
            return

        if action == "add":
            atts = body.get("attachments") or []
            if not isinstance(atts, list):
                atts = []
            wd = body.get("working_dir") or None
            if isinstance(wd, str):
                wd = wd.strip() or None
            actor = getattr(self, "_auth_user", None)
            owner_id = actor.get("id", "") if actor else ""
            result = engine._scheduler.add(
                body.get("name", ""), body.get("task", ""),
                body.get("schedule", ""), body.get("agent", "main"),
                body.get("model"), timeout=int(body.get("timeout", 300)),
                attachments=atts, working_dir=wd,
                user_id=owner_id,
                thinking_level=body.get("thinking_level", "") or "",
                caveman_chat=body.get("caveman_chat", 0) or 0,
                tool_profile=body.get("tool_profile", "") or "",
            )
            self._send_json(result)
        elif action == "pause":
            name = body.get("name", "")
            if not self._schedule_owner_check(name):
                return
            self._send_json(engine._scheduler.pause(name))
        elif action == "resume":
            name = body.get("name", "")
            if not self._schedule_owner_check(name):
                return
            self._send_json(engine._scheduler.resume(name))
        elif action == "delete":
            name = body.get("name", "")
            if not self._schedule_owner_check(name):
                return
            self._send_json(engine._scheduler.remove(name))
        elif action == "run_now":
            name = body.get("name", "")
            if not self._schedule_owner_check(name):
                return
            task_row = engine._scheduler.get_task(name) if hasattr(engine._scheduler, 'get_task') else None
            if task_row:
                t = threading.Thread(target=engine._scheduler._execute_scheduled, args=(task_row,), daemon=True, name=f"sched_now_{name}")
                t.start()
                self._send_json({"status": "triggered", "name": name})
            else:
                self._send_json({"error": f"Task '{name}' not found"}, 404)
        elif action == "history":
            name = body.get("name")
            if name and not self._schedule_owner_check(name):
                return
            self._send_json({"history": engine._scheduler.get_history(
                name, body.get("limit", 20))})
        elif action == "delete_run":
            try:
                run_id = int(body.get("run_id") or 0)
            except (TypeError, ValueError):
                run_id = 0
            if not run_id:
                self._send_json({"error": "run_id is required"}, 400)
                return
            run_row = engine._scheduler.get_run(run_id)
            if run_row and run_row.get("schedule_name"):
                if not self._schedule_owner_check(run_row.get("schedule_name")):
                    return
            res = engine._scheduler.delete_run(run_id)
            if isinstance(res, dict) and res.get("error"):
                self._send_json(res, 400 if "Cannot delete" in res["error"] else 404)
            else:
                self._send_json(res)
        elif action == "clear_history":
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return
            if not self._schedule_owner_check(name):
                return
            self._send_json(engine._scheduler.delete_history(name))
        elif action == "purge_orphan_history":
            user = getattr(self, "_auth_user", None)
            if user and user.get("role") != "admin" and user.get("id") != "__system__":
                self._send_json({"error": "Forbidden: admin only"}, 403)
                return
            self._send_json(engine._scheduler.delete_orphan_history())
        elif action == "edit":
            name = body.get("name", "")
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return
            if not self._schedule_owner_check(name):
                return
            fields = {k: body.get(k) for k in
                      ("task", "schedule", "model", "timeout", "agent",
                       "new_name", "attachments", "working_dir",
                       "thinking_level", "caveman_chat", "tool_profile")
                      if k in body}
            res = engine._scheduler.update(name, fields)
            if isinstance(res, dict) and res.get("error"):
                self._send_json(res, 400)
            else:
                self._send_json(res)
        elif action == "run_detail":
            try:
                run_id = int(body.get("run_id") or 0)
            except (TypeError, ValueError):
                run_id = 0
            if not run_id:
                self._send_json({"error": "run_id is required"}, 400)
                return
            row = engine._scheduler.get_run(run_id)
            if not row:
                self._send_json({"error": f"Run {run_id} not found"}, 404)
                return
            session_id = f"sched-{run_id}"
            spans = []
            try:
                tm = engine._trace_manager
                if tm:
                    trace_id = row.get("trace_id")
                    if trace_id:
                        spans = tm.get_trace(trace_id)
                    elif hasattr(tm, "get_spans_for_session"):
                        spans = tm.get_spans_for_session(session_id)
            except Exception as e:
                spans = []
                row["_trace_error"] = str(e)
            artifacts: list = []
            try:
                art_rows = ChatDB.list_artifacts_for_session(session_id) \
                    if hasattr(ChatDB, "list_artifacts_for_session") \
                    else []
                if not art_rows:
                    all_a = ChatDB.get_all_artifacts(agent_id=row.get("agent"), limit=500)
                    art_rows = [a for a in all_a if a.get("session_id") == session_id]
                for a in art_rows:
                    try:
                        size = a.get("latest_size") or (os.path.getsize(a["path"]) if os.path.isfile(a["path"]) else 0)
                    except OSError:
                        size = 0
                    artifacts.append({
                        "id": a.get("id"),
                        "name": a.get("name"),
                        "path": a.get("path"),
                        "size": size,
                        "type": a.get("type"),
                        "role": a.get("role", "output"),
                        "latest_version": a.get("latest_version", 1),
                    })
            except Exception:
                artifacts = []
            if not artifacts:
                folder = row.get("artifact_folder")
                if folder:
                    agent_id = row.get("agent") or "main"
                    folder_path = os.path.join(
                        engine.AGENTS_DIR, agent_id, "artifacts", folder)
                    if os.path.isdir(folder_path):
                        for fname in sorted(os.listdir(folder_path)):
                            fpath = os.path.join(folder_path, fname)
                            if os.path.isfile(fpath):
                                try:
                                    size = os.path.getsize(fpath)
                                except OSError:
                                    size = 0
                                artifacts.append({
                                    "id": None,
                                    "name": fname,
                                    "size": size,
                                    "path": fpath,
                                })
            self._send_json({
                "run": row,
                "session_id": session_id,
                "spans": spans,
                "artifacts": artifacts,
            })
        else:
            self._send_json({"schedules": engine._scheduler.list_all()})

    def _schedule_owner_check(self, name: str) -> bool:
        """Enforce ownership for non-admin schedule mutations."""
        user = getattr(self, "_auth_user", None)
        if not user or user.get("role") == "admin" or user.get("id") == "__system__":
            return True
        task = engine._scheduler.get_task(name) if engine._scheduler else None
        if not task:
            self._send_json({"error": f"Schedule '{name}' not found"}, 404)
            return False
        if (task.get("user_id") or "") != user.get("id"):
            self._send_json({"error": "Forbidden: not the owner of this schedule"}, 403)
            return False
        return True


# --- Telegram service helpers ---

def _start_telegram_service() -> bool:
    """Start the in-process Telegram bot using config.json settings."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception:
        return False
    tg = config.get("telegram", {})
    token = tg.get("bot_token", "")
    if not token:
        print("Telegram: no bot_token in config.json", flush=True)
        return False
    allowed = tg.get("allowed_users")
    port = server_config.get("port", 8420)
    server_url = f"http://127.0.0.1:{port}"
    default_model = tg.get("model") or server_config.get("default_model", "")
    return _telegram_mod.telegram_service.start(
        token=token, server_url=server_url,
        allowed_users=allowed,
        default_model=default_model,
    )


def _set_telegram_enabled(enabled: bool):
    """Persist telegram.enabled to config.json."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        config.setdefault("telegram", {})["enabled"] = enabled
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        server_config["telegram_enabled"] = enabled
    except Exception as e:
        print(f"Telegram: failed to save enabled={enabled}: {e}", flush=True)


# --- Main ---

def _load_config_file() -> dict:
    """Load config.json if it exists."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _index_chat_transcript(session):
    """Store chat transcript as QMD-indexed .md chunks for semantic search.
    Only indexes non-trivial chats (>=4 messages, not note sessions)."""
    if len(session.messages) < 4:
        return
    # Skip note-editing sessions
    if getattr(session, 'note_context', None):
        return
    # Skip incognito sessions
    if getattr(session, 'status', '') == 'incognito':
        return

    agent_id = session.agent_id
    session_id = session.id

    # Build transcript text
    lines = []
    for m in session.messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str) and content:
            lines.append(f"**{role}**: {content}")
        elif isinstance(content, list):
            # Handle multi-part content (text blocks)
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            if text_parts:
                lines.append(f"**{role}**: {' '.join(text_parts)}")

    if not lines:
        return

    transcript = "\n\n".join(lines)

    # Store as chunked .md files in agents/{agent}/chats-indexed/
    chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
    os.makedirs(chats_dir, exist_ok=True)

    # Use DocumentChunker to split into manageable chunks
    chunks = engine.DocumentChunker.chunk(transcript, chunk_size=2000, chunk_overlap=300)
    if not chunks:
        return

    # Remove old chunks for this session
    prefix = f"chat-{session_id}"
    try:
        for existing in os.listdir(chats_dir):
            if existing.startswith(prefix) and existing.endswith(".md"):
                os.remove(os.path.join(chats_dir, existing))
    except OSError:
        pass

    # Write new chunks
    title = session.title or "Untitled chat"
    summary = session.summary or ""
    import datetime as _dt_idx
    now_iso = _dt_idx.datetime.now().isoformat()

    for chunk in chunks:
        idx = chunk["index"]
        fname = f"{prefix}-{idx:03d}.md"
        fpath = os.path.join(chats_dir, fname)

        fm_lines = ["---"]
        fm_lines.append(f'name: "{engine._yaml_escape(f"{title} (part {idx+1}/{chunk["total"]})")}"')
        fm_lines.append('type: chat_transcript')
        fm_lines.append(f'description: "{engine._yaml_escape(summary)}"')
        fm_lines.append(f'session_id: {session_id}')
        fm_lines.append(f'agent: {agent_id}')
        fm_lines.append(f'chunk_index: {idx}')
        fm_lines.append(f'total_chunks: {chunk["total"]}')
        fm_lines.append(f'created_at: "{now_iso}"')
        if session.project:
            fm_lines.append(f'project: "{engine._yaml_escape(session.project)}"')
        fm_lines.append("---")
        fm_lines.append("")

        try:
            with open(fpath, "w") as f:
                f.write("\n".join(fm_lines) + "\n" + chunk["text"])
        except OSError:
            continue

        # Entity extraction for knowledge graph participation
        try:
            entities = engine._extract_entities(chunk["text"])
            if entities:
                engine._update_entity_index(agent_id, fname, entities)
        except Exception:
            pass

    # Trigger QMD reindex for this agent
    engine._qmd_debounced_embed(agent_id)


def _cleanup_chat_index(session_id: str, agent_id: str):
    """Remove indexed transcript files when a session is deleted."""
    chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
    if not os.path.isdir(chats_dir):
        return
    prefix = f"chat-{session_id}"
    removed = False
    try:
        for fname in os.listdir(chats_dir):
            if fname.startswith(prefix) and fname.endswith(".md"):
                os.remove(os.path.join(chats_dir, fname))
                removed = True
    except OSError:
        pass
    if removed:
        engine._qmd_debounced_embed(agent_id)


def _generate_chat_summary(session):
    """Generate a short LLM summary of a chat session for sidebar display."""
    if not engine._delegate_api_key or len(session.messages) < 2:
        return
    # Set thread-local context for this background thread
    engine._thread_local.current_agent = session.agent
    engine._thread_local.memory_store = None  # No memory injection — summary must be based only on chat content
    # Owner pin so client-mode ambient proxy can pick a tab of the chat owner.
    engine._thread_local.current_user_id = (getattr(session, "user_id", "") or "")
    # Build a condensed view of the conversation (first + last few messages)
    msgs = session.messages
    sample = []
    for m in msgs[:3]:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Extract text from multipart content blocks
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(parts)
        if isinstance(content, str) and content.strip():
            sample.append(f"[{role}] {content[:200]}")
    if len(msgs) > 3:
        for m in msgs[-2:]:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(parts)
            if isinstance(content, str) and content.strip():
                sample.append(f"[{role}] {content[:200]}")

    if not sample:
        return  # No text content to summarize

    prompt = (
        "Summarize this conversation in ONE short sentence (max 60 chars). "
        "Focus on the topic/task, not greetings. Output ONLY the summary, nothing else. "
        "Base your summary ONLY on the conversation content below.\n\n"
        + "\n".join(sample)
    )
    try:
        # Use cheapest model
        model = None
        if engine._models_config:
            for mid, cfg in engine._models_config.items():
                if cfg.get("enabled", True) and "haiku" in mid.lower():
                    model = mid
                    break
            if not model:
                for mid, cfg in sorted(engine._models_config.items(), key=lambda x: x[1].get("cost_input", 999)):
                    if cfg.get("enabled", True):
                        model = mid
                        break
        if not model:
            return

        # GDPR auto-fallback: chat content may contain PII and the summariser
        # model is usually a cheap cloud Haiku. Reroute to the local fallback
        # when findings exist. In hard-block mode without a local route, skip
        # the summary — the session just keeps its existing/derived title.
        try:
            model = engine.gdpr_pick_model_for_background(
                model, sample, purpose="chat_summary")
        except engine.GDPRBlockedError:
            return
        except Exception:
            pass

        from handlers import sidecar_proxy as _sidecar_proxy
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt="Output only a brief summary sentence. No quotes, no prefix.",
            agent_id=session.agent,
            session_id=session.id,
            project=(session.project or ""),
            max_tokens=80,
        )
        result = _res.get("reply") or ""
        if result and not _res.get("error"):
            summary = result.strip().strip('"').strip("'")[:80]
            with session.lock:
                session.summary = summary
            # Persist to DB
            ChatDB.save_session(session.id, session.agent_id, session.model,
                                session.title, session.status, session.created_at,
                                session.last_active, session.project or "", summary)
    except Exception:
        pass
    finally:
        engine._thread_local.current_agent = None
        engine._thread_local.memory_store = None


# ── User profile storage (module level) ────────────────────────────
# The auto-maintained "Memory from chat history" feature. One Markdown file
# per user under agents/main/user_profiles/<uid>.md, mirrored as per-section
# drawers in MemPalace (wing=<uid>--main, room=user_profile) so retrieval
# works the usual way. The file is the source of truth; MemPalace is
# rewritten from the file after every successful save.
#
# Lives at module level (not inside main()) because both the HTTP handler
# methods and the in-main daemon need to call these helpers.

_USER_PROFILE_SECTIONS = (
    "Work context",
    "Personal context",
    "Top of mind",
    "Recent months",
    "Earlier context",
    "Long-term background",
)

def _user_profile_dir() -> str:
    d = os.path.join(engine.AGENTS_DIR, "main", "user_profiles")
    os.makedirs(d, exist_ok=True)
    return d

def _user_profile_path(uid: str) -> str:
    # Defensive sanitize: uid is bcrypt-hex from auth (uuid4().hex[:12]) so
    # there are no path separators in practice. Strip just in case.
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_")
    return os.path.join(_user_profile_dir(), f"{safe}.md")

def _user_profile_history_dir(uid: str) -> str:
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_")
    d = os.path.join(_user_profile_dir(), f"{safe}.history")
    os.makedirs(d, exist_ok=True)
    return d

def _read_user_profile(uid: str) -> str:
    p = _user_profile_path(uid)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except OSError as e:
        print(f"[profile] read failed uid={uid}: {e}", flush=True)
        return ""

def _split_profile_sections(content: str) -> dict[str, str]:
    """Parse a profile file into {section_title: body}. Sections are
    introduced by a level-2 heading (## Work context). Anything outside a
    recognized section goes under '_intro'."""
    out: dict[str, str] = {}
    current = "_intro"
    buf: list[str] = []
    for line in (content or "").splitlines():
        if line.startswith("## "):
            if buf:
                out[current] = "\n".join(buf).strip()
                buf = []
            current = line[3:].strip()
        else:
            buf.append(line)
    if buf:
        out[current] = "\n".join(buf).strip()
    return {k: v for k, v in out.items() if v}

def _purge_drawers_by_room_and_source(wing: str, room: str, source_prefix: str = "") -> int:
    """List drawers in (wing, room) and delete them. Returns count deleted.
    Idempotent.

    NOTE on source_prefix: tool_list_drawers' summary view does not include
    source_file, only content_preview + drawer_id, so we can't actually
    filter by prefix at list time. For the rooms we own (user_profile,
    user_daily_summary) the room itself is exclusively ours, so deleting
    everything in (wing, room) is the right behavior. The argument is kept
    for caller readability; an empty list result is still safe."""
    try:
        from mempalace.mcp_server import tool_list_drawers, tool_delete_drawer
    except ImportError:
        return 0
    deleted = 0
    while True:
        try:
            res = tool_list_drawers(wing=wing, room=room, limit=200, offset=0)
        except Exception as e:
            print(f"[profile-purge] list failed wing={wing} room={room}: {e}", flush=True)
            break
        # tool_list_drawers returns {drawers:[…], count, offset, limit}
        if isinstance(res, dict):
            rows = res.get("drawers") or []
        elif isinstance(res, list):
            rows = res
        else:
            rows = []
        if not rows:
            break
        for r in rows:
            did = (r.get("drawer_id") or r.get("id")) if isinstance(r, dict) else None
            if not did:
                continue
            try:
                tool_delete_drawer(drawer_id=did)
                deleted += 1
            except Exception as e:
                print(f"[profile-purge] delete failed id={did}: {e}", flush=True)
        # Re-list after deleting; if MemPalace pagination is offset-based
        # against a shrinking set, we'd skip rows. Fixed-offset 0 + delete-all
        # converges in O(rows/page) iterations.
        if len(rows) < 200:
            # Re-check whether we cleared everything (pagination edge case)
            try:
                check = tool_list_drawers(wing=wing, room=room, limit=1, offset=0)
                if isinstance(check, dict) and not check.get("drawers"):
                    break
                if isinstance(check, list) and not check:
                    break
            except Exception:
                break
    return deleted

def _purge_user_profile_drawers(uid: str) -> int:
    """Drop every drawer in (wing=user__<uid>, room=user_profile)."""
    return _purge_drawers_by_room_and_source(
        wing=_user_wing(uid),
        room="user_profile",
        source_prefix=f"user/{uid}#profile/",
    )

def _mirror_user_profile_to_mempalace(uid: str, content: str):
    """Rewrite the user_profile drawers from the current file content.
    Drops old drawers first so renamed/removed sections don't linger."""
    try:
        from mempalace.mcp_server import tool_add_drawer
    except ImportError:
        return
    try:
        _purge_user_profile_drawers(uid)
    except Exception:
        pass
    wing = _user_wing(uid)
    sections = _split_profile_sections(content)
    for title, body in sections.items():
        if title == "_intro" or not body:
            continue
        slug = "".join(c.lower() if c.isalnum() else "_" for c in title).strip("_")
        try:
            tool_add_drawer(
                wing=wing,
                room="user_profile",
                content=f"# {title}\n\n{body}"[:8000],
                source_file=f"user/{uid}#profile/{slug}",
                added_by="brain-user-profile",
            )
        except Exception as e:
            print(f"[profile] add_drawer {title!r} failed uid={uid}: {e}", flush=True)

def _write_user_profile_atomic(uid: str, content: str, *, source: str = "manual") -> dict:
    """Atomic write with versioned history. Returns {path, bytes, prior_kept}.
    `source` is logged ('manual', 'daemon', 'reset') for debugging only."""
    path = _user_profile_path(uid)
    prior_kept = False
    try:
        if os.path.isfile(path):
            stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
            hist = os.path.join(_user_profile_history_dir(uid), f"{stamp}.md")
            n = 0
            while os.path.exists(hist):
                n += 1
                hist = os.path.join(_user_profile_history_dir(uid), f"{stamp}-{n}.md")
            shutil.copy2(path, hist)
            prior_kept = True
            # Cap history at 30 entries — disk-bounded but keeps recent rollback.
            try:
                entries = sorted(os.listdir(_user_profile_history_dir(uid)), reverse=True)
                for old in entries[30:]:
                    try:
                        os.remove(os.path.join(_user_profile_history_dir(uid), old))
                    except OSError:
                        pass
            except OSError:
                pass
    except Exception as e:
        print(f"[profile] history snapshot failed uid={uid}: {e}", flush=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"error": f"write failed: {e}"}
    try:
        _mirror_user_profile_to_mempalace(uid, content)
    except Exception as e:
        print(f"[profile] mempalace mirror failed uid={uid}: {e}", flush=True)
    return {"path": path, "bytes": len(content.encode("utf-8")),
            "prior_kept": prior_kept, "source": source}

def _delete_user_profile(uid: str) -> dict:
    """Remove the profile file + the user_profile drawers from MemPalace.
    History dir is intentionally KEPT — that's the recovery path after a
    hasty 'Reset profile'."""
    path = _user_profile_path(uid)
    removed = False
    try:
        os.remove(path)
        removed = True
    except FileNotFoundError:
        pass
    except OSError as e:
        return {"error": f"delete failed: {e}"}
    try:
        _purge_user_profile_drawers(uid)
    except Exception as e:
        print(f"[profile] mempalace purge failed uid={uid}: {e}", flush=True)
    return {"removed": removed, "path": path}


# ── Profile generation (module level so HTTP handler + daemon share) ─

_PROFILE_SECTION_INSTRUCTIONS = (
    "Schema (use exactly these section headings, in this order; if a section "
    "has nothing real to say, write `_(none)_`):\n"
    "## Work context\n"
    "  Role, employer, professional responsibilities. Inferred only from "
    "  what the user actually said about their work.\n"
    "## Personal context\n"
    "  Location, languages, recurring personal interests, household, pets, "
    "  hobbies. No speculation.\n"
    "## Top of mind\n"
    "  What the user has been working on or thinking about in the last 1–2 "
    "  weeks. Specific projects, decisions, open questions.\n"
    "## Recent months\n"
    "  Activity from the last ~3 months that's beyond top-of-mind but still "
    "  fresh. Concrete projects and outcomes.\n"
    "## Earlier context\n"
    "  Older but still relevant. Move things here once they leave Recent months.\n"
    "## Long-term background\n"
    "  Durable identity facts, long-running interests, infrastructure, "
    "  values that surface across many chats.\n"
)

_PROFILE_SYSTEM_PROMPT = (
    "You maintain a user-context profile that an AI assistant reads at the "
    "start of every chat. Output ONLY the profile in Markdown, nothing else "
    "— no preface, no commentary, no JSON, no code fences.\n\n"
    + _PROFILE_SECTION_INSTRUCTIONS +
    "\nHARD RULES:\n"
    "- Never invent facts. If you don't have evidence, leave the section as "
    "  `_(none)_`.\n"
    "- Write in third person about the user (they / their / Alexander…).\n"
    "- Match the user's predominant language (German chats → German profile, "
    "  English chats → English).\n"
    "- Each section is 2–6 sentences max. No bullet lists unless they're a "
    "  natural list of items (places, tools, etc.).\n"
    "- Treat the existing profile (if any) as ground truth. New chat samples "
    "  ADD or DEMOTE facts; do not delete a fact unless a new chat clearly "
    "  contradicts it.\n"
    "- Demote staleness: things in 'Top of mind' that have no fresh evidence "
    "  in the new samples should move to 'Recent months'.\n"
    "- No timestamps. No 'as of <date>' markers. The profile is a snapshot.\n"
    "- Do not include personal data the user shared in passing as 'top of mind' "
    "  (e.g. one-off addresses, IDs, account numbers).\n"
)

def _profile_pick_model() -> str:
    """Prefer the configured refinement model (already proven to follow
    polish-style prompts on this install), fall back to cheapest enabled.
    GDPR auto-fallback applies on top via gdpr_pick_model_for_background."""
    try:
        tc = engine.get_tool_config()
        ref = (tc.get("refinement", {}) or {}).get("model", "")
        if ref and engine._models_config and ref in engine._models_config \
           and engine._models_config[ref].get("enabled", True):
            return ref
    except Exception:
        pass
    if engine._models_config:
        for mid, cfg in engine._models_config.items():
            if cfg.get("enabled", True) and "haiku" in mid.lower():
                return mid
        for mid, cfg in sorted(
            engine._models_config.items(),
            key=lambda x: x[1].get("cost_input", 999),
        ):
            if cfg.get("enabled", True):
                return mid
    return server_config.get("default_model", "")

def _gather_user_chat_samples(uid: str, since_ts: float, *,
                               max_chats: int = 100,
                               sample_chars: int = 250) -> list[str]:
    """Per-chat compact samples ('### title\\nuser: …\\nassistant: …'),
    most-recently-active first. Capped at max_chats."""
    out: list[str] = []
    try:
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, last_active FROM sessions "
                "WHERE user_id = ? AND last_active >= ? "
                "AND (status = '' OR status IS NULL OR status = 'active') "
                "ORDER BY last_active DESC LIMIT ?",
                (uid, since_ts, max_chats),
            ).fetchall()
            for s in rows:
                sid = s["id"]
                title = (s["title"] or "(untitled)").strip()
                first_user = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? "
                    "AND role = 'user' ORDER BY id ASC LIMIT 1",
                    (sid,),
                ).fetchone()
                last_asst = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? "
                    "AND role = 'assistant' AND (compacted = 0 OR compacted IS NULL) "
                    "ORDER BY id DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                def _extract(row, cap):
                    if not row:
                        return ""
                    c = row[0]
                    if isinstance(c, (bytes, bytearray)):
                        try:
                            c = c.decode("utf-8", errors="replace")
                        except Exception:
                            c = str(c)
                    if not isinstance(c, str):
                        try:
                            obj = json.loads(c) if isinstance(c, (bytes, str)) else c
                            if isinstance(obj, list):
                                parts = [b.get("text", "") for b in obj
                                         if isinstance(b, dict) and b.get("type") == "text"]
                                c = " ".join(p for p in parts if p)
                            else:
                                c = str(obj)
                        except Exception:
                            c = str(c)
                    return (c or "").strip().replace("\n", " ")[:cap]
                fu = _extract(first_user, sample_chars)
                la = _extract(last_asst, sample_chars)
                if not fu and not la:
                    continue
                out.append(f"### {title}\nuser: {fu}\nassistant: {la}")
    except Exception as e:
        print(f"[profile] chat-sample gather uid={uid} failed: {e}", flush=True)
    return out

def _user_profile_run_llm(uid: str, prior_profile: str, samples: list[str],
                          greeting_name: str = "") -> str | None:
    """Call the LLM to (re)build the profile. Returns the new content, or
    None on hard failure (caller falls back to prior_profile).

    The daily build is never caveman-compressed — the on-disk profile is
    always clean prose. Compression happens at read-time in send_message
    when the preamble is injected (driven by profile_preamble_caveman).
    """
    if not samples:
        return None
    model = _profile_pick_model()
    if not model:
        print(f"[profile] no model available", flush=True)
        return None
    # GDPR auto-fallback to local on PII findings; raises GDPRBlockedError
    # in hard-block mode without a usable local route — fail closed.
    try:
        model = engine.gdpr_pick_model_for_background(
            model, samples + [prior_profile], purpose="user_profile",
        )
    except Exception as e:
        print(f"[profile] GDPR gate refused uid={uid}: {e}", flush=True)
        return None
    if not model:
        return None
    joined_samples = "\n\n".join(samples)
    if len(joined_samples) > 12000:
        joined_samples = joined_samples[:12000] + "\n\n[…older chats truncated]"
    if prior_profile.strip():
        user_msg = (
            "EXISTING PROFILE (treat as ground truth, edit in place):\n"
            f"```\n{prior_profile.strip()}\n```\n\n"
            "NEW CHAT SAMPLES SINCE LAST UPDATE:\n"
            f"{joined_samples}\n\n"
            "Update the profile. Move stale 'Top of mind' items to "
            "'Recent months' if no fresh evidence appears. Add new facts "
            "from the new samples. Output the COMPLETE new profile."
        )
    else:
        user_msg = (
            "Build the profile from scratch. The user's preferred name "
            f"is {greeting_name or 'unknown'}.\n\n"
            "CHAT SAMPLES (most recent first):\n"
            f"{joined_samples}\n\n"
            "Output the COMPLETE profile using the schema above."
        )
    try:
        # Use the same delegate path as _generate_chat_summary (the existing
        # in-tree pattern for background LLM calls). Returns the assistant's
        # text or a "Delegation error: …" string we filter out.
        # current_agent must be an AgentConfig object, not just the agent id.
        engine._thread_local.current_agent = engine.AgentConfig("main")
        engine._thread_local.current_user_id = ""
        engine._thread_local.memory_store = None
        result = engine._run_delegate(
            messages=[{"role": "user", "content": user_msg}],
            model=model,
            system_prompt=_PROFILE_SYSTEM_PROMPT,
            memory_store=None,
            inference_params={"max_tokens": 2000, "temperature": 0.2},
            tools=False,
        )
        if not result:
            return None
        if isinstance(result, str) and (
            result.startswith("Delegation error") or
            "There's an issue with the selected model" in result
        ):
            print(f"[profile] delegate returned error: {result[:200]}", flush=True)
            return None
        return result.strip()
    except Exception as e:
        print(f"[profile] LLM call uid={uid} failed: {type(e).__name__}: {e}", flush=True)
        return None
    finally:
        engine._thread_local.current_agent = None
        engine._thread_local.memory_store = None

def _profile_run_synchronous(user: dict, since_ts: float, now: float):
    """Run a profile update for one user. Used by the daemon and by the
    on-demand HTTP endpoint. Returns a status dict."""
    uid = user["id"]
    if not since_ts:
        since_ts = now - 90 * 24 * 3600
    # Always pull last 90 days of samples — even an incremental update needs
    # enough context to demote stale items.
    samples = _gather_user_chat_samples(uid, since_ts=now - 90 * 24 * 3600)
    if not samples:
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "no_activity", "")
        return {"status": "no_activity"}
    prior = _read_user_profile(uid)
    prefs = user.get("preferences") or {}
    greeting = (prefs.get("greeting_name") or "").strip() \
               or (user.get("display_name") or "").strip() \
               or (user.get("username") or "")
    new_profile = _user_profile_run_llm(uid, prior, samples,
                                         greeting_name=greeting)
    if not new_profile:
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "error:llm_no_output", "")
        return {"status": "error", "error": "LLM produced no output"}
    if new_profile.startswith("```"):
        new_profile = new_profile.lstrip("`").lstrip("markdown").lstrip("md").lstrip()
        if new_profile.endswith("```"):
            new_profile = new_profile[: -3].rstrip()
    write_res = _write_user_profile_atomic(uid, new_profile, source="daemon")
    if write_res.get("error"):
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, f"error:{write_res['error']}"[:80], "")
        return {"status": "error", "error": write_res["error"]}
    _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "filed", write_res.get("path", ""))
    print(f"[profile] uid={uid} updated ({write_res.get('bytes')} bytes, "
          f"{len(samples)} samples)", flush=True)
    return {"status": "filed", "path": write_res.get("path"),
            "bytes": write_res.get("bytes"), "samples": len(samples)}


_AUDIO_PROVIDER_MIGRATION_FLAG = "_local_mlx_whisper_seeded"


def _migrate_audio_provider_once(file_config: dict) -> dict:
    """One-shot: ensure providers config carries the 'local-mlx-whisper'
    pseudo-provider used by the in-process mlx-whisper transcribe wire.
    Stamps a top-level marker key so subsequent startups never re-add it
    even if the user deletes the entry deliberately.
    """
    if file_config.get(_AUDIO_PROVIDER_MIGRATION_FLAG):
        return file_config
    providers = file_config.setdefault("providers", {})
    if "local-mlx-whisper" not in providers:
        providers["local-mlx-whisper"] = {
            "base_url": "",
            "api_key": "",
            "default_model": "",
            "type": "in_process",
        }
    file_config[_AUDIO_PROVIDER_MIGRATION_FLAG] = True
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        with open(config_path, "w") as f:
            json.dump(file_config, f, indent=2)
    except Exception as e:
        print(f"[migrate] failed to persist local-mlx-whisper provider: {e}", flush=True)
    return file_config


def main():
    # Load config.json for defaults
    file_config = _load_config_file()
    file_config = _migrate_audio_provider_once(file_config)
    providers = file_config.get("providers", {})
    default_provider = file_config.get("default_provider", "")
    provider = providers.get(default_provider, {}) if default_provider else {}
    srv_cfg = file_config.get("server", {})

    parser = argparse.ArgumentParser(description=f"Brain Agent Server v{engine.VERSION}")
    parser.add_argument("--host", default=srv_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=srv_cfg.get("port", 8420))
    parser.add_argument("--api-key", default=provider.get("api_key", ""))
    parser.add_argument("--base-url", default=provider.get("base_url", "http://localhost:8317/v1"))
    parser.add_argument("-m", "--model", default=provider.get("default_model", ""))
    parser.add_argument("--max-context", type=int, default=file_config.get("max_context", 131072))
    args = parser.parse_args()

    # Store all providers and settings
    server_config["disabled_commands"] = file_config.get("disabled_commands", [])
    server_config["providers"] = providers
    server_config["api_key"] = args.api_key
    server_config["base_url"] = args.base_url
    server_config["default_model"] = args.model
    server_config["max_context"] = args.max_context
    server_config["port"] = args.port
    server_config["telegram_enabled"] = file_config.get("telegram", {}).get("enabled", True)
    attachments_cfg = file_config.get("attachments", {})
    server_config["attachment_image_model"] = attachments_cfg.get("image_model", "")
    server_config["gdpr_scanner"] = file_config.get("gdpr_scanner", {}) or {}
    server_config["sidecar"] = file_config.get("sidecar", {}) or {}

    # Initialize models config
    existing_models = file_config.get("models")
    deleted_models = file_config.get("deleted_models", [])
    if providers:
        synced = engine.init_models_config(providers, existing_models,
                                           deleted_models=deleted_models)
        # Persist when (a) first run with no stored models, or (b) the in-memory
        # init upgraded fields on existing rows (e.g. provider-aware
        # thinking_format re-detection upgrading 'none' → 'reasoning_field').
        # Without this branch, the upgrade only lives in RAM until the next
        # explicit Save in the Models tab.
        def _models_differ(a: dict, b: dict) -> bool:
            if set(a.keys()) != set(b.keys()):
                return True
            for k, av in a.items():
                bv = b.get(k, {})
                # Compare just the fields init_models_config actually touches.
                for fld in ("thinking_format", "provider", "max_context",
                            "raw_formats", "profile", "capabilities",
                            "_caps_canonical"):
                    if av.get(fld) != bv.get(fld):
                        return True
            return False
        should_save = bool(synced) and (
            not existing_models or _models_differ(synced, existing_models))
        if should_save:
            try:
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["models"] = synced
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception:
                pass
    elif existing_models:
        engine._models_config = dict(existing_models)

    # Initialize auth system
    _auth_mod.init_auth(file_config)
    if _auth_mod.auth_enabled():
        print(f"Auth: enabled (registration: {'open' if _auth_mod.registration_enabled() else 'closed'})")
    else:
        print("Auth: disabled (single-user mode)")

    # Initialize engine globals
    engine._delegate_api_key = args.api_key
    engine._delegate_base_url = args.base_url
    engine._delegate_fallback_model = args.model

    # Start scheduler
    engine._scheduler = engine.Scheduler()
    engine._scheduler.start()

    # Initialize lossless context manager
    engine._context_manager = engine.ContextManager()
    print(f"Context manager: {engine.CONTEXT_DB} (lossless, {'enabled' if engine._context_manager.get_config().get('enabled') else 'disabled'})")

    # Initialize cost tracking and rate limiting
    engine._cost_tracker = engine.CostTracker()
    engine._rate_limiter = engine.RateLimiter()
    engine._quota_manager = engine.QuotaManager()
    print(f"Cost tracking: {engine.COST_DB}")
    _q_cfg = engine._quota_manager.get_config()
    print(f"Quotas: {'enabled' if _q_cfg.get('enabled') else 'disabled'} "
          f"({_q_cfg.get('billing_cycle')}, enforce_red={_q_cfg.get('enforce_red')})")

    # Initialize tracing and audit trail
    engine._trace_manager = engine.TraceManager()
    engine._audit_log = engine.AuditLog()
    print(f"Tracing: {engine.TRACES_DB}")
    print(f"Audit log: {engine.AUDIT_DB}")

    # Initialize notification manager
    global _notification_manager
    notif_config = file_config.get("notifications", {"enabled": True, "channels": {"in_app": {"enabled": True, "min_severity": "info"}}})
    _notification_manager = _notif_mod.NotificationManager(notif_config)
    # Wire notification hook into engine (for scheduler events etc.)
    def _notif_hook(event_type, title, message, severity="info", agent=None, metadata=None):
        if _notification_manager:
            _notification_manager.notify(event_type, title, message, severity=severity,
                                          agent=agent, metadata=metadata)
    engine._notification_hook = _notif_hook
    print(f"Notifications: {'enabled' if notif_config.get('enabled', False) else 'disabled'}")

    # Ensure memory summary schedules for all agents
    try:
        engine.ensure_memory_summary_schedules()
    except Exception as e:
        print(f"[WARN] Memory summary schedule init: {e}")

    # Ensure relationship discovery schedules for all agents
    try:
        engine.ensure_relationship_discovery_schedules()
    except Exception as e:
        print(f"[WARN] Relationship discovery schedule init: {e}")

    # Start task runner
    engine._task_runner = engine.TaskRunner()

    # Start ingest watcher (watched folders auto-ingestion)
    engine._ingest_watcher = engine.IngestWatcher()
    engine._ingest_watcher.start()
    print("Ingest watcher: started (30s poll)")

    # Initialize main agent
    engine._current_agent = engine.AgentConfig("main")
    engine._memory_store = engine.MemoryStore("main", base_dir=engine._current_agent.memory_dir)

    # Initialize shared MCP manager (singleton used by all request handlers + Web UI)
    engine._mcp_manager = engine.MCPManager()
    main_mcp = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
    mcp_count = engine._mcp_manager.load_config(main_mcp)
    print(f"MCP: loaded {mcp_count} server(s) from mcp.json")

    # One-shot startup: drop empty (0-message) sessions older than 5 minutes.
    # ensureSession() previously pre-created a row on every model switch and
    # every newChat() to trigger warmup; those that never got a message stayed
    # in the DB forever and showed up in the project chat list as
    # "Untitled". Pre-creation was removed in 8.18.2; this cleans up the
    # historical orphans. Idempotent — safe to keep running on every start.
    try:
        with _db_conn() as conn:
            cutoff = time.time() - 300  # 5 minutes
            cur = conn.execute(
                "DELETE FROM sessions WHERE last_active < ? AND id IN ("
                "  SELECT s.id FROM sessions s WHERE NOT EXISTS ("
                "    SELECT 1 FROM messages m WHERE m.session_id = s.id"
                "  )"
                ")",
                (cutoff,),
            )
            n = cur.rowcount or 0
            if n:
                print(f"[startup-purge] dropped {n} orphan empty session(s)", flush=True)
    except Exception as e:
        print(f"[startup-purge] empty-session purge failed: {type(e).__name__}: {e}", flush=True)

    # One-shot startup: backfill `sessions.project_id` from the legacy
    # `sessions.project` (directory name) column. Resolves each unique
    # (agent_id, project_name) pair to the project.json `id` field via
    # ProjectManager.get_project (which mints an id if the file is
    # missing one). Idempotent — only updates rows with empty project_id.
    try:
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT agent_id, project FROM sessions "
                "WHERE project IS NOT NULL AND project != '' "
                "AND (project_id IS NULL OR project_id = '')"
            ).fetchall()
            updated = 0
            for agent_id, proj_name in rows:
                pid = _project_id_for_name(agent_id, proj_name)
                if not pid:
                    continue
                cur = conn.execute(
                    "UPDATE sessions SET project_id = ? "
                    "WHERE agent_id = ? AND project = ? "
                    "AND (project_id IS NULL OR project_id = '')",
                    (pid, agent_id, proj_name),
                )
                updated += cur.rowcount or 0
            if updated:
                conn.commit()
                print(f"[startup-backfill] sessions.project_id: filled {updated} row(s)", flush=True)
    except Exception as e:
        print(f"[startup-backfill] project_id backfill failed: {type(e).__name__}: {e}", flush=True)

    # Backfill: index any unindexed chat transcripts (runs once at startup)
    def _cleanup_orphaned_chat_indexes():
        """Remove chats-indexed files for sessions that no longer exist in the DB."""
        try:
            with _db_conn() as conn:
                existing_sids = {row[0] for row in conn.execute("SELECT id FROM sessions")}
            for agent_dir in os.listdir(engine.AGENTS_DIR):
                chats_dir = os.path.join(engine.AGENTS_DIR, agent_dir, "chats-indexed")
                if not os.path.isdir(chats_dir):
                    continue
                removed = 0
                for fname in os.listdir(chats_dir):
                    if not fname.startswith("chat-") or not fname.endswith(".md"):
                        continue
                    sid = fname.split("-", 1)[1].rsplit("-", 1)[0]
                    if sid not in existing_sids:
                        try:
                            os.remove(os.path.join(chats_dir, fname))
                            removed += 1
                        except OSError:
                            pass
                if removed:
                    print(f"Chat index cleanup: removed {removed} orphaned files for {agent_dir}", flush=True)
                    engine._qmd_debounced_embed(agent_dir)
        except Exception as e:
            print(f"[WARN] Chat index orphan cleanup: {e}", flush=True)

    def _backfill_chat_index():
        """Find sessions with 4+ messages that have no indexed transcript files and index them."""
        time.sleep(10)  # let server fully start
        _cleanup_orphaned_chat_indexes()
        try:
            with _db_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT s.id, s.agent_id, "
                    "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as msg_count "
                    "FROM sessions s WHERE s.status != 'incognito'"
                ).fetchall()
            indexed = 0
            for r in rows:
                if r["msg_count"] < 4:
                    continue
                sid = r["id"]
                agent_id = r["agent_id"]
                chats_dir = os.path.join(engine.AGENTS_DIR, agent_id, "chats-indexed")
                prefix = f"chat-{sid}"
                # Skip if already indexed
                if os.path.isdir(chats_dir):
                    if any(f.startswith(prefix) for f in os.listdir(chats_dir)):
                        continue
                # Load session into memory to index
                session = sessions.get(sid)
                if not session:
                    # Load from DB
                    info = ChatDB.get_session_info(sid)
                    if not info:
                        continue
                    session = ChatSession(info["agent_id"], info.get("model", ""), info.get("project", ""))
                    session.id = sid
                    session.title = info.get("title", "")
                    session.summary = info.get("summary", "")
                    session.status = info.get("status", "active")
                    # Load messages
                    with _db_conn() as conn2:
                        conn2.row_factory = sqlite3.Row
                        msgs = conn2.execute(
                            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                            (sid,)
                        ).fetchall()
                    with session.lock:
                        session.messages = [{"role": m["role"], "content": m["content"]} for m in msgs]
                try:
                    _index_chat_transcript(session)
                    indexed += 1
                except Exception:
                    pass
            if indexed:
                print(f"Chat index backfill: indexed {indexed} sessions", flush=True)
        except Exception as e:
            print(f"[WARN] Chat index backfill: {e}", flush=True)

    threading.Thread(target=_backfill_chat_index, daemon=True, name="chat_index_backfill").start()

    # Unified QMD index keeper: collection registration, file watching, and embedding health
    def _qmd_index_keeper():
        """Single background loop that keeps QMD fully in sync automatically.
        - Waits for QMD to become available (retries on startup)
        - Registers missing collections
        - Detects file changes via mtime polling (fast, every 5s)
        - Every 30s deep-checks index integrity: stale hashes, missing embeddings
        - Runs update+embed as needed — no manual intervention required
        """
        import sqlite3 as _sqlite3
        import hashlib as _hashlib

        agents_dir = engine.AGENTS_DIR
        idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
        last_mtime_snap: dict[str, float] = {}
        FAST_INTERVAL = 5       # seconds between mtime polls
        DEEP_INTERVAL = 30      # seconds between full integrity checks
        last_deep_check = 0.0
        qmd_was_running = False

        def _mtime_snapshot() -> dict[str, float]:
            snap = {}
            try:
                for root, dirs, files in os.walk(agents_dir):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for fname in files:
                        if fname.endswith(".md"):
                            fpath = os.path.join(root, fname)
                            try:
                                snap[fpath] = os.path.getmtime(fpath)
                            except OSError:
                                pass
            except Exception:
                pass
            return snap

        def _backfill_collections():
            """Register any agent dirs and project dirs missing from QMD."""
            existing = {(c["name"] if isinstance(c, dict) else c)
                        for c in BrainAgentHandler._qmd_collections()}
            added = False
            for agent_id in engine.list_agents():
                if agent_id not in existing:
                    agent_dir = os.path.join(agents_dir, agent_id)
                    if os.path.isdir(agent_dir):
                        BrainAgentHandler._qmd_run(["collection", "add", agent_dir, "--name", agent_id])
                        print(f"QMD: registered collection '{agent_id}'")
                        added = True
                # Also register project collections
                projects_dir = os.path.join(agents_dir, agent_id, "projects")
                if os.path.isdir(projects_dir):
                    for proj_name in os.listdir(projects_dir):
                        proj_dir = os.path.join(projects_dir, proj_name)
                        if not os.path.isdir(proj_dir) or proj_name.startswith("."):
                            continue
                        col_name = f"{agent_id}/{proj_name}"
                        if col_name not in existing:
                            BrainAgentHandler._qmd_run(["collection", "add", proj_dir, "--name", col_name])
                            print(f"QMD: registered project collection '{col_name}'")
                            added = True
            return added

        def _deep_check() -> tuple[bool, bool]:
            """Check index for stale content and missing embeddings.
            Returns (needs_update, needs_embed)."""
            needs_update = False
            needs_embed = False
            if not os.path.isfile(idx_path):
                return False, False
            try:
                conn = _sqlite3.connect(idx_path, timeout=2)
                # Pending embeddings
                pending = conn.execute(
                    "SELECT COUNT(*) FROM documents d "
                    "WHERE d.active = 1 AND NOT EXISTS "
                    "(SELECT 1 FROM content_vectors cv WHERE cv.hash = d.hash)"
                ).fetchone()[0]
                if pending > 0:
                    needs_embed = True
                # Build indexed lookup
                indexed = {}
                for row in conn.execute(
                    "SELECT collection, path, hash FROM documents WHERE active = 1"
                ).fetchall():
                    indexed[(row[0].lower(), row[1].lower())] = row[2]
                conn.close()
            except Exception:
                return False, False

            # Compare every .md on disk against the index
            try:
                for agent_id in engine.list_agents():
                    agent_dir = os.path.join(agents_dir, agent_id)
                    if not os.path.isdir(agent_dir):
                        continue
                    for root, dirs, files in os.walk(agent_dir):
                        dirs[:] = [d for d in dirs if not d.startswith('.')]
                        for fname in files:
                            if not fname.endswith(".md"):
                                continue
                            fpath = os.path.join(root, fname)
                            rel = os.path.relpath(fpath, agent_dir)
                            key = (agent_id.lower(), rel.lower())
                            if key not in indexed:
                                needs_update = True
                            else:
                                try:
                                    with open(fpath, "rb") as fh:
                                        if _hashlib.sha256(fh.read()).hexdigest() != indexed[key]:
                                            needs_update = True
                                except OSError:
                                    pass
                            if needs_update:
                                break
                        if needs_update:
                            break
                    if needs_update:
                        break
            except Exception:
                pass
            return needs_update, needs_embed

        # --- Main loop ---
        last_mtime_snap = _mtime_snapshot()
        while True:
            time.sleep(FAST_INTERVAL)
            try:
                running = BrainAgentHandler._is_qmd_running()

                # QMD just came up (or first time): backfill + full sync
                if running and not qmd_was_running:
                    print("QMD: index keeper — QMD detected, syncing...")
                    if _backfill_collections():
                        BrainAgentHandler._qmd_run(["update"], timeout=120)
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    else:
                        # Still do a deep check on first connect
                        nu, ne = _deep_check()
                        if nu:
                            BrainAgentHandler._qmd_run(["update"], timeout=120)
                            BrainAgentHandler._qmd_run(["embed"], timeout=300)
                        elif ne:
                            BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    last_mtime_snap = _mtime_snapshot()
                    last_deep_check = time.time()
                    qmd_was_running = True
                    continue

                qmd_was_running = running
                if not running:
                    continue

                # Fast path: mtime change detection
                current_snap = _mtime_snapshot()
                if current_snap != last_mtime_snap:
                    last_mtime_snap = current_snap
                    BrainAgentHandler._qmd_trigger_update(delay=1.0)
                    last_deep_check = time.time()  # skip deep check right after trigger
                    continue

                # Periodic deep integrity check
                now = time.time()
                if now - last_deep_check >= DEEP_INTERVAL:
                    last_deep_check = now
                    # Also check for new agent collections
                    _backfill_collections()
                    nu, ne = _deep_check()
                    if nu:
                        BrainAgentHandler._qmd_run(["update"], timeout=120)
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
                    elif ne:
                        BrainAgentHandler._qmd_run(["embed"], timeout=300)
            except Exception:
                pass

    # MemPalace migration: QMD index keeper thread disabled; memory layer is now
    # mempalace MCP. The _qmd_index_keeper() function above is dead code and will
    # be removed in C8 cleanup.

    # Start server
    server = ThreadingHTTPServer((args.host, args.port), BrainAgentHandler)
    print(f"Brain Agent Server v{engine.VERSION}")
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"API: {args.base_url}")
    print(f"Model: {args.model}")
    print(f"Agents: {', '.join(engine.list_agents())}")
    if engine._scheduler:
        n = len(engine._scheduler.list_all())
        if n:
            print(f"Scheduled tasks: {n}")
    print()
    print("Endpoints:")
    print("  GET  /v1/status         — server health")
    print("  POST /v1/sessions       — create session")
    print("  POST /v1/chat           — send message (SSE stream)")
    print("  POST /v1/chat/cancel    — cancel request")
    print("  GET  /v1/agents         — list agents (with team metadata)")
    print("  POST /v1/agents/switch  — switch agent")
    print("  GET  /v1/teams          — team structure")
    print("  POST /v1/teams          — manage teams")
    print("  GET  /v1/models         — list models")
    print("  GET  /v1/schedule       — scheduled tasks")
    print("  POST /v1/schedule       — manage schedules")
    print("  GET  /v1/costs          — cost stats")
    print("  GET  /v1/costs/daily    — daily cost breakdown")
    print("  GET  /v1/tasks          — background tasks")
    print("  GET  /v1/agents/{id}/workflows — list workflows")
    print("  POST /v1/agents/{id}/workflows — save workflow")
    print("  POST /v1/agents/{id}/workflows/{name}/run — run workflow")
    print("  GET  /v1/workflows/executions  — list executions")
    # Initialize remote nodes registry
    _init_node_registry()
    nodes_cfg = _load_node_config()
    if nodes_cfg:
        print(f"Remote nodes: {', '.join(nodes_cfg.keys())}")

    # Initialize channel manager (multi-messaging frontends)
    port = server_config.get("port", 8420)
    _adapters_mod.channel_manager = _adapters_mod.ChannelManager(f"http://127.0.0.1:{port}")
    _adapters_mod.channel_manager.load_from_config(file_config)

    # Auto-start Telegram bot if enabled (legacy support)
    if server_config.get("telegram_enabled", True):
        def _start_tg():
            time.sleep(1)
            _start_telegram_service()
        threading.Thread(target=_start_tg, daemon=True, name="telegram-start").start()
    else:
        print("Telegram: disabled in config")


    # File change watcher for SDK — detect file writes that bypass _after_file_write
    _file_mtimes: dict[str, float] = {}

    def _file_change_watcher():
        """Poll agent dirs for .md file changes and trigger post-write pipeline.
        Catches files created/modified by the SDK subprocess which bypasses _after_file_write."""
        import glob as _glob
        # Initial scan
        for agent_id in engine.list_agents():
            agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
            for path in _glob.glob(os.path.join(agent_dir, "*.md")):
                try:
                    _file_mtimes[path] = os.path.getmtime(path)
                except OSError:
                    pass
        while True:
            time.sleep(10)  # Check every 10 seconds
            try:
                for agent_id in engine.list_agents():
                    agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
                    for path in _glob.glob(os.path.join(agent_dir, "*.md")):
                        try:
                            mtime = os.path.getmtime(path)
                        except OSError:
                            continue
                        prev = _file_mtimes.get(path)
                        if prev is None or mtime > prev:
                            _file_mtimes[path] = mtime
                            if prev is not None:
                                # File was modified — trigger post-write pipeline
                                try:
                                    engine._after_file_write(path, action="modified", agent_id=agent_id)
                                except Exception:
                                    pass
            except Exception:
                pass

    threading.Thread(target=_file_change_watcher, daemon=True, name="file-change-watcher").start()

    # MemPalace background miner — fully autonomous artifact ingestion.
    # Three rules (per user 2026-04-25):
    #   1. Scheduled-task artifacts: only output-role files (skip intermediate
    #      scripts/json/etc), regardless of any toggle. Never mines sched chat
    #      content (no session/messages rows exist for sched-* runs).
    #   2. Chat-originated artifacts: only when parent session has
    #      save_to_memory > 0. When ON, mine all files in that folder.
    #   3. mempalace.yaml is managed automatically per agent — server creates
    #      and refreshes it; user never touches it.
    _MEMPALACE_YAML_MARKER = "# managed by brain-agent server.py — do not edit\n"

    def _mempalace_yaml_for_artifacts(wing: str) -> str:
        # Default-room "general" satisfies miner.detect_room fallback. Rooms
        # field must be a list per miner spec, even if minimal.
        return (
            _MEMPALACE_YAML_MARKER
            + "wing: " + wing + "\n"
            + "rooms:\n"
            + "  - name: artifacts\n"
            + "    description: Files produced during chats and tasks\n"
            + "    keywords: [report, output, document]\n"
            + "  - name: general\n"
            + "    description: Fallback room\n"
            + "    keywords: [general]\n"
        )

    def _ensure_mempalace_yaml(project_dir: str, wing: str) -> bool:
        """Write a mempalace.yaml if missing, if the brain-managed marker is
        gone, or if the wing line in the file disagrees with `wing` (the
        expected wing for the caller). Returns True if the file is present
        and matches `wing` (existing or freshly written)."""
        try:
            yaml_path = os.path.join(project_dir, "mempalace.yaml")
            existing_ok = False
            if os.path.isfile(yaml_path):
                try:
                    with open(yaml_path, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(400)
                    has_marker = (head.startswith(_MEMPALACE_YAML_MARKER)
                                  or "wing:" in head)
                    wing_matches = f"wing: {wing}" in head
                    if has_marker and wing_matches:
                        existing_ok = True
                except Exception:
                    existing_ok = False
            if existing_ok:
                return True
            os.makedirs(project_dir, exist_ok=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(_mempalace_yaml_for_artifacts(wing))
            return True
        except Exception as e:
            print(f"[mempalace-miner] failed to write yaml in {project_dir}: {e}", flush=True)
            return False

    def _purge_orphan_chroma_queue(palace_path: str):
        """One-shot cleanup: remove embeddings_queue rows whose target segment
        has no max_seq_id bootstrap (= compactor never saw them, never will).
        Safe — these have been dead since 2026-04-19 and don't affect new writes."""
        try:
            db_path = os.path.join(palace_path, "chroma.sqlite3")
            if not os.path.isfile(db_path):
                return
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            # Find segments without a max_seq_id row — those are the orphans
            orphan_segments = [r[0] for r in cur.execute(
                "SELECT s.id FROM segments s "
                "LEFT JOIN max_seq_id m ON m.segment_id = s.id "
                "WHERE m.seq_id IS NULL"
            ).fetchall()]
            if not orphan_segments:
                con.close()
                return
            # Each segment belongs to a collection; queue rows reference the
            # collection via topic suffix (last 36 chars = collection UUID).
            collection_uuids = [r[0] for r in cur.execute(
                "SELECT DISTINCT collection FROM segments WHERE id IN ({})".format(
                    ",".join("?" * len(orphan_segments))
                ),
                orphan_segments,
            ).fetchall()]
            total = 0
            for cuid in collection_uuids:
                # Only purge rows older than 24h — leave today's writes alone
                cutoff = time.time() - 86400
                n = cur.execute(
                    "DELETE FROM embeddings_queue "
                    "WHERE topic LIKE ? AND strftime('%s', created_at) < ?",
                    (f"%{cuid}", str(int(cutoff))),
                ).rowcount
                total += n
            con.commit()
            con.close()
            if total:
                print(f"[mempalace-miner] purged {total} stale queue row(s) "
                      f"(orphan segments: {len(orphan_segments)})", flush=True)
        except Exception as e:
            print(f"[mempalace-miner] queue cleanup failed: {type(e).__name__}: {e}", flush=True)

    def _list_chat_artifact_folders():
        """Iterate per-agent artifact folders. Yields tuples
        (agent_id, folder_path, folder_name, kind) where kind is 'sched' or 'chat'."""
        try:
            for agent_id in os.listdir(engine.AGENTS_DIR):
                agent_root = os.path.join(engine.AGENTS_DIR, agent_id)
                if not os.path.isdir(agent_root):
                    continue
                if agent_id.startswith(".") or agent_id == "main" and False:
                    pass
                artifacts_root = os.path.join(agent_root, "artifacts")
                if not os.path.isdir(artifacts_root):
                    continue
                for folder_name in os.listdir(artifacts_root):
                    folder_path = os.path.join(artifacts_root, folder_name)
                    if not os.path.isdir(folder_path):
                        continue
                    # Folder names are <date>_<sid_prefix>. sched-task folders
                    # use the sched-* prefix as the sid (brain.py L11319).
                    parts = folder_name.split("_", 1)
                    sid_part = parts[1] if len(parts) > 1 else parts[0]
                    kind = "sched" if sid_part.startswith("sched-") else "chat"
                    yield (agent_id, folder_path, folder_name, kind)
        except Exception as e:
            print(f"[mempalace-miner] discovery error: {type(e).__name__}: {e}", flush=True)

    def _file_text_or_none(path: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
        """Read a file as text (utf-8, errors=replace). Returns None for
        binary blobs we can't reasonably treat as text."""
        try:
            size = os.path.getsize(path)
            if size <= 0 or size > max_bytes:
                return None
            with open(path, "rb") as f:
                blob = f.read()
            # Heuristic: skip if >5% null bytes
            if blob.count(b"\x00") > max(1, len(blob) // 20):
                return None
            return blob.decode("utf-8", errors="replace")
        except Exception:
            return None

    def _sched_run_skipped_by_owner_pref(sid_prefix: str) -> bool:
        """For a sched folder named <date>_sched-<run_id>, resolve run → schedule
        → owner → preferences.memory_sched_default. Returns True iff the owner
        explicitly opted out (pref == 0). Anything else (no owner, pref unset,
        pref ≥ 1) keeps the default 'file artifacts' behavior."""
        if not sid_prefix.startswith("sched-"):
            return False
        try:
            run_id_part = sid_prefix[len("sched-"):]
            if "-" in run_id_part:  # sched-adhoc-<ts>
                return False
            run_id = int(run_id_part)
        except (ValueError, TypeError):
            return False
        try:
            sched_db = os.path.join(engine.AGENTS_DIR, "main", "scheduler.db")
            with sqlite3.connect(sched_db) as conn:
                row = conn.execute(
                    "SELECT s.user_id FROM schedule_history h "
                    "JOIN schedules s ON h.schedule_id = s.id "
                    "WHERE h.id = ?",
                    (run_id,),
                ).fetchone()
            if not row or not row[0]:
                return False
            uid = row[0]
        except Exception:
            return False
        try:
            user = _auth_mod.AuthDB.get_user(uid)
        except Exception:
            return False
        if not user:
            return False
        prefs = user.get("preferences") or {}
        v = prefs.get("memory_sched_default")
        return v == 0

    def _mempalace_miner_loop():
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            print("[mempalace-miner] disabled (mempalace.enabled = false)", flush=True)
            return
        mine_cfg = mcfg.get("mine", {}) or {}
        if not mine_cfg.get("enabled", True):
            print("[mempalace-miner] disabled (mempalace.mine.enabled = false)", flush=True)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            print(f"[mempalace-miner] {err}", flush=True)
            return
        try:
            from mempalace import miner as mp_miner
            from mempalace.mcp_server import tool_add_drawer
        except Exception as e:
            print(f"[mempalace-miner] import failed: {e}", flush=True)
            return

        palace_path = mcfg.get("palace_path", "")
        interval = int(mine_cfg.get("interval_seconds", 1800))
        respect_git = bool(mine_cfg.get("respect_gitignore", True))

        # One-shot: purge orphaned queue rows from earlier runs
        _purge_orphan_chroma_queue(palace_path)
        # One-shot: drop drawers from the deprecated user_daily_summary room.
        # Replaced in v8.17.0 by the per-user profile file under
        # agents/main/user_profiles/. Idempotent — second call is a no-op once
        # the room is empty across all wings.
        try:
            from mempalace.mcp_server import tool_list_drawers as _tld
            # Walk every user wing (cheap — list_drawers paginates by room
            # within wing, so one query per user is bounded).
            try:
                _users = _auth_mod.AuthDB.list_users() if _auth_mod else []
            except Exception:
                _users = []
            _purged_total = 0
            for _u in _users:
                _uid = _u.get("id") or ""
                if not _uid:
                    continue
                try:
                    _purged_total += _purge_drawers_by_room_and_source(
                        wing=f"{_uid}--main",
                        room="user_daily_summary",
                    )
                except Exception:
                    pass
            if _purged_total:
                print(f"[startup-purge] dropped {_purged_total} legacy "
                      f"user_daily_summary drawer(s)", flush=True)
        except ImportError:
            pass
        except Exception as e:
            print(f"[startup-purge] failed: {type(e).__name__}: {e}", flush=True)

        # Small startup delay so we don't compete with initial provider probes.
        time.sleep(15)

        intermediate_exts = engine._ARTIFACT_INTERMEDIATE_EXTS

        while True:
            try:
                mcfg2 = engine._load_mempalace_config()
                if not mcfg2.get("enabled", True):
                    return
                mine2 = mcfg2.get("mine") or {}
                # Build a session_id → save_to_memory map once per cycle
                memory_modes = ChatDB.session_memory_modes() or {}

                drawers_filed = 0
                folders_seen = 0
                folders_skipped_chat = 0
                folders_sched = 0

                for agent_id, folder_path, folder_name, kind in _list_chat_artifact_folders():
                    folders_seen += 1
                    # Reconstruct session_id from folder name. For chat folders
                    # we only have the first 8 chars of the session_id; do a
                    # prefix lookup. For sched-* folders the prefix already is
                    # the full sched-<run_id> form.
                    parts = folder_name.split("_", 1)
                    sid_prefix = parts[1] if len(parts) > 1 else parts[0]

                    if kind == "sched":
                        folders_sched += 1
                        # Per-user opt-out: if the schedule's owner has set
                        # `memory_sched_default = 0`, skip filing this run's
                        # artifacts. Default behavior (pref=null/1/2) keeps
                        # the legacy "always file" path.
                        if _sched_run_skipped_by_owner_pref(sid_prefix):
                            continue
                        # Sched: file only output-role files (extension-based)
                        for fname in os.listdir(folder_path):
                            fpath = os.path.join(folder_path, fname)
                            if not os.path.isfile(fpath):
                                continue
                            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                            if ext in intermediate_exts:
                                continue
                            text = _file_text_or_none(fpath)
                            if not text:
                                continue
                            wing = f"{agent_id}_artifacts"
                            try:
                                res = tool_add_drawer(
                                    wing=wing,
                                    room="artifacts",
                                    content=text[:8000],
                                    source_file=f"session/{sid_prefix}#artifact/{fname}",
                                    added_by="brain-miner-sched",
                                )
                                if isinstance(res, dict) and res.get("success") \
                                   and res.get("reason") != "already_exists":
                                    drawers_filed += 1
                            except Exception as ex:
                                print(f"[mempalace-miner] sched add_drawer failed "
                                      f"{fname}: {ex}", flush=True)
                        continue

                    # Chat: gate on the parent session's save_to_memory toggle.
                    full_sid = ChatDB.session_id_for_prefix(sid_prefix)
                    mem_mode = memory_modes.get(full_sid, 0) if full_sid else 0
                    if mem_mode <= 0:
                        folders_skipped_chat += 1
                        continue

                    # Memory ON: ensure yaml + run miner over the folder.
                    wing = f"{agent_id}_artifacts"
                    if not _ensure_mempalace_yaml(folder_path, wing):
                        continue
                    buf = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(buf):
                            mp_miner.mine(
                                project_dir=folder_path,
                                palace_path=palace_path,
                                wing_override=wing,
                                agent="brain-miner-chat",
                                respect_gitignore=False,
                            )
                        out = buf.getvalue().strip()
                        for line in out.splitlines():
                            s = line.strip()
                            if s.startswith("Drawers filed"):
                                # "Drawers filed: N" — pull the integer
                                try:
                                    drawers_filed += int(s.split(":")[-1].strip().split()[0])
                                except Exception:
                                    pass
                                break
                    except SystemExit:
                        # miner.load_config calls sys.exit on bad yaml — should not
                        # happen now that we always write one, but be defensive.
                        pass
                    except Exception as e:
                        print(f"[mempalace-miner] chat folder {folder_name}: "
                              f"{type(e).__name__}: {e}", flush=True)

                print(f"[mempalace-miner] cycle: filed={drawers_filed} folders={folders_seen} "
                      f"(sched={folders_sched} chat-skip={folders_skipped_chat})", flush=True)
            except Exception as e:
                print(f"[mempalace-miner] cycle error: {type(e).__name__}: {e}", flush=True)

            next_interval = int(((mcfg2.get("mine") or {}).get("interval_seconds", interval)))
            time.sleep(max(60, next_interval))

    threading.Thread(target=_mempalace_miner_loop, daemon=True, name="mempalace-miner").start()

    # --- Sidecar supervisor (Phase 2) ---
    # Auto-start the Anthropic-SDK sidecar subprocess and keep it alive.
    # 3 crashes within 60s → stop auto-restarting and surface an error.
    def _start_sidecar_supervisor():
        import shutil
        import subprocess

        cfg_sc = server_config.get("sidecar") or {}
        if not cfg_sc.get("auto_start", False):
            print("[sidecar] auto_start disabled in config — supervisor not starting", flush=True)
            return

        url = (cfg_sc.get("url") or "http://127.0.0.1:8421").rstrip("/")
        # Parse port from url (we control --port via CLI)
        try:
            from urllib.parse import urlparse
            sidecar_port = urlparse(url).port or 8421
        except Exception:
            sidecar_port = 8421

        venv_python = cfg_sc.get("venv_python") or ".venv_sidecar/bin/python"
        repo_root = os.path.dirname(os.path.abspath(__file__))
        venv_python_abs = (venv_python if os.path.isabs(venv_python)
                           else os.path.join(repo_root, venv_python))
        sidecar_script = os.path.join(repo_root, "sidecar", "sidecar.py")

        if not os.path.isfile(venv_python_abs):
            print(f"[sidecar] FATAL: venv python not found at {venv_python_abs}", flush=True)
            print(f"[sidecar]   create it:  python3 -m venv .venv_sidecar && "
                  f".venv_sidecar/bin/pip install anthropic", flush=True)
            return
        if not os.path.isfile(sidecar_script):
            print(f"[sidecar] FATAL: sidecar.py missing at {sidecar_script}", flush=True)
            return

        crash_window: list[float] = []  # rolling list of crash timestamps

        def _supervisor_loop():
            while True:
                try:
                    print(f"[sidecar] launching {venv_python_abs} {sidecar_script} "
                          f"--port {sidecar_port}", flush=True)
                    proc = subprocess.Popen(
                        [venv_python_abs, sidecar_script, "--port", str(sidecar_port)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=repo_root, bufsize=1,
                        universal_newlines=True,
                    )

                    def _pump_logs(p):
                        try:
                            for line in p.stdout:
                                sys.stdout.write(line)
                                sys.stdout.flush()
                        except Exception:
                            pass

                    threading.Thread(target=_pump_logs, args=(proc,),
                                     daemon=True, name="sidecar-log-pump").start()
                    rc = proc.wait()
                    now = time.time()
                    crash_window.append(now)
                    crash_window[:] = [t for t in crash_window if now - t < 60.0]
                    print(f"[sidecar] subprocess exited rc={rc}  "
                          f"recent_crashes={len(crash_window)}/3", flush=True)
                    if len(crash_window) >= 3:
                        print(f"[sidecar] CIRCUIT BREAKER OPEN — 3 crashes in 60s. "
                              f"Halting auto-restart.", flush=True)
                        return
                    time.sleep(2.0)
                except Exception as e:
                    print(f"[sidecar] supervisor exception: {type(e).__name__}: {e}",
                          flush=True)
                    time.sleep(5.0)

        threading.Thread(target=_supervisor_loop, daemon=True,
                         name="sidecar-supervisor").start()

    _start_sidecar_supervisor()

    def _extract_references_from_tool_payload(tool_name, payload):
        """Turn a tool_result payload into a list of {title, url, snippet} dicts.

        Mirrors web/index.html's `extractReferencesFromToolResult` but server-side.
        Defensive against both raw dicts and JSON-string payloads.
        """
        if payload is None:
            return []
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                # Plain text — treat the whole thing as a single "snippet" record
                # only if it looks non-trivial. For web_fetch raw HTML we'd still
                # want to ingest, so take the first 4k chars.
                txt = payload.strip()
                if not txt:
                    return []
                return [{"title": tool_name, "url": "", "snippet": txt[:4000]}]
        if not isinstance(payload, dict):
            return []

        refs = []
        # exa_search shape: {query, results: [{title, link, snippet}, ...]}
        if tool_name == "exa_search" or "results" in payload:
            for r in payload.get("results", []) or []:
                if not isinstance(r, dict):
                    continue
                refs.append({
                    "title": r.get("title", "") or r.get("name", ""),
                    "url": r.get("link", "") or r.get("url", ""),
                    "snippet": (r.get("snippet", "") or r.get("highlight", ""))[:3000],
                })
        # web_fetch shape: {url, status, length, content}
        elif tool_name == "web_fetch" or "content" in payload:
            content = payload.get("content", "") or payload.get("text", "")
            if content:
                refs.append({
                    "title": payload.get("title", "") or tool_name,
                    "url": payload.get("url", ""),
                    "snippet": content[:4000],
                })
        # read_document shape: varies by parser; usually {pages|sheets|text}
        elif tool_name == "read_document":
            text = (
                payload.get("text")
                or payload.get("content")
                or json.dumps(payload, ensure_ascii=False)[:4000]
            )
            refs.append({
                "title": payload.get("filename", "") or payload.get("path", "") or "document",
                "url": payload.get("path", ""),
                "snippet": text[:4000],
            })
        return refs

    # MemPalace chat-sync daemon — mirrors chat turns, session summaries,
    # attachment metadata, and allowlisted tool_result references from
    # chats.db into MemPalace drawers. Rebuilds closets per (wing, room,
    # source_file) group so chat memories rank on par with mined code.
    def _mempalace_chat_sync_loop():
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            return
        sync_cfg = mcfg.get("chat_sync", {}) or {}
        if not sync_cfg.get("enabled", True):
            print("MemPalace chat-sync: disabled")
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            print(f"MemPalace chat-sync: {err}")
            return
        # Ensure downstream mempalace.mcp_server uses the right palace path.
        palace_path = mcfg.get("palace_path", "")
        if palace_path:
            os.environ.setdefault("MEMPALACE_PALACE_PATH", palace_path)
        try:
            from mempalace.mcp_server import tool_add_drawer
            from mempalace.miner import detect_hall
            from mempalace.palace import (
                get_collection as _get_drawers_col,
                get_closets_collection,
                build_closet_lines,
                purge_file_closets,
                upsert_closet_lines,
            )
        except Exception as e:
            print(f"MemPalace chat-sync: import failed: {e}")
            return

        # Small delay so we don't fight the miner on cold start.
        time.sleep(20)

        while True:
            try:
                mcfg2 = engine._load_mempalace_config()
                if not mcfg2.get("enabled", True):
                    return
                sync_cfg2 = mcfg2.get("chat_sync", {}) or {}
                if not sync_cfg2.get("enabled", True):
                    time.sleep(60)
                    continue

                include_roles = set(sync_cfg2.get("include_roles", ["user", "assistant"]))
                include_tool_results = set(sync_cfg2.get("include_tool_results", []) or [])
                max_chars = int(sync_cfg2.get("max_chars_per_message", 8000))
                include_summary = bool(sync_cfg2.get("include_session_summary", True))
                do_attach_meta = bool(sync_cfg2.get("attachment_metadata_drawer", True))
                do_closets = bool(sync_cfg2.get("build_closets", True))
                closet_head = int(sync_cfg2.get("closet_content_head_chars", 5000))
                default_room = sync_cfg2.get("room", "chat")

                # Classifier gate config
                clf_cfg = sync_cfg2.get("classifier", {}) or {}
                clf_enabled = bool(clf_cfg.get("enabled", False))
                clf_model = clf_cfg.get("model", "")
                clf_file_categories = set(clf_cfg.get("categories_to_file",
                    ["fact", "preference", "decision", "reference"]))
                clf_min_turns = int(clf_cfg.get("min_turns", 0))

                closets_col = None
                if do_closets:
                    try:
                        closets_col = get_closets_collection(palace_path, create=True)
                    except Exception as ce:
                        print(f"[mempalace-chat-sync] closets collection unavailable: {ce}")
                        closets_col = None

                pending = ChatDB.mempalace_sessions_needing_sync() or []
                total_new = 0
                for session_row in pending:
                    sid = session_row["session_id"]
                    agent_id = session_row.get("agent_id") or "main"
                    session_user_id = session_row.get("user_id") or ""
                    after_id = int(session_row.get("last_message_id_filed") or 0)
                    max_msg_id = int(session_row.get("max_message_id") or 0)
                    # save_to_memory: 0=off, 1=on (save all), 2=auto (classifier/min_turns)
                    mem_mode = int(session_row.get("save_to_memory") or 0)
                    msg_count = int(session_row.get("message_count") or 0)

                    # Off: skip entirely
                    if mem_mode == 0:
                        ChatDB.mempalace_update_cursor(sid, max_msg_id)
                        continue

                    # Auto/On: check min_turns (on=1 bypasses, auto=2 respects)
                    if mem_mode == 2 and clf_min_turns > 0 and msg_count < clf_min_turns:
                        ChatDB.mempalace_update_cursor(sid, max_msg_id)
                        continue

                    # Wing resolution (ID-only):
                    #   project session → project_chat__<project_id>
                    #     (NOT project__<id> — that's reserved for mined docs)
                    #   team session    → team__<team_id>
                    #   user session    → user__<user_id>
                    #   anonymous       → "" (skipped)
                    wing = _resolve_session_wing(session_row)
                    if not wing:
                        # Anonymous — advance cursor so we don't reprocess
                        # forever, but don't file anything.
                        ChatDB.mempalace_update_cursor(sid, max_msg_id)
                        continue

                    new_messages = ChatDB.mempalace_load_new_messages(sid, after_id) or []
                    # Per (wing, room, source_file) → list[(drawer_id, text)] for closet rebuild.
                    dirty_groups: dict[tuple, list] = {}

                    def _file_drawer(w, r, content, source_file):
                        if not content:
                            return False
                        content = content[:max_chars]
                        engine.mempalace_activity.store_begin()
                        try:
                            try:
                                res = tool_add_drawer(
                                    wing=w,
                                    room=r,
                                    content=content,
                                    source_file=source_file,
                                    added_by="brain-chat-sync",
                                )
                            except Exception as ex:
                                print(f"[mempalace-chat-sync] add_drawer failed: {ex}")
                                return False
                        finally:
                            engine.mempalace_activity.store_end()
                        if not isinstance(res, dict) or not res.get("success"):
                            return False
                        if res.get("reason") == "already_exists":
                            return False  # don't count dedup hits toward closet rebuild
                        # Stamp hall metadata (tool_add_drawer doesn't support it natively)
                        drawer_id = res.get("drawer_id", "")
                        if drawer_id:
                            try:
                                hall = detect_hall(content)
                                dcol = _get_drawers_col(palace_path, create=False)
                                if dcol and hall:
                                    existing = dcol.get(ids=[drawer_id], include=["metadatas", "documents"])
                                    if existing and existing["ids"]:
                                        meta = dict(existing["metadatas"][0])
                                        meta["hall"] = hall
                                        dcol.upsert(ids=[drawer_id], documents=existing["documents"], metadatas=[meta])
                            except Exception:
                                pass  # non-critical
                        group_key = (w, r, source_file)
                        dirty_groups.setdefault(group_key, []).append(
                            (drawer_id, content)
                        )
                        return True

                    new_last_id = after_id

                    # Build classifier skip-set: message IDs to skip based on LLM classification.
                    # Skip classifier entirely if user toggled save_to_memory on this session.
                    _clf_skip_ids: set[int] = set()
                    if clf_enabled and clf_model and mem_mode == 2:
                        # Pin the chat owner's user_id on the daemon thread so
                        # client-mode ambient proxy can pick a tab of the right
                        # user. Empty for legacy sessions without owner — the
                        # picker returns None and the LLM call fails fast on
                        # an air-gapped server (same fail-fast contract as
                        # scheduled tasks; Stage 2 closes that hole).
                        _prev_clf_uid = getattr(engine._thread_local,
                                                'current_user_id', None)
                        engine._thread_local.current_user_id = session_user_id or ""
                        try:
                            i = 0
                            while i < len(new_messages):
                                m = new_messages[i]
                                m_role = (m.get("role") or "").strip()
                                m_id = int(m.get("id") or 0)
                                # Pair user+assistant for classification
                                if m_role == "user" and i + 1 < len(new_messages):
                                    nxt = new_messages[i + 1]
                                    nxt_role = (nxt.get("role") or "").strip()
                                    nxt_id = int(nxt.get("id") or 0)
                                    if nxt_role == "assistant":
                                        u_text = str(m.get("content") or "")[:2000]
                                        a_text = str(nxt.get("content") or "")[:2000]
                                        category = engine.classify_chat_for_memory(
                                            u_text, a_text, clf_model)
                                        if category and category not in clf_file_categories:
                                            _clf_skip_ids.add(m_id)
                                            _clf_skip_ids.add(nxt_id)
                                            print(f"[mempalace-classifier] skip ({category}): "
                                                  f"{u_text[:60]}", flush=True)
                                        i += 2
                                        continue
                                i += 1
                        finally:
                            engine._thread_local.current_user_id = _prev_clf_uid

                    # Track the current turn's anchor user-message id. Every drawer
                    # filed from this turn (user, assistant, attachment, tool result)
                    # inherits this id in its source_file so per-turn purge/memorise
                    # can target one turn without touching neighbours.
                    current_turn_id = 0
                    # Seed with the last user id already in chats.db up to after_id so
                    # orphan assistant messages (e.g. when the sync cursor advanced
                    # mid-turn) still attach to their originating turn.
                    try:
                        prior_last_user_id = ChatDB.mempalace_last_user_id_before(sid, after_id)
                    except Exception:
                        prior_last_user_id = 0
                    current_turn_id = int(prior_last_user_id or 0)

                    for msg in new_messages:
                        mid = int(msg.get("id") or 0)
                        new_last_id = max(new_last_id, mid)
                        role = (msg.get("role") or "").strip()
                        content = msg.get("content")
                        meta = msg.get("metadata") or {}

                        # New user message opens a new turn.
                        if role == "user":
                            current_turn_id = mid

                        turn_suffix = f"#turn/{current_turn_id}" if current_turn_id else ""

                        # Normal chat turns.
                        if role in include_roles:
                            if mid in _clf_skip_ids:
                                continue
                            if isinstance(content, str):
                                text = content
                            else:
                                try:
                                    text = json.dumps(content, ensure_ascii=False)
                                except Exception:
                                    text = str(content)
                            body = f"[{role}] {text}".strip()
                            if body:
                                source_file = f"session/{sid}{turn_suffix}"
                                if _file_drawer(wing, default_room, body, source_file):
                                    total_new += 1

                        # Attachment metadata.
                        if do_attach_meta and isinstance(meta, dict):
                            files = meta.get("files") or []
                            if isinstance(files, list):
                                for f in files:
                                    if not isinstance(f, dict):
                                        continue
                                    fname = f.get("name") or f.get("filename") or "unknown"
                                    fmime = f.get("mime") or f.get("type") or "application/octet-stream"
                                    fsize = f.get("size") or 0
                                    body = (
                                        f"[attachment from {role or 'message'} "
                                        f"in session {sid}#{mid}]\n"
                                        f"filename: {fname}\n"
                                        f"mime: {fmime}\n"
                                        f"size: {fsize} bytes"
                                    )
                                    source_file = f"session/{sid}{turn_suffix}#attach/{mid}/{fname}"
                                    if _file_drawer(wing, "chat_attachment", body, source_file):
                                        total_new += 1

                        # Tool-result references (allowlisted tools only).
                        if role == "tool" and include_tool_results and isinstance(content, (list, dict, str)):
                            # Brain stores tool results in several shapes; try to
                            # extract (tool_name, payload) pairs defensively.
                            tool_entries = []
                            if isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict):
                                        tname = item.get("name") or item.get("tool_name") or ""
                                        payload = item.get("content") or item.get("result") or item
                                        tool_entries.append((tname, payload))
                            elif isinstance(content, dict):
                                tname = content.get("name") or content.get("tool_name") or ""
                                payload = content.get("content") or content.get("result") or content
                                tool_entries.append((tname, payload))
                            elif isinstance(meta, dict) and meta.get("tool_name"):
                                tool_entries.append((meta.get("tool_name"), content))

                            for tname, payload in tool_entries:
                                if tname not in include_tool_results:
                                    continue
                                # Parse payload into a list of reference records.
                                refs = _extract_references_from_tool_payload(tname, payload)
                                for idx, ref in enumerate(refs):
                                    ref_body = (
                                        f"[{tname} result from session {sid}#{mid}]\n"
                                        f"title: {ref.get('title','')}\n"
                                        f"url: {ref.get('url','')}\n\n"
                                        f"{ref.get('snippet','')}"
                                    )
                                    source_file = f"session/{sid}{turn_suffix}#tool/{tname}/{mid}/{idx}"
                                    if _file_drawer(wing, "reference", ref_body, source_file):
                                        total_new += 1

                    # Session summary — low-frequency text worth indexing separately.
                    summary_hash = ""
                    if include_summary:
                        summary = (session_row.get("summary") or "").strip()
                        if summary:
                            summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
                            if summary_hash != (session_row.get("last_summary_hash") or ""):
                                body = f"[session summary for {sid}]\n{summary}"
                                source_file = f"session/{sid}#summary"
                                if _file_drawer(wing, "chat_summary", body, source_file):
                                    total_new += 1

                    # Rebuild closets per dirty group.
                    if closets_col is not None and dirty_groups:
                        for (w, r, source_file), items in dirty_groups.items():
                            drawer_ids = [did for did, _ in items if did]
                            if not drawer_ids:
                                continue
                            concatenated = "\n\n".join(txt for _, txt in items)[:closet_head]
                            try:
                                purge_file_closets(closets_col, source_file)
                                lines = build_closet_lines(
                                    source_file=source_file,
                                    drawer_ids=drawer_ids,
                                    content=concatenated,
                                    wing=w,
                                    room=r,
                                )
                                if lines:
                                    closet_id_base = (
                                        f"{w}_{r}_"
                                        + hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:12]
                                    )
                                    upsert_closet_lines(
                                        closets_col,
                                        closet_id_base,
                                        lines,
                                        {"source_file": source_file, "wing": w, "room": r},
                                    )
                            except Exception as ce:
                                print(f"[mempalace-chat-sync] closet rebuild failed for {source_file}: {ce}")

                    # Advance cursor even if nothing new was filed (all dedup'd) —
                    # otherwise we keep re-scanning the same tail forever.
                    ChatDB.mempalace_update_cursor(
                        sid,
                        max(new_last_id, max_msg_id),
                        last_summary_hash=summary_hash or session_row.get("last_summary_hash") or "",
                    )

                if total_new:
                    print(f"[mempalace-chat-sync] filed {total_new} new drawer(s) across {len(pending)} session(s)")
            except Exception as e:
                print(f"[mempalace-chat-sync] cycle error: {type(e).__name__}: {e}")

            next_interval = int((engine._load_mempalace_config().get("chat_sync") or {}).get("interval_seconds", 60))
            time.sleep(max(15, next_interval))

    threading.Thread(target=_mempalace_chat_sync_loop, daemon=True, name="mempalace-chat-sync").start()

    # ── Project-sync daemon ─────────────────────────────────────────────
    # Walks every project's manual attachments (`ingested/`) plus any
    # user-configured `input_folders[]` and files them into the project's
    # private MemPalace wing (`project__<name>--<agent>`). Strict isolation:
    # only chats opened *inside* this project can ever see these drawers
    # because (a) chat-sync writes to the same project wing, and (b)
    # mempalace_query is force-scoped when the chat has a project set.
    #
    # Cadence: poll every 30 minutes by default. The user asked for hourly
    # freshness as the floor — 30 min lets a typical edit be discoverable
    # in the worst-case half hour. Overridable via mempalace.project_sync.
    # interval_seconds.
    def _project_sync_loop():
        from engine import sync_log as _sync_log
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            print("[project-sync] disabled (mempalace.enabled = false)", flush=True)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            print(f"[project-sync] {err}", flush=True)
            return
        try:
            from mempalace import miner as mp_miner
            from mempalace.mcp_server import tool_add_drawer  # noqa: F401  (used indirectly via miner)
            from mempalace.palace import get_collection as _get_drawers_col
        except Exception as e:
            print(f"[project-sync] import failed: {e}", flush=True)
            return

        # KG post-pass module — optional; tolerate import failure (the drawer
        # mining cycle still works). Loaded once per daemon start; if disabled
        # in config the helper short-circuits per call.
        try:
            from engine import kg_extract  # noqa: F401
        except Exception as e:
            kg_extract = None  # type: ignore[assignment]
            print(f"[project-sync] kg_extract import failed: "
                  f"{type(e).__name__}: {e}", flush=True)

        # Document conversion module — converts .pdf/.docx into companion
        # .md files under <folder>/.brain-extracted/ before the miner runs.
        # The miner itself only reads .md/.txt/code extensions; without this
        # pass, PDFs dropped into an input folder are silently ignored.
        try:
            from engine import doc_convert  # noqa: F401
        except Exception as e:
            doc_convert = None  # type: ignore[assignment]
            print(f"[project-sync] doc_convert import failed: "
                  f"{type(e).__name__}: {e}", flush=True)

        # Conversion preferences — markitdown vs legacy fitz/python-docx.
        # markitdown produces materially better markdown for LLM retrieval
        # (table structure, heading hierarchy, OCR fallback). Falls through
        # to legacy per-format extractors on any failure.
        def _conv_use_markitdown() -> bool:
            try:
                with open(engine.CONFIG_PATH) as f:
                    top = json.load(f)
                conv = (top.get("conversion") or {})
                return bool(conv.get("use_markitdown", True))
            except Exception:
                return True

        palace_path = mcfg.get("palace_path", "")
        chats_db_path = os.path.join(engine.AGENTS_DIR, "main", "chats.db")

        def _run_kg_for(wing: str, source_prefix: str, item_set_fn,
                        item_kind: str, item_id: str):
            """Run the KG extraction post-pass scoped to (wing, source_prefix).
            Updates the per-item dict via `item_set_fn(kind, id, **fields)` so
            the UI sees triples_extracted + kg_state alongside drawers_filed.
            Refuses (safely) if kg_extract isn't loaded or KG is disabled.
            """
            if kg_extract is None:
                return
            kg_cfg = (engine._load_mempalace_config().get("kg") or {})
            if not kg_cfg.get("enabled", True):
                return
            scopes = kg_cfg.get("scopes") or ["projects"]
            if "projects" not in scopes:
                return
            # Resolve symlinks so the prefix matches what the miner stored in
            # drawer source_file (macOS /tmp → /private/tmp, /var → /private/var,
            # plus user-managed symlinks). Without this, every drawer-mining
            # cycle stores absolute resolved paths but our prefix filter uses
            # the un-resolved one and matches nothing.
            try:
                resolved_prefix = os.path.realpath(source_prefix) if source_prefix else source_prefix
            except OSError:
                resolved_prefix = source_prefix
            if resolved_prefix and not resolved_prefix.endswith(os.sep) \
                    and source_prefix.endswith(os.sep):
                resolved_prefix += os.sep
            model = kg_cfg.get("extraction_model", "") or ""
            profile_name = kg_cfg.get("profile", "normative") or "normative"
            max_triples = int(kg_cfg.get("max_triples_per_drawer", 12))
            max_drawer_chars = int(kg_cfg.get("max_drawer_chars", 6000))
            min_conf = float(kg_cfg.get("min_confidence", 0.5))
            chunk_mode = (kg_cfg.get("chunking_mode") or "source_file").strip()
            if chunk_mode not in ("source_file", "per_drawer"):
                chunk_mode = "source_file"
            source_chunk_chars = int(kg_cfg.get("source_chunk_chars", 3500))
            try:
                import time as _time
                _kg_started_at = _time.time()
                _kg_chunks_done = [0]  # mutable cell for closure
                _kg_chunks_total = [0]

                def _kg_progress_cb(stage, **info):
                    if stage == "extracting":
                        pass  # chunk started — total not yet known here
                    elif stage in ("processed", "error"):
                        _kg_chunks_done[0] += 1
                        item_set_fn(item_kind, item_id,
                            kg_chunks_done=_kg_chunks_done[0],
                            kg_chunks_total=_kg_chunks_total[0],
                            kg_started_at=_kg_started_at,
                            kg_triples_live=info.get("running_total", 0))

                item_set_fn(item_kind, item_id,
                    kg_state="extracting",
                    kg_chunks_done=0,
                    kg_chunks_total=0,
                    kg_started_at=_kg_started_at,
                    kg_triples_live=0)
                res = kg_extract.run_kg_post_pass(
                    palace_path=palace_path, wing=wing,
                    source_prefix=resolved_prefix,
                    adapter_name="brain-project-kg",
                    profile_name=profile_name, model=model,
                    chats_db_path=chats_db_path,
                    max_triples_per_drawer=max_triples,
                    max_drawer_chars=max_drawer_chars,
                    min_confidence=min_conf, skip_code=True,
                    chunking_mode=chunk_mode,
                    source_chunk_chars=source_chunk_chars,
                    log_prefix="[project-sync.kg]",
                    progress_cb=_kg_progress_cb,
                )
                _kg_chunks_total[0] = res.drawers_processed + res.drawers_skipped
                # Cumulative triple count for this source prefix, queried
                # straight from the KG. `res.triples_extracted` is the per-
                # cycle delta — fine to log, wrong for the UI's "M triples"
                # pill which should stay positive across cursor-skip cycles.
                # Cheap SQL: COUNT() over a prefix-scoped slice with the
                # adapter_name filter (3.3.3 schema).
                triples_cumulative = int(res.triples_extracted)
                try:
                    cum_stats = kg_extract.kg_stats_for_wing(
                        palace_path=palace_path,
                        source_prefix=resolved_prefix,
                        adapter_name="brain-project-kg")
                    triples_cumulative = int(cum_stats.get("triples", 0))
                except Exception:
                    pass
                item_set_fn(item_kind, item_id,
                    kg_state=("error" if res.errors and not res.triples_extracted
                              else "idle"),
                    triples_extracted=triples_cumulative,
                    triples_last_cycle=int(res.triples_extracted),
                    kg_drawers_processed=int(res.drawers_processed),
                    kg_parse_errors=int(res.errors),
                    kg_last_error=res.error_msg or "",
                    kg_elapsed_s=round(res.elapsed_s, 1))
            except Exception as e:
                item_set_fn(item_kind, item_id,
                    kg_state="error",
                    kg_last_error=f"{type(e).__name__}: {e}")
                print(f"[project-sync.kg] wing={wing} prefix={source_prefix} "
                      f"failed: {type(e).__name__}: {e}", flush=True)

        def _run_closet_regen_for(wing: str, source_prefix: str = ""):
            """Regenerate LLM-augmented closets for the wing using the same
            model the user selected for KG extraction. Closets boost the
            ranking of vector retrieval (mempalace_query) — this swaps
            MemPalace's regex-based closet generation for an LLM pass that
            captures implicit topics, foreign-language content, and
            contextual references the regex misses.

            Opt-in via mempalace.kg.regenerate_closets. The wrapper is
            **incremental** (kg_extract.run_closet_regen_incremental):
            walks the wing's source files, compares each file's (mtime,
            size) against the closet_regen_progress cursor in chats.db,
            and only triggers the upstream wing-wide rebuild when at
            least one source has changed since the last cycle. With 400
            unchanged PDFs the wrapper short-circuits in milliseconds;
            with one edited PDF it runs the full wing rebuild (upstream
            doesn't accept per-file filters yet) and refreshes every
            cursor row.
            """
            if kg_extract is None:
                return
            kg_cfg = (engine._load_mempalace_config().get("kg") or {})
            if not kg_cfg.get("regenerate_closets"):
                return
            model = kg_cfg.get("extraction_model", "") or ""
            if not model:
                return
            try:
                # Resolve the KG model's provider so we can hand closet_llm
                # the OpenAI-compatible endpoint + key directly. Reuses the
                # same plumbing the chat / KG paths use, so a single config
                # change in the GUI applies here too.
                prov = engine.resolve_provider_for_model(model)
                api_model = engine.get_api_model_id(model)
                endpoint = prov.get("base_url", "")
                api_key = prov.get("api_key", "")
                if not endpoint or not api_model:
                    print(f"[project-sync.closet] {wing}: cannot resolve "
                          f"provider for {model!r} — skipping", flush=True)
                    return
                # Suppress chatty progress output from upstream regen;
                # the incremental wrapper logs its own one-line summary.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    out = kg_extract.run_closet_regen_incremental(
                        palace_path=palace_path, wing=wing,
                        source_prefix=source_prefix or "",
                        chats_db_path=chats_db_path,
                        endpoint=endpoint, api_key=api_key,
                        api_model=api_model,
                        log_prefix="[project-sync.closet]",
                    )
                if isinstance(out, dict) and out.get("error"):
                    print(f"[project-sync.closet] {wing}: error="
                          f"{out['error']}", flush=True)
                return out if isinstance(out, dict) else {}
            except Exception as e:
                print(f"[project-sync.closet] {wing}: failed: "
                      f"{type(e).__name__}: {e}", flush=True)
                return {"error": f"{type(e).__name__}: {e}"}

        def _count_wing_drawers_by_source(wing: str, source_prefix: str) -> int:
            """Authoritative count of drawers in `wing` whose source_file
            startswith(source_prefix). Used to populate per-item drawer
            counts after a mine — survives dedup-only re-runs unchanged.
            """
            try:
                col = _get_drawers_col(palace_path, create=False)
                if not col:
                    return 0
                # Chroma supports operator filters; use $and + startswith via
                # `$contains` is unreliable on metadata. Pull all and filter
                # in-Python — wings are typically small.
                got = col.get(where={"wing": wing}, include=["metadatas"])
                metas = got.get("metadatas") or []
                hits = 0
                for m in metas:
                    sf = (m or {}).get("source_file") or ""
                    if sf.startswith(source_prefix):
                        hits += 1
                return hits
            except Exception:
                return 0

        def _count_wing_drawers_total(wing: str) -> int:
            try:
                col = _get_drawers_col(palace_path, create=False)
                if not col:
                    return 0
                got = col.get(where={"wing": wing}, include=[])
                return len(got.get("ids") or [])
            except Exception:
                return 0

        def _count_wing_files_total(wing: str) -> int:
            """Distinct source_file count in `wing`. One file produces many
            drawers (chunks), so this is what the user actually means when
            they ask "how many files are indexed?" — drawer count is an
            internal storage detail."""
            try:
                col = _get_drawers_col(palace_path, create=False)
                if not col:
                    return 0
                got = col.get(where={"wing": wing}, include=["metadatas"])
                seen: set = set()
                for m in (got.get("metadatas") or []):
                    sf = (m or {}).get("source_file") or ""
                    if sf:
                        seen.add(sf)
                return len(seen)
            except Exception:
                return 0

        # NOTE: A startup-wipe block lived here through 2026-04-28. It was
        # added in 8.18.2 to clean up drawers tagged with the legacy
        # `project__<name>--<agent_id>` wing scheme after the rename to the
        # ID-only `project__<id>` scheme, but had no idempotency gate and
        # silently re-wiped + re-mined every project on every restart for
        # weeks. Removed entirely. If a future migration needs a similar
        # one-time cleanup, build it as an explicit admin endpoint
        # (`POST /v1/mempalace/migrate`) or `brain.py` subcommand — not as
        # implicit boot-time behavior. The migration this block was for has
        # been complete on every live install for weeks; there is nothing
        # left to clean up.

        # Small startup delay so we don't compete with the other two daemons.
        time.sleep(25)

        while True:
            try:
                mcfg2 = engine._load_mempalace_config()
                if not mcfg2.get("enabled", True):
                    return
                ps_cfg = (mcfg2.get("project_sync") or {})
                if not ps_cfg.get("enabled", True):
                    time.sleep(60)
                    continue
                # Default interval is 6 hours (21600s). Steady-state work is
                # incremental (doc_convert mtime/size, mp_miner content-hash
                # dedup, kg_extract cursor, closet_regen cursor) so re-running
                # every 30 min was wasted walks. Manual "Sync now" still works
                # on demand for instant re-mine after a file drop.
                interval = int(ps_cfg.get("interval_seconds", 21600))
                max_files_per_folder = int(ps_cfg.get("max_files_per_folder", 5000))

                # Drain manual "Sync now" requests first.
                with _project_sync_lock:
                    requested = set(_project_sync_requests)
                    req_triggers = dict(_project_sync_request_triggers)
                    _project_sync_requests.clear()
                    _project_sync_request_triggers.clear()

                # Enumerate all (agent, project) pairs by walking AGENTS_DIR.
                pairs = []
                try:
                    for agent_id in sorted(os.listdir(engine.AGENTS_DIR)):
                        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
                        if not os.path.isdir(agent_dir) or agent_id.startswith("."):
                            continue
                        proj_root = os.path.join(agent_dir, "projects")
                        if not os.path.isdir(proj_root):
                            continue
                        for proj_name in sorted(os.listdir(proj_root)):
                            pdir = os.path.join(proj_root, proj_name)
                            if not os.path.isdir(pdir) or proj_name.startswith("."):
                                continue
                            pairs.append((agent_id, proj_name))
                except Exception as e:
                    print(f"[project-sync] enumerate failed: {e}", flush=True)
                    pairs = []

                # Process requested-first, then everyone else.
                ordered = [p for p in pairs if p in requested] + [
                    p for p in pairs if p not in requested
                ]

                cycle_filed = 0
                for agent_id, proj_name in ordered:
                    # Manual "Sync now" overrides per-folder auto_sync gating —
                    # the user is explicitly asking, so skipping their non-auto
                    # folders would be confusing.
                    is_manual = (agent_id, proj_name) in requested
                    _trigger = req_triggers.get(
                        (agent_id, proj_name),
                        "manual" if is_manual else "scheduled")
                    project = engine.ProjectManager.get_project(agent_id, proj_name)
                    if not project:
                        continue
                    if project.get("status") == "archived":
                        continue
                    pdir = project.get("dir") or os.path.join(
                        engine.AGENTS_DIR, agent_id, "projects", proj_name)
                    project_id = project.get("id") or ""
                    if not project_id:
                        # Backfill safety net: get_project() should have set
                        # this on first read, but if persisting failed (RO
                        # filesystem etc.) skip this project for the cycle.
                        print(f"[project-sync] skip {agent_id}/{proj_name}: "
                              f"no project id", flush=True)
                        continue
                    wing = _project_wing(project_id)

                    _run_id = _sync_log.start_run(
                        chats_db_path, project_id, triggered_by=_trigger)
                    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    _project_sync_set_live(agent_id, proj_name,
                        state="syncing", started_at=started_at,
                        files_filed=0, error="", run_id=_run_id)
                    files_filed = 0
                    folders_seen = 0
                    last_error = ""
                    # Per-item state map: keyed by ("attachment", source_hash) for
                    # uploaded docs and ("folder", absolute_path) for input folders.
                    # The web UI joins this onto its rendered lists so each row
                    # carries its own indexed/syncing/error pill.
                    # Seed from the prior persisted run so cumulative counters
                    # (drawers_filed_total) survive — every cycle after the
                    # first is dedup-mostly and would otherwise overwrite a
                    # real "180 drawers indexed" snapshot with 0.
                    prior_items = ((project.get("sync_status") or {}).get("items") or {})
                    item_states: dict[str, dict] = {}
                    for k, v in prior_items.items():
                        if isinstance(v, dict):
                            item_states[k] = dict(v)
                    # Project-level cumulative count (across all cycles).
                    prior_total_indexed = int(
                        (project.get("sync_status") or {}).get("total_indexed") or 0)

                    # Stale-path purge: compare current input_folders against
                    # what's in MemPalace. Any drawer whose source_file starts
                    # with a path that is no longer an input folder gets purged.
                    # This catches manual project.json edits, renames, and
                    # moves that bypass the API handler's built-in purge.
                    _stale_drawers_purged = 0
                    _stale_closets_purged = 0
                    try:
                        _current_folder_prefixes = set()
                        for _fe in (project.get("input_folders") or []):
                            _fp = (_fe.get("path") or "").strip()
                            if _fp:
                                _r = os.path.realpath(os.path.expanduser(_fp))
                                _current_folder_prefixes.add(
                                    _r if _r.endswith(os.sep) else _r + os.sep)
                        # Also allow pdir itself (ingested attachments).
                        _pdir_real = os.path.realpath(pdir)
                        _current_folder_prefixes.add(
                            _pdir_real if _pdir_real.endswith(os.sep)
                            else _pdir_real + os.sep)

                        _mcfg_sp = engine._load_mempalace_config()
                        _palace_sp = _mcfg_sp.get("palace_path", "")
                        if _palace_sp and os.path.isdir(_palace_sp):
                            _ok_sp, _ = engine._ensure_mempalace_importable()
                            if _ok_sp:
                                from mempalace.palace import (
                                    get_collection as _get_col_sp,
                                    get_closets_collection as _get_ccol_sp,
                                )
                                _wing_sp = wing
                                _col_sp = _get_col_sp(_palace_sp, create=False)
                                if _col_sp:
                                    _res_sp = _col_sp.get(
                                        where={"wing": _wing_sp},
                                        include=["metadatas"])
                                    _stale_ids = [
                                        _did for _did, _m in zip(
                                            _res_sp["ids"], _res_sp["metadatas"])
                                        if not any(
                                            (_m.get("source_file") or "").startswith(_p)
                                            for _p in _current_folder_prefixes)
                                    ]
                                    if _stale_ids:
                                        _col_sp.delete(ids=_stale_ids)
                                        _stale_drawers_purged = len(_stale_ids)
                                        print(f"[project-sync] stale-path purge "
                                              f"{agent_id}/{proj_name}: "
                                              f"deleted {_stale_drawers_purged} drawer(s)",
                                              flush=True)
                                _ccol_sp = _get_ccol_sp(_palace_sp, create=False)
                                if _ccol_sp:
                                    _res_sp = _ccol_sp.get(
                                        where={"wing": _wing_sp},
                                        include=["metadatas"])
                                    _stale_cids = [
                                        _cid for _cid, _m in zip(
                                            _res_sp["ids"], _res_sp["metadatas"])
                                        if not any(
                                            (_m.get("source_file") or "").startswith(_p)
                                            for _p in _current_folder_prefixes)
                                    ]
                                    if _stale_cids:
                                        _ccol_sp.delete(ids=_stale_cids)
                                        _stale_closets_purged = len(_stale_cids)
                                        print(f"[project-sync] stale-path purge "
                                              f"{agent_id}/{proj_name}: "
                                              f"deleted {_stale_closets_purged} closet(s)",
                                              flush=True)
                    except Exception as _e_sp:
                        print(f"[project-sync] stale-path purge error "
                              f"{agent_id}/{proj_name}: "
                              f"{type(_e_sp).__name__}: {_e_sp}", flush=True)
                    if _run_id and (_stale_drawers_purged or _stale_closets_purged):
                        _sync_log.step_update(
                            chats_db_path, _run_id, "stale_path_purge",
                            drawers_deleted=_stale_drawers_purged,
                            closets_deleted=_stale_closets_purged,
                            at=time.time())

                    # Pre-scan to estimate cycle work for live progress / ETA.
                    # Cheap: just os.walk the ingested + input folders and count
                    # files (no hashing, no parsing). Counts every regular file;
                    # the miner's gitignore + ext filter will narrow this down,
                    # so the displayed P/T overshoots T slightly — that's fine,
                    # the ETA is approximate by design and overshoot beats
                    # undershoot (progress bar never appears stuck at 100%).
                    cycle_total_files = 0
                    cycle_processed_files = 0
                    cycle_folder_file_counts: dict[str, int] = {}
                    cycle_ingested_file_count = 0
                    ingested_dir_pre = os.path.join(pdir, "ingested")
                    if os.path.isdir(ingested_dir_pre):
                        try:
                            # Count distinct source uploads (hash count), not
                            # chunk count, so the progress P/T reflects "files
                            # the user uploaded" rather than internal chunks.
                            seen_hashes: set = set()
                            for fn in os.listdir(ingested_dir_pre):
                                if fn.startswith("ingest-") and fn.endswith(".md"):
                                    parts_fn = fn.split("-", 2)
                                    if len(parts_fn) >= 2:
                                        seen_hashes.add(parts_fn[1])
                            cycle_ingested_file_count = len(seen_hashes)
                            cycle_total_files += cycle_ingested_file_count
                        except OSError:
                            pass
                    for entry_pre in (project.get("input_folders") or []):
                        fp_pre = entry_pre.get("path") or ""
                        if not fp_pre or not os.path.isdir(fp_pre):
                            continue
                        # Skip auto_sync=false folders unless the project is in
                        # the manual-trigger set. They still get a 0-count entry
                        # so the folder-loop later can render the "paused" state.
                        if not is_manual and entry_pre.get("auto_sync", True) is False:
                            cycle_folder_file_counts[fp_pre] = 0
                            continue
                        rec = bool(entry_pre.get("recursive", True))
                        cnt = 0
                        try:
                            if rec:
                                for _root, _dirs, _files in os.walk(fp_pre):
                                    cnt += len(_files)
                            else:
                                cnt = sum(
                                    1 for e in os.scandir(fp_pre) if e.is_file())
                        except OSError:
                            cnt = 0
                        cycle_folder_file_counts[fp_pre] = cnt
                        cycle_total_files += cnt
                    _project_sync_set_live(agent_id, proj_name,
                        cycle_total_files=cycle_total_files,
                        cycle_processed_files=0)

                    def _item_key(kind: str, ident: str) -> str:
                        return f"{kind}:{ident}"

                    def _bump_processed(n: int):
                        nonlocal cycle_processed_files
                        cycle_processed_files += int(n or 0)
                        _project_sync_set_live(agent_id, proj_name,
                            cycle_processed_files=cycle_processed_files)

                    def _set_item(kind: str, ident: str, **fields):
                        k = _item_key(kind, ident)
                        cur = item_states.setdefault(k, {"kind": kind, "id": ident})
                        cur.update(fields)
                        # Push live snapshot so the UI sees state changes during
                        # the cycle, not just after the project.json write.
                        live = dict(_project_sync_live_status(agent_id, proj_name))
                        items_live = dict(live.get("items") or {})
                        items_live[k] = dict(cur)
                        _project_sync_set_live(agent_id, proj_name,
                            state="syncing", items=items_live,
                            files_filed=files_filed,
                            cycle_total_files=cycle_total_files,
                            cycle_processed_files=cycle_processed_files)

                    # 1. Manual attachments — `ingested/` mined into project wing.
                    #    Each chunk file is named ingest-<src_hash>-<idx>.md;
                    #    we group by src_hash so each upload appears as one item.
                    ingested_dir = os.path.join(pdir, "ingested")
                    if os.path.isdir(ingested_dir):
                        folders_seen += 1
                        # Discover all unique source hashes in the folder so we
                        # can mark each as "syncing" before mining begins.
                        hashes: set[str] = set()
                        try:
                            for fn in os.listdir(ingested_dir):
                                if fn.startswith("ingest-") and fn.endswith(".md"):
                                    parts_fn = fn.split("-", 2)
                                    if len(parts_fn) >= 2:
                                        hashes.add(parts_fn[1])
                        except OSError:
                            pass
                        for h in hashes:
                            _set_item("attachment", h,
                                state="syncing",
                                last_run_started=started_at)
                        if _ensure_mempalace_yaml(ingested_dir, wing):
                            # PDF/DOCX → .md pre-mine pass. The /ingested
                            # upload flow normally pre-chunks PDFs already,
                            # but covering this branch makes the daemon
                            # robust to direct file drops here too.
                            if doc_convert is not None:
                                if _run_id:
                                    _sync_log.step_start(
                                        chats_db_path, _run_id, "doc_convert",
                                        folder=ingested_dir)
                                try:
                                    _stale_cnt = doc_convert.sweep_stale(
                                        ingested_dir,
                                        log_prefix="[project-sync.conv]")
                                    _conv_res = doc_convert.convert_folder(
                                        ingested_dir,
                                        log_prefix="[project-sync.conv]",
                                        use_markitdown=_conv_use_markitdown())
                                    if _run_id:
                                        _sync_log.step_finish(
                                            chats_db_path, _run_id,
                                            "doc_convert",
                                            folder=ingested_dir,
                                            converted=_conv_res.converted,
                                            unchanged=_conv_res.skipped_unchanged,
                                            failed=_conv_res.failed,
                                            stale_removed=_stale_cnt,
                                            seen_total=_conv_res.seen_total,
                                            elapsed_s=round(_conv_res.elapsed_s, 2))
                                except Exception as e:
                                    print(f"[project-sync.conv] "
                                          f"{ingested_dir}: "
                                          f"{type(e).__name__}: {e}",
                                          flush=True)
                                    if _run_id:
                                        _sync_log.step_finish(
                                            chats_db_path, _run_id,
                                            "doc_convert",
                                            folder=ingested_dir,
                                            errors=[str(e)])
                            ingest_filed = 0
                            ingest_err = ""
                            if _run_id:
                                _sync_log.step_start(
                                    chats_db_path, _run_id, "indexing",
                                    folder=ingested_dir)
                            _index_t0 = time.time()
                            buf = io.StringIO()
                            try:
                                with contextlib.redirect_stdout(buf):
                                    mp_miner.mine(
                                        project_dir=ingested_dir,
                                        palace_path=palace_path,
                                        wing_override=wing,
                                        agent="brain-project-sync",
                                        respect_gitignore=False,
                                    )
                                for line in buf.getvalue().splitlines():
                                    s = line.strip()
                                    if s.startswith("Drawers filed"):
                                        try:
                                            ingest_filed = int(
                                                s.split(":")[-1].strip().split()[0])
                                        except Exception:
                                            pass
                                        break
                            except SystemExit:
                                pass
                            except Exception as e:
                                ingest_err = f"{type(e).__name__}: {e}"
                                last_error = ingest_err
                                print(f"[project-sync] {agent_id}/{proj_name} "
                                      f"ingested: {ingest_err}", flush=True)
                            if _run_id:
                                _sync_log.step_finish(
                                    chats_db_path, _run_id, "indexing",
                                    folder=ingested_dir,
                                    drawers_created=ingest_filed,
                                    elapsed_s=round(time.time() - _index_t0, 2),
                                    errors=[ingest_err] if ingest_err else [])
                            files_filed += ingest_filed
                            finished_at_attach = datetime.datetime.now(
                                datetime.timezone.utc).isoformat()
                            # Authoritative count per source: pull all wing
                            # drawers whose source_file references the chunk
                            # files belonging to this hash. Miner mines from
                            # the chunk file, so source_file ends with
                            # ingest-<hash>-<idx>.md.
                            for h in hashes:
                                cnt = _count_wing_drawers_by_source(
                                    wing, f"ingest-{h}-")
                                _set_item("attachment", h,
                                    state=("error" if ingest_err else "indexed"),
                                    last_run_finished=finished_at_attach,
                                    drawers_filed=cnt,
                                    error=ingest_err)
                            # Mark every uploaded source as processed in the
                            # cycle progress. Done as a batch since the miner
                            # call covers them all in one pass.
                            _bump_processed(len(hashes))
                            # KG extraction post-pass for each ingested
                            # attachment hash. Drawers carry source_file
                            # like .../ingested/ingest-<hash>-<idx>.md, so
                            # we can scope precisely per attachment.
                            for h in hashes:
                                if ingest_err:
                                    continue
                                _run_kg_for(
                                    wing=wing,
                                    source_prefix=os.path.join(
                                        ingested_dir, f"ingest-{h}-"),
                                    item_set_fn=_set_item,
                                    item_kind="attachment", item_id=h)
                                if _run_id:
                                    _it_a = item_states.get(
                                        _item_key("attachment", h)) or {}
                                    _sync_log.step_update(
                                        chats_db_path, _run_id, "kg",
                                        folder=ingested_dir,
                                        attachment_hash=h,
                                        triples_this_cycle=_it_a.get("triples_last_cycle", 0),
                                        triples_total=_it_a.get("triples_extracted", 0),
                                        drawers_processed=_it_a.get("kg_drawers_processed", 0),
                                        parse_errors=_it_a.get("kg_parse_errors", 0),
                                        elapsed_s=_it_a.get("kg_elapsed_s", 0),
                                        error=_it_a.get("kg_last_error", ""))
                        else:
                            for h in hashes:
                                _set_item("attachment", h,
                                    state="error",
                                    error="failed to write mempalace.yaml")
                            _bump_processed(len(hashes))

                    # 2. User-specified input folders — each entry has its own
                    #    mempalace.yaml, scanned recursively or top-level only.
                    for entry in (project.get("input_folders") or []):
                        # Cancel check between folders.
                        if _project_sync_cancel_check(project_id):
                            if _run_id:
                                _sync_log.cancel_run(chats_db_path, _run_id)
                            last_error = "cancelled"
                            break
                        folders_seen += 1
                        fpath = entry.get("path", "")
                        if not fpath:
                            continue
                        # Honor the per-folder auto_sync gate on scheduled cycles.
                        # Manual "Sync now" overrides — the user is asking
                        # explicitly. Update the item row so the UI shows the
                        # paused state instead of a stale "syncing".
                        if not is_manual and entry.get("auto_sync", True) is False:
                            existing_drawers = (item_states.get(
                                _item_key("folder", fpath)) or {}).get("drawers_filed", 0)
                            _set_item("folder", fpath,
                                state="paused",
                                drawers_filed=existing_drawers,
                                error="")
                            continue
                        if not os.path.isdir(fpath):
                            _set_item("folder", fpath,
                                state="error",
                                error="folder not found",
                                last_run_started=started_at)
                            _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                            continue
                        _set_item("folder", fpath,
                            state="syncing",
                            last_run_started=started_at,
                            error="")
                        # Tell live status which folder we're chewing on.
                        _project_sync_set_live(agent_id, proj_name,
                            state="syncing", current_folder=fpath,
                            files_filed=files_filed)
                        if not _ensure_mempalace_yaml(fpath, wing):
                            _set_item("folder", fpath,
                                state="error",
                                error="failed to write mempalace.yaml")
                            _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                            continue
                        # Cap the number of top-level entries to avoid runaway
                        # walks on accidentally-pointed-at home dirs etc.
                        try:
                            top_count = sum(1 for _ in os.scandir(fpath))
                            if top_count > max_files_per_folder:
                                msg = (f"folder has {top_count} entries "
                                       f"(>{max_files_per_folder} cap) — skipped")
                                last_error = msg
                                _set_item("folder", fpath,
                                    state="error", error=msg)
                                print(f"[project-sync] {agent_id}/{proj_name} "
                                      f"{fpath}: {msg}", flush=True)
                                _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                                continue
                        except OSError as e:
                            _set_item("folder", fpath,
                                state="error", error=str(e))
                            _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                            continue
                        folder_filed = 0
                        folder_err = ""
                        # PDF/DOCX → .md pre-mine pass. Without this the
                        # MemPalace miner silently skips binary documents
                        # (its READABLE_EXTENSIONS list is text-only).
                        # Idempotent — only re-converts when source mtime+size
                        # changes. Failures don't abort the cycle; per-file
                        # errors are logged and the rest of the folder still
                        # mines.
                        if doc_convert is not None:
                            if _run_id:
                                _sync_log.step_start(
                                    chats_db_path, _run_id, "doc_convert",
                                    folder=fpath)
                            try:
                                _stale_cnt_f = doc_convert.sweep_stale(
                                    fpath, log_prefix="[project-sync.conv]")
                                _conv_res_f = doc_convert.convert_folder(
                                    fpath, log_prefix="[project-sync.conv]",
                                    use_markitdown=_conv_use_markitdown())
                                if _run_id:
                                    _sync_log.step_finish(
                                        chats_db_path, _run_id, "doc_convert",
                                        folder=fpath,
                                        converted=_conv_res_f.converted,
                                        unchanged=_conv_res_f.skipped_unchanged,
                                        failed=_conv_res_f.failed,
                                        stale_removed=_stale_cnt_f,
                                        seen_total=_conv_res_f.seen_total,
                                        elapsed_s=round(_conv_res_f.elapsed_s, 2))
                            except Exception as e:
                                print(f"[project-sync.conv] {fpath}: "
                                      f"{type(e).__name__}: {e}", flush=True)
                                if _run_id:
                                    _sync_log.step_finish(
                                        chats_db_path, _run_id, "doc_convert",
                                        folder=fpath, errors=[str(e)])
                        if _run_id:
                            _sync_log.step_start(
                                chats_db_path, _run_id, "indexing",
                                folder=fpath)
                        _index_t0_f = time.time()
                        buf = io.StringIO()
                        try:
                            with contextlib.redirect_stdout(buf):
                                mp_miner.mine(
                                    project_dir=fpath,
                                    palace_path=palace_path,
                                    wing_override=wing,
                                    agent="brain-project-sync",
                                    respect_gitignore=True,
                                )
                            for line in buf.getvalue().splitlines():
                                s = line.strip()
                                if s.startswith("Drawers filed"):
                                    try:
                                        folder_filed = int(
                                            s.split(":")[-1].strip().split()[0])
                                    except Exception:
                                        pass
                                    break
                        except SystemExit:
                            pass
                        except Exception as e:
                            folder_err = f"{type(e).__name__}: {e}"
                            last_error = folder_err
                            print(f"[project-sync] {agent_id}/{proj_name} "
                                  f"{fpath}: {folder_err}", flush=True)
                        if _run_id:
                            _sync_log.step_finish(
                                chats_db_path, _run_id, "indexing",
                                folder=fpath,
                                drawers_created=folder_filed,
                                elapsed_s=round(time.time() - _index_t0_f, 2),
                                errors=[folder_err] if folder_err else [])
                        files_filed += folder_filed
                        # Authoritative cumulative drawer count: source_file
                        # in the wing always startswith the absolute folder
                        # path for files mined from this folder.
                        cum = _count_wing_drawers_by_source(wing, fpath)
                        _set_item("folder", fpath,
                            state=("error" if folder_err else "indexed"),
                            last_run_finished=datetime.datetime.now(
                                datetime.timezone.utc).isoformat(),
                            drawers_filed=cum,
                            error=folder_err)
                        # Bump cycle progress by the file count we pre-scanned
                        # for this folder. Slight overshoot is fine — see
                        # pre-scan comment.
                        _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                        # KG extraction post-pass for this input folder.
                        # source_prefix is the absolute folder path; the
                        # extractor's per-drawer cursor makes re-runs cheap
                        # (already-processed drawers skipped in O(1)).
                        if not folder_err:
                            _run_kg_for(
                                wing=wing, source_prefix=fpath,
                                item_set_fn=_set_item,
                                item_kind="folder", item_id=fpath)
                            if _run_id:
                                _it = item_states.get(_item_key("folder", fpath)) or {}
                                _sync_log.step_update(
                                    chats_db_path, _run_id, "kg",
                                    folder=fpath,
                                    triples_this_cycle=_it.get("triples_last_cycle", 0),
                                    triples_total=_it.get("triples_extracted", 0),
                                    drawers_processed=_it.get("kg_drawers_processed", 0),
                                    parse_errors=_it.get("kg_parse_errors", 0),
                                    elapsed_s=_it.get("kg_elapsed_s", 0),
                                    error=_it.get("kg_last_error", ""))

                    # Optional: regenerate closets via LLM for richer ranking.
                    # Runs once per project cycle after all folders are mined
                    # and KG-extracted. Opt-in via mempalace.kg.regenerate_closets;
                    # reuses the KG model so a single GUI choice covers both.
                    if last_error != "cancelled":
                        if _run_id:
                            _sync_log.step_start(
                                chats_db_path, _run_id, "closet_rerank")
                        _closet_out = _run_closet_regen_for(wing) or {}
                        if _run_id:
                            _sync_log.step_finish(
                                chats_db_path, _run_id, "closet_rerank",
                                sources_seen=_closet_out.get("sources_seen", 0),
                                sources_stale=_closet_out.get("sources_stale", 0),
                                regen_triggered=_closet_out.get("regen_triggered", False),
                                elapsed_s=round(_closet_out.get("elapsed_s", 0), 2),
                                errors=[_closet_out["error"]]
                                    if _closet_out.get("error") else [])

                    finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    final_state = ("cancelled" if last_error == "cancelled"
                                   else "error" if last_error and files_filed == 0
                                   else "idle")
                    # Authoritative total: query MemPalace for everything in
                    # the project's wing. Survives dedup-only cycles unchanged.
                    total_indexed = _count_wing_drawers_total(wing)
                    # Distinct source-file count — what the user reads as
                    # "how many files are indexed?" (a single PDF chunks into
                    # many drawers, so total_indexed alone is misleading).
                    total_files = _count_wing_files_total(wing)
                    # Authoritative total triples for this project (sum across
                    # all input folders in the wing). Cheap SQL — one COUNT
                    # over a prefix-scoped slice of the KG.
                    total_triples = 0
                    if kg_extract is not None:
                        def _norm_p2(p):
                            try:
                                r = os.path.realpath(p)
                            except OSError:
                                r = p
                            if r and not r.endswith(os.sep):
                                r += os.sep
                            return r
                        try:
                            stats = kg_extract.kg_stats_for_wing(
                                palace_path=palace_path,
                                source_prefix=_norm_p2(pdir),
                                adapter_name="brain-project-kg")
                            total_triples = int(stats.get("triples", 0))
                            # Also accumulate triples reached via input-folder
                            # source files outside pdir.
                            pdir_real = _norm_p2(pdir)
                            for entry in (project.get("input_folders") or []):
                                fp = entry.get("path") or ""
                                fp_real = _norm_p2(fp) if fp else ""
                                if fp_real and not fp_real.startswith(pdir_real):
                                    s2 = kg_extract.kg_stats_for_wing(
                                        palace_path=palace_path,
                                        source_prefix=fp_real,
                                        adapter_name="brain-project-kg")
                                    total_triples += int(s2.get("triples", 0))
                        except Exception as e:
                            print(f"[project-sync] kg stats failed "
                                  f"{agent_id}/{proj_name}: "
                                  f"{type(e).__name__}: {e}", flush=True)
                    sync_row = {
                        "state": final_state,
                        "last_run_started": started_at,
                        "last_run_finished": finished_at,
                        "last_triggered_by": _trigger,
                        "last_files_filed": files_filed,  # delta this cycle
                        "total_indexed": total_indexed,    # cumulative drawers
                        "total_files": total_files,        # cumulative files
                        "total_triples": total_triples,    # KG triples in wing
                        "last_folders_seen": folders_seen,
                        "last_error": last_error,
                        "items": item_states,
                    }
                    try:
                        engine.ProjectManager.update_project(agent_id, proj_name, {
                            "sync_status": sync_row,
                            "input_folders_last_scan": finished_at,
                        })
                    except Exception as e:
                        print(f"[project-sync] persist failed {agent_id}/{proj_name}: "
                              f"{type(e).__name__}: {e}", flush=True)
                    if _run_id and final_state != "cancelled":
                        _sync_log.finish_run(chats_db_path, _run_id, final_state, {
                            "total_files": total_files,
                            "total_indexed": total_indexed,
                            "total_triples": total_triples,
                            "files_filed_this_cycle": files_filed,
                            "folders_seen": folders_seen,
                            "final_state": final_state,
                            "elapsed_s": round(
                                time.time() - (
                                    _sync_log.get_run(chats_db_path, _run_id) or {}
                                ).get("started_at", time.time()), 1),
                            "errors": [last_error] if last_error else [],
                        })
                    _project_sync_clear_live(agent_id, proj_name)
                    cycle_filed += files_filed

                print(f"[project-sync] cycle: filed={cycle_filed} "
                      f"projects={len(pairs)} requested={len(requested)}", flush=True)
            except Exception as e:
                print(f"[project-sync] cycle error: {type(e).__name__}: {e}", flush=True)

            # If a new request arrived mid-cycle it's already in the set.
            # Skip the sleep entirely so it runs immediately.
            with _project_sync_lock:
                has_pending = bool(_project_sync_requests)
            if has_pending:
                continue
            # Sweep the ad-hoc extraction cache once per cycle (companions
            # for chat attachments + arbitrary read_document paths). Project
            # companions under .brain-extracted/ are managed by sweep_stale
            # above and never touched here. 30-day atime LRU is plenty —
            # an active chat re-touches its companions on every read.
            if doc_convert is not None:
                try:
                    doc_convert.evict_adhoc_cache(log_prefix="[project-sync.conv]")
                except Exception as e:
                    print(f"[project-sync.conv] adhoc-evict: "
                          f"{type(e).__name__}: {e}", flush=True)
            # Wait for the next interval, but wake up on demand. Default
            # is 6 hours (21600s) — incremental layers (doc_convert,
            # mp_miner, kg_extract, closet_regen) all cursor-skip on
            # unchanged content, so frequent walks were wasted overhead.
            wait_for = max(60, int(((engine._load_mempalace_config().get(
                "project_sync") or {}).get("interval_seconds", 21600))))
            woken = _project_sync_wakeup.wait(timeout=wait_for)
            if woken:
                _project_sync_wakeup.clear()

    threading.Thread(target=_project_sync_loop, daemon=True,
                     name="mempalace-project-sync").start()

    # Per-user profile maintainer daemon (worker logic is at module level so
    # both the daemon and the /v1/auth/profile-doc/update-now HTTP handler
    # can call it). Once per local hour walks users with
    # daily_summary_enabled and, if the target hour matches AND ≥23h since
    # last fire, regenerates agents/main/user_profiles/<uid>.md from chat
    # samples and mirrors per-section drawers to MemPalace.
    def _user_profile_loop():
        time.sleep(60)
        while True:
            try:
                _user_profile_cycle()
            except Exception as e:
                print(f"[profile] cycle error: {type(e).__name__}: {e}", flush=True)
            time.sleep(1800)

    def _user_profile_cycle():
        users = _auth_mod.AuthDB.list_users_with_preferences()
        if not users:
            return
        now = time.time()
        local_hour = time.localtime(now).tm_hour
        for u in users:
            uid = u.get("id") or ""
            if not uid:
                continue
            prefs = u.get("preferences") or {}
            if not prefs.get("daily_summary_enabled"):
                continue
            target_hour = int(prefs.get("daily_summary_hour_local") or 6)
            if local_hour != target_hour:
                continue
            cur = _auth_mod.AuthDB.get_daily_summary_cursor(uid)
            if (now - float(cur.get("last_run_ts") or 0)) < 23 * 3600:
                continue
            try:
                _profile_run_synchronous(u, since_ts=cur.get("last_run_ts") or 0, now=now)
            except Exception as e:
                print(f"[profile] user={uid} failed: {type(e).__name__}: {e}", flush=True)
                _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, f"error:{type(e).__name__}", "")

    threading.Thread(target=_user_profile_loop, daemon=True, name="user-profile").start()

    # Warmup keeper — fires minimal prefill requests at models flagged with
    # warmup=true so their first real turn lands on a warm KV cache. Runs
    # sequentially (max_concurrent default 1) to avoid saturating the local
    # gateway; respects per-model warmup_ttl_seconds (default 60s).
    def _warmup_keeper_loop():
        # Small startup delay so we don't race provider probes on boot
        time.sleep(5)
        while True:
            try:
                wcfg = server_config.get("warmup", {}) or {}
                if not wcfg.get("enabled", True):
                    time.sleep(30)
                    continue
                interval = int(wcfg.get("interval_seconds", 30))
                allow_cloud_global = bool(wcfg.get("allow_cloud", False))
                max_concurrent = int(wcfg.get("max_concurrent", 1))

                # Snapshot models with warmup=true. Each model picks its own
                # warmup_mode ("full" default | "minimal"). Full primes the
                # KV prefix so first-token latency is ~5-6s; minimal only
                # loads weights. If multiple full-prime models together
                # exceed GPU memory, oMLX will evict as needed — that's a
                # user-managed tradeoff, we don't second-guess it here.
                #
                # Only re-prime models whose state is idle/cold/failed or
                # whose configured mode changed since last prime.
                now = time.time()
                candidates = []
                for mid, _raw_cfg in list(engine._models_config.items()):
                    cfg = engine.resolve_model_settings(mid)
                    if not cfg.get("warmup"):
                        continue
                    if not cfg.get("enabled", True):
                        continue
                    desired_mode = (cfg.get("warmup_mode") or "full").lower()
                    if desired_mode not in ("full", "minimal"):
                        desired_mode = "full"
                    st = engine.get_warmup_state(mid)
                    state_name = st.get("state", "idle")
                    if state_name in ("warming", "skipped_cloud"):
                        continue
                    if state_name == "warm":
                        prev_mode = st.get("mode", "full")
                        if prev_mode == desired_mode:
                            engine.set_warmup_state(mid, next_due_ts=0)
                            continue
                        # Mode flipped — fall through to re-prime.
                    last = max(st.get("last_warmup_ts", 0), st.get("last_used_ts", 0))
                    age = now - last if last else 10 ** 9
                    candidates.append((age, mid, cfg, desired_mode))

                # Oldest first; cap to max_concurrent per cycle
                candidates.sort(key=lambda t: t[0], reverse=True)
                for _, mid, cfg, desired_mode in candidates[:max_concurrent]:
                    allow_cloud = bool(cfg.get("warmup_allow_cloud", allow_cloud_global))
                    t0 = time.time()
                    result = engine.run_model_warmup(
                        mid,
                        allow_cloud=allow_cloud,
                        agent_id="main",
                        timeout=int(wcfg.get("timeout_seconds", 30)),
                        mode=desired_mode,
                    )
                    dur = int((time.time() - t0) * 1000)
                    st_name = result.get("state", "?")
                    if result.get("ok"):
                        print(f"[warmup-keeper] {mid}: warm ({desired_mode}, {dur}ms)")
                    elif st_name == "skipped_cloud":
                        pass
                    else:
                        err = result.get("error", "?")
                        print(f"[warmup-keeper] {mid}: failed — {err}")

                # Second pass — top up the warm session pool toward target
                # depth. Only build for models that are fully warm (weights +
                # KV prefix primed). try_build is a no-op when the pool is
                # already full.
                for mid, _raw_cfg in list(engine._models_config.items()):
                    cfg = engine.resolve_model_settings(mid)
                    if not cfg.get("warmup") or not cfg.get("enabled", True):
                        continue
                    st = engine.get_warmup_state(mid)
                    if st.get("state") != "warm":
                        continue
                    warm_pool.try_build(mid)

                # Wait for either the interval or an explicit wake-up (model
                # config change, manual warmup trigger, etc.). wait() returns
                # True when the event was set — we consume it and re-run.
                woke = _warmup_wakeup.wait(timeout=max(5, interval))
                if woke:
                    _warmup_wakeup.clear()
            except Exception as e:
                print(f"[warmup-keeper] loop error: {type(e).__name__}: {e}")
                time.sleep(30)

    threading.Thread(target=_warmup_keeper_loop, daemon=True, name="warmup-keeper").start()

    def _save_chat_to_memory_callback(session_id: str) -> dict:
        """Enable save_to_memory on a session and trigger immediate sync."""
        # Set mode=1 (on) on session
        s = sessions.get(session_id)
        if s:
            s.save_to_memory = 1
        ChatDB.update_session_save_to_memory(session_id, 1)
        # Reset sync cursor to re-sync from beginning
        ChatDB.mempalace_update_cursor(session_id, 0)
        # Trigger sync in background thread
        def _do_sync():
            try:
                mcfg = engine._load_mempalace_config()
                if not mcfg.get("enabled", True):
                    return
                palace_path = mcfg.get("palace_path", "")
                if not palace_path:
                    return
                sync_cfg = mcfg.get("chat_sync", {}) or {}
                max_chars = int(sync_cfg.get("max_chars_per_message", 8000))
                default_room = sync_cfg.get("room", "chat")
                include_roles = set(sync_cfg.get("include_roles", ["user", "assistant"]))
                ok, _ = engine._ensure_mempalace_importable()
                if not ok:
                    return
                from mempalace.mcp_server import tool_add_drawer
                info = ChatDB.get_session_info(session_id)
                if not info:
                    return
                wing = _resolve_session_wing(info)
                if not wing:
                    return
                msgs = ChatDB.mempalace_load_new_messages(session_id, 0) or []
                filed = 0
                current_turn_id = 0
                for msg in msgs:
                    mid = int(msg.get("id") or 0)
                    role = (msg.get("role") or "").strip()
                    if role == "user":
                        current_turn_id = mid
                    turn_suffix = f"#turn/{current_turn_id}" if current_turn_id else ""
                    if role not in include_roles:
                        continue
                    content = msg.get("content")
                    text = content if isinstance(content, str) else str(content)
                    body = f"[{role}] {text}"[:max_chars]
                    if body.strip():
                        engine.mempalace_activity.store_begin()
                        try:
                            res = tool_add_drawer(wing=wing, room=default_room,
                                                  content=body, source_file=f"session/{session_id}{turn_suffix}",
                                                  added_by="brain-chat-sync")
                            if res.get("success") and res.get("reason") != "already_exists":
                                filed += 1
                        except Exception:
                            pass
                        finally:
                            engine.mempalace_activity.store_end()
                max_id = max((int(m.get("id") or 0) for m in msgs), default=0)
                if max_id:
                    ChatDB.mempalace_update_cursor(session_id, max_id)
                print(f"[mempalace-sync] immediate: filed {filed} drawer(s) for {session_id[:8]}", flush=True)
            except Exception as e:
                print(f"[mempalace-sync] immediate sync error: {e}", flush=True)
        threading.Thread(target=_do_sync, daemon=True).start()
        return {"saved": True, "session_id": session_id}

    engine._save_chat_to_memory_callback = _save_chat_to_memory_callback

    # Start enabled messaging channels
    def _start_channels():
        time.sleep(2)
        if _adapters_mod.channel_manager:
            _adapters_mod.channel_manager.start_all_enabled()
            n = len(_adapters_mod.channel_manager.channels)
            if n:
                print(f"Messaging channels: {n} loaded")
    threading.Thread(target=_start_channels, daemon=True, name="channels-start").start()

    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        _telegram_mod.telegram_service.stop()
        if _adapters_mod.channel_manager:
            _adapters_mod.channel_manager.stop_all()
        if engine._scheduler:
            engine._scheduler.stop()
        if engine._mcp_manager:
            engine._mcp_manager.stop_all()
        server.server_close()


if __name__ == "__main__":
    main()
