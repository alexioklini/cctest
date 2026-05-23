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

import threading


_thread_local = threading.local()


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
