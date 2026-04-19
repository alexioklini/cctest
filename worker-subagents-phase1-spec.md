# Worker Subagents — Phase 1 Implementation Spec

**Scope:** Core wrapper + artifact passthrough. No summariser LLM pass yet.
**Target version:** v8.0.0-alpha
**Estimated effort:** ~2 days
**Grounding:** Based on actual `claude_cli.py` dispatch at line 15028

---

## What Phase 1 Ships

A wrapper around the existing `_execute_tool` dispatch that routes declared
heavy tools through a worker path. In Phase 1 the worker does *not* yet run
a summariser LLM pass — it just captures raw output as an artifact and
returns a deterministic JSON envelope with a pointer. This is deliberate:
Phase 1 proves the routing, locking, and artifact integration in isolation
from LLM behaviour. Phase 2 adds the summariser.

After Phase 1, `run_code` is migrated from its current bespoke output
filtering to the generic path. Observable behaviour must be byte-identical
for all other tools. No user-facing change except for `run_code`, whose
agent-facing result becomes cleaner and smaller.

---

## File-Level Changes

| File | Change | LOC |
|---|---|---|
| `execution.py` (new) | Router, worker, locks, config loader | ~280 |
| `claude_cli.py` | Rename `_execute_tool` body → `_execute_tool_inner`, add 10-line router as new `_execute_tool` | ~15 touched |
| `config.example.json` | New `execution` block | ~10 |
| `server.py` | Emit `worker.started`/`worker.finished` SSE events | ~20 |
| `agents/main/agent.json` | Optional `execution_overrides` block | ~5 |
| `tests/test_worker_phase1.py` (new) | Parity tests + idempotency tests | ~200 |

Net new code ~500 LOC; touched code in `claude_cli.py` ~15 LOC. The
existing function body moves verbatim into `_execute_tool_inner` with no
logic changes — this is an extract-rename refactor, not a rewrite.

---

## New Module: `execution.py`

Standalone module imported by `claude_cli.py`. No circular imports: the
module imports from `claude_cli` only lazily inside functions.

```python
"""
Worker Subagent Execution Module
================================

Runtime wrapper around tool dispatch. Routes heavy tools through a worker
path that writes raw output to the artifact store and returns a compact
envelope. Phase 1: no summariser LLM pass — envelope contains only
artifact references.

Integration: claude_cli._execute_tool() becomes a thin router that calls
route_tool_execution() in this module.
"""

from __future__ import annotations
import json
import os
import threading
import time
import uuid
from typing import Any, Callable

# ---------- Configuration ----------

# Hard-coded profile defaults. Overridden by config.json 'execution.profiles'.
# Tools not listed default to heavy="auto".
DEFAULT_PROFILES: dict[str, dict] = {
    # Web / network — typically heavy
    "exa_search":             {"heavy": True,  "timeout_seconds": 60},
    "web_fetch":              {"heavy": True,  "timeout_seconds": 60},
    "gmail_search":           {"heavy": True,  "timeout_seconds": 60},
    "gmail_inbox":            {"heavy": True,  "timeout_seconds": 60},
    "gmail_read":             {"heavy": True,  "timeout_seconds": 30},
    # Filesystem search — output size unpredictable
    "search_files":           {"heavy": True,  "timeout_seconds": 30},
    # Code execution — formalises the existing artifact pattern
    "python_exec":            {"heavy": True,  "timeout_seconds": 300},
    "execute_command":        {"heavy": True,  "timeout_seconds": 300},
    # Tools known small — explicitly light so 'auto' doesn't kick in
    "read_file":              {"heavy": False},
    "write_file":             {"heavy": False},
    "edit_file":              {"heavy": False},
    "list_directory":         {"heavy": False},
    "mempalace_query":        {"heavy": False},  # already summarised internally
    "save_chat_to_memory":    {"heavy": False},
    "schedule_list":          {"heavy": False},
    "schedule_history":       {"heavy": False},
    # Delegation is its own pattern — don't wrap
    "delegate_task":          {"heavy": False},
    "task_status":            {"heavy": False},
    "task_cancel":            {"heavy": False},
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
            "profiles": dict(DEFAULT_PROFILES),
        }
        try:
            from claude_cli import CONFIG_PATH  # lazy import
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
                # Merge profiles: user overrides win
                user_profiles = exec_block.get("profiles", {})
                cfg["profiles"].update(user_profiles)
        except Exception as e:
            # Never fail startup on config errors. Defaults win.
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
    # Agent-level override via thread-local (set by claude_cli on agent load)
    from claude_cli import _thread_local  # lazy import
    agent_overrides = getattr(_thread_local, 'execution_overrides', None) or {}
    if tool_name in agent_overrides:
        value = agent_overrides[tool_name]
        if isinstance(value, bool):
            return "heavy" if value else "light"
        if value in ("heavy", "light", "auto"):
            return value

    # Config + defaults
    cfg = _load_config()
    profile = cfg["profiles"].get(tool_name, {})
    heavy_field = profile.get("heavy", "auto")
    if isinstance(heavy_field, bool):
        return "heavy" if heavy_field else "light"
    return heavy_field if heavy_field in ("heavy", "light", "auto") else "auto"


# ---------- Worker Idempotency ----------

# Lock registry keyed by (session_id, tool_use_id).
# Event for coordination; dict slot for cached result.
_worker_events: dict[tuple[str, str], threading.Event] = {}
_worker_results: dict[tuple[str, str], str] = {}
_worker_registry_lock = threading.Lock()


def _acquire_worker_slot(key: tuple[str, str]) -> tuple[threading.Event, bool]:
    """Return (event, am_i_the_runner). The runner must call _release_worker_slot
    when done. Non-runners wait on the event."""
    with _worker_registry_lock:
        if key in _worker_events:
            return _worker_events[key], False
        ev = threading.Event()
        _worker_events[key] = ev
        return ev, True


def _release_worker_slot(key: tuple[str, str], result: str) -> None:
    with _worker_registry_lock:
        _worker_results[key] = result
        ev = _worker_events.get(key)
    if ev:
        ev.set()


# ---------- Artifact Storage ----------

def _store_worker_artifact(
    tool_name: str,
    args: dict,
    raw_result: str,
    session_id: str,
    tool_use_id: str,
) -> dict:
    """Write the raw tool result to the agent's artifact folder. Returns
    artifact metadata dict.

    Reuses the existing _get_artifact_session_folder logic so artifacts
    land in the same place users already browse them.
    """
    from claude_cli import (
        _get_artifact_session_folder, _register_artifact_version,
        AGENTS_DIR, _thread_local,
    )

    agent = getattr(_thread_local, 'current_agent', None)
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

    # Register in chats.db for admin dashboard + retention GC
    try:
        _register_artifact_version(artifact_path, "created", agent_id)
    except Exception:
        pass  # registration is best-effort

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
    No-op if no callback is registered (e.g. in unit tests)."""
    from claude_cli import _thread_local  # lazy
    cb = getattr(_thread_local, 'event_callback', None)
    if cb:
        try:
            cb(event_type, payload)
        except Exception:
            pass


# ---------- Worker Execution ----------

def run_worker_subagent(tool_name: str, args: dict, inner_fn: Callable[[str, dict], str]) -> str:
    """Execute a tool inside a worker. Phase 1: no summariser pass.

    Returns a JSON envelope:
        {
            "ok": true,
            "worker": true,
            "summary": "<phase-1 static message>",
            "artifacts": [{...}],
            "duration_seconds": 1.23
        }

    inner_fn is the function that actually runs the tool — in production
    this is claude_cli._execute_tool_inner, passed in to avoid circular import.
    """
    from claude_cli import _thread_local, _err, _ok  # lazy

    session_id = getattr(_thread_local, 'current_session_id', None) or ""
    tool_use_id = getattr(_thread_local, 'tool_use_id', None) or f"local_{uuid.uuid4().hex[:8]}"
    key = (session_id, tool_use_id)

    event, is_runner = _acquire_worker_slot(key)

    if not is_runner:
        # Another thread is already running this exact call. Wait and return
        # the cached result. Protects against SSE retries and provider retries.
        timeout = _load_config()["worker_timeout_seconds"]
        if not event.wait(timeout=timeout):
            return _err(f"worker_subagent: wait timeout after {timeout}s")
        return _worker_results.get(key, _err("worker_subagent: no result after wait"))

    # We are the runner.
    _emit_worker_event("worker.started", {
        "tool_call_id": tool_use_id,
        "tool_name": tool_name,
    })

    # Mark thread-local so nested _execute_tool calls go direct (no recursion)
    _thread_local.in_worker_subagent = True
    start = time.time()
    try:
        raw_result = inner_fn(tool_name, args)
    except Exception as e:
        raw_result = _err(f"{tool_name}: {e}")
    finally:
        _thread_local.in_worker_subagent = False
    duration = time.time() - start

    # Persist raw result as artifact
    artifact_meta = _store_worker_artifact(
        tool_name, args, raw_result, session_id, tool_use_id
    )

    envelope = _ok({
        "worker": True,
        "worker_phase": 1,
        "summary": (
            f"Tool '{tool_name}' completed in {duration:.1f}s. "
            f"Raw output ({artifact_meta['size_bytes']} bytes) "
            f"stored as artifact '{artifact_meta['artifact_id']}'. "
            f"Use get_artifact_detail to retrieve content. "
            f"(Phase 1 — summariser pass arrives in Phase 2.)"
        ),
        "artifacts": [artifact_meta],
        "duration_seconds": round(duration, 3),
    })

    _emit_worker_event("worker.finished", {
        "tool_call_id": tool_use_id,
        "tool_name": tool_name,
        "duration_seconds": round(duration, 3),
        "artifact_count": 1,
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

    from claude_cli import _thread_local, _ok  # lazy

    session_id = getattr(_thread_local, 'current_session_id', None) or ""
    tool_use_id = getattr(_thread_local, 'tool_use_id', None) or f"auto_{uuid.uuid4().hex[:8]}"

    artifact_meta = _store_worker_artifact(
        tool_name, args, result, session_id, tool_use_id
    )

    return _ok({
        "worker": True,
        "worker_phase": 1,
        "auto_isolated": True,
        "summary": (
            f"Tool '{tool_name}' output ({artifact_meta['size_bytes']} bytes) "
            f"exceeded auto-isolation threshold. Raw output stored as artifact "
            f"'{artifact_meta['artifact_id']}'. Use get_artifact_detail to retrieve."
        ),
        "artifacts": [artifact_meta],
    })


# ---------- The Router ----------

def route_tool_execution(
    tool_name: str,
    args: dict,
    inner_fn: Callable[[str, dict], str],
) -> str:
    """Phase 1 entry point. Decides direct vs worker execution.

    inner_fn must be claude_cli._execute_tool_inner — passed in explicitly
    to keep execution.py decoupled from the full claude_cli import graph.
    """
    # Feature flag
    cfg = _load_config()
    if not cfg["workers_enabled"]:
        return inner_fn(tool_name, args)

    # Avoid recursion: if we're already inside a worker, go direct
    from claude_cli import _thread_local  # lazy
    if getattr(_thread_local, 'in_worker_subagent', False):
        return inner_fn(tool_name, args)

    heaviness = _resolve_heaviness(tool_name, args)

    if heaviness == "light":
        return inner_fn(tool_name, args)

    if heaviness == "heavy":
        return run_worker_subagent(tool_name, args, inner_fn)

    # "auto": execute direct, then check size
    result = inner_fn(tool_name, args)
    return maybe_retroactive_isolate(tool_name, args, result)
```

---

## Changes to `claude_cli.py`

Exactly three edits. All mechanical.

### Edit 1 — rename existing `_execute_tool` body

Find:
```python
def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    # --- Built-in pre-hooks ---
    ...
    return result
```
at line 15028.

Rename to `_execute_tool_inner`:
```python
def _execute_tool_inner(name: str, args: dict) -> str:
    """Execute a tool directly — no worker routing. Called by the router
    and by workers themselves.
    (Previously: _execute_tool. Renamed in v8.0.0 for the worker subagent wrapper.)"""
    # --- Built-in pre-hooks ---
    ...
    return result
```

No other changes to the body.

### Edit 2 — add the new `_execute_tool` router

Immediately before the renamed `_execute_tool_inner`, add:

```python
def _execute_tool(name: str, args: dict) -> str:
    """Tool dispatch entry point. Routes through the worker subagent wrapper
    for heavy tools, direct execution for light tools.

    v8.0.0+: routing via execution.route_tool_execution().
    """
    from execution import route_tool_execution
    return route_tool_execution(name, args, _execute_tool_inner)
```

This keeps the public API identical — every existing caller of
`_execute_tool` continues to work unchanged. The renamed inner function
is only called by:

- The new router in `execution.py`
- The worker subagent when nested (via `_thread_local.in_worker_subagent`)

### Edit 3 — expose `CONFIG_PATH`

If not already at module level, ensure `CONFIG_PATH` is importable from
`claude_cli`. Search for where config is loaded (likely near startup);
hoist the filename to a module constant if needed:

```python
CONFIG_PATH = os.path.expanduser("~/.brain-agent/config.json")  # or current path
```

---

## Config Schema (`config.example.json`)

Add a new top-level block:

```json
{
  "providers": [...],
  "mempalace": {...},

  "execution": {
    "workers_enabled": true,
    "auto_threshold_bytes": 8192,
    "worker_timeout_seconds": 120,
    "max_concurrent_workers_per_session": 3,
    "profiles": {
      "exa_search":  {"heavy": true,  "timeout_seconds": 60},
      "web_fetch":   {"heavy": true,  "timeout_seconds": 60},
      "python_exec": {"heavy": true,  "timeout_seconds": 300}
    }
  }
}
```

Per-agent overrides in `agents/<agent>/agent.json` (optional — loaded into
`_thread_local.execution_overrides` by the agent switch logic; one-line
addition to the agent-load path in `claude_cli.py`):

```json
{
  "tool_groups": ["core", "memory", "web"],
  "execution_overrides": {
    "exa_search": true,
    "custom_cheap_tool": false
  }
}
```

---

## SSE Events in `server.py`

The existing SSE infrastructure already forwards events emitted via
`event_callback`. The worker emits two new event types that the web UI
and TUI need to recognise:

**`worker.started`** — payload `{tool_call_id, tool_name}`
**`worker.finished`** — payload `{tool_call_id, tool_name, duration_seconds, artifact_count}`

Web UI change (minimal): in the existing tool-call rendering, after a
`tool_call` event, if a subsequent `worker.started` arrives with matching
`tool_call_id`, replace the "running..." spinner with "working (Ns)" that
updates from timestamps. On `worker.finished`, collapse to the completed
state as usual. This is ~20 lines in the existing streaming handler.

Phase 1 can ship without UI changes — the events simply won't be rendered,
which is fine. Add UI in Phase 3.

---

## Test Plan

`tests/test_worker_phase1.py` — must pass before the migration of `run_code`.

### Smoke tests

1. **Light tool unchanged.** `_execute_tool("read_file", {...})` produces
   identical bytes as before the refactor. Compare against a golden file.
2. **Heavy tool produces envelope.** `_execute_tool("exa_search", {...})`
   returns JSON with `worker: true` and `artifacts: [...]`.
3. **Artifact persisted.** After a heavy call, the artifact file exists on
   disk at the expected path, contains the raw result, and is registered
   in `chats.db`.
4. **Auto threshold fires.** Mock a tool that returns 9KB output, declared
   `heavy: "auto"`. Verify retroactive isolation. Mock 7KB output on the
   same tool, verify direct passthrough.
5. **Feature flag off.** Set `workers_enabled: false`; verify every tool
   goes direct regardless of declaration.

### Idempotency tests

6. **SSE retry.** Two threads call `_execute_tool` concurrently with the
   same `session_id` and `tool_use_id`. The expensive tool runs exactly
   once; both threads receive the same envelope.
7. **Nested call.** Inside a heavy tool's execution, another tool is
   called. That nested call must run direct (not re-enter the worker).
   Verified by checking `_thread_local.in_worker_subagent`.

### Parity test — `run_code` migration

8. **Bit-identical outputs on representative scripts.** Record the current
   `run_code` output (with its bespoke filtering) for 20 representative
   scripts from the chat history. After migrating `run_code` to the
   worker path, the `summary` field must state the same outcome. The
   artifact must contain the same stdout/stderr/files.

This is the migration gate. Don't mark `run_code` as `heavy: true` in
production until test 8 passes green.

### Soft tests (manual)

9. **Cost accounting.** A heavy tool call's cost tracking still lands on
   the parent session. Verify via `/v1/session/:id/stats`.
10. **Audit log.** Both the raw tool execution and the worker envelope
    appear in the audit log with correct `duration_ms` attribution.

---

## Ship Checklist

Before tagging v8.0.0-alpha:

- [ ] `execution.py` exists, passes lint, no circular imports
- [ ] `_execute_tool_inner` rename done, all callers still pass tests
- [ ] Three edits to `claude_cli.py` applied
- [ ] Config schema updated, `config.example.json` committed
- [ ] `CONFIG_PATH` is a module-level constant
- [ ] Phase 1 test suite green (10/10)
- [ ] `run_code` parity test (test 8) green
- [ ] Manual smoke: chat with main agent, call `exa_search`, verify clean
      envelope reaches agent and artifact is browsable in web UI
- [ ] Manual smoke: toggle `workers_enabled: false`, verify full regression
      to prior behaviour
- [ ] Rollback plan documented: set `workers_enabled: false` in config;
      restart server. No data migration required — artifacts created by
      workers are just regular artifacts.

---

## What Phase 1 Does Not Do

Explicitly out of scope — these belong to later phases:

- No LLM summariser pass. `summary` is a static deterministic message.
- No `get_artifact_detail` tool. Agent must use existing `read_file` to
  retrieve artifact contents (inefficient but correct; Phase 2 fixes).
- No SSE progress mid-execution. Only start/finish events.
- No admin dashboard tab. `/v1/workers/recent` endpoint not yet added.
- No thick/loop mode. All workers are thin.
- No extractive or raw summariser modes. N/A until Phase 2.
- No concurrent-worker cap enforcement. `max_concurrent_workers_per_session`
  is in the config but not yet enforced. A stress test in Phase 3 will
  exercise this.

These exclusions are intentional: Phase 1 is about proving the routing
without introducing LLM-behaviour-dependent tests. The moment we add the
summariser, parity testing becomes "similar semantic meaning" rather
than "byte-identical", which is a different (and harder) testing contract.

---

## Phase 2 Preview (Context Only — Do Not Build Yet)

For the reviewer's orientation, Phase 2 adds:

- `_summarise_tool_result()` that calls a cheap provider/model
- `summary` field populated with real LLM-generated prose
- `sections` field populated with suggested retrieval targets
- `get_artifact_detail(artifact_id, query)` as a new tool
- Cost accounting for the summariser pass as a child span
- Per-agent `summariser_model` override

Phase 2 depends on Phase 1 being stable in production for at least a
week. The parity tests that gate Phase 1 are what make Phase 2 safe.
