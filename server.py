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
import server_daemons
from server_lib import notifications as _notif_mod
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


def _derive_session_title(text) -> str:
    """Derive a session title from a user message, stripping any internal
    annotations the chat handler appended to the wire payload (e.g. the
    attachment-routing notice). The title should reflect what the user
    typed, not Brain's bookkeeping.

    `text` may be a plain string OR a multimodal content list
    (`[{"type":"text","text":...}, {"type":"image_url",...}]`) — in the
    latter case we title on the concatenated TEXT parts only, never the
    raw list repr (which produced the "[{'type': 'image_url', ..." titles)."""
    if isinstance(text, list):
        # Multimodal content: keep only the text parts.
        parts = []
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        text = "\n".join(parts)
    elif not isinstance(text, str):
        text = str(text or "")
    # Attachment notice appended in chat.py when files are routed to disk.
    # Pattern: `\n\n[User attached files saved to disk...]\n  - <path>...`
    cut = text.find("\n\n[User attached files")
    if cut != -1:
        text = text[:cut]
    text = text.strip()
    if not text:
        # User sent only attachments without any prose. Fall back to a
        # neutral placeholder so the sidebar isn't blank.
        return "Anhang"
    title = text[:80].strip()
    if len(title) > 60:
        # Cut at last word boundary
        title = title[:60].rsplit(' ', 1)[0]
    return title


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
        # Per-session thinking level: '' = unset (use default at send time),
        # else 'none'|'low'|'medium'|'high'. Sticky across turns, restored on
        # reload — mirrors caveman_mode.
        self.thinking_level: str = ""
        # Per-session research-mode override (sticky across turns).
        # None = use the project's `research_mode` default;
        # True/False = force the override for this chat. Set from the composer
        # button or session settings; persists in chats.db sessions table.
        self.research_mode_override: bool | None = None
        # Manual-web-search escape hatch (sticky per session). False = curated
        # sources only, web_search/web_fetch hard-disabled when a turn carries
        # them. True = also permit autonomous web access on top. Only bites
        # when the turn carries enabled curated URLs. Persists in sessions DB.
        self.allow_further_web: bool = False
        # Sticky opt-in: when True, the post-turn GDPR feedback modal fires
        # after every turn that took a GDPR action (anonymise / local swap), so
        # the user can retry with a different method or abort. Set when the user
        # ticks "Frag mich nachher" in the pre-send modal; cleared when they
        # untick "Frag mich weiter" in the feedback modal. Persists in sessions DB.
        self.gdpr_feedback_ask: bool = False
        # Per-session "Datenschutz-Details sichtbar" toggle (shield detail
        # switch in the composer): when True, the chat view shows the GDPR
        # mark overlays + the expandable detail block. Persisted per chat so
        # reopening restores the state the user left it in. Persists in DB.
        self.gdpr_details_visible: bool = False
        # Per-session Websuche basket (curated web sources). JSON string of a
        # list of {url,title,snippet,query,enabled}. Persists in sessions DB so
        # it never bleeds across chats. Empty string = no basket.
        self.web_basket: str = ""
        # Per-session message queue: JSON list of {id,text} the user typed while
        # a turn was streaming, to auto-send as normal turns once it finishes.
        # Persisted so a reload restores the queue. Empty string = empty.
        self.message_queue: str = ""
        # Goal-Modus: while goal_status == 'active', every send runs the
        # post-turn judge loop (adapt + continue until the goal is met).
        # goal_status: '' none | 'active' | 'fulfilled' | 'capped'.
        # goal_max_iterations 0 = use the admin default. Persist in sessions DB.
        self.goal_text: str = ""
        self.goal_status: str = ""
        self.goal_iteration: int = 0
        self.goal_max_iterations: int = 0
        # Transparent anonymisation sticky preference (step 6.2). When non-
        # empty, the web modal is skipped on every send and this value is
        # forwarded as body.gdpr_action. Allowed: '', 'anonymise',
        # 'local_model', 'continue'. 'cancel' is NEVER persisted here.
        self.gdpr_action_pref: str = ""
        # In-memory only: set by `gdpr_action_pref` POST when the user clears
        # a previously-set pref. While True, the chat worker skips the
        # implicit "session has a mapping → keep anonymising" rule even when
        # `pseudonym_maps` has rows. Reset by reload (acceptable — at restart
        # the user can decide fresh on the next PII send).
        self._gdpr_skip_auto: bool = False
        self._gdpr_mapping_id: str | None = None
        self._gdpr_streamer = None

        self._streaming = False  # True while a chat turn worker is running

        # Warmup state
        self._warmup_done = threading.Event()
        self._warmup_done.set()  # default: no warmup needed
        self._warmup_active = False
        self._warmup_cancel = threading.Event()
        self._warmup_lock = threading.Lock()

        self.agent = engine.AgentConfig(agent_id)

    def add_message(self, role: str, content, metadata=None):
        msg = {"role": role, "content": content}
        if metadata:
            msg["metadata"] = metadata
        with self.lock:
            self.messages.append(msg)
            self.last_active = time.time()
            # Auto-title from first user message. Strip the round-0 preamble
            # (e.g. the artifact-folder note) — it's prepended into `content`
            # for the wire but is not what the user typed, so titling on it
            # would name the chat "[Session artifact folder ...".
            if not self.title and role == "user":
                # Pass content THROUGH (str OR multimodal list) so the title
                # deriver can pull text parts from a multimodal message instead
                # of titling on the raw list repr.
                text = content
                _pre = (metadata or {}).get("preamble") if metadata else None
                if _pre and isinstance(text, str) and text.startswith(_pre):
                    text = text[len(_pre):].lstrip("\n")
                self.title = _derive_session_title(text)
        ChatDB.save_message(self.id, role, content, metadata=metadata)
        ChatDB.save_session(self.id, self.agent_id, self.model, self.title,
                           self.status, self.created_at, self.last_active, self.project or "",
                           user_id=self.user_id)

    def switch_agent(self, agent_id: str, model: str | None = None):
        """Switch this session to a different agent (and optionally model)."""
        new_agent = engine.AgentConfig(agent_id)
        with self.lock:
            self.agent_id = agent_id
            self.agent = new_agent
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
                _tl = str(info.get("thinking_level", "") or "").lower()
                self.thinking_level = (_tl if _tl in
                    ("none", "low", "medium", "high") else "")
                _rmo = info.get("research_mode_override", None)
                self.research_mode_override = (None if _rmo is None
                                                else bool(_rmo))
                self.allow_further_web = bool(info.get("allow_further_web", 0))
                self.gdpr_feedback_ask = bool(info.get("gdpr_feedback_ask", 0))
                self.gdpr_details_visible = bool(info.get("gdpr_details_visible", 0))
                self.web_basket = info.get("web_basket", "") or ""
                self.pinned_sources = info.get("pinned_sources", "") or ""
                self.message_queue = info.get("message_queue", "") or ""
                self.goal_text = info.get("goal_text", "") or ""
                _gst = str(info.get("goal_status", "") or "")
                self.goal_status = (_gst if _gst in
                    ("active", "fulfilled", "capped") else "")
                self.goal_iteration = int(info.get("goal_iteration", 0) or 0)
                self.goal_max_iterations = int(
                    info.get("goal_max_iterations", 0) or 0)
                _pref = info.get("gdpr_action_pref", "") or ""
                self.gdpr_action_pref = (_pref if _pref in
                    ("anonymise", "local_model", "continue") else "")
                self.workflow_run_id = info.get("workflow_run_id", "") or ""
        # Rehydrate a prior GDPR mapping so a reloaded session that ever
        # anonymised continues to pseudonymise on the next turn without
        # requiring the client to re-prompt the user.
        try:
            from handlers.chat import rehydrate_session_gdpr_mapping
            rehydrate_session_gdpr_mapping(self)
        except Exception:
            pass


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
        from server_lib.feedback import FeedbackDB
        FeedbackDB.init()

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
                # NOTE: opening/accessing a session no longer bumps last_active.
                # last_active now reflects real ACTIVITY (a sent message, set in
                # handlers/chat.py) only — so the sidebar's "last used" order and
                # the auto-cleanup clock aren't disturbed by merely reading a chat.
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

    def streaming_session_ids(self) -> set[str]:
        """IDs of sessions with a live chat-turn worker running (in-memory
        `_streaming` flag). This is the authoritative 'currently generating' signal
        for the sidebar/project list pills — accurate (unlike the active_turns DB
        table, which can outlive a crash). Cache-only, no DB load."""
        out = set()
        with self._lock:
            for sid, s in self._sessions.items():
                if s is self._LOADING_SENTINEL:
                    continue
                if getattr(s, "_streaming", False):
                    out.add(sid)
        return out

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


def _project_init_run(agent_id: str, project_name: str, working_dir: str,
                      user_id: str = ""):
    """Kick off the Code-Mode 'init' worker (walk working_dir → write BRAIN.md).
    Delegates to engine.code_init; returns whether a new run started."""
    from engine import code_init
    return code_init.run_init(agent_id, project_name, working_dir, user_id)


def _project_init_status(agent_id: str, project_name: str):
    """Latest Code-Mode init run state for this project (or None)."""
    from engine import code_init
    return code_init.get_status(agent_id, project_name)


def _project_init_cancel(agent_id: str, project_name: str) -> bool:
    """Cancel an in-flight Code-Mode init. True if one was running."""
    from engine import code_init
    return code_init.cancel(agent_id, project_name)


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

    NO-OP when the prefix this would prime is already warm. run_model_warmup
    always primes the BARE main full prefix (it doesn't apply the session's
    project/domain context), so the right pre-check is "is the bare full prefix
    — or, for minimal-mode models, any warm prefix — already warm?". Without
    this, every new chat (incl. switching between a normal chat and a project
    chat on the same local model, where the pool can't serve a claim) re-fired a
    full prime even though the model was already warm — the mid-session re-warm.
    """
    try:
        mcfg = engine.resolve_model_settings(session.model) or {}
        if mcfg.get("warmup"):
            want_minimal = (mcfg.get("warmup_mode") or "full").lower() == "minimal"
            pid = engine.MINIMAL_PREFIX_ID if want_minimal else \
                engine._bare_full_prefix_id(session.model, "main")
            if pid is not None and engine.prefix_is_warm(
                    session.model, pid, minimal=want_minimal):
                return  # already warm — don't re-prime
    except Exception:
        pass  # on any doubt, fall through and prime (safe default)

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


from server_lib.sidecar_supervisor import searxng_supervisor, crawl4ai_supervisor


from handlers.auth import AuthHandlerMixin
from handlers.chat import ChatHandlerMixin
from handlers.sessions_handler import SessionsHandlerMixin
from handlers.providers import ProvidersHandlerMixin
from handlers.projects import ProjectsHandlerMixin
from handlers.wiki import WikiHandlerMixin
from handlers.admin import AdminHandlerMixin
from handlers.admin_workflows import AdminWorkflowHandlers
from handlers.admin_skills_gen import AdminSkillsGenHandlers
from handlers.admin_agents import AdminAgentsHandlers
from handlers.admin_costs import AdminCostsHandlers
from handlers.admin_config import AdminConfigHandlers
from handlers.admin_observability import AdminObservabilityHandlers
from handlers.admin_artifacts import AdminArtifactsHandlers
from handlers.favourites import FavouritesHandlerMixin
from handlers.translate import TranslateHandlerMixin
from handlers.share import ShareHandlerMixin
from handlers.classification import ClassificationHandlerMixin
from handlers.data_review import DataReviewHandlerMixin
from handlers.helpdesk import HelpdeskHandlerMixin
from handlers.feedback import FeedbackHandlerMixin
from handlers.background import BackgroundTasksHandlerMixin


def _fb_id_from_path(path: str) -> int:
    """Pull the numeric feedback id from /v1/feedback/<id>/<verb>. Returns -1
    on a malformed path so the handler resolves to a clean 404, not a 500."""
    parts = path.strip("/").split("/")
    try:
        return int(parts[2])  # v1 / feedback / <id> / <verb>
    except (ValueError, IndexError):
        return -1

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
        AdminWorkflowHandlers.__module__,
        AdminSkillsGenHandlers.__module__,
        AdminAgentsHandlers.__module__,
        AdminCostsHandlers.__module__,
        AdminConfigHandlers.__module__,
        AdminObservabilityHandlers.__module__,
        AdminArtifactsHandlers.__module__,
        FavouritesHandlerMixin.__module__,
        TranslateHandlerMixin.__module__,
        ShareHandlerMixin.__module__,
        ClassificationHandlerMixin.__module__,
        DataReviewHandlerMixin.__module__,
        FeedbackHandlerMixin.__module__,
        BackgroundTasksHandlerMixin.__module__,
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
    ClassificationHandlerMixin,
    DataReviewHandlerMixin,
    HelpdeskHandlerMixin,
    FeedbackHandlerMixin,
    BackgroundTasksHandlerMixin,
    WikiHandlerMixin,
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
        if not payload or not payload.get("user_id"):
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
    _PUBLIC_GET_PATHS = {"/v1/status", "/v1/auth/me", "/v1/changelog/curated"}
    _PUBLIC_POST_PATHS = {"/v1/auth/login", "/v1/auth/refresh"}

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
        "/v1/tools/settings",
        "/v1/research-mode/disciplines",
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
        "/v1/tools/settings",
        "/v1/research-mode/disciplines",
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
        "/v1/costs/rates",
        "/v1/searxng/status",
        "/v1/searxng/engines",
        "/v1/crawl4ai/status",
        "/v1/gdpr/ner-models",
        "/v1/classification/config",
        "/v1/helpdesk/config",
    }

    _ADMIN_POST_EXACT = {
        "/v1/auth/users",
        "/v1/auth/migrate",
        "/v1/auth/permissions",
        "/v1/costs/rates",
        "/v1/translate/tts/voices",
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
        "/v1/tools/settings",
        "/v1/research-mode/disciplines",
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
        "/v1/searxng/restart",
        "/v1/searxng/test-engines",
        "/v1/crawl4ai/restart",
        "/v1/gdpr/ner-models",
        "/v1/classification/config",
        "/v1/helpdesk/config",
        "/v1/admin/classify",
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
        if path.startswith("/v1/translate/tts/voices/"):
            return True
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
        from urllib.parse import unquote
        parts = path.split("/")
        if len(parts) >= 4:
            # The path segment is still percent-encoded (path = self.path), so a
            # non-ASCII or spaced id arrives as e.g. %C3%BC — decode it back to
            # the on-disk name.
            return unquote(parts[3])
        return ""

    def _parse_project_from_path(self, path: str) -> str:
        """Extract project name from /v1/agents/{id}/projects/{name}/..."""
        from urllib.parse import unquote
        parts = path.split("/")
        if len(parts) >= 6:
            # Decode the percent-encoded segment (project names may contain
            # non-ASCII like 'ü' → %C3%BC, or spaces → %20). Without this the
            # lookup uses the literal encoded string and 404s.
            return unquote(parts[5])
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

        if path == "/v1/wiki/config":
            self._handle_wiki_config_get()
        elif path == "/v1/cleanup/config":
            self._handle_cleanup_config_get()
        elif path == "/v1/wiki/tags":
            self._handle_wiki_tags_get()
        elif path == "/v1/wiki/tree":
            self._handle_wiki_tree(path)
        elif path.startswith("/v1/wiki/search"):
            self._handle_wiki_search()
        elif path.startswith("/v1/wiki/pages/"):
            self._handle_wiki_get(path)
        elif path == "/v1/chat/stream":
            self._handle_chat_stream()
        elif path == "/v1/status":
            self._handle_status()
        elif path == "/v1/changelog/curated":
            self._handle_changelog_curated()
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
        elif path == "/v1/sessions/active":
            self._handle_active_sessions()
        elif path == "/v1/sessions/export-bundle/download":
            self._handle_export_bundle_download()
        elif path.startswith("/v1/sessions/search"):
            self._handle_session_search()
        elif path == "/v1/studio/presets":
            self._handle_studio_presets_get()
        elif path.startswith("/v1/sessions/") and path.endswith("/inspect"):
            self._handle_session_inspect(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/gdpr-maps"):
            # Admin-only: list every pseudonym_maps row for this session.
            # Body returns ids + turn_ids + timestamps; the actual mapping
            # contents stay encrypted at rest. Step 6.4.
            self._handle_session_gdpr_maps_list(path)
        elif path.startswith("/v1/sessions/") and "/gdpr-maps/" in path:
            # Admin-only: decrypt one specific mapping and return the
            # before/after pairs so an auditor can see what the user typed
            # vs. what the cloud LLM actually received. Step 6.4.
            self._handle_session_gdpr_map_detail(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/files"):
            self._handle_get_session_files(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/pii-history-summary"):
            # Server-side history PII scan (regex + spaCy NER). The composer
            # history badge in web/js/nav.js unions these counts with its
            # local regex scan so soft-PII (name/address/organisation) that
            # only NER detects still surfaces.
            self._handle_session_pii_history_summary(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/pii-history-detail"):
            # Per-finding history scan WITH source attribution (chat text /
            # history / which attachment) + masked value. Feeds the large
            # GDPR history modal (web/js/panels_gdpr.js openPiiHistoryModal).
            self._handle_session_pii_history_detail(path)
        elif path.startswith("/v1/sessions/") and path.endswith("/pii-decisions-view"):
            # DB-ONLY view for the GDPR history modal: one row per DECIDED value
            # (latest decision), grouped-ready, with status + who/when trail. No
            # live re-scan — avoids the phantom-"open" duplicates a live scan
            # produces when its string form differs from the stored decision.
            self._handle_session_pii_decisions_view(path)
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
        elif path == "/v1/translate/stt-models":
            self._handle_stt_models()
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
        elif path.startswith("/v1/workflows/generate/"):
            self._handle_workflow_generate_get(path)
        elif path == "/v1/skills/match":
            self._handle_skill_match()
        elif path.startswith("/v1/skills/generate/"):
            self._handle_skill_generate_get(path)
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
        elif path == "/v1/costs/breakdown":
            self._handle_costs_breakdown()
        elif path == "/v1/costs/rates":
            self._handle_cost_rates_get()
        elif path == "/v1/plans/usage":
            self._handle_plans_usage()
        elif path == "/v1/quotas/me":
            self._handle_quota_me()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_get()
        elif path == "/v1/tools/settings":
            self._handle_tool_settings_get()
        elif path == "/v1/helpdesk/history":
            self._handle_helpdesk_history()
        elif path == "/v1/helpdesk/config":
            self._handle_helpdesk_config_get()
        elif path == "/v1/classification/config":
            self._handle_classification_config_get()
        elif path == "/v1/classification/scans":
            self._handle_classification_scans_list()
        elif path.startswith("/v1/classification/scans/"):
            self._handle_classification_scan_detail(path)
        elif path == "/v1/data-review/list":
            self._handle_data_review_list()
        elif path.startswith("/v1/data-review/") and path.endswith("/export"):
            self._handle_data_review_export(path)
        elif path.startswith("/v1/data-review/"):
            self._handle_data_review_get(path)
        elif path == "/v1/research-mode/disciplines":
            self._handle_research_mode_disciplines_get()
        elif path == "/v1/code-mode/extension":
            self._handle_code_mode_extension_get()
        elif path == "/v1/research/backend":
            self._handle_research_backend_status()
        elif path == "/v1/gdpr/ner-models":
            self._handle_gdpr_ner_models_get()
        elif path == "/v1/gdpr/decisions/stats":
            self._handle_gdpr_decisions_stats_get()
        elif path.startswith("/v1/gdpr/decisions"):
            self._handle_gdpr_decisions_get()
        elif path == "/v1/quotas/admin/users":
            self._handle_quota_admin_users()
        elif path.startswith("/v1/quotas/admin/breakdown"):
            self._handle_quota_admin_breakdown()
        elif path == "/v1/cache/stats":
            self._send_json(engine._web_cache.stats())
        elif path == "/v1/warmup/status":
            self._handle_warmup_status()
        elif path == "/v1/models/benchmark/status":
            self._handle_benchmark_status()
        elif path == "/v1/searxng/status":
            self._handle_searxng_status()
        elif path == "/v1/crawl4ai/status":
            self._handle_crawl4ai_status()
        elif path == "/v1/searxng/engines":
            self._handle_searxng_engines()
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
        elif path == "/v1/composer/defaults":
            self._handle_composer_defaults_get()
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
        elif path == "/v1/doctor":
            self._handle_doctor()
        elif path == "/v1/lib-versions":
            self._handle_lib_versions()
        elif path == "/v1/services/models":
            self._handle_service_models_get()
        elif path == "/v1/doc-styles":
            self._handle_doc_styles_get()
        elif path == "/v1/mcp/connections":
            self._handle_mcp_list()
        elif path == "/v1/mcp/registry":
            self._handle_mcp_registry()
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes" in path:
            self._handle_notes(path, "GET")
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/init-status"):
            self._handle_project_init_status(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-chats"):
            self._handle_project_code_chats(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/folder-tree" in path:
            self._handle_project_folder_tree(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/web-url-states" in path:
            self._handle_project_web_url_states(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/instruction-files"):
            self._handle_project_instruction_files_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs" in path:
            self._handle_project_docs(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sources"):
            self._handle_project_sources_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/input-folders"):
            self._handle_project_input_folders_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/terminal/sessions"):
            self._handle_terminal_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/terminal/sessions/" in path and path.endswith("/stream"):
            self._handle_terminal_stream(path, path.split("/terminal/sessions/", 1)[1].rsplit("/stream", 1)[0])
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/status"):
            self._handle_code_index_status(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/graph"):
            self._handle_code_index_graph(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/symbols"):
            self._handle_code_index_symbols(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/history"):
            self._handle_code_index_history(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-status"):
            self._handle_project_sync_status(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/ingest-status"):
            self._handle_project_ingest_status(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-runs"):
            self._handle_project_sync_runs(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/sync-runs/" in path:
            self._handle_project_sync_run_detail(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/image"):
            self._handle_project_image_get(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/outputs/" in path:
            self._handle_project_output_get(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/outputs"):
            self._handle_project_outputs_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/research/backends"):
            self._handle_research_backends(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.rstrip("/").endswith("/research/runs"):
            self._handle_research_runs_list(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/research/runs/" in path:
            self._handle_research_run_get(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/instruction-gen/" in path:
            self._handle_project_instruction_gen_get(path)
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
        elif path == "/v1/files/zip":
            self._handle_file_zip()
        elif path == "/v1/files/preview":
            self._handle_file_preview()
        elif path == "/v1/files/stat":
            self._handle_file_stat()
        elif path == "/v1/files/xlsx-grid":
            self._handle_file_xlsx_grid()
        elif path == "/v1/files/file-diff":
            self._handle_file_diff()
        elif path == "/v1/files/xlsm-vba":
            self._handle_file_xlsm_vba()
        elif path == "/v1/files/tree":
            self._handle_file_tree()
        elif path == "/v1/favourites":
            self._handle_favourites_list()
        elif path.startswith("/v1/favourites/image/"):
            self._handle_favourites_image_get(path)
        elif path == "/v1/feedback/mine":
            self._handle_feedback_mine()
        elif path.startswith("/v1/feedback/") and path.endswith("/thread"):
            self._handle_feedback_thread(_fb_id_from_path(path))
        elif path == "/v1/feedback":
            self._handle_feedback_list()
        elif path == "/v1/share":
            self._handle_share_get()
        elif path == "/v1/background-tasks":
            self._handle_background_tasks_list()
        elif path == "/v1/background-tasks/running":
            self._handle_background_tasks_running()
        elif path.startswith("/v1/background-tasks/") and path.endswith("/transcript"):
            self._handle_background_task_transcript(path)
        elif path == "/v1/artifacts":
            self._handle_artifacts_list()
        elif path == "/v1/artifacts/browse":
            self._handle_artifacts_browse()
        elif path.startswith("/v1/artifacts/") and path.endswith("/content"):
            self._handle_artifact_content(path)
        elif path.startswith("/v1/artifacts/") and path.endswith("/thumbnail"):
            self._handle_artifact_thumbnail(path)
        elif path.startswith("/v1/artifacts/") and path.endswith("/download"):
            self._handle_artifact_download(path)
        elif path == "/v1/tools/result":
            self._handle_tool_result_download()
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
        elif path == "/v1/files/save":
            self._handle_file_save()
            return
        elif path == "/v1/files/xlsx-cell":
            self._handle_file_xlsx_cell()
            return
        elif path == "/v1/files/rename":
            self._handle_file_rename()
            return
        elif path == "/v1/files/delete":
            self._handle_file_delete()
            return
        elif path == "/v1/files/mkdir":
            self._handle_file_mkdir()
            return
        elif path == "/v1/files/open-external":
            self._handle_file_open_external()
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
        elif path == "/v1/wiki/config":
            self._handle_wiki_config_save()
        elif path == "/v1/cleanup/config":
            self._handle_cleanup_config_save()
        elif path == "/v1/wiki/tags/rename":
            self._handle_wiki_tag_rename()
        elif path == "/v1/wiki/tags":
            self._handle_wiki_tags_save()
        elif path == "/v1/wiki/pages":
            self._handle_wiki_create(path)
        elif path == "/v1/wiki/from-message":
            self._handle_wiki_from_message()
        elif path.startswith("/v1/wiki/pages/") and "/promote/" in path:
            self._handle_wiki_promote(path)
        elif path.startswith("/v1/wiki/pages/") and path.rstrip("/").endswith("/move"):
            self._handle_wiki_move(path)
        elif path.startswith("/v1/wiki/pages/") and path.rstrip("/").endswith("/generate"):
            self._handle_wiki_generate(path)
        elif path.startswith("/v1/wiki/pages/") and path.rstrip("/").endswith("/media"):
            self._handle_wiki_media(path)
        elif path == "/v1/favourites":
            self._handle_favourites_add()
        elif path.startswith("/v1/favourites/") and path.endswith("/image"):
            self._handle_favourites_image_upload(path)
        elif path == "/v1/feedback":
            self._handle_feedback_submit()
        elif path.startswith("/v1/feedback/") and path.endswith("/message"):
            self._handle_feedback_message(_fb_id_from_path(path))
        elif path.startswith("/v1/feedback/") and path.endswith("/seen"):
            self._handle_feedback_seen(_fb_id_from_path(path))
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
        elif path == "/v1/chat/pause":
            self._handle_chat_pause()
        elif path == "/v1/chat/resume":
            self._handle_chat_resume()
        elif path == "/v1/chat/inject":
            self._handle_chat_inject()
        elif path == "/v1/chat/btw":
            self._handle_chat_btw()
        elif path == "/v1/background-tasks/cancel":
            self._handle_background_task_cancel()
        elif path == "/v1/background-tasks/cancel-session":
            self._handle_background_tasks_cancel_session()
        elif path == "/v1/background-tasks/cancel-tool":
            self._handle_background_task_cancel_tool()
        elif path == "/v1/helpdesk":
            self._handle_helpdesk()
        elif path == "/v1/helpdesk/clear":
            self._handle_helpdesk_clear()
        elif path == "/v1/helpdesk/warmup":
            self._handle_helpdesk_warmup()
        elif path == "/v1/helpdesk/delete":
            self._handle_helpdesk_delete()
        elif path == "/v1/helpdesk/config":
            self._handle_helpdesk_config_save()
        elif path == "/v1/web/search":
            self._handle_web_search()
        elif path == "/v1/studio/presets":
            self._handle_studio_preset_create()
        elif path == "/v1/sessions/manage":
            self._handle_manage_session()
        elif path == "/v1/sessions/export":
            self._handle_export_session()
        elif path == "/v1/sessions/export-bundle":
            self._handle_export_bundle()
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
        elif path == "/v1/searxng/restart":
            self._handle_searxng_restart()
        elif path == "/v1/crawl4ai/restart":
            self._handle_crawl4ai_restart()
        elif path == "/v1/searxng/test-engines":
            self._handle_searxng_test_engines()
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
        elif path == "/v1/workflows/generate":
            self._handle_workflow_generate()
        elif path.startswith("/v1/workflows/generate/") and path.endswith("/cancel"):
            self._handle_workflow_generate_cancel(path)
        elif path == "/v1/skills/generate":
            self._handle_skill_generate()
        elif path.startswith("/v1/skills/generate/") and path.endswith("/cancel"):
            self._handle_skill_generate_cancel(path)
        elif path == "/v1/skills/save":
            self._handle_skill_save()
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/approve"):
            self._handle_workflow_approve(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/cancel"):
            self._handle_workflow_cancel(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/pause"):
            self._handle_workflow_pause(path)
        elif path.startswith("/v1/workflows/executions/") and path.endswith("/resume"):
            self._handle_workflow_resume(path)
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
        elif path == "/v1/composer/defaults":
            self._handle_composer_defaults_save()
        elif path == "/v1/mempalace/kg/config":
            self._handle_kg_config_save()
        elif path == "/v1/doctor/live":
            self._handle_doctor_live()
        elif path == "/v1/services/models":
            self._handle_service_models_save()
        elif path == "/v1/doc-styles":
            self._handle_doc_styles_save()
        elif path == "/v1/mempalace/kg/reextract":
            self._handle_kg_reextract()
        elif path == "/v1/quotas/config":
            self._handle_quota_config_save()
        elif path == "/v1/costs/rates":
            self._handle_cost_rates_save()
        elif path == "/v1/plans/calibrate":
            self._handle_plans_calibrate()
        elif path == "/v1/plans/save":
            self._handle_plans_save()
        elif path == "/v1/plans/delete":
            self._handle_plans_delete()
        elif path == "/v1/tools/settings":
            self._handle_tool_settings_save()
        elif path == "/v1/classification/config":
            self._handle_classification_config_save()
        elif path == "/v1/classification/scan-files":
            self._handle_classification_scan_files()
        elif path == "/v1/classification/scan-folder":
            self._handle_classification_scan_folder()
        elif path == "/v1/classification/scan-project":
            self._handle_classification_scan_project()
        elif path == "/v1/data-review/analyze":
            self._handle_data_review_analyze()
        elif path == "/v1/data-review/overrule":
            self._handle_data_review_overrule()
        elif path == "/v1/data-review/anonymise":
            self._handle_data_review_anonymise()
        elif path == "/v1/data-review/revert":
            self._handle_data_review_revert()
        elif path == "/v1/data-review/state":
            self._handle_data_review_state()
        elif path == "/v1/research-mode/disciplines":
            self._handle_research_mode_disciplines_save()
        elif path == "/v1/code-mode/extension":
            self._handle_code_mode_extension_save()
        elif path == "/v1/gdpr/ner-models":
            self._handle_gdpr_ner_models_post()
        elif path == "/v1/cache/clear":
            engine._web_cache.clear()
            self._send_json({"status": "cleared"})
        elif path.startswith("/v1/sessions/") and path.endswith("/audio-overview"):
            self._handle_session_audio_overview(path)
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
        elif path == "/v1/chat/plan-review":
            self._handle_chat_plan_review()
        elif path == "/v1/chat/gdpr-recovery":
            self._handle_chat_gdpr_recovery()
        elif path == "/v1/chat/handover":
            self._handle_chat_handover()
        elif path == "/v1/attachments/scan":
            self._handle_attachment_scan()
        elif path == "/v1/gdpr/scan-text":
            self._handle_gdpr_scan_text()
        elif path == "/v1/gdpr/decisions":
            self._handle_gdpr_decisions_post()
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
            self._handle_tts_voice_create()
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
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/terminal/run"):
            self._handle_terminal_run(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/terminal/sessions"):
            self._handle_terminal_create(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/terminal/sessions/" in path and path.endswith("/input"):
            self._handle_terminal_input(path, path.split("/terminal/sessions/", 1)[1].rsplit("/input", 1)[0])
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/terminal/sessions/" in path and path.endswith("/close"):
            self._handle_terminal_close(path, path.split("/terminal/sessions/", 1)[1].rsplit("/close", 1)[0])
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/refresh"):
            self._handle_code_index_refresh(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/code-index/rebuild"):
            self._handle_code_index_rebuild(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-now"):
            self._handle_project_sync_now(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/full-resync"):
            self._handle_project_full_resync(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/sync-cancel"):
            self._handle_project_sync_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/generate-instructions"):
            self._handle_project_generate_instructions(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/instruction-gen/" in path and path.endswith("/cancel"):
            self._handle_project_instruction_gen_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/generate"):
            self._handle_project_generate(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/outputs/" in path and path.endswith("/rename"):
            self._handle_project_output_rename(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/outputs/" in path and path.endswith("/archive"):
            self._handle_project_output_archive(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/outputs/" in path and path.endswith("/cancel"):
            self._handle_project_output_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/artifacts/" in path and path.endswith("/archive"):
            self._handle_project_artifact_archive(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/research/runs/" in path and path.endswith("/cancel"):
            self._handle_research_run_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/web-urls/discover-links"):
            self._handle_weburl_discover_links(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/research/search"):
            self._handle_research_search(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/research/deep"):
            self._handle_research_deep(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/instruction-files"):
            self._handle_project_instruction_file_upload(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/init"):
            self._handle_project_init(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and path.endswith("/init-cancel"):
            self._handle_project_init_cancel(path)
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
        elif path == "/v1/admin/classify":
            self._handle_classify_probe()
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
        if path.startswith("/v1/wiki/pages/"):
            self._handle_wiki_update(path)
        elif path.startswith("/v1/studio/presets/"):
            self._handle_studio_preset_update(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
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
        if path.startswith("/v1/wiki/tags/"):
            self._handle_wiki_tag_delete(path)
            return
        if path.startswith("/v1/wiki/pages/"):
            self._handle_wiki_delete(path)
            return
        if path.startswith("/v1/studio/presets/"):
            self._handle_studio_preset_delete(path)
            return
        if path == "/v1/background-tasks":
            self._handle_background_task_delete()
            return
        if path.startswith("/v1/translate/tts/voices/"):
            self._handle_tts_voice_delete(path)
            return
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
            else:
                self._send_json({"error": "Session not found"}, 404)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/notes/" in path:
            self._handle_notes(path, "DELETE")
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/docs/" in path:
            self._handle_project_doc_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/outputs/" in path:
            self._handle_project_output_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/artifacts/" in path:
            self._handle_project_artifact_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/input-folders/" in path:
            self._handle_project_input_folders_delete(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/ingest-jobs/" in path:
            self._handle_project_ingest_job_cancel(path)
        elif path.startswith("/v1/agents/") and "/projects/" in path and "/instruction-files/" in path:
            self._handle_project_instruction_file_delete(path)
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
        elif path.startswith("/v1/feedback/"):
            self._handle_feedback_remove(path)
        elif path.startswith("/v1/translate/glossaries/"):
            slug = path[len("/v1/translate/glossaries/"):]
            self._handle_glossary_delete(slug)
        elif path.startswith("/v1/translate/history/"):
            entry_id = path[len("/v1/translate/history/"):]
            self._handle_translate_history_delete(entry_id)
        elif path.startswith("/v1/classification/scans/"):
            self._handle_classification_scan_delete(path)
        elif path.startswith("/v1/data-review/"):
            self._handle_data_review_delete(path)
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
                with engine.request_context():
                    agent_config = engine.AgentConfig(agent_id)
                    engine.get_request_context().current_agent = agent_config
                    engine.get_request_context().mcp_manager = engine._mcp_manager
                    if session_id:
                        engine.get_request_context().session_id = session_id
                        engine.get_request_context().current_session_id = session_id
                        engine.get_request_context().attachment_image_model = server_config.get("attachment_image_model", "")
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

    def _handle_status(self):
        self._send_json({
            "status": "running",
            "version": engine.VERSION,
            "agents": engine.list_agents(),
            "sessions": len(sessions.list_all()),
            "scheduler_tasks": len(engine._scheduler.list_all()) if engine._scheduler else 0,
            "changelog": [{"version": v, "date": d, "changes": c} for v, d, c in engine.CHANGELOG],
            "disabled_commands": server_config.get("disabled_commands", []),
            # Gates the composer's 🧬 MoA dropdown entry (enabled + non-empty pool).
            "moa_enabled": engine.moa_enabled(),
        })

    def _handle_changelog_curated(self):
        """Curated, end-user-facing version history (German, benefit-oriented) for the
        sidebar version-history modal. Distinct from the technical `changelog` in
        /v1/status — see engine/changelog_curated.py. Public (like /v1/status) so the
        version badge can open it even on the login screen."""
        self._send_json({
            "current_version": engine.VERSION,
            "current_date": engine.VERSION_DATE,
            "entries": engine.CURATED_CHANGELOG,
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
            # Optional project scope: ?project_id=<id> or ?project=<name>. The
            # project view passes this to show only the project's tasks; the
            # agent-global Zeitplan tab omits it and sees everything (unchanged).
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            from urllib.parse import unquote
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            filter_pid = unquote(params.get("project_id", ""))
            proj_name = unquote(params.get("project", ""))
            agent_q = unquote(params.get("agent", "")) or "main"
            if not filter_pid and proj_name:
                filter_pid = _project_id_for_name(agent_q, proj_name)
            if filter_pid:
                schedules = [s for s in schedules
                             if (s.get("project_id") or "") == filter_pid]
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
            # Optional project binding. Validate the id exists + the actor may
            # access the project, so a user can't bind a task to a project they
            # can't see. Empty = agent-global (unchanged behavior).
            project_id = (body.get("project_id") or "").strip()
            if project_id:
                err = self._validate_schedule_project(
                    body.get("agent", "main"), project_id, actor)
                if err:
                    self._send_json({"error": err}, 403)
                    return
            result = engine._scheduler.add(
                body.get("name", ""), body.get("task", ""),
                body.get("schedule", ""), body.get("agent", "main"),
                body.get("model"), timeout=int(body.get("timeout", 300)),
                attachments=atts, working_dir=wd,
                user_id=owner_id,
                thinking_level=body.get("thinking_level", "") or "",
                caveman_chat=body.get("caveman_chat", 0) or 0,
                tool_profile=body.get("tool_profile", "") or "",
                project_id=project_id,
                wiki_file=body.get("wiki_file", 0) or 0,
                goal=body.get("goal", "") or "",
                goal_max_iterations=body.get("goal_max_iterations", 0) or 0,
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
            _hist = engine._scheduler.get_history(name, body.get("limit", 20))
            # Annotate each run with its cost (sum of cost_log rows for the
            # synthetic sched-<run_id> session) so the history list can show it
            # without a per-row run_detail fetch.
            if engine._cost_tracker:
                for _h in _hist:
                    try:
                        _rid = _h.get("id")
                        if _rid is not None:
                            _sc = engine._cost_tracker.get_session_cost(f"sched-{_rid}")
                            _h["cost"] = round(_sc.get("cost", 0.0), 4)
                    except Exception:
                        pass
            self._send_json({"history": _hist})
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
                       "thinking_level", "caveman_chat", "tool_profile",
                       "project_id", "wiki_file", "goal", "goal_max_iterations")
                      if k in body}
            # Validate project access on (re)binding. Empty string is allowed
            # (clears the binding back to agent-global).
            _new_pid = (fields.get("project_id") or "").strip()
            if _new_pid:
                _eff_agent = fields.get("agent") or (
                    (engine._scheduler.get_task(name) or {}).get("agent") or "main")
                err = self._validate_schedule_project(
                    _eff_agent, _new_pid, getattr(self, "_auth_user", None))
                if err:
                    self._send_json({"error": err}, 403)
                    return
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
            # Cost of this run: sum of cost_log rows for the synthetic
            # sched-<run_id> session (same source the chat done-event uses).
            run_cost = None
            try:
                if engine._cost_tracker:
                    _sc = engine._cost_tracker.get_session_cost(session_id)
                    run_cost = round(_sc.get("cost", 0.0), 4)
            except Exception:
                run_cost = None
            self._send_json({
                "run": row,
                "session_id": session_id,
                "spans": spans,
                "artifacts": artifacts,
                "cost": run_cost,
            })
        else:
            self._send_json({"schedules": engine._scheduler.list_all()})

    def _validate_schedule_project(self, agent_id: str, project_id: str,
                                   actor: dict | None) -> str:
        """Validate a project binding for a schedule. Returns "" if OK, else an
        error message. Confirms the project_id resolves to an existing project
        the actor may access (so users can't bind to projects they can't see).
        Admins / system bypass the access check but the project must still
        exist."""
        try:
            projects = engine.ProjectManager.list_projects(agent_id or "main")
        except Exception:
            projects = []
        proj = next((p for p in projects if p.get("id") == project_id), None)
        if not proj:
            return f"Project '{project_id}' not found for agent '{agent_id or 'main'}'"
        is_admin = bool(actor and (actor.get("role") == "admin"
                                   or actor.get("id") == "__system__"))
        if not is_admin and actor:
            if not _auth_mod.can_access_project(actor, proj):
                return "Forbidden: no access to this project"
        return ""

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
    # Surface the auto-maintained profile as a user-visible wiki page (the
    # 'activity summary' the wiki promises). source_ref keeps it a single page
    # that re-versions on each profile update rather than forking. Best-effort.
    try:
        def _wiki_profile():
            try:
                from engine import wiki_store as _wiki
                _wiki.wiki_from_artifact(
                    title="Profil & Aktivität", body_md=content,
                    source="activity", source_ref=f"user-profile/{uid}",
                    user_id=uid, scope="user")
            except Exception as _e:
                print(f"[profile] wiki page sync failed uid={uid}: {_e}", flush=True)
        threading.Thread(target=_wiki_profile, daemon=True,
                         name=f"wiki-profile-{uid[:8]}").start()
    except Exception:
        pass
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
    # GROUNDING is stated FIRST and emphatically: small local models (M4 7B)
    # otherwise pad sections with plausible-sounding but invented background
    # ("years of experience", "deep expertise in …") that was never said.
    # Leading with this + the explicit CAPTURE clause keeps the local model
    # grounded AND stops it dropping stated preferences (verified 5/5 clean on
    # M4 7B; cloud was already correct and is unchanged).
    "GROUNDING (most important): Use ONLY facts the user actually stated in the "
    "chat samples. Do NOT invent background, experience, expertise, or history "
    "that was not explicitly said. If a section has no explicit evidence, its "
    "body MUST be exactly `_(none)_`. Inventing one fact makes the whole "
    "profile wrong.\n"
    "CAPTURE: Any preference, decision, or working style the user explicitly "
    "states ('I prefer X', 'always do Y') IS an explicit fact — record it "
    "(usually under Work context and/or Long-term background).\n\n"
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
    """Dedicated `user_profile_model` knob (split from the shared
    chat_summary_model in v9.166.0 — chat summary is now exclusive) wins;
    otherwise fall back to the server's default_model. No haiku / cheapest
    heuristics — the admin picks the model and we use it. GDPR auto-fallback
    still applies on top via gdpr_pick_model_for_background."""
    configured = (server_config.get("user_profile_model") or "").strip()
    if configured and engine._is_model_available(configured):
        return configured
    return engine._background_model_default() or ""

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
    # GDPR policy gate. Pseudonymises samples + prior profile when admin
    # picked `anonymise` policy; reply is de-anonymised before persistence
    # so the on-disk profile keeps real names. Aborts only on explicit
    # `abort` policy or pseudonymise-fail + `abort` fail action.
    _profile_deanon = engine._identity_deanon
    try:
        _inputs = samples + [prior_profile]
        model, _new_inputs, _profile_deanon = engine.gdpr_pick_model_for_background(
            model, _inputs, purpose="user_profile")
        if _new_inputs is not _inputs:
            samples = list(_new_inputs[:-1])
            prior_profile = _new_inputs[-1]
    except Exception as e:
        print(f"[profile] GDPR gate refused uid={uid}: {e}", flush=True)
        return None
    if not model:
        return None
    joined_samples = "\n\n".join(samples)
    if len(joined_samples) > 12000:
        joined_samples = joined_samples[:12000] + "\n\n[…older chats truncated]"
    if prior_profile.strip():
        # Prefix-cache ordering: constant task instructions FIRST (stable prefix
        # → provider prompt cache hit on a cache-priced model), the volatile
        # existing-profile + new-samples payload LAST. The grounding/capture rules
        # live in the (stable) _PROFILE_SYSTEM_PROMPT — untouched here.
        user_msg = (
            "Update the profile below. Move stale 'Top of mind' items to "
            "'Recent months' if no fresh evidence appears. Add new facts from "
            "the new samples. Treat the EXISTING PROFILE as ground truth and "
            "edit it in place. Output the COMPLETE new profile.\n\n"
            "EXISTING PROFILE:\n"
            f"```\n{prior_profile.strip()}\n```\n\n"
            "NEW CHAT SAMPLES SINCE LAST UPDATE:\n"
            f"{joined_samples}"
        )
    else:
        user_msg = (
            "Build the profile from scratch. The user's preferred name "
            f"is {greeting_name or 'unknown'}.\n\n"
            "CHAT SAMPLES (most recent first):\n"
            f"{joined_samples}\n\n"
            "Output the COMPLETE profile using the schema above."
        )
    with engine.request_context():
        try:
            # Use the same delegate path as _generate_chat_summary (the existing
            # in-tree pattern for background LLM calls). Returns the assistant's
            # text or a "Delegation error: …" string we filter out.
            # current_agent must be an AgentConfig object, not just the agent id.
            engine.get_request_context().current_agent = engine.AgentConfig("main")
            engine.get_request_context().current_user_id = uid
            from handlers import sidecar_proxy as _sidecar_proxy
            _res = _sidecar_proxy.background_call(
                messages=[{"role": "user", "content": user_msg}],
                model=model,
                system_prompt=_PROFILE_SYSTEM_PROMPT,
                agent_id="main",
                user_id=uid,
                cost_purpose="user_profile",
                max_tokens=2000,
            )
            result = _profile_deanon(_res.get("reply") or "")
            if not result:
                if _res.get("error"):
                    print(f"[profile] sidecar returned error: {str(_res['error'])[:200]}", flush=True)
                return None
            return result.strip()
        except Exception as e:
            print(f"[profile] LLM call uid={uid} failed: {type(e).__name__}: {e}", flush=True)
            return None

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
    # Module object handed to the lifted background daemons in
    # server_daemons.py so they can reach this module's singletons
    # (server_config, warm_pool, the _project_sync_* / _warmup_wakeup
    # events, _profile_run_synchronous, ...) without an import cycle.
    _srv = sys.modules[__name__]
    # Seed MEMPALACE_PALACE_PATH process-wide BEFORE anything touches MemPalace.
    # mempalace.mcp_server's _get_collection caches a backend handle keyed on the
    # resolved palace_path; if the FIRST touch happens with the env unset it
    # binds the stale default (~/.mempalace/palace, a dead chroma palace) and
    # caches that failure. The retired chat-sync daemon used to seed this; now we
    # do it at boot so the miner / memdash / wiki-mirror all resolve the right
    # (qdrant) palace from their very first call. setdefault: the plist env wins.
    try:
        _mp_cfg_boot = engine._load_mempalace_config()
        _mp_pp_boot = _mp_cfg_boot.get("palace_path", "")
        if _mp_pp_boot:
            os.environ.setdefault("MEMPALACE_PALACE_PATH", _mp_pp_boot)
    except Exception as _e_mp_boot:
        print(f"[boot] MemPalace palace-path seed skipped: {_e_mp_boot}")
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
    # Default-model precedence: explicit --model > top-level config.default_model
    # (what Settings → Server → Standardmodell persists) > server.default_model >
    # the default provider's default_model. Previously only the provider field
    # was read, so a model set in the UI never took effect at boot.
    _boot_default_model = (
        file_config.get("default_model")
        or srv_cfg.get("default_model")
        or provider.get("default_model", "")
    )
    parser.add_argument("-m", "--model", default=_boot_default_model)
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
    server_config["model_sync_auto_enable"] = bool(file_config.get("model_sync_auto_enable", True))
    server_config["chat_summary_model"] = file_config.get("chat_summary_model", "") or ""
    server_config["classifier_model"] = file_config.get("classifier_model", "") or ""
    server_config["next_prompt_model"] = file_config.get("next_prompt_model", "") or ""
    server_config["wiki_model"] = file_config.get("wiki_model", "") or ""
    server_config["user_profile_model"] = file_config.get("user_profile_model", "") or ""
    server_config["studio_model"] = file_config.get("studio_model", "") or ""
    # Custom Studio presets (v9.302.0) — boot copy; the CRUD handlers live-mirror
    # every save into this dict, so changes apply without a restart.
    server_config["studio_presets"] = file_config.get("studio_presets", []) or []
    server_config["audio_overview_model"] = file_config.get("audio_overview_model", "") or ""
    server_config["code_graph_model"] = file_config.get("code_graph_model", "") or ""
    server_config["deep_research_model"] = file_config.get("deep_research_model", "") or ""
    # instruction_gen_model was missing from this boot copy since 9.189.0 — the
    # Service-Modelle slot persisted to config.json but _server_config() never
    # saw it, so instruction generation silently used the background default.
    server_config["instruction_gen_model"] = file_config.get("instruction_gen_model", "") or ""
    server_config["workflow_gen_model"] = file_config.get("workflow_gen_model", "") or ""
    server_config["skill_gen_model"] = file_config.get("skill_gen_model", "") or ""
    server_config["wiki_gate_model"] = file_config.get("wiki_gate_model", "") or ""
    # goal_judge_model was missing from this boot copy since 9.256.0 — the
    # same trap as instruction_gen_model: the slot persisted to config.json
    # but _server_config() never saw it, so the goal judge silently used the
    # background default.
    server_config["goal_judge_model"] = file_config.get("goal_judge_model", "") or ""
    server_config["auto_route"] = file_config.get("auto_route", {}) or {}
    server_config["moa"] = file_config.get("moa", {}) or {}
    server_config["gdpr_scanner"] = file_config.get("gdpr_scanner", {}) or {}
    server_config["classification"] = file_config.get("classification", {}) or {}
    server_config["classification_scanner"] = file_config.get("classification_scanner", {}) or {}
    server_config["sidecar"] = file_config.get("sidecar", {}) or {}
    server_config["searxng"] = file_config.get("searxng", {}) or {}
    server_config["crawl4ai"] = file_config.get("crawl4ai", {}) or {}
    server_config["codebase_memory"] = file_config.get("codebase_memory", {}) or {}
    # warmup block was never loaded from config.json — every wcfg.get(...) in the
    # keeper + WarmSessionPool fell back to code defaults, so config.json →
    # warmup (interval, pool_depth, allow_cloud, …) was dead. Load it.
    server_config["warmup"] = file_config.get("warmup", {}) or {}

    # Per-tool prompt settings (admin-editable prose appended to system prompt
    # when the tool is in the active set). Migrate from legacy tools.md the
    # first time we see a config without the new key, then persist so admins
    # can edit via /v1/tools/settings without re-migrating.
    tool_settings_cfg = file_config.get("tool_settings")
    persisted_during_init = False
    if tool_settings_cfg is None:
        legacy_md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.md")
        tool_settings_cfg = engine.migrate_tool_settings_from_md(legacy_md)
        if tool_settings_cfg:
            print(f"Tool settings: migrated {len(tool_settings_cfg)} entries from tools.md")
        else:
            tool_settings_cfg = {}
        persisted_during_init = True  # force a write below to capture the seeded purposes
    server_config["tool_settings"] = tool_settings_cfg
    # Mirror onto engine globals so the resolver / renderer / migration helpers
    # see them without an import dependency on server_config.
    engine._tool_settings = server_config["tool_settings"]

    # Research-mode disciplines — admin-editable per-section text. First-boot
    # migration seeds from the brain.py defaults so admins see the live
    # values in the editor instead of an empty dict.
    rmd_cfg = file_config.get("research_mode_disciplines")
    if rmd_cfg is None:
        rmd_cfg = dict(engine.RESEARCH_MODE_DISCIPLINE_DEFAULTS)
        try:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            _cfg_disk = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    _cfg_disk = json.load(f)
            _cfg_disk["research_mode_disciplines"] = rmd_cfg
            with open(config_path, "w") as f:
                json.dump(_cfg_disk, f, indent=2)
            print(f"Research-mode disciplines: seeded {len(rmd_cfg)} sections from defaults")
        except Exception as e:
            print(f"Research-mode disciplines: persist failed: {e}")
    server_config["research_mode_disciplines"] = rmd_cfg
    engine._research_mode_disciplines = server_config["research_mode_disciplines"]

    # Code-mode prompt extension (single editable prose string; like the
    # research-mode disciplines). None on disk → leave None so the built-in
    # default applies (don't seed config.json — keeps it lean; the admin
    # saving it materialises the key).
    _cme = file_config.get("code_mode_extension")
    server_config["code_mode_extension"] = _cme
    engine._code_mode_extension = _cme

    # Snapshot BEFORE forward-migration so any rewrite below participates in
    # the persist gate below.
    _ts_before = json.dumps(server_config["tool_settings"], sort_keys=True)
    # Forward-migration: read_document + read_file routing hints (v8.6.3).
    # Mistral Small was observed picking `read_file` on a .docx attachment
    # because `read_document`'s prose was gated behind
    # `applies_with: ["mempalace_query"]` — so in non-project chats neither
    # tool had any description, leaving only the inline notice text in the
    # user message to steer routing. Without steering in the system prompt,
    # the model fell back to exploratory `read_file` → got ZIP garbage →
    # then `python_exec`/`execute_command` to manually unzip.
    #
    # Idempotent: only rewrites when the leading hint isn't already there.
    # Removes the `applies_with` gate on read_document so the routing prose
    # renders for every chat that has the tool, not just project chats.
    _ts = server_config["tool_settings"]
    _RD_PREFIX = (
        "**USE THIS TOOL for every binary document the user attached** — "
        "PDF, DOCX, XLSX, PPTX, CSV, EML, MSG. It parses the format "
        "server-side and returns clean extracted text. **Do NOT call "
        "`read_file` on binary documents** — `read_file` returns raw "
        "bytes (ZIP/PDF headers etc.) which are useless. **Do NOT call "
        "`python_exec` or `execute_command` to manually unzip / open / "
        "cat a binary attachment** — `read_document` already handles "
        "every common format with the right parser.\n\n"
    )
    _RF_HINT = (
        "**Plain-text files only** — `.txt`, `.md`, `.py`, `.json`, "
        "`.log`, `.csv`, `.yaml`, source code. For binary documents "
        "(PDF, DOCX, XLSX, PPTX, EML, MSG) use `read_document` instead "
        "— `read_file` will return raw bytes which are not useful to you."
    )
    _rd = _ts.get("read_document")
    if isinstance(_rd, dict):
        if not (_rd.get("description") or "").startswith("**USE THIS TOOL"):
            _rd["description"] = _RD_PREFIX + (_rd.get("description") or "")
        if _rd.get("applies_with") == ["mempalace_query"]:
            _rd["applies_with"] = []
    _rf = _ts.get("read_file")
    if isinstance(_rf, dict):
        if not (_rf.get("description") or "").startswith("**Plain-text files only**"):
            _rf["description"] = _RF_HINT + (
                ("\n\n" + _rf["description"]) if _rf.get("description") else "")

    # Seed `purposes` on every TOOL_DISPATCH entry from current behavior
    # (interactive / research_minimal / memory_summary). Idempotent — only
    # writes records that lack a populated purposes list. Runs every boot
    # so newly-added tools get their default purposes without admin action.
    engine.seed_tool_settings_purposes(server_config["tool_settings"])
    # Forward-migration: collapse legacy {enabled, deferred} booleans into the
    # canonical single `state` field ('active'|'inactive'|'deferred') and drop
    # the old keys. Idempotent. Removes the impossible enabled=false+deferred=true
    # combination from disk entirely (it's now unrepresentable).
    _state_migrated = engine.migrate_tool_settings_to_state(server_config["tool_settings"])
    if _state_migrated:
        print(f"Tool settings: migrated {_state_migrated} record(s) to canonical state")
    # ONE-TIME: seed per-use-case `states` from the legacy code base sets so the
    # table becomes the source of truth for purpose membership without changing
    # behaviour. Runs AFTER state migration (needs scalar `state`); idempotent —
    # skips any record that already has a `states` map (admin edits preserved).
    _seeded = engine.seed_tool_purpose_states(server_config["tool_settings"])
    if _seeded:
        print(f"Tool settings: seeded per-use-case states for {_seeded} tool(s)")
    # Backfill the `instruction_gen` purpose column on already-seeded installs
    # (the one-time seed above skips records that already have a states map, so a
    # purpose added later needs its own idempotent backfill). Non-members →
    # inactive, members → their scalar state.
    _bf = engine.backfill_purpose_column(
        server_config["tool_settings"], "instruction_gen", engine._INSTRUCTION_GEN_TOOLS)
    if _bf:
        print(f"Tool settings: backfilled instruction_gen column for {_bf} tool(s)")
    _bf = engine.backfill_purpose_column(
        server_config["tool_settings"], "workflow_step", engine._WORKFLOW_STEP_TOOLS)
    if _bf:
        print(f"Tool settings: backfilled workflow_step column for {_bf} tool(s)")
    _ts_after = json.dumps(server_config["tool_settings"], sort_keys=True)
    if _ts_before != _ts_after or persisted_during_init:
        try:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            _cfg_disk = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    _cfg_disk = json.load(f)
            _cfg_disk["tool_settings"] = server_config["tool_settings"]
            with open(config_path, "w") as f:
                json.dump(_cfg_disk, f, indent=2)
            if _ts_before != _ts_after:
                print(f"Tool settings: seeded purposes for {len(server_config['tool_settings'])} tools")
        except Exception as e:
            print(f"Tool settings: persist failed: {e}")

    # Migrate every agent's legacy tool_groups / extra_tools /
    # deferred_tool_groups fields into the new `tool_overrides` per-tool
    # dict. Idempotent — agents that already have overrides OR have no
    # legacy fields are skipped. The legacy fields stay on disk for now
    # (resolver still reads them in C1; C2 deletes the read paths).
    try:
        for agent_id in engine.list_agents():
            if engine.migrate_agent_tool_overrides(agent_id):
                print(f"Tool settings: migrated agent '{agent_id}' to tool_overrides")
            # Then collapse any legacy {enabled, deferred} override booleans into
            # the canonical `state` field (idempotent; runs after the group→
            # overrides migration above so newly-created override dicts get it too).
            if engine.migrate_agent_tool_overrides_to_state(agent_id):
                print(f"Tool settings: migrated agent '{agent_id}' overrides to canonical state")
    except Exception as e:
        print(f"Tool settings: agent migration failed: {e}")

    # Initialize models config
    existing_models = file_config.get("models")
    deleted_models = file_config.get("deleted_models", [])
    if providers:
        synced = engine.init_models_config(
            providers, existing_models, deleted_models=deleted_models,
            auto_enable_new=file_config.get("model_sync_auto_enable", True))
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

    # spaCy NER (Phase 1: German PER/LOC/ORG → name/address/organisation).
    # Eager-load so the first chat hitting the NER path doesn't pay the
    # ~1.5 s model-load cost. Action policy is governed by the `contact`
    # category in gdpr_scanner — admins who don't want NER findings set
    # `contact: ignore` (default) in Settings → GDPR. The pill in that
    # same tab can additionally load/unload at runtime via
    # POST /v1/gdpr/ner-models. Failures here are logged but never fatal —
    # server boots regardless and NER becomes a no-op for the lang.
    try:
        from engine import pii_ner
        # M9.3 (G12): load de + en. The KYC/DD corpus is majority non-German;
        # scan_text unions both models' findings. en absence is non-fatal
        # (is_available gates it → de-only pipeline).
        pii_ner.load_models(languages=("de", "en"))
    except Exception as e:
        print(f"[startup] spaCy NER skipped: {e}", flush=True)

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

    # Start task runner
    engine._task_runner = engine.TaskRunner()

    # Start ingest watcher (watched folders auto-ingestion)
    engine._ingest_watcher = engine.IngestWatcher()
    engine._ingest_watcher.start()
    print("Ingest watcher: started (30s poll)")

    # Start the async upload-extraction pool (staged /ingest uploads);
    # re-enqueues staging leftovers from a prior run.
    engine.INGEST_QUEUE.start()
    print(f"Ingest queue: started ({engine.INGEST_QUEUE.WORKERS} workers)")

    # Initialize main agent
    engine._current_agent = engine.AgentConfig("main")

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
    # NB: workflow-run sessions (workflow_run_id set, status 'workflow_run')
    # legitimately carry ZERO real message rows — their content is the run's
    # synthetic client-side transcript, backed by workflow_history + on-disk
    # artifacts. Excluding them here keeps a restart from silently deleting the
    # user's bound run chats (they'd otherwise re-mint on next open, but the
    # sidebar entry + seeded artifacts would vanish on every restart).
    try:
        with _db_conn() as conn:
            cutoff = time.time() - 300  # 5 minutes
            cur = conn.execute(
                "DELETE FROM sessions WHERE last_active < ? "
                "AND COALESCE(workflow_run_id, '') = '' AND id IN ("
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

    # One-shot startup: close any project-sync run left in state='running'.
    # Only ONE such run can be live at a time (single daemon thread), so on
    # boot every 'running' row is a leftover (crash/restart, or pre-finally-fix
    # orphan) that otherwise shows as a phantom "mining in progress" forever.
    try:
        from engine import sync_log as _sl
        chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
        n_orphan = _sl.reconcile_orphans(chats_db)
        if n_orphan:
            print(f"[startup-purge] closed {n_orphan} orphaned project-sync "
                  f"run(s) stuck in 'running'", flush=True)
    except Exception as e:
        print(f"[startup-purge] sync-run reconcile failed: "
              f"{type(e).__name__}: {e}", flush=True)

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
    print("  GET  /v1/costs/breakdown — per-use-case × per-model cost breakdown")
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
    threading.Thread(target=server_daemons._file_change_watcher, args=(_srv,), daemon=True, name="file-change-watcher").start()

    # MemPalace background miner — fully autonomous artifact ingestion.
    # Three rules (per user 2026-04-25):
    #   1. Scheduled-task artifacts: only output-role files (skip intermediate
    #      scripts/json/etc), regardless of any toggle. Never mines sched chat
    #      content (no session/messages rows exist for sched-* runs).
    #   2. Chat-originated artifacts: only when parent session has
    #      save_to_memory > 0. When ON, mine all files in that folder.
    #   3. mempalace.yaml is managed automatically per agent — server creates
    #      and refreshes it; user never touches it.

    threading.Thread(target=server_daemons._mempalace_miner_loop, args=(_srv,), daemon=True, name="mempalace-miner").start()

    # (The Anthropic-SDK sidecar subprocess was retired in v9.247.0 — the LLM
    # loop now runs in-process via engine/llm_loop.py. No supervisor needed.)

    # --- SearXNG supervisor ---
    # Brain owns the bundled SearXNG metasearch subprocess (.venv_searxng,
    # `python -m searx.webapp`), same spawn/monitor/circuit-breaker machinery
    # as the sidecar. Powers the searxng_search tool. Manual restart via
    # POST /v1/searxng/restart; state via GET /v1/searxng/status.
    searxng_supervisor.start(server_config)

    # --- crawl4ai render-service supervisor ---
    # Headless-Chromium HTML→markdown for JS-rendered pages (.venv_crawl4ai,
    # crawl4ai/render_service.py). Same supervisor machinery; web_fetch +
    # project-URL mining call it as a fallback when the cheap HTTP fetch yields
    # an empty / JS-shell result. Manual restart via POST /v1/crawl4ai/restart;
    # state via GET /v1/crawl4ai/status.
    crawl4ai_supervisor.start(server_config)

    def _kick_turn_recovery():
        # In-process loop: a turn dies WITH Brain, so there's no external event
        # log to re-attach to (the sidecar that used to provide one is gone). Boot
        # recovery just promotes any persisted partial streaming_text + clears the
        # stale active_turns rows (+ purges orphan pseudonym_maps).
        from handlers.chat import recover_active_turns_on_boot
        try:
            recover_active_turns_on_boot()
        except Exception as e:
            print(f"[turn-recovery] boot scan failed: {e}", flush=True)

    threading.Thread(target=_kick_turn_recovery, daemon=True,
                     name="turn-recovery-kick").start()


    # MemPalace chat-sync daemon — mirrors chat turns, session summaries,
    # attachment metadata, and allowlisted tool_result references from
    # chats.db into MemPalace drawers. Rebuilds closets per (wing, room,
    # source_file) group so chat memories rank on par with mined code.

    # LLM Wiki: chat-sync daemon RETIRED — the wiki is the sole feeder for
    # chat-derived wings (see server_daemons._mempalace_chat_sync_loop). Thread
    # no longer launched. Ingested project knowledge keeps its own (project-sync).

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

    threading.Thread(target=server_daemons._project_sync_loop, args=(_srv,),
                     daemon=True, name="mempalace-project-sync").start()

    # Code-index sync daemon: keeps each code-mode project's codebase-memory
    # index fresh by polling its working_dir fingerprint and re-indexing on
    # change (debounced). Replaces the per-write CodeGraph update; also drains
    # manual Refresh / clean-rebuild requests from the project UI.
    threading.Thread(target=server_daemons._code_index_sync_loop, args=(_srv,),
                     daemon=True, name="code-index-sync").start()

    # Per-user profile maintainer daemon (worker logic is at module level so
    # both the daemon and the /v1/auth/profile-doc/update-now HTTP handler
    # can call it). Once per local hour walks users with
    # daily_summary_enabled and, if the target hour matches AND ≥23h since
    # last fire, regenerates agents/main/user_profiles/<uid>.md from chat
    # samples and mirrors per-section drawers to MemPalace.


    threading.Thread(target=server_daemons._user_profile_loop, args=(_srv,), daemon=True, name="user-profile").start()

    # SearXNG per-engine health probe — hourly, so the Server-settings panel
    # shows a recent up/down + latency per search engine without manual testing.
    threading.Thread(target=server_daemons._searxng_engine_health_loop, args=(_srv,), daemon=True, name="searxng-engine-health").start()
    threading.Thread(target=server_daemons._bgtask_group_timeout_loop, args=(_srv,), daemon=True, name="bgtask-group-timeout").start()

    # Chat cleanup — auto-archive idle private chats (config archive_after_days)
    # then auto-delete long-archived ones (delete_after_days), each stage off
    # when its day-count is 0. Deleting a chat also removes its wiki. No-ops
    # unless config.json → chat_cleanup.enabled is true.
    threading.Thread(target=server_daemons._chat_cleanup_loop, args=(_srv,), daemon=True, name="chat-cleanup").start()

    # Warmup keeper — fires minimal prefill requests at models flagged with
    # warmup=true so their first real turn lands on a warm KV cache. Runs
    # sequentially (max_concurrent default 1) to avoid saturating the local
    # gateway; respects per-model warmup_ttl_seconds (default 60s).

    threading.Thread(target=server_daemons._warmup_keeper_loop, args=(_srv,), daemon=True, name="warmup-keeper").start()

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

    # Graceful shutdown on SIGTERM (launchd's normal stop signal). Without this,
    # launchd SIGKILLs the process and chromadb never runs its atexit HNSW flush
    # → the in-memory HNSW index is lost, sqlite is left far ahead, and the next
    # startup quarantines + rebuilds the index (the recurring MemPalace corruption
    # loop). Re-raising as KeyboardInterrupt routes SIGTERM through the SAME clean
    # finally below, then a normal interpreter exit lets chromadb flush to disk.
    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt()
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass  # not on the main thread (shouldn't happen here) — best-effort

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
