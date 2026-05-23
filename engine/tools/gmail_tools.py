# Gmail tools (extracted from brain.py, A4).
# IMAP/SMTP wrappers around a Gmail account (app-password auth).
# _ok / _err are re-declared locally (trivial JSON wrappers, matching the
# git_tools.py pattern). Brain runtime state (tool config, AGENTS_DIR,
# thread-locals, artifact-folder resolver) is reached lazily via
# `import brain as _brain` inside function bodies to avoid a circular import
# (brain.py imports these tools near module-eval end).

import json
import os
import re
import imaplib
import smtplib
import email
import email.mime.text
import email.mime.multipart
from email.header import decode_header as _decode_header


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _gmail_config():
    """Load Gmail credentials from tools_config, falling back to gmail.json."""
    import brain as _brain
    # Check tools_config first
    tcfg = _brain.get_tool_config().get("gmail", {})
    if tcfg.get("email") and tcfg.get("app_password"):
        return {"email": tcfg["email"], "app_password": tcfg["app_password"]}
    # Fall back to gmail.json
    config_path = os.path.join(_brain.AGENTS_DIR, "main", "gmail.json")
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _decode_mime_header(raw):
    """Decode a MIME-encoded header value."""
    if not raw:
        return ""
    parts = _decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(data))
    return " ".join(decoded)


def _get_email_body(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback to HTML
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    # Strip HTML tags roughly
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def tool_gmail_inbox(args: dict) -> str:
    """List recent emails from Gmail inbox."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured. Create agents/main/gmail.json with email and app_password.")
    limit = args.get("limit", 10)
    folder = args.get("folder", "INBOX")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select(folder, readonly=True)
        _, data = imap.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:]  # most recent
        ids.reverse()

        emails = []
        for eid in ids:
            _, msg_data = imap.fetch(eid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append({
                "id": eid.decode(),
                "from": _decode_mime_header(msg.get("From", "")),
                "subject": _decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
            })
        imap.logout()
        return _ok({"folder": folder, "count": len(emails), "emails": emails})
    except Exception as e:
        return _err(f"gmail_inbox: {e}")


def tool_gmail_read(args: dict) -> str:
    """Read a specific email by ID."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    email_id = args.get("id", "")
    folder = args.get("folder", "INBOX")
    if not email_id:
        return _err("gmail_read: email id is required")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select(folder, readonly=True)
        _, msg_data = imap.fetch(email_id.encode(), "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        body = _get_email_body(msg)
        # Truncate long bodies
        if len(body) > 10000:
            body = body[:10000] + "\n...(truncated)"
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                fn = part.get_filename()
                if fn:
                    attachments.append(_decode_mime_header(fn))
        imap.logout()
        return _ok({
            "id": email_id,
            "from": _decode_mime_header(msg.get("From", "")),
            "to": _decode_mime_header(msg.get("To", "")),
            "cc": _decode_mime_header(msg.get("Cc", "")),
            "subject": _decode_mime_header(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
            "body": body,
            "attachments": attachments,
            "message_id": msg.get("Message-ID", ""),
        })
    except Exception as e:
        return _err(f"gmail_read: {e}")


def tool_gmail_search(args: dict) -> str:
    """Search emails using Gmail search syntax."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    if not query:
        return _err("gmail_search: query is required")
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select("INBOX", readonly=True)
        # Gmail supports X-GM-RAW for full Gmail search syntax
        _, data = imap.search(None, f'X-GM-RAW "{query}"')
        ids = data[0].split()
        ids = ids[-limit:]
        ids.reverse()

        emails = []
        for eid in ids:
            _, msg_data = imap.fetch(eid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append({
                "id": eid.decode(),
                "from": _decode_mime_header(msg.get("From", "")),
                "subject": _decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
            })
        imap.logout()
        return _ok({"query": query, "count": len(emails), "emails": emails})
    except Exception as e:
        return _err(f"gmail_search: {e}")


def tool_gmail_send(args: dict) -> str:
    """Send an email via Gmail SMTP. `to` and `cc` accept either a single
    address string or a list of addresses — LLMs pass both shapes and
    smtplib's to_addrs=... requires a flat list of strings. `attachments`
    is an optional list of file paths; relative paths resolve against the
    current session's artifact folder (same convention as write_file)."""
    import brain as _brain
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")

    def _norm(v) -> list[str]:
        if not v:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in re.split(r"[,;]\s*", v)]
            return [p for p in parts if p]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v)]

    to_list = _norm(args.get("to", ""))
    cc_list = _norm(args.get("cc", ""))
    subject = args.get("subject", "")
    body = args.get("body", "")
    attachments_arg = args.get("attachments") or []
    if isinstance(attachments_arg, str):
        attachments_arg = [attachments_arg]
    if not to_list or not subject:
        return _err("gmail_send: to and subject are required")

    # Resolve attachment paths + read bytes up-front so we can fail cleanly
    # before opening the SMTP connection. Match the write_file / python_exec
    # convention: relative paths resolve against the session artifact folder.
    import mimetypes
    from email.message import EmailMessage as _EmailMessage
    resolved: list[tuple[str, bytes, str, str]] = []  # (name, bytes, maintype, subtype)
    session_id = getattr(_brain._thread_local, 'current_session_id', None)
    agent_ctx = getattr(_brain._thread_local, 'current_agent', None) or _brain._current_agent
    artifact_dir = None
    if session_id and agent_ctx:
        artifact_dir = os.path.join(
            _brain.AGENTS_DIR, agent_ctx.agent_id, "artifacts",
            _brain._get_artifact_session_folder(session_id))
    MAX_ATTACH_BYTES = 20 * 1024 * 1024  # gmail's practical inline cap is ~25MB (base64-encoded)
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
                return _err(f"gmail_send: attachment not found: {p}")
            size = os.path.getsize(path)
            total += size
            if total > MAX_ATTACH_BYTES:
                return _err(f"gmail_send: attachments exceed {MAX_ATTACH_BYTES // (1024*1024)} MB limit")
            with open(path, "rb") as fh:
                data = fh.read()
            mime, _ = mimetypes.guess_type(path)
            maintype, subtype = (mime.split("/", 1) if mime and "/" in mime
                                 else ("application", "octet-stream"))
            resolved.append((os.path.basename(path), data, maintype, subtype))
        except Exception as e:
            return _err(f"gmail_send: failed to read attachment {p}: {e}")

    try:
        # EmailMessage handles multipart + attachments cleanly. We also set
        # the legacy headers the server side expects.
        msg = _EmailMessage()
        msg["From"] = cfg["email"]
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        msg.set_content(body)
        for name, data, maintype, subtype in resolved:
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(cfg["email"], cfg["app_password"])
            smtp.send_message(msg, to_addrs=to_list + cc_list)

        return _ok({
            "status": "sent",
            "to": to_list if len(to_list) > 1 else to_list[0],
            "subject": subject,
            "attachments": [name for name, _b, _m, _s in resolved],
        })
    except Exception as e:
        return _err(f"gmail_send: {e}")


def tool_gmail_reply(args: dict) -> str:
    """Reply to an email."""
    cfg = _gmail_config()
    if not cfg:
        return _err("Gmail not configured.")
    email_id = args.get("id", "")
    body = args.get("body", "")
    if not email_id or not body:
        return _err("gmail_reply: id and body are required")
    try:
        # Fetch original email to get headers
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["email"], cfg["app_password"])
        imap.select("INBOX", readonly=True)
        _, msg_data = imap.fetch(email_id.encode(), "(RFC822)")
        raw = msg_data[0][1]
        original = email.message_from_bytes(raw)
        imap.logout()

        # Build reply
        reply_to = original.get("Reply-To") or original.get("From", "")
        subject = original.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = cfg["email"]
        msg["To"] = reply_to
        msg["Subject"] = subject
        msg["In-Reply-To"] = original.get("Message-ID", "")
        msg["References"] = original.get("Message-ID", "")
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(cfg["email"], cfg["app_password"])
            smtp.send_message(msg)

        return _ok({"status": "replied", "to": reply_to, "subject": subject})
    except Exception as e:
        return _err(f"gmail_reply: {e}")
