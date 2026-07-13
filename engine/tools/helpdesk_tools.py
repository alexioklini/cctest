# Helpdesk ("Brainy") tool bodies.
#
# Read-only tools exclusive to the helpdesk bot (Brainy). They let Brainy answer
# questions about the user's current session, the user themselves, and what the
# user has done across the system — so it can give concrete, personalised tips.
#
# All three are gated to `purpose="helpdesk"` (see brain.resolve_active_tools)
# and read scope from the request context the helpdesk endpoint sets up
# (session_id + current_user_id). They NEVER write.
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `get_request_context` from engine.context.
#   - ChatDB / AuthDB imported directly from server_lib (NOT re-exported on
#     brain). ProjectManager / _scheduler / _read_user_profile reached lazily
#     via `import brain as _brain` (they DO live on brain).
#
# brain.py re-exports all three via `from engine.tools.helpdesk_tools import (...)`.

from __future__ import annotations

from engine.context import get_request_context
from engine.tool_exec import _ok, _err
# ChatDB / AuthDB are NOT re-exported on `brain` — import from their real
# modules (leaf modules, no cycle with engine). ProjectManager / _scheduler /
# _read_user_profile DO live on brain, reached lazily via `import brain as _brain`.
from server_lib.db import ChatDB
from server_lib.auth import AuthDB

# Keep payloads small — Brainy needs orientation, not full transcripts.
_MAX_MESSAGES = 12
_MAX_MSG_CHARS = 600
_MAX_LIST = 25


def _trim(text: str, cap: int = _MAX_MSG_CHARS) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap] + " …"


def tool_helpdesk_session_info(args: dict) -> str:
    """Metadata + recent messages of the session Brainy was opened from."""
    import brain as _brain
    sid = get_request_context().session_id or ""
    if not sid:
        return _err("helpdesk_session_info: no active session")
    info = ChatDB.get_session_info(sid) or {}
    if not info:
        return _err(f"helpdesk_session_info: session {sid} not found")

    msgs = ChatDB.load_messages(sid) or []
    # Keep only the human-visible turns (drop internal thinking/tool rows).
    convo = [m for m in msgs if m.get("role") in ("user", "assistant")]
    recent = []
    for m in convo[-_MAX_MESSAGES:]:
        content = m.get("content")
        if isinstance(content, list):  # structured content blocks
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        recent.append({"role": m.get("role"), "content": _trim(str(content or ""))})

    return _ok({
        "session_id": sid,
        "title": info.get("title") or "",
        "model": info.get("model") or "",
        "project": info.get("project") or "",
        "thinking_level": info.get("thinking_level") or "",
        "status": info.get("status") or "",
        "created_at": info.get("created_at") or "",
        "message_count": len(convo),
        "recent_messages": recent,
    })


def tool_helpdesk_user_context(args: dict) -> str:
    """The user's profile + account preferences (greeting, role, comm-style)."""
    import brain as _brain
    uid = get_request_context().current_user_id or ""
    if not uid:
        return _ok({
            "authenticated": False,
            "note": "Kein angemeldeter Nutzer — anonyme Sitzung.",
        })

    user = {}
    try:
        user = AuthDB.get_user(uid) or {}
    except Exception as e:  # auth DB optional / single-user installs
        return _err(f"helpdesk_user_context: {type(e).__name__}: {e}")

    prefs = user.get("preferences") or {}
    profile = ""
    try:
        profile = _brain._read_user_profile(uid) or ""
    except Exception:
        profile = ""

    return _ok({
        "authenticated": True,
        "display_name": user.get("display_name") or user.get("username") or "",
        "role": user.get("role") or "",
        "greeting_name": prefs.get("greeting_name") or "",
        "job_description": prefs.get("job_description") or "",
        "communication_preferences": prefs.get("communication_preferences") or "",
        "auto_profile": _trim(profile, 4000),
    })


def tool_helpdesk_user_activity(args: dict) -> str:
    """What the user has done here: their chats, projects, and scheduled tasks.

    Lets Brainy give concrete, personalised tips ("du hast neulich ein Projekt
    X angelegt …") instead of generic answers."""
    import brain as _brain
    uid = get_request_context().current_user_id or ""

    # Sessions the user can see. None caller_user_id = admin/all (single-user).
    sessions = []
    try:
        rows = ChatDB.list_sessions(
            caller_user_id=(uid or None),
            visible_user_ids=([uid] if uid else None),
        ) or []
        for s in rows[:_MAX_LIST]:
            sessions.append({
                "title": s.get("title") or "(ohne Titel)",
                "project": s.get("project") or "",
                "messages": s.get("message_count", 0),
                "updated_at": s.get("updated_at") or s.get("created_at") or "",
            })
    except Exception:
        sessions = []

    # Projects.
    projects = []
    try:
        teams = []
        if uid:
            try:
                teams = [t.get("id") for t in (AuthDB.get_user_teams(uid) or [])]
            except Exception:
                teams = []
        for p in (_brain.ProjectManager.list_projects(
                "main", user_id=(uid or None), user_team_ids=teams) or [])[:_MAX_LIST]:
            projects.append({
                "name": p.get("name") or p.get("id") or "",
                "instructions_present": bool(p.get("instructions")),
                "status": p.get("status") or "active",
            })
    except Exception:
        projects = []

    # Scheduled tasks owned by the user.
    schedules = []
    try:
        if _brain._scheduler:
            for sc in (_brain._scheduler.list_all() or []):
                if uid and sc.get("user_id") and sc.get("user_id") != uid:
                    continue
                schedules.append({
                    "name": sc.get("name") or "",
                    "enabled": bool(sc.get("enabled")),
                    "next_run": sc.get("next_run") or "",
                })
                if len(schedules) >= _MAX_LIST:
                    break
    except Exception:
        schedules = []

    # Terminal-chats (code-mode bottom-workspace chats, status='code_chat').
    # Deliberately excluded from the normal session list above (db default
    # filter), so list them explicitly. Cross-reference active_turns — the
    # cross-process record of turns running right now — to flag which ones are
    # LIVE (a turn is streaming this instant), the signal Brainy needs to say
    # "there's a terminal chat active right now".
    terminal_chats = []
    live_count = 0
    try:
        live_ids = {row[0] for row in (ChatDB.list_active_turns() or [])}
        rows = ChatDB.list_sessions(
            status='code_chat',
            caller_user_id=(uid or None),
            visible_user_ids=([uid] if uid else None),
        ) or []
        for s in rows[:_MAX_LIST]:
            is_live = s.get("id") in live_ids
            if is_live:
                live_count += 1
            terminal_chats.append({
                "title": s.get("title") or "(ohne Titel)",
                "project": s.get("project") or "",
                "messages": s.get("message_count", 0),
                "updated_at": s.get("updated_at") or s.get("created_at") or "",
                "live": is_live,
            })
    except Exception:
        terminal_chats = []
        live_count = 0

    return _ok({
        "sessions": sessions,
        "session_count": len(sessions),
        "projects": projects,
        "project_count": len(projects),
        "schedules": schedules,
        "schedule_count": len(schedules),
        "terminal_chats": terminal_chats,
        "terminal_chat_count": len(terminal_chats),
        "terminal_chats_live_now": live_count,
    })


# --- Settings lookup ---------------------------------------------------------
#
# Brainy reads the settings that matter for the question and reasons about them
# itself — we return facts, it produces the recommendation.
#
# It goes through the SAME seams the HTTP endpoints use — `engine._models_config`
# (the runtime source of truth), `AuthDB.get_user_allowed_models`,
# `engine.is_model_local`, `engine.get_coding_plans` — NOT a fresh read of
# config.json. That matters for two reasons:
#   * the endpoints scope by permission (a non-admin sees only the models they
#     were granted, and sensitive fields are stripped). Re-reading the file
#     would hand Brainy everything and quietly bypass that.
#   * config.json is not the runtime truth — the in-memory config is. A file
#     reader would be a second, drifting source.
# Secrets (api keys, tokens) never appear in any section.

_CFG_SECTIONS = ("models", "coding_plans", "quotas", "cost_rates",
                 "providers", "service_models")

# Fields that answer "which model, and what does it cost me?". The internal
# plumbing (warmup flags, sync bookkeeping, raw_formats, inference knobs) is
# left out — it carries no signal for any user-facing question and would push
# 140 models past the context budget.
_MODEL_FIELDS = (
    "display_name", "description", "provider", "is_local", "capabilities",
    "max_context", "profile", "priority",
    "cost_input", "cost_output", "cost_cache_read",
    "cost_per_minute_usd", "cost_per_1k_chars_usd", "cost_per_page_usd",
    "coding_plan", "flat_plan",
)


def _model_row(mid: str, cfg: dict) -> dict:
    """One model, reduced to the decision-relevant fields, with the benchmark
    blob flattened to {task: {capability, tps}}.

    Flattening matters: the raw shape nests `measured` / `override` / `raw` /
    `source` under each of 9 task types. `override` wins where present — that's
    the admin's correction of a bad synthetic benchmark, and it's the number
    that actually drives routing, so it must be the number Brainy sees too."""
    import brain as _brain
    row = {"id": mid}
    for f in _MODEL_FIELDS:
        if cfg.get(f) is not None:
            row[f] = cfg[f]
    try:
        row["is_local"] = _brain.is_model_local(mid)
    except Exception:
        row.setdefault("is_local", False)

    bench = cfg.get("benchmark") or {}
    if isinstance(bench, dict):
        flat = {}
        for task, entry in bench.items():
            if not isinstance(entry, dict):
                continue
            src = entry.get("override") or entry.get("measured") or {}
            cap, tps = src.get("capability"), src.get("tps")
            if cap is None and tps is None:
                continue
            flat[task] = {k: v for k, v in (("capability", cap), ("tps", tps))
                          if v is not None}
        if flat:
            row["benchmark"] = flat

    # Resolve the billing account through the SAME seam billing uses, so the
    # provider default is included (a model may inherit its plan from its
    # provider — see brain.resolve_model_plan_id).
    try:
        pid = _brain.resolve_model_plan_id(mid)
        if pid:
            row["coding_plan"] = pid
        row["billed_at_zero"] = bool(_brain.model_is_flat_plan(mid))
    except Exception:
        pass
    return row


def tool_helpdesk_config(args: dict) -> str:
    """Read the settings relevant to the user's question (read-only, no secrets).

    Brainy analyses the returned JSON and answers — this tool supplies facts,
    not verdicts."""
    import brain as _brain

    section = str((args or {}).get("section") or "").strip().lower()
    if section not in _CFG_SECTIONS:
        return _err(f"helpdesk_config: unbekannter Abschnitt '{section}'. "
                    f"Erlaubt: {', '.join(_CFG_SECTIONS)}.")

    uid = get_request_context().current_user_id or ""

    if section == "models":
        # Same permission scoping the /v1/models/config endpoint applies:
        # a non-admin only ever sees the models granted to them.
        allowed = None
        try:
            user = AuthDB.get_user(uid) if uid else None
            is_admin = bool(user) and user.get("role") == "admin"
            if uid and not is_admin:
                allowed = AuthDB.get_user_allowed_models(uid)
        except Exception:
            allowed = None

        only_enabled = (args or {}).get("enabled_only", True) is not False
        rows = []
        for mid, cfg in (getattr(_brain, "_models_config", None) or {}).items():
            if not isinstance(cfg, dict):
                continue
            if allowed is not None and mid not in allowed:
                continue
            if only_enabled and not cfg.get("enabled"):
                continue
            rows.append(_model_row(mid, cfg))
        rows.sort(key=lambda m: -(m.get("priority") or 0))

        return _ok({
            "section": "models",
            "count": len(rows),
            "enabled_only": only_enabled,
            "hinweis": (
                "capability = 0-100 je Aufgabentyp (höher = besser); tps = gemessene "
                "Tokens/Sekunde (höher = schneller). Preise in $ pro 1 Mio. Token. "
                "is_local=true ⇒ läuft auf diesem Gerät: kostenlos, und die Daten "
                "verlassen das Gerät nicht. billed_at_zero=true ⇒ das Modell läuft in "
                "einem Abo (coding_plan), ein Aufruf kostet real $0 — die Preisfelder "
                "sind dann nur der Listenpreis zum Vergleich. Fehlt ein Preis, ist für "
                "dieses Modell keiner hinterlegt. Aufgabentypen: coding, math, research, "
                "analysis, reporting, creative, orchestration, agentic, fast."
            ),
            "models": rows,
        })

    if section == "coding_plans":
        plans = []
        for p in (_brain.get_coding_plans() or []):
            plans.append({k: p.get(k) for k in
                          ("id", "name", "type", "price", "quota_note",
                           "balance_usd", "windows", "count") if p.get(k) is not None})
        return _ok({
            "section": "coding_plans",
            "hinweis": ("Abrechnungskonten. type='flat' = Abo: Aufrufe kosten real $0, "
                        "dafür gelten Token-Kontingente je Zeitfenster. type='credit' = "
                        "API-Guthaben (balance_usd): echte Abrechnung gegen das Guthaben. "
                        "Die aktuelle Auslastung zeigt das Plan-Popover in der Statusleiste."),
            "coding_plans": plans,
        })

    if section == "quotas":
        qm = getattr(_brain, "_quota_manager", None)
        cfg = {}
        try:
            cfg = dict(qm.get_config()) if qm else {}
        except Exception:
            cfg = {}
        cfg.pop("user_overrides", None)          # other users' limits are none of Brainy's business
        me = {}
        try:
            if qm and uid:
                me = qm.get_user_state(uid) or {}
        except Exception:
            me = {}
        return _ok({
            "section": "quotas",
            "hinweis": ("Kostenkontingente in $ je Nutzerrolle (daily_usd = rollierender "
                        "Tag, cycle_usd = Abrechnungszeitraum). Lokale Modelle zählen NIE "
                        "dagegen. 'mein_stand' ist der Verbrauch des angemeldeten Nutzers."),
            "config": cfg,
            "mein_stand": me,
        })

    if section == "cost_rates":
        return _ok({
            "section": "cost_rates",
            "hinweis": ("Editierbare Preistabelle ($ pro 1 Mio. Token). Greift für Modelle "
                        "OHNE eigenen Preis im Modelle-Grid. Der Schlüssel kann eine "
                        "Modell-ID oder ein Präfix sein; bei mehreren Treffern gewinnt der "
                        "längste. Zusätzlich gibt es eingebaute Standardpreise."),
            "cost_rates": _brain.get_config_cost_rates() or {},
            "ohne_hinterlegten_preis": [m["id"] for m in (_brain.unpriced_models() or [])],
        })

    if section == "providers":
        provs = []
        for name, p in (_brain.get_provider_configs() or {}).items():
            if not isinstance(p, dict):
                continue
            provs.append({
                "name": name,
                "is_local": bool(p.get("is_local")),
                "coding_plan": p.get("coding_plan") or "",
                "api_key_count": len(p.get("api_keys") or ([1] if p.get("api_key") else [])),
            })
        return _ok({
            "section": "providers",
            "hinweis": ("Anbieter. API-Schlüssel werden NIE ausgegeben (nur ihre Anzahl). "
                        "is_local=true ⇒ läuft auf diesem Gerät. coding_plan = "
                        "Abrechnungskonto-Vorgabe für alle Modelle dieses Anbieters "
                        "(ein einzelnes Modell kann sie überstimmen)."),
            "providers": provs,
        })

    # service_models — which model does each background job use? Read the LIVE
    # config via _server_config() (the same seam every runtime reader uses —
    # settings edits mirror into it without a restart), NOT a fresh file read.
    cfg = {}
    try:
        cfg = _brain._server_config() or {}
    except Exception:
        cfg = {}
    svc = {k: v for k, v in cfg.items()
           if k.endswith("_model") and isinstance(v, str)}
    return _ok({
        "section": "service_models",
        "hinweis": ("Welches Modell welche Hintergrund-Aufgabe erledigt. Leer = "
                    "Server-Standard (default_model). Einstellbar unter "
                    "Einstellungen → Service-Modelle."),
        "service_models": svc,
        "default_model": cfg.get("default_model") or "",
    })

