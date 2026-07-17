# Provider-agnostic e-mail tools (Email-Tools v2 — successor of gmail_tools.py,
# EMAIL_TOOLS_V2_PLAN.md). Six tools over configurable connector accounts
# (tools_config.json → email.accounts[], types imap / pop3 / exchange_ews).
#
# Layering (E2): this module owns account resolution + the GDPR seams
# (`_gdpr_anon_tool_text` on every read result, attachment fail-closed +
# egress-relevant address validation on send) — the connectors in
# engine/email_connectors.py are pure protocol code. One fix point for all
# connector types ([[feedback_single_fix_point]]): a new connector cannot
# forget the privacy seams.
#
# Brain runtime state (tool config, AGENTS_DIR, request context, artifact
# folder resolver) is reached lazily via `import brain as _brain` inside
# function bodies (engine modules must not import brain at top level).

import json
import os
import re

from engine.email_connectors import ConnectorError, connector_for_account


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _email_config():
    """The `email` integration record (multi-account model, E3) or None."""
    import brain as _brain
    cfg = _brain.get_tool_config().get("email", {})
    if not cfg or cfg.get("enabled") is False:
        return None
    if not (cfg.get("accounts") or []):
        return None
    return cfg


def _resolve_account(args: dict):
    """Resolve the optional `account` arg to an account dict.

    Returns (account, None) or (None, error_message). An unknown name lists
    the available account names — self-healing for the model (E5)."""
    cfg = _email_config()
    if not cfg:
        return None, ("E-Mail ist nicht konfiguriert. Unter Einstellungen → Tools "
                      "→ email mindestens ein Konto anlegen.")
    accounts = cfg.get("accounts") or []
    name = (args.get("account") or "").strip()
    if not name:
        name = (cfg.get("default_account") or "").strip() or accounts[0].get("name", "")
    for acct in accounts:
        if acct.get("name") == name:
            return acct, None
    available = ", ".join(a.get("name", "?") for a in accounts)
    return None, (f"Unbekanntes E-Mail-Konto '{name}'. Verfügbare Konten: "
                  f"{available}. (email_accounts listet Details.)")


# Deterministic RFC-shape check on every recipient BEFORE the connector
# (all types). The v9.343.0 egress incident showed opaque pseudonym tokens
# only failed "by accident" on the address shape — this makes it explicit
# (EMAIL_TOOLS_V2_PLAN.md Anhang A, note 5). Deliberately loose (no full
# RFC 5322): local@domain.tld, no whitespace.
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_addresses(addrs: list[str]) -> str | None:
    for a in addrs:
        if not _ADDR_RE.match(a):
            return (f"'{a}' ist keine gültige E-Mail-Adresse "
                    "(erwartet: name@domain.tld).")
    return None


def _norm(v) -> list[str]:
    """Normalise `to`/`cc`: string (comma/semicolon-separated) or list → flat list."""
    if not v:
        return []
    if isinstance(v, str):
        parts = [p.strip() for p in re.split(r"[,;]\s*", v)]
        return [p for p in parts if p]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def tool_email_accounts(args: dict) -> str:
    """List configured e-mail accounts (name, type, address, capabilities).
    Read-only, no secrets — the account names feed the `account` parameter
    of the other email tools (E5: names must NOT live in the static schemas)."""
    import brain as _brain
    cfg = _email_config()
    if not cfg:
        return _err("E-Mail ist nicht konfiguriert. Unter Einstellungen → Tools "
                    "→ email mindestens ein Konto anlegen.")
    accounts = cfg.get("accounts") or []
    default = (cfg.get("default_account") or "").strip() or (
        accounts[0].get("name", "") if accounts else "")
    out = []
    for acct in accounts:
        entry = {
            "name": acct.get("name", ""),
            "type": acct.get("type", "imap"),
            "email": acct.get("email", ""),
            "default": acct.get("name") == default,
        }
        try:
            entry["capabilities"] = connector_for_account(acct).capabilities()
        except ConnectorError as e:
            entry["error"] = str(e)
        out.append(entry)
    # Result seam (E2): account addresses are the user's own, but the seam is
    # structural — every read-path result passes it; no-op without a mapping.
    return _brain._gdpr_anon_tool_text(
        _ok({"count": len(out), "default_account": default, "accounts": out}),
        "email_accounts")


def tool_email_inbox(args: dict) -> str:
    """List recent emails from the account's inbox (or another folder)."""
    import brain as _brain
    acct, err = _resolve_account(args)
    if err:
        return _err(err)
    limit = args.get("limit", 10)
    folder = args.get("folder", "INBOX")
    try:
        result = connector_for_account(acct).list_messages(folder=folder, limit=limit)
        result["account"] = acct.get("name", "")
        # M3 (G7/G9): result seam. Mail headers carry third-party names +
        # addresses — dense PII the model never saw before, going straight to
        # the cloud. No-op without an active mapping.
        return _brain._gdpr_anon_tool_text(_ok(result), "email_inbox")
    except Exception as e:
        return _err(f"email_inbox: {e}")


def tool_email_read(args: dict) -> str:
    """Read a specific email by ID."""
    import brain as _brain
    acct, err = _resolve_account(args)
    if err:
        return _err(err)
    email_id = args.get("id", "")
    folder = args.get("folder", "INBOX")
    if not email_id:
        return _err("email_read: email id is required")
    try:
        result = connector_for_account(acct).read_message(email_id, folder=folder)
        result["account"] = acct.get("name", "")
        # M3 (G7/G9): result seam — the mail BODY is the richest unseamed PII
        # source of all the read tools (a whole foreign conversation).
        return _brain._gdpr_anon_tool_text(_ok(result), "email_read")
    except Exception as e:
        return _err(f"email_read: {e}")


def tool_email_search(args: dict) -> str:
    """Search emails. Simple syntax everywhere (from:/subject:/to: + free text);
    accounts with native_query_syntax (Gmail preset) get the provider's full
    syntax via X-GM-RAW (E6). POP3 filters recent headers client-side and says
    so in the result."""
    import brain as _brain
    acct, err = _resolve_account(args)
    if err:
        return _err(err)
    query = args.get("query", "")
    limit = args.get("limit", 10)
    if not query:
        return _err("email_search: query is required")
    try:
        result = connector_for_account(acct).search(query, limit=limit)
        result["account"] = acct.get("name", "")
        # M3 (G7/G9): result seam (see email_inbox).
        return _brain._gdpr_anon_tool_text(_ok(result), "email_search")
    except Exception as e:
        return _err(f"email_search: {e}")


def tool_email_send(args: dict) -> str:
    """Send an email via the account's connector. `to` and `cc` accept either a
    single address string or a list — LLMs pass both shapes. `attachments` is
    an optional list of file paths; relative paths resolve against the current
    session's artifact folder (same convention as write_file)."""
    import brain as _brain
    acct, err = _resolve_account(args)
    if err:
        return _err(err)

    to_list = _norm(args.get("to", ""))
    cc_list = _norm(args.get("cc", ""))
    subject = args.get("subject", "")
    body = args.get("body", "")
    attachments_arg = args.get("attachments") or []
    if isinstance(attachments_arg, str):
        attachments_arg = [attachments_arg]
    if not to_list or not subject:
        return _err("email_send: to and subject are required")
    addr_err = _validate_addresses(to_list + cc_list)
    if addr_err:
        return _err(f"email_send: {addr_err}")

    # M2 (G7) — attachments are FAIL-CLOSED in an anonymising session.
    #
    # The egress gate (brain._gdpr_guard_web_args) inspects the ARGS, so it
    # catches a protected value in `to`/`subject`/`body`. It cannot see the
    # CONTENT of an attached file — and artifact files on disk have already been
    # de-anonymised by the after-file-write reverse (L6). So a mail whose args look
    # perfectly clean (fake recipient refused, fake-free body) could still ship a
    # REAL customer dossier as a .docx: fake body, clear-text attachment.
    #
    # There is no safe automatic answer here (re-anonymising the file would mean
    # sending a document that silently lies), so we refuse and make the human
    # decide. Only bites when a mapping is active; ordinary sessions are untouched.
    if attachments_arg:
        try:
            _mid = _brain.get_request_context()._gdpr_mapping_id or ""
        except Exception:
            _mid = ""
        if _mid:
            return _err(
                "email_send: Anhänge sind in einer anonymisierten Sitzung "
                "gesperrt. Die Datei auf der Platte enthält die ECHTEN Werte "
                "(sie wurde beim Schreiben zurückübersetzt) — sie zu versenden "
                "wäre ein Klartext-Egress am Datenschutz-Gate vorbei. "
                "Sende die Mail ohne Anhang, oder bitte die Nutzerin, die Datei "
                "bewusst manuell zu versenden.")

    # Resolve attachment paths + read bytes up-front so we can fail cleanly
    # before opening the connection. Match the write_file / python_exec
    # convention: relative paths resolve against the session artifact folder.
    import mimetypes
    resolved: list[tuple[str, bytes, str, str]] = []  # (name, bytes, maintype, subtype)
    session_id = _brain.get_request_context().current_session_id
    agent_ctx = _brain.get_request_context().current_agent or _brain._current_agent
    artifact_dir = None
    if session_id and agent_ctx:
        artifact_dir = os.path.join(
            _brain.AGENTS_DIR, agent_ctx.agent_id, "artifacts",
            _brain._get_artifact_session_folder(session_id))
    MAX_ATTACH_BYTES = 20 * 1024 * 1024  # practical inline cap ~25MB base64-encoded
    total = 0
    for p in attachments_arg:
        try:
            path = os.path.expanduser(str(p))
            if not os.path.isabs(path):
                if artifact_dir and os.path.exists(os.path.join(artifact_dir, path)):
                    path = os.path.join(artifact_dir, path)
                else:
                    path = os.path.abspath(path)
            if not os.path.isfile(path):
                return _err(f"email_send: attachment not found: {p}")
            size = os.path.getsize(path)
            total += size
            if total > MAX_ATTACH_BYTES:
                return _err(f"email_send: attachments exceed {MAX_ATTACH_BYTES // (1024*1024)} MB limit")
            with open(path, "rb") as fh:
                data = fh.read()
            mime, _ = mimetypes.guess_type(path)
            maintype, subtype = (mime.split("/", 1) if mime and "/" in mime
                                 else ("application", "octet-stream"))
            resolved.append((os.path.basename(path), data, maintype, subtype))
        except Exception as e:
            return _err(f"email_send: failed to read attachment {p}: {e}")

    try:
        result = connector_for_account(acct).send(
            to_list, cc_list, subject, body, attachments=resolved)
        result["account"] = acct.get("name", "")
        return _ok(result)
    except Exception as e:
        return _err(f"email_send: {e}")


def tool_email_reply(args: dict) -> str:
    """Reply to an email by its ID (threading preserved by the connector)."""
    acct, err = _resolve_account(args)
    if err:
        return _err(err)
    email_id = args.get("id", "")
    body = args.get("body", "")
    if not email_id or not body:
        return _err("email_reply: id and body are required")
    try:
        result = connector_for_account(acct).reply(email_id, body)
        result["account"] = acct.get("name", "")
        return _ok(result)
    except Exception as e:
        return _err(f"email_reply: {e}")


def test_email_account(account_name: str) -> dict:
    """Connectivity test for one account (admin UI, POST /v1/tools/email/test).
    Login/bind + one read, NO send. Returns {ok, detail|error}."""
    acct, err = _resolve_account({"account": account_name or ""})
    if err:
        return {"ok": False, "error": err}
    try:
        return connector_for_account(acct).test()
    except Exception as e:
        return {"ok": False, "error": str(e)}
