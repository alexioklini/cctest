"""Email-Tools v2 (EMAIL_TOOLS_V2_PLAN.md) — Konnektoren, Konto-Auflösung,
Boot-Migration.

Die Konnektoren (engine/email_connectors.py) sind reiner Protokoll-Code ohne
brain-Import — hier gegen gemocktes imaplib/poplib/smtplib getestet. Die
Tool-Schicht (Konto-Auflösung default/benannt/unbekannt, RFC-Adressprüfung)
und die beiden idempotenten Boot-Migrationen laufen gegen brain mit gepatchter
Config.

Run: python3 -m unittest tests.test_email_tools_v2 -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import email_connectors as ec  # noqa: E402
from engine.email_connectors import (  # noqa: E402
    ConnectorError,
    ImapSmtpConnector,
    Pop3Connector,
    _imap_search_criteria,
    _parse_simple_query,
    connector_for_account,
)
from engine.tools import email_tools as et  # noqa: E402


IMAP_ACCOUNT = {
    "name": "gmail", "type": "imap", "preset": "gmail",
    "email": "me@gmail.com", "username": "", "password": "app-pass",
    "imap_host": "imap.gmail.com", "imap_port": 993,
    "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl",
}

GENERIC_IMAP_ACCOUNT = {
    "name": "gmx", "type": "imap",
    "email": "me@gmx.de", "username": "me@gmx.de", "password": "pw",
    "imap_host": "imap.gmx.net", "imap_port": 993,
    "smtp_host": "mail.gmx.net", "smtp_port": 587, "smtp_security": "starttls",
}

POP3_ACCOUNT = {
    "name": "buero", "type": "pop3",
    "email": "b@firma.de", "username": "b", "password": "pw",
    "pop3_host": "pop.firma.de", "pop3_port": 995,
    "smtp_host": "smtp.firma.de", "smtp_port": 587, "smtp_security": "starttls",
}

RAW_HEADER = (b"From: Alice <alice@example.com>\r\n"
              b"Subject: Hello there\r\n"
              b"Date: Mon, 1 Jan 2026 10:00:00 +0000\r\n"
              b"Message-ID: <m1@example.com>\r\n\r\n")

RAW_FULL = (b"From: Alice <alice@example.com>\r\n"
            b"To: me@gmail.com\r\n"
            b"Subject: Hello there\r\n"
            b"Date: Mon, 1 Jan 2026 10:00:00 +0000\r\n"
            b"Message-ID: <m1@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hi, this is the body.\r\n")


class FakeIMAP:
    instances: list = []

    def __init__(self, host, port=993):
        self.host, self.port = host, port
        self.logged_in = None
        self.selected = None
        self.search_crit = None
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        self.logged_in = (user, pw)
        return ("OK", [b"Logged in"])

    def select(self, folder, readonly=False):
        self.selected = folder
        return ("OK", [b"3"])

    def search(self, charset, crit):
        self.search_crit = crit
        return ("OK", [b"1 2 3"])

    def fetch(self, eid, spec):
        raw = RAW_FULL if "HEADER" not in spec else RAW_HEADER
        return ("OK", [(eid + b" ()", raw)])

    def logout(self):
        return ("BYE", [])


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls = False
        self.logged_in = None

    def starttls(self):
        self.tls = True

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append({"msg": msg, "to_addrs": to_addrs,
                              "host": self.host, "tls": self.tls,
                              "login": self.logged_in})

    def quit(self):
        pass


class FakePOP3:
    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def user(self, u):
        self.u = u

    def pass_(self, p):
        self.p = p

    def stat(self):
        return (3, 999)

    def top(self, n, lines):
        hdr = RAW_HEADER.replace(b"Hello there", b"Msg %d" % n)
        return ("+OK", hdr.split(b"\r\n"), len(hdr))

    def retr(self, n):
        return ("+OK", RAW_FULL.split(b"\r\n"), len(RAW_FULL))

    def quit(self):
        pass


def _patch_protocols():
    return (mock.patch.object(ec.imaplib, "IMAP4_SSL", FakeIMAP),
            mock.patch.object(ec.smtplib, "SMTP_SSL", FakeSMTP),
            mock.patch.object(ec.smtplib, "SMTP", FakeSMTP),
            mock.patch.object(ec.poplib, "POP3_SSL", FakePOP3),
            mock.patch.object(ec.poplib, "POP3", FakePOP3))


class ProtocolTestBase(unittest.TestCase):
    def setUp(self):
        FakeIMAP.instances = []
        FakeSMTP.sent = []
        self._patches = _patch_protocols()
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])


class TestQueryTranslation(unittest.TestCase):
    def test_prefixes_and_free_text(self):
        self.assertEqual(
            _imap_search_criteria('from:alice subject:"quarterly report" invoice'),
            '(FROM "alice" SUBJECT "quarterly report" TEXT "invoice")')

    def test_unknown_prefix_becomes_text(self):
        # is:unread is Gmail syntax — on generic IMAP it degrades to TEXT,
        # never to a protocol error.
        crit = _imap_search_criteria("is:unread")
        self.assertIn('TEXT "is:unread"', crit)

    def test_empty_query_is_all(self):
        self.assertEqual(_imap_search_criteria(""), "ALL")

    def test_parse_quoted_free_text(self):
        self.assertEqual(_parse_simple_query('"exact phrase"'),
                         [("", "exact phrase")])


class TestImapConnector(ProtocolTestBase):
    def test_gmail_preset_capabilities(self):
        caps = ImapSmtpConnector(IMAP_ACCOUNT).capabilities()
        self.assertTrue(caps["native_query_syntax"])
        self.assertTrue(caps["folders"])
        self.assertTrue(caps["server_search"])

    def test_generic_account_has_no_native_syntax(self):
        caps = ImapSmtpConnector(GENERIC_IMAP_ACCOUNT).capabilities()
        self.assertFalse(caps["native_query_syntax"])

    def test_list_messages(self):
        result = ImapSmtpConnector(IMAP_ACCOUNT).list_messages(limit=2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["emails"][0]["from"], "Alice <alice@example.com>")
        imap = FakeIMAP.instances[0]
        # Empty username → email is the login (the Gmail case, E3).
        self.assertEqual(imap.logged_in, ("me@gmail.com", "app-pass"))
        self.assertEqual(imap.host, "imap.gmail.com")

    def test_search_uses_xgmraw_only_with_native_syntax(self):
        ImapSmtpConnector(IMAP_ACCOUNT).search("from:alice is:unread")
        self.assertIn("X-GM-RAW", FakeIMAP.instances[0].search_crit)
        ImapSmtpConnector(GENERIC_IMAP_ACCOUNT).search("from:alice")
        self.assertEqual(FakeIMAP.instances[1].search_crit, '(FROM "alice")')

    def test_non_ascii_search_falls_back_to_client_filter(self):
        result = ImapSmtpConnector(GENERIC_IMAP_ACCOUNT).search("Müller")
        self.assertIn("search_scope", result)
        # The fake mailbox has no Müller mails — but the call must not crash
        # and must disclose the reduced scope (Regel 12 — fail loud).
        self.assertEqual(FakeIMAP.instances[0].search_crit, "ALL")

    def test_send_ssl_and_recipients(self):
        result = ImapSmtpConnector(IMAP_ACCOUNT).send(
            ["a@b.de", "c@d.de"], ["e@f.de"], "Betreff", "Text")
        self.assertEqual(result["status"], "sent")
        sent = FakeSMTP.sent[0]
        self.assertEqual(sent["to_addrs"], ["a@b.de", "c@d.de", "e@f.de"])
        self.assertEqual(sent["msg"]["From"], "me@gmail.com")

    def test_send_starttls(self):
        ImapSmtpConnector(GENERIC_IMAP_ACCOUNT).send(["a@b.de"], [], "S", "B")
        self.assertTrue(FakeSMTP.sent[0]["tls"])

    def test_reply_threads_and_prefixes_subject(self):
        result = ImapSmtpConnector(IMAP_ACCOUNT).reply("1", "Antwort")
        self.assertEqual(result["status"], "replied")
        msg = FakeSMTP.sent[0]["msg"]
        self.assertEqual(msg["In-Reply-To"], "<m1@example.com>")
        self.assertEqual(msg["References"], "<m1@example.com>")
        self.assertTrue(msg["Subject"].startswith("Re: "))

    def test_missing_host_fails_loud(self):
        acct = dict(IMAP_ACCOUNT)
        acct.pop("imap_host")
        with self.assertRaises(ConnectorError):
            ImapSmtpConnector(acct).list_messages()


class TestPop3Connector(ProtocolTestBase):
    def test_capabilities_reduced(self):
        caps = Pop3Connector(POP3_ACCOUNT).capabilities()
        self.assertFalse(caps["folders"])
        self.assertFalse(caps["server_search"])
        self.assertTrue(caps["reply"])

    def test_list_ignores_folder_and_says_so(self):
        result = Pop3Connector(POP3_ACCOUNT).list_messages(folder="Archiv", limit=2)
        self.assertIn("ignoriert", result.get("note", ""))
        self.assertEqual(result["folder"], "INBOX")
        self.assertEqual(result["count"], 2)
        # Newest first: message numbers descending.
        self.assertEqual([e["id"] for e in result["emails"]], ["3", "2"])

    def test_search_is_client_side_and_disclosed(self):
        result = Pop3Connector(POP3_ACCOUNT).search("subject:Msg 3")
        self.assertTrue(result["search_scope"].startswith("last_"))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["emails"][0]["id"], "3")

    def test_reply_threads_via_retr(self):
        result = Pop3Connector(POP3_ACCOUNT).reply("2", "Antwort")
        self.assertEqual(result["status"], "replied")
        msg = FakeSMTP.sent[0]["msg"]
        self.assertEqual(msg["In-Reply-To"], "<m1@example.com>")
        self.assertEqual(msg["To"], "Alice <alice@example.com>")

    def test_read_returns_body(self):
        result = Pop3Connector(POP3_ACCOUNT).read_message("1")
        self.assertIn("this is the body", result["body"])


class TestExchangeConnectorOffline(unittest.TestCase):
    def test_capabilities_without_connection(self):
        acct = {"name": "firma", "type": "exchange_ews", "email": "x@firma.de",
                "username": "DOM\\x", "password": "pw", "server": "mail.firma.de"}
        caps = connector_for_account(acct).capabilities()
        self.assertTrue(caps["server_search"])
        self.assertTrue(caps["reply"])

    def test_missing_exchangelib_fails_loud(self):
        acct = {"name": "firma", "type": "exchange_ews", "email": "x@firma.de",
                "password": "pw", "server": "mail.firma.de"}
        conn = connector_for_account(acct)
        with mock.patch.dict(sys.modules, {"exchangelib": None}):
            with self.assertRaises(ConnectorError) as cm:
                conn._lib()
            self.assertIn("exchangelib", str(cm.exception))

    def test_unknown_type_fails_loud(self):
        with self.assertRaises(ConnectorError):
            connector_for_account({"name": "x", "type": "carrier_pigeon"})


EMAIL_CFG = {
    "enabled": True,
    "default_account": "gmail",
    "accounts": [IMAP_ACCOUNT, POP3_ACCOUNT],
}


class TestAccountResolution(unittest.TestCase):
    def _with_cfg(self, cfg):
        import brain
        return mock.patch.object(brain, "get_tool_config",
                                 return_value={"email": cfg})

    def test_default_account(self):
        with self._with_cfg(EMAIL_CFG):
            acct, err = et._resolve_account({})
        self.assertIsNone(err)
        self.assertEqual(acct["name"], "gmail")

    def test_named_account(self):
        with self._with_cfg(EMAIL_CFG):
            acct, err = et._resolve_account({"account": "buero"})
        self.assertIsNone(err)
        self.assertEqual(acct["name"], "buero")

    def test_unknown_account_lists_available(self):
        # Self-healing error (E5): the message must NAME the real accounts.
        with self._with_cfg(EMAIL_CFG):
            acct, err = et._resolve_account({"account": "tippfehler"})
        self.assertIsNone(acct)
        self.assertIn("gmail", err)
        self.assertIn("buero", err)

    def test_missing_default_falls_back_to_first(self):
        cfg = dict(EMAIL_CFG, default_account="")
        with self._with_cfg(cfg):
            acct, err = et._resolve_account({})
        self.assertEqual(acct["name"], "gmail")

    def test_unconfigured(self):
        with self._with_cfg({"enabled": True, "accounts": []}):
            acct, err = et._resolve_account({})
        self.assertIsNone(acct)
        self.assertIn("nicht konfiguriert", err)


class TestAddressValidation(unittest.TestCase):
    def test_pseudonym_token_is_rejected(self):
        # Anhang A note 5: the v9.343 incident showed opaque tokens failing
        # only BY ACCIDENT — this makes the shape check deterministic.
        self.assertIsNotNone(et._validate_addresses(["<EMAIL_1_a812>"]))

    def test_valid_addresses_pass(self):
        self.assertIsNone(et._validate_addresses(
            ["a@b.de", "x.y+z@sub.example.org"]))

    def test_norm_accepts_string_and_list(self):
        self.assertEqual(et._norm("a@b.de; c@d.de, e@f.de"),
                         ["a@b.de", "c@d.de", "e@f.de"])
        self.assertEqual(et._norm(["a@b.de", " "]), ["a@b.de"])


class TestBootMigration(unittest.TestCase):
    def setUp(self):
        import brain
        self.brain = brain
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = os.path.join(self.tmp.name, "tools_config.json")
        self.agents_dir = os.path.join(self.tmp.name, "agents")
        os.makedirs(os.path.join(self.agents_dir, "main"))
        self._p1 = mock.patch.object(brain, "_TOOLS_CONFIG_PATH", self.cfg_path)
        self._p2 = mock.patch.object(brain, "AGENTS_DIR", self.agents_dir)
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)

    def _write(self, cfg):
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f)

    def _read(self):
        with open(self.cfg_path) as f:
            return json.load(f)

    def test_gmail_record_becomes_imap_account(self):
        self._write({"gmail": {"enabled": True, "email": "me@gmail.com",
                               "app_password": "secret"}})
        self.assertTrue(self.brain.migrate_gmail_to_email_config())
        cfg = self._read()
        self.assertNotIn("gmail", cfg)
        email = cfg["email"]
        self.assertEqual(email["default_account"], "gmail")
        acct = email["accounts"][0]
        self.assertEqual(acct["type"], "imap")
        self.assertEqual(acct["preset"], "gmail")
        self.assertEqual(acct["password"], "secret")
        self.assertEqual(acct["imap_host"], "imap.gmail.com")

    def test_gmail_json_fallback_is_collected(self):
        self._write({"gmail": {"enabled": True, "email": "", "app_password": ""}})
        with open(os.path.join(self.agents_dir, "main", "gmail.json"), "w") as f:
            json.dump({"email": "fb@gmail.com", "app_password": "fbpw"}, f)
        self.assertTrue(self.brain.migrate_gmail_to_email_config())
        acct = self._read()["email"]["accounts"][0]
        self.assertEqual(acct["email"], "fb@gmail.com")
        self.assertEqual(acct["password"], "fbpw")

    def test_idempotent_once_email_exists(self):
        self._write({"email": {"enabled": True, "default_account": "x",
                               "accounts": [{"name": "x"}]}})
        self.assertFalse(self.brain.migrate_gmail_to_email_config())
        self.assertEqual(self._read()["email"]["default_account"], "x")

    def test_no_credentials_yields_empty_account_list(self):
        self._write({})
        self.brain.migrate_gmail_to_email_config()
        self.assertEqual(self._read()["email"]["accounts"], [])

    def test_tool_settings_key_rename_preserves_matrix(self):
        settings = {
            "gmail_send": {"state": "active", "states": {"interactive": "active"},
                           "purposes": ["interactive"]},
            "gmail_inbox": {"state": "deferred", "states": {}},
            "read_file": {"state": "active"},
        }
        n = self.brain.migrate_email_tool_settings(settings)
        self.assertEqual(n, 2)
        self.assertNotIn("gmail_send", settings)
        self.assertEqual(settings["email_send"]["states"]["interactive"], "active")
        self.assertEqual(settings["email_inbox"]["state"], "deferred")
        # Idempotent: second run is a no-op.
        self.assertEqual(self.brain.migrate_email_tool_settings(settings), 0)

    def test_stale_old_key_next_to_new_is_dropped(self):
        settings = {"gmail_read": {"state": "active"},
                    "email_read": {"state": "deferred"}}
        self.brain.migrate_email_tool_settings(settings)
        self.assertNotIn("gmail_read", settings)
        self.assertEqual(settings["email_read"]["state"], "deferred")


class TestWiring(unittest.TestCase):
    """The 4-site rule + Egress/Plan-Mode wiring followed the rename (E9)."""

    def test_dispatch_and_groups(self):
        import brain
        for name in ("email_accounts", "email_inbox", "email_read",
                     "email_search", "email_send", "email_reply"):
            self.assertIn(name, brain.TOOL_DISPATCH, name)
            self.assertIn(name, brain.TOOL_GROUPS["email"], name)
        for name in ("gmail_inbox", "gmail_read", "gmail_search",
                     "gmail_send", "gmail_reply"):
            self.assertNotIn(name, brain.TOOL_DISPATCH, name)

    def test_readonly_and_egress_sets(self):
        import brain
        for name in ("email_inbox", "email_read", "email_search", "email_accounts"):
            self.assertIn(name, brain.READONLY_TOOLS, name)
        self.assertIn("email_send", brain.EGRESS_TOOLS)
        self.assertIn("email_reply", brain.EGRESS_TOOLS)
        self.assertNotIn("email_read", brain.EGRESS_TOOLS)

    def test_schemas_exist_and_are_static(self):
        from engine.tool_schemas import TOOL_DEFINITIONS
        by_name = {t["name"]: t for t in TOOL_DEFINITIONS}
        for name in ("email_accounts", "email_inbox", "email_read",
                     "email_search", "email_send", "email_reply"):
            self.assertIn(name, by_name, name)
        for name in ("gmail_inbox", "gmail_read", "gmail_search",
                     "gmail_send", "gmail_reply"):
            self.assertNotIn(name, by_name, name)
        # account parameter present on the five operational tools
        for name in ("email_inbox", "email_read", "email_search",
                     "email_send", "email_reply"):
            props = by_name[name]["input_schema"]["properties"]
            self.assertIn("account", props, name)


if __name__ == "__main__":
    unittest.main()
