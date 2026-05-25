# Helpdesk ("Brainy") HTTP handlers.
#
# Brainy is a friendly, read-only helpdesk bot reachable from the running chat.
# It answers questions about brain-agent itself (via the exclusive
# `brain-agent-guide` skill) and about the user's current session + activity.
#
# Routes (registered in server.py):
#   POST /v1/helpdesk          — ask Brainy a question; SSE stream of the reply
#   GET  /v1/helpdesk/history  — restore Brainy's conversation for a session
#   POST /v1/helpdesk/clear    — clear Brainy's conversation for a session
#   GET  /v1/helpdesk/config   — (admin) read Brainy config (model + prompt)
#   POST /v1/helpdesk/config   — (admin) save Brainy config
#
# The turn runs through sidecar_proxy.helpdesk_call (purpose='helpdesk'), fully
# independent of the main chat worker / live_stream — so Brainy works even while
# the main answer is still streaming.

from __future__ import annotations

import json
import os
import threading

import brain as engine
import server_lib.auth as _auth_mod
from server_lib.db import ChatDB
from server_lib.sse_stream import KEEPALIVE, encode_sse
from handlers import sidecar_proxy

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

_HELPDESK_DEFAULT_PROMPT = (
    "Du bist Brainy 🧠 — der freundliche, kompetente Helpdesk-Assistent von brain-agent. "
    "Hilf dem Nutzer auf Deutsch, kurz und konkret. Lade ZUERST den Skill `brain-agent-guide` "
    "mit use_skill, nutze helpdesk_session_info / helpdesk_user_context / helpdesk_user_activity "
    "für Kontext, und sag, WO in der Oberfläche etwas zu finden ist. Du bist rein lesend — erkläre "
    "Aktionen, statt sie auszuführen."
)

_MAX_HISTORY_TURNS = 20  # cap what we replay into the model


def _load_helpdesk_config() -> dict:
    """Read the `helpdesk` block fresh from config.json (always current)."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f).get("helpdesk") or {}
    except (OSError, json.JSONDecodeError):
        cfg = {}
    return {
        "enabled": cfg.get("enabled", True),
        "model": (cfg.get("model") or "").strip(),
        "max_rounds": int(cfg.get("max_rounds") or 6),
        "system_prompt": (cfg.get("system_prompt") or "").strip() or _HELPDESK_DEFAULT_PROMPT,
    }


def _resolve_helpdesk_model(cfg: dict) -> str:
    """Pick Brainy's model. Configured Brainy model if available, else "Auto":
    the server default (boot value → persisted config.default_model), else the
    best enabled chat model. Returns "" only when the install has no usable
    model at all."""
    mid = (cfg.get("model") or "").strip()
    if mid and engine._is_model_available(mid):
        return mid
    # "Auto". Try, in order: the boot-time server default; the persisted
    # config.json default_model (covers "set in UI but not yet restarted"); then
    # the highest-priority enabled model so Brainy always works.
    cand = (engine._background_model_default() or "").strip()
    if cand and engine._is_model_available(cand):
        return cand
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cand = (json.load(f).get("default_model") or "").strip()
        if cand and engine._is_model_available(cand):
            return cand
    except (OSError, json.JSONDecodeError):
        pass
    try:
        return engine._resolve_auto_model_tiered(None) or ""
    except Exception:
        return ""


def _context_label(ctx: dict) -> str:
    """A stable key for *where* a Brainy turn was asked, used for
    context-filtered replay. A project is the strongest signal (its name);
    otherwise the view type. Empty when unknown. NOT shown to the user and
    NOT sent to the model — purely a replay-filter key."""
    if not isinstance(ctx, dict):
        return ""
    if ctx.get("project"):
        return f"project:{ctx['project']}"
    view = (ctx.get("view") or "").strip()
    if view and view not in ("unknown", ""):
        return f"view:{view}"
    return ""


def _format_view_context(ctx: dict) -> str:
    """A short German note telling Brainy where the user currently is, prepended
    to the question (not stored). Empty when no useful context."""
    if not isinstance(ctx, dict):
        return ""
    label = (ctx.get("label") or "").strip()
    if not label:
        return ""
    bits = [f"Ansicht: {label}"]
    if ctx.get("project"):
        bits.append(f"Projekt: {ctx['project']}")
    if ctx.get("chat_title"):
        bits.append(f"Chat: {ctx['chat_title']}")
    return f"[Kontext — der Nutzer ist gerade hier: {'; '.join(bits)}]\n\n"


# Replay-window knobs. Keep the conversation cheap + on-topic without
# fragmenting storage: replay turns from the user's CURRENT context plus a
# few most-recent regardless of context (so an immediate follow-up after a
# context switch never loses its setup), capped to a tight tail.
_REPLAY_MAX_ROWS = 24       # hard cap on rows fed to the model (~12 exchanges)
_REPLAY_RECENT_KEEP = 4     # always keep this many newest rows, any context


def _select_replay_rows(history: list, current_label: str) -> list:
    """Context-filtered replay selection over flat oldest-first rows.

    Keep a row if it (a) matches the current context, (b) has no label
    (legacy / unknown → matches anything), or (c) is among the most-recent
    few regardless of context. Then keep only the last _REPLAY_MAX_ROWS.
    Storage is untouched — this only narrows what reaches the model, cutting
    both tokens and cross-context bleed."""
    n = len(history)
    recent_from = n - _REPLAY_RECENT_KEEP
    kept = []
    for i, row in enumerate(history):
        label = (row.get("context_label") or "").strip()
        if i >= recent_from or not label or not current_label or label == current_label:
            kept.append(row)
    return kept[-_REPLAY_MAX_ROWS:]


def _build_helpdesk_messages(history: list, new_question: str,
                             current_label: str = "") -> list:
    """Turn the stored Brainy rows + the new question into a clean
    user/assistant message list for the model.

    Two passes. First, context-filtered replay selection (_select_replay_rows)
    narrows history to the current context + a recent tail — cheaper and
    on-topic. Second, normalise to strict alternation: history can be
    malformed — a turn whose reply was empty leaves an unpaired `user` row,
    and a partial delete can leave an orphan `assistant` row. Replaying that
    verbatim yields consecutive same-role turns or a leading assistant turn,
    which providers reject with a 400 — that was why a *second* question to
    Brainy sometimes did nothing. So: drop empties, merge consecutive
    same-role content, drop a leading assistant turn. The new question is
    always the final user turn.
    """
    rows = _select_replay_rows(history, current_label)
    msgs = []
    for row in rows:
        role = row.get("role")
        content = (row.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + content   # merge same-role run
        else:
            msgs.append({"role": role, "content": content})
    while msgs and msgs[0]["role"] != "user":          # must start with user
        msgs.pop(0)
    # Append the new question, merging if history's tail was also a user turn.
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + new_question
    else:
        msgs.append({"role": "user", "content": new_question})
    return msgs


class HelpdeskHandlerMixin:

    # ── POST /v1/helpdesk — ask Brainy (SSE) ──────────────────────────────
    def _handle_helpdesk(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        message = (body.get("message") or "").strip()
        # session_id is OPTIONAL context (the chat the user has open, if any) —
        # NOT the history key. History is per-user.
        session_id = (body.get("session_id") or "").strip()
        view_ctx = body.get("view_context") or {}
        if not message:
            self._send_json({"error": "message required"}, 400)
            return

        cfg = _load_helpdesk_config()
        if not cfg["enabled"]:
            self._send_json({"error": "Brainy ist deaktiviert (siehe Einstellungen)."}, 403)
            return
        model = _resolve_helpdesk_model(cfg)
        if not model:
            self._send_json({"error": "Kein Model für Brainy konfiguriert."}, 503)
            return

        uid = user.get("id") or ""

        # Audit-only access path for admins (Brainy itself is user-private).
        try:
            _auth_mod.AuthDB.audit_write(
                user, "helpdesk.ask",
                target=(view_ctx.get("label") or view_ctx.get("view") or ""),
                details={"q": message[:200], "view": view_ctx.get("view", "")},
                ip=self.client_address[0] if self.client_address else "")
        except Exception:
            pass

        # Build the message list: the user's prior Brainy turns + new question.
        # A per-turn view-context note is prepended to the question (not stored)
        # so Brainy knows where the user currently is. Replay is context-filtered
        # by `label` (current context + recent tail) — see _build_helpdesk_messages.
        label = _context_label(view_ctx)
        history = ChatDB.load_helpdesk_history(uid, limit=_MAX_HISTORY_TURNS * 2) or []
        ctx_note = _format_view_context(view_ctx)
        messages = _build_helpdesk_messages(
            history, (ctx_note + message) if ctx_note else message, current_label=label)

        # Persist the question (the user's text only, without the context note)
        # immediately, so a disconnect mid-stream still records what was asked.
        # The context label is persisted with it → survives reload + restart and
        # drives both the badge and context-filtered replay.
        ChatDB.append_helpdesk_message(uid, "user", message, context_label=label)

        # ── Open the SSE stream ──
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            return

        acc_text = []
        client_gone = threading.Event()

        def emit(event_type: str, data: dict):
            try:
                self.wfile.write(encode_sse(event_type, data))
                self.wfile.flush()
            except (OSError, BrokenPipeError):
                client_gone.set()

        def event_callback(ev_type: str, data: dict):
            # Forward only the events Brainy's mini-chat renders.
            if ev_type == "text_delta":
                txt = data.get("text", "")
                if txt:
                    acc_text.append(txt)
                    emit("text_delta", {"text": txt})
            elif ev_type == "tool_call":
                # Surface a friendly "looking something up" hint.
                emit("tool_call", {"name": data.get("name", "")})
            elif ev_type == "error":
                emit("error", {"message": data.get("message", "Fehler")})

        try:
            result = sidecar_proxy.helpdesk_call(
                messages=messages,
                model=model,
                system_prompt=cfg["system_prompt"],
                session_id=session_id,
                user_id=uid,
                event_callback=event_callback,
                max_rounds=cfg["max_rounds"],
            )
        except Exception as e:  # never leak a 500 mid-stream
            emit("error", {"message": f"{type(e).__name__}: {e}"})
            result = {"reply": "".join(acc_text), "error": str(e)}

        final_text = (result.get("reply") or "".join(acc_text)).strip()
        err = result.get("error")

        if final_text:
            ChatDB.append_helpdesk_message(uid, "assistant", final_text, context_label=label)

        emit("done", {"reply": final_text, "error": err})

    # ── GET /v1/helpdesk/history — the user's own Brainy conversation ─────
    # Paginated, NEWEST-first. Query: before_id (cursor — older than this id),
    # limit (default 20). Returns {messages:[{id,role,content,ts}], has_more}
    # in chronological (oldest-first) order for display.
    def _handle_helpdesk_history(self):
        user = self._require_auth()
        if not user:
            return
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        try:
            before_id = int(qs.get("before_id", [""])[0] or 0) or None
        except (TypeError, ValueError):
            before_id = None
        try:
            limit = max(1, min(100, int(qs.get("limit", ["20"])[0] or 20)))
        except (TypeError, ValueError):
            limit = 20
        uid = user.get("id") or ""
        # Fetch limit+1 to detect whether older rows remain.
        rows = ChatDB.load_helpdesk_history_page(uid, before_id=before_id, limit=limit + 1) or []
        has_more = len(rows) > limit
        rows = rows[:limit]                 # newest-first
        rows = list(reversed(rows))         # → chronological for display
        self._send_json({
            "messages": [{
                "id": r.get("id"),
                "role": r.get("role"),
                "content": r.get("content"),
                "ts": r.get("created_at"),
                "context_label": r.get("context_label") or "",
            } for r in rows],
            "has_more": has_more,           # are there OLDER rows before this page?
        })

    # ── POST /v1/helpdesk/delete — remove rows or a time range ────────────
    # Body: {id} for a single row, {ids:[...]} for several (an exchange = the
    # question row + the answer row), or {start_ts, end_ts} for a group
    # (created_at in [start_ts, end_ts)). User-scoped — can't touch others'.
    def _handle_helpdesk_delete(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        uid = user.get("id") or ""
        # Accept a list of ids (delete a whole exchange) or a single id.
        ids = body.get("ids")
        if not ids and body.get("id"):
            ids = [body["id"]]
        if ids:
            deleted = sum(1 for i in ids if i and ChatDB.delete_helpdesk_message(uid, i))
            self._send_json({"deleted": deleted})
            return
        if body.get("start_ts") is not None and body.get("end_ts") is not None:
            n = ChatDB.delete_helpdesk_range(uid, body["start_ts"], body["end_ts"])
            self._send_json({"deleted": n})
            return
        self._send_json({"error": "id or start_ts+end_ts required"}, 400)

    # ── POST /v1/helpdesk/clear — wipe the user's own Brainy conversation ─
    def _handle_helpdesk_clear(self):
        user = self._require_auth()
        if not user:
            return
        ChatDB.clear_helpdesk_history(user.get("id") or "")
        self._send_json({"status": "cleared"})

    # ── GET /v1/helpdesk/config (admin) ───────────────────────────────────
    def _handle_helpdesk_config_get(self):
        cfg = _load_helpdesk_config()
        cfg["resolved_model"] = _resolve_helpdesk_model(cfg)
        self._send_json(cfg)

    # ── POST /v1/helpdesk/config (admin) ──────────────────────────────────
    def _handle_helpdesk_config_save(self):
        body = self._read_json()
        try:
            config = {}
            if os.path.exists(_CONFIG_PATH):
                with open(_CONFIG_PATH, encoding="utf-8") as f:
                    config = json.load(f)
            block = config.get("helpdesk") or {}
            if "enabled" in body:
                block["enabled"] = bool(body["enabled"])
            if "model" in body:
                block["model"] = (body.get("model") or "").strip()
            if "max_rounds" in body:
                try:
                    block["max_rounds"] = max(1, min(12, int(body["max_rounds"])))
                except (TypeError, ValueError):
                    pass
            if "system_prompt" in body:
                block["system_prompt"] = (body.get("system_prompt") or "").strip()
            config["helpdesk"] = block
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=1, ensure_ascii=False)
            self._send_json({"status": "saved", **_load_helpdesk_config()})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
