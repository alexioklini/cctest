"""M2 (G7) — the egress gate covers every way OFF the machine, and the
shell/script de-anonymiser is deny-by-default.

Two properties, both leak-shaped:

1. `_gdpr_guard_web_args` must fire for email_send/email_reply/generate_image and
   MCP tools, not just the web tools. Live specimen 30051b1f4439: the model called
   gmail_send (now email_send)(to="<EMAIL_1_a812>", body="…IBAN DE38…") and the SMTP send really
   went out — it failed only because that opaque token isn't a valid address. A
   shape-preserving fake address (this pipeline mints exactly those) would have
   been delivered to a stranger.

2. `_gdpr_deanon_tool_args` injects REAL values into execute_command/python_exec
   args (that's L3a's job — `grep <real name> file.csv` must work). The old guard
   was an allow-with-blocklist: de-anonymise unless one of ~15 network markers
   appears. `mail`, `sendmail`, `msmtp`, `osascript` and `gh` were all missing —
   and session b4edbc9dc8e7 has the model reaching for exactly `mail -s "IBAN …"`.
   It is now deny-by-default.

The calibration cases below are the point of this file: deny-by-default is only
worth having if it still admits the LEGITIMATE work. A guard that blocks every
pandas analysis would trade the leak for a silent quality regression — which is
the failure this whole wave exists to prevent.

Bare test interpreter — no server, no network.
"""

import unittest

import brain
from engine.context import get_request_context, request_context


class TestEgressToolSet(unittest.TestCase):
    def test_web_tools_are_egress(self):
        for t in brain.WEB_SEARCH_TOOLS:
            self.assertTrue(brain._is_egress_tool(t), t)

    def test_mail_and_image_tools_are_egress(self):
        # email → the account's SMTP/EWS server; generate_image → api.mistral.ai ALWAYS,
        # regardless of the session model (so even a local session egresses).
        for t in ("email_send", "email_reply", "generate_image"):
            self.assertTrue(brain._is_egress_tool(t), t)

    def test_local_tools_are_not_egress(self):
        for t in ("read_document", "write_file", "grep_files", "render_diagram"):
            self.assertFalse(brain._is_egress_tool(t), t)


# NOTE (2026-07-22): TestGateFiresForMailTool was DELETED. It exercised
# `_gdpr_guard_web_args` (the web/mail egress BLOCK gate), which was removed
# under the PII-in-LLM-only policy: email_send/web tools now run on real data
# and are no longer refused for carrying protected values. The behaviour it
# guarded (refuse email to a fake recipient) is intentionally gone.


class TestArgsDeanonIsUnconditional(unittest.TestCase):
    """2026-07-22 rework: the execute_command/python_exec deny-by-default network
    guard was REMOVED by explicit operator decision. This is a PII-for-LLM seam —
    its job is to keep real PII out of an LLM's context. It is NOT a
    script sandbox: args-deanon now injects REAL values into EVERY non-LLM
    tool's args unconditionally, so a file written by any of them (and any local
    data script) carries real data from the start — which is the whole point of
    the rework (no on-disk reverse pass, no half-written-file race).

    Accepted, on-record trade-off: a model-authored script that reaches the
    network now runs with real values and could send them off-machine. These
    tests pin the NEW behaviour so a future re-introduction of the guard is a
    deliberate, visible change — not a silent regression.
    """

    def _mapping(self):
        import pseudonymizer as ps
        m = ps.new_mapping()
        m.forward["Bonnie Stark"] = "Sam Mitchell"
        m.reverse["Sam Mitchell"] = "Bonnie Stark"
        m.categories["Bonnie Stark"] = "contact"
        return m

    def _deanon(self, tool, args):
        import pseudonymizer as ps
        m = self._mapping()
        try:
            with request_context():
                get_request_context()._gdpr_mapping_id = m.mapping_id
                return brain._gdpr_deanon_tool_args(tool, args)
        finally:
            ps.close_mapping(m.mapping_id)

    def test_local_grep_receives_the_real_name(self):
        out = self._deanon("execute_command",
                           {"command": "grep 'Sam Mitchell' k.csv"})
        self.assertIn("Bonnie Stark", out["command"])
        self.assertNotIn("Sam Mitchell", out["command"])

    def test_python_local_script_receives_real_values(self):
        out = self._deanon(
            "python_exec",
            {"code": "import pandas as pd\ndf=pd.read_csv('k.csv')\n"
                     "print(df[df.name=='Sam Mitchell'])"})
        self.assertIn("Bonnie Stark", out["code"])

    def test_network_command_now_receives_the_real_value(self):
        # Previously this KEPT the fake (the guard). Now unconditional: the mail
        # line receives the real value. Pins the accepted egress trade-off.
        out = self._deanon("execute_command",
                           {"command": "mail -s 'Sam Mitchell' x@y.z"})
        self.assertIn("Bonnie Stark", out["command"])

    def test_network_python_script_now_receives_the_real_value(self):
        out = self._deanon(
            "python_exec",
            {"code": "import requests; requests.post('http://x', "
                     "data='Sam Mitchell')"})
        self.assertIn("Bonnie Stark", out["code"])

    def test_write_tools_are_whitelisted_and_deanonymise_content(self):
        # The file-writing tools now write REAL data from the start: their
        # `content` arg is de-anonymised before the bytes hit disk.
        for tool, arg in (("write_file", "content"),
                          ("write_document", "content"),
                          ("edit_file", "new_string")):
            # Not LLM-arg tools → they de-anonymise (policy: deanon everything
            # except the LLM-arg deny-list).
            self.assertNotIn(tool, brain.GDPR_LLM_ARG_TOOLS, tool)
            out = self._deanon(tool, {"path": "r.md", arg: "Kunde: Sam Mitchell"})
            self.assertIn("Bonnie Stark", out[arg],
                          f"{tool}.{arg} did not de-anonymise")

    def test_no_mapping_is_a_noop(self):
        with request_context():
            get_request_context()._gdpr_mapping_id = ""
            out = brain._gdpr_deanon_tool_args(
                "execute_command", {"command": "grep 'Sam Mitchell' k.csv"})
        self.assertEqual(out["command"], "grep 'Sam Mitchell' k.csv")


if __name__ == "__main__":
    unittest.main()
