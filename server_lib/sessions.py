# Extracted from server.py — Session and SessionManager
import threading
import time
import uuid

import brain as engine
from server_lib.db import ChatDB

# Forward reference: BrainAgentHandler and server_config are defined in server.py.
# Sessions.py is imported by server.py; the references below resolve at call time.


class Session:
    """A conversation session with an agent."""

    def __init__(self, agent_id: str = "main", model: str | None = None,
                 api_key: str = "", base_url: str = "",
                 max_context: int = 131072, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id
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
        # Bound workflow_history.execution_id when this session was created
        # from the inline workflow detail view. Triggers the workflow-run
        # preamble injection so follow-ups have the run's context.
        self.workflow_run_id: str = ""
        self.summary: str = ""  # LLM-generated chat summary for sidebar
        self.sdk_session_id: str | None = None  # Agent SDK session ID for resume
        self._last_summary_at = 0  # Token count at last continuous summary
        self.save_to_memory: bool = False  # User toggle: always file to MemPalace
        self.caveman_mode: int = 0  # 0=off, 1=lite, 2=full, 3=ultra

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
                self.workflow_run_id = info.get("workflow_run_id", "") or ""


class SessionManager:
    """Thread-safe session storage with SQLite persistence."""

    _LOADING_SENTINEL = object()  # Sentinel to prevent duplicate DB loads

    def __init__(self):
        self._sessions: dict = {}
        self._lock = threading.Lock()
        self._load_events: dict = {}  # session_id -> Event for waiters
        ChatDB.init()

    def create(self, **kwargs) -> Session:
        session = Session(**kwargs)
        ChatDB.save_session(session.id, session.agent_id, session.model,
                           session.title, session.status, session.created_at, session.last_active,
                           session.project or "")
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str):
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
                # BrainAgentHandler is defined in server.py; resolved at call time.
                import server as _srv
                prov = _srv.BrainAgentHandler._resolve_provider_static(model) if model else {}
            except Exception:
                prov = {}
            try:
                import server as _srv
                _server_config = _srv.server_config
            except Exception:
                _server_config = {}
            loaded = Session(
                agent_id=info["agent_id"], model=model,
                api_key=prov.get("api_key", _server_config.get("api_key", "")),
                base_url=prov.get("base_url", _server_config.get("base_url", "")),
                max_context=engine.get_model_max_context(model) if model else _server_config.get("max_context", 131072),
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

    def peek(self, session_id: str):
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

    def list_all(self) -> list:
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
