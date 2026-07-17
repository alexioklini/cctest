# E-Mail provider connectors (Email-Tools v2, EMAIL_TOOLS_V2_PLAN.md E1).
#
# Pure protocol code (imaplib / poplib / smtplib / exchangelib) — this module
# must NEVER `import brain` (not even lazily): it is testable without the
# server runtime. Account resolution, GDPR seams, attachment-path resolution
# and result JSON live one layer up in engine/tools/email_tools.py (E2 —
# a new connector structurally cannot forget the privacy seams).
#
# An *account* is a plain dict from tools_config.json → email.accounts[]
# (schema: EMAIL_TOOLS_V2_PLAN.md E3). `username` empty = `email` is the
# login (the Gmail case). Connectors return plain Python dicts; they raise
# ConnectorError (or any Exception) on failure — fail loud, the tool layer
# turns that into an error result the model can read.

import email
import email.utils
import imaplib
import poplib
import re
import smtplib
from email.header import decode_header as _decode_header
from email.message import EmailMessage


class ConnectorError(Exception):
    """A connector-level failure with a message meant for the model/user."""


# ---------------------------------------------------------------------------
# MIME helpers (moved verbatim from the old engine/tools/gmail_tools.py)
# ---------------------------------------------------------------------------

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


def _headers_dict(msg, msg_id):
    """The compact per-message listing entry shared by list/search results."""
    return {
        "id": str(msg_id),
        "from": _decode_mime_header(msg.get("From", "")),
        "subject": _decode_mime_header(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
    }


def _full_message_dict(msg, msg_id, body_limit=10000):
    """The full read_message result (headers + body + attachment names)."""
    body = _get_email_body(msg)
    if len(body) > body_limit:
        body = body[:body_limit] + "\n...(truncated)"
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                attachments.append(_decode_mime_header(fn))
    return {
        "id": str(msg_id),
        "from": _decode_mime_header(msg.get("From", "")),
        "to": _decode_mime_header(msg.get("To", "")),
        "cc": _decode_mime_header(msg.get("Cc", "")),
        "subject": _decode_mime_header(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "body": body,
        "attachments": attachments,
        "message_id": msg.get("Message-ID", ""),
    }


def _build_mime_message(from_addr, to_list, cc_list, subject, body,
                        attachments=None, headers=None):
    """Build an EmailMessage; attachments = [(name, bytes, maintype, subtype)]."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    for k, v in (headers or {}).items():
        if v:
            msg[k] = v
    msg.set_content(body)
    for name, data, maintype, subtype in (attachments or []):
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def _client_filter_headers(entries, query):
    """Client-side search over listing entries (POP3 / non-ASCII IMAP fallback).
    Understands the same simple syntax as the server-side translation:
    `from:x` / `subject:x` / `to:x` tokens plus free text (matched against
    from+subject, case-insensitive substring)."""
    tokens = _parse_simple_query(query)
    out = []
    for e in entries:
        hay_from = (e.get("from") or "").lower()
        hay_subj = (e.get("subject") or "").lower()
        ok = True
        for field, value in tokens:
            v = value.lower()
            if field == "from":
                ok = v in hay_from
            elif field == "subject":
                ok = v in hay_subj
            elif field == "to":
                ok = v in (e.get("to") or "").lower()
            else:  # free text
                ok = v in hay_from or v in hay_subj
            if not ok:
                break
        if ok:
            out.append(e)
    return out


_QUERY_TOKEN_RE = re.compile(r'(\w+):(?:"([^"]*)"|(\S+))|"([^"]*)"|(\S+)')


def _parse_simple_query(query):
    """Parse the simple search syntax into (field, value) pairs.
    field ∈ {'from','subject','to'} for recognised prefixes, '' for free text.
    Values may be quoted (`subject:"quarterly report"`)."""
    tokens = []
    free = []
    for m in _QUERY_TOKEN_RE.finditer(query or ""):
        key, qval, sval, qfree, sfree = m.groups()
        if key and key.lower() in ("from", "subject", "to"):
            tokens.append((key.lower(), qval if qval is not None else sval))
        elif key:
            # Unknown prefix (e.g. is:unread) — treat the whole token as text.
            free.append(f"{key}:{qval if qval is not None else sval}")
        else:
            free.append(qfree if qfree is not None else (sfree or ""))
    if free:
        tokens.append(("", " ".join(free)))
    return tokens


def _imap_search_criteria(query):
    """Translate the simple syntax to standard IMAP SEARCH criteria (E6)."""
    parts = []
    for field, value in _parse_simple_query(query):
        value = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        if field == "from":
            parts.append(f'FROM "{value}"')
        elif field == "subject":
            parts.append(f'SUBJECT "{value}"')
        elif field == "to":
            parts.append(f'TO "{value}"')
        elif value:
            parts.append(f'TEXT "{value}"')
    return "(" + " ".join(parts) + ")" if parts else "ALL"


# ---------------------------------------------------------------------------
# Base connector
# ---------------------------------------------------------------------------

class EmailConnector:
    """Abstract provider connector. Five operations + capability flags.

    Constructors must NOT open a network connection — `capabilities()` and
    account listings need to work offline (email_accounts tool, settings UI).
    """

    type_name = "?"

    def __init__(self, account: dict):
        self.account = account or {}

    # -- config helpers -----------------------------------------------------
    @property
    def address(self) -> str:
        return self.account.get("email", "")

    @property
    def login_user(self) -> str:
        # Empty username = the address is the login (the Gmail case, E3).
        return self.account.get("username") or self.address

    @property
    def password(self) -> str:
        # `app_password` accepted as an alias so a hand-migrated Gmail record
        # keeps working; the canonical field is `password`.
        return self.account.get("password") or self.account.get("app_password") or ""

    def capabilities(self) -> dict:
        raise NotImplementedError

    # -- operations ----------------------------------------------------------
    def list_messages(self, folder="INBOX", limit=10) -> dict:
        raise NotImplementedError

    def read_message(self, msg_id, folder="INBOX") -> dict:
        raise NotImplementedError

    def search(self, query, limit=10) -> dict:
        raise NotImplementedError

    def send(self, to_list, cc_list, subject, body, attachments=None) -> dict:
        raise NotImplementedError

    def reply(self, msg_id, body) -> dict:
        raise NotImplementedError

    def test(self) -> dict:
        """Side-effect-free connectivity check (login/bind, NO send)."""
        raise NotImplementedError

    # -- shared SMTP side (IMAP + POP3 connectors) ----------------------------
    def _smtp_send(self, msg, to_addrs=None):
        host = self.account.get("smtp_host", "")
        if not host:
            raise ConnectorError(f"Konto '{self.account.get('name', '?')}': smtp_host fehlt")
        security = (self.account.get("smtp_security") or "ssl").lower()
        port = int(self.account.get("smtp_port") or (465 if security == "ssl" else 587))
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
        try:
            if security == "starttls":
                smtp.starttls()
            if self.password:
                smtp.login(self.login_user, self.password)
            if to_addrs:
                smtp.send_message(msg, to_addrs=to_addrs)
            else:
                smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# IMAP + SMTP (covers Gmail, GMX, iCloud, Outlook.com-IMAP, generic servers)
# ---------------------------------------------------------------------------

class ImapSmtpConnector(EmailConnector):
    type_name = "imap"

    def capabilities(self) -> dict:
        return {
            "folders": True,
            "server_search": True,
            "native_query_syntax": self._native_query,
            "reply": True,
        }

    @property
    def _native_query(self) -> bool:
        # The gmail preset unlocks X-GM-RAW (full Gmail query syntax, E6);
        # an explicit account flag can force it for Gmail-compatible servers.
        return bool(self.account.get("native_query_syntax")
                    or self.account.get("preset") == "gmail")

    def _imap(self):
        host = self.account.get("imap_host", "")
        if not host:
            raise ConnectorError(f"Konto '{self.account.get('name', '?')}': imap_host fehlt")
        port = int(self.account.get("imap_port") or 993)
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(self.login_user, self.password)
        return imap

    def _fetch_headers(self, imap, ids):
        emails = []
        for eid in ids:
            _, msg_data = imap.fetch(eid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append(_headers_dict(msg, eid.decode()))
        return emails

    def list_messages(self, folder="INBOX", limit=10) -> dict:
        imap = self._imap()
        try:
            imap.select(folder or "INBOX", readonly=True)
            _, data = imap.search(None, "ALL")
            ids = data[0].split()
            ids = ids[-limit:]
            ids.reverse()
            emails = self._fetch_headers(imap, ids)
        finally:
            imap.logout()
        return {"folder": folder or "INBOX", "count": len(emails), "emails": emails}

    def read_message(self, msg_id, folder="INBOX") -> dict:
        imap = self._imap()
        try:
            imap.select(folder or "INBOX", readonly=True)
            _, msg_data = imap.fetch(str(msg_id).encode(), "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
        finally:
            imap.logout()
        return _full_message_dict(msg, msg_id)

    def search(self, query, limit=10) -> dict:
        imap = self._imap()
        try:
            imap.select("INBOX", readonly=True)
            if self._native_query:
                # Gmail: X-GM-RAW carries the full Gmail search syntax.
                crit = f'X-GM-RAW "{query}"'
            else:
                crit = _imap_search_criteria(query)
            scope = None
            if crit.isascii():
                _, data = imap.search(None, crit)
                ids = data[0].split()
                ids = ids[-limit:]
                ids.reverse()
                emails = self._fetch_headers(imap, ids)
            else:
                # imaplib encodes commands as ASCII — a non-ASCII term can't go
                # to the server. Fall back to client-side filtering over recent
                # headers and SAY SO in the result (fail loud, Regel 12).
                _, data = imap.search(None, "ALL")
                ids = data[0].split()[-_CLIENT_SEARCH_WINDOW:]
                ids.reverse()
                entries = self._fetch_headers(imap, ids)
                emails = _client_filter_headers(entries, query)[:limit]
                scope = f"last_{_CLIENT_SEARCH_WINDOW}_headers"
        finally:
            imap.logout()
        result = {"query": query, "count": len(emails), "emails": emails}
        if scope:
            result["search_scope"] = scope
            result["note"] = ("Suchbegriff enthält Nicht-ASCII-Zeichen — "
                              "clientseitig über die letzten Nachrichten gefiltert.")
        return result

    def send(self, to_list, cc_list, subject, body, attachments=None) -> dict:
        msg = _build_mime_message(self.address, to_list, cc_list, subject, body,
                                  attachments)
        self._smtp_send(msg, to_addrs=list(to_list) + list(cc_list))
        return {
            "status": "sent",
            "to": to_list if len(to_list) > 1 else to_list[0],
            "subject": subject,
            "attachments": [a[0] for a in (attachments or [])],
        }

    def reply(self, msg_id, body) -> dict:
        # Fetch the original for Reply-To / Subject / Message-ID threading.
        imap = self._imap()
        try:
            imap.select("INBOX", readonly=True)
            _, msg_data = imap.fetch(str(msg_id).encode(), "(RFC822)")
            original = email.message_from_bytes(msg_data[0][1])
        finally:
            imap.logout()
        reply_to = original.get("Reply-To") or original.get("From", "")
        subject = _decode_mime_header(original.get("Subject", ""))
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg = _build_mime_message(
            self.address, [reply_to], [], subject, body,
            headers={"In-Reply-To": original.get("Message-ID", ""),
                     "References": original.get("Message-ID", "")})
        self._smtp_send(msg)
        return {"status": "replied", "to": reply_to, "subject": subject}

    def test(self) -> dict:
        imap = self._imap()
        try:
            _, data = imap.select("INBOX", readonly=True)
            total = data[0].decode() if data and data[0] else "?"
        finally:
            imap.logout()
        return {"ok": True, "detail": f"IMAP-Login OK, INBOX enthält {total} Nachrichten"}


# How many recent messages POP3 / the non-ASCII IMAP fallback scan client-side.
_CLIENT_SEARCH_WINDOW = 200


# ---------------------------------------------------------------------------
# POP3 + SMTP — deliberately reduced connector (E7)
# ---------------------------------------------------------------------------

class Pop3Connector(EmailConnector):
    type_name = "pop3"

    def capabilities(self) -> dict:
        return {
            "folders": False,          # POP3 has no folders — parameter ignored
            "server_search": False,    # search filters the last N headers client-side
            "native_query_syntax": False,
            "reply": True,             # via RETR of the original + SMTP
        }

    def _pop3(self):
        host = self.account.get("pop3_host", "")
        if not host:
            raise ConnectorError(f"Konto '{self.account.get('name', '?')}': pop3_host fehlt")
        security = (self.account.get("pop3_security") or "ssl").lower()
        port = int(self.account.get("pop3_port") or (995 if security == "ssl" else 110))
        if security == "ssl":
            pop = poplib.POP3_SSL(host, port, timeout=30)
        else:
            pop = poplib.POP3(host, port, timeout=30)
            if security == "starttls":
                pop.stls()
        pop.user(self.login_user)
        pop.pass_(self.password)
        return pop

    @staticmethod
    def _folder_note(folder):
        if folder and folder.upper() != "INBOX":
            return f"POP3 kennt keine Ordner — Parameter folder='{folder}' ignoriert."
        return None

    def _top_headers(self, pop, count):
        """Listing entries for the most recent `count` messages (TOP = headers only)."""
        total = pop.stat()[0]
        nums = list(range(max(1, total - count + 1), total + 1))
        nums.reverse()  # newest first
        entries = []
        for n in nums:
            _, lines, _ = pop.top(n, 0)
            msg = email.message_from_bytes(b"\r\n".join(lines))
            e = _headers_dict(msg, n)
            e["to"] = _decode_mime_header(msg.get("To", ""))
            entries.append(e)
        return entries, total

    def list_messages(self, folder="INBOX", limit=10) -> dict:
        pop = self._pop3()
        try:
            emails, total = self._top_headers(pop, limit)
        finally:
            pop.quit()
        for e in emails:
            e.pop("to", None)
        result = {"folder": "INBOX", "count": len(emails), "total": total, "emails": emails}
        note = self._folder_note(folder)
        if note:
            result["note"] = note
        return result

    def read_message(self, msg_id, folder="INBOX") -> dict:
        pop = self._pop3()
        try:
            _, lines, _ = pop.retr(int(msg_id))
        finally:
            pop.quit()
        msg = email.message_from_bytes(b"\r\n".join(lines))
        result = _full_message_dict(msg, msg_id)
        note = self._folder_note(folder)
        if note:
            result["note"] = note
        # POP3 message numbers are per-connection — the Message-ID header is
        # the stable handle, so it's already included in the result.
        return result

    def search(self, query, limit=10) -> dict:
        pop = self._pop3()
        try:
            entries, _total = self._top_headers(pop, _CLIENT_SEARCH_WINDOW)
        finally:
            pop.quit()
        emails = _client_filter_headers(entries, query)[:limit]
        for e in emails:
            e.pop("to", None)
        return {
            "query": query, "count": len(emails), "emails": emails,
            # E7/Regel 12: POP3 has NO server search — say what was scanned
            # instead of silently pretending full-mailbox coverage.
            "search_scope": f"last_{_CLIENT_SEARCH_WINDOW}_headers",
            "note": ("POP3 hat keine Server-Suche — es wurden nur die Header der "
                     f"letzten {_CLIENT_SEARCH_WINDOW} Nachrichten clientseitig gefiltert."),
        }

    def send(self, to_list, cc_list, subject, body, attachments=None) -> dict:
        msg = _build_mime_message(self.address, to_list, cc_list, subject, body,
                                  attachments)
        self._smtp_send(msg, to_addrs=list(to_list) + list(cc_list))
        return {
            "status": "sent",
            "to": to_list if len(to_list) > 1 else to_list[0],
            "subject": subject,
            "attachments": [a[0] for a in (attachments or [])],
        }

    def reply(self, msg_id, body) -> dict:
        # No IMAP access to the original — but POP3 RETR delivers the full
        # message incl. Message-ID/Reply-To, so threading works (E7).
        pop = self._pop3()
        try:
            _, lines, _ = pop.retr(int(msg_id))
        finally:
            pop.quit()
        original = email.message_from_bytes(b"\r\n".join(lines))
        reply_to = original.get("Reply-To") or original.get("From", "")
        subject = _decode_mime_header(original.get("Subject", ""))
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg = _build_mime_message(
            self.address, [reply_to], [], subject, body,
            headers={"In-Reply-To": original.get("Message-ID", ""),
                     "References": original.get("Message-ID", "")})
        self._smtp_send(msg)
        return {"status": "replied", "to": reply_to, "subject": subject}

    def test(self) -> dict:
        pop = self._pop3()
        try:
            total = pop.stat()[0]
        finally:
            pop.quit()
        return {"ok": True, "detail": f"POP3-Login OK, Postfach enthält {total} Nachrichten"}


# ---------------------------------------------------------------------------
# Exchange EWS (on-prem, exchangelib) — pattern verified by the user's
# working email_service.py specimen (EMAIL_TOOLS_V2_PLAN.md Anhang A)
# ---------------------------------------------------------------------------

class ExchangeEwsConnector(EmailConnector):
    type_name = "exchange_ews"

    def capabilities(self) -> dict:
        return {
            "folders": True,           # inbox/sent/drafts/trash (well-known folders)
            "server_search": True,     # QuerySet filter()
            "native_query_syntax": False,
            "reply": True,             # native .reply() — threading server-side
        }

    def _lib(self):
        # exchangelib is an OPTIONAL dependency — lazy import, fail-loud
        # (E8: a missing lib must not break boot, only Exchange accounts).
        try:
            import exchangelib
            return exchangelib
        except ImportError:
            raise ConnectorError(
                "Exchange-Konnektor: exchangelib ist nicht installiert "
                "(pip install exchangelib im Server-Python).")

    def _ews_account(self):
        ews = self._lib()
        server = self.account.get("server", "")
        if not server and not self.account.get("autodiscover"):
            raise ConnectorError(
                f"Konto '{self.account.get('name', '?')}': server (EWS-Host) fehlt")
        if self.account.get("verify_ssl") is False:
            # Self-signed cert on the on-prem Exchange (Anhang A note 3).
            # ACHTUNG: HTTP_ADAPTER_CLS is a PROCESS-GLOBAL class variable —
            # this disables TLS verification for ALL exchangelib connections
            # in the Brain process, not just this account. Acceptable (there
            # is typically exactly one on-prem Exchange) but deliberate; the
            # admin UI carries a warning at the field.
            from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
            BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
        credentials = ews.Credentials(username=self.login_user, password=self.password)
        if self.account.get("autodiscover"):
            return ews.Account(
                primary_smtp_address=self.address,
                credentials=credentials,
                access_type=ews.DELEGATE,
                autodiscover=True,
            )
        config = ews.Configuration(server=server, credentials=credentials)
        return ews.Account(
            primary_smtp_address=self.address,
            config=config,
            credentials=credentials,
            access_type=ews.DELEGATE,
            autodiscover=False,   # direct server, the verified specimen pattern
        )

    @staticmethod
    def _folder_for(acct, folder):
        f = (folder or "INBOX").strip().upper()
        return {
            "INBOX": acct.inbox,
            "SENT": acct.sent,
            "DRAFTS": acct.drafts,
            "TRASH": acct.trash,
        }.get(f, acct.inbox)

    @staticmethod
    def _item_entry(item):
        sender = ""
        if getattr(item, "sender", None) is not None:
            sender = item.sender.email_address or item.sender.name or ""
        return {
            "id": item.id,
            "from": sender,
            "subject": item.subject or "",
            "date": str(item.datetime_received or ""),
        }

    def list_messages(self, folder="INBOX", limit=10) -> dict:
        acct = self._ews_account()
        f = self._folder_for(acct, folder)
        items = list(f.all().order_by("-datetime_received")[:limit])
        emails = [self._item_entry(i) for i in items]
        result = {"folder": folder or "INBOX", "count": len(emails), "emails": emails}
        if (folder or "INBOX").strip().upper() not in ("INBOX", "SENT", "DRAFTS", "TRASH"):
            result["note"] = (f"Unbekannter Ordner '{folder}' — INBOX verwendet "
                              "(unterstützt: INBOX, SENT, DRAFTS, TRASH).")
        return result

    def read_message(self, msg_id, folder="INBOX") -> dict:
        acct = self._ews_account()
        f = self._folder_for(acct, folder)
        item = f.get(id=msg_id)
        body = item.text_body or ""
        if len(body) > 10000:
            body = body[:10000] + "\n...(truncated)"
        return {
            "id": item.id,
            "from": (item.sender.email_address if item.sender else ""),
            "to": ", ".join((m.email_address or "") for m in (item.to_recipients or [])),
            "cc": ", ".join((m.email_address or "") for m in (item.cc_recipients or [])),
            "subject": item.subject or "",
            "date": str(item.datetime_received or ""),
            "body": body,
            "attachments": [a.name for a in (item.attachments or [])],
            "message_id": item.message_id or "",
        }

    def search(self, query, limit=10) -> dict:
        Q = self._lib().Q
        acct = self._ews_account()
        qs = acct.inbox.all()
        for field, value in _parse_simple_query(query):
            if not value:
                continue
            if field == "from":
                qs = qs.filter(sender__icontains=value)
            elif field == "subject":
                qs = qs.filter(subject__icontains=value)
            elif field == "to":
                qs = qs.filter(Q(to_recipients__icontains=value))
            else:
                qs = qs.filter(Q(subject__icontains=value) | Q(body__icontains=value))
        items = list(qs.order_by("-datetime_received")[:limit])
        emails = [self._item_entry(i) for i in items]
        return {"query": query, "count": len(emails), "emails": emails}

    def send(self, to_list, cc_list, subject, body, attachments=None) -> dict:
        ews = self._lib()
        acct = self._ews_account()
        # EWS always sends as the authenticated mailbox (Anhang A note 4) —
        # matching the tool semantics (From = account address).
        msg = ews.Message(
            account=acct,
            subject=subject,
            body=body,
            to_recipients=[ews.Mailbox(email_address=r) for r in to_list],
            cc_recipients=([ews.Mailbox(email_address=r) for r in cc_list] or None),
        )
        for name, data, maintype, subtype in (attachments or []):
            msg.attach(ews.FileAttachment(name=name, content=data,
                                          content_type=f"{maintype}/{subtype}"))
        msg.send()
        return {
            "status": "sent",
            "to": to_list if len(to_list) > 1 else to_list[0],
            "subject": subject,
            "attachments": [a[0] for a in (attachments or [])],
        }

    def reply(self, msg_id, body) -> dict:
        self._lib()
        acct = self._ews_account()
        item = acct.inbox.get(id=msg_id)
        subject = item.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        reply_to = ""
        if item.reply_to:
            reply_to = item.reply_to[0].email_address or ""
        elif item.sender:
            reply_to = item.sender.email_address or ""
        # Native EWS reply — the server keeps the conversation thread.
        item.reply(subject=subject, body=body,
                   to_recipients=[reply_to] if reply_to else None)
        return {"status": "replied", "to": reply_to, "subject": subject}

    def test(self) -> dict:
        acct = self._ews_account()
        # Account bind + one read — deliberately NO send (Anhang A note 7).
        total = acct.inbox.total_count
        return {"ok": True, "detail": f"EWS-Bind OK, INBOX enthält {total} Nachrichten"}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_CONNECTOR_TYPES = {
    "imap": ImapSmtpConnector,
    "pop3": Pop3Connector,
    "exchange_ews": ExchangeEwsConnector,
}


def connector_for_account(account: dict) -> EmailConnector:
    """Build the connector for an account dict (tools_config → email.accounts[])."""
    ctype = (account.get("type") or "imap").lower()
    cls = _CONNECTOR_TYPES.get(ctype)
    if cls is None:
        raise ConnectorError(
            f"Unbekannter Konto-Typ '{ctype}' (unterstützt: {', '.join(sorted(_CONNECTOR_TYPES))})")
    return cls(account)
