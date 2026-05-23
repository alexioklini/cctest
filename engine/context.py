"""Per-request / per-thread execution context.

Canonical home for the thread-local execution-context object and the small
cluster of helpers that exclusively manage it. This is a LOW-level module:
it imports only the standard library and must NOT import brain (cycle).
Other engine modules and brain import the `_thread_local` instance FROM here.

The `_thread_local` INSTANCE IDENTITY is load-bearing: every module that
touches thread context must see the SAME `threading.local()` object, or
concurrent request context silently bleeds. brain re-exports this object
(`from engine.context import _thread_local`) so `brain._thread_local` and the
brain-aliased `engine._thread_local` resolve to this exact instance.

Thread-local attributes are set dynamically by each entry point (chat,
scheduled, delegate, warmup) — not declared here. Per CLAUDE.md the required
ones are current_agent, mcp_manager, current_session_id, current_user_id (plus
project, research_mode_override, and others set opportunistically).
"""

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, fields as _dc_fields


# ---------------------------------------------------------------------------
# RequestContext + ContextVar (Tier-G thread-local refactor, Phase 1)
# ---------------------------------------------------------------------------
# The single typed home for per-request execution state. Replaces the ad-hoc
# `threading.local()` attribute bag. Stored in a `contextvars.ContextVar` so it
# is isolated per logical context (works for the current threaded model AND any
# future async without rewriting call sites) and torn down atomically via token
# reset (no per-attribute `=None`).
#
# The legacy name `_thread_local` is kept as a COMPATIBILITY SHIM
# (`_RequestContextShim` below) whose attribute access proxies to the active
# RequestContext, so all three historical import spellings keep resolving and
# every existing `_thread_local.x` read/write hits the new storage. The shim is
# removed only in Phase 4, once no raw access remains.
#
# Fields + defaults below mirror the call-site `getattr(_thread_local, x, DEF)`
# defaults measured in Phase 0 (THREADLOCAL_REFACTOR_REPORT.md inventory), so a
# never-set field reads back the same value the old getattr-default produced.


@dataclass
class RequestContext:
    # --- core request identity ---
    current_agent: object = None
    current_user_id: str = ""
    current_session_id: object = None
    session_id: str = ""
    current_team_ids: list = field(default_factory=list)
    memory_store: object = None
    mcp_manager: object = None
    project: object = None
    # --- delegation ---
    delegate_agent_id: object = None
    current_worker_id: object = None
    # --- model select ---
    _current_model: object = None
    current_model: object = None
    # --- project mode ---
    research_mode_override: object = None
    # --- chat / stream ---
    event_callback: object = None
    note_context: object = None
    plan_mode: bool = False
    # --- caveman ---
    caveman_system: int = 0
    caveman_chat: int = 0
    # --- attachments ---
    attachment_image_model: str = ""
    attachments: object = None
    # --- gdpr ---
    _gdpr_mapping_id: str = ""
    _gdpr_anonymising: bool = False
    _gdpr_after_file_write_cb: object = None
    # --- tracing / audit ---
    trace_id: object = None
    current_trace_span: object = None
    audit_source: str = "chat"
    # --- tool exec ---
    tool_use_id: object = None
    tool_round: object = None
    _tool_results_tokens: int = 0
    _discovered_tools: object = None
    # --- exec ---
    execution_overrides: object = None
    _intent_action_recovery_count: object = None
    _guided_tasks_for_msg: object = None
    # --- workflow ---
    workflow_run_id: object = None
    workflow_execution_id: object = None
    workflow_default_model: str = ""
    workflow_agent_id: str = ""
    workflow_allowed_tools: object = None
    # --- dynamic escape hatch: arbitrary keys (e.g. `_artifact_folder_<sid>`)
    #     written via setattr(_thread_local, dynamic_key, val). Not enumerable,
    #     so they live here instead of as declared fields.
    _dynamic: dict = field(default_factory=dict)


# Set of declared field names (everything else routes to `_dynamic`).
_RC_FIELDS = frozenset(f.name for f in _dc_fields(RequestContext) if f.name != "_dynamic")

# The ContextVar holding the active RequestContext. Default factory can't be
# expressed on ContextVar directly, so the shim lazily installs a fresh
# RequestContext per context the first time it's touched.
_request_ctx: "contextvars.ContextVar[RequestContext]" = contextvars.ContextVar("brain_request_ctx")


def _active_ctx() -> RequestContext:
    """Return the RequestContext for the current contextvars context, creating
    a fresh one (and binding it) on first access in this context."""
    try:
        return _request_ctx.get()
    except LookupError:
        rc = RequestContext()
        _request_ctx.set(rc)
        return rc


class _RequestContextShim:
    """Back-compat proxy for the historical `_thread_local` name.

    All attribute access funnels to the active RequestContext (declared fields)
    or its `_dynamic` dict (everything else). The shim is a module-level
    singleton: every importer of `_thread_local` shares it, and it always reads
    the per-context state — so the old name and the new accessors see the SAME
    storage. Removed in Phase 4.
    """

    __slots__ = ()

    def __getattr__(self, name):
        rc = _active_ctx()
        if name in _RC_FIELDS:
            return getattr(rc, name)
        try:
            return rc._dynamic[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        rc = _active_ctx()
        if name in _RC_FIELDS:
            object.__setattr__(rc, name, value)
        else:
            rc._dynamic[name] = value

    def __delattr__(self, name):
        rc = _active_ctx()
        if name in _RC_FIELDS:
            # Reset to the dataclass default.
            object.__setattr__(rc, name, getattr(RequestContext(), name))
        else:
            rc._dynamic.pop(name, None)


_thread_local = _RequestContextShim()


# --- typed accessor + the one context-manager --------------------------------

def get_request_context() -> RequestContext:
    """The active RequestContext (creating + binding a fresh one if none)."""
    return _active_ctx()


@contextmanager
def request_context(**overrides):
    """Enter a NESTED request context with `overrides` applied; reset on exit.

    This is the ONLY sanctioned way to enter + tear down request state. Entering
    pushes a fresh RequestContext (token-tracked); exiting resets the ContextVar
    to its prior value — teardown is automatic + total (every field at once), so
    no per-attribute `=None` cleanup is needed and nested binds (delegate /
    sub-call) stack and pop correctly.

    Unknown keys (not declared RequestContext fields) go to `_dynamic`.
    """
    rc = RequestContext()
    for k, v in overrides.items():
        if k in _RC_FIELDS:
            object.__setattr__(rc, k, v)
        else:
            rc._dynamic[k] = v
    token = _request_ctx.set(rc)
    try:
        yield rc
    finally:
        _request_ctx.reset(token)


# ---------------------------------------------------------------------------
# ExecutionContext — Phase 1 refactor
# ---------------------------------------------------------------------------
# Captures the invariant fields that every execution mode (chat, scheduled,
# delegate) needs.  Built upfront by each entry point and passed to
# init_thread_context() so thread-local setup happens in one place.
# ---------------------------------------------------------------------------

class ExecutionContext:
    """Lightweight struct for per-request execution state.

    Fields are deliberately simple (no defaults that mask omissions):
      mode          – 'chat' | 'scheduled' | 'delegate'
      agent_id      – str  (e.g. 'main')
      session_id    – str  (chat session id or synthetic 'sched-<run_id>')
      user_id       – str  (empty string for background/anonymous)
      team_ids      – list[str]
      project       – str | None  (project name, if any)
      memory_store  – MemoryStore | None
      mcp_manager   – MCPManager | None
    """

    __slots__ = (
        "mode", "agent_id", "session_id", "user_id", "team_ids",
        "project", "memory_store", "mcp_manager",
    )

    def __init__(
        self,
        *,
        mode: str,
        agent_id: str,
        session_id: str = "",
        user_id: str = "",
        team_ids: "list[str] | None" = None,
        project: "str | None" = None,
        memory_store=None,
        mcp_manager=None,
    ):
        self.mode = mode
        self.agent_id = agent_id
        self.session_id = session_id
        self.user_id = user_id
        self.team_ids = team_ids or []
        self.project = project
        self.memory_store = memory_store
        self.mcp_manager = mcp_manager


def init_thread_context(ctx: ExecutionContext, agent_config=None) -> None:
    """Set thread-locals from an ExecutionContext.

    agent_config: AgentConfig instance for the agent (optional; omit when the
    caller has already set _thread_local.current_agent before calling this).
    """
    if agent_config is not None:
        _thread_local.current_agent = agent_config
    _thread_local.current_session_id = ctx.session_id
    _thread_local.session_id = ctx.session_id
    _thread_local.current_user_id = ctx.user_id
    _thread_local.current_team_ids = ctx.team_ids
    _thread_local.delegate_agent_id = ctx.agent_id
    if ctx.memory_store is not None:
        _thread_local.memory_store = ctx.memory_store
    if ctx.mcp_manager is not None:
        _thread_local.mcp_manager = ctx.mcp_manager
    if ctx.project is not None:
        _thread_local.project = ctx.project


def clear_thread_context() -> None:
    """Reset all context thread-locals to safe defaults after a request."""
    _thread_local.current_agent = None
    _thread_local.memory_store = None
    _thread_local.delegate_agent_id = None
    _thread_local.current_session_id = None
    _thread_local.session_id = None
    _thread_local.current_user_id = ""
    _thread_local.current_team_ids = []
    _thread_local.trace_id = None
