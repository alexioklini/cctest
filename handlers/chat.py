# Extracted from server.py — chat/inference handlers
import base64
import copy
import json
import mimetypes
import os
import queue
import socket
import threading
import time

import brain as engine
import pseudonymizer
from handlers import sidecar_proxy
# Generic SSE wire formatting (re-exported for callers/tests).
from server_lib.sse_stream import KEEPALIVE, encode_sse, format_sse  # noqa: F401


# Human-readable purpose labels for the Auto-routing tooltip.
_AUTO_PURPOSE_LABEL = {
    "fast": "quick task",
    "coding": "coding task",
    "analysis": "analysis task",
    "creative": "creative task",
    "agentic": "tool/agent task",
}


def _parse_auto_directive(model: str | None) -> tuple[bool, str | None]:
    """Normalize a composer model value into (want_auto, pool).

    The composer offers two auto modes that differ only by candidate pool:
      - "auto-cloud" → (True, "cloud")  — pick the best-fitting CLOUD model
      - "auto-local" → (True, "local")  — pick the best-fitting LOCAL model
      - "auto"       → (True, "cloud")  — LEGACY: pre-split single Auto, and any
                       agent still pinned to model="auto"; treated as cloud so
                       stored sessions / agent.json keep working unchanged.
    Anything else → (False, None) (a concrete model pick).
    """
    if model == "auto-local":
        return True, "local"
    if model in ("auto-cloud", "auto"):
        return True, "cloud"
    return False, None


def _auto_route_reason(purpose, attachment_mimes, model: str, analysis=None) -> str:
    """Build a short 'why this model' explanation for the Auto picker tooltip.

    Mirrors the tier logic in brain._resolve_auto_model_tiered so the reason
    matches the actual decision: attachments win, then purpose tier, then the
    picked model's own traits (local / reasoning). When the LLM classifier ran,
    `analysis` carries the richer task_types and we lead with those.
    """
    name = engine.get_model_info(model).get("display_name") \
        or engine.get_model_info(model).get("shortname") or model
    mimes = [m for m in (attachment_mimes or []) if m]
    if mimes:
        vision = any(engine._mime_matches(m, engine.get_model_raw_formats(model)) for m in mimes)
        if vision:
            return f"Attachment can be read natively → {name}"
    # Prefer the structured analysis when present — it names the actual task
    # mix (e.g. "research + reporting") instead of the collapsed legacy label,
    # and the complexity that shifted the tier.
    tt = (analysis or {}).get("task_types") if analysis else None
    if tt:
        cx = (analysis or {}).get("complexity")
        cx_note = f", {cx} complexity" if cx in ("low", "high") else ""
        return f"Detected {' + '.join(tt[:3])}{cx_note} → {name}"
    label = _AUTO_PURPOSE_LABEL.get(purpose or "")
    if purpose in ("coding", "analysis"):
        return f"Detected {label} → reasoning model {name}"
    if purpose == "fast":
        where = "local" if engine.is_model_local(model) else "fastest available"
        return f"Detected {label} → {where} model {name}"
    if label:
        return f"Detected {label} → {name}"
    return f"Best general-purpose model → {name}"

# ---------------------------------------------------------------------------
# Transparent anonymisation — module-level helpers
# ---------------------------------------------------------------------------
#
# The chat worker uses these to:
#   * Persist `anonymise` / `deanonymise_*` rows as synthetic tool-call entries
#     that show in chat history but never reach the LLM (skip session.messages,
#     write directly via ChatDB.save_message).
#   * Block the worker thread while the user picks a recovery action on the
#     "anonymisation failed" modal — mirrors the AskUserQuestion pattern but
#     pre-loop (no sidecar dispatch involved).

# GDPR anonymisation-failure recovery state machine — extracted to
# handlers/gdpr_recovery.py. Re-exported here so the chat worker, the
# POST /v1/chat/gdpr-recovery handler, and tests keep resolving these names.
from handlers.gdpr_recovery import (  # noqa: E402,F401
    _gdpr_recovery_clear,
    _gdpr_recovery_lock,
    _gdpr_recovery_pending,
    _gdpr_recovery_register,
    deliver_gdpr_recovery_choice,
)


def rehydrate_session_gdpr_mapping(session) -> bool:
    """Restore `session._gdpr_mapping_id` + `_gdpr_streamer` from the
    persisted `pseudonym_maps` rows, if any. Returns True if a mapping was
    rehydrated. Cheap no-op when the session never anonymised.

    Used by `Session.load_from_db` (reload-from-disk path) and by the chat
    worker at turn start (so follow-up turns of an anonymise session keep
    pseudonymising history + tool outputs without requiring the client to
    re-send `gdpr_action=anonymise` every turn).
    """
    try:
        if getattr(session, "_gdpr_mapping_id", None):
            return True
        prior = ChatDB.list_pseudonym_maps_for_session(session.id) or []
        if not prior:
            return False
        latest_mid = prior[-1][0]
        m = pseudonymizer.get_mapping(latest_mid)
        if m is None:
            m = pseudonymizer.load_mapping(latest_mid)
            if m is not None:
                pseudonymizer.restore_mapping_to_registry(m)
        if m is None:
            return False
        session._gdpr_mapping_id = m.mapping_id
        session._gdpr_streamer = StreamingDeanonymizer(m)
        return True
    except Exception:
        return False


_ATTACH_NOTICE_PREFIXES = (
    "\n\n[User attached files saved to disk",
    "\n\n[User attached image(s)",
)


def _split_attachment_notice(text: str) -> tuple[str, str]:
    """Split a user message into (typed_part, attachment_notice).

    The notice is Brain-generated boilerplate plus literal disk paths and
    must never be PII-scanned: NER misclassifies words like "IMPORTANT" as
    organisation and filenames as addresses, and pseudonymising the path
    breaks `read_document` (the model receives a fake path and can't find
    the file). Match by stable prefix anchored at the start of the notice.
    Returns ("", "") for the notice half when no notice is present.
    """
    if not isinstance(text, str) or not text:
        return text or "", ""
    for prefix in _ATTACH_NOTICE_PREFIXES:
        idx = text.rfind(prefix)
        if idx >= 0:
            return text[:idx], text[idx:]
    return text, ""


def _build_web_sources(web_urls, web_locked):
    """Fetch the user-curated web sources NOW (fresh, per turn).

    Called at TURN time (worker, just before the wire build) so each send
    re-fetches the marked URLs fresh. Returns `(wire_text, sources)` where:
      - `wire_text` is the markdown preamble prepended to the transient wire
        copy of the last user message (the model's view; never persisted).
      - `sources` is the structured per-source record
        `[{title, url, content, error}]` stored on the assistant turn's
        metadata so the chat view + inspector can show each source's FULL
        fetched content individually, like a web_fetch tool-call result.
    Both empty when nothing was fetchable.

    Each source is fetched in full (the whole page) — there is no summary/
    abstract path: a curated source is always read in full.
    """
    import brain as _engine
    sources = []
    blocks = []
    for u in web_urls:
        url = (u.get("url") or "").strip() if isinstance(u, dict) else ""
        if not url:
            continue
        title = (u.get("title") or "").strip() if isinstance(u, dict) else ""
        try:
            _wf_args = {"url": url, "force_fresh": True}
            parsed = json.loads(_engine.tool_web_fetch(_wf_args))
        except (ValueError, TypeError):
            parsed = {}
        if parsed.get("error") or "content" not in parsed:
            err = parsed.get("error", "unknown error")
            sources.append({"title": title or url, "url": url, "content": "", "error": err})
            blocks.append(f"### {title or url}\nURL: {url}\n(could not be fetched: {err})")
        else:
            final_url = parsed.get("url", url)
            content = parsed["content"]
            sources.append({"title": title or final_url, "url": final_url,
                            "content": content, "error": ""})
            blocks.append(f"### {title or final_url}\nURL: {final_url}\n\n{content}")
    if not blocks:
        return "", []
    head = ("[The user selected the following web sources for this task. Their "
            "full fetched content is provided below — base your answer on these "
            "sources. "
            + ("Do NOT search the web or fetch other URLs.]" if web_locked
               else "You may also search or fetch more if needed.]"))
    return head + "\n\n" + "\n\n---\n\n".join(blocks), sources


def _inject_web_preamble_into_wire(messages, preamble):
    """Return a transient wire copy of `messages` with `preamble` prepended to
    the LAST user message's content. The original list + message dicts are NOT
    mutated (shallow-copies the one message it touches), so session.messages /
    the DB stay clean and the fetched content never enters history."""
    if not preamble or not messages:
        return messages
    wire = list(messages)
    for i in range(len(wire) - 1, -1, -1):
        if wire[i].get("role") == "user":
            msg = dict(wire[i])
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = f"{preamble}\n\n{content}"
            elif isinstance(content, list):
                # Multimodal: prepend a text block (keeps image blocks intact).
                msg["content"] = [{"type": "text", "text": preamble}] + content
            else:
                msg["content"] = preamble
            wire[i] = msg
            break
    return wire


def _append_to_wire_user(messages, suffix):
    """Like _inject_web_preamble_into_wire but APPENDS `suffix` to the LAST user
    message (a trailing instruction reads better than a leading one for response-
    style directives, e.g. caveman). Transient wire-only — shallow-copies the one
    message it touches so session.messages / the DB stay clean."""
    if not suffix or not messages:
        return messages
    wire = list(messages)
    for i in range(len(wire) - 1, -1, -1):
        if wire[i].get("role") == "user":
            msg = dict(wire[i])
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = f"{content}{suffix}"
            elif isinstance(content, list):
                # Multimodal: append a trailing text block (keeps image blocks).
                msg["content"] = content + [{"type": "text", "text": suffix}]
            else:
                msg["content"] = suffix
            wire[i] = msg
            break
    return wire


def _session_attachment_paths(session_id: str) -> list[str]:
    """All files attached EARLIER in this session, still on disk under the
    session-scoped /tmp/brain-attachments/<sid>/ dir (the upload path never
    clears it, so attachments accumulate across the whole chat). Sorted for
    stable order. Empty list when the dir is absent / empty."""
    try:
        d = os.path.join("/tmp", "brain-attachments", session_id)
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.join(d, n) for n in os.listdir(d)
            if os.path.isfile(os.path.join(d, n)))
    except Exception:
        return []


_DOC_ATTACH_EXTS = (".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".csv", ".tsv",
                    ".eml", ".msg", ".epub", ".txt", ".md")


def _session_attachments_wire_suffix(session_id: str) -> tuple[str, bool]:
    """A wire-only reminder, appended to the last user message EVERY turn, that
    lists the documents attached so far in this chat — so a later turn can still
    read them with read_document (the upload-turn notice only fires on the turn
    the file arrived; later turns 'forgot' the file → the model answered from
    stale context instead of re-reading, chat 29ce67d2). Transient (never
    persisted → no history bloat / freeze, same pattern as the web preamble).
    Returns (suffix, has_docs); has_docs drives forcing read_document in-prompt.
    Covers the 'extra file attached in a later turn' case automatically: the dir
    accumulates, so every doc ever attached in the session is listed."""
    paths = _session_attachment_paths(session_id)
    docs = [p for p in paths if os.path.splitext(p)[1].lower() in _DOC_ATTACH_EXTS]
    if not docs:
        return "", False
    listing = "\n".join(f"  - {p}" for p in docs)
    suffix = (
        "\n\n[Dateien, die in diesem Chat angehängt wurden und weiterhin auf "
        "Disk verfügbar sind — mit dem read_document-Tool (NICHT read_file) "
        "jederzeit (erneut) lesbar, auch wenn sie in einer früheren Anfrage "
        "hochgeladen wurden:]\n" + listing)
    return suffix, True


def _undelivered_groups_preamble(session_id: str) -> str:
    """Next-turn injection floor for fan-out groups: any completed group whose
    proactive push didn't fire (busy chat) is delivered here. Pops + marks
    consumed in one transaction (ChatDB.pop_undelivered_groups), groups members
    by group_id, formats each via _build_group_preamble. Wire-only."""
    members = ChatDB.pop_undelivered_groups(session_id)
    if not members:
        return ""
    by_group = {}
    for m in members:
        by_group.setdefault(m.get("group_id"), []).append(m)
    return "\n\n".join(_build_group_preamble(g) for g in by_group.values() if g)


def _build_background_task_preamble(session_id: str) -> str:
    """Fold any finished-but-unconsumed background tasks for this session into a
    wire-only preamble. Returns "" when there are none. Marks them consumed in
    the same DB transaction (`pop_unconsumed_background_tasks`), so a task's
    output is delivered to the model on exactly one turn and never persists into
    chat history — the whole 'does not pollute the context window' guarantee."""
    # pop_unconsumed is @_db_safe (returns [] on any SQLite/OS error), so no
    # extra guard is needed — a non-empty return means real finished STANDALONE
    # tasks (grouped tasks are excluded; they come via the group floor below).
    tasks = ChatDB.pop_unconsumed_background_tasks(session_id)
    parts = []
    if tasks:
        blocks = []
        for t in tasks:
            title = t.get("title") or "Hintergrundaufgabe"
            if t.get("status") == "cancelled":
                head = f"### Hintergrundaufgabe „{title}“ (abgebrochen — Teilergebnis)"
            else:
                head = f"### Ergebnis der Hintergrundaufgabe „{title}“"
            body = (t.get("output") or "").strip()
            if not body and t.get("error"):
                body = f"(Kein Ergebnis — Fehler: {t.get('error')})"
            blocks.append(f"{head}\n\n{body}")
        intro = (
            "[Eine oder mehrere von dir gestartete Hintergrundaufgaben sind fertig. "
            "Ihr vollständiges Ergebnis steht dir HIER für diese Antwort zur "
            "Verfügung — nutze es, um die Nachricht des Nutzers zu beantworten. "
            "Dieser Block erscheint nur dieses eine Mal und ist danach nicht mehr im "
            "Verlauf.]"
        )
        parts.append(intro + "\n\n" + "\n\n".join(blocks))
    # Fan-out group FLOOR: any completed group whose proactive delivery didn't
    # fire (user was mid-turn) is delivered here on the next turn.
    grp = _undelivered_groups_preamble(session_id)
    if grp:
        parts.append(grp)
    return "\n\n".join(parts)


# Guards concurrent auto-fire delivery: at most one delivery turn per session in
# flight (keyed by session_id). A turn already running for the session also
# blocks delivery (the normal next-turn injection handles it then).
_bg_delivery_inflight = set()
_bg_delivery_lock = threading.Lock()


def _resolve_session_auto_model(session) -> bool:
    """If `session.model` is the literal "auto", resolve it to a concrete model
    in place (model + provider fields, mirroring the per-turn router in
    _handle_chat) so an auto_route=None delivery turn runs — and cost-logs —
    under a real model id, not "auto" (which has no rate → bills $0).

    Returns the prior directive ("auto"/"auto-cloud"/"auto-local") if a swap
    happened — the caller restores THAT afterwards so the composer keeps showing
    the right Smart mode — or "" (falsy) otherwise. Handles all auto forms.
    The local pool is honored on a delivery turn too: "auto-local" resolves to a
    local model."""
    _directive = session.model
    if _directive not in ("auto", "auto-cloud", "auto-local"):
        return ""
    _pool = "local" if _directive == "auto-local" else "cloud"
    resolved = engine._resolve_auto_model_tiered(None, pool=_pool) or engine.resolve_model("auto")
    if not resolved or resolved == "auto":
        return ""  # nothing enabled to resolve to — leave as-is
    provider = engine.resolve_provider_for_model(resolved)
    with session.lock:
        session.model = resolved
        session.api_key = provider["api_key"]
        session.base_url = provider["base_url"]
        session.max_context = engine.get_model_max_context(resolved)
    return _directive


def deliver_background_results(session_id: str) -> bool:
    """Auto-fire a chat turn that delivers finished background-task output into
    the conversation, when the chat is idle. Called from the background-task
    runner on completion. Returns True if a delivery turn was started.

    Idle-only: if a turn is already streaming for this session, we do NOTHING —
    the in-flight turn's normal next-turn injection (`_build_background_task_
    preamble` in run_session_turn) will pick the results up, so delivering here
    too would double-inject. No-loop: the delivery turn consumes the tasks
    (pop marks consumed_at), so when IT finishes and re-calls this, pop returns
    empty and we stop.
    """
    try:
        session = sessions.get(session_id)  # noqa: F821 — server-injected
    except Exception:
        return False
    if session is None:
        return False

    # Delivery-vs-delivery single-flight (module-level set).
    with _bg_delivery_lock:
        if session_id in _bg_delivery_inflight:
            return False
        _bg_delivery_inflight.add(session_id)

    try:
        # Peek WITHOUT consuming — is there anything finished to deliver? If not,
        # bail before touching session state. (Consuming happens only after we
        # win the idle gate below, so a "busy" bail never loses tasks — they stay
        # unconsumed for the live turn's injection to pick up.)
        if ChatDB.count_unconsumed_background_tasks(session_id) == 0:
            return False

        # ATOMIC idle-gate + streaming-set under session.lock. The check and the
        # set MUST be in one critical section: otherwise a concurrent POST /v1/chat
        # can slip between a separate "is idle?" check and the state-set, and both
        # turns end up active (overwriting live_stream, orphaning active_turns).
        # If a turn is already running, we bail — its normal next-turn injection
        # (_build_background_task_preamble in run_session_turn) carries the result.
        live = LiveStream()
        with session.lock:
            if getattr(session, "_streaming", False) or getattr(session, "live_stream", None):
                return False  # busy — injection on the live turn will deliver
            session.cancel_token = engine.CancelToken()
            session._streaming = True
            session.live_stream = live

        # We own the turn now. Build the delivery message (THIS consumes the
        # tasks — only happens on the winning path).
        preamble = _build_background_task_preamble(session_id)
        if not preamble:
            # Race: someone consumed between the peek and here. Release the
            # streaming state we just claimed and bail cleanly.
            with session.lock:
                session._streaming = False
                session.live_stream = None
            return False
        delivery_msg = (
            preamble
            + "\n\n[Bitte fasse das Ergebnis für den Nutzer zusammen bzw. arbeite "
            "damit weiter — der Nutzer wartet darauf.]"
        )
        try:
            ChatDB.set_streaming_text(session.id, "")
        except Exception:
            pass
        # Persist the delivery message as a real user turn so the conversation
        # reads coherently (result arrived -> assistant responded). It is NOT
        # wire-only here: an auto-fired turn needs a user turn to respond to.
        session.add_message("user", delivery_msg,
                            metadata={"background_delivery": True})

        # Resolve a still-"auto" session model to a concrete one (see the same
        # guard in deliver_background_group) so the auto_route=None delivery turn
        # and its cost row aren't tagged "auto" (= $0). Restore after.
        _restore_auto = _resolve_session_auto_model(session)
        # Re-establish request context on this fresh thread (mirrors
        # _recover_one_turn / the chat worker setup).
        with engine.request_context():
            engine.get_request_context().current_session_id = session_id
            engine.get_request_context().current_user_id = session.user_id or ""
            engine.get_request_context().current_agent = engine.AgentConfig(session.agent_id)
            engine.get_request_context().mcp_manager = engine._mcp_manager
            engine.get_request_context().project = session.project or ""

            t = run_session_turn(
                session, sid=session_id, message=delivery_msg,
                user_content=delivery_msg, chat_mode="", thinking_level=None,
                live=live, saved_paths=[], web_urls=[], web_locked=False,
                project_name=None, preamble_text="", content_blocks=[],
                disk_files=[], auto_route=None, want_auto=False,
            )
            t.join()  # run synchronously on this delivery thread
        if _restore_auto:
            with session.lock:
                session.model = _restore_auto
        return True
    except Exception as e:  # never let a delivery failure kill the runner thread
        print(f"[bg-delivery] failed for {session_id[:8]}: {e}", flush=True)
        return False
    finally:
        with _bg_delivery_lock:
            _bg_delivery_inflight.discard(session_id)


def _build_group_preamble(members: list) -> str:
    """Wire-only preamble for a finished fan-out GROUP. `members` are the rows
    returned by ChatDB.claim_background_group (already single-flight-claimed, so
    no consume/pop here). Includes each member's output-or-error (deliver-with-
    failures) plus the group's follow_up (the recombine instruction)."""
    if not members:
        return ""
    follow_up = ""
    blocks = []
    for m in members:
        title = m.get("title") or "Hintergrundaufgabe"
        st = m.get("status")
        if st == "cancelled":
            head = f"### Hintergrundaufgabe „{title}“ (abgebrochen — Teilergebnis)"
        elif st == "error":
            head = f"### Hintergrundaufgabe „{title}“ (fehlgeschlagen)"
        else:
            head = f"### Ergebnis der Hintergrundaufgabe „{title}“"
        body = (m.get("output") or "").strip()
        if not body and m.get("error"):
            body = f"(Kein Ergebnis — Fehler: {m.get('error')})"
        blocks.append(f"{head}\n\n{body}")
        if not follow_up and (m.get("follow_up") or "").strip():
            follow_up = m["follow_up"].strip()
    intro = (
        "[Alle von dir parallel gestarteten Hintergrundaufgaben sind fertig. "
        "Ihre vollständigen Ergebnisse stehen dir HIER für diese Antwort zur "
        "Verfügung. Dieser Block erscheint nur dieses eine Mal und ist danach "
        "nicht mehr im Verlauf.]"
    )
    tail = (
        f"\n\n[Aufgabe zum Zusammenführen: {follow_up}]" if follow_up
        else "\n\n[Bitte fasse die Ergebnisse für den Nutzer zusammen bzw. arbeite damit weiter.]"
    )
    return intro + "\n\n" + "\n\n".join(blocks) + tail


def deliver_background_group(session_id: str, group_id: str, members: list) -> bool:
    """Fire a JOIN/synthesis turn for a completed fan-out group. Called by the
    background-task runner's LAST finisher only (the atomic claim already
    selected exactly one caller — see ChatDB.claim_background_group). `members`
    is the claimed row set. Mirrors deliver_background_results' atomic idle-gate.
    On a successful proactive turn we mark the group consumed (mark_group_consumed)
    so the injection floor never re-delivers it. If the chat is busy we bail
    WITHOUT consuming — the members stay group_done_at-set + consumed_at-NULL, so
    the next user turn's injection floor (ChatDB.pop_undelivered_groups, folded
    into _build_background_task_preamble) delivers them then. Best-effort proactive
    push with a guaranteed next-turn floor — the group is never lost."""
    try:
        session = sessions.get(session_id)  # noqa: F821 — server-injected
    except Exception:
        return False
    if session is None:
        return False

    preamble = _build_group_preamble(members)
    if not preamble:
        return False
    delivery_msg = preamble

    with _bg_delivery_lock:
        if session_id in _bg_delivery_inflight:
            return False
        _bg_delivery_inflight.add(session_id)
    try:
        # Atomic idle-gate + streaming-set (same invariant as the standalone path).
        live = LiveStream()
        with session.lock:
            if getattr(session, "_streaming", False) or getattr(session, "live_stream", None):
                return False  # busy — best-effort; group stays visible in the panel
            session.cancel_token = engine.CancelToken()
            session._streaming = True
            session.live_stream = live
        # We own the turn — commit the group to THIS delivery (mark consumed) so
        # the next-turn injection floor never re-delivers it, even if the turn
        # below errors mid-flight.
        ChatDB.mark_group_consumed(group_id)
        try:
            ChatDB.set_streaming_text(session.id, "")
        except Exception:
            pass
        session.add_message("user", delivery_msg,
                            metadata={"background_delivery": True, "group_id": group_id})

        # The delivery turn runs with auto_route=None (no per-turn router). If the
        # session model is still the literal "auto" (the originating turn reset it
        # in its finally), resolve it to a concrete model first — else the turn
        # AND its cost-log row run tagged "auto", which bills $0 (no rate for the
        # literal). Restore "auto" after so the composer still shows Auto.
        _restore_auto = _resolve_session_auto_model(session)
        with engine.request_context():
            engine.get_request_context().current_session_id = session_id
            engine.get_request_context().current_user_id = session.user_id or ""
            engine.get_request_context().current_agent = engine.AgentConfig(session.agent_id)
            engine.get_request_context().mcp_manager = engine._mcp_manager
            engine.get_request_context().project = session.project or ""
            t = run_session_turn(
                session, sid=session_id, message=delivery_msg,
                user_content=delivery_msg, chat_mode="", thinking_level=None,
                live=live, saved_paths=[], web_urls=[], web_locked=False,
                project_name=None, preamble_text="", content_blocks=[],
                disk_files=[], auto_route=None, want_auto=False,
            )
            t.join()
        if _restore_auto:
            with session.lock:
                session.model = _restore_auto
        return True
    except Exception as e:
        print(f"[bg-group-delivery] failed for {session_id[:8]} grp {group_id}: {e}", flush=True)
        return False
    finally:
        with _bg_delivery_lock:
            _bg_delivery_inflight.discard(session_id)


def _filter_pii_false_positives(findings, text, session_id):
    """Drop findings whose value the user marked as a FALSE POSITIVE in this
    chat (pii_decisions). FP values must reach the model in the clear — the user
    judged them not to be real PII. Match on the finding's substring value,
    whitespace-collapsed + lowercased (same normalisation the decision used).
    Fail-open: any error returns the findings unchanged (never lose anonymisation
    on a lookup bug)."""
    if not findings or not session_id:
        return findings
    try:
        decided = ChatDB.get_session_pii_decisions(session_id) or {}
        fp_values = {
            (d.get("value") or "").strip().lower()
            for d in decided.values() if d.get("false_positive")
        }
        fp_values.discard("")
        if not fp_values:
            return findings
        out = []
        for f in findings:
            s, e = f.get("start", 0), f.get("end", 0)
            val = _re_ws.sub(" ", text[s:e]).strip().lower() if 0 <= s < e <= len(text) else ""
            if val and val in fp_values:
                continue  # user said this is not PII — leave it in the clear
            out.append(f)
        return out
    except Exception:
        return findings


import re as _re_ws_mod
_re_ws = _re_ws_mod.compile(r"\s+")


def _apply_pii_decisions_to_wire(messages, decisions):
    """Deterministically rewrite the wire-history from the persisted PII
    decision ledger — NO scanning, NO new token mints.

    `decisions` is `ChatDB.get_session_pii_decisions(sid)` (value_hash → record).
    The ledger is append-only: every PII value the session ever saw, with the
    user's decision. We use ONLY the anonymise decisions here: for each value
    whose decision is `turn_action=='anonymise'` (and which has a stored
    `fake_value`), replace EVERY occurrence of the original with the fake in the
    user+assistant message text. Accepted / false-positive / local decisions
    keep the original verbatim (the user chose to send it in clear), and values
    never seen are left untouched (the current turn's own flow handles new PII).

    This is what keeps an already-anonymised value protected on EVERY later turn
    — including ones where the user picks 'continue' for a NEW finding — because
    the decision is read from the ledger, not re-derived from a per-turn action.
    Covers attachment-derived values too: their anonymise decisions are in the
    same ledger (recorded from the complete turn-end mapping).

    `session.messages` is NOT mutated — only the wire copy handed to the sidecar.
    Returns `(wire_messages, replaced_count, counts_by_rule)`.
    """
    # Build the original→fake table from anonymise decisions only. Longest
    # original first so a value that is a substring of another can't be
    # partially shadowed (e.g. a phone inside an address-like string).
    pairs = []
    for d in (decisions or {}).values():
        if not d or d.get("false_positive"):
            continue
        if d.get("turn_action") != "anonymise":
            continue
        orig = d.get("value") or ""
        fake = d.get("fake_value") or ""
        if orig and fake and orig != fake:
            pairs.append((orig, fake, d.get("rule_id") or "unknown"))
    pairs.sort(key=lambda p: -len(p[0]))
    if not pairs:
        return messages, 0, {}

    replaced = 0
    counts: dict[str, int] = {}

    def _rewrite(text):
        nonlocal replaced
        if not text:
            return text
        out = text
        for orig, fake, rid in pairs:
            if orig in out:
                n = out.count(orig)
                out = out.replace(orig, fake)
                replaced += n
                counts[rid] = counts.get(rid, 0) + n
        return out

    wire = []
    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant", "human") or content is None:
            wire.append(msg)
            continue
        if isinstance(content, str):
            new_text = _rewrite(content)
            if new_text != content:
                nm = dict(msg)
                nm["content"] = new_text
                wire.append(nm)
            else:
                wire.append(msg)
        elif isinstance(content, list):
            new_blocks = []
            mutated = False
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    nt = _rewrite(blk.get("text") or "")
                    if nt != (blk.get("text") or ""):
                        nb = dict(blk)
                        nb["text"] = nt
                        new_blocks.append(nb)
                        mutated = True
                        continue
                new_blocks.append(blk)
            if mutated:
                nm = dict(msg)
                nm["content"] = new_blocks
                wire.append(nm)
            else:
                wire.append(msg)
        else:
            wire.append(msg)
    return wire, replaced, counts


def _pseudonymize_history_for_wire(messages, mapping, scanner_cfg):
    """Walk prior `session.messages` and produce a wire-only pseudonymised
    copy. The reused mapping's `forward` table short-circuits already-known
    values, so cost is one scan + zero new mints for stable conversations.
    `session.messages` itself is NOT mutated — the chat UI keeps showing real
    values; only the list handed to the sidecar is rewritten.

    Returns `(wire_messages, new_tokens, finding_counts)`.
    """
    new_tokens = 0
    counts: dict[str, int] = {}
    wire: list[dict] = []
    if not messages:
        return wire, 0, counts
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant") or content is None:
            wire.append(msg)
            continue
        if isinstance(content, str):
            text = content
            typed, notice = _split_attachment_notice(text)
            f = engine._pii_scan_text(typed, cfg=scanner_cfg)
            if not f:
                wire.append(msg)
                continue
            before = len(mapping.forward)
            new_typed = pseudonymizer.pseudonymize_text(
                typed, f, mapping=mapping, source="history")
            new_text = new_typed + notice
            new_tokens += len(mapping.forward) - before
            for x in f:
                rid = x.get("rule_id") or "unknown"
                counts[rid] = counts.get(rid, 0) + 1
            new_msg = dict(msg)
            new_msg["content"] = new_text
            wire.append(new_msg)
        elif isinstance(content, list):
            new_blocks = []
            mutated = False
            for blk in content:
                if not isinstance(blk, dict) or blk.get("type") != "text":
                    new_blocks.append(blk)
                    continue
                text = blk.get("text") or ""
                typed, notice = _split_attachment_notice(text)
                f = engine._pii_scan_text(typed, cfg=scanner_cfg)
                if not f:
                    new_blocks.append(blk)
                    continue
                before = len(mapping.forward)
                new_typed = pseudonymizer.pseudonymize_text(
                    typed, f, mapping=mapping, source="history")
                new_text = new_typed + notice
                new_tokens += len(mapping.forward) - before
                for x in f:
                    rid = x.get("rule_id") or "unknown"
                    counts[rid] = counts.get(rid, 0) + 1
                new_blk = dict(blk)
                new_blk["text"] = new_text
                new_blocks.append(new_blk)
                mutated = True
            if mutated:
                new_msg = dict(msg)
                new_msg["content"] = new_blocks
                wire.append(new_msg)
            else:
                wire.append(msg)
        else:
            wire.append(msg)
    return wire, new_tokens, counts


def emit_gdpr_tool_event_for_session(
    session_id: str,
    *,
    kind: str,
    tool_use_id: str,
    args: dict | None = None,
    result: dict | None = None,
    status: str = "ok",
    duration_ms: int = 0,
):
    """Public seam: callable from anywhere that has a session id but no
    direct `live` reference. Emits a `dispatch` synthetic row immediately
    followed by a `done` row.

    Used from `brain._gdpr_anon_tool_text` (per read_document/read_file
    pseudonymisation) and `brain._after_file_write` callback when the
    write path needs to surface a per-tool-call privacy event. Falls back
    to persistence-only when the session has no live SSE stream attached
    (chat may have ended between the tool call and this emission)."""
    try:
        sess = sessions.peek(session_id)  # noqa: F821 — injected by server
    except Exception:
        sess = None
    live = getattr(sess, "live_stream", None) if sess else None
    if live is None:
        # No live stream — still persist so the rows show on reload.
        class _NullLive:
            def emit(self, *a, **kw): pass
        live = _NullLive()
    _emit_synthetic_tool_event(
        live=live, sid=session_id, kind=kind, tool_use_id=tool_use_id,
        phase="dispatch", args=args or {})
    _emit_synthetic_tool_event(
        live=live, sid=session_id, kind=kind, tool_use_id=tool_use_id,
        phase="done", result=result or {}, status=status,
        duration_ms=duration_ms)


def _emit_synthetic_tool_event(
    *,
    live,
    sid: str,
    kind: str,
    tool_use_id: str,
    phase: str,
    args: dict | None = None,
    result: dict | None = None,
    status: str = "ok",
    duration_ms: int = 0,
):
    """Emit one half of a synthetic tool-call pair (`tool_use` for the
    dispatch, `tool_result` for the done) + persist to messages.

    Persisted with `metadata.synthetic=True` and `metadata.kind=<kind>` so the
    web renderer can style them distinctly. NOT added to `session.messages`
    (the in-memory list handed to the LLM) — the LLM must never see these.
    The web renderer reads them from `GET /v1/sessions/<id>/messages` on
    reload.
    """
    if phase == "dispatch":
        # tool_use row: name + args. result will arrive in the matching done.
        content_obj = {
            "type": "anonymise_dispatch",
            "name": kind,
            "tool_use_id": tool_use_id,
            "args": args or {},
        }
        metadata = {
            "synthetic": True,
            "kind": kind,
            "tool_use_id": tool_use_id,
            "phase": "dispatch",
        }
        message_id = ChatDB.save_message(
            sid, "tool_use", json.dumps(content_obj), metadata=metadata)
        live.emit("synthetic_tool_use", {
            "message_id": message_id,
            "kind": kind,
            "tool_use_id": tool_use_id,
            "args": args or {},
        })
        return message_id

    # phase == "done"
    content_obj = {
        "type": "anonymise_done",
        "name": kind,
        "tool_use_id": tool_use_id,
        "result": result or {},
        "status": status,
        "duration_ms": int(duration_ms),
    }
    metadata = {
        "synthetic": True,
        "kind": kind,
        "tool_use_id": tool_use_id,
        "phase": "done",
        "status": status,
        "duration_ms": int(duration_ms),
    }
    message_id = ChatDB.save_message(
        sid, "tool_result", json.dumps(content_obj), metadata=metadata)
    live.emit("synthetic_tool_result", {
        "message_id": message_id,
        "kind": kind,
        "tool_use_id": tool_use_id,
        "result": result or {},
        "status": status,
        "duration_ms": int(duration_ms),
    })
    return message_id


class PseudonymizeError(Exception):
    """Raised when the anonymisation walker fails (corrupted file, parser
    crash, etc.). Caught by the chat worker which then triggers the recovery
    modal — see `_handle_chat`."""

    def __init__(self, message: str, *, sources: list[str] | None = None):
        super().__init__(message)
        self.sources = sources or []


def make_gdpr_after_file_write_cb(*, mapping_id: str, session_id: str,
                                  agent_id: str):
    """Build a `_after_file_write` callback for a session with active
    anonymisation.

    Returned closure runs on the tool-dispatch thread (where
    `brain._after_file_write` is called from `tool_write_file` / `tool_edit_file`
    / etc.). It de-anonymises the just-written file in place, then emits a
    pair of synthetic tool-call rows (`deanonymise_file`) so the UI shows
    each restore operation in chat history.

    The session's `live_stream` is the live SSE channel; emitting on it is
    thread-safe (LiveStream is shared state, fan-out happens under its own
    lock). If the session was deleted between the LLM tool call and this
    callback firing, we skip silently — the file just stays in
    pseudonymised form, which is failsafe (no PII leak; the user just sees
    tokens).
    """
    def _cb(path: str, action: str, _agent_id: str):
        # No-op when the file is outside the artifact tree — only LLM-written
        # files (tool_write_file etc.) reach the user; we don't want to
        # rewrite arbitrary disk paths the model might touch.
        if not engine._is_artifact_path(path):
            return
        ext = os.path.splitext(path)[1].lower()
        from engine.file_pseudonymize import SUPPORTED_EXTS
        if ext not in SUPPORTED_EXTS:
            return  # Image / binary / unknown — nothing to restore.

        mapping = pseudonymizer.get_mapping(mapping_id)
        if mapping is None:
            # Mapping fell out of the registry (worker `finally` already ran).
            # Try loading the persisted copy.
            try:
                mapping = pseudonymizer.load_mapping(mapping_id)
                if mapping is not None:
                    pseudonymizer.restore_mapping_to_registry(mapping)
            except Exception:
                return
            if mapping is None:
                return

        # Resolve the live session for SSE emission. `sessions` is injected
        # by `server._inject_server_globals()` at boot.
        session = None
        try:
            session = sessions.peek(session_id)  # noqa: F821
        except Exception:
            session = None
        live = getattr(session, "live_stream", None) if session else None

        t0 = time.time()
        tool_use_id = f"deanon_file_{mapping_id[:8]}_{int(t0 * 1000) % 1000000}"
        fname = os.path.basename(path)
        if live is not None:
            try:
                _emit_synthetic_tool_event(
                    live=live, sid=session_id, kind="deanonymise_file",
                    tool_use_id=tool_use_id, phase="dispatch",
                    args={"file": fname, "mapping_id": mapping_id},
                )
            except Exception:
                pass

        try:
            restored = pseudonymizer.deanonymize_file(
                path, path, mapping=mapping)
            status = "ok"
            result = {
                "file": fname,
                "restored": int(restored),
                "mapping_id": mapping_id,
            }
            err = ""
        except Exception as e:
            status = "error"
            result = {
                "file": fname,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "mapping_id": mapping_id,
            }
            err = result["error"]
            restored = 0

        if live is not None:
            try:
                _emit_synthetic_tool_event(
                    live=live, sid=session_id, kind="deanonymise_file",
                    tool_use_id=tool_use_id, phase="done",
                    result=result, status=status,
                    duration_ms=int((time.time() - t0) * 1000),
                )
            except Exception:
                pass

        if engine._audit_log:
            try:
                engine._audit_log.log_action(
                    agent=agent_id, session_id=session_id,
                    action_type="pii_deanonymise_file",
                    tool_name="gdpr_scanner",
                    args_summary=fname,
                    result_summary=(f"restored={restored} mapping_id={mapping_id}"
                                    if status == "ok"
                                    else f"error={err} mapping_id={mapping_id}"),
                    result_status=status if status == "ok" else "error",
                    duration_ms=int((time.time() - t0) * 1000),
                    source="chat",
                )
            except Exception:
                pass

    return _cb


def make_artifact_event_callback(session_id: str):
    """Build a minimal `event_callback` for the tool-dispatch thread.

    The chat worker installs a rich callback (accumulates partial replies,
    tool calls, references, …) on its own thread-local. Tool dispatch
    happens on a different thread — the sidecar POSTs to /v1/tools/call,
    `tool_mcp._apply_context` rebuilds the per-turn thread-locals there.
    Without an `event_callback` on that thread, `brain._after_file_write`
    skips its artifact-registration branch entirely (the `if ecb:` gate),
    so `write_file` / `edit_file` / `python_exec` produce a file on disk
    but no `artifacts` row and no live `artifact_updated` SSE.

    This callback forwards `file_created` / `artifact_updated` events to the
    session's LiveStream so the UI's artifact panel updates live, AND records
    each file on `session._turn_created_files` so the chat worker can persist
    it into the assistant turn's `metadata.files`. The artifact STORE is
    persisted by `_register_artifact_version` (right panel), but the per-message
    `metadata.files` the chat-view badge reads on RELOAD is not — without this
    the badge showed live (via the LiveStream → client `chat.files`) but
    vanished on reload (metadata.files was never written).
    """
    # Events this minimal callback forwards into the session LiveStream. The
    # artifact pair updates the file panel; the user-input events are how the
    # BLOCKING tools (ask_user / ask_user_for_file) reach the client — they fire
    # cb("user_input_needed"/"user_input_received"/"file_upload_needed") and then
    # block on an answer Event. Tool dispatch runs on this thread (not the chat
    # worker's), so this callback is the ONLY event_callback those tools see; if
    # it drops their event the questions never render and the tool blocks until
    # its 300s timeout, killing the turn with no assistant message (the
    # e783c08a regression). Forward them through too.
    _ARTIFACT_EVENTS = ("file_created", "artifact_updated")
    _PASSTHROUGH_EVENTS = ("user_input_needed", "user_input_received",
                           "file_upload_needed",
                           # Generic live tool progress (report_tool_progress):
                           # phase/%/page-i-of-N for the running tool card.
                           # Display-only — never persisted.
                           "tool_progress")

    def _cb(event_type, data):
        if event_type not in _ARTIFACT_EVENTS and event_type not in _PASSTHROUGH_EVENTS:
            return
        try:
            sess = sessions.peek(session_id)  # noqa: F821 — injected by server
        except Exception:
            sess = None
        if sess is None:
            return
        # Record artifact writes for the worker's metadata.files persist
        # (reload-stable badge). Passthrough events carry no file payload.
        if event_type in _ARTIFACT_EVENTS:
            try:
                with sess.lock:
                    if getattr(sess, "_turn_created_files", None) is None:
                        sess._turn_created_files = []
                    sess._turn_created_files.append(data)
            except Exception:
                pass
        live = getattr(sess, "live_stream", None)
        if live is None:
            return
        try:
            live.emit(event_type, data)
        except Exception:
            pass
    return _cb


class StreamingDeanonymizer:
    """Per-turn helper that converts a stream of pseudonymized text deltas
    into a stream of de-anonymized text deltas, holding back partial tokens
    so the user never sees an unfinished `<KIND_N` mid-token.

    Strategy: maintain the raw cumulative buffer. On each delta:
      1. Apply `deanonymize_text` to the full raw buffer.
      2. Find the latest **safe emission point** — the index up to which
         the de-anonymized text is guaranteed not to contain a half-formed
         opaque token (an unclosed `<`).
      3. Emit only the new safe text since the last emission.

    On `flush()` (at turn end), emit whatever's left — any unclosed `<` is
    treated as stray text.

    `restored_count` accumulates total restorations across the stream for
    the final deanonymise pseudo-tool-call event.
    """

    def __init__(self, mapping):
        self.mapping = mapping
        self._raw = []                # raw deltas in arrival order
        self._emitted_len = 0         # chars of de-anonymized text already emitted
        self.restored_count = 0       # accumulated for the final tool-call row

    def feed(self, raw_delta: str) -> str:
        """Consume one raw delta. Returns the de-anonymized chunk to emit
        downstream (may be empty if everything is currently held back)."""
        if not raw_delta:
            return ""
        self._raw.append(raw_delta)
        full_raw = "".join(self._raw)
        full_denon, n = pseudonymizer.deanonymize_text(full_raw, mapping=self.mapping)
        # Replace cumulative count rather than add — deanonymize_text returns
        # the count over the whole buffer, not a delta.
        self.restored_count = n
        # Safe-emission boundary: the last position before any unclosed '<'
        # (an open angle bracket without a matching '>' afterwards may be
        # mid-token; holding it back avoids flashing a partial `<EMAIL_1` to
        # the user).
        last_open = full_denon.rfind("<")
        if last_open >= 0 and full_denon.find(">", last_open) < 0:
            safe_end = last_open
        else:
            safe_end = len(full_denon)
        if safe_end <= self._emitted_len:
            return ""
        chunk = full_denon[self._emitted_len:safe_end]
        self._emitted_len = safe_end
        return chunk

    def flush(self) -> str:
        """Emit any held-back tail. Called when the turn ends."""
        full_raw = "".join(self._raw)
        if not full_raw:
            return ""
        full_denon, n = pseudonymizer.deanonymize_text(full_raw, mapping=self.mapping)
        self.restored_count = n
        if len(full_denon) <= self._emitted_len:
            return ""
        chunk = full_denon[self._emitted_len:]
        self._emitted_len = len(full_denon)
        return chunk

    def final_text(self) -> str:
        """Return the fully de-anonymized cumulative text. Used after the
        stream ends to persist the assistant message with the user-visible
        text rather than the raw tokenized version."""
        full_raw = "".join(self._raw)
        full_denon, n = pseudonymizer.deanonymize_text(full_raw, mapping=self.mapping)
        self.restored_count = n
        return full_denon


def _generate_chat_summary(session):
    """Generate a short LLM summary of a chat session for sidebar display.

    Background thread target: produces a one-sentence synopsis used as the
    page-title hover tooltip and the collapsible Zusammenfassung block above
    turn 1. Regenerated every turn (no `not session.summary` gate) so it
    tracks the latest questions. Summarizes only the user's questions, not the
    assistant's answers.
    Model pick: `server_config.chat_summary_model` if set and enabled, else
    `engine._background_model_default()`. Routes through the sidecar like
    every other non-interactive call.
    """
    if len(session.messages) < 2:
        return
    with engine.request_context():
        engine.get_request_context().current_agent = session.agent
        engine.get_request_context().current_user_id = (getattr(session, "user_id", "") or "")
        engine.get_request_context().cost_purpose = "chat_summary"
        msgs = session.messages
        # Only the user's questions feed the summary — the assistant's answers are
        # excluded by design (sidebar synopsis should reflect what was asked).
        user_msgs = []
        for m in msgs:
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(parts)
            if isinstance(content, str) and content.strip():
                # Strip the round-0 artifact-folder preamble that rides in the
                # first user message's content — it's plumbing, not what the user
                # asked (mirrors add_message's auto-title strip).
                _pre = (m.get("metadata") or {}).get("preamble")
                if _pre and content.startswith(_pre):
                    content = content[len(_pre):].lstrip("\n")
                if content.strip():
                    user_msgs.append(content[:200])
        # Keep the first question (sets the topic) plus the most recent ones.
        if len(user_msgs) > 4:
            sample = user_msgs[:1] + user_msgs[-3:]
        else:
            sample = user_msgs

        if not sample:
            return

        prompt = (
            "Summarize the topics the user has asked about across this conversation "
            "in one short line (max 100 chars). If several distinct topics came up, "
            "cover them briefly rather than only the latest. Focus on the topics/tasks, "
            "not greetings. Output ONLY the summary, nothing else. "
            "Base your summary ONLY on the user questions below.\n\n"
            + "\n".join(sample)
        )
        try:
            configured = (server_config.get("chat_summary_model") or "").strip()
            model = ""
            if configured:
                mcfg = (engine._models_config or {}).get(configured) or {}
                if mcfg.get("enabled", True):
                    model = configured
            if not model:
                model = engine._background_model_default()
            if not model:
                return

            _summary_deanon = engine._identity_deanon
            try:
                model, _new_sample, _summary_deanon = engine.gdpr_pick_model_for_background(
                    model, sample, purpose="chat_summary")
                if _new_sample is not sample:
                    sample = list(_new_sample)
                    prompt = (
                        "Summarize the topics the user has asked about across this conversation "
                        "in one short line (max 100 chars). If several distinct topics came up, "
                        "cover them briefly rather than only the latest. Focus on the topics/tasks, "
                        "not greetings. Output ONLY the summary, nothing else. "
                        "Base your summary ONLY on the user questions below.\n\n"
                        + "\n".join(sample)
                    )
            except engine.GDPRBlockedError:
                return
            except Exception:
                pass

            _res = sidecar_proxy.background_call(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                system_prompt="Output only a brief summary sentence. No quotes, no prefix.",
                agent_id=session.agent_id,
                session_id=session.id,
                user_id=(getattr(session, "user_id", "") or ""),
                project=(session.project or ""),
                cost_purpose="chat_summary",  # cost row written centrally by background_call
                max_tokens=120,
            )
            result = _summary_deanon(_res.get("reply") or "")
            if result and not _res.get("error"):
                summary = result.strip().strip('"').strip("'")[:120]
                with session.lock:
                    session.summary = summary
                ChatDB.save_session(session.id, session.agent_id, session.model,
                                    session.title, session.status, session.created_at,
                                    session.last_active, session.project or "", summary)
        except Exception:
            pass


def _save_handover_artifact(session, summary_doc: str) -> str:
    """Write `summary_doc` into the SOURCE session's artifact folder and register
    it as an artifact version. The caller has already pinned
    `current_session_id` to the source session, so both the disk folder and the
    DB row target the original chat. Best-effort — returns the artifact filename
    on success, "" on any failure (the handover must not break on a save error)."""
    try:
        from datetime import datetime as _dt
        folder = engine._get_artifact_session_folder(session.id)
        agent_id = session.agent_id
        art_dir = os.path.join(engine.AGENTS_DIR, agent_id, "artifacts", folder)
        os.makedirs(art_dir, exist_ok=True)
        name = f"Übergabe-{_dt.now().strftime('%Y-%m-%d_%H%M%S')}.md"
        path = os.path.join(art_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(summary_doc)
        engine._register_artifact_version(path, "created", agent_id)
        return name
    except Exception as e:
        print(f"  [WARN] handover artifact save: {e}", flush=True)
        return ""


def _generate_handover_document(session) -> tuple[str, str, str]:
    """Generate the HANDOVER artifacts for `session`: a (summary_md, transcript_md,
    artifact_name) triple. The SUMMARY is the chat's RESOLVED model's structured
    handover (goal / state / decisions / open items / next steps). The TRANSCRIPT
    is the verbatim full history of the source chat, returned as a SEPARATE
    document so the new chat can work from the summary alone and only open the
    (potentially large) history when it needs the detail. The SUMMARY is also
    saved as an artifact in the SOURCE session's artifact folder — `artifact_name`
    is its filename ("" if the save failed). Returns ("", "", "") on failure.
    Routes the summary through sidecar_proxy.background_call (same GDPR/quota/cost
    seam)."""
    with engine.request_context():
        engine.get_request_context().current_agent = session.agent
        engine.get_request_context().current_user_id = (getattr(session, "user_id", "") or "")
        # Pin the artifact context to the SOURCE session so the saved handover
        # MD lands in the original chat's artifact folder (not the new chat's).
        engine.get_request_context().current_session_id = session.id
        engine.get_request_context().cost_purpose = "handover"
        # Build a compact transcript: user + final-assistant turns (skip thinking/
        # tool rows — they're not in session.messages persisted form anyway).
        lines = []
        for m in session.messages:
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text")
            if not (isinstance(content, str) and content.strip()):
                continue
            _pre = (m.get("metadata") or {}).get("preamble")
            if _pre and content.startswith(_pre):
                content = content[len(_pre):].lstrip("\n")
            who = "Nutzer" if role == "user" else "Assistent"
            lines.append(f"### {who}\n{content.strip()[:6000]}")
        transcript = "\n\n".join(lines)
        if not transcript.strip():
            return "", "", ""

        system_prompt = (
            "Du schreibst ein ÜBERGABE-Dokument (handover) für einen neuen Chat, "
            "der genau dort weitermachen soll, wo dieser aufgehört hat. Schreibe "
            "präzise, strukturiertes Markdown in der Sprache des Gesprächs. "
            "Erfinde NICHTS — nutze nur den Verlauf. Gliederung:\n"
            "# Übergabe\n"
            "## Ziel — worum geht es im Gespräch insgesamt\n"
            "## Bisheriger Stand — was wurde erreicht/entschieden (Stichpunkte)\n"
            "## Wichtige Entscheidungen & Annahmen\n"
            "## Offene Punkte / nächste Schritte\n"
            "## Kontext, den der neue Chat braucht (Dateien, Namen, Werte)\n"
            "Der vollständige Wortlaut-Verlauf liegt dem neuen Chat als SEPARATES "
            "Dokument bei — fasse hier zusammen, kopiere nicht den ganzen Verlauf. "
            "Gib NUR das Markdown-Dokument aus, nichts davor oder danach."
        )
        prompt = (
            "Hier ist der Gesprächsverlauf, den du übergeben sollst:\n\n"
            + transcript)

        # session.model may be a ROUTING DIRECTIVE ("auto"/"auto-cloud"/
        # "auto-local"), not a concrete model id — the per-turn chat router
        # resolves it, but a background call can't. Resolve it to a real model
        # here, else background_call fails and the handover comes back empty
        # (generation_failed). Read-only: don't mutate session.model.
        model = session.model
        if model in ("auto", "auto-cloud", "auto-local"):
            _pool = "local" if model == "auto-local" else "cloud"
            _resolved = (engine._resolve_auto_model_tiered(None, pool=_pool)
                         or engine.resolve_model("auto"))
            if _resolved and _resolved != "auto":
                model = _resolved
        sample = [prompt]
        _deanon = engine._identity_deanon
        try:
            model, _new_sample, _deanon = engine.gdpr_pick_model_for_background(
                model, sample, purpose="handover")
            if _new_sample is not sample and _new_sample:
                prompt = _new_sample[0]
        except engine.GDPRBlockedError:
            raise
        except Exception:
            pass

        _res = sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt=system_prompt,
            agent_id=session.agent_id,
            session_id=session.id,
            user_id=(getattr(session, "user_id", "") or ""),
            project=(session.project or ""),
            cost_purpose="handover",
            max_tokens=4000,
        )
        if _res.get("error"):
            return "", "", ""
        summary = _deanon(_res.get("reply") or "").strip()
        if not summary:
            return "", "", ""
        # Lead-in note on the summary doc so the model knows the full history is
        # available as a SECOND document if it needs the detail.
        summary_doc = (
            summary
            + "\n\n---\n\n"
            + "_Der vollständige Wortlaut-Verlauf des Ursprungs-Chats liegt als "
            + "separates Dokument bei (\"Verlauf – …\"). Lies ihn nur, wenn die "
            + "Zusammenfassung oben für den nächsten Schritt nicht ausreicht._"
        )
        transcript_doc = (
            "# Vollständiger Verlauf des Ursprungs-Chats\n\n"
            + "_Der komplette, wörtliche Verlauf des Chats, aus dem diese Übergabe "
            + "stammt. Nur zum Nachschlagen — die Zusammenfassung steht im "
            + "Übergabe-Dokument._\n\n"
            + _deanon(transcript)
        )
        # Persist the summary as an artifact in the SOURCE session's artifact
        # folder (current_session_id pinned above). Best-effort — a failure here
        # must not break the handover; the new chat still receives both docs.
        artifact_name = _save_handover_artifact(session, summary_doc)
        return summary_doc, transcript_doc, artifact_name


def _attach_usage_meta(meta: dict, usage_totals: dict, sid: str):
    """Write token counts + the live session cost into a partial-message's
    metadata. Used by the cancel/error paths so an interrupted turn persists what
    it consumed (the per-round ledger rows were already written in the `usage`
    handler — this makes the message + status bar reflect them after reload, so a
    mid-stream cancel no longer loses tokens/cost)."""
    meta["tokens_in"] = usage_totals.get("tokens_in", 0)
    meta["tokens_out"] = usage_totals.get("tokens_out", 0)
    meta["last_tokens_in"] = usage_totals.get("last_tokens_in", 0)
    if engine._cost_tracker:
        try:
            meta["cost"] = round(engine._cost_tracker.get_session_cost(sid).get("cost", 0.0), 4)
        except Exception:
            pass


def _sanitize_partial_tools(tools):
    """Drop transient per-tool keys (e.g. `_started_at`, the wall-clock anchor
    used to compute duration_ms) before persisting tools to message metadata.
    A tool whose result never arrived would otherwise carry the raw timestamp
    into the DB. Returns the same list (mutated in place) for call-site brevity."""
    for t in tools or []:
        t.pop("_started_at", None)
    return tools


def build_chat_event_callback(session, live, sid):
    """Build the per-turn SSE event callback + its accumulator state.

    Used by the live chat worker AND by the Brain-restart recovery thread
    (Phase 5 stage 1c) — both need identical persistence + LiveStream-fanout
    semantics. Returns (callback, state); the caller reads state after the
    loop terminates to assemble msg_metadata + the terminal `done` payload.

    `live` is the LiveStream this turn emits into; `sid` is the session id
    (used for ChatDB.set_streaming_text throttling); `session` is the
    Session for add_message("thinking", ...) row writes.
    """
    state = {
        "created_files": [],
        "stream_persist": {"last": 0.0},
        "partial_reply": [],
        "partial_tools": [],
        "partial_thinking": [],
        "thinking_summary": {},
        "usage_totals": {"tokens_in": 0, "tokens_out": 0, "last_tokens_in": 0},
        # True once any round's cost has been logged to the ledger (per-round, in
        # the `usage` handler). The success path checks this so it doesn't re-log
        # the aggregate (which would double-count). Cancel/error are covered too:
        # whatever rounds completed are already logged.
        "cost_logged": False,
        "request_payloads": [],
        # Counts sidecar `empty_round_nudge` events per turn — sidecar nudges
        # the model up to 3× when a round ends without usable text. We
        # surface the count both live (SSE forwards as-is to the client for
        # a composer badge) and post-turn (persisted in msg_metadata +
        # appended as a reload-stable hint at the reply tail).
        "nudge_count": [0],
    }
    created_files = state["created_files"]
    _stream_persist = state["stream_persist"]
    _partial_reply = state["partial_reply"]
    _partial_tools = state["partial_tools"]
    _partial_thinking = state["partial_thinking"]
    _thinking_summary = state["thinking_summary"]
    _usage_totals = state["usage_totals"]
    _request_payloads = state["request_payloads"]
    _nudge_count = state["nudge_count"]

    def event_callback(event_type, data):
        if event_type == "text_delta":
            raw_delta = data.get("text", "")
            _partial_reply.append(raw_delta)
            # Transparent-anonymisation: if a pseudonym mapping is active for
            # this turn, route the delta through the streaming deanonymizer.
            # The user only ever sees de-anonymized text — tokens like
            # `<EMAIL_1_xyz>` are converted back to the original value (or
            # held back if mid-formation) before they reach the SSE queue.
            # The raw token form stays in `_partial_reply` for the
            # streaming_text persistence path (which writes the raw buffer);
            # reload picks up the persisted assistant message (which the
            # worker `finally` finalises with the de-anonymized text).
            streamer = getattr(session, "_gdpr_streamer", None)
            if streamer is not None:
                safe_chunk = streamer.feed(raw_delta)
                if safe_chunk:
                    # Forward the de-anonymized chunk to subscribers; do NOT
                    # also forward the raw delta below.
                    live.emit("text_delta", {"text": safe_chunk})
                # Persist the de-anonymized partial so a reload mid-stream
                # shows real values rather than tokens.
                _now = time.time()
                if _now - _stream_persist["last"] > 0.4:
                    _stream_persist["last"] = _now
                    try:
                        ChatDB.set_streaming_text(
                            sid, streamer.final_text())
                    except Exception:
                        pass
                return  # raw delta intentionally not re-emitted below
            # Incremental persist (throttled): so a client reopening this
            # session — or the chat surviving a server restart mid-stream —
            # can render the partial reply even with no live buffer.
            _now = time.time()
            if _now - _stream_persist["last"] > 0.4:
                _stream_persist["last"] = _now
                try:
                    ChatDB.set_streaming_text(sid, "".join(_partial_reply))
                except Exception:
                    pass
        elif event_type in ("file_created", "artifact_updated"):
            created_files.append(data)
        elif event_type == "thinking_delta":
            _partial_thinking.append(data.get("text", ""))
        elif event_type == "thinking_done":
            # Persist this round's thinking as its own message row so the
            # transcript preserves chronological order: thinking → tool calls →
            # next round's thinking → final assistant text. The engine fires this
            # per tool-round, so multi-round reasoning ends up as multiple rows
            # interleaved with tool_call/tool_result. Skip if no text (opaque path).
            _round_text = data.get("text") or "".join(_partial_thinking)
            _round_text = _round_text.strip()
            if _round_text:
                _tr = data.get("tool_round")
                _meta = {"tool_round": _tr} if _tr is not None else None
                try:
                    session.add_message("thinking", _round_text, metadata=_meta)
                except Exception as _e:
                    print(f"[thinking-persist] failed: {_e}", flush=True)
            # Reset the accumulator so the next round starts fresh.
            _partial_thinking.clear()
        elif event_type == "thinking_summary":
            _thinking_summary.update(data)
        elif event_type == "tool_call":
            name = data.get("name", "")
            args = data.get("args", {})
            tr = data.get("tool_round")
            tuid = data.get("tool_use_id", "")
            # Update existing entry if re-emitted with full args, else append.
            # Carry tool_use_id so the matching tool_result can pair by ID — name
            # matching breaks when one round has two calls of the same tool (e.g.
            # two read_document) whose results arrive out of order under parallel
            # tool calls (model emits N tool_use blocks; parallel_tool_calls=True).
            if args and _partial_tools and _partial_tools[-1].get("name") == name and not _partial_tools[-1].get("args"):
                _partial_tools[-1]["args"] = args
                if tr is not None:
                    _partial_tools[-1]["tool_round"] = tr
                if tuid and not _partial_tools[-1].get("tool_use_id"):
                    _partial_tools[-1]["tool_use_id"] = tuid
            else:
                entry = {"name": name, "args": args}
                if tr is not None:
                    entry["tool_round"] = tr
                if tuid:
                    entry["tool_use_id"] = tuid
                # Wall-clock start, used to compute duration_ms when the result
                # arrives (persisted per-tool so the chat view can show timing
                # on reload — _ts deltas only exist for the live session).
                entry["_started_at"] = time.time()
                _partial_tools.append(entry)
        elif event_type == "tool_result":
            # Attach result to the last matching tool entry and extract
            # normalized references server-side. The cap controls how much
            # of the raw result string we persist — references are stored
            # separately in t["references"] so the client never needs to
            # re-parse the raw result JSON to render the references panel.
            tool_name = data.get("name", "")
            result_tuid = data.get("tool_use_id", "")
            result_str = str(data.get("result", ""))
            if tool_name in ("read_document", "read_file",
                             "read_path", "read_path_original"):
                cap = 50000
            else:
                cap = 5000
            refs = ChatHandlerMixin._extract_references(tool_name, result_str)
            # web_fetch carries its `fetch_method` (raw/markitdown/crawl4ai) at
            # the END of the result JSON, after the large `content` field. The
            # cap can slice it off, so the client's badge regex finds nothing on
            # reload. Capture it before the cap and re-append after the cut so
            # the badge survives truncation. (Live SSE has the full string; this
            # only matters for the persisted copy.)
            capped = result_str[:cap]
            if (tool_name == "web_fetch" and len(result_str) > cap
                    and '"fetch_method"' not in capped):
                import re as _re
                fm = _re.search(r'"fetch_method"\s*:\s*"(?:crawl4ai|markitdown|raw)"', result_str)
                if fm:
                    capped += " …" + fm.group(0)
            # Pair the result to its tool_call by tool_use_id (robust to two
            # same-named calls in one round whose results arrive out of order).
            # Fall back to last-unfilled-by-name only when no id is available
            # (legacy / id-less events) — preserves the prior behavior there.
            def _finish_tool(t):
                t["result"] = capped
                if refs:
                    t["references"] = refs
                # Real execution duration: wall-time from the tool_call event to
                # now. Persisted (ms) so the chat view shows timing on reload —
                # client _ts deltas only exist for the live, in-memory session.
                started = t.pop("_started_at", None)
                if started is not None:
                    t["duration_ms"] = int(max(0.0, time.time() - started) * 1000)
            matched = False
            if result_tuid:
                for t in _partial_tools:
                    if t.get("tool_use_id") == result_tuid and "result" not in t:
                        _finish_tool(t)
                        matched = True
                        break
            if not matched:
                for t in reversed(_partial_tools):
                    if t["name"] == tool_name and "result" not in t:
                        _finish_tool(t)
                        break
            if refs:
                live.emit("references", {
                    "tool_name": tool_name,
                    "references": refs,
                    "tool_round": data.get("tool_round", 0),
                })
        elif event_type == "usage":
            _r_in = data.get("tokens_in", 0)
            _r_out = data.get("tokens_out", 0)
            _usage_totals["tokens_in"] += _r_in
            _usage_totals["tokens_out"] += _r_out
            _usage_totals["last_tokens_in"] = _r_in
            # Attach per-round actual tokens to the matching request_payload
            _ur = data.get("tool_round")
            if _ur is not None:
                for _p in _request_payloads:
                    if _p.get("tool_round") == _ur:
                        _p["tokens_in"] = _r_in
                        _p["tokens_out"] = _r_out
                        break
            # PER-ROUND cost logging: write THIS round's delta to the ledger the
            # moment it arrives, tagged with its tool_round (one cost_log row per
            # round). A mid-stream cancel then loses neither tokens nor cost —
            # every completed round is already persisted. The end-of-turn aggregate
            # log is gone (it would double-count these rows). Keyed by the model
            # that actually answered (fallback wins). Marks the turn logged so the
            # success path doesn't re-log.
            _cost_model = (engine.get_request_context()._fallback_model_used
                           or session.model)
            try:
                engine._log_call_cost(
                    _cost_model, _r_in, _r_out,
                    session_id=sid, tool_round=(_ur or 0),
                    api_key=session.api_key,
                )
                state["cost_logged"] = True
            except Exception as _ce:
                print(f"[chat] per-round cost log failed: {_ce}")
            # Forward a LIVE usage event to the client so the running-turn display
            # AND the status bar update mid-stream (cumulative tokens + the
            # session cost so far). Cheap; no DB write here (the log above owns it).
            _live_cost = None
            if engine._cost_tracker:
                try:
                    _live_cost = round(engine._cost_tracker.get_session_cost(sid).get("cost", 0.0), 4)
                except Exception:
                    _live_cost = None
            live.emit("usage", {
                "tokens_in": _usage_totals["tokens_in"],
                "tokens_out": _usage_totals["tokens_out"],
                "last_tokens_in": _usage_totals["last_tokens_in"],
                "cost": _live_cost,
                "tool_round": _ur,
            })
            return
        elif event_type == "worker_usage":
            # Worker-side LLM call (e.g. summariser) tokens. Add to turn totals
            # so the status bar reflects the real cost. Forward to client for
            # the worker-flow panel.
            _usage_totals["tokens_in"] += data.get("tokens_in", 0)
            _usage_totals["tokens_out"] += data.get("tokens_out", 0)
            # (fall through so the event reaches the SSE queue)
        elif event_type == "request_payload":
            _request_payloads.append(data)
            return  # internal only, don't send to client
        elif event_type == "empty_round_nudge":
            # Sidecar emitted a nudge attempt. Use the attempt counter from
            # the event (1..N) so we don't double-count if the event arrives
            # twice. Fall through to live.emit so the client can show a
            # composer badge during the turn.
            try:
                _attempt = int(data.get("attempt") or 0)
            except Exception:
                _attempt = 0
            if _attempt > _nudge_count[0]:
                _nudge_count[0] = _attempt
        live.emit(event_type, data)

    return event_callback, state


def recover_active_turns_on_boot():
    """Phase 5 stage 1c — Brain-restart recovery.

    On startup, scan `active_turns`. For each row whose turn is still alive in
    the sidecar's per-turn event log (the sidecar survives Brain restarts), spawn
    a recovery thread that re-attaches to `GET /turn/<id>/events?since=0` and
    drives the same event-translation + persistence pipeline the live worker uses
    — so the user's browser reload after a `launchctl kickstart` picks up the
    turn mid-flight via `GET /v1/chat/stream`.

    Rows whose sidecar event log is gone (404 — sidecar also crashed, or the
    5-minute retention janitor purged the log) are tagged with a server-restart
    marker on the persisted streaming_text and the row is cleared.

    Non-blocking: spawns one daemon thread per row and returns immediately.
    Boot path never blocks on this.
    """
    # Transparent-anonymisation: drop any pseudonym_maps rows whose session
    # was deleted between processes. Cheap (one DELETE with a NOT IN clause).
    # We intentionally do NOT prune by age — `persist_maps=true` means users
    # rely on these maps surviving for historical message de-anonymisation;
    # pruning belongs in an explicit admin action, not boot.
    try:
        _purged = ChatDB.purge_orphan_pseudonym_maps()
        if _purged:
            print(f"[turn-recovery] purged {_purged} orphan pseudonym_maps row(s)",
                  flush=True)
    except Exception as e:
        print(f"[turn-recovery] pseudonym_maps purge failed: {e}", flush=True)

    try:
        rows = ChatDB.list_active_turns()
    except Exception as e:
        print(f"[turn-recovery] list_active_turns failed: {e}", flush=True)
        return
    if not rows:
        return
    print(f"[turn-recovery] found {len(rows)} in-flight turn(s) from prior process",
          flush=True)
    for (sid, turn_id, model, started_at) in rows:
        t = threading.Thread(
            target=_recover_one_turn,
            args=(sid, turn_id, model, started_at),
            daemon=True,
            name=f"turn-recover-{turn_id[:8]}",
        )
        t.start()


def _recover_one_turn(sid, turn_id, model, started_at):
    """One recovery worker. See `recover_active_turns_on_boot` for context."""
    import urllib.error
    import urllib.request
    from handlers import sidecar_proxy as _sp
    try:
        session = sessions.get(sid)
    except Exception as e:
        print(f"[turn-recovery] session load failed sid={sid[:8]} turn={turn_id[:8]}: {e}",
              flush=True)
        try:
            ChatDB.clear_active_turn(sid, turn_id)
        except Exception:
            pass
        return
    if session is None:
        print(f"[turn-recovery] session {sid[:8]} not found — clearing row turn={turn_id[:8]}",
              flush=True)
        try:
            ChatDB.clear_active_turn(sid, turn_id)
        except Exception:
            pass
        return

    # Re-establish thread-local context for tools that might still fire on
    # in-flight rounds (some tools peek at current_session_id/current_agent).
    with engine.request_context():
        try:
            engine.get_request_context().current_session_id = sid
            engine.get_request_context().current_user_id = session.user_id or ""
            engine.get_request_context().current_agent = engine.AgentConfig(session.agent_id)
            engine.get_request_context().mcp_manager = engine._mcp_manager
            engine.get_request_context().project = session.project or ""
        except Exception:
            pass

        live = LiveStream()
        with session.lock:
            session._streaming = True
            session.live_stream = live
        event_callback, state = build_chat_event_callback(session, live, sid)
        _partial_reply = state["partial_reply"]
        _partial_tools = state["partial_tools"]
        _partial_thinking = state["partial_thinking"]
        _usage_totals = state["usage_totals"]
        created_files = state["created_files"]

        sc_url = _sp.sidecar_url() + f"/turn/{turn_id}/events?since=0"
        req = urllib.request.Request(sc_url, method="GET")
        req.add_header("Accept", "text/event-stream")

        xlate_state = {
            "round_index": 0,
            "block_types": {},
            "tool_uses": {},
            "tool_results": {},
            "turn_id": turn_id,
        }
        final_text = ""
        final_summary: dict = {}
        cancelled = False
        error_msg = None
        catastrophic = False

        try:
            resp = urllib.request.urlopen(req, timeout=1800.0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"[turn-recovery] sidecar 404 for turn={turn_id[:8]} — "
                      f"event log already purged; marking lost", flush=True)
                catastrophic = True
            else:
                error_msg = f"sidecar HTTP {e.code}: {e.reason}"
        except Exception as e:
            error_msg = f"sidecar transport {type(e).__name__}: {e}"

        if catastrophic or error_msg:
            # Recover what we can: persist whatever streaming_text the prior process
            # already wrote, tagged with a marker, then clear the row.
            try:
                prior_partial, _meta = ChatDB.get_streaming_text(sid)
            except Exception:
                prior_partial = ""
            partial = (prior_partial or "").strip()
            if partial:
                partial += "\n\n*(Server restart — turn lost)*"
                try:
                    session.add_message("assistant", partial, metadata={
                        "model": model, "partial": True, "recovery_lost": True,
                    })
                except Exception:
                    pass
            try:
                live.emit("error", {"message": error_msg or "turn log lost across restart"})
            except Exception:
                pass
            _finalize_recovery(session, live, sid, turn_id)
            return

        try:
            buf = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace")
                if line.startswith(":"):
                    continue
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    if buf:
                        try:
                            evt = json.loads(buf)
                        except Exception:
                            evt = None
                        buf = ""
                        if evt:
                            ev_type = evt.get("type", "")
                            data = evt.get("data") or {}
                            _sp._translate_anthropic_event(
                                ev_type, data, xlate_state, event_callback)
                            if ev_type == "done":
                                final_summary = data
                                final_text = data.get("final_text", "") or ""
                            elif ev_type == "error":
                                error_msg = data.get("message", "sidecar error")
                            elif ev_type == "cancelled":
                                cancelled = True
                    continue
                if line.startswith("data: "):
                    buf += line[6:]
                elif line.startswith("data:"):
                    buf += line[5:]
        except Exception as e:
            error_msg = error_msg or f"recovery drain {type(e).__name__}: {e}"

        # Persist assistant message + emit terminal `done` into LiveStream so any
        # tab attached via GET /v1/chat/stream sees the turn finish.
        try:
            reply = final_text or "".join(_partial_reply).strip()
            if reply:
                msg_metadata = {
                    "model": model,
                    "tokens_in": _usage_totals["tokens_in"],
                    "tokens_out": _usage_totals["tokens_out"],
                    "last_tokens_in": _usage_totals["last_tokens_in"],
                    "tokens": engine._estimate_conversation_tokens(session.messages),
                    "recovered": True,
                }
                if created_files:
                    msg_metadata["files"] = created_files
                if _partial_tools:
                    msg_metadata["tools"] = _sanitize_partial_tools(_partial_tools)
                if cancelled:
                    msg_metadata["partial"] = True
                    reply = reply + "\n\n*(Cancelled)*"
                elif error_msg and not final_text:
                    msg_metadata["partial"] = True
                    reply = reply + f"\n\n*(Recovery error: {str(error_msg)[:200]})*"
                session.add_message("assistant", reply, metadata=msg_metadata)
                done_data = {
                    "text": reply,
                    "tokens": msg_metadata["tokens"],
                    "max_context": session.max_context,
                    "model": model,
                    "tokens_in": _usage_totals["tokens_in"],
                    "tokens_out": _usage_totals["tokens_out"],
                    "last_tokens_in": _usage_totals["last_tokens_in"],
                }
                if created_files:
                    done_data["files"] = created_files
                live.emit("done", done_data)
            else:
                live.emit("error", {"message": error_msg or "no reply recovered"})
        except Exception as e:
            print(f"[turn-recovery] persistence failed turn={turn_id[:8]}: {e}",
                  flush=True)
            try:
                live.emit("error", {"message": str(e)})
            except Exception:
                pass
        finally:
            _finalize_recovery(session, live, sid, turn_id)
            print(f"[turn-recovery] done turn={turn_id[:8]} model={model[:24]} "
                  f"reply={len(final_text)}c rounds={final_summary.get('rounds', 0)} "
                  f"tools={final_summary.get('tool_calls_total', 0)} "
                  f"error={error_msg} cancelled={cancelled}", flush=True)


def _finalize_recovery(session, live, sid, turn_id):
    """Mirror the worker's `finally` block: clear streaming + active_turns row,
    drop the LiveStream attachment, scrub thread-locals."""
    if not live.done:
        try:
            live.emit("error", {"message": "Recovery thread exited without terminal event"})
        except Exception:
            pass
    try:
        with session.lock:
            session._streaming = False
            if session.live_stream is live:
                session.live_stream = None
    except Exception:
        pass
    try:
        ChatDB.set_streaming_text(sid, "")
    except Exception:
        pass
    try:
        ChatDB.clear_active_turn(sid, turn_id)
    except Exception:
        pass
    # Thread-locals: this is a daemon thread; let them die with it. No globals.


_DR_PHASE_LABELS = {
    "planning": "Planen", "searching": "Suchen", "reading": "Lesen",
    "writing": "Bericht schreiben",
}


def _build_deep_research_context(session, sid, message, saved_paths, web_urls, web_locked):
    """Assemble the context the turn already has — attachment text + curated
    web-source content + a short chat-history note — into (extra_context_str,
    history_summary_str) for run_research_chat. Best-effort: any piece that fails
    is simply omitted. Mirrors the normal-turn context sources so Deep Research
    'sees' the same material a regular answer would."""
    blocks = []
    # 1. Attachments uploaded in this session (read their extracted text).
    try:
        att_paths = _session_attachment_paths(sid)
        for p in att_paths[:8]:  # cap — a research corpus shouldn't be unbounded
            try:
                txt = engine.extract_attachment_text(p) or ""
            except Exception:
                txt = ""
            if txt.strip():
                blocks.append(f"### Anhang: {os.path.basename(p)}\n{txt[:8000]}")
    except Exception:
        pass
    # 2. Curated web sources (the Websuche basket) — fetch fresh, like a normal turn.
    try:
        if web_urls:
            _wire, _sources = _build_web_sources(web_urls, web_locked)
            for s in (_sources or []):
                if s.get("content"):
                    blocks.append(f"### Webquelle: {s.get('title') or s.get('url')}\n"
                                  f"{s['content'][:8000]}")
    except Exception:
        pass
    extra_context = "\n\n---\n\n".join(blocks) if blocks else None

    # 3. Short history note (the last few user/assistant turns) so the research
    #    stays coherent with the conversation. Kept brief — it steers, not floods.
    history_summary = None
    try:
        prior = [m for m in session.messages
                 if m.get("role") in ("user", "assistant")
                 and isinstance(m.get("content"), str)]
        # Exclude the just-added current user message (last one).
        prior = prior[:-1] if prior else prior
        if prior:
            tail = prior[-6:]
            history_summary = " | ".join(
                f"{m['role']}: {m['content'][:200]}" for m in tail)
    except Exception:
        pass
    return extra_context, history_summary


def _run_deep_research_turn(session, sid, message, live, *, saved_paths=None,
                            web_urls=None, web_locked=False):
    """Run a Deep Research turn (composer 🔬) and return a run_turn()-shaped
    {"reply": <markdown card>, "error": ..., "_dr_meta": {...}} dict so the normal
    persistence path handles it. Emits a SYNTHETIC tool_call/tool_result pair so
    the run shows in the chat stream like a tool call (with live phase progress),
    feeds the turn's existing context (attachments + web sources + history) into
    the research, and registers the report .md + .html as session artifacts via
    the standard _after_file_write pipeline (HTML last → client auto-opens it)."""
    from engine import deep_research

    # Synthetic tool_call so the chat view shows a live "deep_research" card with
    # phase progress — the same surface the model's own tool calls use. The
    # tool_use_id ties progress + the final result together.
    _tuid = f"dr-{uuid.uuid4().hex[:12]}"
    try:
        live.emit("tool_call", {
            "tool_use_id": _tuid, "name": "deep_research",
            "args": {"topic": message},
        })
    except Exception:
        pass

    def _progress(phase, **counts):
        label = _DR_PHASE_LABELS.get(phase, phase)
        try:
            live.emit("tool_progress", {
                "tool_use_id": _tuid,
                "tool_name": "deep_research",
                "phase": label,
                "note": " · ".join(f"{k}: {v}" for k, v in counts.items() if v),
            })
        except Exception:
            pass

    def _cancelled():
        try:
            return bool(session.cancel_token and session.cancel_token.cancelled)
        except Exception:
            return False

    extra_context, history_summary = _build_deep_research_context(
        session, sid, message, saved_paths or [], web_urls or [], web_locked)

    result = deep_research.run_research_chat(
        agent_id=session.agent_id, session_id=sid, topic=message,
        user_id=session.user_id or "", progress=_progress, cancelled=_cancelled,
        extra_context=extra_context, history_summary=history_summary,
        # The chat's selected model wins (composer pick, or the auto-router's
        # resolved choice — session.model is already concrete by this point).
        preferred_model=session.model)

    if not result.get("ok"):
        err = result.get("error", "unknown error")
        # Close the synthetic tool card with the error so it doesn't spin forever.
        try:
            live.emit("tool_result", {"tool_use_id": _tuid, "name": "deep_research",
                                      "result": f"({err})"})
        except Exception:
            pass
        if err == "cancelled":
            return {"reply": "*(Deep Research abgebrochen.)*", "error": None}
        return {"reply": f"*(Deep Research fehlgeschlagen: {err})*", "error": None}

    # Register both files as session artifacts + emit artifact_updated. The
    # _after_file_write pipeline does BOTH registration AND the SSE emit through
    # get_request_context().event_callback — but the chat WORKER thread doesn't
    # install that callback into its own context (only the tool-dispatch thread
    # does, per-call). So install a callback here that BOTH appends to this
    # turn's created_files (→ msg_metadata['files'] → chat-view badges) AND emits
    # artifact_updated to the live stream (→ right panel). HTML registered LAST →
    # the client auto-opens it. We collect the registered file records so the
    # caller can stuff them into the turn metadata even though _after_file_write's
    # own callback path is what the normal flow relies on.
    _dr_files = []

    def _dr_artifact_cb(event_type, data):
        if event_type in ("file_created", "artifact_updated"):
            _dr_files.append(data)
        try:
            live.emit(event_type, data)
        except Exception:
            pass

    try:
        _ctx = engine.get_request_context()
        _prev_cb = _ctx.event_callback
        _ctx.event_callback = _dr_artifact_cb
        try:
            engine._after_file_write(result["md_path"], "created", session.agent_id)
            engine._after_file_write(result["html_path"], "created", session.agent_id)
        finally:
            _ctx.event_callback = _prev_cb
    except Exception as _e:
        print(f"[deep_research] artifact register failed: {_e}", flush=True)

    meta = result.get("meta") or {}
    stats = result.get("stats") or {}
    cat_label = {
        "product": "Produkt-Recherche", "comparison": "Vergleich",
        "howto": "Anleitung", "factcheck": "Faktencheck",
    }.get(result.get("category", "report"), "Recherchebericht")
    # Close the synthetic tool card with a short result summary.
    try:
        live.emit("tool_result", {
            "tool_use_id": _tuid, "name": "deep_research",
            "result": (f"{cat_label}: {stats.get('sources', '?')} Quellen, "
                       f"{stats.get('urls', '?')} URLs, {stats.get('duration', '')}")})
    except Exception:
        pass

    note = ""
    if extra_context:
        note = " Anhänge/Websuche/Verlauf wurden einbezogen."
    stat_line = " · ".join(filter(None, [
        f"{stats.get('sources', '?')} Quellen",
        f"{stats.get('urls', '?')} URLs gelesen",
        stats.get("duration", ""),
        f"${meta.get('cost', 0):.4f}" if meta.get("cost") else "",
    ]))
    card = (
        f"## {cat_label} erstellt\n\n"
        f"**Thema:** {message}\n\n"
        f"Der vollständige Bericht öffnet sich rechts im **Artefakte-Panel** "
        f"(`{result.get('html_name', 'research.html')}`) — hochwertige HTML-Ansicht, "
        f"als PDF druckbar. Das Markdown (`{result.get('md_name', 'research.md')}`) "
        f"liegt als Quelle daneben.{note}\n\n"
        f"*{stat_line}*\n\n"
        f"Fragen Sie mich gern etwas zum Bericht."
    )
    return {"reply": card, "error": None, "_dr_meta": meta, "_dr_files": _dr_files}


def run_session_turn(session, *, sid, message, user_content, chat_mode, thinking_level, live, saved_paths, web_urls, web_locked, project_name, preamble_text, content_blocks, disk_files, auto_route, want_auto, deep_research=False):
    """Run one chat turn for `session`, end to end.

    Extracted verbatim from the former `_handle_chat.worker()` closure (it
    referenced no `self`/`body`, only these locals + module globals — verified
    before extraction). Both the HTTP path (POST /v1/chat) and the server-side
    auto-fire path (background-task delivery) build the same params and call
    this. Spawns the worker thread and returns it so the caller can stream.
    """
    event_callback, _cb_state = build_chat_event_callback(session, live, sid)
    # Local aliases over the factory's state dict — the worker body below
    # reads these accumulators after the loop returns. Shared mutation:
    # any append the callback does is visible here and vice versa.
    created_files = _cb_state["created_files"]
    _partial_reply = _cb_state["partial_reply"]
    _partial_tools = _cb_state["partial_tools"]
    _partial_thinking = _cb_state["partial_thinking"]
    _thinking_summary = _cb_state["thinking_summary"]
    _usage_totals = _cb_state["usage_totals"]
    _request_payloads = _cb_state["request_payloads"]
    # Classifier tool-gating decision for this turn (set in the domain-context
    # block below); persisted on the turn metadata for the classification modal.
    _gating_decision = None
    _nudge_count = _cb_state["nudge_count"]

    def _rollback_messages(session, sid, target_count):
        """Rollback session.messages to target_count and remove extras from DB.
        Handles intermediate tool_use/tool_result messages from the agentic loop."""
        with session.lock:
            extras = len(session.messages) - target_count
            if extras <= 0:
                return
            session.messages = session.messages[:target_count]
        # Delete the extra messages from DB (they were appended by send_message's tool loop)
        try:
            with _db_conn() as conn:
                # Get all message IDs for this session, ordered by id
                rows = conn.execute(
                    "SELECT id FROM messages WHERE session_id = ? ORDER BY id",
                    (sid,)
                ).fetchall()
                # Keep only the first target_count messages
                if len(rows) > target_count:
                    ids_to_delete = [r[0] for r in rows[target_count:]]
                    conn.executemany("DELETE FROM messages WHERE id = ?", [(mid,) for mid in ids_to_delete])
                    conn.commit()
        except Exception as e:
            print(f"  [WARN] Message rollback DB cleanup: {e}", flush=True)

    def worker():
        with engine.request_context():
            # Set thread-local agent context (thread-safe, no global mutation)
            agent_config = engine.AgentConfig(session.agent_id)
            engine.get_request_context().current_agent = agent_config
            engine.get_request_context().current_session_id = sid
            engine.get_request_context().current_user_id = session.user_id or ""
            # Cost-ledger use-case tag — per-round cost_log rows for this turn
            # land in the "Chat" bucket of the per-use-case breakdown.
            engine.get_request_context().cost_purpose = "chat"
            # (team_ids are set below by apply_domain_context — single source)

            # Reset per-request state (prevents cross-session leaks in pooled threads)
            engine.reset_tool_dedup()
            # Per-turn artifact accumulator shared with the tool-DISPATCH thread.
            # File-write events fire in brain._after_file_write on that separate
            # thread (its event_callback is make_artifact_event_callback), so the
            # worker's own `created_files` list never sees them — which is why the
            # chat-view file badge showed live (via the LiveStream) but vanished
            # on reload (metadata.files was never persisted). The dispatch
            # callback appends here; the worker merges it at persist time.
            with session.lock:
                session._turn_created_files = []

            # Use shared MCP manager (singleton from main())
            engine.get_request_context().mcp_manager = engine._mcp_manager

            # Set plan mode if requested
            engine.get_request_context().plan_mode = (chat_mode == "plan")

            # Domain context (project scope, team_ids, research_mode,
            # disable_web_search lockout) — the SAME shared function the
            # scheduler calls, so a chat and a scheduled task in the same
            # domain run identical logic. The Websuche-basket lockout
            # (web_locked) is passed as the base exclusion; the project
            # web lockout is unioned on top inside apply_domain_context.
            if project_name:
                session.project = project_name
            _base_excl = (["web_fetch", "exa_search", "searxng_search"]
                          if web_locked else None)
            engine.apply_domain_context(
                agent_id=session.agent_id,
                project=(project_name or session.project or ""),
                user_id=session.user_id or "",
                research_mode_override=getattr(session, "research_mode_override", None),
                base_exclude_tools=_base_excl,
            )

            # Classifier-driven tool gating (auto-route + LLM classifier only):
            # reshape this turn's tool DEFERRAL toward the groups the classifier
            # flagged as needed — un-needed groups deferred OUT (still
            # tool_search-discoverable, NOT excluded), needed groups un-deferred
            # IN — but ONLY for models that don't keep a warm KV prefix
            # (brain.classifier_tool_deferral gates that internally; warm/local
            # models get ([],[]) so their static deferral + prefix are untouched
            # on every turn). This does NOT change the model — only deferral.
            _auto_groups = getattr(session, "_auto_tool_groups", None)
            _auto_rm = getattr(session, "_auto_route_model", "") or session.model
            # ATTACHMENT → documents IN-PROMPT (UX, not correctness): when the
            # user attached files this turn, surface the `documents` group
            # (read_document) directly in the prompt so the model doesn't waste a
            # round on a tool_search hop for a tool the attachment notice
            # explicitly tells it to use. The classifier only sees the typed text
            # ("wer hat mehr verbraucht…"), which rarely mentions files, so it
            # often omits `documents`. (Correctness is handled separately: a
            # deferred tool is now dispatchable anyway — see
            # _dispatchable_allowed_tools in sidecar_proxy; this just saves the
            # round-trip.) Only meaningful when the classifier produced a list.
            # Fire when EITHER this turn uploaded files (saved_paths) OR the
            # session already has documents on disk from an earlier turn — so a
            # follow-up question about a previously-attached file still gets
            # read_document in-prompt (chat 29ce67d2: turn 2 had no upload, the
            # classifier deferred read_document, the model never re-read the xlsx).
            _has_session_docs = any(
                os.path.splitext(p)[1].lower() in _DOC_ATTACH_EXTS
                for p in _session_attachment_paths(sid))
            if (saved_paths or _has_session_docs) and isinstance(_auto_groups, list) and "documents" not in _auto_groups:
                _auto_groups = _auto_groups + ["documents"]
            # `_auto_groups` is a LIST when the classifier ran (possibly EMPTY =
            # "no tool groups needed" → defer everything to the floor), or None
            # when there was no signal (keyword fallback / down classifier →
            # leave static deferral). Gate on `is not None`, NOT truthiness, so
            # an empty list still trims (the 11k-"hi" fix).
            _has_signal = _auto_groups is not None
            # Capture the decision (applied or not) so the per-turn
            # classification modal can show what was done with tools.
            _gating_decision = (engine.classifier_gating_decision(_auto_rm, _auto_groups)
                                if _has_signal else None)
            if _has_signal:
                _defer_extra, _undefer = engine.classifier_tool_deferral(_auto_rm, _auto_groups)
                if _defer_extra or _undefer:
                    _ctx = engine.get_request_context()
                    _ctx.defer_extra_tools = _defer_extra
                    # UNION, don't clobber: apply_domain_context may have already
                    # set undefer_tools (code-mode promotes code_graph_* in-prompt).
                    # A bare assign here would drop that and re-defer them.
                    _undefer = list(dict.fromkeys((_ctx.undefer_tools or []) + list(_undefer or [])))
                    _ctx.undefer_tools = _undefer

            # Set note context for AI-assisted note editing
            if session.note_context:
                engine.get_request_context().note_context = session.note_context
            else:
                engine.get_request_context().note_context = None

            # Workflow-run binding: when this session was created from the
            # inline workflow detail view, expose the execution_id so the
            # round-0 preamble can pull a compact summary of the run.
            engine.get_request_context().workflow_run_id = getattr(session, 'workflow_run_id', '') or ''

            # Set caveman modes: chat-level (session toggle) + system-level (model config)
            engine.get_request_context().caveman_chat = session.caveman_mode
            model_cfg = engine.resolve_model_settings(session.model) if engine._models_config else {}
            engine.get_request_context().caveman_system = int(model_cfg.get("caveman_system", 0) or 0)

            # Set worker subagent execution overrides from agent config
            engine.get_request_context().execution_overrides = agent_config.config.get("execution_overrides") or {}

            # Set attachment image model for read_attachment vision support
            engine.get_request_context().attachment_image_model = server_config.get("attachment_image_model", "")

            # Set current model for worker summariser (cache reuse)
            engine.get_request_context()._current_model = session.model

            # Snapshot message count for rollback on failure
            _msg_count_before = len(session.messages)
            _req_start = time.time()

            try:
                # ── Transparent anonymisation (worker-side) ──
                # If the client requested anonymise, this is where it runs.
                # We're inside the SSE response, so synthetic events + the
                # recovery-prompt event reach the client immediately. On
                # success: append the anonymised user message to
                # session.messages and continue. On failure: emit the
                # recovery-required event, block on the Event, branch to
                # local-model or cancel. On cancel: emit done + return.
                nonlocal_message = message
                nonlocal_user_content = user_content
                if session._gdpr_pending_action == "anonymise":
                    # Reuse the session's existing mapping when one exists —
                    # same session = same PII scope, so a value pseudonymised
                    # in turn 1 must map to the same token in turn 2. Minting
                    # a fresh mapping per turn would (a) break cross-turn
                    # token stability (model sees different placeholders for
                    # the same person), (b) re-scan + re-emit synthetic rows
                    # for already-known values, and (c) leave a graveyard of
                    # one-shot pseudonym_maps rows in chats.db.
                    _mapping = None
                    try:
                        _prior_maps = ChatDB.list_pseudonym_maps_for_session(sid) or []
                        if _prior_maps:
                            _latest_mid = _prior_maps[-1][0]
                            _mapping = pseudonymizer.get_mapping(_latest_mid)
                            if _mapping is None:
                                _mapping = pseudonymizer.load_mapping(_latest_mid)
                                if _mapping is not None:
                                    pseudonymizer.restore_mapping_to_registry(_mapping)
                    except Exception:
                        _mapping = None
                    _mapping_reused = _mapping is not None
                    if _mapping is None:
                        _mapping = pseudonymizer.new_mapping()
                    _anon_tool_id = f"anon_{_mapping.mapping_id[:12]}"
                    _t0 = time.time()
                    # Upfront row: this step pseudonymises the typed text
                    # and installs the per-turn mapping. Attachments are NOT
                    # rewritten on disk anymore — `tool_read_document` /
                    # `tool_read_file` pseudonymise extracted text on the
                    # way back to the LLM, emitting their own
                    # `anonymise_read` synthetic rows when findings are
                    # added to this same mapping.
                    _pending_attachments = [
                        os.path.basename(p) for p in saved_paths
                    ]
                    _emit_synthetic_tool_event(
                        live=live, sid=sid, kind="anonymise",
                        tool_use_id=_anon_tool_id, phase="dispatch",
                        args={
                            "scope": "chat_text",
                            "pending_on_read": _pending_attachments,
                            "mapping": "reused" if _mapping_reused else "new",
                        },
                    )
                    # Keep _anon_sources around for the error-path audit
                    # summaries below — those still want the full list.
                    _anon_sources = ["chat_text"] + [
                        f"attachment:{n}" for n in _pending_attachments
                    ]
                    _anon_ok = False
                    try:
                        _scanner_cfg = engine._get_gdpr_scanner_config()
                        # Scan ONLY the user-typed slice. The trailing
                        # attachment notice (`[User attached files saved to
                        # disk. IMPORTANT: …]\n  - /tmp/…/<filename>`) is
                        # Brain-generated boilerplate + literal disk paths
                        # the LLM needs verbatim to call read_document.
                        # spaCy NER otherwise misclassifies "IMPORTANT" as
                        # organisation and filenames as addresses; the
                        # resulting fake path makes read_document fail with
                        # "file not found". Splice the notice back onto the
                        # pseudonymised typed text so the rest of the
                        # pipeline sees the same shape it always did.
                        _typed, _notice = _split_attachment_notice(
                            nonlocal_message)
                        _findings = engine._pii_scan_text(
                            _typed, cfg=_scanner_cfg)
                        # Honour false-positive decisions: a value the user
                        # marked "falsch erkannt" in this chat must NOT be
                        # anonymised. Filter those findings out before
                        # pseudonymising (matched on the finding's value).
                        _findings = _filter_pii_false_positives(
                            _findings, _typed, session.id)
                        if _findings:
                            _pseudo = pseudonymizer.pseudonymize_text(
                                _typed, _findings,
                                mapping=_mapping, source="chat_text")
                            _anonymised = _pseudo + _notice
                            if isinstance(nonlocal_user_content, str):
                                nonlocal_user_content = _anonymised
                            else:
                                for _blk in nonlocal_user_content:
                                    if _blk.get("type") == "text":
                                        _blk["text"] = _anonymised
                                        break
                            nonlocal_message = _anonymised
                        # Locate real-PII spans in the ORIGINAL user text
                        # so the chat UI can paint them with the same
                        # yellow <mark> overlay it uses on assistant
                        # replies. Done after pseudonymisation (so
                        # _mapping.forward is populated) but BEFORE we
                        # save the message — we attach the result both
                        # to the persisted metadata and to the SSE
                        # anonymise_done event below so the live render
                        # picks it up without a reload.
                        try:
                            _live_user_spans = pseudonymizer.find_restored_spans(
                                user_content if isinstance(user_content, str) else (
                                    next((b.get("text", "") for b in (user_content or [])
                                          if isinstance(b, dict) and b.get("type") == "text"), "")
                                ),
                                mapping=_mapping,
                            )
                        except Exception:
                            _live_user_spans = []
                        # Eager mapping install — always persist + install on
                        # the session, even when typed text had no findings.
                        # Tool calls later in the turn (read_document /
                        # read_file / read_attachment) will add to this same
                        # mapping when they scan extracted attachment text.
                        # The streaming deanonymizer reverses every token
                        # before the user sees the assistant reply.
                        pseudonymizer.save_mapping(
                            _mapping, session_id=sid, turn_id=_anon_tool_id)
                        session._gdpr_mapping_id = _mapping.mapping_id
                        session._gdpr_streamer = StreamingDeanonymizer(_mapping)
                        # (Anonymise decisions — original→fake — are recorded
                        # once at turn END from the COMPLETE mapping; see the
                        # worker finally. Doing it here would miss attachment
                        # values minted later by read_document/read_file.)
                        # Per-turn flag read by `_build_system_prompt` post-
                        # process to append the verbatim-token-preservation
                        # clamp. Cleared in the worker's finally below.
                        engine.get_request_context()._gdpr_anonymising = True
                        _anon_done_result = {
                            "scope": "chat_text",
                            "findings": len(_findings),
                            "tokens_minted": len(_mapping.forward),
                            "categories": dict(_mapping.finding_counts),
                            "pending_on_read": _pending_attachments,
                            "mapping": "reused" if _mapping_reused else "new",
                            "mapping_id": _mapping.mapping_id,
                        }
                        # Stash the per-turn outcome so the worker can surface
                        # the feedback modal on the assistant turn. `restored`
                        # is filled in later (after the reply is de-anonymised).
                        #
                        # `active` = this turn ACTIVELY anonymised the user's
                        # OWN input (typed PII or an attachment submitted now),
                        # vs. merely re-pseudonymising prior chat history for the
                        # wire (which happens on every turn of a sticky-anonymise
                        # session even when the user typed clean text). The
                        # feedback modal fires only on active turns — asking "did
                        # it work?" about untouched history would be noise.
                        engine.get_request_context()._gdpr_turn_outcome = {
                            "mode": "anonymise",
                            "findings": len(_findings),
                            "tokens_minted": len(_mapping.forward),
                            "mapping_reused": _mapping_reused,
                            "active": bool(_findings) or bool(_pending_attachments),
                        }
                        if _live_user_spans:
                            # Side-channel: the chat client pulls these
                            # off the synthetic anonymise_done event and
                            # attaches them to the just-appended user
                            # message so the inline yellow <mark>
                            # overlay renders on the request side too,
                            # matching the assistant-side behavior.
                            _anon_done_result["user_spans"] = _live_user_spans
                        _emit_synthetic_tool_event(
                            live=live, sid=sid, kind="anonymise",
                            tool_use_id=_anon_tool_id, phase="done",
                            result=_anon_done_result,
                            status="ok",
                            duration_ms=int((time.time() - _t0) * 1000),
                        )
                        if engine._audit_log:
                            try:
                                engine._audit_log.log_action(
                                    agent=session.agent_id, session_id=sid,
                                    action_type="pii_anonymised",
                                    tool_name="gdpr_scanner",
                                    args_summary=f"{len(_findings)} findings",
                                    result_summary=(
                                        f"mapping_id={_mapping.mapping_id} "
                                        f"categories={list(_mapping.finding_counts)}"),
                                    result_status="success",
                                    duration_ms=int((time.time() - _t0) * 1000),
                                    source="chat",
                                )
                            except Exception:
                                pass
                        _anon_ok = True
                    except Exception as _e:
                        _err_summary = f"{type(_e).__name__}: {str(_e)[:200]}"
                        _emit_synthetic_tool_event(
                            live=live, sid=sid, kind="anonymise",
                            tool_use_id=_anon_tool_id, phase="done",
                            result={"error": _err_summary,
                                    "sources": _anon_sources},
                            status="error",
                            duration_ms=int((time.time() - _t0) * 1000),
                        )
                        try:
                            pseudonymizer.delete_persisted_mapping(_mapping.mapping_id)
                            pseudonymizer.close_mapping(_mapping.mapping_id)
                        except Exception:
                            pass
                        if engine._audit_log:
                            try:
                                engine._audit_log.log_action(
                                    agent=session.agent_id, session_id=sid,
                                    action_type="pii_anonymise_failed",
                                    tool_name="gdpr_scanner",
                                    args_summary=",".join(_anon_sources),
                                    result_summary=_err_summary,
                                    result_status="error",
                                    duration_ms=int((time.time() - _t0) * 1000),
                                    source="chat",
                                )
                            except Exception:
                                pass
                        live.emit("gdpr_recovery_required", {
                            "session_id": sid,
                            "error": _err_summary,
                            "sources": _anon_sources,
                        })
                        _event = _gdpr_recovery_register(sid)
                        _delivered = _event.wait(timeout=300)
                        with _gdpr_recovery_lock:
                            _choice = (_gdpr_recovery_pending.get(sid) or {}).get("choice")
                        _gdpr_recovery_clear(sid)
                        if not _delivered or _choice == "cancel":
                            if engine._audit_log:
                                try:
                                    engine._audit_log.log_action(
                                        agent=session.agent_id, session_id=sid,
                                        action_type="pii_anonymise_failed_cancel",
                                        tool_name="gdpr_scanner",
                                        args_summary=",".join(_anon_sources),
                                        result_summary=(
                                            "timeout" if not _delivered else "user_cancelled"),
                                        result_status="warning",
                                        duration_ms=0, source="chat",
                                    )
                                except Exception:
                                    pass
                            live.emit("done", {
                                "text": "", "tokens": 0, "model": session.model,
                                "cancelled": True, "reason": "gdpr_anonymise_failed",
                            })
                            return
                        # local_model: swap, use ORIGINAL content.
                        _fallback = (engine._get_gdpr_scanner_config().get(
                            "default_local_fallback_model") or "").strip()
                        if not _fallback:
                            live.emit("error", {
                                "message": "Anonymisation failed and no local "
                                           "fallback model is configured."})
                            live.emit("done", {
                                "text": "", "tokens": 0, "model": session.model,
                                "cancelled": True,
                                "reason": "gdpr_no_local_fallback",
                            })
                            return
                        if _fallback != session.model:
                            try:
                                provider = engine.resolve_provider_for_model(_fallback)
                                with session.lock:
                                    session.model = _fallback
                                    session.api_key = provider["api_key"]
                                    session.base_url = provider["base_url"]
                                    session.max_context = engine.get_model_max_context(_fallback)
                                # Update thread-local model reference too.
                                engine.get_request_context()._current_model = session.model
                            except Exception:
                                pass
                        if engine._audit_log:
                            try:
                                engine._audit_log.log_action(
                                    agent=session.agent_id, session_id=sid,
                                    action_type="pii_anonymise_failed_local_swap",
                                    tool_name="gdpr_scanner",
                                    args_summary=",".join(_anon_sources),
                                    result_summary=f"→ {_fallback}",
                                    result_status="success",
                                    duration_ms=0, source="chat",
                                )
                            except Exception:
                                pass
                        # Per-turn outcome: anonymise failed, recovered by
                        # sending the ORIGINAL text to the local fallback model.
                        # Always active — an explicit per-turn recovery happened.
                        engine.get_request_context()._gdpr_turn_outcome = {
                            "mode": "anonymise_failed_local",
                            "model": _fallback,
                            "error": _err_summary,
                            "active": True,
                        }
                        # Fall through with ORIGINAL content + new local model.
                    # User message wasn't added pre-worker for the anonymise
                    # path. Add it now — anonymised on success, original on
                    # local-fallback recovery. Update the rollback snapshot
                    # so it INCLUDES the new user msg (matches non-anonymise
                    # path semantics: rollback strips intermediate tool msgs
                    # but keeps the user msg in place).
                    #
                    # On anonymise SUCCESS: in-memory `session.messages` holds
                    # the pseudonymised text (what goes on the wire to the
                    # cloud LLM on this turn), but the DB row stores the
                    # ORIGINAL text the user typed (so the session inspector,
                    # chat reload, and audit trail show real values — same
                    # symmetry as assistant replies, which are persisted
                    # de-anonymised). The mapping_id rides in metadata so the
                    # admin audit view can still link the row to the
                    # decryption record. On local-fallback recovery `_anon_ok`
                    # is False and `nonlocal_user_content == user_content`,
                    # so both paths persist the same text — no split needed.
                    if _anon_ok and nonlocal_user_content is not user_content:
                        # Split persistence: in-memory `session.messages`
                        # holds the pseudonymised text (what the cloud LLM
                        # receives on this turn), the DB row holds the
                        # ORIGINAL (so the chat UI and reload show real
                        # values). `metadata.wire_content` is the wire-
                        # truth — the session inspector renders it side-
                        # by-side with the original so an auditor can
                        # confirm what actually left the box.
                        with session.lock:
                            _msg = {"role": "user", "content": nonlocal_user_content}
                            session.messages.append(_msg)
                            session.last_active = time.time()
                            if not session.title:
                                from server import _derive_session_title
                                _t = user_content if isinstance(user_content, str) else str(user_content)
                                session.title = _derive_session_title(_t)
                        # Locate every real-PII span in the persisted user
                        # text so the chat UI can highlight them with the
                        # same `<mark class="gdpr-restored">` overlay it
                        # uses on assistant replies. The persisted text
                        # IS the original (with real values), so
                        # find_restored_spans walks the mapping's
                        # `forward` (real→fake) and reports where each
                        # real value sits. For multimodal blocks we
                        # scan the text block(s) only and emit spans
                        # per-block; the renderer pulls them off
                        # `metadata.gdpr_restored_spans`.
                        try:
                            if isinstance(user_content, str):
                                _user_spans = pseudonymizer.find_restored_spans(
                                    user_content, mapping=_mapping)
                            else:
                                _user_spans = []
                                for _b in (user_content or []):
                                    if isinstance(_b, dict) and _b.get("type") == "text":
                                        _user_spans = pseudonymizer.find_restored_spans(
                                            _b.get("text") or "", mapping=_mapping)
                                        if _user_spans:
                                            break
                        except Exception:
                            _user_spans = []
                        _user_meta = {
                            "gdpr_mapping_id": _mapping.mapping_id,
                            "wire_content": nonlocal_user_content,
                        }
                        if _user_spans:
                            _user_meta["gdpr_restored_spans"] = _user_spans
                        if preamble_text:
                            _user_meta["preamble"] = preamble_text
                        ChatDB.save_message(
                            sid, "user", user_content,
                            metadata=_user_meta)
                        ChatDB.save_session(
                            sid, session.agent_id, session.model, session.title,
                            session.status, session.created_at, session.last_active,
                            session.project or "", user_id=session.user_id)
                    else:
                        session.add_message(
                            "user", nonlocal_user_content,
                            metadata=({"preamble": preamble_text}
                                      if preamble_text else None))
                    _msg_count_before = len(session.messages)

                # --- Standard backend ---
                # Use detected purpose from auto-resolve, or fall back to agent's fixed purpose.
                # In the anonymise branch above, `nonlocal_message` is the
                # pseudonymised text; in every other branch it's just the
                # original `message` we copied at function entry. We use
                # `nonlocal_message` directly here — assigning back to
                # `message` would mark `message` as a worker-local for the
                # whole function (Python decides scope at compile-time),
                # and the `nonlocal_message = message` snapshot at the
                # top of the try would crash with UnboundLocalError because
                # the outer-scope `message` is shadowed.
                purpose = session.agent.config.get("model_purpose")
                if not purpose and session.agent.config.get("model") == "auto":
                    purpose = engine.classify_task_purpose(nonlocal_message)
                inf_params = engine.get_inference_params(session.model, purpose)
                # Apply thinking level from request — only when the model supports thinking.
                _model_cfg = engine._models_config.get(session.model, {}) or {}
                _tfmt = _model_cfg.get("thinking_format", "none")
                if thinking_level and thinking_level != "none" and _tfmt != "none":
                    _THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 32768}
                    inf_params["thinking"] = True
                    inf_params["thinking_budget"] = _THINKING_BUDGETS.get(thinking_level, 8192)
                    # Provider-facing reasoning toggle. Engine's _apply_inference_to_payload maps this
                    # per thinking_format: reasoning_effort for mistral_blocks/reasoning_field/openai_opaque,
                    # chat_template_kwargs.enable_thinking for oMLX inline_tags variants, etc.
                    inf_params["thinking_level"] = thinking_level
                else:
                    inf_params.pop("thinking", None)
                    inf_params.pop("thinking_budget", None)
                    inf_params.pop("thinking_level", None)
                # NOTE: the thinking-mode KV prefix is now warmed naturally —
                # mark_prefix_used (after the prefix build below) records the
                # exact prefix each turn hits, thinking included where it changes
                # the tokenised prompt (oMLX enable_thinking). So a thinking turn
                # warms its own prefix on first use; no speculative re-prime of
                # the opposite mode is needed (and the old model-keyed re-prime
                # ping-ponged against the keeper). engine.maybe_reprime_for_thinking
                # remains available for callers that want to PRE-warm a known
                # prefix, but the chat path no longer fires it blind.

                # --- Auto-LCM (per-model automatic context compaction) ---
                # Runs BEFORE the wire build (session.messages is read at the wire
                # assembly below) and AFTER the final model + max_context are
                # settled (auto-route in _handle_chat + the in-worker GDPR swap
                # above). Per-MODEL setting, keyed on the FINAL model. Mutates
                # session.messages + persists like the manual ✂️ button. Skip on
                # recovery turns (the recovery path has its own message handling)
                # and when no context_manager is wired.
                _lcm_state = None
                try:
                    if (engine._context_manager
                            and engine._auto_lcm_enabled(session.model)
                            and not getattr(session, "_is_recovery_turn", False)):
                        _lcm_state = engine._context_manager.auto_balance(
                            session, session.max_context, emit=live.emit)
                        if _lcm_state and _lcm_state.get("ran"):
                            engine.get_request_context()._lcm_state = _lcm_state
                        if _lcm_state and _lcm_state.get("still_over"):
                            # Even max compaction left us over threshold — let the
                            # user decide (retry / new chat w/ handover / fresh).
                            live.emit("auto_lcm_over_threshold", _lcm_state)
                except Exception as _lcm_e:
                    print(f"[chat] auto-lcm skipped: {_lcm_e}", flush=True)

                # Sidecar path: build the system prompt, hand the loop over
                # to the Anthropic SDK in the sidecar process. event_callback
                # translates sidecar SSE → Brain's LiveStream vocabulary, so
                # persistence, references, citation validation all stay on
                # this thread unchanged.
                # SHARED prefix builder — same function the warm-pool prime
                # (run_model_warmup) calls, so the first-turn system prompt +
                # tool set are byte-identical and oMLX reuses the warm KV prefix.
                # On turn 0 _discovered_tools is empty (matches warmup); the
                # anthropic wire-shape (is_openai_shape=False) only changes tool
                # serialization, not the KV-relevant prompt/name set.
                _tool_breakdown = {}
                _system_prompt, _active_tools, _active_tool_names = engine.build_first_turn_prefix(
                    session.model, session.agent_id,
                    mcp_manager=getattr(engine, "_mcp_manager", None),
                    discovered_tools=engine.get_request_context()._discovered_tools or set(),
                    is_openai_shape=False,
                    breakdown=_tool_breakdown,
                )
                # Record the prefix this turn actually hits as warm/used. Running
                # a real turn keeps its KV prefix resident on the GPU — using it
                # IS the best warmup — so the warmup mirror must reflect that, or
                # the keeper / session-warmup would re-prime an already-resident
                # prefix (the mid-session re-warm). thinking enters the prefix id
                # only when it changes the tokenised prompt (oMLX enable_thinking).
                try:
                    _think_in_prefix = (bool(inf_params.get("thinking"))
                                        and engine.prefix_thinking_relevant(session.model))
                    _pid = engine.compute_prefix_id(
                        _system_prompt, _active_tool_names, _think_in_prefix)
                    engine.mark_prefix_used(session.model, _pid)
                    # This turn's prefill evicts the model's other resident
                    # prefixes — mirror that so a stale prefix isn't reported warm.
                    engine.evict_prefixes_except(session.model, _pid)
                except Exception:
                    pass
                # Stash the GROUND-TRUTH per-turn tool resolution (in_prompt /
                # deferred / excluded — exactly what resolve_active_tools handed
                # the wire this turn) for the classification inspector. Runs on
                # EVERY turn (the classifier re-runs every turn), not just turn 0.
                try:
                    engine.get_request_context()._tool_breakdown = _tool_breakdown
                except Exception:
                    pass
                # Persist for the session inspector — overwritten per turn,
                # no history. Best-effort; persist failure must not block
                # the chat call.
                try:
                    with _db_conn() as _ssp_conn:
                        _ssp_conn.execute(
                            "UPDATE sessions SET last_system_prompt = ? WHERE id = ?",
                            (_system_prompt, sid))
                        _ssp_conn.commit()
                except Exception:
                    pass
                # Snapshot the request context into the tool_context dict —
                # the SAME shared builder the scheduler uses, so the
                # sidecar's per-tool-call context rebuild is identical in
                # both. gdpr_mapping_id carries the transparent-anonymise
                # mapping so the tool-dispatch thread installs the
                # _after_file_write callback that rewrites produced files
                # back to real values.
                _tool_context = engine.build_tool_context(
                    session_id=sid,
                    agent_id=session.agent_id,
                    user_id=session.user_id or "",
                    gdpr_mapping_id=getattr(session, "_gdpr_mapping_id", "") or "",
                )
                _sampling = {
                    "temperature": inf_params.get("temperature"),
                    "top_p": inf_params.get("top_p"),
                    "top_k": inf_params.get("top_k"),
                    "stop_sequences": inf_params.get("stop") or inf_params.get("stop_sequences"),
                }
                _max_tokens = int(inf_params.get("max_tokens", 16000) or 16000)
                _agent_cfg = session.agent.config or {}
                _max_rounds = int((_agent_cfg.get("limits") or {}).get("max_tool_rounds", 25) or 25)
                # Transparent anonymisation — DETERMINISTIC, ledger-driven.
                # Prior turns' user messages + assistant replies are persisted
                # de-anonymised (the chat UI shows real values), so without a
                # rewrite they'd ship to the cloud LLM in clear. We rebuild the
                # wire-history straight from the persisted PII decision ledger
                # (pii_decisions): every value the session EVER anonymised is
                # replaced by its stored fake on EVERY subsequent turn — no
                # re-scan, no new mints, and crucially INDEPENDENT of whether a
                # mapping is "live" this turn. This is the fix for the leak where
                # a value anonymised early went out in clear from the turn the
                # user first chose 'continue' for a NEW finding: the decision is
                # read from the ledger, not re-derived from the per-turn action.
                # Accepted / false-positive / local values stay in clear (the
                # user's choice); attachment-anonymised values are covered too
                # (their decisions are in the same ledger).
                # A LOCAL model never sends data off the machine, so the
                # wire-history pseudonymisation is pointless there — skip it so a
                # local turn sees the REAL history (a chat that was cloud+anonymise
                # then switched to a local model must not anonymise the local turn).
                _wire_messages = session.messages
                _wire_is_local = False
                try:
                    _wire_is_local = bool(engine.is_model_local(session.model))
                except Exception:
                    _wire_is_local = False
                try:
                    _pii_decisions = {} if _wire_is_local else ChatDB.get_session_pii_decisions(sid)
                except Exception:
                    _pii_decisions = {}
                if _pii_decisions:
                    _wire_messages, _hist_repl, _hist_counts = (
                        _apply_pii_decisions_to_wire(
                            session.messages, _pii_decisions))
                    if _hist_repl > 0:
                        try:
                            _tuid = (f"anon_hist_{sid[:8]}_"
                                     f"{int(time.time()*1000) % 1_000_000}")
                            emit_gdpr_tool_event_for_session(
                                sid,
                                kind="anonymise_read",
                                tool_use_id=_tuid,
                                args={"source": "history"},
                                result={
                                    "findings": _hist_repl,
                                    "tokens_minted": 0,
                                    "categories": _hist_counts,
                                    "source": "history",
                                },
                                status="ok",
                                duration_ms=0,
                            )
                        except Exception:
                            pass
                # Manual web-search: fetch the curated URLs FRESH now and
                # prepend their content to the wire copy of the last user
                # message only — ephemeral, never persisted into history, so
                # a re-send tomorrow re-fetches instead of replaying a stale
                # page. The fetched text IS recorded on the assistant turn's
                # metadata.web_sources (below) so the session inspector can
                # show exactly which content each turn used — today's
                # weather on today's turn, tomorrow's on tomorrow's —
                # without that content ever re-entering the conversation
                # (metadata is stripped before the wire by _ALLOWED_MSG_KEYS).
                _web_sources_used = []
                if web_urls:
                    _web_pre, _web_sources_used = _build_web_sources(
                        web_urls, web_locked)
                    if _web_pre:
                        _wire_messages = _inject_web_preamble_into_wire(
                            _wire_messages, _web_pre)
                # Detached background tasks: any that FINISHED since the last
                # turn have their full output folded into THIS turn wire-only
                # (same ephemeral seam as web sources — never persisted, so it
                # drops out of context after this turn exactly like a tool
                # result). pop_unconsumed marks them consumed in the same
                # transaction, so each task's output reaches the model once.
                _bg_pre = _build_background_task_preamble(sid)
                if _bg_pre:
                    _wire_messages = _inject_web_preamble_into_wire(
                        _wire_messages, _bg_pre)
                # Research-mode citation discipline — TWO mutually-exclusive paths,
                # chosen by the auto-route classifier mode:
                #  • LLM/hybrid mode → DYNAMIC: trigger on the EFFECTIVE active
                #    tools (_active_tool_names). If any retrieval tool is live
                #    (memory/web-search/web-fetch/doc-read/file-read) the answer
                #    can ground on sources → attach the discipline. ANY chat,
                #    project or not. The per-project research_mode flag is ignored
                #    (disabled in the UI under LLM mode — dynamic covers it).
                #    Keyed off the RESOLVED tool set, NOT the classifier's intent:
                #    the classifier only reduces tools, so its guess could wrongly
                #    suppress discipline on a turn that does retrieve.
                #  • keyword mode → MANUAL only: no dynamic trigger; the explicit
                #    project research_mode flag / session override is the control
                #    (and renders the discipline in the SYSTEM PROMPT, prompt_build).
                # Injected as a WIRE-ONLY preamble (not the system prompt) so the
                # warm-pool KV prefix stays byte-stable (fires for warm/local too).
                # Per-turn discipline record for the classification inspector:
                # whether the research-mode discipline was injected this turn,
                # HOW (wire preamble in LLM mode vs system prompt in keyword
                # mode), and exactly WHICH sections (refusal/precision/citation,
                # honouring admin per-section opt-out). Ground truth, not the
                # static default set. Stashed on the request context; the
                # metadata-build block folds it into auto_route.
                # Code Mode: the project has NO project memory / curated sources
                # — the model reads files in the working directory to DO work, not
                # to ground a cited answer. The grounded-answer / citation
                # discipline (and its validator) make no sense here and were
                # wrongly flagging "N von M ohne Quellenangabe" (chat 4866f92e).
                # working_dir is set on the context only for code-mode projects.
                _code_mode_chat = bool(engine.get_request_context().working_dir)
                _discipline_meta = None
                if _code_mode_chat:
                    session._citation_discipline_active = False
                    _discipline_meta = {"active": False, "trigger": "code_mode"}
                elif engine.classifier_is_llm():
                    _grounding = engine.turn_has_retrieval_tools(_active_tool_names)
                    session._citation_discipline_active = bool(_grounding)
                    if _grounding:
                        _disc = engine.render_research_mode_disciplines()
                        if _disc:
                            _disc_pre = ("GROUNDED-ANSWER DISCIPLINE (this answer draws on "
                                         "retrieved sources — cite + refuse per below):\n" + _disc)
                            _wire_messages = _inject_web_preamble_into_wire(_wire_messages, _disc_pre)
                            _discipline_meta = {
                                "active": True,
                                "source": "wire_preamble",
                                "trigger": "retrieval_tool_active",
                                "sections": engine.active_research_discipline_sections(),
                            }
                    if _discipline_meta is None:
                        _discipline_meta = {"active": False, "trigger": "no_retrieval_tool"}
                else:
                    # Keyword mode: the system-prompt path (prompt_build, gated on
                    # research_mode) already injected the discipline when the flag
                    # is on — record that so the validator runs to match.
                    _rm_on = bool(getattr(session, "research_mode_override", None))
                    if not _rm_on and engine.get_request_context().project:
                        _pc = engine.ProjectManager.get_project(session.agent_id, engine.get_request_context().project)
                        _rm_on = bool((_pc or {}).get("research_mode", False))
                    session._citation_discipline_active = bool(_rm_on)
                    if _rm_on:
                        _discipline_meta = {
                            "active": True,
                            "source": "system_prompt",
                            "trigger": "research_mode",
                            "sections": engine.active_research_discipline_sections(),
                        }
                    else:
                        _discipline_meta = {"active": False, "trigger": "research_mode_off"}
                try:
                    engine.get_request_context()._discipline_meta = _discipline_meta
                except Exception:
                    pass
                # Session attachments (v9.138.0): wire-only reminder, EVERY turn,
                # of the documents attached so far in this chat so a later turn
                # can still read them via read_document — the upload-turn notice
                # only fires on the turn the file arrived, so later turns
                # 'forgot' the file and the model answered from stale context
                # instead of re-reading it (chat 29ce67d2). Transient (never
                # persisted). Also forces read_document in-prompt when the
                # session has docs (read_document is otherwise classifier-
                # deferred on a turn whose text doesn't mention files).
                _att_suffix, _att_has_docs = _session_attachments_wire_suffix(sid)
                if _att_suffix:
                    _wire_messages = _append_to_wire_user(_wire_messages, _att_suffix)
                # Caveman OUTPUT-STYLE (v9.121.0): injected as a trailing wire-only
                # instruction on the last user message — NOT the system prompt and
                # NOT tool descriptions (warm-pool KV prefix stays byte-stable;
                # nothing caveman enters history). effective = session toggle, else
                # the per-model default.
                _cav_eff = (engine.get_request_context().caveman_chat
                            or engine.get_request_context().caveman_system)
                if _cav_eff and _cav_eff in engine.CAVEMAN_CHAT_PROMPTS:
                    _wire_messages = _append_to_wire_user(
                        _wire_messages, engine.CAVEMAN_CHAT_PROMPTS[_cav_eff])
                if deep_research:
                    # Deep Research turn (composer 🔬): run the bounded research
                    # loop INSTEAD of the LLM chat turn. It writes the report as
                    # .html + .md session artifacts and returns a card as the
                    # assistant reply; all downstream persistence/done logic below
                    # treats `_result` exactly like a normal run_turn() result.
                    # Passes the turn's existing context (attachments + curated
                    # web sources + chat history) into the research.
                    _result = _run_deep_research_turn(
                        session, sid, message, live,
                        saved_paths=saved_paths, web_urls=web_urls,
                        web_locked=web_locked)
                    # Fold the research run's usage into the turn totals so the
                    # status bar shows cost + tokens + context like a normal turn.
                    _dr_meta = _result.pop("_dr_meta", None) or {}
                    if _dr_meta:
                        _usage_totals["tokens_in"] += int(_dr_meta.get("tokens_in", 0) or 0)
                        _usage_totals["tokens_out"] += int(_dr_meta.get("tokens_out", 0) or 0)
                        _usage_totals["last_tokens_in"] = int(_dr_meta.get("tokens_in", 0) or 0)
                    # run_research_chat already logged each LLM call's cost to the
                    # ledger KEYED BY THIS chat sid (background_call session_id=sid),
                    # so get_session_cost(sid) below picks it up. Mark cost_logged
                    # so the generic aggregate-log path doesn't double-count.
                    _cb_state["cost_logged"] = True
                    # Carry the registered report files into this turn's
                    # created_files so they get msg_metadata['files'] → chat-view
                    # badges (like write_file). Dedup by path.
                    _dr_files = _result.pop("_dr_files", None) or []
                    _seen = {f.get("path") for f in created_files if isinstance(f, dict)}
                    for _f in _dr_files:
                        if isinstance(_f, dict) and _f.get("path") not in _seen:
                            created_files.append(_f)
                            _seen.add(_f.get("path"))
                else:
                    _result = sidecar_proxy.run_turn(
                        messages=_wire_messages,
                        model=session.model,
                        api_key=session.api_key,
                        base_url=session.base_url,
                        system_prompt=_system_prompt,
                        purpose="interactive",
                        tool_context=_tool_context,
                        sampling=_sampling,
                        thinking_level=(thinking_level if thinking_level and thinking_level != "none" else None),
                        max_tokens=_max_tokens,
                        max_rounds=_max_rounds,
                        event_callback=event_callback,
                        cancel_token=session.cancel_token,
                    )
                # On sidecar error: surface the message to the client AS PART
                # of the assistant reply, but stay on the happy path so the
                # downstream `done` event still fires. Raising here would
                # leave HTTP clients that only listen for `done` blocked.
                _se = _result.get("error")
                _sr = _result.get("reply") or ""
                if _se and not _sr:
                    reply = f"*(Sidecar error: {str(_se)[:300]})*"
                elif _se and _sr:
                    reply = _sr + f"\n\n*(Sidecar error after partial: {str(_se)[:200]})*"
                else:
                    reply = _sr
                if reply:
                    # Log this turn's token usage to the cost ledger. The native
                    # loop used to do this per-round; the SDK-sidecar migration
                    # (v9.0.0) dropped the write path, so interactive chats logged
                    # nothing and session cost read back as $0. Log once per turn
                    # from the accumulated usage totals, keyed by the model that
                    # actually answered (fallback model wins when one was used).
                    _cost_model = (engine.get_request_context()._fallback_model_used
                                   or session.model)
                    # Per-round logging (in the `usage` handler) already wrote each
                    # round's cost row. Only log the aggregate here if NO round
                    # ever did (e.g. a provider that returns usage only in the
                    # final message, so no per-round `usage` event fired) — guarded
                    # so we never double-count.
                    if not _cb_state.get("cost_logged"):
                        try:
                            engine._log_call_cost(
                                _cost_model,
                                _usage_totals["tokens_in"],
                                _usage_totals["tokens_out"],
                                session_id=sid,
                                api_key=session.api_key,
                            )
                        except Exception as _ce:
                            print(f"[chat] cost log failed: {_ce}")
                    # Compute cost before saving
                    session_cost = None
                    if engine._cost_tracker:
                        try:
                            sc = engine._cost_tracker.get_session_cost(sid)
                            session_cost = round(sc.get("cost", 0.0), 4)
                        except Exception:
                            pass
                    # Build metadata: model, tokens, cost, files, tools, duration, usage
                    _req_duration = round(time.time() - _req_start, 2)
                    msg_metadata = {}
                    msg_metadata["model"] = session.model
                    msg_metadata["duration"] = _req_duration
                    msg_metadata["tokens_in"] = _usage_totals["tokens_in"]
                    msg_metadata["tokens_out"] = _usage_totals["tokens_out"]
                    msg_metadata["last_tokens_in"] = _usage_totals["last_tokens_in"]
                    if _request_payloads:
                        msg_metadata["request_payloads"] = _request_payloads
                    fb_model = engine.get_request_context()._fallback_model_used
                    if fb_model:
                        msg_metadata["model"] = fb_model
                        msg_metadata["original_model"] = session.model
                    msg_metadata["tokens"] = engine._estimate_conversation_tokens(session.messages)
                    if session_cost is not None:
                        msg_metadata["cost"] = session_cost
                    # Merge files recorded on the tool-DISPATCH thread (they fire
                    # in _after_file_write off-worker, so the worker's own
                    # `created_files` never saw them — the reload-badge bug).
                    # Dedup by path; keep first occurrence. Mutates `created_files`
                    # in place so the done-event persist (below) picks it up too.
                    try:
                        with session.lock:
                            _dispatch_files = list(getattr(session, "_turn_created_files", None) or [])
                        if _dispatch_files:
                            _seen_paths = {f.get("path") for f in created_files if isinstance(f, dict)}
                            for _f in _dispatch_files:
                                if isinstance(_f, dict) and _f.get("path") not in _seen_paths:
                                    created_files.append(_f)
                                    _seen_paths.add(_f.get("path"))
                    except Exception:
                        pass
                    if created_files:
                        msg_metadata["files"] = created_files
                    # Deep Research turn: flag it on the metadata so the chat view
                    # marks the "Anfrage N" pill with a microscope icon (live + on
                    # reload). Read back in sessions.js → msg._deepResearch.
                    if deep_research:
                        msg_metadata["deep_research"] = True
                    # Manual web-search: record the exact fetched source text
                    # this turn used (the freshly-fetched, ephemeral wire
                    # preamble). Stored on the assistant turn's metadata so
                    # the session inspector can show per-turn which content
                    # was used; stripped before the wire so it never replays.
                    if _web_sources_used:
                        msg_metadata["web_sources"] = _web_sources_used
                    # Auto-LCM: record this turn's compaction level (before/after
                    # tokens + pct, turns compressed/total) on the turn metadata
                    # so the status line + the in-chat compacted block can show
                    # the compaction level after reload, not just live. Only when
                    # auto-LCM actually compacted/expanded this turn.
                    if _lcm_state and _lcm_state.get("ran"):
                        msg_metadata["lcm_state"] = _lcm_state
                    # Persist the auto-route classification + routing decision on
                    # the turn (like web_sources) so the per-turn classification
                    # modal works after reload, not just on the live turn. Only
                    # present when the composer routed via ✨ Auto this turn.
                    if auto_route:
                        _ar_meta = dict(auto_route)
                        if _gating_decision is not None:
                            _ar_meta["tool_gating"] = _gating_decision
                        # GROUND TRUTH: the exact tools resolve_active_tools put
                        # on the wire this turn vs. deferred/excluded — captured
                        # from build_first_turn_prefix, NOT reconstructed from
                        # group tables. Computed per turn (classifier re-runs
                        # every turn). May be unset if the turn errored before
                        # the prefix build.
                        try:
                            if _tool_breakdown:
                                _ar_meta["tool_resolution"] = _tool_breakdown
                        except NameError:
                            pass
                        # Per-turn research-discipline record (active? which
                        # sections? injected via wire-preamble or system prompt?)
                        # for the inspector's discipline section.
                        try:
                            if _discipline_meta is not None:
                                _ar_meta["discipline"] = _discipline_meta
                        except NameError:
                            pass
                        msg_metadata["auto_route"] = _ar_meta
                    if _partial_tools:
                        msg_metadata["tools"] = _sanitize_partial_tools(_partial_tools)
                    # Per-round answer-text split for chronological display:
                    # when the model produced visible answer text across MULTIPLE
                    # rounds (interleaved with tool calls), persist the per-round
                    # segments {round, text} so the renderer can interleave them
                    # with the tool cards (text → tool → text) instead of dumping
                    # all tools above one monolithic answer. Display-only — the
                    # canonical `reply` (joined whole) stays the message content,
                    # so history/wire are unaffected and old clients still show
                    # the full text. Only stored when there's a real split (>1
                    # segment); a single-segment turn renders content as before.
                    try:
                        _segs = _result.get("text_segments") or []
                        if len(_segs) > 1:
                            msg_metadata["text_rounds"] = [
                                {"round": int(s.get("round", 0) or 0),
                                 "text": s.get("text", "") or ""}
                                for s in _segs if (s.get("text") or "").strip()
                            ]
                    except Exception:
                        pass
                    # Leftover thinking deltas that never got a thinking_done (truncated
                    # stream / error before flush). Persist as a fallback thinking row
                    # rather than losing the content.
                    thinking_leftover = "".join(_partial_thinking).strip()
                    if thinking_leftover:
                        try:
                            session.add_message("thinking", thinking_leftover,
                                                 metadata={"tool_round": None, "fallback": True})
                        except Exception:
                            msg_metadata["thinking"] = thinking_leftover  # legacy fallback
                        _partial_thinking.clear()
                    if _thinking_summary:
                        msg_metadata["thinking_summary"] = _thinking_summary
                    # Per-turn state snapshot: thinking level requested + caveman modes applied
                    if thinking_level:
                        msg_metadata["thinking_level"] = thinking_level
                    _cav_chat = int(engine.get_request_context().caveman_chat or 0)
                    _cav_sys = int(engine.get_request_context().caveman_system or 0)
                    if _cav_chat:
                        msg_metadata["caveman_chat"] = _cav_chat
                    if _cav_sys:
                        msg_metadata["caveman_system"] = _cav_sys
                    # --- Citation validator ---
                    # Scans the reply for [Quelle: X — "Y"] brackets, verifies each
                    # quote against the files read this turn, counts uncited claims,
                    # and appends a persistent fidelity warning when the reply
                    # violates the threshold (>30% uncited OR ≥2 unverified quotes).
                    #
                    # The validator now runs whenever the citation discipline was
                    # active this turn — the DYNAMIC grounding case (any chat) OR
                    # the explicit research_mode toggle/project flag (computed +
                    # stored as session._citation_discipline_active before the
                    # run). validate_citations_in_response verifies quotes against
                    # the files read this turn, so it's meaningful outside projects.
                    _research_active = bool(getattr(session, "_citation_discipline_active", False))
                    if _research_active and reply:
                        try:
                            _val = engine.validate_citations_in_response(reply, session_id=sid)
                            _cv_meta = {
                                "verified": _val.get("verified", 0),
                                "unverified_count": len(_val.get("unverified", []) or []),
                                "unverified_samples": [
                                    {"basename": bn, "quote_excerpt": q[:120], "reason": r}
                                    for (bn, q, r) in (_val.get("unverified") or [])[:5]
                                ],
                                "uncited_claims": _val.get("uncited_claims", 0),
                                "claim_total": _val.get("claim_total", 0),
                                "total_brackets": _val.get("total_brackets", 0),
                            }

                            # Citation-Warning: instead of re-rounding (which
                            # turned correct refusals into hallucinated
                            # citations on refusal-bucket questions), we flag
                            # the message in metadata so the frontend can render
                            # a compact "x von y" badge (full text in tooltip)
                            # that survives reload. Same threshold the re-round
                            # used (>30% uncited OR ≥2 unverified quotes). The
                            # prose is no longer baked into `reply` — the badge
                            # is built client-side from these fields.
                            #
                            # Only flag the message when a retrieval tool was
                            # ACTUALLY CALLED this turn (not merely live): if the
                            # model answered from its own knowledge without ever
                            # reading a file / searching / fetching, there were
                            # no sources to cite and "N von N ohne Quellenangabe"
                            # would be misleading. _RETRIEVAL_TOOLS is the same
                            # set turn_has_retrieval_tools uses.
                            _called_tool_names = [
                                (t or {}).get("name") for t in (_partial_tools or [])
                            ]
                            _retrieval_called = engine.turn_has_retrieval_tools(_called_tool_names)
                            if _retrieval_called and engine.citation_reround_needed(_val):
                                _uncited = int(_val.get("uncited_claims", 0) or 0)
                                _ctotal = int(_val.get("claim_total", 0) or 0)
                                _unver = len(_val.get("unverified", []) or [])
                                _parts = []
                                if _ctotal > 0 and _uncited > 0:
                                    _parts.append(
                                        f"{_uncited} von {_ctotal} Behauptungen "
                                        f"ohne Quellenangabe"
                                    )
                                if _unver >= 2:
                                    _parts.append(
                                        f"{_unver} Zitat(e) konnten nicht "
                                        f"in den Quelldateien verifiziert werden"
                                    )
                                if _parts:
                                    _cv_meta["warning_appended"] = True
                                    _cv_meta["warning_text"] = (
                                        "Hinweis zur Quellentreue: "
                                        + "; ".join(_parts)
                                        + ". Möglich ist auch, dass zu dieser "
                                          "Frage keine passenden Informationen "
                                          "in den Quellen vorlagen und die "
                                          "Antwort daher ohne Belege bleiben "
                                          "musste. Bitte einzelne Aussagen vor "
                                          "Weiterverwendung gegen die "
                                          "Originalquellen prüfen."
                                    )

                            msg_metadata["citation_validation"] = _cv_meta
                        except Exception as _e:
                            # Validation must never crash the response; log and continue.
                            try: print(f"[citation-validator] error: {_e}")
                            except Exception: pass

                    # Sidecar empty-round nudge marker — persistent so the
                    # user sees it after reload too, not just live via SSE.
                    # Triggered from attempt 1 (any nudge is unusual; the
                    # model should have answered directly).
                    _nudges = int(_nudge_count[0] or 0)
                    if _nudges > 0:
                        msg_metadata["nudge_count"] = _nudges
                        _gave_up = (reply.strip() ==
                                    "No response was returned. Please modify "
                                    "your request or change the model.")
                        if _gave_up:
                            # Give-up text is already the visible reply — don't
                            # double up with a hint, the message itself says it.
                            pass
                        else:
                            _nudge_hint = (
                                "\n\n---\n\n"
                                f"> ℹ️ **Hinweis**: Das Modell hat {_nudges} "
                                f"Mal neu angesetzt, bevor eine Antwort kam."
                            )
                            reply = reply + _nudge_hint
                    # ── Transparent anonymisation: deanonymize final reply ──
                    # The text_delta path already de-anonymised live deltas;
                    # this pass covers the final assembled reply (which may
                    # include text the streamer held back at flush time, plus
                    # nudge/citation hints appended above). It's also the
                    # canonical text persisted to the messages table.
                    _gdpr_streamer = getattr(session, "_gdpr_streamer", None)
                    _gdpr_mapping_id = getattr(session, "_gdpr_mapping_id", None)
                    if _gdpr_mapping_id and _gdpr_streamer is not None:
                        # Flush any held-back streamer tail to subscribers.
                        _tail = _gdpr_streamer.flush()
                        if _tail:
                            live.emit("text_delta", {"text": _tail})
                        _mapping = pseudonymizer.get_mapping(_gdpr_mapping_id)
                        if _mapping is not None:
                            _deanon_reply, _restored = pseudonymizer.deanonymize_text(
                                reply, mapping=_mapping)
                            _t1 = time.time()
                            _deanon_tool_id = f"deanon_{_gdpr_mapping_id[:12]}"
                            _emit_synthetic_tool_event(
                                live=live, sid=sid, kind="deanonymise_text",
                                tool_use_id=_deanon_tool_id, phase="dispatch",
                                args={"target": "assistant_reply",
                                      "mapping_id": _gdpr_mapping_id},
                            )
                            _emit_synthetic_tool_event(
                                live=live, sid=sid, kind="deanonymise_text",
                                tool_use_id=_deanon_tool_id, phase="done",
                                result={"restored": int(_restored),
                                        "mapping_id": _gdpr_mapping_id},
                                status="ok",
                                duration_ms=int((time.time() - _t1) * 1000),
                            )
                            try:
                                if engine._audit_log:
                                    engine._audit_log.log_action(
                                        agent=session.agent_id, session_id=sid,
                                        action_type="pii_deanonymise_text",
                                        tool_name="gdpr_scanner",
                                        args_summary="assistant_reply",
                                        result_summary=(
                                            f"restored={_restored} "
                                            f"mapping_id={_gdpr_mapping_id}"),
                                        result_status="success",
                                        duration_ms=0, source="chat",
                                    )
                            except Exception:
                                pass
                            # Capture wire-truth before we mutate `reply`.
                            # The session inspector reads this so an auditor
                            # can see the raw LLM output (with pseudonymised
                            # tokens still embedded) alongside the de-
                            # anonymised text the user actually sees in chat.
                            # Skip the metadata bloat when no tokens needed
                            # restoring (pre/post are byte-identical).
                            if _restored:
                                msg_metadata["wire_content"] = reply
                            reply = _deanon_reply
                            msg_metadata["gdpr_mapping_id"] = _gdpr_mapping_id
                            msg_metadata["gdpr_restored"] = int(_restored)
                            # Per-span highlight payload so the UI can mark
                            # each restored value in the assistant reply with
                            # a tooltip ("email — alice@… was anonymised as
                            # <EMAIL_1_7e77>"). Offsets are against `reply`
                            # (the de-anonymised final text). Skipped when
                            # no tokens were restored to keep metadata lean.
                            if _restored:
                                try:
                                    _spans = pseudonymizer.find_restored_spans(
                                        reply, mapping=_mapping)
                                except Exception:
                                    _spans = []
                                if _spans:
                                    msg_metadata["gdpr_restored_spans"] = _spans
                    # ── Per-turn GDPR outcome (metadata.gdpr) ──
                    # Data source for the post-turn feedback modal, for BOTH
                    # occasions:
                    #   anonymise            → N PII anonymised, M restored in reply
                    #   anonymise_failed_local → anonymise failed, answered on local
                    #   local_model          → PII found, answered on the local model
                    # The `active` flag (set at the decision points) tells the
                    # client whether to actually OFFER the feedback modal — only
                    # when THIS turn's own input was anonymised/swapped, not when
                    # anonymise merely re-pseudonymised prior history. Built here
                    # so the de-anonymise `_restored` count (above) is known.
                    _gdpr_outcome = getattr(
                        engine.get_request_context(), "_gdpr_turn_outcome", None)
                    if isinstance(_gdpr_outcome, dict):
                        _gdpr_meta = dict(_gdpr_outcome)
                        if _gdpr_meta.get("mode") == "anonymise":
                            _gdpr_meta["restored"] = int(
                                msg_metadata.get("gdpr_restored", 0) or 0)
                            # Recompute tokens_minted from the LIVE mapping —
                            # the early stash captured only the typed-text slice;
                            # read-side tools (read_document/read_file) may have
                            # added attachment PII to the same mapping mid-turn.
                            try:
                                _gmid = getattr(session, "_gdpr_mapping_id", None)
                                _m_live = (pseudonymizer.get_mapping(_gmid)
                                           if _gmid else None)
                                if _m_live is not None:
                                    _gdpr_meta["tokens_minted"] = len(_m_live.forward)
                            except Exception:
                                pass
                        msg_metadata["gdpr"] = _gdpr_meta
                    else:
                        _local_swap = getattr(session, "_gdpr_local_swap", "") or ""
                        if _local_swap:
                            # Always active — the user explicitly picked the
                            # local model for this turn (pre-worker swap).
                            msg_metadata["gdpr"] = {
                                "mode": "local_model",
                                "model": _local_swap,
                                "active": True,
                            }
                    session.add_message("assistant", reply, metadata=msg_metadata or None)
                    done_data = {
                        "text": reply,
                        "tokens": engine._estimate_conversation_tokens(session.messages),
                        "max_context": session.max_context,
                        "model": session.model,
                        "duration": _req_duration,
                        "tokens_in": _usage_totals["tokens_in"],
                        "tokens_out": _usage_totals["tokens_out"],
                        "last_tokens_in": _usage_totals["last_tokens_in"],
                    }
                    # Per-round answer-text split → client reconstructs the
                    # chronological text↔tool interleave on reload (display-only).
                    if msg_metadata.get("text_rounds"):
                        done_data["text_rounds"] = msg_metadata["text_rounds"]
                    if session_cost is not None:
                        done_data["cost"] = session_cost
                    # Auto-LCM compaction level → live status-line badge picks it
                    # up here; reload reads the same dict from msg_metadata.
                    if msg_metadata.get("lcm_state"):
                        done_data["lcm_state"] = msg_metadata["lcm_state"]
                    # GDPR highlight payload — UI marks each restored span
                    # in the reply with a tooltip. Pulled from the metadata
                    # we just attached to the persisted message; live path
                    # picks it up here, reload reads it from msg_metadata.
                    _gdpr_spans = msg_metadata.get("gdpr_restored_spans") if msg_metadata else None
                    if _gdpr_spans:
                        done_data["gdpr_restored_spans"] = _gdpr_spans
                    # Per-turn GDPR outcome badge — live path picks it up here,
                    # reload reads the same dict from msg_metadata["gdpr"].
                    _gdpr_outcome_meta = msg_metadata.get("gdpr") if msg_metadata else None
                    if _gdpr_outcome_meta:
                        done_data["gdpr"] = _gdpr_outcome_meta
                    # Include fallback model info if a fallback was used
                    fb_model = engine.get_request_context()._fallback_model_used
                    if fb_model:
                        done_data["fallback_model"] = fb_model
                        done_data["original_model"] = session.model
                    # Auto-routing: tell the client which model Auto picked and
                    # why, so the composer can show "Auto (Model)" + tooltip
                    # without dropping the user's "auto" selection.
                    if auto_route:
                        # Match the persisted shape (incl. the tool-gating
                        # decision) so the live turn's classification modal is
                        # identical to a reloaded turn's.
                        _ar_done = dict(auto_route)
                        if _gating_decision is not None:
                            _ar_done["tool_gating"] = _gating_decision
                        try:
                            if _tool_breakdown:
                                _ar_done["tool_resolution"] = _tool_breakdown
                        except NameError:
                            pass
                        try:
                            if _discipline_meta is not None:
                                _ar_done["discipline"] = _discipline_meta
                        except NameError:
                            pass
                        done_data["auto_route"] = _ar_done
                    # Include file attachments
                    if created_files:
                        done_data["files"] = created_files
                    # Deep Research turn → live pill marker (reload reads metadata).
                    if deep_research:
                        done_data["deep_research"] = True
                    live.emit("done", done_data)

                    # Auto chat→wiki: when this session has memory ON/auto
                    # (save_to_memory > 0), re-wikify it in the background so the
                    # conversation lands in the user-visible wiki (and thus
                    # MemPalace) WITHOUT the user having to press 'merken'. This
                    # replaces the retired mempalace-chat-sync daemon as the
                    # automatic feeder. upsert_from_source keys on
                    # source_ref=session/<sid>, so repeated turns re-version the
                    # SAME page (diff-merge, no fork). Debounced per session so a
                    # multi-turn chat doesn't LLM-rebuild the page every round.
                    try:
                        if int(getattr(session, "save_to_memory", 0) or 0) > 0 \
                                and len([m for m in session.messages if m.get("role") == "user"]) >= 1:
                            import time as _t
                            _last = getattr(session, "_last_wiki_sync_at", 0)
                            now = _t.time()
                            # At most once per ~90s of wall-clock per session
                            # (the LLM reorganization is the cost). The final
                            # state is captured on session delete/idle too.
                            if now - _last >= 90:
                                session._last_wiki_sync_at = now
                                _wsid = sid

                                _mem_mode = int(getattr(session, "save_to_memory", 0) or 0)

                                def _auto_wiki(_s=_wsid, _mode=_mem_mode):
                                    try:
                                        from engine import wiki_store as _wiki
                                        # Auto mode (2): an LLM gate decides if the
                                        # conversation is worth saving. On mode (1):
                                        # always file. (Successor to the retired
                                        # per-turn memory classifier.)
                                        if _mode == 2 and not _wiki.wiki_worth_saving(_s):
                                            print(f"[auto-wiki] {_s[:8]} gated: SKIP (not memorable)", flush=True)
                                            return
                                        _wiki.wiki_from_chat(_s)
                                    except Exception as _e:
                                        print(f"[auto-wiki] {_s[:8]} failed: {_e}", flush=True)
                                threading.Thread(target=_auto_wiki, daemon=True,
                                                 name=f"auto-wiki-{sid[:8]}").start()
                    except Exception:
                        pass

                    # Generate chat summary (background, for sidebar display).
                    # Regenerated every turn so the synopsis tracks the latest
                    # questions, not just the opening one.
                    try:
                        if len(session.messages) >= 2:
                            threading.Thread(
                                target=_generate_chat_summary,
                                args=(session,),
                                daemon=True,
                                name=f"chat_summary_{sid}"
                            ).start()
                    except Exception:
                        pass

                    # Index chat transcript for content search (4+ messages, every 4th message or first time)
                    try:
                        msg_count = len(session.messages)
                        if msg_count >= 4 and (msg_count % 4 == 0 or not os.path.isdir(
                                os.path.join(engine.AGENTS_DIR, session.agent_id, "chats-indexed"))):
                            threading.Thread(
                                target=_index_chat_transcript,
                                args=(session,),
                                daemon=True,
                                name=f"chat_index_{sid}"
                            ).start()
                    except Exception:
                        pass
                else:
                    # Empty reply — rollback all intermediate messages from tool loop
                    _rollback_messages(session, sid, _msg_count_before)
                    live.emit("done", {"text": "", "tokens": 0, "model": session.model})
            except engine.TaskCancelled:
                # Persist whatever this turn produced before the cancel — partial
                # text AND/OR tool calls. Gating on text alone lost a turn that
                # had only made tool calls and no text yet (e.g. an ask_user that
                # blocked waiting for an answer, then got cancelled — the tool
                # call showed in the right panel but vanished on cancel, the
                # 1fa62d2d bug). Persist when there's partial text OR partial
                # tools so nothing the user was shown is silently dropped.
                partial = "".join(_partial_reply).strip()
                if partial or _partial_tools:
                    _rollback_messages(session, sid, _msg_count_before)
                    # Marker on its own line; if there was no text, the assistant
                    # message body is just the cancel marker so the tool calls
                    # (in metadata) still render under a real turn.
                    partial = (partial + "\n\n*(Cancelled)*") if partial else "*(Cancelled)*"
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _sanitize_partial_tools(_partial_tools)
                    _attach_usage_meta(meta, _usage_totals, sid)
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                live.emit("error", {"message": "Cancelled"})
            except SystemExit as e:
                # Persist partial text AND/OR tool calls (same principle as the
                # cancel branch — don't drop a turn that only made tool calls).
                partial = "".join(_partial_reply).strip()
                if partial or _partial_tools:
                    _rollback_messages(session, sid, _msg_count_before)
                    _marker = f"*(Engine error: exit code {e.code})*"
                    partial = (partial + f"\n\n{_marker}") if partial else _marker
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _sanitize_partial_tools(_partial_tools)
                    _attach_usage_meta(meta, _usage_totals, sid)
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                live.emit("error", {"message": f"Engine fatal error (exit code {e.code})"})
            except Exception as e:
                import traceback
                traceback.print_exc()
                partial = "".join(_partial_reply).strip()
                if partial or _partial_tools:
                    _rollback_messages(session, sid, _msg_count_before)
                    _marker = f"*(Error: {str(e)[:200]})*"
                    partial = (partial + f"\n\n{_marker}") if partial else _marker
                    meta = {"model": session.model, "partial": True}
                    if _partial_tools:
                        meta["tools"] = _sanitize_partial_tools(_partial_tools)
                    _attach_usage_meta(meta, _usage_totals, sid)
                    session.add_message("assistant", partial, metadata=meta)
                else:
                    _rollback_messages(session, sid, _msg_count_before)
                live.emit("error", {"message": str(e)})
            finally:
                # If the worker died without emitting a terminal event (e.g. a
                # bare process exit), make sure subscribers aren't left hanging.
                if not live.done:
                    live.emit("error", {"message": "Server worker terminated unexpectedly"})
                with session.lock:
                    session._streaming = False
                    if session.live_stream is live:
                        session.live_stream = None
                # Auto mode: the per-turn router swapped session.model to the
                # concrete pick (load-bearing during the turn). Restore the
                # composer's auto DIRECTIVE as the persisted session model so
                # reopening the chat shows the right Smart mode (Cloud/Lokal),
                # not the last working model. The model that actually answered
                # is recorded in the assistant message metadata, so nothing is
                # lost. Falls back to "auto-cloud" if the stash is missing.
                #
                # Gate on want_auto, NOT `auto_route`: on a CONCRETE-model turn
                # the LLM classifier still builds an `auto_route` dict tagged
                # classifier_only=True (so the inspector button shows the tool
                # decision). That is not a model pick — treating it as Auto here
                # would re-freeze the stale directive and lose the manual model
                # on reload (the original bug). want_auto is True iff the
                # composer model itself was an Auto directive.
                if want_auto:
                    _directive = getattr(session, "_composer_auto_model", "") or "auto-cloud"
                    with session.lock:
                        session.model = _directive
                    try:
                        ChatDB.save_session(session.id, session.agent_id, _directive,
                                            session.title, session.status,
                                            session.created_at, session.last_active,
                                            session.project or "", user_id=session.user_id)
                    except Exception:
                        pass
                else:
                    # Manual / concrete model turn (no per-turn router). The user
                    # may have switched the composer to a concrete model on an
                    # existing session — that pick lived only in session.model
                    # in-memory and was never written back, so reopening the chat
                    # read the stale creation value (e.g. a leftover "auto-cloud")
                    # and wrongly showed Smart mode. Persist the model that
                    # actually ran so reload shows the last used model. Guard: only
                    # for a real concrete id (never an auto directive) and only on
                    # a change, to avoid a needless write every turn.
                    _used = session.model or ""
                    if _used and not _parse_auto_directive(_used)[0]:
                        try:
                            _stored = (ChatDB.get_session_info(session.id) or {}).get("model", "")
                        except Exception:
                            _stored = ""
                        if _used != _stored:
                            try:
                                ChatDB.save_session(session.id, session.agent_id, _used,
                                                    session.title, session.status,
                                                    session.created_at, session.last_active,
                                                    session.project or "", user_id=session.user_id)
                            except Exception:
                                pass
                try:
                    ChatDB.set_streaming_text(sid, "")  # finalized — clear the partial
                except Exception:
                    pass
                # Transparent anonymisation: drop the in-memory mapping at
                # turn end. The encrypted SQLite row stays (persist_maps=true
                # per design), so reload paths can still de-anonymise the
                # persisted reply if we ever surface "show what was sent"
                # audit UI. Cancellation / error cases also flow through
                # here, so the registry never leaks across turns.
                _gdpr_mid = getattr(session, "_gdpr_mapping_id", None)
                if _gdpr_mid:
                    # Persist any mid-turn additions: read-side tools
                    # (`_gdpr_anon_tool_text`) mutate `mapping.forward` in
                    # place when they discover new PII, but `save_mapping`
                    # is only called once upfront BEFORE the sidecar
                    # round. Without this second save, reload paths can't
                    # de-anonymise persisted messages that referenced
                    # tokens minted mid-turn. UPSERT on `mapping_id` =
                    # safe to re-call.
                    try:
                        _m_inmem = pseudonymizer.get_mapping(_gdpr_mid)
                        if _m_inmem is not None:
                            pseudonymizer.save_mapping(
                                _m_inmem, session_id=sid, turn_id=_gdpr_mid)
                            # Record one pii_decisions row per anonymised value
                            # (original → fake) so the deterministic pre-send
                            # wire-history pass can reuse the fake on later turns
                            # without re-scanning or decrypting. Done at turn-END
                            # so the mapping is COMPLETE — it covers typed text,
                            # freshly-submitted attachments, AND attachment text
                            # discovered mid-turn by read_document/read_file tools
                            # (the user's "must work for attachments too"). The
                            # decision ledger thus holds every original→fake the
                            # conversation ever produced. Idempotent: the latest
                            # row per value_hash wins, so re-recording stable
                            # values across turns is harmless.
                            try:
                                _anon_decisions = [
                                    {"rule_id": _m_inmem.categories.get(_o, ""),
                                     "value": _o, "fake_value": _f,
                                     "disposition": "anonymise"}
                                    for _o, _f in _m_inmem.forward.items()
                                ]
                                if _anon_decisions:
                                    _au = getattr(engine.get_request_context(),
                                                  "current_user_id", "") or ""
                                    ChatDB.record_pii_decisions(
                                        sid, _au, _gdpr_mid or "",
                                        "anonymise", _anon_decisions)
                            except Exception as _de:
                                print(f"[gdpr] anon decision persist failed: "
                                      f"{_de}", flush=True)
                    except Exception:
                        pass
                    try:
                        pseudonymizer.close_mapping(_gdpr_mid)
                    except Exception:
                        pass
                    session._gdpr_mapping_id = None
                    session._gdpr_streamer = None

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t

class ChatHandlerMixin:

    # Cache: model -> provider config (refreshed when providers change)
    # Provider cache now in engine (resolve_provider_for_model)

    # Tools whose results contain clickable file-path references (MemPalace drawers/triples).
    _PROJECT_REF_TOOLS = frozenset({
        "mempalace_query", "mempalace_get_drawer", "mempalace_list_drawers",
        "mempalace_kg_query", "mempalace_kg_search", "mempalace_kg_neighbors",
    })
    # Tools whose results contain URL references (web searches/fetches).
    _WEB_REF_TOOLS = frozenset({"exa_search", "web_fetch"})

    @staticmethod
    def _resolve_original_path(sf: str) -> str:
        """Resolve a MemPalace source_file to the original binary path.
        .brain-extracted/foo.pdf.md  →  <parent>/foo.pdf
        foo.pdf.md (bare companion)  →  foo.pdf
        Anything else               →  unchanged
        """
        import re
        m = re.match(r'^(.+)/\.brain-extracted/(.+)\.md$', sf)
        if m:
            return f"{m[1]}/{m[2]}"
        m2 = re.match(r'^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$', sf, re.IGNORECASE)
        if m2:
            return m2[1]
        return sf

    @staticmethod
    def _is_document_source(sf: str, room: str = "") -> bool:
        """True if a MemPalace source_file points to a real, openable document.
        Filters out synthetic addresses that aren't clickable files:
        chat turns, chat summaries, user-profile sections, bare turn-ids.
        Single choke point — used by both JSON-loop and regex-sweep so
        non-document drawers can never leak through as references.
        """
        import re as _re
        if not sf:
            return False
        if room in ("chat", "chat_summary", "chat_attachment", "user_profile"):
            return False
        # Synthetic MemPalace addresses (no filesystem path).
        if sf.startswith(("session/", "user/", "team/")):
            return False
        if _re.match(r'^\d+$', sf):
            return False  # bare turn-id
        if _re.match(r'^[a-f0-9]+#summary$', sf, _re.IGNORECASE):
            return False
        return True

    @classmethod
    def _extract_references(cls, tool_name: str, result_str: str) -> list:
        """Extract normalized reference dicts from a tool result string.
        Returns [] for tools that don't produce references.
        Each ref: {title, link, snippet, domain, favicon, source_file?}
        This is the single source of truth — client reads persisted refs
        from metadata.tools[i].references instead of re-parsing results.
        """
        if not result_str:
            return []
        refs = []

        if tool_name in cls._PROJECT_REF_TOOLS:
            # MemPalace tools return JSON with drawers/triples/edges each
            # carrying a source_file. Resolve to original binary path.
            seen = set()
            try:
                data = json.loads(result_str)
                items = (list(data.get("drawers") or []) +
                         list(data.get("results") or []) +
                         list(data.get("triples") or []) +
                         list(data.get("edges") or []))
            except Exception:
                items = []

            for it in items:
                if not isinstance(it, dict):
                    continue
                sf = it.get("source_file") or ""
                if not sf or sf in seen:
                    continue
                seen.add(sf)  # claim before predicate so regex sweep can't re-add
                room = it.get("room") or ""
                if not cls._is_document_source(sf, room):
                    continue
                original = cls._resolve_original_path(sf)
                basename = original.rsplit("/", 1)[-1] or original
                snippet = ""
                if it.get("snippet"):
                    snippet = str(it["snippet"])[:280]
                elif it.get("text"):
                    snippet = str(it["text"])[:280]
                elif it.get("subject") and it.get("predicate") and it.get("object"):
                    snippet = f"({it['subject']}) — [{it['predicate']}] → ({it['object']})"[:280]
                ref = {
                    "title": basename,
                    "link": original,
                    "snippet": snippet,
                    "domain": "project",
                    "favicon": "",
                    "source_file": sf,
                }
                # Wiki drawers: show the PAGE TITLE (not the raw id) + mark as wiki
                # so the client renders a 'Wiki-Seite' badge that opens the page.
                if sf.startswith("wiki/"):
                    pid = sf.split("/", 1)[1]
                    try:
                        from server_lib.db import ChatDB as _CDB
                        pg = _CDB.get_wiki_page(pid)
                    except Exception:
                        pg = None
                    ref["source_kind"] = "wiki"
                    ref["wiki_page_id"] = pid
                    ref["title"] = (pg or {}).get("title") or basename
                refs.append(ref)

            # Regex sweep for any source_file tokens the JSON parse may have missed
            # (truncated result strings, nested structures, etc.)
            import re as _re
            for m in _re.finditer(r'"source_file"\s*:\s*"([^"]+)"', result_str):
                sf = m.group(1)
                if not sf or sf in seen:
                    continue
                seen.add(sf)
                # Regex sweep can't see the drawer's `room`; predicate
                # rejects synthetic addresses on shape alone.
                if not cls._is_document_source(sf):
                    continue
                original = cls._resolve_original_path(sf)
                basename = original.rsplit("/", 1)[-1] or original
                refs.append({
                    "title": basename,
                    "link": original,
                    "snippet": "",
                    "domain": "project",
                    "favicon": "",
                    "source_file": sf,
                })
            return refs

        if tool_name in cls._WEB_REF_TOOLS:
            # Worker envelope: pre-extracted references array takes priority
            try:
                data = json.loads(result_str)
                if data.get("worker") and isinstance(data.get("references"), list):
                    for r in data["references"]:
                        if r and r.get("link"):
                            dom = r.get("domain") or ""
                            refs.append({
                                "title": r.get("title") or dom or r["link"],
                                "link": r["link"],
                                "snippet": r.get("snippet") or "",
                                "domain": dom,
                                "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else "",
                            })
                    if refs:
                        return refs
            except Exception:
                pass

            # Direct JSON result
            try:
                data = json.loads(result_str)
                results = data.get("results") if isinstance(data, dict) else None
                if isinstance(results, list):
                    for r in results:
                        url = r.get("link") or r.get("url") or ""
                        if not url:
                            continue
                        dom = ""
                        try:
                            from urllib.parse import urlparse
                            dom = urlparse(url).hostname or ""
                            dom = dom.removeprefix("www.")
                        except Exception:
                            pass
                        refs.append({
                            "title": r.get("title") or dom or url,
                            "link": url,
                            "snippet": (r.get("snippet") or "")[:200],
                            "domain": dom,
                            "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else "",
                        })
                    return refs
                if isinstance(data, dict) and data.get("url"):
                    url = data["url"]
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(url).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    import re as _re
                    title = dom
                    tm = _re.search(r'<title[^>]*>([^<]+)</title>', data.get("content") or "", _re.IGNORECASE)
                    if tm:
                        title = tm.group(1).strip()
                    refs.append({"title": title, "link": url, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
                    return refs
            except Exception:
                pass

            # Regex fallback for truncated JSON
            import re as _re
            if tool_name == "exa_search":
                for m in _re.finditer(r'"title"\s*:\s*"([^"]*)"[^}]*?"link"\s*:\s*"([^"]*)"', result_str):
                    raw_title, link = m.group(1), m.group(2)
                    try:
                        title = json.loads(f'"{raw_title}"')
                    except Exception:
                        title = raw_title
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(link).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    refs.append({"title": title, "link": link, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
            elif tool_name == "web_fetch":
                m = _re.search(r'"url"\s*:\s*"([^"]*)"', result_str)
                if m:
                    url = m.group(1)
                    dom = ""
                    try:
                        from urllib.parse import urlparse
                        dom = urlparse(url).hostname or ""
                        dom = dom.removeprefix("www.")
                    except Exception:
                        pass
                    title = dom
                    tm = _re.search(r'<title[^>]*>([^<]+)</title>', result_str, _re.IGNORECASE)
                    if tm:
                        title = tm.group(1).strip()
                    refs.append({"title": title, "link": url, "snippet": "", "domain": dom,
                                  "favicon": f"https://www.google.com/s2/favicons?domain={dom}&sz=32" if dom else ""})
            return refs

        return []

    @staticmethod
    def _resolve_provider_static(model: str) -> dict:
        """Find the provider that has the given model. Returns {api_key, base_url, provider_name}.
        Thread-safe. Delegates to engine.resolve_provider_for_model()."""
        if engine._models_config:
            model = engine.resolve_model(model)
        return engine.resolve_provider_for_model(model)

    def _resolve_provider(self, model: str) -> dict:
        """Instance method wrapper for _resolve_provider_static."""
        return BrainAgentHandler._resolve_provider_static(model)

    def _handle_create_session(self):
        body = self._read_json()
        model = body.get("model", server_config["default_model"])
        agent_req = body.get("agent", "main")
        # "auto" is a routing directive — keep it on the session so each turn
        # re-routes, but resolve a concrete model for the initial provider
        # creds + warm-pool/context lookups below. ACL on 'auto' itself is a
        # no-op; the per-turn router only ever picks ACL-allowed models.
        want_auto, _auto_pool = _parse_auto_directive(model)
        resolved_model = engine.resolve_model("auto") if want_auto else model
        # ACL gate: caller must have access to both the agent and the model
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_req):
            self._send_json({"error": f"Access to agent '{agent_req}' not permitted"}, 403)
            return
        if not want_auto and not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        provider = self._resolve_provider(resolved_model)
        project_req = body.get("project", "")
        custom_status_req = body.get("status", "")
        note_req = body.get("note_context", "")

        # Warm session pool claim — only when the incoming request matches
        # the pooled shape exactly: agent=main, no project, no custom status,
        # no note context. Any of those change the system prompt / behavior
        # and would make a pre-primed KV prefix invalid.
        # Warm pool keys on a concrete model; "auto" never claims a pooled
        # session (its model would be wrong for the per-turn pick anyway).
        model_cfg_claim = engine._models_config.get(resolved_model, {})
        pooled = None
        if (not want_auto and model_cfg_claim.get("warmup")
                and agent_req == WarmSessionPool.POOL_AGENT
                and not project_req and not custom_status_req and not note_req):
            pooled = warm_pool.claim(resolved_model)
        if pooled is not None:
            session = pooled
            # Promote from warm_pool status to active (visible in sidebar)
            session.status = "active"
            ChatDB.save_session(
                session.id, session.agent_id, session.model,
                session.title, session.status,
                session.created_at, session.last_active,
                session.project or "",
            )
            # Immediately kick off a replacement build
            threading.Thread(
                target=lambda m=model: warm_pool.try_build(m),
                daemon=True, name=f"warm-pool-refill-{model[:16]}",
            ).start()
            print(f"[warm-pool] claimed {model} ({session.id[:8]})")
        else:
            session = sessions.create(
                agent_id=agent_req,
                model=model,
                api_key=provider["api_key"],
                base_url=provider["base_url"],
                max_context=body.get("max_context") or engine.get_model_max_context(resolved_model),
            )
        # Stamp user ownership (for MemPalace wing scoping)
        auth_user = getattr(self, '_auth_user', None)
        uid = ""
        if auth_user and auth_user.get("id"):
            if auth_user["id"] != "__system__":
                uid = auth_user["id"]
            else:
                # Auth disabled — resolve to the first real user (typically the sole admin)
                try:
                    users = _auth_mod.AuthDB.list_users()
                    if users:
                        uid = users[0]["id"]
                except Exception:
                    pass
        if uid:
            session.user_id = uid
            ChatDB.update_session_user(session.id, uid)
        # Default memory mode: per-user preference wins over the global
        # classifier config. Pref `memory_chats_default` is 0|1|2|null;
        # null means "fall through to classifier.default_mode" so an unset
        # pref doesn't accidentally disable a server-wide opt-in.
        mcfg = engine._load_mempalace_config()
        clf_cfg = (mcfg.get("chat_sync", {}) or {}).get("classifier", {}) or {}
        default_mem = int(clf_cfg.get("default_mode", 0))
        try:
            actor = getattr(self, "_auth_user", None) or {}
            user_prefs = actor.get("preferences") or {}
            pref_chat = user_prefs.get("memory_chats_default")
            if pref_chat is not None:
                default_mem = int(pref_chat)
        except Exception:
            pass
        if default_mem:
            session.save_to_memory = default_mem
            ChatDB.update_session_save_to_memory(session.id, default_mem)
        project = body.get("project", "")
        if project:
            session.project = project
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, project)
        note_context = body.get("note_context", "")
        if note_context:
            session.note_context = note_context
        # Bind to a workflow_history row so the chat loop's round-0 preamble
        # can pull the run summary. Combined with status='workflow_run'
        # below, this hides the session from the sidebar until the user hits
        # "Save to chats" in the inline detail view.
        wf_run_id = body.get("workflow_run_id", "")
        if wf_run_id:
            session.workflow_run_id = wf_run_id
            ChatDB.update_session_workflow_run_id(session.id, wf_run_id)
        # Allow setting custom status (e.g., 'note_chat' to hide from chat lists)
        custom_status = body.get("status", "")
        if custom_status:
            session.status = custom_status
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "",
                               workflow_run_id=session.workflow_run_id)
        # Per-model warmup flag
        mcfg = engine.resolve_model_settings(model)
        warmup_enabled = bool(mcfg.get("warmup", False))

        # Claimed pool sessions are already warm — skip the "warmup" status
        # marker (that's for fresh sessions still prefilling) and skip the
        # redundant _trigger_warmup call.
        claimed = pooled is not None

        # Mark warmup sessions so they don't appear in sidebar until first message
        if warmup_enabled and not custom_status and not claimed:
            session.status = "warmup"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "max_context": session.max_context,
            "project": session.project or "",
            "warmup": warmup_enabled,
            "pre_warmed": claimed,
        })

        # Trigger warmup in background (skip if session was claimed from pool,
        # or if the caller explicitly opted out via body.skip_warmup=true).
        # Eval / batch runners that create one session per question opt out:
        # the per-session prefill collides with the actual chat call on the
        # same provider's queue, occasionally truncating gemma-4-26B replies
        # to empty after the first tool round.
        skip_warmup = bool(body.get("skip_warmup", False))
        if warmup_enabled and not claimed and not skip_warmup:
            _trigger_warmup(session)

    def _handle_switch_agent(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        agent_id = body.get("agent", "main")
        model = body.get("model")
        # ACL gate for agent + (optional) model change
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        if not _auth_mod.can_access_agent(user, agent_id):
            self._send_json({"error": f"Access to agent '{agent_id}' not permitted"}, 403)
            return
        if model and not _auth_mod.can_access_model(user, model):
            self._send_json({"error": f"Access to model '{model}' not permitted"}, 403)
            return
        session.switch_agent(agent_id, model)
        warmup_enabled = False
        if model:
            provider = self._resolve_provider(model)
            session.api_key = provider["api_key"]
            session.base_url = provider["base_url"]
            mcfg = engine.resolve_model_settings(model)
            warmup_enabled = bool(mcfg.get("warmup", False))
        self._send_json({
            "session_id": session.id,
            "agent": session.agent_id,
            "model": session.model,
            "warmup": warmup_enabled,
        })
        if warmup_enabled:
            _trigger_warmup(session)

    def _handle_cancel(self):
        body = self._read_json()
        sid = body.get("session_id", "")
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        session.cancel_token.cancel()
        self._send_json({"status": "cancelled"})

    def _handle_web_search(self):
        """POST /v1/web/search — run a SearXNG web search and return results.

        Search ONLY: no fetch, no LLM. Powers the composer's manual-curation
        web-search flow (the Websuche right-panel tab). The user inspects these
        results, marks the ones to keep, and a later chat turn pre-fetches the
        marked URLs server-side. Pure passthrough to the existing
        `tool_searxng_search` so search behavior (scoring, dedup, the v9.16.0
        per-engine health work) stays single-sourced.
        """
        body = self._read_json()
        query = (body.get("query") or "").strip()
        if not query:
            self._send_json({"error": "No query"}, 400)
            return
        num_results = body.get("num_results", 10)
        try:
            num_results = max(1, min(int(num_results), 30))
        except (TypeError, ValueError):
            num_results = 10
        raw = engine.tool_searxng_search({
            "query": query,
            "num_results": num_results,
            "force_fresh": bool(body.get("force_fresh")),
            # Human-curation panel: keep snippets so the user can eyeball each
            # result before marking it. The LLM-facing tool path omits them.
            "include_snippets": True,
        })
        try:
            self._send_json(json.loads(raw))
        except (ValueError, TypeError):
            self._send_json({"query": query, "results": [],
                             "error": "search returned a non-JSON result"}, 502)

    def _handle_chat(self):
        """Handle chat request with SSE streaming."""
        body = self._read_json()
        sid = body.get("session_id", "")
        message = body.get("message", "")
        model_override = body.get("model")
        chat_mode = body.get("mode", "")
        project_name = body.get("project")  # Optional project scope
        thinking_level = body.get("thinking")  # none, low, medium, high
        # ACL: only owner/team-member/admin can post to the session
        if sid and self._session_access_check(sid) is None:
            return
        # "auto" is a routing directive, not a concrete model — the router
        # picks (and ACL-filters) the real model later. Treat it as a per-turn
        # auto request and drop it from the override path so we don't try to
        # resolve a provider for the literal string "auto".
        want_auto, auto_pool = _parse_auto_directive(model_override)
        if want_auto:
            model_override = None

        # ACL: model override must be permitted
        if model_override:
            user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
            if not _auth_mod.can_access_model(user, model_override):
                self._send_json({"error": f"Access to model '{model_override}' not permitted"}, 403)
                return
        session = sessions.get(sid)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not message:
            self._send_json({"error": "No message"}, 400)
            return

        # Per-session thinking level. The client sends body.thinking from the
        # composer; when present we persist it onto the session so a reload
        # restores the same level (mirrors caveman_mode). When absent (e.g. a
        # non-UI caller), fall back to the session's stored value so the turn
        # still honours the chat's chosen level.
        if thinking_level is None:
            thinking_level = getattr(session, "thinking_level", "") or None
        else:
            _tl_norm = str(thinking_level or "").lower().strip()
            if _tl_norm not in ("none", "low", "medium", "high"):
                _tl_norm = ""
            if (getattr(session, "thinking_level", "") or "") != _tl_norm:
                with session.lock:
                    session.thinking_level = _tl_norm
                try:
                    ChatDB.update_session_thinking_level(session.id, _tl_norm)
                except Exception:
                    pass

        # Custom command expansion
        if message.startswith("/"):
            agent = engine.AgentConfig(session.agent_id)
            custom_cmds = agent.load_commands()
            cmd_word = message.split()[0][1:]  # strip / and get first word
            for cmd in custom_cmds:
                if cmd.get("name", "").lower() == cmd_word.lower():
                    template = cmd.get("template", "")
                    # Replace {{input}} with rest of message
                    rest = message[len(cmd_word) + 1:].strip()
                    message = template.replace("{{input}}", rest)
                    break

        # If model changed, re-resolve provider
        if model_override and model_override != session.model:
            provider = self._resolve_provider(model_override)
            with session.lock:
                session.model = model_override
                session.api_key = provider["api_key"]
                session.base_url = provider["base_url"]

        # Yield to an in-flight BACKGROUND-DELIVERY turn before claiming the
        # stream. A server-fired group/standalone delivery turn (which the user
        # didn't initiate) may be mid-flight; if this user POST set live_stream
        # now it would clobber the delivery's stream and orphan its active_turns
        # row. Background deliveries are short and synchronous, so a brief bounded
        # wait lets it finish, then we proceed cleanly. We wait only while the
        # current stream is a background delivery (metadata flag on the in-flight
        # turn); a normal user turn already streaming is the pre-existing
        # double-POST case and is left as-is (wait expires → proceed). Best-effort:
        # the wait is capped so a stuck delivery never blocks the user for long.
        _wait_deadline = time.time() + 5.0
        while time.time() < _wait_deadline:
            with session.lock:
                busy = getattr(session, "_streaming", False) or getattr(session, "live_stream", None)
            if not busy or session.id not in _bg_delivery_inflight:
                break  # idle, or the busy turn isn't a background delivery
            time.sleep(0.05)

        # Reset cancel token + open a fresh live-event buffer for this turn.
        # The worker thread (below) emits every SSE event into `live`; the HTTP
        # response loop at the end of this method just attaches as one subscriber.
        # Reopening the chat — or watching from another tab — attaches another
        # subscriber via GET /v1/chat/stream and replays the buffer, so it looks
        # like the chat was open all along. The worker is NOT tied to any HTTP
        # connection; only POST /v1/chat/cancel stops it.
        live = LiveStream()
        with session.lock:
            session.cancel_token = engine.CancelToken()
            session._streaming = True
            session.live_stream = live
        ChatDB.set_streaming_text(session.id, "")  # clear any stale partial

        # --- Unified attachment routing: multimodal vs disk based on model capabilities ---
        import base64 as _b64
        import mimetypes as _mt

        def _guess_mime(filename: str) -> str:
            mt, _ = _mt.guess_type(filename)
            return mt or "application/octet-stream"

        # Collect all attachments from both legacy body.images and body.files
        all_attachments = []
        for img in body.get("images", []):
            all_attachments.append({
                "name": "image",
                "content": img.get("data", ""),
                "encoding": "base64",
                "media_type": img.get("media_type", "image/png"),
            })
        for f in body.get("files", []):
            all_attachments.append({
                "name": f.get("name", "file"),
                "content": f.get("content", "") or f.get("data", ""),
                "encoding": f.get("encoding", "base64"),
                "media_type": f.get("media_type") or f.get("type") or _guess_mime(f.get("name", "file")),
            })

        # Auto model selection. Two triggers:
        #   1. Agent config model="auto" — fires ONCE on the session's first
        #      turn (per-session scope keeps the warm-pool KV prefix stable and
        #      the conversation model-consistent).
        #   2. User picked "Auto" in the composer this turn (`want_auto`) —
        #      an explicit per-turn request, honored whenever it's sent.
        # Runs after attachment collection so the pick can honor a turn's files
        # (vision/raw-format capability wins). Skipped when an explicit concrete
        # model override is present.
        # When the user picked "Auto", we re-route EVERY turn (the user expects
        # the best-fitting model per message) and report back the picked model
        # + reason. `auto_route` is captured by the worker for the done event.
        auto_route = None
        agent_cfg = session.agent.config
        # Clear any prior turn's classifier tool-gating up front, so a turn that
        # does NOT auto-route (e.g. a concrete model pick) never inherits a
        # stale gate from an earlier auto turn on this reused session.
        with session.lock:
            session._auto_tool_groups = None
            session._auto_route_model = ""
        auto_by_agent = (agent_cfg.get("model") == "auto" and len(session.messages) == 0)
        if not model_override and (want_auto or auto_by_agent):
            attach_mimes = [a["media_type"] for a in all_attachments]
            # ACL-scope the candidate pool to models the caller may use.
            _user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
            allowed = None
            if _user and _user.get("role") != "admin" and _user.get("id") != "__system__":
                allowed = _auth_mod.AuthDB.get_user_allowed_models(_user["id"])
            # Force the agent_config branch of the resolver regardless of the
            # session agent's own model field, so a user-picked "Auto" routes
            # even when the agent is pinned to a concrete model.
            auto_model, auto_purpose, auto_analysis = engine.resolve_auto_model_for_task(
                {"model": "auto"}, message,
                attachment_mimes=attach_mimes, allowed_models=allowed, pool=auto_pool)
            if auto_model:
                auto_route = {
                    "model": auto_model,
                    "reason": _auto_route_reason(auto_purpose, attach_mimes, auto_model, auto_analysis),
                }
                # Surface the structured analysis (task_types/tools/complexity)
                # when the LLM classifier ran, for the chat-view badge + audit.
                if auto_analysis:
                    auto_route["analysis"] = {
                        "task_types": auto_analysis.get("task_types", []),
                        "tools": auto_analysis.get("tools", []),
                        "complexity": auto_analysis.get("complexity", ""),
                        "reasoning": auto_analysis.get("reasoning", ""),
                    }
            if auto_model and auto_model != session.model:
                provider = self._resolve_provider(auto_model)
                with session.lock:
                    session.model = auto_model
                    session.api_key = provider["api_key"]
                    session.base_url = provider["base_url"]
                    session.max_context = engine.get_model_max_context(auto_model)
            # Stash the classifier's needed tool groups on the session so the
            # worker can reshape the per-turn tool deferral. TOOL OPTIMIZATION is
            # a SEPARATE axis from model selection: it happens iff the per-agent
            # `optimize_tools` flag is on AND the picked model is safe to reshape
            # (cloud or warmup-disabled local — model_should_optimize_tools). So
            # an Auto turn picks a model regardless, but only gates tools when
            # enabled. Cleared each turn (None when off / no analysis) so no
            # stale shape carries over.
            _opt_on = engine.agent_optimize_tools_enabled(agent_cfg)
            _opt_ok = _opt_on and engine.model_should_optimize_tools(auto_model)
            with session.lock:
                session._auto_tool_groups = (
                    auto_analysis.get("tool_groups")
                    if (auto_analysis and _opt_ok) else None)
                session._auto_route_model = auto_model or ""
                # Remember the composer's auto DIRECTIVE (which Smart mode) so
                # the post-turn restore re-persists "auto-cloud"/"auto-local"
                # rather than flattening both to legacy "auto" — a reopened
                # Smart (Lokal) session must come back as Lokal, not Cloud.
                session._composer_auto_model = (
                    "auto-local" if auto_pool == "local" else "auto-cloud")
            # Emit the pick at turn start so the spinner shows the model that's
            # actually doing the work (the composer label stays "Auto").
            if auto_route:
                live.emit("auto_route", auto_route)
        else:
            # EVERY-TURN tool optimization for concrete-model turns: the model is
            # NOT chosen here (no auto-route), but we still classify the prompt so
            # the worker can reshape this turn's tool DEFERRAL toward the needed
            # groups. This is INDEPENDENT of auto-routing — it runs whenever the
            # per-agent `optimize_tools` flag is on AND the model is safe to
            # reshape (model_should_optimize_tools: cloud, or a warmup-DISABLED
            # local model — warmup-enabled models keep a stable KV prefix and are
            # left untouched, classifier_tool_deferral would no-op anyway, so
            # don't pay the classifier cost). Model/provider/session.model are
            # left untouched.
            with session.lock:
                session._auto_tool_groups = None
                session._auto_route_model = session.model or ""
            if (session.model
                    and engine.agent_optimize_tools_enabled(agent_cfg)
                    and engine.model_should_optimize_tools(session.model)):
                try:
                    _ta = engine.resolve_task_analysis(message)
                    # Trim whenever the LLM classifier RAN — keyed on the
                    # presence of `tool_groups`, NOT its truthiness. An EMPTY
                    # list is a valid, strong signal ("this turn needs no tool
                    # groups", e.g. a greeting) and must defer everything down
                    # to the floor — the previous `if _tg:` guard treated [] as
                    # "no signal" and kept the full 65-tool prompt (the 11k-"hi"
                    # bug). A None / keyword-only result (no tool_groups key) is
                    # genuine no-signal → fail-open, static deferral stands.
                    if isinstance(_ta, dict) and "tool_groups" in _ta:
                        with session.lock:
                            session._auto_tool_groups = _ta.get("tool_groups") or []
                    # Surface the classifier decision even on concrete-model
                    # turns (NO auto-routing), so the chat-view inspector button
                    # appears whenever the LLM classifier ran — not only in Auto
                    # mode. We reuse the auto_route metadata shape (so the same
                    # button + modal work unchanged) but mark classifier_only so
                    # the modal can drop the "model decision" section: no model
                    # was chosen here, only the tool surface was reshaped.
                    if isinstance(_ta, dict) and _ta.get("source") == "llm":
                        auto_route = {
                            "classifier_only": True,
                            "model": session.model or "",
                            "analysis": {
                                "task_types": _ta.get("task_types", []),
                                "tools": _ta.get("tools", []),
                                "complexity": _ta.get("complexity", ""),
                                "reasoning": _ta.get("reasoning", ""),
                            },
                        }
                except Exception:
                    pass  # fail-open: no reshape, static deferral stands

        content_blocks = []
        disk_files = []
        MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20MB

        if all_attachments:
            raw_formats = engine.get_model_raw_formats(session.model)
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)

            for f in all_attachments:
                mime = f["media_type"]
                is_base64 = f["encoding"] == "base64"
                # Check file size (base64 is ~4/3 of raw)
                too_large = is_base64 and len(f["content"]) * 3 // 4 > MAX_INLINE_BYTES
                # OpenAI wire format only supports image/* as multimodal content blocks
                api_blocked = not mime.startswith("image/")

                if (engine._mime_matches(mime, raw_formats)
                        and is_base64 and not too_large and not api_blocked):
                    # Route as multimodal content block — LLM sees raw data as image_url data URI
                    data_uri = f"data:{mime};base64,{f['content']}"
                    content_blocks.append({"type": "image_url", "image_url": {"url": data_uri}})
                    # ALSO save image attachments to disk so the model can
                    # manipulate the bytes via shell tools (magick, ffmpeg,
                    # python_exec). The vision block lets it SEE the image;
                    # the disk file lets it PROCESS the image.
                    if is_base64 and mime.startswith("image/"):
                        disk_files.append(f)
                else:
                    # Route to disk — agent uses read_document/read_file
                    disk_files.append(f)

        # ── Manual web-search: capture the user-curated source set ──
        # The composer's Websuche tab lets the user run searches, mark URLs, and
        # accumulate them into a basket; on send the enabled entries arrive in
        # `web_urls_to_fetch`. The actual fetch + injection happens at TURN time
        # (in the worker, just before the wire build) — NOT here and NOT into
        # the persisted user message. Two reasons: (1) the fetched content must
        # be EPHEMERAL — present only on the wire for this turn, never written
        # to session.messages/DB — so re-sending the same prompt tomorrow
        # re-fetches the URLs fresh instead of replaying yesterday's frozen
        # page from history (a weather page would be permanently stale
        # otherwise). (2) it would bloat the user message by tens of KB. When
        # the set is non-empty the web tools are hard-disabled for the turn
        # (see `exclude_tools` in the worker) unless allow_further_web is on.
        # Per-chat Websuche basket only. (Project-level URLs are a DIFFERENT
        # mechanism — they're mined into the project's MemPalace wing + KG by
        # the project-sync daemon, reached via mempalace_query/KG like any
        # project knowledge, NOT injected per-turn here.)
        web_urls = body.get("web_urls_to_fetch") or []
        web_locked = bool(web_urls) and not bool(getattr(session, "allow_further_web", False))
        # Deep Research toggle (composer 🔬): when on, this turn runs the bounded
        # research loop instead of the LLM chat turn and drops the report as
        # session artifacts. Independent of the other toggles (per the design).
        deep_research = bool(body.get("deep_research", False))
        # First-turn preamble: the per-session artifact-folder pointer. It used
        # to live in the system prompt, but that made the prompt session-
        # dependent and broke the oMLX warm-pool KV-prefix match (warmup has no
        # session → no line; the real turn has one → full prefill, ~20s on the
        # 26B). Prepended here to the first user message instead, so the system
        # prompt stays session-agnostic and the warm prefix is reused.
        # Gate the artifact-folder pointer on whether a file-WRITING tool is
        # actually in this turn's prompt. The generic "write relative filenames
        # → Artifacts panel" guidance now lives in the tool descriptions
        # (python_exec/execute_command/write_file/write_document); the preamble
        # carries ONLY the per-session absolute path (which can't be static —
        # it's session-specific and would break the warm-pool KV prefix if put
        # in the system prompt or a tool schema). So: no file-write tool this
        # turn → no path pointer (it was pure noise on a greeting / lookup turn,
        # see the per-turn classifier trimming). _auto_tool_groups is the
        # classifier's needed groups (set just above for both the auto and
        # concrete-model branches): a LIST → show only if it flags a file group;
        # None → no signal (static deferral, file tools present) → show.
        _FILE_WRITE_GROUPS = {"core", "documents", "code_exec"}
        _ctg = getattr(session, "_auto_tool_groups", None)
        _has_file_tools = (_ctg is None) or bool(set(_ctg) & _FILE_WRITE_GROUPS)
        preamble_text = ""
        if len(session.messages) == 0 and _has_file_tools:
            _art_pre = engine._artifact_folder_preamble_text(session.agent_id, session.id)
            if _art_pre:
                preamble_text = _art_pre
                message = f"{_art_pre}\n\n{message}"

        # Build user_content with any multimodal blocks
        if content_blocks:
            content_blocks.append({"type": "text", "text": message})
            user_content = content_blocks
        else:
            user_content = message

        # Save disk-routed files and append notice. `saved_paths` is also
        # consumed by the worker's anonymise block — keep it defined even
        # when no files were attached so the closure capture works.
        saved_paths: list[str] = []
        if disk_files:
            attach_dir = os.path.join("/tmp", "brain-attachments", session.id)
            os.makedirs(attach_dir, exist_ok=True)
            for f in disk_files:
                fname = f.get("name", "file")
                safe_name = fname.replace("/", "_").replace("\\", "_")
                fpath = os.path.join(attach_dir, safe_name)
                content = f.get("content", "")
                if f.get("encoding") == "base64":
                    with open(fpath, "wb") as fp:
                        fp.write(_b64.b64decode(content))
                else:
                    with open(fpath, "w", errors="replace") as fp:
                        fp.write(content)
                saved_paths.append(fpath)
            paths_list = "\n".join(f"  - {p}" for p in saved_paths)
            has_docs = any(os.path.splitext(p)[1].lower() in (".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv")
                           for p in saved_paths)
            has_inline_images = bool(content_blocks)
            if has_docs:
                notice = (f"\n\n[User attached files saved to disk. "
                          f"IMPORTANT: Use the read_document tool (NOT read_file) to read these — "
                          f"read_document handles PDF, DOCX, XLSX, PPTX and other document formats:]\n{paths_list}")
            elif has_inline_images:
                notice = (f"\n\n[User attached image(s). You can already SEE them above as inline content — "
                          f"do NOT call write_file/read_file to load them. The same bytes are ALSO saved to disk "
                          f"if you need to manipulate them with shell tools (e.g. `magick`, `ffmpeg`) or python_exec. "
                          f"Write outputs to the session artifact folder, "
                          f"or call `execute_command` without a `cwd` (it defaults there):]\n{paths_list}")
            else:
                notice = f"\n\n[User attached files saved to disk:]\n{paths_list}"
            message = message + notice
            if isinstance(user_content, str):
                user_content = user_content + notice
            else:
                for block in user_content:
                    if block.get("type") == "text":
                        block["text"] = block["text"] + notice
                        break

        # ── Transparent anonymisation (pre-headers branch) ──
        # If the client's pre-send GDPR modal returned `gdpr_action`, honor it
        # before the user message lands in session.messages.
        #   "local_model"  → swap session.model to the local fallback; no
        #                    anonymisation needed (data stays on-prem). Done
        #                    inline here — no SSE prompt needed, just a model
        #                    swap before warmup-promotion.
        #   "anonymise"    → defer to the worker thread (inside the SSE-open
        #                    region), where a recovery-modal SSE event can
        #                    actually reach the client.
        #   "continue"     → user accepted warn-level findings; no-op.
        gdpr_action = (body.get("gdpr_action") or "").strip().lower()
        # Session-sticky anonymise: once a session has anonymised once (mapping
        # row in pseudonym_maps OR sticky pref == 'anonymise'), every
        # subsequent turn re-enters the anonymise branch automatically — the
        # client doesn't need to re-prompt the user on every send. The
        # composer shield button (`btn-gdpr-pref`) explicitly clears the pref
        # when the user wants to stop anonymising. `local_model` / `continue`
        # prefs win over implicit stickiness (they're explicit non-anonymise
        # choices). The modal still fires the FIRST time PII appears in a
        # session (handled client-side via `has_gdpr_mapping`).
        _had_prior_mapping = False
        try:
            _had_prior_mapping = bool(
                ChatDB.list_pseudonym_maps_for_session(sid) or [])
        except Exception:
            _had_prior_mapping = False
        _pref = (getattr(session, "gdpr_action_pref", "") or "").strip()
        # `_gdpr_skip_auto` is set by `_handle_sessions_manage` when the user
        # explicitly clears the pref via the composer shield. Without it, the
        # implicit "session has a mapping → keep anonymising" rule below would
        # ignore the user's opt-out. The flag is in-memory only — a reload
        # resets it to False, at which point a fresh PII find re-prompts.
        _opted_out = bool(getattr(session, "_gdpr_skip_auto", False))
        # A LOCAL model never sends data off the machine, so anonymisation is
        # pointless there — the sticky "session anonymised once → keep
        # anonymising" rule must NOT fire when this turn runs on a local model
        # (observed: a chat that was cloud+anonymise, then switched to a local
        # model, still anonymised the local turn). An EXPLICIT gdpr_action from
        # the client is still honoured (the user chose it). Only the IMPLICIT
        # sticky/auto path is suppressed for local models.
        # Effective locality of THIS turn: the currently-selected model is local,
        # OR the user picked "local_model" in the decision modal (the turn will be
        # swapped to the local fallback below). Both mean no off-machine egress →
        # no anonymisation (new text, history, OR attachments).
        _is_local_turn = False
        try:
            _is_local_turn = bool(engine.is_model_local(session.model))
            if not _is_local_turn and gdpr_action == "local_model":
                _fb = (engine._get_gdpr_scanner_config().get(
                    "default_local_fallback_model") or "").strip()
                _is_local_turn = bool(_fb and engine.is_model_local(_fb))
        except Exception:
            _is_local_turn = False
        if _is_local_turn and not gdpr_action:
            pass  # local model + no explicit choice → no auto-anonymise
        elif not gdpr_action and _pref == "anonymise":
            gdpr_action = "anonymise"
        elif (not gdpr_action and _had_prior_mapping and not _opted_out
              and _pref not in ("local_model", "continue")):
            gdpr_action = "anonymise"
        # Clear in-memory state only when we're NOT continuing an anonymise
        # session. When we are, rehydrate so the worker's anonymise branch
        # finds a live mapping and the streaming deanonymiser is wired up
        # before any text_delta lands.
        #
        # When the user chose 'continue'/'local' for THIS turn's new finding but
        # the session has ALREADY anonymised earlier values (_had_prior_mapping),
        # we still rehydrate the mapping — NOT to anonymise the new text
        # (_gdpr_pending_action stays empty below, so that's left in clear per
        # the user's choice) but so the REPLY de-anonymiser is active: the
        # deterministic wire-history pass rewrites prior values to their fakes,
        # and the model may echo those fakes, which the streamer reverses back to
        # the real values before the user sees them. Skipped on an explicit
        # shield opt-out (_opted_out). This is what keeps the already-anonymised
        # history protected from turn 5 on without re-anonymising the new value.
        if gdpr_action == "anonymise":
            rehydrate_session_gdpr_mapping(session)
        elif _had_prior_mapping and not _opted_out and not _is_local_turn:
            # Rehydrate the mapping so the reply de-anonymiser + mid-turn
            # read_document pseudonymisation stay active — but NOT for a local
            # turn: a local model gets the real values (no egress), so no
            # mapping, no attachment pseudonymisation, no de-anon needed.
            rehydrate_session_gdpr_mapping(session)
        else:
            session._gdpr_mapping_id = None
            session._gdpr_streamer = None
        session._gdpr_pending_action = gdpr_action if gdpr_action == "anonymise" else ""

        if gdpr_action == "local_model":
            _fallback = (engine._get_gdpr_scanner_config().get(
                "default_local_fallback_model") or "").strip()
            if not _fallback:
                self._send_json(
                    {"error": "No default_local_fallback_model configured; "
                              "GDPR local-model action unavailable."}, 400)
                return
            if _fallback != session.model:
                provider = self._resolve_provider(_fallback)
                with session.lock:
                    session.model = _fallback
                    session.api_key = provider["api_key"]
                    session.base_url = provider["base_url"]
                    session.max_context = engine.get_model_max_context(_fallback)
            # Audit row — single, no synthetic tool-call (no anonymisation
            # happened, just a model swap). Mirrors `pii_auto_fallback`.
            try:
                if engine._audit_log:
                    engine._audit_log.log_action(
                        agent=session.agent_id, session_id=sid,
                        action_type="pii_local_swap",
                        tool_name="gdpr_scanner",
                        args_summary="interactive_chat",
                        result_summary=f"→ {_fallback}",
                        result_status="success",
                        duration_ms=0, source="chat",
                    )
            except Exception:
                pass
            # Per-turn outcome badge: PII found, answered on the local model.
            # Read by the worker when assembling the assistant turn's
            # metadata.gdpr (the worker runs in a separate thread, so this
            # rides on the session like `_gdpr_pending_action` does).
            session._gdpr_local_swap = _fallback
        else:
            session._gdpr_local_swap = ""

        # Persist CLEARTEXT-accepted PII server-side. When the user accepted the
        # findings in the clear — "continue" (send via cloud as-is) or
        # "local_model" (send to a local model unredacted) — the PII goes out
        # un-anonymised, and the chat view marks those values RED. That mark is
        # read back from the pii_decisions table, so without a row the accepted
        # value never gets coloured on reload (the reported bug: the client's
        # modal only persisted "ratable" DOM rows, so an only-"seen" finding —
        # or a stale-JS client — wrote nothing). We re-scan the outgoing text
        # here and persist one row per finding, INDEPENDENT of the client. This
        # is the authoritative seam — the same place the anonymise path mints
        # its pseudonym map. Best-effort: a scan/record failure must never block
        # the send.
        if gdpr_action in ("continue", "local_model") and isinstance(message, str) and message.strip():
            try:
                import re as _re_pii
                _cfg = engine._get_gdpr_scanner_config()
                if _cfg.get("enabled", True):
                    _findings = engine._pii_scan_text(message, cfg=_cfg, max_findings=100)
                    _seen_vals = set()
                    _decisions = []
                    for _f in (_findings or []):
                        _s, _e = _f.get("start", 0), _f.get("end", 0)
                        _val = message[_s:_e] if 0 <= _s < _e <= len(message) else ""
                        _val = _re_pii.sub(r"\s+", " ", _val).strip()
                        if not _val:
                            continue
                        _key = (_f.get("rule_id") or "", _val)
                        if _key in _seen_vals:
                            continue
                        _seen_vals.add(_key)
                        _decisions.append({
                            "rule_id": _f.get("rule_id") or "",
                            "value": _val,
                            "confidence": _f.get("confidence") or 0,
                            "band": engine._pii_band(_f.get("confidence") or 0.5, _cfg),
                            "disposition": engine._pii_resolve_disposition(_f, _cfg),
                            "false_positive": False,
                            "source": "message",
                        })
                    if _decisions:
                        _uid = ""
                        try:
                            _u = getattr(self, "_auth_user", None)
                            _uid = (_u.get("user_id") or "") if isinstance(_u, dict) else ""
                        except Exception:
                            _uid = ""
                        # turn_action mirrors the client's verdict vocabulary so
                        # buildGdprCleartextSpans treats it as accepted-cleartext:
                        # 'send' for continue, 'local' for local_model.
                        _ta = "send" if gdpr_action == "continue" else "local"
                        ChatDB.record_pii_decisions(sid, _uid, "", _ta, _decisions)
            except Exception as _e:
                print(f"[gdpr] cleartext decision persist failed: {_e}", flush=True)

        # gdpr_action="anonymise" runs INSIDE the worker thread (below) so the
        # SSE response is already open + the client is listening when we emit
        # `synthetic_tool_use` / `gdpr_recovery_required` events. Doing the
        # work here, before send_response(200), would deadlock: live.emit()
        # only fans into the in-memory buffer, but the client's fetch() hangs
        # waiting for headers, so it can't fetch the recovery modal payload
        # or POST a choice back. The worker reads
        # session._gdpr_pending_action == 'anonymise' as its trigger.

        # Promote warmup session to active on first message
        if session.status == "warmup":
            session.status = "active"
            ChatDB.save_session(session.id, session.agent_id, session.model,
                               session.title, session.status, session.created_at,
                               session.last_active, session.project or "")

        # Add user message (persisted to DB). When gdpr_action='anonymise',
        # we DEFER the add until the worker has pseudonymized — otherwise the
        # DB briefly holds the raw PII text and the session.messages list
        # would feed the original to the LLM. The worker is responsible for
        # session.add_message("user", ...) in the anonymise branch.
        if session._gdpr_pending_action != "anonymise":
            # `metadata.preamble` carries the round-0 artifact-folder note that
            # was prepended into `content` above. It stays in `content` so the
            # model still sees it on the wire (the sidecar reads content, not
            # metadata), but the UI uses this field to peel the prefix off and
            # render it as a collapsed "Preamble" block instead of inline text.
            _umeta = {"preamble": preamble_text} if preamble_text else None
            session.add_message("user", user_content, metadata=_umeta)

        # SSE streaming setup (start early so we can send compaction events)
        # Disable Nagle's algorithm for real-time SSE delivery
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()  # Ensure headers are pushed before streaming

        # Wait for warmup if in progress (after SSE headers so client stays connected)
        if session._warmup_active:
            try:
                self.wfile.write(b"event: warmup\ndata: {\"status\":\"waiting\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            completed = session._warmup_done.wait(timeout=30)
            try:
                if completed and not session._warmup_cancel.is_set():
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                else:
                    # Warmup cancelled or timed out — proceed anyway but log it
                    reason = "cancelled" if session._warmup_cancel.is_set() else "timed out"
                    print(f"  [warmup] {session.model} {reason}, proceeding without cache ({session.id[:8]})")
                    self.wfile.write(b"event: warmup\ndata: {\"status\":\"ready\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # No tool-result pre-processing: `_apply_tool_result_budget` /
        # `_microcompact` both scan `session.messages` for role=="tool" /
        # tool_result blocks, but in the sidecar architecture those NEVER live
        # in `session.messages` — the sidecar owns the per-turn ephemeral tool
        # exchange end-to-end (see sidecar_proxy._to_anthropic_messages, which
        # keeps only user/assistant). The worker only ever persists user /
        # assistant / thinking rows, so both functions were no-ops on this path
        # (native-loop relics). Tool results reach the model UNCAPPED in the
        # turn that calls them (tool_mcp returns them verbatim) and are never
        # replayed on later turns — neither full nor stubbed.

        # LCM is manual-only (status-bar ✂️ button → POST /v1/context/compact).
        # No automatic trigger here; the user decides when to compact.

        _turn_thread = run_session_turn(
            session, sid=sid, message=message, user_content=user_content,
            chat_mode=chat_mode, thinking_level=thinking_level, live=live,
            saved_paths=saved_paths, web_urls=web_urls, web_locked=web_locked,
            project_name=project_name, preamble_text=preamble_text,
            content_blocks=content_blocks, disk_files=disk_files,
            auto_route=auto_route, want_auto=want_auto, deep_research=deep_research,
        )

        # Stream this turn's events to the originating connection. The worker is
        # decoupled from this connection — if the client disconnects, the worker
        # keeps running and its events stay buffered in `live` for a reconnect.
        self._stream_live_to_client(live, worker_thread=_turn_thread)

    def _stream_live_to_client(self, live, worker_thread=None):
        """Replay `live`'s buffered events to self.wfile, then follow live ones
        until the terminal done/error (or the worker thread dies). Used both by
        the originating POST /v1/chat connection and by GET /v1/chat/stream
        reconnects. A client disconnect here NEVER cancels the worker."""
        sub, replay, already_done = live.attach()
        try:
            for event_type, data in replay:
                self.wfile.write(encode_sse(event_type, data)); self.wfile.flush()
            if already_done:
                return
            while True:
                try:
                    event = sub.get(timeout=5)
                except queue.Empty:
                    if live.done:
                        break
                    if worker_thread is not None and not worker_thread.is_alive():
                        try:
                            self.wfile.write(encode_sse("error", {"message": "Server worker terminated unexpectedly"})); self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    try:
                        self.wfile.write(KEEPALIVE); self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                    continue
                event_type, data = event
                self.wfile.write(encode_sse(event_type, data)); self.wfile.flush()
                if event_type in ("done", "error"):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            live.detach(sub)

    def _handle_chat_stream(self):
        """GET /v1/chat/stream?session_id=X — (re)attach to an in-progress turn.

        Replays every SSE event emitted so far this turn, then follows live ones
        until the terminal done/error. If no turn is running (the chat is idle or
        the turn finished between the client's GET /messages and this call), emits
        a single `idle` event and closes — the client then renders persisted
        messages from GET /messages. Disconnecting NEVER cancels the worker, and
        any number of tabs may attach concurrently.
        """
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        sid = (qs.get("session_id") or [""])[0].strip()
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        if self._session_access_check(sid) is None:
            return
        session = sessions.get(sid)
        # SSE headers
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()
        live = getattr(session, "live_stream", None) if session else None
        if live is None:
            try:
                self.wfile.write(encode_sse("idle", {})); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self._stream_live_to_client(live, worker_thread=None)

    def _handle_cancel_scheduled(self):
        """POST /v1/schedule/cancel — cancel a running scheduled task."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Task name required"}, 400)
            return
        if engine._scheduler and engine._scheduler.cancel_running_task(name):
            self._send_json({"status": "cancelling", "name": name})
        else:
            self._send_json({"error": f"Task '{name}' not running"}, 404)

    def _handle_chat_answer(self):
        """POST /v1/chat/answer — deliver a user answer to a pending ask_user tool call.

        Body shapes:
          {session_id, answer: "..."}                             # single question
          {session_id, answers: {"<question>": "<answer>", ...}}  # batch
        """
        try:
            body = self._read_json()
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        answer = body.get("answer")
        answers = body.get("answers")
        if not session_id or (answer is None and not isinstance(answers, dict)):
            self._send_json({"error": "session_id and answer/answers are required"}, 400)
            return
        if self._session_access_check(session_id) is None:
            return
        # Normalize answers dict values to strings
        if isinstance(answers, dict):
            answers = {str(k): str(v) for k, v in answers.items() if v is not None}
        from brain import deliver_ask_user_answer
        ok = deliver_ask_user_answer(
            session_id,
            answer=str(answer) if answer is not None else None,
            answers=answers if isinstance(answers, dict) and answers else None,
        )
        if not ok:
            self._send_json({"error": "no pending question for this session"}, 404)
            return
        self._send_json({"delivered": True, "session_id": session_id})

    def _handle_chat_gdpr_recovery(self):
        """POST /v1/chat/gdpr-recovery — deliver the user's response to the
        anonymisation-failure modal.

        Body: `{session_id, action: "local_model"|"cancel"}`. Refuses any
        other action — there is intentionally no "send to cloud anyway"
        path; the whole point of the feature is that GDPR data never reaches
        a cloud LLM after a failed anonymisation."""
        try:
            body = self._read_json()
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        action = (body.get("action") or "").strip().lower()
        if not session_id or action not in ("local_model", "cancel"):
            self._send_json(
                {"error": "session_id and action ('local_model'|'cancel') required"},
                400)
            return
        if self._session_access_check(session_id) is None:
            return
        ok = deliver_gdpr_recovery_choice(session_id, action)
        if not ok:
            self._send_json(
                {"error": "no pending GDPR recovery for this session"}, 404)
            return
        self._send_json({"delivered": True, "session_id": session_id,
                         "action": action})

    def _handle_chat_handover(self):
        """POST /v1/chat/handover — generate a handover document for a chat.

        Body: `{session_id}`. The chat's RESOLVED model writes a structured
        markdown handover (goal, decisions, current state, open items, next
        steps) of the whole conversation so it can be attached to a NEW chat
        and the model told to continue where it left off. Returns the markdown
        — the client creates the new chat and seeds it with the attachment +
        a 'this is a handover, continue' prompt. Available in ANY chat (the
        composer button + the auto-LCM over-threshold modal both call this)."""
        try:
            body = self._read_json()
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            self._send_json({"error": "session_id required"}, 400)
            return
        if self._session_access_check(session_id) is None:
            return
        session = sessions.get(session_id)  # noqa: F821 — server-injected
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if len(session.messages) < 2:
            self._send_json({"error": "nothing_to_hand_over",
                             "message": "Dieser Chat hat noch keinen Verlauf für eine Übergabe."}, 400)
            return
        try:
            summary_md, transcript_md, artifact_name = _generate_handover_document(session)
        except engine.GDPRBlockedError as e:
            self._send_json({"error": "gdpr_blocked", "message": str(e)}, 409)
            return
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        if not summary_md:
            self._send_json({"error": "generation_failed",
                             "message": "Die Übergabe konnte nicht erstellt werden."}, 500)
            return
        title = (session.title or session.summary or "Chat").strip()[:80]
        self._send_json({"session_id": session_id, "markdown": summary_md,
                         "transcript": transcript_md, "source_title": title,
                         "artifact_saved": artifact_name})

    def _handle_attachment_scan(self):
        """POST /v1/attachments/scan — upload-time PII scan for one attachment.

        Body: `{session_id, name, content (base64), media_type}`.

        Returns:
          {scanned: true,  attachment_id, source_name, findings: [...],
           categories: {...}, finding_count}
          — extracted text was scanned successfully.

          {scanned: false, attachment_id, source_name, reason: "archive"|"media"
           |"unsupported"|"too_large"|"extract_timeout"|"extract_failed"}
          — scan was not run. `archive` / `media` are accepted gaps (treat as
            'opaque, send if user explicitly accepts'); the rest are
            BLOCKING — the client must refuse to send while any attachment
            on the composer has reason in {unsupported, too_large,
            extract_timeout, extract_failed}.

        Caps: 50 MB file size, 30 s extract+scan timeout.
        """
        import base64 as _b64
        import threading as _threading
        try:
            body = self._read_json()
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        session_id = (body.get("session_id") or "").strip()
        name = (body.get("name") or "").strip() or "file"
        content = body.get("content") or ""
        encoding = body.get("encoding") or "base64"
        # Auth still required (caller must be a known user); session may be
        # absent because the chat hasn't been created yet (composer attach
        # happens before the first send). Fall back to a per-user scratch
        # directory so the temp file still lands somewhere bounded.
        user = self._require_auth()
        if user is None:
            return
        if session_id and self._session_access_check(session_id) is None:
            return
        if not content:
            self._send_json({"error": "content is required"}, 400)
            return

        MAX_BYTES = 50 * 1024 * 1024
        # 120s (was 30s): big PDFs/spreadsheets legitimately take longer to
        # extract+scan, and a timeout is no longer a hard send-blocker on the
        # client (it's surfaced as a non-blocking coverage gap), so a more
        # generous limit lets most real documents finish scanning.
        TIMEOUT_S = 120

        attachment_id = f"{int(time.time() * 1000):x}_{hash(name) & 0xffff:04x}"
        safe_name = name.replace("/", "_").replace("\\", "_")

        # Save to the same dir read_document will look at later; for a
        # session-less scan (composer pre-create) use a per-user scratch
        # dir whose contents read_document would never see anyway — it's
        # only used to feed the parser. The chat worker re-saves the file
        # under the real session dir at send time.
        attach_dir_key = session_id or f"_scan/{user['id']}"
        attach_dir = os.path.join("/tmp", "brain-attachments", attach_dir_key)
        try:
            os.makedirs(attach_dir, exist_ok=True)
        except Exception as _e:
            self._send_json({"error": f"cannot create attach dir: {_e}"}, 500)
            return
        fpath = os.path.join(attach_dir, safe_name)
        try:
            if encoding == "base64":
                raw = _b64.b64decode(content)
            else:
                raw = content.encode("utf-8", errors="replace")
        except Exception as _e:
            self._send_json({"error": f"bad content encoding: {_e}"}, 400)
            return
        if len(raw) > MAX_BYTES:
            self._send_json({
                "scanned": False,
                "attachment_id": attachment_id,
                "source_name": name,
                "reason": "too_large",
                "size": len(raw),
                "cap": MAX_BYTES,
            })
            return
        try:
            with open(fpath, "wb") as fp:
                fp.write(raw)
        except Exception as _e:
            self._send_json({"error": f"write failed: {_e}"}, 500)
            return

        # Extract + scan inside a daemon thread bounded by TIMEOUT_S.
        result_box: dict = {}

        def _worker():
            try:
                text, kind = engine.extract_attachment_text(fpath)
                if kind != "text":
                    result_box["kind"] = kind
                    return
                cfg = engine._get_gdpr_scanner_config()
                # Cap raw findings at 200 — enough for category accuracy on
                # large spreadsheets without shipping thousands of records
                # the modal would never render anyway.
                findings = engine._pii_scan_text(text or "",
                                                 cfg=cfg, max_findings=200)
                result_box["kind"] = "text"
                result_box["findings"] = findings
                result_box["text"] = text or ""
            except Exception as _e:
                result_box["error"] = f"{type(_e).__name__}: {str(_e)[:200]}"

        t = _threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=TIMEOUT_S)
        if t.is_alive():
            self._send_json({
                "scanned": False,
                "attachment_id": attachment_id,
                "source_name": name,
                "reason": "extract_timeout",
                "timeout_seconds": TIMEOUT_S,
            })
            return
        if "error" in result_box:
            self._send_json({
                "scanned": False,
                "attachment_id": attachment_id,
                "source_name": name,
                "reason": "extract_failed",
                "error": result_box["error"],
            })
            return
        kind = result_box.get("kind", "unsupported")
        if kind in ("archive", "media"):
            self._send_json({
                "scanned": False,
                "attachment_id": attachment_id,
                "source_name": name,
                "reason": kind,
            })
            return
        if kind == "unsupported":
            self._send_json({
                "scanned": False,
                "attachment_id": attachment_id,
                "source_name": name,
                "reason": "unsupported",
            })
            return
        findings = result_box.get("findings") or []
        full_text = result_box.get("text") or ""

        def _preview(f):
            s, e = int(f.get("start", 0)), int(f.get("end", 0))
            if 0 <= s < e <= len(full_text):
                return full_text[s:e][:24]
            return ""

        # Aggregate by rule_id. The modal renders one row per rule with
        # the count + up to 3 sample previews. For a 50k-row spreadsheet
        # this collapses what would be thousands of identical-shape
        # findings to a single readable line.
        groups: dict[str, dict] = {}
        SAMPLE_CAP = 3
        for f in findings:
            rid = f.get("rule_id") or "unknown"
            g = groups.get(rid)
            if g is None:
                g = {
                    "rule_id": rid,
                    "label": f.get("label") or rid,
                    "count": 0,
                    "samples": [],
                }
                groups[rid] = g
            g["count"] += 1
            if len(g["samples"]) < SAMPLE_CAP:
                p = _preview(f)
                if p and p not in g["samples"]:
                    g["samples"].append(p)
        # Category counts (rule_id -> count) — kept for backward compat
        # with the old client field; mirrors `groups[rid].count`.
        cats = {rid: g["count"] for rid, g in groups.items()}

        # Sort groups by count desc so the modal shows the dominant finding
        # type first.
        groups_list = sorted(groups.values(), key=lambda g: -g["count"])

        # ─── Classification detection (Phase B) ───
        # Reuse the same extracted full_text. Skip if scanner disabled
        # (detector returns None on disabled state). Detector is fail-open
        # — errors here never block the PII path.
        classification_block: dict | None = None
        try:
            cfg_cls = engine._get_classification_config()
            if cfg_cls.get("enabled", True):
                pdf_path = fpath if fpath.lower().endswith(".pdf") else ""
                result = engine._classification_scan_text(
                    full_text, filename=name, pdf_path=pdf_path,
                ) if full_text else None
                if result:
                    from engine.classification import (
                        LEVEL_LABEL_DE as _LL,
                        LEVEL_RANK as _LR,
                    )
                    # Two independent signals — both reported, but the
                    # action follows the HIGHER one so a confidential-by-
                    # content PDF marked "public" still gets the
                    # confidential policy. Symmetric: a strict-marked PDF
                    # whose content looks bland still gets the strict
                    # policy.
                    marker_lvl = result.get("marker_level")
                    heuristic = (result.get("content_signals") or {}).get(
                        "heuristic_level") or "public"
                    candidates = [lvl for lvl in (marker_lvl, heuristic) if lvl]
                    if candidates:
                        action_level = max(candidates, key=lambda x: _LR.get(x, 0))
                    else:
                        action_level = "unmarked"
                    action = engine._classification_effective_action(
                        action_level, cfg=cfg_cls)
                    classification_block = {
                        "marker_level": marker_lvl,
                        "analyzed_level": heuristic,
                        "final_level": result.get("final_level") or "unmarked",
                        "action_level": action_level,
                        "marker_meta": result.get("marker_meta") or {},
                        "marker_evidence": result.get("marker_evidence") or [],
                        "content_signals": result.get("content_signals") or {},
                        "mismatch": result.get("mismatch"),
                        "effective_action": action,
                        "level_label_de": _LL.get(action_level, action_level),
                    }
        except Exception:
            pass

        # Per-finding full records (9.197.0) so the modal can review attachment
        # PII finding-by-finding (value + confidence/band/disposition + FP
        # checkbox), the same UX as the typed message. Deduped by (rule|value)
        # and capped (a 50k-row spreadsheet must not return 50k rows to the UI —
        # human review of >FULL_CAP distinct values is impractical; the
        # aggregated `groups` above still reflects the true total). Whitespace
        # collapsed in the value (PDF line-breaks).
        import re as _re_af
        # The scan ran in a worker thread; re-fetch cfg here for band/disposition.
        cfg_af = engine._get_gdpr_scanner_config()
        findings_full = []
        seen_vals = set()
        FULL_CAP = 200
        for f in findings:
            s, e = int(f.get("start", 0)), int(f.get("end", 0))
            val = full_text[s:e] if 0 <= s < e <= len(full_text) else ""
            val = _re_af.sub(r"\s+", " ", val).strip()
            if not val:
                continue
            key = (f.get("rule_id") or "") + "|" + val.lower()
            if key in seen_vals:
                continue
            seen_vals.add(key)
            findings_full.append({
                "rule_id": f.get("rule_id") or "?",
                "label": f.get("label") or f.get("rule_id") or "?",
                "category": f.get("category", "personal"),
                "action": f.get("action", "warn"),
                "confidence": f.get("confidence"),
                "band": engine._pii_band(f.get("confidence") or 0.5, cfg_af),
                "disposition": engine._pii_resolve_disposition(f, cfg_af),
                "value": val,
            })
            if len(findings_full) >= FULL_CAP:
                break

        resp = {
            "scanned": True,
            "attachment_id": attachment_id,
            "source_name": name,
            # Server-side aggregation: one entry per rule_id, with the
            # total count + up to 3 sample previews. Kept for the count/badge.
            "groups": groups_list,
            # Per-finding records for the review dialog (deduped + capped).
            "findings_full": findings_full,
            "findings_truncated": len(seen_vals) >= FULL_CAP,
            # Legacy field kept for older clients.
            "findings": [],
            "categories": cats,
            "finding_count": sum(g["count"] for g in groups_list),
            "worst_disposition": engine._pii_worst_disposition(findings, cfg_af),
        }
        if classification_block is not None:
            resp["classification"] = classification_block
        self._send_json(resp)

    def _handle_gdpr_scan_text(self):
        """POST /v1/gdpr/scan-text — server-side PII scan for the pre-send
        composer check.

        The client's `PIIScanner` is regex-only — it can't see findings from
        the server-side spaCy NER pipeline. Without this endpoint, NER-only
        findings (names, addresses, organisations) never trigger the pre-send
        GDPR modal, so e.g. "Mein Name ist Alexander Klinsky" reaches a
        cloud model unanonymised. Background calls (chat_summary, refine,
        next_prompt) already go through `_pii_scan_text` and anonymise
        correctly; this brings the interactive chat path to parity.

        Body: `{text, source?}`. `source` is an optional label included in
        the response groups (defaults to "compose").

        Returns: `{groups: [{rule_id, label, count, samples: [...]}],
                  categories: {rule_id: count},
                  finding_count: int}`
        — same shape as `/v1/attachments/scan` so the client can fold the
        result into the same `scan.bySource` map without a separate path.

        Caps: 200 KB body, 100 findings (matches `_pii_scan_text` default).
        Auth: any authenticated user — same gate as sending a message,
        no separate admin requirement.
        """
        user = self._require_auth()
        if user is None:
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 200 * 1024:
                self._send_json({"error": "text too large (cap 200 KB)"}, 413)
                return
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        text = body.get("text") or ""
        source = (body.get("source") or "compose").strip() or "compose"
        if not isinstance(text, str):
            self._send_json({"error": "text must be a string"}, 400)
            return
        if not text:
            self._send_json({
                "groups": [], "categories": {}, "finding_count": 0,
            })
            return
        cfg = engine._get_gdpr_scanner_config()
        if not cfg.get("enabled", True):
            self._send_json({
                "groups": [], "categories": {}, "finding_count": 0,
                "disabled": True,
            })
            return
        # Eval-only: `{"raw_detection": true}` makes the scan report raw
        # DETECTION capability rather than the production enforcement POLICY.
        # Two production gates are neutralized:
        #   1. min_occurrences — a rule normally needs N distinct values per doc
        #      (name/email/phone=3, date=10, ...); we force every rule to 1.
        #   2. action=='ignore' — _pii_scan_text skips whole categories whose
        #      effective action is 'ignore' (contact/network/business_id in this
        #      deployment), so email/phone/name/IP/org never even surface. We
        #      force every rule's effective action to 'warn' via a rule_overrides
        #      dict whose .get always returns 'warn' (top precedence in
        #      _pii_effective_action). UI never sets raw_detection.
        if body.get("raw_detection"):
            # NB: these must be NON-EMPTY — _pii_effective_action /
            # _pii_min_occurrences do `(cfg.get(key) or {})`, and an empty dict
            # is falsy, which would silently discard the override. The sentinel
            # key keeps them truthy; `.get()` ignores it anyway.
            class _AllOnes(dict):
                def get(self, *a, **k):
                    return 1

            class _AllWarn(dict):
                def get(self, *a, **k):
                    return "warn"
            cfg = dict(cfg)
            cfg["min_occurrences"] = _AllOnes(_sentinel=1)
            cfg["rule_overrides"] = _AllWarn(_sentinel="warn")
        # Eval-only: allow toggling the opt-in name-precision gate per request so
        # the harness can A/B it without flipping the persisted config.
        if "name_precision" in body:
            cfg = dict(cfg)
            cfg["name_precision_gate"] = bool(body.get("name_precision"))
        try:
            findings = engine._pii_scan_text(text, cfg=cfg, max_findings=100)
        except Exception as e:
            print(f"[gdpr_scan_text] failed: {e}", flush=True)
            self._send_json({
                "groups": [], "categories": {}, "finding_count": 0,
                "error": "scan failed",
            })
            return

        # Eval-only escape hatch: `{"full": true}` returns EVERY finding with its
        # raw value + offsets, uncapped, so the PII-detector eval harness can do
        # value-level scoring without losing occurrences to the 3-sample cap.
        # Off by default; the UI path never sets it, so render behaviour is
        # unchanged.
        if body.get("full"):
            import re as _re_full
            items = []
            for f in findings:
                start, end = f.get("start", 0), f.get("end", 0)
                val = text[start:end] if 0 <= start < end <= len(text) else ""
                val = _re_full.sub(r"\s+", " ", val).strip()  # collapse PDF line-breaks
                items.append({
                    "rule_id": f.get("rule_id") or "?",
                    "category": f.get("category", "personal"),
                    "action": f.get("action", "warn"),
                    "confidence": f.get("confidence"),
                    "band": engine._pii_band(f.get("confidence") or 0.5, cfg),
                    "disposition": engine._pii_resolve_disposition(f, cfg),
                    "start": start, "end": end, "value": val,
                })
            self._send_json({
                "findings": items, "finding_count": len(items),
                "worst_disposition": engine._pii_worst_disposition(findings, cfg),
            })
            return

        # Same aggregation as /v1/attachments/scan: one entry per rule_id
        # with up to 3 sample previews. Keeps the client modal's render
        # path identical between attachment + text sources.
        groups: dict[str, dict] = {}
        for f in findings:
            rid = f.get("rule_id") or "?"
            entry = groups.setdefault(rid, {
                "rule_id": rid,
                "label": f.get("label", rid),
                "category": f.get("category", "personal"),
                "action": f.get("action", "warn"),
                "count": 0,
                "samples": [],
                "source": source,
                # Confidence band fields (9.195.0): per-rule worst confidence +
                # disposition so the client modal can render ignore/ask/act.
                "confidence": 0.0,
                "disposition": "ignore",
            })
            entry["count"] += 1
            conf = f.get("confidence") or 0.0
            if conf > entry["confidence"]:
                entry["confidence"] = conf
            disp = engine._pii_resolve_disposition(f, cfg)
            if disp == "anonymise" or (disp == "ask" and entry["disposition"] == "ignore"):
                entry["disposition"] = disp
            if len(entry["samples"]) < 3:
                start, end = f.get("start", 0), f.get("end", 0)
                if 0 <= start < end <= len(text):
                    entry["samples"].append(text[start:end])
        cats = {rid: g["count"] for rid, g in groups.items()}
        groups_list = sorted(groups.values(), key=lambda g: -g["count"])
        self._send_json({
            "groups": groups_list,
            "categories": cats,
            "finding_count": sum(g["count"] for g in groups_list),
            "worst_disposition": engine._pii_worst_disposition(findings, cfg),
        })

    def _handle_gdpr_decisions_post(self):
        """POST /v1/gdpr/decisions — persist the per-finding review outcome from
        the interactive pre-send dialog. Body: {session_id, turn_id?,
        turn_action, decisions: [{rule_id, value, confidence, band, disposition,
        false_positive, source}]}. Records one pii_decisions row per finding —
        drives 'already analysed' (skip re-asking decided values), FP-for-chat
        (FP values skip anonymisation), and global-learning stats."""
        user = self._require_auth()
        if user is None:
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        sid = (body.get("session_id") or "").strip()
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        decisions = body.get("decisions") or []
        if not isinstance(decisions, list):
            self._send_json({"error": "decisions must be a list"}, 400)
            return
        uid = (user.get("user_id") or "") if isinstance(user, dict) else ""
        try:
            n = ChatDB.record_pii_decisions(
                sid, uid, (body.get("turn_id") or "").strip(),
                (body.get("turn_action") or "").strip(), decisions)
        except Exception as e:
            print(f"[gdpr_decisions] record failed: {e}", flush=True)
            self._send_json({"error": "record failed"}, 500)
            return
        self._send_json({"recorded": n})

    def _handle_gdpr_decisions_get(self):
        """GET /v1/gdpr/decisions?session_id=X — prior decisions for a session,
        keyed by value_hash. Lets the client skip re-asking already-decided
        values and honour FP markings on follow-up turns."""
        user = self._require_auth()
        if user is None:
            return
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        sid = (q.get("session_id", [""])[0] or "").strip()
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        try:
            decisions = ChatDB.get_session_pii_decisions(sid)
        except Exception as e:
            print(f"[gdpr_decisions] get failed: {e}", flush=True)
            decisions = {}
        self._send_json({"decisions": decisions, "session_id": sid})

    def _handle_gdpr_decisions_stats_get(self):
        """GET /v1/gdpr/decisions/stats — aggregate per-rule FP stats for global
        learning / threshold tuning (admin)."""
        user = self._require_role("admin")
        if user is None:
            return
        try:
            stats = ChatDB.pii_decision_stats()
        except Exception as e:
            print(f"[gdpr_decisions] stats failed: {e}", flush=True)
            stats = []
        self._send_json({"stats": stats})
