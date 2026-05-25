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
    """Configured model if available, else the server default. '' if none."""
    mid = (cfg.get("model") or "").strip()
    if mid and engine._is_model_available(mid):
        return mid
    return engine._background_model_default() or ""


class HelpdeskHandlerMixin:

    # ── POST /v1/helpdesk — ask Brainy (SSE) ──────────────────────────────
    def _handle_helpdesk(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        session_id = (body.get("session_id") or "").strip()
        message = (body.get("message") or "").strip()
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
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

        # Build the message list: prior Brainy turns + the new question.
        history = ChatDB.load_helpdesk_history(session_id, limit=_MAX_HISTORY_TURNS * 2) or []
        messages = []
        for row in history:
            role = row.get("role")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": row.get("content") or ""})
        messages.append({"role": "user", "content": message})

        # Persist the question immediately (so a disconnect mid-stream still
        # records what was asked).
        ChatDB.append_helpdesk_message(session_id, "user", message, user_id=uid)

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
            ChatDB.append_helpdesk_message(session_id, "assistant", final_text, user_id=uid)

        emit("done", {"reply": final_text, "error": err})

    # ── GET /v1/helpdesk/history ──────────────────────────────────────────
    def _handle_helpdesk_history(self):
        user = self._require_auth()
        if not user:
            return
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session_id = (qs.get("session_id", [""])[0] or "").strip()
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        rows = ChatDB.load_helpdesk_history(session_id) or []
        self._send_json({
            "session_id": session_id,
            "messages": [{"role": r.get("role"), "content": r.get("content")} for r in rows],
        })

    # ── POST /v1/helpdesk/clear ───────────────────────────────────────────
    def _handle_helpdesk_clear(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json()
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        ChatDB.clear_helpdesk_history(session_id)
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
