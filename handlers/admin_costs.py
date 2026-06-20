"""Cost and quota admin handlers.

Sub-mixin of AdminHandlerMixin (handlers/admin.py module-split refactor). Holds
ONLY this area's `_handle_*` methods (+ area-only private helpers).
AdminHandlerMixin inherits this class, so the combined BrainAgentHandler MRO is
unchanged.

Like admin.py, this module references `engine`, `brain`, `client`, `_db_conn`,
`sqlite3`, `subprocess`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals(). This module's name is in that function's
injection list. All other helpers (`_send_json`, `_read_json`,
`_parse_agent_from_path`, …) resolve via `self.` against the combined MRO.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import threading
import urllib.request
import urllib.error
import uuid
import datetime as _dt
from urllib.parse import unquote, urlencode


# Raw cost_log `purpose` → display use-case bucket. The cost ledger records the
# fine-grained purpose (chat_summary, translate_text_rewrite, ...); the breakdown
# groups them into the buckets the user reasons about. Anything not listed (incl.
# pre-migration rows with purpose='') falls into "Sonstige" / "unknown (legacy)".
_USE_CASE_LABELS = {
    "chat": "Chat",
    "interactive": "Chat",
    "citation_reround": "Chat (Zitat-Prüfung)",
    "refine": "Chat-Verfeinerung",
    "chat_summary": "Chat-Zusammenfassung",
    "next_prompt": "Titel / Vorschläge",
    "next_prompt_suggestion": "Titel / Vorschläge",
    "scheduled": "Geplante Aufgaben",
    "background_task": "Hintergrundaufgaben",
    "delegate_task": "Delegation",
    "ask_llm": "ask_llm-Tool",
    "auto_route_classify": "Auto-Routing-Klassifikation",
    "wiki_gate": "Wiki-Auto-Gate (merken?)",
    "soul_chat": "Soul-Editor",
    "workflow": "Workflow",
    "studio": "Studio",
    "deep_research": "Deep Research",
    "audio_overview": "Audio Overview (Podcast)",
    "read_aloud": "Vorlesen (TTS)",
    "ocr": "OCR",
    "translate_text": "Übersetzung",
    "translate_text_rewrite": "Übersetzung",
    "translate_document": "Übersetzung",
    "translate_document_rewrite": "Übersetzung",
    "lang_detect": "Spracherkennung",
    "helpdesk": "Helpdesk (Brainy)",
    "lcm_condense": "Kontext-Verdichtung",
    "lcm_condense_fb": "Kontext-Verdichtung",
    "lcm_summarize": "Kontext-Verdichtung",
    "lcm_summarize_fb": "Kontext-Verdichtung",
    "lcm_recall": "Kontext-Verdichtung",
    "lcm_recall_fb": "Kontext-Verdichtung",
    "auto_memory_extract": "Gedächtnis",
    "memory_extract": "Gedächtnis",
    "memory_classifier": "Gedächtnis",
    "user_profile": "Gedächtnis",
    "relationship_discovery": "Gedächtnis",
    "relationship_discovery_fb": "Gedächtnis",
    "kg_extract": "Knowledge Graph",
    "kg_extract_eval": "Knowledge Graph",
    "code_graph_summary": "Code-Graph",
    "transform": "Sonstige (untagged)",
}
_LEGACY_LABEL = "Unbekannt (Altdaten)"


def _use_case_for(purpose: str) -> str:
    """Map a raw purpose to its display bucket. Empty/unknown → legacy bucket."""
    p = (purpose or "").strip()
    if not p:
        return _LEGACY_LABEL
    if p in _USE_CASE_LABELS:
        return _USE_CASE_LABELS[p]
    # Prefix fallbacks so future fine-grained variants still bucket sensibly.
    if p.startswith("translate"):
        return "Übersetzung"
    if p.startswith("lcm_"):
        return "Kontext-Verdichtung"
    if p.startswith("kg_"):
        return "Knowledge Graph"
    if p.startswith("refine"):
        return "Chat-Verfeinerung"
    return "Sonstige"


def _cost_window_bounds(window: str):
    """Resolve a window key → (since_iso, until_iso, label). Either bound may be
    None (None since = all-time; None until = now). ISO strings are UTC, matching
    cost_log.created_at (which uses sqlite datetime('now') = UTC). Billing-period
    windows reuse the QuotaManager cycle config (single source of truth)."""
    now = _dt.datetime.now(_dt.timezone.utc)

    def iso(d):
        return d.strftime("%Y-%m-%d %H:%M:%S")

    if window == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return iso(start), None, "Heute"
    if window == "week":
        # ISO week: Monday 00:00 UTC of the current week.
        start = (now - _dt.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return iso(start), None, "Diese Woche"
    if window in ("7d", "30d", "180d", "365d"):
        n = int(window[:-1])
        return iso(now - _dt.timedelta(days=n)), None, f"Letzte {n} Tage"
    if window == "ytd":
        start = _dt.datetime(now.year, 1, 1, tzinfo=_dt.timezone.utc)
        return iso(start), None, "Seit Jahresbeginn"
    if window == "all":
        return None, None, "Gesamt"
    if window in ("cycle", "last_cycle"):
        qm = engine._quota_manager
        if qm is None:
            # No quota manager → fall back to calendar month.
            start = _dt.datetime(now.year, now.month, 1, tzinfo=_dt.timezone.utc)
            return iso(start), None, "Aktueller Zeitraum"
        c_start, c_end = qm.cycle_window(now=now)
        if window == "cycle":
            return iso(c_start), iso(c_end), "Aktueller Abrechnungszeitraum"
        # last_cycle: the period ending exactly at this cycle's start.
        prev_end = c_start
        p_start, _ = qm.cycle_window(now=c_start - _dt.timedelta(microseconds=1))
        return iso(p_start), iso(prev_end), "Letzter Abrechnungszeitraum"
    # Unknown window → default to last 30 days.
    return iso(now - _dt.timedelta(days=30)), None, "Letzte 30 Tage"


class AdminCostsHandlers:
    """Cost and quota admin handlers."""

    def _handle_costs(self):
        """GET /v1/costs?agent=X&hours=24&user_id=Y — cost stats."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        stats = engine._cost_tracker.get_stats(agent=agent, hours=hours, user_id=target_uid)
        self._send_json(stats)

    def _handle_costs_daily(self):
        """GET /v1/costs/daily?agent=X&days=7&user_id=Y — daily breakdown."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        days = int(params.get("days", "7"))
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return
        daily = engine._cost_tracker.get_daily(agent=agent, days=days, user_id=target_uid)
        self._send_json({"daily": daily, "days": days, "agent_filter": agent, "user_id": target_uid})

    def _handle_costs_breakdown(self):
        """GET /v1/costs/breakdown?window=<key>&agent=X&user_id=Y — per-use-case
        × per-model cost breakdown for a time window.

        window keys: today, week, 7d, 30d, 180d, 365d, ytd, all, cycle,
        last_cycle. Reuses the quota billing-cycle config for cycle/last_cycle.
        Use-cases are display buckets collapsed from the raw cost_log.purpose via
        _use_case_for; each bucket nests its per-model split. Auth mirrors
        /v1/costs: a user_id filter is owner/admin-gated."""
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        window = unquote(params.get("window", "30d")) or "30d"
        agent = unquote(params.get("agent", "")) or None
        target_uid = unquote(params.get("user_id", "")) or None
        if target_uid is not None:
            user = self._require_auth()
            if not user:
                return
            if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
                self._send_json({"error": "Insufficient permissions"}, 403)
                return

        since_iso, until_iso, label = _cost_window_bounds(window)
        rows = engine._cost_tracker.breakdown(
            since_iso=since_iso, until_iso=until_iso,
            user_id=target_uid, agent=agent)

        # Collapse (purpose, model) rows into use-case buckets, each with a
        # per-model split. Dict-of-dicts keeps the merge O(rows).
        buckets: dict[str, dict] = {}
        total_cost = 0.0
        total_calls = 0
        total_in = 0
        total_out = 0
        for r in rows:
            uc = _use_case_for(r.get("purpose", ""))
            model = r.get("model", "") or "(unbekannt)"
            cost = float(r.get("cost", 0.0) or 0.0)
            calls = int(r.get("calls", 0) or 0)
            t_in = int(r.get("tokens_in", 0) or 0)
            t_out = int(r.get("tokens_out", 0) or 0)
            total_cost += cost
            total_calls += calls
            total_in += t_in
            total_out += t_out
            b = buckets.setdefault(uc, {"use_case": uc, "cost": 0.0, "calls": 0,
                                        "tokens_in": 0, "tokens_out": 0, "_models": {}})
            b["cost"] += cost
            b["calls"] += calls
            b["tokens_in"] += t_in
            b["tokens_out"] += t_out
            m = b["_models"].setdefault(model, {"model": model, "cost": 0.0, "calls": 0,
                                                "tokens_in": 0, "tokens_out": 0})
            m["cost"] += cost
            m["calls"] += calls
            m["tokens_in"] += t_in
            m["tokens_out"] += t_out

        by_use_case = []
        for b in buckets.values():
            models = sorted(b.pop("_models").values(), key=lambda x: -x["cost"])
            for m in models:
                m["cost"] = round(m["cost"], 6)
            b["cost"] = round(b["cost"], 6)
            b["by_model"] = models
            by_use_case.append(b)
        by_use_case.sort(key=lambda x: -x["cost"])

        self._send_json({
            "window": window,
            "label": label,
            "since": since_iso,
            "until": until_iso,
            "total_cost": round(total_cost, 6),
            "total_calls": total_calls,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "agent_filter": agent,
            "user_id": target_uid,
            "by_use_case": by_use_case,
        })

    # --- Per-user cost quotas ---

    def _handle_quota_me(self):
        """GET /v1/quotas/me — current authenticated user's quota state."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            state = engine._quota_manager.get_user_state(user["id"])
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json(state)

    def _handle_quota_config_get(self):
        """GET /v1/quotas/config — admin-only. Full quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        self._send_json(engine._quota_manager.get_config())

    def _handle_quota_config_save(self):
        """POST /v1/quotas/config — admin-only. Update quotas config."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        body = self._read_json() or {}
        try:
            saved = engine._quota_manager.save_config(body)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        # Audit log so changes are traceable
        try:
            if engine._audit_log:
                engine._audit_log.log_action(
                    agent="main",
                    action_type="quota_config_save",
                    tool_name="quota",
                    args_summary=f"by={user.get('username','')} cycle={saved.get('billing_cycle')} enforce={saved.get('enforce_red')}",
                    result_status="ok",
                )
        except Exception:
            pass
        self._send_json(saved)

    def _handle_quota_admin_users(self):
        """GET /v1/quotas/admin/users — admin-only. State for every user."""
        user = self._require_role("admin")
        if not user:
            return
        if not engine._quota_manager:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        try:
            users = _auth_mod.AuthDB.list_users()
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        out = []
        cfg = engine._quota_manager.get_config()
        for u in users:
            try:
                st = engine._quota_manager.get_user_state(u["id"], cfg=cfg)
            except Exception:
                continue
            out.append({
                "user_id": u["id"],
                "username": u.get("username") or "",
                "display_name": u.get("display_name") or "",
                "role": u.get("role") or "user",
                "disabled": bool(u.get("disabled")),
                "level": st["level"],
                "daily": st["daily"],
                "cycle": st["cycle"],
                "has_override": st["has_override"],
            })
        self._send_json({"users": out, "config": cfg})

    def _handle_quota_admin_breakdown(self):
        """GET /v1/quotas/admin/breakdown?user_id=X&days=N — per-user
        per-model + per-day breakdown for the current cycle. Admin sees
        anyone; non-admin only their own user_id."""
        user = self._require_auth()
        if not user:
            return
        if not engine._quota_manager or not engine._cost_tracker:
            self._send_json({"error": "Quota manager not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        from urllib.parse import unquote
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        target_uid = unquote(params.get("user_id", "")) or user["id"]
        if target_uid != user["id"] and user.get("role") != "admin" and user["id"] != "__system__":
            self._send_json({"error": "Insufficient permissions"}, 403)
            return
        try:
            days = max(1, min(365, int(params.get("days", "30"))))
        except ValueError:
            days = 30
        cfg = engine._quota_manager.get_config()
        state = engine._quota_manager.get_user_state(target_uid, cfg=cfg)
        cycle_start_iso = state["cycle"]["starts_at"].replace("T", " ").split("+", 1)[0].split(".")[0]
        per_model = engine._cost_tracker.per_model_user_window(target_uid, cycle_start_iso)
        daily = engine._cost_tracker.get_daily(days=days, user_id=target_uid)
        self._send_json({
            "user_id": target_uid,
            "state": state,
            "per_model": per_model,
            "daily": daily,
            "days": days,
        })
