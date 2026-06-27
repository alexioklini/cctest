"""engine/code_init.py — Code Mode "init": summarize a project's working
directory into a BRAIN.md at its root.

Mirrors what Claude Code's /init does: ONE agentic turn (full file/code tools,
cwd = the project's working_dir) that explores the tree — reads README/configs/
entry points, lists structure — and writes a CLAUDE.md-style summary. We name it
BRAIN.md so it never clobbers a real CLAUDE.md. The file is plain markdown and is
NEVER mined; the system-prompt builder injects it verbatim as the project memory
for code-mode projects.

The turn runs through sidecar_proxy.background_call(purpose="interactive") inside
a project-scoped request context: apply_domain_context sets working_dir +
excludes the MemPalace tools, so the file tools write into working_dir and the
agent can't reach project memory (there is none in code mode).
"""

from __future__ import annotations

import threading
import time
import uuid

from engine.context import request_context

# Lazy brain proxy (avoid import cycle — brain imports engine modules).
import brain as _brain  # noqa: E402  (safe: code_init isn't imported at brain import time)


_INIT_PROMPT = (
    "Erkunde das aktuelle Arbeitsverzeichnis (dein cwd) und schreibe eine "
    "knappe BRAIN.md im Wurzelverzeichnis.\n\n"
    "WICHTIG — Arbeitsteilung mit dem Code-Index: Dieses Projekt hat einen "
    "automatisch aktualisierten Code-Index (Werkzeuge code_search/code_trace/"
    "code_query/code_snippet). Der Index ist die FRISCHE Quelle der Wahrheit für "
    "ALLES Strukturelle: welche Dateien/Funktionen/Klassen es gibt, was eine "
    "Datei enthält, wer was aufruft, Importe, Vererbung. BRAIN.md wird nur einmal "
    "geschrieben und veraltet — DUPLIZIERE daher NICHTS, was der Index liefert.\n\n"
    "BRAIN.md enthält AUSSCHLIESSLICH dauerhaftes, NICHT aus dem Code ableitbares "
    "Wissen:\n"
    "- Wozu das Projekt dient (Zweck, Domäne, Ziele).\n"
    "- Tech-Stack/Framework in einem Satz (nicht als Dateiliste).\n"
    "- Wie man baut/testet/startet (Befehle, Einstiegspunkte NACH ABSICHT, z.B. "
    "'API startet in …').\n"
    "- Projektspezifische Konventionen, Invarianten, Stolperfallen, 'mach X nicht' "
    "— also Urteils-/Erfahrungswissen, das nirgends im AST steht.\n\n"
    "VERBOTEN in BRAIN.md (das macht der Index, frisch): Datei-Inventare, "
    "Verzeichnisbäume, Auflistungen von Funktionen/Klassen pro Datei, 'Datei X "
    "enthält a/b/c', Aufruf-/Abhängigkeitsgraphen. Verweise stattdessen auf die "
    "Code-Werkzeuge ('für Struktur/Aufrufer: code_search/code_trace nutzen').\n\n"
    "Vorgehen: mit list/glob/grep + README/package.json/pyproject o.ä. den ZWECK "
    "und die KONVENTIONEN erfassen (du musst nicht jede Datei lesen), dann "
    "BRAIN.md schreiben (write_file, relativer Pfad 'BRAIN.md'). Existiert schon "
    "eine, aktualisiere sie. Halte es prägnant. Antworte am Ende nur mit einer "
    "kurzen Bestätigung."
)

# Per-(agent,project) run state so the UI can show progress + cancel. One entry
# per project; a new run replaces the prior terminal entry. While `status` is
# "generating" a worker thread is in flight and `turn_id` targets its sidecar
# cancel. Terminal states ("done"/"error"/"cancelled") linger so the UI can show
# the outcome until the next run starts. `_lock` guards all of it.
_runs: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()


def is_running(agent_id: str, project_name: str) -> bool:
    with _lock:
        r = _runs.get((agent_id, project_name))
        return bool(r and r.get("status") == "generating")


def get_status(agent_id: str, project_name: str) -> dict | None:
    """Snapshot of the latest run for this project (or None if never run this
    process). Includes `elapsed` (seconds) computed at read time."""
    with _lock:
        r = _runs.get((agent_id, project_name))
        if not r:
            return None
        out = dict(r)
    out.pop("turn_id", None)  # internal; never leak to the client
    started = out.get("started_at") or 0
    ended = out.get("ended_at") or 0
    ref = ended if ended else time.time()
    out["elapsed"] = round(max(0.0, ref - started), 1) if started else 0.0
    return out


def cancel(agent_id: str, project_name: str) -> bool:
    """Request cancellation of an in-flight init. Returns True if a running init
    was found and a cancel was dispatched to the sidecar."""
    with _lock:
        r = _runs.get((agent_id, project_name))
        if not r or r.get("status") != "generating":
            return False
        r["cancel_requested"] = True
        turn_id = r.get("turn_id") or ""
    if turn_id:
        try:
            from handlers import sidecar_proxy
            sidecar_proxy.cancel_turn(turn_id)
        except Exception as e:
            print(f"[code-init] cancel dispatch failed: {type(e).__name__}: {e}",
                  flush=True)
    return True


def run_init(agent_id: str, project_name: str, working_dir: str,
             user_id: str = "", model: str = "") -> bool:
    """Spawn the init worker thread. Returns False if one is already running for
    this project (caller can report 'already in progress')."""
    key = (agent_id, project_name)
    turn_id = uuid.uuid4().hex
    with _lock:
        cur = _runs.get(key)
        if cur and cur.get("status") == "generating":
            return False
        _runs[key] = {
            "status": "generating",
            "started_at": time.time(),
            "ended_at": 0,
            "turn_id": turn_id,
            "working_dir": working_dir,
            "error": "",
            "cancel_requested": False,
        }
    t = threading.Thread(
        target=_worker,
        args=(agent_id, project_name, working_dir, user_id, model, turn_id),
        daemon=True, name=f"code-init-{project_name}")
    t.start()
    return True


def _finish(key: tuple[str, str], status: str, error: str = ""):
    with _lock:
        r = _runs.get(key)
        if not r:
            return
        # A cancel that landed wins over a same-instant 'done'.
        if r.get("cancel_requested") and status != "cancelled":
            status = "cancelled"
        r["status"] = status
        r["ended_at"] = time.time()
        r["error"] = error


def _worker(agent_id: str, project_name: str, working_dir: str,
            user_id: str, model: str, turn_id: str):
    key = (agent_id, project_name)
    try:
        from handlers import sidecar_proxy
        from engine.context import get_request_context
        # Resolve a model: caller override → server default. Init is a normal
        # agentic turn, so the server's default chat model is fine.
        _model = (model or "").strip() or _brain._background_model_default()
        if not _model:
            print(f"[code-init] {agent_id}/{project_name}: no model available", flush=True)
            _finish(key, "error", "no model available")
            return
        # Project-scoped context. apply_domain_context reads the project's
        # code_mode config and sets working_dir (→ file tools cwd) + excludes the
        # MemPalace tools. The current_agent/user are needed for tool dispatch.
        with request_context(project=project_name,
                             current_user_id=user_id or "",
                             current_session_id=f"code-init-{project_name}"):
            get_request_context().current_agent = _brain.AgentConfig(agent_id)
            _brain.apply_domain_context(agent_id=agent_id, project=project_name,
                                        user_id=user_id or "")
            # Pre-minted turn_id so cancel() can target this run's sidecar turn.
            result = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": _INIT_PROMPT}],
                model=_model,
                system_prompt=(
                    "You are a senior engineer documenting a codebase. Work ONLY "
                    "in the given working directory using your file tools; write "
                    "the summary to BRAIN.md at its root."),
                purpose="interactive",
                agent_id=agent_id,
                project=project_name,
                user_id=user_id or "",
                max_rounds=40,
                cost_purpose="code_init",
                turn_id=turn_id,
            ) or {}
        err = (result.get("error") or "").strip()
        if err:
            _finish(key, "error", err)
        else:
            # Init wrote BRAIN.md → build the code-intelligence index for this
            # project now (the BRAIN.md-write trigger). Tenant cache lives under
            # the project dir; same path apply_domain_context points the code_*
            # tools at. Best-effort — index failure must not fail init.
            try:
                import os as _os
                _pcfg = (_brain.ProjectManager(agent_id).get_project(project_name) or {})
                _wd = (_pcfg.get("working_dir") or "").strip()
                _pdir = _pcfg.get("dir") or _brain.ProjectManager._project_dir(agent_id, project_name)
                if _wd and _os.path.isdir(_wd):
                    _cache = _os.path.join(_pdir, ".cbm-cache")
                    _brain.cbm_index_repository(_wd, cache_dir=_cache)
            except Exception as _e:
                print(f"[code-init] {agent_id}/{project_name}: index after BRAIN.md "
                      f"failed: {type(_e).__name__}: {_e}", flush=True)
            _finish(key, "done")
    except Exception as e:
        print(f"[code-init] {agent_id}/{project_name} failed: "
              f"{type(e).__name__}: {e}", flush=True)
        _finish(key, "error", f"{type(e).__name__}: {e}")
