# AskUser / ask_llm tool bodies (extracted from brain.py, E4).
#
# Four tools that pause the agentic loop to ask the user (or a one-shot LLM):
#   - ask_user          — main-loop question(s); blocks on the session's
#                         pending-answer slot, unblocked by POST /v1/chat/answer.
#   - ask_user_for_file — pause for a file upload; blocks on the workflow/
#                         session pending-file slot, delivered by the upload
#                         endpoint.
#   - ask_llm           — one-shot LLM text-in/text-out via the sidecar (no
#                         loop, no tools).
#   - agent_step        — ONE bounded agentic turn (background_call with the
#                         'workflow_step' purpose toolset) — the workflow
#                         "plan is the program" execution primitive.
#
# ⚠️ BLOCKING STATE STAYS IN BRAIN. The pending-answer / pending-file registries
# and their lifecycle helpers MUST remain single-instance in brain.py because
# the HTTP delivery side mutates them under brain-held locks:
#   - `_ask_user_pending` / `_ask_user_lock` + `_ask_user_register` /
#     `_ask_user_clear` / `deliver_ask_user_answer` (handlers/chat.py,
#     handlers/admin_artifacts.py call `brain.deliver_ask_user_answer`).
#   - `_workflow_file_pending` / `_workflow_file_lock` + `_workflow_file_register`
#     / `_workflow_file_clear` / `deliver_workflow_file_answer`
#     (handlers/admin_workflows.py).
# The moved tool bodies reach those via `import brain as _brain` — only the
# `tool_*` functions and the pure `_normalize_ask_questions` helper move here.
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `get_request_context` from engine.context (event_callback, session/worker ids).
#   - brain runtime symbols (the blocking-state helpers above, `AgentConfig`,
#     `_load_tools_config`, `_current_agent`, `_delegate_fallback_model`)
#     reached lazily via `import brain as _brain`. NO top-level `import brain`.
#
# brain.py re-exports all 4 tools + `_normalize_ask_questions` via
# `from engine.tools.ask_tools import (...)`.

from __future__ import annotations

import time

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


def _ask_turn_cancelled() -> bool:
    """True when the turn this blocking tool runs in was cancelled — the user's
    Stopp must unblock a waiting ask_user immediately, not after its timeout.
    Two cancel channels, mirroring the loop's own is_cancelled sources:
    background (blocking) turns register a per-turn Event in sidecar_proxy;
    interactive turns carry the session's CancelToken."""
    ctx = get_request_context()
    turn_id = getattr(ctx, "current_turn_id", "") or ""
    if turn_id:
        try:
            from handlers.sidecar_proxy import is_turn_cancelled
            if is_turn_cancelled(turn_id):
                return True
        except Exception:
            pass
    sid = ctx.current_session_id or ""
    if sid:
        try:
            import sys as _sys
            _srv = _sys.modules.get("__main__") or _sys.modules.get("server")
            _sessions = getattr(_srv, "sessions", None) if _srv else None
            s = _sessions.peek(sid) if _sessions is not None else None
            tok = getattr(s, "cancel_token", None) if s is not None else None
            if tok is not None and getattr(tok, "cancelled", False):
                return True
        except Exception:
            pass
    return False


def _wait_answer_or_cancel(event, timeout: float) -> tuple[bool, bool]:
    """Block on the pending-answer Event, polling the turn's cancel state ~1×/s.
    Returns (answered, cancelled) — (False, False) means a plain timeout."""
    deadline = time.time() + max(1.0, float(timeout))
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return False, False
        if event.wait(timeout=min(1.0, remaining)):
            return True, False
        if _ask_turn_cancelled():
            return False, True


def _normalize_ask_questions(args: dict):
    """Return (questions_list, is_batch) from args.

    - `questions: [{question, options?}, ...]` → batch
    - `question: "..."` (+ optional `options`) → single, wrapped
    Returns ([], False) on invalid input.
    """
    qs = args.get("questions")
    if isinstance(qs, list) and qs:
        out = []
        for q in qs:
            if isinstance(q, dict) and q.get("question"):
                out.append({
                    "question": str(q["question"]),
                    "options": q.get("options") if isinstance(q.get("options"), list) else None,
                })
        return out, True
    q = args.get("question")
    if isinstance(q, str) and q:
        return [{"question": q, "options": args.get("options") if isinstance(args.get("options"), list) else None}], False
    return [], False


def tool_ask_llm(args: dict) -> str:
    """One-shot LLM call without an agentic loop. For workflows / chats that need
    a quick text-in / text-out transformation. No tools, no memory, no streaming.

    Args:
        prompt: user message
        system: optional system prompt (defaults to a generic helpful assistant)
        model:  model id (defaults to refinement.model from tools_config.json,
                falling back to the agent's preferred model)
    """
    import brain as _brain
    prompt = args.get("prompt") or ""
    if not prompt:
        return _err("ask_llm: 'prompt' is required")
    system_prompt = args.get("system") or (
        "You are a helpful assistant. Answer concisely and directly."
    )
    model = args.get("model") or ""
    # Resolution order: explicit kwarg → workflow MODEL header → workflow AGENT.preferred_model
    # → refinement model → current agent → server fallback.
    if not model:
        model = (get_request_context().workflow_default_model or "").strip()
    if not model:
        wf_agent = get_request_context().workflow_agent_id or ""
        if wf_agent:
            try:
                _ag = _brain.AgentConfig(wf_agent)
                model = (_ag.preferred_model or "").strip()
            except Exception:
                pass
    if not model:
        try:
            tcfg = _brain._load_tools_config() or {}
            model = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        except Exception:
            model = ""
    if not model:
        agent = get_request_context().current_agent or _brain._current_agent
        if agent:
            model = agent.preferred_model or ""
    if not model:
        model = _brain._delegate_fallback_model or ""
    if not model:
        return _err("ask_llm: no model configured")
    # Inherit the workflow's synthetic session_id ("wf-<execution_id>") so the
    # cost log can attribute this LLM call back to the workflow run.
    sid = get_request_context().current_session_id or ""
    try:
        from handlers import sidecar_proxy as _sidecar_proxy
        _agent_id = ""
        _ag = get_request_context().current_agent or _brain._current_agent
        if _ag is not None:
            _agent_id = getattr(_ag, "agent_id", "") or ""
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt=system_prompt,
            agent_id=(_agent_id or "main"),
            session_id=(sid or ""),
            cost_purpose="ask_llm",
        )
        text = _res.get("reply") or ""
        return _ok({"text": text.strip(), "model": model})
    except Exception as e:
        return _err(f"ask_llm: {e}")


def tool_agent_step(args: dict) -> str:
    """Run ONE bounded agentic LLM turn as a workflow step ("Der Plan ist das
    Programm"): the .flow script stays the deterministic spine (order, inputs,
    report), while judgment-heavy plan steps run agentically with tools.

    Args:
        instruction:     what this step must do (required; may be a whole plan
                         section or the entire plan)
        plan:            optional full plan markdown, injected as context so a
                         single step knows the overall method
        files:           optional list of input file paths (uploads from
                         ask_user_for_file, outputs of earlier steps)
        model:           model id (defaults to workflow MODEL header, then the
                         background default)
        max_rounds:      agentic round cap (default 16, hard cap 24)
        expected_output: optional description of the required result shape

    Returns: {text, model, rounds, files} — `files` lists paths written by the
    step's file tools so later steps / artifact seeding can pick them up.
    """
    import os as _os
    import uuid as _uuid
    import brain as _brain
    instruction = (args.get("instruction") or "").strip()
    if not instruction:
        return _err("agent_step: 'instruction' is required")
    plan = (args.get("plan") or "").strip()
    # skill="slug": load a saved SKILL.md as the method for this step. Resolves
    # the workflow OWNER's visible skills (built-in first, then their per-user +
    # shared skills, ACL-gated via ctx.current_user_id — WorkflowExecution sets
    # it to the run's user_id). The skill body becomes the plan (or is prepended
    # to an explicit plan=), so the executor follows the skill's procedure. This
    # is how a workflow "uses a skill": the method lives ONCE in the skill and
    # any workflow references it, instead of duplicating a plan.md per workflow.
    skill_slug = (args.get("skill") or "").strip()
    if skill_slug:
        _ctx0 = get_request_context()
        _agent = _ctx0.current_agent or _brain._current_agent
        _skill_body = None
        if _agent:
            _skill_body = _agent.load_skill(skill_slug)
            if _skill_body is None:
                _uid = _ctx0.current_user_id or ""
                _u = None
                if _uid:
                    try:
                        from server_lib.auth import AuthDB as _AuthDB
                        _u = _AuthDB.get_user(_uid)
                    except Exception:
                        _u = None
                _skill_body = _agent.load_user_skill_body(skill_slug, _u)
        if _skill_body is None:
            return _err(f"agent_step: skill '{skill_slug}' not found or not "
                        f"accessible to this workflow's owner")
        # Skill body is the method. If an explicit plan= was also given, the
        # skill leads and the plan follows as extra context.
        plan = (_skill_body + ("\n\n## Zusätzlicher Plan-Kontext\n" + plan
                               if plan else "")).strip()
    expected_output = (args.get("expected_output") or "").strip()
    files = args.get("files") or []
    if isinstance(files, str):
        files = [files]
    files = [str(f) for f in files if f]
    try:
        max_rounds = int(args.get("max_rounds") or 16)
    except (TypeError, ValueError):
        max_rounds = 16
    max_rounds = max(1, min(max_rounds, 24))

    ctx = get_request_context()
    execution_id = ctx.workflow_execution_id or ""
    # Model resolution: explicit arg → workflow MODEL header → background default.
    model = (args.get("model") or "").strip() or (ctx.workflow_default_model or "").strip()
    if not model:
        try:
            model = _brain._background_model_default()
        except Exception:
            model = ""
    if not model:
        return _err("agent_step: no model configured (set a MODEL header or pass model=)")

    # Shared workspace = the run's session artifact folder: agent_step sets NO
    # working_dir, so the executor's relative file writes fall through
    # _resolve_artifact_dir to the wf-<exec_id> artifact folder — the SAME
    # folder the .flow-level write_file uses. One folder per run: later steps
    # (and the verify step) see earlier steps' files, and everything surfaces
    # in the artifact panel. (A /tmp workspace here split the run's files in
    # two locations — the verify step couldn't find the report.)
    parts = []
    if plan:
        parts.append("## Gesamtplan (Kontext)\n" + plan)
    parts.append("## Auftrag dieses Schritts\n" + instruction)
    if files:
        parts.append("## Eingabedateien (mit Werkzeugen lesen/verarbeiten)\n"
                     + "\n".join(f"- {f}" for f in files))
    parts.append("## Arbeitsordner\nSchreibe Ausgabedateien mit RELATIVEN "
                 "Dateinamen (nur `name.ext`) — sie landen im Artifact-Ordner "
                 "dieses Laufs, wo auch die Dateien früherer Schritte liegen.")
    if expected_output:
        parts.append("## Erwartetes Ergebnis\n" + expected_output)
    user_msg = "\n\n".join(parts)

    # Vision: image inputs become native image_url blocks when the model
    # accepts their MIME (raw_formats match, same gate as chat attachments +
    # the MoA executor pick) — a plan step like "Bild visuell inspizieren"
    # needs the model to SEE the image, not just know its path. Non-image or
    # unsupported files stay path-only (read_document territory).
    user_content = user_msg
    img_files = []
    if files:
        import base64 as _b64
        import mimetypes as _mt
        for f in files:
            mime = (_mt.guess_type(f)[0] or "")
            try:
                if (mime.startswith("image/") and _os.path.isfile(f)
                        and _os.path.getsize(f) < 20 * 1024 * 1024):
                    img_files.append((f, mime))
            except OSError:
                continue
        if img_files and _brain.model_supports_mimes(
                model, [m for _, m in img_files]):
            blocks = [{"type": "text", "text": user_msg}]
            for f, mime in img_files:
                try:
                    with open(f, "rb") as fh:
                        b64 = _b64.b64encode(fh.read()).decode()
                    blocks.append({"type": "image_url",
                                   "image_url": {"url": f"data:{mime};base64,{b64}"}})
                except OSError:
                    continue
            if len(blocks) > 1:
                user_content = blocks

    system_prompt = (
        "Du bist ein Ausführungs-Agent innerhalb eines automatisierten Workflows. "
        "Arbeite den Auftrag dieses Schritts vollständig und methodisch ab; nutze "
        "die verfügbaren Werkzeuge, statt Ergebnisse zu erfinden. Was du nicht "
        "prüfen kannst, kennzeichne explizit als nicht prüfbar. Es gibt keinen "
        "Nutzer, der Rückfragen beantwortet — triff begründete Annahmen und "
        "dokumentiere sie. WICHTIG: Dein Runden-Budget ist begrenzt "
        f"({max_rounds} Werkzeug-Runden) — teile es so ein, dass am Ende sicher "
        "eine VOLLSTÄNDIGE, STRUKTURIERTE Abschlussantwort steht: dein gesamter "
        "Antworttext wird als Liefergebnis dieses Schritts übernommen, also "
        "keine Zwischenkommentare wie 'Nun führe ich Schritt X durch' — halte "
        "Zwischentexte auf ein Minimum und schreibe konkrete Befunde/Zahlen "
        "SOFORT in Ergebnisdateien, nicht nur in deinen Kopf. Die letzte "
        "Antwort muss das Ergebnis selbst enthalten (alle Befunde, Werte, "
        "Bewertungen, Gesamturteil), nicht eine Ankündigung davon."
    )

    turn_id = f"wfstep-{execution_id or 'chat'}-{_uuid.uuid4().hex[:8]}"
    execution = _brain.workflow_get_execution(execution_id) if execution_id else None
    # Live progress: surface each round's tool activity + streamed text in the
    # run view WHILE the step runs (it's a blocking call that would otherwise
    # look frozen for its whole duration). The emit callback feeds the
    # execution's live-progress transcript turn, which the run poll picks up.
    _emit = None
    if execution is not None:
        execution.register_step_turn(turn_id)
        try:
            # Label = the step's own instruction (first line / excerpt) so the
            # run view shows WHICH step is running, not just "a step".
            _first = (instruction or "").strip().splitlines()[0] if instruction else ""
            _label = _first[:140] + ("…" if len(_first) > 140 else "") if _first \
                else "Führt den Workflow-Schritt aus …"
            execution.begin_live_progress(_label)
            _emit = execution.live_progress_event
        except Exception:
            _emit = None
    try:
        from handlers import sidecar_proxy as _sidecar_proxy
        res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": user_content}],
            model=model,
            system_prompt=system_prompt,
            purpose="workflow_step",
            cost_purpose="workflow_step",
            agent_id=(ctx.workflow_agent_id or "main"),
            session_id=(ctx.current_session_id or ""),
            user_id=(ctx.current_user_id or None),
            max_rounds=max_rounds,
            turn_id=turn_id,
            emit=_emit,
        )
    except Exception as e:
        if execution is not None:
            try: execution.end_live_progress()
            except Exception: pass
        return _err(f"agent_step: {e}")
    finally:
        if execution is not None:
            execution.unregister_step_turn(turn_id)
    # Hard failures (real error / cancel): drop the live turn, fail the step.
    if res.get("cancelled"):
        if execution is not None:
            try: execution.end_live_progress()
            except Exception: pass
        return _err("agent_step: cancelled")
    if res.get("error"):
        if execution is not None:
            try: execution.end_live_progress()
            except Exception: pass
        return _err(f"agent_step: {res['error']}")
    text = (res.get("reply") or "").strip()
    hit_cap = (res.get("stop_reason") or "") == "max_rounds"
    # Empty final reply: NOT necessarily a failure — the step may have hit its
    # round budget mid-tool-loop (tools DID run, files may exist). Salvage it
    # instead of failing the WHOLE workflow (the '429/round-limit → No response'
    # dead-end). Only a truly empty AND uncapped turn (no tools, no cap) fails.
    tool_evs = res.get("tool_events") or []
    if not text:
        if hit_cap or tool_evs:
            text = ("_Kein abschließender Antworttext — der Schritt hat die "
                    "Werkzeuge ausgeführt, aber das Runden-/Zeitbudget vor einer "
                    "Zusammenfassung erreicht. Die Zwischenergebnisse und "
                    "geschriebenen Dateien liegen vor._")
        else:
            if execution is not None:
                try: execution.end_live_progress()
                except Exception: pass
            return _err("agent_step: model returned an empty reply")
    # Collect files written by the step's file tools (paths from tool args).
    written: list[str] = []
    _write_tools = {"write_file", "edit_file", "write_document", "edit_document",
                    "xlsx_create", "xlsx_edit", "render_diagram"}
    for ev in tool_evs:
        if ev.get("is_error") or ev.get("name") not in _write_tools:
            continue
        p = ((ev.get("args") or {}).get("path") or "").strip()
        if p and p not in written:
            written.append(p)
    display_text = text
    # Surface a hit round-cap so downstream steps (verify) / the run log can
    # tell "finished" from "ran out of budget mid-plan".
    if hit_cap:
        display_text += ("\n\n[Hinweis: Runden-Limit erreicht — das Ergebnis "
                         "ist möglicherweise unvollständig.]")
    # FREEZE the live progress turn into the completed answer turn — keeps the
    # tool calls/thinking/streamed text the user watched VISIBLE, and appends the
    # final answer. Returns True when it took over the transcript turn, so the
    # interpreter's _capture_transcript must NOT record a second assistant turn.
    finalized = False
    if execution is not None:
        try:
            finalized = execution.finalize_live_progress(
                text=display_text, model=model, files=written)
        except Exception:
            finalized = False
    out = {
        "text": text,
        "display_text": display_text,
        "model": model,
        "rounds": int(res.get("rounds") or 0),
        "files": written,
        "_transcript_done": finalized,
    }
    if hit_cap:
        out["stop_reason"] = "max_rounds"
    return _ok(out)


def tool_ask_user_for_file(args: dict) -> str:
    """Pause and request a file upload from the user.

    Routes the request via the active event_callback as a 'file_upload_needed' SSE event,
    then blocks on a pending-file slot keyed by execution_id (workflow) or session_id (chat).
    Frontend uploads the file to /v1/workflows/upload-pending which delivers the answer.
    """
    import brain as _brain
    prompt = args.get("prompt") or "Please upload a file"
    accept = args.get("accept") or ""
    timeout = int(args.get("timeout_seconds", 600))

    # Prefer workflow execution_id; fall back to session_id for chat use.
    execution_id = get_request_context().workflow_execution_id
    session_id = get_request_context().current_session_id
    key = execution_id or session_id
    if not key:
        return _err("ask_user_for_file requires an active workflow execution or chat session")

    cb = get_request_context().event_callback
    if cb:
        try:
            cb("file_upload_needed", {
                "execution_id": execution_id or "",
                "session_id": session_id or "",
                "prompt": prompt,
                "accept": accept,
                "timeout_seconds": timeout,
            })
        except Exception:
            pass

    event = _brain._workflow_file_register(key)
    try:
        got, was_cancelled = _wait_answer_or_cancel(event, timeout)
        if was_cancelled:
            return _err("ask_user_for_file: turn was cancelled while waiting")
        if not got:
            return _err("ask_user_for_file: timed out waiting for upload")
        with _brain._workflow_file_lock:
            slot = _brain._workflow_file_pending.get(key) or {}
            payload = slot.get("result")
        if not payload:
            return _err("ask_user_for_file: cancelled by user")
        return _ok({
            "path": payload.get("path", ""),
            "filename": payload.get("filename", ""),
            "size_bytes": int(payload.get("size_bytes") or 0),
        })
    finally:
        _brain._workflow_file_clear(key)


def tool_ask_user(args: dict) -> str:
    """Ask the user one or more questions from the main chat loop (not inside a worker)."""
    import brain as _brain
    _ctx = get_request_context()
    session_id = _ctx.current_session_id
    if not session_id:
        return _err("ask_user requires an active session")
    questions, is_batch = _normalize_ask_questions(args)
    if not questions:
        return _err("question or questions is required")
    context_summary = args.get("context_summary", "")
    timeout = int(args.get("timeout_seconds", 300))

    # A DETACHED background task (sub-agent) has no live SSE channel — the turn
    # that spawned it is long finished, so `event_callback` emits into nothing and
    # the question was invisible while the sub-agent blocked to its timeout
    # (user-reported: "einer der Subagenten hat ask_user aufgerufen, das geht
    # komplett unter"). Two changes for that path:
    #   * key the pending slot on the TASK id, not the session id — several
    #     sub-agents can block at once, and none of them may hijack the spawning
    #     chat's own ask_user slot;
    #   * PERSIST the question on the task row, so the 3s subagent poller the UI
    #     already runs can surface it and route an answer back by task id.
    task_id = (_ctx.current_bg_task_id or "") if _ctx.current_bg_task else ""
    key = task_id or session_id

    payload = {
        "session_id": session_id,
        "questions": questions,
        "context_summary": context_summary,
        "timeout_seconds": timeout,
    }
    if not is_batch:
        # Keep legacy fields for single-question consumers
        payload["question"] = questions[0]["question"]
        payload["options"] = questions[0]["options"]

    # The live channel exists only for an interactive turn; a background task's
    # `event_callback` is None (nothing is listening any more).
    cb = None if task_id else _ctx.event_callback
    if task_id:
        payload["task_id"] = task_id
        payload["asked_at"] = time.time()
        try:
            from server_lib.db import ChatDB
            ChatDB.set_background_task_question(task_id, payload)
        except Exception:
            pass  # best-effort: still block + honour a direct answer
    elif cb:
        try:
            cb("user_input_needed", payload)
        except Exception:
            pass

    event = _brain._ask_user_register(key)
    try:
        got, was_cancelled = _wait_answer_or_cancel(event, timeout)
        if was_cancelled:
            return _err("ask_user: turn was cancelled while waiting for an answer")
        if not got:
            return _err("No answer received (timed out)")
        with _brain._ask_user_lock:
            slot = _brain._ask_user_pending.get(key) or {}
            answers_map = slot.get("answers")
            answer_str = slot.get("answer")
        if is_batch:
            if not isinstance(answers_map, dict) or not answers_map:
                return _err("No answers received")
            if cb:
                try:
                    cb("user_input_received", {"session_id": session_id, "answers": answers_map})
                except Exception:
                    pass
            return _ok({"answers": answers_map})
        # Single-question path. Prefer explicit answer, fall back to first value in answers map.
        if answer_str is None and isinstance(answers_map, dict) and answers_map:
            answer_str = next(iter(answers_map.values()))
        if answer_str is None:
            return _err("No answer received")
        if cb:
            try:
                cb("user_input_received", {"session_id": session_id, "answer": answer_str})
            except Exception:
                pass
        return _ok({"answer": answer_str})
    finally:
        _brain._ask_user_clear(key)
        if task_id:
            # Clear the persisted question on EVERY exit — answered, timed out or
            # errored — so a dead question can't linger as a card in the UI.
            try:
                from server_lib.db import ChatDB
                ChatDB.set_background_task_question(task_id, None)
            except Exception:
                pass
