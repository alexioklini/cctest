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
#   - brain runtime (ChatDB, AuthDB, ProjectManager, scheduler, profile reader)
#     reached lazily via `import brain as _brain` — no top-level import (cycle).
#
# brain.py re-exports all three via `from engine.tools.helpdesk_tools import (...)`.

from __future__ import annotations

from engine.context import get_request_context
from engine.tool_exec import _ok, _err

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
    info = _brain.ChatDB.get_session_info(sid) or {}
    if not info:
        return _err(f"helpdesk_session_info: session {sid} not found")

    msgs = _brain.ChatDB.load_messages(sid) or []
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
        user = _brain.AuthDB.get_user(uid) or {}
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
        rows = _brain.ChatDB.list_sessions(
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
                teams = [t.get("id") for t in (_brain.AuthDB.get_user_teams(uid) or [])]
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

    return _ok({
        "sessions": sessions,
        "session_count": len(sessions),
        "projects": projects,
        "project_count": len(projects),
        "schedules": schedules,
        "schedule_count": len(schedules),
    })
