"""Low-level Server-Sent Events (SSE) wire formatting.

Generic, reusable plumbing for ANY SSE endpoint — no chat/LiveStream
knowledge, no brain/handlers imports (would create an import cycle). Takes
event data as plain params and returns the bytes to write to the socket.

The chat-specific streaming machinery (worker thread, event_callback that
persists thinking rows, LiveStream attach/replay semantics) lives in
handlers/chat.py and is NOT here — only the byte-level serialisation is.

Extracted from handlers/chat.py during the module-extraction refactor; folds
the 3 duplicated `f"event: ...\ndata: {json.dumps(...)}\n\n"` sites onto a
single formatter.
"""
from __future__ import annotations

import json

# 5-second keepalive comment line. SSE comments (lines starting with ':') are
# ignored by clients but keep the TCP connection from idling out. See
# handlers/CLAUDE.md — "SSE streams use 5s keepalive comments — don't remove."
KEEPALIVE: bytes = b": keepalive\n\n"


def format_sse(event_type: str, data) -> str:
    """Format one SSE event frame as a string.

    `data` is JSON-serialised. The frame is the canonical
    `event: <type>\\ndata: <json>\\n\\n` shape every Brain SSE endpoint uses.
    """
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def encode_sse(event_type: str, data) -> bytes:
    """Same as format_sse but UTF-8 encoded, ready to write to a socket."""
    return format_sse(event_type, data).encode("utf-8")
