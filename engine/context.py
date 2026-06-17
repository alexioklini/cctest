"""Per-request execution context.

Canonical home for the typed request-context object + the helpers that manage
it. This is a LOW-level module: it imports only the standard library and must
NOT import brain (cycle). Other engine modules and brain reach request state via
`from engine.context import get_request_context` (or `request_context`).

State lives in a `contextvars.ContextVar` holding a `RequestContext` dataclass —
isolated per logical context (works for the current threaded model AND any future
async without rewriting call sites) and torn down atomically via token-reset
(no per-attribute `=None`). The Tier-G refactor replaced the previous ad-hoc
`threading.local()` attribute bag (and its `_thread_local` compatibility shim,
removed in Phase 4) with this — see THREADLOCAL_REFACTOR_REPORT.md.

Read/write request state ONLY through `get_request_context().<field>`; enter +
tear it down ONLY through `with request_context(**overrides):`. Field defaults
below mirror the old call-site `getattr(..., x, DEFAULT)` defaults so a never-set
field reads back the same value it always did.

NOTE: the DB-connection `threading.local()` pools elsewhere in the codebase
(`_db_pool`, `_auth_db_pool`, …) are a SEPARATE, correct use of thread-locals —
deliberately untouched by Tier-G.
"""

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field, fields as _dc_fields


# ---------------------------------------------------------------------------
# RequestContext + ContextVar (Tier-G thread-local refactor)
# ---------------------------------------------------------------------------
# The single typed home for per-request execution state. Stored in a
# `contextvars.ContextVar`; entered/torn-down via the `request_context()`
# context-manager below. Field defaults mirror the call-site getattr-defaults
# measured in the Phase-0 inventory.


@dataclass
class RequestContext:
    # --- core request identity ---
    current_agent: object = None
    current_user_id: str = ""
    current_session_id: object = None
    session_id: str = ""
    current_team_ids: list = field(default_factory=list)
    mcp_manager: object = None
    project: object = None
    # --- delegation ---
    delegate_agent_id: object = None
    current_worker_id: object = None
    # Written (never read) in execution.py worker-subagent path. Declared so the
    # typed accessor resolves; behaviour-identical to the old bare attr write.
    in_worker_subagent: bool = False
    # --- model select ---
    _current_model: object = None
    current_model: object = None
    # Read (never written) in handlers/chat.py cost logging — a vestige of the
    # deleted native-loop quota force-local swap. Always None today; declared so
    # the typed accessor resolves (behavior-identical to the old getattr-None).
    _fallback_model_used: object = None
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
    # Per-turn GDPR outcome, surfaced to the user as a chat badge + redo control
    # (set in the chat worker at the anonymise / local-fallback decision points,
    # read when assembling the assistant turn's metadata.gdpr). Dict or None.
    _gdpr_turn_outcome: object = None
    # --- tracing / audit ---
    trace_id: object = None
    current_trace_span: object = None
    audit_source: str = "chat"
    # --- tool exec ---
    tool_use_id: object = None
    tool_round: object = None
    _tool_results_tokens: int = 0
    _discovered_tools: object = None
    # Per-turn tool exclusion. Names listed here are dropped from the resolved
    # tool set for this turn (resolve_active_tools subtracts them), even when
    # otherwise enabled. Set on the chat worker's request context; read by the
    # resolver. Generic — used by the manual-web-search flow to hard-disable
    # web_search/web_fetch when the user supplies a curated source set.
    exclude_tools: object = None
    # Per-turn classifier-driven tool DEFERRAL adjustment (non-warmup models
    # only — never set for local/warmup models so their KV prefix is stable).
    # `defer_extra_tools`: extra tool names to push OUT of the initial prompt
    # this turn (still tool_search-discoverable). `undefer_tools`: normally-
    # deferred tool names to force INTO the prompt because the classifier
    # flagged their group as needed. Both fold into resolve_active_tools's
    # defer computation. Set by the chat worker from classifier_tool_deferral.
    defer_extra_tools: object = None
    undefer_tools: object = None
    # Helpdesk ("Brainy") mode. Set ONLY by the helpdesk endpoint's background
    # call. Unlocks the backend-exclusive `brain-agent-guide` skill (hidden from
    # normal chat) and selects the `helpdesk` tool-resolver purpose. Never set on
    # a normal chat/scheduler/warmup turn.
    helpdesk_mode: bool = False
    # Cost-ledger use-case tag for this turn (chat, chat_summary, scheduled,
    # translate, ...). Read by brain._log_call_cost as the default `purpose` when
    # the caller doesn't pass one explicitly, so cost_log rows are attributed to
    # a use-case for the per-use-case cost breakdown. Set by each caller inside
    # its own `with request_context()`; '' → "unknown (legacy)" in the breakdown.
    cost_purpose: str = ""
    # True while a detached background task is executing (engine/background_tasks
    # ._run). run_background_task reads it to refuse nested spawns (no fan-out
    # regress). Never set on a normal chat/scheduler/warmup turn.
    current_bg_task: bool = False
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


# The `_thread_local` compatibility shim (a proxy object whose attribute access
# funnelled to the active RequestContext) was removed in Phase 4 of the Tier-G
# refactor once every call site moved to `get_request_context()` /
# `request_context(...)`. There is no longer a `_thread_local` name — request
# state is reached ONLY through the accessor + the context-manager below.


# --- typed accessor + the one context-manager --------------------------------

def get_request_context() -> RequestContext:
    """The active RequestContext (creating + binding a fresh one if none)."""
    return _active_ctx()


def report_tool_progress(*, phase: str = "", pct: float | None = None,
                         note: str = "", current: int | None = None,
                         total: int | None = None) -> None:
    """Emit a LIVE progress update for the currently-dispatching tool call.

    Generic, reusable by ANY tool: while a (synchronous) tool runs, call this to
    push a `tool_progress` event into the session LiveStream so the chat view's
    live tool card shows a phase label + optional % bar — e.g. a PDF read going
    'pymupdf4llm Seite 12/37 (32%)' → 'Wechsle zu fitz' → 'OCR 5/37'. Works
    during the blocking dispatch because the event flows out on the SSE queue
    (a different thread drains it).

    All fields optional: `phase` (short label), `pct` (0–100; derived from
    current/total when not given), `note` (extra detail), `current`/`total`
    (e.g. page i of N). Best-effort + silent: no event_callback (mining /
    background / non-chat callers) → no-op; never raises into the tool.

    Auto-tagged with the dispatch thread's `tool_use_id` so the client targets
    the right card. Live-only — never persisted (the final result + any backend
    badge carry the durable record).
    """
    try:
        ctx = _active_ctx()
        cb = getattr(ctx, "event_callback", None)
        if cb is None:
            return
        if pct is None and total:
            try:
                pct = max(0.0, min(100.0, (float(current or 0) / float(total)) * 100.0))
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        cb("tool_progress", {
            "tool_use_id": getattr(ctx, "tool_use_id", None),
            "phase": phase,
            "pct": (round(pct, 1) if isinstance(pct, (int, float)) else None),
            "note": note,
            "current": current,
            "total": total,
        })
    except Exception:
        pass


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
      mcp_manager   – MCPManager | None
    """

    __slots__ = (
        "mode", "agent_id", "session_id", "user_id", "team_ids",
        "project", "mcp_manager",
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
        mcp_manager=None,
    ):
        self.mode = mode
        self.agent_id = agent_id
        self.session_id = session_id
        self.user_id = user_id
        self.team_ids = team_ids or []
        self.project = project
        self.mcp_manager = mcp_manager


def init_thread_context(ctx: ExecutionContext, agent_config=None) -> None:
    """Set request-context fields from an ExecutionContext, into the active
    RequestContext (via `get_request_context()`).

    Callers wrap this in `with request_context():` so teardown is automatic.
    agent_config: AgentConfig instance for the agent (optional; omit when the
    caller has already set current_agent before calling this).
    """
    rc = get_request_context()
    if agent_config is not None:
        rc.current_agent = agent_config
    rc.current_session_id = ctx.session_id
    rc.session_id = ctx.session_id
    rc.current_user_id = ctx.user_id
    rc.current_team_ids = ctx.team_ids
    rc.delegate_agent_id = ctx.agent_id
    if ctx.mcp_manager is not None:
        rc.mcp_manager = ctx.mcp_manager
    if ctx.project is not None:
        rc.project = ctx.project


def clear_thread_context() -> None:
    """Reset the core request-context fields to safe defaults.

    Mostly superseded by `request_context()`'s automatic token-reset teardown;
    retained for the diagnostic warmup-prefix script + any caller that resets
    in place rather than via the context-manager.
    """
    rc = get_request_context()
    rc.current_agent = None
    rc.delegate_agent_id = None
    rc.current_session_id = None
    rc.session_id = None
    rc.current_user_id = ""
    rc.current_team_ids = []
    rc.trace_id = None
