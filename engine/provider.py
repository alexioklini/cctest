# Extracted from claude_cli.py — provider routing, concurrency queue, warmup

# Cross-module dependencies (not imported here — caller must ensure these are
# available in the namespace when this module is used in its original context):
#
#   CONFIG_PATH          — path to config.json (defined at module level in claude_cli.py)
#   _thread_local        — threading.local() instance (line 10327 in claude_cli.py)
#   _models_config       — dict of per-model config (line 18425 in claude_cli.py)
#   _mcp_manager         — global MCPManager instance (line 5066 in claude_cli.py)
#   _delegate_api_key    — str, set by server at startup (line 10506)
#   _delegate_base_url   — str, set by server at startup (line 10507)
#   MAX_DELEGATE_TOOL_ROUNDS — int constant (line 10380)
#   _MEMORY_TOOL_NAMES   — set of memory tool names (line 10383)
#   TOOL_DEFINITIONS_OPENAI — list of OpenAI-format tool defs (line 1433)
#   TOOL_GROUPS          — dict mapping group names to sets of tool names (line 1453)
#   TaskCancelled        — exception class used for queue cancellation
#   GDPRBlockedError     — exception class for GDPR hard-block
#   MemoryStore          — class for per-agent memory storage
#   AgentConfig          — class for per-agent configuration
#   CancelToken          — cancel/escape watcher type
#   make_headers         — function to build HTTP auth headers
#   get_api_model_id     — function to resolve wire model id
#   get_model_max_output — function to get max output tokens for a model
#   get_inference_params — function to get per-model inference params
#   resolve_model_settings — function to get merged model settings
#   _apply_inference_to_payload — function to apply thinking/inference params
#   _build_system_prompt — function to build the system prompt string
#   _get_agent_tool_names — function returning allowed tool names for current agent
#   _get_token_config    — function returning per-agent token config
#   _filter_tools        — function to filter tool list by allowed names
#   _filter_mcp_tools    — function to filter MCP tool list
#   _should_defer_mcp    — function deciding whether to defer MCP tools
#   _handle_openai_response — core streaming response handler
#   gdpr_pick_model_for_background — GDPR auto-fallback selector

import contextlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid


def _resolve_delegate_tools(tools: bool | str) -> list:
    """Resolve tool definitions for _run_delegate based on tools parameter."""
    if not tools:
        return []
    if tools == "memory_only":
        allowed = _MEMORY_TOOL_NAMES
    else:
        allowed = _get_agent_tool_names()
    return _filter_tools(TOOL_DEFINITIONS_OPENAI, allowed, is_openai=True)


def _run_delegate(messages: list[dict], model: str, system_prompt: str,
                  memory_store=None,
                  cancel_token=None,
                  event_callback=None,
                  inference_params: dict | None = None,
                  tools: bool | str = True,
                  session_id: str | None = None) -> str | None:
    """Run a delegated task in a fresh context. Returns the final text response.
    Thread-safe: uses thread-local storage for memory instead of swapping globals.

    tools: True=all tools, False=no tools, "memory_only"=only memory tools
    """
    # GDPR auto-fallback: scheduled tasks, agent-to-agent delegation, and
    # delegate_task tool calls run without user interaction. Reroute to the
    # local fallback when `messages` contain PII and the chosen model is
    # cloud. In hard-block mode with no local route available the delegate
    # is refused (returns the usual error-string shape callers already
    # recognise).
    try:
        _delegate_blobs = []
        if system_prompt:
            _delegate_blobs.append(system_prompt)
        for _m in (messages or []):
            _c = _m.get("content") if isinstance(_m, dict) else None
            if isinstance(_c, str):
                _delegate_blobs.append(_c)
            elif isinstance(_c, list):
                for _b in _c:
                    if isinstance(_b, dict) and _b.get("type") == "text":
                        _t = _b.get("text")
                        if isinstance(_t, str):
                            _delegate_blobs.append(_t)
        try:
            model = gdpr_pick_model_for_background(model, _delegate_blobs, purpose="delegate")
        except GDPRBlockedError as _ge:
            return f"Delegation error: {_ge}"
    except GDPRBlockedError:
        raise  # never swallow a block
    except Exception:
        pass

    _prov = resolve_provider_for_model(model)

    # Store memory in thread-local so tool_memory_* can find it
    if memory_store:
        _thread_local.memory_store = memory_store

    api_key = _prov["api_key"]
    base_url = _prov["base_url"]

    headers = make_headers(api_key)
    endpoint = f"{base_url}/chat/completions"
    aug_messages = [{"role": "system", "content": system_prompt}] + messages

    payload = {
        "model": get_api_model_id(model),
        "max_tokens": get_model_max_output(model),
        "messages": aug_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": _resolve_delegate_tools(tools),
    }
    provider = _models_config.get(model, {}).get("provider", "")
    _apply_inference_to_payload(payload, inference_params or {}, provider, scoped_model=model)

    try:
        _thread_local.max_tool_rounds = MAX_DELEGATE_TOOL_ROUNDS
        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
            _provider_name = _prov.get("provider_name", "") or "default"
            _agent_ctx = getattr(_thread_local, "current_agent", None)
            _agent_id_ctx = _agent_ctx.agent_id if _agent_ctx else None
            _user_id_ctx = getattr(_thread_local, "current_user_id", None)
            _session_id_ctx = getattr(_thread_local, "current_session_id", None)
            _queue_cm = _provider_queue.acquire_if(
                _provider_name, label="delegate",
                session_id=_session_id_ctx, agent_id=_agent_id_ctx,
                user_id=_user_id_ctx, model=model,
                event_callback=event_callback, cancel_token=cancel_token,
            )
            _queue_cm.__enter__()
            _released = [False]
            def _release():
                if _released[0]:
                    return
                _released[0] = True
                try:
                    _queue_cm.__exit__(None, None, None)
                except Exception:
                    pass
            try:
                with urllib.request.urlopen(request) as response:
                    return _handle_openai_response(
                        response, payload, messages, model, api_key, base_url,
                        True, True, headers, endpoint,
                        cancel_token, 0, event_callback,
                        session_id=session_id,
                        release_slot=_release)
            finally:
                _release()
        finally:
            _thread_local.max_tool_rounds = None
    except Exception as e:
        return f"Delegation error: {e}"


# Globals for delegation (set in _run_interactive)
_delegate_fallback_model: str | None = None
_delegate_api_key: str = ""
_delegate_base_url: str = ""

# Provider cache for resolve_provider_for_model
_provider_cache: dict[str, dict] = {}
_provider_cache_lock = threading.Lock()
_provider_cache_time: float = 0


def resolve_provider_for_model(model: str) -> dict:
    """Resolve provider credentials for a model. Returns {api_key, base_url, provider_name}.

    Single source of truth for model→provider resolution. Uses model config's provider
    field, falls back to delegate defaults. Thread-safe with 60s cache.
    """
    global _provider_cache_time

    # Check cache first
    now = time.time()
    with _provider_cache_lock:
        if model in _provider_cache and now - _provider_cache_time < 60:
            return _provider_cache[model].copy()

    # Default: delegate globals (set by server at startup)
    result = {
        "api_key": _delegate_api_key,
        "base_url": _delegate_base_url,
        "provider_name": "default",
    }

    # Look up model's configured provider
    model_cfg = _models_config.get(model, {})
    provider_name = model_cfg.get("provider", "")
    if provider_name:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(cfg_path) as f:
                prov = json.load(f).get("providers", {}).get(provider_name, {})
            if prov:
                result = {
                    "api_key": prov.get("api_key", ""),
                    "base_url": prov.get("base_url", ""),
                    "provider_name": provider_name,
                }
        except Exception:
            pass

    with _provider_cache_lock:
        _provider_cache[model] = result
        _provider_cache_time = now
    return result


def clear_provider_cache():
    """Clear the provider resolution cache (call after config changes)."""
    global _provider_cache_time
    with _provider_cache_lock:
        _provider_cache.clear()
        _provider_cache_time = 0


# --- Warmup Registry ---
#
# Per-model warmup state tracker. A background keeper daemon (in server.py)
# fires minimal prefill requests against local model endpoints so the first
# real user turn hits a warm KV cache and time-to-first-token is minimised.
# Each model's warmup is opt-in via models_config[id].warmup + warmup_ttl_seconds.
#
# State values: "idle" (never warmed), "warming", "warm", "failed", "skipped_cloud"

_warmup_state: dict[str, dict] = {}
_warmup_state_lock = threading.Lock()


def _is_local_base_url(base_url: str) -> bool:
    """Return True if base_url looks like a local gateway (localhost/LAN IP)."""
    if not base_url:
        return False
    u = base_url.lower()
    if "localhost" in u or "127.0.0.1" in u or "0.0.0.0" in u:
        return True
    # RFC1918 private IPv4 ranges
    import re as _re
    m = _re.search(r"//([^/:]+)", u)
    if not m:
        return False
    host = m.group(1)
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (ValueError, IndexError):
            pass
    return False


# --- Provider Concurrency Queue ---
#
# Local LLM gateways (oMLX, cliproxyapi) typically cannot process multiple
# /chat/completions in parallel even when multiple models are loaded. We
# serialize per-provider via a semaphore + strict-FIFO waitlist so concurrent
# chats/delegates/warmups line up instead of fighting for the same GPU.
#
# Config (config.json → providers.<name>.max_concurrent):
#   0         (default)  unlimited — no queue
#   >=1                  cap at N simultaneous calls
#
# Tickets carry (provider, label, session_id, agent_id, user_id, model,
# enqueued_at). While a ticket waits, the queue emits queue_wait events via
# event_callback so the UI can show "waiting in queue, position 3 of 5".

_PROVIDER_QUEUE_TIMEOUT_DEFAULT = 300  # seconds


class _ProviderTicket:
    __slots__ = ("id", "provider", "label", "session_id", "agent_id",
                 "user_id", "model", "enqueued_at", "acquired_at",
                 "released_at", "event_callback", "cancel_token",
                 "_released", "_position", "_admin_cancelled",
                 "_admin_cancel_reason")

    def __init__(self, provider, label, session_id, agent_id, user_id, model,
                 event_callback=None, cancel_token=None):
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.label = label
        self.session_id = session_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.model = model
        self.enqueued_at = time.time()
        self.acquired_at = 0.0
        self.released_at = 0.0
        self.event_callback = event_callback
        self.cancel_token = cancel_token
        self._released = False
        self._position = 0  # last-emitted position (1-based); 0 = none yet
        self._admin_cancelled = False
        self._admin_cancel_reason = ""


class _ProviderQueueSlot:
    """Per-provider serialization primitive + FIFO waitlist."""

    def __init__(self, provider: str, max_concurrent: int):
        self.provider = provider
        self.max_concurrent = max(1, int(max_concurrent))
        self._sem = threading.Semaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._waiters: list[_ProviderTicket] = []  # FIFO
        self._active: list[_ProviderTicket] = []

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "provider": self.provider,
                "max_concurrent": self.max_concurrent,
                "active_count": len(self._active),
                "waiting_count": len(self._waiters),
                "active": [
                    {
                        "id": t.id,
                        "label": t.label,
                        "session_id": t.session_id,
                        "agent_id": t.agent_id,
                        "user_id": t.user_id,
                        "model": t.model,
                        "age_ms": int((now - (t.acquired_at or t.enqueued_at)) * 1000),
                    }
                    for t in self._active
                ],
                "waiting": [
                    {
                        "id": t.id,
                        "label": t.label,
                        "session_id": t.session_id,
                        "agent_id": t.agent_id,
                        "user_id": t.user_id,
                        "model": t.model,
                        "age_ms": int((now - t.enqueued_at) * 1000),
                        "position": i + 1,
                    }
                    for i, t in enumerate(self._waiters)
                ],
            }


class LocalProviderQueue:
    """Per-provider concurrency gate + FIFO waitlist.

    Providers opt in via `max_concurrent` in their config.json entry:
      - 0 / missing:  no queue (unlimited), acquire_if is a no-op
      - >= 1:          semaphore cap; extras wait FIFO

    Default is 0 — cloud providers stay unlimited unless explicitly capped.
    Thread-safe; supports cancel_token for clean cancellation while waiting.
    """

    def __init__(self):
        self._slots: dict[str, _ProviderQueueSlot] = {}
        self._lock = threading.Lock()

    def _resolve_max_concurrent(self, provider_name: str) -> int:
        if not provider_name or provider_name == "default":
            return 0
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(cfg_path) as f:
                prov = json.load(f).get("providers", {}).get(provider_name, {})
            return max(0, int(prov.get("max_concurrent", 0) or 0))
        except Exception:
            return 0

    def _get_or_create_slot(self, provider_name: str) -> _ProviderQueueSlot | None:
        max_c = self._resolve_max_concurrent(provider_name)
        if max_c <= 0:
            return None
        with self._lock:
            slot = self._slots.get(provider_name)
            if slot is None or slot.max_concurrent != max_c:
                slot = _ProviderQueueSlot(provider_name, max_c)
                self._slots[provider_name] = slot
            return slot

    def snapshot_all(self) -> dict:
        with self._lock:
            slots = list(self._slots.values())
        # snapshot each slot without holding the outer lock
        return {
            "providers": {s.provider: s.snapshot() for s in slots},
        }

    def cancel_ticket(self, ticket_id: str, reason: str = "") -> dict:
        """Admin action: cancel a queued or running ticket.

        Waiting: marks the ticket cancelled so the waiting thread raises
          TaskCancelled on its next 200ms loop tick, ~instant.
        Running: fires the ticket's cancel_token (same as pressing the chat's
          stop button). The handler loop inside `_handle_openai_response`
          checks `escape_watcher.cancelled` on every SSE line, so the current
          HTTP call aborts at the next incoming byte. If the upstream is
          completely stalled (no bytes), the urllib socket read blocks — the
          cancel still takes effect as soon as the server sends any chunk,
          including keepalives.

        Returns a result dict describing what was cancelled.
        """
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            with slot._lock:
                for t in slot._waiters:
                    if t.id == ticket_id:
                        t._admin_cancelled = True
                        t._admin_cancel_reason = reason or "admin cancel"
                        return {
                            "ok": True, "state": "waiting",
                            "ticket_id": ticket_id, "provider": slot.provider,
                            "label": t.label, "session_id": t.session_id,
                        }
                for t in slot._active:
                    if t.id == ticket_id:
                        t._admin_cancelled = True
                        t._admin_cancel_reason = reason or "admin cancel"
                        # Fire the ticket's cancel_token if present — this is
                        # the same signal the per-chat Stop button uses.
                        ct = t.cancel_token
                        fired = False
                        if ct is not None:
                            try:
                                # EscapeWatcher / CancelToken both expose cancel()
                                if hasattr(ct, "cancel"):
                                    ct.cancel()
                                    fired = True
                                else:
                                    # Some watchers just expose .cancelled = True
                                    try:
                                        ct.cancelled = True
                                        fired = True
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        return {
                            "ok": True, "state": "running",
                            "ticket_id": ticket_id, "provider": slot.provider,
                            "label": t.label, "session_id": t.session_id,
                            "cancel_token_fired": fired,
                        }
        return {"ok": False, "error": "ticket not found", "ticket_id": ticket_id}

    def reset_config(self):
        """Drop all slots; next acquire rebuilds from fresh config."""
        with self._lock:
            # Never destroy a slot while it holds active tickets; just clear
            # so a future acquire rebuilds. Active tickets retain their ref.
            self._slots.clear()

    @contextlib.contextmanager
    def acquire_if(self, provider_name: str, *, label: str,
                   session_id: str | None = None,
                   agent_id: str | None = None,
                   user_id: str | None = None,
                   model: str | None = None,
                   event_callback=None,
                   cancel_token=None,
                   timeout: float | None = None):
        """Block until the provider's semaphore has a free slot.

        No-op if the provider isn't queued (max_concurrent <= 0).

        Scope: wrap *only* the HTTP call to /chat/completions. Release as soon
        as the LLM response completes — don't hold across tool execution or
        nested calls. The gateway's actual bottleneck is one HTTP call at a
        time; tool work happens locally and doesn't consume gateway capacity.
        This also avoids a deadlock where a worker subagent's nested
        send_message (e.g. summariser) would block on the outer chat's slot.

        Emits queue_wait / queue_acquired / queue_released events via
        event_callback. Raises TimeoutError on timeout, TaskCancelled on cancel.
        """
        slot = self._get_or_create_slot(provider_name)
        if slot is None:
            yield None
            return

        ticket = _ProviderTicket(
            provider=provider_name, label=label, session_id=session_id,
            agent_id=agent_id, user_id=user_id, model=model,
            event_callback=event_callback, cancel_token=cancel_token,
        )
        to = float(timeout) if timeout is not None else _PROVIDER_QUEUE_TIMEOUT_DEFAULT

        # Enqueue + emit initial position
        with slot._lock:
            slot._waiters.append(ticket)
            ticket._position = len(slot._waiters)
            waiting_count = len(slot._waiters)
            active_count = len(slot._active)
        self._emit(ticket, "queue_wait", slot, ticket._position,
                   waiting_count, active_count)

        deadline = time.time() + to
        acquired = False
        try:
            # Wait for our turn: we must be the head of the waiter list AND
            # the semaphore must have a free slot.
            while True:
                if cancel_token and getattr(cancel_token, "cancelled", False):
                    raise TaskCancelled()
                if ticket._admin_cancelled:
                    raise TaskCancelled(
                        f"Queue cancel by admin: {ticket._admin_cancel_reason or 'no reason given'}"
                    )
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Provider '{provider_name}' queue timeout after {to}s "
                        f"(label={label}, position={ticket._position})"
                    )
                # Only the FIFO head is allowed to try acquiring the semaphore;
                # others wait for the head to be popped. Poll every 200ms so we
                # can respond to cancels and re-emit position when it changes.
                with slot._lock:
                    is_head = slot._waiters and slot._waiters[0].id == ticket.id
                    cur_position = 0
                    for i, t in enumerate(slot._waiters):
                        if t.id == ticket.id:
                            cur_position = i + 1
                            break
                if cur_position != ticket._position:
                    ticket._position = cur_position
                    self._emit(ticket, "queue_wait", slot, cur_position,
                               len(slot._waiters), len(slot._active))
                if is_head:
                    got = slot._sem.acquire(timeout=min(0.2, remaining))
                    if got:
                        acquired = True
                        with slot._lock:
                            try:
                                slot._waiters.remove(ticket)
                            except ValueError:
                                pass
                            ticket.acquired_at = time.time()
                            slot._active.append(ticket)
                        self._emit(ticket, "queue_acquired", slot, 0,
                                   len(slot._waiters), len(slot._active))
                        break
                else:
                    # Not the head — sleep briefly and re-check.
                    time.sleep(min(0.2, max(0.05, remaining)))
            yield ticket
        except BaseException:
            # On cancel/timeout/exception before acquisition, remove from waiters.
            if not acquired:
                with slot._lock:
                    try:
                        slot._waiters.remove(ticket)
                    except ValueError:
                        pass
            raise
        finally:
            if acquired and not ticket._released:
                ticket._released = True
                ticket.released_at = time.time()
                with slot._lock:
                    try:
                        slot._active.remove(ticket)
                    except ValueError:
                        pass
                    waiting_count = len(slot._waiters)
                    active_count = len(slot._active)
                slot._sem.release()
                self._emit(ticket, "queue_released", slot, 0,
                           waiting_count, active_count)

    def _emit(self, ticket: _ProviderTicket, event_type: str, slot,
              position: int, waiting_count: int, active_count: int):
        cb = ticket.event_callback
        if not cb:
            return
        try:
            cb(event_type, {
                "provider": ticket.provider,
                "label": ticket.label,
                "model": ticket.model,
                "session_id": ticket.session_id,
                "agent_id": ticket.agent_id,
                "position": position,
                "waiting": waiting_count,
                "active": active_count,
                "max_concurrent": slot.max_concurrent,
                "ticket_id": ticket.id,
                "wait_ms": int((time.time() - ticket.enqueued_at) * 1000),
            })
        except Exception:
            pass


_provider_queue = LocalProviderQueue()


def get_provider_queue() -> LocalProviderQueue:
    return _provider_queue


def get_warmup_state(model: str) -> dict:
    """Get a copy of the current warmup state for a model."""
    with _warmup_state_lock:
        return dict(_warmup_state.get(model, {
            "state": "idle",
            "last_warmup_ts": 0,
            "last_used_ts": 0,
            "last_error": "",
            "next_due_ts": 0,
        }))


def set_warmup_state(model: str, **fields):
    """Update warmup state fields for a model."""
    with _warmup_state_lock:
        cur = _warmup_state.setdefault(model, {
            "state": "idle",
            "last_warmup_ts": 0,
            "last_used_ts": 0,
            "last_error": "",
            "next_due_ts": 0,
        })
        cur.update(fields)


def mark_model_used(model: str):
    """Call when a real request hits a model.

    A real turn keeps the model loaded in GPU memory and leaves a valid KV
    prefix cached — effectively, using the model is itself the best warmup.
    So we bump both last_used_ts and (if currently cold/idle) flip the state
    to warm so the UI reflects reality without triggering a redundant prime.
    """
    now = time.time()
    with _warmup_state_lock:
        cur = _warmup_state.setdefault(model, {
            "state": "idle",
            "last_warmup_ts": 0,
            "last_used_ts": 0,
            "last_error": "",
            "next_due_ts": 0,
        })
        cur["last_used_ts"] = now
        if cur.get("state") in ("idle", "cold", "failed"):
            cur["state"] = "warm"
            cur["last_warmup_ts"] = now
            cur["last_error"] = ""


def all_warmup_states() -> dict:
    """Return snapshot of all tracked warmup states."""
    with _warmup_state_lock:
        return {k: dict(v) for k, v in _warmup_state.items()}


# Inflight tracker for thinking-mode re-primes — prevents stacking concurrent
# warmups against the same model when a chat toggles thinking rapidly.
_reprime_inflight: set = set()
_reprime_inflight_lock = threading.Lock()


def maybe_reprime_for_thinking(model: str, thinking: bool, agent_id: str = "main") -> bool:
    """Trigger a background re-prime if the requested thinking mode differs
    from what's currently primed. No-op when:
      - the model has no warmup configured
      - the model has thinking_format=none (toggle is a no-op anyway)
      - a re-prime for this model is already in flight
      - the cached state already matches the requested mode

    Returns True if a re-prime thread was kicked off.
    """
    cfg = _models_config.get(model, {}) or {}
    if not cfg.get("warmup"):
        return False
    if cfg.get("thinking_format", "none") == "none":
        return False
    state = get_warmup_state(model)
    # If we've never primed yet, the first real turn does the work — skip.
    if state.get("state") not in ("warm", "warming"):
        return False
    if bool(state.get("thinking_primed")) == bool(thinking):
        return False
    with _reprime_inflight_lock:
        if model in _reprime_inflight:
            return False
        _reprime_inflight.add(model)

    def _bg():
        try:
            allow_cloud = bool(cfg.get("warmup_allow_cloud"))
            mode = (cfg.get("warmup_mode") or "full").lower()
            run_model_warmup(model, allow_cloud=allow_cloud,
                             agent_id=agent_id, mode=mode, thinking=thinking)
        finally:
            with _reprime_inflight_lock:
                _reprime_inflight.discard(model)

    threading.Thread(target=_bg, daemon=True,
                     name=f"reprime-{model[:20]}-think{int(thinking)}").start()
    return True


def run_model_warmup(model: str, allow_cloud: bool = False,
                     agent_id: str = "main", timeout: int = 30,
                     mode: str = "full", thinking: bool = False) -> dict:
    """Fire a single prefill request against a model's provider.

    Returns a result dict: {ok, state, duration_ms, error, mode}. Updates
    _warmup_state.

    `mode` controls the payload shape:
      - "full":    system prompt + tools + "." user. Primes the KV cache so
                   the user's first real turn reuses the prefix. Best when
                   exactly one warmup model is active (its KV stays resident).
      - "minimal": 1-token user ("."), no system, no tools. Only loads the
                   model's weights into GPU memory — no KV prefix primed.
                   Used when multiple warmup models are active, because they'd
                   evict each other's KV cache anyway, so paying the prefill
                   cost is wasted work.

    `thinking` (oMLX thinking-capable models only): when True, the warmup
    payload sets chat_template_kwargs.enable_thinking=true so the primed KV
    prefix matches a chat that has thinking enabled. Without this match, the
    first thinking-on turn cache-misses and pays full prefill cost.
    """
    t0 = time.time()
    prov = resolve_provider_for_model(model)
    base_url = prov.get("base_url", "")
    api_key = prov.get("api_key", "")

    if not base_url:
        err = "no base_url"
        set_warmup_state(model, state="failed", last_error=err)
        return {"ok": False, "state": "failed", "error": err, "duration_ms": 0, "mode": mode}

    if not allow_cloud and not _is_local_base_url(base_url):
        set_warmup_state(model, state="skipped_cloud",
                         last_error="cloud provider (warmup.allow_cloud=false)")
        return {"ok": False, "state": "skipped_cloud",
                "error": "cloud skipped", "duration_ms": 0, "mode": mode}

    set_warmup_state(model, state="warming", last_error="", mode=mode,
                     thinking_primed=bool(thinking))

    try:
        agent_config = AgentConfig(agent_id)
        _thread_local.current_agent = agent_config
        _thread_local.memory_store = MemoryStore(agent_id, base_dir=agent_config.memory_dir)

        all_tools: list = []
        # Reuse the process-global MCP manager so warmup's tool list matches
        # the real chat payload. Without this, the KV prefix differs (MCP
        # tools are in the real request but not the warmup) and oMLX's
        # prompt cache misses on the first turn — defeating warmup entirely.
        mcp_mgr = _mcp_manager if mode == "full" else None
        _thread_local.mcp_manager = mcp_mgr

        if mode == "full":
            # Mirror send_message's first-turn payload byte-for-byte.
            system_prompt = _build_system_prompt(include_memory_summary=True)
            allowed = _get_agent_tool_names()
            all_tools = _filter_tools(TOOL_DEFINITIONS_OPENAI, allowed, is_openai=True)
            tcfg = _get_token_config()
            deferred_groups = set(tcfg.get("deferred_tool_groups") or [])
            if deferred_groups:
                deferred_tool_names = set()
                for dg in deferred_groups:
                    deferred_tool_names.update(TOOL_GROUPS.get(dg, set()))
                all_tools = [t for t in all_tools
                             if t["function"]["name"] not in deferred_tool_names]
            # Merge in MCP tools exactly like send_message does. Discovered
            # tools are empty on turn 0 (no tool_search has run yet), so any
            # deferred MCP tools stay deferred — matches real first-turn.
            if mcp_mgr:
                mcp_tools = mcp_mgr.get_tool_definitions_openai()
                mcp_tools = _filter_mcp_tools(mcp_tools, is_openai=True)
                defer_mcp = tcfg.get("defer_mcp_tools", "auto")
                should_defer = _should_defer_mcp(defer_mcp, mcp_tools, model, is_openai=True)
                if should_defer:
                    mcp_tools = []  # discovered_tools is empty on turn 0
                all_tools.extend(mcp_tools)
            all_tools.sort(key=lambda t: t.get("function", {}).get("name", ""))
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "."},
            ]
        else:  # "minimal"
            messages = [{"role": "user", "content": "."}]

        endpoint = f"{base_url}/chat/completions"
        # Match send_message: stream=True + stream_options so the tokenised
        # request shape is identical to what the real first turn sends.
        # max_tokens=8 is fine — the KV prefix covers system+tools+user and
        # doesn't depend on the output-length budget.
        payload = {
            "model": get_api_model_id(model),
            "max_tokens": 8,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if all_tools:
            payload["tools"] = all_tools
        model_cfg = resolve_model_settings(model)
        if all_tools and model_cfg.get("parallel_tool_calls", True):
            payload["parallel_tool_calls"] = True
        # KV-prefix stability: real chat requests now go through
        # _apply_inference_to_payload, which forces enable_thinking on/off for
        # oMLX thinking-capable models. The warmup payload must mirror that
        # exactly or the prefix won't match and prompt cache will miss.
        # When `thinking=True` we inject the same legacy flag the chat path
        # uses so the helper renders enable_thinking=true into the payload.
        provider_name = prov.get("provider_name", "")
        _inf = get_inference_params(model)
        if thinking:
            _inf = dict(_inf)
            _inf["thinking"] = True
            _inf["thinking_level"] = "high"
        _apply_inference_to_payload(payload, _inf, provider_name, scoped_model=model)
        headers = make_headers(api_key)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        _provider_name = prov.get("provider_name", "") or "default"
        with _provider_queue.acquire_if(
            _provider_name, label="warmup",
            session_id=None, agent_id=agent_id, user_id=None,
            model=model, event_callback=None, cancel_token=None,
            timeout=max(timeout * 2, 60),
        ):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
        dur_ms = int((time.time() - t0) * 1000)
        now = time.time()
        set_warmup_state(model, state="warm", last_warmup_ts=now,
                         last_error="", mode=mode,
                         thinking_primed=bool(thinking))
        return {"ok": True, "state": "warm", "duration_ms": dur_ms,
                "error": "", "mode": mode, "thinking": bool(thinking)}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        err = f"HTTP {e.code}: {body or e.reason}"
        set_warmup_state(model, state="failed", last_error=err,
                         last_warmup_ts=time.time(), mode=mode)
        return {"ok": False, "state": "failed", "error": err,
                "duration_ms": int((time.time() - t0) * 1000), "mode": mode}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        set_warmup_state(model, state="failed", last_error=err,
                         last_warmup_ts=time.time(), mode=mode)
        return {"ok": False, "state": "failed", "error": err,
                "duration_ms": int((time.time() - t0) * 1000), "mode": mode}
    finally:
        # Clear thread-local agent context so we don't leak into other threads
        for attr in ("current_agent", "mcp_manager", "memory_store"):
            if hasattr(_thread_local, attr):
                try:
                    delattr(_thread_local, attr)
                except AttributeError:
                    pass
