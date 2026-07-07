# AI-generation of WORKFLOWS (.flow + plan.md) from a chat, a plan document,
# or a natural-language description. Mirrors engine/instruction_gen.py
# (gen-row → daemon thread → background_call → poll), but the LLM call is a
# single forced-tool (structured output) round — all source material is
# gathered deterministically up front, no agentic tool loop.
#
# Architecture ("Der Plan ist das Programm"): the generated .flow is a THIN
# deterministic spine — collect inputs (ask_user_for_file), run the plan's
# judgment steps agentically (agent_step), write the report (write_file),
# verify (agent_step with an auditor instruction). The method itself lives in
# natural language in the <name>.plan.md sidecar, seeded into the run as the
# `plan_md` variable. Chat sources prefer the session's APPROVED MoA plan
# (the versioned `ausfuehrungsplan.md` artifact) and pin the chat's executor
# model via the MODEL header — a past tool-call sequence is NOT recoverable
# for interactive chats (traces are scheduler-only), so the plan, not the
# trace, is what gets reproduced.
#
# Validation is CODE, not LLM: _wf_parse (syntax) + AST walk (every CALL tool
# must exist in TOOL_DISPATCH, every function must be a known builtin). One
# retry round with the error list; a still-broken draft lands as
# `ready_with_warnings` so the user can fix it in the editor.

import json as _json
import os
import re
import threading
import uuid

import brain as _brain
from engine.workflow import (
    _WF_BUILTINS, _wf_parse, WorkflowError,
    _WFAssign, _WFBinOp, _WFCall, _WFDict, _WFFnCall, _WFFor, _WFGetAttr,
    _WFGetItem, _WFIf, _WFInterpStr, _WFList, _WFReturn, _WFUnary,
)
from server_lib.db import ChatDB

# ── Live progress registry (in-memory, per gen_id) — instruction_gen pattern ──
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


# Tools the generator may reference in a .flow — curated subset of
# TOOL_DEFINITIONS (name + description injected into the prompt). Deliberately
# NOT the full dispatch table: workflows should stay thin spines; everything
# judgment-heavy goes through agent_step, which brings its own broad toolset.
_FLOW_TOOL_ALLOWLIST = (
    "ask_user_for_file", "agent_step", "ask_llm",
    "read_file", "write_file", "list_directory",
    "read_document", "python_exec",
    "transcribe_audio", "translate_text", "translate_document",
    "web_fetch", "searxng_search", "exa_search",
    "render_diagram", "xlsx_create", "xlsx_query",
)


def _tool_palette_text() -> str:
    lines = []
    by_name = {t.get("name"): t for t in _brain.TOOL_DEFINITIONS}
    for name in _FLOW_TOOL_ALLOWLIST:
        td = by_name.get(name)
        if not td:
            continue
        desc = " ".join((td.get("description") or "").split())
        if len(desc) > 260:
            desc = desc[:257] + "…"
        props = ((td.get("input_schema") or {}).get("properties") or {})
        req = set((td.get("input_schema") or {}).get("required") or [])
        args = ", ".join(
            f"{p}{'' if p in req else '?'}" for p in props)
        lines.append(f"- `{name}({args})` — {desc}")
    return "\n".join(lines)


_GEN_SYSTEM_PROMPT_TEMPLATE = """\
Du bist ein Workflow-Architekt für Brain-Agent. Deine Aufgabe: aus dem \
gelieferten Quellmaterial (Chat-Verlauf, Ausführungsplan oder Beschreibung) \
einen WIEDERVERWENDBAREN Workflow erzeugen, der die dort gezeigte Arbeit für \
NEUE Eingaben nahezu identisch reproduziert. Du lieferst ZWEI Teile über den \
Funktionsaufruf `submit_workflow`:

1. `plan_md` — der Ausführungsplan als Markdown: die natürlichsprachliche \
Methodik in nummerierten Schritten (`### Schritt N — Titel`). Liegt im \
Quellmaterial bereits ein Plan vor, übernimm ihn möglichst WÖRTLICH und \
ersetze nur konkrete Eingaben (Dateinamen, Personen, Werte) durch neutrale \
Platzhalter-Formulierungen ("das hochgeladene Bild", "die Eingabedatei"). \
Erfinde KEINE neuen Schritte, lasse keine aus.

2. `flow_source` — das deterministische Rückgrat in der .flow-DSL (Grammatik \
unten). Es sammelt die Eingaben ein, führt den Plan agentisch aus, schreibt \
den Report und verifiziert ihn. Die Methodik gehört in plan_md, NICHT in \
einzelne DSL-Aufrufe — kompiliere den Plan nicht in Einzelschritt-CALLs, \
außer die Quelle ist eine simple mechanische Kette (dann sind direkte \
Tool-CALLs ohne plan_md richtig).

## .flow-DSL (vollständige Grammatik)
```
WORKFLOW "Anzeigename"
DESCRIPTION "Ein Satz, was der Workflow tut"
TRIGGER manual
MODEL modell-id            # optional: pinnt das Ausführungsmodell

SET var = AUSDRUCK         # Strings "..." (mit {{var}}-Interpolation), Zahlen,
                           # TRUE/FALSE/NULL, [Listen], {dicts}, var.feld, var[i],
                           # + - * / == != < > <= >= AND OR NOT
SET res = CALL tool_name arg1=wert arg2=wert    # Tool-Aufruf, Ergebnis = dict
CALL? tool_name arg=wert   # ? = weicher Fehler (last_error statt Abbruch)
IF bedingung:              # Einrückung = Block; ELSE: erlaubt
    ...
FOR EACH item IN liste:
    ...
RETURN wert
```
Eingebaute Funktionen: len, str, int, float, bool, now("%H%M%S"), lower, \
upper, trim, contains, split, join, replace, plan_steps.
`plan_steps(plan_md)` zerlegt einen Plan deterministisch in \
[{index, title, body}]-Schritte.
Die Variable `plan_md` ist automatisch mit dem Inhalt von plan_md vorbelegt, \
wenn du eins lieferst — NICHT selbst laden.

## Verfügbare Tools für CALL
<<TOOL_PALETTE>>

## Bauregeln (verbindlich)
- Jede Eingabedatei des Nutzers: `SET f = CALL ask_user_for_file \
prompt="…" accept="image/*"` — der Pfad ist dann `f.path`.
- Urteils-/Analyse-Schritte: `SET r = CALL agent_step plan=plan_md \
instruction="Führe den Plan vollständig am Eingabebild aus" \
files=[f.path] max_rounds=20 expected_output="…"` — EIN agent_step für den \
ganzen Plan ist der Normalfall (dann max_rounds=20 setzen — ein voller Plan \
braucht viele Werkzeugrunden); `FOR EACH s IN plan_steps(plan_md):` mit einem \
agent_step pro Schritt nur, wenn die Schritte stark getrennte \
Zwischenergebnisse brauchen. Bilddateien in `files=` SIEHT das Modell nativ \
(sofern es Bilder unterstützt) — Plan-Schritte wie „Bild visuell \
inspizieren" funktionieren also. Alle Schritte eines Laufs teilen sich EINEN \
Arbeitsordner: relative Dateinamen aus früheren Schritten (z. B. der \
Report-Pfad) sind in späteren Schritten direkt lesbar.
- Report IMMER explizit persistieren: `SET report_path = "report_" + \
now("%Y%m%d_%H%M%S") + ".md"` dann `CALL write_file path=report_path \
content=r.text`.
- VERIFY-SCHRITT (Pflicht, aktiv): nach dem Report ein zweiter agent_step, \
der als Auditor prüft, ob der Report alle Plan-Schritte abdeckt<<VERIFY_MODEL_HINT>>. \
Sein Ergebnis ans Ende des Reports anhängen oder als eigene Datei schreiben.
- `RETURN report_path` am Ende.
- Texte für Nutzer (prompts, Report-Inhalte) auf DEUTSCH, Bezeichner englisch.
- Workflow-Name kurz und sprechend; description EIN Satz Nutzen.
<<MODEL_PIN_HINT>>

## Ausgabe
Rufe GENAU EINMAL `submit_workflow` auf. `notes` = 2-4 Sätze an den Nutzer: \
was der Workflow tut, welche Eingaben er erwartet, was ggf. zu prüfen ist."""


_SUBMIT_TOOL = {
    "name": "submit_workflow",
    "description": "Liefere den fertigen Workflow (Pflicht-Ausgabeweg).",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Dateiname-tauglicher Kurzname (a-z, 0-9, -, _)"},
            "flow_source": {"type": "string", "description": "Vollständige .flow-Quelle"},
            "plan_md": {"type": "string", "description": "Ausführungsplan-Markdown; leer wenn kein Plan nötig"},
            "notes": {"type": "string", "description": "Hinweise an den Nutzer (2-4 Sätze)"},
        },
        "required": ["name", "flow_source", "notes"],
    },
}

_MAX_TRANSCRIPT_CHARS = 60000
_MAX_SOURCE_TEXT_CHARS = 40000


def _resolve_model() -> str:
    """Dedicated workflow-generator model (Service-Modelle → 'Workflow-Generator');
    falls back to the background default so the feature works before an admin
    sets the slot."""
    try:
        m = (_brain._server_config().get("workflow_gen_model") or "").strip()
        if m and _brain._is_model_available(m):
            return m
    except Exception:
        pass
    return _brain._background_model_default()


# ── Chat-source extraction (deterministic) ───────────────────────────────────

def _extract_moa_context(sid: str, msgs: list) -> dict:
    """Pull the approved MoA plan + pinned models out of a stored session.
    Returns {plan_md, executor, planner} (all may be empty). Precedence per
    exploration: ausfuehrungsplan.md artifact (approved, versioned) →
    moa_planner draft card; executor from metadata.auto_route.moa →
    moa_plan_review card."""
    out = {"plan_md": "", "executor": "", "planner": ""}
    # 1) The approved plan artifact (one stable file per session, latest on disk).
    try:
        for a in (ChatDB.list_artifacts_for_session(sid) or []):
            if (a.get("name") or "") == "ausfuehrungsplan.md":
                p = a.get("path") or ""
                if p and os.path.exists(p):
                    with open(p, "r") as f:
                        text = f.read()
                    # Strip the persist-header comment (<!-- … -->) if present.
                    text = re.sub(r"^\s*<!--.*?-->\s*", "", text, flags=re.DOTALL)
                    out["plan_md"] = text.strip()
                break
    except Exception:
        pass
    for m in reversed(msgs):
        role = m.get("role") or ""
        # 2) Executor/planner from the assistant turn's routing metadata.
        if role == "assistant" and not out["executor"]:
            moa = (((m.get("metadata") or {}).get("auto_route") or {}).get("moa") or {})
            if isinstance(moa, dict):
                out["executor"] = str(moa.get("executor") or "")
                out["planner"] = str(moa.get("planner") or "")
        # 3) Synthetic MoA cards: plan-review outcome + planner draft fallback.
        if role == "tool_result":
            content = m.get("content")
            if isinstance(content, str):
                try:
                    content = _json.loads(content)
                except Exception:
                    content = None
            if not isinstance(content, dict):
                continue
            result = content.get("result") or {}
            if content.get("name") == "moa_plan_review" and not out["executor"]:
                out["executor"] = str(result.get("executor") or "")
            if content.get("name") == "moa_planner" and not out["plan_md"]:
                out["plan_md"] = str(result.get("draft") or "").strip()
                if not out["planner"]:
                    out["planner"] = str(result.get("model") or "")
        if out["plan_md"] and out["executor"]:
            break
    return out


def _chat_source_material(sid: str) -> tuple[str, dict]:
    """Build the source-material block for a chat session + the MoA context."""
    info = ChatDB.get_session_info(sid)
    if not info:
        raise ValueError(f"Session {sid} nicht gefunden")
    msgs = ChatDB.load_messages(sid)
    if not msgs:
        raise ValueError("Die Sitzung hat keine Nachrichten")
    from handlers.sessions_handler import _build_conversation_markdown
    transcript = _build_conversation_markdown(sid, info, msgs)
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        transcript = (transcript[:_MAX_TRANSCRIPT_CHARS]
                      + "\n\n[… Verlauf gekürzt]")
    moa = _extract_moa_context(sid, msgs)
    parts = []
    if moa["plan_md"]:
        parts.append("## Freigegebener Ausführungsplan aus dem Chat "
                     "(verbindliche Vorlage für plan_md)\n" + moa["plan_md"])
    if moa["executor"]:
        parts.append(f"## Ausführungsmodell des Chats (als MODEL-Header pinnen)\n"
                     f"{moa['executor']}")
    parts.append("## Chat-Verlauf\n" + transcript)
    return "\n\n".join(parts), moa


# ── Validation (code, not LLM) ───────────────────────────────────────────────

def _walk_flow_nodes(nodes):
    for node in nodes:
        yield node
        if isinstance(node, _WFIf):
            yield from _walk_flow_nodes(node.then_body)
            yield from _walk_flow_nodes(node.else_body)
        elif isinstance(node, _WFFor):
            yield from _walk_flow_nodes([node.iterable])
            yield from _walk_flow_nodes(node.body)
        elif isinstance(node, _WFAssign):
            yield from _walk_flow_nodes([node.value])
        elif isinstance(node, _WFReturn):
            yield from _walk_flow_nodes([node.value])
        elif isinstance(node, _WFCall):
            yield from _walk_flow_nodes([expr for _, expr in node.kwargs])
        elif isinstance(node, _WFBinOp):
            yield from _walk_flow_nodes([node.left, node.right])
        elif isinstance(node, _WFUnary):
            yield from _walk_flow_nodes([node.operand])
        elif isinstance(node, (_WFGetAttr,)):
            yield from _walk_flow_nodes([node.base])
        elif isinstance(node, _WFGetItem):
            yield from _walk_flow_nodes([node.base, node.index])
        elif isinstance(node, _WFList):
            yield from _walk_flow_nodes(node.items)
        elif isinstance(node, _WFDict):
            yield from _walk_flow_nodes([v for pair in node.pairs for v in pair])
        elif isinstance(node, _WFFnCall):
            yield from _walk_flow_nodes(node.args)
        elif isinstance(node, _WFInterpStr):
            # {{expr}} parts are parsed lazily at runtime — parse them now so a
            # broken interpolation fails validation, not the first run.
            for kind, val in node.parts:
                if kind == "expr":
                    try:
                        sub = _wf_parse(f"SET _x = {val}").body[0]
                        yield from _walk_flow_nodes([sub.value])
                    except WorkflowError as e:
                        raise WorkflowError(
                            f"Interpolation {{{{{val}}}}}: {e}", node.line)


def validate_flow(source: str) -> list[str]:
    """Deterministic checks: syntax, tool existence, builtin existence.
    Returns a list of human-readable errors (empty = valid)."""
    errors: list[str] = []
    try:
        prog = _wf_parse(source)
    except WorkflowError as e:
        return [f"Syntaxfehler Zeile {e.line}: {e}"]
    try:
        for node in _walk_flow_nodes(prog.body):
            if isinstance(node, _WFCall):
                if node.tool not in _brain.TOOL_DISPATCH:
                    errors.append(
                        f"Zeile {node.line}: unbekanntes Tool '{node.tool}'")
            elif isinstance(node, _WFFnCall):
                if node.name not in _WF_BUILTINS:
                    errors.append(
                        f"Zeile {node.line}: unbekannte Funktion '{node.name}()'")
    except WorkflowError as e:
        errors.append(f"Zeile {e.line}: {e}")
    return errors


def _unique_workflow_name(agent_id: str, base: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", (base or "workflow").lower()).strip("_")[:56]
    safe = safe or "workflow"
    name = safe
    n = 2
    while _brain.WorkflowEngine.get_workflow_source(agent_id, name) is not None:
        name = f"{safe}-{n}"
        n += 1
    return name


# ── Generation worker ────────────────────────────────────────────────────────

def _run_generation(*, gen_id: str, agent_id: str, source_kind: str,
                    source_ref: str, source_text: str, instructions: str,
                    attachments: list, user_id: str):
    def _cancelled():
        return ChatDB.workflow_gen_cancelled(gen_id)

    try:
        model = _resolve_model()
        if not model:
            ChatDB.update_workflow_gen(
                gen_id, status="error",
                error="Kein Modell verfügbar — bitte unter Service-Modelle ein "
                      "Modell für 'Workflow-Generator' setzen.")
            return
        ChatDB.update_workflow_gen(gen_id, phase="gathering", model=model)
        _push_step(gen_id, "phase", f"Sammelt Quellmaterial (Modell: {model})")

        moa = {"plan_md": "", "executor": "", "planner": ""}
        if source_kind == "chat":
            material, moa = _chat_source_material(source_ref)
        elif source_kind == "plan":
            text = (source_text or "")[:_MAX_SOURCE_TEXT_CHARS]
            material = "## Vorliegender Ausführungsplan (verbindliche Vorlage " \
                       "für plan_md)\n" + text
        else:  # nl
            material = "## Beschreibung des gewünschten Workflows\n" \
                       + (source_text or "")[:_MAX_SOURCE_TEXT_CHARS]
        for att in attachments or []:
            an = str((att or {}).get("name") or "Anhang")
            at = str((att or {}).get("text") or "")[:_MAX_SOURCE_TEXT_CHARS]
            if at.strip():
                material += f"\n\n## Beigelegte Datei: {an}\n{at}"

        verify_hint = ""
        if moa.get("planner"):
            verify_hint = (f' (nutze dafür model="{moa["planner"]}" — '
                           f"das Planner-Modell des Chats)")
        model_pin = ("- Setze `MODEL " + moa["executor"] + "` — das Modell, das "
                     "die Arbeit im Quell-Chat nachweislich ausgeführt hat."
                     if moa.get("executor") else
                     "- Setze einen MODEL-Header nur, wenn die Quelle ein "
                     "bestimmtes Modell nahelegt; sonst weglassen.")
        system_prompt = (_GEN_SYSTEM_PROMPT_TEMPLATE
                         .replace("<<TOOL_PALETTE>>", _tool_palette_text())
                         .replace("<<VERIFY_MODEL_HINT>>", verify_hint)
                         .replace("<<MODEL_PIN_HINT>>", model_pin))

        user_parts = []
        if instructions.strip():
            user_parts.append("## Zusätzliche Vorgaben des Nutzers\n"
                              + instructions.strip())
        user_parts.append(material)
        user_parts.append("Erzeuge jetzt den Workflow via submit_workflow.")
        user_msg = "\n\n".join(user_parts)

        if _cancelled():
            ChatDB.update_workflow_gen(gen_id, status="cancelled", phase="")
            return

        from handlers import sidecar_proxy
        result_obj = None
        warnings: list[str] = []
        feedback = ""
        for attempt in (1, 2):
            ChatDB.update_workflow_gen(gen_id, phase="writing")
            _push_step(gen_id, "phase",
                       "Verfasst den Workflow …" if attempt == 1
                       else "Korrigiert Validierungsfehler …")
            turn_id = uuid.uuid4().hex
            with _PROGRESS_LOCK:
                _TURN_IDS[gen_id] = turn_id
            res = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": user_msg + feedback}],
                model=model,
                system_prompt=system_prompt,
                purpose="transform",
                cost_purpose="workflow_gen",
                agent_id=agent_id,
                session_id=f"wfgen-{gen_id}",
                user_id=user_id,
                max_rounds=1,
                forced_tool=_SUBMIT_TOOL,
                turn_id=turn_id,
            )
            with _PROGRESS_LOCK:
                _TURN_IDS.pop(gen_id, None)
            if res.get("cancelled") or _cancelled():
                ChatDB.update_workflow_gen(gen_id, status="cancelled", phase="")
                return
            if res.get("error"):
                ChatDB.update_workflow_gen(
                    gen_id, status="error", error=str(res["error"])[:500])
                return
            result_obj = res.get("forced_tool_input") or {}
            flow_source = str(result_obj.get("flow_source") or "")
            if not flow_source.strip():
                ChatDB.update_workflow_gen(
                    gen_id, status="error",
                    error="Das Modell lieferte keine .flow-Quelle.")
                return
            _push_step(gen_id, "phase", "Validiert den Entwurf …")
            warnings = validate_flow(flow_source)
            if not warnings:
                break
            _push_step(gen_id, "info",
                       f"{len(warnings)} Validierungsfehler — "
                       + "; ".join(warnings[:3]))
            feedback = ("\n\n## Dein voriger Entwurf\n```\n" + flow_source
                        + "\n```\n\n## Validierungsfehler (beheben!)\n"
                        + "\n".join(f"- {w}" for w in warnings))

        name = _unique_workflow_name(
            agent_id, str(result_obj.get("name") or ""))
        status = "ready_with_warnings" if warnings else "ready"
        _push_step(gen_id, "phase",
                   "Fertig." if not warnings
                   else f"Fertig mit {len(warnings)} Warnung(en) — bitte im "
                        f"Editor prüfen.")
        ChatDB.update_workflow_gen(
            gen_id, status=status, phase="",
            flow_source=str(result_obj.get("flow_source") or ""),
            plan_md=str(result_obj.get("plan_md") or ""),
            notes=str(result_obj.get("notes") or ""),
            warnings=_json.dumps(warnings, ensure_ascii=False),
            suggested_name=name)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            _push_step(gen_id, "error", f"{type(e).__name__}: {e}"[:160])
            ChatDB.update_workflow_gen(
                gen_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def request_cancel(gen_id: str) -> bool:
    ChatDB.cancel_workflow_gen(gen_id)
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
    ChatDB.create_workflow_gen(gen_id, agent_id, source_kind,
                               source_ref or "", user_id or "")
    threading.Thread(
        target=_run_generation,
        kwargs={
            "gen_id": gen_id, "agent_id": agent_id,
            "source_kind": source_kind, "source_ref": source_ref or "",
            "source_text": source_text or "", "instructions": instructions or "",
            "attachments": attachments or [], "user_id": user_id or "",
        },
        daemon=True, name=f"wf_gen_{gen_id[:8]}").start()
    return gen_id
