# Addendum: Worker Subagent Interactive Control Model

**Extends:** `worker-subagents.md`
**Status:** Proposed
**Priority:** P1 — required for Phase 1 shipping
**Effort:** +2 days on top of base proposal (~8 days total for Phase 1-2)

---

## Why This Addendum Exists

The base worker-subagent proposal assumes a fire-and-forget model: tool
call routed to worker, worker runs, worker returns. This does not cover
three cases that are mandatory for a user-facing multi-agent system:

1. **Status queryability** — the calling agent must be able to inform the
   user at any moment what a running worker is doing, without waiting for
   worker completion.
2. **Intervention** — the user (via the calling agent) must be able to
   abort a worker, pause it, or inject additional information into its
   execution.
3. **Subagent questions** — a worker that gets stuck must have a path to
   ask for clarification rather than guessing or failing silently.

Without these, workers become opaque black boxes: the user types a
question, waits 45 seconds, gets a wrong answer, and has no insight or
control during execution. That is a regression from today's
direct-execution model, where tool progress is visible via the existing
streaming output.

This addendum specifies the control primitives, the UX for each, and how
they compose with the Phase 1-2 rollout.

---

## Design Principles (extend base proposal)

8. **Workers are interruptible by default.** Every worker must reach a
   safepoint — a place where it can check for abort, pause, or injected
   input — at least every N seconds (configurable, default 2s for thick
   mode, N/A for thin because they are atomic).
9. **Questions are explicit, rare, and routed.** A worker cannot silently
   block on user input. Asking requires an explicit `worker_ask_user`
   tool call that names the question. Answers route back via
   `worker_id`, not globally.
10. **Status is cheap and always available.** Calling `worker_status` must
    be O(1) and safe to call from any thread or session. No locks, no
    LLM, no side effects.
11. **Control operations are idempotent.** Aborting an already-aborted
    worker returns success. Pausing a paused worker is a no-op. This
    makes the main agent's control logic robust against retries.
12. **No invisible state.** Every control operation (abort, pause,
    resume, send, answer) is logged with `(session_id, worker_id, actor,
    timestamp, payload)`. The audit trail already exists; workers
    participate in it.

---

## Worker Lifecycle States

```text
        ┌───────────┐
        │  QUEUED   │  waiting for worker slot
        └─────┬─────┘
              │ slot available
              ▼
        ┌───────────┐
        │  RUNNING  │  actively executing; can reach safepoint
        └┬──┬──┬──┬─┘
         │  │  │  │
         │  │  │  └─────► WAITING_FOR_USER  (worker_ask_user was called)
         │  │  │                 │
         │  │  │                 │ user answers OR main agent answers
         │  │  │                 ▼
         │  │  │           RUNNING
         │  │  │
         │  │  └────────► PAUSED   (worker_pause)
         │  │                 │
         │  │                 │ worker_resume OR worker_send
         │  │                 ▼
         │  │           RUNNING
         │  │
         │  └───────────► ABORTED    (worker_abort) — terminal
         │
         ▼
        COMPLETED                 (normal finish) — terminal
        FAILED                    (unrecoverable error) — terminal
        TIMED_OUT                 (exceeded timeout_seconds) — terminal
```

Terminal states (ABORTED, COMPLETED, FAILED, TIMED_OUT) are permanent.
Artifacts produced up to the terminal transition are preserved. A summary
— possibly partial — is generated for every terminal state so the main
agent always has something to reason over.

### Safepoints

A safepoint is where the worker checks control flags. The location
depends on worker mode:

- **Thin worker**: entry (before tool call), after tool call, after
  summariser. Three safepoints. Abort between them is deterministic;
  abort *during* the tool call itself is best-effort (depends on the
  tool's cancellation support). See *Abort Semantics* below.
- **Thick worker**: before each loop iteration, before each LLM call,
  before each tool dispatch within the worker. Many safepoints. Abort
  and pause are effectively instant.

---

## Control Primitives

Five new tools added to the main agent's toolkit. All are light tools
(bounded output) and available by default unless an agent's
`execution_overrides.disable_worker_control: true` excludes them.

### `worker_status`

Query current state of one worker or all workers in the session. Never
blocks.

```json
{
  "name": "worker_status",
  "description": "Get current state of a running or completed worker subagent. Use this to inform the user what a background task is doing.",
  "input_schema": {
    "type": "object",
    "properties": {
      "worker_id": {
        "type": "string",
        "description": "Specific worker ID. Omit for all workers in this session."
      }
    }
  }
}
```

Returns:

```json
{
  "workers": [
    {
      "worker_id": "wkr_2026_04_18_a3b2",
      "tool": "exa_search",
      "state": "RUNNING",
      "started_at": "2026-04-18T15:42:01+02:00",
      "elapsed_seconds": 18,
      "phase": "fetching results page 3 of 5",
      "last_message": "retrieved 2100 bytes from api",
      "artifacts_so_far": 1,
      "has_pending_question": false,
      "estimated_completion_seconds": 25
    }
  ]
}
```

`phase` and `last_message` are set by the worker at each safepoint — the
summariser is not involved. `estimated_completion_seconds` is a rolling
average from past worker runs of the same tool (stored in a small SQLite
table, computed server-side).

### `worker_abort`

Request termination. Idempotent.

```json
{
  "name": "worker_abort",
  "input_schema": {
    "type": "object",
    "properties": {
      "worker_id": { "type": "string" },
      "reason": {
        "type": "string",
        "description": "Logged in audit trail; shown to user in UI"
      }
    },
    "required": ["worker_id"]
  }
}
```

Behaviour: sets the worker's `cancel_event`. The worker transitions to
ABORTED at its next safepoint. A partial summary is generated from any
artifacts produced so far, with `aborted: true` and `abort_reason` in
the return contract. The main agent receives the partial result and can
relay it or retry.

If the worker is in the middle of an uninterruptible operation (e.g., a
synchronous HTTP call that does not honour cancellation), abort waits up
to `worker_abort_grace_seconds` (default 5) and then forcibly terminates
the thread. This is a last-resort case and logged as
`abort_forced: true`.

### `worker_pause`

Stop at next safepoint without terminating. Only meaningful for thick
workers; calling on a thin worker returns an error
(`thin_workers_cannot_pause`).

```json
{
  "name": "worker_pause",
  "input_schema": {
    "type": "object",
    "properties": {
      "worker_id": { "type": "string" },
      "reason": { "type": "string" }
    },
    "required": ["worker_id"]
  }
}
```

State transitions RUNNING → PAUSED. The worker idles (does not consume
LLM tokens, does not execute tools) until `worker_resume` or
`worker_send` is called.

### `worker_resume`

Resume a paused worker without adding input.

```json
{
  "name": "worker_resume",
  "input_schema": {
    "type": "object",
    "properties": {
      "worker_id": { "type": "string" }
    },
    "required": ["worker_id"]
  }
}
```

### `worker_send`

Inject information into a running or paused worker. The worker reads
its input queue at the next safepoint; the injected message is added
to its context before the next LLM call (thick mode) or is ignored with
a warning (thin mode — there is no next LLM call).

```json
{
  "name": "worker_send",
  "description": "Send additional context or instructions to a running worker. Useful when the user provides a clarification mid-execution.",
  "input_schema": {
    "type": "object",
    "properties": {
      "worker_id": { "type": "string" },
      "message": { "type": "string" },
      "role": {
        "enum": ["user", "system"],
        "default": "user",
        "description": "user = additional info from end user; system = main-agent directive"
      }
    },
    "required": ["worker_id", "message"]
  }
}
```

If the worker is PAUSED, `worker_send` also resumes it (single-step:
resume + inject in one call). This is the common pattern: pause → ask
user → send answer (which resumes).

---

## Subagent Questions — The `worker_ask_user` Tool

The hard case. A thick worker discovers mid-execution that it needs
input to proceed. Rather than guessing, it calls:

```json
{
  "name": "worker_ask_user",
  "description": "Ask the user a question that cannot be decided from available context. The worker will pause until answered. Use sparingly — prefer making reasonable decisions autonomously.",
  "input_schema": {
    "type": "object",
    "properties": {
      "question": { "type": "string" },
      "options": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Optional multiple-choice list. If provided, user sees a quick-select UI."
      },
      "context_summary": {
        "type": "string",
        "description": "Brief context so the user understands why the question is being asked"
      },
      "timeout_seconds": {
        "type": "integer",
        "default": 300,
        "description": "If no answer arrives in this window, worker aborts"
      }
    },
    "required": ["question"]
  }
}
```

`worker_ask_user` is only available to thick workers. Thin workers that
try to call it receive an error and are expected to fail cleanly instead.

### Flow

```text
 thick worker                    main agent                   user
     │                                │                          │
     │ worker_ask_user(q, context)    │                          │
     ├───────────────────────────────►│                          │
     │ state: WAITING_FOR_USER        │                          │
     │ SSE event: worker.question     │                          │
     │                                │ receives question event  │
     │                                │ decides:                 │
     │                                │   (a) relay to user      │
     │                                │   (b) answer from context│
     │                                │   (c) abort worker       │
     │                                │                          │
     │                        (a) relay:                         │
     │                                ├──── chat msg ───────────►│
     │                                │                     user │
     │                                │◄────── reply ────────────┤
     │                                │                          │
     │                   worker_send(worker_id, user_reply)      │
     │◄───────────────────────────────┤                          │
     │ state: RUNNING                 │                          │
     │ continues with answer in ctx   │                          │
```

### Chat UX

When the main agent relays a worker question to the user, it appears in
the chat as a distinct message type:

```text
┌─────────────────────────────────────────────────────────────┐
│ 💬 main: I'm investigating your DORA Art. 30 question. The  │
│    research worker needs a clarification.                    │
├─────────────────────────────────────────────────────────────┤
│ 🔧 Worker #wkr_a3b2 (exa_deep_search)                       │
│                                                              │
│    Context: I found three EBA documents that could be       │
│    'the ITS on reporting' — a 2024 draft, a 2025 final,     │
│    and a 2026 amendment. They have different requirements.  │
│                                                              │
│    Question: Which version should I base the analysis on?   │
│                                                              │
│    ○ 2024 draft                                             │
│    ○ 2025 final                                             │
│    ● 2026 amendment  ← (recommend)                          │
│    ○ Compare all three                                      │
│                                                              │
│    [Answer]  [Let main agent decide]  [Abort worker]        │
└─────────────────────────────────────────────────────────────┘
```

Key UX decisions:
- Question messages are **visually distinct** from normal chat
  (indented, different background, worker origin badge).
- Options (if provided) render as a selectable list; free-form answer
  also available.
- **"Let main agent decide"** button delegates to the main agent, which
  either answers or calls `worker_abort`. This avoids forcing the user
  to answer questions they don't care about.
- **"Abort worker"** is always visible because a question may reveal
  that the worker is fundamentally off-track.
- If `timeout_seconds` elapses without an answer, the worker aborts
  with `abort_reason: "question_timeout"`.

### Main-Agent-Answers Path

The main agent has the full conversation context and may already know
the answer to the worker's question. When `worker.question` arrives,
the main agent evaluates:

1. Did the user already state a preference relevant to this question?
2. Is there session-level context (current project, user preferences,
   past answers) that answers this?
3. Is the question one the worker should have decided autonomously?

If any of these apply, the main agent calls
`worker_send(worker_id, answer, role: "system")` directly. No user
interruption. The question is still logged (so the user can see in the
trace that the worker asked, and that the main agent answered on their
behalf).

This is the preferred path when possible — it minimises user
interruptions. But it must be used conservatively: answering on the
user's behalf about regulatory content (DORA, audit) could push wrong
information into the worker's reasoning. Per-scope configuration
(reusing wiki's scope model, if that ships) can disable main-agent
auto-answering for sensitive workers.

### Multiple Concurrent Questions

A main agent with three active workers could receive three concurrent
questions. Each renders as its own card in the chat, with its own
worker_id. User answers each independently; order does not matter. The
main agent tracks pending questions in a small state dict and does not
conflate them.

Configuration cap: `config.json` `execution.max_pending_questions_per_session`
(default 3). If exceeded, new `worker_ask_user` calls fail with a
clear error ("too many pending questions, resolve existing ones
first"), and the worker can either retry later or abort.

---

## How the Main Agent Reports Status to the User

This is the flow for the specific requirement "der aufrufende agent muss
dem nutzer zu jeder zeit auskunft geben können":

```text
User: Was macht das denn gerade? [asked during worker execution]
main: [worker_status()]  ← zero-cost call, no LLM in worker
      [returns state of all workers in session]
      Currently one worker is running: it's an exa_search for
      "DORA Art. 30 reporting". It's been running for 24 seconds,
      currently fetching results page 3 of 5. Estimated another
      10-15 seconds until it has a summary ready.
```

`worker_status` is designed to be called mid-conversation without
disrupting anything. It's also callable by the user directly via a
`/workers` slash command in the TUI and Web UI (reads the same endpoint
the tool reads, no LLM round-trip needed).

For proactive status, the main agent can be prompted in `soul.md`:

> When a worker is running longer than 20 seconds, proactively tell
> the user what it is doing. Use worker_status to check before
> speculating.

Not a hardcoded behaviour — an agent-personality choice — but worth
documenting so users of the system know how to configure it.

---

## Server-Side State Machine

### WorkerRegistry

In-memory singleton in `server.py`, thread-safe:

```python
class WorkerRegistry:
    """
    Per-process registry of active and recently-completed workers.
    Persisted to SQLite for restart recovery (completed/terminal states
    only; running workers are killed on restart).
    """
    def register(worker: Worker) -> None: ...
    def get(worker_id: str) -> Optional[Worker]: ...
    def list_session(session_id: str) -> list[Worker]: ...
    def update_state(worker_id, new_state, phase=None, message=None): ...
    def cancel(worker_id, reason) -> bool: ...
    def pause(worker_id, reason) -> bool: ...
    def resume(worker_id) -> bool: ...
    def send(worker_id, message, role) -> bool: ...
    def ask_user(worker_id, question) -> str:
        """Blocks until answered; called from worker thread."""
    def answer(worker_id, answer) -> bool:
        """Non-blocking; called from main agent."""
```

Each `Worker` holds:

```python
@dataclass
class Worker:
    worker_id: str
    session_id: str
    parent_call_id: str       # the tool_call_id that spawned it
    agent_id: str
    tool_name: str
    mode: Literal["thin", "thick"]
    state: WorkerState
    started_at: datetime
    phase: str                # user-visible
    last_message: str         # user-visible
    
    cancel_event: threading.Event
    pause_event: threading.Event      # set = paused
    input_queue: queue.Queue          # worker_send payloads
    pending_question: Optional[WorkerQuestion]
    answer_event: threading.Event     # worker blocks on this
    answer_value: Optional[str]
    
    artifacts: list[ArtifactRef]
    thread: threading.Thread
    cost: CostAccumulator
```

### SSE Events (extends base proposal)

| Event | Payload | Purpose |
|---|---|---|
| `worker.started` | worker_id, tool, mode, estimated | Base proposal |
| `worker.progress` | worker_id, phase, elapsed, message | Base proposal |
| `worker.finished` | worker_id, state, artifacts, duration | Base proposal |
| `worker.paused` | worker_id, reason, actor | **New** |
| `worker.resumed` | worker_id, actor | **New** |
| `worker.aborted` | worker_id, reason, partial: bool | **New** |
| `worker.question` | worker_id, question, options, context | **New** |
| `worker.answered` | worker_id, answer, answered_by | **New** |

The web UI subscribes to all of these. The TUI subscribes to the same
stream and renders a worker-status panel.

---

## Implications for Phase 1-2 Rollout

The base proposal's Phase 1 (core wrapper) now must include:

- `WorkerRegistry` with thread-safe state machine
- `cancel_event`, `pause_event`, `input_queue`, `answer_event` plumbing
- `worker_status`, `worker_abort` control tools (minimum viable for
  interruptibility)
- SSE events: `worker.started`, `worker.progress`, `worker.aborted`,
  `worker.finished`

Phase 2 (summariser) adds:

- `worker_pause`, `worker_resume`, `worker_send` — meaningful once
  thick mode exists
- `worker_ask_user` tool + question/answer flow
- Web UI chat message type for worker questions
- Default soul.md guidance for proactive status reporting

Revised effort: Phase 1 grows from ~2 days to ~3 days. Phase 2 stays
~2 days but shifts emphasis from summariser to question/answer flow.
Total Phase 1-2: ~5 days (was ~4). Worth it — without these primitives
the feature is not shippable.

---

## Open Questions Specific to Interactive Control

1. **What happens if a worker's session is deleted while running?**
   Proposal: session delete triggers abort of all workers in that
   session with `abort_reason: "session_deleted"`. Artifacts are
   purged per the same cascade that purges MemPalace drawers
   (v7.5.0 behaviour).

2. **Can user questions be answered async via email/notifications?**
   E.g., worker asks a question, user is offline, question arrives
   via the notification system (email, webhook, Telegram — all
   already wired). User replies through the same channel, reply
   routes back via `worker_send`.
   Interesting for long-running thick workers; out of scope for
   Phase 1-2. Revisit after Phase 3.

3. **Can the main agent ask the user a question without spawning a
   worker first?** The `worker_ask_user` tool is currently
   worker-only. A parallel `ask_user` tool for the main agent would
   be symmetrical and generally useful. Out of scope for this
   proposal; could be a separate small feature.

4. **Question rate limiting.** A buggy thick worker could call
   `worker_ask_user` in a loop. Cap: `max_questions_per_worker`
   (default 5). Exceeded → worker force-aborts with
   `abort_reason: "question_spam"`.

5. **Should thin workers ever be upgrade-able to thick mid-flight?**
   If a thin worker's output is unexpectedly structured (e.g., the
   tool returned an ambiguity instead of a result), could it
   escalate to thick mode? No — keep thin/thick decision at
   dispatch time only. Escalation paths are confusing and rarely
   needed in practice. A tool that genuinely needs escalation
   should be declared thick from the start.

---

## Summary

The base worker-subagent proposal describes *what* runs in isolation.
This addendum describes *how the user and main agent stay in control*
during that isolation. Five new control tools, one new
question-asking tool, a formal state machine, five new SSE events,
and a chat UX pattern for worker questions. Roughly +1 day on
Phase 1 and +1 day on Phase 2.

Without this addendum, workers are fire-and-forget black boxes — a
step backward from today's visible tool execution. With it, workers
become a strictly better version of the current model: same
visibility, better control, smaller context footprint.
