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
    "transcribe": "Transkription (STT)",
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

        # API list price ("was würde es OHNE Flatrate kosten"): models flagged
        # `flat_plan` log $0 real cost but keep the provider list price in their
        # REGULAR cost_* fields. Computed at READ time from the summed tokens via
        # the normal rate lookup — no schema change, retroactive for all rows.
        def _list_cost(model: str, purpose: str, t_in: int, t_out: int, t_cr: int) -> float | None:
            # model_is_flat_plan, NOT the raw flat_plan field: since 9.283.0
            # flat status also comes from the coding_plan LINK (type != credit)
            # — the raw-field check silently showed list price $0 for glm/kimi
            # (found 2026-07-05 while answering a usage question).
            if not engine.model_is_flat_plan(model):
                return None          # not flat → list price == real cost
            from engine.quotas import _get_cost_rate, _unit_list_cost
            # Synthetic unit-billed rows (OCR pages / TTS chars / STT audio-seconds
            # in tokens_in) reconstruct from their per-MODEL unit rates, not tokens.
            _ul = _unit_list_cost(purpose or "", t_in, model)
            if _ul is not None:
                return _ul
            r = _get_cost_rate(model)
            return (t_in * r["input"] + t_out * r["output"]
                    + t_cr * r["cache_read"]) / 1e6

        # Prompt-cache savings: what the cache-HIT tokens would have cost at
        # the full input rate minus what they cost at the cache_read rate —
        # the "ohne Prompt-Caching wäre es teurer"-Dimension, distinct from
        # the flat-plan savings above.
        def _cache_savings(model: str, t_cr: int) -> float:
            if not t_cr:
                return 0.0
            from engine.quotas import _get_cost_rate
            r = _get_cost_rate(model)
            return max(0.0, t_cr * (r["input"] - r["cache_read"]) / 1e6)

        # Collapse (purpose, model) rows into use-case buckets, each with a
        # per-model split. Dict-of-dicts keeps the merge O(rows).
        buckets: dict[str, dict] = {}
        total_cost = 0.0
        total_cost_list = 0.0
        total_cache_savings = 0.0
        total_calls = 0
        total_in = 0
        total_out = 0
        total_cache_read = 0
        for r in rows:
            uc = _use_case_for(r.get("purpose", ""))
            model = r.get("model", "") or "(unbekannt)"
            cost = float(r.get("cost", 0.0) or 0.0)
            calls = int(r.get("calls", 0) or 0)
            t_in = int(r.get("tokens_in", 0) or 0)
            t_out = int(r.get("tokens_out", 0) or 0)
            # cache-HIT tokens (billed at the discounted cache_read rate) — surfaced
            # separately so the UI can show cache hit-rate + realized savings.
            t_cr = int(r.get("cache_read_tokens", 0) or 0)
            _lc = _list_cost(model, r.get("purpose", ""), t_in, t_out, t_cr)
            cost_list = cost if _lc is None else _lc
            c_save = _cache_savings(model, t_cr)
            total_cost += cost
            total_cost_list += cost_list
            total_cache_savings += c_save
            total_calls += calls
            total_in += t_in
            total_out += t_out
            total_cache_read += t_cr
            b = buckets.setdefault(uc, {"use_case": uc, "cost": 0.0, "cost_list": 0.0,
                                        "cache_savings": 0.0, "calls": 0,
                                        "tokens_in": 0, "tokens_out": 0,
                                        "cache_read_tokens": 0, "_models": {}})
            b["cost"] += cost
            b["cost_list"] += cost_list
            b["cache_savings"] += c_save
            b["calls"] += calls
            b["tokens_in"] += t_in
            b["tokens_out"] += t_out
            b["cache_read_tokens"] += t_cr
            m = b["_models"].setdefault(model, {"model": model, "cost": 0.0,
                                                "cost_list": 0.0, "cache_savings": 0.0,
                                                "calls": 0,
                                                "tokens_in": 0, "tokens_out": 0,
                                                "cache_read_tokens": 0})
            m["cost"] += cost
            m["cost_list"] += cost_list
            m["cache_savings"] += c_save
            m["calls"] += calls
            m["tokens_in"] += t_in
            m["tokens_out"] += t_out
            m["cache_read_tokens"] += t_cr

        by_use_case = []
        for b in buckets.values():
            models = sorted(b.pop("_models").values(), key=lambda x: -x["cost"])
            for m in models:
                m["cost"] = round(m["cost"], 6)
                m["cost_list"] = round(m["cost_list"], 6)
                m["cache_savings"] = round(m["cache_savings"], 6)
            b["cost"] = round(b["cost"], 6)
            b["cost_list"] = round(b["cost_list"], 6)
            b["cache_savings"] = round(b["cache_savings"], 6)
            b["by_model"] = models
            by_use_case.append(b)
        by_use_case.sort(key=lambda x: -x["cost"])

        self._send_json({
            "window": window,
            "label": label,
            "since": since_iso,
            "until": until_iso,
            "total_cost": round(total_cost, 6),
            "total_cost_list": round(total_cost_list, 6),
            "total_cache_savings": round(total_cache_savings, 6),
            "total_calls": total_calls,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "total_cache_read_tokens": total_cache_read,
            "agent_filter": agent,
            "user_id": target_uid,
            "by_use_case": by_use_case,
        })

    # --- Coding-plan usage estimator (flat-plan window quotas) ---

    @staticmethod
    def _plan_config_path():
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

    @staticmethod
    def _plan_models(plan: dict) -> list[str]:
        """Resolve a plan's model set: every model that BILLS against this plan —
        via its own `coding_plan` link or its provider's default (the linkage
        lives on the MODEL/PROVIDER, not the plan, so the dashboard only shows
        plans that are actually wired up).

        Goes through `engine.resolve_model_plan_id` — the same seam the billing
        decision (`model_is_flat_plan`) uses, so a model can never bill against
        one plan while being displayed under another."""
        pid = plan.get("id") or ""
        return [mid for mid in (getattr(engine, "_models_config", None) or {})
                if engine.resolve_model_plan_id(mid) == pid]

    @staticmethod
    def _utc_to_local_hm(iso_utc: str) -> str:
        """'YYYY-MM-DD HH:MM:SS' (UTC) → 'HH:MM' local — for reset-time display."""
        try:
            d = _dt.datetime.strptime(iso_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc)
            return d.astimezone().strftime("%H:%M")
        except Exception:
            return iso_utc

    def _plan_window_since(self, win: dict, plan: dict, models: list[str]) -> tuple[str, str | None]:
        """(since_iso_utc, resets_at|None) for a plan window.

        `session_5h` mirrors the vendors' real 5h quota: a FIXED window opens
        with the first request (after the previous window expired) and resets
        exactly 5h later — the window chain is reconstructed from the ledger's
        request timestamps. `weekly` steps fixed 7-day cycles from `anchor`
        (subscription date), `monthly` calendar months from `anchor`. The
        legacy rolling_* kinds stay supported. Every window start is CLIPPED
        to the plan's `since` (activation) so pre-plan traffic (e.g. the
        credit-era glm/kimi calls) never counts against the plan."""
        now = _dt.datetime.utcnow()
        kind = win.get("kind") or "session_5h"
        plan_since = (plan.get("since") or "1970-01-01 00:00:00")

        def _clip(iso: str) -> str:
            return max(iso, plan_since)

        if kind == "session_5h":
            ts = engine._cost_tracker.call_timestamps(models, plan_since)
            if not ts:
                return _clip(now.strftime("%Y-%m-%d %H:%M:%S")), None
            ws = _dt.datetime.strptime(ts[0], "%Y-%m-%d %H:%M:%S")
            for t in ts[1:]:
                td = _dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                if td >= ws + _dt.timedelta(hours=5):
                    ws = td
            if now >= ws + _dt.timedelta(hours=5):
                # Fenster abgelaufen, noch keine neue Anfrage → Quota leer.
                return now.strftime("%Y-%m-%d %H:%M:%S"), None, None
            reset_dt = ws + _dt.timedelta(hours=5)
            return (_clip(ws.strftime("%Y-%m-%d %H:%M:%S")),
                    self._utc_to_local_hm(reset_dt.strftime("%Y-%m-%d %H:%M:%S")) + " Uhr",
                    int((reset_dt - now).total_seconds()))
        if kind == "weekly":
            try:
                anchor = _dt.datetime.strptime(win.get("anchor") or plan_since[:10], "%Y-%m-%d")
            except ValueError:
                anchor = now - _dt.timedelta(days=7)
            start = anchor
            while start + _dt.timedelta(days=7) <= now:
                start += _dt.timedelta(days=7)
            return (_clip(start.strftime("%Y-%m-%d %H:%M:%S")),
                    (start + _dt.timedelta(days=7)).strftime("%Y-%m-%d"),
                    int((start + _dt.timedelta(days=7) - now).total_seconds()))
        if kind == "rolling_5h":
            return _clip((now - _dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")), None, None
        if kind == "rolling_7d":
            return _clip((now - _dt.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")), None, None
        if kind == "monthly":
            try:
                anchor = _dt.datetime.strptime(win.get("anchor") or "", "%Y-%m-%d")
            except ValueError:
                anchor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            start = anchor
            while True:
                ny, nm = (start.year + (start.month // 12), start.month % 12 + 1)
                try:
                    nxt = start.replace(year=ny, month=nm)
                except ValueError:
                    nxt = start.replace(year=ny, month=nm, day=1)
                if nxt > now:
                    return (_clip(start.strftime("%Y-%m-%d %H:%M:%S")), nxt.strftime("%Y-%m-%d"),
                            int((nxt - now).total_seconds()))
                start = nxt
        return _clip((now - _dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")), None, None

    _PLAN_WINDOW_LABELS = {"session_5h": "5h", "rolling_5h": "5h",
                           "weekly": "Woche", "rolling_7d": "Woche", "monthly": "Monat"}

    def _handle_plans_usage(self):
        """GET /v1/plans/usage — estimated coding-plan window usage, computed
        from our own cost_log (no vendor quota API exists). Weighted count:
        fresh_in/out/cached × the plan's `count` weights (Z.ai discounts cached
        prompt tokens ~0.67 in its plan counter)."""
        if not self._require_auth():
            return
        if not engine._cost_tracker:
            self._send_json({"error": "Cost tracking not initialized"}, 503)
            return
        try:
            with open(self._plan_config_path()) as f:
                plans = (json.load(f).get("coding_plans") or [])
        except Exception as e:
            self._send_json({"error": f"config read failed: {e}"}, 500)
            return
        out = []
        for plan in plans:
            models = self._plan_models(plan)
            if not models:
                continue          # nur Pläne zeigen, die real mit Modellen verknüpft sind
            ptype = (plan.get("type") or "flat")
            windows = []
            if ptype == "credit":
                # API-Guthaben (z. B. Kilo): verbraucht = SUMME der real
                # verrechneten Kosten der verknüpften Modelle seit der letzten
                # Aufladung (anchor); verfügbar = Kontingent − verbraucht.
                anchor = (plan.get("anchor") or "1970-01-01") + " 00:00:00"
                s = engine._cost_tracker.token_sums(models, anchor)
                balance = float(plan.get("balance_usd") or 0.0)
                spent = round(float(s["cost"]), 4)
                # Reichweite bei gleichem Tempo: Tagesburn seit Aufladung →
                # verbleibende Tage (None bei <6h Historie oder Burn 0).
                days_left = None
                try:
                    _a = _dt.datetime.strptime(anchor, "%Y-%m-%d %H:%M:%S")
                    _el_d = (_dt.datetime.utcnow() - _a).total_seconds() / 86400.0
                    if _el_d > 0.25 and spent > 0:
                        days_left = round((balance - spent) / (spent / _el_d), 1)
                except Exception:
                    pass
                windows.append({
                    "kind": "credit", "label": "Guthaben",
                    "balance_usd": balance,
                    "used_usd": spent,
                    "remaining_usd": round(balance - spent, 4),
                    "pct": round(100.0 * spent / balance, 1) if balance > 0 else None,
                    "days_left_est": days_left,
                    "calls": s["calls"],
                    "anchor": plan.get("anchor") or "",
                })
            else:
                cw = plan.get("count") or {}
                w_in = float(cw.get("fresh_in", 1.0) or 0.0)
                w_out = float(cw.get("out", 1.0) or 0.0)
                w_cr = float(cw.get("cached", 1.0) or 0.0)
                for win in (plan.get("windows") or []):
                    since, resets, resets_in = self._plan_window_since(win, plan, models)
                    s = engine._cost_tracker.token_sums(models, since)
                    used = (s["tokens_in"] * w_in + s["tokens_out"] * w_out
                            + s["cache_read_tokens"] * w_cr)
                    limit = float(win.get("limit_tokens") or 0)
                    pct = round(100.0 * used / limit, 1) if limit > 0 else None
                    # Hochrechnung: projizierter Stand am Fensterende bei
                    # gleichem Tempo (used / verstrichene Fensterzeit). Erst ab
                    # 10% verstrichener Zeit belastbar; rolling-Fenster sind
                    # per Definition "voll verstrichen" → Projektion = pct.
                    projected = None
                    _len_s = {"session_5h": 5 * 3600, "weekly": 7 * 86400}.get(win.get("kind"))
                    if win.get("kind") == "monthly" and resets_in is not None:
                        try:
                            _start = _dt.datetime.strptime(since, "%Y-%m-%d %H:%M:%S")
                            _len_s = int((_dt.datetime.utcnow() - _start).total_seconds()) + resets_in
                        except Exception:
                            _len_s = None
                    if pct is not None:
                        if _len_s and resets_in is not None:
                            _el = max(0.0, 1.0 - resets_in / _len_s)
                            projected = round(pct / _el, 1) if _el >= 0.10 else None
                        elif win.get("kind", "").startswith("rolling"):
                            projected = pct
                    windows.append({
                        "kind": win.get("kind"),
                        "label": self._PLAN_WINDOW_LABELS.get(win.get("kind"), win.get("kind")),
                        "limit_tokens": int(limit),
                        "used_est": int(used),
                        "pct": pct,
                        "projected_pct": projected,
                        "calls": s["calls"],
                        "resets_at": resets,
                        "resets_in_s": resets_in,
                        "anchor": win.get("anchor") or "",
                    })
            out.append({
                "id": plan.get("id"), "name": plan.get("name") or plan.get("id"),
                "type": ptype,
                "count": plan.get("count") or {},
                "balance_usd": plan.get("balance_usd"),
                "anchor": plan.get("anchor") or "",
                "price": plan.get("price") or "", "price_note": plan.get("price_note") or "",
                "url": plan.get("url") or "", "quota_note": plan.get("quota_note") or "",
                "models": models, "calibrated_at": plan.get("calibrated_at") or "",
                "windows": windows,
            })
        self._send_json({"plans": out})

    def _handle_plans_save(self):
        """POST /v1/plans/save {plan} — admin: create or update a coding-plan /
        billing-account object (upsert by id). Model linkage happens on the
        MODEL (models.<id>.coding_plan) in the Models grid, not here."""
        user = self._require_auth()
        if not user:
            return
        if user.get("role") != "admin" and user.get("id") != "__system__":
            self._send_json({"error": "admin only"}, 403)
            return
        body = self._read_json()
        plan = body.get("plan") or {}
        pid = (plan.get("id") or "").strip()
        if not pid or not (plan.get("name") or "").strip():
            self._send_json({"error": "id und name erforderlich"}, 400)
            return
        ptype = (plan.get("type") or "flat")
        if ptype not in ("flat", "credit"):
            self._send_json({"error": "type muss 'flat' oder 'credit' sein"}, 400)
            return
        # Whitelist der persistierten Felder (kein Blind-Merge von Client-JSON).
        clean = {"id": pid, "name": plan.get("name").strip(), "type": ptype,
                 "price": str(plan.get("price") or ""),
                 "quota_note": str(plan.get("quota_note") or ""),
                 "url": str(plan.get("url") or ""),
                 "calibrated_at": _dt.date.today().isoformat()}
        if ptype == "credit":
            try:
                clean["balance_usd"] = float(plan.get("balance_usd") or 0.0)
            except (TypeError, ValueError):
                self._send_json({"error": "balance_usd (Zahl) erforderlich"}, 400)
                return
            clean["anchor"] = str(plan.get("anchor") or _dt.date.today().isoformat())
        else:
            cw = plan.get("count") or {}
            clean["count"] = {"fresh_in": float(cw.get("fresh_in", 1.0) or 0),
                              "out": float(cw.get("out", 1.0) or 0),
                              "cached": float(cw.get("cached", 1.0) or 0)}
            wins = []
            for w in (plan.get("windows") or []):
                if w.get("kind") not in ("session_5h", "weekly", "rolling_5h", "rolling_7d", "monthly"):
                    continue
                try:
                    lim = int(float(w.get("limit_tokens") or 0))
                except (TypeError, ValueError):
                    continue
                if lim <= 0:
                    continue
                entry = {"kind": w["kind"], "limit_tokens": lim}
                if w["kind"] == "monthly":
                    entry["anchor"] = str(w.get("anchor") or _dt.date.today().replace(day=1).isoformat())
                elif w["kind"] == "weekly":
                    entry["anchor"] = str(w.get("anchor") or _dt.date.today().isoformat())
                wins.append(entry)
            if not wins:
                self._send_json({"error": "mindestens ein Fenster mit Limit erforderlich"}, 400)
                return
            clean["windows"] = wins
        cfg_path = self._plan_config_path()
        try:
            with open(cfg_path) as f:
                config = json.load(f)
            plans = config.get("coding_plans") or []
            # Aktivierungszeitpunkt (`since`, Vor-Plan-Verkehr-Clipping) beim
            # Upsert erhalten; Neuanlage = jetzt.
            _old = next((p for p in plans if p.get("id") == pid), None)
            clean["since"] = ((_old or {}).get("since")
                              or _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            plans = [p for p in plans if p.get("id") != pid] + [clean]
            config["coding_plans"] = plans
            with open(cfg_path, "w") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self._send_json({"ok": True, "plan": clean})
        except Exception as e:
            self._send_json({"error": f"Speichern fehlgeschlagen: {e}"}, 500)

    def _handle_plans_delete(self):
        """POST /v1/plans/delete {plan_id} — admin: remove a plan object AND
        detach it from every model AND provider that referenced it (a dangling
        provider default would silently keep billing models against a plan that
        no longer exists)."""
        user = self._require_auth()
        if not user:
            return
        if user.get("role") != "admin" and user.get("id") != "__system__":
            self._send_json({"error": "admin only"}, 403)
            return
        pid = (self._read_json().get("plan_id") or "").strip()
        if not pid:
            self._send_json({"error": "plan_id erforderlich"}, 400)
            return
        cfg_path = self._plan_config_path()
        try:
            with open(cfg_path) as f:
                config = json.load(f)
            config["coding_plans"] = [p for p in (config.get("coding_plans") or [])
                                      if p.get("id") != pid]
            detached = 0
            for mid, mc in (config.get("models") or {}).items():
                if (mc.get("coding_plan") or "") == pid:
                    mc.pop("coding_plan", None)
                    detached += 1
            detached_provs = 0
            for pname, pc in (config.get("providers") or {}).items():
                if isinstance(pc, dict) and (pc.get("coding_plan") or "") == pid:
                    pc.pop("coding_plan", None)
                    detached_provs += 1
            with open(cfg_path, "w") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            # In-Memory-Modelle nachziehen (Verknüpfung wirkt in der Abrechnung).
            # Provider brauchen das nicht: engine.get_provider_configs liest sie
            # mtime-gecacht von der Platte, die wir gerade geschrieben haben.
            for mid, mc in (getattr(engine, "_models_config", None) or {}).items():
                if (mc.get("coding_plan") or "") == pid:
                    mc.pop("coding_plan", None)
            self._send_json({"ok": True, "detached_models": detached,
                             "detached_providers": detached_provs})
        except Exception as e:
            self._send_json({"error": f"Löschen fehlgeschlagen: {e}"}, 500)

    def _handle_plans_calibrate(self):
        """POST /v1/plans/calibrate {plan_id, window_kind, dashboard_pct} —
        admin: refit a window's limit from the vendor dashboard's REAL percent
        (limit = current window usage / pct). Stamps calibrated_at."""
        user = self._require_auth()
        if not user:
            return
        if user.get("role") != "admin" and user.get("id") != "__system__":
            self._send_json({"error": "admin only"}, 403)
            return
        body = self._read_json()
        plan_id = (body.get("plan_id") or "").strip()
        wkind = (body.get("window_kind") or "").strip()
        # Credit-Aufladung: {plan_id, balance_usd[, anchor]} setzt Kontingent +
        # Aufladedatum neu (statt Prozent-Kalibrierung).
        if body.get("balance_usd") is not None:
            try:
                bal = float(body.get("balance_usd"))
            except (TypeError, ValueError):
                self._send_json({"error": "balance_usd (Zahl) erforderlich"}, 400)
                return
            cfg_path = self._plan_config_path()
            try:
                with open(cfg_path) as f:
                    config = json.load(f)
                plan = next((p for p in (config.get("coding_plans") or [])
                             if p.get("id") == plan_id), None)
                if not plan or (plan.get("type") or "flat") != "credit":
                    self._send_json({"error": f"Credit-Plan nicht gefunden: {plan_id}"}, 404)
                    return
                plan["balance_usd"] = bal
                plan["anchor"] = str(body.get("anchor") or _dt.date.today().isoformat())
                plan["calibrated_at"] = _dt.date.today().isoformat()
                with open(cfg_path, "w") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                self._send_json({"ok": True, "plan_id": plan_id,
                                 "balance_usd": bal, "anchor": plan["anchor"]})
            except Exception as e:
                self._send_json({"error": f"Aufladung fehlgeschlagen: {e}"}, 500)
            return
        try:
            pct = float(body.get("dashboard_pct"))
        except (TypeError, ValueError):
            self._send_json({"error": "dashboard_pct (Zahl) erforderlich"}, 400)
            return
        if pct <= 0 or pct > 100:
            self._send_json({"error": "dashboard_pct muss in (0, 100] liegen"}, 400)
            return
        cfg_path = self._plan_config_path()
        try:
            with open(cfg_path) as f:
                config = json.load(f)
            plans = config.get("coding_plans") or []
            plan = next((p for p in plans if p.get("id") == plan_id), None)
            win = next((w for w in (plan.get("windows") or []) if w.get("kind") == wkind), None) if plan else None
            if not win:
                self._send_json({"error": f"Plan/Fenster nicht gefunden: {plan_id}/{wkind}"}, 404)
                return
            models = self._plan_models(plan)
            cw = plan.get("count") or {}
            since, _, _ri = self._plan_window_since(win, plan, models)
            s = engine._cost_tracker.token_sums(models, since)
            used = (s["tokens_in"] * float(cw.get("fresh_in", 1.0) or 0)
                    + s["tokens_out"] * float(cw.get("out", 1.0) or 0)
                    + s["cache_read_tokens"] * float(cw.get("cached", 1.0) or 0))
            if used <= 0:
                self._send_json({"error": "Keine Nutzung im Fenster — Kalibrierung braucht Verkehr im Ledger"}, 400)
                return
            win["limit_tokens"] = int(round(used / (pct / 100.0)))
            plan["calibrated_at"] = _dt.date.today().isoformat()
            with open(cfg_path, "w") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self._send_json({"ok": True, "plan_id": plan_id, "window_kind": wkind,
                             "new_limit_tokens": win["limit_tokens"],
                             "used_est": int(used)})
        except Exception as e:
            self._send_json({"error": f"Kalibrierung fehlgeschlagen: {e}"}, 500)

    # --- Editable cost-rate table ---

    def _handle_cost_rates_get(self):
        """GET /v1/costs/rates — the editable rate table + what it resolves to.

        Three blocks:
          `rates`     — config.json → cost_rates (what the admin owns)
          `builtin`   — engine._cost_rates (the code seed, read-only, shown so
                        an admin can see WHY a model already has a price)
          `unpriced`  — cloud models resolving to $0: the silent-$0 gap made
                        visible. These bill as free today.
        """
        user = self._require_auth()
        if not user:
            return
        if user.get("role") != "admin" and user.get("id") != "__system__":
            self._send_json({"error": "admin only"}, 403)
            return
        try:
            self._send_json({
                "rates": engine.get_config_cost_rates(),
                "builtin": {k: dict(v) for k, v in (engine._cost_rates or {}).items()},
                "unpriced": engine.unpriced_models(),
            })
        except Exception as e:
            self._send_json({"error": f"Raten lesen fehlgeschlagen: {e}"}, 500)

    def _handle_cost_rates_save(self):
        """POST /v1/costs/rates {rates:{<model-or-prefix>: {input, output,
        cache_read?}}} — admin: replace the editable rate table (config.json →
        cost_rates). USD per 1M tokens.

        Full replace, not merge: the client sends the table it just edited, so a
        merge would make deletion impossible. Keys may be an exact model id OR a
        prefix (`claude-opus` matches `claude-opus-4-6-…`); `_match_rate_table`
        resolves the LONGEST match, so a prefix never shadows a more specific id."""
        user = self._require_auth()
        if not user:
            return
        if user.get("role") != "admin" and user.get("id") != "__system__":
            self._send_json({"error": "admin only"}, 403)
            return
        raw = (self._read_json().get("rates") or {})
        if not isinstance(raw, dict):
            self._send_json({"error": "rates muss ein Objekt sein"}, 400)
            return
        clean = {}
        for mid, r in raw.items():
            mid = (mid or "").strip()
            if not mid or not isinstance(r, dict):
                continue
            try:
                inp = float(r.get("input") or 0.0)
                out = float(r.get("output") or 0.0)
            except (TypeError, ValueError):
                self._send_json({"error": f"Ungültige Zahl bei '{mid}'"}, 400)
                return
            if inp < 0 or out < 0:
                self._send_json({"error": f"Negativer Preis bei '{mid}'"}, 400)
                return
            entry = {"input": inp, "output": out}
            if r.get("cache_read") not in (None, ""):
                try:
                    ccr = float(r["cache_read"])
                except (TypeError, ValueError):
                    self._send_json({"error": f"Ungültiger Cache-Preis bei '{mid}'"}, 400)
                    return
                if ccr < 0:
                    self._send_json({"error": f"Negativer Cache-Preis bei '{mid}'"}, 400)
                    return
                entry["cache_read"] = ccr
            clean[mid] = entry
        cfg_path = self._plan_config_path()
        try:
            with open(cfg_path) as f:
                config = json.load(f)
            config["cost_rates"] = clean
            with open(cfg_path, "w") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            # engine.get_config_cost_rates is mtime-cached on config.json — the
            # write above makes the new table live; no restart, no cache bust.
            self._send_json({"ok": True, "count": len(clean),
                             "unpriced": engine.unpriced_models()})
        except Exception as e:
            self._send_json({"error": f"Speichern fehlgeschlagen: {e}"}, 500)

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
