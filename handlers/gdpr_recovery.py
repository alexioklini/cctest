"""GDPR anonymisation-failure recovery — modal state machine.

When transparent anonymisation fails mid-turn, the chat worker blocks on a
per-session recovery slot while the user picks an action on the
"anonymisation failed" modal. This mirrors the AskUserQuestion blocking
pattern but fires PRE-loop (no sidecar dispatch involved).

Extracted from handlers/chat.py during the module-extraction refactor. The
names are re-exported from handlers/chat.py so existing callers and
tests/test_chat_worker_helpers.py keep resolving. The HTTP entrypoint
(`POST /v1/chat/gdpr-recovery` → ChatHandlerMixin._handle_chat_gdpr_recovery)
stays in chat.py and calls deliver_gdpr_recovery_choice here.

Pure module-level state — no brain/handlers imports.
"""
from __future__ import annotations

import threading

# Pending recovery slots — `{session_id: {"event": Event, "choice": str|None}}`.
# Keyed by session id because only one anonymisation can be in flight per
# session at a time (one turn = one mapping). Choice is "local_model" |
# "cancel"; default "cancel" on timeout (safe — never falls through to cloud).
_gdpr_recovery_pending: dict[str, dict] = {}
_gdpr_recovery_lock = threading.Lock()


def _gdpr_recovery_register(session_id: str) -> threading.Event:
    """Open a recovery slot. Returns the Event the worker waits on."""
    event = threading.Event()
    with _gdpr_recovery_lock:
        _gdpr_recovery_pending[session_id] = {"event": event, "choice": None}
    return event


def _gdpr_recovery_clear(session_id: str) -> None:
    with _gdpr_recovery_lock:
        _gdpr_recovery_pending.pop(session_id, None)


def deliver_gdpr_recovery_choice(session_id: str, choice: str) -> bool:
    """Called by POST /v1/chat/gdpr-recovery. Returns True if a worker was
    waiting on this session. `choice` is "local_model" or "cancel"."""
    if choice not in ("local_model", "cancel"):
        return False
    with _gdpr_recovery_lock:
        slot = _gdpr_recovery_pending.get(session_id)
        if not slot:
            return False
        slot["choice"] = choice
        slot["event"].set()
    return True
