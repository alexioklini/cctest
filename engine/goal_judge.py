"""Goal-Modus judge — evaluates a turn's reply against the session/task goal.

One entry point: `judge(...)`. Called by the chat worker (handlers/chat.py)
and the scheduler (engine/scheduler.py) after each turn while a goal is
active. Uses a forced-tool background call so the verdict arrives as a
validated dict (no free-text JSON parsing). Judge failures are terminal for
the goal loop by design — the caller stops iterating, never retries the
judge (an unreliable judge must not be able to spin the loop).

No top-level `import brain` (engine-module invariant) — brain runtime is
reached lazily inside the functions.
"""

# Absolute ceiling for goal iterations, clamped everywhere a max-iterations
# value enters the system (manage action, scheduler CRUD, composer defaults).
GOAL_ITER_HARD_CAP = 10

# Tool schema the judge is forced to call (Anthropic flat shape, consumed by
# sidecar_proxy's forced_tool path).
GOAL_VERDICT_TOOL = {
    "name": "goal_verdict",
    "description": ("Bewertung, ob das definierte Ziel durch die letzte "
                    "Assistenten-Antwort erreicht wurde."),
    "input_schema": {
        "type": "object",
        "properties": {
            "fulfilled": {
                "type": "boolean",
                "description": "true, wenn das Ziel vollständig erreicht ist",
            },
            "impossible": {
                "type": "boolean",
                "description": ("true, wenn das Ziel objektiv unerreichbar ist "
                                "oder der Assistent aus legitimen Gründen "
                                "abgelehnt hat (Sicherheit, fehlende Daten oder "
                                "Berechtigungen)"),
            },
            "reasoning": {
                "type": "string",
                "description": "Knappe Begründung der Bewertung (1-3 Sätze)",
            },
            "continue_instruction": {
                "type": "string",
                "description": ("Nur wenn fulfilled=false und impossible=false: "
                                "konkrete nächste Anweisung, wie die Antwort dem "
                                "Ziel näher kommt"),
            },
        },
        "required": ["fulfilled", "reasoning"],
    },
}

_JUDGE_SYSTEM_PROMPT = (
    "Du bist ein strenger, fairer Prüfer. Bewerte ausschließlich, ob die "
    "letzte Assistenten-Antwort das definierte Ziel erfüllt.\n"
    "Regeln:\n"
    "1. Das Ziel ist vollständig erfüllt → fulfilled=true.\n"
    "2. Der Assistent hat aus legitimen Gründen abgelehnt (Sicherheit, "
    "fehlende Daten, fehlende Berechtigungen) oder das Ziel ist objektiv "
    "unerreichbar → impossible=true. Erzwinge in diesem Fall KEINE "
    "Fortsetzung.\n"
    "3. Sonst: formuliere eine knappe, konkrete continue_instruction in der "
    "Sprache der Unterhaltung. Wiederhole keine bereits erledigten Schritte; "
    "benenne präzise, was fehlt oder falsch ist.\n"
    "Antworte ausschließlich über das Tool goal_verdict."
)

# Input caps — the judge sees the goal + the tail of the exchange, never the
# full transcript (context growth is the loop's, not the judge's, problem).
_LAST_USER_CAP = 2000
_REPLY_TAIL_CAP = 12000


def _config_composer_defaults() -> dict:
    import brain as _brain
    try:
        return (_brain._server_config().get("composer_defaults") or {})
    except Exception:
        return {}


def goal_mode_enabled() -> bool:
    """Admin kill-switch (config.json → composer_defaults.goal_mode_enabled)."""
    return bool(_config_composer_defaults().get("goal_mode_enabled", True))


def resolve_max_iterations(override: int = 0) -> int:
    """Effective iteration cap: per-session/task override → admin default → 5,
    clamped to GOAL_ITER_HARD_CAP."""
    try:
        override = int(override or 0)
    except (TypeError, ValueError):
        override = 0
    if override > 0:
        return max(1, min(GOAL_ITER_HARD_CAP, override))
    try:
        default = int(_config_composer_defaults().get("goal_max_iterations", 5) or 5)
    except (TypeError, ValueError):
        default = 5
    return max(1, min(GOAL_ITER_HARD_CAP, default))


def judge(*, goal: str, reply: str, iteration: int, max_iterations: int,
          session_id: str = "", agent_id: str = "main", project: str = "",
          user_id=None, last_user_msg: str = "") -> dict:
    """Judge `reply` against `goal`.

    Returns {"fulfilled": bool, "impossible": bool, "reasoning": str,
             "continue_instruction": str, "error": str|None}.
    `error` non-None means the judge itself failed (model unresolvable, GDPR
    skip/block, call error, missing verdict) — the caller must stop the loop.
    """
    import brain as _brain
    from handlers import sidecar_proxy

    out = {"fulfilled": False, "impossible": False, "reasoning": "",
           "continue_instruction": "", "error": None}

    model = ""
    try:
        model = (_brain._server_config().get("goal_judge_model") or "").strip()
    except Exception:
        model = ""
    if not model:
        model = _brain._background_model_default()
    if not model:
        out["error"] = "no_judge_model"
        return out

    _lu = (last_user_msg or "").strip()
    if len(_lu) > _LAST_USER_CAP:
        _lu = _lu[:_LAST_USER_CAP] + " …[gekürzt]"
    _rp = (reply or "").strip()
    if len(_rp) > _REPLY_TAIL_CAP:
        _rp = "…[Anfang gekürzt] " + _rp[-_REPLY_TAIL_CAP:]
    blob = (
        f"ZIEL:\n{goal}\n\n"
        f"ITERATION: {iteration}/{max_iterations}\n\n"
        + (f"LETZTE NUTZER-NACHRICHT:\n{_lu}\n\n" if _lu else "")
        + f"LETZTE ASSISTENTEN-ANTWORT:\n{_rp}"
    )

    # GDPR gate — same seam every background call uses. The judge inspects
    # chat content, so it must respect the background PII policy (swap /
    # anonymise / skip / block).
    deanon = lambda t: t  # noqa: E731 — identity until the gate replaces it
    try:
        model, _blobs, deanon = _brain.gdpr_pick_model_for_background(
            model, [blob], purpose="goal_judge")
        if isinstance(_blobs, list) and _blobs:
            blob = _blobs[0]
    except _brain.GDPRSkipError as _se:
        out["error"] = f"gdpr_skip: {_se}"
        return out
    except _brain.GDPRBlockedError as _ge:
        out["error"] = f"gdpr_blocked: {_ge}"
        return out
    except Exception:
        pass  # scanner bugs never block the judge; fall through unswapped

    try:
        result = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": blob}],
            model=model,
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            purpose="transform",
            agent_id=agent_id or "main",
            session_id=session_id,
            project=project or "",
            user_id=user_id,
            max_tokens=1024,
            max_rounds=1,
            cost_purpose="goal_judge",
            forced_tool=GOAL_VERDICT_TOOL,
            prompt_cache_key=f"goal-judge-{session_id}" if session_id else "",
        )
    except Exception as e:
        out["error"] = f"judge_call_failed: {e}"
        return out

    if not isinstance(result, dict) or result.get("error"):
        out["error"] = f"judge_call_failed: {(result or {}).get('error', 'no result')}"
        return out
    verdict = result.get("forced_tool_input")
    if not isinstance(verdict, dict):
        out["error"] = "no_verdict"
        return out

    out["fulfilled"] = bool(verdict.get("fulfilled"))
    out["impossible"] = bool(verdict.get("impossible"))
    out["reasoning"] = deanon(str(verdict.get("reasoning", "") or ""))
    out["continue_instruction"] = deanon(
        str(verdict.get("continue_instruction", "") or "").strip())
    # A continue verdict without an instruction can't drive an iteration —
    # treat it as a judge failure rather than looping on an empty message.
    if not out["fulfilled"] and not out["impossible"] and not out["continue_instruction"]:
        out["error"] = "empty_continue_instruction"
    return out
