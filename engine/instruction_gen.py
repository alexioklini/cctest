# AI-generation of project INSTRUCTIONS (the per-project markdown brief that is
# injected into every project chat). Replaces the manual workflow the user did
# by hand: open a chat, attach the reference docs, write a prompt, let the agent
# read the docs + produce the instruction document, then paste it into the
# project view (see project `risikoanalysen` + chat 92ddc378 for the gold doc).
#
# Flow:
#   1. handler inserts a project_instruction_gen row status='generating', returns gen_id
#   2. start_generation() spawns a daemon thread running _run_generation()
#   3. thread: builds a context preamble that NAMES the project's reference files
#      (instruction-files, on disk) + ingested folders + web URLs, then runs ONE
#      AGENTIC background_call (purpose='interactive' → full toolset, max_rounds>1)
#      so the model READS the reference files with read_document and QUERIES the
#      project wing with mempalace_query — exactly like the manual chat.
#   4. the produced markdown is stored on the row as `result_md`. The UI polls,
#      and on 'ready' loads it into the instructions editor for review + Save.
#      It is NEVER written to project.json automatically (review-before-save).

import json as _json
import os
import threading
import uuid

import brain as _brain
from engine.context import request_context
from server_lib.db import ChatDB

# ── Live progress registry (in-memory, per gen_id) ──────────────────────────
# The agentic loop runs inside a synchronous background_call (no streaming), so
# to make it TRANSPARENT in the dialog we record a human-readable step log here:
#  - the worker appends phase steps (gathering/writing/done),
#  - tool_mcp.handle_tools_call calls note_tool_call() for each tool the model
#    invokes on the synthetic `instrgen-<gen_id>` session ("Liest …", "Fragt …").
# The GET endpoint merges these live steps with the DB row's status/error.
# Dropped when the run reaches a terminal state (keep memory bounded).
_PROGRESS: dict[str, list[dict]] = {}
_PROGRESS_LOCK = threading.Lock()
_SESSION_PREFIX = "instrgen-"
# turn_id per gen_id, so the cancel endpoint can ask the sidecar to stop the
# in-flight agentic loop (cooperative DB flag alone can't interrupt the blocking
# sidecar call — the turn_id + POST /cancel/<turn_id> does).
_TURN_IDS: dict[str, str] = {}


def _push_step(gen_id: str, kind: str, text: str) -> None:
    """Append one progress step (kind: phase|tool|info|error)."""
    if not gen_id:
        return
    with _PROGRESS_LOCK:
        steps = _PROGRESS.setdefault(gen_id, [])
        steps.append({"kind": kind, "text": text, "n": len(steps) + 1})
        # Bound the log so a runaway loop can't grow it without limit.
        if len(steps) > 200:
            del steps[: len(steps) - 200]


def get_steps(gen_id: str) -> list[dict]:
    with _PROGRESS_LOCK:
        return list(_PROGRESS.get(gen_id, []))


def _drop_steps(gen_id: str) -> None:
    # Keep the final log around briefly is unnecessary — the DB row carries the
    # terminal status/result; the live log is only useful while running. We keep
    # it until the next poll cycle by NOT deleting immediately; instead the GET
    # endpoint serves it alongside the terminal row. Bounded by the 200-cap and
    # process restarts, so an explicit drop isn't required for correctness.
    pass


def _gen_id_from_session(session_id: str) -> str:
    """Map a synthetic instruction-gen session id back to its gen_id, or ''."""
    if session_id and session_id.startswith(_SESSION_PREFIX):
        return session_id[len(_SESSION_PREFIX):]
    return ""


def note_tool_call(session_id: str, name: str, args: dict) -> None:
    """Hook called from tool_mcp.handle_tools_call for every tool dispatch.
    No-op unless this is an instruction-gen session. Translates the tool call
    into a friendly step so the user sees what the agent is doing live."""
    gen_id = _gen_id_from_session(session_id or "")
    if not gen_id:
        return
    try:
        if name == "read_document":
            tgt = (args or {}).get("path") or (args or {}).get("filename") or ""
            tgt = os.path.basename(str(tgt).rstrip("/")) or str(tgt)
            _push_step(gen_id, "tool", f"Liest Referenzdatei: {tgt}")
        elif name == "mempalace_query":
            q = str((args or {}).get("query") or "").strip()
            _push_step(gen_id, "tool",
                       f"Fragt Projektwissen ab: „{q[:80]}“" if q else "Fragt Projektwissen ab")
        elif name in ("web_fetch",):
            url = str((args or {}).get("url") or "").strip()
            _push_step(gen_id, "tool", f"Lädt Web-Quelle: {url[:90]}")
        elif name in ("exa_search", "searxng_search"):
            q = str((args or {}).get("query") or "").strip()
            _push_step(gen_id, "tool", f"Web-Suche: „{q[:80]}“")
        else:
            _push_step(gen_id, "tool", f"Werkzeug: {name}")
    except Exception:
        pass

# How many agentic rounds the generation may take (read several reference files +
# a couple of wing queries, then write). Bounded so a tool-loop can't run away;
# the manual chat that produced the gold doc needed only a handful of reads.
_MAX_ROUNDS = 12

# This use case runs under the DEDICATED purpose 'instruction_gen', whose tool
# set is admin-configurable in the per-use-case tool matrix (Tools-Einstellungen
# → column "Projektanweisung"). The default set is seeded from
# brain._INSTRUCTION_GEN_TOOLS (read + project-knowledge + web-research tools);
# the model WRITES the document as its final text, not via a tool.
_PURPOSE = "instruction_gen"
# Broad seed query for the wing snapshot in the preamble — just enough to show
# the model what ingested knowledge exists so it knows mempalace_query is worth
# calling. The real retrieval happens via the model's own tool calls.
_WING_PEEK_QUERY = "overview key topics main subject"
_WING_PEEK_N = 5
# Per-file cap for INLINE reference-file content in the preamble. The reference/
# template doc is the most important input, so we inline it (no read_document
# loop). Bounded so a huge file can't blow the context; the model is told if a
# file was cut and may read the full one with read_document.
_INSTR_FILE_INLINE_CAP = 40000


# The META-PROMPT. This is the feature's core value: it carries the GENERAL
# craft of writing good Brain-Agent project instructions, so even a one-line
# user intent yields a document with the full structural DNA of a hand-made one.
# The user supplies the INTENT (what the project is for); this supplies the FORM.
_META_SYSTEM_PROMPT = """\
Du bist ein erfahrener Projekt-Architekt für KI-Agenten-Systeme. Deine Aufgabe \
ist es, eine **Projektanweisung** (project instructions) für ein Brain-Agent-Projekt \
zu verfassen — ein einzelnes, in sich geschlossenes Markdown-Dokument.

## Was eine Projektanweisung IST und WOFÜR sie dient
Die Projektanweisung wird bei JEDER Unterhaltung in diesem Projekt automatisch in \
den System-Kontext des Assistenten injiziert. Sie ist die *stehende Arbeitsanweisung*, \
die festlegt, was das Projekt tut, auf welcher Grundlage, nach welcher Methodik und \
in welcher Ergebnisform. Sie wird einmal geschrieben und steuert danach jede einzelne \
Aufgabe im Projekt. Sie ist KEINE einmalige Aufgabenbeschreibung und KEIN Bericht — \
sie ist die wiederverwendbare Spielanleitung, mit der der Assistent jeden konkreten \
Fall (jeden Kunden, jedes Dokument, jede Anfrage) konsistent bearbeitet.

## Eigenschaften einer OPTIMALEN Projektanweisung
1. **Ziel & Liefergegenstand zuerst** — Was produziert das Projekt, für wen, und \
   in welchem EXAKTEN Ausgabeformat (Dateityp, Aufbau)? Das ist der wichtigste \
   Hebel für konsistente Ergebnisse über alle späteren Unterhaltungen hinweg.
2. **Referenz- & Grundlagen-Dokumente explizit benennen** — Jede beigelegte Datei, \
   jeden eingelesenen Ordner und jede Web-Quelle namentlich auflisten UND angeben, \
   WOFÜR sie verwendet wird (nicht nur „siehe Anhang"). Der Assistent muss in einer \
   späteren Unterhaltung wissen, welche Quelle welche Frage beantwortet.
3. **Methodik als wiederholbaren Schritt-für-Schritt-Prozess** — Datenerhebung → \
   Analyse → Bewertung/Synthese → Ergebniserstellung. Konkret und nummeriert, \
   sodass jeder Durchlauf reproduzierbar ist.
4. **Datenquellen unterscheiden** — pro Aufgabe vom Nutzer gelieferte Eingaben vs. \
   im Projekt hinterlegtes Referenzwissen vs. Webrecherche — und WANN welche zu \
   nutzen ist. Wenn Recherche zum Umfang gehört, eine konkrete Recherche-Strategie.
5. **Dem Ergebnis eine EXAKTE Vorlage geben** — Abschnitte, Tabellen, Platzhalter \
   in eckigen Klammern, Beispielformulierungen. Der größte Hebel für konsistente \
   Liefergegenstände. Wenn eine Referenz-/Vorlagendatei vorliegt, deren Struktur \
   exakt übernehmen.
6. **Rolle, Werkzeuge, Qualitätssicherung & Glossar** — die Rolle des Assistenten, \
   welche Werkzeuge er nutzen soll (read_document für beigelegte Dateien, \
   mempalace_query für Projektwissen, web_fetch/Suche für Recherche), \
   Plausibilitäts-/Vollständigkeitsregeln und ein Glossar der Fachbegriffe.
7. **In sich geschlossen und in der Sprache der Quellen/des Auftrags** verfasst.

## Wie du vorgehst
- Der Inhalt der beigelegten Referenz-/Begleitdateien ist dir unten bereits \
  DIREKT eingefügt — nutze ihn als verbindliche Grundlage. Wenn eine Referenz-\
  Ergebnisdatei vorliegt, ist deren Aufbau die verbindliche Vorlage für \
  Abschnitt 5 (Ergebnisstruktur). Nur wenn eine Datei als „gekürzt" oder „nicht \
  inline extrahierbar" markiert ist, lies sie bei Bedarf mit `read_document` \
  (rufe dasselbe Dokument NICHT mehrfach auf).
- WENN das Projekt eingelesene Ordner/Web-Quellen hat, frage relevantes Wissen \
  GEZIELT mit `mempalace_query` ab (wenige, fokussierte Abfragen), um Grundlagen \
  korrekt wiederzugeben. Optional kannst du mit Web-Suche/`web_fetch` ergänzen.
- Verliere dich NICHT in Werkzeugaufrufen — nach maximal wenigen Lese-/Abfrage-\
  schritten schreibe die vollständige Projektanweisung als finale Textantwort.

## Ausgabe
Gib AUSSCHLIESSLICH das fertige Markdown-Dokument aus — keine Vorrede, keine \
Erklärung, kein Codeblock-Zaun darum. Beginne direkt mit der Überschrift (# …). \
Das Dokument muss umfassend und sofort als Projektanweisung verwendbar sein."""


def _project_sources_preamble(agent_id: str, project_name: str, project_cfg: dict,
                              user_id: str) -> str:
    """Build the user-message preamble that tells the model exactly which sources
    this project has: reference/instruction files (with disk paths for
    read_document), ingested input folders, and project web URLs — plus a short
    peek at the project wing so it knows mempalace_query is worthwhile."""
    lines = []

    # 1) Instruction/reference files — INLINE their extracted content directly.
    # These are the reference/template the document must follow exactly, so we
    # hand them to the model up front (extracted server-side via the shared
    # doc pipeline) instead of relying on read_document — which made the model
    # loop re-reading the same large file instead of writing. Capped per file so
    # a huge reference can't blow the context; the model is told if it was cut
    # and may read the full file with read_document on demand.
    instr_files = project_cfg.get("instruction_files") or []
    if instr_files:
        idir = _brain.ProjectManager._instruction_files_dir(agent_id, project_name)
        for f in instr_files:
            if not isinstance(f, dict):
                continue
            fn = f.get("filename") or ""
            if not fn:
                continue
            path = os.path.join(idir, fn)
            if not os.path.exists(path):
                continue
            try:
                text, kind = _brain.extract_attachment_text(path)
            except Exception:
                text, kind = "", "unsupported"
            if kind == "text" and (text or "").strip():
                body = text.strip()
                truncated = ""
                if len(body) > _INSTR_FILE_INLINE_CAP:
                    body = body[:_INSTR_FILE_INLINE_CAP]
                    truncated = (f"\n\n[… gekürzt auf {_INSTR_FILE_INLINE_CAP:,} Zeichen — "
                                 f"vollständige Datei bei Bedarf mit read_document lesen: {path}]")
                lines.append(f"## Beigelegte Referenzdatei: {fn}")
                lines.append("```")
                lines.append(body + truncated)
                lines.append("```")
                lines.append("")
            else:
                # Binary/media/unsupported — give the path so read_document can try.
                lines.append(f"## Beigelegte Referenzdatei: {fn} "
                             f"(Inhalt nicht inline extrahierbar — bei Bedarf mit "
                             f"read_document lesen: {path})")
                lines.append("")

    # 2) Ingested input folders (mined into the project wing).
    folders = project_cfg.get("input_folders") or []
    folder_names = [
        (fo.get("path") or fo.get("name") or "") for fo in folders
        if isinstance(fo, dict)
    ]
    folder_names = [fn for fn in folder_names if fn]
    if folder_names:
        lines.append("## Eingelesene Quellordner (Inhalt via mempalace_query abfragbar):")
        for fn in folder_names:
            lines.append(f"  - {fn}")
        lines.append("")

    # 3) Project web URLs (mined into the wing as well).
    web_urls = project_cfg.get("web_urls") or []
    if web_urls:
        lines.append("## Hinterlegte Web-Quellen:")
        for u in web_urls:
            if not isinstance(u, dict):
                continue
            url = u.get("url") or ""
            title = u.get("title") or ""
            if url:
                lines.append(f"  - {url}" + (f" ({title})" if title else ""))
        lines.append("")

    # 4) A short wing peek so the model sees there IS ingested knowledge to query
    # (only if there are mined sources; the query force-scopes to the project).
    if folder_names or web_urls:
        try:
            with request_context(project=project_name, current_agent=agent_id,
                                 current_user_id=user_id):
                import json as _json
                raw = _brain.tool_mempalace_query(
                    {"query": _WING_PEEK_QUERY, "n_results": _WING_PEEK_N})
            data = _json.loads(raw)
            drawers = (data or {}).get("drawers", []) if isinstance(data, dict) else []
            if drawers:
                lines.append("## Auszug aus dem Projektwissen (zur Orientierung — "
                             "weitere Abfragen via mempalace_query):")
                for d in drawers:
                    src = os.path.basename((d.get("source_file") or "Quelle").rstrip("/"))
                    snippet = (d.get("text") or "").strip().replace("\n", " ")[:240]
                    if snippet:
                        lines.append(f"  - [{src}] {snippet}…")
                lines.append("")
        except Exception:
            pass

    return "\n".join(lines).strip()


def _resolve_model() -> str:
    """The DEDICATED instruction-generation model (Service-Modelle ->
    'Projektanweisungen (KI-Generierung)'). Falls back to the background default
    only if the dedicated slot is empty/unavailable, so the feature still works
    out of the box before an admin sets it."""
    try:
        m = (_brain._server_config().get("instruction_gen_model") or "").strip()
        if m and _brain._is_model_available(m):
            return m
    except Exception:
        pass
    return _brain._background_model_default()


def _run_generation(*, gen_id: str, agent_id: str, project_name: str,
                    user_prompt: str, user_id: str):
    """Daemon-thread body: build preamble → agentic generate → store result_md →
    flip row to ready/error. Cooperative cancel between phases."""
    def _cancelled():
        return ChatDB.instruction_gen_cancelled(gen_id)

    try:
        if _cancelled():
            ChatDB.update_instruction_gen(gen_id, status="cancelled", phase="")
            return

        project_cfg = _brain.ProjectManager.get_project(agent_id, project_name) or {}

        model = _resolve_model()
        if not model:
            ChatDB.update_instruction_gen(
                gen_id, status="error",
                error="Kein Modell verfügbar — bitte unter Service-Modelle ein "
                      "Modell für 'Projektanweisungen (KI-Generierung)' setzen.")
            return

        ChatDB.update_instruction_gen(gen_id, phase="gathering", model=model)
        _push_step(gen_id, "phase", f"Sammelt Projektquellen (Modell: {model})")
        preamble = _project_sources_preamble(agent_id, project_name, project_cfg, user_id)

        display = project_cfg.get("name") or project_name
        existing = (project_cfg.get("instructions") or "").strip()

        parts = [f"# Projekt: {display}", ""]
        parts.append("## Auftrag des Nutzers (Ziel/Intent der Projektanweisung):")
        parts.append(user_prompt.strip() or "(kein zusätzlicher Auftrag angegeben)")
        parts.append("")
        if preamble:
            parts.append(preamble)
            parts.append("")
        if existing:
            # Give the model the existing instructions as a starting point to
            # refine/extend rather than ignore (the user may regenerate to improve).
            parts.append("## Bisherige Projektanweisung (als Ausgangsbasis, darf "
                         "überarbeitet/erweitert werden):")
            parts.append(existing[:8000])
            parts.append("")
        parts.append("Erstelle jetzt die vollständige Projektanweisung als Markdown.")
        user_msg = "\n".join(parts)

        if _cancelled():
            ChatDB.update_instruction_gen(gen_id, status="cancelled", phase="")
            return

        ChatDB.update_instruction_gen(gen_id, phase="writing")
        _push_step(gen_id, "phase", "Liest Quellen und verfasst die Projektanweisung …")
        # Pre-mint the turn_id so the cancel endpoint can stop the in-flight
        # agentic loop via the sidecar (the cooperative DB flag alone can't
        # interrupt the blocking call).
        turn_id = uuid.uuid4().hex
        with _PROGRESS_LOCK:
            _TURN_IDS[gen_id] = turn_id
        from handlers import sidecar_proxy
        result = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": user_msg}],
            model=model,
            system_prompt=_META_SYSTEM_PROMPT,
            # Dedicated purpose → admin-configurable read+research tool set so the
            # agentic loop reads the reference files + queries the project wing/KG
            # + may web-search, exactly like the manual chat that produced the gold
            # doc. No write/exec/email tools (configurable in the tool matrix).
            purpose=_PURPOSE,
            cost_purpose="instruction_gen",
            agent_id=agent_id,
            session_id=f"{_SESSION_PREFIX}{gen_id}",
            project=project_name,
            user_id=user_id,
            max_rounds=_MAX_ROUNDS,
            turn_id=turn_id,
        )
        with _PROGRESS_LOCK:
            _TURN_IDS.pop(gen_id, None)
        if result.get("cancelled") or _cancelled():
            _push_step(gen_id, "info", "Generierung abgebrochen.")
            ChatDB.update_instruction_gen(gen_id, status="cancelled", phase="")
            return
        if result.get("error"):
            _push_step(gen_id, "error", f"Fehler: {str(result['error'])[:160]}")
            ChatDB.update_instruction_gen(
                gen_id, status="error", error=str(result["error"])[:500])
            return
        reply = (result.get("reply") or "").strip()
        # Strip an accidental ```markdown fence if the model wrapped the doc.
        if reply.startswith("```"):
            nl = reply.find("\n")
            if nl != -1:
                reply = reply[nl + 1:]
            if reply.rstrip().endswith("```"):
                reply = reply.rstrip()[:-3].rstrip()
        if not reply:
            _push_step(gen_id, "error", "Das Modell lieferte eine leere Ausgabe.")
            ChatDB.update_instruction_gen(
                gen_id, status="error", error="Das Modell lieferte eine leere Ausgabe.")
            return

        if _cancelled():
            ChatDB.update_instruction_gen(gen_id, status="cancelled", phase="")
            return

        _push_step(gen_id, "phase", f"Fertig — {len(reply):,} Zeichen erzeugt.")
        ChatDB.update_instruction_gen(
            gen_id, status="ready", phase="", result_md=reply)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            _push_step(gen_id, "error", f"{type(e).__name__}: {e}"[:160])
            ChatDB.update_instruction_gen(
                gen_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def request_cancel(gen_id: str) -> bool:
    """Signal cancellation: set the cooperative DB flag AND ask the sidecar to
    stop the in-flight agentic loop (via the stored turn_id). Returns True if a
    turn was actively cancelled (best-effort)."""
    ChatDB.cancel_instruction_gen(gen_id)
    _push_step(gen_id, "info", "Abbruch angefordert …")
    turn_id = None
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


def start_generation(*, agent_id: str, project: dict, user_prompt: str,
                     user_id: str) -> str:
    """Insert a generating row + spawn the agentic worker. Returns the gen_id.
    Caller has already validated project membership."""
    gen_id = uuid.uuid4().hex
    project_id = project.get("id") or ""
    project_name = project.get("folder_name") or project.get("name") or ""
    ChatDB.create_instruction_gen(gen_id, agent_id, project_id, user_prompt or "", user_id)
    threading.Thread(
        target=_run_generation,
        kwargs={
            "gen_id": gen_id, "agent_id": agent_id, "project_name": project_name,
            "user_prompt": user_prompt or "", "user_id": user_id,
        },
        daemon=True, name=f"instr_gen_{gen_id[:8]}").start()
    return gen_id
