# AI-generation of a SKILL.md (reusable procedure) from a chat, an approved
# MoA plan document, or a natural-language description. Mirrors
# engine/workflow_gen.py (gen-row → daemon thread → background_call → poll),
# but the deliverable is a per-user skill (frontmatter + markdown body), NOT a
# .flow workflow. The LLM call is a single forced-tool (structured output)
# round — all source material is gathered deterministically up front, no
# agentic tool loop.
#
# The generated skill is what a future agent loads via `use_skill`: a distilled
# REUSABLE procedure (trigger, prerequisites, numbered steps, gotchas, worked
# example) — NOT a transcript replay. Chat sources reuse workflow_gen's
# deterministic source extraction, so the session's approved MoA plan
# (`ausfuehrungsplan.md`) feeds the skill identically.
#
# Validation is CODE, not LLM, and light (a skill body is free markdown): the
# slug must be filename-safe, the required fields non-empty, and the body must
# not be a near-verbatim copy of the source transcript. One retry round with
# the error list; a still-thin draft lands as `ready_with_warnings` so the user
# can fix it in the editor.

import json as _json
import re
import threading
import uuid

import brain as _brain
from engine.workflow_gen import _chat_source_material, _MAX_SOURCE_TEXT_CHARS
from server_lib.db import ChatDB

# ── Live progress registry (in-memory, per gen_id) — workflow_gen pattern ──
_PROGRESS: dict[str, list[dict]] = {}
_PROGRESS_LOCK = threading.Lock()
_TURN_IDS: dict[str, str] = {}


def _push_step(gen_id: str, kind: str, text: str) -> None:
    if not gen_id:
        return
    with _PROGRESS_LOCK:
        steps = _PROGRESS.setdefault(gen_id, [])
        steps.append({"kind": kind, "text": text, "n": len(steps) + 1})
        if len(steps) > 200:
            del steps[: len(steps) - 200]


def get_steps(gen_id: str) -> list[dict]:
    with _PROGRESS_LOCK:
        return list(_PROGRESS.get(gen_id, []))


_GEN_SYSTEM_PROMPT = """\
Du bist ein Skill-Autor für Brain-Agent. Deine Aufgabe: aus dem gelieferten \
Quellmaterial (Chat-Verlauf, Ausführungsplan oder Beschreibung) einen \
WIEDERVERWENDBAREN Skill zu destillieren — eine SKILL.md, die ein späterer \
Agent per `use_skill` lädt, um dieselbe Art Aufgabe für NEUE Eingaben \
auszuführen.

Ein Skill ist KEINE Abschrift des Chats. Extrahiere die zugrundeliegende \
METHODE und schreibe sie als klare, anwendbare Anleitung:
- Auslöser: wann dieser Skill anzuwenden ist (1 Satz).
- Voraussetzungen / benötigte Eingaben.
- Nummerierte Vorgehensschritte (`## Schritt N — Titel`), so konkret, dass ein \
Agent sie ohne den Ursprungs-Chat ausführen kann.
- Fallstricke / worauf zu achten ist.
- Ein knappes Beispiel, wenn es hilft.
Ersetze konkrete Einzelfall-Werte (Dateinamen, Personen, Zahlen aus dem Chat) \
durch neutrale Platzhalter ("die Eingabedatei", "das hochgeladene Bild"). \
KEINE erfundenen Schritte, keine Auslassungen gegenüber dem Quellmaterial.

Du lieferst das Ergebnis GENAU EINMAL über den Funktionsaufruf `submit_skill`:
- `slug` — dateiname-tauglich (nur a-z, 0-9, Bindestrich), kurz und sprechend.
- `display_name` — lesbarer Titel.
- `description` — EIN Satz: Auslöser/Zweck (wird zur Skill-Auswahl angezeigt).
- `body_md` — der vollständige Markdown-Rumpf der SKILL.md (ohne Frontmatter — \
die setzt das System). Beginne direkt mit einer Überschrift.
- `notes` — 2-4 Sätze an den Nutzer: was der Skill abdeckt und was ggf. zu \
prüfen ist.

Texte auf DEUTSCH, Fachbegriffe/Bezeichner englisch."""


_SUBMIT_TOOL = {
    "name": "submit_skill",
    "description": "Liefere den fertigen Skill (Pflicht-Ausgabeweg).",
    "input_schema": {
        "type": "object",
        "properties": {
            "slug": {"type": "string",
                     "description": "Dateiname-tauglicher Kurzname (a-z, 0-9, -)"},
            "display_name": {"type": "string", "description": "Lesbarer Titel"},
            "description": {"type": "string",
                            "description": "1 Satz — Auslöser/Zweck"},
            "body_md": {"type": "string",
                        "description": "Vollständiger SKILL.md-Rumpf (Markdown, "
                                       "ohne Frontmatter)"},
            "notes": {"type": "string",
                      "description": "Hinweise an den Nutzer (2-4 Sätze)"},
        },
        "required": ["slug", "display_name", "description", "body_md", "notes"],
    },
}

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


# ── Validation (code, not LLM) ───────────────────────────────────────────────

def _norm_slug(base: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (base or "").lower()).strip("-")[:56]
    return s or "skill"


def _unique_slug(agent_id: str, base: str) -> str:
    """Agent-global unique slug across BOTH the shared skills/ dir and the
    per-user user_skills/ dir (avoids collisions with existing skills)."""
    safe = _norm_slug(base)
    name = safe
    n = 2
    while _brain.AgentConfig.user_skill_exists(agent_id, name):
        name = f"{safe}-{n}"
        n += 1
    return name


def validate_skill(draft: dict, source_material: str) -> list[str]:
    """Deterministic checks: slug shape, required fields non-empty, body is not
    a near-verbatim copy of the source. Returns human-readable errors."""
    errors: list[str] = []
    slug = _norm_slug(str(draft.get("slug") or ""))
    if not _SLUG_RE.match(slug):
        errors.append("slug ist nicht dateiname-tauglich (nur a-z, 0-9, -).")
    body = str(draft.get("body_md") or "").strip()
    if len(body) < 80:
        errors.append("body_md ist zu kurz — der Skill braucht eine echte "
                      "Anleitung, keine Stichworte.")
    if not str(draft.get("description") or "").strip():
        errors.append("description fehlt (ein Satz Auslöser/Zweck).")
    if not str(draft.get("display_name") or "").strip():
        errors.append("display_name fehlt.")
    # Anti-transcript guard: reject if the body is >90% a substring of the raw
    # source material (a lazy copy instead of a distilled procedure).
    if body and source_material:
        norm_body = " ".join(body.split()).lower()
        norm_src = " ".join(source_material.split()).lower()
        if len(norm_body) > 200 and norm_body[:400] in norm_src:
            errors.append("body_md wirkt wie eine wörtliche Abschrift des "
                          "Quellmaterials — destilliere die METHODE, nicht den "
                          "Chat-Verlauf.")
    return errors


# ── Generation worker ────────────────────────────────────────────────────────

def _run_generation(*, gen_id: str, agent_id: str, source_kind: str,
                    source_ref: str, source_text: str, instructions: str,
                    attachments: list, user_id: str):
    def _cancelled():
        return ChatDB.skill_gen_cancelled(gen_id)

    try:
        model = _resolve_model()
        if not model:
            ChatDB.update_skill_gen(
                gen_id, status="error",
                error="Kein Modell verfügbar — bitte unter Service-Modelle ein "
                      "Modell für 'Skill-Generator' setzen.")
            return
        ChatDB.update_skill_gen(gen_id, phase="gathering", model=model)
        _push_step(gen_id, "phase", f"Sammelt Quellmaterial (Modell: {model})")

        if source_kind == "chat":
            material, _moa = _chat_source_material(source_ref)
        elif source_kind == "plan":
            text = (source_text or "")[:_MAX_SOURCE_TEXT_CHARS]
            material = "## Vorliegender Ausführungsplan (Grundlage des Skills)\n" \
                       + text
        else:  # nl
            material = "## Beschreibung des gewünschten Skills\n" \
                       + (source_text or "")[:_MAX_SOURCE_TEXT_CHARS]
        for att in attachments or []:
            an = str((att or {}).get("name") or "Anhang")
            at = str((att or {}).get("text") or "")[:_MAX_SOURCE_TEXT_CHARS]
            if at.strip():
                material += f"\n\n## Beigelegte Datei: {an}\n{at}"

        user_parts = []
        if instructions.strip():
            user_parts.append("## Zusätzliche Vorgaben des Nutzers\n"
                              + instructions.strip())
        user_parts.append(material)
        user_parts.append("Erzeuge jetzt den Skill via submit_skill.")
        user_msg = "\n\n".join(user_parts)

        if _cancelled():
            ChatDB.update_skill_gen(gen_id, status="cancelled", phase="")
            return

        from handlers import sidecar_proxy
        result_obj: dict = {}
        warnings: list[str] = []
        feedback = ""
        for attempt in (1, 2):
            ChatDB.update_skill_gen(gen_id, phase="writing")
            _push_step(gen_id, "phase",
                       "Verfasst den Skill …" if attempt == 1
                       else "Korrigiert Validierungsfehler …")
            turn_id = uuid.uuid4().hex
            with _PROGRESS_LOCK:
                _TURN_IDS[gen_id] = turn_id
            res = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": user_msg + feedback}],
                model=model,
                system_prompt=_GEN_SYSTEM_PROMPT,
                purpose="transform",
                cost_purpose="skill_gen",
                agent_id=agent_id,
                session_id=f"skillgen-{gen_id}",
                user_id=user_id,
                max_rounds=1,
                forced_tool=_SUBMIT_TOOL,
                turn_id=turn_id,
            )
            with _PROGRESS_LOCK:
                _TURN_IDS.pop(gen_id, None)
            if res.get("cancelled") or _cancelled():
                ChatDB.update_skill_gen(gen_id, status="cancelled", phase="")
                return
            if res.get("error"):
                ChatDB.update_skill_gen(
                    gen_id, status="error", error=str(res["error"])[:500])
                return
            result_obj = res.get("forced_tool_input") or {}
            if not str(result_obj.get("body_md") or "").strip():
                ChatDB.update_skill_gen(
                    gen_id, status="error",
                    error="Das Modell lieferte keinen Skill-Rumpf.")
                return
            _push_step(gen_id, "phase", "Validiert den Entwurf …")
            warnings = validate_skill(result_obj, material)
            if not warnings:
                break
            _push_step(gen_id, "info",
                       f"{len(warnings)} Hinweis(e) — " + "; ".join(warnings[:3]))
            feedback = ("\n\n## Dein voriger Entwurf (body_md)\n```\n"
                        + str(result_obj.get("body_md") or "")[:4000]
                        + "\n```\n\n## Zu beheben\n"
                        + "\n".join(f"- {w}" for w in warnings))

        slug = _unique_slug(agent_id, str(result_obj.get("slug") or
                                           result_obj.get("display_name") or ""))
        status = "ready_with_warnings" if warnings else "ready"
        _push_step(gen_id, "phase",
                   "Fertig." if not warnings
                   else f"Fertig mit {len(warnings)} Hinweis(en) — bitte im "
                        f"Editor prüfen.")
        ChatDB.update_skill_gen(
            gen_id, status=status, phase="",
            slug=slug,
            display_name=str(result_obj.get("display_name") or slug),
            description=str(result_obj.get("description") or ""),
            body_md=str(result_obj.get("body_md") or ""),
            notes=str(result_obj.get("notes") or ""),
            warnings=_json.dumps(warnings, ensure_ascii=False))
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            _push_step(gen_id, "error", f"{type(e).__name__}: {e}"[:160])
            ChatDB.update_skill_gen(
                gen_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def _resolve_model() -> str:
    """Dedicated skill-generator model (Service-Modelle → 'Skill-Generator');
    falls back to the background default so the feature works before an admin
    sets the slot."""
    try:
        m = (_brain._server_config().get("skill_gen_model") or "").strip()
        if m and _brain._is_model_available(m):
            return m
    except Exception:
        pass
    return _brain._background_model_default()


def request_cancel(gen_id: str) -> bool:
    ChatDB.cancel_skill_gen(gen_id)
    _push_step(gen_id, "info", "Abbruch angefordert …")
    with _PROGRESS_LOCK:
        turn_id = _TURN_IDS.get(gen_id)
    if turn_id:
        try:
            from handlers import sidecar_proxy
            sidecar_proxy.cancel_turn(turn_id)
            return True
        except Exception:
            pass
    return False


def start_generation(*, agent_id: str, source_kind: str, source_ref: str = "",
                     source_text: str = "", instructions: str = "",
                     attachments: list | None = None, user_id: str = "") -> str:
    """Insert a generating row + spawn the worker. Returns the gen_id.
    source_kind: 'chat' (source_ref = session_id) | 'plan' | 'nl'
    (source_text = plan markdown / description)."""
    gen_id = uuid.uuid4().hex
    ChatDB.create_skill_gen(gen_id, agent_id, source_kind,
                            source_ref or "", user_id or "")
    threading.Thread(
        target=_run_generation,
        kwargs={
            "gen_id": gen_id, "agent_id": agent_id,
            "source_kind": source_kind, "source_ref": source_ref or "",
            "source_text": source_text or "", "instructions": instructions or "",
            "attachments": attachments or [], "user_id": user_id or "",
        },
        daemon=True, name=f"skill_gen_{gen_id[:8]}").start()
    return gen_id
